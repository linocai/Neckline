"""持仓开平仓的系统自动记录编排层(plan §五 V2-⑩,蓝图 §2.2/§5.2/§5.7)。

**产品前提**:买入只要求票+价+量,卖出只要求价+量(+可选标签)——「系统自动记录,
人只补充机器不知道的信息」。本模块把 ⑩-A/B/D 的"其余自动关联"接起来,供
`api/app.py`(HTTP)与 `scripts/positions.py`(CLI)两个入口共用,**行为不因调用方
是谁而不同**(同项目 `ts_code` 归一"写入通道单一处理"的既定姿势)。

一次买入落三样东西:
    1. `entry_snapshots` 冻结一行(machine-knowable 的一切,best-effort、单项失败
       不牵连整体——核心管线对可选情报输入必须包保险丝)。
    2. `position_plans` version=1(从来源篮子卡继承建仓观察区间/最高追价/离场参考
       区间/验证与失效条件/主要风险;无来源篮子或卡未就绪 → 空计划如实标,不省略
       这一行)。
    3. `user_actions` 落 kind='buy'/'sell'(服务端自动落,不经用户操作)。

**来源篮子查找是唯一的"读"面**:只读 `baskets`/`basket_members`/`basket_cards`
三张表(经 `neckline.selection.basket_store` 与本模块自己的一条只读 SQL),**本模块
对这四张表(含 `tier_history`)零写入**——选股与持仓职责独立、信息互通,不回头
修改对方已冻结的历史信息(蓝图 §2.3/§6;守门单测见 `tests/test_positions_entry.py`)。

**与 `neckline.decision_log` 无关**:决策日志退役(⑩-C)在 `api/app.py` +
`neckline/decision_log.py` 两处独立处理,本模块不 import、不读、不写该表。

**D0/D+1 口径**:「当日现役卡」= `buy_date` 的上一交易日(`prev_trading_day`)那天
冻结的篮子——闭环是「D0 盘后扫描形成篮子 → D+1 盘中验证/用户买入」,买入日即 D+1。
查不到当日现役卡 = 独立买入,如实标 `reason`,不臆造来源。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline import user_actions
from neckline.calendar import prev_trading_day
from neckline.db import connection, init_schema
from neckline.review.parse import normalize_ts_code
from neckline.selection import basket_store
from neckline.sentinel import positions as pos_store

logger = logging.getLogger(__name__)

_EPS = 1e-9  # CLAUDE.md 纪律阈值比较容差体例(边界判定同源)

# entry_snapshots 本轮 MVP 刻意不采集的机器可知项(如实披露,不是"没看当成没有"):
# 竞价表现需要读当日 `auction_snapshots` 分区(⑧ 存拍产物,依赖当天是否命中 T1/T2
# 关注池窗口才会有数据);资金流依赖 `report/sector_moneyflow.py::compute_sector_moneyflow`
# 现算(EOD 报告管线专用、非在线廉价点查,买入热路径不适合现算,同 P0-23 纪律精神)。
# 两项都不是"技术上做不到",是本块工期内的范围收窄,登记在完工记录里。
SNAPSHOT_NOT_CAPTURED = ("capital_flow", "auction_performance")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# ══════════════════════════════════════════════════════════════════════════
# 来源篮子查找(只读 baskets / basket_members / basket_cards)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SourceBasketMember:
    """当日现役卡里查到的来源篮子成员引用(找不到 → `find_source_basket_member`
    返回 `None`,由调用方如实标"独立买入")。"""

    basket_id: int
    basket_key: str
    basket_name: str
    driver: str
    tier: int
    role_llm: Optional[str]
    role_mech: Optional[str]
    role_conflict: bool
    card_version: Optional[int]              # None = 有篮子无卡(合法中间态)
    card: Optional[Dict[str, Any]] = None    # 卡正文(card_json 解析后);无卡时 None
    member_entry: Optional[Dict[str, Any]] = None  # 卡里该票的成员节;无卡时 None

    @property
    def role(self) -> Optional[str]:
        """单栏"角色"取值:机械角色优先(Tier 机械分定档同一条"机械优先"精神),
        机械缺席才退 LLM 角色。"""
        return self.role_mech or self.role_llm


def find_source_basket_member(
    ts_code: str, buy_date: date, *, db_path: Optional[Path] = None
) -> Optional[SourceBasketMember]:
    """当日现役卡里查这只票的来源篮子(⑩-A/B「查不到=独立买入如实标」)。

    D0 = `buy_date` 的上一交易日。一票同日理论上只归属一个篮子的主篮(⑤
    `assign_primary` 保证 lift 主归属唯一),但保守起见:多条命中时优先
    `is_primary=1`,再退回 `basket_id` 最小者(确定性 tie-break)。

    **只读,零写入**——不 import 任何 basket_store 写函数,見 `tests/
    test_positions_entry.py` 的 AST 守门。"""
    init_schema(db_path)
    try:
        d0 = prev_trading_day(buy_date)
    except Exception:  # noqa: BLE001 —— 交易日历异常不该掀翻买入主流程
        logger.warning(
            "[positions_entry] 算不出 %s 的上一交易日,视为查无来源篮子", buy_date, exc_info=True
        )
        return None
    d0_str = _d(d0)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT b.id, b.basket_key, b.name, b.driver, b.tier, "
            "bm.role_llm, bm.role_mech, bm.role_conflict, bm.is_primary "
            "FROM basket_members bm JOIN baskets b ON b.id = bm.basket_id "
            "WHERE b.trade_date=? AND bm.ts_code=? "
            "ORDER BY bm.is_primary DESC, b.id ASC",
            (d0_str, ts_code),
        ).fetchall()
    if not rows:
        return None
    r = rows[0]
    basket_id = int(r[0])
    card_row = basket_store.load_basket_card(basket_id, db_path=db_path)  # version=None → 最新版本
    card_version: Optional[int] = None
    card_json: Optional[Dict[str, Any]] = None
    member_entry: Optional[Dict[str, Any]] = None
    if card_row is not None:
        card_version = int(card_row["version"])
        parsed = card_row.get("card")
        if isinstance(parsed, dict):
            card_json = parsed
            for m in parsed.get("members") or []:
                if isinstance(m, dict) and m.get("ts_code") == ts_code:
                    member_entry = m
                    break
    return SourceBasketMember(
        basket_id=basket_id, basket_key=str(r[1]), basket_name=str(r[2]), driver=str(r[3]),
        tier=int(r[4]), role_llm=r[5], role_mech=r[6], role_conflict=bool(r[7]),
        card_version=card_version, card=card_json, member_entry=member_entry,
    )


# ══════════════════════════════════════════════════════════════════════════
# 计划继承(position_plans)
# ══════════════════════════════════════════════════════════════════════════

def _member_plan_fields(member_entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not member_entry:
        return {
            "entry_zone": None, "entry_zone_clamp": "absent",
            "max_chase": None, "max_chase_clamp": "absent",
            "exit_reference": None, "exit_reference_clamp": "absent",
        }
    return {
        "entry_zone": member_entry.get("entry_zone"),
        "entry_zone_clamp": member_entry.get("entry_zone_clamp"),
        "max_chase": member_entry.get("max_chase"),
        "max_chase_clamp": member_entry.get("max_chase_clamp"),
        "exit_reference": member_entry.get("exit_reference"),
        "exit_reference_clamp": member_entry.get("exit_reference_clamp"),
    }


def build_inherited_plan(
    source: Optional[SourceBasketMember],
) -> Tuple[Dict[str, Any], Optional[int], Optional[int]]:
    """从来源篮子卡拼装 `position_plans.plan_json`(⑩-B 五项:建仓观察区间/最高
    追价/离场参考区间/验证与失效条件/主要风险)。返回
    `(plan_json, source_basket_id, source_card_version)`。

    无来源篮子 或 有篮子但卡未就绪(合法中间态,同 ⑦「有篮子无卡」)→ **空计划如实
    标**(`available=False` + `reason`)——`position_plans` 仍会落 version=1 这一行,
    不是省略整条记录。"""
    if source is None:
        return (
            {
                "available": False, "reason": "no_source_basket",
                "source_basket_key": None, "source_basket_name": None, "driver": None,
                "entry_zone": None, "entry_zone_clamp": "absent",
                "max_chase": None, "max_chase_clamp": "absent",
                "exit_reference": None, "exit_reference_clamp": "absent",
                "verification_spec": None, "invalidation_spec": None, "risks": [],
            },
            None, None,
        )
    if source.card is None:
        return (
            {
                "available": False, "reason": "card_not_ready",
                "source_basket_key": source.basket_key, "source_basket_name": source.basket_name,
                "driver": source.driver,
                "entry_zone": None, "entry_zone_clamp": "absent",
                "max_chase": None, "max_chase_clamp": "absent",
                "exit_reference": None, "exit_reference_clamp": "absent",
                "verification_spec": None, "invalidation_spec": None, "risks": [],
            },
            source.basket_id, None,
        )
    plan = {
        "available": True, "reason": None,
        "source_basket_key": source.basket_key, "source_basket_name": source.basket_name,
        "driver": source.driver,
        **_member_plan_fields(source.member_entry),
        "verification_spec": source.card.get("verification_spec"),
        "invalidation_spec": source.card.get("invalidation_spec"),
        "risks": list(source.card.get("risks") or []),
    }
    return plan, source.basket_id, source.card_version


def create_position_plan_v1(
    position_id: int, plan_json: Dict[str, Any], *,
    source_basket_id: Optional[int], source_card_version: Optional[int],
    db_path: Optional[Path] = None,
) -> int:
    """开仓时自动落 `position_plans` version=1(⑩-B)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO position_plans (position_id, version, source_basket_id, "
            "source_card_version, plan_json, note, created_at) VALUES (?,1,?,?,?,?,?)",
            (position_id, source_basket_id, source_card_version,
             json.dumps(plan_json, ensure_ascii=False), None, now),
        )
        return int(cur.lastrowid)


def create_position_plan_version(
    position_id: int, plan_json: Dict[str, Any], *,
    note: Optional[str] = None, db_path: Optional[Path] = None,
) -> int:
    """用户创建计划新版本(⑩-B「用户可创建新版本,新版本不修改原始篮子卡」)。

    `source_basket_id`/`source_card_version` 承袭该持仓已有计划行的来源(新版本仍是
    "从同一张 D0 卡出发的用户修订",不是凭空另起一份来源)。**本函数不写
    `baskets`/`basket_cards` 一字节**——签名里根本没有相关参数,物理上碰不到那两张表。

    HTTP 入口留给 ⑭-B(`POST /positions/{id}/plans`,见 PROJECT_PLAN §五 V2-⑭ 新
    端点清单);本函数是它将调用的领域层实现,⑩ 先把能力建好。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT source_basket_id, source_card_version, MAX(version) FROM position_plans "
            "WHERE position_id=?",
            (position_id,),
        ).fetchone()
    if row is None or row[2] is None:
        raise ValueError(
            f"create_position_plan_version: position_id={position_id} 无既有计划"
            "(缺 version=1)——开仓时应已自动落一行,无法在此基础上创建新版本"
        )
    source_basket_id, source_card_version, max_version = row
    next_version = int(max_version) + 1
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO position_plans (position_id, version, source_basket_id, "
            "source_card_version, plan_json, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (position_id, next_version, source_basket_id, source_card_version,
             json.dumps(plan_json, ensure_ascii=False), note, now),
        )
        return int(cur.lastrowid)


def list_position_plans(position_id: int, db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """某持仓的全部计划版本(升序)。只读,无写副作用。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, position_id, version, source_basket_id, source_card_version, "
            "plan_json, note, created_at FROM position_plans WHERE position_id=? ORDER BY version",
            (position_id,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r[0], "position_id": r[1], "version": r[2],
            "source_basket_id": r[3], "source_card_version": r[4],
            "plan": json.loads(r[5]), "note": r[6], "created_at": r[7],
        })
    return out


def latest_position_plan(position_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    plans = list_position_plans(position_id, db_path=db_path)
    return plans[-1] if plans else None


def evaluate_entry_deviation(buy_price: float, plan_json: Dict[str, Any]) -> Optional[str]:
    """⑩-B:计划价 vs 实际成交价明显偏离 → 提示「原盈亏结构已变」,**不质问、不
    阻断、不进任何判定**(纯展示位)。

    「明显偏离」= 买入价落在卡上建仓观察区间 `[low, high]` 之外(区间本身已是 ⑦
    夹逼闸给出的结果,不在本函数另造一个百分比阈值)。无 `entry_zone`(独立买入 /
    卡未就绪 / 该条本就被夹逼拒收)→ 无从比较,返回 `None`——"没法判断"与"未偏离"
    不是一回事,不可混同。"""
    zone = plan_json.get("entry_zone") if plan_json else None
    if not isinstance(zone, dict):
        return None
    low, high = zone.get("low"), zone.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    if buy_price < low - _EPS or buy_price > high + _EPS:
        return "原盈亏结构已变:实际成交价与建仓观察区间存在偏离(参考,非指令)"
    return None


# ══════════════════════════════════════════════════════════════════════════
# entry_snapshots 冻结
# ══════════════════════════════════════════════════════════════════════════

def _quote_to_dict(quote: Any) -> Optional[Dict[str, Any]]:
    if quote is None:
        return None
    price = getattr(quote, "price", None)
    pre_close = getattr(quote, "pre_close", None)
    chg_pct = None
    if (
        isinstance(price, (int, float)) and isinstance(pre_close, (int, float))
        and pre_close not in (None, 0)
    ):
        chg_pct = round((price - pre_close) / pre_close, 4)
    return {
        "price": price, "pre_close": pre_close, "chg_pct": chg_pct,
        "open": getattr(quote, "open", None), "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None), "volume": getattr(quote, "volume", None),
        "amount": getattr(quote, "amount", None), "ts": getattr(quote, "ts", None),
        "source": getattr(quote, "source", None),
    }


def _lookup_industry(ts_code: str, db_path: Optional[Path]) -> Optional[str]:
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT industry FROM stock_basic WHERE ts_code=?", (ts_code,)
        ).fetchone()
    return row[0] if row and row[0] else None


def _sector_strength_snapshot(
    industry: Optional[str], buy_date: date, db_path: Optional[Path]
) -> Optional[Dict[str, Any]]:
    """板块强度快照(**只读** `report/industry_strength_store.py` 的 EOD 预计算表,
    单日点查,不现算——P0-23 纪律)。取 D0(`buy_date` 上一交易日)那天的行业强度,
    与来源篮子查找同一个 D0 口径。查无该行业 / 未达标(成员数不足)→ `None`。"""
    if not industry:
        return None
    from neckline.report.industry_strength_store import load_industry_strength

    d0 = prev_trading_day(buy_date)
    rows = load_industry_strength(d0, db_path=db_path)
    for r in rows:
        if r.industry == industry:
            return {
                "trade_date": _d(d0), "industry": industry,
                "median_ret": r.median_ret, "industry_rank": r.industry_rank,
                "is_strength_day": r.is_strength_day, "persist_days": r.persist_days,
                "member_count": r.member_count,
            }
    return None


def _build_snapshot_payload(
    ts_code: str, buy_date: date, buy_price: float, qty: int,
    source: Optional[SourceBasketMember], quote: Optional[Any], db_path: Optional[Path],
) -> Dict[str, Any]:
    """拼装 `entry_snapshots.snapshot_json`(机器可知的一切,best-effort)。任何单项
    子取数失败只警告 + 该项落 `None`,不牵连整份快照(核心管线对可选情报输入必须
    包保险丝的极简版)。"""
    snapshot: Dict[str, Any] = {
        "captured_at": _now(),
        "buy_price": buy_price, "qty": qty,
        "quote": None, "quote_unavailable_reason": None,
        "basket": None,
        "sector_strength": None,
        "not_captured": list(SNAPSHOT_NOT_CAPTURED),
    }
    q = _quote_to_dict(quote)
    if q is not None:
        snapshot["quote"] = q
    else:
        snapshot["quote_unavailable_reason"] = "no_realtime_quote"

    industry: Optional[str] = None
    if source is not None:
        member = source.member_entry or {}
        snapshot["basket"] = {
            "found": True,
            "basket_id": source.basket_id, "basket_key": source.basket_key,
            "basket_name": source.basket_name, "driver": source.driver,
            "tier": source.tier, "role_llm": source.role_llm, "role_mech": source.role_mech,
            "role_conflict": source.role_conflict,
            "card_version": source.card_version, "card_ready": source.card is not None,
            "industry": member.get("industry"), "industry_lift": member.get("industry_lift"),
            "k4_tag": member.get("k4_tag"),
        }
        industry = member.get("industry")
    else:
        snapshot["basket"] = {"found": False, "reason": "no_matching_basket_member"}
        try:
            industry = _lookup_industry(ts_code, db_path)
        except Exception:  # noqa: BLE001
            logger.warning(
                "[positions_entry] 查 stock_basic.industry 失败(独立买入行业强度取数跳过)",
                exc_info=True,
            )
            industry = None

    try:
        snapshot["sector_strength"] = _sector_strength_snapshot(industry, buy_date, db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[positions_entry] 板块强度取数失败,该项落 None", exc_info=True)
        snapshot["sector_strength"] = None

    return snapshot


def freeze_entry_snapshot(
    position_id: int, ts_code: str, trade_date: date, snapshot: Dict[str, Any], *,
    basket_id: Optional[int], card_version: Optional[int], tier: Optional[int],
    role: Optional[str], db_path: Optional[Path] = None,
) -> int:
    """冻结一行(`entry_snapshots`,**只 INSERT,永不 UPDATE/DELETE**)。`position_id`
    每次开仓都是全新自增 id,同 key 二次写击穿 `UNIQUE(position_id)` 抛
    `IntegrityError` 是**期望行为**(只可能是编程错误,不该被静默吞掉——同 `basket_
    cards`/`entry_snapshots` 冻结三律的既定判据,见 `tests/test_v2_schema_guard.py`)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
            "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, ts_code, _d(trade_date), basket_id, card_version, tier, role,
             json.dumps(snapshot, ensure_ascii=False), now),
        )
        return int(cur.lastrowid)


# ══════════════════════════════════════════════════════════════════════════
# user_actions 自动记账(⑩-D:buy/sell 服务端自动落)
# ══════════════════════════════════════════════════════════════════════════

def _record_buy_action(
    position_id: int, ts_code: str, buy_date: date, buy_price: float, qty: int,
    source: Optional[SourceBasketMember], db_path: Optional[Path],
) -> None:
    user_actions.record(
        "buy", ts_code=ts_code, position_id=position_id,
        payload={
            "buy_price": buy_price, "qty": qty, "buy_date": _d(buy_date),
            "source_basket_key": source.basket_key if source else None,
        },
        db_path=db_path,
    )


def _record_sell_action(
    position_id: int, ts_code: str, sell_date: date, sell_price: float, qty: int,
    close_reason: Optional[str], db_path: Optional[Path],
) -> None:
    user_actions.record(
        "sell", ts_code=ts_code, position_id=position_id,
        payload={
            "sell_price": sell_price, "qty": qty, "sell_date": _d(sell_date),
            "close_reason": close_reason,
        },
        db_path=db_path,
    )


# ══════════════════════════════════════════════════════════════════════════
# 编排入口(API 与 CLI 共用)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class BuyResult:
    position_id: int
    source_basket_key: Optional[str]
    source_basket_name: Optional[str]
    tier: Optional[int]
    role: Optional[str]
    plan_available: bool
    plan_deviation_notice: Optional[str]


def record_buy(
    ts_code: str, buy_price: float, qty: int, buy_date: date, *,
    note: Optional[str] = None, buy_fees: Optional[float] = None,
    quote: Optional[Any] = None, db_path: Optional[Path] = None,
) -> BuyResult:
    """买入编排入口(⑩-A/B/D)。**买入本身永远成功**——来源篮子查找 / 快照子项
    是 best-effort,任何一项失败都不阻断开仓;仅 `entry_snapshots`/`position_plans`
    两个核心写入(而非其内容的丰富度)是硬保证。

    `quote`:调用方预先解析好的实时报价(或 `None`)——本函数**不自己发起网络
    请求**,免联网依赖便于单测与 CLI/API 共用同一套注入姿势(API 侧走既有
    `_QUOTES_FN` 钩子,CLI 侧自行 best-effort 拉一次)。
    """
    code = normalize_ts_code(ts_code)

    source: Optional[SourceBasketMember] = None
    try:
        source = find_source_basket_member(code, buy_date, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 来源篮子查找失败不该阻断买入
        logger.warning("[positions_entry] 来源篮子查找失败,按独立买入处理", exc_info=True)

    plan_json, source_basket_id, source_card_version = build_inherited_plan(source)
    snapshot = _build_snapshot_payload(code, buy_date, buy_price, qty, source, quote, db_path)

    position_id = pos_store.open_position(
        code, buy_price, qty, buy_date, note=note, buy_fees=buy_fees, db_path=db_path,
    )
    freeze_entry_snapshot(
        position_id, code, buy_date, snapshot,
        basket_id=(source.basket_id if source else None),
        card_version=source_card_version,
        tier=(source.tier if source else None),
        role=(source.role if source else None),
        db_path=db_path,
    )
    create_position_plan_v1(
        position_id, plan_json,
        source_basket_id=source_basket_id, source_card_version=source_card_version,
        db_path=db_path,
    )
    try:
        _record_buy_action(position_id, code, buy_date, buy_price, qty, source, db_path)
    except Exception:  # noqa: BLE001 —— user_actions 记账失败不影响开仓已成功这一事实
        logger.warning("[positions_entry] user_actions 记 buy 失败(不影响开仓已记账)", exc_info=True)

    deviation = evaluate_entry_deviation(buy_price, plan_json)
    return BuyResult(
        position_id=position_id,
        source_basket_key=(source.basket_key if source else None),
        source_basket_name=(source.basket_name if source else None),
        tier=(source.tier if source else None),
        role=(source.role if source else None),
        plan_available=bool(plan_json.get("available")),
        plan_deviation_notice=deviation,
    )


def record_sell(
    position_id: int, sell_price: float, sell_date: date, *,
    close_reason: Optional[str] = None, sell_fees: Optional[float] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """卖出编排入口(⑩-A/D)。返回是否命中该持仓(不存在/已清仓 → `False`,同
    `sentinel.positions.close_position` 既定语义,调用方据此 404)。"""
    position = pos_store.get_position(position_id, db_path=db_path)
    ok = pos_store.close_position(
        position_id, sell_price=sell_price, sell_date=sell_date,
        close_reason=close_reason, sell_fees=sell_fees, db_path=db_path,
    )
    if ok and position is not None:
        try:
            _record_sell_action(
                position_id, position.ts_code, sell_date, sell_price, position.qty,
                close_reason, db_path,
            )
        except Exception:  # noqa: BLE001 —— 同上,记账失败不影响清仓已成功这一事实
            logger.warning("[positions_entry] user_actions 记 sell 失败(不影响清仓已记账)", exc_info=True)
    return ok


__all__ = [
    "SNAPSHOT_NOT_CAPTURED",
    "SourceBasketMember",
    "BuyResult",
    "find_source_basket_member",
    "build_inherited_plan",
    "create_position_plan_v1",
    "create_position_plan_version",
    "list_position_plans",
    "latest_position_plan",
    "evaluate_entry_deviation",
    "freeze_entry_snapshot",
    "record_buy",
    "record_sell",
]
