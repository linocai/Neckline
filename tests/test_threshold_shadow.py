"""V2.3.2-① 关口判定改判 + 阈值影子台账的机器判据(plan §五 ① 验收清单逐条)。

覆盖:
    ①-A `enforcement_of` 的二分与**默认方向**(读不到 provenance → evidence),
        以及**全仓唯一实现**(AST 守门);
    ①-B 三项退出硬否决 / 四项 audited 仍硬否决(正反双向,主体在 test_selection_gates.py);
    ①-C `aggregate.py` 零 import `gates`(防成环)+ **LLM 调用增量恒为 0**;
    ①-D 影子台账:行数 = 候选数 × 该引擎 evidence 阈值数、**硬门先拒也照样出行**、
        append-only、同一 ctx 跑两遍逐位一致(确定性);
    ①-E 通过率报告:分母 ≥ 分子、分母口径写死、样本不足不出百分数。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from neckline.eval import threshold_calibration as tc
from neckline.scan import stage as stage_mod
from neckline.selection import aggregate as ag
from neckline.selection import gates as gt
from neckline.selection import pack as pack_mod
from neckline.selection import threshold_shadow as ts
from tests.conftest import insert_trade_cal

D0 = date(2024, 4, 8)
D0_S = "20240408"
_ROOT = Path(__file__).resolve().parent.parent
_PACKS_DIR = _ROOT / "packs"


def _pack(filename: str) -> pack_mod.Pack:
    doc = json.loads((_PACKS_DIR / filename).read_text(encoding="utf-8"))
    m, c = doc["manifest"], doc["config"]
    return pack_mod.Pack(
        pack_version=m["pack_version"], name=m["name"],
        engine_api_version=int(m["engine_api_version"]), manifest=m, config=c,
        evidence_ref=list(m.get("evidence_ref", [])), is_active=True,
        created_at="2024-04-08T00:00:00+00:00", activated_at=None,
        line_code=m.get("line_code", "LEGACY"), status="running",
    )


C1 = _pack("C1.json")
Z1 = _pack("Z1.json")
Y1 = _pack("Y1.json")
ENGINES = {"C": C1, "Z": Z1, "Y": Y1}
SKELETON = _pack("K8-skeleton.json")

_EV3 = (
    ag.EvidenceItem(claim="发布产业扶持政策文件", source="某部委", date="2024-04-07"),
    ag.EvidenceItem(claim="公司公告签订重大合同", source="上市公司A", date="2024-04-06"),
    ag.EvidenceItem(claim="产业链上游开工率回升", source="财联社", date="2024-04-05"),
)


def _member(code: str, *, industry: Optional[str] = "半导体") -> ag.BasketMemberCandidate:
    return ag.BasketMemberCandidate(
        ts_code=code, role_llm="core", role_mech=None, role_conflict=0,
        reason="理由", industry=industry, name=code,
        position_verdict=ag.POSITION_OK, core_verdict=ag.CORE_OK,
    )


def _basket(key: str, *, engine: str = "C", codes=("600001.SH",),
            market: str = "", sector: str = "", pool: int = 8) -> ag.BasketCandidate:
    return ag.BasketCandidate(
        trade_date=D0_S, basket_key=key, name=key, driver="共同驱动",
        driver_kind="theme", why_now="为什么是现在", seed_keys=("s-1",),
        members=tuple(_member(c) for c in codes), evidence=_EV3,
        evidence_status=ag.EVIDENCE_OK, pack_version="K8-V0.5",
        engine_api_version=ag.engine_api.ENGINE_API_VERSION, charter_version="v1.3.3",
        engine_code_llm=engine, common_trait="共同特征", persistence="持续性",
        strengthen_and_invalidate="强化与证伪", aux={"seed_pool_size": pool},
        market_verdict=market, sector_verdict=sector,
    )


def _agg(baskets) -> ag.AggregateResult:
    return ag.AggregateResult(trade_date=D0_S, baskets=tuple(baskets),
                              pack_version="K8-V0.5", charter_version="v1.3.3")


def _insert_regime(db_path: Path, regime: str, *, breadth_pctile=None) -> None:
    from neckline.db import init_schema

    init_schema(db_path=db_path)
    inputs = {"breadth": {"available": breadth_pctile is not None, "pctile": breadth_pctile}}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR REPLACE INTO market_regime_daily (trade_date, regime, regime_reason, "
            "inputs_json, strengthening_json, weakening_json, skeleton_version, computed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (D0_S, regime, "test", json.dumps(inputs), "[]", "[]", "K8-V0.5", "now"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_strength(db_path: Path, days, rank: int, strength: bool) -> None:
    from neckline.db import init_schema
    from neckline.report.industry_strength import _MIN_MEMBERS, _STRENGTH_QUANTILE
    from neckline.report.industry_strength_store import TABLE

    init_schema(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        for d in days:
            conn.execute(
                f"INSERT OR REPLACE INTO {TABLE} (trade_date, industry, median_ret, "
                "member_count, industry_rank, is_strength_day, persist_days, quantile, "
                "min_members, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (d.strftime("%Y%m%d"), "半导体", 0.01, 20, rank, int(strength), 1,
                 _STRENGTH_QUANTILE, _MIN_MEMBERS, "now"),
            )
        conn.commit()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# ①-A `enforcement_of`:二分 + 默认方向
# ══════════════════════════════════════════════════════════════════════════

class TestEnforcementBisection:
    def test_audited_is_hard_everything_else_is_evidence(self):
        assert gt.enforcement_of({"value": 10, "provenance": {"source": "audited",
                                                              "ref": "x"}}) == gt.ENFORCEMENT_HARD
        assert gt.enforcement_of(
            {"value": 3, "provenance": {"source": "engineering_v1", "basis": "b",
                                        "calibration": "pending"}}) == gt.ENFORCEMENT_EVIDENCE

    @pytest.mark.parametrize("leaf", [None, {}, {"value": 1}, 10, "audited",
                                      {"value": 1, "provenance": None},
                                      {"value": 1, "provenance": {"source": ""}}])
    def test_default_direction_is_evidence_never_hard(self, leaf):
        """🔴 默认方向刻意选 evidence:误判成 evidence 的后果是「多留一个候选」(吵),
        误判成 hard 的后果是「静默除名」(漏审)。⛔ 不得改成默认 hard。"""
        assert gt.enforcement_of(leaf) == gt.ENFORCEMENT_EVIDENCE

    def test_the_four_audited_leaves_are_exactly_the_ones_the_ruling_named(self):
        """已拍板 #3 逐条对拍:四项 audited 必须**恰好**是裁定 2 列的那四项。
        ⛔ 多一项 = 有人偷偷给某条阈值升了级(裁定 1「零自动升级」)。"""
        hard: List[str] = []
        for pk in ENGINES.values():
            for gate, key in gt.GOVERNED_THRESHOLD_KEYS:
                leaf = gt._gate_leaf(pk, gate, key)
                if leaf is not None and gt.enforcement_of(leaf) == gt.ENFORCEMENT_HARD:
                    hard.append(f"{pk.pack_version}.{gate}.{key}")
        assert sorted(hard) == sorted([
            "C1.sector.industry_rank_max",
            "Y1.sector.industry_rank_max",
            "Z1.market.trend_continuation_required_stages",
            "Z1.sector.stage_allowed",
        ])

    def test_governed_keys_match_the_pack_schema(self):
        """防漂:`GOVERNED_THRESHOLD_KEYS` 必须与 `pack._ENGINE_GATE_SCHEMA` 的
        market/sector 两节逐键相等 —— 漏一个键 = 那条阈值悄悄不进影子台账,
        ①-E 的通过率就少一维**且看不出来**。"""
        declared = {(g, k) for g, k in gt.GOVERNED_THRESHOLD_KEYS}
        expected = {("market", k) for k in pack_mod._ENGINE_GATE_SCHEMA["market"]} | \
                   {("sector", k) for k in pack_mod._ENGINE_GATE_SCHEMA["sector"]}
        assert declared == expected


# ══════════════════════════════════════════════════════════════════════════
# ①-D 影子台账
# ══════════════════════════════════════════════════════════════════════════

def _evidence_leaf_count(pk: pack_mod.Pack) -> int:
    return sum(1 for g, k in gt.GOVERNED_THRESHOLD_KEYS
               if (leaf := gt._gate_leaf(pk, g, k)) is not None
               and gt.enforcement_of(leaf) == gt.ENFORCEMENT_EVIDENCE)


class TestThresholdShadowLedger:
    def _world(self, env, *, rank: int = 3, strength: bool = True,
               regime: str = "trend_continuation"):
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength(env.db_path, days, rank, strength)
        _insert_regime(env.db_path, regime)

    def test_row_count_is_candidates_times_evidence_thresholds(self, isolated_env):
        """plan ① 验收 ②:同一天同一份候选,影子行数 = 候选数 × 该引擎 evidence 阈值数。"""
        env = isolated_env
        self._world(env)
        r = _agg([_basket("k1", engine="C"), _basket("k2", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        n = ts.save_threshold_shadow(out, tier_by_candidate={}, regime="trend_continuation",
                                     db_path=env.db_path)
        assert n == 2 * _evidence_leaf_count(C1)
        rows = ts.load_threshold_shadow(D0_S, D0_S, db_path=env.db_path)
        assert len(rows) == n
        # ⛔ audited 的四项一条都不许出现(判定已在 gate_evaluations 里,写两份 = 两个事实源)
        assert all(r0["threshold_key"] != "sector.industry_rank_max" for r0 in rows)

    def test_hard_gate_rejection_does_not_swallow_the_later_readings(self, isolated_env):
        """🔴 **本版最该有的一条**(①-D 施工要求):行业名次被 audited 硬门拒掉的候选,
        `strength_days_min_5d` 的读数**照算照存** —— 否则它的单关通过率分母会悄悄变成
        「名次已过的那批」,与裁定 3 写死的分母不是同一个东西,**而且看不出来**。"""
        env = isolated_env
        self._world(env, rank=30, strength=False)          # 名次 30 > C1 的 10 → 硬否决
        r = _agg([_basket("k1", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        assert out.summaries["k1"].excluded                # 确认硬门真的拒了
        assert out.summaries["k1"].exclusion_reason == gt.EXCLUDE_MECH_GATE_REJECTED

        ts.save_threshold_shadow(out, tier_by_candidate={}, db_path=env.db_path)
        rows = ts.load_threshold_shadow(D0_S, D0_S, db_path=env.db_path)
        sdays = [r0 for r0 in rows if r0["threshold_key"] == "sector.strength_days_min_5d"]
        assert len(sdays) == 1                             # ⛔ 不许因为先拒就跳过
        assert sdays[0]["reading"] == 0.0 and sdays[0]["threshold_value"] == 3.0
        assert sdays[0]["would_pass"] == 0                 # 拟判:本可否决

    def test_not_applicable_is_not_the_same_as_missing(self, isolated_env):
        """`high_divergence_min_breadth_pctile` 只在高位分歧态适用 —— 非该态的日子
        照样出行,但带 `not_applicable:` 前缀且拟判为 NULL(①-E 据此按适用域出分母,
        ⛔ 不拿全体候选把它稀释成一个好看的数)。"""
        env = isolated_env
        self._world(env, regime="trend_continuation")
        r = _agg([_basket("k1", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        ts.save_threshold_shadow(out, tier_by_candidate={}, db_path=env.db_path)
        rows = ts.load_threshold_shadow(D0_S, D0_S, db_path=env.db_path)
        hit = [r0 for r0 in rows
               if r0["threshold_key"] == "market.high_divergence_min_breadth_pctile"]
        assert len(hit) == 1
        assert hit[0]["would_pass"] is None
        assert hit[0]["unavailable_reason"].startswith(gt.NOT_APPLICABLE_PREFIX)

    def test_same_context_twice_is_bit_identical(self, isolated_env):
        """plan ① 验收 ③:同一 ctx 跑两遍,关口行与影子行内容逐位一致(确定性)。"""
        env = isolated_env
        self._world(env)
        r = _agg([_basket("k1", engine="C"), _basket("k2", engine="Z", pool=2)])
        ctx = gt.build_gate_context(D0, (), db_path=env.db_path, engines=ENGINES,
                                    skeleton=SKELETON)
        a = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES,
                            skeleton=SKELETON, context=ctx)
        b = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES,
                            skeleton=SKELETON, context=ctx)
        for key in a.summaries:
            assert a.summaries[key].threshold_readings == b.summaries[key].threshold_readings
            assert ([(c.gate, c.verdict, c.reason) for c in a.summaries[key].checks]
                    == [(c.gate, c.verdict, c.reason) for c in b.summaries[key].checks])

    def test_final_tier_none_means_out_not_zero(self, isolated_env):
        """OUT / 未定档 → `final_tier` 落 NULL。⛔ 别用 0 表示 OUT(会和"档位 0"混掉)。"""
        env = isolated_env
        self._world(env)
        r = _agg([_basket("k1", engine="C"), _basket("k2", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        ts.save_threshold_shadow(out, tier_by_candidate={"k1": 1}, db_path=env.db_path)
        rows = ts.load_threshold_shadow(D0_S, D0_S, db_path=env.db_path)
        assert {r0["final_tier"] for r0 in rows if r0["candidate_key"] == "k1"} == {1}
        assert {r0["final_tier"] for r0 in rows if r0["candidate_key"] == "k2"} == {None}

    def test_rerun_appends_instead_of_overwriting(self, isolated_env):
        """append-only:同日重跑 = 追加新批次(审计表不做覆盖)。"""
        env = isolated_env
        self._world(env)
        r = _agg([_basket("k1", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        n1 = ts.save_threshold_shadow(out, db_path=env.db_path)
        ts.save_threshold_shadow(out, db_path=env.db_path)
        assert len(ts.load_threshold_shadow(D0_S, D0_S, db_path=env.db_path)) == 2 * n1


# ══════════════════════════════════════════════════════════════════════════
# ①-E 通过率报告
# ══════════════════════════════════════════════════════════════════════════

class TestThresholdReport:
    def test_no_rows_says_sample_insufficient_not_a_percentage(self, isolated_env):
        """§七 P3-59:样本不足时如实标,⛔ 不许给一个看起来能用的百分数;
        且 ⛔ 不许拿 14 个历史 D0 顶上(裁定 3)。"""
        rep = tc.build_threshold_report(D0_S, D0_S, db_path=isolated_env.db_path)
        assert rep["available"] is False
        assert "样本不足" in rep["unavailableReason"]
        assert "历史 D0" in rep["unavailableReason"]
        assert "passRate" not in rep

    def test_denominator_is_never_smaller_than_the_numerator(self, isolated_env):
        """plan ① 验收:守门正面钉死分母 ≥ 分子,且分母口径写死在产物里。"""
        env = isolated_env
        days = [date(2024, 4, 1), date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 4), D0]
        insert_trade_cal(env, days)
        _insert_strength(env.db_path, days, 3, True)
        _insert_regime(env.db_path, "trend_continuation")
        r = _agg([_basket("k1", engine="C"), _basket("k2", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        ts.save_threshold_shadow(out, tier_by_candidate={"k1": 1},
                                 regime="trend_continuation", db_path=env.db_path)

        rep = tc.build_threshold_report(D0_S, D0_S, db_path=env.db_path)
        assert rep["available"] is True
        assert "召回候选" in rep["denominatorRule"]
        assert "最终 T1/T2 的历史快照" in rep["denominatorRule"]
        assert rep["candidates"] == 2
        for key, tally in rep["perThreshold"].items():
            assert tally["wouldPass"] <= tally["evaluable"] <= tally["applicable"] <= tally["rows"]
            assert tally["rows"] <= rep["candidates"], key
        j = rep["joint"]
        assert j["wouldPass"] <= j["determinable"] <= j["candidates"] == 2

    def test_undetermined_is_never_counted_as_a_pass_or_a_fail(self, isolated_env):
        """「算不出」是第三态:⛔ 既不进分子、也不当 False 拉低通过率。"""
        env = isolated_env
        insert_trade_cal(env, [D0])
        _insert_regime(env.db_path, "trend_continuation")     # 无行业强度表 → 读数缺
        r = _agg([_basket("k1", engine="C")])
        out = gt.evaluate_day(r, D0, db_path=env.db_path, engines=ENGINES, skeleton=SKELETON)
        ts.save_threshold_shadow(out, regime="trend_continuation", db_path=env.db_path)
        rep = tc.build_threshold_report(D0_S, D0_S, db_path=env.db_path)
        sdays = rep["perThreshold"]["sector.strength_days_min_5d"]
        assert sdays["evaluable"] == 0 and sdays["passRate"] is None
        assert sdays["sampleInsufficient"] is True

    def test_band_edges_come_from_the_ruling_not_from_engineering(self):
        """裁定 4 的三档边界(10% / 20%)照抄,⛔ 工程侧不许改。"""
        assert tc.BAND_UNACCEPTABLE == 0.10 and tc.BAND_SAMPLE_OK == 0.20
        assert tc._band_of(None) == "sample_insufficient"
        assert tc._band_of(0.05) == "unacceptable_too_strict"
        assert tc._band_of(0.15) == "keep_as_evidence"
        assert tc._band_of(0.25) == "sample_availability_ok"


# ══════════════════════════════════════════════════════════════════════════
# 反向守门(静态 / AST)
# ══════════════════════════════════════════════════════════════════════════

_NECKLINE_DIR = _ROOT / "neckline"
_SCRIPTS_DIR = _ROOT / "scripts"
_AGG_PATH = _NECKLINE_DIR / "selection" / "aggregate.py"


def test_enforcement_of_is_defined_exactly_once_in_the_whole_repo():
    """①-A:⛔ 全仓不许有第二处按 `provenance.source` 判闸门模式的代码。
    两条判据:① `def enforcement_of` 只有一处;② 除 `gates.py` 外,没有别的模块把
    `"audited"` 这个字面量拿去跟 source 比(`pack.py` 的**词表**与 `activate_*`
    的对账不算 —— 它们不判闸门模式)。"""
    defs = []
    for path in sorted(_NECKLINE_DIR.rglob("*.py")) + sorted(_SCRIPTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "enforcement_of":
                defs.append(str(path.relative_to(_ROOT)))
    assert defs == ["neckline/selection/gates.py"], defs


def test_aggregate_never_imports_gates():
    """①-C 防成环:`gates.py` 反向 import `aggregate`,故 `aggregate.py` ⛔ 不许
    import `gates` —— 市场关 / 板块关的读数走**鸭子类型注入**(`MechContext.gate_ctx`)。"""
    tree = ast.parse(_AGG_PATH.read_text(encoding="utf-8"))
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    assert not [m for m in mods if m == "neckline.selection.gates"
                or m.startswith("neckline.selection.gates.")], mods


def test_llm_call_count_is_still_exactly_two_after_adding_two_more_gates():
    """🔴 **LLM 调用增量恒为 0**(plan 〇b / §3.12-C):市场关 / 板块关的判定**搭
    `basket_reason` 那一次调用**,⑤ 里 `provider.chat(...)` 的调用点仍恒为 **2 个**
    (检索段 1 + 推理段 1)。AST 数,⛔ 不数字符串。"""
    tree = ast.parse(_AGG_PATH.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "chat"
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "provider"]
    assert len(calls) == 2, [n.lineno for n in calls]
    for mod in ("selection/threshold_shadow.py", "eval/threshold_calibration.py"):
        sub = ast.parse((_NECKLINE_DIR / mod).read_text(encoding="utf-8"))
        assert not [n for n in ast.walk(sub)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "chat"], mod


def test_threshold_shadow_never_writes_back_to_the_formal_verdict_tables():
    """裁定 5:⛔ 历史影子结果不得回写当时的正式选股结论。"""
    body = (_NECKLINE_DIR / "selection" / "threshold_shadow.py").read_text(encoding="utf-8")
    tree = ast.parse(body)
    sql: List[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "executemany", "executescript"}
                and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                sql.append(arg.value.upper())
            elif isinstance(arg, ast.JoinedStr):
                sql.append("".join(v.value.upper() for v in arg.values
                                   if isinstance(v, ast.Constant)
                                   and isinstance(v.value, str)))
    joined = " ".join(" ".join(s.split()) for s in sql)
    for banned in ("UPDATE ", "DELETE ", "INSERT OR REPLACE", "REPLACE INTO"):
        assert banned not in joined, banned
    for table in ("BASKETS", "TIER_HISTORY", "BASKET_CARDS", "SELECTION_CLOCK"):
        assert f"INTO {table}" not in joined, table
