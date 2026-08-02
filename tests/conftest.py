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
    src_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
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


def seed_industry_strength(settings: Settings, days: List[date]) -> dict:
    """把 `industry_strength_daily` 预计算表喂上(v1.4-⑩ / §七 P0-23)。

    **凡端到端跑 pipeline / 信息卡 / 问询台的测试都要先调它** —— 这三条在线路径 v1.4-⑩
    起**只读表**、不再现算(现算 = 全历史 `scan_parquet`,生产跑不完)。表没喂 = 保险丝
    降级(空 `industry_scores`),那是**另一组断言**(降级不崩 + 如实披露),别拿它当
    "取数坏了"。

    走的就是生产同一条写入路径(`refresh_industry_strength`,只读当日一个分区),**不是
    测试专用的第二套写法** —— 夹具和生产写侧共用同一份代码,夹具喂出来的表就是生产表。
    `days` 传该测试铺过 `daily` 分区的交易日(升序;顺序无关,函数内部会排序)。

    ⚠ `db_path` **必须显式传**(见 CLAUDE.md「测试隔离」条:`isolated_env` 不重写
    `neckline.db` 的 settings 绑定,`db_path=None` 会静默落到真实项目库)。"""
    from neckline.report.industry_strength_store import refresh_industry_strength

    return refresh_industry_strength(
        days, parquet_dir=settings.parquet_dir, db_path=settings.db_path
    )


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


def set_activation_timeline(db_path, events, *, active: str = None) -> None:
    """测试专用:把「章程激活历史」**重写**成一条确定的时间线(不受 `now()` 抖动影响)。

    ⚠ v1.4 review 🟡-1 之后,纪律判定的时间轴事实源 = **append-only 表**
    `strategy_activation_log`(`brain._activation_events`),`strategy_versions.activated_at`
    降级为兼容/展示列。**故造历史时间线必须写这张表** —— 只 UPDATE `activated_at`(v1.4
    之前的老姿势)已经不再决定判向,会造出"库里写着一套、判定按另一套"的假夹具。

    本 helper 一次性 `DELETE + INSERT` 该表:测试要的是「假装历史长这样」,不是生产语义;
    **生产侧永远只经 `brain.save_version/activate_version` 追加,不删不改**(这也是为什么
    重写逻辑住在 tests/ 而不是 brain 里 —— 生产代码里根本不该存在改写历史的函数)。

    `events` = `[(版本号, 激活戳 ISO), ...]`,按发生先后给;同一版本可以出现多次(回滚)。
    同步刷新每个版本的 `activated_at` = 它**最后一次**激活的戳(与生产不变式一致),并把
    `is_active` 置到 `active`(缺省 = 最后一个事件的版本)。
    """
    from neckline.db import connection, init_schema

    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute("DELETE FROM strategy_activation_log")
        for version, stamp in events:
            conn.execute(
                "INSERT INTO strategy_activation_log (version, activated_at, via, note) "
                "VALUES (?,?,'test','tests.conftest.set_activation_timeline')",
                (version, stamp),
            )
        last_stamp = {}
        for version, stamp in events:
            last_stamp[version] = stamp
        for version, stamp in last_stamp.items():
            conn.execute(
                "UPDATE strategy_versions SET activated_at=? WHERE version=?", (stamp, version)
            )
        winner = active if active is not None else (events[-1][0] if events else None)
        if winner is not None:
            conn.execute(
                "UPDATE strategy_versions SET is_active = CASE WHEN version=? THEN 1 ELSE 0 END",
                (winner,),
            )


def seed_synthetic_market(
    settings: Settings,
    *,
    start: date = date(2024, 1, 2),
    n_days: int = 30,
    with_industry_strength: bool = True,
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
    # v1.3-③-C3:常驻概念板块「储能」(名称即 settings_store.DEFAULT_INTEL_WATCH_BOARDS 之一,
    # 走「五常驻按 ths_index.name 精确匹配」路径)。成分只含 600001.SH/600002.SH(不含 300001.SZ)。
    write_flat_parquet(settings, "ths_index.parquet", [
        {"ts_code": "885921.TI", "name": "储能"},
    ])
    write_flat_parquet(settings, "ths_member.parquet", [
        {"index_code": "885921.TI", "con_code": "600001.SH"},
        {"index_code": "885921.TI", "con_code": "600002.SH"},
    ])
    # v1.4-⑩(§七 P0-23):行业强度**预计算表**日更 —— 一个「看起来正常」的市场,16:05
    # 日更该跑的都跑过了,表里该有行。**必须放在 `insert_stock_basic` 之后**(要行业映射)。
    # 本 fixture 的 3 只有价票同属「电气设备」(3 < `_MIN_MEMBERS`=5)→ 落行但
    # `industry_rank` 为 NULL,故 `load_industry_strength` 仍返回空列表 —— 与 v1.4-② 现算
    # 时代的行为逐位一致(判据侧无变化),但**新鲜度是「就绪」而不是「未就绪」**,这正是
    # 「没有(不够格)」与「没看(表空)」的分野。`with_industry_strength=False` 可造
    # 「日更没跑」的降级场景。
    if with_industry_strength:
        seed_industry_strength(settings, dates)
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
    # v1.3-⑥:`GET/PUT /settings/intel-boards` 是 app.py 端点层首次直接读 parquet
    # (`ths_index.parquet` 板块名校验)——同 `_DB_PATH_OVERRIDE` 姿势隔离,防止落到
    # 真实项目 `data/parquet`(未设置此项时其它测试从未触发过 parquet 读取,新增本行
    # 对既有测试零行为影响)。
    monkeypatch.setattr(app_mod, "_PARQUET_DIR_OVERRIDE", api_settings.parquet_dir)
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


def insert_decision_log_row(
    db_path,
    *,
    ts_code: str,
    why_buy: str = "",
    why_entry_price: str = "",
    invalidation: str = "",
    thesis_tags=None,
    playbook_tag: str = "SWING_CHASE",
    contingency_scenarios=None,
    name=None,
    target_price=None,
    exit_low=None,
    exit_high=None,
    planned_price=None,
    planned_qty=None,
    max_chase_pct=None,
    status: str = "pending",
    position_id=None,
    revision_of=None,
    created_at=None,
):
    """v2.0.0 起(PROJECT_PLAN §五 V2-⑩-C)`decision_log` 表停写留档、
    `neckline.decision_log` 不再提供 `create_decision` 等写函数——测试仍需要历史
    pending/filled/cancelled 行做 fixture(如 `pending_track` 的追踪对象、
    `exec_hint` C3 的最近决策查询),直接裸 SQL 插入,不经任何已退役的应用层写口。

    返回 `DecisionRow`(复用 `neckline.decision_log.get_decision` 装配),调用方
    原有的属性访问写法(`row.id`/`row.why_buy`/...)不必改。"""
    import json as _json
    from datetime import datetime as _dt, timezone as _tz

    from neckline.db import connection, init_schema
    from neckline.decision_log import get_decision

    init_schema(db_path)
    now = created_at or _dt.now(_tz.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO decision_log ("
            "ts_code, name, created_at, why_buy, why_entry_price, target_price, "
            "exit_low, exit_high, thesis_tags, invalidation, contingency_scenarios, "
            "playbook_tag, planned_price, planned_qty, status, position_id, revision_of, updated_at, "
            "max_chase_pct"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts_code, name, now, why_buy, why_entry_price, target_price,
                exit_low, exit_high, _json.dumps(list(thesis_tags or []), ensure_ascii=False),
                invalidation, _json.dumps(list(contingency_scenarios or []), ensure_ascii=False),
                playbook_tag, planned_price, planned_qty, status, position_id, revision_of, now,
                max_chase_pct,
            ),
        )
        new_id = int(cur.lastrowid)
    row = get_decision(new_id, db_path=db_path)
    assert row is not None
    return row


def set_decision_status(db_path, decision_id: int, status: str, *, position_id=None) -> None:
    """测试夹具:直接改历史行状态(模拟 v2.0.0 之前 `link_decision`/`cancel_decision`
    的最终效果),同样绕开已退役的应用层写函数——**只测试用**,生产代码不许有第二处
    对 `decision_log` 的 UPDATE(全仓 grep 守门,见 `tests/test_decision_log.py`)。"""
    from datetime import datetime as _dt, timezone as _tz

    from neckline.db import connection, init_schema

    init_schema(db_path)
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE decision_log SET status=?, position_id=COALESCE(?, position_id), "
            "updated_at=? WHERE id=?",
            (status, position_id, now, decision_id),
        )


__all__ = [
    "fake_settings",
    "isolated_env",
    "insert_trade_cal",
    "business_days",
    "write_daily_fixture",
    "seed_industry_strength",
    "insert_stock_basic",
    "insert_namechange",
    "TEST_RULE_V1_CONFIG",
    "seed_active_rule_v1",
    "set_activation_timeline",
    "seed_synthetic_market",
    "write_flat_parquet",
    "insert_decision_log_row",
    "set_decision_status",
]
