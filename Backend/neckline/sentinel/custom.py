"""自然语言临时提醒的**确定性执行器**(plan §五 V2-⑪-C:「执行归确定性哨兵,
`sentinel/` 内新增 `custom.py`,读 `rule_json`;**LLM 不参与执行**」)。

**这个模块看不见 LLM,也看不见用户原话**:它只读 `custom_alerts.rule_json`(已经过
`neckline/custom_alerts.py::normalize_rule` 白名单校验的结构化规则)。`nl_text` 一个
字都不参与判定 —— §2.8-C 第 2 条「LLM 产出的自由文本一律不进哨兵判据」的物理落地。

**⛔ 永不自动交易**(§3.8 铁律 + ⑪-C 验收条款):本模块**零下单 / 撤单 / 改止损**
调用 —— 它能做的全部事情就是「算出某条规则今天这一拍成不成立」,以及把成立这件事
交给上层去通知。`tests/test_sentinel_custom.py` 有 AST 守门:本文件不许出现任何
交易动作符号,也不许 import 任何券商 / 下单模块(本项目根本没有那种模块,守门是为
了保证以后也不会有人往这里加)。

**三值判定(True / False / 数据不可得)**,不是两值:

    · 某条件所需的数据这一拍拿不到(拉不到行情、量比还在早盘窗口、该票没有持仓
      所以算不出相对成本…)→ 该条件是 **`None`**,不是 `False`。
    · `logic=all`:任一 `False` → 整条 `False`;否则任一 `None` → 整条 `None`(不判)。
    · `logic=any`:任一 `True` → 整条 `True`;否则任一 `None` → 整条 `None`(不判)。

  「没有」与「没看」必须分得开(§铁律):把缺数据当成"条件不满足"会让用户以为系统
  在盯着,其实那一拍根本没数据。不判就是不判,不假装判过。

**冷却 / 去重 / 防重沿用 `sentinel_events` 台账**(⑪-B 原文):`sentinel='custom_alert'`,
`event_key='alert{id}#{第几次}'` —— 每次命中一行(带 `pushed_at`),因此:

    · **同一条提醒不重复轰炸** 靠 `max_fires`(默认 1)与 `fired_count` 对比;
    · **冷却** 靠台账里该提醒最后一行的 `pushed_at`;
    · 每次命中的序号不同 ⇒ 不与 `UNIQUE(trade_date, sentinel, ts_code, event_key)`
      打架(那条唯一约束在这里的作用是「同一次命中不会因为重跑落两行」)。

**推送不在这里发**:与既有四哨兵同一套分工 —— 本模块只判定,`engine.run_tick` 统一
负责推送 + 落台账 + 记 `fired_count`(它才知道推送到底成没成)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline import custom_alerts as store
from neckline.calendar import CN_TZ
from neckline.db import connection, init_schema
from neckline.sentinel.attention import PEER_MIN_SAMPLE, PEER_WEAK_RET, intraday_return
from neckline.sentinel.intraday import elapsed_trading_minutes, intraday_vol_ratio
from neckline.sentinel.positions import Position
from neckline.data.realtime import Quote

logger = logging.getLogger(__name__)

# `sentinel_events.sentinel` 列的取值(台账里这一族事件的名字)。
SENTINEL_NAME = "custom_alert"

_EPS = 1e-9


@dataclass(frozen=True)
class CustomAlertHit:
    alert: store.CustomAlert
    condition_text: str                 # 人读描述(由结构化规则生成,不是 LLM 文本)
    event_key: str                      # 台账 event_key('alert{id}#{n}')
    values: Dict[str, Any] = field(default_factory=dict)   # 各条件的实测值(审计)


@dataclass
class CustomEvalResult:
    hits: List[CustomAlertHit] = field(default_factory=list)
    expired_ids: List[int] = field(default_factory=list)     # 本拍被翻成 expired 的
    # 逐条提醒的「本拍为什么没命中」(审计 / 冒烟可读;不进推送)
    skipped: Dict[int, str] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# 比较器(一律带 _EPS 容差,CLAUDE.md 体例)
# ══════════════════════════════════════════════════════════════════════════

def compare(value: float, op: str, threshold: float) -> bool:
    if op == store.OP_GE:
        return value >= threshold - _EPS
    if op == store.OP_LE:
        return value <= threshold + _EPS
    if op == store.OP_GT:
        return value > threshold + _EPS
    if op == store.OP_LT:
        return value < threshold - _EPS
    raise ValueError(f"未登记的比较符 {op!r}")     # normalize_rule 已挡,这里是双保险


# ══════════════════════════════════════════════════════════════════════════
# 指标取值(拿不到一律 None,**绝不 or 0.0 兜底**)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MetricContext:
    """算指标要用到的一整拍上下文(全部由调用方传入,本模块不自己拉价、不联网)。"""

    now: datetime
    quotes: Dict[str, Quote]
    avg_cost: Dict[str, float]           # ts_code -> 加权平均持仓成本(只含 open 仓)
    prev5_avg_volume: Dict[str, float]   # ts_code -> 前5日均量(手)
    basket_members: Dict[str, Tuple[str, ...]]  # ts_code -> 其来源篮子的成员代码


def _avg_cost_map(positions: Sequence[Position]) -> Dict[str, float]:
    """按股数加权的平均持仓成本(一票可分批多次开仓 → 必须加权,不能取最后一笔)。"""
    agg: Dict[str, Tuple[float, int]] = {}
    for p in positions:
        amt, qty = agg.get(p.ts_code, (0.0, 0))
        agg[p.ts_code] = (amt + float(p.buy_price) * int(p.qty), qty + int(p.qty))
    return {c: (a / q) for c, (a, q) in agg.items() if q > 0}


def metric_value(
    metric: str, cond: Mapping[str, Any], ts_code: Optional[str], ctx: MetricContext
) -> Optional[float]:
    """算一个指标当前的值。**拿不到 → `None`**(见模块头三值判定)。"""
    q = ctx.quotes.get(ts_code) if ts_code else None
    if metric == store.METRIC_PRICE:
        return float(q.price) if q is not None and q.price > 0 else None
    if metric == store.METRIC_CHG_PCT:
        return intraday_return(q)
    if metric == store.METRIC_VS_COST:
        if q is None or q.price <= 0 or not ts_code:
            return None
        cost = ctx.avg_cost.get(ts_code)
        if not cost or cost <= 0:
            return None      # 没有该票的持仓 → 算不出"相对成本",不猜一个成本出来
        return q.price / cost - 1.0
    if metric == store.METRIC_FROM_DAY_HIGH:
        if q is None or q.high <= 0 or q.price <= 0:
            return None
        return q.price / q.high - 1.0    # 恒 ≤ 0(见 custom_alerts 的口径注释)
    if metric == store.METRIC_VOLUME_RATIO:
        if q is None or not ts_code:
            return None
        base = ctx.prev5_avg_volume.get(ts_code, 0.0)
        ratio, note = intraday_vol_ratio(
            float(q.volume), float(base), elapsed_trading_minutes(ctx.now)
        )
        return ratio if note in ("ok", "closed") else None   # early / no_base → 不判
    if metric == store.METRIC_INDEX_CHG_PCT:
        return intraday_return(ctx.quotes.get(str(cond.get("ref") or "")))
    if metric == store.METRIC_BASKET_WEAK_RATIO:
        if not ts_code:
            return None
        members = ctx.basket_members.get(ts_code) or ()
        rets = [r for r in (intraday_return(ctx.quotes.get(m)) for m in members if m != ts_code)
                if r is not None]
        if len(rets) < PEER_MIN_SAMPLE:
            return None      # 样本不足 → 不判(不是"占比 0")
        return sum(1 for r in rets if r <= PEER_WEAK_RET + _EPS) / len(rets)
    return None      # normalize_rule 已挡未知 metric;这里保守返回"不可得"


def evaluate_rule(
    rule: Mapping[str, Any], ts_code: Optional[str], ctx: MetricContext
) -> Tuple[Optional[bool], Dict[str, Any]]:
    """三值判定一条规则,返回 `(True/False/None, 各条件实测值)`。"""
    conds = list(rule.get("conditions") or [])
    if not conds:
        return None, {}
    logic = str(rule.get("logic") or store.LOGIC_ALL)
    values: Dict[str, Any] = {}
    results: List[Optional[bool]] = []
    for i, c in enumerate(conds):
        metric = str(c.get("metric") or "")
        v = metric_value(metric, c, ts_code, ctx)
        key = f"{i}:{metric}"
        values[key] = v
        if v is None:
            results.append(None)
            continue
        results.append(compare(v, str(c["op"]), float(c["value"])))
    if logic == store.LOGIC_ALL:
        if any(r is False for r in results):
            return False, values
        if any(r is None for r in results):
            return None, values
        return True, values
    # LOGIC_ANY
    if any(r is True for r in results):
        return True, values
    if any(r is None for r in results):
        return None, values
    return False, values


# ══════════════════════════════════════════════════════════════════════════
# 冷却台账(沿用 sentinel_events)
# ══════════════════════════════════════════════════════════════════════════

def last_fired_at(alert_id: int, db_path: Optional[Path] = None) -> Optional[datetime]:
    """该提醒最近一次命中的时刻(UTC aware);从未命中 → `None`。

    读 `sentinel_events`(⑪-B 指定的台账),**跨交易日**查 —— `persist=1` 的长期提醒
    的冷却不该在零点被重置。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(pushed_at) FROM sentinel_events WHERE sentinel=? AND event_key LIKE ?",
            (SENTINEL_NAME, f"alert{int(alert_id)}#%"),
        ).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        dt = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    from datetime import timezone as _tz
    return dt if dt.tzinfo else dt.replace(tzinfo=_tz.utc)


def _cooldown_ok(alert: store.CustomAlert, now: datetime, db_path: Optional[Path]) -> bool:
    if alert.cooldown_seconds <= 0:
        return True
    last = last_fired_at(alert.id, db_path=db_path)
    if last is None:
        return True
    now_utc = now if now.tzinfo else now.astimezone()
    return (now_utc - last).total_seconds() >= alert.cooldown_seconds - _EPS


def next_event_key(alert: store.CustomAlert) -> str:
    """本次命中在台账里的 `event_key`(见模块头:序号让每次命中各占一行)。"""
    return f"alert{alert.id}#{alert.fired_count + 1}"


# ══════════════════════════════════════════════════════════════════════════
# 一拍编排(engine.run_tick 在独立 try 里调它)
# ══════════════════════════════════════════════════════════════════════════

def evaluate_alerts(
    now: datetime,
    *,
    quotes: Dict[str, Quote],
    positions: Sequence[Position],
    prev5_avg_volume: Optional[Dict[str, float]] = None,
    basket_members: Optional[Dict[str, Tuple[str, ...]]] = None,
    db_path: Optional[Path] = None,
) -> CustomEvalResult:
    """跑一拍全部 active 提醒。

    顺序:**先把到期的翻掉**(收盘自动失效这条安全要求),再逐条判 —— 顺序反了会让
    一条刚过期的提醒在本拍还响一次。

    `now` 允许 naive(哨兵主循环传的就是本地 naive `datetime.now()`);本函数按**北京
    时间**解读它(市场时刻口径,CLAUDE.md 定案:调用方传入的 naive 时刻按北京时间读)。"""
    result = CustomEvalResult()
    now_cn = now if now.tzinfo else now.replace(tzinfo=CN_TZ)
    try:
        result.expired_ids = store.expire_due(now_cn, db_path=db_path)
    except Exception:  # noqa: BLE001 —— 翻状态失败不该让本拍全部提醒罢工
        logger.warning("[custom] 到期失效处理失败(本拍继续判活跃提醒)", exc_info=True)

    alerts = store.list_alerts(status=store.STATUS_ACTIVE, db_path=db_path)
    if not alerts:
        return result

    ctx = MetricContext(
        now=now,
        quotes=quotes,
        avg_cost=_avg_cost_map(positions),
        prev5_avg_volume=dict(prev5_avg_volume or {}),
        basket_members=dict(basket_members or {}),
    )
    for a in alerts:
        if not store.in_active_window(a, now_cn):
            result.skipped[a.id] = "outside_active_window"
            continue
        if a.max_fires > 0 and a.fired_count >= a.max_fires:
            result.skipped[a.id] = "max_fires_reached"
            continue
        if not _cooldown_ok(a, now_cn, db_path):
            result.skipped[a.id] = "cooldown"
            continue
        if not a.rule:
            result.skipped[a.id] = "empty_rule"      # rule_json 坏了(已 warning 过)
            continue
        verdict, values = evaluate_rule(a.rule, a.ts_code, ctx)
        if verdict is None:
            result.skipped[a.id] = "insufficient_data"
            continue
        if verdict is False:
            result.skipped[a.id] = "not_met"
            continue
        result.hits.append(CustomAlertHit(
            alert=a, condition_text=store.describe_rule(a.rule),
            event_key=next_event_key(a), values=values,
        ))
    return result


def build_basket_member_map(sources: Mapping[int, Any], positions: Sequence[Position]) -> Dict[str, Tuple[str, ...]]:
    """`ts_code -> 其来源篮子的成员代码`(供 `basket_weak_ratio` 指标用)。

    输入的 `sources` 是 `attention.load_position_sources()` 的产物(**复用,不另查一遍
    库**);一票多笔持仓时取第一笔的来源(⑤ 的主归属唯一,理论上都一样)。"""
    out: Dict[str, Tuple[str, ...]] = {}
    for p in positions:
        src = sources.get(int(p.id))
        if src is None or p.ts_code in out:
            continue
        out[p.ts_code] = tuple(getattr(src, "member_codes", ()) or ())
    return out


def subject_text(alert: store.CustomAlert, quotes: Mapping[str, Quote]) -> str:
    """推送正文里的「谁」。有行情就带上名字(免费源自带 `name`),没有就用代码;
    大盘级提醒说「大盘」。"""
    if not alert.ts_code:
        return "大盘"
    q = quotes.get(alert.ts_code)
    name = getattr(q, "name", "") if q is not None else ""
    return f"{name}({alert.ts_code})" if name else alert.ts_code


def trade_date_of(now: datetime) -> date:
    """台账落行用的交易日(北京时间的那一天)。"""
    now_cn = now if now.tzinfo else now.replace(tzinfo=CN_TZ)
    return now_cn.astimezone(CN_TZ).date()


__all__ = [
    "SENTINEL_NAME", "CustomAlertHit", "CustomEvalResult", "MetricContext",
    "compare", "metric_value", "evaluate_rule", "evaluate_alerts",
    "last_fired_at", "next_event_key", "build_basket_member_map", "subject_text",
    "trade_date_of",
]
