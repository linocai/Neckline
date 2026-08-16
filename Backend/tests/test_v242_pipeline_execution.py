from __future__ import annotations

import json
import sqlite3
from datetime import date

from neckline.llm.base import LLMResult, SearchHit
from neckline.scan.seeds import DriverSeed
from neckline.search.tavily import TavilySearchResponse
from neckline.selection.direction_pipeline import run_direction_pipeline


def _config(**changes):
    config = {
        "version": "test-v1", "mechanical_shortlist_limit": 25,
        "deep_initial_limit": 20, "triage_batch_size": 25,
        "triage_concurrency": 1, "deep_reason_batch_size": 20, "fill_batch_size": 2,
        "sufficient_candidate_count": 20, "normal_before_reserve": True,
        "coverage_industry_min": 1, "coverage_seed_kind_min": 1,
        "coverage_potential_czy_min": 0, "selection_token_budget": 100_000,
        "max_total_deep": 20, "max_fill_rounds": 0,
        "cross_seed_merge_policy": "identity_only",
    }
    config.update(changes)
    return config


def _result(payload, *, usage=True):
    return LLMResult(
        ok=True, provider="stub", model="stub-model", content=json.dumps(payload, ensure_ascii=False),
        prompt_tokens=5 if usage else None, completion_tokens=7 if usage else None,
        total_tokens=12 if usage else None, raw_usage={"stub": True}, usage_unavailable=not usage,
    )


def _check(verdict="ok"):
    return {
        "verdict": verdict, "support": ["公开事实"], "counter_evidence": [],
        "missing": [], "reason": "已有公开证据支持判断",
    }


def _card_material(code):
    return {
        "upside_path": "驱动验证后沿均线逐步抬升",
        "entries": [{"ts_code": code, "low": 9.8, "high": 10.2,
                     "max_chase": 10.5, "exit_low": 11.2, "exit_high": 12.0,
                     "why": "回踩当日实体中枢观察"}],
        "verification": "多数成员守住支撑并继续放量",
        "invalidation": "多数成员跌破机械失效条件",
        "risks": ["事件兑现速度不及预期"], "tier_note": None,
    }


class _Provider:
    name = "stub"
    model = "stub-model"

    def __init__(self, kind: str, *, usage=True):
        self.kind = kind
        self.usage = usage
        self.calls = []

    def chat(self, messages, *, enable_search, search_query=None, transport=None):
        self.calls.append((messages, enable_search, search_query))
        request = json.loads(messages[-1].content.split("\n", 1)[1])
        if self.kind == "triage":
            return _result({"directions": [
                {"directionId": item["directionId"], "disposition": "deep", "reason": "机械方向完整"}
                for item in request["directions"]
            ]}, usage=self.usage)
        return _result({"directions": [
            {
                "directionId": item["directionId"], "decision": "candidate",
                "decisionReason": "驱动与成员均有公开证据",
                "name": item["brief"]["label"],
                "driver": "公开事件驱动", "driver_kind": "event", "why_now": "当日出现新证据",
                "common_trait": "都直接受同一事件驱动", "persistence": "仍在验证窗口",
                "strengthen_and_invalidate": "增量公告强化，事件撤回证伪", "evidence_conflicts": "",
                "engine_code": "Z", "market_check": _check(), "sector_check": _check(),
                "members": [{
                    "ts_code": item["brief"]["memberCodes"][0], "role": "core",
                    "reason": "是该方向的容量代表", "primary_claim": "yes",
                    "primary_claim_reason": "直接驱动、代表性与协同一致",
                    "position_check": _check(), "core_check": _check(),
                }],
                "narrative": "完整研究材料",
                "card_material": _card_material(item["brief"]["memberCodes"][0]),
            }
            for item in request["directions"]
        ]}, usage=self.usage)


class _TimedProvider(_Provider):
    def __init__(self, kind: str, clock, *, advance: float = 0):
        super().__init__(kind)
        self.clock = clock
        self.advance = advance

    def chat(self, messages, *, enable_search, search_query=None, transport=None):
        result = super().chat(
            messages, enable_search=enable_search,
            search_query=search_query, transport=transport,
        )
        self.clock[0] += self.advance
        return result


class _MalformedReasonProvider(_Provider):
    def __init__(self):
        super().__init__("reason")

    def chat(self, messages, *, enable_search, search_query=None, transport=None):
        self.calls.append((messages, enable_search, search_query))
        request = json.loads(messages[-1].content.split("\n", 1)[1])
        return _result({"directions": [{
            "directionId": item["directionId"], "decision": "candidate",
            "decisionReason": "声称可成篮", "name": item["brief"]["label"],
            "driver": "公开事件驱动", "driver_kind": "event", "why_now": "今日",
            "members": [], "narrative": "缺成员的坏结构",
        } for item in request["directions"]]})


class _MissingCardMaterialProvider(_Provider):
    def __init__(self):
        super().__init__("reason")

    def chat(self, messages, *, enable_search, search_query=None, transport=None):
        result = super().chat(
            messages, enable_search=enable_search, search_query=search_query, transport=transport,
        )
        payload = json.loads(result.content)
        for item in payload["directions"]:
            item.pop("card_material", None)
        return _result(payload)


class _Search:
    provider = "tavily"
    search_depth = "basic"

    def __init__(self, *, clock=None, advance: float = 0):
        self.clock = clock
        self.advance = advance
        self.calls = []

    def search(self, query, *, transport=None):
        self.calls.append((query, transport))
        if self.clock is not None:
            self.clock[0] += self.advance
        return TavilySearchResponse(
            ok=True, query=query,
            hits=(SearchHit(
                title="交易所公告", link="https://example.test/notice",
                media="交易所", publish_date="2026-08-14", content="公开事件驱动",
            ),),
            credits=1, request_id=f"search-{len(self.calls)}", wall_ms=int(self.advance * 1000),
        )


def _seeds(n=21):
    return [DriverSeed(
        seed_key=f"s-{index}", seed_kind="hot_industry" if index % 2 else "limit_cluster",
        label=f"方向{index}", member_codes=(f"000{index:03d}.SZ",),
        evidence={"industry": "医药" if index % 2 else "电子"},
    ) for index in range(n)]


def test_pipeline_sees_all_triages_without_search_and_only_deep_researches_initial_twenty(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage, search, reason = _Provider("triage"), _Search(), _Provider("reason")
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(), config=_config(), triage_provider=triage,
        research_client=search, reason_provider=reason, db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert len(outcome.briefs) == 21
    assert len(outcome.proposals) == 20
    assert len(triage.calls) == 1 and triage.calls[0][1] is False
    assert len(search.calls) == 20
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM selection_directions").fetchone()[0] == 21
        rows = conn.execute("SELECT task,enable_search,usage_unavailable FROM selection_llm_calls ORDER BY id").fetchall()
        searches = conn.execute(
            "SELECT provider,search_depth,credits,status FROM selection_search_calls ORDER BY id"
        ).fetchall()
        linked = conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition IN "
            "('triage_deep','research_complete','reasoning_complete') AND llm_call_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows[0] == ("direction_triage", 0, 0)
    assert all(row[1] == 0 for row in rows)
    assert rows[-1] == ("deep_reason", 0, 0)
    assert len(searches) == 20
    assert all(row == ("tavily", "basic", 1, "ok") for row in searches)
    assert linked == 41


def test_mechanical_shortlist_keeps_all_visible_but_only_triages_forty_eight(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage, search, reason = _Provider("triage"), _Search(), _Provider("reason")
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(60),
        config=_config(mechanical_shortlist_limit=48, max_total_deep=20),
        triage_provider=triage, research_client=search, reason_provider=reason,
        db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert len(outcome.briefs) == 60
    triaged = sum(len(json.loads(call[0][-1].content.split("\n", 1)[1])["directions"]) for call in triage.calls)
    assert triaged == 48
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM selection_directions").fetchone()[0] == 60
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition='mechanical_shortlisted'"
        ).fetchone()[0] == 48
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition='mechanical_reserve'"
        ).fetchone()[0] == 12
    finally:
        conn.close()


def test_deep_prompt_and_validator_share_the_presented_member_whitelist(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    seed = DriverSeed(
        seed_key="two-members", seed_kind="hot_industry", label="医药商业",
        member_codes=("600001.SH", "600002.SH"), evidence={"industry_rank": 1},
    )
    reason = _Provider("reason")
    outcome = run_direction_pipeline(
        date(2026, 8, 14), [seed], config=_config(),
        triage_provider=_Provider("triage"), research_client=_Search(),
        reason_provider=reason, allowed_members_by_seed={seed.seed_key: ("600002.SH",)},
        reason_context_builder=lambda _brief, _research: "authoritative-mechanical-context",
        db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert outcome.proposals[0]["members"][0]["ts_code"] == "600002.SH"
    request = json.loads(reason.calls[0][0][-1].content.split("\n", 1)[1])
    assert request["directions"][0]["brief"]["memberCodes"] == ["600002.SH"]
    assert request["directions"][0]["mechanicalContext"] == "authoritative-mechanical-context"


def test_deep_contract_error_is_unavailable_does_not_refill_and_keeps_prior_snapshot(isolated_env):
    from neckline.db import init_schema
    from neckline.selection.run_store import create_run, finish_run, latest_published_run_id

    init_schema(isolated_env.db_path)
    prior = create_run("20260814", _config(), run_id="prior-contract-safe", db_path=isolated_env.db_path)
    finish_run(prior, selection_state="complete", published=True, db_path=isolated_env.db_path)
    search = _Search()
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(25), config=_config(
            sufficient_candidate_count=99, max_total_deep=25, max_fill_rounds=3,
        ), triage_provider=_Provider("triage"), research_client=search,
        reason_provider=_MalformedReasonProvider(), db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "unavailable"
    assert len(search.calls) == 20  # first cohort only; contract failure cannot trigger refill
    assert latest_published_run_id("20260814", db_path=isolated_env.db_path) == prior
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute(
            "SELECT publication_state,stop_reason FROM selection_runs WHERE run_id=?", (outcome.run_id,)
        ).fetchone() == ("unavailable", "reasoning_contract_error")
    finally:
        conn.close()


def test_missing_card_material_is_contract_error_not_empty_success(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(1), config=_config(),
        triage_provider=_Provider("triage"), research_client=_Search(),
        reason_provider=_MissingCardMaterialProvider(), db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "unavailable"
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute(
            "SELECT stop_reason FROM selection_runs WHERE run_id=?", (outcome.run_id,)
        ).fetchone()[0] == "reasoning_contract_error"
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events "
            "WHERE transition='reasoning_contract_error'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_pipeline_stops_unavailable_when_actual_usage_is_missing(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage = _Provider("triage", usage=False)
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(2), config=_config(), triage_provider=triage,
        research_client=_Search(), reason_provider=_Provider("reason"), db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "unavailable"
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute("SELECT usage_unavailable FROM selection_llm_calls").fetchone()[0] == 1
        assert conn.execute("SELECT publication_state FROM selection_runs").fetchone()[0] == "unavailable"
    finally:
        conn.close()


def test_missing_direction_config_is_unavailable_and_keeps_prior_published_state(isolated_env):
    """An absent production config is never a route back to legacy first-20 selection."""
    from neckline.db import init_schema
    from neckline.selection.run_store import (
        create_run, finish_run, latest_publication_state, latest_published_run_id,
    )

    init_schema(isolated_env.db_path)
    prior = create_run("20260814", _config(), run_id="prior-complete", db_path=isolated_env.db_path)
    finish_run(prior, selection_state="complete", text="上一份冻结快照", published=True,
               db_path=isolated_env.db_path)

    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(2), config=None,
        triage_provider=None, research_client=None, reason_provider=None,
        db_path=isolated_env.db_path,
    )

    assert outcome.selection_state == "unavailable"
    assert latest_published_run_id("20260814", db_path=isolated_env.db_path) == prior
    # The live overlay truthfully says unavailable, while every frozen fact is
    # still resolved through the prior published run ID above.
    assert latest_publication_state("20260814", db_path=isolated_env.db_path) == {
        "selectionState": "unavailable",
        "selectionStateText": "今日选股配置尚未确认，保留上一份已完成结果。",
    }
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute(
            "SELECT publication_state, stop_reason FROM selection_runs WHERE run_id=?",
            (outcome.run_id,),
        ).fetchone() == ("unavailable", "configuration_unavailable")
    finally:
        conn.close()


def test_pipeline_fills_after_initial_twenty_until_real_qualification_callback_is_satisfied(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage, search, reason = _Provider("triage"), _Search(), _Provider("reason")
    rounds = []

    def qualified(proposals, _research):
        rounds.append(len(proposals))
        # Models can return 20 syntactically-valid answers that mechanical/gate
        # evaluation rejects. Only the second fill creates enough publishable
        # candidates for this explicitly configured target.
        return 0 if len(proposals) == 20 else 21

    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(22),
        config=_config(sufficient_candidate_count=21, max_total_deep=22, max_fill_rounds=1),
        triage_provider=triage, research_client=search, reason_provider=reason,
        qualification_callback=qualified, db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert rounds == [20, 22]
    assert len(search.calls) == 22
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition='fill_evaluated'"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_elapsed_wall_time_never_truncates_selection(
    isolated_env, monkeypatch,
):
    """Selection has no aggregate time cutoff; only Tokens/direction caps stop it."""
    from neckline.db import init_schema
    from neckline.selection import direction_pipeline

    init_schema(isolated_env.db_path)
    clock = [0.0]
    monkeypatch.setattr(direction_pipeline.time, "monotonic", lambda: clock[0])
    triage = _TimedProvider("triage", clock)
    search = _Search(clock=clock, advance=10_000)
    reason = _TimedProvider("reason", clock, advance=10_000)

    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(4),
        config=_config(
            deep_initial_limit=20, deep_reason_batch_size=2,
            max_total_deep=4,
        ),
        triage_provider=triage, research_client=search, reason_provider=reason,
        db_path=isolated_env.db_path,
    )

    assert outcome.selection_state == "complete"
    assert len(outcome.proposals) == 4
    assert len(reason.calls) == 2
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        tasks = [row[0] for row in conn.execute(
            "SELECT task FROM selection_llm_calls ORDER BY id"
        )]
    finally:
        conn.close()
    assert tasks == ["direction_triage", "deep_reason", "deep_reason"]


def test_each_completed_cohort_rechecks_sufficiency_and_stops_later_deep_work(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    search = _Search()
    reason = _Provider("reason")
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(10),
        config=_config(
            deep_reason_batch_size=2, sufficient_candidate_count=3,
            max_total_deep=10,
        ),
        triage_provider=_Provider("triage"), research_client=search,
        reason_provider=reason, qualification_callback=lambda proposals, _research: len(proposals),
        db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert len(outcome.proposals) == 4
    assert len(search.calls) == 4
    assert len(reason.calls) == 2
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition='deep_not_needed'"
        ).fetchone()[0] == 6
        assert conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition='fill_evaluated'"
        ).fetchone()[0] == 2
    finally:
        conn.close()


def test_observe_only_ignores_cost_budget_but_still_enforces_direction_caps(isolated_env, monkeypatch):
    from neckline.db import init_schema
    from neckline.selection import direction_pipeline

    init_schema(isolated_env.db_path)
    clock = [0.0]
    monkeypatch.setattr(direction_pipeline.time, "monotonic", lambda: clock[0])
    triage = _TimedProvider("triage", clock, advance=2)
    search = _Search(clock=clock, advance=2)
    reason = _TimedProvider("reason", clock, advance=2)

    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(35),
        config=_config(
            mechanical_shortlist_limit=35,
            selection_token_budget=1,
            max_total_deep=30, fill_batch_size=5, max_fill_rounds=2,
            sufficient_candidate_count=99,
        ),
        triage_provider=triage, research_client=search, reason_provider=reason,
        budget_mode="observe_only", db_path=isolated_env.db_path,
    )

    assert outcome.selection_state == "complete"
    assert len(outcome.proposals) == 30
    assert len(search.calls) == 30
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        config_json = conn.execute(
            "SELECT config_json FROM selection_runs WHERE run_id=?", (outcome.run_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert json.loads(config_json)["runtimeBudgetMode"] == "observe_only"
