"""B90 selection is an editorial subset, exercised through the real producer."""
import json
import socket
import sqlite3
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta
from io import StringIO

import pytest

from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, read_actual_api, explicit_bindings
from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.worker import run_once
from neckline.k10.windows import SHANGHAI


@pytest.mark.parametrize("selection", ("company_subset", "catalyst_subset", "empty", "invalid"))
def test_final_editor_controls_exact_published_subset(tmp_path, monkeypatch, selection):
    editor_inputs = []
    expected = []

    class SelectionTransport(DirectRoundTransport):
        def respond(self, request):
            packet = self._packet(request)
            if "companies" in packet and "choices" in packet.get("output", {}):
                self._record("prioritize")
                rows = packet["companies"]
                editor_inputs.append(rows)
                company = next(row for row in rows if len(row["catalysts"]) == 2)
                retained = company["catalysts"][:1] if selection == "catalyst_subset" else company["catalysts"]
                choices = [{"companyCode": company["companyCode"], "catalystKeys": [
                    {"canonicalKey": item["canonicalKey"], "stageKey": item["stageKey"]}
                    for item in retained
                ]}]
                if selection == "empty":
                    choices = []
                elif selection == "invalid":
                    choices[0]["catalystKeys"] = [{"canonicalKey": "not-in-frozen-input", "stageKey": "initial"}]
                else:
                    expected.extend((company["companyCode"], item["canonicalKey"]) for item in retained)
                return self._ok({"choices": choices})
            response = super().respond(request)
            if packet.get("action") == "research_round" and response.status_code == 200:
                # An initial identity is derived locally. The researcher need
                # not duplicate bookkeeping or fill obsolete prose templates.
                value = json.loads(response.json()["choices"][0]["message"]["content"])
                for assessment in value["companyAssessments"]:
                    assessment.pop("identity", None)
                    for field in ("rank", "role", "summary", "priorityReason", "gap", "rankChangeConditions", "twoDayReason"):
                        assert field not in assessment
                return self._ok(value)
            return response

    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", SelectionTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name=selection,
                                   selected_event_count=3, expect_handler_failure=selection == "invalid")
    assert len(editor_inputs) == 1
    assert sorted(len(row["catalysts"]) for row in editor_inputs[0]) == [1, 2]
    for row in editor_inputs[0]:
        for catalyst in row["catalysts"]:
            assert catalyst["analysisText"] and catalyst["sourceRefs"]
    assert flow.calls.get("classify", 0) == 0
    envelope, _, _, _ = read_actual_api(flow.db_path)
    report = envelope["report"]
    assert envelope["schemaVersion"] == 10
    with sqlite3.connect(f"file:{flow.db_path}?mode=ro", uri=True) as conn:
        samples = conn.execute("SELECT s.company_code,e.stable_key FROM k10_publication_samples s "
                               "JOIN k10_events e ON e.event_id=s.event_id").fetchall()
        assert sorted(samples) == sorted(expected)
        assert conn.execute("SELECT count(*) FROM k10_company_windows").fetchone()[0] == len({code for code, _ in expected})
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()[0] == 0
    if selection == "invalid":
        assert flow.task_status == "failed"
        assert report["discovery"]["outcome"] == "not_completed"
        assert not report["availableAt"] and report["eveningCards"] == []
    else:
        assert flow.task_status == "completed"
        assert report["availableAt"] and report["delivery"]["outcome"] == "complete"
        assert report["discovery"]["outcome"] == ("no_recommendation" if selection == "empty" else "recommendations")
        cards = report["eveningCards"]
        assert len(cards) == len({code for code, _ in expected})
        assert sum(len(card["catalysts"]) for card in cards) == len(expected)
        for card in cards:
            for catalyst in card["catalysts"]:
                assert catalyst["analysisText"] == "消息显示送样进展，和该公司业务直接相关；订单仍待公司确认。"
                assert catalyst["sourceRefs"]


def test_b90_repeated_reason_keeps_original_identity_and_window(tmp_path, monkeypatch):
    """Removing the serial classifier must not turn refreshed news into a new opportunity."""
    monkeypatch.setattr(base, "TITLE_COUNT", 3)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    first = base.run_full_scale_flow(tmp_path, monkeypatch, name="identity", selected_event_count=1)
    assert first.task_status == "completed"
    original, = store.list_opportunities(db_path=first.db_path)
    binding = explicit_bindings(first.db_path)
    later = base.RUN_AT + timedelta(days=1)
    # The second producer needs the next exchange day to validate its window,
    # even when publication ultimately reuses the first opportunity.
    with sqlite3.connect(first.db_path) as conn:
        conn.execute("INSERT INTO trade_cal(exchange,cal_date,is_open) VALUES ('SSE',?,1)",
                     ((base.DAY + timedelta(days=3)).strftime("%Y%m%d"),))
    transport, _ = base.install_offline_transports(monkeypatch, refusal_event=None,
        selected_event_count=1, fixture_run_at=later)
    stdout = StringIO()
    with redirect_stdout(stdout):
        assert cli_main(["enqueue", "--db", str(first.db_path), "--kind", "evening",
            "--trading-day", later.date().isoformat(),
            "--config-id", binding["config_id"], "--config-revision", str(binding["config_revision"]),
            "--execution-config-id", binding["execution_id"],
            "--execution-config-revision", str(binding["execution_revision"])]) == 0
    task_id = stdout.getvalue().strip()
    failures = []
    def handler(context):
        try:
            return pipeline.production_scan_handler(
                replace(context, clock=lambda: later), tushare_token="fixture-token",
                parquet_dir=tmp_path / "repeat-parquet", now=lambda: later)
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
            raise
    result = run_once(db_path=first.db_path, task_id=task_id, worker_id="b90-repeat",
        lease_for=timedelta(minutes=5), clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True,
        handlers={"evening_scan": handler})
    assert result is not None and result.status == "completed", failures
    current, = store.list_opportunities(db_path=first.db_path)
    for field in ("opportunityId", "companyWindowId", "firstBatchId", "d1TradeDate", "d2TradeDate"):
        assert current[field] == original[field]
    assert not any(call == "classify" for call, _ in transport.calls)
    envelope, _, _, _ = read_actual_api(first.db_path)
    catalyst, = envelope["report"]["eveningCards"][0]["catalysts"]
    assert catalyst["opportunityId"] == original["opportunityId"]
    assert catalyst["analysisText"] and catalyst["sourceRefs"]
    with sqlite3.connect(first.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','running','unknown')").fetchone()[0] == 0
