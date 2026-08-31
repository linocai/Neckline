"""FastAPI 应用主体：鉴权、输入输出映射、启动初始化与早晨两拍编排。"""

from __future__ import annotations

import asyncio
import logging
import zipfile
from io import BytesIO
import math
import os
from contextlib import asynccontextmanager
# `date_cls` 别名:多个端点用 `date: str = ""` 作查询参数名(客户端契约),
# 函数内会把模块级的 `date` 类型遮住 —— 别名让「今天」这种取值仍拿得到。
from datetime import date, datetime, time, timedelta
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status

from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.schemas import (
    DeviceRegisterIn,
    LLMRoutesIn,
    LLMRoutesOut,
    OkOut,
    ProviderCreateIn,
    ProviderOut,
    ProviderUpdateIn,
    ProvidersListOut,
    PushKindOut,
    PushSettingsOut,
    ReviewBinderyOut,
    ReviewConclusionIn,
    ReviewConclusionsOut,
    ReviewGetOut,
    ReviewOverviewOut,
    ReviewSegmentOut,
    ReviewUploadOut,
    SettingsOut,
    SettingsProviderOut,
    SettingsPushIn,
    SettingsReviewColMapIn,
    TavilySettingsIn,
    TavilySettingsOut,
    WeeklyReviewOut,
)
from neckline.api.stores import upsert_device
from neckline.calendar import CN_TZ, is_trading_day, trading_days_between
from neckline.config import ensure_data_dirs, settings
from neckline.llm.factory import get_provider
from neckline import notify_kinds
from neckline import dedup
from neckline.settings_store import (
    create_provider,
    delete_provider,
    get_app_settings,
    get_llm_routes,
    get_tavily_api_key,
    list_providers_public,
    set_llm_routes,
    set_push_kinds,
    set_review_col_map,
    set_tavily_api_key,
    update_provider,
)

logger = logging.getLogger(__name__)

# P2-C：复盘上传是用户输入边界，不依赖 Content-Length（它只是优化，不能作判据）。
REVIEW_UPLOAD_MAX_FILES = 5
REVIEW_UPLOAD_MAX_SINGLE_BYTES = 10 * 1024 * 1024
REVIEW_UPLOAD_MAX_TOTAL_BYTES = 20 * 1024 * 1024
REVIEW_UPLOAD_MAX_UNZIPPED_BYTES = 100 * 1024 * 1024


def _read_review_upload(upload: UploadFile, *, total_before: int) -> bytes:
    """有上限地读取单文件，并在解析前拒绝 zip bomb。"""
    data = upload.file.read(REVIEW_UPLOAD_MAX_SINGLE_BYTES + 1)
    if len(data) > REVIEW_UPLOAD_MAX_SINGLE_BYTES:
        raise HTTPException(status_code=413, detail="单个文件不能超过 10 MB")
    if total_before + len(data) > REVIEW_UPLOAD_MAX_TOTAL_BYTES:
        raise HTTPException(status_code=413, detail="本次上传总计不能超过 20 MB")
    try:
        with zipfile.ZipFile(BytesIO(data)) as workbook:
            if sum(info.file_size for info in workbook.infolist()) > REVIEW_UPLOAD_MAX_UNZIPPED_BYTES:
                raise HTTPException(status_code=413, detail="工作簿解压后不能超过 100 MB")
    except zipfile.BadZipFile:
        # 非 xlsx 仍交由既有逐文件解析告知用户；它不是配额问题。
        pass
    return data

# API 版本必须与客户端工程同步；后端热修复用 release set 后缀单独标识。
# 部署是否生效以生产 health 响应为准。
VERSION = "v2.7.0"
RELEASE_SET = "v2.7.0-b21"
API_PREFIX = "/api/v1"

# —— 测试注入开关(生产恒 True / 恒默认)——————————————————————————————————
# startup 是否挂 9:26/10:00 两拍；冒烟可显式关闭。
ENABLE_MORNING_TASKS = os.environ.get("NECKLINE_ENABLE_MORNING_TASKS", "1") != "0"
_DB_PATH_OVERRIDE: Optional[Path] = None      # 隔离库(None → settings.db_path)
_PARQUET_DIR_OVERRIDE: Optional[Path] = None  # 隔离 parquet 根(None → settings.parquet_dir)
_DATA_DIR_OVERRIDE: Optional[Path] = None     # 隔离 data 根(None → settings.data_dir)


def _db() -> Optional[Path]:
    return _DB_PATH_OVERRIDE


#: 非窗口时段的待机探测间隔(PROJECT_PLAN §5.7.3「非窗口时段 5 分钟一探,不空转」)。
_MORNING_IDLE_POLL_SEC = 300
#: 两个窗口附近收紧的探测间隔。
#:
#: 🔴 **为什么必须收紧,⛔ 不能只留 5 分钟一探**:两拍的窗口本身都比 5 分钟窄或等宽
#: —— 核对表 9:26–9:29 **只有 3 分钟**,结算拍 10:00–10:05 **正好 5 分钟**。
#: 5 分钟一探撞上等宽窗口时,相邻两次探测可以一次落在 9:59、下一次落在 10:04(赶上),
#: 也可以一次落在 9:58、下一次落在 10:05:30(**整窗错过**)——「今天跑没跑过」
#: 于是变成一道看运气的题。收紧到 30 秒后,两个窗口各自至少被探到 6 次。
_MORNING_TIGHT_POLL_SEC = 30
_INTRADAY_CAPTURE_POLL_SEC = 30

#: 收紧区间(**各自比窗口早开一点**,给「起 tick → 读清单 → 读预案」留出余量)。
#: ⚠ 判据的单一源仍在两个 tick 自己那里(`is_auction_window` / `is_settle_window`)——
#: 这里只决定**多久探一次**,⛔ 不决定跑不跑。
_TIGHT_WINDOWS: Tuple[Tuple[time, time], ...] = (
    (time(9, 20), time(9, 30)),      # 9:26–9:29 竞价核对表
    (time(9, 55), time(10, 6)),      # 10:00–10:05 结算拍(裁定 10)
)


def _is_tight_poll(now: datetime) -> bool:
    """交易日 且 落在任一收紧区间内。"""
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return any(start <= t < end for start, end in _TIGHT_WINDOWS)


async def _morning_loop(stop_event: asyncio.Event) -> None:
    """9:26 核对与 10:00 结算的早晨轮询。

    系统不持续观察盘中价格，也不跟踪个人交易。两拍各自独立 ``try/except``：
    | 拍 | 窗口 | 推送 | 产物 |
    |---|---|---|---|
    | 竞价核对表 | 9:26–9:29 | 有(APNs) | `已触发放弃 / 待开盘后观察` 两段,⛔ 无「成立」 |
    | 结算拍(裁定 10) | 10:00–10:05 | **无** | 四分支结果追加至成绩包 |

    🔴 ⛔ **不新增 systemd unit** —— 两拍都跑在既有常驻 `neckline.service` 里
    (PROJECT_PLAN §9.3)。多一个 unit 就多一条双跑路径,而「当日只跑一次」记在
    `neckline/dedup.py` 的台账里,双触发会把「今天跑没跑过」变成一道要现场推理的题。

    🔴 **推送只在第一拍**:结算拍**零推送**(裁定 10 —— 它是结算,不是提醒)。
    本函数里 `notify.*` 只出现在竞价那一支下面,守门单测 G21 跑一次结算断言
    APNs 调用计数 = 0。
    """
    logger.info("早晨轮询已挂载(S8:9:26 竞价核对表 + 10:00 结算拍,零新增 unit)")
    while not stop_event.is_set():
        # Scheduling semantics are A-share exchange time, never host-local
        # time (NB cloud/container timezone is only defence in depth).
        now = datetime.now(CN_TZ)
        # 🔴 两拍**各自独立** `try/except`(§5.7.3):一拍炸了不影响另一拍。
        # 🔴 **两拍都丢进线程池**(R2-09):它们内部做 HTTP + SQLite,是**同步阻塞**。
        # 单源最坏 `2 × (3+5) = 16 s`(`data/realtime.py` 的 connect=3s / read=5s /
        # 2 次尝试),双源顺序 → 单拍最坏约 **32 s**,而收紧区间的轮询间隔是 30 s
        # —— 阻塞窗口与轮询周期**重叠**。行情源抽风时 `/api/v1/health`、`/api/v1/checklist/{date}`、
        # `/selection/latest` 会一起卡住,而 9:26–9:29 与 9:55–10:06 正是用户盯着
        # App 看核对表的时刻(他看到的是「App 转圈」,日志只说「实时源请求异常」)。
        # ⛔ 别改成 `create_task` + 同步函数,那不解决阻塞;两拍本来就是纯同步、
        # 不共享状态,SQLite 也是每次新连接。
        # ⚠ 写成 `to_thread(lambda: f(now))` 而不是 `to_thread(f, now)`:守门
        # (G21 那组)按**字面**核「两拍是不是各自从这个循环里被调起来的」,
        # 而 `to_thread(f, now)` 里根本没有 `f(now)` 这个形状。⛔ 别顺手"清理"掉。
        try:
            await asyncio.to_thread(lambda: _morning_checklist_tick(now))
        except Exception:  # noqa: BLE001 —— 早晨循环不许被单拍异常掀翻
            logger.warning("[morning] 竞价核对表那一拍异常(已吞,不影响结算拍)",
                           exc_info=True)
        try:
            await asyncio.to_thread(lambda: _morning_settle_tick(now))
        except Exception:  # noqa: BLE001
            logger.warning("[morning] 10:00 结算拍异常(已吞,不影响竞价拍)", exc_info=True)
        interval = _MORNING_TIGHT_POLL_SEC if _is_tight_poll(now) else _MORNING_IDLE_POLL_SEC
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _intraday_capture_loop(stop_event: asyncio.Event) -> None:
    """Freeze real provider snapshots for due K9-v3 packages and their benchmark.

    This runs in the same single-process service as the D1 two-patch loop;
    deploy/neckline.service must not add uvicorn workers or a second recorder
    unit, otherwise a source point can be duplicated by separate processes.
    """
    from neckline.auction.recorder import is_capture_window, record_snapshot

    logger.info("盘中分时采样已挂载（K9-v3 候选及冻结基准）")
    while not stop_event.is_set():
        now = datetime.now(CN_TZ)
        if is_capture_window(now, db_path=_db() or settings.db_path):
            try:
                result = await asyncio.to_thread(
                    lambda: record_snapshot(now, db_path=_db() or settings.db_path, parquet_dir=_PARQUET_DIR_OVERRIDE)
                )
                if result.ran:
                    logger.debug("[intraday] captured=%s unavailable=%s", result.captured, result.unavailable)
            except Exception:  # noqa: BLE001 -- a recorder outage must not kill API or morning settlement
                logger.warning("[intraday] 分时采样异常，已由审计/结算如实标不可评价", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_INTRADAY_CAPTURE_POLL_SEC)
        except asyncio.TimeoutError:
            pass


def _morning_checklist_tick(now: datetime) -> None:
    """9:26—9:29 那一拍:跑核对表 → 落库 → **判门槛** → 推一条 APNs。

    ⚠ 窗口外 / 当日已跑 → `run_checklist_tick` 内部**零落库**直接返回,
    本函数只是把它调起来;⛔ 这里不复判窗口(判据的单一源在 `pipeline.py`)。"""
    from neckline.auction import pipeline as auction_pipeline
    from neckline.auction.readings import collect_auction_readings
    from neckline.dedup import (
        already_pushed, delivered_device_keys, record_device_delivered, record_pushed,
    )

    target_db = _db() or settings.db_path
    if not auction_pipeline.is_auction_window(now, db_path=target_db):
        return
    readings = collect_auction_readings(now.date(), db_path=target_db, parquet_dir=_PARQUET_DIR_OVERRIDE)
    res = auction_pipeline.run_checklist_tick(now, db_path=target_db,
                                              parquet_dir=_PARQUET_DIR_OVERRIDE, readings=readings)
    if res.skipped_reason:
        logger.debug("[morning] 竞价核对表跳过:%s", res.skipped_reason)
        return
    # 推送门槛的单一源是 `ChecklistRunResult.should_push`,⛔ 不在这里另判一次。
    if res.should_push and not already_pushed(now.date(), auction_pipeline.AUCTION_SCOPE, "",
                                               auction_pipeline.EVENT_CHECKLIST, db_path=target_db):
        delivered = delivered_device_keys(
            now.date(), auction_pipeline.AUCTION_SCOPE, "", auction_pipeline.EVENT_CHECKLIST,
            db_path=target_db,
        )
        delivery_id = (
            f"{now.date():%Y%m%d}:{auction_pipeline.AUCTION_SCOPE}:"
            f"{auction_pipeline.EVENT_CHECKLIST}"
        )
        outcome = notify.push_checklist_summary(
            res.counts, db_path=target_db, skip_device_keys=delivered, delivery_id=delivery_id,
        )
        for device_key in outcome.delivered_device_keys:
            record_device_delivered(
                now.date(), auction_pipeline.AUCTION_SCOPE, "", auction_pipeline.EVENT_CHECKLIST,
                device_key, db_path=target_db,
            )
        # Aggregate completion is written only after every current target is
        # terminal.  Transient failures leave the event open, while successful
        # devices are skipped on the next 30-second replay.
        if outcome.delivery_complete:
            record_pushed(now.date(), auction_pipeline.AUCTION_SCOPE, "", auction_pipeline.EVENT_CHECKLIST,
                          payload=res.counts, db_path=target_db)


def _morning_settle_tick(now: datetime) -> None:
    """10:00—10:05 追加同一 K9-v3 成绩包的 D1 开盘阶段，零推送。"""
    from neckline.auction import settle as auction_settle
    from neckline.auction.readings import collect_open_readings

    target_db = _db() or settings.db_path
    if not auction_settle.is_settle_window(now, db_path=target_db):
        return
    readings = collect_open_readings(now.date(), db_path=target_db, parquet_dir=_PARQUET_DIR_OVERRIDE)
    res = auction_settle.run_settle_tick(now, db_path=target_db,
                                         parquet_dir=_PARQUET_DIR_OVERRIDE, readings=readings)
    if res.skipped_reason:
        logger.debug("[morning] 结算拍跳过:%s", res.skipped_reason)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # —— startup ——
    require_api_token_ready()                 # fail-fast:API_TOKEN len>=16
    ensure_data_dirs()
    from neckline.db import init_schema
    init_schema(_db())
    app.state._stop_event = asyncio.Event()
    app.state._morning_task = None
    app.state._intraday_task = None
    if ENABLE_MORNING_TASKS:
        app.state._morning_task = asyncio.create_task(_morning_loop(app.state._stop_event))
        app.state._intraday_task = asyncio.create_task(_intraday_capture_loop(app.state._stop_event))
    yield
    # —— shutdown ——
    app.state._stop_event.set()
    task = app.state._morning_task
    if task is not None:
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
    intraday_task = app.state._intraday_task
    if intraday_task is not None:
        try:
            await asyncio.wait_for(intraday_task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            intraday_task.cancel()


app = FastAPI(title="Neckline", version=VERSION, lifespan=lifespan)


# —— health(免鉴权,供 nginx / 客户端自检)————————————————————————————————

@app.get(f"{API_PREFIX}/health")
def health() -> dict:
    return {"status": "ok", "version": VERSION, "releaseSet": RELEASE_SET}


# —— 4A.5 设置 + 设备注册 ——————————————————————————————————————————————

@app.get(f"{API_PREFIX}/settings", dependencies=[Depends(require_token)])
def get_settings() -> SettingsOut:
    """读设置(不回 key 明文,只回 keySet:bool)。V2-② 起 `providers`/`routes` 取代
    V1 的 `llmProvider`/`llmKeySet`(plan §五 V2-②「契约变更」)。"""
    st = get_app_settings(db_path=_db())
    routes, _default_provider = get_llm_routes(db_path=_db())
    providers = [
        SettingsProviderOut(
            name=p.name, model=p.model, hasWebSearch=p.has_web_search,
            keySet=p.key_set, enabled=p.enabled,
        )
        for p in list_providers_public(db_path=_db())
    ]
    return SettingsOut(
        providers=providers,
        routes=routes,
        tavily=TavilySettingsOut(keySet=st.tavily_key_set),
        push=PushSettingsOut(kinds=[
            PushKindOut(
                kind=k, level=notify_kinds.level_of(k),
                label=notify_kinds.KIND_LABEL[k], enabled=st.push_kinds[k],
            )
            for k in notify_kinds.ALL_KINDS
        ]),
        reviewColMap=st.review_col_map,
    )


# —— LLM Provider 注册表 —————————————————————————————————————————————

def _provider_out(rec) -> ProviderOut:
    return ProviderOut(
        name=rec.name, baseUrl=rec.base_url, model=rec.model, hasWebSearch=rec.has_web_search,
        searchEngine=rec.search_engine, notes=rec.notes, enabled=rec.enabled,
        keySet=bool(rec.api_key),
    )


@app.get(f"{API_PREFIX}/settings/providers", dependencies=[Depends(require_token)])
def list_settings_providers() -> ProvidersListOut:
    """列出全部 Provider(不含 key 明文)。"""
    return ProvidersListOut(items=[
        ProviderOut(
            name=p.name, baseUrl=p.base_url, model=p.model, hasWebSearch=p.has_web_search,
            searchEngine=p.search_engine, notes=p.notes, enabled=p.enabled, keySet=p.key_set,
        )
        for p in list_providers_public(db_path=_db())
    ])


@app.post(f"{API_PREFIX}/settings/providers", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_token)])
def create_settings_provider(body: ProviderCreateIn) -> ProviderOut:
    """新建 Provider(🔴,自填制:任意 OpenAI 兼容端点)。`name` 已存在 → 409。"""
    try:
        rec = create_provider(
            body.name, body.baseUrl, body.model, api_key=body.apiKey,
            has_web_search=body.hasWebSearch, search_engine=body.searchEngine,
            notes=body.notes, enabled=body.enabled, db_path=_db(),
        )
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail={"ok": False, "reason": "already_exists"})
    return _provider_out(rec)


@app.put(f"{API_PREFIX}/settings/providers/{{name}}", dependencies=[Depends(require_token)])
def update_settings_provider(name: str, body: ProviderUpdateIn) -> ProviderOut:
    """局部更新 Provider(🔴)。未出现的字段不改(pydantic v2 `model_fields_set`
    判"键缺失 vs 显式传值"的既定体例,CLAUDE.md 定案);`name` 不存在 → 404。
    `get_provider()` 下次调用即现读 DB 生效(运行时,不重启)。"""
    fields = body.model_fields_set
    kwargs: Dict[str, Any] = {}
    if "baseUrl" in fields:
        kwargs["base_url"] = body.baseUrl
    if "model" in fields:
        kwargs["model"] = body.model
    if "apiKey" in fields:
        kwargs["api_key"] = body.apiKey
    if "hasWebSearch" in fields:
        kwargs["has_web_search"] = body.hasWebSearch
    if "searchEngine" in fields:
        kwargs["search_engine"] = body.searchEngine
    if "notes" in fields:
        kwargs["notes"] = body.notes
    if "enabled" in fields:
        kwargs["enabled"] = body.enabled
    rec = update_provider(name, db_path=_db(), **kwargs)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return _provider_out(rec)


@app.delete(f"{API_PREFIX}/settings/providers/{{name}}", dependencies=[Depends(require_token)])
def delete_settings_provider(name: str) -> OkOut:
    """删除 Provider(🔴)。不存在 → 404。"""
    if not delete_provider(name, db_path=_db()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.get(f"{API_PREFIX}/settings/llm-routes", dependencies=[Depends(require_token)])
def get_settings_llm_routes() -> LLMRoutesOut:
    """读任务→Provider 路由表 + 默认 Provider。"""
    routes, default_provider = get_llm_routes(db_path=_db())
    return LLMRoutesOut(routes=routes, defaultProvider=default_provider)


@app.put(f"{API_PREFIX}/settings/llm-routes", dependencies=[Depends(require_token)])
def put_settings_llm_routes(body: LLMRoutesIn) -> LLMRoutesOut:
    """全量覆盖式写任务→Provider 路由表 + 默认 Provider(🔴,同 push 六开关必填
    风格)。`routes` 出现不认识的任务名 → 422(`reason="invalid_task"`)。"""
    try:
        set_llm_routes(body.routes, body.defaultProvider, db_path=_db())
    except LookupError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_provider", "message": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_task", "message": str(e)})
    routes, default_provider = get_llm_routes(db_path=_db())
    return LLMRoutesOut(routes=routes, defaultProvider=default_provider)


@app.put(f"{API_PREFIX}/settings/tavily", dependencies=[Depends(require_token)])
def put_settings_tavily(body: TavilySettingsIn) -> TavilySettingsOut:
    """写入/替换 Tavily API key。明文只进 DB，不进入响应或日志。"""
    if not body.apiKey.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail={"ok": False, "reason": "invalid_tavily_key"})
    set_tavily_api_key(body.apiKey, db_path=_db())
    return TavilySettingsOut(keySet=bool(get_tavily_api_key(db_path=_db())))


@app.delete(f"{API_PREFIX}/settings/tavily", dependencies=[Depends(require_token)])
def delete_settings_tavily() -> TavilySettingsOut:
    """显式清除 Tavily key；与“编辑时留空=不改”彻底分开。"""
    set_tavily_api_key(None, db_path=_db())
    return TavilySettingsOut(keySet=False)


@app.put(f"{API_PREFIX}/settings/push", dependencies=[Depends(require_token)])
def put_settings_push(body: SettingsPushIn) -> OkOut:
    """写推送开关(**V2-⑪ 起按 `kind` 配**,不按三级 category 配 —— 按 category 配
    会连坐,D5 定案)。`kinds` 必须给全 `notify_kinds.ALL_KINDS` 每一个键;缺键或
    出现未登记 kind → 422(`reason="invalid_push_kinds"`),同 `PUT /settings/llm-routes`
    的 `invalid_task` 体例。"""
    try:
        set_push_kinds(body.kinds, db_path=_db())
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_push_kinds", "message": str(e)})
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


# —— V2-⑪-C 自然语言临时提醒(`custom_alerts`)————————————————————————————
#
# 落库路径**只有一条**(`POST /alerts`):LLM 解析(`POST /alerts/parse`)只是把表单
# 先替用户填好并出一张确认卡,**它自己不写库**——⑪-C 原文「展示确认卡……用户确认后
# 才落库」。手填降级走的也是 `POST /alerts`,两条入口共用同一套白名单校验。
#
# **删除 = 取消,不物理删行**(`DELETE /alerts/{id}` → `status='cancelled'`):台账
# 留痕,「用户主动取消」与「到点自动失效」两种下场必须分得开。


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

    if not files:
        raise HTTPException(status_code=422, detail="请至少选择一个 xlsx 文件")
    if len(files) > REVIEW_UPLOAD_MAX_FILES:
        raise HTTPException(status_code=413, detail="一次最多上传 5 个文件")

    app_settings = get_app_settings(db_path=_db())
    col_map = app_settings.review_col_map or None

    all_trades = []
    parse_warnings: List[str] = []
    sheet_formats: Dict[str, str] = {}
    total_bytes = 0
    for f in files:
        filename = f.filename or "未命名文件"
        content = _read_review_upload(f, total_before=total_bytes)
        total_bytes += len(content)
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

    # 单周亏损达到固定阈值时，周复盘仍会如实标记强制复盘。

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


# —— V2-⑭-B 画像 / 策略包 / 评价 ————————————————————————————————————————
#
# 🔴 **画像初期不得反向影响客观 Tier**(蓝图 4.4 禁令):这两个端点**只读展示**,
# `neckline/selection/` 与 `neckline/scan/` 全目录零 `profile` 引用(守门单测锁死)。


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


# ══════════════════════════════════════════════════════════════════════════
# 交割单分析台
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 **这一层无 LLM 调用**(架构 §六 逐字)。系统只做三件事:解析(`/review/upload`)、
# **装订**(`/review/bindery`)、**结论存档**(`/review/conclusions`)。
# 对话与总结在**系统之外** —— 用户把装订好的材料带到聊天框,总结用第三条端点存回来。
#
# 🔴 **三条成绩线互不进入对方的分子分母**(架构 §五):本段的一切都属于「我的成绩」
# 那条线。它**读** `k9_*` 的报告 / 预案 / 清单当**材料**(架构 §六 明文要求),
# 但 ⛔ 一个字都不往 `k9_*` 写;反方向 `scorecard/**` ⛔ 零 import `neckline.review`。


@app.get(f"{API_PREFIX}/review/bindery", dependencies=[Depends(require_token)])
def get_review_bindery(week: str = "", preSessions: int = -1, postSessions: int = -1) -> ReviewBinderyOut:
    """行情材料装订:每笔回合前后的 K 线 + 买卖点标注 + 同期大盘 + 同期申万二级
    + 当时那几天的报告与预案快照。

    `week` = ISO 周 `YYYY-Www`(⚠ 与 `/review` 同键形)。`preSessions` / `postSessions`
    缺省(< 0)时用 `bindery` 的模块常量 —— 它们是**上下文长度**不是策略参数
    (同 `explain/input.py::KLINE_SESSIONS`),换个值不会让任何一笔成交变成另一笔。

    **降级契约**(⛔ 一律不 404,同 `/review` 惯例):
      · 缺 week / 那周没上传过交割单 → `found=False`(这是**「没有」**,输入只能由
        用户给,系统查过表确实没有);
      · 装订过程炸了 → `found=True` + `unavailableReason`(⛔ 不拿空材料冒充装订成功)。

    🛑 **容量**:窗口内**全部票**走一次 parquet glob、行业 / 报告 / 预案 / 清单各走
    一次区间 SQL(§12 坑 1:本端点跑在常驻 `neckline.service` 里)。⛔ 别在这里
    按票或按日循环取数。

    🔴 **两个上下文长度有上界**(R2-07):`0 ≤ n ≤ MAX_WINDOW_SESSIONS`,越界 **422**。
    从前只判 `< 0` —— 实测 `preSessions=postSessions=350000` 会算出一个 70 万个
    交易日的窗口(**4.2 s / +42 MB RSS**),而这个端点跑在**常驻**服务上、
    §13.1-B5 正在为 900 M 的余量发愁:一个来自查询串的整数就能把它拖住。
    ⚠ 上界取 `MAX_WINDOW_SESSIONS` 而不是另编一个数 —— 它本来就是这份材料的容量上限,
    要多于它的上下文,装订层也会削回去。
    """
    from neckline.review import bindery
    from neckline.review.store import load_weekly_review

    for name, raw in (("preSessions", preSessions), ("postSessions", postSessions)):
        if raw >= 0 and raw > bindery.MAX_WINDOW_SESSIONS:
            raise HTTPException(
                status_code=422,
                detail=(f"{name} 最多 {bindery.MAX_WINDOW_SESSIONS} 个交易日"
                        f"(装订材料的容量上限,见 §12 坑 1);收到 {raw}。"
                        "缺省(< 0)= 用模块常量。"))
    week = (week or "").strip()
    if not week:
        return ReviewBinderyOut(ok=True, found=False)
    rec = load_weekly_review(week, db_path=_db())
    if rec is None:
        return ReviewBinderyOut(
            ok=True, found=False, week=week,
            unavailableReason="本周尚未上传交割单 —— 装订需要券商交割单,"
                              "系统补不出没上传的那一份(上传在 macOS 端的复盘 · 对账页)。")

    try:
        review = _review_from_archive(week, rec["result"])
        binding = bindery.bind_week(
            review,
            pre_sessions=bindery.PRE_SESSIONS if preSessions < 0 else preSessions,
            post_sessions=bindery.POST_SESSIONS if postSessions < 0 else postSessions,
            db_path=_db(),
        )
    except Exception as exc:  # noqa: BLE001  装订炸了不该 500,如实说这次没装成
        logger.warning("[review] %s 装订异常(已降级)", week, exc_info=True)
        return ReviewBinderyOut(
            ok=True, found=True, week=week,
            unavailableReason=f"本周材料本次未装订成功:{type(exc).__name__}(详见服务端日志)。")
    return ReviewBinderyOut(
        ok=True, found=True, week=week,
        binding=binding.to_dict(),
        markdown=bindery.render_binding_markdown(binding),
    )


def _review_from_archive(week: str, result: Dict[str, Any]):
    """把 `reviews.result_json` 里冻住的那份 `weekly_review_dict()` 还原成
    `WeeklyReview`(只还原装订用得到的那部分:周界 + `RoundTrip` 列表)。

    🔴 **装订读的是存档那一份,⛔ 不重新解析交割单**:交割单文件不在服务端留存
    (上传即解析即丢),而且「同一周装订两次得到不同材料」本身就是错的。
    ⚠ 归档行可能带有本页不使用的键，这里只读取装订所需字段。
    ⚠ **`buyDate` 解不出的行整条跳过并 WARNING**:买入日是窗口的锚,没有它这一笔
    根本不知道该铺哪一段行情。⛔ 不拿今天或周一顶上去(那会画出一段与成交无关的图,
    而看图的人不会知道)—— 跳过是**少一笔**,顶上去是**错一笔**。
    """
    from neckline.review.reconcile import RoundTrip, WeeklyReview, week_range

    def _day(raw):
        try:
            return datetime.strptime(raw, "%Y%m%d").date() if raw else None
        except (TypeError, ValueError):
            return None

    w_start, w_end = week_range(week)
    trips = []
    for r in (result or {}).get("roundTrips") or []:
        buy_date = _day(r.get("buyDate"))
        if buy_date is None:
            logger.warning("[review] %s 的存档里有一行 roundTrip 没有可解析的 buyDate"
                           "(tsCode=%r),本次装订跳过这一笔", week, r.get("tsCode"))
            continue
        trips.append(RoundTrip(
            ts_code=r.get("tsCode", ""), name=r.get("name", ""),
            buy_date=buy_date, buy_price=float(r.get("buyPrice") or 0.0),
            qty=int(r.get("qty") or 0), fees=float(r.get("fees") or 0.0),
            sell_date=_day(r.get("sellDate")),
            sell_price=r.get("sellPrice"), closed=bool(r.get("closed")),
        ))
    review = WeeklyReview(week=week, week_start=w_start, week_end=w_end)
    review.round_trips = trips
    review.closed_round_trips = [rt for rt in trips if rt.closed]
    return review


@app.post(f"{API_PREFIX}/review/conclusions", dependencies=[Depends(require_token)])
def post_review_conclusion(body: ReviewConclusionIn) -> ReviewConclusionsOut:
    """存一版复盘结论(架构 §六 第 3 件事)。

    🔴 **append-only**:每存一次写 `version + 1` 的新行,⛔ 老版本一个字不动 ——
    复盘结论是下一周做决定的依据,被静默改写之后就再也查不回来了。
    入参不合法 → **422 并把问题一次列全**(⛔ 不静默截断正文:截掉的恰恰是结尾那句
    结论,而用户会以为存进去了)。
    """
    from neckline.review import conclusions

    try:
        saved = conclusions.save(
            body.week, body.title, body.body,
            tags=body.tags, author=body.author, db_path=_db())
    except conclusions.ConclusionInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    versions = conclusions.load_versions(body.week, db_path=_db())
    return ReviewConclusionsOut(
        ok=True, week=body.week, latest=saved.to_dict(),
        versions=[c.to_dict() for c in versions],
    )


@app.get(f"{API_PREFIX}/review/conclusions", dependencies=[Depends(require_token)])
def get_review_conclusions(week: str = "", q: str = "", limit: int = 20) -> ReviewConclusionsOut:
    """读结论存档。传 `week` → 那一周的最新版 + 全部版本;否则按 `q` 检索
    (空 `q` = 最近几周各出最新版)——「下周可检索」的入口。

    ⚠ `latest=None` = **那周还没写过结论**(⛔ 不是「这周没问题」)。
    """
    from neckline.review import conclusions

    week = (week or "").strip()
    if week:
        versions = conclusions.load_versions(week, db_path=_db())
        latest = versions[-1].to_dict() if versions else None
        return ReviewConclusionsOut(
            ok=True, week=week, latest=latest,
            versions=[c.to_dict() for c in versions],
        )
    hits = conclusions.search(q, limit=limit, db_path=_db())
    return ReviewConclusionsOut(ok=True, matches=[c.to_dict() for c in hits])


# —— 复盘板块：聚合只读 ————————————————————————————————————————————————

def _week_anchor(week: str) -> date_cls:
    """`week` 接受该周任意一天 `YYYYMMDD`；非法或缺省时降级为今天。"""
    if len(week) == 8 and week.isdigit():
        try:
            return datetime.strptime(week, "%Y%m%d").date()
        except ValueError:
            pass
    return date_cls.today()


def _reconcile_segment(week_key: str) -> ReviewSegmentOut:
    """对账段。

    🔴 **`available=true` + `found=false` 才是"这周没上传交割单"的正确说法**
    (⛔ 不是 `available=false`):对账的必需输入(券商交割单)**只能由用户手动给**,
    系统查过 `reviews` 表、确实没有这一行 —— 那是**「没有」**,不是**「没看」**。
    ⚠ 与上面画像段刻意判得不同:画像缺席 = 系统自己那一步没跑(周度批算未运行)= 没看。
    两者给用户的动作完全不同(去上传 vs 等系统),⛔ 别"统一"成同一种。"""
    from neckline.review.store import load_weekly_review

    label = "交割单对账"
    rec = load_weekly_review(week_key, db_path=_db())
    if rec is None:
        return ReviewSegmentOut(
            available=True, label=label, asOf=week_key,
            detail={"found": False, "week": week_key,
                    "note": "本周尚未上传交割单 —— 对账需要券商交割单,"
                            "系统补不出没上传的那一份(上传在 macOS 端的复盘 · 对账页)。"},
        )
    return ReviewSegmentOut(
        available=True, label=label, asOf=week_key,
        detail={"found": True, "week": rec["week"], "generatedAt": rec["generatedAt"],
                "result": rec["result"], "material": rec.get("material") or ""},
    )


def _conclusions_segment(week_key: str) -> ReviewSegmentOut:
    """结论存档段。

    🔴 同对账段的三态读法:**`available=true` + `detail.found=false` 才是「这周还没写
    结论」**(⛔ 不是 `available=false`)—— 结论只能由用户写,系统查过表确实没有那一行。
    ⛔ 别把「还没写」渲染成「这周没问题」。
    """
    from neckline.review import conclusions

    label = "结论存档"
    versions = conclusions.load_versions(week_key, db_path=_db())
    if not versions:
        return ReviewSegmentOut(
            available=True, label=label, asOf=week_key,
            detail={"found": False, "week": week_key,
                    "note": "本周还没写过复盘结论 —— 把装订好的材料带到聊天框得出结论后,"
                            "用「结论存档」存回来。⛔ 「还没写」不等于「这周没问题」。"},
        )
    return ReviewSegmentOut(
        available=True, label=label, asOf=week_key,
        detail={"found": True, "week": week_key, "latest": versions[-1].to_dict(),
                "versionCount": len(versions)},
        items=[c.to_dict() for c in versions],
    )


@app.get(f"{API_PREFIX}/review/overview", dependencies=[Depends(require_token)])
def get_review_overview(week: str = "", asOf: str = "") -> ReviewOverviewOut:
    """复盘板块「累计」页的聚合读:对账与结论存档两段。

    `week` = 该周任意一天 `YYYYMMDD`（缺省本周）；`asOf` 是保留的请求参数，
    当前不参与聚合结果。

    两段各自独立说"有 / 没有 / 没取到"。任一段异常只降级本段。

    ⚠ **装订材料刻意不在这里**:它要读 parquet 行情,属于「点一下才算」的动作,
    单独走 `GET /review/bindery`(⛔ 别塞进这个每次进板块都会拉的聚合读,§12 坑 1)。"""
    out = ReviewOverviewOut()
    anchor = _week_anchor(week)
    try:
        from neckline.review.reconcile import iso_week_key

        out.weekKey = iso_week_key(anchor)
    except Exception:  # noqa: BLE001
        logger.warning("[review] ISO 周键计算异常", exc_info=True)

    monday = anchor - timedelta(days=anchor.weekday())
    days = trading_days_between(monday, monday + timedelta(days=6))
    lo, hi = (days[0], days[-1]) if days else (None, None)
    out.weekStart = lo.strftime("%Y%m%d") if lo else ""
    out.weekEnd = hi.strftime("%Y%m%d") if hi else ""

    for field_name, build in (
        ("reconcile", lambda: _reconcile_segment(out.weekKey)),
        ("conclusions", lambda: _conclusions_segment(out.weekKey)),
    ):
        try:
            setattr(out, field_name, build())
        except Exception as exc:  # noqa: BLE001  段级保险丝:一段炸不连坐其余三段
            logger.warning("[review] overview 的 %s 段装配异常(已降级为不可得)",
                           field_name, exc_info=True)
            setattr(out, field_name, ReviewSegmentOut(
                available=False,
                unavailableReason=f"本段本次未取得:{type(exc).__name__}(详见服务端日志)。"))
    return out


# K9-v3 成绩包接口。包是历史主实体，路由不再按“今天”猜测某一批记录。
@app.get(f"{API_PREFIX}/scoreboard/packages", dependencies=[Depends(require_token)])
def get_scoreboard_packages(state: str = Query(..., pattern="^(active|settled)$")) -> dict:
    from neckline.scorecard import packages
    return {"strategyVersion": "K9-v3", "state": state,
            "packages": [_package_summary(item) for item in packages.list_packages(state=state, db_path=_db())]}


@app.get(f"{API_PREFIX}/scoreboard/packages/{{batch_id}}", dependencies=[Depends(require_token)])
def get_scoreboard_package(batch_id: str) -> dict:
    from neckline.scorecard import packages
    result = packages.load_package(batch_id, db_path=_db())
    if result is None:
        raise HTTPException(status_code=404, detail="成绩包不存在或数据库尚未迁移")
    return _package_detail(result)


@app.post(f"{API_PREFIX}/scoreboard/packages/{{batch_id}}/playbooks/{{ts_code}}", dependencies=[Depends(require_token)])
def append_k9_v3_playbook_revision(batch_id: str, ts_code: str, body: dict[str, Any]) -> dict:
    """Append, never overwrite, a user-confirmed K9-v3 pre-plan revision."""
    from neckline.k9 import v3_playbook
    from neckline.scorecard import packages
    package = packages.load_package(batch_id, db_path=_db())
    candidate = next((x for x in (package or {}).get("candidates", []) if x["tsCode"] == ts_code), None)
    if candidate is None:
        raise HTTPException(status_code=404, detail="成绩包候选不存在")
    original = candidate.get("frozenD0Playbook") or candidate.get("playbook") or {}
    skeleton = original.get("mechanicalSkeleton") if isinstance(original, Mapping) else None
    if not isinstance(skeleton, Mapping):
        raise HTTPException(status_code=409, detail="预案机械骨架缺失，不能安全修改")
    try:
        validated = v3_playbook.validate_output({"candidates": [{"tsCode": ts_code, **body}]}, {ts_code: skeleton}, source="user")
        revision = packages.append_user_playbook_revision(
            batch_id=batch_id, ts_code=ts_code, playbook=validated[ts_code],
            provenance={"source": "user", "api": "k9-v3-playbook-revision-v1"}, db_path=_db(),
            now=datetime.now(CN_TZ))
    except (v3_playbook.PlaybookUnavailable, packages.PackageConflict) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    latest = packages.load_package(batch_id, db_path=_db())
    item = next(x for x in latest["candidates"] if x["tsCode"] == ts_code)
    return {"batchId": batch_id, "tsCode": ts_code, "revision": revision,
            "playbook": item["playbook"], "history": item["playbookHistory"]}


@app.get(f"{API_PREFIX}/checklists/{{batch_id}}", dependencies=[Depends(require_token)])
def get_batch_checklist(batch_id: str) -> dict:
    """只读取一份 K9-v3 包的 9:29 核对；三组永远同时出现。"""
    from neckline.auction import store as auction_store
    result = auction_store.load_checklist(batch_id, db_path=_db())
    if result is None:
        raise HTTPException(status_code=404, detail="成绩包不存在或数据库尚未迁移")
    return result


def _package_summary(item: Mapping[str, Any]) -> dict:
    return {"batchId": item["batch_id"], "selectionDate": item["selection_date"],
            "signalTradeDate": item["signal_trade_date"], "d1TradeDate": item["d1_trade_date"],
            "d2TradeDate": item["d2_trade_date"], "revision": item["revision"], "state": item["state"],
            "coverageState": item["coverage_state"], "strategyVersion": item["strategy_version"],
            "paramsPackageVersion": item["params_package_version"], "packVersion": item["pack_version"],
            "labelContractVersion": item["label_contract_version"], "candidateCount": item["candidate_count"],
            "createdAt": item["created_at"]}


def _package_detail(item: Mapping[str, Any]) -> dict:
    result = _package_summary({**item, "candidate_count": len(item["candidates"])})
    result["frozenContract"] = item["frozen_contract"]
    result["candidates"] = item["candidates"]
    return result


# ══════════════════════════════════════════════════════════════════════════
# 选股：报告三态与当日清单
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 **三态必须原样透传**(裁定 5):`state ∈ {has_list, empty, not_run}`。
#   · `empty`   = 跑通了、结果是空的 —— **可以被信任**;
#   · `not_run` = 系统没工作,`gaps` 逐条说明缺什么,`listingSize` 为 **null**。
# 客户端⛔ 不许把 `not_run` 渲染成「今天没有」,也⛔ 不许把 `null` 显示成 0。
#
# 🔴 **双日期契约**(LRN-20260816-001):`reportDate` 管标题 / 推送 / 可见身份,
# `tradeDate` 管 EOD 读数 / 清单 / 审计键。周日报告两者不同,⛔ 客户端别只取一个。
#
# ⚠ 个股详情(`/selection/{date}/stock/{code}`)要解释层资料 + 预案,归 S9 / S10。


def _selection_stocks(batch_ids: Sequence[object]) -> list:
    """Read immutable K9-v3 package candidates referenced by a report."""
    # The report holds only package ids; package candidates are immutable and are
    # the sole source for current selection details.
    from neckline.scorecard import packages
    out = []
    for batch_id in batch_ids:
        package = packages.load_package(str(batch_id), db_path=_db())
        if package is None:
            continue
        for item in package["candidates"]:
            channels = list(item.get("channels", []))
            out.append({"tsCode": item["tsCode"], "name": item.get("name"),
                        "swL2Code": item.get("swL2Code"), "swL2Name": item.get("swL2Name"),
                        "patterns": channels, "primaryPattern": channels[0] if channels else "",
                        "channelRanks": item.get("channelRanks", {}),
                        "playbook": item.get("playbook"), "baseline": item.get("baseline"),
                        "thresholds": item.get("thresholds"), "batchId": batch_id})
    return out


def _selection_payload(row: dict) -> dict:
    structured = row["structured"]
    stocks = _selection_stocks(structured.get("batchIds", []) if isinstance(structured, dict) else [])
    payload = {
        "reportDate": row["report_date"],
        "tradeDate": row["trade_date"],
        "state": row["state"],
        "headline": row["headline"],
        "gaps": row["gaps"],
        "strategy": row["strategy"],
        "strategyVersion": row["strategy_version"],
        "paramsPackageVersion": row["params_package_version"],
        "packId": row["pack_id"],
        "packVersion": row["pack_version"],
        "listingSize": row["listing_size"],
        "generatedAt": row["generated_at"],
        "markdown": row["markdown"],
        "structured": structured,
        "direction": structured.get("direction") if isinstance(structured, dict) else None,
        "market": structured.get("market") if isinstance(structured, dict) else None,
        "coverage": structured.get("coverage") if isinstance(structured, dict) else None,
        "batchIds": structured.get("batchIds", []) if isinstance(structured, dict) else [],
        # §5.11 今日清单要的逐只摘要(**现装,不进冻结件**,见 `_selection_stocks`)。
        "stocks": stocks,
    }
    payload["copyText"] = _selection_copy_text(payload)
    return payload


def _selection_copy_text(payload: Mapping[str, Any]) -> str:
    """由已保存事实确定性拼出的可复制中文，不重写冻结报告。"""
    lines = [str(payload.get("headline") or "每日报告"),
             f"报告日：{payload.get('reportDate') or '—'}；行情截至：{payload.get('tradeDate') or '—'}"]
    direction = payload.get("direction") or {}
    if direction.get("state") == "ready":
        lines += ["", "市场方向", str(direction.get("summary") or "")]
        for theme in direction.get("themes") or []:
            lines.append(f"- {theme.get('name', '')}：{theme.get('reason', '')}")
    elif direction:
        lines += ["", "市场方向", "方向解读暂未生成。"]
    market = payload.get("market") or {}
    if market:
        lines += ["", "市场事实"]
        limit_map = market.get("limitMap") if isinstance(market, Mapping) else None
        if isinstance(limit_map, Mapping):
            bits = []
            labels = (("limitUpCount", "涨停"), ("limitDownCount", "跌停"), ("zabanCount", "炸板"))
            for key, label in labels:
                if limit_map.get(key) is not None:
                    bits.append(f"{label} {limit_map[key]} 家")
            if bits:
                lines.append("；".join(bits) + "。")
        median = market.get("marketMedianRet") if isinstance(market, Mapping) else None
        if isinstance(median, (int, float)):
            lines.append(f"全市场涨跌幅中位数：{median * 100:.2f}%。")
        if len(lines) and lines[-1] == "市场事实":
            lines.append("当日市场资料已保存，具体数值请在报告中查看。")
    coverage = payload.get("coverage")
    if isinstance(coverage, Mapping):
        lines += ["", "覆盖率"]
        summary = coverage.get("summary")
        if isinstance(summary, str) and summary.strip():
            lines.append(summary.strip())
        else:
            lines.append("前一日清单覆盖情况已保存；缺少可核对记录时不会按 0% 处理。")
    lines += ["", "清单"]
    stocks = payload.get("stocks") or []
    if not stocks:
        lines.append("今天没有可供进一步阅读的清单。")
    for stock in stocks:
        lines.append(f"{stock.get('name') or stock.get('tsCode')}（{stock.get('tsCode')}）")
        if stock.get("oneLineProfile"):
            lines.append(str(stock["oneLineProfile"]))
        baseline = stock.get("baseline") or {}
        close = baseline.get("close")
        lines.append("收盘价（截至行情日）：" + (f"{float(close):.2f}" if close is not None else "资料暂未保存"))
        playbook = stock.get("playbook") or {}
        level_keys = ("invalidation", "firstResistance", "secondResistance")
        if all(playbook.get(key) is not None for key in level_keys):
            lines.append("失效价 {0}；第一压力位 {1}；第二压力位 {2}".format(
                playbook["invalidation"], playbook["firstResistance"], playbook["secondResistance"]))
            revision = playbook.get("revision")
            lines.append(f"预案第 {revision} 版" if revision is not None else "预案修订号未保存。")
        else:
            lines.append("明日预案：资料暂未生成。")
    lines += ["", "以上为研究材料，不构成交易指令。"]
    return "\n".join(lines)


@app.get(f"{API_PREFIX}/selection/latest", dependencies=[Depends(require_token)])
def get_selection_latest() -> dict:
    """最近一份报告(三态 + 双日期 + 方向背景 + 清单)。

    库里一份都没有 → `state='not_run'` 的**空态**,⛔ 不 500:那是「还没跑过」,
    是一个正常的可读结论。"""
    from neckline.report import store as report_store

    row = report_store.latest_k9_report(db_path=_db())
    if row is None:
        return {
            "state": "not_run",
            "headline": "今天没跑成 · 尚无任何报告",
            "gaps": ["库里还没有任何一份 K9 报告"],
            "listingSize": None,
            "stocks": [],
        }
    return _selection_payload(row)


@app.get(f"{API_PREFIX}/selection/{{trade_date}}", dependencies=[Depends(require_token)])
def get_selection_by_date(trade_date: str) -> dict:
    """按**交易日**查历史报告(⚠ 不是发布日 —— 双日期契约里它才是审计键)。"""
    from neckline.report import store as report_store

    try:
        day = datetime.strptime(trade_date, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="trade_date 必须是 YYYYMMDD")
    row = report_store.load_k9_report(day, db_path=_db())
    if row is None:
        raise HTTPException(status_code=404, detail=f"{trade_date} 没有报告")
    return _selection_payload(row)


@app.get(f"{API_PREFIX}/usage/summary", dependencies=[Depends(require_token)])
def get_usage_summary(days: int = 5) -> dict:
    """真实 Token / Tavily credits 的去敏只读汇总。"""
    from neckline.llm import usage
    return usage.summary(days=days, db_path=_db())


def _parse_day(raw: str) -> date_cls:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="日期必须是 YYYYMMDD")


def _today() -> date_cls:
    """本机当天(上海时区,`CN_TZ` 是全仓唯一源)。

    ⚠ 单测靠 **monkeypatch 本函数**注入「今天是哪天」—— ⛔ 不给端点加一个可以从
    请求里传的日期参数:那等于把冻结闸的开关交到闸外面。
    """
    return datetime.now(CN_TZ).date()


__all__ = ["app", "VERSION", "RELEASE_SET", "API_PREFIX"]
