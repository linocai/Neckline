"""1.3 止损验证 + 1.4 止盈/时间退出(§6 P7/P8)。组合级回测,母战法基线 =
strength=none + pullback(P3 结论:强势筛选削 edge,用最不坏的选股基线隔离退出效应)。
无市场过滤(P1 单列)、无禁买(P4-6 单列)、无冷却(P9 单列),只动退出参数。

网格(粗):
  · 1.3 止损:stop_pct ∈ {None,3%,5%,8%,10%},hold=3,无止盈
  · P8 时间退出:hold ∈ {2,3,4,5},stop=5%,无止盈
  · P7 回落止盈:retrace ∈ {None,5%,8%,10%,15%},stop=5%,hold=5(给回落止盈作用空间)
样本内定调;选定值样本外另跑(honest,只看有限次)。

运行:python -m research.p7p8_exits
"""

from __future__ import annotations

import polars as pl

from neckline.strategy.momentum import MomentumConfig
from research import lab


def base(**kw) -> MomentumConfig:
    d = dict(strength="none", buypoint="pullback", cooldown_days=0,
             stop_pct=0.05, take_profit_retrace=None, max_hold_days=3)
    d.update(kw)
    return MomentumConfig(**d)


def grid(configs, start, end):
    rows = []
    for label, cfg in configs:
        rep, _ = lab.run_pf(cfg, start, end)
        rows.append(lab.summary_row(rep, label))
    return pl.DataFrame(rows)


def main():
    lab.get_panel()
    S, E = lab.SAMPLE_IN_START, lab.SAMPLE_IN_END

    print("=" * 78)
    print("1.3 止损线验证(样本内 2020-2024,hold=3,无止盈)")
    print("=" * 78)
    stops = [("无止损", base(stop_pct=None)),
             ("止损-3%", base(stop_pct=0.03)),
             ("止损-5%", base(stop_pct=0.05)),
             ("止损-8%", base(stop_pct=0.08)),
             ("止损-10%", base(stop_pct=0.10))]
    print(lab.fmt(grid(stops, S, E)))

    print("\n" + "=" * 78)
    print("P8 时间退出天数(样本内,stop=-5%,无止盈)")
    print("=" * 78)
    holds = [(f"hold={h}", base(max_hold_days=h)) for h in (2, 3, 4, 5)]
    print(lab.fmt(grid(holds, S, E)))

    print("\n" + "=" * 78)
    print("P7 回落止盈(样本内,stop=-5%,hold=5)")
    print("=" * 78)
    tps = [("无止盈", base(max_hold_days=5, take_profit_retrace=None)),
           ("回落-5%", base(max_hold_days=5, take_profit_retrace=0.05)),
           ("回落-8%", base(max_hold_days=5, take_profit_retrace=0.08)),
           ("回落-10%", base(max_hold_days=5, take_profit_retrace=0.10)),
           ("回落-15%", base(max_hold_days=5, take_profit_retrace=0.15))]
    print(lab.fmt(grid(tps, S, E)))

    print("\n" + "=" * 78)
    print("退出基线(none+pullback,stop-5%,hold3)分层:按年 / 按市场状态(样本内)")
    print("=" * 78)
    rep, pfolio = lab.run_pf(base(), S, E)
    print("[按年]")
    print(lab.fmt(lab.stratify_by_year(pfolio.closed_trades)))
    print("[按市场状态]")
    print(lab.fmt(lab.stratify_by_state(pfolio.closed_trades)))


if __name__ == "__main__":
    main()
