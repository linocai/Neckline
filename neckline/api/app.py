"""FastAPI 应用主体(plan 4A + 4B.3 单 unit 内哨兵 asyncio 任务)。

绑 127.0.0.1:8002(nginx 反代,与 LinoN 8001 共存)。`/api/v1/health` 免鉴权;其余端点
过 `require_token`。startup:fail-fast 校验 `API_TOKEN`(len>=16)+ `init_schema` + 起
哨兵后台轮询任务(§3.6「哨兵折进 FastAPI 单 unit 的 lifespan asyncio 任务」,不另起进程)。
shutdown:置位 stop_event,优雅停轮询。

**同码不重写**:报告 / 看板 / 持仓 / 问询台的领域逻辑全部复用现有模块,端点只做「装配 +
出入参映射 + 鉴权」。

**测试注入(沿 LinoN 模块级替身姿势)**:`ENABLE_SENTINEL`(关后台轮询)、`_DB_PATH_OVERRIDE`
(隔离库)、`_QUOTES_FN`/`_PANEL_FN`/`_PROVIDER_FN`(免联网 / 免真 LLM)。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status

from neckline import watchlist as watchlist_store
from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.inquiry import run_inquiry
from neckline.api.schemas import (
    BoardEventOut,
    BoardOut,
    CandidateOut,
    DeviceRegisterIn,
    EntrySuggestionOut,
    InquiryIn,
    InquiryOut,
    LLMJudgmentOut,
    OkOut,
    PositionCloseIn,
    PositionOpenIn,
    PositionOpenOut,
    PositionOut,
    PositionsOut,
    PushSettingsOut,
    ReportOut,
    RetreatBrakeOut,
    ReviewGetOut,
    ReviewUploadOut,
    SettingsLLMIn,
    SettingsOut,
    SettingsPushIn,
    SettingsReviewColMapIn,
    ThsExportOut,
    ThsReconcileOut,
    WatchlistAddIn,
    WatchlistAddOut,
    WatchlistCheckLLMOut,
    WatchlistCheckOut,
    WatchlistItemOut,
    WatchlistOut,
    WatchlistPinIn,
    WeeklyReviewOut,
)
from neckline.api.stores import upsert_device
from neckline.calendar import is_trading_day, prev_trading_day
from neckline.config import ensure_data_dirs
from neckline.llm.factory import get_provider
from neckline.report import store as report_store
from neckline.sentinel import dedup
from neckline.sentinel import positions as pos_store
from neckline.sentinel.intraday import is_intraday_now
from neckline.settings_store import get_app_settings, set_llm, set_push, set_review_col_map

logger = logging.getLogger(__name__)

VERSION = "0.6.0-v1.1ABCD"
API_PREFIX = "/api/v1"

# —— 测试注入开关(生产恒 True / 恒默认)——————————————————————————————————
# startup 是否起哨兵后台轮询;可用环境变量 NECKLINE_ENABLE_SENTINEL=0 关(冒烟脚本用)。
ENABLE_SENTINEL = os.environ.get("NECKLINE_ENABLE_SENTINEL", "1") != "0"
_DB_PATH_OVERRIDE: Optional[Path] = None      # 隔离库(None → settings.db_path)
_QUOTES_FN: Optional[Callable[[List[str]], Dict[str, Any]]] = None  # 实时拉价(None → sentinel.quotes)
_PANEL_FN: Optional[Callable[..., Any]] = None                       # 问询台面板(None → 真 build_research_panel)
_PROVIDER_FN: Optional[Callable[[Optional[Path]], Any]] = None       # 问询台 provider(None → get_provider)

# 哨兵轮询节奏
_SENTINEL_POLL_SEC = 60
_SENTINEL_LUNCH_POLL_SEC = 300
_SENTINEL_IDLE_POLL_SEC = 300
# v1.1-A.1:开盘前收紧轮询——9:20–9:30 段每 30s 一探,确保 9:25:30 盘前校准窗口必被命中
# (非交易时段 5min 一探会错过 9:25:30)。盘前分支只在此窗口跑,盘中(9:30+)节奏不变。
_SENTINEL_PREOPEN_POLL_SEC = 30
_PREOPEN_START = time(9, 20)
_PREOPEN_END = time(9, 30)


def _db() -> Optional[Path]:
    return _DB_PATH_OVERRIDE


# —— 哨兵后台轮询(4B.3;折进 lifespan asyncio,单 unit 省内存)——————————————————

def _is_preopen(now: datetime) -> bool:
    """开盘前收紧轮询窗口:交易日 且 09:20 ≤ now.time() < 09:30。`is_intraday_now` 对该段
    返 False(盘中从 9:30 起),两窗口不重叠,故用 `elif` 串接安全。"""
    return is_trading_day(now.date()) and _PREOPEN_START <= now.time() < _PREOPEN_END


async def _sentinel_loop(stop_event: asyncio.Event) -> None:
    """交易时段每 60s 调 `run_tick`(阻塞活 run in thread,不卡事件循环);退潮首次触发
    → APNs 刹车推送(白名单四类之一)。**v1.1-A**:开盘前 9:20–9:30 收紧到 30s 一探并跑
    `run_precall_tick`(盘前校准 + D5 扫描,当日只跑一次,内部自防重),9:26 汇总 / D5 推送
    经 `notify` 白名单入口。**现有 9:35 起 intraday 判逻辑一字不改**。非交易时段优雅待机
    (每 5min 探一次,不空转)。"""
    from neckline.sentinel.engine import run_tick
    from neckline.sentinel.precall import run_precall_tick

    logger.info("哨兵后台轮询已挂载(单 unit lifespan asyncio;含 v1.1 盘前校准分支)")
    while not stop_event.is_set():
        now = datetime.now()
        interval = _SENTINEL_IDLE_POLL_SEC
        if is_intraday_now(now):
            try:
                result = await asyncio.to_thread(run_tick, now, db_path=_db())
                if result.retreat_alert is not None:
                    await asyncio.to_thread(
                        notify.push_retreat_brake, result.retreat_alert.reason_text, db_path=_db()
                    )
            except Exception:  # noqa: BLE001  单拍异常绝不能拖垮轮询
                logger.warning("哨兵一拍异常(已吞,继续轮询)", exc_info=True)
            interval = _SENTINEL_LUNCH_POLL_SEC if time(11, 30) <= now.time() < time(13, 0) else _SENTINEL_POLL_SEC
        elif _is_preopen(now):
            try:
                pr = await asyncio.to_thread(run_precall_tick, now, db_path=_db())
                if pr.ran:
                    if pr.summary_actionable > 0:
                        await asyncio.to_thread(notify.push_precall_summary, pr.counts, db_path=_db())
                    for ex in pr.d5_exits:
                        await asyncio.to_thread(
                            notify.push_d5_exit, ex.name, ex.ts_code, ex.d, db_path=_db()
                        )
            except Exception:  # noqa: BLE001  盘前一拍异常同样绝不能掀翻轮询主循环
                logger.warning("盘前校准一拍异常(已吞,继续轮询)", exc_info=True)
            interval = _SENTINEL_PREOPEN_POLL_SEC
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # —— startup ——
    require_api_token_ready()                 # fail-fast:API_TOKEN len>=16
    ensure_data_dirs()
    from neckline.db import init_schema
    init_schema(_db())
    app.state._stop_event = asyncio.Event()
    app.state._sentinel_task = None
    if ENABLE_SENTINEL:
        app.state._sentinel_task = asyncio.create_task(_sentinel_loop(app.state._stop_event))
    yield
    # —— shutdown ——
    app.state._stop_event.set()
    task = app.state._sentinel_task
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()


app = FastAPI(title="Neckline", version=VERSION, lifespan=lifespan)


# —— health(免鉴权,供 nginx / 客户端自检)————————————————————————————————

@app.get(f"{API_PREFIX}/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION}


# —— 4A.2 报告 ————————————————————————————————————————————————————————

def _shape_candidate(c: Dict[str, Any], judgment: Optional[Dict[str, Any]]) -> CandidateOut:
    """报告落库的候选 JSON 快照 → 客户端四件套契约。同码不重写:字段直接取自
    `Candidate.public_dict()` 存档,不在此重算任何领域值。"""
    llm = None
    if judgment is not None:
        llm = LLMJudgmentOut(
            verdict=judgment.get("verdict", ""),
            narrative=judgment.get("narrative", ""),
            degraded=bool(judgment.get("degraded", False)),
        )
    return CandidateOut(
        rank=c.get("rank", 0),
        code=c.get("ts_code", ""),
        name=c.get("name", ""),
        score=c.get("score", 0.0),
        board=c.get("board", ""),
        buyPoint=c.get("entry_plan", ""),
        stop=c.get("stop_loss", ""),
        target=c.get("target", ""),
        invalidation=c.get("invalidation_text", ""),
        invalidationSpec=c.get("invalidation_spec", {}) or {},
        entrySpec=c.get("entry_spec", {}) or {},
        formTags=c.get("pattern_tags", []) or [],
        hotSectors=c.get("hot_sectors", []) or [],
        sectorNames=c.get("sector_names", []) or [],
        llmJudgment=llm,
    )


def _shape_watchlist_check(d: Dict[str, Any]) -> WatchlistCheckOut:
    """自选体检落库(或内存)快照 → 客户端契约。同 `_shape_candidate` 的透传惯例,
    不在此重算任何领域值(评分/红绿灯/四件套全部来自
    `neckline.report.watchlist_check.WatchlistCheckItem.public_dict()`)。"""
    llm = None
    jr = d.get("llm_judgment")
    if jr:
        llm = WatchlistCheckLLMOut(
            verdict=jr.get("verdict", ""), narrative=jr.get("narrative", ""),
            degraded=bool(jr.get("degraded", False)),
        )
    return WatchlistCheckOut(
        code=d.get("ts_code", ""),
        name=d.get("name", ""),
        pinned=bool(d.get("pinned", False)),
        source=d.get("source", "manual"),
        hasData=bool(d.get("has_data", True)),
        close=d.get("close", 0.0) or 0.0,
        board=d.get("board", "MAIN"),
        score=d.get("score"),
        patternTags=d.get("pattern_tags", []) or [],
        hotSectors=d.get("hot_sectors", []) or [],
        sectorNames=d.get("sector_names", []) or [],
        greenLight=bool(d.get("green_light", False)),
        disqualifiers=d.get("disqualifiers", []) or [],
        buyPointTriggered=bool(d.get("buy_point_triggered", False)),
        buyPoint=d.get("entry_plan", ""),
        stop=d.get("stop_loss", ""),
        target=d.get("target", ""),
        invalidation=d.get("invalidation_text", ""),
        invalidationSpec=d.get("invalidation_spec", {}) or {},
        entrySpec=d.get("entry_spec", {}) or {},
        statusChanged=bool(d.get("status_changed", False)),
        llmJudgment=llm,
    )


def _shape_report(rep: Dict[str, Any]) -> ReportOut:
    from neckline.report.pipeline import compute_missed_entry_hint

    td = rep["trade_date"]
    d = datetime.strptime(td, "%Y%m%d").date()
    judgments = {j["ts_code"]: j for j in report_store.load_llm_judgments(d, db_path=_db())}
    candidates = [_shape_candidate(c, judgments.get(c.get("ts_code", ""))) for c in rep.get("candidates", [])]
    watchlist_check = [_shape_watchlist_check(w) for w in rep.get("watchlist", []) if isinstance(w, dict)]
    return ReportOut(
        tradeDate=td,
        generatedAt=rep.get("generated_at", ""),
        strategyVersion=rep.get("strategy_version", ""),
        sentiment=rep.get("sentiment", {}),
        sectors=rep.get("sectors", []),
        candidates=candidates,
        watchlistCheck=watchlist_check,
        missedEntryHint=compute_missed_entry_hint(d, db_path=_db()),   # v1.1-B.4 实时算(补录后自动消失)
    )


def _empty_report(reason: str) -> ReportOut:
    return ReportOut(
        tradeDate="", generatedAt="", strategyVersion="",
        sentiment={}, sectors=[], candidates=[], degraded=True, reason=reason,
    )


@app.get(f"{API_PREFIX}/report/latest", dependencies=[Depends(require_token)])
def report_latest() -> ReportOut:
    td = report_store.latest_report_date(db_path=_db())
    if td is None:
        return _empty_report("no_report")
    rep = report_store.load_report_by_str(td, db_path=_db())
    if rep is None:
        return _empty_report("no_report")
    return _shape_report(rep)


@app.get(f"{API_PREFIX}/report", dependencies=[Depends(require_token)])
def report_by_date(date: str = "") -> ReportOut:
    """指定交易日报告(历史回放,§2.6)。缺/查不到 → degraded 空态(HTTP 200,沿降级契约)。"""
    if not (len(date) == 8 and date.isdigit()):
        return _empty_report("bad_date")
    rep = report_store.load_report_by_str(date, db_path=_db())
    if rep is None:
        return _empty_report("no_report")
    return _shape_report(rep)


# —— 4A.3 盘中看板 ————————————————————————————————————————————————————

_SENTINEL_LABEL = {"entry": "买点", "invalidation": "证伪", "holding": "持仓"}
# v1.1:盘前校准 / D5 两新 sentinel 类型的中文标签(G.3 客户端看板明细;未识别原样透传)。
_SENTINEL_LABEL.update({"precall": "盘前校准", "d5exit": "D5退出"})


@app.get(f"{API_PREFIX}/board", dependencies=[Depends(require_token)])
def board() -> BoardOut:
    """当日盘中看板(§2.4 拍板:买点/证伪/持仓只进看板,不进 APNs)。数据源 = 当日
    `sentinel_events` 表聚合,**看板只读、不触发任何新判断**。"""
    trade_date = date.today()
    brake = dedup.retreat_brake_state(trade_date, db_path=_db())
    retreat = RetreatBrakeOut(active=bool(brake), reason=(brake or {}).get("reason", "") if brake else "")
    events_raw = dedup.load_events_for_date(trade_date, db_path=_db())
    names = _resolve_names([e["ts_code"] for e in events_raw if e["ts_code"]])
    events: List[BoardEventOut] = []
    asof = ""
    for e in events_raw:
        # 市场级标记(空 ts_code)不进事件列表:退潮刹车(retreat/brake)已走 retreatBrake
        # 红条;盘前校准的当日「tick 已跑」标记(precall/tick)是内部去重锚,均非用户可见事件。
        if not e["ts_code"]:
            continue
        asof = e.get("pushed_at", "") or asof
        events.append(BoardEventOut(
            sentinel=_SENTINEL_LABEL.get(e["sentinel"], e["sentinel"]),
            code=e["ts_code"],
            name=names.get(e["ts_code"], e["ts_code"]),
            eventKey=e["event_key"],
            verdict=(e.get("payload") or {}).get("body", ""),
            ts=e.get("pushed_at", ""),
        ))
    return BoardOut(tradeDate=trade_date.strftime("%Y%m%d"), asof=asof, retreatBrake=retreat, events=events)


# —— 4A.4 持仓 ————————————————————————————————————————————————————————

def _resolve_names(codes: List[str]) -> Dict[str, str]:
    """从 `stock_basic` 补股票名(看板/持仓展示用)。查不到 → 不填(调用方兜底回 code)。"""
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    try:
        import polars as pl

        from neckline.data.market_data import load_stock_basic
        sb = load_stock_basic(_db())
        if sb.is_empty():
            return {}
        sb = sb.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "name"])
        return dict(zip(sb["ts_code"].to_list(), sb["name"].to_list()))
    except Exception:  # noqa: BLE001
        logger.warning("补股票名失败(降级为空)", exc_info=True)
        return {}


def _resolve_prices(codes: List[str]) -> Dict[str, float]:
    """按需拉一拍实时价(持仓展示 price)。任何源失败 / 无网络 → 空 dict,客户端按
    buy_price 兜底(§4A.4)。走可注入的 `_QUOTES_FN`(单测免联网)。"""
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    fetch = _QUOTES_FN
    if fetch is None:
        from neckline.sentinel.quotes import get_quotes
        fetch = get_quotes
    try:
        quotes = fetch(codes)
    except Exception:  # noqa: BLE001
        logger.warning("持仓拉实时价失败(price 缺省 0)", exc_info=True)
        return {}
    out: Dict[str, float] = {}
    for code, q in (quotes or {}).items():
        price = getattr(q, "price", None)
        if price is None and isinstance(q, dict):
            price = q.get("price")
        if price:
            out[code] = float(price)
    return out


def _active_config() -> Tuple[float, int, float, Optional[float]]:
    """现役策略 config 的四个值(单一事实源 `brain.active_config`,§3.8 铁律):
    (stop_pct, max_hold_days, single_cap, take_profit_retrace)。无现役版本(异常状态)
    → 退回 `MomentumConfig` 字段默认(不在此另拍字面量)。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig

    fb = MomentumConfig()
    cfg = brain.active_config(db_path=_db())
    stop_pct = cfg.get("stop_pct") or fb.stop_pct
    max_hold = cfg.get("max_hold_days") or fb.max_hold_days
    single_cap = cfg.get("single_cap") or fb.single_cap
    tpr = cfg.get("take_profit_retrace", fb.take_profit_retrace)
    return float(stop_pct), int(max_hold), float(single_cap), (float(tpr) if tpr else None)


def _stop_line(buy_price: float, stop_pct: float) -> float:
    """派生止损线 = 买入价 ×(1−stop_pct)(读现役 config,§2.1 单一常量,不硬编 0.95)。"""
    return round(buy_price * (1 - stop_pct), 2)


def _retrace_state(
    position: "pos_store.Position", price: float, peak_hist: float, take_profit_retrace: Optional[float]
) -> Optional[Dict[str, Any]]:
    """回落止盈状态(plan B.1;**复用 `holding.check_take_profit` 判定「是否触发」,不重写
    阈值比较**)。无实时价(price≤0)→ None(算不出回落)。"""
    if price <= 0:
        return None
    from neckline.sentinel.holding import check_take_profit
    from neckline.sentinel.quotes import Quote

    q = Quote(code=position.ts_code, name="", price=price, pre_close=0.0, open=0.0,
              high=0.0, low=0.0, volume=0.0, amount=0.0, ts="", source="derived")
    peak = max(peak_hist or 0.0, price)
    reason = check_take_profit(position, q, peak_hist, take_profit_retrace)
    retrace_pct = (peak - price) / peak if peak > 0 else 0.0
    return {"peak": round(peak, 2), "retracePct": round(retrace_pct, 4), "triggered": reason is not None}


def _today_action(
    d_count: int, max_hold: int, dist_to_stop_pct: Optional[float], retrace_state: Optional[Dict[str, Any]]
) -> str:
    """今日动作提示文案(纯展示层,优先级:D5离场 > 回落止盈 > 跌破/逼近止损 > 持有中)。"""
    if d_count >= max_hold:
        return f"D{d_count} 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)"
    if retrace_state and retrace_state.get("triggered"):
        return "回落止盈已触发,按计划离场"
    if dist_to_stop_pct is not None:
        if dist_to_stop_pct <= 0:
            return "现价已跌破止损线,若条件单未成交请立即人工确认(系统不代下单)"
        if dist_to_stop_pct <= 0.02:
            return f"距止损线 {dist_to_stop_pct:.1%},盯紧条件单"
    return f"持有中(D{d_count}/D{max_hold})"


@app.get(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def list_positions() -> PositionsOut:
    holdings = pos_store.load_open_positions(db_path=_db())
    codes = [h.ts_code for h in holdings]
    names = _resolve_names(codes)
    prices = _resolve_prices(codes)
    stop_pct, max_hold, _single_cap, tpr = _active_config()
    today = date.today()
    out: List[PositionOut] = []
    for h in holdings:
        price = prices.get(h.ts_code, 0.0) or 0.0
        stop_line = _stop_line(h.buy_price, stop_pct)
        buy = datetime.strptime(h.buy_date, "%Y%m%d").date()
        dcount = pos_store.d_count(buy, today)
        dist = (price - stop_line) / price if price > 0 else None
        retrace = None
        if price > 0:
            from neckline.sentinel.engine import _historical_peak_close
            peak_hist = _historical_peak_close(h, today, None)
            retrace = _retrace_state(h, price, peak_hist, tpr)
        out.append(PositionOut(
            id=h.id, code=h.ts_code, name=names.get(h.ts_code, h.ts_code),
            buyPrice=h.buy_price, qty=h.qty, entryReason=h.note or "",
            buyDate=h.buy_date, price=price,
            status=h.status, stopLine=stop_line, stopOrderChecked=False,
            dCount=dcount, maxHoldDays=max_hold,
            distToStopPct=(round(dist, 4) if dist is not None else None),
            retraceState=retrace,
            todayAction=_today_action(dcount, max_hold, dist, retrace),
        ))
    return PositionsOut(holdings=out)


@app.get(f"{API_PREFIX}/positions/entry-suggestion", dependencies=[Depends(require_token)])
def entry_suggestion(code: str = "", price: float = 0.0) -> EntrySuggestionOut:
    """一键补录预填推荐(plan v1.1-B.3,**只读计算,不写台账**)。推荐 `qty` = 按现役
    `single_cap` 与现价取整手 `floor(single_cap/price/100)*100`(A 股 100 股/手);派生
    `stop_line` = 现价×(1−`stop_pct`)。price≤0 → qty=0、stop_line=0(防除零)。补录/清仓
    写入仍走既有 `POST /positions` / `POST /positions/{id}/close`(不改)。"""
    stop_pct, _max_hold, single_cap, _tpr = _active_config()
    if price <= 0:
        return EntrySuggestionOut(code=code, price=price, qty=0, stopLine=0.0)
    qty = int(math.floor(single_cap / price / 100) * 100)
    return EntrySuggestionOut(code=code, price=price, qty=qty, stopLine=_stop_line(price, stop_pct))


@app.post(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def open_position(body: PositionOpenIn) -> PositionOpenOut:
    """开仓录入(§3.8 铁律:系统永不自动下单,此处只录台账)。buy_date 取今日自然日。"""
    pid = pos_store.open_position(
        ts_code=body.code, buy_price=body.buy_price, qty=body.qty,
        buy_date=date.today(), note=(body.entry_reason or None), db_path=_db(),
    )
    stop_pct, _mh, _sc, _tpr = _active_config()
    return PositionOpenOut(ok=True, position_id=pid, stop_line=_stop_line(body.buy_price, stop_pct))


@app.post(f"{API_PREFIX}/positions/{{position_id}}/close", dependencies=[Depends(require_token)])
def close_position(position_id: int, body: PositionCloseIn) -> OkOut:
    if body.sell_time and len(body.sell_time) == 8 and body.sell_time.isdigit():
        sell_date = datetime.strptime(body.sell_time, "%Y%m%d").date()
    else:
        sell_date = date.today()
    ok = pos_store.close_position(position_id, sell_price=body.sell_price, sell_date=sell_date, db_path=_db())
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"ok": False, "reason": "not_holding"},
        )
    return OkOut(ok=True)


# —— v1.1-C 自选池(watchlist)+ 同花顺 txt 互转/对账 ————————————————————————
# **增删只经本节端点**(任务拍板「增删只经用户端点,系统代码路径绝不写入」)——
# `neckline.watchlist` 的写入函数(`add_watchlist`/`remove_watchlist`/`set_pinned`)
# 只应被这里调用,报告管线/哨兵/问询台只调用只读的 `list_watchlist*`。

def _latest_watchlist_check_by_code() -> Dict[str, Dict[str, Any]]:
    """最近一份报告的自选体检快照,按 `ts_code` 建索引(`GET /watchlist`「列表 +
    各只体检最近快照」,plan C.1)。查无报告 / 该报告体检节为空 → 空 dict(该票
    尚未被任何报告体检过,`check` 字段回 None,不是报错)。"""
    td = report_store.latest_report_date(db_path=_db())
    if not td:
        return {}
    rep = report_store.load_report_by_str(td, db_path=_db())
    if rep is None:
        return {}
    return {w["ts_code"]: w for w in rep.get("watchlist", []) if isinstance(w, dict) and w.get("ts_code")}


def _shape_watchlist_item(item: "watchlist_store.WatchlistItem", check_by_code: Dict[str, Dict[str, Any]]) -> WatchlistItemOut:
    check_raw = check_by_code.get(item.ts_code)
    return WatchlistItemOut(
        code=item.ts_code, name=item.name, addedAt=item.added_at,
        source=item.source, note=item.note or "", pinned=item.pinned,
        updatedAt=item.updated_at,
        check=_shape_watchlist_check(check_raw) if check_raw else None,
    )


@app.get(f"{API_PREFIX}/watchlist", dependencies=[Depends(require_token)])
def get_watchlist() -> WatchlistOut:
    items = watchlist_store.list_watchlist(db_path=_db())
    check_by_code = _latest_watchlist_check_by_code()
    return WatchlistOut(
        items=[_shape_watchlist_item(w, check_by_code) for w in items],
        maxSize=watchlist_store.MAX_WATCHLIST_SIZE,
    )


@app.post(f"{API_PREFIX}/watchlist", dependencies=[Depends(require_token)])
def post_watchlist(body: WatchlistAddIn) -> WatchlistAddOut:
    """加一只自选(**≤30 上限硬校验,超限 422**,任务拍板)。已存在该代码 → 幂等
    更新 name/note,不算新增、不占额度。"""
    try:
        item = watchlist_store.add_watchlist(
            body.code, name=body.name, note=body.note, db_path=_db(),
        )
    except watchlist_store.WatchlistFullError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"ok": False, "reason": "watchlist_full", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"ok": False, "reason": "invalid_code", "message": str(e)},
        )
    return WatchlistAddOut(item=_shape_watchlist_item(item, {}))


@app.delete(f"{API_PREFIX}/watchlist/{{code}}", dependencies=[Depends(require_token)])
def delete_watchlist(code: str) -> OkOut:
    ok = watchlist_store.remove_watchlist(code, db_path=_db())
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.put(f"{API_PREFIX}/watchlist/{{code}}/pin", dependencies=[Depends(require_token)])
def put_watchlist_pin(code: str, body: WatchlistPinIn) -> OkOut:
    ok = watchlist_store.set_pinned(code, body.pinned, db_path=_db())
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/watchlist/reconcile-ths", dependencies=[Depends(require_token)])
def reconcile_ths(file: UploadFile = File(...)) -> ThsReconcileOut:
    """同花顺自选 txt 对账(plan C.4)。拿同花顺 PC 端导出的 txt(一行一代码)与
    Neckline 当前自选池比差集;不做任何写入(对齐动作由客户端按差异结果调上面
    的 CRUD 端点)。"""
    content = file.file.read()
    ths_codes = watchlist_store.parse_ths_txt(content)
    neckline_codes = watchlist_store.list_watchlist_codes(db_path=_db())
    diff = watchlist_store.reconcile_ths(ths_codes, neckline_codes)
    return ThsReconcileOut(**diff)


@app.get(f"{API_PREFIX}/watchlist/export-ths", dependencies=[Depends(require_token)])
def export_ths() -> ThsExportOut:
    """导出当前自选为同花顺可导入 txt(plan C.4)。"""
    codes = watchlist_store.list_watchlist_codes(db_path=_db())
    return ThsExportOut(text=watchlist_store.export_ths_txt(codes), count=len(codes))


# —— 4A.5 问询台 ————————————————————————————————————————————————————————

def _inquiry_basis_pool_date() -> tuple:
    """确定性检查的 EOD 基准日 + 入池当日(v1.1-D 问询窗口修复后语义变化)。

    basis:确定性检查用的 EOD 数据基准日,不变——最近一份已生成报告的交易日
    (最可靠的"有数据日");无报告 → 日历默认(今日交易日则今日,否则上一交易日)。

    pool_date(v1.1-D 简化,**不再等于 basis**):`add_to_inquiry_pool` 的
    `trade_date` 参数只是"这票哪天被问询入池"的审计留痕,**不再承担"该被哪份报告
    消费"的职责**——旧写法 `pool_date == basis_date` 会让 16:35 报告已生成后才
    问询通过的票,入池 `trade_date` 停留在"今天"(因为此时 basis 已经能读到今天
    的报告),而下一份该消费它的报告是明天的,`trade_date` 与明天的 report_date
    永远对不上 → 永久掉缝(生产真洞,详见 PROJECT_PLAN §五 v1.1-D.1 根因)。
    消费改靠 `inquiry_pool.consumed_report_date` 待消费标记(`build_report` 侧,
    `neckline.api.stores.load_pending_inquiry_codes`),故 pool_date 直接取「今日
    交易日历口径」即可,不必再绑定 basis。"""
    lr = report_store.latest_report_date(db_path=_db())
    if lr:
        basis = datetime.strptime(lr, "%Y%m%d").date()
    else:
        today0 = date.today()
        basis = today0 if is_trading_day(today0) else prev_trading_day(today0)
    today = date.today()
    pool_date = today if is_trading_day(today) else prev_trading_day(today)
    return basis, pool_date


@app.post(f"{API_PREFIX}/inquiry", dependencies=[Depends(require_token)])
def inquiry(body: InquiryIn) -> InquiryOut:
    """问询台(§2.5):确定性检查(纪律 + 同码评分 + 板块年龄)→ LLM 自然语言 → 裁决二值。
    永不产「买」(裁决枚举只两值 + system prompt guardrail + 代码级裁决)。缺 key → 确定性
    照跑、LLM 段占位降级,不崩。"""
    basis_date, pool_date = _inquiry_basis_pool_date()
    provider = (_PROVIDER_FN or (lambda dbp: get_provider(db_path=dbp)))(_db())
    quotes_fn = _QUOTES_FN
    if quotes_fn is None:
        from neckline.sentinel.quotes import get_quotes
        quotes_fn = get_quotes
    result = run_inquiry(
        body.code,
        [{"role": m.role, "content": m.content} for m in body.messages],
        basis_date=basis_date, pool_date=pool_date, db_path=_db(),
        provider=provider, quotes_fn=quotes_fn, panel_fn=_PANEL_FN,
    )
    return InquiryOut(
        ok=True, code=body.code, reply=result["reply"], verdict=result["verdict"],
        evidence=result["evidence"], degraded=result["degraded"],
    )


# —— 4A.5 设置 + 设备注册 ——————————————————————————————————————————————

@app.get(f"{API_PREFIX}/settings", dependencies=[Depends(require_token)])
def get_settings() -> SettingsOut:
    """读设置(不回 key 明文,只回 llmKeySet:bool)。"""
    st = get_app_settings(db_path=_db())
    return SettingsOut(
        llmProvider=st.llm_provider,
        llmKeySet=st.llm_key_set,
        push=PushSettingsOut(
            report=st.push_report, retreatBrake=st.push_retreat,
            precall=st.push_precall, d5exit=st.push_d5exit,
        ),
        reviewColMap=st.review_col_map,
    )


@app.put(f"{API_PREFIX}/settings/llm", dependencies=[Depends(require_token)])
def put_settings_llm(body: SettingsLLMIn) -> OkOut:
    """写 LLM 供应商 + key(🔴)。`get_provider()` 下次调用即现读 DB 生效(运行时,不重启)。
    key 绝不回日志 / 绝不回响应明文;provider 白名单由 schema Literal + settings_store 双校验。"""
    set_llm(body.provider, body.apiKey, db_path=_db())
    return OkOut(ok=True)


@app.put(f"{API_PREFIX}/settings/push", dependencies=[Depends(require_token)])
def put_settings_push(body: SettingsPushIn) -> OkOut:
    """写 APNs 四类推送开关(v1.1-G.1:契约扩至四字段,`app_settings.push_precall`/
    `push_d5exit` 两列在 v1.1-A/B 已建,本端点补上写入接线)。"""
    set_push(body.report, body.retreatBrake, body.precall, body.d5exit, db_path=_db())
    return OkOut(ok=True)


@app.put(f"{API_PREFIX}/settings/review-col-map", dependencies=[Depends(require_token)])
def put_settings_review_col_map(body: SettingsReviewColMapIn) -> OkOut:
    """周复盘交割单列映射(plan 4D.1「留 review_col_map 可覆盖以支持两家券商原始
    格式」)。空字典 → 清空覆盖,`/review/upload` 退回内置默认列名。"""
    set_review_col_map(body.colMap, db_path=_db())
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/devices", dependencies=[Depends(require_token)])
def register_device(body: DeviceRegisterIn) -> OkOut:
    upsert_device(body.token, body.platform, db_path=_db())
    return OkOut(ok=True)


# —— 4D 周复盘工作台(对账引擎;plan 4D.1/4D.2)——————————————————————————————
# **同码不重写**:解析(review.parse)/ FIFO 对账(review.reconcile)/ 材料(review.material)
# 全部复用 `neckline/review/` 领域模块,端点只做「装配 + 出入参映射」。

@app.post(f"{API_PREFIX}/review/upload", dependencies=[Depends(require_token)])
def review_upload(files: List[UploadFile] = File(...)) -> ReviewUploadOut:
    """拖入一份或多份 xlsx 交割单 → 解析 → 对账 → 落 `reviews` 表(幂等覆盖同周)。
    单份文件解析失败(非法工作簿)不拖垮其它文件;某一行数据有瑕疵只降级为
    `parseWarnings`,不中断整份解析(§4D.1「解析失败逐行降级、缺列优雅提示,不崩」)。
    """
    from neckline.review.material import build_material_text
    from neckline.review.parse import parse_workbook
    from neckline.review.reconcile import run_weekly_review, weekly_review_dict
    from neckline.review.store import save_weekly_review

    app_settings = get_app_settings(db_path=_db())
    col_map = app_settings.review_col_map or None

    all_trades = []
    parse_warnings: List[str] = []
    sheet_formats: Dict[str, str] = {}
    for f in files:
        filename = f.filename or "未命名文件"
        content = f.file.read()   # 同步端点,走底层文件对象(免 await,与全项目其它端点风格一致)
        try:
            result = parse_workbook(content, filename, col_map=col_map, db_path=_db())
        except ValueError as e:
            parse_warnings.append(f"{filename}:{e}")
            continue
        all_trades.extend(result.trades)
        parse_warnings.extend(f"{filename} · {w.sheet}!row{w.row}:{w.message}" for w in result.warnings)
        for sheet_name, fmt in result.sheet_formats.items():
            sheet_formats[f"{filename} · {sheet_name}"] = fmt

    reviews, data_warnings = run_weekly_review(all_trades, db_path=_db())
    weeks_out: List[WeeklyReviewOut] = []
    for review in reviews:
        material = build_material_text(review)
        try:
            save_weekly_review(review, material=material, db_path=_db())
        except Exception:  # noqa: BLE001  落库失败不应丢掉已算出的对账结果
            logger.warning("周复盘落库失败(%s),响应仍返回本次算出的结果", review.week, exc_info=True)
        weeks_out.append(WeeklyReviewOut(week=review.week, result=weekly_review_dict(review), material=material))

    return ReviewUploadOut(
        ok=True, weeks=weeks_out, parseWarnings=parse_warnings,
        dataWarnings=data_warnings, sheetFormats=sheet_formats,
    )


@app.get(f"{API_PREFIX}/review", dependencies=[Depends(require_token)])
def review_by_week(week: str = "") -> ReviewGetOut:
    """读某周已存档的对账结果(历史回放;客户端务必走 makeURL 免 `?` 编码坑,同
    `GET /report?date=` 惯例)。缺 week / 查无该周 → `found=False`(HTTP 200,
    降级契约同 `_empty_report`,不是 404——"这周还没上传过交割单"是正常场景)。"""
    from neckline.review.store import load_weekly_review

    week = (week or "").strip()
    if not week:
        return ReviewGetOut(ok=True, found=False)
    rec = load_weekly_review(week, db_path=_db())
    if rec is None:
        return ReviewGetOut(ok=True, found=False, week=week)
    return ReviewGetOut(
        ok=True, found=True, week=rec["week"], generatedAt=rec["generatedAt"],
        result=rec["result"], material=rec.get("material") or "",
    )


__all__ = ["app", "VERSION", "API_PREFIX"]
