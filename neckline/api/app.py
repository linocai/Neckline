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
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, status

from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.inquiry import run_inquiry
from neckline.api.schemas import (
    BoardEventOut,
    BoardOut,
    CandidateOut,
    DeviceRegisterIn,
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
    SettingsLLMIn,
    SettingsOut,
    SettingsPushIn,
)
from neckline.api.stores import upsert_device
from neckline.calendar import is_trading_day, prev_trading_day
from neckline.config import ensure_data_dirs, settings
from neckline.llm.factory import get_provider
from neckline.report import store as report_store
from neckline.sentinel import dedup
from neckline.sentinel import positions as pos_store
from neckline.sentinel.intraday import is_intraday_now
from neckline.settings_store import get_app_settings, set_llm, set_push

logger = logging.getLogger(__name__)

VERSION = "0.4.0-stage4A"
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


def _db() -> Optional[Path]:
    return _DB_PATH_OVERRIDE


# —— 哨兵后台轮询(4B.3;折进 lifespan asyncio,单 unit 省内存)——————————————————

async def _sentinel_loop(stop_event: asyncio.Event) -> None:
    """交易时段每 60s 调 `run_tick`(阻塞活 run in thread,不卡事件循环);退潮首次触发
    → APNs 刹车推送(只推两类之一)。非交易时段优雅待机(每 5min 探一次,不空转)。"""
    from neckline.sentinel.engine import run_tick

    logger.info("哨兵后台轮询已挂载(单 unit lifespan asyncio)")
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
            t = now.time()
            from datetime import time as _t
            interval = _SENTINEL_LUNCH_POLL_SEC if _t(11, 30) <= t < _t(13, 0) else _SENTINEL_POLL_SEC
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


def _shape_report(rep: Dict[str, Any]) -> ReportOut:
    td = rep["trade_date"]
    d = datetime.strptime(td, "%Y%m%d").date()
    judgments = {j["ts_code"]: j for j in report_store.load_llm_judgments(d, db_path=_db())}
    candidates = [_shape_candidate(c, judgments.get(c.get("ts_code", ""))) for c in rep.get("candidates", [])]
    return ReportOut(
        tradeDate=td,
        generatedAt=rep.get("generated_at", ""),
        strategyVersion=rep.get("strategy_version", ""),
        sentiment=rep.get("sentiment", {}),
        sectors=rep.get("sectors", []),
        candidates=candidates,
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
        if e["sentinel"] == "retreat":
            continue  # 退潮进红条,不进事件列表
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


def _stop_line(buy_price: float) -> float:
    """派生止损线 = 买入价 ×(1-5%)(§2.1 -5% 单一常量)。"""
    return round(buy_price * 0.95, 2)


@app.get(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def list_positions() -> PositionsOut:
    holdings = pos_store.load_open_positions(db_path=_db())
    codes = [h.ts_code for h in holdings]
    names = _resolve_names(codes)
    prices = _resolve_prices(codes)
    out: List[PositionOut] = []
    for h in holdings:
        out.append(PositionOut(
            id=h.id, code=h.ts_code, name=names.get(h.ts_code, h.ts_code),
            buyPrice=h.buy_price, qty=h.qty, entryReason=h.note or "",
            buyDate=h.buy_date, price=prices.get(h.ts_code, 0.0) or 0.0,
            status=h.status, stopLine=_stop_line(h.buy_price), stopOrderChecked=False,
        ))
    return PositionsOut(holdings=out)


@app.post(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def open_position(body: PositionOpenIn) -> PositionOpenOut:
    """开仓录入(§3.8 铁律:系统永不自动下单,此处只录台账)。buy_date 取今日自然日。"""
    pid = pos_store.open_position(
        ts_code=body.code, buy_price=body.buy_price, qty=body.qty,
        buy_date=date.today(), note=(body.entry_reason or None), db_path=_db(),
    )
    return PositionOpenOut(ok=True, position_id=pid, stop_line=_stop_line(body.buy_price))


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


# —— 4A.5 问询台 ————————————————————————————————————————————————————————

def _inquiry_basis_pool_date() -> tuple:
    """确定性检查的 EOD 基准日 + 海选池目标日(§2.5)。basis = 最近一份报告的交易日
    (最可靠的"有数据日");无报告 → 日历默认(今日交易日则今日,否则上一交易日)。
    pool_date == basis_date(海选池按数据日入池,报告为该日(重)生成时纳入)。"""
    lr = report_store.latest_report_date(db_path=_db())
    if lr:
        d = datetime.strptime(lr, "%Y%m%d").date()
        return d, d
    today = date.today()
    d = today if is_trading_day(today) else prev_trading_day(today)
    return d, d


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
        push=PushSettingsOut(report=st.push_report, retreatBrake=st.push_retreat),
    )


@app.put(f"{API_PREFIX}/settings/llm", dependencies=[Depends(require_token)])
def put_settings_llm(body: SettingsLLMIn) -> OkOut:
    """写 LLM 供应商 + key(🔴)。`get_provider()` 下次调用即现读 DB 生效(运行时,不重启)。
    key 绝不回日志 / 绝不回响应明文;provider 白名单由 schema Literal + settings_store 双校验。"""
    set_llm(body.provider, body.apiKey, db_path=_db())
    return OkOut(ok=True)


@app.put(f"{API_PREFIX}/settings/push", dependencies=[Depends(require_token)])
def put_settings_push(body: SettingsPushIn) -> OkOut:
    set_push(body.report, body.retreatBrake, db_path=_db())
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/devices", dependencies=[Depends(require_token)])
def register_device(body: DeviceRegisterIn) -> OkOut:
    upsert_device(body.token, body.platform, db_path=_db())
    return OkOut(ok=True)


__all__ = ["app", "VERSION", "API_PREFIX"]
