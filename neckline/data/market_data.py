"""数据访问层(plan 0.6)。polars `scan_parquet` 惰性接口 + SQLite 元数据读取。

存储布局(plan §3.3,按年分区):
    data/parquet/<table>/year=YYYY/<trade_date>.parquet   —— 一日一文件
    table ∈ {daily, daily_basic, adj_factor, moneyflow_dc, index_daily, limit_derived}

【铁律】前视截断:任何查询不得返回 > 请求日的数据(§3.8,回测第 T 日任何计算都
不得读到 > T 的数据)。本层两个查询函数的截断保证:
    · `get_market_slice(trade_date)`  —— 只精确匹配 trade_date 这一天,结构上不可能
      多拿。
    · `get_stock_history(code, start, end, as_of=...)` —— 返回行严格满足
      `trade_date <= end`;若调用方传了 `as_of` 且 `end > as_of`,视为疑似前视 bug,
      截到 `as_of` 并打 warning。
单测见 `tests/test_market_data.py`(锁死"查询不得返回越界数据")。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

import polars as pl

from neckline.config import settings
from neckline.data.tushare_client import to_ts_code

logger = logging.getLogger(__name__)

DateLike = Union[date, datetime, str]

_VALID_TABLES = {
    "daily",
    "daily_basic",
    "adj_factor",
    "moneyflow_dc",
    "index_daily",
    "limit_derived",
    "top_list",
    # v1.4-①-B(§七 P0-2):当日停牌名单(TuShare `suspend_d`,600 元档实测可用)。用于把
    # 「持仓票当日无 EOD 行」区分成 `suspended`(真停牌)vs `data_gap`(数据源缺口)——
    # 「没有」与「没看」必须能分开(§3.8),不许一律标 unknown 糊过去。
    "suspend_d",
    # V2-①(plan §五 V2-①,§3.10-A):盘中存拍两张事实表——「只增不改的高频事实」,
    # 走 `write_table_day` 铁律按日分区,不进 SQLite(SQLite 装不下 关注池 200 只 ×
    # 240 分钟/日的量级)。落盘时机 = D4 拍板「内存累计 + 15:05 收盘后一次性落盘」,
    # 写入逻辑留给 V2-⑧;本块只声明表名与数值列 canonical dtype。
    "intraday_ticks",
    "auction_snapshots",
}


def _to_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def table_dir(table: str, parquet_dir: Optional[Path] = None) -> Path:
    if table not in _VALID_TABLES:
        raise ValueError(f"未知表名 {table!r},合法值:{sorted(_VALID_TABLES)}")
    return (parquet_dir or settings.parquet_dir) / table


def day_file_path(table: str, trade_date: DateLike, parquet_dir: Optional[Path] = None) -> Path:
    dt = _to_date(trade_date)
    return table_dir(table, parquet_dir) / f"year={dt.year}" / f"{dt.strftime('%Y%m%d')}.parquet"


# —— 各表数值列 canonical dtype 声明(单一事实源)——————————————————————————
# 本项目所有 TuShare 数值列的 canonical dtype 统一是 Float64,故这里只声明「哪些列是
# 数值列」,dtype 由 `CANONICAL_FLOAT` 单点给出。
#
# **为什么必须显式声明,而不能"向既有分区看齐"(2026-07-27 生产真踩,v1.3.5 修)**:
# `_align_to_table_schema` 原实现拿 `_scan_table(...).collect_schema()` 当 target,而那
# 等于「**排序后第一个分区文件**的 schema」——**没人保证第一个文件是对的**。
# `moneyflow_dc` 的首个分区是 2020-01-02 的**空文件**(该接口 2023-09-11 才有数据,
# backfill 给早期日期落了 897 个 0 行文件,空列经 pandas object → polars String),于是:
#   · 2023-09-11..2026-07-20 的 688 个真数据分区是 Float64(正确);
#   · 2026-07-21 起每天新落的真数据被"对齐"到那个脏 String 基准,一路写成 String;
#   · 读侧 `scan_parquet` 整表 union 时 Float64 撞 String → SchemaError。
# 后果:v1.3 新增的候选情报管线首次全表读 `moneyflow_dc`,**2026-07-27 的 16:35 报告
# 当场崩掉、当日无报告**(见 PROJECT_PLAN §九 / CLAUDE.md 同名坑条目)。也就是说
# 2026-07-21 那次"向既有分区看齐"的修法,对**基准本身是脏的**这一情形不但无效,还会
# 把干净的新数据一起拖下水。
#
# 只列**会经 TuShare 落盘**的数值列。`limit_derived` 的布尔列 / `consec_limit_up_days`
# (UInt32)由本项目自己算(`data/limit_derived.py`),不经 TuShare、无漂移风险,不声明。
CANONICAL_FLOAT = pl.Float64

TABLE_FLOAT_COLS: Dict[str, Tuple[str, ...]] = {
    "daily": ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"),
    "daily_basic": (
        "close", "turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb",
        "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share",
        "total_mv", "circ_mv",
    ),
    "adj_factor": ("adj_factor",),
    "moneyflow_dc": (
        "pct_change", "close", "net_amount", "net_amount_rate",
        "buy_elg_amount", "buy_elg_amount_rate", "buy_lg_amount", "buy_lg_amount_rate",
        "buy_md_amount", "buy_md_amount_rate", "buy_sm_amount", "buy_sm_amount_rate",
    ),
    "index_daily": ("close", "open", "high", "low", "pre_close", "change", "pct_chg", "vol", "amount"),
    "limit_derived": ("limit_pct", "limit_up_price", "limit_down_price"),
    "top_list": (
        "close", "pct_change", "turnover_rate", "amount", "l_sell", "l_buy", "l_amount",
        "net_amount", "net_rate", "amount_rate", "float_values",
    ),
    # v1.4-①-B:`suspend_d` 返回 ts_code / trade_date / suspend_timing / suspend_type,
    # **一个数值列都没有** → 空元组。空元组 ≠ 未声明:未声明会退回「向既有分区看齐」的
    # 旧行为并打 WARNING(脏基准风险),显式声明空元组才是「这张表确实没有数值列」。
    "suspend_d": (),
    # V2-①(plan §五 V2-①,§3.10-A):盘中存拍两张事实表的数值列声明(表结构见
    # PROJECT_PLAN §五 V2-① 「parquet 两表」)。volume/amount 口径同 `daily` 既有惯例
    # (手 / 元);cum_volume/cum_amount 是当日累计量,同样声明 Float64(与本项目其它
    # "计数类"列——如 `daily.vol`——统一走 CANONICAL_FLOAT 的既有口径一致,不特殊化)。
    "intraday_ticks": ("price", "volume", "amount", "cum_volume", "cum_amount"),
    "auction_snapshots": ("auction_price", "auction_volume", "auction_amount", "pre_close", "gap_pct"),
}


def _align_to_table_schema(table: str, df: pl.DataFrame, parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """写入前把 df 各列类型对齐到 **canonical 声明**(TuShare 类型漂移防线)。

    背景(2026-07-21 生产真踩):TuShare 某日返回的 daily_basic 里 turnover_rate_f
    全空,pandas 落成 object → polars String,写盘后与历史分区 Float64 冲突,
    scan_parquet 整表读取直接 SchemaError,16:35 报告任务崩掉。
    对策:落盘统一入口做 cast(strict=False,非法值转 null)。

    **v1.3.5 改口径**:对齐目标从「既有分区的第一个文件」改为 `TABLE_FLOAT_COLS` 的
    **显式声明**——首个分区可能本身就是脏的(见该常量注释里的 2026-07-27 生产事故)。
    声明覆盖不到的列(如 TuShare 新增列)仍沿用旧的「向既有分区看齐」兜底,但**绝不**
    参与已声明列的判定。表未在 `TABLE_FLOAT_COLS` 里声明 → 打 WARNING 后整体退回旧
    行为(**不静默**,提醒补声明);表尚无分区且无声明 → 原样通过。
    """
    casts: List[pl.Expr] = []

    declared = TABLE_FLOAT_COLS.get(table)
    if declared is None:
        logger.warning(
            "write_table_day(%s): 该表未在 TABLE_FLOAT_COLS 声明数值列 canonical dtype,本次退回"
            "「向既有分区看齐」的旧行为——旧行为在首个分区本身是脏的时候会把干净数据一起带偏"
            "(2026-07-27 生产真踩)。请在 market_data.TABLE_FLOAT_COLS 补上该表声明。",
            table,
        )
        declared = ()
    else:
        casts.extend(
            pl.col(c).cast(CANONICAL_FLOAT, strict=False)
            for c in declared
            if c in df.columns and df.schema[c] != CANONICAL_FLOAT
        )

    # 声明未覆盖的列(TuShare 新增列 / 非数值列)仍向既有分区看齐:聊胜于无的兜底,
    # 已声明列一律不走这条路径(canonical 是唯一权威,不被脏基准反悔)。
    lf = _scan_table(table, parquet_dir)
    if lf is not None:
        declared_set = set(declared)
        try:
            target = lf.collect_schema()
        except Exception:  # noqa: BLE001 —— 既有分区已被毒化到连 schema 都读不出:声明仍然有效,不因兜底失败而崩
            logger.warning(
                "write_table_day(%s): 既有分区 schema 读取失败,未声明列本次不做对齐"
                "(已声明列仍按 canonical 落盘)", table, exc_info=True,
            )
        else:
            casts.extend(
                pl.col(name).cast(dtype, strict=False)
                for name, dtype in target.items()
                if name not in declared_set and name in df.columns and df.schema[name] != dtype
            )

    if casts:
        logger.warning(
            "write_table_day(%s): 检测到 %d 列类型与 canonical 声明/既有分区不一致,已 cast(TuShare 类型漂移)",
            table, len(casts),
        )
        df = df.with_columns(casts)
    return df


def write_table_day(table: str, trade_date: DateLike, df: pl.DataFrame, parquet_dir: Optional[Path] = None) -> Path:
    """写一天一表的 Parquet 文件(backfill / daily_update 落盘统一入口,幂等覆盖)。
    写入前经 `_align_to_table_schema` 对齐既有分区类型,防 TuShare 类型漂移毒化分区。"""
    path = day_file_path(table, trade_date, parquet_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    df = _align_to_table_schema(table, df, parquet_dir)
    df.write_parquet(path)
    return path


def day_file_exists(table: str, trade_date: DateLike, parquet_dir: Optional[Path] = None) -> bool:
    return day_file_path(table, trade_date, parquet_dir).exists()


def _years_in_range(start: date, end: date) -> List[int]:
    """`[start, end]` 覆盖到的年份(升序)。分区布局 `year=YYYY/YYYYMMDD.parquet` 由
    `day_file_path` **按 `trade_date.year` 生成**,故「某年目录里只可能有该年日期的行」
    是结构性保证 —— 这就是按年裁剪合法的全部依据。"""
    return list(range(start.year, end.year + 1))


def _scan_table(
    table: str, parquet_dir: Optional[Path] = None, years: Optional[Sequence[int]] = None
) -> Optional[pl.LazyFrame]:
    """惰性 scan 某表分区。表目录不存在 / 无文件 → None(优雅降级,调用方返回空
    DataFrame 而非报错——回测早期区间某表可能尚未 backfill 完整属正常态)。

    **`years` = 只 glob 这几年的分区(v1.4.1 热修 §七 P1-26)**。缺省 `None` = 全部年份
    (老行为)。**这不是语义变化,是纯 I/O 裁剪**:调用方随后照旧按 `trade_date` 过滤,
    而 `year=YYYY` 目录里结构上只可能有该年的行(见 `_years_in_range`),被跳过的年份
    provably 一行都匹配不上。

    **为什么要紧(2026-07-29 生产实测)**:全 glob 要打开 **1592 个 parquet footer**;
    开发机 Mac 上察觉不到,生产 2 vCPU 箱上单次就要好几秒。信息卡端点一次请求里
    `compute_sentiment` 就做 5 次单日横截面 + 单票面板 2 次 + 大盘线 1 次 = **8 次全 glob**
    → 端点实测 18~20s,客户端 12s 超时**必然失败**(用户报障「信息卡总是加载失败」)。
    同一条链的上一集是 §七 P0-23(全历史扫描 784 万行,700M cap 直接 OOM)。**新增任何
    带日期范围的取数路径,都要顺手把年份传下来。**"""
    d = table_dir(table, parquet_dir)
    if not d.exists():
        return None
    import glob

    if years is None:
        pattern = str(d / "year=*" / "*.parquet")
        files = glob.glob(pattern)
    else:
        files = []
        for y in years:
            files.extend(glob.glob(str(d / f"year={int(y)}" / "*.parquet")))
    if not files:
        return None
    # 传文件列表而非 glob 模式:按年裁剪后不再是单一模式(可能跨 2 个年目录)。
    # polars 对 list[str] 与 glob 模式的读取语义一致(union by name)。
    return pl.scan_parquet(sorted(files))


def get_market_slice(trade_date: DateLike, table: str = "daily", parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """全市场某交易日横截面。只精确匹配 trade_date,结构上不可能拿到 >请求日 的数据。

    v1.4.1(§七 P1-26):**只 glob 该日所在那一年**的分区(~244 个文件而非 1592 个);
    结果逐位不变(其余年份的分区里结构上没有该日的行)。"""
    dt = _to_date(trade_date)
    lf = _scan_table(table, parquet_dir, years=[dt.year])
    if lf is None:
        return pl.DataFrame()
    return lf.filter(pl.col("trade_date") == dt).collect()


def get_stock_history(
    code: str,
    start: DateLike,
    end: DateLike,
    table: str = "daily",
    as_of: Optional[DateLike] = None,
    parquet_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """单票历史区间 [start, end](闭区间)。`as_of` 是当前模拟日上限保护:
    `end > as_of` 会被截断到 `as_of` 并打 warning(疑似调用方前视 bug)。"""
    sd, ed = _to_date(start), _to_date(end)
    if as_of is not None:
        as_of_d = _to_date(as_of)
        if ed > as_of_d:
            logger.warning(
                "get_stock_history(%s): end(%s) > as_of(%s),已截断到 as_of(疑似前视 bug,请检查调用方)",
                code, ed, as_of_d,
            )
            ed = as_of_d
    if sd > ed:
        return pl.DataFrame()
    # v1.4.1(§七 P1-26):只 glob 区间覆盖到的年份(信息卡单票面板是 420 自然日 =
    # 最多 2 个年目录,而非全部 7 年 1592 个 footer)。结果逐位不变,见 `_scan_table`。
    lf = _scan_table(table, parquet_dir, years=_years_in_range(sd, ed))
    if lf is None:
        return pl.DataFrame()
    ts_code = to_ts_code(code)
    return (
        lf.filter((pl.col("ts_code") == ts_code) & (pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed))
        .sort("trade_date")
        .collect()
    )


def scan_table_range(table: str, start: DateLike, end: DateLike, parquet_dir: Optional[Path] = None) -> pl.DataFrame:
    """整表按 [start, end] 读(不按 ts_code 过滤,全市场该区间所有行)。供批量运算
    (如 backfill 侧的 limit_derived 连板计算)用,区别于按单票取历史的
    `get_stock_history`。同样满足"不返回 > end 的数据"。"""
    sd, ed = _to_date(start), _to_date(end)
    if sd > ed:
        return pl.DataFrame()
    # v1.4.1(§七 P1-26):同 `get_stock_history`,只 glob 区间覆盖到的年份。
    lf = _scan_table(table, parquet_dir, years=_years_in_range(sd, ed))
    if lf is None:
        return pl.DataFrame()
    return lf.filter((pl.col("trade_date") >= sd) & (pl.col("trade_date") <= ed)).collect()


def get_index_history(
    index_code: str, start: DateLike, end: DateLike, as_of: Optional[DateLike] = None, parquet_dir: Optional[Path] = None
) -> pl.DataFrame:
    """指数区间日线(与 get_stock_history 同款前视保护,index_daily 表 ts_code 即指数代码)。"""
    return get_stock_history(index_code, start, end, table="index_daily", as_of=as_of, parquet_dir=parquet_dir)


# —— SQLite 元数据读取(stock_basic / namechange / trade_cal;小表,可整表读)——————

def load_stock_basic(db_path: Optional[Path] = None) -> pl.DataFrame:
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT ts_code, symbol, name, industry, market, list_date, delist_date, list_status FROM stock_basic"
        ).fetchall()
    finally:
        conn.close()
    df = pl.DataFrame(
        rows,
        schema={
            "ts_code": pl.Utf8,
            "symbol": pl.Utf8,
            "name": pl.Utf8,
            "industry": pl.Utf8,
            "market": pl.Utf8,
            "list_date": pl.Utf8,
            "delist_date": pl.Utf8,
            "list_status": pl.Utf8,
        },
        orient="row",
    )
    return df.with_columns(
        pl.col("list_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("delist_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
    )


def resolve_stock_names(codes: Sequence[str], db_path: Optional[Path] = None) -> Dict[str, str]:
    """`ts_code -> name`(`stock_basic` 当前名称)。**「按代码查中文名」的唯一实现**——
    `api.app._resolve_names`(看板/持仓展示)与 `api.inquiry`(喂 LLM 的材料 + 联网
    搜索查询词)都走这里,不各自写一份 `load_stock_basic` + filter。

    查不到 / 任何异常 → 该 code 不出现在返回 dict 里(调用方自行兜底回 code),
    **绝不抛**:补名字是展示与检索的增强,不该让主链路崩。"""
    wanted = [c for c in dict.fromkeys(codes) if c]
    if not wanted:
        return {}
    try:
        sb = load_stock_basic(db_path)
        if sb.is_empty():
            return {}
        sb = sb.filter(pl.col("ts_code").is_in(wanted)).select(["ts_code", "name"])
        return {c: n for c, n in zip(sb["ts_code"].to_list(), sb["name"].to_list()) if n}
    except Exception:  # noqa: BLE001
        logger.warning("按 ts_code 补股票名失败(降级为空)", exc_info=True)
        return {}


def load_namechange(db_path: Optional[Path] = None) -> pl.DataFrame:
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT ts_code, name, start_date, end_date, ann_date, change_reason FROM namechange"
        ).fetchall()
    finally:
        conn.close()
    df = pl.DataFrame(
        rows,
        schema={
            "ts_code": pl.Utf8,
            "name": pl.Utf8,
            "start_date": pl.Utf8,
            "end_date": pl.Utf8,
            "ann_date": pl.Utf8,
            "change_reason": pl.Utf8,
        },
        orient="row",
    )
    return df.with_columns(
        pl.col("start_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
        pl.col("end_date").str.strptime(pl.Date, "%Y%m%d", strict=False),
    )


def load_trade_cal_days(exchange: str = "SSE", db_path: Optional[Path] = None) -> List[date]:
    """全部交易日(is_open=1),升序。供 limit_derived / calendar 缓存使用。"""
    conn = sqlite3.connect(str(db_path or settings.db_path))
    try:
        rows = conn.execute(
            "SELECT cal_date FROM trade_cal WHERE exchange=? AND is_open=1 ORDER BY cal_date", (exchange,)
        ).fetchall()
    finally:
        conn.close()
    return [datetime.strptime(r[0], "%Y%m%d").date() for r in rows]


__all__ = [
    "table_dir",
    "day_file_path",
    "write_table_day",
    "day_file_exists",
    "get_market_slice",
    "get_stock_history",
    "scan_table_range",
    "get_index_history",
    "load_stock_basic",
    "resolve_stock_names",
    "load_namechange",
    "load_trade_cal_days",
]
