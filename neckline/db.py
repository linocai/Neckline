"""SQLite 元数据 / 业务台账层(plan §3.3)。

存放:交易日历(`trade_cal`)、股票 / 行业元数据(`stock_basic`)、股票曾用名 /
ST 状态历史(`namechange`)。回测大表(daily 等)走 Parquet,不进本库
——见 `neckline.data.tushare_client` 与 `scripts/backfill.py`。

设计:薄封装,stdlib `sqlite3` 直连,不引入 ORM。所有写入用
`INSERT OR REPLACE` / `INSERT OR IGNORE` 保证脚本可重复跑(幂等)。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from neckline.config import settings

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
-- watchlist_json(v1.1-C.3 自选体检):`WatchlistCheckItem.public_dict()` 的 JSON
-- 数组快照,老报告行(建表早于本列)经 `_migrate_columns` 幂等补列取默认值
-- '[]'(前向兼容——旧报告没有这节,读回来就是空数组,不是 NULL 炸 json.loads)。
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
-- llm_provider/llm_api_key:App 设置屏改的 LLM key/供应商,`get_provider()` 解析优先级
-- DB 覆盖 → `.env` 兜底(§3.4,运行时生效不重启)。**高危区**:key 服务端存取,DB 文件 600、
-- gitignored、rsync 永不同步覆盖(plan 不变量);GET /settings 只回 llmKeySet:bool,绝不回明文。
-- push_report/push_retreat:APNs 两类推送开关(§2.4 拍板,默认开可关)。
-- review_col_map:周复盘交割单列映射(JSON,4D 用)。
-- push_precall/push_d5exit:v1.1-A/B 新增两类 APNs 推送开关(盘前校准 9:26 汇总 /
-- D5 时间退出),默认开;老库经 `_migrate_columns` 幂等 ALTER 补列(见下)。
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
    triggered_json     TEXT NOT NULL DEFAULT '[]',  -- 本 tick 触发的条件族键(供下一拍持续性判定)
    red_via_json       TEXT NOT NULL DEFAULT '[]',  -- 红色触发路径(multi_condition / persist:<族>),审计留痕
    tier               TEXT NOT NULL DEFAULT 'none', -- none | yellow | red | red_latched
    recorded_at        TEXT NOT NULL,       -- ISO8601
    PRIMARY KEY (trade_date, hhmm)
);

-- v1.2-B 预注册决策日志(plan §五 v1.2-B,§2.1 第 3 条人机协作配套)。下单前录八项
-- (v1.4-⑤-B 起加第⑨项,见下),时间戳先于成交防结果污染;**审计件、非下单件**——
-- 本表任何写入路径(见 `neckline.decision_log`)绝无下单/撤单/拉行情副作用。
-- created_at:**服务端生成**,任何调用方(含 API 入参)都不能覆盖,杜绝预注册时间
-- 被伪造。八项预注册字段:why_buy①/why_entry_price②/target_price③/exit_low+
-- exit_high④/thesis_tags⑤(枚举码 JSON 数组)/invalidation⑥/contingency_scenarios⑦
-- (情景树 JSON 数组,每项 {scenario,trigger,action,matched};scenario/trigger/action
-- 是不可编辑预注册内容,matched 是唯一可事后翻的结果标记,专用端点
-- `set_scenario_outcomes` 才能碰)/playbook_tag⑧(单选枚举码)。
-- **不可编辑口径**:①-⑥ + ⑦的 scenario/trigger/action + ⑧ + ⑨(max_chase_pct)全表
-- 无任何 UPDATE 语句触碰这些列(见 `neckline.decision_log` 模块注释逐一核对);改动
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
-- 上限"。API 层要求**必须显式传该键**(即便值是 null)才能创建/修订决策日志——见
-- `api/app.py::_extract_max_chase_pct_or_400`;本表/领域层 `neckline.decision_log`
-- 对 Python 直调方(CLI/单测)保留 `None` 默认,不强制,前向兼容既有调用点。
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
    max_hold_effective  INTEGER NOT NULL DEFAULT 5,
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
]


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


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """对既有表做「缺列即补」的幂等迁移(见 `_COLUMN_MIGRATIONS` 注释)+ v1.2-A 激活戳
    一次性回填(幂等,见 `_backfill_activated_at`)+ v1.4 激活历史播种(幂等,见
    `_seed_activation_log`)。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _backfill_activated_at(conn)
    _seed_activation_log(conn)


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
