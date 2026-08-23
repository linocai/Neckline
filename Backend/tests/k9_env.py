"""K9 策略层单测的**合成市场**(V2.5.0 S6)。

⛔ 不是测试文件(不以 `test_` 开头,pytest 不收集),是给 `test_k9_layer.py` /
`test_v250_s6_k9_guard.py` / `test_report_k9.py` 共用的夹具工厂。

**造一个刚好能把四个通道各点亮一只票的市场**,外加逐条边界的反例。全部走
`tmp_path` 临时库 + 临时 parquet 根(§10 测试纪律:⛔ 绝不 fallback 到工作库)。

⚠ **这里的数字全是夹具,不是标定值、不是建议值、不是默认值。** 它们的唯一职责是
让判据有东西可判 —— 真值待标定(PROJECT_PLAN §8 的 20 项),生产参数包由 whynotme
标定、用户确认后放进 `Backend/config/`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import polars as pl

from neckline.facts import pack as fact_pack
from neckline.facts import store as fact_store
from neckline.k9 import params as P
from tests.conftest import (
    insert_namechange,
    insert_stock_basic,
    insert_sw_members,
    insert_trade_cal,
    write_daily_fixture,
)

#: 合成市场的交易日数。必须 ≥ `p3.longWindow`(夹具取 60),否则 p3 拿不到长窗读数。
SESSIONS = 70

#: 每只票的固定基准价。所有「平票」永远收在这里 → 行业中位数恒 0,
#: 相对强度因此就是那只票自己的涨跌幅,判据好读也好断言。
BASE_PRICE = 10.0

_FLAT_VOL = 100_000.0
_FLAT_AMOUNT = 1_000_000.0

#: `(ts_code, l2_code, l2_name)`。每个行业都配 4 只平票,让中位数恒为 0。
INDUSTRIES: Dict[str, str] = {
    "801080.SI": "半导体",
    "801081.SI": "电池",
    "801082.SI": "医疗器械",
    "801083.SI": "汽车零部件",
    "801125.SI": "白酒Ⅱ",       # K9 §二 第 2 条:整个二级行业排除
    "801099.SI": "其它Ⅱ",       # 边界反例都放这里
}

P1_CODE = "600001.SH"       # 放量启动
P2_CODE = "600002.SH"       # 超跌反弹
P3_CODE = "600003.SH"       # 中等生转强
P4_CODE = "600004.SH"       # 资金异动

# —— 逐条边界的反例 ————————————————————————————————————————————————————
STAR_CODE = "688001.SH"     # 1 科创板
BAIJIU_CODE = "600051.SH"   # 2 白酒
ST_CODE = "600061.SH"       # 3 ST
NEW_CODE = "600071.SH"      # 5 次新股
LIMIT_UP_CODE = "600081.SH"  # 8 当日涨停
ONE_LINE_CODE = "600091.SH"  # 一字跌停(⛔ 不被边界排除,由 p2 的判据挡掉)
ILLIQUID_CODE = "600101.SH"  # 7 流动性过弱
SPIKE_CODE = "600111.SH"     # 9 冲高回落
INTRADAY_HALT_CODE = "600121.SH"   # 裁定 12:盘中临时停牌 → **照常参与**
FULL_HALT_CODE = "600131.SH"       # 6 全天停牌(人造异常行:它本不该出现在 daily)
#: 6(后半句)**当日一行 daily 都没有** —— 这才是全天停牌在真实数据里的样子
#: (§4.6 实测:150 个交易日 2001 行全天停牌,**0 行**出现在 daily)。
#: 它平时正常交易、只在当日缺席,用来锁 R3-🔴-5:这种票在 `k9_disposition` 里
#: **必须有行**,否则「昨天为什么没选中它」对它答不上来。
NO_DAILY_CODE = "600141.SH"


def _flat_codes() -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for i, l2 in enumerate(("801080.SI", "801081.SI", "801082.SI", "801083.SI")):
        for j in range(4):
            out.append((f"6002{i}{j}.SH", l2))   # ⚠ 6002xx 段:与下面的边界反例码不重叠
    out.append(("600052.SH", "801125.SI"))
    for c in (STAR_CODE, BAIJIU_CODE, ST_CODE, NEW_CODE, LIMIT_UP_CODE, ONE_LINE_CODE,
              ILLIQUID_CODE, SPIKE_CODE, INTRADAY_HALT_CODE, FULL_HALT_CODE,
              NO_DAILY_CODE):
        out.append((c, "801099.SI" if c != BAIJIU_CODE else "801125.SI"))
    return out


UNIVERSE: Dict[str, str] = {
    P1_CODE: "801080.SI", P2_CODE: "801081.SI",
    P3_CODE: "801082.SI", P4_CODE: "801083.SI",
    **{c: l2 for c, l2 in _flat_codes()},
}


@dataclass(frozen=True)
class Bar:
    open: float
    high: float
    low: float
    close: float
    vol: float
    amount: float
    net_amount: float


def _flat_bar(net: float = 0.0, vol: float = _FLAT_VOL,
              amount: float = _FLAT_AMOUNT) -> Bar:
    return Bar(BASE_PRICE, BASE_PRICE, BASE_PRICE, BASE_PRICE, vol, amount, net)


def _amount_of(code: str) -> float:
    """给每只票一个**互不相同**的成交额:全市场成交额完全并列是合成数据才有的退化
    情形,会让「后百分之几」失去意义。`ILLIQUID_CODE` 刻意压到最低。"""
    if code == ILLIQUID_CODE:
        return 1_000.0
    if code in (P1_CODE, P2_CODE, P3_CODE, P4_CODE, INTRADAY_HALT_CODE):
        # B17 把流动性底部比例固定为 20%；四个通道正例必须明确不在底部组，
        # 否则这份夹具测到的是边界而不是通道。
        return 10_000_000.0 + int(code[2:6]) * 100.0
    return _FLAT_AMOUNT * (1.0 + (int(code[2:6]) % 17) / 5.0)


def _path(code: str, sessions: Sequence[date]) -> List[Bar]:
    """一只票整段的日线。索引 `-1` 就是**当日**(判据全落在它身上)。"""
    n = len(sessions)
    amt = _amount_of(code)
    bars = [_flat_bar(amount=amt) for _ in range(n)]

    if code == P1_CODE:
        # 横盘很久 → 今天 +5% 且放量 3 倍(振幅窗口内极差 5%,远小于门槛)
        bars[-1] = Bar(10.0, 10.5, 10.0, 10.5, _FLAT_VOL * 3, amt * 3, 100.0)
    elif code == INTRADAY_HALT_CODE:
        # 与 P1 同形:用来证明「盘中临时停牌」照常参与召回(裁定 12)
        bars[-1] = Bar(10.0, 10.5, 10.0, 10.5, _FLAT_VOL * 3, amt * 3, 50.0)
    elif code == P2_CODE:
        # 今天 −8%(主板跌停 10% → 归一化跌幅 0.8),非一字,量 1.5 倍
        bars[-1] = Bar(10.0, 10.0, 9.2, 9.2, _FLAT_VOL * 1.5, amt, -500.0)
    elif code == P3_CODE:
        # 长窗累计 ≈ 0;上一个短窗 +0.1%/天,当前短窗 +0.4%/天(转正且在改善);量未放
        px = BASE_PRICE
        for i in range(n):
            if i < n - 10:
                step = 0.0
            elif i < n - 5:
                step = 0.001
            else:
                step = 0.004
            nxt = round(px * (1 + step), 4)
            bars[i] = Bar(px, max(px, nxt), min(px, nxt), nxt, _FLAT_VOL, amt, 0.0)
            px = nxt
    elif code == P4_CODE:
        # 钱一直在进,价格没动(K9 §3.5 的画像)
        bars = [_flat_bar(net=10_000.0, amount=amt) for _ in range(n)]
    elif code == ILLIQUID_CODE:
        bars = [_flat_bar(amount=amt) for _ in range(n)]
    elif code == LIMIT_UP_CODE:
        bars[-1] = Bar(10.5, 11.0, 10.4, 11.0, _FLAT_VOL * 2, amt * 2, 200.0)
    elif code == ONE_LINE_CODE:
        # 一字跌停:开 = 高 = 低 = 收 = 跌停价 9.00
        bars[-1] = Bar(9.0, 9.0, 9.0, 9.0, _FLAT_VOL * 2, amt, -900.0)
    elif code == SPIKE_CODE:
        # 冲高 +10% 收 +6% → 回落 4 个点
        bars[-1] = Bar(10.2, 11.0, 10.1, 10.6, _FLAT_VOL * 2, amt * 2, 0.0)
    return bars


#: 已铺好的模板目录(按 `today` 缓存)。见 `seed()` 的说明。
_TEMPLATES: Dict[str, Path] = {}


def seed(env, *, today: Optional[date] = None, cache: bool = True) -> date:
    """铺满整段历史并**逐日冻结事实包**。返回当日(`as_of`)。

    ⚡ **同一进程内只真造一次**:70 个交易日 × 冻结一遍要 4 秒多,而本组有十几条
    用例都要这份市场。第一次造完把 `data/` 整个目录留成模板,之后按测试逐个**拷贝**
    (~0.1 秒)。每个测试仍然拿到**自己的**库与 parquet 根 —— 拷贝出来的是副本,
    互不影响(§10:⛔ 测试之间不许共享可写状态)。传 `cache=False` 可强制重造。

    ⚠ 模板里 `fact_packs.sources_json` 记的是**模板目录**的上游分区路径 —— 那是
    provenance 字段,不参与任何判定;真实链路里它当然记的是真路径。
    """
    key = (today or date(2024, 4, 30)).isoformat()
    if cache and key in _TEMPLATES:
        _restore(env, _TEMPLATES[key])
        return _sessions(today)[-1]
    day = _seed_fresh(env, today)
    if cache:
        _TEMPLATES[key] = _snapshot(env, key)
    return day


def _snapshot(env, key: str) -> Path:
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix=f"k9-env-{key}-"))
    shutil.copytree(env.parquet_dir, root / "parquet")
    shutil.copy2(env.db_path, root / "neckline.db")
    return root


def _restore(env, template: Path) -> None:
    import shutil

    if env.parquet_dir.exists():
        shutil.rmtree(env.parquet_dir)
    shutil.copytree(template / "parquet", env.parquet_dir)
    shutil.copy2(template / "neckline.db", env.db_path)
    import neckline.calendar.trading_calendar as tc_mod
    tc_mod.reset_cache()


def _seed_fresh(env, today: Optional[date]) -> date:
    sessions = _sessions(today)
    insert_trade_cal(env, sessions)
    insert_stock_basic(env, [
        {
            "ts_code": c,
            "name": ("ST示例" if c == ST_CODE else f"示例{c[:6]}"),
            "market": "科创板" if c == STAR_CODE else "主板",
            "list_date": sessions[-3] if c == NEW_CODE else date(2019, 1, 2),
        }
        for c in UNIVERSE
    ])
    insert_namechange(env, [
        {"ts_code": c, "name": ("ST示例" if c == ST_CODE else f"示例{c[:6]}"),
         "start_date": date(2019, 1, 2)}
        for c in UNIVERSE
    ])
    insert_sw_members(env, [
        {"ts_code": c, "l2_code": l2, "l2_name": INDUSTRIES[l2]}
        for c, l2 in UNIVERSE.items()
    ])
    _insert_classify(env)

    paths = {c: _path(c, sessions) for c in UNIVERSE}
    for i, day in enumerate(sessions):
        _seed_day(env, day, paths, i, last=(i == len(sessions) - 1))
        built = fact_pack.build(day, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert isinstance(built, fact_pack.CompletePack), getattr(built, "missing", None)
        fact_store.freeze_pack(built, parquet_dir=env.parquet_dir, db_path=env.db_path)
    return sessions[-1]


def _sessions(today: Optional[date]) -> List[date]:
    end = today or date(2024, 4, 30)
    days: List[date] = []
    d = end
    while len(days) < SESSIONS:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _insert_classify(env) -> None:
    import sqlite3
    from datetime import datetime as _dt

    conn = sqlite3.connect(str(env.db_path))
    try:
        for code, name in INDUSTRIES.items():
            conn.execute(
                "INSERT OR REPLACE INTO sw_industry_classify "
                "(index_code,name,level,parent_code,src,fetched_at) VALUES (?,?,?,?,?,?)",
                (code, name, "L2", "0", "SW2021", _dt.now().isoformat(timespec="seconds")),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_day(env, day: date, paths: Dict[str, List[Bar]], i: int, *, last: bool) -> None:
    daily: List[dict] = []
    basic: List[dict] = []
    adj: List[dict] = []
    flow: List[dict] = []
    for code, bars in paths.items():
        if code == FULL_HALT_CODE and not last:
            continue                       # 全天停牌的票平时也在 daily 里(它只在当日停)
        if code == NO_DAILY_CODE and last:
            continue                       # 当日**一行都没有**:全天停牌在真实数据里的样子
        b = bars[i]
        prev = bars[i - 1].close if i > 0 else BASE_PRICE
        daily.append({
            "ts_code": code, "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "pre_close": prev, "change": b.close - prev,
            "pct_chg": (b.close / prev - 1) * 100 if prev else 0.0,
            "vol": b.vol, "amount": b.amount,
        })
        basic.append({"ts_code": code, "turnover_rate": 5.0, "turnover_rate_f": 6.0,
                      "volume_ratio": 1.2, "circ_mv": 1e6, "total_mv": 2e6,
                      "free_share": 1e5})
        adj.append({"ts_code": code, "adj_factor": 1.0})
        flow.append({"ts_code": code, "net_amount": b.net_amount, "net_amount_rate": 0.5,
                     "buy_elg_amount": 0.0, "buy_lg_amount": 0.0})
    write_daily_fixture(env, "daily", day, daily)
    write_daily_fixture(env, "daily_basic", day, basic)
    write_daily_fixture(env, "adj_factor", day, adj)
    write_daily_fixture(env, "moneyflow_dc", day, flow)

    limits: List[dict] = []
    if last:
        limits = [
            {"ts_code": LIMIT_UP_CODE, "board": "MAIN", "status": "limit_up",
             "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
             "is_limit_up": True, "is_limit_down": False, "is_zaban": False,
             "consec_limit_up_days": 1},
            {"ts_code": ONE_LINE_CODE, "board": "MAIN", "status": "limit_down",
             "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
             "is_limit_up": False, "is_limit_down": True, "is_zaban": False,
             "consec_limit_up_days": 0},
        ]
    write_daily_fixture(env, "limit_derived", day, limits) if limits else _empty_limits(env, day)

    suspend: List[dict] = []
    if last:
        suspend = [
            # 裁定 12:盘中临时停牌 —— 当天正常交易,⛔ 不排除、照常计入中位数
            {"ts_code": INTRADAY_HALT_CODE, "suspend_type": "S", "suspend_timing": "9:30-9:40"},
            # 全天停牌 —— 本不该出现在 daily(人造异常行,用来锁边界第 6 条)
            {"ts_code": FULL_HALT_CODE, "suspend_type": "S", "suspend_timing": None},
            # 全天停牌的**常态**:当日 daily 里一行都没有(⛔ 不进 suspend_anomaly)
            {"ts_code": NO_DAILY_CODE, "suspend_type": "S", "suspend_timing": None},
        ]
    _write_suspend(env, day, suspend)


def _empty_limits(env, day: date) -> None:
    from neckline.data.market_data import write_table_day

    write_table_day("limit_derived", day, pl.DataFrame(schema={
        "ts_code": pl.String, "trade_date": pl.Date, "board": pl.String,
        "status": pl.String, "limit_pct": pl.Float64,
        "limit_up_price": pl.Float64, "limit_down_price": pl.Float64,
        "is_limit_up": pl.Boolean, "is_limit_down": pl.Boolean, "is_zaban": pl.Boolean,
        "consec_limit_up_days": pl.Int64,
    }), parquet_dir=env.parquet_dir)


def _write_suspend(env, day: date, rows: List[dict]) -> None:
    from neckline.data.market_data import write_table_day

    df = (
        pl.DataFrame([{**r, "trade_date": day} for r in rows])
        if rows
        else pl.DataFrame(schema={"ts_code": pl.String, "trade_date": pl.Date,
                                  "suspend_type": pl.String, "suspend_timing": pl.String})
    )
    write_table_day("suspend_d", day, df, parquet_dir=env.parquet_dir)


# ══════════════════════════════════════════════════════════════════════════
# 参数包夹具
# ══════════════════════════════════════════════════════════════════════════

def raw_params(**overrides) -> dict:
    """一份**结构完整**的参数包原文(每个值都显式给出 —— `K9Params` 没有默认值)。

    ⚠ 数字是夹具,⛔ 不是标定值。
    """
    def tiers(strict: dict, relaxed: dict) -> dict:
        return {"strict": strict, "relaxed": relaxed}

    raw = {
        "packageVersion": "k9-params-fixture",
        "factPackVersion": fact_pack.PACK_VERSION,
        "calibratedBy": "unit-test",
        "calibratedAt": "2026-08-20T00:00:00Z",
        "approvedBy": "unit-test",
        "approvedAt": "2026-08-20T00:00:00Z",
        "boundary": {
            "newListingDays": 30,
            "liquidityWindowDays": 20,
            "liquidityBottomPct": 0.2,
            "spikeFadeRetPct": 5.0,
            "spikeFadeGapPct": 3.0,
        },
        "industry": {
            "minMembers": 3,
            "excludedL2Codes": ["801125.SI"],
            "heatAbsentPolicy": "renormalize",
        },
        # 裁定 13/14/15:放量倍数的分母窗口与分界值 V(V **不分档**)
        "volume": {"maDays": 20, "eruptionMultiple": 2.0},
        "channels": {
            "p1": tiers({"ampWindowDays": 20, "ampMaxPct": 25.0, "minRetPct": 0.0},
                        {"ampWindowDays": 20, "ampMaxPct": 40.0, "minRetPct": 0.0}),
            "p2": tiers({"normDropMin": 0.7, "maDays": 20, "minVolMultiple": 1.2},
                        {"normDropMin": 0.5, "maDays": 20, "minVolMultiple": 1.0}),
            "p3": tiers({"longWindow": 60, "shortWindow": 5, "flatBand": 0.05},
                        {"longWindow": 60, "shortWindow": 5, "flatBand": 0.08}),
            "p4": tiers({"dailyInflowRankPct": 0.2, "cumDays": 5,
                         "cumInflowRankPct": 0.2, "lagRankGap": 0.3},
                        {"dailyInflowRankPct": 0.4, "cumDays": 5,
                         "cumInflowRankPct": 0.4, "lagRankGap": 0.2}),
        },
        "ranking": {
            "weights": {"industryHeat": 0.4, "patternStrength": 0.4, "relay": 0.2},
            "patternSubWeights": {
                "p1": {"volMultiple": 0.4, "upsideRoomFar": 0.3, "relStrength": 0.3},
                "p2": {"relStrengthShortfall": 1.0},
                "p3": {"shortWindowImprovement": 0.5, "upsideRoomNear": 0.5},
                "p4": {"inflowRank": 0.6, "volumeRatioRank": 0.4},
            },
            "relayLookbackDays": 10,
            "relaySource": "recalled",
            "relayScoring": "binary",
            "upsideRoomMechDays": 20,
        },
        "quota": {"min": 10, "max": 20, "floorPerChannel": 1,
                  "overStrictConsecutiveDays": 3},
    }
    for path, value in overrides.items():
        node = raw
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return raw


def params(env, tmp_path: Path, **overrides) -> P.K9Params:
    """把夹具原文写成文件并走**完整校验路径**加载(⛔ 不绕过 `load`)。"""
    target = tmp_path / "k9-params.fixture.json"
    target.write_text(json.dumps(raw_params(**overrides), ensure_ascii=False),
                      encoding="utf-8")
    return P.load(target, db_path=env.db_path)


__all__ = [
    "SESSIONS", "UNIVERSE", "INDUSTRIES",
    "P1_CODE", "P2_CODE", "P3_CODE", "P4_CODE",
    "STAR_CODE", "BAIJIU_CODE", "ST_CODE", "NEW_CODE", "LIMIT_UP_CODE",
    "ONE_LINE_CODE", "ILLIQUID_CODE", "SPIKE_CODE",
    "INTRADAY_HALT_CODE", "FULL_HALT_CODE",
    "seed", "raw_params", "params",
]
