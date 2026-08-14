from __future__ import annotations

import json
import sqlite3
from datetime import date

from neckline.llm.base import LLMResult
from neckline.scan.seeds import DriverSeed
from neckline.selection.direction_pipeline import run_direction_pipeline


def _config(**changes):
    config = {
        "version": "test-v1", "deep_initial_limit": 20, "triage_batch_size": 25,
        "triage_concurrency": 1, "deep_reason_batch_size": 20, "fill_batch_size": 2,
        "sufficient_candidate_count": 20, "normal_before_reserve": True,
        "coverage_industry_min": 1, "coverage_seed_kind_min": 1,
        "coverage_potential_czy_min": 0, "selection_token_budget": 100_000,
        "selection_wall_seconds": 600, "max_total_deep": 20, "max_fill_rounds": 0,
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
        if self.kind == "search":
            return _result({"evidence": [{"claim": "公告", "source": "交易所", "date": "2026-08-14"}]}, usage=self.usage)
        return _result({"directions": [
            {
                "directionId": item["directionId"], "name": item["brief"]["label"],
                "driver": "公开事件驱动", "driver_kind": "event", "why_now": "当日出现新证据",
                "seed_keys": [item["brief"]["seedKey"]], "members": [], "narrative": "完整研究材料",
                "card_material": {"upside_path": "验证后延续"},
            }
            for item in request["directions"]
        ]}, usage=self.usage)


def _seeds(n=21):
    return [DriverSeed(
        seed_key=f"s-{index}", seed_kind="hot_industry" if index % 2 else "limit_cluster",
        label=f"方向{index}", member_codes=(f"000{index:03d}.SZ",),
        evidence={"industry": "医药" if index % 2 else "电子"},
    ) for index in range(n)]


def test_pipeline_sees_all_triages_without_search_and_only_deep_researches_initial_twenty(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage, search, reason = _Provider("triage"), _Provider("search"), _Provider("reason")
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(), config=_config(), triage_provider=triage,
        research_provider=search, reason_provider=reason, db_path=isolated_env.db_path,
    )
    assert outcome.selection_state == "complete"
    assert len(outcome.briefs) == 21
    assert len(outcome.proposals) == 20
    assert len(triage.calls) == 1 and triage.calls[0][1] is False
    assert len(search.calls) == 20 and all(call[1] is True for call in search.calls)
    conn = sqlite3.connect(isolated_env.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM selection_directions").fetchone()[0] == 21
        rows = conn.execute("SELECT task,enable_search,usage_unavailable FROM selection_llm_calls ORDER BY id").fetchall()
        linked = conn.execute(
            "SELECT count(*) FROM selection_direction_events WHERE transition IN "
            "('triage_deep','research_complete','reasoning_complete') AND llm_call_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows[0] == ("direction_triage", 0, 0)
    assert all(row[1] == 1 for row in rows[1:21])
    assert rows[-1] == ("deep_reason", 0, 0)
    assert linked == 61


def test_pipeline_stops_unavailable_when_actual_usage_is_missing(isolated_env):
    from neckline.db import init_schema

    init_schema(isolated_env.db_path)
    triage = _Provider("triage", usage=False)
    outcome = run_direction_pipeline(
        date(2026, 8, 14), _seeds(2), config=_config(), triage_provider=triage,
        research_provider=_Provider("search"), reason_provider=_Provider("reason"), db_path=isolated_env.db_path,
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
        triage_provider=None, research_provider=None, reason_provider=None,
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
    triage, search, reason = _Provider("triage"), _Provider("search"), _Provider("reason")
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
        triage_provider=triage, research_provider=search, reason_provider=reason,
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
