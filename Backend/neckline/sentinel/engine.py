"""单拍编排(plan 阶段3)。`run_tick(now, ...)` 是哨兵一次轮询要做的全部事情:
组装关注池 → 批量拉价 → **持仓哨兵**判定 → 防重 → 推送 → 落账。`scripts/sentinel.py`
的主循环只是反复调用本函数;`scripts/smoke_sentinel.py` 也调用同一个函数(喂
合成的历史日行情)——**同一份编排代码**,不是"正式跑一套、测试再写一套"。

🔴 **V2.4.0 P0 换血:两段盘中判决整段删除(撤销判断权,不是调阈值)**

    · **退潮哨兵**(代理关注池 → 「大盘退潮」→ 一条**全局刹车 + 停止开新仓**的
      交易动作语义)—— 删。
      测量样本是**代理关注池**不是全市场、「昨日同时段」不是同一批票、约 60s 一拍的
      「连续两拍」只代表两分钟、红事件全天闩锁不因修复翻转 —— **测量范围与动作权限
      不匹配**(审计规格 P0.1)。
    · **证伪哨兵**(瞬时跌破 VWAP / 低开未翻红 / 折算量比异常 → 一条**剔除类**
      盘中判决)—— 删。
      所有 T1/T2 成员共用**一套全局常量**而非每票 D0 冻结条件;事件当日按固定
      `event_key` 闩锁,后续站回 VWAP / 翻红都不会翻转前端结论。

    **产品决定(审计规格 P0 定位逐字)**:用户自行观察盘中分时;系统保留 D0 冻结预案
    与必要的持仓纪律提示,**不再据普通盘中波动出「证伪」判决,不再据代理关注池出
    全局「退潮刹车 / 停止开新仓」**。
    ⛔ **不许以任何形式复活**:不许改名叫「风险」「观察」,不许换成分数 / 状态机 /
    交通灯,不许新建 `intraday_current_state` 之类替代表。
    ⚠ `sentinel/{invalidation,retreat,retreat_store,mainline}.py` **文件仍在**(回滚 +
    历史行为留档),但**本模块对它们零 import** —— 判据是「生产入口有没有它」,不是
    「文件在哪」(§3.14-A);守门单测 AST 扫本文件的调用点数必须为 0。

**原则守护(§2.4 铁律,写进编排顺序本身,不是靠人记住)**:
    1. 拉不到行情(quotes 缺该票)→ 对应哨兵该票直接跳过(已在各哨兵纯函数
       内部处理,`quote=None` 时返回 None),不是"当没发生"而是"没有意见"。
    2. 无篮子(V2 引擎未跑过 / 今日无篮子定档)/ 无持仓 都是合法状态,只是那部分
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
from neckline.dedup import already_pushed, record_pushed
from neckline.sentinel.holding import (
    HoldingAlert,
    check_exit_reference_reached,
    evaluate_holding,
)
from neckline.sentinel.intraday import is_intraday_now
from neckline.sentinel.positions import Position
from neckline.data.realtime import Quote, get_quotes
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

# ⛔ V2.4.0 P0:退潮"重启后首拍不触发红色"的进程态闸(`_RETREAT_WARMED_DATES` /
# `_consume_retreat_first_tick` / `reset_retreat_process_state`)随退潮判级整段删除
# —— 没有判级就没有"首拍是否允许升红"这个问题。


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
    # ⛔ V2.4.0 P0:`retreat_active` / `retreat_alert` / `retreat_warning` /
    # `breadth_snapshot` / `invalidation_signals` **五个观测位整体删除** —— 不是置成
    # 恒 False / 恒空,而是让"这一拍出没出退潮或证伪结论"这个问题在类型上不存在。
    # 留一个恒 False 的位就是「前端隐藏、后台仍在判」的温床(P0.4-9 红线)。
    holding_alerts: List[HoldingAlert] = field(default_factory=list)
    pushed_events: List[str] = field(default_factory=list)
    skipped_duplicate: int = 0


def _candidate_return(quote: Optional[Quote]) -> Optional[float]:
    if quote is None or not quote.pre_close:
        return None
    return quote.price / quote.pre_close - 1


# ⛔ V2.4.0 P0:`_hot_sector_peer_returns`(退潮「主线板块跳水」样本的收益率)随退潮
# 判级一并删除。⚠ **别与下面的 `_position_sector_peer_returns` 混** —— 那是**持仓哨兵**
# 的 `sector_dive`(某只**持仓**所属概念板块的同伴收益率),P0.3 明令保留,一行未动。


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

    **⑪-D-B 闸② 的读侧(2026-08-03)**:只认 `plan_json.exit_reference_armed is
    True` 的行。**缺键 = 不武装**(fail-closed,⛔ 不是"老行按老行为放行")——
    §2.8-C-3 记名豁免的前提② 是「该数值**已过**机械 sanity 闸」,一份没有武装位的
    计划就是**没过闸**,不是"过了但没写"。武装态的唯一产地是 `positions_entry.
    _arm_fields`(开仓 + 计划新版本两条路径都经它),本函数只读不判。

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
        armed = plan.get("exit_reference_armed") is True if isinstance(plan, dict) else False
        low = ref.get("low") if isinstance(ref, dict) else None
        high = ref.get("high") if isinstance(ref, dict) else None
        if armed and isinstance(low, (int, float)) and isinstance(high, (int, float)):
            out[int(pid)] = (float(low), float(high))
        else:
            # 新版本没有离场参考 / 未武装 → 不沿用旧版本的(现役计划说了算)。
            out.pop(int(pid), None)
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
    """跑一拍。`quotes_fn` 可覆盖(默认 `data.realtime.get_quotes`)——冒烟脚本
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

    # —— 1) 退潮哨兵 ⛔ 已删(V2.4.0 P0)———————————————————————————————————
    #     原第 1 段(宽度快照 → 主线切片估计 → `evaluate_retreat` 判级 → 逐拍
    #     `record_retreat_metrics` → 红色刹车推送 / 黄色预警落看板)**整段删除**。
    #     `retreat_metrics` 表**只读保留、不再有新行**;历史行一行不动。
    #     ⚠ **心跳证据换人**:此前"盘中哨兵还活着"看的是 `retreat_metrics` 每拍 +1,
    #     现在看 `sentinel_events` 的 `sentinel='capture'` 行 + journal 的 tick 日志。
    #
    # —— 2) 证伪哨兵 ⛔ 已删(V2.4.0 P0)———————————————————————————————————
    #     原第 2 段(遍历 `wu.targets` 调 `check_invalidation` → 剔除类判决推送)
    #     **整段删除**;`sentinel='invalidation'` 不再有新事件。
    #     🔴 **别把它与 D0 冻结的「判断失效位置」搞混**:那是卡上的 `invalidation_spec` /
    #     `close_below_stop_line`(交易资格四件套第 4 件)与竞价层的
    #     `auction/mech.py::hit_invalidation`,**两者都是明令保留的能力,一行未动**。
    #
    #     编号 1)/2) 的空位刻意保留,便于对照旧日志与历史 review 记录。

    # —— 4) 持仓哨兵(P0.3 明令保留:管理已有仓位任何时候都要做)———————————————
    if wu.positions:
        active_rule = brain.get_active(db_path=db_path)
        stop_pct = _DEFAULT_STOP_PCT
        take_profit_retrace = None
        # V2.2-⑤:现役章程的止损口径(强制条件单 / 亏损警戒),判据单一源 `brain`。
        # 只换 `check_stop_approach` 的文案口吻,**判定与阈值一字未动**;`v2.2-k8` 激活前
        # 恒 False = 与 V2.2 之前逐字节相同(§2.1 前置提示:激活前本节全文一字有效)。
        # V2.3.2-⑤:现役行的 config **就在 `active_rule` 上**,直接传进判据(⛔ 别让它
        # 再去库里查一遍;判据优先级见 `brain.stop_is_advisory` docstring)。
        if active_rule is not None:
            cfg = active_rule.rule.get("config", {}) or {}
            stop_advisory = brain.stop_is_advisory(active_rule.version, cfg)
            stop_pct = cfg.get("stop_pct") or _DEFAULT_STOP_PCT
            take_profit_retrace = cfg.get("take_profit_retrace")
        else:
            stop_advisory = brain.stop_is_advisory(None)
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
                historical_peak_close=peak, peer_returns=peer_rets, stop_advisory=stop_advisory,
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
