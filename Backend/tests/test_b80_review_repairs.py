"""Review reproductions through actual producers/readers; all transports offline."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, time
from io import StringIO
import json
import sqlite3

import httpx
import pytest

from neckline.api import k10 as api
from neckline.k10 import store, v2_store
from neckline.k10.cli import main as cli_main
from neckline.k10.research_contracts import ResearchRoundResult
from neckline.k10.research_context import canonical_context_request
from neckline.k10.windows import SHANGHAI
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, explicit_bindings, read_actual_api
from .test_v350_research_round import (
    _runtime, _RoundModel, _RoundVerifier, _query, _question, REF,
    _snapshot, _packet, _document, _complete_round, research_round_request_spec,
)


@pytest.mark.parametrize("interrupt", [False, True])
def test_queued_morning_has_own_readonly_deadline_result(tmp_path, monkeypatch, interrupt):
    day = base.DAY
    morning = datetime.combine(day, time(8, 30), SHANGHAI)
    deadline = morning.replace(hour=9, minute=20)
    db = tmp_path / "queued.sqlite"
    config, revision, execution, execution_revision = base.seed_database(db)
    args = ["enqueue", "--db", str(db), "--kind", "morning", "--trading-day", day.isoformat(),
            "--config-id", config, "--config-revision", str(revision),
            "--execution-config-id", execution, "--execution-config-revision", str(execution_revision)]
    if interrupt:
        original = v2_store.write_report_materials
        def crash(*a, **kw):
            original(*a, **kw)
            raise RuntimeError("interrupt producer metadata write")
        monkeypatch.setattr(v2_store, "write_report_materials", crash)
        with pytest.raises(RuntimeError, match="interrupt producer"):
            cli_main(args)
        with sqlite3.connect(db) as conn:
            for table in ("k10_tasks", "k10_scans", "k10_v2_report_runs", "k10_task_execution_bindings"):
                assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        return
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main(args) == 0
    task_id = output.getvalue().strip()
    assert store.get_task(task_id=task_id, db_path=db).status == "queued"
    class BusinessDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return deadline.astimezone(tz) if tz else deadline.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", BusinessDateTime)
    with sqlite3.connect(db) as conn:
        version = conn.execute("PRAGMA data_version").fetchone()[0]
        with base.actual_api(db, **explicit_bindings(db)) as client:
            response = client.get("/api/v1/k10/v2/reports/latest?window=morning").json()
            report = response["report"]
            assert response["state"] == "available"
            assert response["reason"]["reason"] == "delivery_deadline_reached"
            assert report["status"] == "queued" and report["availableAt"] is None
            assert datetime.fromisoformat(report["deliveryDeadlineAt"]) == deadline
            assert not report["addedCards"] and not report["updatedCards"]
        assert conn.execute("PRAGMA data_version").fetchone()[0] == version
    # Repeated producer delivery preserves report identity and creates no call.
    with redirect_stdout(StringIO()):
        assert cli_main(args) == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_v2_report_runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM k10_external_attempts").fetchone()[0] == 0


def test_existing_report_remains_readable_when_active_configuration_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "TITLE_COUNT", 1)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="config-reading", selected_event_count=1)
    report = read_actual_api(flow.db_path)[0]["report"]
    with sqlite3.connect(flow.db_path) as conn:
        conn.execute("UPDATE k10_title_triage_policy_revisions SET approval_state='draft',approved_at=NULL")
    with base.actual_api(flow.db_path, **explicit_bindings(flow.db_path)) as client:
        assert any(row["state"] != "configured" for row in client.get("/api/v1/k10/configuration").json()["scopes"])
        actual = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()
        assert actual["state"] == "available" and actual["report"] == report
        assert client.get(f"/api/v1/k10/v2/reports/{report['reportId']}/materials").status_code == 200
        # Absence still truthfully says unconfigured; no strategy default.
        assert client.get("/api/v1/k10/v2/reports/latest?window=morning").json()["state"] == "not_configured"


def test_failed_negative_title_excludes_same_company_before_ranking(tmp_path, monkeypatch):
    original_news = base._FullScaleNews
    class NegativeNews(original_news):
        def fetch_incremental(self, request):
            result = super().fetch_incremental(request)
            return replace(result, documents=tuple(replace(doc, metadata={**doc.metadata,
                "title": doc.metadata["title"] + (" 300002.SZ 公司公告重大合同取消" if doc.external_id == "acceptance-0001" else "")})
                for doc in result.documents))
    class Refusal(DirectRoundTransport):
        def respond(self, request):
            packet = self._packet(request)
            if "items" in packet and "inputCount" not in packet and any("重大合同取消" in row["title"] for row in packet["items"]):
                self._record("negative_title_refused")
                return httpx.Response(400, json={"error": {"code": "invalid_request_error", "message": "Content Exists Risk"}})
            if "candidates" in packet and "choices" in packet.get("output", {}):
                assert "300002.SZ" not in {row["companyCode"] for row in packet["candidates"]}
            return super().respond(request)
    monkeypatch.setattr(base, "_FullScaleNews", NegativeNews)
    monkeypatch.setattr(base, "TITLE_COUNT", 130)
    monkeypatch.setattr(base, "DeterministicTransport", Refusal)
    flow = base.run_full_scale_flow(tmp_path, monkeypatch, name="negative-title", selected_event_count=12)
    report = read_actual_api(flow.db_path)[0]["report"]
    assert flow.calls["negative_title_refused"] == 1
    assert report["delivery"]["outcome"] == "partial"
    assert "300002.SZ" not in {row["companyCode"] for row in report["eveningCards"]}
    assert report["eveningCards"], "unaffected companies still receive the partial report"
    assert any("300002.SZ" in row["companyCodes"] for row in report["delivery"]["gaps"])


def test_round_generates_execution_ids_and_states_without_changing_business_refs():
    query = {key: value for key, value in _query().to_dict().items() if key not in {"pathId", "state", "resultSummary"}}
    fulltext = {"questionId": "q-order", "sourceRef": REF,
                "reasonExcerptInsufficient": "缺少限定条件", "expectedJudgmentChange": "影响关联"}
    raw = {"action": "research_round", "questions": [_question().to_dict()],
           "queryPaths": [query], "fulltextRequests": [fulltext],
           "conclusion": {"researchStatus": "continue_research"}}
    first = ResearchRoundResult.from_dict(raw)
    assert first == ResearchRoundResult.from_dict(raw)
    assert first.query_paths[0].path_id and first.query_paths[0].state == "planned"
    assert first.fulltext_requests[0].request_id and first.fulltext_requests[0].state == "requested"
    assert first.query_paths[0].question_id == "q-order" and first.fulltext_requests[0].source_ref == REF
    contract = research_round_request_spec(snapshot=_snapshot(), evidence_packet=_packet())[1]["outputContract"]
    assert not {"pathId", "state", "resultSummary"} & contract["queryPaths"][0].keys()
    assert not {"requestId", "state", "admissionRef"} & contract["fulltextRequests"][0].keys()

    # New model replies cannot claim execution that the program has not done.
    query.update(state="searched", resultSummary="模型声称已完成")
    fulltext.update(state="fulfilled", admissionRef=REF)
    assert ResearchRoundResult.from_dict(raw) == first
    legacy = {**raw, "queryPaths": [{**query, "pathId": "paid-path"}],
              "fulltextRequests": [{**fulltext, "requestId": "paid-fulltext"}]}
    restored = ResearchRoundResult.from_dict(legacy)
    assert restored.query_paths[0].state == "searched"
    assert restored.query_paths[0].result_summary == "模型声称已完成"
    assert restored.fulltext_requests[0].state == "fulfilled"
    assert restored.fulltext_requests[0].admission_ref == REF
    assert ResearchRoundResult.from_dict(legacy, model_reply=True) == first


@pytest.mark.parametrize("explicit_id", [False, True])
def test_model_execution_state_cannot_skip_necessary_search(tmp_path, explicit_id):
    query = {key: value for key, value in _query().to_dict().items() if key != "pathId"}
    query.update(state="searched", resultSummary="模型声称已搜索")
    if explicit_id:
        query["pathId"] = "model-supplied-id"
    model = _RoundModel([{"action": "research_round", "questions": [_question().to_dict()],
        "queryPaths": [query], "conclusion": {"researchStatus": "continue_research", "companyMappings": []}},
        _complete_round()])
    verifier = _RoundVerifier(search_document=_document("search-confirmation",
        text="独立公告：公司尚未签约。", excerpt="公司尚未签约。"))
    runtime = _runtime(tmp_path, model=model, verifier=verifier)
    runtime._run_b78()
    assert len(verifier.search_paths) == 1
    assert len(model.calls) == 2


def test_same_material_spelling_does_not_drive_a_third_model_round(tmp_path):
    def read(unused):
        return {"action": "research_round", "contextRequests": [{"kind": "source", "sourceRef": REF,
            "location": "excerpt", "purpose": "核对当前问题", "id": unused}]}
    model = _RoundModel([read("placeholder-a"), read("placeholder-b")])
    runtime = _runtime(tmp_path, model=model, verifier=_RoundVerifier())
    runtime._run_b78()
    assert len(model.calls) == 2, "unchanged material cannot justify another provider call"
    assert canonical_context_request({"kind": "company_fields", "companyCode": "000001.SZ", "fields": ["summary", "business"]}) == canonical_context_request({"kind": "company_fields", "companyCode": "000001.SZ", "fields": ["business", "summary", "summary"], "id": "irrelevant"})
