"""B6 · 汇总裁决书 + K2 候选大脑落库(不激活)。

B6.2 K2 候选大脑落库:`brain.save_version("K2", rule={config,...}, changelog, metrics,
activate=False)` —— **is_active=0 不激活**;落库后**断言 K1 仍是唯一现役**。
B6.3 K2 vs K1 对比裁决书:样本外全指标对照 + 分层 + 推荐意见(写进 k2_report.md)。

**K2 rule.config = B1-B5 定的采纳集**。中心命题(B4)否决后,K2 的诚实内容 = K1 核心选股
不变(情绪 gate / 主线成员 / 追强势 全部削 edge 被否),仅纳入 B5 止盈/高弹的采纳项
(由 B5 结果定,见 K2_CONFIG_OVERRIDES)。K2 定位 = **候选,裁决书推荐「不激活」**。

运行:
    python -m research.b6_finalize --compare      # 只算 K2 vs K1 对照(不落库)
    python -m research.b6_finalize --commit        # 落库 K2(activate=False)+ 断言 K1 现役
"""

from __future__ import annotations

import sys
from datetime import date

import polars as pl

from neckline.strategy import brain
from neckline.strategy.momentum import MomentumConfig
from neckline.research.panel import SAMPLE_IN_START, SAMPLE_IN_END, SAMPLE_OUT_START, SAMPLE_OUT_END
from research import lab
from research.b4_central import build_k2_panel

# —— K2 相对 K1 的采纳改动(B5 定;B4 中心命题否决 → 选股/门控层零采纳)——
# 由 B5 结果填入(见 B5 节裁决)。空 dict = K2 与 K1 完全相同(即「未找到稳健改进」)。
K2_CONFIG_OVERRIDES: dict = {}


def k1_config() -> dict:
    return dict(brain.active_config())


def k2_config() -> dict:
    return {**k1_config(), **K2_CONFIG_OVERRIDES}


def compare_k2_vs_k1(panel: pl.DataFrame) -> dict:
    """K2 vs K1 样本内/外全指标 + 样本外分层(年/市场状态)。"""
    k1 = MomentumConfig(**k1_config())
    k2 = MomentumConfig(**k2_config())
    out = {}
    for wlabel, a, b in [("in", SAMPLE_IN_START, SAMPLE_IN_END), ("out", SAMPLE_OUT_START, SAMPLE_OUT_END)]:
        rows = []
        r1, p1 = lab.run_pf(k1, a, b, panel=panel, buy_gate=None)
        rows.append(lab.summary_row(r1, "K1"))
        r2, p2 = lab.run_pf(k2, a, b, panel=panel, buy_gate=None)
        rows.append(lab.summary_row(r2, "K2候选"))
        out[wlabel] = pl.DataFrame(rows)
        if wlabel == "out":
            out["k1_year"] = lab.stratify_by_year(p1.closed_trades)
            out["k1_state"] = lab.stratify_by_state(p1.closed_trades)
            out["k2_year"] = lab.stratify_by_year(p2.closed_trades)
            out["k2_state"] = lab.stratify_by_state(p2.closed_trades)
            out["_r1_out"], out["_r2_out"] = r1, r2
    return out


def commit_k2(metrics: dict) -> None:
    """落库 K2(activate=False)并断言 K1 仍唯一现役。"""
    changelog = (
        "K2 候选大脑(研究产出,不激活)。中心命题『情绪进攻段×主线成员内追强势有正期望』"
        "经 B1-B4 回测**否决**(印证阶段1均值回归结论,延伸到该子域):情绪gate/主线成员/"
        "追强势三层在样本内组合级灾难(-14%~-72%)、样本外唯一正收益是情绪gate的非平稳"
        "regime效应(非该子域edge)、walk-forward K2输7/10。选股/门控层零采纳。"
        f"K2 config 相对 K1 的改动 = {K2_CONFIG_OVERRIDES or '无(与K1相同)'}(B5 止盈/高弹研究定)。"
        "裁决书推荐:**不激活**,K1 仍为现役。详见 research/k2_report.md。"
    )
    before = {v.version: v.is_active for v in brain.list_versions()}
    assert before.get("K1") is True, f"落库前 K1 应为现役,实际 {before}"

    brain.save_version("K2", rule={"config": k2_config(), "central_proposition": "rejected"},
                       changelog=changelog, metrics=metrics, activate=False)

    versions = {v.version: v.is_active for v in brain.list_versions()}
    assert versions.get("K2") is False, f"K2 必须 is_active=0,实际 {versions}"
    assert versions.get("K1") is True, f"K1 必须仍现役,实际 {versions}"
    active = brain.get_active()
    assert active is not None and active.version == "K1", f"现役必须是 K1,实际 {active.version if active else None}"
    print(f"[落库] K2 已落库 is_active=0;现役仍为 K1。list_versions={versions}")


if __name__ == "__main__":
    panel = build_k2_panel()
    cmp = compare_k2_vs_k1(panel)
    print("=== K2 vs K1 样本内 ==="); print(lab.fmt(cmp["in"]))
    print("=== K2 vs K1 样本外 ==="); print(lab.fmt(cmp["out"]))
    print("=== 样本外分层 K1 按年 ==="); print(lab.fmt(cmp["k1_year"]))
    print("=== 样本外分层 K1 按状态 ==="); print(lab.fmt(cmp["k1_state"]))
    print("=== 样本外分层 K2 按年 ==="); print(lab.fmt(cmp["k2_year"]))
    print("=== 样本外分层 K2 按状态 ==="); print(lab.fmt(cmp["k2_state"]))

    if "--commit" in sys.argv:
        r1, r2 = cmp["_r1_out"], cmp["_r2_out"]
        metrics = {
            "central_proposition": "rejected",
            "sample_out": {
                "K1": {"total_ret": r1.total_return, "pf": r1.profit_factor,
                       "max_dd": r1.max_drawdown, "final_equity": r1.final_equity, "n": r1.n_trades},
                "K2": {"total_ret": r2.total_return, "pf": r2.profit_factor,
                       "max_dd": r2.max_drawdown, "final_equity": r2.final_equity, "n": r2.n_trades},
            },
            "note": "K2 候选研究产出,裁决书推荐不激活;详见 research/k2_report.md",
        }
        commit_k2(metrics)
