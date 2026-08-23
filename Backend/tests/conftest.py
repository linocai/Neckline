"""共享测试夹具。核心目标:测试与真实 `data/`(项目实际 Parquet/SQLite)完全隔离
——每个测试拿一份 tmp_path 下的干净 DB/Parquet 目录,不依赖、也不污染真实数据。

Settings 是 frozen dataclass,不能 `setattr` 单个字段;换库路径按 LinoN 教训用
"替身对象 + monkeypatch 模块级 settings 名字"(每个 `from neckline.config import
settings` 的模块各自 patch 一遍,因为各模块持有各自的本地绑定)。

**§七 P4-48 全局兜底重定向(V2.2-① 结案,治类不治例)**:见文件最上方那段
`os.environ["DB_PATH"]` 注入 —— 它必须发生在本文件(乃至整个 pytest 进程)第一次
import `neckline.config` **之前**,所以不住在夹具里、直接写在 import 区之前。
"""

from __future__ import annotations

# ══════════════════════════════════════════════════════════════════════════
# §七 P4-48(测试隔离写库残留,V2.2-① 结案):**全局兜底重定向,治类不治例**。
# 病根:全量 pytest 里任何经 `db_path=None` 兜底的调用(`neckline/db.py` 的
# `db_path or settings.db_path`)都会**静默写真实开发库** `data/neckline.db`
# ——A8 批次修过一轮调用点,后续版本新增测试又带回(逐例修是打地鼠)。
# 修法:`neckline.config._load_settings()` 本来就支持 `DB_PATH` 环境变量覆盖
# (冒烟/隔离测试的既有后门)——在**任何 neckline 模块被 import 之前**把它指到
# 一次性临时目录,则所有 `db_path=None` 兜底从此天然落在废弃桶里,新测试怎么漏
# 传都污染不到真实开发库。真需要真库数据的护栏用例走 `real_db_readonly_copy`
# (它按 `neckline.config.DB_PATH` **常量**找真库,不受本环境变量影响,已核)。
# ⚠ 若外部已显式设了 DB_PATH(如 A8 探针法手动重定向),尊重外部值不再覆盖。
# 机器判据:`tests/test_db_isolation_guardrail.py` 的 P4-48 段(重定向失效即红)。
# ══════════════════════════════════════════════════════════════════════════
import os as _os
import os.path as _ospath
import sys as _sys
import tempfile as _tempfile

if "DB_PATH" not in _os.environ:
    _os.environ["DB_PATH"] = _ospath.join(
        _tempfile.mkdtemp(prefix="neckline-tests-dbredirect-"), "neckline.db"
    )
if "neckline.config" in _sys.modules:   # 万一有插件抢先 import 过,best-effort 刷新
    _sys.modules["neckline.config"].reload_settings()

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


@pytest.fixture(scope="session")
def real_db_readonly_copy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """§七 P4-25(v1.5-④-A4):给「刻意读真实开发库 K1 现役行」的护栏用例
    (`test_k3_oversold_guardrail.py`/`test_k2_mainline_guardrail.py`/
    `test_v13_exit_6y_baseline.py`)提供一份**一次性副本**的 `db_path`。

    **为什么需要这个夹具**:这几个用例的意图是校验"当前真实 K1 config 在合成盘上
    的选股结果没被新增字段污染",数据必须来自真库(不能拿 `isolated_env` 临时
    库顶替——那样测不出"大脑真的没被后续研究改动"这件事)。但 `brain.active_config()`
    裸调用(不传 `db_path`)会命中 `neckline/db.py` 自己的模块级 `settings.db_path`
    ——项目 CLAUDE.md「测试隔离」条早已记载:`isolated_env`/`api_env` 只重写
    `market_data`/`trading_calendar`/`tushare_client` 三处 `settings` 绑定,
    **不含 `neckline.db`**——于是每次调用触发的 `init_schema()`(`active_config`→
    `get_active`→...的连锁,`_migrate_columns` 幂等 `ALTER TABLE`)会把新表/新列
    的幂等迁移顺手写进开发者的真实工作库(§七 P4-25 原始发现,2026-07-29 已实测
    复现:`llm_judgments.search_engine` 这一列就是这样在本机被提前建出来的)。

    本夹具用 `sqlite3` 官方 backup API(WAL 模式下比 `shutil.copy2` 更可靠,同项目
    生产 `.backup` 既有姿势,见 CLAUDE.md「生产实战定案」节)把真库拷一份**会话级
    临时副本**;调用方对副本传 `db_path=`——校验的仍是真库当时的 K1 行,但
    `init_schema` 的任何副作用只落在这份用完即扔的副本上,不碰真实
    `data/neckline.db`。**session 级作用域**:一次会话内多个用例共享同一份副本
    (副本本身只读、不会被测试写坏,复制一次即可,不必每个用例重拷)。

    真库不存在(全新 clone / CI 环境无 `data/`)→ `pytest.skip` 并给出清晰原因,
    不伪造一个假的 K1 行去凑测试通过。"""
    import sqlite3

    from neckline.config import DB_PATH

    if not DB_PATH.exists():
        pytest.skip(f"真实开发库不存在({DB_PATH}),此护栏用例需要真库现役 K1 行,本环境无法运行。")
    dest = tmp_path_factory.mktemp("real_db_copy") / "neckline_readonly_copy.db"
    # ``mode=ro`` alone may still participate in SQLite's WAL shared-memory
    # coordination and update the working database's ``-shm`` sidecar.  The
    # fixture only copies frozen facts, so immutable is both accurate and
    # necessary to keep even test reads from touching operational state.
    src_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()
    return dest


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

    for mod in (deps_mod, apns_mod, tc_mod, md_mod, ts_mod):
        monkeypatch.setattr(mod, "settings", api_settings)
    api_settings.data_dir.mkdir(parents=True, exist_ok=True)
    api_settings.parquet_dir.mkdir(parents=True, exist_ok=True)
    init_schema(db_path=api_settings.db_path)
    tc_mod.reset_cache()

    monkeypatch.setattr(app_mod, "ENABLE_MORNING_TASKS", False)
    monkeypatch.setattr(app_mod, "_DB_PATH_OVERRIDE", api_settings.db_path)
    monkeypatch.setattr(app_mod, "_PARQUET_DIR_OVERRIDE", api_settings.parquet_dir)
    # 复盘上传材料也必须留在隔离目录，不能读到真实项目数据。
    monkeypatch.setattr(app_mod, "_DATA_DIR_OVERRIDE", api_settings.data_dir)
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


def markdown_modulo_generated_at(bundle) -> str:
    """把报告 markdown 里的 `generated_at` 审计戳换成占位符,供「同一天重跑两次
    逐字节一致」这类**可复现性**断言使用(唯一实现,⛔ 别在各测试里各抄一份)。

    🔴 **这不是"把断言放宽",是在修一个写错了的前提**:`render.py` 报告头第一行
    就印着 `*生成时间(UTC):{generated_at} · …*`(自阶段2.5 起一直如此),而
    `pipeline.build_report` 里 `generated_at = datetime.now(timezone.utc)
    .isoformat(timespec="seconds")` 是**秒精度墙钟**。两次背靠背 `build_report`
    只要跨过一个整秒边界,裸比全文就红 —— 失败概率 ≈ 两次调用的间隔 ÷ 1 秒,于是
    **孤立跑几乎不红、全量跑(机器忙、间隔被拉长)间歇红**。这正是它当初能被写下
    并活到今天的原因:每次复查都"跑一遍绿了"。

    归一化**按 bundle 自己那一串 `generated_at` 逐字替换**,⛔ 不按正则去猜那一行
    —— 除这个戳以外的任何不确定性(排序不稳、hash 盐、别的时间派生值)照样会让
    断言红,断言强度一分没降。
    """
    stamp = bundle.generated_at
    assert stamp and stamp in bundle.markdown, (
        "报告头不再包含 `generated_at` 审计戳 —— 本归一化已成空操作,"
        "「重跑逐字节一致」的断言会退化成裸比全文。请同步修正本函数与调用方,"
        f"⛔ 别直接删掉调用(generated_at={stamp!r})。"
    )
    return bundle.markdown.replace(stamp, "<GENERATED_AT>")


def insert_sw_members(settings: Settings, rows: List[dict]) -> None:
    """写 `sw_industry_member`(V2.5.0 S3):申万二级归属是**判据输入** —— 事实包的
    `sw_l2_code` / 行业中位数 / 相对强度全靠它,K9 第一层的白酒排除也按 `l2_code` 走。

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
