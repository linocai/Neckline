"""B5 · 止盈三方案赛马 + 高弹题材风险预算化(在 B4 定的 K2 票池 & 门控下 + K1 框架下)。

B5.1/5.2 止盈三方案(默认关闭字段,已在 momentum.py 落地):
  A 固定 +15% 落袋(take_profit_fixed=0.15)
  B 回落止盈 5%(K1 现行:take_profit_retrace=0.05)
  C 混合(both:+15% 前用回落 5%、触 15% 即走)
先验:用户历史最大四笔盈利全在 +14~17% 了结——若三方案差距不大,尊重用户风格选固定
+15%(如实报告差距量级,不为微小差异否定用户偏好)。

B5.3 高弹题材风险预算化:黑名单一刀切(forbid_high_elasticity=True,K1)vs 减半参与
(high_elasticity_half=True,单笔 ×0.5 建仓而非剔除)。指标:期望/回撤 + **对次日跌停
类事件暴露**(买入次日 is_limit_down,对标立项归因 7 笔买入次日跌停)。

运行:python -m research.b5_exits
"""

from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional, Set

import polars as pl

from neckline.strategy import brain
from neckline.strategy.momentum import MomentumConfig
from neckline.calendar import next_trading_day
from neckline.research.panel import SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END
from research import lab
from research.b4_central import build_k2_panel, attack_gate

# B4 采纳的 K2 票池强势构型(由 B4 结果定;缺省 limitup_gene——B4 若否决,B5 仍在该子域
# 内比止盈机制,结论进 B6.4 降级建议)。
K2_STRENGTH = "limitup_gene"


def _cfg(**over) -> MomentumConfig:
    return MomentumConfig(**{**brain.active_config(), **over})


def _winner_exit_stats(trades) -> Dict[str, float]:
    """盈利回合的了结点分布(平均/中位 pnl_pct;+14~17% 命中率)。"""
    pos = [t.pnl_pct for t in trades if t.pnl > 0]
    if not pos:
        return {"n_win": 0, "avg_win_pct": float("nan"), "med_win_pct": float("nan"), "in_14_17_frac": float("nan")}
    s = pl.Series(pos)
    in_band = float(((s >= 0.14) & (s <= 0.17)).sum()) / len(pos)
    return {"n_win": len(pos), "avg_win_pct": float(s.mean()), "med_win_pct": float(s.median()),
            "in_14_17_frac": in_band}


def take_profit_race(panel: pl.DataFrame, pool: str, window: str) -> pl.DataFrame:
    """pool='K2'(满额gate+member+强势) 或 'K1'(裸 K1,无gate无member);比三止盈方案。"""
    a, b = (SAMPLE_IN_START, SAMPLE_IN_END) if window == "in" else (SAMPLE_OUT_START, SAMPLE_OUT_END)
    gate = attack_gate("full") if pool == "K2" else None
    base = dict(strength=K2_STRENGTH, require_mainline_member=True) if pool == "K2" else dict()
    schemes = {
        "A 固定+15%": {"take_profit_retrace": None, "take_profit_fixed": 0.15},
        "B 回落5%(K1现行)": {"take_profit_retrace": 0.05, "take_profit_fixed": None},
        "C 混合(回落5%+固定15%)": {"take_profit_retrace": 0.05, "take_profit_fixed": 0.15},
    }
    rows = []
    for name, sc in schemes.items():
        cfg = _cfg(**base, **sc)
        rep, pf = lab.run_pf(cfg, a, b, panel=panel, buy_gate=gate)
        ws = _winner_exit_stats(pf.closed_trades)
        rows.append({"pool": pool, "scheme": name, "n_trades": rep.n_trades,
                     "total_ret": rep.total_return, "pf": rep.profit_factor,
                     "max_dd": rep.max_drawdown, "win_rate": rep.win_rate,
                     "avg_win_pct": ws["avg_win_pct"], "in14_17": ws["in_14_17_frac"]})
    return pl.DataFrame(rows)


# ======================================================================
#  B5.3 高弹题材风险预算化
# ======================================================================

def _next_day_limitdown_exposure(trades, ld_lookup: Dict) -> float:
    """买入次日 is_limit_down 的回合占比(次日跌停暴露)。"""
    if not trades:
        return float("nan")
    hit = 0
    for t in trades:
        nd = next_trading_day(t.buy_date)
        if ld_lookup.get((t.ts_code, nd), False):
            hit += 1
    return hit / len(trades)


def high_elasticity_budget(panel: pl.DataFrame, window: str) -> pl.DataFrame:
    """黑名单一刀切 vs 减半参与。用 K1 框架(无 gate/member,隔离高弹处置本身)。"""
    a, b = (SAMPLE_IN_START, SAMPLE_IN_END) if window == "in" else (SAMPLE_OUT_START, SAMPLE_OUT_END)
    # 次日跌停查找表(用面板 is_limit_down)
    ld = panel.filter(pl.col("is_limit_down")).select(["ts_code", "trade_date"])
    ld_lookup = {(r["ts_code"], r["trade_date"]): True for r in ld.iter_rows(named=True)}
    variants = {
        "黑名单一刀切(K1)": dict(forbid_high_elasticity=True, high_elasticity_half=False),
        "全额参与": dict(forbid_high_elasticity=False, high_elasticity_half=False),
        "减半参与": dict(forbid_high_elasticity=False, high_elasticity_half=True),
    }
    rows = []
    for name, ov in variants.items():
        cfg = _cfg(**ov)
        rep, pf = lab.run_pf(cfg, a, b, panel=panel, buy_gate=None)
        exp = _next_day_limitdown_exposure(pf.closed_trades, ld_lookup)
        rows.append({"variant": name, "n_trades": rep.n_trades, "total_ret": rep.total_return,
                     "pf": rep.profit_factor, "max_dd": rep.max_drawdown,
                     "next_day_limitdown_frac": exp})
    return pl.DataFrame(rows)


if __name__ == "__main__":
    panel = build_k2_panel()
    print("=" * 70 + "\nB5.2 止盈三方案赛马(K2 票池:满额gate+主线成员+" + K2_STRENGTH + ")\n" + "=" * 70)
    for w in ("in", "out"):
        print(f"\n--- {'样本内' if w=='in' else '样本外'} ---")
        print(lab.fmt(take_profit_race(panel, "K2", w)))
    print("\n" + "=" * 70 + "\nB5.2 止盈三方案赛马(K1 裸池,贴近用户实盘 + 先验对照)\n" + "=" * 70)
    for w in ("in", "out"):
        print(f"\n--- {'样本内' if w=='in' else '样本外'} ---")
        print(lab.fmt(take_profit_race(panel, "K1", w)))
    print("\n" + "=" * 70 + "\nB5.3 高弹题材风险预算化(K1 框架,隔离高弹处置)\n" + "=" * 70)
    for w in ("in", "out"):
        print(f"\n--- {'样本内' if w=='in' else '样本外'} ---")
        print(lab.fmt(high_elasticity_budget(panel, w)))
