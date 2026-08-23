"""FastAPI 应用主体(plan 4A + 4B.3 单 unit 内哨兵 asyncio 任务)。

绑 127.0.0.1:8002(nginx 反代,与 LinoN 8001 共存)。`/api/v1/health` 免鉴权;其余端点
过 `require_token`。startup:fail-fast 校验 `API_TOKEN`(len>=16)+ `init_schema` + 起
哨兵后台轮询任务(§3.6「哨兵折进 FastAPI 单 unit 的 lifespan asyncio 任务」,不另起进程)。
shutdown:置位 stop_event,优雅停轮询。

**同码不重写**:报告 / 看板 / 持仓的领域逻辑全部复用现有模块,端点只做「装配 +
出入参映射 + 鉴权」。

**测试注入**：`ENABLE_MORNING_TASKS`（关早晨两拍）、`_DB_PATH_OVERRIDE`
(隔离库)、`_PARQUET_DIR_OVERRIDE` / `_DATA_DIR_OVERRIDE`(隔离产物目录)。
"""

from __future__ import annotations

import asyncio
import logging
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
from neckline.config import ensure_data_dirs
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

# 系统版本规约(2026-07-22 用户定):v主.次.修 三段式,与策略版本(K 字头)双线解耦。
# v1.1 = SOP 补洞大版本;第三段 = 其后的快修(v1.1.1 = 退潮哨兵双级制重构)。
# v1.3(2026-07-26 用户拍板合并发布):v1.2(0/A/A2/B/G/E)+ v1.3(①②③④⑤⑥)一次上云,
# 对外版号直接跳 v1.3(跳过 v1.2 对外版号,见 PROJECT_PLAN §五 v1.3-⑦-A)。
# v1.3.1(2026-07-27 快修):独立审计缺陷修复(纪律章程 + 金额判定 9 条,🔴-1「D5 判一次
# 定格」为首)+ 行业闸判据 板内占比 → lift(富集度)。**未激活任何章程行**(is_active 仍 K1)。
# v1.3.3(2026-07-27 快修,用户拍板):**拆墙**——章程 v1.3.3 把 `forbid_high_elasticity`
# True→False(创业板/科创板不再被纪律层禁买,周复盘/问询台/自选体检/回测四处口径统一);
# 问询台**审判员 → 自由分析师**(硬栏杆与二值裁决退役、软护栏只留 prompt 层、海选池
# 自动写入退役改一键加自选);`positions`/`decision_log`/`inquiry_pool` 写入通道补
# `ts_code` 归一(生产真洞:裸 6 位入库致 EOD 持仓管线 join 不上)。
# v1.3.4(2026-07-27 快修,用户拍板):**问询台联网搜索实际搜错了东西**——① 供应商推导的
# 检索词紧跟最后一条 user 消息,而问询台最后一条是用户的代词提问(「这只票…」),身份
# 信息在更早的材料消息里救不回来 → 搜回泛泛板块新闻,模型退回训练数据作答;② `det.name`
# 自建库起声明了、`build_llm_context` 一直在读,**却从没赋过值**,材料首行恒「名称:未知」,
# 中文名这个最值钱的检索词被白扔。修法 = `provider.chat()` 加**可选** `search_query`
# (不传时 payload 逐字节不变,护栏单测锁死)+ 问询台补中文名并显式传检索词。
# 同批加「联网搜索命中 0 条」的埋点与用户侧露出(报告脚注 / 问询 evidence / 消息面扫描
# 状态),因为搜索静默返 0 条时模型照样写得出像样的分析——见 `llm.base.search_coverage_line`。
# ⚠ 被证伪的排除项(别再查一遍):GLM 那份 payload 里 `enable`/`search_result` 发字符串
# `"True"`、`count` 发 `"5"` **不是 bug**,真 key A/B 实证接口会正确解析成 bool/int。
# v1.3.5(2026-07-28 快修,用户拍板):**2026-07-27 的 16:35 报告当场崩掉、当日无报告**
# —— `moneyflow_dc` 分区 schema 分裂(2020-2023 的 897 个 0 行空文件落成 String,
# 2023-09-11..2026-07-20 的 688 个真数据是 Float64)→ v1.3 新增的候选情报管线首次对该表
# 做全表 `scan_parquet` → SchemaError → 整个 `build_report` 崩。两半修法:
# ① **数据**:历史脏分区已在生产修缮为 Float64，
#    零损失对拍全过);② **代码**:`_align_to_table_schema` 的对齐目标从「既有分区的**第一个
#    文件**」改为 `market_data.TABLE_FLOAT_COLS` 的**显式 canonical 声明**——旧口径的致命
#    假设是「第一个分区一定是对的」,而 moneyflow_dc 的首个分区恰恰是脏的,于是 2026-07-21
#    起每天的真数据都被"对齐"成 String,越修越坏;③ 候选管线内部对**可选情报输入**
#    (板块资金流)的调用补保险丝——排序少一维可以,掀翻整份报告不行。
# ⚠ 别再走的死路:2026-07-21 那次"向既有分区看齐"的修法对 daily_basic 有效、对
# moneyflow_dc **无效且有害**,因为它的基准本身就是脏的。判据是「基准可信吗」,不是「有没有对齐」。
# 📌 **一次失败部署的留痕(2026-07-29,已结案勿再重演)**:本版号 **07-29 上午首次尝试上云
# 时被回滚过** —— 当时 `compute_industry_strength` 对 `daily` 扫全历史 784 万行,生产
# (2 vCPU/1.6G)700M cap OOM-kill、1400M cap 600s 跑不完,16:35 报告主链与 info-card /
# 问询台三处全中招(§七 P0-23)。**该阻塞已由第 ⑩ 块修复**(行业强度预计算落表
# `industry_strength_daily`,在线路径只读表、不再全表现算),当日傍晚重新完整上云。
# 教训已入项目 CLAUDE.md:**「本地实测廉价」不是生产结论,新增全表 `scan_parquet` 路径
# 上云前必须在生产机上单独计时 + 量峰值。**
#
# v1.4.0:`-p1` 半版状态结束——① 之后的 ②~⑧ 全部就位
# (② 行业强度单一源 / ③ 正选漏斗三级排序键 / ④ 信息卡与考卷同构 / ⑤ exec_hint + 决策
# 日志第⑨项追价上限 / ⑥ 判定精度〔逐笔章程 · 自选隔日轮扫 · 定格日标注〕/ ⑦ 挂单追踪
# 与问询记录落库 / ⑧ 双端客户端跟进)。历史提示:`-p1` 曾是「云上是个半版」的标记,
# 只上第 ① 块 P0 地基,因 `002036.SZ` 停牌票假警报有硬时限(0729 09:26 误推 D5 离场)。
# v1.4.1(热修,§七 **P1-26** 结案):**信息卡端点在生产要 18~20 秒,客户端 12s 默认超时
# → 用户实际体验是「信息卡总是加载失败」**,且失败诱发反复重试、把常驻服务顶到内存节流线
# (实测 `memory.events high=10703`、`MemoryCurrent≈MemoryHigh=440M`),越试越慢。
# **根因不是行业强度**(那一维 ⑩ 之后已是读表 5ms),是**取数层全 glob**:分区布局是
# `year=YYYY/YYYYMMDD.parquet` 共 1592 个文件,而 `_scan_table` 不论请求哪一天都把 1592 个
# parquet footer 全打开;信息卡一次请求里 `compute_sentiment`(5 次全市场横截面)+ 单票
# 420 日面板(daily/adj_factor)+ 大盘指数线 = **8 次全 glob**。
# **修法**:取数层按 `year=` 裁剪(`market_data._scan_table(years=)`,由 `get_market_slice` /
# `get_stock_history` / `scan_table_range` 传入区间覆盖的年份;`market_state_labels` 的起点
# 由写死 2019 改为按 MA 窗口回看)——**纯 I/O 优化,结果逐位不变**(等价单测锁死),
# 依据是「`year=YYYY` 目录里结构上只可能有该年的行」。客户端同步把该请求超时给到 60s
# (照问询台惯例)。**⚠ 顺带收窄了脏分区的传染半径**:单日/区间读不再打开无关年份,
# 故 v1.3.5 那条「历史脏分区不会自愈」现在只对**跨到脏那年**的读成立(守门单测已改写记录)。
# API 与两个客户端工程的版本号必须同批变更；守门测试负责校验三处一致。
# 本地修改不代表生产已部署，只有生产 `/health` 返回本值才算后端到位。
VERSION = "v2.5.1"
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

    系统不持续观察盘中价格，也不跟踪持仓。两拍各自独立 ``try/except``：
    | 拍 | 窗口 | 推送 | 产物 |
    |---|---|---|---|
    | 竞价核对表 | 9:26–9:29 | 有(APNs) | `已触发放弃 / 待开盘后观察` 两段,⛔ 无「成立」 |
    | 结算拍(裁定 10) | 10:00–10:05 | **无** | 三分支终值 → `k9_d1_verdicts` |

    🔴 ⛔ **不新增 systemd unit** —— 两拍都跑在既有常驻 `neckline.service` 里
    (PROJECT_PLAN §9.3)。多一个 unit 就多一条双跑路径,而「当日只跑一次」记在
    `neckline/dedup.py` 的台账里,双触发会把「今天跑没跑过」变成一道要现场推理的题。

    🔴 **推送只在第一拍**:结算拍**零推送**(裁定 10 —— 它是结算,不是提醒)。
    本函数里 `notify.*` 只出现在竞价那一支下面,守门单测 G21 跑一次结算断言
    APNs 调用计数 = 0。
    """
    logger.info("早晨轮询已挂载(S8:9:26 竞价核对表 + 10:00 结算拍,零新增 unit)")
    while not stop_event.is_set():
        now = datetime.now()
        # 🔴 两拍**各自独立** `try/except`(§5.7.3):一拍炸了不影响另一拍。
        # 🔴 **两拍都丢进线程池**(R2-09):它们内部做 HTTP + SQLite,是**同步阻塞**。
        # 单源最坏 `2 × (3+5) = 16 s`(`data/realtime.py` 的 connect=3s / read=5s /
        # 2 次尝试),双源顺序 → 单拍最坏约 **32 s**,而收紧区间的轮询间隔是 30 s
        # —— 阻塞窗口与轮询周期**重叠**。行情源抽风时 `/health`、`/checklist/{date}`、
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


def _morning_checklist_tick(now: datetime) -> None:
    """9:26—9:29 那一拍:跑核对表 → 落库 → **判门槛** → 推一条 APNs。

    ⚠ 窗口外 / 当日已跑 → `run_checklist_tick` 内部**零落库**直接返回,
    本函数只是把它调起来;⛔ 这里不复判窗口(判据的单一源在 `pipeline.py`)。"""
    from neckline.auction import pipeline as auction_pipeline

    res = auction_pipeline.run_checklist_tick(now, db_path=_db(),
                                              parquet_dir=_PARQUET_DIR_OVERRIDE)
    if res.skipped_reason:
        # B21:可信空清单保持静默；只有前一交易日报告明确 `not_run` 才提醒一次。
        if res.skipped_reason == auction_pipeline.SKIP_NO_LISTING and res.d0_date is not None:
            from neckline.report import store as report_store

            report = report_store.load_k9_report(res.d0_date, db_path=_db())
            event = "previous_report_not_run"
            if (report is not None and report.get("state") == "not_run"
                    and not dedup.already_pushed(now.date(), "auction", "", event,
                                                db_path=_db())):
                notify.push_previous_report_not_run(res.d0_date.strftime("%Y%m%d"), db_path=_db())
                dedup.record_pushed(now.date(), "auction", "", event,
                                    payload={"d0Date": res.d0_date.strftime("%Y%m%d")},
                                    db_path=_db())
        logger.debug("[morning] 竞价核对表跳过:%s", res.skipped_reason)
        return
    # 推送门槛的单一源是 `ChecklistRunResult.should_push`,⛔ 不在这里另判一次。
    if res.should_push:
        notify.push_checklist_summary(res.counts, db_path=_db())


def _morning_settle_tick(now: datetime) -> None:
    """10:00—10:05 那一拍:一次性结算快照 → 三分支终值。

    🔴 **零推送、不进 App 首屏**(裁定 10)。本函数**一行 `notify` 都没有** ——
    结算拍的产物只从 `GET /scoreboard/verdicts/{date}` 出去。"""
    from neckline.auction import settle as auction_settle

    res = auction_settle.run_settle_tick(now, db_path=_db(),
                                         parquet_dir=_PARQUET_DIR_OVERRIDE)
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
    if ENABLE_MORNING_TASKS:
        app.state._morning_task = asyncio.create_task(_morning_loop(app.state._stop_event))
    yield
    # —— shutdown ——
    app.state._stop_event.set()
    task = app.state._morning_task
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


# —— V2-② LLM Provider 注册表(自填制,plan §3.10-B)—— `PUT /settings/llm`
# 已删(D2=A 路已拍板,老 App 打老机不会撞到新服务端,不做 legacy 兼容层,见
# plan §五 V2-②「契约变更」/⑭-D)。————————————————————————————————————

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

    # ⚠ **V2.2-⑤-B:原 `circuit.auto_unlock_for_reviews(...)` 接线已删**(裁定 #8 —— 强制
    # 复盘解锁是被删的三件机制之一,没有锁自然也没有解锁)。
    # ⚠ **§2.1 第 4 条「单周亏损 ≥ 总仓 2% → 强制复盘」一字不动、⛔ 别连坐删**:它不是熔断,
    # 判据仍是 `reconcile.FORCED_REVIEW_LOSS_FRAC`,周复盘照常判(下面 `review.forced_review`)。

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
# V2.5.0 S11 · 交割单分析台(架构 §六,PROJECT_PLAN §5.9)
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
    ⚠ 历史行可能带着 V2.4.x 的多余键(`planChecks` / `disciplineViolations` …),
    这里**只挑要的**,多余键一律忽略(⛔ 不因为老行多几个键就炸)。
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


# —— V2.2-② 行情状态层:只读端点 ————————————————————————————————————————


# —— V2.3.3-⑤ D1 集合竞价确认层(`GET /auction[?date=]`,K8.md §二十)———————————
#
# **两个全新 reason,客户端 `mapReason` 必须各加一个 case**(⛔ 别指望 fallback:
# 404 的 fallback 是 `.notHolding`「持仓已清」,那句话与竞价报告毫无关系):
#   · `auction_not_ready`(**404**)—— 当日 `auction_reports` **无行** = 竞价层没跑过
#     / 还没到 9:26。文案方向「今天的竞价报告还没生成」。
#   · `auction_corrupt` (**500**)—— **有行但读不出**(json 列解不出 / 内容键全缺)。
#     文案方向「竞价报告数据损坏,需要排查」。
#     ⛔ **两者必须分开**(B1 定案原文):混成一类 = 客户端永远重试、永远显示"还没生成",
#     而那份报告是**冻结件**(`INSERT OR IGNORE` 永不覆盖)→ 坏了就是永久坏的
#     = 静默永久失败。
#
# 🔴 **「有行但 `baskets_covered=0`」是 200,不是 404**(〇b-6,§七 P0-39 同款病):
# 「竞价层没跑」与「跑过了、D0 当天就没有 T1/T2 篮子」是两种相反的成因,
# ⛔ 不许混成一句「今天没有竞价报告」。后者走 200 + `basketsUnavailableReason` 说出口。
#
# 🔴 **为什么这里 404 而 `/market-regime` 一律 200**:`market_regime_daily` 是**日更
# 只读表的区间查询**(缺行 = 那天没批算);竞价报告是**冻结件点查**,与 `basket_cards`
# 同族 → 照 B1。**这是刻意的不同,⛔ 别"统一"。**
#
# ⛔ **零现算、零写库**:本端点只 SELECT(常驻服务与盘中哨兵同进程,P0-23)。


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
    """结论存档段(V2.5.0 S11,架构 §六 第 3 件事)。

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

    `week` = 该周任意一天 `YYYYMMDD`(缺省本周);`asOf` 保留兼容位(画像段已随
    `profile/` 在 S1 退役,⛔ 不再有消费方)。

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


# ══════════════════════════════════════════════════════════════════════════
# V2.5.0 S4 · 覆盖率成绩线(PROJECT_PLAN §5.12 / §5.8.1)
# ══════════════════════════════════════════════════════════════════════════

#: `window` 的上限:一次最多回看多少个已算出的交易日。⚠ 这是**接口分页上限**,
#: 与 §8 的待标定参数无关(同 `MAX_LOOKBACK_PACKS` 那类工程容量上限的性质)。
_COVERAGE_WINDOW_MAX = 250
_COVERAGE_WINDOW_DEFAULT = 20


@app.get(f"{API_PREFIX}/scoreboard/coverage", dependencies=[Depends(require_token)])
def get_scoreboard_coverage(window: int = _COVERAGE_WINDOW_DEFAULT) -> dict:
    """覆盖率 + 漏检归因(架构 §5.2)。

    🔴 **这条线不读参数包**:`coverageAll` 以涨停为口径,涨停是硬事实。
    参数标定完成之前它就是那把尺子(§5.8.1)。

    ⚠ **NULL 不是 0**,响应里原样保留:
        · `coverageAll = null` —— 昨天还没有清单(上线首日 / 参数未配置的日子);
        · `coverageInPool = null` —— 没有 D−1 的全市场 disposition(边界参数缺失)。
    客户端**必须**把 null 渲染成「尚不可得」而不是 0%。
    """
    from neckline.scorecard import store as scorecard_store

    n = max(1, min(int(window or _COVERAGE_WINDOW_DEFAULT), _COVERAGE_WINDOW_MAX))
    days = scorecard_store.load_coverage_days(limit=n, db_path=_db())
    latest_misses = []
    if days:
        newest = datetime.strptime(days[0]["trade_date"], "%Y%m%d").date()
        latest_misses = scorecard_store.load_misses(newest, db_path=_db())
    return {
        "window": n,
        "days": [
            {
                "tradeDate": d["trade_date"],
                "packVersion": d["pack_version"],
                "limitUpCount": d["limit_up_count"],
                "limitDownCount": d["limit_down_count"],
                "zabanCount": d["zaban_count"],
                "zabanRate": d["zaban_rate"],
                "maxConsecDays": d["max_consec_days"],
                "clusterCount": d["cluster_count"],
                "listingTradeDate": d["listing_trade_date"],
                "listingSize": d["listing_size"],
                "coveredCount": d["covered_count"],
                "coverageAll": d["coverage_all"],
                "inPoolDenominator": d["in_pool_denominator"],
                "coveredInPool": d["covered_in_pool"],
                "coverageInPool": d["coverage_in_pool"],
                "census": d["census_json"],
            }
            for d in days
        ],
        "latestMisses": [
            {
                "tradeDate": m["trade_date"], "tsCode": m["ts_code"], "name": m["name"],
                "board": m["board"], "l2Code": m["sw_l2_code"], "l2Name": m["sw_l2_name"],
                "consecLimitUpDays": m["consec_limit_up_days"],
                "reason": m["reason"], "detail": m["detail"],
            }
            for m in latest_misses
        ],
        "missReasonCounts": scorecard_store.miss_reason_counts(db_path=_db()),
    }


@app.get(f"{API_PREFIX}/scoreboard/listing", dependencies=[Depends(require_token)])
def get_scoreboard_listing(window: int = _COVERAGE_WINDOW_DEFAULT) -> dict:
    """最近若干个已经走完 D+4 的正式清单日五指标。

    成立率分母是正式清单全量；兑现率与错杀率只在相应终值且存在预案压力位的
    样本内计算。行业分与选票分始终分开返回，不提供合计字段。
    """
    from neckline.scorecard import listing

    n = max(1, min(int(window or _COVERAGE_WINDOW_DEFAULT), _COVERAGE_WINDOW_MAX))
    return listing.load_scorecard(window=n, db_path=_db())


# ══════════════════════════════════════════════════════════════════════════
# V2.5.0 S7 · 选股(报告三态 + 当日清单,PROJECT_PLAN §5.12 / §5.10)
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


def _selection_stocks(trade_date: str) -> list:
    """清单上每只票的**摘要**:形态标注 / 上方机械空间 / 三个价位 / 三分支预案摘要。

    🔴 **为什么在这里装而不是塞进 `k9_reports.structured_json`**:那份 JSON 是随报告
    **冻结**的产物(S7),它的键序与内容要逐字节可复现;而这四样东西分别住在
    `k9_listing_entries` / `k9_channel_hits` / `k9_playbooks` / `k9_explain_notes`
    四张表里,其中预案是 **append-only 版本化**的(用户改一次就多一版)——
    把它冻进报告快照,用户改完预案报告就与库里对不上了。故本段**每次请求现装**,
    ⛔ 不动任何冻结件。

    🔴 **为什么不让客户端逐只去问** `/selection/{date}/stock/{code}`:清单一天 10–20 只,
    首屏就是 20 次请求。这里四次批量查询取全(⛔ 无按票循环)。

    ⚠ **每一样缺席都各自如实标**(§5.10 / S9-S10 的三态纪律):
      · `upsideRoomMechPct = null` —— 这只票只被 p2 / p4 召回,**本形态不看这一项**
        (K9 §3.3 / §3.5 的强度性里没有它),⛔ 不是「上方没有空间」;
      · `playbook = null` —— 那天没给这一只冻预案 → **明早核对不了它**;
      · `newsState = null` —— 解释层没跑过这一只(⛔ 与 `"unverified"`「查过没查成」
        不是一回事,两者都⛔ 不许显示成「无异常」)。
    """
    from neckline.explain import store as explain_store
    from neckline.k9 import store as k9_store
    from neckline.playbook import store as pb_store

    day = _parse_day(trade_date)
    listing = k9_store.load_listing(day, db_path=_db())
    if not listing:
        return []
    codes = [e["ts_code"] for e in listing]
    room = k9_store.load_upside_room_mech(day, codes=codes, db_path=_db())
    playbooks = pb_store.load_latest(day, codes=codes, db_path=_db())
    notes = explain_store.load_notes(day, codes=codes, db_path=_db())
    closes: Dict[str, float] = {}
    try:
        from neckline.facts import store as fact_store
        frame = fact_store.load_pack(day, db_path=_db()).rows
        if "ts_code" in frame.columns and "close" in frame.columns:
            closes = {str(r["ts_code"]): float(r["close"])
                      for r in frame.select(["ts_code", "close"]).iter_rows(named=True)
                      if r["close"] is not None}
    except Exception:
        # 历史报告的事实包可能已按保留规则裁剪；报告仍然可读。
        closes = {}
    out = []
    for e in listing:
        code = e["ts_code"]
        note = notes.get(code)
        pb = playbooks.get(code)
        profile = note.get("profile", {}) if note else {}
        one_line = str(profile.get("company") or profile.get("position") or "").strip()
        out.append({
            "tsCode": code,
            "name": e["name"],
            "swL2Code": e["sw_l2_code"],
            "swL2Name": e["sw_l2_name"],
            "patterns": e["patterns"],
            "primaryPattern": e["primary_pattern"],
            "tier": e["tier"],
            "seatKind": e["seat_kind"],
            "rank": e["rank"],
            "referenceClose": closes.get(code),
            "oneLineProfile": one_line or None,
            # 裁定 1:**上方机械空间**(机械、排序用)⛔ 永不与预案的第一压力位互顶。
            "upsideRoomMechPct": room.get(code),
            "playbook": None if pb is None else pb.to_dict(),
            "newsState": None if note is None else note["news_state"],
            "newsCategory": None if note is None else note["news_category"],
            "klineComment": None if note is None else note["kline_comment"],
            "explainOk": None if note is None else bool(note["llm_ok"]),
        })
    return out


def _selection_payload(row: dict) -> dict:
    stocks = _selection_stocks(row["trade_date"])
    structured = row["structured"]
    payload = {
        "reportDate": row["report_date"],
        "tradeDate": row["trade_date"],
        "state": row["state"],
        "headline": row["headline"],
        "gaps": row["gaps"],
        "strategy": row["strategy"],
        "paramsPackageVersion": row["params_package_version"],
        "packId": row["pack_id"],
        "packVersion": row["pack_version"],
        "listingSize": row["listing_size"],
        "strictCount": row["strict_count"],
        "relaxedCount": row["relaxed_count"],
        "generatedAt": row["generated_at"],
        "markdown": row["markdown"],
        "structured": structured,
        "direction": structured.get("direction") if isinstance(structured, dict) else None,
        "market": structured.get("market") if isinstance(structured, dict) else None,
        "coverage": structured.get("coverage") if isinstance(structured, dict) else None,
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
        close = stock.get("referenceClose")
        lines.append("收盘价（截至行情日）：" + (f"{float(close):.2f}" if close is not None else "资料暂未保存"))
        levels = (stock.get("playbook") or {}).get("levels") or {}
        if levels:
            lines.append("失效价 {0}；第一压力位 {1}；第二压力位 {2}".format(
                levels.get("invalidation", "—"), levels.get("firstResistance", "—"), levels.get("secondResistance", "—")))
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


def _explain_api(note: Optional[dict]) -> Optional[dict]:
    """在线解释 DTO 使用 API 统一的 camelCase；数据库内部键保持 snake_case。"""
    if note is None:
        return None
    return {
        "tsCode": note.get("ts_code"),
        "profile": note.get("profile", {}),
        "klineComment": note.get("kline_comment"),
        "newsState": note.get("news_state"),
        "newsCategory": note.get("news_category"),
        "news": note.get("news", {}),
        "llmOk": bool(note.get("llm_ok")),
        "filledBy": note.get("filled_by"),
        "createdAt": note.get("created_at"),
    }


# —— V2.5.0 S9/S10:个股详情与预案修改入口 ————————————————————————————————


@app.get(f"{API_PREFIX}/selection/{{trade_date}}/stock/{{ts_code}}",
         dependencies=[Depends(require_token)])
def get_selection_stock(trade_date: str, ts_code: str) -> dict:
    """个股详情 = **解释层资料 + 日K 评价 + 完整预案(全部版本)**。

    ⚠ 三段各自可能缺席,**各自如实标**:
      · `explain=null`  那天解释层没跑过 / 这一只没跑成;
      · `playbook=null` 那天没给这一只冻预案 → **明早核对不了它**;
      · `newsState`     三态(clean / excluded / **unverified**)——
        `unverified` 是「没查成」,⛔ 客户端不许把它显示成「无异常」。
    """
    from neckline.explain import store as explain_store
    from neckline.k9 import store as k9_store
    from neckline.playbook import skeleton as skeleton_mod
    from neckline.playbook import store as pb_store

    day = _parse_day(trade_date)
    listing = {r["ts_code"]: r for r in k9_store.load_listing(day, db_path=_db())}
    entry = listing.get(ts_code)
    if entry is None:
        raise HTTPException(status_code=404,
                            detail=f"{ts_code} 不在 {trade_date} 的清单里")
    notes = explain_store.load_notes(day, codes=[ts_code], db_path=_db())
    versions = pb_store.load_versions(day, ts_code, db_path=_db())
    # 🔴 **改预案要填哪几个数由服务端说**(唯一源 = `playbook/skeleton.py`,同
    # `PushKindOut.label` 的先例)。客户端硬编一份键表 = 第二份事实源,必然漂 ——
    # 而漂的后果是用户改完点提交拿一个英文 422,界面上却一路是绿的。
    # ⚠ 槽位**只有数值**(`kind ∈ {price, percent}`),⛔ 没有「理由」「评价」这类键
    # (架构 §四 第 4 条:预案层知道形态,但不做好坏评价)。
    # ⚠ 形态骨架**不可改** —— 这里给的是「方括号里那几个数」,不是「哪个量跟谁比」。
    try:
        slots = [
            {"key": s.key, "kind": s.kind, "label": s.label, "hint": s.hint}
            for s in skeleton_mod.all_slots(str(entry["primary_pattern"]))
        ]
    except Exception:  # noqa: BLE001  没登记骨架的形态 → 界面不给改,⛔ 不猜一组键
        slots = []
    return {
        "tradeDate": trade_date,
        "tsCode": ts_code,
        "entry": {
            "name": entry["name"], "patterns": entry["patterns"],
            "primaryPattern": entry["primary_pattern"], "tier": entry["tier"],
            "seatKind": entry["seat_kind"], "rank": entry["rank"],
            "swL2Code": entry["sw_l2_code"], "swL2Name": entry["sw_l2_name"],
        },
        "explain": _explain_api(notes.get(ts_code)),
        "playbook": versions[-1].to_dict() if versions else None,
        "playbookVersions": [p.to_dict() for p in versions],
        "playbookSlots": slots,
    }


@app.post(f"{API_PREFIX}/selection/{{trade_date}}/stock/{{ts_code}}/playbook",
          dependencies=[Depends(require_token)])
def post_stock_playbook(trade_date: str, ts_code: str, body: dict) -> dict:
    """**用户修改预案**(K9 §6.4「最终确认由我盘后逐只过目,可修改」)。

    🔴 **append-only**:本端点**只新增一个版本**,原冻结版本一个字不改
    (`k9_playbooks` 的主键含 `version`,应用层也没有 `UPDATE` 那条 SQL)。

    **契约**:请求体只收**数值**,键集 = `playbook/skeleton.py::required_keys(pattern)`
    (由该票的 `primary_pattern` 决定,响应 404/422 里会逐个列出来)。
    多一个键、少一个键、或者哪个值不是数字 → **422**,⛔ 不是「忽略多余的」
    —— 忽略等于默许往预案里塞自由文本评价(§5.2 边界④ 第 2 条)。

    ⚠ 形态骨架**不可改**:用户能改的是方括号里的数,不是「哪个量跟谁比」
    (骨架是机械的,K9 §6.4 分工表)。

    🔴 **冻结闸:D1 一开始就不许再改这一天的预案**(R2-03,落实 K9 §六「D0 **冻结**」
    与 K9 §6.4「最终确认由我**盘后**逐只过目」)。窗口 = 从 D0 收盘到 **D1 零点**
    (D0 是周五就含整个周末),`today >= next_trading_day(D0)` → **409**。

    为什么必须挡住:裁定 10 说「三分支判定的唯一权威是 10:00 结算拍」——
    复审实测过一条把这句话的分母整个抽掉的路径:9:27 判待观察 → **9:45 改一版**把
    成立门槛压到脚下 → 10:01 结算吐 `confirmed`,而账上 `playbook_version` 还记着
    v1。权威那一拍代入了一份**在看过竞价之后**才写下的条件,且事后查不出来。
    ⚠ 这不是「不许改预案」,是「不许**在看过今天的盘之后**改**今天要核对的那一份**」
    —— 明天的清单明天照常可以改。
    ⛔ 返回明确原因,**不是静默忽略**:静默忽略会让用户以为自己改成功了。
    """
    from neckline.calendar import next_trading_day
    from neckline.k9 import store as k9_store
    from neckline.playbook import fill as playbook_fill
    from neckline.playbook import model as pb_model
    from neckline.playbook import skeleton as skeleton_mod
    from neckline.playbook import store as pb_store

    day = _parse_day(trade_date)
    d1 = next_trading_day(day)
    today = _today()
    if today >= d1:
        raise HTTPException(
            status_code=409,
            detail=(f"{trade_date} 的预案已在 D0 冻结,⛔ 不能再改:它要核对的那一天"
                    f"({d1:%Y-%m-%d})已经开始了(今天 {today:%Y-%m-%d})。"
                    "K9 §六 的窗口是「盘后逐只过目」—— 从 D0 收盘到 D1 零点;"
                    "过了这条线再改,10:00 结算拍代入的就会是一份在看过竞价之后"
                    "才写下的条件。要改请改**今天**这一天的清单。"))
    listing = {r["ts_code"]: r for r in k9_store.load_listing(day, db_path=_db())}
    entry = listing.get(ts_code)
    if entry is None:
        raise HTTPException(status_code=404,
                            detail=f"{ts_code} 不在 {trade_date} 的清单里")
    pattern = str(entry["primary_pattern"])
    values, why = playbook_fill.validate_fill(pattern, body)
    if why:
        raise HTTPException(
            status_code=422,
            detail=f"{why};本形态({pattern})要的数值键:"
                   f"{list(skeleton_mod.required_keys(pattern))}")
    item = pb_model.PlaybookInput(
        ts_code=ts_code, name=entry["name"],
        patterns=tuple(entry["patterns"]), primary_pattern=pattern,
        sw_l2_name=entry["sw_l2_name"], close=0.0,
        prev_close=None, high=None, low=None,
    )
    version = pb_store.next_version(day, ts_code, db_path=_db())
    try:
        pb = playbook_fill.assemble(item, values, trade_date=day, version=version,
                                    filled_by="user", source=pb_model.SOURCE_USER)
    except pb_model.PlaybookInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    pb_store.save(pb, db_path=_db())
    return {"tradeDate": trade_date, "tsCode": ts_code, "version": version,
            "playbook": pb.to_dict()}


# —— V2.5.0 S8:次日核对表与 D1 结算 ————————————————————————————————————————


@app.get(f"{API_PREFIX}/checklist/{{trade_date}}", dependencies=[Depends(require_token)])
def get_checklist(trade_date: str) -> dict:
    """**9:29 竞价核对表**:`已触发放弃 / 待开盘后观察` 两段。

    🔴 **响应体里没有「成立」这个取值**(裁定 10 / 守门 G20)——
    `segments` 逐段来自 `ChecklistVerdict` 这个**二值枚举**,类型上就没有第三段。
    `footnote` 恒带一句「成立由 10:00 结算,9:30–10:00 由我自己判定」。

    404 = **那天没跑过那一拍**(⛔ 不是「跑了、表是空的」——那会返回一张两段皆空的表)。

    🔴 **404 的两种原因必须分开说**(R2-11):「D0 本来就没有清单,今天没有要核对的
    东西」是**可信的空**;「那一拍没跑成」是**系统没工作**。从前两者共用一句
    「没有竞价核对表」—— 用户无法分辨,而这正是本项目在别处一贯坚持的三态纪律。
    ⚠ 这里**只把话说清**:是否要在「昨天没有清单」的早晨推一条,是产品决定
    (现行「不推」写在 `auction/pipeline.py::should_push` 的 docstring 里,
    是一次自觉选择)—— 已登记 PROJECT_PLAN §13.1 等用户裁定,⛔ 施工侧不自选。
    """
    from neckline.auction import store as auction_store
    from neckline.calendar import prev_trading_day
    from neckline.k9 import store as k9_store

    day = _parse_day(trade_date)
    out = auction_store.load_checklist(day, db_path=_db())
    if out is None:
        d0 = prev_trading_day(day)
        visible_day = f"{day.year}年{day.month}月{day.day}日"
        visible_d0 = f"{d0.year}年{d0.month}月{d0.day}日"
        if not k9_store.load_listing_codes(d0, db_path=_db()):
            raise HTTPException(
                status_code=404,
                detail=(f"{visible_day}没有竞价核对表：{visible_d0}没有清单，"
                        "今天没有要核对的东西。这是“没有”，不是“没跑成”。"))
        raise HTTPException(
            status_code=404,
            detail=(f"{visible_day}没有竞价核对表：{visible_d0}有清单，"
                    "但今天的竞价核对没有跑成，请检查服务状态。"))
    return out


@app.get(f"{API_PREFIX}/scoreboard/verdicts/{{trade_date}}",
         dependencies=[Depends(require_token)])
def get_verdicts(trade_date: str) -> dict:
    """**10:00 结算拍的三分支终值**(含 `decidedStage`)。

    🔴 **挂在 `scoreboard` 下而不是 `checklist` 下**(裁定 10):这样「它属于成绩线、
    不属于早盘首屏」在**路由上**就看得出来。

    ⚠ `verdict` / `decidedStage` 为 `null` = **今天还没定案**(9:29 判了待观察、
    10:00 那一拍还没跑或没跑成),⛔ 不是「观察」——「观察」是 10:00 真看过之后的
    结论,它带着 `decidedStage='open30'`。
    """
    from neckline.auction import store as auction_store

    day = _parse_day(trade_date)
    rows = auction_store.load_verdicts(day, db_path=_db())
    return {
        "tradeDate": trade_date,
        "verdicts": [
            {
                "tsCode": r["ts_code"], "d0Date": r["d0_date"], "pattern": r["pattern"],
                "playbookVersion": r["playbook_version"],
                "auctionVerdict": r["auction_verdict"],
                "verdict": r["verdict"], "decidedStage": r["decided_stage"],
                "auctionReadings": r["auction_readings"],
                "open30Readings": r["open30_readings"],
                "branches": r["open30_branches"] or (
                    [r["auction_branch"]] if r["auction_branch"] else []),
                "settledAt": r["settled_at"],
            }
            for r in rows
        ],
    }


__all__ = ["app", "VERSION", "API_PREFIX"]
