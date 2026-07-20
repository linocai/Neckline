"""事件研究(plan 1.1 赛马 / 1.2 禁买对照 / 分层报告)。

给一个「买入信号」布尔表达式,在研究面板上度量**每笔信号买入后的前瞻收益**
分布——毛/净(扣成本)、胜率、盈利因子,可按持有天数、按年、按市场状态分层。

执行/成本模型(对齐 Broker,honest):
    · 买 T+1 开盘价、卖 T+(1+d) 开盘价(`fwd_ret_d`,d=持有交易日)。
    · 只统计 `fwd_buyable`(次日有成交且非涨停,涨停买不进/停牌跳过与 Broker 一致)。
    · 单边成本 `cost_oneside`(滑点+佣金+印花税/2 的粗估),净收益 = 毛 − 2×单边。

这不是组合回测(无仓位/敞口约束、每笔等权、允许同日无限多笔),测的是**信号本身
的期望值**——赛马选强势定义、量化禁买规则边际贡献的最快且可比的口径。组合层的
现实约束由 `neckline.backtest` 引擎另测。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import polars as pl

# 单边成本粗估:滑点 10bp + 佣金 2.5bp + (印花税 5bp 仅卖出,摊到单边约 2.5bp) ≈ 15bp。
# 双边 ≈ 30bp,与 Broker 费用模型量级一致(见 backtest/broker.py)。
DEFAULT_COST_ONESIDE = 0.0015


def _stats_for_hold(df: pl.DataFrame, d: int, cost_oneside: float) -> Dict[str, float]:
    col = f"fwd_ret_{d}"
    sub = df.filter(pl.col("fwd_buyable") & pl.col(col).is_not_null())
    n = sub.height
    if n == 0:
        return {"hold_days": d, "n": 0, "win_rate": float("nan"), "mean_gross": float("nan"),
                "median_gross": float("nan"), "mean_net": float("nan"), "profit_factor": float("nan")}
    gross = sub[col]
    net = gross - 2 * cost_oneside  # 买卖各一次单边成本
    wins = net.filter(net > 0)
    losses = net.filter(net < 0)
    gross_profit = float(wins.sum()) if wins.len() else 0.0
    gross_loss = abs(float(losses.sum())) if losses.len() else 0.0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    return {
        "hold_days": d,
        "n": n,
        "win_rate": float((net > 0).sum()) / n,
        "mean_gross": float(gross.mean()),
        "median_gross": float(gross.median()),
        "mean_net": float(net.mean()),
        "profit_factor": pf,
    }


def event_study(
    panel: pl.DataFrame,
    signal_expr: pl.Expr,
    hold_days: Sequence[int] = (1, 2, 3, 4, 5),
    cost_oneside: float = DEFAULT_COST_ONESIDE,
) -> pl.DataFrame:
    """信号全期前瞻收益统计(每个持有天数一行)。"""
    sig = panel.filter(signal_expr)
    rows = [_stats_for_hold(sig, d, cost_oneside) for d in hold_days]
    return pl.DataFrame(rows)


def event_study_grouped(
    panel: pl.DataFrame,
    signal_expr: pl.Expr,
    group_col: str,
    hold_days: Sequence[int] = (3,),
    cost_oneside: float = DEFAULT_COST_ONESIDE,
) -> pl.DataFrame:
    """按 `group_col`(如 year / sse_above_ma)分层的前瞻收益统计。默认只报持有 3 日
    (骨架期主用),可传多个 hold_days。每个 (组×持有天数) 一行。"""
    sig = panel.filter(signal_expr)
    out_rows: List[dict] = []
    groups = sig.select(group_col).unique().sort(group_col)[group_col].to_list()
    for g in groups:
        gsub = sig.filter(pl.col(group_col) == g) if g is not None else sig.filter(pl.col(group_col).is_null())
        for d in hold_days:
            r = _stats_for_hold(gsub, d, cost_oneside)
            r[group_col] = g
            out_rows.append(r)
    if not out_rows:
        return pl.DataFrame()
    cols = [group_col, "hold_days", "n", "win_rate", "mean_gross", "mean_net", "profit_factor"]
    return pl.DataFrame(out_rows).select([c for c in cols if c in out_rows[0]])


def compare_signals(
    panel: pl.DataFrame,
    named_signals: Dict[str, pl.Expr],
    hold_days: Sequence[int] = (1, 2, 3, 4, 5),
    cost_oneside: float = DEFAULT_COST_ONESIDE,
) -> pl.DataFrame:
    """多信号赛马:每个信号 × 每个持有天数一行,并排比较。"""
    out: List[pl.DataFrame] = []
    for name, expr in named_signals.items():
        df = event_study(panel, expr, hold_days, cost_oneside).with_columns(pl.lit(name).alias("signal"))
        out.append(df)
    if not out:
        return pl.DataFrame()
    res = pl.concat(out)
    front = ["signal", "hold_days", "n", "win_rate", "mean_gross", "mean_net", "profit_factor"]
    return res.select(front)


def fmt_table(df: pl.DataFrame, floatfmt: str = "{:.4f}") -> str:
    """把统计表渲染成对齐的纯文本表(报告 markdown 用)。"""
    if df.is_empty():
        return "(空)"
    cols = df.columns
    rows = []
    for r in df.iter_rows(named=True):
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("nan" if v != v else (floatfmt.format(v) if abs(v) < 1e6 else "inf"))
            else:
                cells.append(str(v))
        rows.append(cells)
    widths = [max(len(c), *(len(row[i]) for row in rows)) for i, c in enumerate(cols)]
    line = lambda cells: " | ".join(s.rjust(widths[i]) for i, s in enumerate(cells))
    header = line(cols)
    sep = "-|-".join("-" * w for w in widths)
    return "\n".join([header, sep] + [line(r) for r in rows])


__all__ = [
    "event_study",
    "event_study_grouped",
    "compare_signals",
    "fmt_table",
    "DEFAULT_COST_ONESIDE",
]
