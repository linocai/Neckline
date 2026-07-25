"""熔断纪律(plan §五 v1.2-A2 / §2.1 第 7 条,🔴 碰纪律章程 + 金额判定)。

**连续 3 笔止损** 或 **单日实现净亏 ≥ 4000 元** → 触发熔断(当日停开新仓、次日只减
不加,完成一次强制复盘后解锁)。系统**无法物理阻止下单** → 本模块只做「触发/解锁
事件留痕 + 派生锁定态」,**纯提醒层,绝不代下单 / 撤单 / 改止损**(§3.8);服务端也
绝不拦 `POST /positions`,只在状态里回锁定态供客户端自律灰化。

**阈值单一源 = 命名常量(住本模块,非 strategy_versions config)**:`CIRCUIT_
CONSECUTIVE_STOPS=3` / `CIRCUIT_DAILY_LOSS_YUAN=4000.0`。理由同 `review/reconcile.py
::FORCED_REVIEW_LOSS_FRAC`——章程拍板的固定政策值、非回测参数,不进大脑、不占 K
命名空间。连续止损判据用到的 `stop_pct` **仍读现役 config**(`brain.active_config`,
不硬编 -5%)。

**两条触发口径(写死,§2.1 第 7 条 / plan A2.3)**:
    · 连续 3 笔止损:全部已平仓持仓按 `(sell_date, id)` 升序,从最近一笔往前数**尾部
      连续**的止损离场笔数(显式 `close_reason=STOP_LOSS`,或 `close_reason` NULL 时
      走价格近似兜底);遇一笔非止损离场即断链归零;尾部连续 ≥3 → 触发。
    · 单日净亏 ≥4000:某 `sell_date` 当日**全部平仓回合的净实现盈亏合计**
      `Σ(sell−buy)×qty`(**净口径,盈亏可互抵**)≤ −4000 → 触发。不含费用(positions
      无费用字段,差异在周复盘对账时收敛)。**净口径的「大赢单遮蔽」缺口由连续 3 笔
      止损触发独立兜住**(后者只认止损链、不看盈亏抵消)。

**离场原因兜底(plan A2.1 ②,近似口径,已标注)**:仅当 `close_reason IS NULL` 时,
熔断评估**近似**判定 `sell_price ≤ buy_price×(1−stop_pct)+_EPS` → 计止损;用户显式
选了非 NULL 码则**信标注、不用价格二次猜**。**兜底只用于熔断计数、绝不回写
`positions.close_reason`**(库里仍 NULL,不臆造历史);归因材料展示时标「近似」。

**锁定态 = 派生**(照 CLAUDE.md「审计时间戳 + 独立消费标记不用一个字段身兼两职」
教训):存在 `unlocked_at IS NULL` 的行即锁定,锁/解锁各自落列;**锁定跨日持续**
(「次日只减不加」),解锁前一直锁定,**无自动时间解锁**。**已锁定时重复触发幂等**
(`evaluate_after_close` 前置查锁定态,不新开第二行)。

**解锁两路径(均复用强制复盘同源,不另造复盘)**:① 客户端「熔断复盘」按钮 →
`unlock(via='review_ack')`;② 周复盘覆盖触发周且走强制复盘口径 →
`auto_unlock_for_reviews`(判据 = `WeeklyReview.forced_review`,即
`reconcile.is_forced_review`,不另起口径)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.db import connection, init_schema
from neckline.sentinel.positions import (
    CLOSE_REASON_STOP_LOSS,
    STATUS_CLOSED,
    load_all_positions,
)

# —— 熔断阈值命名常量(单一源,住本模块;禁止把 3 / 4000 抄进任何别处)——————————————
CIRCUIT_CONSECUTIVE_STOPS = 3
CIRCUIT_DAILY_LOSS_YUAN = 4000.0

# 触发原因码
TRIGGER_CONSECUTIVE_STOPS = "consecutive_stops"
TRIGGER_DAILY_LOSS = "daily_loss"

# 解锁路径码(A2.7)
UNLOCK_VIA_REVIEW_ACK = "review_ack"        # 客户端「熔断复盘」按钮
UNLOCK_VIA_WEEKLY_REVIEW = "weekly_review"  # 周复盘覆盖触发周自动解锁

# 阈值比较浮点容差(CLAUDE.md 记的纪律阈值比较一律加 _EPS,不写裸 >=/<=)。
_EPS = 1e-9

_SELECT_COLS = (
    "id, triggered_at, trigger_reason, trigger_ref_date, basis_json, "
    "unlocked_at, unlocked_via, created_at"
)


@dataclass
class CircuitEpisode:
    id: int
    triggered_at: str
    trigger_reason: str          # consecutive_stops | daily_loss
    trigger_ref_date: str        # 'YYYYMMDD'
    basis: Dict[str, Any] = field(default_factory=dict)  # 判据留痕(诚实边界透出)
    unlocked_at: Optional[str] = None    # None=仍锁定
    unlocked_via: Optional[str] = None
    created_at: str = ""

    @property
    def locked(self) -> bool:
        return self.unlocked_at is None

    @property
    def basis_trades_count(self) -> int:
        return len(self.basis.get("position_ids", []) or [])

    @property
    def basis_window(self) -> str:
        return str(self.basis.get("window", ""))

    @property
    def note(self) -> str:
        return str(self.basis.get("note", ""))


@dataclass
class CircuitState:
    locked: bool
    episode: Optional[CircuitEpisode] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _loads(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def _fmt(d: str) -> str:
    """'YYYYMMDD' → 'YYYY-MM-DD'(诚实边界文案展示用);非法格式原样返回。"""
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d


def _window_str(dates: List[str]) -> str:
    if not dates:
        return ""
    lo, hi = min(dates), max(dates)
    return _fmt(lo) if lo == hi else f"{_fmt(lo)}~{_fmt(hi)}"


def _parse_ref(ref: str) -> Optional[date]:
    try:
        return datetime.strptime(ref, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _row_to_episode(row) -> CircuitEpisode:
    return CircuitEpisode(
        id=row[0], triggered_at=row[1], trigger_reason=row[2], trigger_ref_date=row[3],
        basis=_loads(row[4], {}), unlocked_at=row[5], unlocked_via=row[6], created_at=row[7],
    )


# —— 止损离场判定(显式码优先,NULL 才价格兜底)————————————————————————————————

def _active_stop_pct(db_path: Optional[Path]) -> float:
    """现役 config 的 `stop_pct`(单一事实源,§3.8 铁律,不硬编 -5%)。无现役版本 →
    退回 `MomentumConfig` 字段默认(与 api `_active_config` 同款兜底)。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig

    cfg = brain.active_config(db_path=db_path)
    val = cfg.get("stop_pct")
    return float(val) if val else float(MomentumConfig().stop_pct)


def _is_stop_loss_close(pos, stop_pct: float) -> bool:
    """一笔已平仓是否为「止损离场」。**显式 `close_reason` 非空 → 信用户标注**(只认
    `STOP_LOSS`);**仅当 `close_reason` NULL/空 → 价格近似兜底**(`sell_price ≤
    buy_price×(1−stop_pct)+_EPS`,plan A2.1 ②)。"""
    if pos.close_reason:  # 非空 = 用户显式标注 → 不用价格二次猜
        return pos.close_reason == CLOSE_REASON_STOP_LOSS
    if pos.sell_price is None or pos.buy_price is None or pos.buy_price <= 0:
        return False
    threshold = pos.buy_price * (1.0 - stop_pct)
    return pos.sell_price <= threshold + _EPS


# —— 触发检测(纯函数,便于单测)————————————————————————————————————————————

def _consecutive_note(n: int, approx: int) -> str:
    tail = f",其中 {approx} 笔离场原因未标注、按卖出价近似判定为止损" if approx else ""
    return (
        f"连续 {n} 笔止损离场触发熔断(基于台账 {n} 笔已补录成交{tail});"
        "今日停开新仓、次日只减不加,完成一次强制复盘后解锁。"
    )


def _daily_note(ref_date: str, net: float, k: int) -> str:
    return (
        f"{_fmt(ref_date)} 单日实现净亏 ¥{abs(net):,.0f}(基于台账 {k} 笔已补录成交平仓,"
        f"净口径盈亏互抵后)触发熔断;今日停开新仓、次日只减不加,完成一次强制复盘后解锁。"
    )


def detect_trigger(
    closed_sorted: List, ref_date: str, stop_pct: float
) -> Tuple[Optional[str], Dict[str, Any]]:
    """从(已按 `(sell_date, id)` 升序的)已平仓回合检测熔断触发。返回
    `(trigger_reason | None, basis)`。连续止损优先于单日净亏检测(两者独立,连续止损
    兜住单日净口径的大赢单遮蔽缺口)。纯函数——不碰 DB,便于单测穷举边界。"""
    # ① 连续 3 笔止损(尾部连续,遇非止损断链归零)
    chain: List = []
    for p in reversed(closed_sorted):
        if _is_stop_loss_close(p, stop_pct):
            chain.append(p)
        else:
            break
    if len(chain) >= CIRCUIT_CONSECUTIVE_STOPS:
        chain_asc = list(reversed(chain))  # 时间升序
        approx = sum(1 for p in chain_asc if not p.close_reason)
        basis = {
            "position_ids": [p.id for p in chain_asc],
            "codes": [p.ts_code for p in chain_asc],
            "consecutive_stops": len(chain_asc),
            "approx_count": approx,
            "window": _window_str([p.sell_date for p in chain_asc]),
            "stop_pct": stop_pct,
            "note": _consecutive_note(len(chain_asc), approx),
        }
        return TRIGGER_CONSECUTIVE_STOPS, basis

    # ② 单日实现净亏 ≥ 4000(净口径,盈亏互抵)
    day = [p for p in closed_sorted if p.sell_date == ref_date and p.sell_price is not None]
    if day:
        net = sum((p.sell_price - p.buy_price) * p.qty for p in day)
        if net <= -CIRCUIT_DAILY_LOSS_YUAN + _EPS:
            basis = {
                "position_ids": [p.id for p in day],
                "codes": [p.ts_code for p in day],
                "daily_net_pnl": round(net, 2),
                "window": _fmt(ref_date),   # 'YYYY-MM-DD'(basisWindow 展示口径)
                "note": _daily_note(ref_date, net, len(day)),
            }
            return TRIGGER_DAILY_LOSS, basis

    return None, {}


# —— 读 ——————————————————————————————————————————————————————————————————

def get_episode(episode_id: int, db_path: Optional[Path] = None) -> Optional[CircuitEpisode]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM circuit_breaker WHERE id=?", (episode_id,)
        ).fetchone()
    return _row_to_episode(row) if row else None


def current_locked_episode(db_path: Optional[Path] = None) -> Optional[CircuitEpisode]:
    """当前仍锁定的触发行(`unlocked_at IS NULL`,取最新一条;幂等保证至多一条锁定)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM circuit_breaker WHERE unlocked_at IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_episode(row) if row else None


def is_locked(db_path: Optional[Path] = None) -> bool:
    return current_locked_episode(db_path=db_path) is not None


def get_state(db_path: Optional[Path] = None) -> CircuitState:
    """权威锁定态(供 `GET /circuit` 与 `PositionsOut.circuit`)。锁定 → 带当前触发
    episode;未锁定 → episode=None。"""
    ep = current_locked_episode(db_path=db_path)
    return CircuitState(locked=ep is not None, episode=ep)


def list_episodes(db_path: Optional[Path] = None) -> List[CircuitEpisode]:
    """全部熔断事件(触发时刻降序),供归因/审计与测试用。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLS} FROM circuit_breaker ORDER BY id DESC"
        ).fetchall()
    return [_row_to_episode(r) for r in rows]


# —— 触发(折进 close_position 服务路径)——————————————————————————————————————

def evaluate_after_close(
    sell_date: date, db_path: Optional[Path] = None
) -> Optional[CircuitEpisode]:
    """录入卖出后评估熔断(`app.py::close_position` 端点里调,尽力而为)。越过任一
    阈值且当前**未锁定** → 建触发行并返回该 episode(调用方据此推送);已锁定 →
    幂等返回 None(不新开第二行);未触发 → None。"""
    init_schema(db_path)
    if is_locked(db_path=db_path):
        return None  # 已锁定,幂等不新开第二行
    stop_pct = _active_stop_pct(db_path)
    closed = [
        p for p in load_all_positions(db_path=db_path)
        if p.status == STATUS_CLOSED and p.sell_date and p.sell_price is not None
    ]
    closed.sort(key=lambda p: (p.sell_date, p.id))

    reason, basis = detect_trigger(closed, _d(sell_date), stop_pct)
    if reason is None:
        return None

    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO circuit_breaker "
            "(triggered_at, trigger_reason, trigger_ref_date, basis_json, unlocked_at, unlocked_via, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (now, reason, _d(sell_date), json.dumps(basis, ensure_ascii=False), None, None, now),
        )
        new_id = int(cur.lastrowid)
    return get_episode(new_id, db_path=db_path)


# —— 解锁(两路径,均复用强制复盘同源)————————————————————————————————————————

def unlock(via: str = UNLOCK_VIA_REVIEW_ACK, db_path: Optional[Path] = None) -> bool:
    """解锁当前所有仍锁定的触发行(幂等保证至多一条,防御性解锁全部)。返回是否有
    行被解锁(全无锁定 → False,客户端「已是解锁态」)。主路径:客户端熔断复盘按钮
    (`via='review_ack'`)。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE circuit_breaker SET unlocked_at=?, unlocked_via=? WHERE unlocked_at IS NULL",
            (now, via),
        )
        return cur.rowcount > 0


def auto_unlock_for_reviews(reviews, db_path: Optional[Path] = None) -> int:
    """周复盘自动解锁(plan A2.7 自动路径)。对每个**走了强制复盘口径**
    (`WeeklyReview.forced_review is True`,即 `reconcile.is_forced_review` 同源、
    不另造)的 ISO 周,若其 `[week_start, week_end]` 覆盖某**仍锁定**触发行的
    `trigger_ref_date` → 该行置 `unlocked_at` + `unlocked_via='weekly_review'`。
    返回被自动解锁的行数。非强制复盘周即使覆盖触发日也不解锁(必须真走了强制复盘)。"""
    init_schema(db_path)
    forced_weeks = [(r.week_start, r.week_end) for r in reviews if getattr(r, "forced_review", False)]
    if not forced_weeks:
        return 0
    now = _now()
    unlocked = 0
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, trigger_ref_date FROM circuit_breaker WHERE unlocked_at IS NULL"
        ).fetchall()
        for cid, ref in rows:
            ref_d = _parse_ref(ref)
            if ref_d is None:
                continue
            if any(ws <= ref_d <= we for ws, we in forced_weeks):
                conn.execute(
                    "UPDATE circuit_breaker SET unlocked_at=?, unlocked_via=? WHERE id=?",
                    (now, UNLOCK_VIA_WEEKLY_REVIEW, cid),
                )
                unlocked += 1
    return unlocked


__all__ = [
    "CIRCUIT_CONSECUTIVE_STOPS",
    "CIRCUIT_DAILY_LOSS_YUAN",
    "TRIGGER_CONSECUTIVE_STOPS",
    "TRIGGER_DAILY_LOSS",
    "UNLOCK_VIA_REVIEW_ACK",
    "UNLOCK_VIA_WEEKLY_REVIEW",
    "CircuitEpisode",
    "CircuitState",
    "detect_trigger",
    "get_episode",
    "current_locked_episode",
    "is_locked",
    "get_state",
    "list_episodes",
    "evaluate_after_close",
    "unlock",
    "auto_unlock_for_reviews",
]
