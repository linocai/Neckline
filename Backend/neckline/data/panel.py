"""行情研究面板(V2.5.0 S1:自 `strategy/features.py` 原样搬入 `data/`)。

搬家理由(PROJECT_PLAN §3.3):**它是数据面板,不是策略** —— 前复权拼接、日线与
`daily_basic` 合流、涨跌停派生列合流、前瞻收益列,全部是「今天/这些天的行情长什么样」
的事实计算,不承载任何「什么样的票会涨」的主张。K9 的策略主张一律住在 `k9/`。

⚠ 原模块头(下附)写的是 K8 时代「母战法信号源 / 同码三跑道」的定位;那套战法已随
`strategy/` 整包退役,其中的 `signals` / `brain` 语义不再存在。本文件保留的是**面板
构建**部分,内容一字未改。

以下为原模块头 ——
母战法信号源(plan 1.1，§2.6「同码三跑道」的源头）。

本模块是**信号逻辑的唯一定义处**：强势定义、买点、禁买过滤、市场状态标签，
全部实现为**在前复权 daily 面板上的向量化 polars 表达式 / 纯函数**，不依赖回测
引擎的 Order/Portfolio/Broker 机制。三跑道复用方式：

    · 喂历史（回测）  → `build_research_panel(start,end)` 一次算全历史面板，
      事件研究直接在面板上过滤 + 聚合前瞻收益；组合回测的策略读同一面板按日切片。
    · 喂今日（报告）  → 同一 `add_features` 作用在「截至今日的历史 + 今日横截面」，
      取最后一天的行即当日候选评分（阶段 2 复用，本阶段不实现报告）。
    · 喂单票（问询台）→ 同一 `add_features` 作用在单票历史，取最后一行（阶段 4）。

**无前视铁律（§3.8）**：所有特征列只用「当前行及更早」的数据——rolling/shift 一律
后向窗口，`min_samples` 卡满窗口（不足窗口的早期行为 null，自然被信号过滤剔除）。
前瞻收益列（`fwd_*`）只在事件研究里用来度量「买入后」的表现，**信号列绝不引用
它们**——两类列在面板里并存但用途严格分离（事件研究读 fwd_*，策略选股只读特征）。

**前复权锚点**：`add_features` 里的 qfq 锚点是传入面板每只票的最新一行（`apply_qfq`
的口径）。强势/买点信号全是**比值/相对量**（`close/ma20`、`ret_20d`、`dist_from_high`
等），对 qfq 的每票标量缩放不敏感（同一除权段内缩放抵消），锚点选择不影响选股。
组合回测的成交 P&L 由引擎用自己的锚点（区间末尾）另算，与本面板的特征值解耦。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import polars as pl

from neckline.data.adjust import apply_qfq
from neckline.data.board import classify
from neckline.data.market_data import (
    get_index_history,
    load_namechange,
    load_stock_basic,
    scan_table_range,
)

# —— 前复权价格列（daily 原始列前复权后覆盖同名，特征全在复权价上算）——————————
_PRICE_COLS = ("open", "high", "low", "close", "pre_close")

# 上证综指（市场状态过滤器 P1 / 分层报告基准）
SSE_INDEX = "000001.SH"


# ======================================================================
#  特征计算（纯 polars 表达式，作用在已排序 [ts_code, trade_date] 的面板）
# ======================================================================

def add_features(daily_qfq: pl.DataFrame) -> pl.DataFrame:
    """给前复权 daily 面板加母战法特征列。输入需含
    `ts_code/trade_date/open/high/low/close/pre_close/vol/amount`（价格列已前复权）。

    全部后向窗口，无前视。早期不足窗口的行相应特征为 null。
    """
    if daily_qfq.is_empty():
        return daily_qfq
    df = daily_qfq.sort(["ts_code", "trade_date"])

    over = "ts_code"
    df = df.with_columns(
        # —— 均线 ——
        pl.col("close").rolling_mean(5, min_samples=5).over(over).alias("ma5"),
        pl.col("close").rolling_mean(10, min_samples=10).over(over).alias("ma10"),
        pl.col("close").rolling_mean(20, min_samples=20).over(over).alias("ma20"),
        # —— 量能 ——
        pl.col("vol").rolling_mean(5, min_samples=5).over(over).alias("vol_ma5"),
        pl.col("vol").rolling_mean(20, min_samples=20).over(over).alias("vol_ma20"),
        pl.col("amount").rolling_mean(20, min_samples=20).over(over).alias("amount_ma20"),
        # —— 20 日高/低（含当日）——
        pl.col("high").rolling_max(20, min_samples=20).over(over).alias("high_20d"),
        pl.col("low").rolling_min(20, min_samples=20).over(over).alias("low_20d"),
        # —— 平台突破用：不含当日的前 N 日最高收盘（platform_high）——
        pl.col("close").shift(1).rolling_max(20, min_samples=20).over(over).alias("prev_close_max_20d"),
        # —— 区间涨幅 ——
        (pl.col("close") / pl.col("close").shift(5).over(over) - 1).alias("ret_5d"),
        (pl.col("close") / pl.col("close").shift(10).over(over) - 1).alias("ret_10d"),
        (pl.col("close") / pl.col("close").shift(20).over(over) - 1).alias("ret_20d"),
        # —— 当日形态 ——
        (pl.col("close") / pl.col("pre_close") - 1).alias("ret_1d"),
        (pl.col("open") / pl.col("pre_close") - 1).alias("gap_open"),
        (pl.col("close") / pl.col("open") - 1).alias("body"),
        ((pl.col("high") - pl.col("low")) / pl.col("pre_close")).alias("amplitude"),
    )

    df = df.with_columns(
        # 距 20 日高点（≤0，越接近 0 越靠近前高）
        (pl.col("close") / pl.col("high_20d") - 1).alias("dist_from_high_20d"),
        # 当日量比（今日成交量 / 近 5 日均量）——放量指标
        (pl.col("vol") / pl.col("vol_ma5")).alias("vol_ratio_5"),
        # 均线多头排列
        ((pl.col("close") > pl.col("ma20")) & (pl.col("ma5") > pl.col("ma20"))).alias("above_ma20_bullish"),
    )
    return df


def merge_limit_features(panel: pl.DataFrame, limit_derived: pl.DataFrame) -> pl.DataFrame:
    """并入涨停基因特征：当日是否涨停、连板数、过去 20 日涨停次数。

    `limit_derived` 是稀疏表（只有命中行），左连接后未命中行填 False/0。涨停次数
    用后向 rolling_sum，无前视。
    """
    if panel.is_empty():
        return panel
    ld = (
        limit_derived.select(["ts_code", "trade_date", "is_limit_up", "is_limit_down", "consec_limit_up_days"])
        if not limit_derived.is_empty()
        else pl.DataFrame(
            schema={
                "ts_code": pl.Utf8,
                "trade_date": pl.Date,
                "is_limit_up": pl.Boolean,
                "is_limit_down": pl.Boolean,
                "consec_limit_up_days": pl.Int64,
            }
        )
    )
    out = panel.join(ld, on=["ts_code", "trade_date"], how="left").with_columns(
        pl.col("is_limit_up").fill_null(False),
        pl.col("is_limit_down").fill_null(False),
        pl.col("consec_limit_up_days").fill_null(0),
    )
    out = out.sort(["ts_code", "trade_date"]).with_columns(
        pl.col("is_limit_up").cast(pl.Int64).rolling_sum(20, min_samples=1).over("ts_code").alias("limitup_count_20d")
    )
    return out


def merge_daily_basic(panel: pl.DataFrame, daily_basic: pl.DataFrame) -> pl.DataFrame:
    """并入换手率 / 量比 / 流通市值（daily_basic）。缺失（如某日无 basic）→ null。"""
    if panel.is_empty() or daily_basic.is_empty():
        return panel
    db = daily_basic.select(
        ["ts_code", "trade_date", "turnover_rate", "volume_ratio", "circ_mv", "total_mv", "free_share"]
    )
    return panel.join(db, on=["ts_code", "trade_date"], how="left")


def merge_meta(
    panel: pl.DataFrame,
    stock_basic: Optional[pl.DataFrame] = None,
    namechange: Optional[pl.DataFrame] = None,
    db_path: Optional[Path] = None,
) -> pl.DataFrame:
    """并入选股域元数据：board（板块）、is_st（as-of 当日是否 ST/*ST）、
    days_since_listing（自上市的自然日数，次新过滤 P6 用）。

    - board：`stock_basic.market` 优先，缺失退代码前缀正则（复用 `board.classify`）。
    - is_st：`namechange` 按 trade_date 做 backward as-of join（复用 limit_derived 口径）。
    - days_since_listing：`(trade_date − list_date)` 自然日（次新阈值用自然日粗口径即可）。
    """
    if panel.is_empty():
        return panel
    sb = stock_basic if stock_basic is not None else load_stock_basic(db_path)
    nc = namechange if namechange is not None else load_namechange(db_path)

    # board：逐票分类一次（~5900 行）再 join 回大表
    codes = pl.DataFrame({"ts_code": panel["ts_code"].unique()})
    sb_small = codes.join(sb.select(["ts_code", "market", "list_date"]), on="ts_code", how="left")
    boards = [classify(m, c).value for m, c in zip(sb_small["market"].to_list(), sb_small["ts_code"].to_list())]
    sb_small = sb_small.with_columns(pl.Series("board", boards))
    out = panel.join(sb_small.select(["ts_code", "board", "list_date"]), on="ts_code", how="left")
    out = out.with_columns(pl.col("board").fill_null("MAIN"))
    out = out.with_columns(
        pl.when(pl.col("list_date").is_not_null())
        .then((pl.col("trade_date") - pl.col("list_date")).dt.total_days())
        .otherwise(99999)
        .alias("days_since_listing")
    )

    # is_st：as-of backward join（namechange 该 ts_code 在 trade_date 当天生效的名称）
    if not nc.is_empty():
        nc2 = (
            nc.filter(pl.col("start_date").is_not_null())
            .sort(["ts_code", "start_date"])
            .with_columns(pl.col("name").str.strip_chars("*").str.starts_with("ST").alias("is_st"))
            .select(["ts_code", "start_date", "is_st"])
        )
        out = out.sort(["ts_code", "trade_date"]).join_asof(
            nc2, left_on="trade_date", right_on="start_date", by="ts_code", strategy="backward"
        )
        out = out.with_columns(pl.col("is_st").fill_null(False)).drop("start_date", strict=False)
    else:
        out = out.with_columns(pl.lit(False).alias("is_st"))
    return out


def add_forward_returns(panel: pl.DataFrame, max_hold: int = 5) -> pl.DataFrame:
    """加事件研究用的前瞻收益列（**只供事件研究，信号列绝不引用**）。

    执行模型对齐 Broker：T 日决策 → T+1 开盘价买入 → 持有 d 交易日 → T+(1+d) 开盘价卖出。
    - `fwd_entry_open` = 次日开盘价（T+1 open）
    - `fwd_ret_{d}`（d=1..max_hold）= T+(1+d) open / T+1 open − 1（毛收益，未扣成本）
    - `fwd_buyable` = 次日可买入（次日有成交且非涨停；对齐 Broker「涨停买不进/停牌跳过」）

    区间末尾不足前瞻窗口的行相应列为 null，事件研究按 null 剔除。
    """
    df = panel.sort(["ts_code", "trade_date"])
    df = df.with_columns(
        pl.col("open").shift(-1).over("ts_code").alias("fwd_entry_open"),
        pl.col("is_limit_up").shift(-1).over("ts_code").alias("_next_limit_up"),
        pl.col("open").shift(-1).over("ts_code").is_not_null().alias("_next_has_bar"),
    )
    df = df.with_columns(
        (pl.col("_next_has_bar") & ~pl.col("_next_limit_up").fill_null(False)).alias("fwd_buyable")
    )
    exprs = []
    for d in range(1, max_hold + 1):
        exprs.append(
            (pl.col("open").shift(-(1 + d)).over("ts_code") / pl.col("fwd_entry_open") - 1).alias(f"fwd_ret_{d}")
        )
    df = df.with_columns(exprs)
    return df.drop(["_next_limit_up", "_next_has_bar"])


# ======================================================================
#  市场状态标签（P1 争议项 + 全局分层报告依据）
# ======================================================================

def market_state_labels(
    start: date, end: date, ma_window: int = 20, parquet_dir: Optional[Path] = None
) -> pl.DataFrame:
    """上证综指市场状态：每交易日标 `sse_close / sse_ma{N}` 与 `sse_above_ma`（bool）。

    MA 用后向窗口（不含未来）。返回 `trade_date / sse_close / sse_ma / sse_above_ma / year`。
    供 P1 市场过滤器与「所有结果按市场状态分层」的报告要求使用。
    """
    # 多取 ma_window 个交易日前的数据算 MA(否则区间头部 MA 为 null)。
    # **v1.4.1(§七 P1-26)**:起点由写死的 `date(2019,1,1)` 改为「`start` 往前留够 MA 所需
    # 的一小段」——`rolling_mean(ma_window, min_samples=ma_window)` 是**后向窗口**,要让
    # `start` 当天的 MA 非空,只需要 `start` 之前有 `ma_window` 个交易日,不需要整段历史。
    # 缓冲取 `ma_window*3 + 30` 自然日(20 日 MA → 90 自然日 ≈ 60 个交易日,含长假仍绰绰
    # 有余),**返回值逐位不变**(函数末尾照旧 filter 回 `[start, end]`),纯 I/O 裁剪:
    # 信息卡的 60 日窗只需 1~2 个 `year=` 目录,不再整表 glob 1592 个 footer。
    lookback_start = start - timedelta(days=ma_window * 3 + 30)
    idx = get_index_history(SSE_INDEX, lookback_start, end, parquet_dir=parquet_dir)
    if idx.is_empty():
        return pl.DataFrame(
            schema={
                "trade_date": pl.Date,
                "sse_close": pl.Float64,
                "sse_ma": pl.Float64,
                "sse_above_ma": pl.Boolean,
                "year": pl.Int32,
            }
        )
    idx = idx.sort("trade_date").with_columns(
        pl.col("close").rolling_mean(ma_window, min_samples=ma_window).alias("sse_ma")
    )
    idx = idx.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
    return idx.select(
        pl.col("trade_date"),
        pl.col("close").alias("sse_close"),
        pl.col("sse_ma"),
        (pl.col("close") > pl.col("sse_ma")).alias("sse_above_ma"),
        pl.col("trade_date").dt.year().cast(pl.Int32).alias("year"),
    )


# ======================================================================
#  研究面板装配（事件研究 + 组合回测策略共享的单一入口）
# ======================================================================

def build_research_panel(
    start: date,
    end: date,
    with_forward: bool = True,
    max_hold: int = 5,
    parquet_dir: Optional[Path] = None,
) -> pl.DataFrame:
    """一次性装配全历史研究面板（前复权 daily + 特征 + 涨停基因 + daily_basic
    + 市场状态 [+ 前瞻收益]）。事件研究与组合回测策略共享此面板 = 同码。

    注意：为让区间头部的 20 日窗口特征有效，实际多加载 ~40 个日历日的前置数据算
    特征，最后裁剪回 [start, end]。前瞻收益天然需要区间末尾之后的数据，末尾
    `max_hold+1` 天的 fwd_* 会是 null（事件研究按 null 剔除，如实少算末尾样本）。
    """
    # 前置缓冲：多取约 40 自然日（≈ 27 交易日 > 20 窗口）保证头部 MA20/ret20 有效
    from datetime import timedelta

    load_start = start - timedelta(days=45)
    daily = scan_table_range("daily", load_start, end, parquet_dir=parquet_dir)
    if daily.is_empty():
        return daily
    adj = scan_table_range("adj_factor", load_start, end, parquet_dir=parquet_dir)
    if not adj.is_empty():
        merged = daily.join(
            adj.select(["ts_code", "trade_date", "adj_factor"]), on=["ts_code", "trade_date"], how="left"
        )
        adjusted = apply_qfq(merged, price_cols=_PRICE_COLS)
        qfq_cols = [f"{c}_qfq" for c in _PRICE_COLS]
        daily = adjusted.drop(list(_PRICE_COLS)).rename(dict(zip(qfq_cols, _PRICE_COLS)))

    panel = add_features(daily)

    limit_derived = scan_table_range("limit_derived", load_start, end, parquet_dir=parquet_dir)
    panel = merge_limit_features(panel, limit_derived)

    daily_basic = scan_table_range("daily_basic", load_start, end, parquet_dir=parquet_dir)
    panel = merge_daily_basic(panel, daily_basic)

    panel = merge_meta(panel)

    if with_forward:
        panel = add_forward_returns(panel, max_hold=max_hold)

    # 裁剪回请求区间（特征已用到前置缓冲，此处安全）
    panel = panel.filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))

    # 并入市场状态标签
    states = market_state_labels(start, end, parquet_dir=parquet_dir)
    if not states.is_empty():
        panel = panel.join(states, on="trade_date", how="left")
    return panel.sort(["trade_date", "ts_code"])


__all__ = [
    "add_features",
    "merge_limit_features",
    "merge_daily_basic",
    "merge_meta",
    "add_forward_returns",
    "market_state_labels",
    "build_research_panel",
    "SSE_INDEX",
]
