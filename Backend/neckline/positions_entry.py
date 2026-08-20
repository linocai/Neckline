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
import sqlite3
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
# 现算(EOD 报告管线专用、非在线廉价点查,买入热路径不适合现算,同 P0-23 纪律精神);
# 换手率/量比是**归一化**指标(换手率=成交量/流通股本,量比=成交量/历史均量),
# `quote` 只带 `data.realtime.Quote` 原生的**绝对量**(`volume`/`amount`,单位手/元),
# 归一化需要额外拉 `daily_basic`(流通股本/历史均量基准)——同样是买入热路径不适合
# 现拉的重活。三项都不是"技术上做不到",是本块工期内的范围收窄,登记在完工记录里。
SNAPSHOT_NOT_CAPTURED = ("capital_flow", "auction_performance", "turnover_rate", "volume_ratio")


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
    card_version: Optional[int]              # None = 有篮子无卡(合法中间态);有卡行(含损坏)则为该行版本号
    card: Optional[Dict[str, Any]] = None    # 卡正文(card_json 解析后);无卡**或卡损坏**时均为 None
    member_entry: Optional[Dict[str, Any]] = None  # 卡里该票的成员节;无卡时 None
    # `card is None` 时用它分流「没有行」vs「有行但读不出」(basket_store 唯一检测点
    # `_decode_card_json` 的判定结果原样转发,⛔ 不在本模块另写一份判据)。B1 同类:
    # 见 `build_inherited_plan`。
    card_corrupt: bool = False

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
    # `load_basket_card` 是「这张卡读不读得出」的**唯一检测点**(basket_store._decode_
    # card_json,B1 裁定);读到损坏行时它已经打过 ERROR 了,这里只管原样转发
    # `card_corrupt`,⛔ 不重新判一遍(同 `api/app.py::get_basket_card` 的复用姿势)。
    card_row = basket_store.load_basket_card(basket_id, db_path=db_path)  # version=None → 最新版本
    card_version: Optional[int] = None
    card_json: Optional[Dict[str, Any]] = None
    member_entry: Optional[Dict[str, Any]] = None
    card_corrupt = False
    if card_row is not None:
        card_version = int(card_row["version"])
        card_corrupt = bool(card_row.get("card_corrupt"))
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
        card_corrupt=card_corrupt,
    )


# ══════════════════════════════════════════════════════════════════════════
# 计划继承(position_plans)
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# ⑪-D-B 闸②:take_profit kind 的开仓武装判定
# ══════════════════════════════════════════════════════════════════════════
# 2026-08-03 planner 裁定(V2 review 判定线 🟡-3 收口,PROJECT_PLAN §五 ⑪-D)。
# ⑦ 的卡生成闸(`basket_card.clamp_exit_reference`,要求 `exit_low > D0 close`)只
# 保证「这个数在 D0 收盘之上」;买入价可以远高于 D0 收盘(次日高开追进去),继承下来
# 的 `exit_low` 因此**仍可能 ≤ 你的成本**。此时「触达离场参考」要么开盘立刻响、要么
# 在亏损位响,两种都是噪声 —— 故**该票的 `take_profit` kind 不武装**。
#
# ⛔ 边界(plan 逐字):**只是不武装那一条推送** —— 不改计划内容(`exit_reference`
# 原样继承、照常展示)、不阻断开仓、不影响任何其他 kind。
ARM_REASON_NO_EXIT_REFERENCE = "no_exit_reference"   # 计划里压根没有离场参考(无来源篮子 / 卡未就绪 / 被 ⑦ 夹逼拒收)
ARM_REASON_BELOW_ENTRY_PRICE = "below_entry_price"   # 闸②:exit_low ≤ 实际成交价
ARM_REASON_USER_MUTED = "user_muted"                 # ⑮ per-position「不提醒」开关(本块只留读写点,开关本身归 ⑮)

_ARM_NOTE_TEXT: Dict[str, str] = {
    ARM_REASON_NO_EXIT_REFERENCE: "本次没有离场参考,本票不做触达提醒",
    ARM_REASON_BELOW_ENTRY_PRICE: "离场参考低于你的成本,本票不做触达提醒",
    ARM_REASON_USER_MUTED: "你已关闭本票的触达提醒",
}


def exit_reference_arm_note(reason: Optional[str]) -> Optional[str]:
    """未武装理由的人读文案(**单一源**,与 `basket_card.clamp_reason_text` 同体例
    ——不由客户端/渲染层各自拍文案)。`reason is None`(已武装)→ `None`。"""
    return None if reason is None else _ARM_NOTE_TEXT.get(reason, reason)


def evaluate_exit_reference_arming(
    exit_reference: Any, buy_price: Optional[float], *, muted: bool = False
) -> Tuple[bool, Optional[str]]:
    """⑪-D-B 闸②:这笔仓的 `take_profit` kind 该不该武装。返回 `(armed, reason)`,
    `armed=True` 时 `reason is None`。**纯函数**(不碰 DB / 不看行情),供开仓与
    ⑮ 的 per-position 开关共用同一套语义。

    判定顺序(刻意如此):① 用户关了 → `user_muted`(用户意图优先于一切机械判定,
    ⛔ 不因为"数字其实挺合理"就替用户重新打开);② 计划里没有离场参考 → `no_exit_
    reference`(**「没有」与「不合格」分开落码**,项目一贯纪律);③ `exit_low ≤ 实际
    成交价` → `below_entry_price`;④ 否则武装。

    `buy_price` 缺失或非正 → 与 ③ 同样**不武装**(理由仍记 `below_entry_price`?
    不 —— 记 `no_exit_reference` 也不对)。这里的处置:**按闸②未通过处理**,理由
    `below_entry_price`,因为"比不出来"与"比出来不合格"对推送的后果一样(§2.8-C-3
    前提②「该数值已过机械 sanity 闸,未过 → 不武装」——**没比过 = 没过**)。实务上
    `buy_price` 永远来自用户提交的成交价,走不到这一支。"""
    if muted:
        return False, ARM_REASON_USER_MUTED
    low = exit_reference.get("low") if isinstance(exit_reference, dict) else None
    if not isinstance(low, (int, float)) or isinstance(low, bool) or low <= 0:
        return False, ARM_REASON_NO_EXIT_REFERENCE
    if not isinstance(buy_price, (int, float)) or isinstance(buy_price, bool) or buy_price <= 0:
        return False, ARM_REASON_BELOW_ENTRY_PRICE
    if float(low) <= float(buy_price) + _EPS:
        return False, ARM_REASON_BELOW_ENTRY_PRICE
    return True, None


def _arm_fields(exit_reference: Any, buy_price: Optional[float], *, muted: bool) -> Dict[str, Any]:
    """武装态三件套(**engine 旁路 E 读 `exit_reference_armed`,⑮ 读写
    `exit_reference_muted`**)。三个键在 `plan_json` 里恒存在,不搞"缺键即默认"。"""
    armed, reason = evaluate_exit_reference_arming(exit_reference, buy_price, muted=muted)
    return {
        "exit_reference_armed": armed,
        "exit_reference_armed_reason": reason,
        "exit_reference_armed_note": exit_reference_arm_note(reason),
        "exit_reference_muted": bool(muted),
    }


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
    source: Optional[SourceBasketMember], *, buy_price: Optional[float],
) -> Tuple[Dict[str, Any], Optional[int], Optional[int]]:
    """从来源篮子卡拼装 `position_plans.plan_json`(⑩-B 五项:建仓观察区间/最高
    追价/离场参考区间/验证与失效条件/主要风险)。返回
    `(plan_json, source_basket_id, source_card_version)`。

    无来源篮子 或 有篮子但卡未就绪(合法中间态,同 ⑦「有篮子无卡」)→ **空计划如实
    标**(`available=False` + `reason`)——`position_plans` 仍会落 version=1 这一行,
    不是省略整条记录。

    **卡损坏是第三态,与「卡未就绪」分得开**(2026-08-04,B1 同类裁定):`source.
    card_corrupt=True`(basket_store 唯一检测点判定,见 `SourceBasketMember` 字段
    注释)→ `reason="card_corrupt"`,⛔ 不降格成 `card_not_ready`——那张卡是冻结件、
    `INSERT OR IGNORE` 永不覆盖,坏了就是永久坏的;当成"未就绪"处理会让继承计划
    永远显示"还没生成"而那张卡这辈子不会来。此态下 `source_card_version` 仍如实
    回填该行的版本号(**不是** `None`)—— 与「没有行」区分开,方便定位坏在哪一版。

    `buy_price`(⑪-D-B 闸②,2026-08-03):**必填关键字**,不给默认 —— 闸②是红线闸,
    "忘了传"等于静默把 `take_profit` kind 武装给一个没比过成交价的数字。三条空计划
    路径同样落武装态三件套(值必为未武装 + `no_exit_reference`),**键恒存在**。"""
    # V2.2-③-E:交易资格四件套(K8 §十一)在**继承时校验**——判定唯一实现在
    # `basket_card.member_trade_plan_missing`(与 T1 必要条件同一份判据,不各写)。
    # 三键语义:`trade_plan_complete` True=四件齐 / False=有缺件 / None=无来源计划
    # 可验(独立买入 —— 「没法验」不是「验过了不齐」,⛔ 不混);缺件清单
    # `trade_plan_missing`;警示文案(⛔ 不是拦截)由 `plan_incomplete_notice()` 给。
    from neckline.selection.basket_card import member_trade_plan_missing

    if source is None:
        return (
            {
                "available": False, "reason": "no_source_basket",
                "source_basket_key": None, "source_basket_name": None, "driver": None,
                "entry_zone": None, "entry_zone_clamp": "absent",
                "max_chase": None, "max_chase_clamp": "absent",
                "exit_reference": None, "exit_reference_clamp": "absent",
                **_arm_fields(None, buy_price, muted=False),
                "verification_spec": None, "invalidation_spec": None, "risks": [],
                "trade_plan_complete": None, "trade_plan_missing": [],
            },
            None, None,
        )
    if source.card is None:
        # 「没有行」(card_not_ready)与「有行但读不出」(card_corrupt)分流——判据原样
        # 转发自 basket_store 的唯一检测点(`source.card_corrupt`),⛔ 本函数不重判。
        missing = member_trade_plan_missing(None, None)
        return (
            {
                "available": False,
                "reason": "card_corrupt" if source.card_corrupt else "card_not_ready",
                "source_basket_key": source.basket_key, "source_basket_name": source.basket_name,
                "driver": source.driver,
                "entry_zone": None, "entry_zone_clamp": "absent",
                "max_chase": None, "max_chase_clamp": "absent",
                "exit_reference": None, "exit_reference_clamp": "absent",
                **_arm_fields(None, buy_price, muted=False),
                "verification_spec": None, "invalidation_spec": None, "risks": [],
                "trade_plan_complete": False, "trade_plan_missing": missing,
            },
            source.basket_id, source.card_version,
        )
    member_fields = _member_plan_fields(source.member_entry)
    missing = member_trade_plan_missing(source.card, source.member_entry)
    plan = {
        "available": True, "reason": None,
        "source_basket_key": source.basket_key, "source_basket_name": source.basket_name,
        "driver": source.driver,
        **member_fields,
        **_arm_fields(member_fields.get("exit_reference"), buy_price, muted=False),
        "verification_spec": source.card.get("verification_spec"),
        "invalidation_spec": source.card.get("invalidation_spec"),
        "risks": list(source.card.get("risks") or []),
        "trade_plan_complete": not missing, "trade_plan_missing": missing,
    }
    return plan, source.basket_id, source.card_version


def plan_incomplete_notice(plan_json: Optional[Dict[str, Any]]) -> Optional[str]:
    """四件套缺件的**警示**文案(⑩ 开仓响应 + ⑮ 客户端展示共用;⛔ 不是拦截 ——
    系统只审计不代下单,§3.8)。`trade_plan_complete` 缺键(老计划,建于 V2.2-③
    之前)或为 `None`(无来源计划可验)→ `None`,不拿今天的判据追认历史。"""
    if not plan_json or plan_json.get("trade_plan_complete") is not False:
        return None
    from neckline.selection.basket_card import trade_plan_missing_label

    label = trade_plan_missing_label(list(plan_json.get("trade_plan_missing") or []))
    return label or "次日交易预案不完整(缺件清单未记录)"


def create_position_plan_v1(
    position_id: int, plan_json: Dict[str, Any], *,
    source_basket_id: Optional[int], source_card_version: Optional[int],
    db_path: Optional[Path] = None, conn: Optional[sqlite3.Connection] = None,
) -> int:
    """开仓时自动落 `position_plans` version=1(⑩-B)。

    `conn`(🟡 Y7):同 `freeze_entry_snapshot` —— 给了就并进调用方的事务。这一行**必须
    与开仓同生共死**:`create_position_plan_version` 见到无 v1 会直接 `ValueError`,
    「有仓无 v1」是个走不出去的死局。"""
    now = _now()
    sql = ("INSERT INTO position_plans (position_id, version, source_basket_id, "
           "source_card_version, plan_json, note, created_at) VALUES (?,1,?,?,?,?,?)")
    row = (position_id, source_basket_id, source_card_version,
           json.dumps(plan_json, ensure_ascii=False), None, now)
    if conn is not None:
        return int(conn.execute(sql, row).lastrowid)
    init_schema(db_path)
    with connection(db_path) as own:
        return int(own.execute(sql, row).lastrowid)


def create_position_plan_version(
    position_id: int, plan_json: Dict[str, Any], *,
    note: Optional[str] = None, db_path: Optional[Path] = None,
) -> int:
    """用户创建计划新版本(⑩-B「用户可创建新版本,新版本不修改原始篮子卡」)。

    `source_basket_id`/`source_card_version` 承袭该持仓已有计划行的来源(新版本仍是
    "从同一张 D0 卡出发的用户修订",不是凭空另起一份来源)。**本函数不写
    `baskets`/`basket_cards` 一字节**——签名里根本没有相关参数,物理上碰不到那两张表。

    **武装态由本函数重算,不由调用方说了算(⑪-D-B 闸②)**:新版本里的 `exit_
    reference` 是用户改过的数字,必须**重新过一遍闸②**(拿这笔仓的真实成交价比),
    否则"写个新版本"就成了绕开红线闸的后门。用户意图那一半(`exit_reference_muted`)
    **承袭上一版**,除非本次 `plan_json` 里显式给了该键(⑮ 的开关正是这样翻它)。

    HTTP 入口留给 ⑭-B(`POST /positions/{id}/plans`,见 PROJECT_PLAN §五 V2-⑭ 新
    端点清单);本函数是它将调用的领域层实现,⑩ 先把能力建好。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT source_basket_id, source_card_version, MAX(version) FROM position_plans "
            "WHERE position_id=?",
            (position_id,),
        ).fetchone()
        prev_row = conn.execute(
            "SELECT plan_json FROM position_plans WHERE position_id=? "
            "ORDER BY version DESC LIMIT 1",
            (position_id,),
        ).fetchone()
        price_row = conn.execute(
            "SELECT buy_price FROM positions WHERE id=?", (position_id,)
        ).fetchone()
    if row is None or row[2] is None:
        raise ValueError(
            f"create_position_plan_version: position_id={position_id} 无既有计划"
            "(缺 version=1)——开仓时应已自动落一行,无法在此基础上创建新版本"
        )
    source_basket_id, source_card_version, max_version = row
    next_version = int(max_version) + 1

    prev_muted = False
    if prev_row is not None:
        try:
            prev_muted = bool((json.loads(prev_row[0]) or {}).get("exit_reference_muted"))
        except (json.JSONDecodeError, TypeError):
            logger.warning("[positions_entry] position_id=%s 上一版计划解不出,"
                           "本次静音位按未静音处理", position_id)
    muted = bool(plan_json.get("exit_reference_muted", prev_muted))
    buy_price = price_row[0] if price_row is not None else None
    plan_json = {
        **plan_json,
        **_arm_fields(plan_json.get("exit_reference"), buy_price, muted=muted),
    }

    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO position_plans (position_id, version, source_basket_id, "
            "source_card_version, plan_json, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (position_id, next_version, source_basket_id, source_card_version,
             json.dumps(plan_json, ensure_ascii=False), note, now),
        )
        return int(cur.lastrowid)


def set_exit_reference_muted(
    position_id: int, muted: bool, *, db_path: Optional[Path] = None,
) -> int:
    """⑮ per-position「不提醒」开关的**服务端写入点**(⑪-D-D 列为 ⑮ 应做项,本块
    只把读写点留好、不做 UI 与端点)。落法 = 在最新计划之上**追加一个新版本**
    (`position_plans` 是版本化只增表,⛔ 不就地改历史行),武装态由
    `create_position_plan_version` 重算。返回新版本行的 rowid。

    ⚠ 只翻静音位,**不动计划正文任何一项** —— 用户说的是「这只票的这个数不靠谱,
    别烦我」,不是「改我的计划」。"""
    latest = latest_position_plan(position_id, db_path=db_path)
    if latest is None:
        raise ValueError(
            f"set_exit_reference_muted: position_id={position_id} 无既有计划"
            "(缺 version=1)——开仓时应已自动落一行"
        )
    plan = dict(latest["plan"] or {})
    plan["exit_reference_muted"] = bool(muted)
    return create_position_plan_version(
        position_id, plan,
        note=("用户关闭本票触达提醒" if muted else "用户恢复本票触达提醒"),
        db_path=db_path,
    )


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


def load_entry_snapshot(position_id: int, db_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读某持仓的建仓冻结快照(⑩-A,`UNIQUE(position_id)` 使一仓一行)。只读,无写副作用。

    `None` = **这笔仓没有快照行**(该仓建于 ⑩ 之前,或写入当时整段失败)——⛔ 不是
    「快照是空的」;`snapshot_json.not_captured` 才是「这次采集里哪几项没采到」。
    键保持 **snake_case**(领域形状),转 camel 是 API 层的事。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            "SELECT position_id, ts_code, trade_date, basket_id, card_version, tier, role, "
            "snapshot_json, created_at FROM entry_snapshots WHERE position_id=?",
            (int(position_id),),
        ).fetchone()
    if r is None:
        return None
    try:
        snapshot = json.loads(r[7]) if r[7] else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("[positions_entry] position_id=%s 的 snapshot_json 解不出,按空 dict 读回",
                       position_id)
        snapshot = {}
    return {
        "position_id": int(r[0]), "ts_code": str(r[1]), "trade_date": str(r[2]),
        "basket_id": r[3], "card_version": r[4], "tier": r[5], "role": r[6],
        "snapshot": snapshot, "created_at": str(r[8]),
    }


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
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """冻结一行(`entry_snapshots`,**只 INSERT,永不 UPDATE/DELETE**)。`position_id`
    每次开仓都是全新自增 id,同 key 二次写击穿 `UNIQUE(position_id)` 抛
    `IntegrityError` 是**期望行为**(只可能是编程错误,不该被静默吞掉——同 `basket_
    cards`/`entry_snapshots` 冻结三律的既定判据,见 `tests/test_v2_schema_guard.py`)。

    `conn`(🟡 Y7):给了就复用调用方的 connection(不自开事务、不 commit),供
    `record_buy` 把三段核心写入并成一个事务;不给就自开自提交,行为与本参数加入前等价。"""
    now = _now()
    sql = ("INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
           "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)")
    row = (position_id, ts_code, _d(trade_date), basket_id, card_version, tier, role,
           json.dumps(snapshot, ensure_ascii=False), now)
    if conn is not None:
        return int(conn.execute(sql, row).lastrowid)
    init_schema(db_path)
    with connection(db_path) as own:
        return int(own.execute(sql, row).lastrowid)


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
    # 🟡 Y7:`True` = 这次请求**没有开新仓**,是同一个 `idempotency_key` 的重放
    # (返回的是那笔既有仓的结果)。如实透出,别让"看起来成功了"掩盖"其实什么都没发生"。
    replayed: bool = False
    # V2.2-③-E:四件套缺件警示(None = 四件齐或无来源计划可验;⛔ 不是拦截)。
    plan_incomplete_notice: Optional[str] = None


def find_position_by_idempotency_key(
    key: str, *, db_path: Optional[Path] = None,
) -> Optional[int]:
    """按幂等键查已开的仓(🟡 Y7)。查无 → `None`。空键一律当"没给"(不参与去重)。"""
    if not key:
        return None
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM positions WHERE idempotency_key=?", (key,)
        ).fetchone()
    return int(row[0]) if row else None


def replay_buy(position_id: int, *, db_path: Optional[Path] = None) -> BuyResult:
    """把一笔**已经开好**的仓重放成 `BuyResult`(🟡 Y7 幂等重放)。

    信息全部取自开仓当时**冻结**下来的两行(`entry_snapshots` + `position_plans` v1),
    ⛔ 不重新查来源篮子、不重新拉行情 —— 重放要还原的是「那一刻记下了什么」,现在再查
    一遍等于给同一个 position_id 编第二套来源,那才是真的说谎。查不到冻结行(理论上
    不该发生,除非是幂等键加入之前开的老仓)→ 各项如实留空,不编。
    """
    init_schema(db_path)
    with connection(db_path) as conn:
        snap = conn.execute(
            "SELECT tier, role, snapshot_json FROM entry_snapshots WHERE position_id=?",
            (position_id,),
        ).fetchone()
        plan_row = conn.execute(
            "SELECT plan_json FROM position_plans WHERE position_id=? AND version=1",
            (position_id,),
        ).fetchone()
    basket: Dict[str, Any] = {}
    if snap is not None:
        try:
            basket = (json.loads(snap[2]) or {}).get("basket") or {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("[positions_entry] position_id=%s 的开仓快照解不出,重放按空来源",
                           position_id)
    plan_json: Dict[str, Any] = {}
    if plan_row is not None:
        try:
            plan_json = json.loads(plan_row[0]) or {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("[positions_entry] position_id=%s 的计划 v1 解不出,重放按空计划",
                           position_id)
    return BuyResult(
        position_id=position_id,
        source_basket_key=basket.get("basket_key"),
        source_basket_name=basket.get("basket_name"),
        tier=(snap[0] if snap is not None else None),
        role=(snap[1] if snap is not None else None),
        plan_available=bool(plan_json.get("available")),
        # ⚠ 偏离提示**不重放**:它是「这一笔成交价 vs 计划区间」的比较结论,而重放这次
        # 请求根本没有成交。原样重算会拿新请求的价去比旧计划,像是在评价一笔并不存在的
        # 交易;留空 = 「本次没有新的成交可评」,如实。
        plan_deviation_notice=None,
        # 四件套缺件警示**照重放**:它是开仓当时冻在计划 v1 里的事实,不随时间变。
        plan_incomplete_notice=plan_incomplete_notice(plan_json),
        replayed=True,
    )


def record_buy(
    ts_code: str, buy_price: float, qty: int, buy_date: date, *,
    note: Optional[str] = None, buy_fees: Optional[float] = None,
    quote: Optional[Any] = None, db_path: Optional[Path] = None,
    idempotency_key: Optional[str] = None,
) -> BuyResult:
    """买入编排入口(⑩-A/B/D)。**买入本身永远成功**——来源篮子查找 / 快照子项
    是 best-effort,任何一项失败都不阻断开仓;仅 `entry_snapshots`/`position_plans`
    两个核心写入(而非其内容的丰富度)是硬保证。

    `quote`:调用方预先解析好的实时报价(或 `None`)——本函数**不自己发起网络
    请求**,免联网依赖便于单测与 CLI/API 共用同一套注入姿势(API 侧走既有
    `_QUOTES_FN` 钩子,CLI 侧自行 best-effort 拉一次)。

    **三段核心写入并进一个事务(契约线审计 🟡 Y7,2026-08-03)**:`open_position` →
    `freeze_entry_snapshot` → `create_position_plan_v1` 以前是三条独立连接、各自提交。
    保险丝只包了「快照内容丰富度」,**三个写入本身裸奔**:开仓成功之后任何一步抛异常 →
    API 返 500,而**仓已经落库了**;客户端按 500 重试 = **重复开仓**。同时留下「有仓无
    快照 / 有仓无计划 v1」的中间态,而 `create_position_plan_version` 见到无 v1 直接
    `ValueError` —— 那是个走不出去的死局。现在三写同 `with connection(...)`:一起成功,
    或者一起没发生(`connection()` 只在正常退出 commit,异常路径 close 即弃)。
    `user_actions` 记账**留在事务外**维持 best-effort(它是审计流水,不该有权回滚开仓)。

    `idempotency_key`(🟡 Y7):非空时同键二次调用**不开第二笔仓**,直接重放那笔既有仓的
    结果(`BuyResult.replayed=True`)。两道闸:应用层先查(快路径)+ `positions` 上的
    部分唯一索引(真闸,挡并发)。⛔ 不传键 = 不设防,与本参数加入前逐字节等价 ——
    CLI 与历史补录本来就不该被幂等键约束。
    """
    code = normalize_ts_code(ts_code)

    if idempotency_key:
        existing = find_position_by_idempotency_key(idempotency_key, db_path=db_path)
        if existing is not None:
            logger.info("[positions_entry] 幂等键 %s 命中既有持仓 %s,重放上次结果、不开新仓",
                        idempotency_key, existing)
            return replay_buy(existing, db_path=db_path)

    source: Optional[SourceBasketMember] = None
    try:
        source = find_source_basket_member(code, buy_date, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 来源篮子查找失败不该阻断买入
        logger.warning("[positions_entry] 来源篮子查找失败,按独立买入处理", exc_info=True)

    plan_json, source_basket_id, source_card_version = build_inherited_plan(
        source, buy_price=buy_price)
    snapshot = _build_snapshot_payload(code, buy_date, buy_price, qty, source, quote, db_path)

    init_schema(db_path)
    try:
        with connection(db_path) as conn:
            position_id = pos_store.open_position(
                code, buy_price, qty, buy_date, note=note, buy_fees=buy_fees,
                conn=conn, idempotency_key=idempotency_key or None,
            )
            freeze_entry_snapshot(
                position_id, code, buy_date, snapshot,
                basket_id=(source.basket_id if source else None),
                card_version=source_card_version,
                tier=(source.tier if source else None),
                role=(source.role if source else None),
                conn=conn,
            )
            create_position_plan_v1(
                position_id, plan_json,
                source_basket_id=source_basket_id, source_card_version=source_card_version,
                conn=conn,
            )
    except sqlite3.IntegrityError:
        # 并发同键:两条请求都没查到、都进来插,第二条被部分唯一索引挡下(**库级闸才是
        # 真闸**,上面那次查询只是快路径)。此时整个事务已回滚,库里只有第一条那笔仓 ——
        # 重放它,而不是把 IntegrityError 抛成 500 让客户端再重试一轮。
        existing = find_position_by_idempotency_key(idempotency_key or "", db_path=db_path)
        if existing is None:
            raise           # 不是幂等键撞车 → 是真的写坏了,不许吞
        logger.info("[positions_entry] 幂等键 %s 并发撞车,重放既有持仓 %s",
                    idempotency_key, existing)
        return replay_buy(existing, db_path=db_path)

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
        plan_incomplete_notice=plan_incomplete_notice(plan_json),
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


# ══════════════════════════════════════════════════════════════════════════
#  连续止损纯提醒(V2.2-⑤-B 第 9 项;API 与 CLI 两个清仓入口共用**同一段**编排)
# ══════════════════════════════════════════════════════════════════════════

# 看板事件的落点(`sentinel_events`):sentinel='circuit'(⑤-B 第 6 项刻意复用既有名字,
# 不新增 kind / 不新增 sentinel 类型),ts_code = 刚清仓那只票,event_key 固定。
# ⚠ **不是市场级空 ts_code** —— 空 ts_code 会被 `GET /board` 的市场级过滤吞掉(那条过滤
# 只给退潮黄色预警开了口子),而且锚在具体那笔卖出上,同一天第 3、第 4 笔各留一条,
# 与「第 4 笔再提醒一条」的语义天然对齐(`INSERT OR IGNORE` 按票去重,不会同票刷屏)。
CONSECUTIVE_STOPS_SENTINEL = "circuit"
CONSECUTIVE_STOPS_EVENT_KEY = "consecutive_stops"


def notice_consecutive_stops_after_close(
    position_id: int, *, sell_date: date, db_path: Optional[Path] = None,
) -> Optional[int]:
    """清仓之后的**连续止损纯提醒**(裁定 #8;熔断三件机制已全删)。

    达到 `circuit.CIRCUIT_CONSECUTIVE_STOPS` → **一条看板事件 + 一条推送**,返回当前尾部
    连续止损笔数;未达阈值返回该笔数(不推);出任何错返回 `None`。

    🔴 **零状态铁律(§五 〇b-7)**:本函数**不建任何"熔断行"、不落任何锁定标志、不改
    `POST /positions/{id}/close` 的返回值语义**。用户原话:「我不需要你替我做决定;这个
    程序永远是提醒」。⛔ 不许在这里(或任何地方)补一个灰化位 / 「建议今天别开仓」的
    自动状态位。⛔ 也别发明"解锁后才重推"——没有锁,就没有重置。

    **尽力而为**:提醒是旁路,任何异常一律吞掉 + WARNING,**绝不阻断清仓已记账这一事实**
    (承 v1.2-A2 F.3 的既有纪律)。"""
    try:
        from neckline.api import notify
        from neckline.sentinel import circuit
        from neckline.dedup import record_pushed

        count = circuit.count_tail_consecutive_stops(db_path=db_path)
        if count < circuit.CIRCUIT_CONSECUTIVE_STOPS:
            return count
        position = pos_store.get_position(position_id, db_path=db_path)
        ts_code = position.ts_code if position is not None else ""
        body = (
            f"连续 {count} 笔以止损离场(基于台账 {count} 笔已补录成交)。"
            f"这是一条提醒,系统不改变任何设置、也不替你做决定。"
        )
        try:
            record_pushed(
                sell_date, CONSECUTIVE_STOPS_SENTINEL, ts_code, CONSECUTIVE_STOPS_EVENT_KEY,
                payload={"body": body, "consecutive_stops": count}, db_path=db_path,
            )
        except Exception:  # noqa: BLE001 —— 看板事件落库失败不该吃掉那条推送
            logger.warning("[positions_entry] 连续止损看板事件落库失败(已吞,推送照发)", exc_info=True)
        try:
            notify.push_consecutive_stops_notice(count, ts_code=ts_code, db_path=db_path)
        except Exception:  # noqa: BLE001
            logger.warning("[positions_entry] 连续止损提醒推送失败(已吞,看板事件已留痕)", exc_info=True)
        logger.warning("⚠ 连续 %d 笔止损离场(纯提醒,系统零动作)", count)
        return count
    except Exception:  # noqa: BLE001 —— 提醒异常绝不能掀翻清仓主流程
        logger.warning("[positions_entry] 连续止损提醒评估异常(已吞,不影响清仓已记账)", exc_info=True)
        return None


__all__ = [
    "SNAPSHOT_NOT_CAPTURED",
    "CONSECUTIVE_STOPS_SENTINEL",
    "CONSECUTIVE_STOPS_EVENT_KEY",
    "notice_consecutive_stops_after_close",
    "ARM_REASON_NO_EXIT_REFERENCE",
    "ARM_REASON_BELOW_ENTRY_PRICE",
    "ARM_REASON_USER_MUTED",
    "SourceBasketMember",
    "BuyResult",
    "find_source_basket_member",
    "build_inherited_plan",
    "plan_incomplete_notice",
    "evaluate_exit_reference_arming",
    "exit_reference_arm_note",
    "create_position_plan_v1",
    "create_position_plan_version",
    "set_exit_reference_muted",
    "list_position_plans",
    "latest_position_plan",
    "load_entry_snapshot",
    "evaluate_entry_deviation",
    "freeze_entry_snapshot",
    "record_buy",
    "record_sell",
]
