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


def insert_stock_basic(settings: Settings, rows: List[dict]) -> None:
    """写 `stock_basic`(SQLite)测试行,供需要股票中文名/板块/上市日的模块
    (`report/candidates.py` 的名称解析等)使用。每行至少给 `ts_code`,其余字段有
    合理缺省(`list_status="L"`);日期字段传 `date` 对象或 'YYYYMMDD' 字符串均可。"""
    import sqlite3

    from neckline.db import init_schema

    init_schema(db_path=settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        for r in rows:
            list_date = r.get("list_date")
            if isinstance(list_date, date):
                list_date = list_date.strftime("%Y%m%d")
            delist_date = r.get("delist_date")
            if isinstance(delist_date, date):
                delist_date = delist_date.strftime("%Y%m%d")
            conn.execute(
                "INSERT OR REPLACE INTO stock_basic "
                "(ts_code,symbol,name,industry,market,list_date,delist_date,list_status) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    r["ts_code"],
                    r.get("symbol", r["ts_code"].split(".")[0]),
                    r.get("name", r["ts_code"]),
                    r.get("industry"),
                    r.get("market", "主板"),
                    list_date,
                    delist_date,
                    r.get("list_status", "L"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def insert_namechange(settings: Settings, rows: List[dict]) -> None:
    """写 `namechange`(SQLite)测试行——`features.merge_meta` 的 `is_st` 判定真正
    依据的是这张表(按 `name` 前缀 `ST`/`*ST` + as-of 生效日判断),不是
    `stock_basic.name`。每行至少给 `ts_code`/`name`/`start_date`。"""
    import sqlite3

    from neckline.db import init_schema

    init_schema(db_path=settings.db_path)
    conn = sqlite3.connect(str(settings.db_path))
    try:
        for r in rows:
            start_date = r["start_date"]
            if isinstance(start_date, date):
                start_date = start_date.strftime("%Y%m%d")
            end_date = r.get("end_date")
            if isinstance(end_date, date):
                end_date = end_date.strftime("%Y%m%d")
            conn.execute(
                "INSERT OR REPLACE INTO namechange (ts_code,name,start_date,end_date,ann_date,change_reason) "
                "VALUES (?,?,?,?,?,?)",
                (r["ts_code"], r["name"], start_date, end_date, r.get("ann_date"), r.get("change_reason")),
            )
        conn.commit()
    finally:
        conn.close()


# rule v1 的精简镜像(阶段2 报告管线测试专用)——**有意不 import research/rule_v1.py
# 的 RULE_V1**:tests/ 不应依赖 research/(纯研究脚本,可能改动/有导入期副作用),
# 测试夹具须自包含。字段含义与真实 rule v1 完全一致(见 research/rule_v1.py 注释),
# 只是各自独立维护,不假设两边逐字同步。
TEST_RULE_V1_CONFIG = dict(
    strength="none",
    buypoint="pullback",
    forbid_high_elasticity=True,   # 主板 only
    stop_pct=0.05,
    take_profit_retrace=0.05,
    max_hold_days=5,
    cooldown_days=0,
    single_cap=20000.0,
    max_positions=5,
    max_exposure_frac=0.60,
    week_halving=False,
)


def seed_active_rule_v1(settings: Settings, extra_config: dict = None) -> None:
    """把 `TEST_RULE_V1_CONFIG` 存成大脑现役版本 `v1`(`neckline.strategy.brain`),
    供需要"某个现役规则"才能跑的报告管线测试使用(`build_report` 无现役版本时会
    直接拒绝生成报告)。"""
    from neckline.strategy import brain

    cfg = dict(TEST_RULE_V1_CONFIG)
    cfg.update(extra_config or {})
    brain.save_version(
        "v1", {"config": cfg}, "测试夹具:镜像 rule v1(不依赖 research/)",
        metrics={}, activate=True, db_path=settings.db_path,
    )


def seed_synthetic_market(
    settings: Settings,
    *,
    start: date = date(2024, 1, 2),
    n_days: int = 30,
) -> List[date]:
    """铺一份"看起来正常"的多票多日合成行情(daily/adj_factor/daily_basic +
    stock_basic + namechange + trade_cal),覆盖 `base_universe_expr` 与 rule v1
    pullback 买点的全部前置条件——供 `test_pipeline.py`/`test_report_consistency.py`
    这类"要跑通整条 I/O 管线"的测试复用,避免各处重新手搓一遍合成行情。

    返回交易日列表(升序),**最后一天即"报告日"**,固定 3 只票:
        · "600001.SH" 主板,持续上涨后报告日小幅回调 → 应通过 rule v1(pullback)入池。
        · "600002.SH" 主板但当前是 *ST → 应被 base_universe(`~is_st`)剔除。
        · "300001.SZ" 创业板,价格路径与 600001.SH 相同 → 应被 rule v1 主板 only 剔除。
    三者的存在与否(通过/剔除)本身就是"熔断线"——验证 mask 确实在筛选,不是摆设。

    **v1.3-③-C3**:同时铺一个「常驻概念板块」`储能`(`ths_index`/`ths_member`,成分
    = {600001.SH, 600002.SH})——让候选情报管线(`report/intel_candidates.py`,K1 entry
    mask 退役后 `build_report` 的候选生成源)能识别到一个 step① 板块并从其成员里产候选。
    刻意**不含 300001.SZ**(它不是任何 step① 板块成员 → 情报管线天然不纳入),从而
    「问询台强制纳入」/「自选体检独立于候选」等既有断言(300001.SZ 不在候选)继续成立。
    只铺 `ths_index`+`ths_member`(不铺 `ths_daily`)——板块常驻按名精确匹配即可入 step①,
    板块年龄/资金流的完整链路由 `test_intel_candidates.py` 的手搓面板专测。
    """
    dates = business_days(start, n_days)
    insert_trade_cal(settings, dates)

    codes = ["600001.SH", "600002.SH", "300001.SZ"]

    def _path(n: int) -> List[float]:
        closes = [10.0 * (1.01 ** i) for i in range(n - 1)]
        closes.append(closes[-1] * 0.99)  # 报告日(最后一天)小幅回调,满足 pullback 买点
        return closes

    price_paths = {c: _path(n_days) for c in codes}
    for i, d in enumerate(dates):
        daily_rows, adj_rows, basic_rows = [], [], []
        for code, closes in price_paths.items():
            c = closes[i]
            pre = closes[i - 1] if i > 0 else c
            daily_rows.append({
                "ts_code": code, "open": c, "high": c, "low": c, "close": c, "pre_close": pre,
                "vol": 100000.0, "amount": 30000.0,
            })
            adj_rows.append({"ts_code": code, "adj_factor": 1.0})
            basic_rows.append({
                "ts_code": code, "turnover_rate": 5.0, "volume_ratio": 1.0,
                "circ_mv": 1_000_000.0, "total_mv": 1_000_000.0, "free_share": 100_000.0,
            })
        write_daily_fixture(settings, "daily", d, daily_rows)
        write_daily_fixture(settings, "adj_factor", d, adj_rows)
        write_daily_fixture(settings, "daily_basic", d, basic_rows)

    # v1.3-③-C3 行业闸:600001/600002 给同一行业「电气设备」→ 在「储能」板块内 100% 主导 → 过闸
    # (否则无 industry 一律不通过闸,情报候选会空掉,test_pipeline 的 600001 入选断言会挂)。
    insert_stock_basic(settings, [
        {"ts_code": "600001.SH", "name": "示例甲", "market": "主板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
        {"ts_code": "600002.SH", "name": "*ST示例乙", "market": "主板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
        {"ts_code": "300001.SZ", "name": "示例丙", "market": "创业板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
    ])
    insert_namechange(settings, [
        {"ts_code": "600002.SH", "name": "*ST示例乙", "start_date": start - timedelta(days=365)},
    ])
    # v1.3-③-C3:常驻概念板块「储能」(名称即 settings_store.DEFAULT_INTEL_WATCH_BOARDS 之一,
    # 走「五常驻按 ths_index.name 精确匹配」路径)。成分只含 600001.SH/600002.SH(不含 300001.SZ)。
    write_flat_parquet(settings, "ths_index.parquet", [
        {"ts_code": "885921.TI", "name": "储能"},
    ])
    write_flat_parquet(settings, "ths_member.parquet", [
        {"index_code": "885921.TI", "con_code": "600001.SH"},
        {"index_code": "885921.TI", "con_code": "600002.SH"},
    ])
    return dates


API_TEST_TOKEN = "test_token_at_least_16_chars_xyz"


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    """带 `api_token` 的隔离 Settings(阶段4 API 测试专用;`fake_settings` 的 api_token
    为 None,鉴权测试需要一个 len>=16 的 token)。"""
    import dataclasses
    data_dir = tmp_path / "data"
    return dataclasses.replace(
        Settings(
            tushare_token=None, llm_provider=None, llm_api_key=None,
            project_root=tmp_path, data_dir=data_dir,
            parquet_dir=data_dir / "parquet", db_path=data_dir / "neckline.db",
        ),
        api_token=API_TEST_TOKEN,
    )


@pytest.fixture
def api_env(api_settings: Settings, monkeypatch: "pytest.MonkeyPatch"):
    """把 API 服务用到的 `settings` 名字全部换成隔离 Settings、建空 schema、关哨兵后台
    轮询、把 app 的 DB 指向隔离库、`_QUOTES_FN` 置空(免联网)。yield 隔离 Settings。"""
    import neckline.api.app as app_mod
    import neckline.api.deps as deps_mod
    import neckline.calendar.trading_calendar as tc_mod
    import neckline.data.market_data as md_mod
    import neckline.data.tushare_client as ts_mod
    import neckline.push.apns as apns_mod
    import neckline.settings_store as ss_mod
    from neckline.db import init_schema

    for mod in (deps_mod, apns_mod, tc_mod, md_mod, ts_mod):
        monkeypatch.setattr(mod, "settings", api_settings)
    monkeypatch.setattr(ss_mod, "_default_settings", api_settings)

    api_settings.data_dir.mkdir(parents=True, exist_ok=True)
    api_settings.parquet_dir.mkdir(parents=True, exist_ok=True)
    init_schema(db_path=api_settings.db_path)
    tc_mod.reset_cache()

    monkeypatch.setattr(app_mod, "ENABLE_SENTINEL", False)
    monkeypatch.setattr(app_mod, "_DB_PATH_OVERRIDE", api_settings.db_path)
    monkeypatch.setattr(app_mod, "_QUOTES_FN", lambda codes: {})
    yield api_settings
    tc_mod.reset_cache()


@pytest.fixture
def client(api_env: Settings):
    """`TestClient(app)`,带隔离环境(`api_env`);测试用 `AUTH` 头带 Bearer token。"""
    from fastapi.testclient import TestClient

    import neckline.api.app as app_mod
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def AUTH() -> dict:
    return {"Authorization": f"Bearer {API_TEST_TOKEN}"}


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
    "insert_stock_basic",
    "insert_namechange",
    "TEST_RULE_V1_CONFIG",
    "seed_active_rule_v1",
    "seed_synthetic_market",
    "write_flat_parquet",
]
