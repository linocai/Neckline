"""B1.3–B1.5 · 情绪闸门可交易性检验(对标阶段 1 P1 MA20 闸门,同口径可比)。

核心实验(与 P1 完全同口径):用 `lab.run_pf(K1_cfg, buy_gate=情绪允许开仓日集合)`
把「休息」日排除开新仓(二值闸门),对照 K1 全天开仓。闸门在**决策日 T** 用 T 的
EOD 情绪判定(与 P1 用 T 日收盘 MA20 状态同口径,决策 T→成交 T+1,无前视)。

第二实验:半额档 sizing 调制(半额日 single_cap 减半)——以 research 层策略子类
`SentimentSizedStrategy` 实现,**零生产改动**(不碰 MomentumConfig / MomentumStrategy)。

样本内定阈 + 样本外有限次 + walk-forward + 年/状态分层 + ±1 格敏感性(防悬崖)。

运行:python -m research.b1_gate
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Set, Tuple

import polars as pl

from neckline.strategy import brain
from neckline.strategy.momentum import MomentumConfig, MomentumStrategy
from neckline.backtest.engine import BacktestEngine
from neckline.backtest.walk_forward import generate_walk_forward_windows
from research import lab
from research.b1_sentiment import (
    CACHE, TierThresholds, assign_tier, build_sentiment_panel, FULL, HALF, REST,
)
from neckline.research.panel import (
    SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END,
)


def k1_config() -> MomentumConfig:
    return MomentumConfig(**brain.active_config())


def load_tiered_panel(th: TierThresholds = TierThresholds()) -> pl.DataFrame:
    if CACHE.exists():
        base = pl.read_parquet(CACHE).drop(["_base_tier", "tier"], strict=False)
    else:
        base = build_sentiment_panel()
    return assign_tier(base, th)


def gate_set(panel: pl.DataFrame, allowed: Tuple[str, ...]) -> Set[date]:
    """允许开新仓的决策日集合 = tier ∈ allowed 的交易日。"""
    return set(panel.filter(pl.col("tier").is_in(list(allowed)))["trade_date"].to_list())


# ======================================================================
#  第二实验:半额 sizing(research 层策略子类,零生产改动)
# ======================================================================

class SentimentSizedStrategy(MomentumStrategy):
    """在 MomentumStrategy 之上,按当日情绪档位调制单笔上限:
       满额日 = single_cap;半额日 = single_cap × half_frac;休息日由 buy_gate 挡掉。
    仅覆盖 `_effective_single_cap`,其余选股/退出/纪律逻辑完全继承(同码)。"""

    def __init__(self, *args, tier_by_date: Optional[Dict[date, str]] = None,
                 half_frac: float = 0.5, **kwargs):
        super().__init__(*args, **kwargs)
        self._tier_by_date = tier_by_date or {}
        self._half_frac = half_frac

    def _effective_single_cap(self, t: date) -> float:
        base = super()._effective_single_cap(t)
        if self._tier_by_date.get(t) == HALF:
            return base * self._half_frac
        return base


def run_sized(cfg: MomentumConfig, start: date, end: date, tier_by_date: Dict[date, str],
              buy_gate: Set[date], half_frac: float = 0.5):
    p = lab.get_panel()
    strat = SentimentSizedStrategy(
        p, cfg, initial_cash=lab.INITIAL_CASH, buy_gate=buy_gate,
        tier_by_date=tier_by_date, half_frac=half_frac,
    )
    eng = BacktestEngine(strat, start, end, initial_cash=lab.INITIAL_CASH,
                         adjusted_daily=lab.adjusted_daily_cached(start, end))
    rep = eng.run()
    return rep, eng.last_portfolio


# ======================================================================
#  实验编排
# ======================================================================

def _summ(rep, label: str) -> dict:
    return lab.summary_row(rep, label)


def experiment_binary(cfg: MomentumConfig, panel: pl.DataFrame) -> Dict[str, pl.DataFrame]:
    """二值闸门核心实验:naked / 排除休息(非休息=满∪半) / 仅满额,样本内+样本外。"""
    g_nonrest = gate_set(panel, (FULL, HALF))
    g_full = gate_set(panel, (FULL,))
    out = {}
    for lbl, a, b in [("样本内", SAMPLE_IN_START, SAMPLE_IN_END),
                      ("样本外", SAMPLE_OUT_START, SAMPLE_OUT_END)]:
        rows = []
        rep_naked, pf_naked = lab.run_pf(cfg, a, b, buy_gate=None)
        rows.append(_summ(rep_naked, "K1 裸(全天开仓)"))
        rep_nr, pf_nr = lab.run_pf(cfg, a, b, buy_gate=g_nonrest)
        rows.append(_summ(rep_nr, "K1+闸门(排除休息日)"))
        rep_f, pf_f = lab.run_pf(cfg, a, b, buy_gate=g_full)
        rows.append(_summ(rep_f, "K1+闸门(仅满额日)"))
        out[lbl] = pl.DataFrame(rows)
        if lbl == "样本外":
            out["_pf_naked_out"] = pf_naked
            out["_pf_nonrest_out"] = pf_nr
    return out


def experiment_sizing(cfg: MomentumConfig, panel: pl.DataFrame) -> pl.DataFrame:
    """半额 sizing 实验:排除休息日 + 半额日单笔减半 vs 排除休息日全额。"""
    g_nonrest = gate_set(panel, (FULL, HALF))
    tier_by_date = dict(zip(panel["trade_date"].to_list(), panel["tier"].to_list()))
    rows = []
    for lbl, a, b in [("样本内", SAMPLE_IN_START, SAMPLE_IN_END),
                      ("样本外", SAMPLE_OUT_START, SAMPLE_OUT_END)]:
        rep_full, _ = lab.run_pf(cfg, a, b, buy_gate=g_nonrest)
        rows.append({**_summ(rep_full, f"{lbl}·非休息全额"), "window": lbl})
        rep_half, _ = run_sized(cfg, a, b, tier_by_date, g_nonrest, half_frac=0.5)
        rows.append({**_summ(rep_half, f"{lbl}·非休息+半额减半"), "window": lbl})
    return pl.DataFrame(rows)


def experiment_walk_forward(cfg: MomentumConfig, panel: pl.DataFrame,
                            train_days: int = 252, test_days: int = 126) -> pl.DataFrame:
    """walk-forward:每个滚动样本外窗口对比 naked vs 排除休息日闸门。用固定(生产口径)
    情绪阈值,考察闸门是否跨窗稳定改善(非单窗偶然)。"""
    g_nonrest = gate_set(panel, (FULL, HALF))
    wins = generate_walk_forward_windows(SAMPLE_IN_START, SAMPLE_OUT_END, train_days, test_days)
    rows = []
    for w in wins:
        rep_n, _ = lab.run_pf(cfg, w.test_start, w.test_end, buy_gate=None)
        rep_g, _ = lab.run_pf(cfg, w.test_start, w.test_end, buy_gate=g_nonrest)
        rows.append({
            "test_start": w.test_start, "test_end": w.test_end,
            "naked_ret": rep_n.total_return, "gate_ret": rep_g.total_return,
            "naked_pf": rep_n.profit_factor, "gate_pf": rep_g.profit_factor,
            "gate_better_ret": rep_g.total_return > rep_n.total_return,
        })
    return pl.DataFrame(rows)


def experiment_sensitivity(cfg: MomentumConfig) -> pl.DataFrame:
    """±1 格敏感性(防悬崖):扰动 休息判据 min_zaban_rest / min_lu_rest,看样本外
    「排除休息」闸门表现是否平滑(阈值一动就塌方 = 过拟合)。"""
    rows = []
    grid = []
    for mzr in (0.40, 0.45, 0.50, 0.55, 0.60):
        grid.append(("min_zaban_rest", mzr, TierThresholds(min_zaban_rest=mzr)))
    for mlr in (10, 15, 20, 25):
        grid.append(("min_lu_rest", mlr, TierThresholds(min_lu_rest=mlr)))
    for name, val, th in grid:
        panel = load_tiered_panel(th)
        g = gate_set(panel, (FULL, HALF))
        n_rest = panel.filter(pl.col("tier") == REST).height
        rep, _ = lab.run_pf(cfg, SAMPLE_OUT_START, SAMPLE_OUT_END, buy_gate=g)
        rows.append({
            "param": name, "value": val, "n_rest_days": n_rest,
            "out_ret": rep.total_return, "out_pf": rep.profit_factor,
            "out_maxdd": rep.max_drawdown, "out_final": rep.final_equity,
        })
    return pl.DataFrame(rows)


if __name__ == "__main__":
    cfg = k1_config()
    panel = load_tiered_panel()
    print(f"K1 config: strength={cfg.strength} buypoint={cfg.buypoint} stop={cfg.stop_pct} "
          f"tp_retrace={cfg.take_profit_retrace} hold={cfg.max_hold_days} "
          f"forbid_high_elasticity={cfg.forbid_high_elasticity}")

    print("\n" + "=" * 70)
    print("B1.3 二值闸门核心实验(对标 P1,同口径)")
    print("=" * 70)
    binexp = experiment_binary(cfg, panel)
    for lbl in ("样本内", "样本外"):
        print(f"\n--- {lbl} ---")
        print(lab.fmt(binexp[lbl]))

    print("\n--- 样本外分层:K1 裸 vs 排除休息闸门 ---")
    print("[K1 裸·按年]");   print(lab.fmt(lab.stratify_by_year(binexp["_pf_naked_out"].closed_trades)))
    print("[K1 裸·按状态]"); print(lab.fmt(lab.stratify_by_state(binexp["_pf_naked_out"].closed_trades)))
    print("[闸门·按年]");    print(lab.fmt(lab.stratify_by_year(binexp["_pf_nonrest_out"].closed_trades)))
    print("[闸门·按状态]");  print(lab.fmt(lab.stratify_by_state(binexp["_pf_nonrest_out"].closed_trades)))

    print("\n" + "=" * 70)
    print("B1.3 第二实验:半额档 sizing 调制")
    print("=" * 70)
    print(lab.fmt(experiment_sizing(cfg, panel)))

    print("\n" + "=" * 70)
    print("B1.3 walk-forward(252 训练 / 126 测试 滚动)")
    print("=" * 70)
    wf = experiment_walk_forward(cfg, panel)
    print(lab.fmt(wf))
    if not wf.is_empty():
        nbetter = int(wf["gate_better_ret"].sum())
        print(f"\n闸门跑赢裸的窗口数:{nbetter}/{wf.height}")

    print("\n" + "=" * 70)
    print("B1.4 ±1 格敏感性(防悬崖,样本外)")
    print("=" * 70)
    print(lab.fmt(experiment_sensitivity(cfg)))
