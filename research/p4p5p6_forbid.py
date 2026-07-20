"""P4/P5/P6 禁买规则边际(plan 1.2 / §6)。绿盘大阴线禁买线(P4)、距 20 日高点阈值
(P5)、票型黑名单(P6 次新 / 高弹题材)。每项 with/without 对照,信号级事件研究
(快、可比)。基信号 = 全域选股域(不叠强势,单看禁买规则对「买入后收益」的边际,
避免与 P3 强势筛选混淆)。

运行:python -m research.p4p5p6_forbid
"""

from __future__ import annotations

import polars as pl

from neckline.research import eventstudy as ES
from neckline.research.panel import base_universe_expr, in_sample, out_sample
from neckline.strategy import signals as S
from research import lab


def main():
    panel = lab.get_panel()
    pin = in_sample(panel)
    pout = out_sample(panel)
    base = base_universe_expr()

    # ---- P4 绿盘大阴线禁买线:粗网格 -2%/-3%/-4%/-5% ----
    print("=" * 78)
    print("P4 绿盘大阴线禁买(样本内,hold=3)。基线=全域;各行=剔除当日跌幅≤阈值后")
    print("=" * 78)
    p4 = {"全域(不禁)": base}
    for thr in (-0.02, -0.03, -0.04, -0.05):
        p4[f"禁ret1d<={thr:.0%}"] = base & ~S.forbid_green_bigdown(thr)
    # 反向:被禁掉的那批(绿盘大阴线本身)前瞻收益——验证「买绿盘大阴线是死因」
    p4["【被禁】ret1d<=-3%"] = base & S.forbid_green_bigdown(-0.03)
    print(ES.fmt_table(ES.compare_signals(pin, p4, hold_days=(3,))))

    # ---- P5 距 20 日高点阈值:-10%/-15%/-20%/-25% ----
    print("\n" + "=" * 78)
    print("P5 距 20 日高点过远禁买(样本内,hold=3)")
    print("=" * 78)
    p5 = {"全域(不禁)": base}
    for thr in (-0.10, -0.15, -0.20, -0.25):
        p5[f"禁dist<={thr:.0%}"] = base & ~S.forbid_far_from_high(thr)
    p5["【被禁】dist<=-15%"] = base & S.forbid_far_from_high(-0.15)
    print(ES.fmt_table(ES.compare_signals(pin, p5, hold_days=(3,))))

    # ---- P6 票型黑名单:次新(不同天数)/ 高弹题材(创科北) ----
    print("\n" + "=" * 78)
    print("P6 票型黑名单(样本内,hold=3)。次新 / 高弹题材 with/without")
    print("=" * 78)
    p6 = {"全域(不禁)": base}
    for days in (60, 120, 250):
        p6[f"禁次新<{days}日"] = base & ~S.forbid_new_stock(days)
    p6["禁高弹(创科北)"] = base & ~S.forbid_high_elasticity()
    p6["【被禁】次新<120日"] = base & S.forbid_new_stock(120)
    p6["【被禁】高弹题材"] = base & S.forbid_high_elasticity()
    print(ES.fmt_table(ES.compare_signals(pin, p6, hold_days=(3,))))

    # ---- 组合:三禁全开 vs 全域(样本内 + 样本外)----
    print("\n" + "=" * 78)
    print("P4+P5+P6 三禁全开 vs 全域(样本内 & 样本外,hold=3)")
    print("=" * 78)
    all_forbid = (
        base & ~S.forbid_green_bigdown(-0.03) & ~S.forbid_far_from_high(-0.15)
        & ~S.forbid_new_stock(120) & ~S.forbid_high_elasticity()
    )
    combo = {"全域": base, "三禁全开": all_forbid}
    print("[样本内]")
    print(ES.fmt_table(ES.compare_signals(pin, combo, hold_days=(3,))))
    print("[样本外]")
    print(ES.fmt_table(ES.compare_signals(pout, combo, hold_days=(3,))))

    # ---- 分层:三禁全开 vs 全域 按年(全期)----
    print("\n" + "=" * 78)
    print("三禁全开 按年分层(全期,hold=3)")
    print("=" * 78)
    print(ES.fmt_table(ES.event_study_grouped(panel, all_forbid, "year", hold_days=(3,))))


if __name__ == "__main__":
    main()
