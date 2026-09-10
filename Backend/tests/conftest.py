"""Synthetic fixtures: no working database, market files, .env or live credentials."""
from __future__ import annotations

import os as _os
import tempfile as _tempfile
from pathlib import Path as _Path

_os.environ["PYTHON_DOTENV_DISABLED"] = "1"
_os.environ["DB_PATH"] = str(_Path(_tempfile.mkdtemp(prefix="neckline-tests-")) / "isolated.sqlite")

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
    # **2026-07-27 share→lift 改判据补丁**:闸判据从「板内占比」改「lift=板内占比÷全市场占比」
    # 后,板内 100% 不再自动过闸——若全市场(=本隔离库 stock_basic 全部行)恰好也是 100% 电气
    # 设备(此前只有这 3 只票),lift≡1 永远卡在阈值上(见 `report/intel_candidates.py::
    # _market_industry_shares`)。补 50 只无价「背景填充」股票(只进 stock_basic,不进任何板块
    # 成员)把全市场行业分布拉开,恢复"板内同行业默认过闸"的原设计意图(同一坑、同一修法,见
    # `tests/test_intel_candidates.py::_seed_market` 的 `market_filler` 参数)。
    insert_stock_basic(settings, [
        {"ts_code": "600001.SH", "name": "示例甲", "market": "主板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
        {"ts_code": "600002.SH", "name": "*ST示例乙", "market": "主板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
        {"ts_code": "300001.SZ", "name": "示例丙", "market": "创业板", "industry": "电气设备", "list_date": start - timedelta(days=365)},
    ] + [
        {"ts_code": f"9{j:05d}.SZ", "name": f"背景{j}", "industry": "背景填充行业"} for j in range(50)
    ])
    insert_namechange(settings, [
        {"ts_code": "600002.SH", "name": "*ST示例乙", "start_date": start - timedelta(days=365)},
    ])
    # 🔴 V2.5.0 S3:这里原先还调一个 `seed_industry_strength(settings, dates)`,
    # 铺 K8 的 `industry_strength_daily` 预计算表。**那个函数在 S1 删测试时就已经
    # 不存在了**(调用点没跟着删,靠 `TestEntryScreens` 里只剩夹具、没有用例才没炸)。
    # 本片随 `report/industry_strength.py` 整体退役一并摘除:行业强度的新家是
    # `facts/industry.py`(申万二级中位数,**无最小成员数门槛** —— 那是策略参数)。
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
            tushare_token=None,
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
    from neckline.db import init_schema

    for mod in (app_mod, deps_mod, apns_mod, tc_mod, md_mod, ts_mod):
        monkeypatch.setattr(mod, "settings", api_settings)
    api_settings.data_dir.mkdir(parents=True, exist_ok=True)
    api_settings.parquet_dir.mkdir(parents=True, exist_ok=True)
    init_schema(db_path=api_settings.db_path)
    from neckline.k10.schema import initialize_schema
    from neckline.k10.notifications import initialize_notifications_schema
    initialize_schema(api_settings.db_path)
    initialize_notifications_schema(api_settings.db_path)
    tc_mod.reset_cache()

    monkeypatch.setattr(app_mod, "_DB_PATH_OVERRIDE", api_settings.db_path)
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


def source_code_only(path: Path) -> str:
    """一个源文件**剥掉注释与 docstring** 之后的代码文本(守门用)。

    🔴 **为什么必须有它**:本仓的模块头习惯把「⛔ 不许做 X」连同 X 的名字一起写进
    docstring —— 裸文本 grep「有没有出现 X」于是**每次都红**,而
    「**一个对自己的注释报警的闸门等于没有闸门**」(CLAUDE.md ⑰ 现场教训:
    `preflight_a_route.sh` 被自己的护栏注释绊住,真出事那天没人会信它)。
    用 `ast.unparse` 重写一遍即可:注释天然消失,docstring 逐个摘掉,
    **真代码里的字符串常量原样保留**(SQL 仍然扫得到)。
    """
    import ast as _ast

    tree = _ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in _ast.walk(tree):
        if not isinstance(node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef,
                                 _ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], _ast.Expr)
                and isinstance(body[0].value, _ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [_ast.Pass()]
    return _ast.unparse(tree)




def insert_sw_members(settings: Settings, rows: List[dict]) -> None:
    """写 `sw_industry_member`(V2.5.0 S3):申万二级归属是**判据输入** —— 事实包的
    `sw_l2_code` 是行业归属的稳定事实；K10 的白酒硬排按 `l2_code` 走。

    每行至少给 `ts_code` 与 `l2_code`;`l1_*` / `l3_*` 缺省从 `l2_*` 派生(测试里
    只有二级是判据,一级 / 三级只是随包冻结的追溯字段)。⛔ 不经任何联网 fetcher。"""
    import sqlite3
    from datetime import datetime as _dt

    from neckline.db import init_schema

    init_schema(db_path=settings.db_path)
    now = _dt.now().isoformat(timespec="seconds")
    conn = sqlite3.connect(str(settings.db_path))
    try:
        for r in rows:
            l2c = r["l2_code"]
            l2n = r.get("l2_name", l2c)
            conn.execute(
                "INSERT OR REPLACE INTO sw_industry_member "
                "(ts_code,name,l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,"
                " in_date,out_date,is_current,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    r["ts_code"], r.get("name", r["ts_code"]),
                    r.get("l1_code", f"L1-{l2c}"), r.get("l1_name", f"一级-{l2n}"),
                    l2c, l2n,
                    r.get("l3_code", f"L3-{l2c}"), r.get("l3_name", f"三级-{l2n}"),
                    r.get("in_date"), r.get("out_date"),
                    0 if r.get("out_date") else 1, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "fake_settings",
    "isolated_env",
    "markdown_modulo_generated_at",
    "source_code_only",
    "insert_trade_cal",
    "business_days",
    "write_daily_fixture",
    "insert_stock_basic",
    "insert_namechange",
    "insert_sw_members",
    "seed_synthetic_market",
]


@pytest.fixture(autouse=True)
def deny_external_network(monkeypatch):
    """Offline acceptance: deny DNS and sockets except explicit loopback addresses."""
    import ipaddress
    import socket

    def allowed(host):
        if host in {None, "localhost"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    resolve, connect, connect_ex, sendto = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex, socket.socket.sendto

    def checked_resolve(host, *args, **kwargs):
        if not allowed(host):
            raise RuntimeError(f"Offline tests deny external DNS: {host}")
        return resolve(host, *args, **kwargs)

    def check_address(sock, address):
        if sock.family in {socket.AF_INET, socket.AF_INET6} and not allowed(address[0]):
            raise RuntimeError(f"Offline tests deny external network: {address[0]}")

    def checked_connect(sock, address):
        check_address(sock, address)
        return connect(sock, address)

    def checked_connect_ex(sock, address):
        check_address(sock, address)
        return connect_ex(sock, address)

    def checked_sendto(sock, data, *args):
        check_address(sock, args[-1])
        return sendto(sock, data, *args)

    monkeypatch.setattr(socket, "getaddrinfo", checked_resolve)
    monkeypatch.setattr(socket.socket, "connect", checked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", checked_connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", checked_sendto)


@pytest.fixture(autouse=True)
def inherit_offline_subprocess_guard(monkeypatch):
    import subprocess
    original = subprocess.Popen
    guard = str(Path(__file__).parent / 'offline_guard')

    def guarded(*args, **kwargs):
        import shlex
        command = args[0] if args else kwargs.get('args', [])
        words = shlex.split(command) if isinstance(command, str) else list(command)
        # Python children inherit the socket/DNS guard. Reject native network
        # clients and shell wrappers rather than letting them bypass Python.
        if words and Path(str(words[0])).name in {'curl','wget','ssh','scp','sftp','nc','ncat','netcat','sh','bash','zsh','fish','env'}:
            raise RuntimeError('Offline tests deny native network subprocess')
        if kwargs.get('shell'):
            raise RuntimeError('Offline tests deny shell subprocess bypass')
        env = dict(kwargs.get('env') or _os.environ)
        env['PYTHONPATH'] = guard + _os.pathsep + env.get('PYTHONPATH', '')
        kwargs['env'] = env
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', guarded)
