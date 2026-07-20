"""阶段 1 组合级研究第二批(一进程内跑,共享 sample-in/out 前复权缓存,省 qfq 重算):
P9 冷却期、P10 仓位纪律 + 次周减半、P1 市场过滤器、P6 尾部检验。

BASE = 退出研究(1.3/P7/P8)定调后的母战法退出基线(见 stage1_report.md P7/P8 节)。
运行:python -m research.phase2
"""

from __future__ import annotations

import polars as pl

from neckline.strategy import signals as S  # noqa: F401 (保留:未来禁买探针可用)
from neckline.strategy.momentum import MomentumConfig
from research import lab

# —— 退出基线(1.3/P7/P8 结论):strength=none + pullback,-5% 止损,hold=5,回落止盈 5%(P7 微优)——
BASE = dict(strength="none", buypoint="pullback", stop_pct=0.05, max_hold_days=5,
            take_profit_retrace=0.05, cooldown_days=0)


def cfg(**kw) -> MomentumConfig:
    d = dict(BASE)
    d.update(kw)
    return MomentumConfig(**d)


def grid(rows, start, end, buy_gate=None):
    out = []
    for label, c in rows:
        rep, _ = lab.run_pf(c, start, end, buy_gate=buy_gate)
        out.append(lab.summary_row(rep, label))
    return pl.DataFrame(out)


def main():
    lab.get_panel()
    S_, E_ = lab.SAMPLE_IN_START, lab.SAMPLE_IN_END
    O_S, O_E = lab.SAMPLE_OUT_START, lab.SAMPLE_OUT_END

    # ---------------- P9 冷却期 ----------------
    print("=" * 78)
    print("P9 冷却期(同票亏损后 N 交易日不再买;样本内)")
    print("=" * 78)
    rows = [(f"cooldown={d}", cfg(cooldown_days=d)) for d in (0, 5, 10, 20)]
    print(lab.fmt(grid(rows, S_, E_)))

    # ---------------- P10 仓位纪律 ----------------
    print("\n" + "=" * 78)
    print("P10-A 最多持仓只数(单笔 2 万固定;样本内)")
    print("=" * 78)
    rows = [(f"max_pos={n}", cfg(max_positions=n)) for n in (3, 5, 8, 10)]
    print(lab.fmt(grid(rows, S_, E_)))

    print("\n" + "=" * 78)
    print("P10-B 总敞口上限(样本内)")
    print("=" * 78)
    rows = [(f"exposure={e:.0%}", cfg(max_exposure_frac=e)) for e in (0.40, 0.60, 0.80, 1.00)]
    print(lab.fmt(grid(rows, S_, E_)))

    print("\n" + "=" * 78)
    print("P10-C 单笔上限(样本内)")
    print("=" * 78)
    rows = [(f"single_cap={int(v)}", cfg(single_cap=v)) for v in (10000, 20000, 40000)]
    print(lab.fmt(grid(rows, S_, E_)))

    print("\n" + "=" * 78)
    print("P10-D 次周单笔减半(挂起项;样本内+外)。阈值=单周实现亏损≥5%×初始资金")
    print("=" * 78)
    rows_in = [("基线(不减半)", cfg()), ("次周减半", cfg(week_halving=True))]
    print("[样本内]"); print(lab.fmt(grid(rows_in, S_, E_)))
    print("[样本外]"); print(lab.fmt(grid(rows_in, O_S, O_E)))

    # ---------------- P1 市场过滤器 ----------------
    print("\n" + "=" * 78)
    print("P1 市场过滤器(只在上证>MA20 开新仓 vs 全天开仓;样本内+外)")
    print("=" * 78)
    gate = lab.bull_days()
    rep_in_off, pf_in_off = lab.run_pf(cfg(), S_, E_)
    rep_in_on, pf_in_on = lab.run_pf(cfg(), S_, E_, buy_gate=gate)
    rep_out_off, _ = lab.run_pf(cfg(), O_S, O_E)
    rep_out_on, _ = lab.run_pf(cfg(), O_S, O_E, buy_gate=gate)
    tbl = pl.DataFrame([
        lab.summary_row(rep_in_off, "样本内·全天开仓"),
        lab.summary_row(rep_in_on, "样本内·仅MA20上方"),
        lab.summary_row(rep_out_off, "样本外·全天开仓"),
        lab.summary_row(rep_out_on, "样本外·仅MA20上方"),
    ])
    print(lab.fmt(tbl))
    print("\n[P1 分层] 全天开仓(样本内)按市场状态的回合:")
    print(lab.fmt(lab.stratify_by_state(pf_in_off.closed_trades)))

    # ---------------- P6 尾部检验(组合级 max_dd / 最差回合) ----------------
    print("\n" + "=" * 78)
    print("P6 尾部检验(高弹/次新黑名单对 max_dd 与最差回合的影响;样本内)")
    print("=" * 78)
    rows = [("基线(不禁)", cfg()),
            ("禁高弹题材", cfg(forbid_high_elasticity=True)),
            ("禁次新<120日", cfg(forbid_new_days=120)),
            ("禁高弹+次新", cfg(forbid_high_elasticity=True, forbid_new_days=120))]
    out = []
    for label, c in rows:
        rep, pf = lab.run_pf(c, S_, E_)
        worst = min((t.pnl_pct for t in pf.closed_trades), default=float("nan"))
        r = lab.summary_row(rep, label)
        r["worst_trade_pct"] = worst
        out.append(r)
    print(lab.fmt(pl.DataFrame(out)))


if __name__ == "__main__":
    main()
