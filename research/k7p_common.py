"""K7 前置 · 注意力口径审计(战役一/二)共用工具。

预注册见 `research/k7_pre_report.md` §0(判分口径)与 `k7_pre2_report.md` §0。
只进研究缓存,生产零改动(承 k4p_common 姿势,并复用其 fwd_ld/fwd_lu 特征)。

**注意力口径(与 k4p 的买入期望口径的区别,§0.3 预注册)**:
    · close 口径前向 `fwd_c_ret_d = close(T+d)/close(T) − 1`——不受可买性截断,
      回答「这只票接下来强不强」(注意力问题),不是「买入赚不赚」;
    · MFE_3/MAE_3 锚 T+1 开盘(`fwd_entry_open`),只在 `fwd_buyable` 子集上算
      (买不进的票谈不上人判层吃波段);
    · 机会密度 = P(MFE_3 ≥ +5%)、P(3 日内任一日收盘涨停);
    · 左尾 = fwd_ld_next / fwd_ld_hold3(k4p_common 既有列)。
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

import polars as pl

from k4p_common import add_k4p_features, base_expr, oneword_event_expr, fmt  # noqa: F401

MFE_TH = 0.05  # 机会密度阈值(§0.3 预注册)

__all__ = [
    "MFE_TH",
    "add_k7p_features",
    "attention_row",
    "attention_table",
    "seg_exprs",
    "base_expr",
    "oneword_event_expr",
    "fmt",
]


def add_k7p_features(panel: pl.DataFrame) -> pl.DataFrame:
    """叠加注意力口径特征(先过 add_k4p_features 拿 fwd_ld_*/fwd_lu_next)。"""
    df = add_k4p_features(panel)
    df = df.sort(["ts_code", "trade_date"])
    over = "ts_code"

    h1 = pl.col("high").shift(-1).over(over)
    h2 = pl.col("high").shift(-2).over(over)
    h3 = pl.col("high").shift(-3).over(over)
    l1 = pl.col("low").shift(-1).over(over)
    l2 = pl.col("low").shift(-2).over(over)
    l3 = pl.col("low").shift(-3).over(over)
    lu1 = pl.col("is_limit_up").shift(-1).over(over).fill_null(False)
    lu2 = pl.col("is_limit_up").shift(-2).over(over).fill_null(False)
    lu3 = pl.col("is_limit_up").shift(-3).over(over).fill_null(False)

    df = df.with_columns(
        (pl.col("close").shift(-1).over(over) / pl.col("close") - 1).alias("fwd_c_ret_1"),
        (pl.col("close").shift(-3).over(over) / pl.col("close") - 1).alias("fwd_c_ret_3"),
        (pl.max_horizontal(h1, h2, h3) / pl.col("fwd_entry_open") - 1).alias("mfe_3"),
        (pl.min_horizontal(l1, l2, l3) / pl.col("fwd_entry_open") - 1).alias("mae_3"),
        (lu1 | lu2 | lu3).alias("fwd_lu_hold3"),
        # MFE/MAE 有效性:T+3 行存在(高低价齐);不足 3 日的序列尾行剔出统计。
        pl.col("high").shift(-3).over(over).is_not_null().alias("fwd3_valid"),
    )
    return df.sort(["trade_date", "ts_code"])


def seg_exprs() -> List[tuple]:
    """三分段:样本内(2020-2024)/样本外(2025-2026.07)/2026 单列(§0.2)。"""
    d24 = date(2024, 12, 31)
    d25 = date(2025, 1, 1)
    return [
        ("样本内20-24", pl.col("trade_date") <= d24),
        ("样本外25-26", pl.col("trade_date") >= d25),
        ("2026分段", pl.col("year") == 2026),
    ]


def attention_row(events: pl.DataFrame, label: str) -> dict:
    """一个事件集的注意力口径汇总行(§0.3 全量指标)。

    close 口径在全事件上算;MFE/MAE/机会密度/左尾在 fwd_buyable & fwd3_valid 子集
    上算(并如实报 buyable 率——「买不进」本身是结论,如 H10 龙头常一字)。
    """
    n = events.height
    if n == 0:
        return {"group": label, "n": 0}
    c1 = events["fwd_c_ret_1"].drop_nulls()
    c3 = events["fwd_c_ret_3"].drop_nulls()
    sub = events.filter(pl.col("fwd_buyable") & pl.col("fwd3_valid") & pl.col("mfe_3").is_not_null())
    nb = sub.height
    row = {
        "group": label,
        "n": n,
        "c_ret1_mean": float(c1.mean()) if c1.len() else float("nan"),
        "c_ret3_mean": float(c3.mean()) if c3.len() else float("nan"),
        "c_ret3_med": float(c3.median()) if c3.len() else float("nan"),
        "buyable_rate": float(events["fwd_buyable"].mean()),
        "n_buyable": nb,
    }
    if nb == 0:
        row.update({k: float("nan") for k in (
            "mfe3_p50", "mfe3_p75", "mfe3_p90", "opp_ge5", "lu_hold3",
            "mae3_p50", "mae3_p10", "ld_next", "ld_hold3")})
        return row
    mfe = sub["mfe_3"]
    mae = sub["mae_3"]
    row.update({
        "mfe3_p50": float(mfe.median()),
        "mfe3_p75": float(mfe.quantile(0.75)),
        "mfe3_p90": float(mfe.quantile(0.90)),
        "opp_ge5": float((mfe >= MFE_TH).mean()),
        "lu_hold3": float(sub["fwd_lu_hold3"].mean()),
        "mae3_p50": float(mae.median()),
        "mae3_p10": float(mae.quantile(0.10)),
        "ld_next": float(sub["fwd_ld_next"].mean()),
        "ld_hold3": float(sub["fwd_ld_hold3"].mean()),
    })
    return row


def attention_table(named_events: List[tuple], seg_expr: Optional[pl.Expr] = None) -> pl.DataFrame:
    """多事件集 × 单分段的注意力汇总表。named_events = [(label, df), ...]。"""
    rows = []
    for label, ev in named_events:
        sub = ev.filter(seg_expr) if seg_expr is not None else ev
        rows.append(attention_row(sub, label))
    return pl.DataFrame(rows, infer_schema_length=None)
