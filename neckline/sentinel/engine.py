"""单拍编排(plan 阶段3)。`run_tick(now, ...)` 是哨兵一次轮询要做的全部事情:
组装关注池 → 批量拉价 → 四哨兵判定 → 防重 → 推送 → 落账。`scripts/sentinel.py`
的主循环只是反复调用本函数;`scripts/smoke_sentinel.py` 也调用同一个函数(喂
合成的历史日行情)——**同一份编排代码**,不是"正式跑一套、测试再写一套"。

**原则守护(§2.4 铁律,写进编排顺序本身,不是靠人记住)**:
    1. 退潮哨兵先判——一旦当日已触发红色刹车(`sentinel_events` 表里已有
       `(trade_date,"retreat","","brake")`,不论是今天哪一拍触发的),买点哨兵
       **本拍直接跳过**,不产生任何新的开仓许可信号,即便某只候选这一刻价量结构
       确实满足条件。持仓哨兵与证伪哨兵不受影响——管理已有仓位的风险、和把
       已经变坏的候选标记"剔除勿进",在退潮当日依然是有意义的信息。
    2. 拉不到行情(quotes 缺该票)→ 对应哨兵该票直接跳过(已在各哨兵纯函数
       内部处理,`quote=None` 时返回 None),不是"当没发生"而是"没有意见"。
    3. 无候选(报告未生成)/ 无持仓 都是合法状态,只是那部分哨兵没有对象可判,
       不报错。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.report.candidates import Candidate
from neckline.report.sectors import load_member_map
from neckline.sentinel import basket_verify, capture
from neckline.sentinel.channels import (
    LEVEL_CRITICAL,
    LEVEL_INFO,
    LEVEL_WARN,
    PushChannel,
    default_channels,
    push_all,
)
from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.entry import EntrySignal, check_entry
from neckline.sentinel.holding import HoldingAlert, evaluate_holding
from neckline.sentinel.intraday import is_intraday_now
from neckline.sentinel.invalidation import InvalidationSignal, check_invalidation
from neckline.sentinel.positions import Position
from neckline.sentinel.quotes import Quote, get_quotes
from neckline.sentinel.retreat import (
    SAME_TIME_WINDOW_MIN,
    MarketBreadthSnapshot,
    RetreatAlert,
    RetreatMetrics,
    compute_breadth_snapshot,
    evaluate_retreat,
)
from neckline.sentinel.retreat_store import (
    load_prev_tick_triggered,
    load_same_time_zaban_baseline,
    record_retreat_metrics,
)
from neckline.sentinel.universe import (
    DEFAULT_BREADTH_CAP,
    WatchUniverse,
    load_prev5_avg_volume,
    load_stock_meta,
    load_watch_universe,
)
from neckline.strategy import brain

logger = logging.getLogger(__name__)

_DEFAULT_STOP_PCT = 0.05  # 仅当大脑无现役版本(异常状态)时的兜底,不是本模块拍的值

# 退潮"重启后首拍不触发红色"保守闸(修法2)。记录本进程已跑过退潮判定的交易日;
# 某日的首拍(含进程午间重启后的首拍)`allow_red=False`——只允许黄色,不落全天
# 闩锁的红色刹车。理由:红色是高危不可逆动作(全天禁开新仓),重启后的首拍上一拍
# 内存态不可信,宁可延后一拍(60s)也不误闩。跨日续跑时每日首拍同样保守(当日首拍
# 本就无"上一拍"可对,且首拍即 ≥2 条同拍属极端,延后一拍代价可忽略)。
_RETREAT_WARMED_DATES: set = set()


def _consume_retreat_first_tick(trade_date: date) -> bool:
    """返回本进程内该交易日是否为**首拍**(True→本拍红色降级为黄色)。有副作用:
    调用即把该日标记为已热身。测试可用 `reset_retreat_process_state()` 复位。"""
    key = trade_date.strftime("%Y%m%d")
    first = key not in _RETREAT_WARMED_DATES
    _RETREAT_WARMED_DATES.add(key)
    return first


def reset_retreat_process_state() -> None:
    """清空"已热身交易日"记忆(单测隔离用;等价于进程刚重启)。"""
    _RETREAT_WARMED_DATES.clear()


@dataclass
class TickResult:
    trade_date: date
    now: datetime
    skipped_non_trading: bool = False
    report_found: bool = False
    watched_codes: int = 0
    quotes_fetched: int = 0
    # —— V2-⑧ 旁路(存拍 + 篮子验证)的观测位。**它们不参与任何纪律判定**,只是让
    #    冒烟脚本 / 日志看得见旁路有没有在跑;任一为 0 都不影响四哨兵与熔断。————
    captured_ticks: int = 0
    basket_states: Dict[int, str] = field(default_factory=dict)   # basket_id -> 本拍状态
    basket_rows_written: int = 0
    retreat_active: bool = False
    retreat_alert: Optional[RetreatAlert] = None       # 仅红色刹车时非空(驱动 APNs/通道推送)
    retreat_warning: Optional[str] = None              # 黄色预警文案(只进看板,不推送)
    breadth_snapshot: Optional[MarketBreadthSnapshot] = None
    entry_signals: List[EntrySignal] = field(default_factory=list)
    invalidation_signals: List[InvalidationSignal] = field(default_factory=list)
    holding_alerts: List[HoldingAlert] = field(default_factory=list)
    pushed_events: List[str] = field(default_factory=list)
    skipped_duplicate: int = 0


def _candidate_return(quote: Optional[Quote]) -> Optional[float]:
    if quote is None or not quote.pre_close:
        return None
    return quote.price / quote.pre_close - 1


def _hot_sector_peer_returns(candidates: List[Candidate], quotes: Dict[str, Quote]) -> List[float]:
    """退潮哨兵「主线板块跳水」的样本——关注池里恰好命中"今日热门板块"标签的
    候选(§2.2 板块热度),用它们已经拉到的行情算盘中收益率,不额外拉价。"""
    rets: List[float] = []
    for c in candidates:
        if not c.hot_sectors:
            continue
        r = _candidate_return(quotes.get(c.ts_code))
        if r is not None:
            rets.append(r)
    return rets


def _position_sector_peer_returns(
    position: Position, quotes: Dict[str, Quote], member_map: Dict[str, List[str]]
) -> List[float]:
    """持仓哨兵「所属板块跳水预警」的样本——关注池里与本持仓共享至少一个概念
    板块标签的其它票(不含自己),用其已拉到的行情算收益率。`member_map` 为空 /
    本票无板块归属 → 空列表(诚实"无数据",不是"板块健康")。"""
    own_sectors = set(member_map.get(position.ts_code, []))
    if not own_sectors:
        return []
    rets: List[float] = []
    for code, quote in quotes.items():
        if code == position.ts_code:
            continue
        other_sectors = member_map.get(code)
        if not other_sectors or not own_sectors.intersection(other_sectors):
            continue
        r = _candidate_return(quote)
        if r is not None:
            rets.append(r)
    return rets


def _historical_peak_close(position: Position, trade_date: date, parquet_dir: Optional[Path]) -> float:
    """持仓自买入日至今(不含今日,今日未收盘)的收盘价峰值。用真实原始收盘价
    (非前复权)——持仓的 `buy_price` 是真实成交价,两者同锚,不引入复权系数
    错配;短持有期(母战法2-5日)内跨除权的概率也很低。"""
    from neckline.calendar import prev_trading_day
    from neckline.data.market_data import get_stock_history

    buy_date = datetime.strptime(position.buy_date, "%Y%m%d").date()
    end = prev_trading_day(trade_date)
    if buy_date > end:
        return position.buy_price  # 今天就是买入日,尚无"此前"历史可算峰值
    hist = get_stock_history(position.ts_code, buy_date, end, table="daily", parquet_dir=parquet_dir)
    if hist.is_empty():
        return position.buy_price
    return float(hist["close"].max())


def run_tick(
    now: datetime,
    *,
    channels: Optional[List[PushChannel]] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
) -> TickResult:
    """跑一拍。`quotes_fn` 可覆盖(默认 `sentinel.quotes.get_quotes`)——冒烟脚本
    用它注入"某历史日的合成盘中快照",不改一行编排逻辑。"""
    trade_date = now.date()
    if not is_intraday_now(now):
        return TickResult(trade_date=trade_date, now=now, skipped_non_trading=True)

    channels = channels if channels is not None else default_channels()
    fetch = quotes_fn or (lambda codes: get_quotes(codes))

    wu: WatchUniverse = load_watch_universe(trade_date, breadth_cap=breadth_cap, db_path=db_path, parquet_dir=parquet_dir)
    quotes = fetch(wu.codes) if wu.codes else {}
    meta = load_stock_meta(wu.codes, db_path=db_path) if wu.codes else {}
    prev5 = load_prev5_avg_volume(wu.codes, trade_date, parquet_dir=parquet_dir) if wu.codes else {}

    result = TickResult(
        trade_date=trade_date, now=now, report_found=wu.report_found,
        watched_codes=len(wu.codes), quotes_fetched=len(quotes),
    )

    # —— V2-⑧ 旁路 A:盘中存拍(内存累计,15:05 才落盘)——————————————————————
    # **零额外网络**(用的就是上面这一拍已经拉到的 `quotes`),**独立 try**:存拍出任何
    # 问题都只 WARNING,四哨兵与熔断的判定路径一行不动(⑧-B/⑧-D)。
    try:
        result.captured_ticks = capture.record_intraday_tick(trade_date, now, quotes)
    except Exception:  # noqa: BLE001
        logger.warning("盘中存拍本拍失败(已吞,不影响哨兵判定)", exc_info=True)

    # —— V2-⑧ 旁路 B:篮子验证状态机(D+1 验证,判据 = ⑦ 冻结卡里的 spec)——————
    # ⚠ 语义红线:它**只回答「D0 那份驱动假设今天成不成立」**,⛔ 不触发任何交易动作、
    # 不进推送、不改任何持仓判定(⑦-b / ⑧-C2)。同样独立 try。
    try:
        vres = basket_verify.run_intraday_verification(
            trade_date, quotes, attempted_codes=wu.codes, now=now,
            db_path=db_path, baskets=wu.baskets,
        )
        result.basket_states = dict(vres.states)
        result.basket_rows_written = vres.rows_written
    except Exception:  # noqa: BLE001
        logger.warning("篮子验证本拍失败(已吞,不影响哨兵判定)", exc_info=True)

    def _maybe_push(
        sentinel: str, ts_code: str, event_key: str, title: str, body: str, level: str,
        payload_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if already_pushed(trade_date, sentinel, ts_code, event_key, db_path=db_path):
            result.skipped_duplicate += 1
            return
        delivered = push_all(channels, title, body, level=level)
        payload = {"body": body, "delivered": delivered}
        if payload_extra:
            payload.update(payload_extra)
        record_pushed(trade_date, sentinel, ts_code, event_key, payload=payload, db_path=db_path)
        result.pushed_events.append(f"{sentinel}:{ts_code or '-'}:{event_key}")

    # —— 1) 退潮哨兵(先判,决定买点哨兵是否本拍抑制;双级制见 retreat.py 模块头)——
    hhmm = now.strftime("%H%M")
    breadth_snapshot = compute_breadth_snapshot(trade_date, quotes, meta)
    result.breadth_snapshot = breadth_snapshot
    hot_peer_rets = _hot_sector_peer_returns(wu.candidates, quotes)
    hot_avg = (sum(hot_peer_rets) / len(hot_peer_rets)) if hot_peer_rets else None
    metrics = RetreatMetrics(
        trade_date=trade_date, hhmm=hhmm, sample_size=breadth_snapshot.sample_size,
        limit_up_count=breadth_snapshot.limit_up_count, limit_down_count=breadth_snapshot.limit_down_count,
        zaban_count=breadth_snapshot.zaban_count, zaban_rate=breadth_snapshot.zaban_rate,
        hot_sector_avg_chg=hot_avg,
    )

    retreat_active = already_pushed(trade_date, "retreat", "", "brake", db_path=db_path)
    if retreat_active:
        # 当日已闩锁红色:仍逐拍落指标(审计连续性 / 成绩单),不再判级、不再推送。
        record_retreat_metrics(metrics, triggered=[], tier="red_latched", red_via=[], db_path=db_path)
    else:
        prev_triggered = load_prev_tick_triggered(trade_date, hhmm, db_path=db_path)
        baseline = load_same_time_zaban_baseline(
            trade_date, hhmm, window_min=SAME_TIME_WINDOW_MIN, db_path=db_path
        )
        first_tick = _consume_retreat_first_tick(trade_date)
        decision = evaluate_retreat(
            breadth_snapshot,
            now_time=now.time(),
            same_time_zaban_baseline=baseline,
            hot_sector_avg_chg=hot_avg,
            hot_sector_sample=len(hot_peer_rets),
            prev_tick_triggered=prev_triggered,
            allow_red=not first_tick,
        )
        record_retreat_metrics(
            metrics, triggered=decision.triggered, tier=decision.tier,
            red_via=decision.red_via, db_path=db_path,
        )
        event_payload = {
            "metrics": metrics.metric_payload(),
            "triggered": decision.triggered,
            "red_via": decision.red_via,
        }
        if decision.is_red:
            retreat_active = True
            result.retreat_alert = RetreatAlert(reasons=decision.reasons)
            _maybe_push(
                "retreat", "", "brake",
                "退潮刹车:今日计划作废、禁开新仓",
                decision.reason_text, LEVEL_CRITICAL, payload_extra=event_payload,
            )
        elif decision.is_yellow:
            # 黄色预警:只落看板事件(event_key="warn",一天首次),不推送、不抑制买点。
            result.retreat_warning = decision.reason_text
            if not already_pushed(trade_date, "retreat", "", "warn", db_path=db_path):
                record_pushed(
                    trade_date, "retreat", "", "warn",
                    payload={"body": "【黄色预警】" + decision.reason_text, **event_payload},
                    db_path=db_path,
                )
    result.retreat_active = retreat_active

    # v1.1-C.2「自选票享候选同级待遇」:买点/证伪哨兵对候选与「昨晚体检已触发
    # 买点」的自选票一视同仁——两者的 entry_spec/invalidation_spec 都是昨晚写死
    # 的,盘中只读不重算(§2.4 铁律)。退潮哨兵的板块联动样本(`_hot_sector_peer_
    # returns`)刻意只用 `wu.candidates`,不纳入自选(见 `universe.py` 模块头
    # 注释「四类哨兵」不含退潮)。
    entry_pool = wu.candidates + wu.watchlist_candidates

    # —— 2) 买点哨兵(退潮生效时本拍整体跳过,不逐票判断)——————————————————
    if not retreat_active:
        for c in entry_pool:
            sig = check_entry(c, quotes.get(c.ts_code), prev5.get(c.ts_code, 0.0), now)
            if sig is not None:
                result.entry_signals.append(sig)
                _maybe_push(
                    "entry", c.ts_code, "trigger",
                    f"买点确认:{c.name}({c.ts_code})", sig.reason, LEVEL_INFO,
                )

    # —— 3) 证伪哨兵(不受退潮抑制——"剔除勿进"任何时候都是有效信息)——————————
    for c in entry_pool:
        inv = check_invalidation(c, quotes.get(c.ts_code), prev5.get(c.ts_code, 0.0), now)
        if inv is not None:
            result.invalidation_signals.append(inv)
            _maybe_push(
                "invalidation", c.ts_code, "trigger",
                f"剔除勿进:{c.name}({c.ts_code})", inv.reason_text, LEVEL_WARN,
            )

    # —— 4) 持仓哨兵(不受退潮抑制——管理已有仓位任何时候都要做)——————————————
    if wu.positions:
        active_rule = brain.get_active(db_path=db_path)
        stop_pct = _DEFAULT_STOP_PCT
        take_profit_retrace = None
        if active_rule is not None:
            cfg = active_rule.rule.get("config", {}) or {}
            stop_pct = cfg.get("stop_pct") or _DEFAULT_STOP_PCT
            take_profit_retrace = cfg.get("take_profit_retrace")
        else:
            logger.warning("策略大脑无现役版本,持仓哨兵止损线退回兜底值 %.0f%%(非正常状态)", stop_pct * 100)

        member_map = load_member_map(parquet_dir=parquet_dir)
        _level_by_key = {"stop_approach": LEVEL_CRITICAL, "take_profit": LEVEL_INFO, "sector_dive": LEVEL_WARN}
        for p in wu.positions:
            peak = _historical_peak_close(p, trade_date, parquet_dir)
            peer_rets = _position_sector_peer_returns(p, quotes, member_map)
            alert = evaluate_holding(
                p, quotes.get(p.ts_code), stop_pct=stop_pct, take_profit_retrace=take_profit_retrace,
                historical_peak_close=peak, peer_returns=peer_rets,
            )
            if alert.triggered:
                result.holding_alerts.append(alert)
                for key, reason in alert.alerts.items():
                    _maybe_push(
                        "holding", p.ts_code, key,
                        f"持仓提醒:{p.ts_code}", reason, _level_by_key.get(key, LEVEL_INFO),
                    )

    return result


__all__ = ["TickResult", "run_tick"]
