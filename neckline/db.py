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
CREATE TABLE IF NOT EXISTS strategy_versions (
    version         TEXT PRIMARY KEY,   -- 策略版本号,K 字头整数(K1/K2/...;系统版本走 v 字头,两线解耦)
    created_at      TEXT NOT NULL,      -- ISO8601
    rule_json       TEXT NOT NULL,      -- 规则参数快照(JSON)
    changelog       TEXT NOT NULL,      -- 本版为何这样定(过堂结论摘要)
    metrics_json    TEXT NOT NULL DEFAULT '{}',  -- 定版回测指标(JSON)
    is_active       INTEGER NOT NULL DEFAULT 0
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
]


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """对既有表做「缺列即补」的幂等迁移(见 `_COLUMN_MIGRATIONS` 注释)。"""
    for table, column, ddl in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


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
