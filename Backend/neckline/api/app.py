"""FastAPI 应用主体(plan 4A + 4B.3 单 unit 内哨兵 asyncio 任务)。

绑 127.0.0.1:8002(nginx 反代,与 LinoN 8001 共存)。`/api/v1/health` 免鉴权;其余端点
过 `require_token`。startup:fail-fast 校验 `API_TOKEN`(len>=16)+ `init_schema` + 起
哨兵后台轮询任务(§3.6「哨兵折进 FastAPI 单 unit 的 lifespan asyncio 任务」,不另起进程)。
shutdown:置位 stop_event,优雅停轮询。

**同码不重写**:报告 / 看板 / 持仓的领域逻辑全部复用现有模块,端点只做「装配 +
出入参映射 + 鉴权」。

**测试注入(沿 LinoN 模块级替身姿势)**:`ENABLE_SENTINEL`(关后台轮询)、`_DB_PATH_OVERRIDE`
(隔离库)、`_QUOTES_FN`(免联网)。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from contextlib import asynccontextmanager
# `date_cls` 别名:多个端点用 `date: str = ""` 作查询参数名(客户端契约),
# 函数内会把模块级的 `date` 类型遮住 —— 别名让「今天」这种取值仍拿得到。
from datetime import date, datetime, time
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status

from neckline import decision_log as decision_log_store
from neckline import user_actions
from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.schemas import (
    AuctionDataStatusOut,
    AuctionIndexGapOut,
    AuctionMarketOverviewOut,
    AuctionMemberRowOut,
    AuctionOut,
    AuctionQualityDetailOut,
    AuctionQuoteCheckOut,
    AuctionRiskOut,
    AuctionVerdictOut,
    BoardEventOut,
    BoardOut,
    AlertConditionIn,
    AlertCreateIn,
    AlertParseIn,
    AlertParseOut,
    AlertsListOut,
    AlertUpdateIn,
    BasketCardOut,
    BasketDailyOut,
    BasketOut,
    BasketReviewOut,
    BasketsListOut,
    BasketVerificationOut,
    ConfirmationCardOut,
    ContingencyScenarioOut,
    CustomAlertOut,
    DecisionCreateIn,
    DecisionNoteOut,
    DecisionOut,
    DecisionsListOut,
    DecisionTrackOut,
    DecisionTrackRowOut,
    DeviceRegisterIn,
    EntrySuggestionOut,
    InfoCardNewsItemOut,
    InfoCardNewsOut,
    InfoCardOut,
    InfoCardSnapshotOut,
    InfoCardTopListOut,
    EntrySnapshotOut,
    EvalWeeklyOut,
    K4AdvisoryOut,
    NewsAlertOut,
    NewsAlertScanStatusOut,
    LLMRoutesIn,
    LLMRoutesOut,
    TavilySettingsIn,
    TavilySettingsOut,
    MarketRegimeDayOut,
    MarketRegimeOut,
    OkOut,
    PackOut,
    PacksListOut,
    PortfolioAlertOut,
    PositionAlertOut,
    PositionCloseIn,
    PositionOpenIn,
    PositionOpenOut,
    PositionOut,
    PositionPlanCreateIn,
    PositionPlanOut,
    PositionPlansOut,
    PositionsOut,
    ProfileOut,
    ProviderCreateIn,
    ProviderOut,
    ProviderUpdateIn,
    ProvidersListOut,
    PushKindOut,
    PushSettingsOut,
    ReportOut,
    RetreatBrakeOut,
    ReviewGetOut,
    ReviewHandoffOut,
    ReviewOverviewOut,
    ReviewSegmentOut,
    ReviewUploadOut,
    ScoreContribOut,
    SelectionClockOut,
    SelectionClocksOut,
    SettingsOut,
    SettingsProviderOut,
    SettingsPushIn,
    SettingsReviewColMapIn,
    TierOut,
    TradeClockEventOut,
    TradeClockNoteIn,
    TradeClockNoteOut,
    TradeClockOut,
    WeeklyReviewOut,
)
from neckline.api.stores import upsert_device
from neckline.calendar import CN_TZ, is_trading_day
from neckline.config import ensure_data_dirs
from neckline.llm.factory import get_provider
from neckline.report import store as report_store
from neckline import custom_alerts, notify_kinds
from neckline import dedup
from neckline.sentinel import positions as pos_store
from neckline.sentinel.intraday import is_intraday_now
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
# V2.0.0(⑮,2026-08-03):契约换血 + 客户端双端改版同批到位。⚠ **三方同一次提交动**
# ——`App/project.yml` 与 `pbxproj` 的 `MARKETING_VERSION` 必须同为 `2.0.0`
# (守门单测 `tests/test_client_version_governance.py` 锁三处恒等,漏一处立刻红)。
# ⚠ ⑭ 刻意没升它:提前升会让守门单测常年红,版号归 ⑮。
# V2.4.0(P4.4,2026-08-12):可信度与减法版(P0 退役盘中通用证伪 + 代理池退潮刹车 /
# P1 选股关口与 Tier 语义 / P2 竞价数据可靠性 / P3 持仓语义与前端三层收敛 / P4 发布治理)。
# **三处同一次提交动**:本行 + `App/project.yml` **两处** `MARKETING_VERSION`
# (顶层 base + app target,刻意重复;守门只比 app target,故两处都得手动改)+
# `xcodegen generate` 重生 pbxproj。⚠ 改完必须跑一次 `xcodegen generate` ——
# 它顺手修好 project 级漂移,而守门看不见那一处。
# V2.4.2 RC:版本只能通过 `App/scripts/prepare_release_candidate.sh` 与客户端一起切换。
# ⚠ 本行改动**不构成部署**:生产 `/health` 要到真正 rsync + 重启之后才返此版本。
VERSION = "v2.5.0"
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
# V2.1-⑤:`GET /review/{overview,handoff}` 要读**离线落盘**的周度校准产物
# (`data/reports/calibration/`)——这是 app.py 端点层首次直接读 `data_dir` 下的
# 文件产物。同 `_PARQUET_DIR_OVERRIDE` 姿势新增注入点:CLAUDE.md「测试隔离」条明载
# `api_env` **不重写** `neckline.config.settings`,不给注入点就会读到真实项目的
# `data/reports/`(而那正是"一测就踩、断言全错还不报错"的那类泄漏)。
_DATA_DIR_OVERRIDE: Optional[Path] = None     # 隔离 data 根(None → settings.data_dir)
_QUOTES_FN: Optional[Callable[[List[str]], Dict[str, Any]]] = None  # 实时拉价(None → data.realtime)

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


def _calibration_dir() -> Optional[Path]:
    """周度校准产物目录(`<data>/reports/calibration`)。`None` = 用
    `review/handoff.py::calibration_dir()` 自己的缺省(真实 `settings.data_dir`)。"""
    return None if _DATA_DIR_OVERRIDE is None else (_DATA_DIR_OVERRIDE / "reports" / "calibration")


# —— 哨兵后台轮询(4B.3;折进 lifespan asyncio,单 unit 省内存)——————————————————

def _is_preopen(now: datetime) -> bool:
    """开盘前收紧轮询窗口:交易日 且 09:20 ≤ now.time() < 09:30。`is_intraday_now` 对该段
    返 False(盘中从 9:30 起),两窗口不重叠,故用 `elif` 串接安全。"""
    return is_trading_day(now.date()) and _PREOPEN_START <= now.time() < _PREOPEN_END


async def _sentinel_loop(stop_event: asyncio.Event) -> None:
    """交易时段每 60s 调 `run_tick`(阻塞活 run in thread,不卡事件循环)。
    ⛔ **V2.4.0 P0:原「退潮首次触发 → APNs 刹车推送」一路已删**(退潮判级退役)。
    **v1.1-A**:开盘前 9:20–9:30 收紧到 30s 一探并跑
    `run_precall_tick`(盘前校准 + D5 扫描,当日只跑一次,内部自防重),9:26 汇总 / D5 推送
    经 `notify` 白名单入口。**现有 9:35 起 intraday 判逻辑一字不改**。非交易时段优雅待机
    (每 5min 探一次,不空转)。

    **V2-⑧-B 挂了两条旁路分支**(存拍,各自独立 try/except,**不改任何轮询节奏、
    不影响四哨兵与盘前校准的成败**):09:25–09:30 竞价快照;15:05–15:35 当日存拍一次性
    落盘 + 记 `capture_status`。盘中每一拍的分钟报价累计在 `run_tick` 内部完成(用的就是
    那一拍已经拉到的行情,零额外网络)。

    **V2.3.3-④ 第三条盘前旁路**:09:26–09:29 **D1 集合竞价确认层**
    (`neckline/auction/`,K8.md §二十)—— 同样独立 try/except、当日一次、内部自防重。
    ⚠ 它**会调 LLM**,故有 **9:29 硬截止**(`auction/pipeline.py`):最迟 9:29 返回,
    9:30 的 intraday 第一拍不受影响。⛔ **不新增 systemd unit**(跑在本常驻进程里)。"""
    from neckline.auction import pipeline as auction_pipeline
    from neckline.sentinel import capture
    from neckline.sentinel.engine import run_tick
    from neckline.sentinel.precall import run_precall_tick

    logger.info("哨兵后台轮询已挂载(单 unit lifespan asyncio;含 v1.1 盘前校准分支)")
    while not stop_event.is_set():
        now = datetime.now()
        interval = _SENTINEL_IDLE_POLL_SEC
        if is_intraday_now(now):
            try:
                # ⛔ V2.4.0 P0:原「退潮首次触发 → `push_retreat_brake` APNs 刹车推送」
                # 那两行**已删除** —— `run_tick` 不再判退潮,`TickResult` 也不再有
                # `retreat_alert` 这个位。⛔ 不许以任何形式接回来。
                await asyncio.to_thread(run_tick, now, db_path=_db())
            except Exception:  # noqa: BLE001  单拍异常绝不能拖垮轮询
                logger.warning("哨兵一拍异常(已吞,继续轮询)", exc_info=True)
            interval = _SENTINEL_LUNCH_POLL_SEC if time(11, 30) <= now.time() < time(13, 0) else _SENTINEL_POLL_SEC
        elif _is_preopen(now):
            try:
                pr = await asyncio.to_thread(run_precall_tick, now, db_path=_db())
                if pr.ran:
                    # 汇总推送门槛 = `should_push_summary`(单一源在 PrecallResult)= 有需要
                    # 动作的判定。⚠ V2.2-⑤-B:原「或熔断锁定中」的必发豁免已随熔断退役取消。
                    if pr.should_push_summary:
                        await asyncio.to_thread(
                            notify.push_precall_summary, pr.counts, db_path=_db(),
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
            # V2.3.3-④ 旁路:9:26 **D1 集合竞价确认层**(当日一次,内部自防重)。
            # **独立 try**,与盘前校准 / 竞价存拍的成败互不影响;**9:29 硬截止**保证
            # 最迟 9:29 返回 → 9:30 的 intraday 第一拍不受影响。
            # ⚠ 顺序:precall(9:25:30)→ capture(9:25–9:30)→ auction(9:26–9:29);
            # preopen 轮询 30s 一探,9:26:00 那一拍进本分支,前两者各自 dedup 跳过、零重复。
            if auction_pipeline.is_auction_window(now):
                try:
                    ar = await asyncio.to_thread(
                        auction_pipeline.run_auction_pipeline, now,
                        db_path=_db(), parquet_dir=_parquet_dir(),
                    )
                    # 门槛单一源 = `AuctionRunResult.should_push`(veto / 命中失效位 /
                    # LLM 非 ok)。⛔ 不许"平静的早晨也发一条"。
                    if ar.ran and ar.should_push:
                        await asyncio.to_thread(
                            notify.push_auction_summary, ar.counts, db_path=_db(),
                        )
                except Exception:  # noqa: BLE001
                    logger.warning("竞价确认层异常(已吞,竞价层是旁路)", exc_info=True)
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

def _selection_run_overlay(trade_date: date_cls) -> Dict[str, str]:
    """读取 V2.4.2 选股运行态的 A 类覆盖层。

    报告快照是 B 类冻结事实；正在运行、部分完成或不可用都不能回写它。核心
    选择管线尚未安装（例如旧数据库或回放旧源码）时，宁可不发这两个可选字段，
    也不把未知状态编成 ``complete``。
    """
    try:
        from neckline.selection.run_store import latest_publication_state

        raw = latest_publication_state(trade_date.strftime("%Y%m%d"), db_path=_db())
    except ImportError:
        return {}
    except Exception:  # noqa: BLE001 - 状态覆盖不得让已发布快照无法读取
        logger.warning("[report] 读取选股运行态覆盖失败，按无覆盖返回", exc_info=True)
        return {}
    if not isinstance(raw, Mapping):
        return {}
    state = raw.get("selectionState")
    text = raw.get("selectionStateText")
    if not isinstance(state, str) or not state:
        return {}
    out = {"selectionState": state}
    if isinstance(text, str) and text:
        out["selectionStateText"] = text
    return out


def _shape_basket_daily(payload, *, trade_date: Optional[date_cls] = None) -> BasketDailyOut:
    """报告落库的篮子日报快照 → 客户端契约。**同码不重写**:快照已是 camelCase
    (`report/basket_daily.py::BasketDaily.to_public_dict()`,与 `intel`/`sectorMoneyflow`
    的透传惯例一致),这里只做 pydantic 收口。

    老报告(建于 `basket_daily_json` 列之前)→ `basket_daily_from_snapshot` 给一份
    三段全 `available=false` 的诚实占位,⛔ 不冒充「那天没有篮子」。"""
    from neckline.report.basket_daily import basket_daily_from_snapshot

    shaped = basket_daily_from_snapshot(payload)
    # 只在读 API 时叠一层运行态；不修改 `payload`，更不修改 reports 表里的冻结 JSON。
    if trade_date is not None:
        shaped.update(_selection_run_overlay(trade_date))
    return BasketDailyOut(**shaped)


def _shape_news_alert(a: Dict[str, Any], names: Dict[str, str]) -> NewsAlertOut:
    """`news_alerts` 表行 → 客户端契约(v1.3-③-C4)。表不存 `name`(同
    `llm_judgments` 惯例),这里从 `stock_basic` 解析补上展示便利字段。"""
    code = a.get("ts_code", "")
    return NewsAlertOut(
        code=code, name=names.get(code, code),
        category=a.get("category", ""), summary=a.get("summary", ""), source=a.get("source", ""),
    )


def _shape_report(rep: Dict[str, Any]) -> ReportOut:
    from neckline.data.market_data import resolve_stock_names
    from neckline.report.news_alerts_store import load_news_alerts
    from neckline.report.pipeline import compute_missed_entry_hint

    td = rep["trade_date"]
    d = datetime.strptime(td, "%Y%m%d").date()
    # v1.3-③-C4:命中告警条目独立表实时查(同 llm_judgments 的「live join」惯例,
    # 不像 intel 那样整段嵌 JSON——见 news_alerts.py 模块头设计说明)。
    alert_rows = load_news_alerts(d, db_path=_db())
    alert_names = resolve_stock_names(list({r["ts_code"] for r in alert_rows}), _db()) if alert_rows else {}
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
        reportDate=rep.get("report_date") or td,
        tradeDate=td,
        generatedAt=rep.get("generated_at", ""),
        strategyVersion=rep.get("strategy_version", ""),
        sentiment=rep.get("sentiment", {}),
        sectors=rep.get("sectors", []),
        # V2-⑭-B:篮子日报三段取代已退役的 `candidates`(透传落库快照,随报告冻住)。
        basketDaily=_shape_basket_daily(rep.get("basket_daily"), trade_date=d),
        missedEntryHint=compute_missed_entry_hint(d, db_path=_db()),   # v1.1-B.4 实时算(补录后自动消失)
        intel=rep.get("intel", {}),                       # v1.3-③-C1,透传落库快照(同 sentiment 惯例)
        sectorMoneyflow=rep.get("sector_moneyflow", {}),   # v1.3-③-C2,透传落库快照
        newsAlerts=news_alerts,                            # v1.3-③-C4,独立表实时查
        newsAlertsScan=news_alerts_scan,                    # v1.3-③-C4,透传落库快照(同 intel 惯例)
        dataFreshness=rep.get("data_freshness", {}),        # v1.4-①-C,透传落库快照(不读时重算)
    )


def _empty_report(reason: str) -> ReportOut:
    return ReportOut(
        reportDate="", tradeDate="", generatedAt="", strategyVersion="",
        sentiment={}, sectors=[], degraded=True, reason=reason,
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
    """单只完整信息卡(plan §五 v1.4-④-B;**V2-⑬-N 保留改造**为篮子成员详情页地基)。
    **服务端现算,不落库**——K 线/RS 线/行业分歧线/快照/消息面/龙虎榜/市场语境全部
    独立现读 parquet/DB。

    ⚠ **V2-⑬-N:数据来源由「候选快照」换成「篮子成员」**。V1 从当日报告的
    `candidates_json` 里找这只票(候选榜已随 ⑬-1 删除,那条路恒空);V2 改为在
    **D0 冻结的篮子成员**里找,并新增三块(所属篮子与共同驱动 / 本票角色含对拍分歧 /
    与同篮其他成员的对比,见 `info_card.build_basket_context`)+ ⑬-N-K7 成员标注件
    展示区(读 `selection/member_tags.py` 同一份实现)。

    `k4_flags`(§硬要求「复用③已算好的 k4_flags,不重算」)V2 取自**卡里冻结的成员节**
    `members[].k4_tag`;卡没生成 / 该票没标 → 空列表(**如实空**,不现算补齐)。

    404 两个 reason(客户端 `mapReason` 须各有 case,守项目 CLAUDE.md 404 映射坑):
    `report_not_found`(日期格式非法 / 该日从未生成过报告)、`code_not_in_report`
    (该日报告存在,但这只票当日既不在任何篮子里、也不在历史候选快照里)。
    ⚠ **`code_not_in_report` 这个字符串刻意复用不改** —— 客户端 `mapReason` 已有
    对应 case,复用已有 reason 不需要新 case(CLAUDE.md 明文)。"""
    from neckline.report.info_card import build_basket_context, build_info_card
    from neckline.review.parse import normalize_ts_code

    rep = report_store.load_report_by_str(date, db_path=_db()) if (len(date) == 8 and date.isdigit()) else None
    if rep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={"ok": False, "reason": "report_not_found"})
    ts_code = normalize_ts_code(code)
    trade_date = datetime.strptime(rep["trade_date"], "%Y%m%d").date()

    basket_ctx = build_basket_context(ts_code, trade_date, db_path=_db())
    name = ts_code
    k4_flags: List[str] = []
    if basket_ctx.available:
        name = _resolve_names([ts_code]).get(ts_code) or ts_code
        # 卡里冻结的 K4 标(⑤ 成员卫生线打的 `avoid_flag` 标),不重算。
        k4_flags = [t for t in [_member_k4_tag(basket_ctx, ts_code)] if t]
    else:
        # 历史报告(⑬-1 之前生成、`candidates_json` 里还有真候选)仍可看信息卡 ——
        # 这是**读历史**的合法路径,不是把候选榜接回来。
        cand = next((c for c in rep.get("candidates", []) if c.get("ts_code") == ts_code), None)
        if cand is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                                detail={"ok": False, "reason": "code_not_in_report"})
        name = cand.get("name") or ts_code
        k4_flags = cand.get("k4_flags", []) or []

    card = build_info_card(
        trade_date, ts_code, k4_flags=k4_flags, name=name,
        parquet_dir=_parquet_dir(), db_path=_db(),
    )
    return InfoCardOut(**card.to_public_dict())


def _member_k4_tag(ctx, ts_code: str) -> Optional[str]:
    """从已装配好的篮子上下文里取这只票冻结的 K4 标(没有 → None,**不现算**)。"""
    from neckline.selection.basket_store import load_basket_card

    if not ctx.available or ctx.basket_id is None:
        return None
    try:
        row = load_basket_card(ctx.basket_id, db_path=_db())
    except Exception:  # noqa: BLE001
        return None
    for m in ((row or {}).get("card") or {}).get("members") or []:
        if m.get("ts_code") == ts_code:
            return m.get("k4_tag")
    return None


# —— V2-⑭-B 篮子端点(⑤⑥⑦⑧⑨ 的产出上 API 面)————————————————————————————
#
# **两个 404 reason 必须分得开(⑭-B 定死,⛔ 不许合并)**:
#   · `basket_not_found` —— 这个篮子本身不存在(系统丢了篮子)。
#   · `card_not_ready`   —— **篮子在,卡还没生成**(⑦ 的事务 2 独立于事务 1,合法中间态)。
# 合并成一个就把「没有」和「没看」混了。`card_not_ready` 是**全新字符串**,客户端
# `APIClient.mapReason` **必须加新 case**(文案方向「本篮的卡还没生成」,⛔ 不是「篮子
# 不存在」)—— 404 的 fallback 是 `.notHolding`「持仓已清」,不加 case 就会显示成那句
# 驴唇不对马嘴的话(v1.4 `watchlist` 的 `not_found` 已经这么踩过一次)。

# **第三态 `card_corrupt` = 500 + reason(2026-08-04 planner 裁定,小审 🔵 B-3)**:
#   · 判据分界:`basket_cards` **有行但读不出**(`card_json` 解不出 / 顶层必需键缺失)
#     → `card_corrupt`;**根本没有行** → 仍是 `card_not_ready`。
#   · **为什么给它 500 而不是塞进 404 家族**(⛔ 别按"体例一致"改回去):① 404 是在
#     说谎,卡**存在**、只是读不出;② 它会与 `card_not_ready` 撞成同一类,而两者要求
#     的反应完全相反(等 ⑦ 补 version=1 vs 需人排查);③ **决定性一条**:卡是冻结件、
#     `INSERT OR IGNORE` 永不覆盖 → 坏了就是永久坏的,客户端当 `card_not_ready` 处理
#     就会永远重试、界面永远显示「卡还没生成」而那张卡这辈子不会来 = **静默永久失败**;
#     ④ 放 500 这一类,连只看状态码的采集器也不会误当良性态。**防误判优先于家族整齐。**
#   · 体例一致性另有保住方式:响应体仍是同一个 `{"reason": ...}` 形状,`mapReason`
#     仍是唯一语义映射点(客户端 `send()` 的 500 分支已接进 `mapReason`)。
REASON_BASKET_NOT_FOUND = "basket_not_found"
REASON_CARD_NOT_READY = "card_not_ready"
REASON_CARD_CORRUPT = "card_corrupt"


def _shape_basket(ref, *, with_card: bool = True, card_version: Optional[int] = None) -> BasketOut:
    """`BasketRef` + 冻结卡 + Tier 留痕 → 契约。**snake→camel 走
    `basket_daily.card_to_public_dict` 这个唯一转换点**,API 层不另写一份。"""
    from neckline.report.basket_daily import card_to_public_dict
    from neckline.report.score_display import score_view
    from neckline.selection.basket_store import load_basket_card, load_tier_history

    card_out: Optional[BasketCardOut] = None
    version: Optional[int] = None
    reason: Optional[str] = None
    if with_card:
        row = load_basket_card(ref.basket_id, version=card_version, db_path=_db())
        payload = card_to_public_dict((row or {}).get("card")) if row else None
        if payload is None:
            # 列表/详情端点里卡只是**内嵌可选字段**,不像 `/card` 那样"整个请求就是要这张
            # 卡",故这里**照返 200 + 如实的 reason**(⛔ 不把一篮坏卡升级成整份清单 500);
            # 但两态必须分得开 —— 降格成 `card_not_ready` 就把数据事故说成了等待中。
            reason = REASON_CARD_CORRUPT if (row and row.get("card_corrupt")) else REASON_CARD_NOT_READY
        else:
            card_out = BasketCardOut(**payload)
            version = (row or {}).get("version")
    th = load_tier_history(ref.basket_id, db_path=_db())
    # V2.1-④ 百分制:从**同一份已冻结的** `mech_breakdown` 换算(⛔ 零重算、零取数),
    # 唯一实现在展示层 `report/score_display.py`。`None` → 两个新键退化成
    # `null` + `[]`(**⛔ 不是 0 分**)。
    sv = score_view(th["mech_score"], th["mech_breakdown"]) if th else None
    return BasketOut(
        basketId=ref.basket_id, basketKey=ref.basket_key, name=ref.name,
        tradeDate=ref.trade_date, tier=ref.tier, memberCodes=list(ref.member_codes),
        engineCode=getattr(ref, "engine_code", None),
        engineVersion=getattr(ref, "engine_version", None),
        skeletonVersion=getattr(ref, "skeleton_version", None),
        card=card_out, cardVersion=version, cardUnavailableReason=reason,
        tierHistory=(TierOut(
            basketId=ref.basket_id, tradeDate=th["trade_date"], tier=th["tier"],
            mechScore=th["mech_score"], mechBreakdown=th["mech_breakdown"],
            rankInTier=th["rank_in_tier"], rankMech=th["rank_mech"],
            llmRankDelta=th["llm_rank_delta"], llmReason=th["llm_reason"],
            packVersion=th["pack_version"],
            scorePercent=(sv or {}).get("scorePercent"),
            scoreContributions=[ScoreContribOut(**c) for c in ((sv or {}).get("contributions") or [])],
        ) if th else None),
    )


@app.get(f"{API_PREFIX}/baskets", dependencies=[Depends(require_token)])
def list_baskets(date: str = "", tier: int = 0) -> BasketsListOut:
    """某交易日的篮子清单(V2.1 起新数据只有 T1/T2,按 tier 升序、basket_key 升序,
    **确定性**)。

    `date` 缺省 = 最近一份报告的交易日;`tier` 可选过滤,`0` = 全部。
    ⚠ **`tier=3` 仍在白名单里,是刻意的**(V2.1-② 读侧宽容):T3 已于 V2.1 退役、
    写侧不再产生新的 tier=3 行,但 `baskets` 表里躺着 V2 时代的历史行 —— 把 `3` 从
    过滤白名单里删掉 = 历史日期按 T3 查会被当成"非法档位"退化成"全部",**用户拿不到
    也看不出**。收窄的是**写侧**(`selection/tier.py::TIERS`),不是这里。
    **空列表是合法输出**:「今日无篮子达到定档标准」(⑥-b-B)—— ⛔ 不是 404,
    也⛔ 不许为了让界面好看而放宽任何一条质量线。日期格式非法 → 空列表 + 原 `date`
    回显(同 `GET /report` 的降级契约,不 4xx)。"""
    from neckline.selection.basket_store import load_baskets_for_date

    day = date or (report_store.latest_report_date(db_path=_db()) or "")
    if not (len(day) == 8 and day.isdigit()):
        return BasketsListOut(tradeDate=date, items=[])
    tiers = (tier,) if tier in (1, 2, 3) else None   # ⚠ 3 = 历史档位,读侧宽容(见 docstring)
    refs = load_baskets_for_date(day, tiers=tiers, db_path=_db())
    return BasketsListOut(tradeDate=day, items=[_shape_basket(r) for r in refs])


@app.get(f"{API_PREFIX}/baskets/{{basket_id}}", dependencies=[Depends(require_token)])
def get_basket(basket_id: int) -> BasketOut:
    """单个篮子(含冻结卡与 Tier 留痕)。不存在 → 404 `basket_not_found`。

    ⚠ **篮子在、卡没生成**不是 404:照返 200,`card=null` +
    `cardUnavailableReason='card_not_ready'`(合法中间态,客户端据此说「卡还没生成」)。"""
    from neckline.selection.basket_store import load_basket

    ref = load_basket(basket_id, db_path=_db())
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_BASKET_NOT_FOUND})
    return _shape_basket(ref)


@app.get(f"{API_PREFIX}/baskets/{{basket_id}}/card", dependencies=[Depends(require_token)])
def get_basket_card(basket_id: int, version: int = 0) -> BasketCardOut:
    """一张冻结的篮子卡(⑦)。`version` 缺省 `0` = 取**最新版本**;给具体版本号则取那一版
    (D0 原判恒 `version=1`,D+1 的新信息追加 `version=2,3…`,**D0 行一字不改**)。

    **三个 reason,语义两两相反,⛔ 客户端必须各有 case**:
      · `basket_not_found`(404)—— 篮子本身不存在。
      · `card_not_ready` (404)—— **篮子在、卡还没生成**(⑦ 事务 2 独立于事务 1)。
        文案方向「本篮的卡还没生成」,**不是**「篮子不存在」——后者会让用户以为系统丢了篮子。
      · `card_corrupt`   (**500**)—— **有卡行但读不出**(json 解不出 / 顶层必需键缺失)。
        ⛔ 不是 404、⛔ 不许降格成 `card_not_ready`:那张卡是冻结件,**不会自己好**,
        当成「还没生成」就是让客户端永远等一张永远不来的卡(裁定详见上方常量块)。
    """
    from neckline.report.basket_daily import card_to_public_dict
    from neckline.selection.basket_store import load_basket, load_basket_card

    if load_basket(basket_id, db_path=_db()) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_BASKET_NOT_FOUND})
    row = load_basket_card(basket_id, version=(version or None), db_path=_db())
    if row and row.get("card_corrupt"):
        # store 侧已打 ERROR(唯一检测点),这里只负责如实转成 500 + reason。
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail={"ok": False, "reason": REASON_CARD_CORRUPT})
    payload = card_to_public_dict((row or {}).get("card")) if row else None
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_CARD_NOT_READY})
    return BasketCardOut(**payload)


@app.get(f"{API_PREFIX}/baskets/{{basket_id}}/verification", dependencies=[Depends(require_token)])
def get_basket_verification(basket_id: int, date: str = "") -> BasketVerificationOut:
    """某篮某日的验证状态(⑧「当前状态」三路读法唯一实现,本端点只读不判)。

    `date` 缺省 = 今天。**三个位分别回答不同问题,⛔ 客户端不许合并**:
    `state` 四态 / `provisional`(盘中暂态、未收盘定论)/ `notEvaluated`(**今天还没判过**,
    不是「判了是 unclear」)。篮子不存在 → 404 `basket_not_found`;
    **篮子在、今天没判过**照返 200 + `notEvaluated=true`(⛔ 不是 404)。"""
    from neckline.sentinel.basket_verify_store import current_state, list_rows
    from neckline.selection.basket_store import load_basket

    if load_basket(basket_id, db_path=_db()) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_BASKET_NOT_FOUND})
    day = (datetime.strptime(date, "%Y%m%d").date()
           if (len(date) == 8 and date.isdigit()) else date_cls.today())
    st = current_state(basket_id, day, db_path=_db())
    rows = list_rows(basket_id, day, db_path=_db())
    return BasketVerificationOut(
        basketId=basket_id, tradeDate=day.strftime("%Y%m%d"), state=st.state, label=st.label,
        source=st.source, observedAt=st.observed_at, provisional=st.provisional,
        notEvaluated=st.not_evaluated, evidence=st.evidence,
        rows=[{"state": r.state, "source": r.source, "observedAt": r.observed_at,
               "evidence": r.evidence} for r in rows],
    )


@app.get(f"{API_PREFIX}/baskets/{{basket_id}}/review", dependencies=[Depends(require_token)])
def get_basket_review(basket_id: int, date: str = "") -> BasketReviewOut:
    """某篮某个复盘日(D+1)的盘后复盘(⑨)。`date` 缺省 = 今天。

    篮子不存在 → 404 `basket_not_found`;**篮子在、那天还没复盘**→ 404
    `not_found`(复用既有 reason 字符串,客户端 `mapReason` 已有 case;
    CLAUDE.md 明文:复用已有 reason 不需要新 case)。"""
    from neckline.review.basket_review_store import load_review
    from neckline.selection.basket_store import load_basket

    ref = load_basket(basket_id, db_path=_db())
    if ref is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_BASKET_NOT_FOUND})
    day = date if (len(date) == 8 and date.isdigit()) else date_cls.today().strftime("%Y%m%d")
    row = load_review(basket_id, day, db_path=_db())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": "not_found"})
    meta = (row.mech.get("meta") or {}) if isinstance(row.mech, dict) else {}
    return BasketReviewOut(
        basketId=basket_id, basketKey=ref.basket_key, name=ref.name, tier=ref.tier,
        d0=str(meta.get("d0") or ""), reviewDate=row.review_date, depth=row.depth,
        mech=row.mech if isinstance(row.mech, dict) else {},
        llmText=row.llm_text, llmSkipReason=row.llm_skip_reason, degraded=bool(row.degraded),
    )


# —— 4A.3 盘中看板 ————————————————————————————————————————————————————

_SENTINEL_LABEL = {"entry": "买点", "invalidation": "证伪", "holding": "持仓"}
# v1.1:盘前校准 / D5 两新 sentinel 类型的中文标签(G.3 客户端看板明细;未识别原样透传)。
_SENTINEL_LABEL.update({"precall": "盘前校准", "d5exit": "D5退出"})
# v1.1-H2:退潮黄色预警(retreat/warn)进事件列表的标签(红色 retreat/brake 仍走
# retreatBrake 红条,不进列表)。客户端 SentinelKind 无 "退潮" 枚举 → kind=nil →
# 中性色渲染,不崩(不改客户端)。
_SENTINEL_LABEL.update({"retreat": "退潮"})
# V2.2-⑤-B:连续止损**纯提醒**的看板事件(sentinel='circuit',复用既有名字不新增类型)。
# 客户端 `SentinelKind` 无此枚举 → kind=nil → 中性色渲染,不崩(同 v1.1-H2「退潮」先例,
# ⛔ 本版不动客户端)。**它是一条事件,不是状态** —— 看板上没有、也不许有任何锁定横幅。
_SENTINEL_LABEL.update({"circuit": "连续止损提醒"})


@app.get(f"{API_PREFIX}/board", dependencies=[Depends(require_token)])
def board() -> BoardOut:
    """⚠ **LEGACY AUDIT(V2.4.0 P0 起)—— 端点保留一个兼容周期,新客户端零调用。**

    ① 端点**不删**:已装的 2.3.x iPhone 包在换包前仍会拉它,删了就是 404
       (下一个破坏性 API 大版本再统一清理,P0.5);
    ② **不再作为任何产品状态来源**:v2.4.0 客户端不请求本端点、不据它画任何东西;
    ③ 返回的仍是**历史行**:退潮判级与通用盘中证伪已退役,`retreatBrake.active`
       在新链路下**永远不会再被置为 true**,`sentinel='invalidation'` 也不再有新行 ——
       但**部署当日库里可能已有当天早些时候写下的旧行**,本端点照实返回,
       ⛔ **不通过删历史行让界面「看起来修好了」**(P0.5 末条)。

    当日盘中看板。数据源 = 当日 `sentinel_events` 表聚合,**只读、不触发任何新判断**。
    """
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
        from neckline.data.realtime import get_quotes
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


def _resolve_quote_one(code: str) -> Optional[Any]:
    """单票**完整**实时报价对象(v2.0.0 ⑩-A:供 `entry_snapshots` 捕捉涨幅/量能等)。

    复用与 `_resolve_prices` 同一个可注入钩子 `_QUOTES_FN`(单测免联网、且沿用
    既有 `test_price_injected_from_quotes` 的注入姿势,不必为快照另开一套 mock
    机制);与 `_resolve_prices` 不同的是**不裁剪成只剩 price**,原样把 Quote
    对象透给 `positions_entry`,由它决定要不要 / 怎么用其余字段。任何源失败 /
    无网络 → `None`,不崩(开仓主流程不能因取不到实时价而失败)。"""
    fetch = _QUOTES_FN
    if fetch is None:
        from neckline.data.realtime import get_quotes
        fetch = get_quotes
    try:
        quotes = fetch([code])
    except Exception:  # noqa: BLE001
        logger.warning("[positions] 开仓快照拉实时价失败(quote 落 None)", exc_info=True)
        return None
    return (quotes or {}).get(code)


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


def _active_config() -> Tuple[float, Optional[int], float, Optional[float]]:
    """现役策略 config 的四个值(单一事实源 `brain.active_config`,§3.8 铁律):
    (stop_pct, max_hold_days, single_cap, take_profit_retrace)。无现役版本(异常状态)
    → 退回 `MomentumConfig` 字段默认(不在此另拍字面量)。

    ⚠ **V2.2-⑤:`max_hold_days` 现在可能是 `None`**(`v2.2-k8` = 章程不设时间退出)。
    **⛔ 不许在这里拿一个默认天数顶上** —— 那是把"没有这条规矩"悄悄换成"D5 该走",
    正是 §3.11-E 否决哨兵位时说的那种"看不出来"的病。调用方按 `None` 各自如实处理。

    **兜底判据是「键缺失」不是「falsy」(2026-07-27 审计 🔵-9)**:旧写法 `cfg.get(k) or fb.k`
    会把章程**显式**设的 0 / None(如未来某版 `stop_pct=None` = 不设止损)悄悄换回默认
    0.05 —— 那是「章程说不设止损、系统偷偷给你设了 5%」。现按 `k in cfg` 判存在性,显式值
    一律照用;只有键真缺失才落 `MomentumConfig` 字段默认。`stop_pct` 若显式 None,
    调用方(止损线/距止损)按 0.0 处理 = 不派生止损线,与「不设止损」语义一致。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum_config import MomentumConfig

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
        (int(max_hold) if max_hold is not None else None),
        float(single_cap),
        (float(tpr) if tpr is not None else None),
    )


def _active_momentum_config() -> "MomentumConfig":
    """现役 config → 完整 `MomentumConfig`(携 v1.3 两档时间退出字段);无现役版本 → 字段默认。
    供 `PositionOut` 两档时间退出派生(`classify_time_exit`,单一源 sentinel/precall)复用。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum_config import MomentumConfig

    cfg = brain.active_config(db_path=_db())
    return MomentumConfig(**cfg) if cfg else MomentumConfig()


def _stop_line(buy_price: float, stop_pct: float) -> float:
    """派生止损线 = 买入价 ×(1−stop_pct)(读现役 config,§2.1 单一常量,不硬编 0.95)。

    ⚠ **V2.3.2-⑤:这条线在 `loss_warning_action=="review"` 的章程下叫「亏损警戒线」**
    —— **算法与数值一字未改**(`stop_pct` 仍是唯一源),变的只是它触发什么:到线只发
    亏损警戒、由用户完成离场决策,**系统不代下单、更不自动卖出**(K8.md §十三)。
    对外语义由 `_loss_warning()` 随契约下发,客户端据此决定这条线怎么称呼。"""
    return round(buy_price * (1 - stop_pct), 2)


def _loss_warning() -> Tuple[Optional[float], Optional[str]]:
    """现役章程的对外退出语义 `(loss_warning_pct, loss_warning_action)`(V2.3.2-⑤)。

    **唯一源同 `_active_config`** = 现役 `strategy_versions` config。老章程行没有这两个
    字段 → `(None, None)` = **该章程没有声明过这个语义**;⛔ 不拿 `stop_pct` 顶上去,
    也⛔ 不在这里替它推断成"强制条件单"(那是客户端缺键时的展示层默认,不是服务端结论)。
    """
    from neckline.strategy import brain

    cfg = brain.active_config(db_path=_db())
    pct = cfg.get("loss_warning_pct")
    action = cfg.get("loss_warning_action")
    return (
        float(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None,
        action if isinstance(action, str) and action else None,
    )


def _retrace_state(
    position: "pos_store.Position", price: float, peak_hist: float, take_profit_retrace: Optional[float]
) -> Optional[Dict[str, Any]]:
    """回落止盈状态(plan B.1;**复用 `holding.check_take_profit` 判定「是否触发」,不重写
    阈值比较**)。无实时价(price≤0)→ None(算不出回落)。"""
    if price <= 0:
        return None
    from neckline.sentinel.holding import check_take_profit
    from neckline.data.realtime import Quote

    q = Quote(code=position.ts_code, name="", price=price, pre_close=0.0, open=0.0,
              high=0.0, low=0.0, volume=0.0, amount=0.0, ts="", source="derived")
    peak = max(peak_hist or 0.0, price)
    reason = check_take_profit(position, q, peak_hist, take_profit_retrace)
    retrace_pct = (peak - price) / peak if peak > 0 else 0.0
    return {"peak": round(peak, 2), "retracePct": round(retrace_pct, 4), "triggered": reason is not None}


def _today_action(
    d_count: int, eff_max: Optional[int], dist_to_stop_pct: Optional[float],
    retrace_state: Optional[Dict[str, Any]], time_exit_state: str,
    *, stop_advisory: bool = False,
) -> str:
    """今日动作提示文案(纯展示层,优先级:时间退出 > 回落止盈 > 跌破/逼近止损 > 持有中)。
    v1.3-① 两档:`hard_cap_exit`/`time_exit_next_day` 走离场优先;`profit_exempt` 是「豁免时间
    退出、交回落+止损管到硬上限」的持有态(不抢离场);`holding` 常规持有。K1 单档下
    `time_exit_state` 只会是 `time_exit_next_day`(d≥max_hold)或 `holding`,行为与 v1.3 前一致。

    **v1.4-①-B `suspended_hold`**:当日无 EOD 行且尚未定格 → 判向挂起。文案**最优先**
    (它就是为了盖掉那句「按计划离场」——催用户去卖一只卖不掉的票正是 P0-2 的病根),
    且必须说清「D 计数照走、判向挂着、复牌当日再定」。

    🔴 **V2.4.0 P3.1**:advisory 分支的「这条线叫什么 / 触发后怎么办」统一走
    `neckline.strategy.charter_copy` 单一源——修此前遗留的一处真 bug:advisory 分支
    曾经仍把这条线叫「止损线」、说「离场决策在你」,K8.md §十九 要求 review 口径下
    这条线叫「亏损警戒线」、触发后说「触发后由你复核原判断」(逐字)。
    """
    from neckline.sentinel.precall import HARD_CAP_EXIT, PROFIT_EXEMPT, SUSPENDED_HOLD, TIME_EXIT_NEXT_DAY
    from neckline.strategy import charter_copy

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
        # V2.2-⑤:止损口径由现役章程决定(⛔ 判定与阈值一字未动,只换这句话在说什么)。
        if dist_to_stop_pct <= 0:
            return (f"止损警戒:现价已跌破{charter_copy.stop_line_label(True)},"
                    f"{charter_copy.stop_action_phrase(True)}(系统不代下单)"
                    if stop_advisory else
                    "现价已跌破止损线,若条件单未成交请立即人工确认(系统不代下单)")
        if dist_to_stop_pct <= 0.02:
            return (f"止损警戒:距{charter_copy.stop_line_label(True)} {dist_to_stop_pct:.1%},"
                    f"{charter_copy.stop_action_phrase(True)}"
                    if stop_advisory else f"距止损线 {dist_to_stop_pct:.1%},盯紧条件单")
    if time_exit_state == PROFIT_EXEMPT:
        return f"浮盈豁免时间退出,交回落止盈+止损管到硬上限(D{d_count}/D{eff_max})"
    # V2.2-⑤:章程无时间退出条款 → **不编一个 D 上限出来**(`eff_max is None`),
    # 如实说明持有天数只是计数、不指向任何离场日。
    # 🔴 V2.4.0 P3.1:这句话走 `charter_copy.TIME_EXIT_DISABLED_COPY` 单一源
    # (K8.md §十三 逐字「本版无机械时间退出 —— D 计数只作记录」)——⛔ 别在这里
    # 再拍一份措辞:客户端 `Position.timeExitDisclosure` 与这句同屏出现(横幅 +
    # 卡底那行),两处措辞不同就是「一屏两个名字」(V2.3.2-⑤ 实拍逮到过)。
    # ⚠ **只改持仓界面这一面**:盘后 markdown 报告(`report/render.py`)与周复盘
    # (`review/reconcile.py`)仍用章程术语「无时间退出条款」——那是**审计面**、
    # 读者是在核对章程条款本身,P3.1 没动它们,⛔ 别顺手"统一"过去。
    if eff_max is None:
        return f"持有中(D{d_count};{charter_copy.TIME_EXIT_DISABLED_COPY})"
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






# —— V2.4.0 P0.5+ 持仓提醒的新下发通道 ——————————————————————————————————————
#
# 🔴 **它是 P0.3「先迁移再删页面」的落点**:亏损警戒 / 离场参考 / 板块跳水这三类提醒
# 此前**只经 `GET /board`** 下发,而 P0 要求新客户端零调用 `/board` —— 不先把通道换掉
# 就删页面 = **静默弄丢仍然有效的持仓提醒**(P0.3 末段明令)。
# ⛔ **不新建端点、不新建表、不新建取数实现** —— 复用 `dedup.load_events_for_date`。
#
# 🔴 **V2.4.0 复审 🔴-1 整改(用户裁定 A,2026-08-12):取数口径由「只取 `holding`」
# 改成**正面白名单**四类。**⛔ 白名单不是黑名单** —— 退役的两类(`retreat` /
# `invalidation`)不是"没写进来",是**结构上进不来**(守门双向锁:四类都在 + 退役两类
# 一个都不许混进来)。
#
# 为什么是这四类(逐条出处,⛔ 不是手感):
#   · `holding`   —— P0.5+ 原文就写着,亏损警戒 / 离场参考 / 板块跳水。
#   · `circuit`   —— `consecutive_stops`(连续三笔止损)是 **K8 §十三 明写的能力**,
#                    `GET /circuit` 的 docstring 白纸黑字「没有替代端点 —— 提醒走推送
#                    与看板事件」,页面删掉之后那两条腿断了一条。
#   · `precall`   —— `position_low_open`(9:26 集合竞价开盘已逼近/跌破亏损警戒线)
#                    **根本没有推送 kind**(`precall.py::_record` 只落库不推),
#                    页面删掉 = 它在任何界面上都不存在。
#   · `attention` —— `basket_peers_weak` / `holding_decoupled` 两类的 `scope`
#                    **就是持仓代码**,不是全局刹车;APNs 是一次性打扰(可被开关掐掉、
#                    划走就没了),**不是"入口"**。
# ⚠ `attention/sector_bid_fade`(scope = 指数码)与 `market_shock`(scope = 空)
#   匹配不到任何持仓 → 结构性不出现在本通道。
#   ✅ **2026-08-12 用户裁定 ② 已给它们指定落点** = 下面的**组合环境提醒**(§七 P1-81 销案);
#   裁定原文明写「⛔ 也不重复塞进单票详情」,故本通道**另加一道按 kind 的显式排除**
#   (`_PORTFOLIO_ONLY_KINDS`)—— ⚠ 排除不是"补上原本会漏进来的东西":scope 那两条
#   结构性理由**仍然成立**,这一道是**把裁定写成机器判据**,让它不因将来某次改 scope 而失效。
_POSITION_ALERT_SENTINELS: FrozenSet[str] = frozenset({
    "holding", "attention", "circuit", "precall",
})

# —— 组合环境提醒:两类**只走顶部那一段**的事件(2026-08-12 用户裁定 ②)——————————
#
# 🔴 **裁定原文(逐字,⛔ 不许加减)**:「两类提醒统一落在**持仓页顶部的「组合环境提醒」**。
# `sector_bid_fade` **按板块展示,并列出受影响持仓**;`market_shock` 作为**全组合提醒**。
# 二者均使用**黄色**提醒,**只提供环境证据,不给交易指令,不影响选股等级和交易资格,
# 也不重复塞进单票详情**。」
#
# ⚠ 这里用 `payload.kind`(事件落库时冻结的那个码)判,**⛔ 不用 `event_key`** ——
#   `fade` / `shock` 两个键短得没有辨识度,而 `kind` 就是 `notify_kinds` 的正式码。
#   老行缺 `kind` 时退回 `event_key` 反查(见 `_portfolio_kind_of`),⛔ 不静默丢事件。
_PORTFOLIO_ONLY_KINDS: FrozenSet[str] = frozenset({
    notify_kinds.KIND_SECTOR_BID_FADE, notify_kinds.KIND_MARKET_SHOCK,
})

#: 老行缺 `payload.kind` 时的 `event_key` → kind 反查(两类各一个静态键,历史去重键
#: ⛔ 一个字都不许改 —— 改了同一件事在老库与新库里就去重不到一起)。
_PORTFOLIO_EVENT_KEY_KIND: Dict[str, str] = {
    "fade": notify_kinds.KIND_SECTOR_BID_FADE,
    "shock": notify_kinds.KIND_MARKET_SHOCK,
}

#: kind → 展示范围。`sector` = 按板块展示 + 列受影响持仓;`portfolio` = 全组合。
_PORTFOLIO_SCOPE_KIND: Dict[str, str] = {
    notify_kinds.KIND_SECTOR_BID_FADE: "sector",
    notify_kinds.KIND_MARKET_SHOCK: "portfolio",
}

# 🔴 **展示强调档 = `warn`(黄色),两类同档 —— 这是 2026-08-12 用户裁定 ③ 的原话**
# (「`sector_bid_fade` 与 `market_shock` 在组合环境提醒中统一按 `warn` 展示」),
# ⛔ **不是工程侧按同族项推出来的默认值**(`_POSITION_ALERT_LEVEL` 后四条才是那种)。
# ⚠ 同一份裁定同时确认了 `_POSITION_ALERT_LEVEL` 现有四条新增项**一个字节不动**:
#   「`position_low_open = critical`;`consecutive_stops = warn`;`decoupled = warn`;
#    `basket* = warn`」。
_PORTFOLIO_ALERT_LEVEL: Dict[str, str] = {
    notify_kinds.KIND_SECTOR_BID_FADE: "warn",
    notify_kinds.KIND_MARKET_SHOCK: "warn",
}

# 🔴 **退役两类:显式列出来当反向断言用**(守门单测拿它与白名单做交集必须为空)。
# ⛔ 它**不是**过滤器 —— 过滤器是上面那张白名单;这张表只是让"退役的进不来"
# 这件事有一个**可被机器检查的名字**。
_RETIRED_ALERT_SENTINELS: FrozenSet[str] = frozenset({"retreat", "invalidation"})

# `eventKey` → 展示层强调档。**这是"这条提醒在持仓卡上有多醒目",不是推送分级**
# (推送分级的唯一源是 `notify_kinds.LEVEL_OF_KIND`,管的是"响不响、怎么响")——
# 两者刻意分开:`sector_dive` / `take_profit` 在推送侧是立即级,在这一屏上不是。
# ⚠ **前四条是 P0.5+ 已发布的判定,本次一个字节没动**;后四条随复审 🔴-1 补,
#   逐条对齐已有同族项:
#   · `position_low_open` —— 说的就是 `stop_approach` 那条**同一根亏损警戒线**
#     (同一个 `stop_pct`、同一句"逼近/跌破"),故同档 `critical`。
#   · `consecutive_stops` —— 讲的是**已经发生的三笔**这个行为模式,不是手上这一笔
#     此刻的价位;与 `sector_dive`(环境性)同族 → `warn`。
#   · `decoupled` / `basket<id>` —— ⑪-A 四监测在 `notify_kinds.LEVEL_OF_KIND` 里
#     就是「重要不紧急」(今天要处理、不必打断手头的事)→ `warn`。
# ✅ **2026-08-12 用户裁定 ③ 已逐条确认这四条,原话**:「`position_low_open = critical`;
#   `consecutive_stops = warn`;`decoupled = warn`;`basket* = warn`。」
#   🔴 它们自此**不再是"工程侧按同族项推的默认值",而是用户裁定值** —— 要改得再拍一次板。
# ⚠ 未登记的 event_key 落 `info`(如实中性,不冒充紧急,也不吞掉)。
_POSITION_ALERT_LEVEL: Dict[str, str] = {
    "stop_approach": "critical",
    "sector_dive": "warn",
    "take_profit": "info",
    "exit_reference": "info",
    # —— 复审 🔴-1 补齐(裁定 A)——————————————————————————————————————
    "position_low_open": "critical",
    "consecutive_stops": "warn",
    "decoupled": "warn",
}

#: `attention/basket_peers_weak` 的 `event_key` 是 **`basket<篮子 id>`**(动态)——
#: 精确匹配的 dict 逮不到它。⛔ 别为此把 `event_key` 改成静态串:那是历史去重键,
#: 改了会让同一件事在老库与新库里去重不到一起。
_POSITION_ALERT_LEVEL_PREFIX: Dict[str, str] = {"basket": "warn"}


def _position_alert_level(event_key: str) -> str:
    """`event_key` → 展示强调档。精确表优先,再试前缀表,都不认 → `info`。"""
    if event_key in _POSITION_ALERT_LEVEL:
        return _POSITION_ALERT_LEVEL[event_key]
    for pre, lvl in _POSITION_ALERT_LEVEL_PREFIX.items():
        if event_key.startswith(pre):
            return lvl
    return "info"


def _today_position_alerts(trade_date: date) -> Dict[str, List[PositionAlertOut]]:
    """当日**四类**哨兵事件按 `ts_code` 分组(时间升序,`load_events_for_date`
    已按 `pushed_at, id` 排好,这里只保序分桶)。

    🔴 **正面白名单 `_POSITION_ALERT_SENTINELS`,⛔ 不是黑名单**:`invalidation` /
    `retreat` 自 V2.4.0 P0 起已停写(库里仍有历史行),它们**不在白名单里 = 结构上
    进不来**;换成"排除这两个"的写法,日后再退役一类就会静默漏进来。
    ⚠ 仍然**只画该持仓自己的行**:`ts_code` 为空的市场级行(`market_shock`)由
    `if not code` 天然排除;`sector_bid_fade` 的 scope 是指数码,匹配不到任何持仓。
    🔴 **2026-08-12 用户裁定 ② 之后又多了一道显式排除**(`_PORTFOLIO_ONLY_KINDS`):
    那两类的落点是**持仓页顶部的组合环境提醒**,裁定原文「⛔ 也不重复塞进单票详情」。
    ⚠ 这一道**不是**在补一个原本会漏进来的洞(scope 那两条理由仍然成立),
    而是把裁定写成机器判据 —— 将来谁改了 scope,这里也不会静默把它们塞回单票。
    读库异常 → 空 dict(持仓卡是每日最常看的一屏,**绝不因为一条提醒读不到就掀翻它**)。
    """
    try:
        events = dedup.load_events_for_date(trade_date, db_path=_db())
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
        logger.warning("[positions] 读当日持仓提醒失败(按无提醒处理,不影响持仓列表)", exc_info=True)
        return {}
    out: Dict[str, List[PositionAlertOut]] = {}
    for e in events:
        if e.get("sentinel") not in _POSITION_ALERT_SENTINELS:
            continue
        if _portfolio_kind_of(e) in _PORTFOLIO_ONLY_KINDS:
            continue                     # 裁定 ②:这两类只走组合环境提醒
        code = e.get("ts_code") or ""
        if not code:
            continue
        key = e.get("event_key", "")
        out.setdefault(code, []).append(PositionAlertOut(
            eventKey=key,
            verdict=(e.get("payload") or {}).get("body", ""),
            ts=e.get("pushed_at", ""),
            level=_position_alert_level(key),
        ))
    return out


def _portfolio_kind_of(event: Mapping[str, Any]) -> str:
    """一条 `sentinel_events` 行的**组合环境提醒 kind**(不是这两类 → 空串)。

    优先取 `payload.kind`(落库时冻结的正式码);老行缺这一键时按 `event_key` 反查
    (只对 `sentinel='attention'` 生效 —— `fade` / `shock` 两个键太短,别的哨兵
    将来撞上就麻烦了)。⛔ 反查表里那两个键是**历史去重键,一个字都不许改**。
    """
    if event.get("sentinel") != "attention":
        return ""
    kind = str((event.get("payload") or {}).get("kind") or "")
    if kind:
        return kind
    return _PORTFOLIO_EVENT_KEY_KIND.get(str(event.get("event_key") or ""), "")


def _today_portfolio_alerts(trade_date: date) -> List[PortfolioAlertOut]:
    """当日**组合环境提醒**(2026-08-12 用户裁定 ②;§七 P1-81 的落点)。

    🔴 **只读、只透传**:源是 `sentinel_events` 已冻结的那几行,服务端
    ⛔ 不重判、⛔ 不按"现在的持仓"重算受影响清单、⛔ 不合成任何"环境正常"。
    ⚠ **受影响持仓取事件当时冻结的那一批**(`payload.metrics.holders`);
    这一键缺席 = `affectedRecorded=False`(第三态「本次未记录」),
    ⛔ 不折平成「没有受影响的持仓」。
    读库异常 → 空列表(同 `_today_position_alerts`:持仓这一屏绝不因它掀翻)。
    """
    try:
        events = dedup.load_events_for_date(trade_date, db_path=_db())
    except Exception:  # noqa: BLE001 —— 可选情报的保险丝(§铁律)
        logger.warning("[positions] 读当日组合环境提醒失败(按无提醒处理)", exc_info=True)
        return []
    out: List[PortfolioAlertOut] = []
    for e in events:
        kind = _portfolio_kind_of(e)
        if kind not in _PORTFOLIO_ONLY_KINDS:
            continue
        payload = e.get("payload") or {}
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else None
        holders = metrics.get("holders") if metrics is not None else None
        recorded = isinstance(holders, list)
        out.append(PortfolioAlertOut(
            kind=kind,
            scopeKind=_PORTFOLIO_SCOPE_KIND.get(kind, "portfolio"),
            scopeCode=str(e.get("ts_code") or ""),
            verdict=str(payload.get("body") or ""),
            ts=str(e.get("pushed_at") or ""),
            level=_PORTFOLIO_ALERT_LEVEL.get(kind, "warn"),
            affectedCodes=[str(c) for c in holders] if recorded else [],
            affectedRecorded=recorded,
        ))
    return out


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
    lw_pct, lw_action = _loss_warning()   # V2.3.2-⑤:对外退出语义(整批一次读,逐行同值)
    # V2.2-⑤:现役章程的止损口径(强制条件单 / 亏损警戒),只换 `todayAction` 文案口吻。
    from neckline.strategy import brain as _brain
    stop_advisory = _brain.active_stop_is_advisory(db_path=_db())
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
    # V2.4.0 P0.5+:今日持仓提醒的新下发通道(原先只经 `GET /board`,而新客户端零调用它)。
    # 一次读当日全部事件、在内存里按 ts_code 分组 —— ⛔ 不逐持仓查一遍库。
    alerts_by_code = _today_position_alerts(today)
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
        # V2.2-⑤:章程无时间退出条款(`max_hold` is None)→ 没有"晚于 D{n}"这个量,恒 0。
        locked_late = (max(0, locked_day - max_hold)
                       if locked_day is not None and max_hold is not None else 0)
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
            status=h.status, stopLine=stop_line,
            lossWarningPct=lw_pct, lossWarningAction=lw_action,
            takeProfitRetrace=tpr,   # V2.4.0 P3.1:现役章程口径指纹,零新增读库(见 schemas.py)
            stopOrderChecked=False,
            dCount=dcount, maxHoldDays=max_hold,
            distToStopPct=(round(dist, 4) if dist is not None else None),
            retraceState=retrace,
            todayAction=_today_action(dcount, eff_max, dist, retrace, te_state,
                                      stop_advisory=stop_advisory),
            maxHoldDaysEffective=eff_max, timeExitState=te_state,
            timeExitLockedDay=locked_day, timeExitLockedLateDays=locked_late,
            buyFees=h.buy_fees, sellFees=h.sell_fees,
            priceStale=(stale.to_public_dict() if stale is not None else None),
            k4DataUnavailable=snap.get("data_unavailable"),   # None=老快照未记录,如实透 null
            k4Advisory=k4_advisory, scenarioReviewPending=bool(snap.get("scenario_review")),
            alerts=alerts_by_code.get(h.ts_code, []),
        ))
    # ✅ v2.3.0:`circuit` 键**已物理删除**(两步淘汰第二步,判据见 `schemas.py` 该节注释)。
    # 🔴 组合环境提醒(裁定 ②):与逐持仓那份**读同一次库的结果**在语义上是两段,
    # 但取数走各自的过滤器 —— ⛔ 别为了"省一次读库"把两者合成一个循环:
    # 那会让「不重复塞进单票详情」这条裁定失去一个独立可读的落点。
    return PositionsOut(holdings=out, portfolioAlerts=_today_portfolio_alerts(today))


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
    lw_pct, lw_action = _loss_warning()   # V2.3.2-⑤:同 `/positions`,这条线的对外语义
    cap_ceil = float(single_cap)
    cap_floor = cap_ceil * _ENTRY_SUGGESTION_FLOOR_FRAC
    if price <= 0:
        return EntrySuggestionOut(
            code=code, price=price, qtyLow=0, qtyHigh=0,
            capFloor=cap_floor, capCeil=cap_ceil, stopLine=0.0,
            lossWarningPct=lw_pct, lossWarningAction=lw_action,
        )
    return EntrySuggestionOut(
        code=code, price=price,
        qtyLow=int(math.floor(cap_floor / price / 100) * 100),
        qtyHigh=int(math.floor(cap_ceil / price / 100) * 100),
        capFloor=cap_floor, capCeil=cap_ceil,
        stopLine=_stop_line(price, stop_pct),
        lossWarningPct=lw_pct, lossWarningAction=lw_action,
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
    「给错了」必须能分开)。

    **v2.0.0(⑩-A/B)**:写入改走 `neckline.positions_entry.record_buy`(唯一编排
    入口,CLI `scripts/positions.py add` 共用同一份逻辑)——除落台账本身外,同时
    冻结 `entry_snapshots`、继承 `position_plans` version=1、`user_actions` 落
    `kind='buy'`。这些全部是**系统自动关联的增强**,任何一项失败都不影响开仓本身
    成功(`positions_entry.record_buy` 内部已包保险丝);响应新增的
    `sourceBasketKey`/`tier`/`role`/`planAvailable`/`planDeviationNotice` 字段纯
    展示,老客户端忽略未知键不受影响。**实时报价只在买入日=今天时才取**(历史
    补录若也拿"此刻"的实时价会把无关的当下行情焊进历史快照,是数据污染不是丰富)。

    **v2.0.0(契约线审计 🟡 Y7,2026-08-03)**:可选 `idempotencyKey` —— 同键二次提交
    **不开第二笔仓**,直接重放那笔既有仓的结果并标 `replayed=true`。开仓是**不可逆
    记账**,而「服务端已落库、响应没回到客户端 → 客户端重试」是最常见的一类重复;
    重复一笔仓之后,持仓哨兵、仓位纪律、周复盘对账全都建立在错的持仓上。不传键 =
    不设防(CLI / 老客户端 / 历史补录行为逐字节不变)。
    """
    from neckline import positions_entry

    buy_date = _parse_buy_date_or_400(body.buyDate)
    quote = _resolve_quote_one(body.code) if buy_date == date.today() else None
    result = positions_entry.record_buy(
        body.code, body.buy_price, body.qty, buy_date,
        note=(body.entry_reason or None), buy_fees=body.buyFees,
        quote=quote, db_path=_db(), idempotency_key=body.idempotencyKey,
    )
    stop_pct, _mh, _sc, _tpr = _active_config()
    return PositionOpenOut(
        ok=True, position_id=result.position_id,
        stop_line=_stop_line(body.buy_price, stop_pct),
        sourceBasketKey=result.source_basket_key, sourceBasketName=result.source_basket_name,
        tier=result.tier, role=result.role,
        planAvailable=result.plan_available, planDeviationNotice=result.plan_deviation_notice,
        planIncompleteNotice=result.plan_incomplete_notice,
        replayed=result.replayed,
    )


@app.post(f"{API_PREFIX}/positions/{{position_id}}/close", dependencies=[Depends(require_token)])
def close_position(position_id: int, body: PositionCloseIn) -> OkOut:
    """清仓录入(§3.8 只记账,永不代下单/撤单)。可选 `closeReason` 落库(v1.2-A2,
    v2.0.0 起枚举扩至九枚,见 `positions.CLOSE_REASON_CODES`)。

    **V2.2-⑤-B(裁定 #8 熔断整体退役)**:清仓后只折进**纯提醒** —— 算一次尾部连续止损
    数,达 3 就推一条提醒 + 落一条看板事件,**⛔ 不建行、不锁、不改任何返回值语义**
    (`OkOut(ok=True)` 逐字段不变)。提醒**尽力而为、异常吞掉不阻断清仓主流程**。

    **v2.0.0(⑩-D)**:写入改走 `neckline.positions_entry.record_sell`(同 `record_
    buy` 姿势),清仓成功后额外落 `user_actions(kind='sell')`,该记账失败不影响
    清仓本身成功。"""
    from neckline import positions_entry

    if body.sell_time and len(body.sell_time) == 8 and body.sell_time.isdigit():
        sell_date = datetime.strptime(body.sell_time, "%Y%m%d").date()
    else:
        sell_date = date.today()
    ok = positions_entry.record_sell(
        position_id, sell_price=body.sell_price, sell_date=sell_date,
        close_reason=body.closeReason, sell_fees=body.sellFees, db_path=_db(),
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"ok": False, "reason": "not_holding"},
        )
    # 连续止损**纯提醒**折进清仓路径(V2.2-⑤-B;与 CLI 共用同一段编排,行为不因入口而异)。
    positions_entry.notice_consecutive_stops_after_close(
        position_id, sell_date=sell_date, db_path=_db(),
    )
    return OkOut(ok=True)


# —— V2-⑭-B 计划继承(`position_plans`)+ 建仓快照(`entry_snapshots`)————————
#
# **⑩-E 信息互通边界**:持仓侧可读篮子卡、可追加自己的计划版本,**不得回头修改
# 对方已冻结的历史信息** —— 本节两个端点对 `baskets`/`basket_cards`/`tier_history`
# 零写入(AST 守门单测锁死),`create_position_plan_version` 签名里根本没有相关参数。

def _shape_plan(row: Dict[str, Any]) -> PositionPlanOut:
    return PositionPlanOut(
        id=row["id"], positionId=row["position_id"], version=row["version"],
        sourceBasketId=row["source_basket_id"], sourceCardVersion=row["source_card_version"],
        plan=row["plan"], note=row["note"], createdAt=row["created_at"],
    )


@app.get(f"{API_PREFIX}/positions/{{position_id}}/plans", dependencies=[Depends(require_token)])
def list_plans(position_id: int) -> PositionPlansOut:
    """某持仓的全部计划版本(升序,`version=1` 是从 D0 卡继承的原判)。

    **空列表 = 这笔仓不存在或建于 ⑩ 之前**(⑩ 起开仓必落 v1,"有仓无 v1"是走不出去的
    死局,故正常仓一定 ≥1 行)。不 404 —— 列表端点的既定降级契约。"""
    from neckline import positions_entry

    return PositionPlansOut(items=[_shape_plan(r) for r in
                                   positions_entry.list_position_plans(position_id, db_path=_db())])


@app.post(f"{API_PREFIX}/positions/{{position_id}}/plans",
          status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_token)])
def create_plan_version(position_id: int, body: PositionPlanCreateIn) -> PositionPlanOut:
    """用户创建计划新版本(⑩-B)。**新版本不修改原始篮子卡**(单测锁死)。

    ⚠ **武装态由服务端重算,请求体说了不算**(⑪-D-B 闸②):新版本里的 `exit_reference`
    是用户改过的数字,必须拿这笔仓的**真实成交价**重过一遍闸,否则"写个新版本"就成了
    绕开红线闸的后门。用户意图那一半(`exit_reference_muted`)承袭上一版,除非本次
    `plan` 里显式给了该键。

    该持仓无既有计划(缺 `version=1`)→ 400 `no_base_plan`(**全新 reason**,客户端
    `mapReason` 需加 case;它不是「持仓不存在」,而是「这笔仓没有可继承的基线」)。"""
    from neckline import positions_entry

    try:
        positions_entry.create_position_plan_version(
            position_id, dict(body.plan or {}), note=body.note, db_path=_db(),
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"ok": False, "reason": "no_base_plan", "message": str(e)}) from None
    latest = positions_entry.latest_position_plan(position_id, db_path=_db())
    return _shape_plan(latest)


@app.get(f"{API_PREFIX}/positions/{{position_id}}/entry-snapshot",
         dependencies=[Depends(require_token)])
def get_entry_snapshot(position_id: int) -> EntrySnapshotOut:
    """建仓瞬间的冻结快照(⑩-A)。无快照行 → 404 `not_found`(复用既有 reason)。

    ⚠ `snapshot.not_captured` 如实列出**本次没采到**的项(⑩ 范围内:资金流 / 竞价表现 /
    换手率 / 量比四项未采集)—— ⛔ 别把"没采"读成"没有"。"""
    from neckline import positions_entry

    row = positions_entry.load_entry_snapshot(position_id, db_path=_db())
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": "not_found"})
    return EntrySnapshotOut(
        positionId=row["position_id"], tsCode=row["ts_code"], tradeDate=row["trade_date"],
        basketId=row["basket_id"], cardVersion=row["card_version"], tier=row["tier"],
        role=row["role"], snapshot=row["snapshot"], createdAt=row["created_at"],
    )


# —— ⚠ V2.2-⑤-B:`GET /circuit` 与 `POST /circuit/unlock` **两条端点已删**(裁定 #8)——
# 熔断整体退役 = 锁定态 / 次日只减不加 / 强制复盘解锁三件机制全删,故:
#   · `POST /circuit/unlock` 是「解锁」动作,**随机制消失**;
#   · `GET /circuit` **没有替代端点** —— 提醒走推送与看板事件,**不走状态查询**。
# 两条路径自此由 FastAPI 天然返 **404**(⛔ 别加一条返空态的兼容路由:那等于把"已退役"
# 讲成"查得到、恰好没锁",又是一个看不出来的状态位)。
# ✅ **`PositionsOut.circuit` 已于 v2.3.0 删键**(两步淘汰第二步):逐版核实历代客户端
# 都只解 `PositionsListResponse { holdings }`,**从没有一版声明过这个字段**,故删它零风险
# —— 零删键铁律没被破例,是核实之后发现这个键根本没有消费方。
# ⚠ 客户端里那两条活调用由 ⑥ 删,本版先登记进
# `tests/test_contract_crosscheck.py::PENDING_CLIENT_CALLS_TO_BE_REMOVED_IN_15`。


# —— v1.2-B 预注册决策日志(§2.1 第 3 条 / plan §五 v1.2-B)——————————————————
# **v2.0.0(⑩-C)决策日志强制表单退役**:`decision_log` 表停写留档(历史行只读
# 归因,`neckline.decision_log` 不再提供任何写函数)。本节只剩两个**只读**端点
# (`GET /decisions` / `GET /decisions/{id}/track`,§3.8「审计件、非下单件」精神
# 不变);`create_decision` 复用 `POST /decisions` 路径但已换血成蓝图 §2.2/§5.2
# 的「用户可选补充」入口——不再触碰 `decision_log`,改落 `user_actions`。旧的
# `link`/`cancel`/`revise`/`scenario-outcome` 四个端点随写入口一起下线(历史行
# 「不可编辑」的既有铁律现在升级成「完全不可写」,这四个端点存在的唯一理由就是
# 编辑历史行,理由消失、端点随之消失)。

def _shape_decision(row: "decision_log_store.DecisionRow") -> DecisionOut:
    """决策日志领域行 → 客户端契约。同 `_shape_candidate` 的透传惯例。**只读侧
    使用**(GET 装配历史行);v2.0.0 起没有任何写端点会构造 `DecisionRow` 来源于
    新写入,所以这里出现的永远是历史数据。"""
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


@app.post(f"{API_PREFIX}/decisions", dependencies=[Depends(require_token)])
def create_decision(body: DecisionCreateIn) -> DecisionNoteOut:
    """用户可选补充入口(v2.0.0 起,⑩-C 退役重定义;蓝图 §2.2/§5.2)。**全部字段
    可选**——空提交(不传 `labels`/`voiceNote`)同样 200、`recorded=[]`,这正是
    「不传五必填 → 200 而非 400」的落点:旧版此处的九项强制表单 + `maxChasePct`
    二选一强制校验已随 `decision_log` 写入口一起下线,不再有任何理由 400。

    `labels`(蓝图 §2.2 七枚标签)与 `voiceNote`(一句可选语音说明)分别落
    `user_actions` 的 `kind='label'`/`'voice_note'`(⑩-D),**不写 `decision_log`**
    (grep 守门见 `tests/test_decision_log.py`)。`code`/`positionId` 只是挂载点。"""
    recorded: List[str] = []
    if body.labels:
        user_actions.record(
            "label", ts_code=(body.code or None), position_id=body.positionId,
            payload={"labels": list(body.labels)}, db_path=_db(),
        )
        recorded.append("label")
    if body.voiceNote:
        user_actions.record(
            "voice_note", ts_code=(body.code or None), position_id=body.positionId,
            payload={"text": body.voiceNote}, db_path=_db(),
        )
        recorded.append("voice_note")
    return DecisionNoteOut(ok=True, recorded=recorded)


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
                # V2.4.0 P0:退役位随契约下发,客户端据此隐藏开关(⛔ 不硬编黑名单)。
                retired=(k in notify_kinds.RETIRED_KINDS),
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

def _alert_out(a: "custom_alerts.CustomAlert", now_cn: Optional[datetime] = None) -> CustomAlertOut:
    now_cn = now_cn or datetime.now(CN_TZ)
    return CustomAlertOut(
        id=a.id, tsCode=a.ts_code, nlText=a.nl_text, rule=a.rule,
        condition=(custom_alerts.describe_rule(a.rule) if a.rule else ""),
        activeFrom=a.active_from, activeTo=a.active_to, expiresAt=a.expires_at,
        persist=a.persist, cooldownSeconds=a.cooldown_seconds, maxFires=a.max_fires,
        firedCount=a.fired_count, status=a.status,
        expiredNow=custom_alerts.is_expired_at(a, now_cn),
        createdAt=a.created_at, updatedAt=a.updated_at,
    )


def _rule_from_conditions(conds: List[AlertConditionIn], logic: str) -> Dict[str, Any]:
    out: List[Dict[str, Any]] = []
    for c in conds:
        item: Dict[str, Any] = {"metric": c.metric, "op": c.op, "value": c.value}
        if c.ref is not None:
            item["ref"] = c.ref
        if c.refBasketId is not None:
            item["ref_basket_id"] = c.refBasketId
        out.append(item)
    return {"logic": logic, "conditions": out}


@app.get(f"{API_PREFIX}/alerts", dependencies=[Depends(require_token)])
def list_custom_alerts(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    code: Optional[str] = None,
) -> AlertsListOut:
    """列临时提醒。查询参数 `status` ∈ active|expired|cancelled(缺省 = 全部)。

    ⚠ **V2-⑭-B 契约修正(契约线 🔵 B7)**:此前查询参数名是 Python 形参名
    `status_filter` 直接漏成契约键 —— 与全仓 camelCase 取向不合,且 `status` 才是
    客户端会猜的那个名字。改用 `Query(alias="status")`:形参名仍叫 `status_filter`
    (`status` 会遮住模块级 `fastapi.status`),对外键改成 `status`。
    ⚠ **这是一次真的破坏性改名**,但 D2=A 路下老 App 打老机、不会撞到本服务端,
    且 `/alerts` 五端点由 ⑪-C 新建、**至今零客户端调用方**(⑮ 才接线),现在改成本最低。
    **只读**:到期但尚未被哨兵翻状态的行照实回 `status='active'` + `expiredNow=true`,
    不在读路径偷偷改库。"""
    rows = custom_alerts.list_alerts(status=status_filter, ts_code=code, db_path=_db())
    now_cn = datetime.now(CN_TZ)
    return AlertsListOut(items=[_alert_out(a, now_cn) for a in rows])


@app.post(f"{API_PREFIX}/alerts", status_code=status.HTTP_201_CREATED,
          dependencies=[Depends(require_token)])
def create_custom_alert(body: AlertCreateIn) -> CustomAlertOut:
    """建一条提醒(用户已确认)。规则不合白名单 → 422 `invalid_rule`(消息可读、
    原样展示给用户);已有一条**同标的 + 规则逐字节相同**的 active 提醒 → 409
    `duplicate_alert`(蓝图 5.6 安全要求 1「相同提醒去重」)。"""
    rule = _rule_from_conditions(body.conditions, body.logic)
    try:
        dup = custom_alerts.find_duplicate(rule, body.tsCode, db_path=_db())
    except custom_alerts.RuleValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_rule", "message": str(e)})
    if dup is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                             detail={"ok": False, "reason": "duplicate_alert", "alertId": dup.id})
    try:
        created = custom_alerts.create_alert(
            rule=rule, nl_text=body.nlText, ts_code=body.tsCode,
            active_from=body.activeFrom, active_to=body.activeTo, expires_at=body.expiresAt,
            persist=body.persist, cooldown_seconds=body.cooldownSeconds,
            max_fires=body.maxFires, db_path=_db(),
        )
    except custom_alerts.RuleValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_rule", "message": str(e)})
    # ⑩-D 五类用户行为之一(`alert`):建提醒是用户行为,落 append-only 台账。
    try:
        user_actions.record(
            "alert", ts_code=created.ts_code,
            payload={"alertId": created.id, "rule": created.rule, "nlText": created.nl_text},
            db_path=_db(),
        )
    except Exception:  # noqa: BLE001  记账失败不该让用户的提醒建不成
        logger.warning("记录 user_actions(alert)失败,提醒本身已建立", exc_info=True)
    return _alert_out(created)


@app.put(f"{API_PREFIX}/alerts/{{alert_id}}", dependencies=[Depends(require_token)])
def update_custom_alert(alert_id: int, body: AlertUpdateIn) -> CustomAlertOut:
    """局部更新一条提醒(未出现的字段不改)。不存在 → 404 `not_found`;规则不合
    白名单 → 422 `invalid_rule`。"""
    fields = body.model_fields_set
    rule = None
    if body.conditions is not None:
        rule = _rule_from_conditions(body.conditions, body.logic or custom_alerts.LOGIC_ALL)
    try:
        updated = custom_alerts.update_alert(
            alert_id, rule=rule,
            nl_text=body.nlText if "nlText" in fields else None,
            active_from=body.activeFrom if "activeFrom" in fields else None,
            active_to=body.activeTo if "activeTo" in fields else None,
            expires_at=body.expiresAt if "expiresAt" in fields else None,
            persist=body.persist if "persist" in fields else None,
            cooldown_seconds=body.cooldownSeconds if "cooldownSeconds" in fields else None,
            max_fires=body.maxFires if "maxFires" in fields else None,
            reset_fired=body.resetFired, db_path=_db(),
        )
    except custom_alerts.RuleValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                             detail={"ok": False, "reason": "invalid_rule", "message": str(e)})
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail={"ok": False, "reason": "not_found"})
    return _alert_out(updated)


@app.delete(f"{API_PREFIX}/alerts/{{alert_id}}", dependencies=[Depends(require_token)])
def cancel_custom_alert(alert_id: int) -> OkOut:
    """取消一条提醒(**改状态,不删行**)。不存在 → 404 `not_found`。"""
    if custom_alerts.get_alert(alert_id, db_path=_db()) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                             detail={"ok": False, "reason": "not_found"})
    custom_alerts.cancel_alert(alert_id, db_path=_db())
    return OkOut(ok=True)


@app.post(f"{API_PREFIX}/alerts/parse", dependencies=[Depends(require_token)])
def parse_custom_alert(body: AlertParseIn) -> AlertParseOut:
    """自然语言 → 结构化规则 + **七项确认卡**(⑪-C)。**本端点不写库**。

    **恒返回 200**(交互式接口):LLM 不可用 → `degraded=true` + `manualForm`(手填
    表单字段表),解析不出规则 → `ok=false` + 可读 `reason`。⛔ 不静默失败、也不
    用 4xx 把失败包装成"客户端的错"。"""
    from neckline.llm import nl_alert as nl

    existing = [
        {"id": a.id, "ts_code": a.ts_code, "condition": custom_alerts.describe_rule(a.rule)}
        for a in custom_alerts.list_alerts(status=custom_alerts.STATUS_ACTIVE, db_path=_db())
    ]
    parsed = nl.parse_nl_alert(body.text, ts_code_hint=body.tsCode, existing=existing, db_path=_db())
    out = AlertParseOut(
        ok=parsed.ok, action=parsed.action, reason=parsed.reason, narrative=parsed.narrative,
        degraded=parsed.degraded, manualForm=parsed.manual_form,
        targetAlertId=parsed.target_alert_id,
    )
    if parsed.ok and parsed.action == nl.ACTION_QUERY:
        now_cn = datetime.now(CN_TZ)
        rows = custom_alerts.list_alerts(
            status=custom_alerts.STATUS_ACTIVE, ts_code=parsed.ts_code, db_path=_db()
        )
        out.matches = [_alert_out(a, now_cn) for a in rows]
        return out
    card = nl.confirmation_card_for(parsed)
    if card is not None:
        out.confirmationCard = ConfirmationCardOut(
            subject=card.subject, condition=card.condition, activeWindow=card.active_window,
            notifyLimit=card.notify_limit, expiry=card.expiry,
            quoteDelayDisclosure=card.quote_delay_disclosure, noAutoTrade=card.no_auto_trade,
            rule=card.rule,
        )
        out.draft = AlertCreateIn(
            tsCode=parsed.ts_code, nlText=body.text,
            conditions=[AlertConditionIn(
                metric=c["metric"], op=c["op"], value=c["value"],
                ref=c.get("ref"), refBasketId=c.get("ref_basket_id"),
            ) for c in (parsed.rule or {}).get("conditions", [])],
            logic=(parsed.rule or {}).get("logic", custom_alerts.LOGIC_ALL),
            activeFrom=parsed.active_from, activeTo=parsed.active_to,
            expiresAt=parsed.expires_at, persist=parsed.persist,
            cooldownSeconds=parsed.cooldown_seconds, maxFires=parsed.max_fires,
        )
    return out


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

def _profile_out(as_of: str, rows: List[Dict[str, Any]]) -> ProfileOut:
    """空 `as_of` = **该期从未算过**(不是"算出来是空的");有 `as_of` 无行 = 算过了、
    这一期没有够样本的维度。⛔ 两者在界面上必须讲不同的话。"""
    if not as_of:
        return ProfileOut(asOf="", available=False,
                          unavailableReason="该期画像尚未生成(周度批算未运行)。")
    return ProfileOut(asOf=as_of, available=True, items=rows)


@app.get(f"{API_PREFIX}/profile/preference", dependencies=[Depends(require_token)])
def get_profile_preference(asOf: str = "") -> ProfileOut:
    """偏好画像:「**喜欢什么**」(常买题材 / 角色 / 入场方式 / 常选 Tier)。

    每行带 `sampleN` / `windowStart` / `windowEnd` / `confidence`。
    ⚠ `confidence='low'`(样本不足)时客户端**必须**显式写「样本不足,不给结论」——
    **单笔不成偏好**(⑫-B 硬要求)。`asOf` 缺省 = 最近一期。"""
    from neckline.profile.store import latest_as_of, load_preference

    day = asOf or (latest_as_of("preference", db_path=_db()) or "")
    return _profile_out(day, load_preference(day, _db()) if day else [])


@app.get(f"{API_PREFIX}/profile/capability", dependencies=[Depends(require_token)])
def get_profile_capability(asOf: str = "") -> ProfileOut:
    """能力画像:「**什么真有效**」(胜率 / 盈亏比 / 是否跑赢同篮未选股票)。

    ⚠ **与偏好画像是两张账,⛔ 不合并**:一个说"你喜欢什么",一个说"你哪些选择真的
    赚到钱" —— 合成一张就等于用喜好给能力背书。`vsPeerDelta=null` = 配对样本不足,
    **不是"没有差异"**。`asOf` 缺省 = 最近一期。"""
    from neckline.profile.store import latest_as_of, load_capability

    day = asOf or (latest_as_of("capability", db_path=_db()) or "")
    return _profile_out(day, load_capability(day, _db()) if day else [])


@app.get(f"{API_PREFIX}/packs", dependencies=[Depends(require_token)])
def list_selection_packs() -> PacksListOut:
    """选股策略包清单(append-only + 单现役)。

    ⚠ **策略包与纪律章程是两条版本线、两张表、两套激活流程,永不混用**(§五 红线 6)。
    本端点**只读**:激活走 `scripts/activate_pack.py` 的四道闸(**绝不暴露给客户端**,
    同「大脑激活绝不走 API」的既定纪律)。"""
    from neckline.selection.pack import list_packs

    return PacksListOut(items=[_pack_out(p) for p in list_packs(_db())])


@app.get(f"{API_PREFIX}/packs/{{pack_version}}", dependencies=[Depends(require_token)])
def get_selection_pack(pack_version: str) -> PackOut:
    """单个策略包(含 manifest 与 config 全文)。不存在 → 404 `not_found`。"""
    from neckline.selection.pack import get_pack

    p = get_pack(pack_version, _db())
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": "not_found"})
    return _pack_out(p)


def _pack_out(p) -> PackOut:
    return PackOut(
        packVersion=p.pack_version, isActive=bool(p.is_active),
        createdAt=getattr(p, "created_at", "") or "",
        activatedAt=getattr(p, "activated_at", None),
        manifest=dict(getattr(p, "manifest", {}) or {}),
        config=dict(getattr(p, "config", {}) or {}),
    )


@app.get(f"{API_PREFIX}/eval/weekly", dependencies=[Depends(require_token)])
def get_eval_weekly(week: str = "") -> EvalWeeklyOut:
    """周度评价校准报告(⑨-C,含安慰剂对照臂)。`week` = 该周任意一天 'YYYYMMDD',
    缺省 = 本周。

    🔴 **V2.2-④ 起改为「读周度 unit 落盘的产物」,⛔ 不再在线现算**(§七 **P4-46**
    在本块结案)。理由两条,都不是偏好:
      ① 归因分层键从 2 个扩到 4 个(骨架 × 引擎 × 版本 × 条件集),现算成本必然上升,
         P4-46 原文的触发条件「生产实测 > 5s」大概率当场成立;
      ② 本端点跑在常驻 `neckline.service` 里、**与盘中哨兵同进程** —— §七 **P0-23**
         原教旨:重活进常驻服务 = `MemoryHigh` 先节流 → 进程陷进回收死循环 =
         **卡死不报错**,盘中点一次就拖累哨兵。
    ⛔ **查不到不许现算自愈**,如实降级(与 `/review/handoff` 完全同一条纪律):
      · 产物不在   → `available=false`,原因写明**会自愈**(等下一次周度作业);
      · 产物读不出 → `available=false`,原因写明**不会自愈**、要人排查。
        两句话必须分开 —— 合成一句就是叫人一直等一份永远好不了的产物。

    ⚠ **评价是长期统计,不是单日打分**:样本窗未就绪时如实说,⛔ 不拿半截样本给结论。
    """
    from neckline.review.research_artifact import week_bounds
    from neckline.review.handoff import (
        CAL_CORRUPT, CAL_OK, load_calibration_markdown, load_calibration_with_status,
    )

    try:
        anchor = (datetime.strptime(week, "%Y%m%d").date()
                  if (len(week) == 8 and week.isdigit()) else date_cls.today())
        start, end = week_bounds(anchor)
        if start is None or end is None:
            return EvalWeeklyOut(
                available=False,
                unavailableReason="这一周没有交易日,没有可校准的窗口。")
        lo, hi = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        out_dir = _calibration_dir()
        payload, status = load_calibration_with_status(lo, hi, out_dir)
        if status == CAL_CORRUPT:
            return EvalWeeklyOut(
                weekStart=lo, weekEnd=hi, available=False,
                unavailableReason=(
                    f"本窗口({lo}→{hi})的周度校准产物**读不出**(文件在、JSON 解析失败)。"
                    f"它是落盘产物、**不会自己好** —— 需人工排查,⛔ 别当成「还没生成」等下去。"
                ))
        if status != CAL_OK or payload is None:
            return EvalWeeklyOut(
                weekStart=lo, weekEnd=hi, available=False,
                unavailableReason=(
                    f"本窗口({lo}→{hi})尚无周度校准产物 —— 周度作业("
                    f"whynotme 离线周任务还没跑到这个窗口。"
                    f"**会自愈**:下一次周度作业跑完即有。⛔ 在线路径不补算(§七 P0-23)。"
                ))
        return EvalWeeklyOut(
            weekStart=lo, weekEnd=hi, available=True, result=dict(payload),
            markdown=(load_calibration_markdown(lo, hi, out_dir) or ""),
        )
    except Exception as exc:  # noqa: BLE001  评价报告是审计件,炸了如实说,不 500
        logger.warning("[eval] 周度校准产物读取异常(已降级为不可得)", exc_info=True)
        return EvalWeeklyOut(available=False,
                             unavailableReason=f"周度评价产物本次读取失败:{type(exc).__name__}")


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


# —— V2.2-② 行情状态层:只读端点 ————————————————————————————————————————

def _regime_row_out(row: Dict[str, Any]) -> MarketRegimeDayOut:
    from neckline.scan.regime import REGIME_LABELS

    return MarketRegimeDayOut(
        tradeDate=row["trade_date"],
        regime=row["regime"],
        regimeLabel=REGIME_LABELS.get(row["regime"], row["regime"]),
        regimeReason=row["regime_reason"],
        inputs=row["inputs"],
        strengthening=row["strengthening"],
        weakening=row["weakening"],
        skeletonVersion=row["skeleton_version"],
        computedAt=row["computed_at"],
    )


def _parse_yyyymmdd(s: str) -> Optional[date_cls]:
    if len(s) == 8 and s.isdigit():
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    return None


@app.get(f"{API_PREFIX}/market-regime", dependencies=[Depends(require_token)])
def market_regime(
    date: str = "",
    date_from: str = Query(default="", alias="from"),
    date_to: str = Query(default="", alias="to"),
) -> MarketRegimeOut:
    """行情状态 D0 盘后三态(V2.2-②,`market_regime_daily` 只读)。`date` 缺省 =
    表内最近一日;`from`/`to` 给出时走区间(⚠ `from` 是 Python 关键字,形参
    `date_from` + `Query(alias="from")`,同 `/review/handoff` 既有姿势)。

    🔴 三条硬边界(体例照 `/review` 一族):**零现算**(判定 16:35 落表,本端点
    只 SELECT —— 常驻服务与盘中哨兵同进程,P0-23);**零写库**;**一律不 404**
    (缺行/表空/参数非法一律 200 + `available=false` + 自由文本原因)——
    **零新增 reason 字符串**,`SERVER_REASONS` 与客户端 `mapReason` 一字不动。"""
    from neckline.scan import regime_store

    if date_from or date_to:
        lo = _parse_yyyymmdd(date_from) if date_from else None
        hi = _parse_yyyymmdd(date_to) if date_to else None
        if (date_from and lo is None) or (date_to and hi is None):
            return MarketRegimeOut(
                available=False,
                unavailableReason="from/to 参数格式非法(应为 YYYYMMDD),本次未取数。",
            )
        lo = lo or hi
        hi = hi or lo
        rows = regime_store.load_market_regime_range(lo, hi, db_path=_db())
        if not rows:
            return MarketRegimeOut(
                available=False,
                unavailableReason=(
                    f"{lo.strftime('%Y%m%d')}~{hi.strftime('%Y%m%d')} 区间无行情状态判定行"
                    "(D0 盘后批算未跑,或区间内无交易日)。缺行 = 不知道,不猜。"
                ),
            )
        return MarketRegimeOut(available=True, days=[_regime_row_out(r) for r in rows])

    if date:
        d = _parse_yyyymmdd(date)
        if d is None:
            return MarketRegimeOut(
                available=False,
                unavailableReason="date 参数格式非法(应为 YYYYMMDD),本次未取数。",
            )
        row = regime_store.load_market_regime(d, db_path=_db())
        if row is None:
            return MarketRegimeOut(
                available=False,
                unavailableReason=(
                    f"{date} 无行情状态判定行(D0 盘后批算未跑或该日非交易日)。"
                    "缺行 = 不知道,不猜。"
                ),
            )
        return MarketRegimeOut(available=True, day=_regime_row_out(row))

    row = regime_store.load_latest_market_regime(db_path=_db())
    if row is None:
        return MarketRegimeOut(
            available=False,
            unavailableReason="行情状态层尚无任何判定行(D0 盘后批算还没跑过)。",
        )
    return MarketRegimeOut(available=True, day=_regime_row_out(row))


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
REASON_AUCTION_NOT_READY = "auction_not_ready"
REASON_AUCTION_CORRUPT = "auction_corrupt"

#: 市场级报告的**内容键**:判"读不出"用「**有其一**」而不是「都要有」
#: (CLAUDE.md B1 定案:各消费方吃不同键子集,且误判代价不对称 —— 判错 = 好数据看不到
#: 且不可自愈)。
_AUCTION_REPORT_CONTENT_KEYS = ("index_gaps_json", "market_anchors_json", "risks_json",
                               "missing_codes_json", "conflict_codes_json", "notes_json")


def _auction_llm_unavailable_reason(llm_stage: str) -> str:
    """LLM 段缺席时的**一句人话**(⛔ 不把 `llm_stage` 这个枚举码直接当文案印出去)。

    ⚠ 「9:29 到了模型还没回」是**设计内**的(硬截止是结构性保证,不是故障)——
    文案必须这么说,⛔ 别写成"出错了"。"""
    if llm_stage.startswith("call_failed"):
        return f"本次 LLM 调用失败({llm_stage}),竞价结论全部按『待解释』记录。"
    return {
        "pending": "机械段已落库,LLM 解释还没回来(本次报告尚未结案)。",
        "pending_explanation": "9:29 硬截止到达时 LLM 还没回,本次没有市场概览"
                               "(设计内:迟到的结论一律丢弃,⛔ 不拿 9:30 之后的话冒充 9:29)。",
        "provider_none": "未配置可用的 LLM provider,本次只有机械层的数据报告与失效警报。",
        "parse_failed": "LLM 输出里没有可解析的结构化段,本次结论全部按『待解释』记录。",
        "budget_exhausted": "推理预算账已耗尽,本次没有发起竞价解释调用。",
        "ok": "本次 LLM 没有给出市场概览这一段。",
    }.get(llm_stage, f"本次没有市场概览(LLM 段状态:{llm_stage or '未记录'})。")


def _shape_auction_member(m: Any) -> AuctionMemberRowOut:
    """`members_json` 一条 → 契约(snake→camel 的**唯一转换点**)。

    🔴 `hitInvalidation` / `gapUpDeviation` 的 `None` 原样保留 —— 它是「**没判**」
    (锚失效 / 卡上无冻结价位 / 开盘价未发布 / 有篮无卡 / 没抓到),⛔ 不许折成
    `False`「没问题」。⚠ 两个 `*UndeterminedReason` 是那个 `None` 的**可查原因码**,
    一并原样透传;老行没有这两个键 → `None`(客户端说「原因未记录」,仍不说「无异常」)。"""
    d = dict(m or {})
    return AuctionMemberRowOut(
        tsCode=str(d.get("ts_code") or ""), name=str(d.get("name") or ""),
        role=d.get("role"),
        auctionPrice=d.get("auction_price"), preClose=d.get("pre_close"),
        gapPct=d.get("gap_pct"), auctionVolume=d.get("auction_volume"),
        auctionAmount=d.get("auction_amount"), volVsPrev5Frac=d.get("vol_vs_prev5_frac"),
        relToSector=d.get("rel_to_sector"), relToIndex=d.get("rel_to_index"),
        # 🔴 裁定 P3-70:两个读数各自带上「减的是哪一支 / 哪一组」与「没有时为什么」;
        # 老行(整改前冻的 `members_json`)没有这些键 → 缺省 `unavailable` / `None`,
        # 客户端照实说「原因未记录」,⛔ 仍不许渲染成 0 或「持平」。
        relToSectorSource=str(d.get("rel_to_sector_source") or "unavailable"),
        relToSectorReason=d.get("rel_to_sector_reason"),
        sectorPeerCodes=[str(c) for c in (d.get("sector_peer_codes") or [])],
        sectorIndexCode=d.get("sector_index_code"),
        sectorBenchmarkGapPct=d.get("sector_benchmark_gap_pct"),
        industry=d.get("industry"),
        indexBenchmarkCode=d.get("index_benchmark_code"),
        indexBenchmarkGapPct=d.get("index_benchmark_gap_pct"),
        relToIndexReason=d.get("rel_to_index_reason"),
        hitInvalidation=d.get("hit_invalidation"), gapUpDeviation=d.get("gap_up_deviation"),
        hitInvalidationUndeterminedReason=d.get("hit_invalidation_undetermined_reason"),
        gapUpDeviationUndeterminedReason=d.get("gap_up_deviation_undetermined_reason"),
        anchorStale=bool(d.get("anchor_stale")),
        planFit=str(d.get("plan_fit") or "unknown"),
        dataQuality=str(d.get("data_quality") or "insufficient"),
        volumeNote=d.get("volume_note"),
        # 🔴 V2.4.0 P2.1/P2.2:老快照(V2.4.0 之前冻的 `members_json`)没有这几个键
        # → 空串 / `None`,客户端照实说「本次没记这一位」,⛔ 不许渲染成「校验通过」。
        quoteFreshness=str(d.get("quote_freshness") or ""),
        quoteStatus=str(d.get("quote_status") or ""),
        quoteSource=d.get("quote_source"),
        quoteTimestamp=d.get("quote_ts"),
        sourceDegraded=bool(d.get("source_degraded")),
        sourceConflict=d.get("source_conflict"),
        validationErrors=[str(e) for e in (d.get("validation_errors") or [])],
    )


#: 篮子级的 json 列;某一列**读不出**时该段退化成空 + 在 `notes` 里如实点名。
_AUCTION_VERDICT_JSON_COLS = ("members_json", "sector_sync_json", "rel_strength_json",
                              "history_json", "hit_invalidation_json", "plan_consistency_json",
                              "reasons_json", "llm_fields_json", "quality_detail_json")

#: 数据质量三态的**坏度序**(`ok < degraded < insufficient`)。⚠ 只在这里排一次序,
#: 领域层的同款在 `auction/quality.py::worse_of` —— 两处由守门单测对拍。
_DQ_RANK = {"ok": 0, "degraded": 1, "insufficient": 2}


def _worst_quality(values: Sequence[Any]) -> Optional[str]:
    """一组分域质量取**最差**的那一个;全为 `None`(老行 / 没有篮子)→ `None`。

    🔴 `None` **不许被当成 `ok`**(施工图 §五 P2.3:旧报告没有这些字段时显示
    「旧版本未细分」,⛔ 不得默认成正常)。
    """
    vals = [str(v) for v in values if v]
    if not vals:
        return None
    return max(vals, key=lambda v: _DQ_RANK.get(v, 2))


def _shape_auction_quality_details(payload: Any) -> List[AuctionQualityDetailOut]:
    """`auction_reports.quote_quality_json` → 契约(snake→camel 的**唯一转换点**)。

    ⚠ 老报告该列是 NULL → 空数组(客户端据此说「旧版本未细分」);
    ⛔ 不许因为"空"就把分域质量默认成正常。
    """
    if not isinstance(payload, Mapping):
        return []
    out: List[AuctionQualityDetailOut] = []
    for code in sorted(payload.keys()):
        d = payload.get(code)
        if not isinstance(d, Mapping):
            continue
        checks = [
            AuctionQuoteCheckOut(
                role=str(c.get("role") or ""), source=str(c.get("source") or ""),
                status=str(c.get("status") or ""),
                errors=[str(e) for e in (c.get("errors") or [])],
                tsRaw=str(c.get("ts_raw") or ""), tsParsed=c.get("ts_parsed"),
                price=c.get("price"), preClose=c.get("pre_close"), open=c.get("open"),
                volume=c.get("volume"), amount=c.get("amount"),
            )
            for c in (d.get("checks") or []) if isinstance(c, Mapping)
        ]
        out.append(AuctionQualityDetailOut(
            tsCode=str(d.get("ts_code") or code), freshness=str(d.get("freshness") or ""),
            status=str(d.get("status") or ""), chosenRole=d.get("chosen_role"),
            chosenSource=d.get("chosen_source"),
            sourceDegraded=bool(d.get("source_degraded")), conflict=d.get("conflict"),
            # 🔴 复审 🔴-2:落库当时算好的那一位,**原样透传**。
            # ⚠ 老行没有这一键 → `False`(保守:不声称核验过),
            #   ⛔ 不在这里用 `checks` 重新推一遍 —— 那就是第二份判别式。
            crossVerified=bool(d.get("cross_verified")),
            errors=[str(e) for e in (d.get("errors") or [])], checks=checks,
        ))
    return out


def _shape_auction_verdict(row: Dict[str, Any]) -> AuctionVerdictOut:
    return AuctionVerdictOut(
        basketId=int(row.get("basket_id") or 0),
        basketKey=str(row.get("basket_key") or ""), name=str(row.get("name") or ""),
        coveredTier=int(row.get("covered_tier") or 0),
        engineCode=row.get("engine_code"), engineVersion=row.get("engine_version"),
        skeletonVersion=str(row.get("skeleton_version") or ""),
        regimeAtD0=row.get("regime_at_d0"),
        dataQuality=str(row.get("data_quality") or "insufficient"),
        # 🔴 V2.4.0 P2.3:两列**原样透传**,`None` = 旧行(V2.3.3 及更早)没有分域
        # 概念 → 客户端显示「旧版本未细分」,⛔ 不得默认成正常。
        criticalDataQuality=row.get("critical_data_quality"),
        contextDataQuality=row.get("context_data_quality"),
        qualityDetail=dict(row.get("quality_detail_json") or {}),
        verdict=str(row.get("verdict") or "pending_explanation"),
        verdictRaw=row.get("verdict_raw"), clampedBy=row.get("clamped_by"),
        reasons=[str(r) for r in (row.get("reasons_json") or [])],
        members=[_shape_auction_member(m) for m in (row.get("members_json") or [])],
        sectorSync=dict(row.get("sector_sync_json") or {}),
        relStrength=dict(row.get("rel_strength_json") or {}),
        history=dict(row.get("history_json") or {}),
        planConsistency=dict(row.get("plan_consistency_json") or {}),
        hitInvalidation=[str(c) for c in (row.get("hit_invalidation_json") or [])],
        manualNoteAttached=bool(row.get("manual_note_attached")),
        llmStage=str(row.get("llm_stage") or ""),
    )


@app.get(f"{API_PREFIX}/auction", dependencies=[Depends(require_token)])
def get_auction(date: str = "") -> AuctionOut:
    """D1 集合竞价确认层的**竞价小报告五块**(V2.3.3-⑤,K8.md §二十)。

    `date` 缺省 = 今天(D1)。**三态**见上方常量块:无行 404 `auction_not_ready` /
    读不出 500 `auction_corrupt` / 有行 200(**含 `baskets_covered=0`**)。

    🔴 **竞价结论只说明竞价反映出的信息,不等于买入指令**(K8 §二十 逐字)——
    本端点纯只读,⛔ 不接任何交易动作。
    """
    from neckline.auction import AUCTION_MANUAL_NOTE, AUCTION_PROXY_SAMPLE_NOTE
    from neckline.auction import store as auction_store

    day = date if (len(date) == 8 and date.isdigit()) else date_cls.today().strftime("%Y%m%d")
    row = auction_store.load_report(day, db_path=_db())
    if row is None:
        # 「当日无行」= 竞价层**没跑过**(还没到 9:26 / 非交易日 / 服务当时没起)。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": REASON_AUCTION_NOT_READY})
    corrupt_cols = list(row.get("_corrupt_columns") or [])
    content_all_missing = all(row.get(k) is None for k in _AUCTION_REPORT_CONTENT_KEYS)
    if corrupt_cols or content_all_missing:
        logger.error("[auction] 当日竞价报告读不出(date=%s,坏列=%s,内容键全缺=%s)",
                     day, corrupt_cols, content_all_missing)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail={"ok": False, "reason": REASON_AUCTION_CORRUPT})

    notes = [str(n) for n in (row.get("notes_json") or [])]
    verdict_rows = auction_store.load_verdicts(day, db_path=_db())
    for vr in verdict_rows:
        bad = [c for c in (vr.get("_corrupt_columns") or []) if c in _AUCTION_VERDICT_JSON_COLS]
        if bad:
            # ⚠ **一篮读不出不升级成整份 500**(同 `_shape_basket` 对坏卡的既有取舍):
            # 市场段与其余篮子都是好数据,500 会把它们一起藏起来。但**必须当面点名**
            # ——⛔ 不许静默退化成空段(那才是把"读不出"讲成"本来就没有")。
            notes.append(f"篮子 {vr.get('basket_key') or vr.get('basket_id')} 的"
                         f"{'、'.join(bad)} 读不出,该段本次为空(需要排查,⛔ 不是"
                         f"「本来就没有」)。")

    llm_stage = str(row.get("llm_stage") or "")
    overview_text = row.get("market_overview")
    market = AuctionMarketOverviewOut(
        indexGaps=[AuctionIndexGapOut(tsCode=str(v.get("ts_code") or c),
                                      name=str(v.get("name") or ""), gapPct=v.get("gap_pct"))
                   for c, v in (row.get("index_gaps_json") or {}).items()],
        anchors=[AuctionIndexGapOut(tsCode=str(a.get("ts_code") or ""),
                                    name=str(a.get("name") or ""), gapPct=a.get("gap_pct"))
                 for a in (row.get("market_anchors_json") or [])],
        text=overview_text,
        # 🔴 `text=nil` 必须配一句原因(⛔ 不冒充"没内容")。
        textUnavailableReason=(None if overview_text
                               else _auction_llm_unavailable_reason(llm_stage)),
        # 🟡-1:LLM 对「市场锚点」那批标的的一句解释(K8 §二十:锚点只解释资金方向、
        # 不取得交易资格)。⚠ 它落在 `auction_reports.anchors_note` 这一列 ——
        # ⛔ 别再让它「模型写了、解析了、然后整条丢掉」。
        anchorsNote=row.get("anchors_note"),
    )
    baskets = [_shape_auction_verdict(vr) for vr in verdict_rows]
    # 🔴 V2.4.0 P2.1/P2.2/P2.3:数据状态块的分域质量与逐票核验账。
    # ⚠ **两个 `None` 的含义刻意不同**:老报告(V2.3.3 及更早)整份没有分域列 →
    #   客户端说「旧版本未细分」;新报告但当日零篮子 → 关键域无从谈起。两者都
    #   ⛔ 不得默认成正常;区分靠 `qualityDetails` 有没有内容(老报告恒空)。
    quality_details = _shape_auction_quality_details(row.get("quote_quality_json"))
    invalid_codes = sorted({d.tsCode for d in quality_details
                            if d.freshness == "insufficient" and d.checks})
    validation_errors: List[str] = []
    for d in quality_details:
        for e in d.errors:
            if e not in validation_errors:
                validation_errors.append(e)
    critical_dq = _worst_quality([vr.get("critical_data_quality") for vr in verdict_rows])
    context_dq = _worst_quality([vr.get("context_data_quality") for vr in verdict_rows])
    covered = int(row.get("baskets_covered") or 0)
    reason: Optional[str] = None
    if not baskets:
        reason = (
            f"跑过了,但 D0({row.get('d0_date') or '未记录'})当天没有 T1/T2 篮子 —— "
            f"本次没有可验证的交易假设(⛔ 不是竞价层没跑:没跑是 404)。"
            if covered == 0 else
            f"当日报告记着 {covered} 篮,但篮子级明细一行都读不到(需要排查)。"
        )
    return AuctionOut(
        tradeDate=str(row.get("trade_date") or day),
        d0Date=str(row.get("d0_date") or ""),
        dataStatus=AuctionDataStatusOut(
            source=str(row.get("source") or "unknown"),
            capturedAt=str(row.get("captured_at") or ""),
            requestedCodes=int(row.get("requested_codes") or 0),
            fetchedCodes=int(row.get("fetched_codes") or 0),
            missingCodes=[str(c) for c in (row.get("missing_codes_json") or [])],
            invalidCodes=invalid_codes,
            conflictCodes=[str(c) for c in (row.get("conflict_codes_json") or [])],
            dataQuality=str(row.get("data_quality") or "insufficient"),
            # 🔴 V2.4.0 P2.3:报告级分域质量 = **各篮子取更差的那个**;
            # `None` = 老报告没有分域概念 **或** 本次没有篮子(关键域无从谈起)——
            # 两者都由客户端说成「旧版本未细分」/「本次没有篮子」,⛔ 不得默认成正常。
            criticalDataQuality=critical_dq,
            contextDataQuality=context_dq,
            qualityDetails=quality_details,
            validationErrors=validation_errors,
        ),
        marketOverview=market,
        baskets=baskets,
        basketsUnavailableReason=reason,
        risks=[AuctionRiskOut(kind=str(r.get("kind") or ""), text=str(r.get("text") or ""))
               for r in (row.get("risks_json") or []) if isinstance(r, dict)],
        # 小纸条:**挂了才发**,文案本体是服务端常量(K8 §二十 逐字)——
        # ⛔ 客户端不许自己写这段字(同 `BASKET_CARD_DISCLAIMER` 既有体例)。
        manualNote=(AUCTION_MANUAL_NOTE if row.get("manual_note_attached") else None),
        # 🔴 **恒发**:竞价强势股只在竞价观察池内排序,不是全市场竞价排行(§五 ⑨-B-2)。
        proxySampleNote=AUCTION_PROXY_SAMPLE_NOTE,
        # 🔴 裁定 ①:**当天那一份**观察范围的自述。空串 = v2.4.0 之前落的报告
        # (那时没有独立观察池)—— ⛔ 客户端不许读成「范围正常」。
        observationScopeNote=str((row.get("observation_json") or {}).get("scope_note") or ""),
        llmStage=llm_stage,
        notes=notes,
    )


# —— V2.1-⑤ 复盘板块:聚合读 + 校准移交件 ————————————————————————————————
#
# 🔴 **两条端点的三条硬边界**(⛔ 施工时别改主意,守门在 `tests/test_review_handoff.py`
# 与 `tests/test_api_review.py`):
#   ① **零现算**:只读已冻结 / 已落盘的产物。它们跑在常驻 `neckline.service` 里、
#      **与盘中哨兵同进程** —— §七 P0-23 的原教旨:重活进常驻服务 = `MemoryHigh`
#      先节流 → 卡死不报错,盘中点一次就拖累哨兵。⛔ 永不调 `calibration.build_report`
#      (静态 AST + 运行期双向守门)。
#   ② **零写库**(纯 GET,一行都不写)。
#   ③ **一律不 404**:空态走 `available=false` → V2.1 **零新增 reason 字符串**,
#      `SERVER_REASONS` 与客户端 `mapReason` 一字不动。

def _week_anchor(week: str) -> date_cls:
    """`week` = 该周任意一天 `YYYYMMDD`;非法 / 缺省 → 今天(同 `/eval/weekly` 惯例,
    **降级不 4xx**)。"""
    if len(week) == 8 and week.isdigit():
        try:
            return datetime.strptime(week, "%Y%m%d").date()
        except ValueError:
            pass
    return date_cls.today()


def _calibration_segment(lo, hi) -> ReviewSegmentOut:
    """校准段:**只读离线产物**,三态分开说话(`ok` / 没生成 / 读不出)。

    🔴 **包成绩单 = 产物里的 `strata` 本身**(已按 `pack_version × verification_ruleset_version`
    分层)——⛔ 不在这里另建第二份聚合,那就是"同一个数两个算法"的老病。"""
    from neckline.review import handoff as ho

    label = "包成绩单 · 周度校准"
    if lo is None or hi is None:
        return ReviewSegmentOut(available=False, label=label,
                                unavailableReason="该周没有交易日,本周无校准窗口。")
    lo_s, hi_s = lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d")
    out_dir = _calibration_dir()
    payload, status = ho.load_calibration_with_status(lo_s, hi_s, out_dir)
    if status == ho.CAL_OK:
        return ReviewSegmentOut(available=True, label=label, asOf=f"{lo_s}→{hi_s}",
                                detail=dict(payload or {}))
    latest = ho.list_calibration_artifacts(out_dir)
    detail = {"latestAvailable": latest[0].label} if latest else {}
    if status == ho.CAL_CORRUPT:
        # ⛔ 不降级成"还没生成":文件在那儿、不会自己好,是要人排查的事故。
        reason = (f"本窗口({lo_s}→{hi_s})的周度校准产物**读不出**"
                  f"(文件在、JSON 解析失败)—— 它不会自愈,需人工排查。")
    else:
        reason = (f"本窗口({lo_s}→{hi_s})的周度校准产物尚未生成 —— 周度作业按周离线"
                  f"落盘,在线路径只读产物、**不补算**。等下一次周度作业跑完即有。")
    return ReviewSegmentOut(available=False, label=label, asOf=f"{lo_s}→{hi_s}",
                            unavailableReason=reason, detail=detail)


def _profile_segment(label: str, po: ProfileOut) -> ReviewSegmentOut:
    """画像段 = **直接复用 `/profile/*` 两个端点的返回**(同码不重写)——
    两条路上的画像永远讲同一句话,⛔ 不在这里另写一遍"没有 vs 没看"的判读。"""
    return ReviewSegmentOut(available=po.available, unavailableReason=po.unavailableReason,
                            label=label, asOf=po.asOf, items=list(po.items))


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


def _iteration_of(lo, hi) -> Dict[str, Any]:
    """V2.2-④ 三段共用的取数:**只读落盘产物里的 `iteration` 段**,⛔ 零在线现算
    (与 `/review/handoff` 完全同一条纪律 —— 本端点跑在常驻服务里,§七 P0-23)。

    ⚠ 调用方**每次请求只调一次**再把结果分给三段(见 `get_review_overview`):
    校准产物带着安慰剂逐日表,可能是几百 KB 的 JSON —— 一次请求读三遍没必要。
    """
    from neckline.review.handoff import CAL_OK, load_calibration_with_status

    if lo is None or hi is None:
        return {}
    payload, status_ = load_calibration_with_status(
        lo.strftime("%Y%m%d"), hi.strftime("%Y%m%d"), _calibration_dir())
    if status_ != CAL_OK or not isinstance(payload, dict):
        return {}
    it = payload.get("iteration")
    return dict(it) if isinstance(it, dict) else {}


def _clock_segment(lo, hi, kind: str, it: Optional[Dict[str, Any]] = None) -> ReviewSegmentOut:
    """`selectionClock` / `tradeClock` 两段(V2.2-④)。

    ⚠ 空态两句话刻意分开:**产物没落盘** = 周度作业还没跑到这个窗口(「没看」→
    `available=false`);**产物在但这一段样本为 0** = 这周确实没有结案样本 /
    没有真实买入(「没有」→ `available=true` + 空内容)。合成一句读者就分不清
    「等系统」还是「本来就没有」。"""
    it = _iteration_of(lo, hi)
    label = "选股时钟 · D0 全部 T1/T2" if kind == "selection" else "交易时钟 · 真实买入"
    if not it:
        return ReviewSegmentOut(
            available=False, label=label,
            unavailableReason=("本窗口尚无周度校准产物(周六 09:00 的周度作业还没跑到"
                               "这个窗口)—— **会自愈**;⛔ 在线路径不补算(§七 P0-23)。"))
    if kind == "selection":
        detail = (it.get("selection") or {})
        detail = {"overall": detail.get("overall") or {},
                  "byStratum": detail.get("byStratum") or [],
                  "strataKey": it.get("strataKey") or [],
                  "samples": (it.get("samples") or {}).get("selectionClock", 0)}
    else:
        detail = dict(it.get("trade") or {})
    return ReviewSegmentOut(
        available=True, label=label,
        asOf=f"{lo.strftime('%Y%m%d')}→{hi.strftime('%Y%m%d')}", detail=detail)


def _iteration_segment(lo, hi, it: Optional[Dict[str, Any]] = None) -> ReviewSegmentOut:
    """`iterationSuggestions` 段(V2.2-④-D 四分类建议)。

    🔴 **分界线未由用户拍板时,`items` 里每行 `klass=null`** + `klassStatus=
    'thresholds_undecided'` —— 客户端必须把它显示成「**待你拍板**」,
    ⛔ 不许渲染成「观察」(「还没决定」与「样本不足」是两件事)。
    段本身仍 `available=true`:统计量是**有**的,缺的只是那两个数。"""
    it = _iteration_of(lo, hi) if it is None else it
    if not it:
        return ReviewSegmentOut(
            available=False, label="修改建议 · 保留 / 观察 / 降权 / 淘汰",
            unavailableReason=("本窗口尚无周度校准产物 —— **会自愈**;"
                               "⛔ 在线路径不补算(§七 P0-23)。"))
    return ReviewSegmentOut(
        available=True, label="修改建议 · 保留 / 观察 / 降权 / 淘汰",
        asOf=f"{lo.strftime('%Y%m%d')}→{hi.strftime('%Y%m%d')}",
        items=list(it.get("suggestions") or []),
        detail={"thresholds": it.get("thresholds") or {},
                "disclaimer": it.get("disclaimer") or ""},
    )


def _observations_segment() -> ReviewSegmentOut:
    """观察项段:静态登记册(与 `PROJECT_PLAN.md` §七 Backlog 的闭合由守门单测钉死)。
    它**恒 available** —— 清单本身一直在,空不空是内容的事。"""
    from neckline.review.handoff import HANDOFF_OBSERVATIONS

    return ReviewSegmentOut(available=True, label="观察项 · 等证据的策略问题",
                            items=[dict(o) for o in HANDOFF_OBSERVATIONS])


@app.get(f"{API_PREFIX}/review/overview", dependencies=[Depends(require_token)])
def get_review_overview(week: str = "", asOf: str = "") -> ReviewOverviewOut:
    """复盘板块「累计」页的五段聚合读(V2.1-⑤)。

    `week` = 该周任意一天 `YYYYMMDD`(缺省本周);`asOf` = 画像期(缺省最近一期)。

    **五段各自独立说"有 / 没有 / 没取到"**,⛔ 不许一个总开关罩住五段 —— 校准产物没
    生成、画像没批算、这周没传交割单是三件互不相干的事。**每段各自包保险丝**:任一段
    炸了只让那一段 `available=false`,其余四段照出(⛔ 不 500)。"""
    out = ReviewOverviewOut()
    anchor = _week_anchor(week)
    try:
        from neckline.review.reconcile import iso_week_key

        out.weekKey = iso_week_key(anchor)
    except Exception:  # noqa: BLE001
        logger.warning("[review] ISO 周键计算异常", exc_info=True)

    lo = hi = None
    try:
        from neckline.review.research_artifact import week_bounds

        lo, hi = week_bounds(anchor)          # 该周的**交易日**首尾(与产物命名同源)
    except Exception:  # noqa: BLE001  交易日历读不到不该掀翻整页
        logger.warning("[review] 周边界(交易日)解析异常", exc_info=True)
    out.weekStart = lo.strftime("%Y%m%d") if lo else ""
    out.weekEnd = hi.strftime("%Y%m%d") if hi else ""

    try:                      # V2.2-④ 三段共用的产物:一次请求只读一遍
        iteration: Dict[str, Any] = _iteration_of(lo, hi)
    except Exception:  # noqa: BLE001  读不出只让那三段说"没取到",⛔ 不连坐前五段
        logger.warning("[review] overview 的双时钟产物读取异常", exc_info=True)
        iteration = {}

    for field_name, build in (
        ("calibration", lambda: _calibration_segment(lo, hi)),
        ("preference", lambda: _profile_segment("偏好画像 · 喜欢什么",
                                                get_profile_preference(asOf=asOf))),
        ("capability", lambda: _profile_segment("能力画像 · 什么真有效",
                                                get_profile_capability(asOf=asOf))),
        ("reconcile", lambda: _reconcile_segment(out.weekKey)),
        ("observations", _observations_segment),
        # V2.2-④ 三段(各自独立 available,⛔ 不被上面五段罩住)。
        # ⚠ `iteration` **整个请求只读一次产物**再分给三段(那份 JSON 可能几百 KB)。
        ("selectionClock", lambda: _clock_segment(lo, hi, "selection", iteration)),
        ("tradeClock", lambda: _clock_segment(lo, hi, "trade", iteration)),
        ("iterationSuggestions", lambda: _iteration_segment(lo, hi, iteration)),
    ):
        try:
            setattr(out, field_name, build())
        except Exception as exc:  # noqa: BLE001  段级保险丝:一段炸不连坐其余四段
            logger.warning("[review] overview 的 %s 段装配异常(已降级为不可得)",
                           field_name, exc_info=True)
            setattr(out, field_name, ReviewSegmentOut(
                available=False,
                unavailableReason=f"本段本次未取得:{type(exc).__name__}(详见服务端日志)。"))
    return out


@app.get(f"{API_PREFIX}/review/handoff", dependencies=[Depends(require_token)])
def get_review_handoff(
    date_from: str = Query(default="", alias="from"),
    date_to: str = Query(default="", alias="to"),
    asOf: str = "",
) -> ReviewHandoffOut:
    """导出一份**能直接交给策略台**的校准移交件(V2.1-⑤)。

    `from`/`to` 缺省 = **最近一期已落盘的校准窗口**(⛔ 不是"现在算一份");
    `asOf` = 画像期(缺省最近一期)。

    ⚠ **`from` 是 Python 关键字**,故形参名 `date_from` + `Query(alias="from")` ——
    URL 上仍是客户端契约里那个 `?from=`(同 `GET /decisions` 的既有姿势)。

    整段包保险丝:异常 → `available=false` + 可读原因,**⛔ 不 500、⛔ 不 404**。"""
    from neckline.review import handoff as ho

    try:
        h = ho.build_handoff(date_from, date_to, out_dir=_calibration_dir(),
                             db_path=_db(), profile_as_of=asOf)
    except Exception as exc:  # noqa: BLE001  移交件是审计件,炸了如实说,不 500
        logger.warning("[review] 校准移交件装配异常(已降级为不可得)", exc_info=True)
        return ReviewHandoffOut(
            available=False,
            unavailableReason=f"校准移交件本次生成失败:{type(exc).__name__}(详见服务端日志)。")
    return ReviewHandoffOut(
        available=h.available, unavailableReason=h.unavailable_reason,
        windowFrom=h.window_from, windowTo=h.window_to, generatedAt=h.generated_at,
        sampleN=dict(h.sample_n), markdown=h.markdown,
    )


# ══════════════════════════════════════════════════════════════════════════
# V2.2-④ 双时钟:两条只读 + **一条写**(`POST …/note` 是本版唯一新增写端点)
# ══════════════════════════════════════════════════════════════════════════

@app.get(f"{API_PREFIX}/clocks/selection", dependencies=[Depends(require_token)])
def get_selection_clocks(
    date_from: str = Query(default="", alias="from"),
    date_to: str = Query(default="", alias="to"),
) -> SelectionClocksOut:
    """已结案的选股时钟(按 **D0** 区间;缺省 = 不设该端)。

    🔴 **样本 = D0 全部 T1/T2,与用户买没买无关**(K8 §十四)。空列表 = 这段时间**没有
    结案样本**(合法态)—— ⛔ 别读成"系统没跑",那要看当日复盘与段状态。

    整段包保险丝:异常 → 空列表 + 服务端日志,**⛔ 不 500、⛔ 不 404**(同
    `/review/overview` 的既定姿势:审计读端不该因为读不出就把 App 打红)。"""
    from neckline.review.selection_clock import list_closures

    lo = (date_from or "").strip() or None
    hi = (date_to or "").strip() or None
    try:
        rows = list_closures(lo, hi, db_path=_db())
    except Exception:  # noqa: BLE001
        logger.warning("[clocks] 选股时钟读取异常(已降级为空列表)", exc_info=True)
        rows = []
    return SelectionClocksOut(
        dateFrom=lo or "", dateTo=hi or "",
        items=[SelectionClockOut(
            basketId=r["basket_id"], d0Date=r["d0_date"], d1Date=r["d1_date"],
            coveredTier=r["covered_tier"], regimeAtD0=r["regime_at_d0"],
            tierAccuracy=r["tier_accuracy"], untriggeredReason=r["untriggered_reason"],
            closedAt=r["closed_at"], skeletonVersion=r["skeleton_version"],
            verificationRulesetVersion=r["verification_ruleset_version"],
            engineBreakdown=r["engine_breakdown"], mech=r["mech"],
        ) for r in rows],
    )


@app.get(f"{API_PREFIX}/clocks/trade/{{position_id}}", dependencies=[Depends(require_token)])
def get_trade_clock(position_id: int) -> TradeClockOut:
    """一笔真实买入的交易时钟 + 全部事件流水。

    **404 只在这笔仓没有交易时钟时触发**,复用既有 `not_found` reason 字符串 ——
    ⛔ 不新增 reason(客户端 `mapReason` 一字不动,V2.2-⑥ 契约要求)。"""
    from neckline.review.trade_clock import list_events, load_trade_clock

    clock = load_trade_clock(position_id, db_path=_db())
    if clock is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": "not_found"})
    events = list_events(clock["id"], db_path=_db())
    return TradeClockOut(
        positionId=clock["position_id"], tsCode=clock["ts_code"],
        basketId=clock["basket_id"], openedOn=clock["opened_on"],
        closedOn=clock["closed_on"], status=clock["status"],
        entryPlan=clock["entry_plan"], final=clock["final"],
        events=[TradeClockEventOut(
            id=e["id"], eventDate=e["event_date"], kind=e["kind"],
            mech=e["mech"], userNote=e["user_note"], createdAt=e["created_at"],
        ) for e in events],
    )


@app.post(f"{API_PREFIX}/clocks/trade/{{position_id}}/note",
          dependencies=[Depends(require_token)])
def post_trade_clock_note(position_id: int, body: TradeClockNoteIn) -> TradeClockNoteOut:
    """追加一条**用户主观说明**(K8 §十五「用户只补充系统无法识别的主观原因,每次一条
    简短说明」)。**本版唯一新增写端点。**

    · **纯追加**:写 `trade_clock_events` 新行,⛔ 不改任何既有行。
    · **404 只在这笔仓没有交易时钟时触发**,复用既有 `not_found`,⛔ 不新增 reason。
    · 空 / 超长 → **422**(fail loud,⛔ 不静默截断 —— 截断会把用户写的话改掉一半还
      装作收下了)。⚠ **刻意不给它一个新 reason 字符串**:V2.2 契约要求「零新增
      reason」(⑥ 表格 + `tests/test_contract_crosscheck.py` 的机器判据),而这一类
      「请求体本身不合法」在本项目一贯就是 pydantic 校验的 422 形状 —— 长度上限因此
      直接写在 `TradeClockNoteIn.note` 的 `Field` 里,与其它端点的非法请求体同款。
    · ⛔ **不做 LLM 代猜**(§七 P3-28 纪律不变):这条是用户自己写的,系统不生成、
      不改写、不合并。响应顺带回 `coverage`(「本期 N 笔中有 M 笔带说明」),让稀疏
      程度当场可见 —— 那正是 P3-28 候选解法 ① 的落点。"""
    from neckline.review.trade_clock import UserNoteError, append_user_note, note_coverage

    try:
        row = append_user_note(position_id, body.note, db_path=_db())
    except UserNoteError as exc:
        # 领域层的 fail loud 兜底(正常路径已被 DTO 的 `Field` 约束拦在 422)。
        # ⛔ 不带 `reason` 键:那会往 reason 面上加一个新字符串,违反本版「零新增 reason」。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"ok": False, "message": str(exc)},
        ) from exc
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "reason": "not_found"})
    return TradeClockNoteOut(
        ok=True, eventId=row["id"], eventDate=row["event_date"],
        coverage=note_coverage(db_path=_db()),
    )


__all__ = ["app", "VERSION", "API_PREFIX"]
