"""共享测试夹具。核心目标:测试与真实 `data/`(项目实际 Parquet/SQLite)完全隔离
——每个测试拿一份 tmp_path 下的干净 DB/Parquet 目录,不依赖、也不污染真实数据。

Settings 是 frozen dataclass,不能 `setattr` 单个字段;换库路径按 LinoN 教训用
"替身对象 + monkeypatch 模块级 settings 名字"(每个 `from neckline.config import
settings` 的模块各自 patch 一遍,因为各模块持有各自的本地绑定)。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import List

import polars as pl
import pytest

from neckline.config import Settings


@pytest.fixture
def fake_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        tushare_token=None,
        llm_provider=None,
        llm_api_key=None,
        project_root=tmp_path,
        data_dir=data_dir,
        parquet_dir=data_dir / "parquet",
        db_path=data_dir / "neckline.db",
    )


@pytest.fixture
def isolated_env(fake_settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """把 calendar / market_data / tushare_client 用到的 `settings` 名字全部换成
    指向 tmp_path 的替身,建好空 schema,测试结束后 calendar 缓存重置(不泄漏到
    下一个测试)。"""
    import neckline.calendar.trading_calendar as tc_mod
    import neckline.data.market_data as md_mod
    import neckline.data.tushare_client as ts_mod
    from neckline.db import init_schema

    monkeypatch.setattr(tc_mod, "settings", fake_settings)
    monkeypatch.setattr(md_mod, "settings", fake_settings)
    monkeypatch.setattr(ts_mod, "settings", fake_settings)

    fake_settings.data_dir.mkdir(parents=True, exist_ok=True)
    fake_settings.parquet_dir.mkdir(parents=True, exist_ok=True)
    init_schema(db_path=fake_settings.db_path)

    tc_mod.reset_cache()
    yield fake_settings
    tc_mod.reset_cache()


def insert_trade_cal(
    settings: Settings,
    open_days: List[date],
    exchange: str = "SSE",
    range_start: date = None,  # type: ignore[assignment]
    range_end: date = None,  # type: ignore[assignment]
) -> None:
    """写一段【稠密】trade_cal(每个自然日一行,is_open 0/1 都写)——照真实 TuShare
    trade_cal 的形状(每天都有记录,不是只记交易日)。

    【坑】早期版本只写 `open_days`(is_open=1)本身,不写 gap 日的 is_open=0 行,
    导致 DB "覆盖范围"(`coverage_min/max`,校 trading_calendar._in_db_coverage)
    收窄到 open_days 的 min~max,任何落在这个窗口之外的查询(如 open_days 之前的
    元旦)会被误判成"DB 覆盖不到"而跌回静态表 + 工作日近似兜底——把本该断言
    False 的非交易日错判成 True(`test_is_trading_day_false_for_gap_and_weekend`
    踩过)。默认 range 在 open_days 前后各留 5 天缓冲,专治这类边界场景。
    """
    import sqlite3
    from datetime import timedelta

    if not open_days:
        return
    start = range_start or (min(open_days) - timedelta(days=5))
    end = range_end or (max(open_days) + timedelta(days=5))
    open_set = set(open_days)

    conn = sqlite3.connect(str(settings.db_path))
    try:
        rows = []
        cur = start
        while cur <= end:
            rows.append((exchange, cur.strftime("%Y%m%d"), 1 if cur in open_set else 0, ""))
            cur += timedelta(days=1)
        conn.executemany(
            "INSERT OR REPLACE INTO trade_cal (exchange, cal_date, is_open, pretrade_date) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def business_days(start: date, n: int) -> List[date]:
    """简单生成 n 个"交易日"(跳过周六周日,不管节假日——测试专用简化日历)。"""
    out: List[date] = []
    cur = start
    while len(out) < n:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def write_daily_fixture(
    settings: Settings,
    table: str,
    trade_date: date,
    rows: List[dict],
) -> None:
    """按 market_data 的落盘约定(`<parquet_dir>/<table>/year=YYYY/<trade_date>.parquet`)
    写一天的测试数据,不经过 tushare_client(纯手工构造行)。"""
    from neckline.data.market_data import write_table_day

    df = pl.DataFrame(rows)
    if "trade_date" not in df.columns:
        df = df.with_columns(pl.lit(trade_date).alias("trade_date"))
    write_table_day(table, trade_date, df, parquet_dir=settings.parquet_dir)


def write_flat_parquet(settings: Settings, filename: str, rows: List[dict]) -> Path:
    """写一个不按年份分区的扁平 Parquet 文件到 `parquet_dir` 根下——同花顺概念板块
    三张表的落盘方式(plan 1.6/`scripts/backfill_concept.py`:`ths_index.parquet` /
    `ths_daily.parquet` / `ths_member.parquet`,阶段2 report/sectors.py 与
    report/candidates.py 的测试共用本 helper)。"""
    path = settings.parquet_dir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(path)
    return path


__all__ = [
    "fake_settings",
    "isolated_env",
    "insert_trade_cal",
    "business_days",
    "write_daily_fixture",
    "write_flat_parquet",
]
