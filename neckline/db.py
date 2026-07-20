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
    version         TEXT PRIMARY KEY,   -- 语义版本号,如 'v1'
    created_at      TEXT NOT NULL,      -- ISO8601
    rule_json       TEXT NOT NULL,      -- 规则参数快照(JSON)
    changelog       TEXT NOT NULL,      -- 本版为何这样定(过堂结论摘要)
    metrics_json    TEXT NOT NULL DEFAULT '{}',  -- 定版回测指标(JSON)
    is_active       INTEGER NOT NULL DEFAULT 0
);

-- 盘后报告存档(plan 2.5)。一个交易日一行(幂等覆盖,重跑报告不留重复行);
-- *_json 是该次报告的结构化快照(情绪仪表盘/强势板块/候选20只四件套),供事后
-- 审计与历史回放核对;markdown 是渲染产物全文。
CREATE TABLE IF NOT EXISTS reports (
    trade_date       TEXT PRIMARY KEY,   -- 'YYYYMMDD'
    generated_at     TEXT NOT NULL,      -- ISO8601
    strategy_version TEXT NOT NULL,      -- 生成本报告时用的大脑版本号(strategy_versions.version)
    sentiment_json   TEXT NOT NULL,
    sectors_json     TEXT NOT NULL,
    candidates_json  TEXT NOT NULL,
    markdown         TEXT NOT NULL
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
"""


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
    """建表(幂等,`IF NOT EXISTS`)。backfill / init_calendar 脚本入口处调用。"""
    with connection(db_path) as conn:
        conn.executescript(_SCHEMA)


__all__ = ["get_connection", "connection", "init_schema"]
