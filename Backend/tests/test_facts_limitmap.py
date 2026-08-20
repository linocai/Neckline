"""涨停分布与涨停簇单测(V2.5.0 S3,PROJECT_PLAN §5.3.1「市场级读数」)。

**本文件在 S3 被整体重写**。S1 版测的是 K8 口径的 `limit_cluster_daily`
(按 `stock_basic.industry` 旧 110 行业 + 概念板块两个锚点、upsert 落表)。
S3 按裁定 3 切到申万二级、按 K9 §3.0 砍掉概念锚点、按 §5.3.1 改为纯函数不落表。

覆盖:
    ① 簇锚在**申万二级**(⛔ 不是 `stock_basic.industry`,⛔ 不是概念板块);
    ② 连板簇是同日簇的子集(`consec_limit_up_days >= 2`);
    ③ 孤身涨停不成簇(`MIN_CLUSTER_SIZE`);
    ④ 查无申万归属的票不参与聚类(⛔ 不凑一个「其它」簇);
    ⑤ 炸板率的分母 0 → `None`(⛔ 不是 0);
    ⑥ 缺列当场抛(⛔ 不降级成「今天没涨停」);
    ⑦ 纯函数:同输入两次调用逐位相同,且**零 I/O**。
"""

from __future__ import annotations

import polars as pl
import pytest

from neckline.facts import limitmap


def _row(
    code: str,
    l2_code: str | None,
    *,
    up: bool = False,
    down: bool = False,
    zaban: bool = False,
    consec: int = 0,
    board: str = "MAIN",
) -> dict:
    return {
        "ts_code": code,
        "board": board,
        "sw_l2_code": l2_code,
        "sw_l2_name": None if l2_code is None else f"名-{l2_code}",
        "is_limit_up": up,
        "is_limit_down": down,
        "is_limit_open": zaban,
        "consec_limit_up_days": consec,
    }


def _frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema={
        "ts_code": pl.String, "board": pl.String,
        "sw_l2_code": pl.String, "sw_l2_name": pl.String,
        "is_limit_up": pl.Boolean, "is_limit_down": pl.Boolean,
        "is_limit_open": pl.Boolean, "consec_limit_up_days": pl.Int64,
    })


# ══════════════════════════════════════════════════════════════════════════
# ① 簇锚在申万二级
# ══════════════════════════════════════════════════════════════════════════

def test_clusters_are_anchored_on_sw_l2_not_legacy_industry():
    """🔴 遗留 2 的机器判据:涨停簇必须按**申万二级代码**分组。

    这条要紧是因为 **S4 覆盖率线以涨停为口径** —— 归因落在旧的 110 行业分类上,
    「漏掉的是哪一类票」整条结论就是错的(裁定 3)。"""
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=1),
        _row("600002.SH", "801080.SI", up=True, consec=1),
        _row("600003.SH", "801125.SI", up=True, consec=1),
    ]))
    same_day = [c for c in out.clusters if c.kind == limitmap.SAME_DAY]
    assert [c.l2_code for c in same_day] == ["801080.SI"]
    assert same_day[0].members == ("600001.SH", "600002.SH")
    assert same_day[0].l2_name == "名-801080.SI"


def test_concept_boards_are_gone_from_the_module_entirely():
    """K9 §3.0 / 架构 §3.1:**概念板块不进入任何机械计算**。S1 版本里的
    `concept_membership_map` / `anchor_concept` 必须物理消失,⛔ 不留兼容入口。"""
    for gone in ("concept_membership_map", "cluster_members_by_anchor",
                 "make_cluster_key", "refresh_limit_clusters", "load_limit_clusters", "TABLE"):
        assert not hasattr(limitmap, gone), f"{gone} 还在 —— 概念锚点/落表路径没删干净"
    src = (limitmap.__doc__ or "") + "".join(
        (getattr(limitmap, n).__doc__ or "") for n in limitmap.__all__ if hasattr(limitmap, n)
    )
    assert "anchor_concept" not in src


# ══════════════════════════════════════════════════════════════════════════
# ② 连板簇是同日簇的子集
# ══════════════════════════════════════════════════════════════════════════

def test_consecutive_cluster_is_a_subset_of_same_day():
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=3),
        _row("600002.SH", "801080.SI", up=True, consec=2),
        _row("600003.SH", "801080.SI", up=True, consec=1),   # 首板,不进连板簇
    ]))
    same = {c.l2_code: c for c in out.clusters if c.kind == limitmap.SAME_DAY}
    consec = {c.l2_code: c for c in out.clusters if c.kind == limitmap.CONSECUTIVE}
    assert same["801080.SI"].size == 3
    assert consec["801080.SI"].size == 2
    assert set(consec["801080.SI"].members) < set(same["801080.SI"].members)
    assert consec["801080.SI"].max_consec_days == 3
    assert out.max_consec_days == 3
    assert out.consec_histogram == {1: 1, 2: 1, 3: 1}


def test_single_consecutive_stock_does_not_form_a_cluster():
    """一只连板票不构成「接力」:同日簇成立(3 只同行业),连板簇不成立(只 1 只)。"""
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=2),
        _row("600002.SH", "801080.SI", up=True, consec=1),
        _row("600003.SH", "801080.SI", up=True, consec=1),
    ]))
    assert [c.kind for c in out.clusters] == [limitmap.SAME_DAY]


# ══════════════════════════════════════════════════════════════════════════
# ③④ 门槛与「查无该行业」
# ══════════════════════════════════════════════════════════════════════════

def test_lone_limit_up_is_not_a_cluster():
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=1),
        _row("600004.SH", "801125.SI", up=True, consec=1),
    ]))
    assert out.clusters == ()
    assert out.limit_up_count == 2


def test_stocks_without_sw_membership_never_form_an_other_bucket():
    """「查无行业」不是一个行业。⛔ 不许把它们凑成一个「其它」簇 —— 那会在覆盖率
    归因里冒出一个不存在的题材。"""
    out = limitmap.compute(_frame([
        _row("600001.SH", None, up=True, consec=1),
        _row("600002.SH", None, up=True, consec=1),
        _row("600003.SH", "", up=True, consec=1),
    ]))
    assert out.clusters == ()
    assert out.limit_up_count == 3


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 炸板率:分母 0 → None(⛔ 不是 0)
# ══════════════════════════════════════════════════════════════════════════

def test_zaban_rate_is_none_when_nothing_could_have_broken():
    out = limitmap.compute(_frame([_row("600001.SH", "801080.SI")]))
    assert out.zaban_rate is None, "「没炸」与「没得炸」是两件事,⛔ 不许写 0"
    assert out.max_consec_days is None
    assert out.limit_up_count == 0


def test_zaban_rate_counts_limit_up_plus_zaban_as_denominator():
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=1),
        _row("600002.SH", "801080.SI", up=True, consec=1),
        _row("600003.SH", "801080.SI", zaban=True),
        _row("600004.SH", "801080.SI", down=True),
    ]))
    assert out.zaban_rate == pytest.approx(1 / 3)
    assert out.limit_down_count == 1
    assert out.by_board["MAIN"] == {"limitUp": 2, "limitDown": 1, "zaban": 1}


def test_board_split_is_reported_separately():
    out = limitmap.compute(_frame([
        _row("600001.SH", "801080.SI", up=True, consec=1, board="MAIN"),
        _row("300001.SZ", "801080.SI", up=True, consec=1, board="GEM"),
    ]))
    assert set(out.by_board) == {"MAIN", "GEM"}
    assert out.by_board["GEM"]["limitUp"] == 1


# ══════════════════════════════════════════════════════════════════════════
# ⑥⑦ 缺列当场抛;纯函数
# ══════════════════════════════════════════════════════════════════════════

def test_missing_column_raises_instead_of_pretending_a_quiet_market():
    df = _frame([_row("600001.SH", "801080.SI", up=True, consec=1)]).drop("sw_l2_code")
    with pytest.raises(ValueError, match="缺列"):
        limitmap.compute(df)


def test_compute_is_pure_and_reproducible():
    rows = _frame([
        _row("600002.SH", "801080.SI", up=True, consec=2),
        _row("600001.SH", "801080.SI", up=True, consec=1),
    ])
    a, b = limitmap.compute(rows), limitmap.compute(rows)
    assert a == b
    assert a.to_dict() == b.to_dict()
    # 成员按 ts_code 升序,与入参行序无关(可复现)
    assert a.clusters[0].members == ("600001.SH", "600002.SH")


def test_empty_frame_degrades_to_an_honest_zero_map():
    out = limitmap.compute(pl.DataFrame())
    assert out.limit_up_count == 0 and out.clusters == () and out.zaban_rate is None
