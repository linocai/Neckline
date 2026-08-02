"""K7 前置 · 战役二 H13:弱势股放量突破(辨识度激活,注意力口径)。

预注册见 `research/k7_pre2_report.md` §2。三座坟对位:C1 = K3 臂④坟对拍(方法学
校准,预期复现负延续);C2 = 星细胞近邻(加放量+突破结构约束);C3 = 地天板 proxy。
判决预期封顶:C2/C3 即便过档 1 也封到档 2(情报标注),除非 2026 注意力量同向为正。

背景轴(承战役三,k3_panel):年线下 = ma250 非空 且 ~(close>ma250 且 ma250_slope_up)。

独立可重跑:`python research/k7p_h13_breakout.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from k7p_common import (  # noqa: E402
    add_k7p_features, attention_table, base_expr, fmt, oneword_event_expr, seg_exprs,
)

K3_PANEL = Path(__file__).resolve().parent / "_cache" / "k3_panel.parquet"

TREND_BELOW = (
    pl.col("ma250").is_not_null()
    & ~((pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up"))
)
HIGH_VOL = pl.col("vol") >= 2 * pl.col("vol_ma20")
DEEP_LOW = (pl.col("low") / pl.col("pre_close") - 1) <= -0.05
BIG_RED = pl.col("ret_1d") >= 0.05


def main() -> None:
    panel = add_k7p_features(pl.read_parquet(K3_PANEL))
    dom = panel.filter(base_expr() & TREND_BELOW)

    c3 = dom.filter(DEEP_LOW & BIG_RED & ~oneword_event_expr())
    c1 = dom.filter(pl.col("is_limit_up") & HIGH_VOL & ~(DEEP_LOW & BIG_RED))
    c2 = dom.filter(
        ~pl.col("is_limit_up") & BIG_RED & HIGH_VOL
        & (pl.col("close") > pl.col("prev_close_max_20d"))
        & ~(DEEP_LOW & BIG_RED) & ~oneword_event_expr()
    )
    ctrl_b = dom.filter((pl.col("ret_1d") >= 0.0) & (pl.col("ret_1d") < 0.05))
    dom_all = dom.filter(~oneword_event_expr())

    groups = [
        ("C1年线下涨停放量(坟对拍)", c1),
        ("C2非涨停放量大红突破", c2),
        ("C3大逆转(地天板proxy)", c3),
        ("对照B年线下平凡红盘", ctrl_b),
        ("对照A年线下域全体", dom_all),
    ]
    print(f"=== 事件数:C1={c1.height} C2={c2.height} C3={c3.height} "
          f"对照B={ctrl_b.height} 域={dom_all.height} ===")
    for seg, expr in seg_exprs():
        print(f"\n-- {seg} --")
        print(fmt(attention_table(groups, expr), intcols=("n", "n_buyable")))


if __name__ == "__main__":
    main()
