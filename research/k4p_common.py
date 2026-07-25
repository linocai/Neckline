"""K4 前置 · 用户漏斗审计(事件研究档)三假设 runner 共用工具。

**只进研究面板缓存,生产零改动**(承 `research/k3_panel.py` 姿势)。所有度量口径
与 `neckline.research.eventstudy` 严格对齐(同 `fwd_buyable` 过滤、同双边成本
`DEFAULT_COST_ONESIDE`),在其之上补「左尾 p5/p10 + 次日/持有内跌停暴露」——K3
教训:均值口径测不到左尾,诚实判决必须同报尾部。

**前向口径(施工前读 `features.py::add_forward_returns` 确认,写进报告 §0)**:
    · 决策日 D → **T+1 开盘价买入** → 持有 d 交易日 → **T+(1+d) 开盘价卖出**。
    · `fwd_ret_d` = T+(1+d) open / T+1 open − 1(毛,开盘口径,非收盘)。
    · `fwd_buyable` = 次日有成交且非涨停(涨停买不进/停牌跳过,对齐 Broker)。
两组对比一律同口径同持有窗(H3 成交组/市价对照都退出在 T+(1+N) open)。

新特征列(只在本模块现算,不落生产面板,不改任何生产列语义):
    · `vol_above_ma20_cnt3`(H1):前 3 个交易日中 vol>vol_ma20 的天数,
      per ts_code,shift(1) 防未来函数。
    · `fwd_low_1`/`fwd_high_1`/`fwd_open_1`(H3):次日最低/最高/开盘(挂单成交判定)。
    · `fwd_ld_next`/`fwd_ld_hold3`:次日 / 持有 3 日内任一日收盘跌停(左尾暴露标)。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import polars as pl

from neckline.research.eventstudy import DEFAULT_COST_ONESIDE
from neckline.research.panel import base_universe_expr

# 次新剔除阈值(§0 预注册:days_since_listing >= 120)
MIN_DAYS_SINCE_LISTING = 120

__all__ = [
    "DEFAULT_COST_ONESIDE",
    "MIN_DAYS_SINCE_LISTING",
    "add_k4p_features",
    "base_expr",
    "oneword_event_expr",
    "hold_table",
    "exposure_row",
    "fmt",
    "year_2026_expr",
]


def add_k4p_features(panel: pl.DataFrame) -> pl.DataFrame:
    """在研究面板上叠加 K4 前置三假设所需的新特征(只进研究缓存,不改生产列)。"""
    df = panel.sort(["ts_code", "trade_date"])
    over = "ts_code"

    # H1:前 3 交易日放量天数(不含当日;shift(1) 防未来函数)。
    is_vol_above = (pl.col("vol") > pl.col("vol_ma20")).cast(pl.Int64)
    df = df.with_columns(
        is_vol_above.rolling_sum(3, min_samples=3).over(over).shift(1).over(over).alias("vol_above_ma20_cnt3")
    )

    # H3:次日 OHLC(挂单成交判定 + 一字跌停排除)。fwd_open_1 = fwd_entry_open(已有,别名对齐)。
    df = df.with_columns(
        pl.col("low").shift(-1).over(over).alias("fwd_low_1"),
        pl.col("high").shift(-1).over(over).alias("fwd_high_1"),
        pl.col("open").shift(-1).over(over).alias("fwd_open_1"),
    )

    # 左尾暴露:次日 / 持有 3 日内任一日收盘跌停(仅度量,信号列不引用)。
    ld1 = pl.col("is_limit_down").shift(-1).over(over).fill_null(False)
    ld2 = pl.col("is_limit_down").shift(-2).over(over).fill_null(False)
    ld3 = pl.col("is_limit_down").shift(-3).over(over).fill_null(False)
    df = df.with_columns(
        ld1.alias("fwd_ld_next"),
        (ld1 | ld2 | ld3).alias("fwd_ld_hold3"),
        # H3:次日是否涨停(一字涨停=飞走 → 挂低单/市价皆买不进,两口径同剔)。
        pl.col("is_limit_up").shift(-1).over(over).fill_null(False).alias("fwd_lu_next"),
    )
    return df.sort(["trade_date", "ts_code"])


def base_expr() -> pl.Expr:
    """§0 主判决域:base_universe_expr() + 剔次新(days_since_listing >= 120),全板块。"""
    return base_universe_expr() & (pl.col("days_since_listing") >= MIN_DAYS_SINCE_LISTING)


def oneword_event_expr() -> pl.Expr:
    """事件日本身为一字涨停/跌停(open=high=low=close,幅度到限)→ 剔除(§0 可交易性)。
    一字板判据:命中涨/跌停 且 当日无振幅(high==low)。"""
    return (pl.col("is_limit_up") | pl.col("is_limit_down")) & (pl.col("high") == pl.col("low"))


def hold_table(
    events: pl.DataFrame,
    holds: Sequence[int] = (1, 2, 3, 4, 5),
    cost_oneside: float = DEFAULT_COST_ONESIDE,
) -> pl.DataFrame:
    """事件集的前瞻收益 + 左尾统计(每持有天数一行)。口径与 eventstudy._stats_for_hold
    严格一致(fwd_buyable & fwd_ret_d 非空过滤,net = gross − 2×单边成本),额外补
    p5_net / p10_net(左尾分位)。"""
    rows: List[dict] = []
    for d in holds:
        col = f"fwd_ret_{d}"
        sub = events.filter(pl.col("fwd_buyable") & pl.col(col).is_not_null())
        n = sub.height
        if n == 0:
            rows.append({"hold": d, "n": 0, "win_rate": float("nan"), "mean_gross": float("nan"),
                         "mean_net": float("nan"), "p5_net": float("nan"), "p10_net": float("nan"),
                         "pf": float("nan")})
            continue
        gross = sub[col]
        net = gross - 2 * cost_oneside
        wins = net.filter(net > 0)
        losses = net.filter(net < 0)
        gp = float(wins.sum()) if wins.len() else 0.0
        gl = abs(float(losses.sum())) if losses.len() else 0.0
        pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
        rows.append({
            "hold": d,
            "n": n,
            "win_rate": float((net > 0).sum()) / n,
            "mean_gross": float(gross.mean()),
            "mean_net": float(net.mean()),
            "p5_net": float(net.quantile(0.05)),
            "p10_net": float(net.quantile(0.10)),
            "pf": pf,
        })
    return pl.DataFrame(rows)


def exposure_row(events: pl.DataFrame, label: str) -> dict:
    """事件集(条件在 fwd_buyable=可买入子集上)的跌停暴露 + 中位/均值参考。
    次日跌停率 = P(买入后 D+1 收盘跌停);持有跌停率 = P(D+1..D+3 任一收盘跌停)。"""
    sub = events.filter(pl.col("fwd_buyable"))
    n = sub.height
    if n == 0:
        return {"group": label, "n": 0, "next_ld_rate": float("nan"), "hold3_ld_rate": float("nan")}
    return {
        "group": label,
        "n": n,
        "next_ld_rate": float(sub["fwd_ld_next"].mean()),
        "hold3_ld_rate": float(sub["fwd_ld_hold3"].mean()),
    }


def year_2026_expr() -> pl.Expr:
    """2026 分段(生存视角单列):trade_date 落在 2026 年。"""
    return pl.col("year") == 2026


def fmt(df: pl.DataFrame, floatfmt: str = "{:.4f}", intcols: Sequence[str] = ("n", "hold", "year")) -> str:
    """markdown 对齐表(pipe 表格)。复用 lab.fmt 的风格,intcols 显示为整数。"""
    if df is None or df.is_empty():
        return "(空)"
    cols = df.columns
    out_rows = []
    for r in df.iter_rows(named=True):
        cells = []
        for c in cols:
            v = r[c]
            if v is None:
                cells.append("nan")
            elif c in intcols and isinstance(v, (int, float)) and v == v:
                cells.append(str(int(v)))
            elif isinstance(v, bool):
                cells.append(str(v))
            elif isinstance(v, float):
                cells.append("nan" if v != v else ("inf" if abs(v) > 1e6 else floatfmt.format(v)))
            else:
                cells.append(str(v))
        out_rows.append(cells)
    widths = [max(len(c), *(len(r[i]) for r in out_rows)) for i, c in enumerate(cols)]
    line = lambda cells: "| " + " | ".join(s.rjust(widths[i]) for i, s in enumerate(cells)) + " |"
    sep = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
    return "\n".join([line(cols), sep] + [line(r) for r in out_rows])
