"""当日事实包的装配与 DTO(架构第一层 · 事实层,PROJECT_PLAN §5.3)。

**职责**:只回答「今天市场发生了什么」。**只装一天的事实,⛔ 不装任何窗口量** ——
窗口长度全是策略参数,装进事实层就把可调项埋进了不该调的地方(架构 §二 判据)。

🔴 **「数据未到齐 → 不冻结」是类型错误,不是布尔标志**(§5.3.2 第 1 条):
`build()` 返回 `CompletePack | IncompletePack`;`IncompletePack` **没有 rows、没有
freeze**,而 `facts/store.py::freeze_pack()` 的签名只接受 `CompletePack`。于是
「谁忘了检查」这件事在结构上不可能发生。

**三个产物三个载体**(§5.3.1,⛔ 不许混):
    · 大表(一行一只票,40 列 + `trade_date`)→ parquet 日分区 `fact_pack/`
    · 行业事实(申万二级中位数)→ SQLite `sw_industry_daily`
    · 市场级读数(指数 / 涨停分布 / 涨停簇摘要 / 连板高度 / 炸板率 / 全市场中位涨幅)
      → `fact_packs.market_json`(体量小,直接 JSON)

**装配的两条纪律**:
1. 🛑 **只读当日一个分区**(§12 坑 1):一律 `pl.read_parquet(day_file_path(...))`,
   ⛔ 不用 `get_market_slice` / `scan_table_range` —— 它们走 `year=*/**.parquet` 全
   glob,会打开 1500+ 个 parquet footer;全历史扫描在生产 2vCPU/1.6G 上是 OOM-kill。
2. **行数 = 当日 `daily` 的行数**。`daily` 里没有的票不进包 —— 「今天没交易」这件事
   由**缺席**表达,而不是造一行全空的记录。K9 第一层第 6 条「停牌 = `suspend_flag=='S'`
   **或当日无 daily 行**」的后半句因此是结构性满足的(⚠ 全市场 disposition 要覆盖
   「一只票都没交易过」的情形时,由 S6 自己去 union `stock_basic`,不是本层的事)。

**⛔ 事实层不知道下游有哪些策略**(架构 §二 边界①):列名不得含 `pattern` /
`channel` / `recall` / `k9` / `rank` / `score` 词根(`FORBIDDEN_COLUMN_ROOTS`,
守门单测 G1 逐列扫描),且 `facts/**` 零 import `k9` / `explain` / `playbook` /
`scorecard`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import polars as pl

from neckline.data.board import Board, classify
from neckline.data.limit_derived import is_st_name
from neckline.data.market_data import day_file_path, load_namechange, load_stock_basic
from neckline.data.sw_industry import load_l2_map
from neckline.db import connection, init_schema
from neckline.facts import completeness as completeness_mod
from neckline.facts import industry as industry_mod
from neckline.facts import limitmap as limitmap_mod

logger = logging.getLogger(__name__)

#: 事实层口径版本。🔴 口径变了就**发新版本**(§5.3.2 第 3 条),⛔ 没有静默重写这条路。
#: 参数包的 `factPackVersion` 必须逐字等于它,否则该参数包无效(§5.4.3 校验 3)。
#:
#: · `fp-1`(S3 首版):S 类停牌**一律**排除出行业中位数并计为异常。
#: · `fp-2`(**裁定 12 返工**):只剔**全天停牌**;盘中临时停牌**照常计入**中位数,
#:   `suspend_flag` 因此新增 `I` 取值。这改的是中位数本身的口径 → 必须升版,
#:   ⛔ 不许在 `fp-1` 上静默重算(那会让「同一版包」在两天里意思不同)。
PACK_VERSION = "fp-2"

#: 策略层历史读取上限(§3.2:**工程容量上限,不是策略参数**)。
#: 120 × 5500 × 10 列 ≈ 53 MB,2vCPU/1.6G 扛得住;参数包里任何窗口 > 它一律判配置无效。
MAX_LOOKBACK_PACKS = 120

#: 事实包大表的列(**定死**,§5.3.1)。顺序即落盘顺序。
#: `trade_date` 是第 41 列 —— §5.3.1 的 40 列表里没写它(它是分区键),但本仓所有
#: parquet 日分区表都带这一列,`load_pack_range` 也要靠它区分天,故落盘时补上。
PACK_COLUMNS: Tuple[str, ...] = (
    "trade_date",
    # —— 身份 ——
    "ts_code", "name", "board", "list_date", "is_st", "suspend_flag",
    # —— 申万(裁定 3:二级 L2 是判据口径,L1/L3 一并冻结供追溯)——
    "sw_l1_code", "sw_l1_name", "sw_l2_code", "sw_l2_name", "sw_l3_code",
    # —— 价量(原始未复权)——
    "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount", "adj_factor",
    # —— 当日衍生 ——
    "ret_1d", "amp_1d", "limit_up_price", "limit_down_price",
    "is_limit_up", "is_limit_down", "is_limit_open", "consec_limit_up_days",
    # —— daily_basic ——
    "turnover_rate", "turnover_rate_f", "volume_ratio", "circ_mv", "total_mv", "free_share",
    # —— 资金流 ——
    "net_amount", "net_amount_rate", "buy_elg_amount", "buy_lg_amount",
    # —— 行业相对(裁定 2)——
    "sw_l2_median_ret", "rel_strength_1d",
)

#: 边界① 的字段名黑名单(守门单测 G1)。事实层列名出现这些词根 = 它开始知道下游
#: 有哪些策略了。⛔ 不许加豁免,要加列先想清楚它是不是事实。
FORBIDDEN_COLUMN_ROOTS: Tuple[str, ...] = ("pattern", "channel", "recall", "k9", "rank", "score")

#: `suspend_flag` 的**闭合取值**(🔴 **裁定 12** 之后是四值,不是三值)。
#:
#: | 值 | 含义 | 当天交易了吗 | 进中位数吗 | K9 第一层第 6 条排除吗 |
#: |---|---|---|---|---|
#: | `none` | 不在当日停牌名单里 | 是 | 是 | 否 |
#: | `S` | **全天停牌**(`suspend_type='S'` 且 `suspend_timing` 为空) | 否 | **否** | **是** |
#: | `I` | **盘中临时停牌**(`suspend_type='S'` 且 `suspend_timing` 非空,如 `'9:30-9:40'`) | 是 | **是** | 否 |
#: | `R` | **复牌**(`suspend_type='R'`) | 是 | 是 | 否 |
#:
#: 🔴 **为什么把 `I` 单列成一个值,而不是留个布尔列或让下游自己看 timing**:
#: 「停牌」这个词在本系统里有**两个**消费方(行业中位数、K9 第一层第 6 条),
#: 裁定 12 要求两处口径一致。把判别做成**一个列的一个取值**,下游写
#: `suspend_flag == 'S'` 就自动是对的;留成「S + 另一列 timing」则每个消费方都要
#: 记得再 and 一次,而忘记的那次不会报错、只会把一只正常交易的票悄悄从中位数里
#: 抹掉。⛔ 不许把 `I` 折回 `S`。
SUSPEND_NONE = "none"
SUSPEND_HALTED = "S"          # 全天停牌 —— 唯一被剔除的一类(裁定 12)
SUSPEND_INTRADAY = "I"        # 盘中临时停牌 —— 当天正常交易,照常计入(裁定 12)
SUSPEND_RESUMED = "R"         # 复牌 —— 当天正常交易(§4.6)


@dataclass(frozen=True)
class CompletePack:
    """数据到齐、可以冻结的当日事实包。**只有它能进 `freeze_pack()`。**"""

    trade_date: date
    pack_version: str
    rows: pl.DataFrame                                   # 大表,列 = PACK_COLUMNS
    industry_rows: Tuple[industry_mod.IndustryDay, ...]  # 申万二级当日中位数
    market: Dict[str, object]                            # → fact_packs.market_json
    sources: Tuple[completeness_mod.SourceRecord, ...]
    suspend_anomaly_count: int

    @property
    def row_count(self) -> int:
        return int(self.rows.height)


@dataclass(frozen=True)
class IncompletePack:
    """数据未到齐 —— 报告「今天没跑成」并**逐条列出缺口**(架构 §3.5)。

    🔴 **本类刻意没有 `rows`,也刻意没有任何 freeze 方法。**「不冻结」因此是一个
    类型事实,不是某个人记得检查的布尔标志(§5.3.2 第 1 条)。⛔ 不许给它加。
    """

    trade_date: date
    pack_version: str
    missing: Tuple[str, ...]

    def describe(self) -> str:
        return "、".join(self.missing) if self.missing else "未知缺口"


Pack = Union[CompletePack, IncompletePack]


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════

def _read_day(table: str, trade_date: date, parquet_dir: Optional[Path]) -> pl.DataFrame:
    """只读当日那一个文件(§12 坑 1)。不存在 → 空 DataFrame。"""
    path = day_file_path(table, trade_date, parquet_dir)
    if not path.exists():
        return pl.DataFrame()
    return pl.read_parquet(path)


def _suspend_records(
    trade_date: date, parquet_dir: Optional[Path]
) -> Dict[str, Tuple[str, Optional[str]]]:
    """`ts_code → (suspend_type, suspend_timing)` 的**原始**读数(不在名单里的票由
    调用方填 `'none'`)。原始 `suspend_type` 只有 `S`/`R` 两值 —— 四值的
    `suspend_flag`(含 `I`)由 `_suspend_flag_of` 从这两列**推**出来。

    ⚠ **当前数据路径里只会出现 S**:`tushare_client.ts_suspend_d_all` 调接口时就传了
    `suspend_type='S'`,R 类记录根本不落地。本函数**仍然**按 `suspend_type` 逐行映射
    而不是「凡在名单里就算停牌」—— 哪天有人放宽了那个抓取条件,认 R 会当场误杀正常
    交易日(§4.6:20230103 的 000045.SZ 是 R,当天涨停 +10.01%)。⛔ 别把这段
    「多余」的映射优化掉。

    🔴 **`suspend_timing` 是判别位,⛔ 别当成没用的附加列**:
    `suspend_timing IS NULL` = **全天停牌**;非空(如 `'9:30-9:40'`)= **盘中临时停牌**,
    那只票**当天照常交易、照常有 daily 行**。150 个交易日实测:
    全天停牌 2001 行 —— **0 行**出现在 daily(§4.6 那句「天然不在 daily 里」成立);
    盘中停牌 36 行 —— **35 行**出现在 daily,分布在 25/150 天(17% 的日子)。
    **用户 2026-08-20 据此裁定 12:只剔全天停牌,盘中临时停牌照常计入中位数。**
    """
    df = _read_day("suspend_d", trade_date, parquet_dir)
    if df.is_empty() or "ts_code" not in df.columns:
        return {}
    if "suspend_type" not in df.columns:
        logger.warning("[fact_pack] suspend_d 当日分区无 suspend_type 列,停牌断言本日退化为空")
        return {}
    timings = (
        df["suspend_timing"].to_list()
        if "suspend_timing" in df.columns
        else [None] * df.height
    )
    out: Dict[str, Tuple[str, Optional[str]]] = {}
    for code, kind, timing in zip(df["ts_code"].to_list(), df["suspend_type"].to_list(), timings):
        k = (str(kind).strip().upper() if kind is not None else "")
        if k in (SUSPEND_HALTED, SUSPEND_RESUMED):
            t = str(timing).strip() if timing is not None and str(timing).strip() else None
            out[str(code)] = (k, t)
    return out


def _suspend_flag_of(kind: str, timing: Optional[str]) -> str:
    """原始 `(suspend_type, suspend_timing)` → 四值 `suspend_flag`(**裁定 12**)。

    🔴 这是「哪些票算停牌」的**唯一**判别实现。行业中位数与 K9 第一层第 6 条
    都只认它的产物,⛔ 不许任何下游再看一次 `suspend_timing` 自己判一遍。
    """
    if kind == SUSPEND_RESUMED:
        return SUSPEND_RESUMED
    if kind == SUSPEND_HALTED:
        # ⛔ 不许简化成「凡 S 即停牌」:盘中停过十分钟的票当天是正常交易的。
        return SUSPEND_INTRADAY if timing else SUSPEND_HALTED
    return SUSPEND_NONE


def _meta_frame(db_path: Optional[Path], trade_date: date) -> pl.DataFrame:
    """`ts_code / name / board / list_date / is_st`。

    `is_st` 走 `namechange` 的 as-of 生效名(⛔ 不看 `stock_basic.name`:那是**当前**
    名,拿它判历史日会把今天摘帽的票在历史上也判成非 ST),口径复用
    `limit_derived.is_st_name`(§5.4.4 第 3 条:⛔ 别另写一份)。"""
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return pl.DataFrame(
            schema={"ts_code": pl.String, "name": pl.String, "board": pl.String,
                    "list_date": pl.Date, "is_st": pl.Boolean}
        )
    boards = [
        classify(m, c).value
        for m, c in zip(sb["market"].to_list(), sb["ts_code"].to_list())
    ]
    meta = sb.select(["ts_code", "name", "list_date"]).with_columns(
        pl.Series("board", boards, dtype=pl.String)
    )

    nc = load_namechange(db_path)
    if nc.is_empty():
        return meta.with_columns(pl.lit(False).alias("is_st"))
    nc = (
        nc.filter(pl.col("start_date").is_not_null() & (pl.col("start_date") <= trade_date))
        .sort(["ts_code", "start_date"])
        .group_by("ts_code")
        .last()
        .with_columns(is_st_name().alias("is_st"))
        .select(["ts_code", "is_st"])
    )
    return meta.join(nc, on="ts_code", how="left").with_columns(
        pl.col("is_st").fill_null(False)
    )


def _sw_frame(db_path: Optional[Path]) -> pl.DataFrame:
    """`ts_code → l1/l2/l3`(当前有效归属快照)。

    ⚠ §5.3.5 的已知语义差:回填包用的是**今天的**申万归属快照,不是那天的。
    写在明处,⛔ 别写「自动检测行业变更并回改历史」的机灵代码;要重置就整段重跑。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT ts_code, l1_code, l1_name, l2_code, l2_name, l3_code "
            "FROM sw_industry_member WHERE is_current=1"
        ).fetchall()
    schema = {
        "ts_code": pl.String, "sw_l1_code": pl.String, "sw_l1_name": pl.String,
        "sw_l2_code": pl.String, "sw_l2_name": pl.String, "sw_l3_code": pl.String,
    }
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows, schema=schema, orient="row")


_DAILY_BASIC_COLS = (
    "turnover_rate", "turnover_rate_f", "volume_ratio", "circ_mv", "total_mv", "free_share",
)
_MONEYFLOW_COLS = ("net_amount", "net_amount_rate", "buy_elg_amount", "buy_lg_amount")
_LIMIT_COLS = ("limit_up_price", "limit_down_price", "is_limit_up", "is_limit_down", "is_zaban",
               "consec_limit_up_days")


def _select_optional(df: pl.DataFrame, cols: Tuple[str, ...]) -> pl.DataFrame:
    """按需取列;上游少给了哪一列就补 null(⛔ 不抛 —— 缺整张表才是缺口,那已在
    `completeness` 判过了;缺某一列由 null 如实表达)。

    ⚠ 判据是**有没有 `ts_code` 列**,不是 `is_empty()`:稀疏表当日 0 行是**合法的
    市场事实**(一只涨停都没有的日子),而 0 行的分区可能只带一列 `trade_date`。
    拿它去 join 会抛「找不到 ts_code」—— 那正是把一个平静的日子读成故障。"""
    if "ts_code" not in df.columns:
        return pl.DataFrame(schema={"ts_code": pl.String, **{c: pl.Float64 for c in cols}})
    keep = ["ts_code"] + [c for c in cols if c in df.columns]
    out = df.select(keep)
    for c in cols:
        if c not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias(c))
    return out.select(["ts_code", *cols])


def build(
    trade_date: date,
    *,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Pack:
    """装配当日事实包。数据未到齐 → `IncompletePack(missing=[逐条])`。"""
    comp = completeness_mod.check(trade_date, parquet_dir=parquet_dir, db_path=db_path)
    if not comp.ok:
        logger.warning("[fact_pack] %s 数据未到齐,不冻结:%s", trade_date, comp.missing())
        return IncompletePack(
            trade_date=trade_date, pack_version=PACK_VERSION, missing=tuple(comp.missing())
        )

    daily = _read_day("daily", trade_date, parquet_dir)
    daily = industry_mod.attach_ret_1d(daily)
    if daily.is_empty():
        return IncompletePack(
            trade_date=trade_date, pack_version=PACK_VERSION,
            missing=("daily:当日分区无可用行(close/pre_close 全空或 pre_close 为 0)",),
        )

    basic = _select_optional(_read_day("daily_basic", trade_date, parquet_dir), _DAILY_BASIC_COLS)
    adj = _select_optional(_read_day("adj_factor", trade_date, parquet_dir), ("adj_factor",))
    flow = _select_optional(_read_day("moneyflow_dc", trade_date, parquet_dir), _MONEYFLOW_COLS)
    limits = _read_day("limit_derived", trade_date, parquet_dir)
    limits = (
        limits.select([c for c in ("ts_code", *_LIMIT_COLS) if c in limits.columns])
        # ⚠ 「当日一只涨停都没有」是**合法的市场事实**:`limit_derived` 是只存
        # 「有信号」行的稀疏表,那天的分区可以是 0 行。⛔ 不许把它读成故障。
        if "ts_code" in limits.columns
        else pl.DataFrame(schema={"ts_code": pl.String})
    )

    meta = _meta_frame(db_path, trade_date)
    sw = _sw_frame(db_path)
    suspend_records = _suspend_records(trade_date, parquet_dir)
    #: 裁定 12:`S`(全天停牌)/ `I`(盘中临时停牌)/ `R`(复牌)三类在这里就分开,
    #: 下游一律只认这一列。
    suspend_of = {c: _suspend_flag_of(k, t) for c, (k, t) in suspend_records.items()}

    df = (
        daily.join(meta, on="ts_code", how="left")
        .join(sw, on="ts_code", how="left")
        .join(basic, on="ts_code", how="left")
        .join(adj, on="ts_code", how="left")
        .join(flow, on="ts_code", how="left")
        .join(limits, on="ts_code", how="left")
    )

    df = df.with_columns(
        pl.col("ts_code")
        .replace_strict(suspend_of, default=SUSPEND_NONE, return_dtype=pl.String)
        .alias("suspend_flag"),
        ((pl.col("high") - pl.col("low")) / pl.col("pre_close")).alias("amp_1d"),
    )
    for col, dtype, fill in (
        ("is_limit_up", pl.Boolean, False),
        ("is_limit_down", pl.Boolean, False),
        ("is_zaban", pl.Boolean, False),
        ("consec_limit_up_days", pl.Int64, 0),
        ("is_st", pl.Boolean, False),
    ):
        if col not in df.columns:
            df = df.with_columns(pl.lit(fill, dtype=dtype).alias(col))
        else:
            df = df.with_columns(pl.col(col).fill_null(fill).cast(dtype))
    # `is_limit_open` = 炸板(§5.3.1 的列名);上游 `limit_derived` 里叫 `is_zaban`。
    df = df.with_columns(pl.col("is_zaban").alias("is_limit_open"))
    if "board" not in df.columns:
        df = df.with_columns(pl.lit(Board.MAIN.value).alias("board"))
    df = df.with_columns(pl.col("board").fill_null(Board.MAIN.value))

    # —— 申万二级中位数(裁定 2)+ 停牌断言(§5.3.4,裁定 12 收窄)————————————
    # 🔴 裁定 12:**只把全天停牌剔出中位数**。盘中临时停牌(`I`)当天正常交易、
    # 有完整涨跌幅,照常参与 —— ⛔ 不许把它并回 `halted`。
    l2_of = load_l2_map(db_path)
    halted = {c for c, k in suspend_of.items() if k == SUSPEND_HALTED}
    intraday_in_daily = sorted(
        c for c in df["ts_code"].to_list() if suspend_of.get(c) == SUSPEND_INTRADAY
    )
    industry_rows, anomalies = industry_mod.compute_day(df, l2_of, halted)
    anomaly_detail = _anomaly_breakdown(anomalies, intraday_in_daily, suspend_records)
    if anomalies:
        logger.warning(
            "[fact_pack] %s 停牌断言被违反:%d 只**全天停牌**的票竟然出现在 daily 里"
            "(§4.6 实测 150 天 2001 行全天停牌 0 行进过 daily,这是真异常),"
            "已排除出行业中位数并记入 suspend_anomaly_count:%s",
            trade_date, len(anomalies), anomalies[:10],
        )
    if intraday_in_daily:
        logger.info(
            "[fact_pack] %s 有 %d 只**盘中临时停牌**的票出现在 daily —— 这是常态不是异常"
            "(裁定 12:当天正常交易,照常计入行业中位数),⛔ 不计入 suspend_anomaly_count",
            trade_date, len(intraday_in_daily),
        )
    median_of = {r.l2_code: r.median_ret for r in industry_rows}
    df = df.with_columns(
        pl.col("sw_l2_code")
        .replace_strict(median_of, default=None, return_dtype=pl.Float64)
        .alias("sw_l2_median_ret")
    )
    df = df.with_columns((pl.col("ret_1d") - pl.col("sw_l2_median_ret")).alias("rel_strength_1d"))

    df = df.with_columns(pl.lit(trade_date).alias("trade_date"))
    for col in PACK_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))
    rows = df.select(list(PACK_COLUMNS)).sort("ts_code")

    market = _market_readings(rows, trade_date, parquet_dir, industry_rows, anomaly_detail)

    return CompletePack(
        trade_date=trade_date,
        pack_version=PACK_VERSION,
        rows=rows,
        industry_rows=tuple(industry_rows),
        market=market,
        sources=comp.sources,
        suspend_anomaly_count=len(anomalies),
    )


def _anomaly_breakdown(
    anomalies: List[str],
    intraday_in_daily: List[str],
    records: Dict[str, Tuple[str, Optional[str]]],
) -> Dict[str, object]:
    """`fact_packs.market_json.suspendAnomaly` 的内容(纯记账,⛔ 不改任何口径)。

    🔴 **裁定 12 之后这里只对一件事告警**:「`suspend_type='S'` 且 `suspend_timing`
    为空(**全天停牌**)的票竟然出现在 `daily` 里」。150 个交易日实测该情形
    **0 次**(2001 行全天停牌无一进 daily),所以它真发生时就是数据事故。

    **盘中临时停牌不是异常**(实测 36 行里 35 行都在 daily、分布在 25/150 天):
    它们当天正常交易、照常计入中位数,这里只把判别证据(只数与 timing 串)原样
    留在包里,让「那天有几只票盘中停过」事后仍查得到 —— ⛔ 但**不进
    `suspend_anomaly_count`**,把常态记成异常等于让告警从此没人看。
    """
    return {
        # 真异常:全天停牌却出现在 daily。正常恒 0。
        "total": len(anomalies),
        "codes": sorted(anomalies)[:50],
        # 常态记账:盘中临时停牌且当天有 daily 行 —— **照常计入**中位数(裁定 12)。
        "intradayCounted": len(intraday_in_daily),
        "intradayTimings": {
            c: records[c][1] for c in intraday_in_daily[:50] if c in records
        },
    }


def _market_readings(
    rows: pl.DataFrame,
    trade_date: date,
    parquet_dir: Optional[Path],
    industry_rows: List[industry_mod.IndustryDay],
    anomaly_detail: Dict[str, object],
) -> Dict[str, object]:
    """市场级读数 → `fact_packs.market_json`(§5.3.1)。体量小,直接 JSON。"""
    lmap = limitmap_mod.compute(rows)
    idx = _read_day("index_daily", trade_date, parquet_dir)
    indices: List[dict] = []
    if not idx.is_empty() and {"ts_code", "close", "pct_chg"} <= set(idx.columns):
        indices = [
            {"tsCode": r["ts_code"], "close": r["close"], "pctChg": r["pct_chg"]}
            for r in idx.select(["ts_code", "close", "pct_chg"]).sort("ts_code").iter_rows(named=True)
        ]

    ret = rows["ret_1d"].drop_nulls()
    median_ret = float(ret.median()) if ret.len() else None

    missing_sw = int(
        rows.filter(pl.col("sw_l2_code").is_null() | (pl.col("sw_l2_code") == "")).height
    )
    if missing_sw:
        logger.warning(
            "[fact_pack] %s 有 %d 只票查无申万归属 —— §4.4 实测覆盖率应为 100%%,"
            "掉下来通常是成分表没拉全(翻页丢了一半)或分类快照陈旧,不是「这些票没有行业」",
            trade_date, missing_sw,
        )

    return {
        "limitMap": lmap.to_dict(),
        "indices": indices,
        "marketMedianRet": median_ret,
        "swCoverage": {"total": int(rows.height), "missing": missing_sw},
        "industryCount": len(industry_rows),
        "suspendAnomaly": anomaly_detail,
    }


__all__ = [
    "PACK_VERSION",
    "MAX_LOOKBACK_PACKS",
    "PACK_COLUMNS",
    "FORBIDDEN_COLUMN_ROOTS",
    "SUSPEND_NONE",
    "SUSPEND_HALTED",
    "SUSPEND_INTRADAY",
    "SUSPEND_RESUMED",
    "CompletePack",
    "IncompletePack",
    "Pack",
    "build",
]
