"""P3 强势定义赛马(plan 1.1 / §6 P3)。三候选:涨停基因 / 20 日涨幅(绝对+分位) /
量价结构,单独 & 叠加两类买点,信号级事件研究(样本内定调)+ 组合级对照。

复现并延伸首轮结论(45dc6b4):追强势短线在此频率是否有净 edge?诚实报告。
运行:python -m research.p3_strength
"""

from __future__ import annotations

import polars as pl

from neckline.research import eventstudy as ES
from neckline.research.panel import base_universe_expr, in_sample, out_sample
from neckline.strategy import signals as S
from neckline.strategy.momentum import MomentumConfig
from research import lab

pl.Config.set_tbl_rows(50)


def strength_exprs():
    base = base_universe_expr()
    return {
        "base_universe(全域)": base,
        "limitup_gene>=1": base & S.strength_limitup_gene(1),
        "ret20>=0.15": base & S.strength_ret_rank(0.15),
        "ret20_pct>=0.90": base & S.strength_ret_rank_pct(0.90),
        "volprice": base & S.strength_volprice(),
    }


def strength_x_buy():
    base = base_universe_expr()
    out = {}
    for sname, sexpr in [
        ("limitup_gene", S.strength_limitup_gene(1)),
        ("ret20", S.strength_ret_rank(0.15)),
        ("ret20pct", S.strength_ret_rank_pct(0.90)),
        ("volprice", S.strength_volprice()),
    ]:
        out[f"{sname}+pullback"] = base & sexpr & S.buy_pullback()
        out[f"{sname}+breakout"] = base & sexpr & S.buy_breakout(1.5)
    return out


def main():
    panel = lab.get_panel()
    pin = in_sample(panel)
    pout = out_sample(panel)

    print("=" * 78)
    print("P3-A 强势定义单独(样本内 2020-2024,信号级事件研究,net 扣双边~30bp)")
    print("=" * 78)
    print(ES.fmt_table(ES.compare_signals(pin, strength_exprs(), hold_days=(3,))))

    print("\n" + "=" * 78)
    print("P3-B 强势×买点(样本内,hold=3)")
    print("=" * 78)
    print(ES.fmt_table(ES.compare_signals(pin, strength_x_buy(), hold_days=(3,))))

    print("\n" + "=" * 78)
    print("P3-C 各强势定义 hold 1-5 天曲线(样本内,volprice+pullback vs 全域)")
    print("=" * 78)
    base = base_universe_expr()
    curve = {
        "全域": base,
        "volprice+pullback": base & S.strength_volprice() & S.buy_pullback(),
        "ret20pct+pullback": base & S.strength_ret_rank_pct(0.90) & S.buy_pullback(),
    }
    print(ES.fmt_table(ES.compare_signals(pin, curve, hold_days=(1, 2, 3, 4, 5))))

    print("\n" + "=" * 78)
    print("P3-D 分层:volprice+pullback 按年(样本内+外,hold=3)")
    print("=" * 78)
    sig = base & S.strength_volprice() & S.buy_pullback()
    print(ES.fmt_table(ES.event_study_grouped(panel, sig, "year", hold_days=(3,))))

    print("\n" + "=" * 78)
    print("P3-E 分层:volprice+pullback 按市场状态(全期,hold=3)")
    print("=" * 78)
    print(ES.fmt_table(ES.event_study_grouped(panel, sig, "sse_above_ma", hold_days=(3,))))

    print("\n" + "=" * 78)
    print("P3-F 样本外验证(2025-2026,hold=3):强势定义单独")
    print("=" * 78)
    print(ES.fmt_table(ES.compare_signals(pout, strength_exprs(), hold_days=(3,))))

    print("\n" + "=" * 78)
    print("P3-G 组合级对照(样本内 2020-2024,母战法退出默认:-5%止损/hold3/无止盈)")
    print("=" * 78)
    rows = []
    for name, strength in [("none(全域)", "none"), ("limitup_gene", "limitup_gene"),
                           ("ret20", "ret20"), ("ret20_pct", "ret20_pct"), ("volprice", "volprice")]:
        cfg = MomentumConfig(strength=strength, buypoint="pullback")
        rep, pf = lab.run_pf(cfg, lab.SAMPLE_IN_START, lab.SAMPLE_IN_END)
        rows.append(lab.summary_row(rep, name + "+pullback"))
    print(lab.fmt(pl.DataFrame(rows)))


if __name__ == "__main__":
    main()
