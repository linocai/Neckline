"""B4 · 中心命题回测(K2 vs K1,walk-forward,消融)★本研究中心。

中心命题:阶段 1 否决的「追强势无正期望」是**全时段×全市场**的平均结论——
「**情绪进攻段 × 主线成员内追强势**」这个子域的期望从未被检验。B4 检验之。

K2 候选构型 = `base_universe ∩ 情绪进攻段(B1 满额 gate) ∩ 主线成员(B3 mask) ∩ 强势形态`。
消融矩阵:K1 全时段全市场基线 → +情绪 gate → +主线成员 → +强势形态,逐层加 + 各自
单独去掉。信号级(eventstudy)打底 + 组合级(run_pf)定论。walk-forward 对手 K1。

**边界**:纪律设定全读现役 K1 config(不硬编);主线 mask 与情绪 gate 以默认关闭字段/
外部注入实现(B4.0,K1 逐位不变已单测)。资金面 moneyflow 不纳入(承阶段 1,且历史
分区有类型漂移坑,见报告 §0.3)。

运行:python -m research.b4_central
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Set

import polars as pl

from neckline.research import eventstudy as es
from neckline.strategy import brain, signals as S
from neckline.strategy.momentum import MomentumConfig
from neckline.backtest.walk_forward import generate_walk_forward_windows
from neckline.research.panel import (
    base_universe_expr, SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END,
)
from research import lab

CACHE = Path(__file__).resolve().parent / "_cache"
K2_PANEL = CACHE / "k2_panel.parquet"


def build_k2_panel(rebuild: bool = False) -> pl.DataFrame:
    """K2 面板 = panel_full + is_mainline_member(B3)+ _full_day/_nonrest_day(B1 情绪进攻段)。"""
    if K2_PANEL.exists() and not rebuild:
        return pl.read_parquet(K2_PANEL)
    panel = pl.read_parquet(CACHE / "panel_full.parquet")
    mem = pl.read_parquet(CACHE / "mainline_members.parquet").select(["trade_date", "ts_code", "is_member"])
    panel = panel.join(mem, on=["trade_date", "ts_code"], how="left").with_columns(
        pl.col("is_member").fill_null(False).alias("is_mainline_member")
    ).drop("is_member")
    sent = pl.read_parquet(CACHE / "sentiment_daily.parquet")
    full_days = set(sent.filter(pl.col("tier") == "满额")["trade_date"].to_list())
    nonrest_days = set(sent.filter(pl.col("tier") != "休息")["trade_date"].to_list())
    panel = panel.with_columns(
        pl.col("trade_date").is_in(list(full_days)).alias("_full_day"),
        pl.col("trade_date").is_in(list(nonrest_days)).alias("_nonrest_day"),
    )
    panel.write_parquet(K2_PANEL)
    return panel


def attack_gate(kind: str = "full") -> Set[date]:
    sent = pl.read_parquet(CACHE / "sentiment_daily.parquet")
    if kind == "full":
        return set(sent.filter(pl.col("tier") == "满额")["trade_date"].to_list())
    return set(sent.filter(pl.col("tier") != "休息")["trade_date"].to_list())


def k1_cfg() -> MomentumConfig:
    return MomentumConfig(**brain.active_config())


# ======================================================================
#  信号级消融(eventstudy;子域内每笔等权,测「信号本身」期望)
# ======================================================================

def _strength_expr(kind: str) -> Optional[pl.Expr]:
    return {
        "none": None,
        "limitup_gene": S.strength_limitup_gene(1),
        "volprice": S.strength_volprice(),
        "ret20_pct": S.strength_ret_rank_pct(0.90),
    }[kind]


def signal_ablation(panel: pl.DataFrame, window: str = "in", holds=(1, 2, 3, 5)) -> pl.DataFrame:
    a, b = (SAMPLE_IN_START, SAMPLE_IN_END) if window == "in" else (SAMPLE_OUT_START, SAMPLE_OUT_END)
    sub = panel.filter((pl.col("trade_date") >= a) & (pl.col("trade_date") <= b))
    base = base_universe_expr()
    pull = S.buy_pullback()
    gate = pl.col("_full_day")
    mem = pl.col("is_mainline_member")
    configs = {
        "L0 全域 base+pull(对标P3)": base & pull,
        "L1 +情绪进攻段(满额)": base & pull & gate,
        "L2 +主线成员": base & pull & gate & mem,
        "L3 +强势·涨停基因(=K2a)": base & pull & gate & mem & S.strength_limitup_gene(1),
        "L3 +强势·volprice(=K2b)": base & pull & gate & mem & S.strength_volprice(),
        "L3 +强势·ret20pct(=K2c)": base & pull & gate & mem & S.strength_ret_rank_pct(0.90),
        "LOO K2a−gate": base & pull & mem & S.strength_limitup_gene(1),
        "LOO K2a−member": base & pull & gate & S.strength_limitup_gene(1),
        "LOO K2a−strength(=L2)": base & pull & gate & mem,
    }
    rows = []
    for name, expr in configs.items():
        for h in holds:
            r = es.event_study(sub, expr, hold_days=(h,)).to_dicts()[0]
            rows.append({"config": name, "hold": h, "n": r["n"], "win": r["win_rate"],
                         "mean_net": r["mean_net"], "pf": r["profit_factor"]})
    return pl.DataFrame(rows)


# ======================================================================
#  组合级消融(run_pf;带 -5% 止损/hold5/仓位纪律,子域定论)
# ======================================================================

def _cfg(strength="none", member=False):
    base = brain.active_config()
    return MomentumConfig(**{**base, "strength": strength, "require_mainline_member": member})


def portfolio_ablation(panel: pl.DataFrame, window: str = "in") -> pl.DataFrame:
    a, b = (SAMPLE_IN_START, SAMPLE_IN_END) if window == "in" else (SAMPLE_OUT_START, SAMPLE_OUT_END)
    g_full = attack_gate("full")
    rows = []

    def run(label, cfg, gate):
        rep, pf = lab.run_pf(cfg, a, b, panel=panel, buy_gate=gate)
        rows.append(lab.summary_row(rep, label))
        return pf

    run("K1 基线(none,无gate,无member)", _cfg("none", False), None)
    run("+情绪进攻段(满额)", _cfg("none", False), g_full)
    run("+主线成员", _cfg("none", True), g_full)
    run("K2a +强势涨停基因", _cfg("limitup_gene", True), g_full)
    run("K2b +强势volprice", _cfg("volprice", True), g_full)
    run("K2c +强势ret20pct", _cfg("ret20_pct", True), g_full)
    run("LOO K2a−gate", _cfg("limitup_gene", True), None)
    run("LOO K2a−member", _cfg("limitup_gene", False), g_full)
    return pl.DataFrame(rows)


def walk_forward_k2_vs_k1(panel: pl.DataFrame, k2_strength: str, k2_member: bool,
                          train_days=252, test_days=126) -> pl.DataFrame:
    g_full = attack_gate("full")
    k1 = _cfg("none", False)
    k2 = _cfg(k2_strength, k2_member)
    wins = generate_walk_forward_windows(SAMPLE_IN_START, SAMPLE_OUT_END, train_days, test_days)
    rows = []
    for w in wins:
        rk1, _ = lab.run_pf(k1, w.test_start, w.test_end, panel=panel, buy_gate=None)
        rk2, _ = lab.run_pf(k2, w.test_start, w.test_end, panel=panel, buy_gate=g_full)
        rows.append({"test_start": w.test_start, "k1_ret": rk1.total_return, "k2_ret": rk2.total_return,
                     "k1_pf": rk1.profit_factor, "k2_pf": rk2.profit_factor, "k2_n": rk2.n_trades,
                     "k2_better": rk2.total_return > rk1.total_return})
    return pl.DataFrame(rows)


if __name__ == "__main__":
    panel = build_k2_panel()
    ins = panel.filter((pl.col("trade_date") >= SAMPLE_IN_START) & (pl.col("trade_date") <= SAMPLE_IN_END))
    print(f"K2 面板 {panel.height} 行 | 样本内满额日占比 {ins['_full_day'].mean():.1%} "
          f"主线成员占比 {ins['is_mainline_member'].mean():.1%}")

    print("\n" + "=" * 70 + "\nB4.2 信号级消融(样本内)\n" + "=" * 70)
    print(lab.fmt(signal_ablation(panel, "in")))
    print("\n" + "=" * 70 + "\nB4.2 信号级消融(样本外)\n" + "=" * 70)
    print(lab.fmt(signal_ablation(panel, "out")))

    print("\n" + "=" * 70 + "\nB4.3 组合级消融(样本内)\n" + "=" * 70)
    print(lab.fmt(portfolio_ablation(panel, "in")))
    print("\n" + "=" * 70 + "\nB4.3 组合级消融(样本外)\n" + "=" * 70)
    print(lab.fmt(portfolio_ablation(panel, "out")))

    print("\n" + "=" * 70 + "\nB4.3 walk-forward: K2a(涨停基因+gate+member) vs K1\n" + "=" * 70)
    wf = walk_forward_k2_vs_k1(panel, "limitup_gene", True)
    print(lab.fmt(wf))
    if not wf.is_empty():
        print(f"\nK2a 跑赢 K1 窗口数:{int(wf['k2_better'].sum())}/{wf.height}")
