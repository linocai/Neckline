"""SQLite 元数据 / 业务台账层(plan §3.3)。

存放:交易日历(`trade_cal`)、股票 / 行业元数据(`stock_basic`)、股票曾用名 /
ST 状态历史(`namechange`)。回测大表(daily 等)走 Parquet,不进本库
——见 `neckline.data.tushare_client` 与 `scripts/backfill.py`。

设计:薄封装,stdlib `sqlite3` 直连,不引入 ORM。所有写入用
`INSERT OR REPLACE` / `INSERT OR IGNORE` 保证脚本可重复跑(幂等)。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from neckline.config import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_cal (
    exchange        TEXT NOT NULL,
    cal_date        TEXT NOT NULL,   -- 'YYYYMMDD'
    is_open         INTEGER NOT NULL,
    pretrade_date   TEXT,
    PRIMARY KEY (exchange, cal_date)
);
CREATE INDEX IF NOT EXISTS idx_trade_cal_date ON trade_cal(cal_date);

CREATE TABLE IF NOT EXISTS stock_basic (
    ts_code         TEXT PRIMARY KEY,
    symbol          TEXT,
    name            TEXT,
    industry        TEXT,
    market          TEXT,            -- 主板/创业板/科创板/北交所(Tushare 原生字段)
    list_date       TEXT,            -- 'YYYYMMDD'
    delist_date     TEXT,
    list_status     TEXT NOT NULL    -- L=上市 D=退市 P=暂未上市
);
CREATE INDEX IF NOT EXISTS idx_stock_basic_market ON stock_basic(market);

CREATE TABLE IF NOT EXISTS namechange (
    ts_code         TEXT NOT NULL,
    name            TEXT NOT NULL,
    start_date      TEXT NOT NULL,   -- 'YYYYMMDD',该名称生效起始日
    end_date        TEXT,            -- 'YYYYMMDD' | NULL(NULL=沿用至今)
    ann_date        TEXT,
    change_reason   TEXT,
    PRIMARY KEY (ts_code, start_date, name)
);
CREATE INDEX IF NOT EXISTS idx_namechange_code ON namechange(ts_code);

CREATE TABLE IF NOT EXISTS backfill_log (
    table_name      TEXT NOT NULL,
    trade_date      TEXT NOT NULL,   -- 'YYYYMMDD'
    status          TEXT NOT NULL,   -- 'ok' | 'empty'
    row_count       INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL,   -- ISO8601,写入时间(断点续跑判据)
    PRIMARY KEY (table_name, trade_date)
);

-- 策略大脑版本表(plan 1.9 / §2.6「大脑带版本号 + 变更日志 + 实盘表现按版本归因」)。
-- rule_json:该版本的规则参数(MomentumConfig 采纳值 + 市场过滤决策等)全量快照。
-- metrics_json:定版时的样本内/外回测指标(可比性证据)。is_active:当前现役版本(唯一)。
-- activated_at(v1.2-A):该版本**最后一次**「成为现役」的时刻(ISO8601);NULL=从未激活。
-- ⚠ **v1.4 review 🟡-1 起它不再是时间线事实源**(一个版本只能存一个戳,表达不了「被激活过
-- 两次」;回滚重激活会把旧戳前移 = 静默改判历史周)。事实源已改为 append-only 的
-- `strategy_activation_log`(见下表);本列**保留**作兼容/展示位(「现役是什么时候上任的」),
-- 仍由 `brain.save_version/activate_version` 同步刷新,历史表缺失的老库靠它兜底解析。
CREATE TABLE IF NOT EXISTS strategy_versions (
    version         TEXT PRIMARY KEY,   -- 策略版本号,K 字头整数(K1/K2/...);章程修订走系统 v 字头
                                        -- (如 v1.2:config 承 K 血缘、仅改仓位字段,不占 K 命名空间)
    created_at      TEXT NOT NULL,      -- ISO8601
    rule_json       TEXT NOT NULL,      -- 规则参数快照(JSON)
    changelog       TEXT NOT NULL,      -- 本版为何这样定(过堂结论摘要)
    metrics_json    TEXT NOT NULL DEFAULT '{}',  -- 定版回测指标(JSON)
    is_active       INTEGER NOT NULL DEFAULT 0,
    activated_at    TEXT                -- ISO8601 | NULL(v1.2-A 遗留单戳,见表头注释)
);

-- 章程**激活历史**(v1.4 review 🟡-1 修复,2026-07-29):纪律判定时间轴的**唯一事实源**。
-- **append-only,只增不改不删** —— `brain.activate_version` / `save_version(activate=True)`
-- 每激活一次**追加**一行,永不 UPDATE/DELETE 既有行。
-- **为什么必须是事件流**:旧模型「一个版本一个 activated_at 戳」表达不了同一版本被激活两次。
-- `scripts/activate_charter.py` 的白名单**明确保留 v1.3 作唯一合法回退目标**,即回滚是设计内
-- 路径;一旦回滚,旧模型会把该版本的戳前移到回滚时刻 → 回滚之前那段历史落进「早于所有激活」
-- 兜底 → `reviews` 是幂等覆盖表,重传交割单即整段改判 = 2026-07-27 审计 🟡-3 封掉的洗白口经
-- 回退路径复活(判定线审计已用临时库实测复现:07-22 的 3 万违纪在回滚后凭空消失)。
-- 事件流下,「07-22 那一刻现役的是谁」由**当时那条事件**回答,后来发生什么都改不了它。
-- **老库(本表不存在或为空)**:解析器回退读 `strategy_versions.activated_at` 单戳 =
-- 与 v1.4 之前逐位一致的旧行为(见 `brain._activation_events`);首次 `init_schema` 会用
-- 该列**幂等播种**本表(见 `_seed_activation_log`),播种后即由本表接管。
CREATE TABLE IF NOT EXISTS strategy_activation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    version         TEXT NOT NULL,      -- strategy_versions.version(不设外键:历史事件不该被版本行的存废牵连)
    activated_at    TEXT NOT NULL,      -- ISO8601;生产唯一写入者 brain._now() → 恒 UTC(+00:00)
    via             TEXT NOT NULL DEFAULT '',   -- 'activate_version' | 'save_version' | 'seed' | 测试注入
    note            TEXT NOT NULL DEFAULT ''    -- 审计备注(如「重激活/回滚」「从 activated_at 播种」)
);
CREATE INDEX IF NOT EXISTS idx_strategy_activation_log_at ON strategy_activation_log(activated_at, id);

-- 盘后报告存档(plan 2.5)。一个交易日一行(幂等覆盖,重跑报告不留重复行);
-- *_json 是该次报告的结构化快照(情绪仪表盘/强势板块/候选20只四件套),供事后
-- 审计与历史回放核对;markdown 是渲染产物全文。
-- watchlist_json(v1.1-C.3 自选体检;⚠ **v2.0.0-⑬-11 起停写**,列保留不 DROP):
-- `WatchlistCheckItem.public_dict()` 的 JSON 数组快照,老报告行(建表早于本列)经
-- `_migrate_columns` 幂等补列取默认值 '[]'(前向兼容——旧报告没有这节,读回来就是
-- 空数组,不是 NULL 炸 json.loads)。V2 起 `store.save_report` **不再把该列写进
-- INSERT 列表**,新行一律吃 DDL 默认 '[]';`store._parse_watchlist_json` 只服务历史
-- 行的归因只读(自选体检整节已删,见 `watchlist` 表头注释)。
-- intel_json/sector_moneyflow_json(v1.3-③ C1/C2):`report/intel.py::IntelReport.
-- to_public_dict()` / `report/sector_moneyflow.py::SectorMoneyflowReport.
-- to_public_dict()` 的 JSON 快照(均为单个对象,非数组——`sector_moneyflow` 需要
-- 携带 available/unavailableReason 等元信息,不能只是裸榜单,否则"2023-09 前无
-- 数据"这类诚实留空原因无处安放),同 watchlist_json 先例——老报告行幂等补列取
-- 默认值 '{}',前向兼容。
-- news_alerts_scan_json(v1.3-③-C4):`report/news_alerts.py::NewsAlertsReport.
-- scan_statuses_public()` 的 JSON 数组快照——**只落扫描状态(没扫到/扫了没有的
-- 元信息),不落命中告警本身**(告警条目已在独立 `news_alerts` 表,不重复存两
-- 份)。之所以扫描状态也要随报告落一份快照(而非只活在内存 `NewsAlertsReport`
-- 里):历史报告回放(`GET /report/{date}`)时仍需分清"当时没扫到"与"当时扫了
-- 确认没有",不能只看 `news_alerts` 表有没有那天的行——空行两种含义都成立。
CREATE TABLE IF NOT EXISTS reports (
    trade_date            TEXT PRIMARY KEY,   -- 'YYYYMMDD'
    generated_at          TEXT NOT NULL,      -- ISO8601
    strategy_version      TEXT NOT NULL,      -- 生成本报告时用的大脑版本号(strategy_versions.version)
    sentiment_json        TEXT NOT NULL,
    sectors_json          TEXT NOT NULL,
    candidates_json       TEXT NOT NULL,
    markdown              TEXT NOT NULL,
    watchlist_json        TEXT NOT NULL DEFAULT '[]',
    intel_json            TEXT NOT NULL DEFAULT '{}',
    sector_moneyflow_json TEXT NOT NULL DEFAULT '{}',
    news_alerts_scan_json TEXT NOT NULL DEFAULT '[]'
);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑬-2「单票 LLM 审判 → 删调用路径」):
-- 写入方 `report/store.py::save_llm_judgment` / `delete_llm_judgments` 已物理删除,
-- 唯一的产出编排 `pipeline._judge_candidates_with_budget` 随候选榜一并退役。历史行
-- (v1.0~v1.5.2 生产数据)供归因只读,读函数 `load_llm_judgments` 保留。
-- ⚠ **`llm/judge.py::judge_candidate` 本体不在删除之列** —— ⑬-2 明确它保留为「通用
-- LLM 调用 + 降级链 + verdict 解析」工具(含 `narrative_splitter` 依赖注入纪律);
-- V2 的篮子链路走 ⑤ 的两段式,只在需要结论标签处用它,且**不落本表**。
--
-- LLM 逻辑审判存档(plan 2.4,v1.5-②起 20 只全覆盖)。实际发起过调用的候选每只
-- 一行(预算耗尽被跳过、未发起调用的不落此表,查 candidates_json 里的
-- judge_skipped);search_hits_json 是该次审判用到的联网搜索结果全文(§2.4「搜索
-- 结果全文落 SQLite 存档」,供事后审计"当时为何否决" + 自建历史新闻快照)。
-- degraded=1 表示「LLM 未激活」占位,不是真实判断。search_engine 列(v1.5-④-A3,
-- §七 P1-7)由 `_migrate_columns` 幂等补列——**不在本 CREATE TABLE 里**,同
-- decision_log.max_chase_pct 既有惯例(新增列一律只登记进 `_COLUMN_MIGRATIONS`)。
CREATE TABLE IF NOT EXISTS llm_judgments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    ts_code          TEXT NOT NULL,
    provider         TEXT NOT NULL,
    model            TEXT,
    verdict          TEXT NOT NULL,      -- 通过 | 否决 | 未激活
    narrative        TEXT NOT NULL,
    degraded         INTEGER NOT NULL DEFAULT 0,
    degrade_reason   TEXT,
    search_hits_json TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    UNIQUE(trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_llm_judgments_trade_date ON llm_judgments(trade_date);

-- 持仓台账(plan 阶段3,§2.4 持仓哨兵的数据源)。极简账本,不造重界面——
-- `scripts/positions.py` CLI 录入/清仓。一票可多次开仓(分批建仓),故不以
-- ts_code 为主键;status='open' 的行是盘中哨兵持仓哨兵的监控对象。
-- close_reason(v1.2-A2 熔断纪律):离场原因枚举码(STOP_LOSS/TAKE_PROFIT/TIME_EXIT/
-- INVALIDATION/MANUAL),客户端清仓时选(不选 → NULL)。熔断「连续 3 笔止损」判据据此
-- 判「是否止损离场」;NULL 时才走价格近似兜底(见 `sentinel/circuit.py`)。老库经
-- `_migrate_columns` 幂等补列(NULL 默认,历史清仓行不臆造原因)。
CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code         TEXT NOT NULL,
    buy_price       REAL NOT NULL,
    qty             INTEGER NOT NULL,        -- 股数(非"手")
    buy_date        TEXT NOT NULL,           -- 'YYYYMMDD'
    status          TEXT NOT NULL DEFAULT 'open',  -- open | closed
    sell_price      REAL,
    sell_date       TEXT,                    -- 'YYYYMMDD'
    note            TEXT,
    close_reason    TEXT,                    -- v1.2-A2 离场原因枚举码 | NULL(见上方注释)
    buy_fees        REAL,                    -- v1.3 补录开仓实付买入费用(佣金+过户费,不含印花税)| NULL=未录
    sell_fees       REAL,                    -- v1.3 清仓实付卖出费用(真实发生后回填,周复盘对账用真数)| NULL
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_ts_code ON positions(ts_code);

-- 哨兵事件防重台账(plan 阶段3工程要求「状态防重推」)。同一 (trade_date, sentinel,
-- ts_code, event_key) 只推一次——推过即落一行,进程重启后重新扫描到同一事件时
-- 查表命中即跳过,不会重复推当日已推事件。ts_code 对无单票语义的事件(如退潮哨兵
-- 的市场级刹车)留空串,不用 NULL(NULL 在 UNIQUE 约束里不去重,见 SQLite 语义)。
CREATE TABLE IF NOT EXISTS sentinel_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL,           -- 'YYYYMMDD'
    sentinel        TEXT NOT NULL,           -- entry | retreat | holding | invalidation
    ts_code         TEXT NOT NULL DEFAULT '',
    event_key       TEXT NOT NULL,           -- 事件去重键,如 "trigger"/"stop"/"target"/"sector_dive"
    payload_json    TEXT NOT NULL DEFAULT '{}',
    pushed_at       TEXT NOT NULL,
    UNIQUE(trade_date, sentinel, ts_code, event_key)
);
CREATE INDEX IF NOT EXISTS idx_sentinel_events_trade_date ON sentinel_events(trade_date);

-- 阶段4 (4A) 应用设置(单行,plan §五 阶段4 / 4A.5)。id 恒为 1(CHECK 约束保证只有一行)。
-- llm_provider/llm_api_key:**V1 遗留列,V2-② 起停写**(不 DROP,同项目"删表一律
-- 停写留档"纪律的列级版本)——单 provider 时代的 `resolve_llm`/`set_llm` 已退役,
-- 被 `llm_providers` 表(任意 OpenAI 兼容端点自填,见该 CREATE TABLE 注释)与本表
-- 下方 `llm_task_routes`/`llm_default_provider` 两列取代,详见 `neckline/
-- settings_store.py` 模块头。**高危区**:key 服务端存取,DB 文件 600、gitignored、
-- rsync 永不同步覆盖(plan 不变量);`GET /settings*` 只回 `keySet: bool`,绝不回明文。
-- push_report/push_retreat:APNs 两类推送开关(§2.4 拍板,默认开可关)。
-- review_col_map:周复盘交割单列映射(JSON,4D 用)。
-- push_precall/push_d5exit:v1.1-A/B 新增两类 APNs 推送开关(盘前校准 9:26 汇总 /
-- D5 时间退出),默认开;老库经 `_migrate_columns` 幂等 ALTER 补列(见下)。
-- push_report/push_retreat/push_precall/push_d5exit/push_circuit/push_holding_alert:
--   **V1 六类开关列,v2.0.0-⑪ 起停写**(不 DROP,同 llm_provider 的列级停写留档纪律)
--   —— 通知重构成「三级 × N kind」后开关按 **kind** 配,落点换成下面的 `push_kinds`
--   JSON 列。老库既有取值经 `_seed_push_kinds` **一次性播种**进 `push_kinds`(用户
--   之前关掉的开关不会因为改版被悄悄打开),播种后这六列不再被读也不再被写。
-- push_kinds(v2.0.0-⑪):JSON 对象 `{"<kind>": 0|1, ...}`,kind 取值域唯一源
--   `neckline/notify_kinds.py::ALL_KINDS`。NULL=从未配置(全部 kind 取默认 = 开);
--   JSON 里缺某个 kind 同样取默认开(新增 kind 上线后老库不必回填)。
CREATE TABLE IF NOT EXISTS app_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    llm_provider    TEXT,
    llm_api_key     TEXT,
    push_report     INTEGER NOT NULL DEFAULT 1,
    push_retreat    INTEGER NOT NULL DEFAULT 1,
    push_precall    INTEGER NOT NULL DEFAULT 1,
    push_d5exit     INTEGER NOT NULL DEFAULT 1,
    push_circuit    INTEGER NOT NULL DEFAULT 1,   -- v1.2-A2:第五类推送(熔断提醒)开关,默认开
    push_holding_alert INTEGER NOT NULL DEFAULT 1, -- v1.3-②:第六类推送(K4 持仓派发警报)开关,默认开
    review_col_map  TEXT NOT NULL DEFAULT '{}',
    -- v1.3-③-C3:候选情报管线「五板块常驻」可配名单(JSON 数组存**板块中文名**,
    -- 运行时按 ths_index.name 精确匹配解析 ts_code;NULL=未配置=用
    -- settings_store.DEFAULT_INTEL_WATCH_BOARDS,'[]'=用户显式清空=无常驻)。
    -- ⚠ **v2.0.0-⑬-1/⑬-13 起停写留档**(五常驻板块保底删除,裁定 #9-c):列保留不
    -- DROP(历史配置留档),`settings_store.get/set_intel_watch_boards` 与
    -- `GET|PUT /settings/intel-boards` 两端点、客户端设置屏板块选择均已物理删除。
    intel_watch_boards TEXT,
    updated_at      TEXT
);

-- 阶段4 (4A) APNs 设备注册表(plan 4A.5 / 4B.5,复用 LinoN device_tokens 语义)。
-- token 为 APNs device token(唯一);16:00 报告 / 退潮刹车推送时遍历本表所有设备。
CREATE TABLE IF NOT EXISTS devices (
    token       TEXT PRIMARY KEY,
    platform    TEXT NOT NULL DEFAULT 'ios',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑬-10「问询台 forced 海选池通道 → 删」):
-- 写入方 `add_to_inquiry_pool`、消费查询 `load_pending_inquiry_codes`、消费标记
-- `mark_inquiry_pool_consumed` 三个函数已物理删除,`build_report` 不再传 `forced_codes`。
-- **只留 `neckline.api.stores.load_inquiry_pool` 一个只读**,唯一消费方是周复盘
-- `review/reconcile.py::check_plan_and_ledger` 的「计划内(问询台海选池)」归因判定
-- —— 那是对历史成交的判定,删了会改写历史周的 plan_status,故保留。
-- (自动写入方其实早在 v1.3.3 就退役了,V2 删的是剩下的消费侧闭环。)
--
-- 阶段4 (4A) 问询台海选池(plan §2.5 / 4A.5)。问询台裁决「初审通过进当晚海选池」→
-- 落本表(当日),当晚 `report.py` 生成报告时把海选池的票强制纳入候选评分 universe
-- (不改评分逻辑,只扩输入)。UNIQUE(trade_date, ts_code) 幂等,同日同票复问不重复入池。
-- consumed_report_date(v1.1-D 问询窗口修复):NULL=待消费,非 NULL=已被该交易日的
-- 报告消费。`trade_date` 只是「入池当日」的审计留痕,消费匹配不再靠它(见
-- `neckline.api.stores.load_pending_inquiry_codes`/`mark_inquiry_pool_consumed`
-- 与 `neckline.report.pipeline.build_report` 的消费改法)。
CREATE TABLE IF NOT EXISTS inquiry_pool (
    trade_date  TEXT NOT NULL,           -- 'YYYYMMDD'(入池当日,审计用,非消费匹配键)
    ts_code     TEXT NOT NULL,
    name        TEXT,
    reason      TEXT,                    -- 初审通过时的一句依据(供报告/审计留痕)
    created_at  TEXT NOT NULL,
    consumed_report_date TEXT,           -- 'YYYYMMDD' | NULL(NULL=待消费)
    PRIMARY KEY (trade_date, ts_code)
);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑬-11,裁定 #9-a「自选池 + 同花顺对账
-- 直接删」):本表自 V2.0.0 起**无任何读写路径** —— 领域模块 `neckline/watchlist.py`、
-- 自选体检 `neckline/report/watchlist_check.py`、五个 `/watchlist*` 端点(含
-- `reconcile-ths`/`export-ths`)、客户端 `WatchlistView.swift` 均已物理删除,哨兵关注池
-- 自 ⑧-A 起已不读它。历史行(v1.1-C~v1.5.2 生产数据)只供归因审计手工查询。DDL 与下面
-- 的历史字段注释**原样保留不删**;⚠ 注释里提到的 `neckline.watchlist` 模块已不存在。
-- 连带:①`reports.watchlist_json` 列同步停写(列保留,见该列注释);②问询台的「+自选」
-- 是它唯一写回主链的通道,删除后**问询台成纯分析入口**(Plan ⑬-11 已登记,非遗漏);
-- ③ §八 第 9 项(用户提供同花顺自选 txt)作废。
--
-- v1.1-C 自选池(plan §五 v1.1-C.1)。≤30 上限服务端硬校验(见 `neckline.watchlist`,
-- 建表本身不限;超限在写入函数里拒绝),增删只经用户显式端点(系统代码路径——报告/
-- 哨兵/问询台——绝不自动写本表,只读)。pinned=1 表示用户点名「每日必审」(v1.1-C.3
-- LLM 只审 changed∪pinned 的判据之一);source 留痕入池来源(manual/candidate/
-- inquiry/ths_import),纯审计,不影响任何逻辑分支。
CREATE TABLE IF NOT EXISTS watchlist (
    ts_code     TEXT PRIMARY KEY,
    name        TEXT,
    added_at    TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'manual',
    note        TEXT,
    pinned      INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);

-- 阶段4 (4D) 周复盘对账存档(plan 4D.2,week 为 ISO 周 'YYYY-Www')。本块(4A)仅建表
-- (幂等,forward-compat),对账逻辑与写入在 4D 落地。
CREATE TABLE IF NOT EXISTS reviews (
    week            TEXT PRIMARY KEY,       -- 'YYYY-Www'
    generated_at    TEXT NOT NULL,
    result_json     TEXT NOT NULL DEFAULT '{}',
    material        TEXT,
    updated_at      TEXT
);

-- v1.1-H2 退潮哨兵逐拍指标(§2.4 退潮双级制重构,2026-07-22 生产过敏后拍板)。
-- 每个盘中 tick 落一行(~330 行/交易日):供①「飙升条件」对比昨日同一时刻(±5min 窗)
-- 的炸板率(修复"早盘进行时值 vs 昨晚收盘定稿值"基数不同的结构性假信号);②「持续性
-- 要求」读上一拍触发条件族(triggered_json)判"连续 2 拍";③未来给红色刹车算命中率
-- 成绩单(全量指标 + 判级 + 触发路径全部留痕)。PK(trade_date,hhmm) → 同分钟重复 tick
-- INSERT OR REPLACE 幂等覆盖(生产 60s 一拍,hhmm 天然唯一)。tier ∈ none/yellow/red/
-- red_latched(当日已闩锁后仍逐拍记指标,审计连续性,不再判级)。
CREATE TABLE IF NOT EXISTS retreat_metrics (
    trade_date         TEXT NOT NULL,       -- 'YYYYMMDD'
    hhmm               TEXT NOT NULL,       -- 'HHMM' 本 tick 时刻(24h)
    sample_size        INTEGER NOT NULL DEFAULT 0,
    limit_up_count     INTEGER NOT NULL DEFAULT 0,
    limit_down_count   INTEGER NOT NULL DEFAULT 0,
    zaban_count        INTEGER NOT NULL DEFAULT 0,
    zaban_rate         REAL NOT NULL DEFAULT 0.0,
    hot_sector_avg_chg REAL,                -- NULL = 本 tick 无热门板块可比样本(诚实"无数据")
    -- V2-⑧-F(2026-08-03):主线跳水样本的**构成**留痕(codes + 逐码来源标签 + 样本量 +
    -- 种子计数 + 不可用原因)。每拍都落(触发与否都落)——「样本是机械派生、没被 LLM
    -- 塑形」这件事必须事后可审计,不能靠读代码相信。见 `sentinel/mainline.py`。
    hot_sector_sample_json TEXT NOT NULL DEFAULT '{}',
    -- V2-⑧-G-D 追加(review 判定线 🟡-N1 一并处理,2026-08-03):昨日涨停宽度代理
    -- 样本的**需求量 vs 实际采纳量**留痕(codes + size + restricted_from),形状照
    -- 上面 `hot_sector_sample_json` 的姊妹字段。每拍都落,防日后审计炸板率的人把
    -- 池配额压后的实际样本量(如 71)误当成"当天全部涨停股"。见 `sentinel/universe.
    -- py::WatchUniverse.breadth_extra_payload()`。
    breadth_extra_sample_json TEXT NOT NULL DEFAULT '{}',
    triggered_json     TEXT NOT NULL DEFAULT '[]',  -- 本 tick 触发的条件族键(供下一拍持续性判定)
    red_via_json       TEXT NOT NULL DEFAULT '[]',  -- 红色触发路径(multi_condition / persist:<族>),审计留痕
    tier               TEXT NOT NULL DEFAULT 'none', -- none | yellow | red | red_latched
    recorded_at        TEXT NOT NULL,       -- ISO8601
    PRIMARY KEY (trade_date, hhmm)
);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑩-C 决策日志强制表单退役):本表
-- 自 V2.0.0 起不再有任何写入路径(`neckline.decision_log` 只保留 `get_decision`/
-- `list_decisions` 两个只读函数),历史行(v1.2-B~v1.5.2 生产数据)供归因只读,
-- `GET /decisions`/`GET /decisions/{id}/track` 继续读它;`POST /decisions` 复用
-- 同一 URL 但已换血成「用户可选补充」入口(落 `user_actions`,不再碰本表)。
-- DDL 与下面全部历史字段注释**原样保留不删**(不可编辑口径 / 链根语义 /
-- max_chase_pct 语义等对历史行依然成立,只是不会再有新行诞生)。
--
-- v1.2-B 预注册决策日志(plan §五 v1.2-B,§2.1 第 3 条人机协作配套)。下单前录八项
-- (v1.4-⑤-B 起加第⑨项,见下),时间戳先于成交防结果污染;**审计件、非下单件**——
-- 本表任何写入路径(见 `neckline.decision_log`)绝无下单/撤单/拉行情副作用。
-- created_at:**服务端生成**,任何调用方(含 API 入参)都不能覆盖,杜绝预注册时间
-- 被伪造。八项预注册字段:why_buy①/why_entry_price②/target_price③/exit_low+
-- exit_high④/thesis_tags⑤(枚举码 JSON 数组)/invalidation⑥/contingency_scenarios⑦
-- (情景树 JSON 数组,每项 {scenario,trigger,action,matched};scenario/trigger/action
-- 是不可编辑预注册内容,matched 是唯一可事后翻的结果标记,专用端点
-- `set_scenario_outcomes` 才能碰)/playbook_tag⑧(单选枚举码)。
-- **不可编辑口径(历史行,写入口已退役)**:①-⑥ + ⑦的 scenario/trigger/action +
-- ⑧ + ⑨(max_chase_pct)在 v2.0.0 之前全表无任何 UPDATE 语句触碰这些列;改动
-- 只能 `revise_decision` 新增一行,`revision_of` 落**链根** id(该行若自身是修订行则
-- 取其 `revision_of`,否则该行本身即链根)——归因永远 `WHERE revision_of IS NULL`
-- 取首版,或 `WHERE revision_of=<根id>` 一步取全部修订,无需递归遍历链条。
-- status:pending(预注册待决)/filled(成交后经 link 关联)/cancelled(用户放弃)/
-- expired(v1.3-④ 挂单追踪 N 交易日到期自动置,见 `report/pending_track.py`)。position_id:成交后经
-- `link_decision` 回填,关联 `positions.id`(无 SQL 级 FK 约束,同本库其它表惯例)。
-- planned_price/planned_qty:"我打算挂多少价/多少股"(v1.2-B 起既有,一直可选)。
-- max_chase_pct(v1.4-⑤-B,需求 2 补充,⑨,幂等迁移列见 `_COLUMN_MIGRATIONS`,**不在
-- 本 CREATE TABLE 里**——同本库既有惯例,新增列一律只登记进 `_COLUMN_MIGRATIONS`,
-- 靠 `_migrate_columns` 幂等 `ALTER TABLE` 补齐,新库/老库同一条路径):"开盘冲多高
-- 我就放弃、盘中不追补"——**与 planned_price 是两件事,不许合并**,相对昨收百分比
-- (如 3.0=+3%,不是小数 0.03),允许负值(只在低开时买),NULL=用户显式选择"不设
-- 上限"(v2.0.0 之前 API 层要求必须显式传该键才能创建/修订,该校验已随写入口退役)。
CREATE TABLE IF NOT EXISTS decision_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code                 TEXT NOT NULL,
    name                    TEXT,
    created_at              TEXT NOT NULL,       -- ISO8601,服务端生成,客户端不许传
    why_buy                 TEXT NOT NULL,       -- ①为什么买
    why_entry_price         TEXT NOT NULL,       -- ②为什么这个入场价
    target_price            REAL,                -- ③目标价
    exit_low                REAL,                -- ④离场价格区间(下沿)
    exit_high               REAL,                -- ④离场价格区间(上沿)
    thesis_tags             TEXT NOT NULL DEFAULT '[]',  -- ⑤论点标签,枚举码 JSON 数组
    invalidation             TEXT NOT NULL,       -- ⑥证伪条件
    contingency_scenarios   TEXT NOT NULL DEFAULT '[]',  -- ⑦应对方案·情景树,JSON 数组
    playbook_tag            TEXT NOT NULL,       -- ⑧打法标签,枚举码(单选)
    planned_price            REAL,
    planned_qty               INTEGER,
    status                    TEXT NOT NULL DEFAULT 'pending',  -- pending|filled|cancelled|expired
    position_id               INTEGER,             -- 成交后回填,关联 positions.id
    revision_of                INTEGER,             -- 修订链根 id,NULL=首版
    updated_at                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_log_ts_code ON decision_log(ts_code);
CREATE INDEX IF NOT EXISTS idx_decision_log_status ON decision_log(status);
CREATE INDEX IF NOT EXISTS idx_decision_log_revision_of ON decision_log(revision_of);
CREATE INDEX IF NOT EXISTS idx_decision_log_created_at ON decision_log(created_at);

-- 🔴 **V2.2-⑤-B 起停写留档不 DROP**(PROJECT_PLAN §五 ⑤-B 第 3 项;§七 **P4-31** 的停写
-- 留档表由七张扩到**八张**)。2026-08-09 用户裁定 #8「熔断整体删除」——**锁定态 / 次日
-- 只减不加 / 强制复盘解锁三件机制全删**,单日 −4000 档一并删;`sentinel/circuit.py` 现在
-- 只剩一个无状态纯函数 `count_tail_consecutive_stops`(读 `positions`,**不碰本表**),
-- 读写函数一并删(无下游消费方,同 `inquiry_log` 先例;**查历史行走 `sqlite3`**)。
-- **本表因此长期处于「有历史行、零新增行」状态**,守门单测锁死零写入调用点。
-- ⛔ **不 DROP**:历史归因与审计要用,且 DROP 不可逆(要不要 DROP 的三条判据见 §七 P4-31)。
-- 以下为原表注释(**历史留痕,机制已不生效**):
-- v1.2-A2 熔断纪律事件表(plan §五 v1.2-A2 / §2.1 第 7 条,🔴)。连续 3 笔止损 或
-- 单日实现净亏 ≥4000 元 → 触发熔断(当日停开新仓、次日只减不加,完成一次强制复盘后
-- 解锁)。**熔断是纯提醒层**——本表只做「触发/解锁事件留痕 + 派生锁定态」,绝不代
-- 下单/撤单(§3.8);阈值 3/4000 是命名常量(住 `sentinel/circuit.py`,非
-- strategy_versions config——政策值非回测参数,同 FORCED_REVIEW_LOSS_FRAC)。
-- **当前是否锁定 = 派生**:存在 `unlocked_at IS NULL` 的行即锁定(照 CLAUDE.md「审计
-- 时间戳 + 独立消费标记不用一个字段身兼两职」教训,锁/解锁各自落列);已锁定时重复
-- 触发幂等(evaluate 前置查锁定态,不新开第二行)。
-- trigger_reason:consecutive_stops(连续止损)/ daily_loss(单日净亏)。
-- trigger_ref_date:触发所在交易日('YYYYMMDD',= 评估时那笔清仓的 sell_date),
--   周复盘自动解锁按它落到哪个 ISO 周。basis_json:判据留痕(参与判定的 position_id
--   清单 + 当日净盈亏 or 连续止损笔数 + 近似判定笔数 + 时窗),诚实边界「基于台账 N 笔
--   已补录成交」透出用。unlocked_via:review_ack(客户端熔断复盘按钮)/ weekly_review
--   (周复盘覆盖触发周且走强制复盘口径自动解锁)。
CREATE TABLE IF NOT EXISTS circuit_breaker (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at     TEXT NOT NULL,          -- ISO8601 触发时刻
    trigger_reason   TEXT NOT NULL,          -- consecutive_stops | daily_loss
    trigger_ref_date TEXT NOT NULL,          -- 'YYYYMMDD' 触发所在交易日
    basis_json       TEXT NOT NULL DEFAULT '{}',  -- 判据留痕(诚实边界透出)
    unlocked_at      TEXT,                   -- NULL=仍锁定
    unlocked_via     TEXT,                   -- review_ack | weekly_review | NULL
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_circuit_breaker_unlocked ON circuit_breaker(unlocked_at);
CREATE INDEX IF NOT EXISTS idx_circuit_breaker_ref_date ON circuit_breaker(trigger_ref_date);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑬-12,裁定 #9-b「呼吸台账删」):本表
-- 自 V2.0.0 起**无任何读写路径** —— 领域模块 `neckline/breathing.py` 与三个
-- `/breathing*` 端点、客户端 `BreathingLedgerView.swift` 均已物理删除,历史行
-- (v1.2-G~v1.5.2 生产数据)只供归因审计手工查询。DDL 与下面全部历史字段注释
-- **原样保留不删**(对历史行依然成立,只是不会再有新行诞生);⚠ 注释里提到的
-- `neckline.breathing.*` 函数名是**历史语义说明**,那个模块已不存在。
-- 连带:§七 P3-11 的「呼吸 T 净贡献」归因维随之废弃。
--
-- v1.2-G 呼吸试验仓台账 · T 子账(plan §五 v1.2-G,§2.1 第 3 条仓位分配「2 短线追击 +
-- 1 呼吸底仓试验」配套)。底仓是 `positions` 表的一行(语义不变,本表绝不覆盖它);
-- 持有期内的多次日内 T 是「一个底仓 → N 次 T」一对多关系,`positions` 扩列表达不了
-- N 笔,故落本子表——每行 = 一次**已闭合**的 T 回合(先买后卖 / 先卖后买,方向仅供
-- `note` 自由备注,不落结构化列;T 盈亏统一 `=(sell_price−buy_price)×qty−fees`,见
-- `neckline.breathing.compute_t_pnl`)。`fees` 由调用方(客户端录入)如实给,本表 /
-- `neckline.breathing` 模块任何地方都不按费率估算(G.2「不硬编费率」;2 万规模双边
-- 佣金+印花税≈20 元≈0.1% 只是 plan 的背景参考数字,不是常量)。`position_id` 关联
-- `positions.id`(无 SQL 级 FK 约束,同 `decision_log.position_id` 惯例,存在性校验
-- 交应用层 `neckline.breathing.add_trade` 做)。**打法标签唯一源 = `decision_log.
-- playbook_tag`**(v1.2-B ⑧),本表不存第二份——「这个底仓是不是呼吸仓」由它名下
-- 是否有 T 子账体现,不是本表字段。「先手」成本优势(摊薄成本 / 与现价距离)读时
-- 派生、不落列,见 `neckline.breathing.compute_base_cost_adj`/`compute_edge_to_price`。
CREATE TABLE IF NOT EXISTS breathing_t_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL,
    buy_price       REAL NOT NULL,
    sell_price      REAL NOT NULL,
    qty             INTEGER NOT NULL,
    fees            REAL NOT NULL DEFAULT 0.0,
    t_date          TEXT NOT NULL,           -- 'YYYYMMDD'
    note            TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_breathing_t_trades_position_id ON breathing_t_trades(position_id);

-- v1.3-② 持仓 K4 每日体检 + D5 净浮盈快照(plan §五 v1.3-②)。16:35 报告管线对每只 open
-- 持仓在当日 EOD 面板上重算 K4 advisory 命中 + 算好 D5 收盘净浮盈 → 落本表一行(一持仓一
-- 交易日一行,幂等覆盖)。两个消费方:①`GET /positions` 读最近一份快照嵌 k4Advisory[](像
-- watchlist 读体检快照);②**次日 9:25:30 `sentinel/precall.py` 读定格判向**
-- (`time_exit_locked_state`,见下;审计 🔴-1 前是「读最近一份 net_float 重判」,已改)。
-- net_float:D5 收盘净浮盈估算(现价EOD×qty − buy_price×qty − buy_fees实录 − 估算卖出费,
--   见 neckline/fees.py::estimate_net_float);停牌/无 EOD 数据 → NULL(precall 侧退保守判非浮盈)。
-- time_exit_state/max_hold_effective:16:35 权威两档时间退出分类(classify_time_exit,单一源
--   sentinel/precall)。k4_hits_json:命中项 JSON 数组 [{code,label,level,evidence,evidenceStrength}]。
-- time_exit_locked_state / time_exit_locked_date / time_exit_locked_net_float(v1.3 审计修复
--   🔴-1「D5 判一次定格」,2026-07-27 用户拍板方案 A):两档时间退出的**判向定格记录**。
--   16:35 首次遇到 d_count ≥ max_hold_days 的那一天,用**当日 EOD 收盘**净浮盈判一次向
--   (profit_exempt | time_exit_next_day)并写进这三列,此后**逐日原样带过来**(每天的行都
--   自描述「今天生效的判向是哪天、按多少净浮盈定格的」);三个消费点(precall / 16:35 /
--   GET /positions)一律读定格值,**不再用当日最新净浮盈重判**。NULL = 尚未定格(d 未到判定点、
--   或现役单档 K1 章程根本不进两档分支)。理由(勿删):①回测验证过的规则才是能守的规则——
--   逐日重判意味着实盘执行的规则从未被回测验证(引擎 momentum.py::_time_exit_reason 就是判
--   一次定格);②堵死「D5 判该走→用户没走→D6 转浮盈→D7 系统改口豁免」这条**违纪被事后
--   合法化**的路。D15 硬上限(max_hold_days_profit)不受定格影响,仍按 d_count 无条件判。
-- has_strong:是否含「强价量证据」命中(= 触发第六类 APNs 派发警报的门槛,题材类弱证据不计入)。
-- scenario_review:该持仓是否有关联决策日志(via decision_log.position_id)含非空情景树待每日
--   对照(②-D 提醒,勾选仍走既有 scenario-outcome 端点,本表只做「挑出来」)。PK 保证幂等重跑。
-- v1.3-③-C4 消息面告警(plan §五 v1.3-③-C4)。16:35 报告管线对**持仓 + 自选**
-- (不是全市场)票扫描四类消息(减持/立案/暴雷/监管)→ 命中落本表一行。
-- trade_date = **首次记录该事件的报告日**(审计留痕 + 同日重跑幂等的判据一部分,
-- 不是事件本身发生日)。event_date/event_key(2026-07-26 必改新增,plan §五
-- v1.3-③-C4):事件级跨日去重——REDUCTION 类 event_date=TuShare ann_date、
-- event_key=`holder_name|change_vol|change_ratio`,同一事件只在最先扫到它的
-- 那份报告里落一行,此后即使仍在回看窗口内重新扫到,也不再新增行(查询见
-- report/news_alerts_store.py::load_seen_event_keys)。LLM 来源两列恒
-- NULL/''(不参与、也匹配不到跨日去重,维持"可能连续几天重复"的现状,见
-- report/news_alerts.py 模块头)。UNIQUE(ts_code, trade_date, category,
-- event_key) 幂等——同一报告日内的同一事件不重复(同日重跑幂等覆盖为最新一次
-- 扫描结果,同 llm_judgments/holding_eod_check 惯例);**跨日**的去重不是靠这
-- 个约束(trade_date 每天不同,约束管不到跨天),是靠上面说的应用层查询过滤。
-- category 枚举码:REDUCTION(减持,TuShare stk_holdertrade 结构化)/
-- INVESTIGATION(立案)/BLOWUP(暴雷)/REGULATORY(监管)(后三类 LLM 联网搜索,
-- 见该模块 docstring 数据源侦察结论)。source 留痕来源(tushare_holdertrade /
-- llm_<provider>)。**不存 name**(展示名读时从 stock_basic 解析,同
-- llm_judgments 惯例不重复存,见 report/news_alerts_store.py)。
CREATE TABLE IF NOT EXISTS news_alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,       -- 'YYYYMMDD' 首次记录该事件的报告日(审计,非事件日)
    event_date  TEXT,                -- 'YYYYMMDD' 事件本身发生日 | NULL(LLM 来源恒 NULL)
    event_key   TEXT NOT NULL DEFAULT '',  -- 跨日去重键 | ''(LLM 来源恒空串,不参与去重)
    category    TEXT NOT NULL,       -- REDUCTION | INVESTIGATION | BLOWUP | REGULATORY
    summary     TEXT NOT NULL,
    source      TEXT NOT NULL,       -- tushare_holdertrade | llm_glm | llm_kimi 等
    created_at  TEXT NOT NULL,
    UNIQUE(ts_code, trade_date, category, event_key)
);
CREATE INDEX IF NOT EXISTS idx_news_alerts_trade_date ON news_alerts(trade_date);
CREATE INDEX IF NOT EXISTS idx_news_alerts_ts_code ON news_alerts(ts_code);
CREATE INDEX IF NOT EXISTS idx_news_alerts_event ON news_alerts(category, event_date, event_key);

CREATE TABLE IF NOT EXISTS holding_eod_check (
    position_id         INTEGER NOT NULL,
    trade_date          TEXT NOT NULL,          -- 'YYYYMMDD' EOD 日
    d_count             INTEGER NOT NULL DEFAULT 1,
    net_float           REAL,                   -- D5 收盘净浮盈估算 | NULL=停牌/无数据(保守判非浮盈)
    time_exit_state     TEXT NOT NULL DEFAULT 'holding',  -- time_exit_next_day|profit_exempt|hard_cap_exit|holding
    -- ⚠ **V2.2-⑤ 起可空**:`NULL` = 该行当时的现役章程**没有时间退出条款**(`v2.2-k8`,
    -- `max_hold_days=None`)—— 没有"有效硬上限"这回事。⛔ 不拿 5 或 0 顶上(§3.11-E 否决
    -- 哨兵位的同一种病:`('holding', 5)` 在 K1 的 D2 与 K8 的任何一天下会长得一模一样,
    -- 事后归因分不开)。**老库的 `NOT NULL` 由 `_relax_holding_eod_check_notnull` 一次性
    -- 原子放宽**(见其 docstring);`DEFAULT 5` 保留只为老调用点省略该列时不炸。
    max_hold_effective  INTEGER DEFAULT 5,
    k4_hits_json        TEXT NOT NULL DEFAULT '[]',
    has_strong          INTEGER NOT NULL DEFAULT 0,
    scenario_review     INTEGER NOT NULL DEFAULT 0,
    time_exit_locked_state      TEXT,   -- 定格判向 profit_exempt|time_exit_next_day|NULL(未定格)
    time_exit_locked_date       TEXT,   -- 'YYYYMMDD' 定格发生日(审计:哪天判的)
    time_exit_locked_net_float  REAL,   -- 定格所用的当日 EOD 净浮盈(审计:按多少判的)
    created_at          TEXT NOT NULL,
    PRIMARY KEY (position_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_holding_eod_check_position ON holding_eod_check(position_id);
CREATE INDEX IF NOT EXISTS idx_holding_eod_check_trade_date ON holding_eod_check(trade_date);

-- v1.3-④ 挂单未成交追踪(plan §五 v1.3-④,原 v1.2.1-C 全文)。追踪 `decision_log`
-- `status='pending'`(挂了没成交的计划)的后续 N=5 个交易日走势(常量
-- `report/pending_track.py::DECISION_PENDING_TRACK_DAYS`,单一源),检验用户
-- 「逆向选择:专接下坠、错过起飞」假设。16:35 报告管线每天对每条仍 pending 的
-- 决策落一行(同 (decision_id, trade_date) 幂等覆盖,同日重跑不重复)。
-- d_offset:距 `created_at`(创建当日本身不计)后第几个交易日,正常情况下 1..N;
-- 若报告曾断跑导致一次性跳过第 N 天,如实记录【实际】offset(可能 >N)后立即令
-- 该决策过期,不假装观测发生在第 N 天,也绝不让决策卡死在 pending(见该模块
-- docstring `_offset`/`track_pending_decisions`)。close:当日 EOD 收盘(前复权
-- 口径同 `strategy.features.build_research_panel`)。ret_from_plan:相对
-- `planned_price` 的累计收益;`planned_price` 缺失(NULL)时本列亦为 NULL,不臆造。
CREATE TABLE IF NOT EXISTS decision_pending_track (
    decision_id     INTEGER NOT NULL,
    trade_date      TEXT NOT NULL,          -- 'YYYYMMDD' 追踪快照日
    d_offset        INTEGER NOT NULL,       -- 距 created_at 后第几个交易日(见表头注释)
    close           REAL NOT NULL,
    ret_from_plan   REAL,                   -- 相对 planned_price 的累计收益 | NULL(无 planned_price)
    recorded_at     TEXT NOT NULL,
    PRIMARY KEY (decision_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_decision_pending_track_decision ON decision_pending_track(decision_id);

-- ⚠ **V2.1-① 起停写留档**(PROJECT_PLAN §五 V2.1-①「问询台整链退役」,§七 P4-31
-- 由六张扩到七张):问询台整条产品链(端点 / `api/inquiry.py` / 客户端 tab)已物理
-- 删除,写入方 `neckline.api.stores.create_inquiry_log` 随之消失。**本表自此无任何
-- 读写路径**——`list_inquiry_logs`/`get_inquiry_log` 也已删除,不像 `inquiry_pool`
-- 那样留一个只读函数:`inquiry_pool` 仍被周复盘 `review/reconcile.py` 消费(对历史
-- 成交的归因判定),本表**零下游消费方**,读函数留着就是死码(同 `watchlist` 表
-- v2.0.0-⑬-11 的处置口径)。历史行(v1.4~v2.0.0 生产数据)只供审计,查询走 `sqlite3`
-- 直连,不再有任何 Python API。DDL 与下面的历史字段注释**原样保留不删**;⚠ 注释里
-- 提到的 `api/inquiry.py`/`POST /inquiry`/`InquiryOut` 均已不存在。
--
-- v1.4-⑦-B 问询记录档案(plan §五 v1.4-⑦-B / §七 P3-13)。`POST /inquiry` 每问一次
-- 落一行(答案已经算好之后落库,失败不影响当次回答——旁路写入,见
-- `api/inquiry.py::run_inquiry` 结尾的 try/except)。**与 `inquiry_pool` 是两件事,
-- 不要混用**:`inquiry_pool` 是 v1.3.3 已退役的历史队列表(海选票「待哪份报告消费」,
-- 见上方该表注释),本表是问询问答本身的档案记录——**纯追加(append-only)、无
-- "消费"语义**:每行落库即完整、终态,不等待任何下游处理,故不需要「审计时间戳 +
-- 独立消费标记」两字段拆分那一套(那是给队列表用的模式,见 CLAUDE.md `inquiry_pool`
-- 掉缝教训;本表天生不是队列,套用会画蛇添足)。
-- question:本轮实际问题(messages 里最后一条非空 user 消息;messages=[] 或全是
-- assistant 时落空串,代表"只看这只票的材料,没有具体追问")。
-- materials_json:确定性材料快照(DeterministicResult 摘要,不含 evidence——那是
-- 独立列,同 `InquiryOut.evidence` 一份数据两处落地不重复定义)。
-- answer:LLM 回答原文,或降级路径 `_degraded_reply` 的文案(降级同样是"实质回答",
-- 一并落档,不是只存成功案例)。
-- evidence_json/search_hits_json:分别对应 `InquiryOut.evidence`(展示事实条目)与
-- 本次联网搜索命中全文(同 `llm_judgments.search_hits_json` 惯例,供事后审计"当时
-- 搜到了什么";降级/未触发搜索时落 '[]',不是"确认无消息"——那是两回事)。
-- verdict:纯描述性标注(已分析/已分析·有风险提示),不是判决(§2.5 v1.3.3)。
-- position_id/decision_id:**当前无写入方**——`POST /inquiry` 未接收这两个入参,
-- 列存在只为未来人工关联/归因(P3-11)留口子,新行恒 NULL,不代表悬空引用。
CREATE TABLE IF NOT EXISTS inquiry_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,       -- ISO8601,服务端生成
    ts_code             TEXT NOT NULL,
    name                TEXT,
    question            TEXT NOT NULL DEFAULT '',
    materials_json      TEXT NOT NULL DEFAULT '{}',
    answer              TEXT NOT NULL,
    evidence_json       TEXT NOT NULL DEFAULT '[]',
    search_hits_json    TEXT NOT NULL DEFAULT '[]',
    verdict             TEXT NOT NULL,
    position_id         INTEGER,
    decision_id         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inquiry_log_ts_code ON inquiry_log(ts_code);
CREATE INDEX IF NOT EXISTS idx_inquiry_log_created_at ON inquiry_log(created_at);

-- v1.4-⑩-A(§七 P0-23)行业强度预计算物化表。读写单一通道 =
-- `neckline/report/industry_strength_store.py`(判据实现仍只有一份,在
-- `report/industry_strength.py`;本表只是**缓存物化**,不是第二套判据)。
-- **为什么存 SQLite 而不是 parquet**(五条,勿"改进"回去):①体量小到不该分区
-- (~110 行/日,全历史 ~17.5 万行 ≈ 20MB),parquet 日分区会再造 1500+ 个小文件
-- = P0-23 的病根本身;②读模式是「按日点查 + 按行业跨日回看」,SQL 索引天生合适;
-- ③完全绕开 parquet schema 漂移雷区(v1.3.5 两次崩报告那条链),不必新增
-- `_VALID_TABLES`/`TABLE_FLOAT_COLS` 声明;④`PRIMARY KEY` upsert 天然幂等;
-- ⑤跟着 `neckline.db` 一起被 `.backup` 备份,不另立备份纪律。
-- **三个可空列的语义定死:`NULL ≠ 0`** —— `NULL` = 「当日该行业成员数 < min_members,
-- 没评」(**没看**);`0` = 「评了,不是强度日 / 持续 0 天」(**看了,没有**)。承 §3.8
-- 「『没有』与『没看』必须能分开」,读侧不许 `or 0` 抹平。
-- **落全部行业(member_count >= 1),不只达标行业**:同一张表同时喂两个消费方——判据侧
-- (A2/B3 + 排序键①,只吃 `industry_rank IS NOT NULL` 的行)与 ④ 信息卡的 60 日行业中位数
-- 序列(`industry_median_return_series` 口径本就不受 `_MIN_MEMBERS` 约束)。
-- **`quantile`/`min_members` 是口径指纹**:读侧只接受与当前常量相等的行,不等 → 视同缺行
-- (走保险丝)+ WARNING「口径已变更,请重跑 bootstrap」。口径一改,全表必须重算。
CREATE TABLE IF NOT EXISTS industry_strength_daily (
    trade_date      TEXT NOT NULL,      -- 'YYYYMMDD'(同 holding_eod_check / decision_pending_track 惯例)
    industry        TEXT NOT NULL,      -- stock_basic.industry 原文
    median_ret      REAL NOT NULL,      -- 当日该行业成员 ret_1d 中位数(所有行业都有,含未达标行业)
    member_count    INTEGER NOT NULL,   -- 当日有 ret_1d 的成员数(>=1 才落行)
    industry_rank   INTEGER,            -- NULL = 当日未参与排名(member_count < min_members)
    is_strength_day INTEGER,            -- NULL = 当日未参与评定;0/1 = 参与了且不是/是强度日
    persist_days    INTEGER,            -- NULL = 当日未参与评定;>=0 = 连续强度日(断裂重置)
    quantile        REAL NOT NULL,      -- 产出该行时的 _STRENGTH_QUANTILE(口径指纹)
    min_members     INTEGER NOT NULL,   -- 产出该行时的 _MIN_MEMBERS(口径指纹)
    computed_at     TEXT NOT NULL,      -- ISO8601(审计:这行什么时候算的)
    PRIMARY KEY (trade_date, industry)
);
CREATE INDEX IF NOT EXISTS idx_industry_strength_daily_industry ON industry_strength_daily(industry, trade_date);

-- ⚠ **v2.0.0 起停写留档**(PROJECT_PLAN §五 V2-⑬-3「单票参考件三件套展示位 → 删」):
-- 生成侧 `report/reference_plan.py` 与读写通道 `report/reference_plan_store.py`
-- **已物理删除**,契约键 `CandidateOut.referencePlan` 随候选榜一并退役。历史行供归因
-- 只读(要读得自己写 SQL,应用层已无访问函数)。
-- ⚠ **夹逼 / 冻结 / 口径指纹 / disclaimer 这套体例不是废弃,是整套移交篮子卡**
-- (`selection/basket_card.py`,§2.8-A 对照表);围栏 JSON 解析器早在 ⑤ 就搬去了通用件
-- `neckline/llm/json_block.py`(⑬-3 明文「删 reference_plan.py 时只删那层再导出,
-- 别把通用件一起陪葬」),它现在服务 ⑤ 聚合层与 ⑦ 卡生成。
--
-- v1.5-①-E 参考件三件套(需求 9,§2.0 第〇原则「参考件必须落库,将来与实际走势/
-- 成交对拍 = LLM 参谋成绩单」)。读写单一通道 = `neckline/report/reference_plan_store.py`,
-- 生成侧唯一实现 = `neckline/report/reference_plan.py`。**参考件不进任何机器判据**——
-- 本表只被落库/审计/未来的对拍报表(§七 P3-11)消费,哨兵/推送/排序键/候选去留一律
-- 不读本表(§2.0 第一条,三条守门单测锁死)。
-- `status`:ok(通过+至少一件有效)| vetoed(否决,三件套全 null)|
-- unavailable(LLM未激活/调用失败/JSON解析失败/本块异常,三件套全 null——"没看"不是
-- "没有")。`buy_clamp`/`exit_clamp` 留夹逼判定留痕("没给"absent 与"给了被拦"
-- rejected_* 分开,不许混同)。`stop_pct` 是口径指纹(产出该行时的现役止损比例,
-- 供将来回看"这条参考件是在哪版章程下生成的")。
CREATE TABLE IF NOT EXISTS reference_plans (
    trade_date    TEXT NOT NULL,          -- 'YYYYMMDD'
    ts_code       TEXT NOT NULL,
    status        TEXT NOT NULL,          -- ok | vetoed | unavailable
    verdict       TEXT NOT NULL,          -- 既有审判标签:通过|否决|未激活
    close         REAL NOT NULL,          -- 当日收盘(参考件的锚,审计用)
    limit_up      REAL,                   -- 当时算的明日涨停价(NULL=算不出)
    limit_down    REAL,                   -- 当时算的明日跌停价
    buy_low       REAL, buy_high  REAL,   -- NULL = 未给 / 被拦(看 buy_clamp)
    buy_clamp     TEXT NOT NULL,          -- ok|absent|rejected_out_of_limit|rejected_malformed|rejected_no_limit
    buy_why       TEXT,
    stop_price    REAL,                   -- 系统算的 close×(1-stop_pct),非 LLM 产出
    stop_pct      REAL,                   -- 产出该行时的现役 stop_pct(口径指纹)
    -- take_profit_retrace(v1.5.1,两线 review 共同的「章程 −5%/回落止盈 8% 硬编文案」修复):
    -- 产出该行时的现役回落止盈比例,与 stop_pct 成对的第二个口径指纹,供展示层动态生成
    -- 标签(章程一改,数字与标签同步走)。由 `_migrate_columns` 幂等补列——**不在本
    -- CREATE TABLE 里**,同本库既有惯例(见 llm_judgments.search_engine / decision_log.
    -- max_chase_pct):生产 v1.5.0 已建过本表,新库老库必须走同一条补列路径。
    exit_low      REAL, exit_high REAL,
    exit_clamp    TEXT NOT NULL,          -- ok|absent|rejected_malformed
    exit_why      TEXT,
    script_text   TEXT,
    veto_reason   TEXT,
    provider      TEXT, model TEXT,
    degraded      INTEGER NOT NULL DEFAULT 0,
    degrade_reason TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_reference_plans_code ON reference_plans(ts_code, trade_date);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.0.0 新表(plan §五 V2-① 表与共享信息层地基,2026-08-02 立项)。18 张 SQLite 新表
-- 一次到位(另 2 张 parquet 表 `intraday_ticks`/`auction_snapshots` 声明在
-- `neckline/data/market_data.py`)。三类分存(§2.8-B 第 1 条):事实(EOD 预计算,
-- corr_matrix_daily / limit_cluster_daily / leader_structure_daily)/ 用户行为
-- (user_actions,append-only)/ 模型判断(baskets 系列 / tier_history / position_plans /
-- profile 系列 / selection_packs 系列),互不覆盖。冻结 / 追加 / 不回写三律的机器判据见
-- `tests/test_v2_schema_guard.py`;本节只建表,读写逻辑留给各消费块(建表块/写入块对照
-- 见 PROJECT_PLAN §五「V2 新表汇总」)。
-- ══════════════════════════════════════════════════════════════════════════

-- 模型判断:篮子本体(D0 冻结,plan §五 V2-①/⑦)。basket_key = crc32(trade_date|driver_slug)
-- 十六进制串(跨进程可复现,承 §五「铁律」zlib.crc32 纪律),UNIQUE(trade_date, basket_key)
-- 天然幂等去重。engine_api_version/charter_version/pack_version 是口径指纹(同
-- reference_plans.stop_pct 既有惯例),供事后回看"这个篮子是在哪版引擎/章程/策略包下
-- 生成的"。
CREATE TABLE IF NOT EXISTS baskets (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date         TEXT NOT NULL,              -- D0
  basket_key         TEXT NOT NULL,              -- crc32(trade_date|driver_slug) 十六进制,跨进程可复现
  name               TEXT NOT NULL,
  driver             TEXT NOT NULL,              -- 共同驱动(一句话,LLM 产出)
  driver_kind        TEXT NOT NULL,              -- theme|policy|event|commodity|overseas|rotation|limit_cluster
  tier               INTEGER NOT NULL,           -- 1|2|3
  pack_version       TEXT NOT NULL,              -- 现役策略包版本(归因用)
  engine_api_version INTEGER NOT NULL,
  charter_version    TEXT NOT NULL,              -- 生成时的现役章程(口径指纹)
  via                TEXT NOT NULL DEFAULT 'auto',   -- auto | preseed
  evidence_status    TEXT NOT NULL DEFAULT 'ok',     -- ok | search_unavailable | partial
  created_at         TEXT NOT NULL,
  UNIQUE(trade_date, basket_key)
);

-- 模型判断:篮子成员与角色(plan §五 V2-①/⑦),含机械/LLM 角色对拍分歧。role_conflict=1
-- 时两侧判断不一致——分歧原样入卡展示,不静默采信任一方;is_primary 处理"同票多篮"场景
-- 的主归属(行业闸 lift 最高者唯一为 1)。
CREATE TABLE IF NOT EXISTS basket_members (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id       INTEGER NOT NULL,
  ts_code         TEXT NOT NULL,
  role_llm        TEXT NOT NULL,          -- leader | core | elastic
  role_mech       TEXT,                   -- 来自 leader_structure_daily;NULL=机械侧无判定
  role_conflict   INTEGER NOT NULL DEFAULT 0,   -- 1 = 两侧冲突,分歧入卡不静默采信
  reason          TEXT NOT NULL,          -- 为何是这只而不是同题材其他票
  is_primary      INTEGER NOT NULL DEFAULT 1,   -- 主归属篮(同票多篮时唯一 1)
  created_at      TEXT NOT NULL,
  UNIQUE(basket_id, ts_code)
);

-- 模型判断:篮子卡(plan §五 V2-①/⑦,蓝图 4.6)。**冻结表**——写入即不可改,D+1 追加
-- 新 version 而不覆盖旧版本(version=1 恒是 D0 原判)。本表任何行一律只 INSERT,不提供
-- 修改或抹除既有行的路径(靠"没有那个路径"担保,不靠自觉;守门单测见
-- `tests/test_v2_schema_guard.py`——UNIQUE(basket_id, version) 撞键即报错 + 全仓文本扫描
-- 断言看不到会改写本表既有行的语句)。stop_pct/take_profit_retrace/charter_version/
-- pack_version/engine_api_version 是生成当时的口径指纹快照。
CREATE TABLE IF NOT EXISTS basket_cards (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id          INTEGER NOT NULL,
  version            INTEGER NOT NULL,          -- 1 = D0 原判;D+1 追加 2,3…
  card_json          TEXT NOT NULL,             -- 蓝图 4.6 全项 + 结构化 spec + disclaimer
  stop_pct           REAL,                      -- 口径指纹(生成时现役 config)
  take_profit_retrace REAL,
  charter_version    TEXT,
  pack_version       TEXT,
  engine_api_version INTEGER,
  created_at         TEXT NOT NULL,
  UNIQUE(basket_id, version)
);

-- 模型判断:验证状态流水(plan §五 V2-①/⑧)。**append-only**——每次盘中/EOD 观测到新
-- 状态就追加一行,不回改早前行(状态演变本身是审计对象,"曾经 partial 后来 verified"
-- 不该被抹去)。读侧取"最新状态"用 `ORDER BY id DESC LIMIT 1`,不是覆盖写。
CREATE TABLE IF NOT EXISTS basket_verification (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id     INTEGER NOT NULL,
  trade_date    TEXT NOT NULL,          -- D+1
  observed_at   TEXT NOT NULL,          -- ISO8601 北京时间
  state         TEXT NOT NULL,          -- verified | partial | unclear | falsified
  source        TEXT NOT NULL,          -- intraday | eod
  evidence_json TEXT NOT NULL DEFAULT '{}',   -- 命中了哪条结构化条件
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_basket_verif ON basket_verification(basket_id, trade_date);

-- 模型判断:每日复盘(plan §五 V2-①/⑨)。机械判(mech_json)与 LLM 解释(llm_text)分列,
-- llm_text=NULL 明确表示"未生成"(预算耗尽/降级),不拿空串冒充"生成了但没内容"
-- (§3.8「没有」与「没看」必须分开)。UNIQUE(basket_id, review_date) 幂等覆盖同日重跑。
CREATE TABLE IF NOT EXISTS basket_review_daily (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id     INTEGER NOT NULL,
  review_date   TEXT NOT NULL,          -- 被复盘的那个交易日(= D+1)
  depth         TEXT NOT NULL,          -- full(T1/T2) | brief(T3)
  mech_json     TEXT NOT NULL,          -- 九项机械判 + 数据来源标注(存拍/EOD近似)
  llm_text      TEXT,                   -- NULL = 未生成(预算或降级),不冒充"没内容"
  llm_skip_reason TEXT,
  degraded      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE(basket_id, review_date)
);

-- 模型判断:Tier 定档留痕(plan §五 V2-①/⑥)。rank_mech(机械序)与 rank_in_tier(LLM
-- 微调后最终序)两者都落库(§2.8-C 第 1 条:LLM 只能改档内序,微调理由 llm_reason 必须
-- 留痕,不许只存最终结果、抹掉机械原始序)。UNIQUE(trade_date, basket_id) 一日一档。
CREATE TABLE IF NOT EXISTS tier_history (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date      TEXT NOT NULL,
  basket_id       INTEGER NOT NULL,
  tier            INTEGER NOT NULL,
  mech_score      REAL NOT NULL,
  mech_breakdown_json TEXT NOT NULL,    -- 五维分项 + 权重(来自现役包)
  rank_in_tier    INTEGER NOT NULL,     -- 最终序
  rank_mech       INTEGER NOT NULL,     -- 机械序(LLM 微调前)
  llm_rank_delta  INTEGER NOT NULL DEFAULT 0,
  llm_reason      TEXT,
  pack_version    TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  UNIQUE(trade_date, basket_id)
);

-- 事实(EOD 预计算,plan §五 V2-①/④,P0-23 纪律:在线只读、不现算):板内/簇内滚动
-- 20 日相关。corr=NULL 表示样本不足(禁写 0——0 是"算出来不相关",NULL 是"算不出",
-- 两者不同)。约定 code_a < code_b 只存一遍,不重复存对称的两行。
CREATE TABLE IF NOT EXISTS corr_matrix_daily (
  trade_date  TEXT NOT NULL,
  scope_key   TEXT NOT NULL,      -- 簇 or 板块的稳定键
  code_a      TEXT NOT NULL,
  code_b      TEXT NOT NULL,      -- 约定 code_a < code_b,不存两遍
  window      INTEGER NOT NULL,   -- 20
  corr        REAL,               -- NULL = 样本不足,禁写 0
  n_obs       INTEGER NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, scope_key, code_a, code_b, window)
);

-- 事实(EOD 预计算,plan §五 V2-①/④):涨停共振簇。cluster_key 同样走 crc32 稳定键
-- (跨进程/跨天可复现,承 §五铁律)。
CREATE TABLE IF NOT EXISTS limit_cluster_daily (
  trade_date       TEXT NOT NULL,
  cluster_key      TEXT NOT NULL,   -- crc32 稳定键
  ts_code          TEXT NOT NULL,
  cluster_kind     TEXT NOT NULL,   -- same_day | consecutive
  cluster_size     INTEGER NOT NULL,
  consecutive_days INTEGER NOT NULL,
  anchor_industry  TEXT,
  anchor_concept   TEXT,
  computed_at      TEXT NOT NULL,
  PRIMARY KEY (trade_date, cluster_key, ts_code)
);

-- 事实(EOD 预计算,plan §五 V2-①/④):簇内龙头结构。rs_rank/limit_height=NULL 表示
-- 算不出(禁写 0,同 corr 列的纪律);role_mech 是 basket_members.role_mech 的机械侧来源。
CREATE TABLE IF NOT EXISTS leader_structure_daily (
  trade_date   TEXT NOT NULL,
  cluster_key  TEXT NOT NULL,
  ts_code      TEXT NOT NULL,
  rs_rank      INTEGER,          -- NULL = 算不出,禁写 0
  limit_height INTEGER,
  amount_share REAL,
  role_mech    TEXT NOT NULL,    -- leader | core | elastic | unknown
  computed_at  TEXT NOT NULL,
  PRIMARY KEY (trade_date, cluster_key, ts_code)
);

-- 用户行为(plan §五 V2-①,§2.8-B 第 1 条):**唯一落点,append-only**。读写单一实现
-- `neckline/user_actions.py`——该模块只提供 `record`(INSERT)与 `list_actions`(只读
-- 查询),不提供任何修改或抹除既有行的函数(靠"没有那个函数"担保,不靠自觉;守门单测
-- 见 `tests/test_v2_schema_guard.py`)。
-- ⚠ **两列刻意不同时区**(契约线审计 🟡 Y2,2026-08-03 收口):`occurred_at` = 事件发生
-- 时刻,走**北京时间**(市场时刻轴,`calendar.CN_TZ`);`created_at` = 落库审计戳,走
-- **UTC**(同 `strategy_versions.activated_at` 与全仓 store 惯例)。归一在写侧收口
-- (`user_actions.normalize_occurred_at`),**这一列里永远只有一种时区** —— 过滤与排序
-- 都是字符串比较,混了时区就会静默筛错时段。⛔ 别"统一"这两列,各自 docstring 已定死。
CREATE TABLE IF NOT EXISTS user_actions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at  TEXT NOT NULL,     -- ISO8601 **北京时间**(+08:00,写侧归一,见上方注释)
  kind         TEXT NOT NULL,     -- view | select | buy | sell | alert | label | voice_note
  ts_code      TEXT,
  basket_id    INTEGER,
  position_id  INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at   TEXT NOT NULL     -- ISO8601 **UTC**(审计戳,与 occurred_at 不同轴,见上)
);
CREATE INDEX IF NOT EXISTS idx_user_actions_kind_time ON user_actions(kind, occurred_at);

-- 事实(plan §五 V2-①/⑩):开仓自动快照,决策日志强制表单的替代——`decision_log` 自此
-- 停写留档(表保留,不新增行,不 DROP)。**冻结表**——一笔持仓一行(UNIQUE(position_id)),
-- 写入即不可改(同 basket_cards 的冻结纪律,共用同一份守门单测)。
CREATE TABLE IF NOT EXISTS entry_snapshots (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id    INTEGER NOT NULL,
  ts_code        TEXT NOT NULL,
  trade_date     TEXT NOT NULL,
  basket_id      INTEGER,          -- NULL = 非篮子来源(手动补录)
  card_version   INTEGER,
  tier           INTEGER,
  role           TEXT,
  snapshot_json  TEXT NOT NULL,    -- 机器可知的一切:价量/涨幅/换手/量比/板块强度/资金流/红黄牌/竞价表现…
  created_at     TEXT NOT NULL,
  UNIQUE(position_id)
);

-- 模型判断(plan §五 V2-①/⑩):持仓计划,版本化——新版本(用户调整/系统重算)不改写
-- 篮子卡原始判断(§2.8-B 第 2 条「不得因持仓盈亏回头修改原始选股结论」的落地)。
-- version=1 恒从篮子卡继承。
CREATE TABLE IF NOT EXISTS position_plans (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id         INTEGER NOT NULL,
  version             INTEGER NOT NULL,   -- 1 = 从篮子卡继承
  source_basket_id    INTEGER,
  source_card_version INTEGER,
  plan_json           TEXT NOT NULL,      -- 建仓区间/最高追价/离场参考/验证失效/主要风险
  note                TEXT,
  created_at          TEXT NOT NULL,
  UNIQUE(position_id, version)
);

-- 用户行为(plan §五 V2-①/⑪):自然语言临时提醒。rule_json 是哨兵唯一判据(结构化,
-- 不是 nl_text 本身——§2.8-C 第 2 条「LLM 产出的自由文本一律不进哨兵判据」的落地);
-- nl_text 只留痕用户原话。expires_at 为 NULL 且 persist=0 时收盘自动失效。可改(用户
-- 自己的规则,取消/编辑走用户显式操作,不受三律约束)。
CREATE TABLE IF NOT EXISTS custom_alerts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_code          TEXT,               -- NULL = 大盘级
  nl_text          TEXT NOT NULL,      -- 用户原话(留痕)
  rule_json        TEXT NOT NULL,      -- 结构化规则(哨兵唯一判据)
  active_from      TEXT,               -- 'HH:MM' 生效窗起
  active_to        TEXT,
  expires_at       TEXT,               -- NULL 且 persist=0 → 收盘自动失效
  persist          INTEGER NOT NULL DEFAULT 0,   -- 1 = 显式长期有效
  cooldown_seconds INTEGER NOT NULL DEFAULT 0,
  max_fires        INTEGER NOT NULL DEFAULT 1,
  fired_count      INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'active',  -- active | expired | cancelled
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

-- 模型判断(plan §五 V2-①/⑫):偏好画像 / 能力画像,两张账分开、每期一版(不覆盖旧期,
-- UNIQUE(as_of_date, dimension, value) 天然按期分行)。confidence 样本不足一律 low 并
-- 标注(不静默拿低样本量冒充可信结论)。**初期不得反向影响客观 Tier**(§2.8-B 第 5 条)。
CREATE TABLE IF NOT EXISTS profile_preference (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date   TEXT NOT NULL,
  dimension    TEXT NOT NULL,     -- theme | role | entry_style | tier | auction_habit
  value        TEXT NOT NULL,
  share        REAL NOT NULL,     -- 占比
  sample_n     INTEGER NOT NULL,
  window_start TEXT NOT NULL,
  window_end   TEXT NOT NULL,
  confidence   TEXT NOT NULL,     -- low | medium | high(样本不足一律 low 并标注)
  computed_at  TEXT NOT NULL,
  UNIQUE(as_of_date, dimension, value)
);
CREATE TABLE IF NOT EXISTS profile_capability (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date    TEXT NOT NULL,
  dimension     TEXT NOT NULL,
  value         TEXT NOT NULL,
  sample_n      INTEGER NOT NULL,
  win_rate      REAL, profit_factor REAL, avg_mfe REAL, avg_mae REAL,
  vs_peer_delta REAL,              -- 相对同篮未选成员
  window_start  TEXT NOT NULL, window_end TEXT NOT NULL,
  confidence    TEXT NOT NULL,
  computed_at   TEXT NOT NULL,
  UNIQUE(as_of_date, dimension, value)
);

-- 策略包(plan §五 V2-①/③,§12.1/§12.4):**声明式配置包,不是代码插件**——包里只装
-- 参数与规则声明,执行引擎永远住系统线仓库(§12.1 定案,勿重开)。append-only + 单
-- 现役:新包版本追加行,`is_active`/`activated_at` 像 `strategy_versions` 一样在唯一
-- 现役行上切换——这不违反"追加"三律(三律守门单测覆盖的是 user_actions /
-- basket_verification / selection_pack_activation_log 三张纯事件表;`selection_packs`
-- 与 `strategy_versions` 同属"版本注册表",激活切换属正常职责,事件本身另落
-- activation_log,同 `strategy_versions` vs `strategy_activation_log` 的既有分工)。
CREATE TABLE IF NOT EXISTS selection_packs (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_version       TEXT NOT NULL UNIQUE,
  name               TEXT NOT NULL,
  engine_api_version INTEGER NOT NULL,
  manifest_json      TEXT NOT NULL,
  config_json        TEXT NOT NULL,
  evidence_ref       TEXT,               -- research/*.md 指针
  is_active          INTEGER NOT NULL DEFAULT 0,
  created_at         TEXT NOT NULL,
  activated_at       TEXT
);
-- 「任一时刻至多一个现役包」的**库级约束**见 `_POST_MIGRATION_INDEXES`(契约线审计 🔵 B3)。
-- ⚠ 刻意**不写在这里**:`_SCHEMA` 走 `executescript` 一把梭,老库上若已存在两行
-- is_active=1(手工 SQL 遗留),这条 CREATE 会抛 IntegrityError 并**中断整个建表脚本**
-- —— 一个本该"防止新脏数据"的约束会把已有脏数据的库直接锁死开不了机。
-- 策略包激活事件流(plan §五 V2-①/③)。**append-only 事件流**,照 `strategy_activation_log`
-- 先例单列一表——"版本注册表"与"激活事件"分开落点,不把事件塞进版本表。
CREATE TABLE IF NOT EXISTS selection_pack_activation_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pack_version TEXT NOT NULL,
  action       TEXT NOT NULL,      -- activate | deactivate
  via          TEXT NOT NULL,      -- cli | seed
  note         TEXT,
  at           TEXT NOT NULL       -- UTC(同 brain._now() 口径)
);

-- LLM Provider 注册表(plan §五 V2-①/②,取代 GLM/Kimi 枚举,§3.10-B)。可改(用户自填/
-- 编辑),不受三律约束。api_key 绝不出现在任何 HTTP 响应里(同 app_settings.llm_api_key
-- 既有安全纪律,§3.4「高危区」);`app_settings` 装不下 N 个 provider(id=1 单行表
-- CHECK 约束),注册表另落本表,**路由**留在 `app_settings` 新列(见下 `_COLUMN_MIGRATIONS`)。
CREATE TABLE IF NOT EXISTS llm_providers (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT NOT NULL UNIQUE,   -- 用户自取,如 deepseek / glm
  base_url       TEXT NOT NULL,
  model          TEXT NOT NULL,
  api_key        TEXT,                   -- 绝不出现在任何 HTTP 响应里
  has_web_search INTEGER NOT NULL DEFAULT 0,
  search_engine  TEXT,                   -- 带检索时的引擎取值(沿用 v1.5 埋点口径)
  notes          TEXT,
  enabled        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════════════════
-- V2-④b 追加(2026-08-02,K7 需求 1b):第 21 张新表,①/④ 均已完工提交,本表
-- **不回头改那两块的 DDL 段**,单独作为 ④b 自己的施工内容追加在这里(同 ① 既有
-- 体例:CREATE TABLE IF NOT EXISTS 天然幂等,新表不进 `_COLUMN_MIGRATIONS`)。
-- ══════════════════════════════════════════════════════════════════════════

-- 事实(EOD 预计算,plan §五 V2-④b,K7 需求 1b):行业题材阶段六态状态机,取代
-- `driver_freshness` 维度原先借用的 `stock_persist_days` 单调函数。stage 六态
-- 英文码(ignition|fermentation|overheat|divergence|ebb|none)唯一中英映射源见
-- `neckline/scan/stage.py::STAGE_LABELS`。persist_days 直接读自
-- `industry_strength_daily`(单一源,本表不重算、不改动那张表);limit_up_count
-- 读自 `data/limit_derived.py`,NULL 表示当日该表分区缺失、算不出(禁写
-- 0——0 是"该行业当日零涨停"的真值,与"算不出"必须分开)。
CREATE TABLE IF NOT EXISTS industry_stage_daily (
  trade_date       TEXT NOT NULL,          -- 'YYYYMMDD'
  industry         TEXT NOT NULL,          -- stock_basic.industry 口径
  stage            TEXT NOT NULL,          -- ignition|fermentation|overheat|divergence|ebb|none
  is_strength_day  INTEGER NOT NULL,       -- 0/1,留痕供复现
  persist_days     INTEGER,                -- 读自 industry_strength_daily,NULL=该表当日无行
  limit_up_count   INTEGER,                -- 当日该行业涨停家数,NULL=算不出(禁写 0)
  member_count     INTEGER,                -- 参与中位数的成员数(<5 时强度日恒 0)
  stage_reason     TEXT,                   -- 落到该态的判据留痕(可读)
  spec_fingerprint TEXT NOT NULL,          -- 口径指纹:q/成员数下限/近N日窗口 的序列化
  computed_at      TEXT NOT NULL,
  PRIMARY KEY (trade_date, industry)
);
CREATE INDEX IF NOT EXISTS idx_industry_stage_date ON industry_stage_daily(trade_date);

-- ══════════════════════════════════════════════════════════════════════════
-- V2-⑯-D 补记(2026-08-04,定向小修):⑥ 溢出篮**跨进程**留痕。不进「篮子四表」
-- (`baskets`/`basket_members`/`tier_history`/`basket_cards`,`neckline/selection/
-- basket_store.py` 的唯一写入口)——单独一张表追加在这里,同 ④b 的既有体例
-- (新表不回头改早前 DDL 段、不进 `_COLUMN_MIGRATIONS`)。
-- ══════════════════════════════════════════════════════════════════════════

-- ⚠ **这不是给溢出篮一个身份**:⑥-b-C 的裁定原样有效——溢出篮不臆造 tier、不落
-- `baskets`、不落 `tier_history`,永远没有 `basket_id`。⑥-b-C 写下那条裁定时晚间链
-- 还是单进程,"留痕在内存态 `TierResult.dropped`"天然够用;⑯-D 把 ⑤⑥⑦
-- (`neckline-basket.service`)与报告(`neckline-report.service`)拆进两个进程后,
-- 内存态活不过进程边界,报告 ③b 因此**永远**看到 `None`(三条出路见 `deploy/
-- neckline-report.service` 头部,本表是出路③的落地)。
-- 本表**只是搬运工,不是审计账本**:`trade_date` 主键,`INSERT OR REPLACE` 整行
-- 覆写(同日重跑 = 只认最近一次 ⑤⑥ 的结果,不追加历史、不提供按篮子查询的入口)。
-- 三态靠"有没有这一行"+ 数组是否为空区分(与 `EveningChainResult.dropped_baskets`
-- 的 `None`/`[]`/`[...]` 同一套纪律,见 `report/evening.py` 模块头):
--   无行            = ⑤⑥ 本次(迄今)没跑过;
--   有行、`[]`      = 跑了,今天零溢出;
--   有行、非空数组   = 跑了,有溢出。
-- 读写唯一实现:`neckline/selection/basket_dropped_handoff.py`。
CREATE TABLE IF NOT EXISTS basket_dropped_handoff (
  trade_date   TEXT PRIMARY KEY,
  dropped_json TEXT NOT NULL,      -- [{basket_key, reason, mech_score}, ...],允许 []
  created_at   TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════════════════
-- §七 P0-39(2026-08-05,生产实打):⑤ 驱动聚合的**段状态**跨进程留痕。
-- 同 ⑯-D 的既有体例:新表追加在这里,不回头改 ⑤/⑥/⑦ 的 DDL 段、不进
-- `_COLUMN_MIGRATIONS`,更**不碰篮子四表**(`baskets`/`basket_members`/
-- `tier_history`/`basket_cards` 的冻结语义原样不动)。
-- ══════════════════════════════════════════════════════════════════════════

-- **为什么非有这张表不可**:`baskets` 零行有两种完全相反的成因——「⑤⑥ 跑过、
-- 今天确实没有够格的篮子」(合法市场结论)与「⑤ 压根没跑成(no_provider /
-- 预算尽 / 调用失败)」(系统缺席)。零行本身分不出这两件事,报告 ③ 节据此把
-- 后者渲染成了前者,即 P0-39。本表只回答一件事:**最近一次 ⑤ 在这个交易日
-- 跑成什么样**。判读逻辑(哪些段状态算"跑过")的唯一实现在
-- `neckline/selection/basket_stage_handoff.py`,⛔ 报告层不许自己再推一遍。
-- 三态与 `basket_dropped_handoff` 同一套纪律:
--   无行                        = ⑤ 本次(迄今)没跑过 → ③ 如实标「本段未取得」;
--   有行、`reason_stage=ok`     = 跑了 → 零篮子是真结论,可以说「今日无篮子达标」;
--   有行、`reason_stage` 是缺席码 = 没跑成 → ③ 标「本段未取得」+ 原因码。
-- `trade_date` 主键 + `INSERT OR REPLACE`:同日重跑只认最近一次,不追加历史。
CREATE TABLE IF NOT EXISTS basket_stage_handoff (
  trade_date   TEXT PRIMARY KEY,
  search_stage TEXT NOT NULL,      -- ⑤ 检索段(ok|partial|no_provider|…;只披露,不进判读)
  reason_stage TEXT NOT NULL,      -- ⑤ 推理段(判「跑没跑成」的那一个)
  basket_count INTEGER NOT NULL,   -- ⑤ 产出的候选篮数(留痕,判读不依赖它)
  notes_json   TEXT NOT NULL,      -- AggregateResult.notes 原样(判 aggregate_failed:* 等)
  created_at   TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.2-②(2026-08,K8 §一 行情状态层):D0 盘后三态判定,EOD 预计算落表、在线只读
-- (P0-23 纪律;§3.11-B 点名的两张全市场级预计算表之一)。同既有体例:新表追加在
-- 这里,进 `CREATE TABLE IF NOT EXISTS`,⛔ 不进 `_COLUMN_MIGRATIONS`(那只收
-- 「给既有表补列」)。判定唯一实现 = `neckline/scan/regime.py::decide_regime`,
-- 读写唯一通道 = `neckline/scan/regime_store.py`。`regime` 恒非 NULL(默认态 =
-- trend_continuation,不是「不知道」;当日**缺行**才是「不知道」,由消费方按
-- `available=false` 披露)。inputs_json 五维各自带 available/unavailable_reason
-- (缺维不当 0 参与比较,§3.8);skeleton_version = 阈值口径指纹(骨架包版本,
-- 无骨架线现役时为哨兵串 'engine_default',⛔ 不写 NULL、不伪造版本号)。
-- ══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS market_regime_daily (
  trade_date        TEXT PRIMARY KEY,
  regime            TEXT NOT NULL,   -- trend_continuation | high_divergence | rotation_confirmed
  regime_reason     TEXT NOT NULL,   -- 机器可读原因码串(逐条判据留痕)
  inputs_json       TEXT NOT NULL,   -- 五维原始读数(每维带 available/unavailable_reason)
  strengthening_json TEXT NOT NULL,  -- 增强方向(行业/概念 + 依据)
  weakening_json    TEXT NOT NULL,   -- 减弱方向
  skeleton_version  TEXT NOT NULL,   -- 口径指纹(阈值住骨架包)
  computed_at       TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.2-③-C(2026-08,K8 §二「落地起跳」):全市场逐票**原始读数**,EOD 预计算
-- 落表、在线只读(P0-23 纪律;§3.11-B 点名的两张全市场级预计算表之二,§七 P4-50
-- 登记的第二条「全市场逐票 × 多年回看」批算路径——上生产前必须隔离实测)。
--
-- 🔴 2026-08-09 用户裁定 #11 后表改名重定义:`landing_state_daily` → 本表
-- `landing_metrics_daily`(⚠ 旧表**从未上产**——批 1 只部署了 ①②,生产零表
-- 零行,故本次是**干净重构**,不受「停写留档不 DROP」约束——那条红线保护的是
-- 已上产数据,本表不构成它的例外)。旧表 DDL 不保留、不迁移、不留痕迹。
--
-- 用户原话:「推翻,不要搞这个机械层了……其实对于 LLM 来说,它能够完全决定的。
-- 所以说这个地方的判定直接给到大模型。」—— 机械层因此从「四态判定 + 十二个
-- 阈值」收窄为「只算事实、不下结论」:`metrics_json` 是十四项原始读数(比值/
-- 收益率/布尔事实,⛔ 不含任何判定结论或阈值比较结果),`metrics_missing` 逐项
-- 说明「哪个读数没取到 + 为什么」(⛔ 缺数不当 0,诚实披露)。**判定在 LLM 侧
-- 发生**(六关⑤位置关,`neckline/selection/gates.py`,产出
-- `position_verdict ∈ {ok,weak,unfit}` 落 `gate_evaluations`,复用
-- `basket_reason` 那一次调用,⛔ 不新增 LLM 调用)。同既有体例:新表追加在
-- 这里,⛔ 不进 `_COLUMN_MIGRATIONS`。装配唯一实现 =
-- `neckline/scan/landing.py::compute_landing_metrics`,读写唯一通道 =
-- `neckline/scan/landing_store.py`。**本表没有 `state` 列**——当日整行
-- **缺失**才是「没算过」,由消费方按「缺行 = 不知道」披露(与
-- `market_regime_daily` 缺行同一套纪律)。🔴 本表只产**注意力分层**,⛔ 不得被
-- 读成买入期望背书(§3.8-(b);雷区对照与前向证伪义务见 `landing.py` 模块头与
-- §七 P3-49)。`platform_days` 等派生量一律住 `metrics_json`,⛔ 不另起一张表
-- (plan §五 ③-F)。
-- ══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS landing_metrics_daily (
  trade_date       TEXT NOT NULL,
  ts_code          TEXT NOT NULL,
  metrics_json     TEXT NOT NULL,  -- 十四项原始读数(事实),⛔ 不含任何判定结论/阈值比较结果
  metrics_missing  TEXT NOT NULL,  -- {读数键: 缺失原因码}(诚实披露,⛔ 缺数不当 0/默认值)
  computed_at      TEXT NOT NULL,
  PRIMARY KEY (trade_date, ts_code)
);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.2-③(2026-08,K8 §五 六道关口):关口判定留痕,**append-only**(每候选每关
-- 一行;成员级关口〔核心/位置〕每候选每关每成员一行)。唯一写入口 =
-- `neckline/selection/gates.py::save_gate_evaluations`(六关唯一实现同文件),
-- 本表零 UPDATE/DELETE 路径(守门:`tests/test_v2_schema_guard.py` 的 append-only
-- 文本扫描)。用途两条(plan §五 ③-B 原文):
--   ① 报告 ③b「未定档披露」的升级版 —— 现在说得出**卡在哪一关、差多少**;
--   ② ④ 周度「各因素对正确率的贡献」(K8 §十六 选股侧第 8 项)的**直接数据源**
--      —— 没有这张表,那一项算不出来。
-- `candidate_key` = ⑤ 的 basket_key(候选在被 ⑥ 落库拿到 basket_id 之前就可能被
-- 关口除名,故键用篮子候选键、不用 basket_id —— 被硬否决的候选恰恰是最需要留痕的)。
-- `verdict` 三态:pass|degrade|reject(③-A 二分:机械关只会 pass/reject,证据关只会
-- pass/degrade;混出别的组合 = gates.py 有 bug)。⚠ `engine_code`/`engine_version`
-- 在**引擎已解析**的行上一律填(plan 原注「成员级关口才有」写于裁定 #9 之前;单篮子
-- 单引擎后篮子级关口同样按该篮引擎的阈值分支判,不填 = 归因丢信息,施工时如实扩)。
-- 🔴 **两道 LLM 成员级关口(⑤位置 / ④核心)的行:`score`/`threshold` 恒为 NULL**
-- —— 裁定 #11/#12 之后这两关**零阈值**,判定是模型输出而不是一次数值比较;要复核
-- 「它当时拿什么下的判断」只能看 `evidence_json`,那里**同时**存着当次读数
-- (`metrics` + `metrics_missing`)与模型那句理由。⛔ 别往这两列回填一个数去"补全"
-- 报表:一填就等于宣称当时有过一条及格线,而那正是被推翻的东西。
CREATE TABLE IF NOT EXISTS gate_evaluations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date    TEXT NOT NULL,
  candidate_key TEXT NOT NULL,   -- 篮子候选键(⑤ 的 basket_key)
  ts_code       TEXT,            -- NULL = 篮子级关口(市场/驱动/板块/证据),非空 = 成员级(核心/位置)
  gate          TEXT NOT NULL,   -- market|driver|sector|core|position|evidence
  gate_kind     TEXT NOT NULL,   -- mech|llm
  verdict       TEXT NOT NULL,   -- pass|degrade|reject
  score         REAL,            -- 该关的定量读数(可空)
  threshold     REAL,            -- 当次生效阈值(可空)
  engine_code   TEXT,
  engine_version TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gate_eval_day ON gate_evaluations(trade_date, gate);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.2-④-A(2026-08,K8 §十四 选股时钟):D0 的**全部** T1/T2 篮子在 D1 收盘统一
-- 验证一次,验完即**结案**。唯一实现 = `neckline/review/selection_clock.py`。
--
-- 🔴 **结案 = 只增不改**:写入一律 `INSERT OR IGNORE`(同 `basket_cards` 冻结律),
-- `basket_id` 上的 UNIQUE 就是「只结一次案」这条规则的库级落点。⚠ **与
-- `basket_review_daily` 的 `UNIQUE(basket_id, review_date)` 覆盖式刻意不同**:那张
-- 是「每日复盘」(同日重跑可覆盖),这张是「**结案**」(结了就是结了)。⛔ 别"统一"。
--
-- 🔴 **「买没买不影响样本」是结构性保证,不靠自觉**:写入路径(那个模块)**零 import**
-- 持仓 —— 守门单测按 AST 扫 `neckline.sentinel.positions` / `positions_entry` /
-- 字面 `positions` 表名(同 `basket_cards` 冻结表「靠没有那条路担保」的既有体例)。
--
-- `regime_at_d0` 可空 = 当日 `market_regime_daily` **缺行**(如实,⛔ 不填默认态);
-- `tier_accuracy` / `untriggered_reason` 同理可空(⛔ 不拿空串冒充"判过了")。
-- `engine_breakdown_json` 随裁定 #9(单篮子单引擎)退化为两键
-- `{"engine_code":…, "engine_version":…}`,**列名保留不改** —— 将来开混引擎时它能
-- 原地扩成逐成员映射,零 schema 变更。
-- ══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS selection_clock (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id      INTEGER NOT NULL UNIQUE,   -- 一篮一次,UNIQUE 即「只结一次案」
  d0_date        TEXT NOT NULL,
  d1_date        TEXT NOT NULL,
  covered_tier   INTEGER NOT NULL,          -- D0 那天的 tier(1|2)
  regime_at_d0   TEXT,                      -- K8 验证内容①(NULL = 当日无行,如实)
  mech_json      TEXT NOT NULL,             -- 九项验证内容逐项(每项带 available/source)
  engine_breakdown_json TEXT NOT NULL,      -- 引擎归因快照(裁定 #9 → 两键即可)
  tier_accuracy  TEXT,                      -- K8 验证内容⑨:T1/T2 分层准确性判定
  untriggered_reason TEXT,                  -- K8 验证内容⑧:未触发原因(触发了则 NULL)
  closed_at      TEXT NOT NULL,
  skeleton_version TEXT NOT NULL,
  verification_ruleset_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_selection_clock_d0 ON selection_clock(d0_date);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.2-④-B(2026-08,K8 §十四 交易时钟):**实际买入是启动的唯一条件**,跟到全部
-- 离场后结案。唯一实现 = `neckline/review/trade_clock.py`。
--
-- `position_id` 上的 UNIQUE 是**确定性外键**(§七 P3-38:与交割单 `RoundTrip`
-- ⛔ 不做任何近似匹配 —— 那条纪律一字不变,本表只认 `positions.id`)。
-- `basket_id` 可空 = 非篮子来源的手动开仓(**合法**,不是缺陷)。
-- `final_json` 只在结案时写(运行中恒 NULL —— 「还没结案」与「结案了但八项算不出」
-- 必须分得开)。`entry_plan_json` = 开仓时 `position_plans` version=1 的四件套快照。
-- ⚠ 本表**允许 UPDATE**(`status`/`closed_on`/`final_json`/`updated_at`),它是一个
-- 有生命周期的对象,不是冻结件 —— 冻结的是 `trade_clock_events` 那条流水。
-- ══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS trade_clock (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  position_id INTEGER NOT NULL UNIQUE,      -- 确定性外键(⛔ 不做任何近似匹配,见 §七 P3-38)
  ts_code     TEXT NOT NULL,
  basket_id   INTEGER,                      -- NULL = 非篮子来源的手动开仓(合法)
  opened_on   TEXT NOT NULL,
  closed_on   TEXT,
  status      TEXT NOT NULL,                -- running | closed
  entry_plan_json TEXT NOT NULL,            -- 开仓时从 position_plans 冻的四件套快照
  final_json  TEXT,                         -- 结案时的八项验证(K8 §十四)
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_clock_status ON trade_clock(status);

-- 交易时钟事件流水,**append-only**(同 `basket_verification` / `user_actions` 三律)。
-- `user_note` = K8 §十五「用户只补充系统无法识别的主观原因,每次一条简短说明」——
-- **只追加**,⛔ 不改既有行、⛔ 不做 LLM 代猜(§七 P3-28 原文纪律一字不变)。
CREATE TABLE IF NOT EXISTS trade_clock_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_clock_id INTEGER NOT NULL,
  event_date TEXT NOT NULL,
  kind TEXT NOT NULL,          -- d1_open | daily_check | target_zone | invalidation | manual_note | close
  mech_json TEXT NOT NULL DEFAULT '{}',
  user_note TEXT,              -- K8 §十五:用户补充的主观原因,每次一条简短说明
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trade_clock_events ON trade_clock_events(trade_clock_id, event_date);

-- ═══ V2.3.2-①-D 阈值影子台账(策略线裁定 5 的落点),**append-only** ═══════════
-- 「这条**待定**阈值若按硬门跑,本可通过 / 本可否决」的逐候选逐阈值留痕。
-- 唯一写入实现 `neckline/selection/threshold_shadow.py`(零 UPDATE / 零 DELETE /
-- 零 INSERT OR REPLACE;同日重跑 = 追加新批次,`created_at` 区分)。
--
-- 🔴 **⛔ 不许拿它回写当时的正式选股结论**(裁定 5):本表只增不改、只读不写回
-- `baskets` / `tier_history` / `basket_cards` / `selection_clock`。
--
-- ⚠ **与「OUT 研究影子对照」(`out_shadow_daily`)是两件完全不同的事,⛔ 不许混名**:
--   · 本表单位 = 候选 × 阈值键,问「这条阈值该不该恢复成硬门」;
--   · 那张表单位 = OUT 票 × D1,问「被判 OUT 的票是不是被错杀」。
--
-- ⚠ 只对 `enforcement=evidence` 的阈值出行;`audited` 那四项的判定已在
--   `gate_evaluations` 里,再写一份 = 两个事实源。
-- ⚠ `unavailable_reason` 以 `not_applicable:` 开头 = 该规则今天**不适用**(如
--   `high_divergence_min_breadth_pctile` 只在高位分歧态适用),**不是缺数** ——
--   ①-E 出通过率时按适用域取分母,⛔ 不拿全体候选把它稀释成一个好看的数。
CREATE TABLE IF NOT EXISTS threshold_shadow_evals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  candidate_key TEXT NOT NULL,       -- = baskets.basket_key(进关之前的召回候选)
  engine_code TEXT,
  engine_version TEXT,
  skeleton_version TEXT,
  threshold_key TEXT NOT NULL,       -- `<gate>.<key>` 全称,如 sector.cluster_members_min
  reading REAL,                      -- 机械读数(类别型阈值恒 NULL)
  threshold_value REAL,              -- 门槛值(类别型阈值恒 NULL)
  would_pass INTEGER,                -- 1/0 = 若按硬门跑的拟判;NULL = 算不出或不适用
  unavailable_reason TEXT NOT NULL DEFAULT '',
  llm_verdict TEXT,                  -- 该关 evidence 半边的 LLM 三值(ok|weak|unfit;NULL = 没给)
  regime TEXT,                       -- D0 行情状态(适用域分母靠它,NULL = 当日无行)
  final_tier INTEGER,                -- 该候选当日最终定档(1/2;NULL = OUT / 未定档)
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threshold_shadow_day
  ON threshold_shadow_evals(trade_date, threshold_key);

-- ═══ V2.3.2-②-B OUT 一等状态:D0 **股票级** OUT 清单,**append-only** ═════════
-- K8 §六 定死候选状态只有三个:T1 / T2 / **OUT**。③b 那一节自此装**两类行**:
--   · **OUT 行**(关口未过 / 引擎缺席)—— 本表,**股票级**,一票一行;
--   · **未定档行**(`capacity_overflow`)—— **不是 OUT**(K8 §八 的 OUT 适用状态里
--     没有"位置满"),维持篮子级,仍走 `basket_dropped_handoff` / `droppedBaskets`。
-- 🔴 这条分类**直接决定 OUT 研究影子对照的样本域**:溢出篮**不进**影子对照
--    (它们没被判"不够格",混进去会污染错杀分析)。
--
-- **为什么非要一张表**(两条现有路径都取不全 OUT 票):
--   · `basket_dropped_handoff.dropped_json` **不含成员代码**(且它自称"只是搬运工、
--     不是审计账本",`INSERT OR REPLACE` 整行覆写);
--   · `gate_evaluations` 的成员级行**只在篮子跑到关口层时才有** —— `no_active_engine`
--     / `engine_unresolved` 两种早退候选**零成员行**。
-- ⛔ 别去改 `basket_dropped_handoff` 的语义来凑合。
--
-- 唯一写入实现 `neckline/selection/basket_store.py::save_out_candidates`
-- (零 UPDATE / 零 DELETE / 零 INSERT OR REPLACE;同日重跑靠 UNIQUE 幂等)。
CREATE TABLE IF NOT EXISTS out_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  d0_date TEXT NOT NULL,
  basket_key TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  role TEXT,                         -- leader | core | elastic(LLM 主张,展示用)
  engine_code TEXT,
  engine_version TEXT,
  skeleton_version TEXT,
  out_gate TEXT,                     -- 卡在哪一关(gates.GATE_* 六码之一;NULL = 非关口原因)
  out_reason TEXT NOT NULL,          -- ③b 原因码(与 droppedBaskets 同一套码,⛔ 不另起)
  out_detail TEXT,                   -- 差多少 / 模型理由(原因码串,数值已内嵌)
  created_at TEXT NOT NULL,
  UNIQUE(d0_date, basket_key, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_out_candidates_day ON out_candidates(d0_date);

-- ═══ V2.3.2-③-A OUT 研究影子对照:D1 前向读数,**append-only** ═══════════════
-- K8 §十四:被判 OUT 的票在 D1 实际走成什么样(六项读数)。⛔ **不进 T1/T2、不启
-- 交易时钟、不计入正式样本、不增加用户手工填写**(K8 §十四 逐字)——结构性保证由
-- `neckline/review/out_shadow.py` 的零 import / 零写表守门单测钉死。
--
-- ⚠ **与阈值影子(`threshold_shadow_evals`)是两件完全不同的事,⛔ 不许混名**:
--   · 本表单位 = **OUT 票 × D1**,问「被判 OUT 的票是不是被错杀」;
--   · 那张表单位 = 候选 × 阈值键,问「这条待定阈值该不该恢复成硬门」。
--
-- 🔴 **主键刻意是 `(d0_date, ts_code)`、⛔ 不含 `basket_key`**:同一票在同一 D0 可能
--    出现在多个 OUT 篮(篮子间成员可重叠),但 **D1 读数是这只票的属性** —— 存两份
--    就是两个事实源。`out_gate`/`out_reason` 取**确定性的第一条**(按 `basket_key`
--    升序),全部出局记录另存进 `support_and_invalidation_json` 的 `all_out_records`
--    键(⛔ 别用"最后写入的赢"这种非确定性写法)。
-- ⚠ `rel_strength` 存的是**板块基准**那一个(⑧-1:板块为主、指数为辅);两个基准的
--    完整读数都在 json 里。
CREATE TABLE IF NOT EXISTS out_shadow_daily (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  d0_date TEXT NOT NULL,
  ts_code TEXT NOT NULL,
  d1_date TEXT NOT NULL,
  pct_chg REAL,                      -- K8 §十四 ①涨跌幅(小数,同 member_return 口径)
  high REAL,                         -- ②最高价
  low REAL,                          -- ③最低价
  close_state TEXT,                  -- ④收盘状态(复用 ⑨ 可买性同名口径的四码)
  rel_strength REAL,                 -- ⑤相对强弱(**板块基准**;指数基准在 json 里)
  support_and_invalidation_json TEXT NOT NULL DEFAULT '{}',   -- ⑥支撑与失效原始数据
  out_gate TEXT,
  out_reason TEXT,
  engine_code TEXT,
  engine_version TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(d0_date, ts_code)
);
CREATE INDEX IF NOT EXISTS idx_out_shadow_d0 ON out_shadow_daily(d0_date);

-- ═══ V2.3.2-③-B 周度 OUT 集中复核的**跨周状态**(⑧-2 拍板),**append-only** ═══
-- 🔴 **为什么必须落表**:⑧-2 的扩大 / 恢复判据是「**连续 2 次**周度复核」——跨周状态
--    用内存计数器必然丢;而**用库里的计数器同样错**:重跑一次周报就会把它推进一格
--    (§六 那条教训的同款)。故本表按 `week_anchor` **一周一行**(UNIQUE),
--    「连续几次」由读**最近两行**现算 —— **重跑同一周只会命中已有行(INSERT OR
--    IGNORE),永远推不动连续计数**。
-- ⛔ 本表只改**研究复核范围**:不改 OUT 身份、不进 T1/T2、不计入正式样本。
CREATE TABLE IF NOT EXISTS out_shadow_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  week_anchor TEXT NOT NULL UNIQUE,  -- 该周的锚日(YYYYMMDD),一周一行
  window_from TEXT NOT NULL,
  window_to TEXT NOT NULL,
  scope_top INTEGER NOT NULL,        -- 本次复核了几只「表现最强」(5 或 10)
  scope_random INTEGER NOT NULL,     -- 本次复核了几只「随机」(3 或 5)
  expanded INTEGER NOT NULL DEFAULT 0,   -- 1 = 本次处于扩大态
  reviewed_count INTEGER NOT NULL DEFAULT 0,
  obvious_miskill_count INTEGER NOT NULL DEFAULT 0,  -- ⑧-2 五条同时满足的只数
  result_json TEXT NOT NULL DEFAULT '{}',
  llm_stage TEXT,
  created_at TEXT NOT NULL
);

-- ══════════════════════════════════════════════════════════════════════════
-- V2.3.3-②(K8.md §二十 D1 集合竞价确认层):9:26—9:29 冻结一次竞价结果的**市场级**
-- 报告。一日一行(`trade_date` UNIQUE = D1)。唯一实现 = `neckline/auction/store.py`。
--
-- 🔴 **两阶段写**:9:26 机械段先 `INSERT OR IGNORE`(`llm_stage='pending'`),LLM 回来
--    或 9:29 硬截止之后**只 UPDATE 下面这几列**:
--      `market_overview` / `anchors_note` / `risks_json` / `manual_note_attached` /
--      `llm_stage` / `llm_elapsed_ms` / `notes_json` / `updated_at`
--    —— **机械列永不改**(守门单测锁 UPDATE 语句的列集合,
--    `tests/test_v233_auction_guards.py`;它同时对**非字面量 SQL** 与
--    `DELETE FROM auction_*` 报红,扫描域含 `neckline/**` 与 `scripts/**`)。
-- 🔴 **本表刻意不进 `_APPEND_ONLY_TABLES`**:它是**有生命周期的对象**(先落机械段、
--    后补结论),与 `trade_clock` 同族同处置,与 `basket_cards` / `selection_clock`
--    的 `INSERT OR IGNORE` 冻结体例**刻意不同**。⛔ 别"统一"。上面那条列白名单守门
--    就是这条例外的**代偿闸门** —— 缺了它,不进 append-only 这一步就是个后门。
-- ⛔ **事后不许补跑**(K8「报告发出后结束、不持续观察 9:30 以后的价格」):窗口外
--    调用 `run_auction_pipeline` 一律 `skipped_reason='not_auction_window'` + **零落库**。
-- ⚠ **「没跑」与「跑了没有」必须分得开**(§七 P0-39 同款病):当日**无行** = 竞价层
--    没跑过(端点 404 `auction_not_ready`);**有行但 `baskets_covered=0`** = 跑过了、
--    D0 当天就没有 T1/T2 篮子(200 + 篮子段空 + 说出口)。
-- ══════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS auction_reports (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date          TEXT NOT NULL UNIQUE,  -- D1(YYYYMMDD)= 竞价发生那天
  d0_date             TEXT NOT NULL,         -- 被验证的 D0(= prev_trading_day(D1))
  -- 小报告第 1 块「数据状态」(K8 §二十)
  source              TEXT NOT NULL,         -- sina | tencent | mixed | unknown(逐票 Quote.source 汇总;
                                             -- unknown = 一条都没抓到,⛔ 不拿主源名冒充)
  captured_at         TEXT NOT NULL,         -- 🔴 **真正拉完价的那一刻**(ISO 秒精度,北京时间)
                                             -- ⛔ 不是轮询那一拍的 `now`:precall + capture +
                                             -- 组清单可能吃掉几分钟,慢的早晨会写下一份用开盘后
                                             -- 价格却自称 9:26 冻结的报告(复审 🟡-2)。
                                             -- 越窗 → `data_quality` 降级 → 闸 1 夹成中性。
  requested_codes     INTEGER NOT NULL,
  fetched_codes       INTEGER NOT NULL,    -- 🔵 **V2.4.0 复审 🔵-6:这一列的语义在 P2 变过**
                                             -- —— `snap.quotes` 自 P2 起只装**通过七项校验**的
                                             -- 读数,于是它从「抓到几个」变成「几个**能用**」。
                                             -- 🔴 **版本判别列 = `quote_quality_json IS NULL`**
                                             -- (= v2.3.3 及更早的口径);⛔ 别拿两个版本的数直接比。
                                             -- 同理下面的市场级 `data_quality`。
  missing_codes_json  TEXT NOT NULL,         -- ["600xxx.SH", …] 拉不到的
  conflict_codes_json TEXT NOT NULL,         -- 跨源冲突。🔴 **V2.4.0 P2.2 起真的会有值**:
                                             -- `get_quotes_dual()` 双源批量核验已上线(净增 1 次
                                             -- HTTP 请求 / 早晨),四类结论性冲突见
                                             -- `auction/__init__.py::CONFLICT_CODES`。
                                             -- ⚠ V2.3.3 时代「结构性恒空、⛔ 不加第二次请求」的
                                             -- 旧口径**已被 K8 §二十「有界双源核验」推翻**
                                             -- (§五 P2.2 抬头写明出处,§七 P4-66 改判)。
  data_quality        TEXT NOT NULL,         -- ok | degraded | insufficient(市场级;结构性判据,⛔ 非百分比)
                                             -- ⚠ 市场级这一列**仍是整体覆盖率读数**,刻意没有收窄
                                             -- 成关键域(它不驱动任何夹逼闸);篮子级那一列才收窄了。
                                             -- 分域读数见 quote_quality_json 与 auction_verdicts 的三新列。
  -- 小报告第 2 块「市场与主线概览」
  index_gaps_json     TEXT NOT NULL,         -- {"000001.SH":{"gap_pct":…}, "399001.SZ":…, "399006.SZ":…}
  market_anchors_json TEXT NOT NULL,         -- 竞价强势股(**代理样本**,不是全市场竞价排行)
  market_overview     TEXT,                  -- LLM 一段话;NULL = 未生成(⛔ 不冒充"没内容")
  anchors_note        TEXT,                  -- LLM 对「竞价强势股 = 市场锚点」那批标的的一句解释
                                             -- (K8 §二十:锚点只解释资金方向、**不取得交易资格**)。
                                             -- ⚠ 属 LLM 段白名单;NULL = 未生成。
                                             -- 🔴 它是 ③-B 契约里 LLM **必须**输出的键 ——
                                             -- 缺这一列 = 模型每次都生成它、然后被编排层丢掉,
                                             -- 界面那个分支永远不执行且没有任何东西会报错(复审 🟡-1)。
  -- 小报告第 4 块「异常与风险」
  risks_json          TEXT NOT NULL,         -- [{"kind":…,"text":…}] 机械异常 + LLM 补充
  -- 小报告第 5 块「APP 人工观察小纸条」:存的是"挂没挂",文案本体是代码常量单一源
  manual_note_attached INTEGER NOT NULL DEFAULT 0,   -- = 逐篮 manual_note_attached 的 OR,写侧一处算
  -- LLM 段状态
  llm_stage           TEXT NOT NULL,         -- pending | ok | pending_explanation | provider_none
                                             -- | parse_failed | call_failed:* | budget_exhausted
  llm_elapsed_ms      INTEGER,
  baskets_covered     INTEGER NOT NULL DEFAULT 0,
  notes_json          TEXT NOT NULL,
  created_at          TEXT NOT NULL,
  updated_at          TEXT NOT NULL
);

-- 篮子级:一篮一行。🔴 **`basket_id` UNIQUE 的形状刻意对齐 `selection_clock`** ——
-- K8.md §二十 末段把样本单位写死为 **`D0 日期 × 篮子 × 引擎版本`**,而
-- `selection_clock.basket_id UNIQUE` 正是这个定义的物理承载 → 周度聚合可直接复用
-- `eval/iteration.stratum_of()`,**零新统计代码**。
-- ⛔ **别把本表改成一票一行** —— 那会让"样本量"在不改任何统计代码的情况下悄悄变成
-- 另一个量(成员多的篮子权重变大),而 30/80 是按**篮子数**拍板的。
-- 同为**两阶段写**;第二阶段只准 UPDATE:`verdict` / `verdict_raw` / `clamped_by` /
-- `reasons_json` / `llm_fields_json` / `manual_note_attached` / `llm_stage` / `updated_at`。
CREATE TABLE IF NOT EXISTS auction_verdicts (
  id                    INTEGER PRIMARY KEY AUTOINCREMENT,
  basket_id             INTEGER NOT NULL UNIQUE,
  trade_date            TEXT NOT NULL,       -- D1
  d0_date               TEXT NOT NULL,
  basket_key            TEXT NOT NULL,
  name                  TEXT NOT NULL,
  covered_tier          INTEGER NOT NULL,    -- D0 原始等级(1|2)
  engine_code           TEXT,                -- 归因分层键;老篮子为 NULL(如实)
  engine_version        TEXT,
  skeleton_version      TEXT NOT NULL,
  regime_at_d0          TEXT,                -- 周度聚合维度;NULL = 当日 market_regime_daily 无行(如实)
  -- 机械层(第一次写,永不改)
  data_quality          TEXT NOT NULL,       -- 篮级 ok | degraded | insufficient
                                             -- 🔴 **V2.4.0 P2.3 起本列 = 关键域质量**(含义收窄,
                                             -- 施工图 §五 P2.3);V2.3.3 及更早的行里它是**整体**质量
                                             -- —— 老行靠 `critical_data_quality` 为 NULL 分辨
                                             -- (客户端显示「旧版本未细分」,⛔ 不得默认成正常)。
  members_json          TEXT NOT NULL,       -- 逐票明细(键表见 §五 ②-F;🔴 一律发枚举码)
  sector_sync_json      TEXT NOT NULL,       -- 同向数 / 强弱分布 / 所属上市板块对照指数读数
                                             -- (⛔ 那一组不是「板块基准」:主板票落到的就是
                                             --  市场指数本身;板块基准见 rel_strength_json)
  rel_strength_json     TEXT NOT NULL,       -- 候选 vs 板块 / vs 市场指数
  history_json          TEXT NOT NULL,       -- 自身历史对照(history_days_available + 逐日原始值)
  hit_invalidation_json TEXT NOT NULL,       -- ["600xxx.SH", …] 命中 D0 冻结失效位(机械事实)
                                             -- 🔴 这是**独立警报通道**:机械段第一次写就落库,
                                             -- 恒定出现在第 4 块与推送里,**不受 LLM 缺席 /
                                             -- 三道夹逼闸影响** —— 也正因如此,闸 1「数据缺失
                                             -- 只能形成中性」才可以无例外执行。
  plan_consistency_json TEXT NOT NULL,       -- 逐票 plan_fit 五态汇总
  -- LLM 层(第二次写)
  verdict               TEXT NOT NULL,       -- confirm | neutral | veto | pending_explanation
                                             -- ⛔ 全仓禁止出现 qualified / wait / cancelled(K8 明令)
  verdict_raw           TEXT,                -- 夹逼**前**模型原话的三值;NULL = 模型没给
  clamped_by            TEXT,                -- NULL | clamped_by_data_quality | clamped_by_single_strong
                                             -- | clamped_by_missing_strong_evidence | clamped_by_y1_low_weight
  reasons_json          TEXT NOT NULL,       -- 一至两条白话理由(K8 §二十)
  llm_fields_json       TEXT NOT NULL,       -- 模型结构化字段原样存档(= 闸 2/3 的输入,可回溯)
  manual_note_attached  INTEGER NOT NULL DEFAULT 0,
  llm_stage             TEXT NOT NULL,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auction_verdicts_date ON auction_verdicts(trade_date);
CREATE INDEX IF NOT EXISTS idx_auction_verdicts_d0   ON auction_verdicts(d0_date);
"""

# 幂等列迁移(plan v1.1 §五「均 CREATE TABLE IF NOT EXISTS / 幂等迁移」)。生产库
# 早已存在,`CREATE TABLE IF NOT EXISTS` **不会**给既有表补列——新增列必须走
# `ALTER TABLE ... ADD COLUMN`,并用 `PRAGMA table_info` 先探测存在性做到重复跑不炸。
# 只登记「给既有表新增可空 / 带常量默认的列」这一类无损迁移;改主键 / 改类型 /
# 删列等破坏性动作**不走这里**(SQLite ALTER 也不支持,需另建表迁移,本项目暂无)。
# 注:SQLite `ADD COLUMN` 的 NOT NULL 列必须带常量 DEFAULT(已满足)。
_COLUMN_MIGRATIONS = [
    # v1.1-A/B:两类新推送开关(默认开),老库补列即取常量默认 1。
    ("app_settings", "push_precall", "INTEGER NOT NULL DEFAULT 1"),
    ("app_settings", "push_d5exit", "INTEGER NOT NULL DEFAULT 1"),
    # v1.1-C:自选体检快照(老报告行补列取默认 '[]',前向兼容,见 CREATE TABLE 注释)。
    ("reports", "watchlist_json", "TEXT NOT NULL DEFAULT '[]'"),
    # v1.1-D:问询窗口修复——消费标记列,可空(NULL=待消费),老库补列后既有行均为
    # NULL,等同「历史遗留票下一次报告即可被消费」,不会丢票也不会误判已消费。
    ("inquiry_pool", "consumed_report_date", "TEXT"),
    # v1.2-A:大脑激活时间线(历史洗白修复)。可空(NULL=从未激活);加列后一次性回填
    # 现役 K1(见 `_backfill_activated_at`)。老库既有 is_active=1 行经回填拿到激活戳。
    ("strategy_versions", "activated_at", "TEXT"),
    # v1.2-A:周复盘该周 governing 大脑版本号(按周落库,审计"这周用哪版章程判的")。
    ("reviews", "strategy_version", "TEXT"),
    # v1.2-A2 熔断纪律:①离场原因(NULL=未标注,熔断走价格近似兜底);②第五类推送开关
    # (默认开,与退潮刹车同级)。老库幂等补列,历史行取默认(close_reason NULL / push_circuit 1)。
    ("positions", "close_reason", "TEXT"),
    ("app_settings", "push_circuit", "INTEGER NOT NULL DEFAULT 1"),
    # v1.3-①:补录开仓实付买入费用 + 清仓实付卖出费用(供 D5 净浮盈判向 / 周复盘对账用真数)。
    # 均可空(NULL=未录),老库幂等补列;实盘估算见 neckline/fees.py(诚实标注估算)。
    ("positions", "buy_fees", "REAL"),
    ("positions", "sell_fees", "REAL"),
    # v2.0.0(契约线审计 🟡 Y7):`POST /positions` 幂等键。客户端自带(缺省 NULL = 没给),
    # 同键二次提交 = 重放上一次的结果,**不开第二笔仓**。防重靠下面 `_POST_MIGRATION_INDEXES`
    # 里的**部分唯一索引**(NULL 不参与去重,正是我们要的:没给键的历史/CLI 录入不受影响),
    # 不靠应用层自觉 —— 并发同键两条请求都查不到时,库级约束是最后一道闸。
    ("positions", "idempotency_key", "TEXT"),
    # v1.3-②:第六类推送开关(K4 持仓派发警报,用户 2026-07-26 拍板独立 category + 独立开关,
    # 默认开)。老库幂等补列取常量默认 1。§2.4 推送白名单五类→六类。
    ("app_settings", "push_holding_alert", "INTEGER NOT NULL DEFAULT 1"),
    # v1.3-③ C1/C2:复盘情报件 + 板块资金流快照(见 CREATE TABLE reports 注释)。
    # 老报告行(建于本列之前)幂等补列取默认值,读回来是空结构('{}' / '[]'),不是
    # NULL 炸 json.loads——同 watchlist_json 前向兼容先例。
    ("reports", "intel_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("reports", "sector_moneyflow_json", "TEXT NOT NULL DEFAULT '{}'"),
    # v1.3-③-C3:候选情报管线「五板块常驻」可配名单(可空,NULL=未配置→用
    # settings_store.DEFAULT_INTEL_WATCH_BOARDS 默认五板块;见 CREATE TABLE app_settings 注释)。
    ("app_settings", "intel_watch_boards", "TEXT"),
    # v1.3-③-C4:消息面扫描状态快照(老报告行幂等补列取默认 '[]',前向兼容,见
    # CREATE TABLE reports 注释)。
    ("reports", "news_alerts_scan_json", "TEXT NOT NULL DEFAULT '[]'"),
    # v1.3-③-C4(2026-07-26 必改):事件级跨日去重两列。**⚠ 局限性如实记录**:
    # ALTER TABLE ADD COLUMN 能给已存在的 news_alerts 表补上这两列,但 SQLite
    # 不支持 ALTER 改 UNIQUE 约束——若某环境在本次修复前已建过 news_alerts 表
    # (约束仍是旧的 UNIQUE(ts_code,trade_date,category)),补列后约束本身不会
    # 跟着变新;本项目当前唯一受影响的是本地开发库这一份空表(未部署、未进生产,
    # 已单独 cp -p 备份后删表重建,见完工报告),真实生产 ECS 从未见过 v1.3、
    # 走「新表 CREATE TABLE IF NOT EXISTS」路径直接拿到新约束,不受此局限影响。
    ("news_alerts", "event_date", "TEXT"),
    ("news_alerts", "event_key", "TEXT NOT NULL DEFAULT ''"),
    # v1.3 审计修复 🔴-1(2026-07-27,用户拍板「D5 判一次定格」方案 A):两档时间退出的
    # 判向定格三列。均可空(NULL=尚未定格,含现役单档 K1 恒不定格),老库幂等补列后既有行
    # 全 NULL —— 等同「历史持仓还没定格过,下一次 16:35 到判定点时再定格」,不臆造历史判向。
    ("holding_eod_check", "time_exit_locked_state", "TEXT"),
    ("holding_eod_check", "time_exit_locked_date", "TEXT"),
    ("holding_eod_check", "time_exit_locked_net_float", "REAL"),
    # v1.4-①-B(§七 P0-2):当日体检是否因**无 EOD 行**(停牌/数据缺口)被整份跳过。
    # **可空且不给默认值**:老行(建于本列之前)是 NULL = 「当时没记这一位,不知道」——
    # 刻意不用 `DEFAULT 0` 把历史行一律说成「体检过了」(空牌 = 体检过没问题 vs 没体检,
    # 必须能分开,§3.8)。1=跳过体检,0=正常体检过。
    ("holding_eod_check", "data_unavailable", "INTEGER"),
    # v1.4-①-C(§七 P0-3):板块数据新鲜度快照(`{sectorDataDate,sectorLagDays,stale}`)。
    # **随报告冻住**,不在读时重算——读三天前的报告该看到当时的新鲜度,不是今天的。
    # 老报告行幂等补列取默认 '{}'(= 该版本还没有新鲜度概念,**不是**「新鲜」)。
    ("reports", "data_freshness_json", "TEXT NOT NULL DEFAULT '{}'"),
    # v1.4-⑤-B(需求 2 补充):决策日志第⑨项「最高追价上限」,相对昨收百分比(如
    # 3.0=+3%,允许负值)。**可空、不给默认值**——老行(建于本列之前)是 NULL,与"用户
    # 显式选择不设上限"在存储层无法区分(两者都是 NULL),但那是历史行的固有模糊,不
    # 影响新行起的强制语义(API 层要求新建/修订时必须显式传该键,见
    # `api/app.py::_extract_max_chase_pct_or_400`)。见 CREATE TABLE decision_log 注释。
    ("decision_log", "max_chase_pct", "REAL"),
    # v1.5-④-A3(§七 P1-7 定案):候选审判实际使用的搜索引擎取值(GLM
    # `web_search.search_engine`,由 provider 写入,见 `llm/providers/glm.py::
    # _SEARCH_ENGINE` 单一源)。**可空、不回填猜测**——老行(建于本列之前)NULL=
    # 未记录,不臆造"当时用的是 search_pro"(生产历史上虽只用过 search_pro,但
    # 「记录」与「推断」是两回事,§3.8「没有」与「没看」必须分开的同一条纪律)。
    ("llm_judgments", "search_engine", "TEXT"),
    # v1.5.1(两线 review 共同项:「章程 −5%」「回落止盈 8%」硬编文案):产出该参考件时
    # 的现役回落止盈比例,与既有 `stop_pct` 列成对的第二个口径指纹。**可空、不回填**
    # ——老行(v1.5.0 生成的)NULL = 当时没记这一位,展示层据此退化成不带数字的
    # 「章程止损/章程回落止盈」文案,不拿今天的章程去追认历史报告(同 search_engine
    # 「记录」≠「推断」的同一条纪律)。生产 v1.5.0 已建过 reference_plans 表,故走补列。
    ("reference_plans", "take_profit_retrace", "REAL"),
    # V2-①(建列)/V2-②(读写解析逻辑,plan §五 V2-①/②,§3.10-B):LLM 双 Agent 路由。
    # llm_default_provider 缺路由时的兜底(`llm_providers.name`);llm_task_routes 是
    # 「任务 → provider name」JSON 映射(`app_settings.llm_task_routes`,非 NULL 默认
    # '{}' = 未配任何任务级路由,全部退回默认 provider)。读写见
    # `neckline/settings_store.py::get_llm_routes`/`set_llm_routes`,解析见
    # `neckline/llm/router.py::resolve_task_provider_name` + `neckline/llm/
    # factory.py::get_provider`。
    ("app_settings", "llm_default_provider", "TEXT"),
    ("app_settings", "llm_task_routes", "TEXT NOT NULL DEFAULT '{}'"),
    # V2-⑪(plan §五 V2-⑪-B,D5):通知三级 × N kind —— 开关**按 kind 配**的落点。
    # 可空(NULL=从未配置=全部 kind 默认开);老库既有六列取值经 `_seed_push_kinds`
    # 一次性播种进来(见该函数),播种后 V1 六列停写留档。
    ("app_settings", "push_kinds", "TEXT"),
    # V2-⑧-F(plan §五 V2-⑧-F,2026-08-03):退潮「主线板块跳水」样本构成留痕。
    # 非 NULL 默认 '{}' = 「这一拍没记样本构成」(老行建于本列之前,**不是**「样本为空」
    # ——两者在读侧靠 `unavailable_reason` 有没有这个键区分)。见 `sentinel/mainline.py`。
    ("retreat_metrics", "hot_sector_sample_json", "TEXT NOT NULL DEFAULT '{}'"),
    # V2-⑧-G-D 追加(review 判定线 🟡-N1 一并处理,2026-08-03):昨日涨停宽度代理样本
    # 的需求量 vs 实际采纳量留痕,形状照 `hot_sector_sample_json`。非 NULL 默认 '{}'
    # = 「这一拍没记样本构成」(老行建于本列之前,**不是**「样本为空」)。见
    # `sentinel/universe.py::WatchUniverse.breadth_extra_payload()`。
    ("retreat_metrics", "breadth_extra_sample_json", "TEXT NOT NULL DEFAULT '{}'"),
    # V2.2-①(plan §五 ①「表与契约变更」):`selection_packs` 从「单包制」升级
    # 「多版本线注册表」的两根柱子。历史两行(K4-pack-v1 / K7-pack-v1)靠 DEFAULT
    # 天然落位 `LEGACY` / `running`,⛔ **init_schema 只建列,不改业务行** —— 清
    # LEGACY 行的 `is_active` 是业务动作,住 `scripts/oneoff/retire_legacy_packs.py`。
    # `line_code` ∈ {V, C, Z, Y, LEGACY}(骨架线 / 三条引擎线 / 历史单包制),取值
    # 校验在写侧 `selection/pack.py`(SQLite ADD COLUMN 不能带 CHECK,不硬塞)。
    # `status` ∈ {running, stopped}(K8 §四「引擎状态」:停止 = 不产候选,保留历史
    # 版本与复盘数据)。⚠ `status` 本版**只由 DEFAULT 落位、无任何切换入口**
    # (用户 2026-08-09 裁定,详见 `selection/pack.py::get_active_engines` docstring)。
    ("selection_packs", "line_code", "TEXT NOT NULL DEFAULT 'LEGACY'"),
    ("selection_packs", "status", "TEXT NOT NULL DEFAULT 'running'"),
    # V2.2-③-E(plan §五 ③-E,裁定 #9 单篮子单引擎):`baskets` 幂等补三列 ——
    # 篮子级引擎归属(成员继承篮子引擎,`basket_members` ⛔ 不加引擎列)。三列均
    # **可空、不回填**:老行(建于 K8 之前)NULL = 「当时没有引擎归属概念」,⛔ 不拿
    # 今天的现役引擎去追认历史篮子(同 `llm_judgments.search_engine`「记录 ≠ 推断」
    # 的既有纪律)。skeleton_version = 生成当时的骨架线版本口径指纹(与
    # `market_regime_daily.skeleton_version` 同语义)。
    ("baskets", "engine_code", "TEXT"),
    ("baskets", "engine_version", "TEXT"),
    ("baskets", "skeleton_version", "TEXT"),
    # V2-⑭-A(plan §五 V2-⑭-A「报告重构为篮子日报」):篮子日报快照。
    # **随报告一起冻住**(同 `intel_json`/`data_freshness_json` 既定惯例):今日篮子
    # (T1/T2/T3,每篮一张卡)+ ③b 未定档篮子(`capacity_overflow` /
    # `below_quality_line` 两个原因码**分开存**)+ 昨日篮子复盘。老报告行(建于本列
    # 之前)幂等补列取默认 '{}' → 读回空 dict,客户端按「该版本还没有篮子日报概念」
    # 处理,**不是**「今天没有篮子」——「没有」与「没看」必须能分开(§3.8)。
    # ⚠ ③b 的 `droppedBaskets` 之所以必须落在这里:⑥ 的 `TierResult.dropped` **不进
    # `baskets` 表**(`baskets.tier` NOT NULL,溢出篮无档可填),报告快照是它唯一的
    # 落点;不存 = 历史回放永远看不到"那天有多少好货装不下"。
    ("reports", "basket_daily_json", "TEXT NOT NULL DEFAULT '{}'"),
    # ── V2.4.0 P2.4(2026-08-12):竞价层数据可靠性。**全部可空、旧行保持 NULL**。
    # 🔴 **NULL 的含义是「这一版还没有分域 / 逐票核验这个概念」,⛔ 不是「正常」**
    # (施工图 §五 P2.3 逐字:旧报告没有这些字段时显示「旧版本未细分」,
    #  ⛔ 不得默认成正常)—— 所以刻意**不给 DEFAULT**,老行读回来就是 NULL。
    # 🔴 四列**全部是机械冻结列**:⛔ 不得加进 `auction/store.py` 的
    # `LLM_UPDATABLE_*_COLUMNS` 白名单(守门单测锁死)。
    # ⛔ **不重写 v2.3.3 的历史竞价报告**(〇-5「历史只读」)。
    ("auction_reports", "quote_quality_json", "TEXT"),      # 逐票七项校验 + 双源原始读数
    ("auction_verdicts", "critical_data_quality", "TEXT"),  # 关键域质量(= 收窄后的 data_quality)
    ("auction_verdicts", "context_data_quality", "TEXT"),   # 上下文域质量(⛔ 不夹逼结论)
    ("auction_verdicts", "quality_detail_json", "TEXT"),    # 两域逐项账(缺了什么、冲突在哪)
    # ── V2.4.0 P2.5(2026-08-12):⑤ 段状态表补两位「机械 seed」留痕。
    # 🔴 **为什么必须落表**:K8 §十 要求「系统缺席时保留机械候选数量和简短摘要」,
    # 而 ⑯-D 之后 ⑤ 与报告段可能跑在**两个进程**里 —— 内存传不过去,报告层又
    # ⛔ 零现算(P0-23)。这张表本来就是 P0-39 为「跨进程读段状态」建的,落在这里
    # 是同一个理由的延长线,⛔ 不另起一张表。
    # ⚠ 可空不给默认:老行 NULL = **当时没记这一位**,⛔ 不拿 `0` 冒充「一个种子都没有」。
    ("basket_stage_handoff", "seed_count", "INTEGER"),
    ("basket_stage_handoff", "seed_summary", "TEXT"),
    # ── V2.4.0 P4.3(2026-08-12):四线原子激活的**批次号**。
    # 一次 `activate_pack_set`(K8-V0.8 + C2/Z2/Y2)写进去的 8 条事件共享同一个
    # `batch_id`,单包激活(`activate_pack`)与所有历史行一律 NULL。
    # 🔴 **可空、无默认**:NULL = 「这条事件不属于任何原子批次」,⛔ 不拿空串冒充。
    # ⚠ 本表在 `_APPEND_ONLY_TABLES` 里 —— **加列不违反 append-only**(不是
    # UPDATE/DELETE),写入仍只 INSERT(守门单测扫本表的 SQL 动词)。
    ("selection_pack_activation_log", "batch_id", "TEXT"),
]


def _seed_push_kinds(conn: sqlite3.Connection) -> None:
    """V2-⑪ 一次性播种(幂等):把 V1 六个推送开关列的**现有取值**搬进新的
    `app_settings.push_kinds` JSON 列。

    只碰 `push_kinds IS NULL` 的那一行 —— 重跑不变(已播种的行不再命中),用户之后
    在设置屏改过的取值也不会被这里覆盖回去。

    **为什么必须播种而不是"新列默认全开"**:老库里用户可能已经关掉了某一类推送
    (如嫌 K4 派发警报吵),改版后若让新列取默认全开,等于**替用户把他关掉的通知
    又打开了**——这是通知系统最不能犯的错。六类之外的新 kind(`custom_alert` /
    四监测)不写进播种 JSON,由 `settings_store.get_push_kinds` 按「缺键取默认开」
    补齐(它们是全新能力,用户从未表达过意见,默认开与 V1 惯例一致)。

    在 `_migrate_columns` 加列之后调用(此时 `push_kinds` 列已存在)。"""
    from neckline.notify_kinds import LEGACY_COLUMN_OF_KIND

    row = conn.execute(
        "SELECT push_kinds FROM app_settings WHERE id=1"
    ).fetchone()
    if row is None or row[0] is not None:
        return  # 无设置行 / 已播种过 → 什么都不做
    cols = list(LEGACY_COLUMN_OF_KIND.items())          # [(kind, column), ...] 确定性序
    legacy = conn.execute(
        f"SELECT {', '.join(c for _k, c in cols)} FROM app_settings WHERE id=1"
    ).fetchone()
    if legacy is None:
        return
    seeded = {kind: (1 if legacy[i] else 0) for i, (kind, _c) in enumerate(cols)}
    conn.execute(
        "UPDATE app_settings SET push_kinds=? WHERE id=1 AND push_kinds IS NULL",
        (json.dumps(seeded, ensure_ascii=False, sort_keys=True),),
    )


def _backfill_activated_at(conn: sqlite3.Connection) -> None:
    """v1.2-A 一次性回填(幂等):对**唯一现役且 activated_at 仍空**的版本(生产=K1)
    以 `created_at` 作激活时间代理回填 `activated_at`。只碰 `is_active=1 AND activated_at
    IS NULL` 的行——重跑不变(已回填的行不再命中),从未激活的版本(is_active=0)保持
    NULL(正确)。老库无此戳会导致 `config_active_at` 落 legacy 兜底;回填后走时间线解析。
    在 `_migrate_columns` 加列之后调用(此时 activated_at 列已存在)。"""
    conn.execute(
        "UPDATE strategy_versions SET activated_at = created_at "
        "WHERE is_active = 1 AND activated_at IS NULL"
    )


def _seed_activation_log(conn: sqlite3.Connection) -> None:
    """v1.4 review 🟡-1 一次性播种(幂等):把老库 `strategy_versions.activated_at` 里那条
    「每版最后一次激活」的单戳时间线,搬进 append-only 的 `strategy_activation_log`。

    **判据 = 本表为空才播种**(不是「逐 (version, at) 对补齐」):播种是一次性的迁移动作,
    之后本表由 `brain` 的激活入口独占追加。若改成每次 init_schema 都拿 `activated_at` 去补,
    那么任何一次手工 SQL 改列都会凭空往历史里注入事件 —— append-only 的价值恰恰在于
    「事件只由激活动作产生」。空库(strategy_versions 无带戳行)播 0 行,本表继续为空,
    下次 init_schema 再试也仍是 no-op(便宜:一次 EXISTS + 一次 INSERT…SELECT)。

    顺序:必须在 `_backfill_activated_at` **之后**调 —— 生产 K1 的戳是那一步回填出来的,
    先播种会把 K1 漏掉(它当时还是 NULL)。"""
    seeded = conn.execute("SELECT 1 FROM strategy_activation_log LIMIT 1").fetchone()
    if seeded is not None:
        return
    conn.execute(
        "INSERT INTO strategy_activation_log (version, activated_at, via, note) "
        "SELECT version, activated_at, 'seed', "
        "       '从 strategy_versions.activated_at 播种(v1.4 review 🟡-1 迁移)' "
        "FROM strategy_versions WHERE activated_at IS NOT NULL "
        "ORDER BY activated_at, version"
    )


# **依赖迁移列的索引**(v2.0.0 起,契约线审计 🟡 Y7 引入这个小机制):`_SCHEMA` 是
# `executescript` 一把梭,里面的 `CREATE INDEX` 在**老库**上会先于 `ALTER TABLE ADD COLUMN`
# 执行 —— 引用迁移列的索引写进 `_SCHEMA` 会在老库上直接报 "no such column"。故这类索引
# 一律登记到这里,由 `_migrate_columns` 在补完列**之后**建(同样 `IF NOT EXISTS` 幂等)。
_POST_MIGRATION_INDEXES = (
    # `POST /positions` 幂等键的库级防重。**部分索引**(`WHERE ... IS NOT NULL`):没给键的
    # 行(CLI / 历史补录 / 老客户端)不参与去重 —— SQLite 的 UNIQUE 本来就不去重 NULL,
    # 这里写明 WHERE 是为了让"只约束给了键的那些行"这件事在 DDL 上就一目了然。
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_idempotency_key "
    "ON positions(idempotency_key) WHERE idempotency_key IS NOT NULL",
    # V2.2-①:「任一时刻至多一个现役策略包」改口径为「**每线**至多一个现役」
    # (plan §五 ①「唯一现役约束改口径」)。旧的全表口径索引
    # `idx_selection_packs_single_active`(契约线审计 🔵 B3,2026-08-03 加)先 DROP
    # 再建 per-line 版 —— 骨架 V 与引擎 C/Z/Y 四条线要并跑,全表唯一会把「激活第
    # 二条线」直接拦死。防「手工 SQL 造两行同线现役」的 B3 原意由新索引原样接管。
    # ⚠ **部署顺序铁律(plan §五 ① 原文)**:生产上必须**先**由
    # `scripts/oneoff/retire_legacy_packs.py` 把 LEGACY 行的 `is_active` 清零,**再**
    # 激活骨架线 —— 顺序反了新索引照样能建(单行不冲突),但接下来会出现「LEGACY 与
    # V 两行同时现役」,两行都合法、`get_active_*` 各取各的,正是 B3 要防的
    # 「今天用的是哪个包看运气」。init_schema 管不了这条(它不改业务行),⑦ 部署清单管。
    "DROP INDEX IF EXISTS idx_selection_packs_single_active",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_selection_packs_single_active_per_line "
    "ON selection_packs(line_code, is_active) WHERE is_active = 1",
)


def _relax_holding_eod_check_notnull(conn: sqlite3.Connection) -> None:
    """把老库 `holding_eod_check.max_hold_effective` 的 `NOT NULL` **一次性原子放宽**(V2.2-⑤)。

    **为什么非做不可**:`v2.2-k8` 章程 `max_hold_days=None`(K8 §十三 时间退出退役)→ 16:35
    体检算出的「有效硬上限」是 `None`。老列是 `NOT NULL`,写 `NULL` 会 `IntegrityError`
    → **整份 16:35 报告崩掉**(承 CLAUDE.md「一处裸奔就把『少一维』升级成『当日无报告』」)。
    而写 5 顶上是**说谎**:`('holding', 5)` 在 K1 的 D2 与 K8 的任何一天下长得一模一样。

    **SQLite 无 `ALTER COLUMN`**,唯一办法是「建新表 → 拷 → 删旧 → 改名」。三条安全措施:
      ① **有闸**:先查 `PRAGMA table_info`,只有该列仍是 `notnull=1` 时才动 → 天然幂等,
         新库(本文件 DDL 已是可空)与已迁移过的库都直接跳过,一条语句都不执行;
      ② **原子**:显式 `BEGIN IMMEDIATE` / `COMMIT`(Python `sqlite3` 对 DDL **不**自动开
         事务,不显式 BEGIN 就是逐句 autocommit —— 那才是"删到一半断电"的真风险);
      ③ **不锁死开机**:失败 → `ROLLBACK` + 吵一条 WARNING + 继续启动(同上面唯一索引
         那条的既定姿势)。写侧另有 `IntegrityError` 兜底(见 `holding_store`),不会因为
         这里没迁成就在 16:35 崩掉。

    拷贝用**显式列名**(不 `SELECT *`):迁移列 `data_unavailable` 等在老库里位置不定,
    按位置拷会串列。表很小(每持仓每交易日一行),一次性成本可忽略。"""
    info = list(conn.execute("PRAGMA table_info(holding_eod_check)"))
    if not info:
        return                                   # 表还没建(理论上 executescript 已建)
    target = [r for r in info if r[1] == "max_hold_effective"]
    if not target or not target[0][3]:           # r[3] = notnull
        return                                   # 已是可空 → 一条语句都不执行
    cols = [r[1] for r in info]
    col_list = ", ".join(cols)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE holding_eod_check__v22 (
                position_id         INTEGER NOT NULL,
                trade_date          TEXT NOT NULL,
                d_count             INTEGER NOT NULL DEFAULT 1,
                net_float           REAL,
                time_exit_state     TEXT NOT NULL DEFAULT 'holding',
                max_hold_effective  INTEGER DEFAULT 5,
                k4_hits_json        TEXT NOT NULL DEFAULT '[]',
                has_strong          INTEGER NOT NULL DEFAULT 0,
                scenario_review     INTEGER NOT NULL DEFAULT 0,
                time_exit_locked_state      TEXT,
                time_exit_locked_date       TEXT,
                time_exit_locked_net_float  REAL,
                data_unavailable    INTEGER,
                created_at          TEXT NOT NULL,
                PRIMARY KEY (position_id, trade_date)
            )
        """)
        conn.execute(
            f"INSERT INTO holding_eod_check__v22 ({col_list}) "
            f"SELECT {col_list} FROM holding_eod_check"
        )
        conn.execute("DROP TABLE holding_eod_check")
        conn.execute("ALTER TABLE holding_eod_check__v22 RENAME TO holding_eod_check")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holding_eod_check_position "
                     "ON holding_eod_check(position_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_holding_eod_check_trade_date "
                     "ON holding_eod_check(trade_date)")
        conn.execute("COMMIT")
        logger.info("[db] holding_eod_check.max_hold_effective 已由 NOT NULL 放宽为可空"
                    "(V2.2-⑤:章程无时间退出条款时该值是 NULL,不是 5)")
    except sqlite3.Error:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        logger.warning(
            "[db] holding_eod_check.max_hold_effective 放宽为可空失败 —— **不拦启动**"
            "(写侧另有 IntegrityError 兜底,见 report/holding_store.save_holding_eod_checks)。"
            "请人工核对该表后重跑一次 `init_schema`。", exc_info=True,
        )


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """对既有表做「缺列即补」的幂等迁移(见 `_COLUMN_MIGRATIONS` 注释)+ 依赖迁移列的索引
    (`_POST_MIGRATION_INDEXES`,必须在补列之后建)+ v1.2-A 激活戳一次性回填(幂等,见
    `_backfill_activated_at`)+ v1.4 激活历史播种(幂等,见 `_seed_activation_log`)+
    V2-⑪ 推送开关按 kind 播种(幂等,见 `_seed_push_kinds`)。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    for stmt in _POST_MIGRATION_INDEXES:
        try:
            conn.execute(stmt)
        except sqlite3.IntegrityError:
            # **已有脏数据的库不许被开机脚本锁死**:唯一索引的职责是「防止新的脏数据」,
            # 不是「让一个已经脏了的库开不了机」——`init_schema` 被所有入口调用,在这里
            # 硬抛等于 API / 哨兵 / 16:35 报告全线阵亡,还只给一句 "UNIQUE constraint
            # failed"。故:吵得足够响 + 说清怎么修 + 继续跑(读侧另有告警,如
            # `pack.get_active_pack` 遇多行现役会再吵一次)。
            logger.warning(
                "[db] 唯一索引建不上,说明库里**已经存在**违反该约束的历史行:%s —— "
                "本次跳过建索引、继续启动(新的重复写入因此暂时拦不住)。"
                "请人工核对并清理重复行后重跑一次 `init_schema`(如 selection_packs "
                "多行 is_active=1 → 把多余的置 0;positions 幂等键重复 → 保留正确那笔)。",
                stmt, exc_info=True,
            )
    _relax_holding_eod_check_notnull(conn)
    _backfill_activated_at(conn)
    _seed_activation_log(conn)
    _seed_push_kinds(conn)


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """开一条新连接(调用方负责 close,或用 `connection()` 上下文管理器)。"""
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def connection(db_path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema(db_path: Optional[Path] = None) -> None:
    """建表(幂等,`IF NOT EXISTS`)+ 既有表缺列补齐(幂等 `ALTER TABLE`,见
    `_migrate_columns`)。backfill / init_calendar / 各 store 入口处调用。"""
    with connection(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_columns(conn)


__all__ = ["get_connection", "connection", "init_schema"]
