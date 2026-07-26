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
-- activated_at(v1.2-A):该版本「成为现役」的时刻(ISO8601);NULL=从未激活。周复盘
-- 按周取「当时现役」config 判纪律靠它解析时间线(`brain.config_active_at`),防止用今天
-- 的章程重判历史周洗白旧违纪。加列 + 一次性回填现役 K1 见下方 `_COLUMN_MIGRATIONS` /
-- `_backfill_activated_at`。
CREATE TABLE IF NOT EXISTS strategy_versions (
    version         TEXT PRIMARY KEY,   -- 策略版本号,K 字头整数(K1/K2/...);章程修订走系统 v 字头
                                        -- (如 v1.2:config 承 K 血缘、仅改仓位字段,不占 K 命名空间)
    created_at      TEXT NOT NULL,      -- ISO8601
    rule_json       TEXT NOT NULL,      -- 规则参数快照(JSON)
    changelog       TEXT NOT NULL,      -- 本版为何这样定(过堂结论摘要)
    metrics_json    TEXT NOT NULL DEFAULT '{}',  -- 定版回测指标(JSON)
    is_active       INTEGER NOT NULL DEFAULT 0,
    activated_at    TEXT                -- ISO8601 | NULL(v1.2-A 激活时间线,见表头注释)
);

-- 盘后报告存档(plan 2.5)。一个交易日一行(幂等覆盖,重跑报告不留重复行);
-- *_json 是该次报告的结构化快照(情绪仪表盘/强势板块/候选20只四件套),供事后
-- 审计与历史回放核对;markdown 是渲染产物全文。
-- watchlist_json(v1.1-C.3 自选体检):`WatchlistCheckItem.public_dict()` 的 JSON
-- 数组快照,老报告行(建表早于本列)经 `_migrate_columns` 幂等补列取默认值
-- '[]'(前向兼容——旧报告没有这节,读回来就是空数组,不是 NULL 炸 json.loads)。
CREATE TABLE IF NOT EXISTS reports (
    trade_date       TEXT PRIMARY KEY,   -- 'YYYYMMDD'
    generated_at     TEXT NOT NULL,      -- ISO8601
    strategy_version TEXT NOT NULL,      -- 生成本报告时用的大脑版本号(strategy_versions.version)
    sentiment_json   TEXT NOT NULL,
    sectors_json     TEXT NOT NULL,
    candidates_json  TEXT NOT NULL,
    markdown         TEXT NOT NULL,
    watchlist_json   TEXT NOT NULL DEFAULT '[]'
);

-- LLM 逻辑审判存档(plan 2.4)。前10只候选每只一行;search_hits_json 是该次审判
-- 用到的联网搜索结果全文(§2.4「搜索结果全文落 SQLite 存档」,供事后审计"当时为何
-- 否决" + 自建历史新闻快照)。degraded=1 表示「LLM 未激活」占位,不是真实判断。
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

-- v1.2-B 预注册决策日志(plan §五 v1.2-B,§2.1 第 3 条人机协作配套)。下单前录八项,
-- 时间戳先于成交防结果污染;**审计件、非下单件**——本表任何写入路径(见
-- `neckline.decision_log`)绝无下单/撤单/拉行情副作用。
-- created_at:**服务端生成**,任何调用方(含 API 入参)都不能覆盖,杜绝预注册时间
-- 被伪造。八项预注册字段:why_buy①/why_entry_price②/target_price③/exit_low+
-- exit_high④/thesis_tags⑤(枚举码 JSON 数组)/invalidation⑥/contingency_scenarios⑦
-- (情景树 JSON 数组,每项 {scenario,trigger,action,matched};scenario/trigger/action
-- 是不可编辑预注册内容,matched 是唯一可事后翻的结果标记,专用端点
-- `set_scenario_outcomes` 才能碰)/playbook_tag⑧(单选枚举码)。
-- **不可编辑口径**:①-⑥ + ⑦的 scenario/trigger/action + ⑧ 全表无任何 UPDATE 语句
-- 触碰这些列(见 `neckline.decision_log` 模块注释逐一核对);改动只能
-- `revise_decision` 新增一行,`revision_of` 落**链根** id(该行若自身是修订行则取其
-- `revision_of`,否则该行本身即链根)——归因永远 `WHERE revision_of IS NULL` 取首版,
-- 或 `WHERE revision_of=<根id>` 一步取全部修订,无需递归遍历链条。
-- status:pending(预注册待决)/filled(成交后经 link 关联)/cancelled(用户放弃)/
-- expired(v1.2.1-C 挂单追踪 N 日到期,本块只建列不写)。position_id:成交后经
-- `link_decision` 回填,关联 `positions.id`(无 SQL 级 FK 约束,同本库其它表惯例)。
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
-- watchlist 读体检快照);②**次日 9:25:30 `sentinel/precall.py` 的 net_float_provider 读
-- 最近一份 net_float**(= v1.3-① 留的 seam 接线点;没有它浮盈豁免形同虚设,见该模块注释)。
-- net_float:D5 收盘净浮盈估算(现价EOD×qty − buy_price×qty − buy_fees实录 − 估算卖出费,
--   见 neckline/fees.py::estimate_net_float);停牌/无 EOD 数据 → NULL(precall 侧退保守判非浮盈)。
-- time_exit_state/max_hold_effective:16:35 权威两档时间退出分类(classify_time_exit,单一源
--   sentinel/precall)。k4_hits_json:命中项 JSON 数组 [{code,label,level,evidence,evidenceStrength}]。
-- has_strong:是否含「强价量证据」命中(= 触发第六类 APNs 派发警报的门槛,题材类弱证据不计入)。
-- scenario_review:该持仓是否有关联决策日志(via decision_log.position_id)含非空情景树待每日
--   对照(②-D 提醒,勾选仍走既有 scenario-outcome 端点,本表只做「挑出来」)。PK 保证幂等重跑。
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
    created_at          TEXT NOT NULL,
    PRIMARY KEY (position_id, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_holding_eod_check_position ON holding_eod_check(position_id);
CREATE INDEX IF NOT EXISTS idx_holding_eod_check_trade_date ON holding_eod_check(trade_date);
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


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """对既有表做「缺列即补」的幂等迁移(见 `_COLUMN_MIGRATIONS` 注释)+ v1.2-A 激活戳
    一次性回填(幂等,见 `_backfill_activated_at`)。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    _backfill_activated_at(conn)


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
