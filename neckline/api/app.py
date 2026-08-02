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

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status

from neckline import breathing as breathing_store
from neckline import decision_log as decision_log_store
from neckline import watchlist as watchlist_store
from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.inquiry import run_inquiry
from neckline.api.schemas import (
    BoardEventOut,
    BoardOut,
    BreathingTradeIn,
    BreathingTradeOut,
    BreathingTradesOut,
    CandidateOut,
    CircuitEpisodeOut,
    CircuitStateOut,
    ContingencyScenarioOut,
    DecisionCreateIn,
    DecisionLinkIn,
    DecisionOut,
    DecisionReviseIn,
    DecisionsListOut,
    DecisionTrackOut,
    DecisionTrackRowOut,
    DeviceRegisterIn,
    DispatchAlertOut,
    EntrySuggestionOut,
    ExecHintOut,
    InfoCardNewsItemOut,
    InfoCardNewsOut,
    InfoCardOut,
    InfoCardSnapshotOut,
    InfoCardSummaryOut,
    InfoCardTopListOut,
    InquiryIn,
    InquiryLogOut,
    InquiryLogsListOut,
    InquiryOut,
    IntelRankOut,
    IntelWatchBoardsIn,
    IntelWatchBoardsOut,
    K4AdvisoryOut,
    LLMJudgmentOut,
    NewsAlertOut,
    NewsAlertScanStatusOut,
    LLMRoutesIn,
    LLMRoutesOut,
    OkOut,
    PositionCloseIn,
    PositionOpenIn,
    PositionOpenOut,
    PositionOut,
    PositionsOut,
    ProviderCreateIn,
    ProviderOut,
    ProviderUpdateIn,
    ProvidersListOut,
    PushSettingsOut,
    ReferencePlanBuyOut,
    ReferencePlanExitOut,
    ReferencePlanOut,
    ReportOut,
    RetreatBrakeOut,
    ReviewGetOut,
    ReviewUploadOut,
    ScenarioOutcomeIn,
    SettingsOut,
    SettingsProviderOut,
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
from neckline.api.stores import get_inquiry_log, list_inquiry_logs, upsert_device
from neckline.calendar import is_trading_day, prev_trading_day
from neckline.config import ensure_data_dirs
from neckline.llm.factory import get_provider
from neckline.llm.router import TASK_INQUIRY
from neckline.report import store as report_store
from neckline.sentinel import circuit as circuit_store
from neckline.sentinel import dedup
from neckline.sentinel import positions as pos_store
from neckline.sentinel.intraday import is_intraday_now
from neckline.settings_store import (
    create_provider,
    delete_provider,
    get_app_settings,
    get_intel_watch_boards,
    get_llm_routes,
    list_providers_public,
    set_intel_watch_boards,
    set_llm_routes,
    set_push,
    set_review_col_map,
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
# ① **数据**:`scripts/fix_moneyflow_schema.py` 把 902 个脏分区 cast 回 Float64(生产已跑,
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
# v1.5.0(§五 v1.5-⑤-E「A2 客户端版本号治理」):本行在 ⑤ 就已改成 v1.5.0——纯本地源码
# 常量变更,**不构成部署**(生产此刻仍跑 v1.4.1,直到 ⑥ 真正上云重启服务)。这么早改的
# 原因:⑤-E 的守门单测要求 `VERSION`(去 v 前缀)与客户端 `project.yml`/`pbxproj` 的
# `MARKETING_VERSION` 三者恒等,三者必须在同一次提交里一起动,否则守门测试本身就会
# 常年红。⑥-A「版号 → v1.5.0」到时只是把这行已经写好的代码部署上线,不再是新改动。
# v1.5.1(2026-07-30,两线 review 黄牌集中修复)→ **已上云 12:07**。
# v1.5.2(2026-07-30,用户报障:LLM 把 2024 年研报当现行参照 —— 三处提示词都没注入当前
# 日期):纯提示词层修复(日期锚 + 时效纪律 + 检索词年份引导),**零契约改动、客户端
# 零改动**(已装 1.5.1 App 无需换包,设置屏会显示版本差提示,属预期)。同上,本行与
# `project.yml`/`pbxproj` 同一次提交动;`/health` 返 v1.5.2 即为本次部署的到位判据。
VERSION = "v1.5.2"
API_PREFIX = "/api/v1"

# —— 测试注入开关(生产恒 True / 恒默认)——————————————————————————————————
# startup 是否起哨兵后台轮询;可用环境变量 NECKLINE_ENABLE_SENTINEL=0 关(冒烟脚本用)。
ENABLE_SENTINEL = os.environ.get("NECKLINE_ENABLE_SENTINEL", "1") != "0"
_DB_PATH_OVERRIDE: Optional[Path] = None      # 隔离库(None → settings.db_path)
# v1.3-⑥ 后端补齐:`GET/PUT /settings/intel-boards` 校验板块名需读 `ths_index.parquet`
# (`report.sectors.load_index_names`)——这是 app.py 端点层首次直接触碰 parquet(此前
# 全部经 `_db()` SQLite 或已由 `market_data`/`tushare_client` 模块内部处理)。同
# `_DB_PATH_OVERRIDE` 姿势新增此注入点,供测试指向隔离 parquet 目录,不污染/依赖真实
# 项目 `data/parquet`。
_PARQUET_DIR_OVERRIDE: Optional[Path] = None  # 隔离 parquet 目录(None → settings.parquet_dir)
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


def _parquet_dir() -> Optional[Path]:
    return _PARQUET_DIR_OVERRIDE


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
    (每 5min 探一次,不空转)。

    **V2-⑧-B 挂了两条旁路分支**(存拍,各自独立 try/except,**不改任何轮询节奏、
    不影响四哨兵与盘前校准的成败**):09:25–09:30 竞价快照;15:05–15:35 当日存拍一次性
    落盘 + 记 `capture_status`。盘中每一拍的分钟报价累计在 `run_tick` 内部完成(用的就是
    那一拍已经拉到的行情,零额外网络)。"""
    from neckline.sentinel import capture
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
                    # 汇总推送门槛 = `should_push_summary`(单一源在 PrecallResult):有需动作
                    # 判定 **或** 熔断锁定中(审计 🟡-4:锁定期零判定也要发「今日只减不加」)。
                    if pr.should_push_summary:
                        await asyncio.to_thread(
                            notify.push_precall_summary, pr.counts,
                            circuit_locked=pr.circuit_locked, db_path=_db(),
                        )
                    for ex in pr.d5_exits:
                        await asyncio.to_thread(
                            notify.push_d5_exit, ex.name, ex.ts_code, ex.d,
                            kind=ex.state, max_hold_effective=ex.max_hold_effective,
                            two_tier=ex.two_tier, db_path=_db(),
                        )
            except Exception:  # noqa: BLE001  盘前一拍异常同样绝不能掀翻轮询主循环
                logger.warning("盘前校准一拍异常(已吞,继续轮询)", exc_info=True)
            # V2-⑧-B 旁路:09:25 竞价快照(当日一次,内部自防重)。**独立 try**,
            # 与上面盘前校准的成败互不影响。
            if capture.is_auction_capture_window(now):
                try:
                    await asyncio.to_thread(
                        capture.run_auction_capture, now.date(), now,
                        db_path=_db(), parquet_dir=_parquet_dir(),
                    )
                except Exception:  # noqa: BLE001
                    logger.warning("竞价快照采集异常(已吞,存拍是旁路)", exc_info=True)
            interval = _SENTINEL_PREOPEN_POLL_SEC
        elif capture.is_flush_window(now):
            # V2-⑧-B 旁路:15:05 之后把当日内存累计一次性落盘(D4 拍板)+ 记
            # `capture_status`。幂等(台账在 SQLite,重启也不会重复写),窗口内多探
            # 几次无副作用;**不改 interval**,收盘后仍是 5min 一探的待机节奏。
            try:
                await asyncio.to_thread(
                    capture.flush_day, now.date(),
                    db_path=_db(), parquet_dir=_parquet_dir(), now=now,
                )
            except Exception:  # noqa: BLE001
                logger.warning("盘中存拍落盘异常(已吞,存拍是旁路)", exc_info=True)
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

def _shape_info_card_summary(d: Optional[Dict[str, Any]]) -> Optional[InfoCardSummaryOut]:
    """`Candidate.info_card_summary` 存档(v1.4-④,`info_card.InfoCardSummary.
    to_public_dict()` 已是 camelCase)→ `InfoCardSummaryOut`。空/缺(老报告快照、或
    该次生成时保险丝触发降级)→ `None`,客户端按"该信息暂不可用"处理,不冒充
    "确认无内容"(同 `intel_rank` 惯例,§3.8)。"""
    if not d:
        return None
    news = d.get("news") or {}
    top_list = d.get("topList") or {}
    return InfoCardSummaryOut(
        snapshot=InfoCardSnapshotOut(**(d.get("snapshot") or {})),
        mildBand=bool(d.get("mildBand", False)),
        news=InfoCardNewsOut(
            scanned=bool(news.get("scanned", False)),
            items=[InfoCardNewsItemOut(**it) for it in news.get("items", []) or []],
            unavailableReason=news.get("unavailableReason"),
        ),
        topList=InfoCardTopListOut(**top_list),
    )


def _shape_reference_plan(d: Optional[Dict[str, Any]]) -> Optional[ReferencePlanOut]:
    """`Candidate.reference_plan` 存档(v1.5-①,`reference_plan.ReferencePlan.
    to_public_dict()` 已是 camelCase)→ `ReferencePlanOut`。空/缺(老报告快照、或该次
    生成整体异常)→ `None`,客户端不冒充"确认无参考"(同 `_shape_info_card_summary`
    姿势,§2.0 第〇原则)。"""
    if not d:
        return None
    return ReferencePlanOut(
        status=d.get("status", "unavailable"),
        buy=ReferencePlanBuyOut(**d["buy"]) if d.get("buy") else None,
        buyUnavailableReason=d.get("buyUnavailableReason"),
        exit=ReferencePlanExitOut(**d["exit"]) if d.get("exit") else None,
        exitUnavailableReason=d.get("exitUnavailableReason"),
        script=d.get("script"),
        vetoReason=d.get("vetoReason"),
        unavailableReason=d.get("unavailableReason"),
        disclaimer=d.get("disclaimer", ""),
        degraded=bool(d.get("degraded", False)),
    )


# —— v1.5-③-B:老四件套「键保留 + 值换过渡文案」单一源(PROJECT_PLAN §五 v1.5-③-B,
#    向后兼容硬约束的落点)——————————————————————————————————————————————————
# 已装 v1.4.1 客户端对 `buyPoint`/`stop`/`target`/`invalidation` 四键是**硬解码**
# (`client/Models.swift::Candidate.init(from:)` 用 `try c.decode(String.self,…)`,
# 非 `decodeIfPresent`):服务端一旦不发这四个键就整份报告解不出、今日计划全空。
# v1.5.0 起候选生成路径(`intel_candidates.py`)不再产出这四件套的自然语言文案
# (`Candidate.entry_plan` 等字段恒为默认空串),故 `_shape_candidate` **不再从落库
# 快照读取这四个字段**,一律无条件下发本常量——同一句话发四遍(四键语义已合一:
# 「查看新版参考三件套」),不按字段各自拍不同文案(避免四句话各自维护、更难保持
# 一致)。**老报告快照(v1.5.0 前生成,`entry_plan` 等字段是真文本)同样统一改发本
# 通知**,不按报告新旧分叉行为——老客户端拿旧报告与新报告的体验应一致(反正它也
# 用不了 `referencePlan`),避免"有的历史报告能看到真文案、有的看不到"这种不必要
# 的不一致。真正删除这四个键的条件见 PROJECT_PLAN §七 P3-27(双端换包到 ≥1.5.0 后)。
LEGACY_FOURPIECE_NOTICE = "本版已由「参考三件套」取代四件套,请更新 App 查看(参考、非指令)。"


def _shape_candidate(c: Dict[str, Any], judgment: Optional[Dict[str, Any]]) -> CandidateOut:
    """报告落库的候选 JSON 快照 → 客户端契约。同码不重写:字段直接取自
    `Candidate.public_dict()` 存档,不在此重算任何领域值——**唯一例外是老四件套
    四键**,见 `LEGACY_FOURPIECE_NOTICE` 上方注释(无条件下发过渡文案,不读快照)。"""
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
        buyPoint=LEGACY_FOURPIECE_NOTICE,
        stop=LEGACY_FOURPIECE_NOTICE,
        target=LEGACY_FOURPIECE_NOTICE,
        invalidation=LEGACY_FOURPIECE_NOTICE,
        invalidationSpec=c.get("invalidation_spec", {}) or {},
        entrySpec=c.get("entry_spec", {}) or {},
        formTags=c.get("pattern_tags", []) or [],
        hotSectors=c.get("hot_sectors", []) or [],
        sectorNames=c.get("sector_names", []) or [],
        # v1.3-③-C3:候选新语义字段(旧报告快照无 → 默认空,前向兼容)。
        k4Flags=c.get("k4_flags", []) or [],
        intelRank=IntelRankOut(**(c.get("intel_rank") or {})),
        # v1.4-④-B:信息卡摘要(老报告快照无该键 → None,前向兼容)。
        infoCard=_shape_info_card_summary(c.get("info_card_summary")),
        # v1.4-⑤-A:执行提示(老报告快照无该键 → 默认空列表,前向兼容)。
        execHints=[ExecHintOut(**h) for h in (c.get("exec_hints") or [])],
        # v1.5-①-F:参考件三件套(老报告快照无该键/该键为 None → None,前向兼容)。
        referencePlan=_shape_reference_plan(c.get("reference_plan")),
        llmJudgment=llm,
        # v1.5-②-B:预算耗尽未发起(老报告快照无该键 → False,前向兼容;与
        # `llmJudgment is None` 单独并存不冲突——见 CandidateOut.judgeSkipped 注释)。
        judgeSkipped=bool(c.get("judge_skipped", False)),
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
        # v1.5-④-A1:K4 派发警示(老报告快照无该键 → 默认空列表,前向兼容)。`level`
        # 不透传(契约故意省略,见 `DispatchAlertOut` docstring)。
        dispatchAlerts=[
            DispatchAlertOut(
                code=h.get("code", ""), label=h.get("label", ""),
                evidence=h.get("evidence", ""), evidenceStrength=h.get("evidence_strength", ""),
            )
            for h in (d.get("dispatch_alerts") or [])
        ],
    )


def _shape_news_alert(a: Dict[str, Any], names: Dict[str, str]) -> NewsAlertOut:
    """`news_alerts` 表行 → 客户端契约(v1.3-③-C4)。表不存 `name`(同
    `llm_judgments` 惯例),这里从 `stock_basic` 解析补上展示便利字段。"""
    code = a.get("ts_code", "")
    return NewsAlertOut(
        code=code, name=names.get(code, code),
        category=a.get("category", ""), summary=a.get("summary", ""), source=a.get("source", ""),
    )


def _shape_report(rep: Dict[str, Any]) -> ReportOut:
    from neckline.report.candidates import _load_stock_names
    from neckline.report.news_alerts_store import load_news_alerts
    from neckline.report.pipeline import compute_missed_entry_hint

    td = rep["trade_date"]
    d = datetime.strptime(td, "%Y%m%d").date()
    judgments = {j["ts_code"]: j for j in report_store.load_llm_judgments(d, db_path=_db())}
    candidates = [_shape_candidate(c, judgments.get(c.get("ts_code", ""))) for c in rep.get("candidates", [])]
    watchlist_check = [_shape_watchlist_check(w) for w in rep.get("watchlist", []) if isinstance(w, dict)]
    # v1.3-③-C4:命中告警条目独立表实时查(同 llm_judgments 的「live join」惯例,
    # 不像 intel/watchlist 那样整段嵌 JSON——见 news_alerts.py 模块头设计说明)。
    alert_rows = load_news_alerts(d, db_path=_db())
    alert_names = _load_stock_names(list({r["ts_code"] for r in alert_rows}), _db()) if alert_rows else {}
    news_alerts = [_shape_news_alert(a, alert_names) for a in alert_rows]
    news_alerts_scan = [
        NewsAlertScanStatusOut(
            source=s.get("source", ""), scanned=bool(s.get("scanned", False)), reason=s.get("reason", ""),
            codesTotal=s.get("codesTotal", 0), codesFailed=s.get("codesFailed", 0),
            # v1.3-⑥ 后端补齐:领域层早产出 codesSkipped(见 news_alerts.py),此前这里
            # 没读取 → 契约清单承诺的字段实际从未抵达客户端(schemas.py 同批已补字段声明)。
            codesSkipped=s.get("codesSkipped", 0),
            # v1.3.4 同批新增(老报告快照没有这个键 → 缺省 0,前向兼容不崩)。
            codesNoSearch=s.get("codesNoSearch", 0),
            # v1.4-⑥-B 自选隔日轮扫披露(同上:领域层产出后这里必须显式读,否则 pydantic
            # 丢弃;老快照无此键 → 缺省 ""/0)。与 codesSkipped 语义不合并,见 schemas.py。
            rotationGroup=s.get("rotationGroup", ""),
            codesRotationDeferred=s.get("codesRotationDeferred", 0),
        )
        for s in rep.get("news_alerts_scan", [])
    ]
    return ReportOut(
        tradeDate=td,
        generatedAt=rep.get("generated_at", ""),
        strategyVersion=rep.get("strategy_version", ""),
        sentiment=rep.get("sentiment", {}),
        sectors=rep.get("sectors", []),
        candidates=candidates,
        watchlistCheck=watchlist_check,
        missedEntryHint=compute_missed_entry_hint(d, db_path=_db()),   # v1.1-B.4 实时算(补录后自动消失)
        intel=rep.get("intel", {}),                       # v1.3-③-C1,透传落库快照(同 sentiment 惯例)
        sectorMoneyflow=rep.get("sector_moneyflow", {}),   # v1.3-③-C2,透传落库快照
        newsAlerts=news_alerts,                            # v1.3-③-C4,独立表实时查
        newsAlertsScan=news_alerts_scan,                    # v1.3-③-C4,透传落库快照(同 intel 惯例)
        dataFreshness=rep.get("data_freshness", {}),        # v1.4-①-C,透传落库快照(不读时重算)
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


@app.get(f"{API_PREFIX}/report/{{date}}/info-card/{{code}}", dependencies=[Depends(require_token)])
def report_info_card(date: str, code: str) -> InfoCardOut:
    """单只完整信息卡(plan §五 v1.4-④-B,考卷信息集与实盘情报包同构的落地端点)。
    **服务端现算,不落库**——除 `k4_flags`(§硬要求"复用③已算好的 k4_flags,不重算")
    原样取自当日报告存档外,其余(K 线/RS 线/行业分歧线/快照/消息面/龙虎榜/市场语境)
    全部独立现读 parquet/DB,不依赖 `CandidateOut.infoCard` 摘要位是否算成功。

    404 两个 reason(客户端 `mapReason` 须各加 case,守项目 CLAUDE.md 404 映射坑):
    `report_not_found`(日期格式非法 / 该日从未生成过报告)、`code_not_in_report`
    (该日报告存在,但这只票不在当日候选榜里——`code` 经 `normalize_ts_code` 归一后
    比对,裸 6 位代码与带后缀 `ts_code` 均可)。"""
    from neckline.report.info_card import build_info_card
    from neckline.review.parse import normalize_ts_code

    rep = report_store.load_report_by_str(date, db_path=_db()) if (len(date) == 8 and date.isdigit()) else None
    if rep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "report_not_found"})
    ts_code = normalize_ts_code(code)
    cand = next((c for c in rep.get("candidates", []) if c.get("ts_code") == ts_code), None)
    if cand is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "code_not_in_report"})
    trade_date = datetime.strptime(rep["trade_date"], "%Y%m%d").date()
    card = build_info_card(
        trade_date, ts_code,
        k4_flags=cand.get("k4_flags", []) or [],
        name=cand.get("name") or ts_code,
        parquet_dir=_parquet_dir(), db_path=_db(),
    )
    return InfoCardOut(**card.to_public_dict())


# —— 4A.3 盘中看板 ————————————————————————————————————————————————————

_SENTINEL_LABEL = {"entry": "买点", "invalidation": "证伪", "holding": "持仓"}
# v1.1:盘前校准 / D5 两新 sentinel 类型的中文标签(G.3 客户端看板明细;未识别原样透传)。
_SENTINEL_LABEL.update({"precall": "盘前校准", "d5exit": "D5退出"})
# v1.1-H2:退潮黄色预警(retreat/warn)进事件列表的标签(红色 retreat/brake 仍走
# retreatBrake 红条,不进列表)。客户端 SentinelKind 无 "退潮" 枚举 → kind=nil →
# 中性色渲染,不崩(不改客户端)。
_SENTINEL_LABEL.update({"retreat": "退潮"})


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
        # 市场级标记(空 ts_code)默认不进事件列表:退潮红色刹车(retreat/brake)已走
        # retreatBrake 红条;盘前校准的当日「tick 已跑」标记(precall/tick)是内部去重锚。
        # **例外**(v1.1-H2):退潮黄色预警(retreat/warn)是唯一要进列表的市场级事件——
        # 它不走红条(不是刹车)、只作看板提示,verdict 文案已带「黄色预警」前缀。
        is_yellow_retreat = e["sentinel"] == "retreat" and e["event_key"] == "warn"
        if not e["ts_code"] and not is_yellow_retreat:
            continue
        asof = e.get("pushed_at", "") or asof
        events.append(BoardEventOut(
            sentinel=_SENTINEL_LABEL.get(e["sentinel"], e["sentinel"]),
            code=e["ts_code"],
            name=("市场情绪" if is_yellow_retreat else names.get(e["ts_code"], e["ts_code"])),
            eventKey=e["event_key"],
            verdict=(e.get("payload") or {}).get("body", ""),
            ts=e.get("pushed_at", ""),
        ))
    return BoardOut(tradeDate=trade_date.strftime("%Y%m%d"), asof=asof, retreatBrake=retreat, events=events)


# —— 4A.4 持仓 ————————————————————————————————————————————————————————

def _resolve_names(codes: List[str]) -> Dict[str, str]:
    """从 `stock_basic` 补股票名(看板/持仓展示用)。查不到 → 不填(调用方兜底回 code)。

    v1.3.4 起实现下沉到 `data.market_data.resolve_stock_names`(唯一实现),本函数只
    绑定本进程的 db_path——问询台也要按代码查中文名(喂 LLM 材料 + 联网检索词),
    两处不各写一份 `load_stock_basic` + filter。"""
    from neckline.data.market_data import resolve_stock_names

    return resolve_stock_names(codes, _db())


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


def _resolve_price_stale(codes: List[str]) -> Dict[str, Any]:
    """持仓票的价格陈旧度(v1.4-①-B / §七 P0-2)。`{ts_code: PriceStale}`,**只含当日
    无 EOD 行的票**。领域实现在 `data/price_stale.py`(只读 parquet + 日历,不联网)。

    整体 try 兜底:陈旧度是**附加诚实标注**,它自己出问题绝不能掀翻持仓列表(持仓卡是
    每日最常看的一屏)。降级后表现 = 与 v1.4 之前一致(不带 priceStale、判向不挂起)。"""
    if not codes:
        return {}
    try:
        from neckline.data.price_stale import resolve_price_stale

        return resolve_price_stale(codes, date.today(), parquet_dir=_parquet_dir())
    except Exception:  # noqa: BLE001
        logger.warning("持仓价格陈旧度判定异常(已降级为空,不阻断持仓列表)", exc_info=True)
        return {}


def _active_config() -> Tuple[float, int, float, Optional[float]]:
    """现役策略 config 的四个值(单一事实源 `brain.active_config`,§3.8 铁律):
    (stop_pct, max_hold_days, single_cap, take_profit_retrace)。无现役版本(异常状态)
    → 退回 `MomentumConfig` 字段默认(不在此另拍字面量)。

    **兜底判据是「键缺失」不是「falsy」(2026-07-27 审计 🔵-9)**:旧写法 `cfg.get(k) or fb.k`
    会把章程**显式**设的 0 / None(如未来某版 `stop_pct=None` = 不设止损)悄悄换回默认
    0.05 —— 那是「章程说不设止损、系统偷偷给你设了 5%」。现按 `k in cfg` 判存在性,显式值
    一律照用;只有键真缺失才落 `MomentumConfig` 字段默认。`stop_pct` 若显式 None,
    调用方(止损线/距止损)按 0.0 处理 = 不派生止损线,与「不设止损」语义一致。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig

    fb = MomentumConfig()
    cfg = brain.active_config(db_path=_db())

    def _pick(key, fallback):
        return cfg[key] if key in cfg else fallback

    stop_pct = _pick("stop_pct", fb.stop_pct)
    max_hold = _pick("max_hold_days", fb.max_hold_days)
    single_cap = _pick("single_cap", fb.single_cap)
    tpr = _pick("take_profit_retrace", fb.take_profit_retrace)
    return (
        float(stop_pct if stop_pct is not None else 0.0),
        int(max_hold),
        float(single_cap),
        (float(tpr) if tpr is not None else None),
    )


def _active_momentum_config() -> "MomentumConfig":
    """现役 config → 完整 `MomentumConfig`(携 v1.3 两档时间退出字段);无现役版本 → 字段默认。
    供 `PositionOut` 两档时间退出派生(`classify_time_exit`,单一源 sentinel/precall)复用。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum import MomentumConfig

    cfg = brain.active_config(db_path=_db())
    return MomentumConfig(**cfg) if cfg else MomentumConfig()


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
    d_count: int, eff_max: int, dist_to_stop_pct: Optional[float],
    retrace_state: Optional[Dict[str, Any]], time_exit_state: str,
) -> str:
    """今日动作提示文案(纯展示层,优先级:时间退出 > 回落止盈 > 跌破/逼近止损 > 持有中)。
    v1.3-① 两档:`hard_cap_exit`/`time_exit_next_day` 走离场优先;`profit_exempt` 是「豁免时间
    退出、交回落+止损管到硬上限」的持有态(不抢离场);`holding` 常规持有。K1 单档下
    `time_exit_state` 只会是 `time_exit_next_day`(d≥max_hold)或 `holding`,行为与 v1.3 前一致。

    **v1.4-①-B `suspended_hold`**:当日无 EOD 行且尚未定格 → 判向挂起。文案**最优先**
    (它就是为了盖掉那句「按计划离场」——催用户去卖一只卖不掉的票正是 P0-2 的病根),
    且必须说清「D 计数照走、判向挂着、复牌当日再定」。"""
    from neckline.sentinel.precall import HARD_CAP_EXIT, PROFIT_EXEMPT, SUSPENDED_HOLD, TIME_EXIT_NEXT_DAY

    if time_exit_state == SUSPENDED_HOLD:
        return (f"停牌/无当日行情,时间退出判向挂起(D{d_count} 照常累计,"
                f"复牌当日收盘再定格)")
    if time_exit_state == HARD_CAP_EXIT:
        return f"D{d_count} 已达浮盈硬上限 D{eff_max},按计划离场(浮盈豁免时间退出到顶)"
    if time_exit_state == TIME_EXIT_NEXT_DAY:
        return f"D{d_count} 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)"
    if retrace_state and retrace_state.get("triggered"):
        return "回落止盈已触发,按计划离场"
    if dist_to_stop_pct is not None:
        if dist_to_stop_pct <= 0:
            return "现价已跌破止损线,若条件单未成交请立即人工确认(系统不代下单)"
        if dist_to_stop_pct <= 0.02:
            return f"距止损线 {dist_to_stop_pct:.1%},盯紧条件单"
    if time_exit_state == PROFIT_EXEMPT:
        return f"浮盈豁免时间退出,交回落止盈+止损管到硬上限(D{d_count}/D{eff_max})"
    return f"持有中(D{d_count}/D{eff_max})"


def _locked_time_exit_day(buy_date: date, locked_date: Optional[str]) -> Optional[int]:
    """定格发生当时的 D 计数(v1.4-⑥-C)。`locked_date` 是 `holding_eod_check.
    time_exit_locked_date`('YYYYMMDD',定格发生那天)。

    **未定格 / 老快照没记这一格 / 串坏了 → None**(如实说不知道,不拿今天冒充定格日
    ——那会把一个从没发生过的"D5 准时定格"编出来)。D 计数复用 `positions.d_count`
    单一源(买入日 = D1,交易日历口径),不在 API 层另算一份日历。"""
    if not locked_date:
        return None
    try:
        d = datetime.strptime(locked_date, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None
    return pos_store.d_count(buy_date, d)


def _shape_circuit(state: "circuit_store.CircuitState") -> CircuitStateOut:
    """熔断领域状态 → 客户端契约(诚实边界字段透出)。同 `_shape_candidate` 透传惯例。"""
    if state.episode is None:
        return CircuitStateOut(locked=state.locked)
    ep = state.episode
    return CircuitStateOut(
        locked=state.locked,
        episode=CircuitEpisodeOut(
            triggerReason=ep.trigger_reason,
            triggeredAt=ep.triggered_at,
            triggerRefDate=ep.trigger_ref_date,
            basisTradesCount=ep.basis_trades_count,
            basisWindow=ep.basis_window,
            note=ep.note,
        ),
    )


@app.get(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def list_positions() -> PositionsOut:
    from neckline.report.holding_store import load_latest_checks_by_position, locked_time_exit_map
    from neckline.sentinel.precall import resolve_time_exit

    holdings = pos_store.load_open_positions(db_path=_db())
    codes = [h.ts_code for h in holdings]
    names = _resolve_names(codes)
    prices = _resolve_prices(codes)
    stop_pct, max_hold, _single_cap, tpr = _active_config()
    cfg = _active_momentum_config()
    # v1.3-② 持仓 K4 牌:读最近一份 16:35 体检快照嵌 k4Advisory[] + scenarioReviewPending
    # (服务端算好,客户端不重算 250 日面板;刚开仓未体检 → 空数组/False)。
    k4_snapshots = load_latest_checks_by_position(db_path=_db())
    # 定格判向:与 precall 走**同一个** store 读函数(审计 🔴-1 的主题就是「三个消费点各读
    # 各的」,故这里刻意不图省事去读 k4 快照里那份带过来的副本)。
    locked = locked_time_exit_map(db_path=_db())
    # v1.4-①-B(§七 P0-2):持仓票当日无 EOD 行 → 陈旧度 + 原因(停牌 / 数据缺口 / 不知道)。
    # 只读 parquet + 日历,不联网(请求期不能挂网络);整体异常一律降级为「没有陈旧票」,
    # **绝不掀翻持仓列表**(持仓卡是每日最常看的一屏)。
    stale_map = _resolve_price_stale(codes)
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
        # v1.3-① 两档时间退出:服务端判好 maxHoldDaysEffective + timeExitState 下发,客户端不重算。
        # **审计 🔴-1 修复(用户拍板方案 A「D5 判一次定格」)**:判向在 D5 那天由 16:35 EOD 管线
        # 定格落库,本端点只**读定格值**交 `resolve_time_exit` 解析——**不再用实时价现算净浮盈
        # 重判**。旧写法有两个病:①请求期用实时价 → 同一天里刷新一次就可能翻向;②与 16:35
        # EOD close / precall 快照三处数据源各不相同。净浮盈的唯一判向口径已收敛到 EOD close
        # (见 `report/holding_k4_check.py`)。
        snap = k4_snapshots.get(h.id) or {}
        stale = stale_map.get(h.ts_code)
        # v1.4-①-B:`data_unavailable` 只在**判定点且尚未定格**时把判向改成 `suspended_hold`
        # (见 `resolve_time_exit`);其余分支逐位不变。判据用**当日无 EOD 行**这一位,
        # 与 16:35 管线的 `has_data` 同义(两处各自就近取数,不互相依赖对方的快照)。
        lock = locked.get(h.id) or {}
        te_state, eff_max = resolve_time_exit(
            dcount, cfg, lock.get("state"),
            data_unavailable=stale is not None,
        )
        # v1.4-⑥-C(§七 P1-6):定格日 ≠ D5 的显式标注。**纯派生展示位,不参与上面的判向**
        # —— 判向已在上一行由 `resolve_time_exit` 读定格值算完,这里只是把「那天是 D 几、
        # 比 max_hold_days 晚了几天」翻出来给界面提示(EOD 管线断跑 / ①-B 停牌票复牌后定格
        # 都会晚于 D5,系统一直如实落库但此前界面不说)。`d_count` 复用 `positions.d_count`
        # 单一源,不在这里重算日历。
        locked_day = _locked_time_exit_day(buy, lock.get("date"))
        locked_late = max(0, locked_day - max_hold) if locked_day is not None else 0
        k4_advisory = [
            K4AdvisoryOut(
                code=hit.get("code", ""), label=hit.get("label", ""),
                level=hit.get("level", "normal"), evidence=hit.get("evidence", ""),
                evidenceStrength=hit.get("evidence_strength", "price_volume"),
            )
            for hit in (snap.get("hits") or [])
        ]
        out.append(PositionOut(
            id=h.id, code=h.ts_code, name=names.get(h.ts_code, h.ts_code),
            buyPrice=h.buy_price, qty=h.qty, entryReason=h.note or "",
            buyDate=h.buy_date, price=price,
            status=h.status, stopLine=stop_line, stopOrderChecked=False,
            dCount=dcount, maxHoldDays=max_hold,
            distToStopPct=(round(dist, 4) if dist is not None else None),
            retraceState=retrace,
            todayAction=_today_action(dcount, eff_max, dist, retrace, te_state),
            maxHoldDaysEffective=eff_max, timeExitState=te_state,
            timeExitLockedDay=locked_day, timeExitLockedLateDays=locked_late,
            buyFees=h.buy_fees, sellFees=h.sell_fees,
            priceStale=(stale.to_public_dict() if stale is not None else None),
            k4DataUnavailable=snap.get("data_unavailable"),   # None=老快照未记录,如实透 null
            k4Advisory=k4_advisory, scenarioReviewPending=bool(snap.get("scenario_review")),
        ))
    return PositionsOut(holdings=out, circuit=_shape_circuit(circuit_store.get_state(db_path=_db())))


# 补录预填区间的**保守下沿因子**(下限档金额 = `single_cap` × 本值)。**纯展示层因子,
# 住这一处**——不是领域常量:领域上的唯一事实源仍是现役 config 的 `single_cap`(违纪
# 判定上限,§2.1 第 3 条)。改这个数只改「界面上默认给用户看的下沿档」,不改任何纪律判定。
_ENTRY_SUGGESTION_FLOOR_FRAC = 0.5


@app.get(f"{API_PREFIX}/positions/entry-suggestion", dependencies=[Depends(require_token)])
def entry_suggestion(code: str = "", price: float = 0.0) -> EntrySuggestionOut:
    """一键补录预填**区间**(plan v1.2-E.5,**只读计算,不写台账**)。

    v1.2 章程把 `single_cap` 的语义从「推荐值」改成「**违纪判定上限**」(§2.1 第 3 条:
    单笔金额不定死,由用户当场在区间内自定)——故这里返两档而非一个数,**系统不替用户
    拍板单笔金额**:`qtyHigh`/`capCeil` 对应违纪上限(**非推荐值**,客户端文案须标注),
    `qtyLow`/`capFloor` 是保守下沿。手数按 A 股 100 股/手向下取整;派生 `stopLine` =
    现价×(1−`stop_pct`)。price≤0 → 两档手数均 0、stopLine=0(防除零)。补录/清仓写入仍走
    既有 `POST /positions` / `POST /positions/{id}/close`(不改)。"""
    stop_pct, _max_hold, single_cap, _tpr = _active_config()
    cap_ceil = float(single_cap)
    cap_floor = cap_ceil * _ENTRY_SUGGESTION_FLOOR_FRAC
    if price <= 0:
        return EntrySuggestionOut(
            code=code, price=price, qtyLow=0, qtyHigh=0,
            capFloor=cap_floor, capCeil=cap_ceil, stopLine=0.0,
        )
    return EntrySuggestionOut(
        code=code, price=price,
        qtyLow=int(math.floor(cap_floor / price / 100) * 100),
        qtyHigh=int(math.floor(cap_ceil / price / 100) * 100),
        capFloor=cap_floor, capCeil=cap_ceil,
        stopLine=_stop_line(price, stop_pct),
    )


# v1.4-①-A 两个新 400 reason 码(**客户端 `APIClient.mapReason` 必须逐个加 case**,守项目
# CLAUDE.md「404/reason 映射」坑:别指望 fallback 猜对文案)。
REASON_NOT_TRADING_DAY = "not_trading_day"
REASON_FUTURE_BUY_DATE = "future_buy_date"


def _parse_buy_date_or_400(raw: Optional[str]) -> date:
    """`PositionOpenIn.buyDate` → `date`。**None/空串 = 未指定 → 今天**(与 v1.4 之前
    `buy_date=date.today()` 写死的行为逐位一致,老客户端零感知)。

    校验顺序刻意是「格式 → 未来日 → 交易日」:未来日先判,是因为「明天」很可能同时也是
    交易日,若先判交易日会把未来日误报成 `not_trading_day`(reason 说谎)。格式非法不静默
    退回今天 —— 「没给」与「给错了」必须能分开(§3.8 铁律)。"""
    if raw is None or not str(raw).strip():
        return date.today()
    s = str(raw).strip()
    try:
        parsed = datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "reason": REASON_NOT_TRADING_DAY},
        ) from None
    if parsed > date.today():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "reason": REASON_FUTURE_BUY_DATE},
        )
    from neckline.calendar import is_trading_day
    if not is_trading_day(parsed):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "reason": REASON_NOT_TRADING_DAY},
        )
    return parsed


@app.post(f"{API_PREFIX}/positions", dependencies=[Depends(require_token)])
def open_position(body: PositionOpenIn) -> PositionOpenOut:
    """开仓录入(§3.8 铁律:系统永不自动下单,此处只录台账)。

    **v1.4-①-A(§七 P0-1)**:`buyDate`(可选 'YYYYMMDD')= 真实成交日,**缺省仍取今天**
    (老客户端不传时与 v1.4 之前逐位一致)。补录历史成交时买入日被盖成补录当天 → D 计数 /
    两档时间退出判向 / 回落止盈峰值起点 / 周复盘持有天数**全部起点错**,故开这个口子。
    校验两条,违反即 400 + reason(客户端 `APIClient.mapReason` 各有 case):
      · 非交易日(`trade_cal` 判)→ `not_trading_day`
      · 晚于今天 → `future_buy_date`
    格式非法('YYYYMMDD' 之外)同样按 `not_trading_day` 拒(不静默吞成今天——「没给」和
    「给错了」必须能分开)。写入仍走既有领域层 `sentinel/positions.py::open_position`
    (它本就收 `buy_date`),**不重构领域层**。
    """
    buy_date = _parse_buy_date_or_400(body.buyDate)
    pid = pos_store.open_position(
        ts_code=body.code, buy_price=body.buy_price, qty=body.qty,
        buy_date=buy_date, note=(body.entry_reason or None),
        buy_fees=body.buyFees, db_path=_db(),
    )
    stop_pct, _mh, _sc, _tpr = _active_config()
    return PositionOpenOut(ok=True, position_id=pid, stop_line=_stop_line(body.buy_price, stop_pct))


@app.post(f"{API_PREFIX}/positions/{{position_id}}/close", dependencies=[Depends(require_token)])
def close_position(position_id: int, body: PositionCloseIn) -> OkOut:
    """清仓录入(§3.8 只记账,永不代下单/撤单)。可选 `closeReason` 落库(v1.2-A2);
    清仓后折进熔断评估(`circuit.evaluate_after_close`)——越阈值即建触发行 + 第五类
    APNs 推送。**熔断是纯提醒层**:评估/推送**尽力而为、异常吞掉不阻断清仓主流程**
    (F.3),服务端**绝不因熔断拦清仓**(本就是「只减」方向)。"""
    if body.sell_time and len(body.sell_time) == 8 and body.sell_time.isdigit():
        sell_date = datetime.strptime(body.sell_time, "%Y%m%d").date()
    else:
        sell_date = date.today()
    ok = pos_store.close_position(
        position_id, sell_price=body.sell_price, sell_date=sell_date,
        close_reason=body.closeReason, sell_fees=body.sellFees, db_path=_db(),
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"ok": False, "reason": "not_holding"},
        )
    # 熔断评估折进清仓路径(尽力而为,失败绝不阻断清仓成功响应)。
    try:
        episode = circuit_store.evaluate_after_close(sell_date, db_path=_db())
        if episode is not None:
            notify.push_circuit_breaker(episode, db_path=_db())
    except Exception:  # noqa: BLE001  熔断评估/推送异常绝不能掀翻清仓主流程(F.3)
        logger.warning("熔断评估/推送异常(已吞,不阻断清仓)", exc_info=True)
    return OkOut(ok=True)


# —— v1.2-A2 熔断纪律状态 + 解锁(§2.1 第 7 条,🔴)——————————————————————————————
# **熔断是纯提醒层**(§3.8):本节端点只读锁定态 / 记录用户解锁 ack,**绝不代下单/
# 撤单、绝不拦 `POST /positions`**(客户端「开新仓」入口自律灰化)。解锁本就是用户
# 动作(读强制复盘材料后确认),故可走 API(与「大脑激活绝不暴露给客户端」不同)。

@app.get(f"{API_PREFIX}/circuit", dependencies=[Depends(require_token)])
def get_circuit() -> CircuitStateOut:
    """权威熔断锁定态(plan A2.8;客户端今日计划面横幅/「开新仓」灰化据此)。"""
    return _shape_circuit(circuit_store.get_state(db_path=_db()))


@app.post(f"{API_PREFIX}/circuit/unlock", dependencies=[Depends(require_token)])
def unlock_circuit() -> OkOut:
    """客户端「熔断复盘」按钮解锁(plan A2.7 主路径,`unlocked_via='review_ack'`)。
    诚实:系统不能验证用户「真的复盘了」,但强制把材料摆到面前 + 记录 ack(客户端
    先展示确定性材料再调本端点)。无锁定态时幂等成功(已是解锁态)。"""
    circuit_store.unlock(via=circuit_store.UNLOCK_VIA_REVIEW_ACK, db_path=_db())
    return OkOut(ok=True)


# —— v1.2-B 预注册决策日志(§2.1 第 3 条 / plan §五 v1.2-B)——————————————————
# **审计件、非下单件**(§3.8):本节端点全部只做「装配 + 出入参映射」,领域读写
# 全部委托 `neckline.decision_log`(唯一写入通道,同 watchlist/positions 姿势),
# 端点本身无任何下单 / 撤单 / 拉行情副作用。**八项落库后不可编辑**——本节没有
# 任何「改八项内容」的端点;唯一的修改路径是 `revise`(新增修订行)与
# `scenario-outcome`(只翻情景树 `matched`)。

def _shape_decision(row: "decision_log_store.DecisionRow") -> DecisionOut:
    """决策日志领域行 → 客户端契约。同 `_shape_candidate` 的透传惯例。"""
    return DecisionOut(
        id=row.id, code=row.ts_code, name=row.name or row.ts_code, createdAt=row.created_at,
        whyBuy=row.why_buy, whyEntryPrice=row.why_entry_price,
        targetPrice=row.target_price, exitLow=row.exit_low, exitHigh=row.exit_high,
        thesisTags=list(row.thesis_tags), invalidation=row.invalidation,
        contingencyScenarios=[
            ContingencyScenarioOut(
                scenario=s.get("scenario", ""), trigger=s.get("trigger", ""),
                action=s.get("action", ""), matched=bool(s.get("matched", False)),
            )
            for s in row.contingency_scenarios
        ],
        playbookTag=row.playbook_tag, plannedPrice=row.planned_price, plannedQty=row.planned_qty,
        maxChasePct=row.max_chase_pct,
        status=row.status, positionId=row.position_id, revisionOf=row.revision_of,
    )


# v1.4-⑤-B(需求 2 补充)决策日志第⑨项「最高追价上限」400 reason(**客户端 `mapReason`
# 必须加 case**,守项目 CLAUDE.md「404/reason 映射」坑)。
REASON_MAX_CHASE_REQUIRED = "max_chase_required"


def _extract_max_chase_pct_or_400(body: "DecisionCreateIn | DecisionReviseIn") -> Optional[float]:
    """`maxChasePct`(⑨)必须是**主动选择**——要么填数字,要么显式传 `null`(=不设
    上限)。**省略该 JSON 键**(客户端表单没让用户做选择/老写法)→ 400
    `reason=max_chase_required`;显式传 `null` 合法,原样放行。

    **用 `model_fields_set` 区分"缺键" vs "显式 null"**:pydantic v2 的
    `BaseModel.model_fields_set` 是请求体里**实际出现过**的字段名集合——`maxChasePct`
    字段本身声明了默认值 `None`(供 Python 直调方/旧版契约友好),若只看
    `body.maxChasePct is None` 无法分辨"用户主动选了不设上限"与"这个键压根没出现在
    请求体里",必须查 `model_fields_set`(FastAPI 用请求体 JSON 构造该模型时会如实
    记录哪些键被传入,与字段默认值机制完全独立)。"""
    if "maxChasePct" not in body.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "reason": REASON_MAX_CHASE_REQUIRED},
        )
    return body.maxChasePct


@app.post(f"{API_PREFIX}/decisions", dependencies=[Depends(require_token)])
def create_decision(body: DecisionCreateIn) -> DecisionOut:
    """预注册决策日志(九项,plan B.1/B.2 + v1.4-⑤-B)。**`created_at` 服务端生成,
    忽略客户端任何时间戳入参**——`DecisionCreateIn` 本就无 createdAt 字段,请求体里
    同名字段(若有)会被 pydantic 直接丢弃,不会传导到 `decision_log_store.create_decision`
    (该函数签名同样无此形参)。**`maxChasePct`(⑨)必须显式传**(填数字或显式
    `null`),缺键 → 400,见 `_extract_max_chase_pct_or_400`。"""
    max_chase_pct = _extract_max_chase_pct_or_400(body)
    row = decision_log_store.create_decision(
        ts_code=body.code, name=body.name, why_buy=body.whyBuy, why_entry_price=body.whyEntryPrice,
        target_price=body.targetPrice, exit_low=body.exitLow, exit_high=body.exitHigh,
        thesis_tags=list(body.thesisTags), invalidation=body.invalidation,
        contingency_scenarios=[s.model_dump() for s in body.contingencyScenarios],
        playbook_tag=body.playbookTag, planned_price=body.plannedPrice, planned_qty=body.plannedQty,
        max_chase_pct=max_chase_pct,
        db_path=_db(),
    )
    return _shape_decision(row)


@app.get(f"{API_PREFIX}/decisions", dependencies=[Depends(require_token)])
def list_decisions(
    status: str = "", code: str = "",
    from_: str = Query(default="", alias="from"), to: str = "",
    position_id: int = 0,
) -> DecisionsListOut:
    """客户端历史 + macOS 归因表(plan B.2)。默认返全部,可按 `status`/`code`/
    `from`/`to`(created_at 日期区间,'YYYYMMDD')/`position_id`(v1.3-②-D 情景树每日对照,
    挑出该持仓关联决策)过滤。`position_id=0`(缺省)= 不按持仓过滤(向后兼容旧调用)。"""
    rows = decision_log_store.list_decisions(
        status=(status or None), ts_code=(code or None),
        date_from=(from_ or None), date_to=(to or None),
        position_id=(position_id or None), db_path=_db(),
    )
    return DecisionsListOut(items=[_shape_decision(r) for r in rows])


@app.get(f"{API_PREFIX}/decisions/{{decision_id}}/track", dependencies=[Depends(require_token)])
def decision_track(decision_id: int) -> DecisionTrackOut:
    """挂单未成交追踪出口(plan §五 v1.4-⑦-A,P3-12)。领域数据自 v1.3-④ 起已在攒
    (`report/pending_track.py::track_pending_decisions`,16:35 报告管线收尾调用),
    本端点只是首次把它接上 API——**同码不重写**,读专用 `load_track_rows`,不重算。

    **404 只有一种情形**:`decision_id` 本身不存在 → `reason="not_found"`。**决策
    存在但还没攒到任何追踪快照**(刚创建、未到下一交易日,或已 filled/cancelled
    从而从没进过追踪窗口)不是 404——如实返回 `rows=[]` 的空态,「没攒到数据」与
    「没这条决策」必须能分开(§3.8)。"""
    row = decision_log_store.get_decision(decision_id, db_path=_db())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    from neckline.report.pending_track import load_track_rows

    tracks = load_track_rows(decision_id, db_path=_db())
    return DecisionTrackOut(
        status=row.status,
        planPrice=row.planned_price,
        rows=[
            DecisionTrackRowOut(
                tradeDate=t["tradeDate"], dOffset=t["dOffset"],
                close=t["close"], retFromPlan=t["retFromPlan"],
            )
            for t in tracks
        ],
    )


@app.post(f"{API_PREFIX}/decisions/{{decision_id}}/link", dependencies=[Depends(require_token)])
def link_decision(decision_id: int, body: DecisionLinkIn) -> OkOut:
    """成交后一键关联(plan B.2):`status` 置 filled + `position_id` 回填。"""
    ok = decision_log_store.link_decision(decision_id, body.positionId, db_path=_db())
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/decisions/{{decision_id}}/cancel", dependencies=[Depends(require_token)])
def cancel_decision(decision_id: int) -> OkOut:
    """用户放弃该预注册计划(plan B.2):`status` 置 cancelled。"""
    ok = decision_log_store.cancel_decision(decision_id, db_path=_db())
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/decisions/{{decision_id}}/revise", dependencies=[Depends(require_token)])
def revise_decision(decision_id: int, body: DecisionReviseIn) -> DecisionOut:
    """新增一行修订(plan B.2「改动只新增修订行,不改旧行」,v1.4-⑤-B 起九项全量
    重录)。旧行(`decision_id`)原地不变;新行 `revisionOf` 指向**链根** id(见
    `decision_log.revise_decision` 文档)。修订等于重新预注册一整套九项内容,同一份
    「`maxChasePct` 必须显式传」纪律适用(缺键 → 400,同 `create_decision`)。
    `decision_id` 不存在 → 404。"""
    max_chase_pct = _extract_max_chase_pct_or_400(body)
    row = decision_log_store.revise_decision(
        decision_id, why_buy=body.whyBuy, why_entry_price=body.whyEntryPrice,
        target_price=body.targetPrice, exit_low=body.exitLow, exit_high=body.exitHigh,
        thesis_tags=list(body.thesisTags), invalidation=body.invalidation,
        contingency_scenarios=[s.model_dump() for s in body.contingencyScenarios],
        playbook_tag=body.playbookTag, planned_price=body.plannedPrice, planned_qty=body.plannedQty,
        max_chase_pct=max_chase_pct,
        db_path=_db(),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return _shape_decision(row)


@app.post(f"{API_PREFIX}/decisions/{{decision_id}}/scenario-outcome", dependencies=[Depends(require_token)])
def scenario_outcome(decision_id: int, body: ScenarioOutcomeIn) -> OkOut:
    """情景树⑦结果标记专用端点(plan B.2)——**只翻 `matched`,绝不改
    `scenario`/`trigger`/`action`**(不可编辑口径的唯一例外落点)。`decision_id`
    不存在 → 404;`outcomes` 任一 `index` 越界 → 422。"""
    try:
        ok = decision_log_store.set_scenario_outcomes(
            decision_id, [o.model_dump() for o in body.outcomes], db_path=_db(),
        )
    except decision_log_store.ScenarioIndexError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"ok": False, "reason": "scenario_index_out_of_range", "message": str(e)},
        )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return OkOut(ok=True)


# —— v1.2-G 呼吸试验仓台账(§2.1 第 3 条仓位分配 / plan §五 v1.2-G)——————————————
# 底仓是普通 `positions` 行(不改其语义、本节端点绝不写 `positions`);T 仓走独立子表
# `breathing_t_trades`,领域读写 + 派生计算全部委托 `neckline.breathing`(同 decisions
# 一节「装配 + 出入参映射」姿势)。现价走既有 `_resolve_prices`(同 `list_positions`
# 的取价路径,不新拉数据源);`baseCostAdj`/`edgeToPrice` 算不出时下发 null,不崩。

def _shape_breathing_trade(t: "breathing_store.BreathingTrade") -> BreathingTradeOut:
    """T 子账领域行 → 客户端契约。同 `_shape_candidate` 的透传惯例;`tPnl` 取自领域
    层的 `t_pnl` 派生属性(单一公式源 `breathing.compute_t_pnl`)。"""
    return BreathingTradeOut(
        id=t.id, positionId=t.position_id, buyPrice=t.buy_price, sellPrice=t.sell_price,
        qty=t.qty, fees=t.fees, tDate=t.t_date, tPnl=round(t.t_pnl, 2), note=t.note or "",
    )


@app.get(f"{API_PREFIX}/breathing/{{position_id}}/trades", dependencies=[Depends(require_token)])
def list_breathing_trades(position_id: int) -> BreathingTradesOut:
    """T 子账列表 + 底仓摊薄成本 / 先手距离派生(plan G.4)。底仓(`positions` 行)
    不存在 → 404(算不出摊薄成本,同其它「引用对象不存在」端点一致的 404 语义)。"""
    pos = pos_store.get_position(position_id, db_path=_db())
    if pos is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    trades = breathing_store.list_trades(position_id, db_path=_db())
    base_cost_adj = breathing_store.compute_base_cost_adj(pos.buy_price, pos.qty, trades)
    price = _resolve_prices([pos.ts_code]).get(pos.ts_code)
    edge = breathing_store.compute_edge_to_price(base_cost_adj, price)
    return BreathingTradesOut(
        items=[_shape_breathing_trade(t) for t in trades],
        baseCostAdj=(round(base_cost_adj, 4) if base_cost_adj is not None else None),
        edgeToPrice=(round(edge, 4) if edge is not None else None),
    )


@app.post(f"{API_PREFIX}/breathing/{{position_id}}/trades", dependencies=[Depends(require_token)])
def post_breathing_trade(position_id: int, body: BreathingTradeIn) -> BreathingTradeOut:
    """录入一次 T(plan G.4)。`fees` 客户端给多少落多少,不猜、不按费率估算(G.2)。
    底仓不存在 → 404(不建孤儿 T 子账行)。"""
    t_date = None
    if body.tDate and len(body.tDate) == 8 and body.tDate.isdigit():
        t_date = datetime.strptime(body.tDate, "%Y%m%d").date()
    trade = breathing_store.add_trade(
        position_id, buy_price=body.buyPrice, sell_price=body.sellPrice, qty=body.qty,
        fees=body.fees, t_date=t_date, note=body.note, db_path=_db(),
    )
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return _shape_breathing_trade(trade)


@app.delete(f"{API_PREFIX}/breathing/trades/{{trade_id}}", dependencies=[Depends(require_token)])
def delete_breathing_trade(trade_id: int) -> OkOut:
    """误录可删(plan G.4)。不存在 → 404。"""
    ok = breathing_store.delete_trade(trade_id, db_path=_db())
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
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

def _inquiry_basis_date() -> date:
    """确定性材料的 EOD 基准日——最近一份已生成报告的交易日(最可靠的"有数据日");
    无报告 → 日历默认(今日交易日则今日,否则上一交易日)。

    **v1.3.3:原 `_inquiry_basis_pool_date()` 返回的第二个值 `pool_date` 已删除**——
    问询台不再自动写 `inquiry_pool`(「初审通过进海选池」退役,改由用户在客户端一键加
    自选)。`inquiry_pool` 表与报告侧消费逻辑保留不动(向后兼容,空池 noop),只是不再
    有自动写入方,故这里也不再需要算"入池当日"。"""
    lr = report_store.latest_report_date(db_path=_db())
    if lr:
        return datetime.strptime(lr, "%Y%m%d").date()
    today0 = date.today()
    return today0 if is_trading_day(today0) else prev_trading_day(today0)


@app.post(f"{API_PREFIX}/inquiry", dependencies=[Depends(require_token)])
def inquiry(body: InquiryIn) -> InquiryOut:
    """问询台(§2.5,**v1.3.3 = 自由分析师**):确定性材料(同码评分 + 硬线提示 + 板块年龄
    + K4 安检)→ LLM 自由叙述回答用户实际问的问题。**不再有裁决、不再有拦截**——任何票
    都给实质回答,纪律命中项降级为回答里的警告标注。软护栏「不下买卖指令」只在 prompt 层
    (刻意不做枚举强校验/输出后处理);真正的护栏是 §3.8「系统永不下单」。缺 key → 确定性
    材料照给、LLM 段占位降级,不崩。**v1.4-⑦-B**:`run_inquiry` 内部旁路把本次问答落进
    `inquiry_log`(失败不影响本次回答),`inquiryId` 原样透传(落库失败时为 `None`)。"""
    provider = (_PROVIDER_FN or (lambda dbp: get_provider(TASK_INQUIRY, db_path=dbp)))(_db())
    quotes_fn = _QUOTES_FN
    if quotes_fn is None:
        from neckline.sentinel.quotes import get_quotes
        quotes_fn = get_quotes
    result = run_inquiry(
        body.code,
        [{"role": m.role, "content": m.content} for m in body.messages],
        basis_date=_inquiry_basis_date(), db_path=_db(),
        provider=provider, quotes_fn=quotes_fn, panel_fn=_PANEL_FN,
    )
    return InquiryOut(
        ok=True, code=body.code, reply=result["reply"], verdict=result["verdict"],
        evidence=result["evidence"], degraded=result["degraded"],
        inquiryId=result.get("inquiryId"),
    )


@app.get(f"{API_PREFIX}/inquiries", dependencies=[Depends(require_token)])
def list_inquiries(limit: int = 20, offset: int = 0, tsCode: str = "") -> InquiryLogsListOut:
    """问询历史列表(plan §五 v1.4-⑦-B / §七 P3-13),按最近问询在前排序。**与
    `inquiry_pool`(已退役历史队列表)无关**——本端点读的是 `inquiry_log` 档案表。
    `limit`/`offset` 简单分页(无 `total`,同 `DecisionsListOut`/`WatchlistOut` 等既有
    列表端点惯例——列表页翻页读不到下一页空数组即知到底,不必服务端额外算总数)。"""
    items = list_inquiry_logs(
        limit=limit, offset=offset, ts_code=(tsCode or None), db_path=_db(),
    )
    return InquiryLogsListOut(items=[InquiryLogOut(**i) for i in items])


@app.get(f"{API_PREFIX}/inquiries/{{inquiry_id}}", dependencies=[Depends(require_token)])
def inquiry_detail(inquiry_id: int) -> InquiryLogOut:
    """问询记录详情(plan §五 v1.4-⑦-B)。不存在 → 404 `reason="not_found"`。"""
    row = get_inquiry_log(inquiry_id, db_path=_db())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "not_found"})
    return InquiryLogOut(**row)


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
        push=PushSettingsOut(
            report=st.push_report, retreatBrake=st.push_retreat,
            precall=st.push_precall, d5exit=st.push_d5exit, circuit=st.push_circuit,
            holdingAlert=st.push_holding_alert,
        ),
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
    """局部更新 Provider(🔴)。未出现的字段不改(`model_fields_set` 判据,同
    `_extract_max_chase_pct_or_400` 先例);`name` 不存在 → 404。`get_provider()`
    下次调用即现读 DB 生效(运行时,不重启)。"""
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
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_task", "message": str(e)})
    routes, default_provider = get_llm_routes(db_path=_db())
    return LLMRoutesOut(routes=routes, defaultProvider=default_provider)


@app.put(f"{API_PREFIX}/settings/push", dependencies=[Depends(require_token)])
def put_settings_push(body: SettingsPushIn) -> OkOut:
    """写 APNs 六类推送开关(v1.3-②:契约扩至六字段,第六 = K4 持仓派发警报
    `app_settings.push_holding_alert`,默认开;用户 2026-07-26 拍板独立开关)。
    六字段均必填(缺 → 422)。"""
    set_push(body.report, body.retreatBrake, body.precall, body.d5exit, body.circuit,
             body.holdingAlert, db_path=_db())
    return OkOut(ok=True)


@app.get(f"{API_PREFIX}/settings/intel-boards", dependencies=[Depends(require_token)])
def get_settings_intel_boards() -> IntelWatchBoardsOut:
    """读候选情报管线「五板块常驻」名单(plan v1.3-⑥ 后端补齐②)。存取本身早在
    v1.3-③-C3 就绪(`settings_store.get_intel_watch_boards`);本端点只补 HTTP 读路径。
    从未配置 → 默认五板块(`DEFAULT_INTEL_WATCH_BOARDS`);用户曾显式清空 → 空列表。"""
    return IntelWatchBoardsOut(boards=get_intel_watch_boards(db_path=_db()))


@app.put(f"{API_PREFIX}/settings/intel-boards", dependencies=[Depends(require_token)])
def put_settings_intel_boards(body: IntelWatchBoardsIn) -> IntelWatchBoardsOut:
    """写「五板块常驻」名单(plan v1.3-⑥ 后端补齐②)。**禁模糊匹配**——每个名字须能在
    `ths_index.name` 精确匹配到(同 `report.intel_candidates._resolve_watch_board_codes`
    的精确匹配口径,不重推一遍),匹配不到 → 422 + 明确 `reason`(`board_not_found`)与
    具体哪些名字没匹配到(`unresolved`),不静默接受用户会以为生效、实际情报管线跑起来
    还是精确匹配失败被诚实跳过(`_resolve_watch_board_codes` 的 `unresolved` 只落
    warning 日志,用户看不到——故端点层必须先行拦截,不能让写入端悄悄收一个错的名字)。
    允许空列表(显式清空常驻,与「从未配置」回退默认语义不同,见 `set_intel_watch_boards`)。
    返回写入后的最终名单(与 GET 同形状,便于客户端直接刷新展示)。"""
    from neckline.report.sectors import load_index_names

    if body.boards:
        valid_names = set(load_index_names(parquet_dir=_parquet_dir()).values())
        unresolved = [nm for nm in body.boards if nm not in valid_names]
        if unresolved:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "ok": False, "reason": "board_not_found", "unresolved": unresolved,
                    "message": f"以下板块名未能在 ths_index.name 精确匹配到(禁模糊匹配,请核对全名):{unresolved}",
                },
            )
    set_intel_watch_boards(body.boards, db_path=_db())
    return IntelWatchBoardsOut(boards=get_intel_watch_boards(db_path=_db()))


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

    # v1.2-A2 自动解锁(plan A2.7 自动路径):某触发行的 trigger_ref_date 落在一个走了
    # 强制复盘口径(forced_review=True,即 reconcile.is_forced_review 同源)的 ISO 周内 →
    # 该行自动解锁(unlocked_via='weekly_review')。尽力而为,失败不阻断周复盘响应。
    try:
        circuit_store.auto_unlock_for_reviews(reviews, db_path=_db())
    except Exception:  # noqa: BLE001
        logger.warning("周复盘熔断自动解锁异常(已吞,不阻断周复盘)", exc_info=True)

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
