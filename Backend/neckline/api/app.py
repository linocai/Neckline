"""FastAPI 应用主体(plan 4A + 4B.3 单 unit 内哨兵 asyncio 任务)。

绑 127.0.0.1:8002(nginx 反代,与 LinoN 8001 共存)。`/api/v1/health` 免鉴权;其余端点
过 `require_token`。startup:fail-fast 校验 `API_TOKEN`(len>=16)+ `init_schema` + 起
哨兵后台轮询任务(§3.6「哨兵折进 FastAPI 单 unit 的 lifespan asyncio 任务」,不另起进程)。
shutdown:置位 stop_event,优雅停轮询。

**同码不重写**:报告 / 看板 / 持仓的领域逻辑全部复用现有模块,端点只做「装配 +
出入参映射 + 鉴权」。

**测试注入(沿 LinoN 模块级替身姿势)**:`ENABLE_SENTINEL`(关后台轮询)、`_DB_PATH_OVERRIDE`
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
from datetime import date, datetime, time
from datetime import date as date_cls
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, status

from neckline.api import notify
from neckline.api.deps import require_api_token_ready, require_token
from neckline.api.schemas import (
    DeviceRegisterIn,
    EvalWeeklyOut,
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
from neckline.calendar import CN_TZ, is_trading_day
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
# startup 是否挂早晨轮询;可用环境变量 NECKLINE_ENABLE_SENTINEL=0 关(冒烟脚本用)。
# ⚠ **环境变量名刻意不改**(同 `sentinel_events` 表名留着的理由,PROJECT_PLAN §3.2):
# 改名要同步动 `scripts/smoke_api.sh` 与生产 unit,一次改名换零产品价值。
ENABLE_SENTINEL = os.environ.get("NECKLINE_ENABLE_SENTINEL", "1") != "0"
_DB_PATH_OVERRIDE: Optional[Path] = None      # 隔离库(None → settings.db_path)
_PARQUET_DIR_OVERRIDE: Optional[Path] = None  # 隔离 parquet 根(None → settings.parquet_dir)
# `GET /review/{overview}` 与 `GET /eval/weekly` 要读**离线落盘**的周度校准产物
# (`data/reports/calibration/`)。CLAUDE.md「测试隔离」条明载 `api_env` **不重写**
# `neckline.config.settings`,不给注入点就会读到真实项目的 `data/reports/`
# (那正是"一测就踩、断言全错还不报错"的那类泄漏)。
_DATA_DIR_OVERRIDE: Optional[Path] = None     # 隔离 data 根(None → settings.data_dir)


def _db() -> Optional[Path]:
    return _DB_PATH_OVERRIDE


def _calibration_dir() -> Optional[Path]:
    """周度校准产物目录(`<data>/reports/calibration`)。`None` = 用
    `review/handoff.py::calibration_dir()` 自己的缺省(真实 `settings.data_dir`)。"""
    return None if _DATA_DIR_OVERRIDE is None else (_DATA_DIR_OVERRIDE / "reports" / "calibration")

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
    """早晨轮询(V2.5.0 S1:自 `_sentinel_loop` 瘦身而来)。

    🔴 **本版只剩早晨,盘中四哨兵整块退役**(裁定 7):买点 / 证伪 / 持仓 / 退潮
    四条盘中判定、盘中存拍、盘前校准、自定义提醒**全部已物理删除**,
    ⛔ 不许以任何形式接回来。系统不持续观察 9:30 以后的价格、不推送盘中提醒、
    不跟踪持仓(架构 §四)。

    **本片(S8)起两拍齐全**(PROJECT_PLAN §5.7.3),
    **各自独立 `try/except`,一拍炸了不影响另一拍**:

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
        try:
            _morning_checklist_tick(now)
        except Exception:  # noqa: BLE001 —— 早晨循环不许被单拍异常掀翻
            logger.warning("[morning] 竞价核对表那一拍异常(已吞,不影响结算拍)",
                           exc_info=True)
        try:
            _morning_settle_tick(now)
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
    if ENABLE_SENTINEL:
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


# —— 4A.2 报告 ————————————————————————————————————————————————————————


# —— ⚠ V2.5.0 S12:七个 K8 时代的 reason 常量**已随它们的端点一起删除** ————————
#
# `basket_not_found` / `card_not_ready` / `card_corrupt`(篮子与冻结卡)、
# `not_trading_day` / `future_buy_date`(补录开仓的日期校验)、
# `auction_not_ready` / `auction_corrupt`(K8 竞价确认层)—— 这七条的**端点已在 S1
# 全部删除**,常量却留了下来。留着不是无害的:契约对拍
# (`tests/test_contract_crosscheck.py`)按「`app.py` 里出现的 reason 字面量」反推
# 服务端 reason 面,一个再也 raise 不出来的常量会**要求客户端一直养着一个死 case**,
# 而那个 case 的存在又让人以为对应端点还在。⛔ 别为了"以后可能用得上"留死码。
#
# 🔴 本版剩下的 reason 面只有 6 条,全部出自设置屏(见
# `tests/test_contract_crosscheck.py::SERVER_REASONS`):新增会返 4xx 的端点时,
# **必须同时**更新那份清单与客户端 `APIClient.mapReason`。
# ⚠ K9 的四条新端点(`/selection/*` / `/checklist/*`)返的是**纯字符串 detail**
# (「20260430 没有报告」这类),⛔ 不进 reason 面 —— 它们不需要客户端换算,
# 原文直接给用户看比一个英文码更清楚。


# —— V2-⑭-B 计划继承(`position_plans`)+ 建仓快照(`entry_snapshots`)————————
#
# **⑩-E 信息互通边界**:持仓侧可读篮子卡、可追加自己的计划版本,**不得回头修改
# 对方已冻结的历史信息** —— 本节两个端点对 `baskets`/`basket_cards`/`tier_history`
# 零写入(AST 守门单测锁死),`create_position_plan_version` 签名里根本没有相关参数。


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
                    f"本窗口({lo}→{hi})尚无周度校准产物 —— **离线**周度校准作业"
                    f"还没跑到这个窗口。"
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
    """
    from neckline.review import bindery
    from neckline.review.store import load_weekly_review

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


def _observations_segment() -> ReviewSegmentOut:
    """观察项段:静态登记册(与 `PROJECT_PLAN.md` §七 Backlog 的闭合由守门单测钉死)。
    它**恒 available** —— 清单本身一直在,空不空是内容的事。"""
    from neckline.review.handoff import HANDOFF_OBSERVATIONS

    return ReviewSegmentOut(available=True, label="观察项 · 等证据的策略问题",
                            items=[dict(o) for o in HANDOFF_OBSERVATIONS])


@app.get(f"{API_PREFIX}/review/overview", dependencies=[Depends(require_token)])
def get_review_overview(week: str = "", asOf: str = "") -> ReviewOverviewOut:
    """复盘板块「累计」页的聚合读。V2.5.0 S11 起共**四段**:
    校准 / 对账 / **结论存档** / 观察项。

    `week` = 该周任意一天 `YYYYMMDD`(缺省本周);`asOf` 保留兼容位(画像段已随
    `profile/` 在 S1 退役,⛔ 不再有消费方)。

    **四段各自独立说"有 / 没有 / 没取到"**,⛔ 不许一个总开关罩住 —— 校准产物没生成、
    这周没传交割单、这周还没写结论是三件互不相干的事。**每段各自包保险丝**:任一段
    炸了只让那一段 `available=false`,其余三段照出(⛔ 不 500)。

    ⚠ **装订材料刻意不在这里**:它要读 parquet 行情,属于「点一下才算」的动作,
    单独走 `GET /review/bindery`(⛔ 别塞进这个每次进板块都会拉的聚合读,§12 坑 1)。"""
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

    for field_name, build in (
        ("calibration", lambda: _calibration_segment(lo, hi)),
        ("reconcile", lambda: _reconcile_segment(out.weekKey)),
        ("conclusions", lambda: _conclusions_segment(out.weekKey)),
        ("observations", _observations_segment),
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
    room = k9_store.load_upside_room_mech(day, db_path=_db())
    playbooks = pb_store.load_latest(day, codes=codes, db_path=_db())
    notes = explain_store.load_notes(day, codes=codes, db_path=_db())
    out = []
    for e in listing:
        code = e["ts_code"]
        note = notes.get(code)
        pb = playbooks.get(code)
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
    return {
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
        "structured": row["structured"],
        # §5.11 今日清单要的逐只摘要(**现装,不进冻结件**,见 `_selection_stocks`)。
        "stocks": _selection_stocks(row["trade_date"]),
    }


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


def _parse_day(raw: str) -> date_cls:
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="日期必须是 YYYYMMDD")


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
        "explain": notes.get(ts_code),
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
    """
    from neckline.k9 import store as k9_store
    from neckline.playbook import fill as playbook_fill
    from neckline.playbook import model as pb_model
    from neckline.playbook import skeleton as skeleton_mod
    from neckline.playbook import store as pb_store

    day = _parse_day(trade_date)
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
    """
    from neckline.auction import store as auction_store

    out = auction_store.load_checklist(_parse_day(trade_date), db_path=_db())
    if out is None:
        raise HTTPException(status_code=404, detail=f"{trade_date} 没有竞价核对表")
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


# ══════════════════════════════════════════════════════════════════════════
# V2.5.0 S13 · K8 历史数据的只读追溯(裁定 6,PROJECT_PLAN §5.12)
# ══════════════════════════════════════════════════════════════════════════

@app.get(f"{API_PREFIX}/legacy/k8/baskets", dependencies=[Depends(require_token)])
def get_legacy_k8_baskets(date: str = "") -> dict:
    """**K8 只读追溯的唯一入口**(裁定 6:旧表保留、只读、不迁移、不回填)。

    `date` = `YYYYMMDD`;缺省只回总览(库里有哪几天、共多少篮),供先定位再点查。

    🔴 **只读**:领域实现 `neckline/legacy_k8.py` 走 `sqlite3` 的 `mode=ro` 连接,
    且模块里结构上**只有 SELECT**(守门单测扫 `INSERT`/`UPDATE`/`DELETE`/`CREATE`/
    `DROP`/`ALTER` 零命中)。⛔ 本路径不 `init_schema`、不建表、不回填。
    写方法(POST/PUT/DELETE)未注册 → FastAPI 自动 **405**。

    ⚠ **返回的是 K8 的语义,不是 K9 的**:`tier` / `driver` / `roleLlm` 这些字段属于
    已退役的那条链,⛔ 不许被翻译成 K9 的 `pattern` / `seatKind`,也 ⛔ 不进任何成绩线。

    ⚠ **一律 200,三态分开说**(⛔ 不 404):`available=false` = 这个库根本没有 K8
    篮子表;`available=true, found=false` = 有 K8 历史但不是那一天。
    """
    from neckline import legacy_k8

    raw = (date or "").strip()
    day = _parse_day(raw) if raw else None
    return legacy_k8.load_baskets(day, db_path=_db())


__all__ = ["app", "VERSION", "API_PREFIX"]
