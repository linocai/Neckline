"""⑦-K7 成员标注件 `neckline/selection/member_tags.py` 单测(plan §五 V2-⑦ 验收
「⑦-K7 追加三条」逐条)。

覆盖:
    ① **三个标签各一条命中 / 不命中** + **缺数据 `absent`**(不写 False 冒充
       「已判定为否」)。
    ② **四不守门**:标签码不出现在 `_TIER_SCORE_INPUTS` / 原语特征白名单 / 任何
       包的 `tier.dims` / `neckline/sentinel/` 全目录;且**打标前后 Tier 序与成员
       去留逐位不变**(同一份数据跑两遍,一遍关标注一遍开标注)。
    ③ **文案每处带「参考、非指令」**(渲染层 grep + 单一源后缀)。
另加:判据口径与 `research/k7p_h12_pullback.py` 预注册定义逐条对齐;
`streak_top_flags` 的并列 / 多簇 / `limit_height<1` 三条约定;两条取数路径
(注入 vs 自读)产出一致 —— ⑬-N 信息卡交叉断言的地基。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl
import pytest

from neckline.selection import aggregate as ag
from neckline.selection import basket_card as bc
from neckline.selection import member_tags as mt
from neckline.selection import primitives as prim
from neckline.selection import tier as ti

D0 = date(2024, 4, 8)
D0_S = "20240408"
_REPO = Path(__file__).resolve().parent.parent
_PACKS_DIR = _REPO / "packs"


def _row(**over: Any) -> Dict[str, Any]:
    """一只**同时满足强势资格 + 回调态 + 企稳日**的票(= 龙回头位命中基线)。
    各用例只覆盖它的一个键,便于逐条证伪。"""
    base = {
        "ts_code": "600000.SH",
        "limitup_count_20d": 3,      # ≥2
        "ret_20d": 0.30,             # ≥ +25%
        "dist_from_high_20d": -0.12,  # ∈ [−25%, −8%]
        "close": 10.0,
        "ma20": 9.0,                 # close > ma20
        "vol": 800.0,
        "vol_ma5": 1000.0,           # vol < vol_ma5(缩量)
        "ret_1d": 0.005,             # ∈ [−3%, +2%]
        "streak_top": False,
    }
    base.update(over)
    return base


# ══════════════════════════════════════════════════════════════════════════
# ① 三个标签:命中 / 不命中 / 缺数据
# ══════════════════════════════════════════════════════════════════════════

def test_pullback_leader_hit():
    r = mt.evaluate_member_tags(_row())
    assert mt.TAG_PULLBACK_LEADER in r.codes()
    assert mt.TAG_PULLBACK_LEADER not in r.absent


@pytest.mark.parametrize("over,why", [
    ({"limitup_count_20d": 1}, "强势资格:近20日涨停不足2次"),
    ({"ret_20d": 0.20}, "强势资格:ret_20d 不足 +25%"),
    ({"dist_from_high_20d": -0.05}, "回调态:回调太浅(还在追入带)"),
    ({"dist_from_high_20d": -0.40}, "回调态:回调过深"),
    ({"ma20": 10.5}, "回调态:close 未站上 ma20(结构已破)"),
    ({"vol": 1200.0}, "企稳日:放量不是缩量"),
    ({"ret_1d": -0.06}, "企稳日:大阴线"),
    ({"ret_1d": 0.05}, "企稳日:大阳线"),
])
def test_pullback_leader_miss(over, why):
    r = mt.evaluate_member_tags(_row(**over))
    assert mt.TAG_PULLBACK_LEADER not in r.codes(), why
    # **不命中 ≠ 缺数据**:数据齐全时落 missed,不落 absent。
    assert mt.TAG_PULLBACK_LEADER in r.missed
    assert mt.TAG_PULLBACK_LEADER not in r.absent


@pytest.mark.parametrize("boundary,expect", [
    ({"limitup_count_20d": 2, "ret_20d": 0.25}, True),          # 两个下界都恰好取等
    ({"dist_from_high_20d": -0.08}, True),                       # 回调带上界取等
    ({"dist_from_high_20d": -0.25}, True),                       # 回调带下界取等
    ({"ret_1d": -0.03}, True),                                   # 企稳带下界取等
    ({"ret_1d": 0.02}, True),                                    # 企稳带上界取等
    ({"ret_1d": 0.2 - 0.18}, True),                              # 0.02 的浮点噪声版
])
def test_pullback_leader_boundaries_are_inclusive(boundary, expect):
    """边界一律取等(judgement 阈值比较带 `_EPS` 容差 —— CLAUDE.md「纪律阈值比较
    一律加容差」;`0.2-0.18` 在二进制浮点下 ≠ 0.02,裸 `<=` 会漏判)。"""
    r = mt.evaluate_member_tags(_row(**boundary))
    assert (mt.TAG_PULLBACK_LEADER in r.codes()) is expect


def test_chase_zone_hit_and_miss():
    hit = mt.evaluate_member_tags(_row(dist_from_high_20d=-0.01))
    assert mt.TAG_WARN_CHASE_ZONE in hit.codes()
    # 同一只票不可能既是回调位又是追入位 —— 判据互斥,这里顺手锁死。
    assert mt.TAG_PULLBACK_LEADER not in hit.codes()

    miss = mt.evaluate_member_tags(_row(dist_from_high_20d=-0.12))
    assert mt.TAG_WARN_CHASE_ZONE in miss.missed

    # 不强势 → 贴着前高也不打这个标(它是「**强势**且贴高」)。
    weak = mt.evaluate_member_tags(_row(ret_20d=0.01, dist_from_high_20d=-0.01))
    assert mt.TAG_WARN_CHASE_ZONE in weak.missed


def test_streak_top_hit_miss_absent():
    assert mt.TAG_WARN_STREAK_TOP in mt.evaluate_member_tags(_row(streak_top=True)).codes()
    assert mt.TAG_WARN_STREAK_TOP in mt.evaluate_member_tags(_row(streak_top=False)).missed
    r = mt.evaluate_member_tags(_row(streak_top=None))
    assert mt.TAG_WARN_STREAK_TOP in r.absent
    assert mt.TAG_WARN_STREAK_TOP not in r.missed


@pytest.mark.parametrize("missing", [
    "limitup_count_20d", "ret_20d", "dist_from_high_20d", "close", "ma20",
    "vol", "vol_ma5", "ret_1d",
])
def test_missing_input_yields_absent_not_false(missing):
    """plan 原文:「`ret_20d` / 20 日高 / `ma20` / 连板高度任一算不出 → 该标签
    `absent` 且不显示,**不写 false 冒充「已判定为否」**」。"""
    r = mt.evaluate_member_tags(_row(**{missing: None}))
    assert mt.TAG_PULLBACK_LEADER in r.absent
    assert mt.TAG_PULLBACK_LEADER not in r.missed
    assert mt.TAG_PULLBACK_LEADER not in r.codes()
    # 强势资格所需的两键缺失时,追入位也跟着 absent;其余键不牵连它(各自降级)。
    if missing in ("limitup_count_20d", "ret_20d", "dist_from_high_20d"):
        assert mt.TAG_WARN_CHASE_ZONE in r.absent
    else:
        assert mt.TAG_WARN_CHASE_ZONE not in r.absent


def test_all_absent_when_nothing_known():
    r = mt.evaluate_member_tags({"ts_code": "000001.SZ"})
    assert set(r.absent) == set(mt.ALL_TAG_CODES)
    assert r.codes() == () and r.missed == ()


def test_bool_is_not_a_number():
    """`True` 在 Python 里等于 1 —— 拿它当 `ret_20d` 必须判成"算不出",不能静默
    参与比较(会得出荒谬结论且完全看不出来)。"""
    r = mt.evaluate_member_tags(_row(ret_20d=True))
    assert mt.TAG_PULLBACK_LEADER in r.absent


def test_tag_order_is_deterministic():
    r = mt.evaluate_member_tags(_row(dist_from_high_20d=-0.01, streak_top=True))
    assert r.codes() == (mt.TAG_WARN_STREAK_TOP, mt.TAG_WARN_CHASE_ZONE)


# ══════════════════════════════════════════════════════════════════════════
# 判据口径 = 研究线预注册定义(不重新发明)
# ══════════════════════════════════════════════════════════════════════════

def test_thresholds_match_preregistered_h12_definition():
    """默认阈值逐条等于 `research/k7_pre2_report.md` §1 / `k7p_h12_pullback.py`
    的预注册定义 —— 通过"边界值恰好翻转"来验证,不去读函数签名的默认值
    (读签名等于自己跟自己对答案)。"""
    # 强势资格下界:1 次涨停不算强势,2 次算
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(_row(limitup_count_20d=2)).codes()
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(_row(limitup_count_20d=1)).missed
    # ret_20d 下界 +25%
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(_row(ret_20d=0.2499)).missed
    # 回调带 [−25%, −8%]
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(_row(dist_from_high_20d=-0.0799)).missed
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(_row(dist_from_high_20d=-0.2501)).missed
    # 追入带 > −3%
    assert mt.TAG_WARN_CHASE_ZONE in mt.evaluate_member_tags(_row(dist_from_high_20d=-0.03)).missed
    assert mt.TAG_WARN_CHASE_ZONE in mt.evaluate_member_tags(_row(dist_from_high_20d=-0.0299)).codes()


def test_thresholds_are_overridable_for_sensitivity_runs():
    """阈值住在**函数关键字默认值**上(不是模块级全局)——研究线的宽/严档敏感性
    因此可以直接传参跑,不必改代码。"""
    row = _row(limitup_count_20d=1, ret_20d=0.21)
    assert mt.TAG_PULLBACK_LEADER in mt.evaluate_member_tags(row).missed
    wide = mt.evaluate_member_tags(row, strong_min_limitups_20d=1, strong_min_ret_20d=0.20)
    assert mt.TAG_PULLBACK_LEADER in wide.codes()


# ══════════════════════════════════════════════════════════════════════════
# ③ 文案:每处带「参考、非指令」+ 单一源
# ══════════════════════════════════════════════════════════════════════════

def test_every_tag_text_carries_reference_only_suffix():
    for code in mt.ALL_TAG_CODES:
        text = mt.tag_text(code)
        assert text.endswith(mt.REFERENCE_ONLY_SUFFIX), code
        assert "参考、非指令" in text


def test_tag_objects_carry_text_label_tone_and_source():
    r = mt.evaluate_member_tags(_row(dist_from_high_20d=-0.01, streak_top=True))
    for tag in r.tags:
        assert tag.label and tag.text.endswith(mt.REFERENCE_ONLY_SUFFIX)
        assert tag.tone in (mt.TONE_NEUTRAL, mt.TONE_WARN)
        assert "k7_pre2_report" in tag.source          # 数字带来源
        d = tag.to_dict()
        assert set(d) == {"code", "label", "tone", "text", "source"}


def test_public_member_contract_adds_labels_for_absent_tags():
    """默认展示层拿中文标签；旧 `tagsAbsent` 仍保留给兼容/审计。"""
    from neckline.report.basket_daily import card_member_to_public_dict

    public = card_member_to_public_dict({
        "ts_code": "600000.SH", "tags_absent": [mt.TAG_WARN_STREAK_TOP],
    })
    assert public["tagsAbsent"] == [mt.TAG_WARN_STREAK_TOP]
    assert public["tagAbsences"] == [{
        "code": mt.TAG_WARN_STREAK_TOP,
        "label": mt.tag_label(mt.TAG_WARN_STREAK_TOP),
    }]


def test_reference_only_suffix_is_single_sourced_in_module_text():
    """文案主体里**不许**自己再写一遍后缀(否则会出现"参考、非指令。参考、非指令。"
    这种双份,且改一处漏一处)——后缀只由 `tag_text()` 追加。"""
    src = (_REPO / "neckline" / "selection" / "member_tags.py").read_text(encoding="utf-8")
    assert src.count('REFERENCE_ONLY_SUFFIX = "') == 1
    for body in ("机会密度约为市场的", "簇内连板高度第一", "强势且贴着 20 日高"):
        assert body in src
    # 主体常量里不含后缀本身
    from neckline.selection.member_tags import _TEXT_BODY
    for code, body in _TEXT_BODY.items():
        assert mt.REFERENCE_ONLY_SUFFIX not in body, code


# ══════════════════════════════════════════════════════════════════════════
# ② 四不守门(静态 grep 三条 + 行为对比一条)
# ══════════════════════════════════════════════════════════════════════════

def test_tag_codes_not_in_tier_score_inputs():
    """不进机械分:标签码与 ⑥ 的白名单**交集为空**。"""
    assert set(mt.ALL_TAG_CODES).isdisjoint(ti._TIER_SCORE_INPUTS)
    assert set(mt.TAG_INPUTS).isdisjoint(ti._TIER_SCORE_INPUTS)


def test_tag_codes_not_in_primitive_feature_whitelist_nor_pack_dims():
    """不进排序键:标签码既不是任何原语可引用的特征,也不出现在任何包的
    `tier.dims`(仓库里的真包逐份扫)。"""
    assert set(mt.ALL_TAG_CODES).isdisjoint(prim._ALLOWED_FEATURES)
    for p in prim.PRIMITIVES.values():
        assert set(mt.ALL_TAG_CODES).isdisjoint(set(p.inputs)), p.name
    packs = sorted(_PACKS_DIR.glob("*.json"))
    assert packs, "packs/ 下一个包都没有,这条守门就成了空跑"
    for f in packs:
        doc = json.loads(f.read_text(encoding="utf-8"))
        dims = doc.get("config", {}).get("tier", {}).get("dims", [])
        weights = doc.get("config", {}).get("tier", {}).get("weights", {})
        assert set(mt.ALL_TAG_CODES).isdisjoint(dims), f.name
        assert set(mt.ALL_TAG_CODES).isdisjoint(weights), f.name


def test_tag_codes_absent_from_sentinel_tree():
    """不进哨兵判据:`neckline/sentinel/` 全目录零命中(plan 验收原文)。"""
    hits: List[str] = []
    for path in sorted((_REPO / "neckline" / "sentinel").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for code in mt.ALL_TAG_CODES + ("member_tags",):
            if code in text:
                hits.append(f"{path.name}:{code}")
    assert not hits, f"⑦-K7 标注件泄漏进哨兵目录:{hits}"


def test_member_tags_module_does_not_import_sentinel_or_tier():
    src = (_REPO / "neckline" / "selection" / "member_tags.py").read_text(encoding="utf-8")
    assert "neckline.sentinel" not in src
    assert "selection.tier" not in src and "from neckline.selection import tier" not in src


# —— 「打标前后 Tier 序与成员去留逐位不变」———————————————————————————————

def _member(code: str, **over: Any) -> ag.BasketMemberCandidate:
    kw: Dict[str, Any] = dict(
        ts_code=code, role_llm="core", role_mech=None, role_conflict=0,
        reason="成员理由", name=code,
    )
    kw.update(over)
    return ag.BasketMemberCandidate(**kw)


def _basket(key: str, codes: List[str]) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=f"篮子{key}", driver="共同驱动",
        driver_kind="theme", why_now="为什么是现在", seed_keys=(f"s-{key}",),
        members=tuple(_member(c) for c in codes),
        evidence=(ag.EvidenceItem(claim="证据", source="某来源", date="2024-04-07"),),
        evidence_status=ag.EVIDENCE_OK, pack_version="K7-pack-v1",
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v1.3.3",
    )


class _FakeDecision:
    """duck-typed 的 ⑥ 定档结果(⑦ 刻意不 import `tier`,这里也照办)。"""

    def __init__(self, tier: int, rank: int, score: float) -> None:
        self.tier, self.rank_in_tier, self.rank_mech = tier, rank, rank
        self.mech_score, self.breakdown, self.llm_reason = score, {"dims": {}}, None


def test_tagging_changes_neither_tier_order_nor_member_roster():
    """plan 验收 ②后半:**同一份数据跑两遍,一遍关标注一遍开标注** → Tier 序与
    成员去留逐位不变。标注件是纯展示位,除了 `members[].tags` 之外一个字节都不该动。"""
    baskets = [_basket("b1", ["600000.SH", "600001.SH"]), _basket("b2", ["000001.SZ"])]
    decisions = {"b1": _FakeDecision(1, 1, 0.9), "b2": _FakeDecision(2, 1, 0.5)}
    mechs = {c: bc.MemberMech(ts_code=c, close=10.0, ma20=9.0, limit_up=11.0,
                              limit_down=9.0, stop_price=9.5)
             for c in ("600000.SH", "600001.SH", "000001.SZ")}
    batch = mt.tags_for_members(
        list(mechs), D0,
        panel_rows={c: _row(ts_code=c) for c in mechs},
        streak_top={"600000.SH": True},
    )

    def _cards(with_tags: bool) -> List[Dict[str, Any]]:
        return [
            bc.build_basket_card(
                b, D0, tier_decision=decisions[b.basket_key], mechs=mechs,
                tag_batch=batch, with_tags=with_tags, stop_pct=0.05,
            ).to_card_json()
            for b in baskets
        ]

    off, on = _cards(False), _cards(True)
    assert any(m["tags"] for c in on for m in c["members"]), "开关打开时至少要有一个标"
    assert all(not m["tags"] for c in off for m in c["members"])
    for a, b in zip(off, on):
        assert [m["ts_code"] for m in a["members"]] == [m["ts_code"] for m in b["members"]]
        for key in ("tier", "rank_in_tier", "rank_mech", "mech_score", "basket_key"):
            assert a[key] == b[key]
        # 除 tags / tags_absent 之外,成员节逐字节相同
        for ma, mb in zip(a["members"], b["members"]):
            assert {k: v for k, v in ma.items() if k not in ("tags", "tags_absent")} == \
                   {k: v for k, v in mb.items() if k not in ("tags", "tags_absent")}


# ══════════════════════════════════════════════════════════════════════════
# `streak_top_flags`:并列 / 多簇 / limit_height<1
# ══════════════════════════════════════════════════════════════════════════

def _seed_leader_rows(db_path: Path, rows: List[dict]) -> None:
    import sqlite3

    from neckline.db import init_schema
    from neckline.scan.leader import TABLE

    init_schema(db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executemany(
            f"INSERT INTO {TABLE} (trade_date, cluster_key, ts_code, rs_rank, limit_height,"
            " amount_share, role_mech, computed_at) VALUES (?,?,?,?,?,?,?,?)",
            [(D0_S, r["cluster_key"], r["ts_code"], r.get("rs_rank"), r.get("limit_height"),
              r.get("amount_share", 0.1), r.get("role_mech", "core"), "2024-04-08T09:00:00+00:00")
             for r in rows],
        )
        conn.commit()
    finally:
        conn.close()


def test_streak_top_flags_ties_multicluster_and_zero_height(isolated_env):
    """判据落在**共振邻域**(所在全部簇的成员并集)上,不是逐簇各判一次 ——
    ④ 的概念聚类下一只票同日常属于十几个簇,「任一簇头名即头名」会让 86% 的涨停票
    都被打上警示(2026-07-24 真实数据 36/42),那等于没有警示。"""
    _seed_leader_rows(isolated_env.db_path, [
        # 簇 A:两只并列最高(都算头名 —— 警示从严,标注件不做 tie-break)
        {"cluster_key": "A", "ts_code": "600000.SH", "limit_height": 3},
        {"cluster_key": "A", "ts_code": "600001.SH", "limit_height": 3},
        {"cluster_key": "A", "ts_code": "600002.SH", "limit_height": 1},
        # 簇 B:600002 在这个簇里是最高的,但它的**共振邻域**含簇 A 的 3 板票 →
        # 不是头名(这正是「任一簇头名」与「邻域头名」分道扬镳的那一格)
        {"cluster_key": "B", "ts_code": "600002.SH", "limit_height": 1},
        {"cluster_key": "B", "ts_code": "600003.SH", "limit_height": 1},
        # 簇 C:全员 0 连板 → 头名没有意义,不打标
        {"cluster_key": "C", "ts_code": "600004.SH", "limit_height": 0},
        {"cluster_key": "C", "ts_code": "600005.SH", "limit_height": 0},
        # 簇 D:全员齐平 2 板 → 没有「后排」,文案「次日跌停 3× 于后排」无从成立
        {"cluster_key": "D", "ts_code": "600006.SH", "limit_height": 2},
        {"cluster_key": "D", "ts_code": "600007.SH", "limit_height": 2},
    ])
    codes = [f"60000{i}.SH" for i in range(8)] + ["600009.SH"]
    flags = mt.streak_top_flags(codes, D0, db_path=isolated_env.db_path)
    assert flags["600000.SH"] is True and flags["600001.SH"] is True   # 并列最高都打标
    assert flags["600002.SH"] is False         # 簇 B 内最高,但邻域里有 3 板票
    assert flags["600003.SH"] is False
    assert flags["600004.SH"] is False         # limit_height=0 不算头名
    assert flags["600006.SH"] is False and flags["600007.SH"] is False  # 齐平无后排
    assert "600009.SH" not in flags            # 不在表里 → 调用方落 absent,不是 False


def test_streak_top_flags_empty_table_is_absent_not_false(isolated_env):
    flags = mt.streak_top_flags(["600000.SH"], D0, db_path=isolated_env.db_path)
    assert flags == {}
    batch = mt.tags_for_members(["600000.SH"], D0, db_path=isolated_env.db_path,
                                panel_rows={"600000.SH": _row()})
    assert mt.TAG_WARN_STREAK_TOP in batch.get("600000.SH").absent
    assert batch.leader_available is False


def test_streak_top_flags_survives_null_limit_height(isolated_env):
    _seed_leader_rows(isolated_env.db_path, [
        {"cluster_key": "A", "ts_code": "600000.SH", "limit_height": None},
        {"cluster_key": "A", "ts_code": "600001.SH", "limit_height": 2},
        {"cluster_key": "A", "ts_code": "600002.SH", "limit_height": 1},
    ])
    flags = mt.streak_top_flags(["600000.SH", "600001.SH", "600002.SH"], D0,
                                db_path=isolated_env.db_path)
    assert "600000.SH" not in flags            # 连板高度算不出 → absent
    assert flags["600001.SH"] is True
    assert flags["600002.SH"] is False


def test_streak_top_needs_a_lower_peer_in_the_neighbourhood(isolated_env):
    """独苗簇(邻域里没有比它低的票)不打标 —— 「3× 于后排」得先有后排。"""
    _seed_leader_rows(isolated_env.db_path, [
        {"cluster_key": "A", "ts_code": "600000.SH", "limit_height": 4},
        {"cluster_key": "A", "ts_code": "600001.SH", "limit_height": 4},
    ])
    flags = mt.streak_top_flags(["600000.SH", "600001.SH"], D0, db_path=isolated_env.db_path)
    assert flags == {"600000.SH": False, "600001.SH": False}


# ══════════════════════════════════════════════════════════════════════════
# 两条取数路径产出一致(⑬-N 信息卡交叉断言的地基)
# ══════════════════════════════════════════════════════════════════════════

def test_injected_and_self_read_paths_agree(isolated_env, monkeypatch):
    """`tags_for_members` 无论是"调用方注入已算好的行"还是"自己去读",结论必须
    逐位相同 —— ⑬-N 要断言的「同一票同一天,信息卡与篮子卡标签集合逐位相同」就
    建立在这条上。"""
    rows = {"600000.SH": _row(ts_code="600000.SH")}
    monkeypatch.setattr(mt, "load_tag_panel_rows", lambda *a, **k: dict(rows))
    _seed_leader_rows(isolated_env.db_path, [
        {"cluster_key": "A", "ts_code": "600000.SH", "limit_height": 4},
        {"cluster_key": "A", "ts_code": "600001.SH", "limit_height": 1},
    ])
    injected = mt.tags_for_members(
        ["600000.SH"], D0, db_path=isolated_env.db_path,
        panel_rows=rows, streak_top={"600000.SH": True},
    )
    self_read = mt.tags_for_members(["600000.SH"], D0, db_path=isolated_env.db_path)
    assert injected.get("600000.SH").codes() == self_read.get("600000.SH").codes()
    assert injected.get("600000.SH").absent == self_read.get("600000.SH").absent


def test_batch_get_for_unknown_code_is_all_absent():
    batch = mt.tags_for_members([], D0)
    assert batch.get("999999.SZ").absent == mt.ALL_TAG_CODES


def test_load_tag_panel_rows_survives_missing_parquet(tmp_path: Path):
    """面板读不到 → 空 dict(相关标签落 absent),**不抛**。"""
    assert mt.load_tag_panel_rows(["600000.SH"], D0, parquet_dir=tmp_path) == {}


def test_load_tag_panel_rows_returns_expected_columns(isolated_env):
    from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

    days = business_days(date(2024, 2, 1), 60)
    insert_trade_cal(isolated_env, days)
    for i, d in enumerate(days):
        write_daily_fixture(isolated_env, "daily", d, [{
            "ts_code": "600000.SH", "trade_date": d, "open": 10.0 + i * 0.1,
            "high": 10.5 + i * 0.1, "low": 9.5 + i * 0.1, "close": 10.0 + i * 0.1,
            "pre_close": 9.9 + i * 0.1, "vol": 1000.0 + i, "amount": 100000.0 + i,
        }])
    rows = mt.load_tag_panel_rows(["600000.SH"], days[-1], parquet_dir=isolated_env.parquet_dir)
    got = rows.get("600000.SH")
    assert got is not None
    for key in ("ret_20d", "dist_from_high_20d", "close", "ma20", "vol", "vol_ma5", "ret_1d"):
        assert key in got, key
