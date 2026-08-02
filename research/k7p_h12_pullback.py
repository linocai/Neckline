"""K7 前置 · 战役二 H12:强势股抗跌回调(「龙回头」关注池,注意力口径)。

预注册见 `research/k7_pre2_report.md` §1。雷区合规:战役三裁的是机械买入组合级
期望,本假设审计关注池的注意力价值;结论只进 seeds 关注池/情报标注,不成为买点。

定义(主判档):
    · 强势资格:limitup_count_20d ≥2 且 ret_20d ≥ +25%(敏感性:宽 ≥1&20% / 严 ≥3&30%)
    · 回调态:dist_from_high_20d ∈ [−25%, −8%] 且 close > ma20(敏感性 ma10)
    · 企稳日:vol < vol_ma5 且 ret_1d ∈ [−3%, +2%]
    · 对照 A = 同期域全体;对照 B = 强势资格 × 高位追入带(dist > −3%)

独立可重跑:`python research/k7p_h12_pullback.py`
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lab import get_panel  # noqa: E402
from k7p_common import (  # noqa: E402
    add_k7p_features, attention_table, base_expr, fmt, oneword_event_expr, seg_exprs,
)

STRENGTH_GRID = {
    "宽档(≥1&20%)": (1, 0.20),
    "主判(≥2&25%)": (2, 0.25),
    "严档(≥3&30%)": (3, 0.30),
}
MAIN_STRENGTH = "主判(≥2&25%)"


def strength_expr(min_lu: int, min_ret: float) -> pl.Expr:
    return (pl.col("limitup_count_20d") >= min_lu) & (pl.col("ret_20d") >= min_ret)


def pullback_expr(ma_col: str) -> pl.Expr:
    return (
        (pl.col("dist_from_high_20d") <= -0.08)
        & (pl.col("dist_from_high_20d") >= -0.25)
        & (pl.col("close") > pl.col(ma_col))
    )


CALM_DAY = (pl.col("vol") < pl.col("vol_ma5")) & (pl.col("ret_1d") >= -0.03) & (pl.col("ret_1d") <= 0.02)


def main() -> None:
    panel = add_k7p_features(get_panel())
    dom = panel.filter(base_expr() & ~oneword_event_expr())

    for sname, (min_lu, min_ret) in STRENGTH_GRID.items():
        strong = strength_expr(min_lu, min_ret)
        for ma_col in ("ma20", "ma10"):
            if sname != MAIN_STRENGTH and ma_col != "ma20":
                continue  # 敏感性只动一格
            tag = "主判" if (sname == MAIN_STRENGTH and ma_col == "ma20") else "敏感性"
            pool = dom.filter(strong & pullback_expr(ma_col) & CALM_DAY)
            ctrl_b = dom.filter(strong & (pl.col("dist_from_high_20d") > -0.03))
            groups = [
                (f"龙回头池[{sname}/{ma_col}]", pool),
                ("对照A域全体", dom),
                ("对照B强势高位追入带", ctrl_b),
            ]
            print(f"\n=== [{tag}] 强势档 {sname} × 结构线 {ma_col}:池事件 {pool.height} ===")
            for seg, expr in seg_exprs():
                if tag == "敏感性" and seg != "2026分段":
                    continue
                print(f"\n-- {seg} --")
                print(fmt(attention_table(groups, expr), intcols=("n", "n_buyable")))


if __name__ == "__main__":
    main()
