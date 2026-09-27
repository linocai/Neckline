"""B91 real morning evidence delivery: all parent reasons and on-demand shared reads."""
from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
import json
import sqlite3
import sys
from pathlib import Path

from neckline.k10 import morning_runtime, pipeline
from neckline.k10.ingestion import SqliteIngestionWriter
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.sources import SourceDocumentInput
from neckline.k10.worker import run_once
from tests import v340_acceptance_fixture as base
from tests.test_v350_cli_api import DirectRoundTransport, explicit_bindings, generate_acceptance


def _append_shared_industry_original(*, database: Path, fetched_at: datetime) -> dict[str, int | str]:
    """Seed an already-persisted shared source without naming either company.

    The real morning producer must expose only its compact index at first, then
    the review agent selects this exact local original.  It is not injected
    into a report or work item.
    """
    saved = SqliteIngestionWriter(db_path=database).append_document_version(
        source_key="tushare-major-news",
        document=SourceDocumentInput(
            external_id="b91-industry-second-reason",
            canonical_url="https://fixture.invalid/b91/industry-second-reason",
            original_text="上游晶圆代工方公告：此前送样合作终止，后续不会继续该项目。",
            excerpt=None,
            published_at=fetched_at - timedelta(minutes=2),
            published_precision="exact",
            fetched_at=fetched_at,
            fetch_version="b91-morning-evidence",
            metadata={"title": "行业公告：送样合作终止"},
        ),
    )
    return {"documentId": saved.version.document_id, "revision": saved.version.revision}


def test_b91_real_morning_reads_non_named_shared_original_and_preserves_all_parent_reasons(tmp_path, monkeypatch):
    """A non-first reason can use a non-named shared original without body fanout."""
    evening = generate_acceptance(tmp_path / "producer", monkeypatch, scenario="complete")
    database = evening.database
    bindings = explicit_bindings(database)
    parent = evening.report["report"]
    multi = next(card for card in parent["eveningCards"] if len(card["catalysts"]) == 2)
    second_reason = multi["catalysts"][1]
    shared_ref = _append_shared_industry_original(
        database=database,
        fetched_at=datetime(2026, 9, 9, 8, 0, tzinfo=base.SHANGHAI),
    )
    parent_reason_ids = {
        card["companyCode"]: {catalyst["opportunityId"] for catalyst in card["catalysts"]}
        for card in parent["eveningCards"]
    }
    original_respond = DirectRoundTransport.respond
    observed = {"initialBodies": [], "sourceIndex": [], "secondContext": [], "readBodies": [],
                "independentOriginals": {}}

    def review_wire(self, request):
        wire = json.loads(request.content)
        message = wire["messages"][-1]["content"]
        if "<untrusted-evidence>" not in message:
            return original_respond(self, request)
        evidence = json.loads(message.split("<untrusted-evidence>\n", 1)[1].split("\n</untrusted-evidence>", 1)[0])
        company = evidence["original"]["candidate"]["companyCode"]
        reasons = evidence["parentReasons"]
        assert {reason["opportunityId"] for reason in reasons} == parent_reason_ids[company]
        if company == multi["companyCode"]:
            contexts = evidence["parentReasonContexts"]
            assert {context["opportunityId"] for context in contexts} == parent_reason_ids[company]
            second_context = next(context for context in contexts if context["opportunityId"] == second_reason["opportunityId"])
            assert all(context["candidateId"] and context["original"]["candidate"]["candidateId"] == context["candidateId"]
                       for context in contexts), "each catalyst must retain its own frozen candidate context"
            assert second_context["reasonDocuments"], "second frozen reason must carry its original document"
            expected_second_refs = {
                (ref["documentId"], ref["revision"])
                for ref in second_reason["sourceRefs"]
            }
            actual_second_refs = {
                (document["documentId"], document["revision"])
                for document in second_context["reasonDocuments"]
            }
            assert actual_second_refs == expected_second_refs
            assert any(
                document["originalText"] and (document["documentId"], document["revision"]) in expected_second_refs
                for document in second_context["reasonDocuments"]
            ), "the second catalyst's own original must enter the model wire"
            observed["secondContext"].append(second_context["reasonDocuments"])
            source_index = evidence["morningSourceIndex"]
            assert (any(
                item["documentId"] == shared_ref["documentId"] and item["revision"] == shared_ref["revision"]
                and item["title"] == "行业公告：送样合作终止"
                for item in source_index
            ) or any(
                item["documentId"] == shared_ref["documentId"] and item["revision"] == shared_ref["revision"]
                for item in evidence["morningDocuments"]
            )), "non-named shared material must remain selectable and then readable by identity"
            observed["sourceIndex"].append(source_index)
            has_shared_body = any(
                document["documentId"] == shared_ref["documentId"] and document["revision"] == shared_ref["revision"]
                for document in evidence["morningDocuments"]
            )
            observed["initialBodies"].append([document["documentId"] for document in evidence["morningDocuments"]])
            if not has_shared_body:
                return self._ok({
                    "action": "read", "documentId": shared_ref["documentId"], "revision": shared_ref["revision"],
                    "rationale": "行业公告直接涉及第二条送样理由，先读取已保存原件。",
                })
            observed["readBodies"].append(evidence["morningDocuments"])
        independent = evidence["independentVerificationDocuments"]
        if not independent:
            return self._ok({
                "action": "search", "question": "隔夜是否出现独立反证？",
                "query": f"离线晨报 {company} 独立核验",
                "rationale": "核验冻结公司及全部理由。",
            })
        original_document_ids = {
            document["documentId"] for document in independent
            if isinstance(document.get("originalText"), str) and document["originalText"].strip()
        }
        excerpt = next((document for document in independent
                        if document["documentId"] not in original_document_ids
                        and (not isinstance(document.get("originalText"), str) or not document["originalText"].strip())), None)
        if excerpt is not None:
            return self._ok({
                "action": "extract", "documentId": excerpt["documentId"], "revision": excerpt["revision"],
                "rationale": "独立搜索只给出摘录，需要读取该已返回来源的原文再判断。",
            })
        observed["independentOriginals"][company] = [
            {"documentId": document["documentId"], "revision": document["revision"]}
            for document in independent
            if isinstance(document.get("originalText"), str) and document["originalText"].strip()
        ]
        return self._ok({
            "action": "conclude", "material": False, "reasonStatus": "current",
            "observationStatus": "current",
            "summary": "已读取冻结理由、相关行业原件和独立资料，未见需要改变判断的隔夜事实。",
            "materialContraryEvidence": [],
        })

    monkeypatch.setattr(DirectRoundTransport, "respond", review_wire)
    monkeypatch.setattr(DirectRoundTransport, "loopback_morning_discovery_zero", True)
    morning_time = datetime(2026, 9, 9, 8, 35, tzinfo=base.SHANGHAI)

    def resolver(**_kwargs):
        provider = MeteredProvider(
            ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro", name="fixture",
            api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1, use_streaming=False,
        )
        provider.max_attempts = 1
        return ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolver)
    monkeypatch.setattr(pipeline, "_now", lambda: morning_time)
    stdout = StringIO()
    with base.redirect_stdout(stdout):
        assert base.cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", "2026-09-09",
            "--config-id", bindings["config_id"], "--config-revision", str(bindings["config_revision"]),
            "--execution-config-id", bindings["execution_id"],
            "--execution-config-revision", str(bindings["execution_revision"]),
        ]) == 0
    task_id = stdout.getvalue().strip()

    def handler(context):
        return pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=tmp_path / "parquet", now=lambda: morning_time,
        )

    terminal = run_once(
        db_path=database, task_id=task_id, worker_id="b91-evidence", lease_for=timedelta(minutes=5),
        handlers={"morning_scan": handler}, clock=lambda: morning_time, require_b76_contract=True,
    )
    assert terminal is not None and terminal.status == "completed"
    assert observed["sourceIndex"] and observed["secondContext"] and observed["readBodies"]
    assert shared_ref["documentId"] not in observed["initialBodies"][0], (
        "shared original must not fan out into every initial company payload"
    )
    assert any(
        document["documentId"] == shared_ref["documentId"] and document["originalText"].startswith("上游晶圆")
        for document in observed["readBodies"][-1]
    )

    with base.actual_api(database, **bindings) as client:
        response = client.get("/api/v1/k10/v2/reports/latest", params={"window": "morning"})
        assert response.status_code == 200
        report = response.json()["report"]
        item = next(row for row in report["morningReview"]["items"] if row["companyCode"] == multi["companyCode"])
        assert item["status"] == "completed" and item["outcome"] == "no_material_change"
        source_keys = {(ref["documentId"], ref["revision"]) for ref in item["sourceRefs"]}
        assert (shared_ref["documentId"], shared_ref["revision"]) in source_keys
        document = client.get(
            f"/api/v1/k10/documents/{shared_ref['documentId']}",
            params={"revision": shared_ref["revision"], "offset": 0, "limit": 6000},
        )
        assert document.status_code == 200
        page = document.json()
        assert page["contentKind"] == "original" and page["body"].startswith("上游晶圆")
        original_refs = observed["independentOriginals"][multi["companyCode"]]
        # Public Schema10 deliberately exposes the deduplicated material union
        # as sourceRefs.  The stored work item retains the independent-role
        # audit; the client must still be able to open the exact original that
        # this completed review actually used.
        assert original_refs and any((ref["documentId"], ref["revision"]) in source_keys for ref in original_refs)
        independent_ref = next(ref for ref in original_refs
                               if (ref["documentId"], ref["revision"]) in source_keys)
        independent_page = client.get(
            f"/api/v1/k10/documents/{independent_ref['documentId']}",
            params={"revision": independent_ref["revision"], "offset": 0, "limit": 6000},
        )
        assert independent_page.status_code == 200
        assert independent_page.json()["contentKind"] == "original"
        assert independent_page.json()["body"].startswith("离线独立来源")

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND state IN ('started','running','unknown')",
            (task_id,),
        ).fetchone() == (0,)


def test_b91_cached_dynamic_evidence_resumes_without_replaying_provider(tmp_path, monkeypatch):
    """A crash after the durable conclusion keeps its tool-derived evidence.

    The nested real CLI/worker scenario above performs a local shared read,
    independent search, and optional fulltext extraction.  Interrupt exactly
    after that complete conclusion is cached but before the parent marks its
    work item terminal.  Recovery must build the same API-visible report from
    the cached refs and coverage without opening another external operation.
    """
    from neckline.k10 import v2_store

    original_save = v2_store.save_morning_result
    original_run_once = run_once
    crashed = {"done": False}

    class CrashAfterDurableCache(BaseException):
        pass

    def save_then_crash(**kwargs):
        saved = original_save(**kwargs)
        if not crashed["done"]:
            crashed["done"] = True
            raise CrashAfterDurableCache()
        return saved

    def crash_then_reclaim(**kwargs):
        first_clock = kwargs["clock"]()
        try:
            original_run_once(**kwargs)
        except CrashAfterDurableCache:
            pass
        else:
            raise AssertionError("expected crash after durable morning cache")
        database = kwargs["db_path"]
        with sqlite3.connect(database) as connection:
            attempts_before_resume = connection.execute(
                "SELECT COUNT(*) FROM k10_external_attempts"
            ).fetchone()[0]
        resumed = dict(kwargs)
        resumed["worker_id"] = "b91-evidence-cache-reclaim"
        resumed["clock"] = lambda: first_clock + timedelta(minutes=6)
        terminal = original_run_once(**resumed)
        with sqlite3.connect(database) as connection:
            attempts_after_resume = connection.execute(
                "SELECT COUNT(*) FROM k10_external_attempts"
            ).fetchone()[0]
        assert attempts_after_resume == attempts_before_resume
        return terminal

    monkeypatch.setattr(v2_store, "save_morning_result", save_then_crash)
    monkeypatch.setattr(sys.modules[__name__], "run_once", crash_then_reclaim)
    test_b91_real_morning_reads_non_named_shared_original_and_preserves_all_parent_reasons(tmp_path, monkeypatch)
