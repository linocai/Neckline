"""单拍编排(plan 阶段3)。`run_tick(now, ...)` 是哨兵一次轮询要做的全部事情:
组装关注池 → 批量拉价 → 四哨兵判定 → 防重 → 推送 → 落账。`scripts/sentinel.py`
的主循环只是反复调用本函数;`scripts/smoke_sentinel.py` 也调用同一个函数(喂
合成的历史日行情)——**同一份编排代码**,不是"正式跑一套、测试再写一套"。

**原则守护(§2.4 铁律,写进编排顺序本身,不是靠人记住)**:
    1. 退潮哨兵先判——一旦当日已触发红色刹车(`sentinel_events` 表里已有
       `(trade_date,"retreat","","brake")`,不论是今天哪一拍触发的)→ ⚠ **V2-⑬-1 起
       买点哨兵已退役**(它 100% 由 K1 per-code `entry_spec` 驱动,V2 无单票买点计划),
       退潮红色刹车因此只剩「不产生任何新的开仓许可信号」这一层语义上的保证 ——
       系统本就不再产生开仓信号。持仓哨兵与证伪哨兵不受退潮影响——管理已有仓位的
       风险、和把已经变坏的关注目标标记"剔除勿进",在退潮当日依然是有意义的信息。
    2. 拉不到行情(quotes 缺该票)→ 对应哨兵该票直接跳过(已在各哨兵纯函数
       内部处理,`quote=None` 时返回 None),不是"当没发生"而是"没有意见"。
    3. 无篮子(V2 引擎未跑过 / 今日无篮子定档)/ 无持仓 都是合法状态,只是那部分
       哨兵没有对象可判,不报错。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from neckline import custom_alerts as custom_alerts_store
from neckline import notify_kinds
from neckline.db import connection
from neckline.report.sectors import load_member_map
from neckline.sentinel import attention, basket_verify, capture
from neckline.sentinel import custom as custom_alerts_tick
from neckline.sentinel.channels import (
    LEVEL_CRITICAL,
    LEVEL_INFO,
    LEVEL_WARN,
    PushChannel,
    default_channels,
    push_all,
)
from neckline.sentinel.dedup import already_pushed, record_pushed
from neckline.sentinel.holding import (
    HoldingAlert,
    check_exit_reference_reached,
    evaluate_holding,
)
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
    # —— V2-⑪ 旁路(⑪-A 四监测 / ⑪-C NL 临时提醒)的观测位。**同样不参与任何纪律
    #    判定**;任一为空都不影响四哨兵与熔断。————————————————————————————
    attention_alerts: List[str] = field(default_factory=list)     # "kind:scope:event_key"
    attention_unavailable: Dict[str, str] = field(default_factory=dict)
    merged_exposure: List[Any] = field(default_factory=list)      # attention.MergedExposureGroup
    custom_alert_hits: List[int] = field(default_factory=list)    # 命中的 custom_alerts.id
    custom_alerts_expired: List[int] = field(default_factory=list)
    # —— 2026-08-03 用户拍板旁路(离场参考区间触达,驱动 APNs `take_profit` kind)
    #    的观测位。同样不参与任何纪律判定;为空不影响四哨兵与熔断。———————————
    exit_reference_hits: List[str] = field(default_factory=list)  # 命中的 ts_code
    retreat_active: bool = False
    retreat_alert: Optional[RetreatAlert] = None       # 仅红色刹车时非空(驱动 APNs/通道推送)
    retreat_warning: Optional[str] = None              # 黄色预警文案(只进看板,不推送)
    breadth_snapshot: Optional[MarketBreadthSnapshot] = None
    invalidation_signals: List[InvalidationSignal] = field(default_factory=list)
    holding_alerts: List[HoldingAlert] = field(default_factory=list)
    pushed_events: List[str] = field(default_factory=list)
    skipped_duplicate: int = 0


def _candidate_return(quote: Optional[Quote]) -> Optional[float]:
    if quote is None or not quote.pre_close:
        return None
    return quote.price / quote.pre_close - 1


def _hot_sector_peer_returns(targets: List["WatchTarget"], quotes: Dict[str, Quote]) -> List[float]:
    """退潮哨兵「主线板块跳水」的样本,用已经拉到的行情算盘中收益率,不额外拉价。

    ⚠ **V2-⑬-1 换样本源(判定逻辑与阈值一行未改)**:V1 取「关注池里命中今日热门
    板块标签的候选」;候选榜已删,V2 换成 **T1/T2 篮子成员全体** —— 篮子的定义
    就是「同一个驱动串起来的一组票」、且 T1/T2 是当日注意力最高的两档,它比
    V1 的「热门板块标签」更贴近"主线"这个词。⛔ 不新增任何阈值。"""
    rets: List[float] = []
    for t in targets:
        r = _candidate_return(quotes.get(t.ts_code))
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


def _load_exit_references(
    position_ids: List[int], db_path: Optional[Path]
) -> Dict[int, Tuple[float, float]]:
    """`position_id -> (exit_low, exit_high)`,取 `position_plans` **最新版本**继承
    的离场参考区间(2026-08-03 用户拍板,APNs `take_profit` kind 的触发源 ——
    ⚠ 与 `holding.check_take_profit`〔回落止盈,机械纪律〕刻意不同源,见该函数
    docstring)。查无计划行 / 无来源篮子 / 卡未就绪 / 该票离场参考被 ⑦ 夹逼拒收 →
    该 position_id 缺此键(**不臆造默认区间**,同 `positions_entry.
    evaluate_entry_deviation`"无从比较就不比较"的既有姿势)。

    **只读 `position_plans` 表,不 import `positions_entry`**(同 `attention.
    load_position_sources` 既有惯例:sentinel 层直接读表,不绕业务写入层)。按
    `(position_id, version)` 升序取行,同一 position_id 后出现的行覆盖前面的
    (= 取最新版本),与 `positions_entry.latest_position_plan` 语义一致。"""
    if not position_ids:
        return {}
    placeholders = ",".join("?" * len(position_ids))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT position_id, plan_json FROM position_plans "
            f"WHERE position_id IN ({placeholders}) ORDER BY position_id, version",
            tuple(position_ids),
        ).fetchall()
    out: Dict[int, Tuple[float, float]] = {}
    for pid, plan_json in rows:
        try:
            plan = json.loads(plan_json) if plan_json else {}
        except (json.JSONDecodeError, TypeError):
            continue
        ref = plan.get("exit_reference") if isinstance(plan, dict) else None
        low = ref.get("low") if isinstance(ref, dict) else None
        high = ref.get("high") if isinstance(ref, dict) else None
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            out[int(pid)] = (float(low), float(high))
        else:
            out.pop(int(pid), None)   # 新版本没有离场参考 → 不沿用旧版本的(现役计划说了算)
    return out


def _default_notifier() -> Any:
    """APNs 措辞层的默认实现(**惰性 import**:`neckline.api.notify` 会拉起 API 层的
    存取模块,模块级 import 会让 `sentinel` 反向依赖 `api`;放在函数体里两边都干净,
    同 `api/app.py::_sentinel_loop` 惰性 import 哨兵的既有姿势)。"""
    from neckline.api import notify

    return notify


def run_tick(
    now: datetime,
    *,
    channels: Optional[List[PushChannel]] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Quote]]] = None,
    notifier: Optional[Any] = None,
) -> TickResult:
    """跑一拍。`quotes_fn` 可覆盖(默认 `sentinel.quotes.get_quotes`)——冒烟脚本
    用它注入"某历史日的合成盘中快照",不改一行编排逻辑。

    `notifier`(V2-⑪,2026-08-03 起再加持仓三事件旁路)可覆盖 `neckline.api.notify`
    (APNs 三级措辞层)——服务于 ⑪ 的两条旁路(四监测 / NL 临时提醒)+ 持仓哨兵三
    事件升级立即级的两条旁路(`_maybe_push` 的 `apns_kind` 分支 + 离场参考区间旁路);
    既有四哨兵**仍走 `channels`**,console/Bark 推送路径一行未动。"""
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

    # `_notify()` 提前到此处定义(原先随 V2-⑪ 两条旁路一起定义在本函数尾部)——
    # `_maybe_push` 的 `apns_kind` 分支(2026-08-03 新增)要在"4) 持仓哨兵"那段
    # 调用它,时间上早于文件尾部那两条旁路,闭包捕获的是**名字**、按调用时刻解析,
    # 定义必须先于第一次调用,不能仍留在文件尾部。
    notify_mod: Optional[Any] = notifier if notifier is not None else None

    def _notify() -> Any:
        nonlocal notify_mod
        if notify_mod is None:
            notify_mod = _default_notifier()
        return notify_mod

    def _maybe_push(
        sentinel: str, ts_code: str, event_key: str, title: str, body: str, level: str,
        payload_extra: Optional[Dict[str, Any]] = None,
        apns_kind: Optional[str] = None,
    ) -> None:
        if already_pushed(trade_date, sentinel, ts_code, event_key, db_path=db_path):
            result.skipped_duplicate += 1
            return
        delivered = push_all(channels, title, body, level=level)
        payload = {"body": body, "delivered": delivered}
        if payload_extra:
            payload.update(payload_extra)
        if apns_kind is not None:
            # 2026-08-03 用户拍板旁路:stop_approach/sector_dive 复用**同一份**
            # console 文案(title/body 原样转手)推 APNs——同一次 already_pushed/
            # record_pushed 去重,不二次措辞、不二次去重(⛔ 不得看板一条+APNs
            # 另一种说法)。失败只 WARNING,不影响本次 console 推送与去重记账
            # 已经完成的事实(与 ⑪ 两条旁路的"炸了只吞"同一条纪律)。
            try:
                outcome = _notify().push_holding_risk_alert(
                    apns_kind, title, body, code=ts_code, db_path=db_path,
                )
                payload["apnsSent"] = getattr(outcome, "sent", 0)
                payload["apnsSkippedReason"] = getattr(outcome, "skipped_reason", "")
            except Exception:  # noqa: BLE001
                logger.warning(
                    "[tick] 持仓风险 APNs 旁路失败(已吞,不影响看板/Bark 已完成的推送)",
                    exc_info=True,
                )
        record_pushed(trade_date, sentinel, ts_code, event_key, payload=payload, db_path=db_path)
        result.pushed_events.append(f"{sentinel}:{ts_code or '-'}:{event_key}")

    # —— 1) 退潮哨兵(先判,决定买点哨兵是否本拍抑制;双级制见 retreat.py 模块头)——
    hhmm = now.strftime("%H%M")
    breadth_snapshot = compute_breadth_snapshot(trade_date, quotes, meta)
    result.breadth_snapshot = breadth_snapshot
    hot_peer_rets = _hot_sector_peer_returns(wu.targets, quotes)
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

    # —— 2) 证伪哨兵(不受退潮抑制——"剔除勿进"任何时候都是有效信息)——————————
    # ⚠ **V2-⑬-1**:判定对象 = D0 冻结的 T1/T2 篮子成员(`wu.targets`),不再是 V1 候选;
    # 判定逻辑与阈值一行未改(证伪 spec 本就是零入参全局常量)。**买点哨兵已退役**
    # (原第 2 段),编号顺延不重排 —— 其余段落的序号沿用历史编号,便于对照旧日志。
    for t in wu.targets:
        inv = check_invalidation(t, quotes.get(t.ts_code), prev5.get(t.ts_code, 0.0), now)
        if inv is not None:
            result.invalidation_signals.append(inv)
            _maybe_push(
                "invalidation", t.ts_code, "trigger",
                f"剔除勿进:{t.name}({t.ts_code})", inv.reason_text, LEVEL_WARN,
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
        # 2026-08-03 用户拍板:stop_approach/sector_dive 复用同一份 console 文案旁路
        # 推 APNs(见 `_maybe_push` 的 `apns_kind` 分支)。⚠ `take_profit` 故意**不在
        # 这里**——回落止盈继续只驱动 console/Bark,APNs 的 take_profit kind 改由
        # 下面独立的「旁路 E:离场参考区间触达」驱动(见该处注释,触发源修正详情
        # 见 `holding.check_exit_reference_reached` docstring)。
        _apns_kind_by_key = {
            "stop_approach": notify_kinds.KIND_STOP_APPROACH,
            "sector_dive": notify_kinds.KIND_SECTOR_DIVE,
        }
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
                        apns_kind=_apns_kind_by_key.get(key),
                    )

    # ══════════════════════════════════════════════════════════════════════
    # 以下三段是旁路(⑪-A 四监测 / ⑪-C NL 临时提醒 / 2026-08-03 离场参考区间触达)。
    #
    #   · 各自独立 `try/except`,异常只 WARNING —— 与 ⑧ 的存拍/篮子验证同一条纪律:
    #     旁路炸了绝不许影响上面四哨兵与熔断的任何判定,也不许掀翻主循环。
    #   · **它们不读、不改任何纪律状态**:退潮闩锁、止损线、D 计数、熔断,一个都不碰。
    #   · 推送走 APNs 三级(`notifier`,按 kind 配开关,`_notify()` 定义已提前到
    #     `_maybe_push` 之前,见该处注释),**不进 `channels`** —— 既有四哨兵的
    #     Bark/日志通道保持原样,新 kind 不混进去。
    #   · 台账仍落 `sentinel_events`(⑪-B 原文:冷却/去重/防重沿用该表),因此这些
    #     事件同样会出现在 `GET /board` 的当日事件流里。
    # ══════════════════════════════════════════════════════════════════════

    # 两条旁路共用的「持仓 → D0 来源篮子」关联(查一次,别查两遍)。
    position_sources: Dict[int, Any] = {}
    if wu.positions:
        try:
            position_sources = attention.load_position_sources(wu.positions, db_path=db_path)
        except Exception:  # noqa: BLE001
            logger.warning("[tick] 查持仓来源篮子失败(两条 ⑪ 旁路按无来源处理)", exc_info=True)

    # —— V2-⑪ 旁路 C:注意力四监测(⑪-A)————————————————————————————————
    try:
        att = attention.evaluate_attention(
            trade_date, wu.positions, quotes, meta, sources=position_sources, db_path=db_path,
        )
        result.merged_exposure = list(att.merged_exposure)
        result.attention_unavailable = dict(att.unavailable)
        for a in att.alerts:
            if already_pushed(trade_date, "attention", a.scope, a.event_key, db_path=db_path):
                result.skipped_duplicate += 1
                continue
            outcome = _notify().push_attention_alert(
                a.kind, a.title, a.what_happened, plan_touched=a.plan_touched,
                code=a.scope if a.position_id is not None else "",
                position_id=a.position_id, merged_exposure_note=a.merged_exposure_note,
                db_path=db_path,
            )
            # 台账**无论推没推出去都落**(开关关掉 = 不打扰,不等于这件事没发生;
            # 看板照样要看得见)——同 `_maybe_push` 的既有姿势。
            record_pushed(
                trade_date, "attention", a.scope, a.event_key,
                payload={
                    "kind": a.kind, "title": a.title, "body": a.what_happened,
                    "planTouched": a.plan_touched, "mergedExposure": a.merged_exposure_note,
                    "metrics": a.metrics, "positionId": a.position_id,
                    "delivered": getattr(outcome, "sent", 0),
                    "skippedReason": getattr(outcome, "skipped_reason", ""),
                },
                db_path=db_path,
            )
            result.attention_alerts.append(f"{a.kind}:{a.scope or '-'}:{a.event_key}")
    except Exception:  # noqa: BLE001
        logger.warning("注意力监测本拍失败(已吞,不影响哨兵判定)", exc_info=True)

    # —— V2-⑪ 旁路 D:自然语言临时提醒(⑪-C,执行归确定性哨兵)——————————————
    try:
        member_map_for_alerts = custom_alerts_tick.build_basket_member_map(
            position_sources, wu.positions
        )
        cres = custom_alerts_tick.evaluate_alerts(
            now, quotes=quotes, positions=wu.positions, prev5_avg_volume=prev5,
            basket_members=member_map_for_alerts, db_path=db_path,
        )
        result.custom_alerts_expired = list(cres.expired_ids)
        for hit in cres.hits:
            outcome = _notify().push_custom_alert(
                hit.alert.id,
                custom_alerts_tick.subject_text(hit.alert, quotes),
                hit.condition_text,
                code=hit.alert.ts_code or "",
                quote_delay_note=custom_alerts_store.QUOTE_DELAY_DISCLOSURE,
                db_path=db_path,
            )
            record_pushed(
                trade_date, custom_alerts_tick.SENTINEL_NAME, hit.alert.ts_code or "",
                hit.event_key,
                payload={
                    "kind": "custom_alert", "alertId": hit.alert.id,
                    "body": hit.condition_text, "values": hit.values,
                    "nlText": hit.alert.nl_text,
                    "delivered": getattr(outcome, "sent", 0),
                    "skippedReason": getattr(outcome, "skipped_reason", ""),
                },
                db_path=db_path,
            )
            custom_alerts_store.mark_fired(hit.alert.id, db_path=db_path)
            result.custom_alert_hits.append(hit.alert.id)
    except Exception:  # noqa: BLE001
        logger.warning("临时提醒本拍失败(已吞,不影响哨兵判定)", exc_info=True)

    # —— 旁路 E:离场参考区间触达(2026-08-03 用户拍板,APNs `take_profit` kind 的
    #    定向任务书)—————————————————————————————————————————————————————
    # ⚠ 与上面"4) 持仓哨兵"里 `evaluate_holding` 的「回落止盈」(`check_take_
    # profit`,机械纪律,驱动 console/Bark)**刻意不同源、不合并**——见
    # `sentinel/holding.py::check_exit_reference_reached` docstring。本旁路只服务
    # APNs `take_profit` kind:独立 try/except、独立 `sentinel_events` 去重
    # (event_key="exit_reference",与 4) 的 event_key="take_profit" 互不冲突、不
    # 抢占彼此的去重槽位)、**不进 channels**(console/Bark 继续只反映回落止盈,
    # 一字不动)。查无 `position_plans` / 无离场参考 → 如实不判该票这一条(不编
    # 默认目标价)。
    if wu.positions:
        try:
            exit_refs = _load_exit_references([p.id for p in wu.positions], db_path)
            for p in wu.positions:
                ref = exit_refs.get(p.id)
                if ref is None:
                    continue
                q = quotes.get(p.ts_code)
                if q is None:
                    continue
                reason = check_exit_reference_reached(p, q, ref[0], ref[1])
                if reason is None:
                    continue
                if already_pushed(trade_date, "holding", p.ts_code, "exit_reference", db_path=db_path):
                    result.skipped_duplicate += 1
                    continue
                outcome = _notify().push_holding_risk_alert(
                    notify_kinds.KIND_TAKE_PROFIT, f"离场参考提醒:{p.ts_code}", reason,
                    code=p.ts_code, db_path=db_path,
                )
                record_pushed(
                    trade_date, "holding", p.ts_code, "exit_reference",
                    payload={
                        "kind": notify_kinds.KIND_TAKE_PROFIT, "body": reason,
                        "exitLow": ref[0], "exitHigh": ref[1],
                        "delivered": getattr(outcome, "sent", 0),
                        "skippedReason": getattr(outcome, "skipped_reason", ""),
                    },
                    db_path=db_path,
                )
                result.exit_reference_hits.append(p.ts_code)
        except Exception:  # noqa: BLE001
            logger.warning("离场参考区间触达检查本拍失败(已吞,不影响哨兵判定)", exc_info=True)

    return result


__all__ = ["TickResult", "run_tick"]
