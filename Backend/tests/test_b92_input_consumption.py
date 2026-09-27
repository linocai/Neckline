"""B92 time and consumption regressions through real collection/report entry points.

All source replies are deterministic examples, never provider smoke evidence.
"""
from datetime import datetime, timedelta, timezone
from pytest import MonkeyPatch

from neckline.k10 import store
from neckline.k10 import cli
from neckline.k10.collection_runtime import create_collection_handler
from neckline.k10.worker import run_once
from tests.test_b92_collection_flow import _cli, _configured


def _collect(database, config_id, revision, *, slot, published_at, content):
    class Source:
        endpoint = "https://mcp.jin10.com/mcp"
        protocol_version = "2025-11-25"

        def __init__(self, **kwargs):
            assert kwargs["token"] == "fixture-token"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def call_tool_raw(self, name, arguments):
            assert not arguments
            rows = [{"id": "stable-flash", "url": "https://flash.jin10.com/detail/stable-flash",
                     "title": None, "content": content, "time": published_at}] if name == "list_flash" else []
            return {"structuredContent": {"status": 200, "data": {
                "items": rows, "has_more": False, "next_cursor": None}}}

    at = datetime.fromisoformat(slot).astimezone(timezone.utc) + timedelta(minutes=1)
    class BusinessClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)
    with MonkeyPatch.context() as patch:
        patch.setattr(cli, "datetime", BusinessClock)
        task_id = _cli("enqueue-collection", "--db", str(database), "--slot", slot,
                       "--config-id", config_id, "--config-revision", str(revision))
    result = run_once(db_path=database, task_id=task_id, worker_id="time-fixture",
        lease_for=timedelta(minutes=5), clock=lambda: at, require_b76_contract=True,
        handlers={"collect_news": create_collection_handler(
            tushare_token=None, jin10_token="fixture-token", client_factory=Source)})
    assert result is not None and result.task_id == task_id
    task = store.task_execution_input(task_id=task_id, db_path=database)
    return task["checkpoint"]["sources"]["jin10-flash"]["documentRefs"]


def test_recollection_does_not_refresh_publication_or_create_a_revision(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    first = _collect(database, config_id, revision, slot="2026-09-26T08:00:00+08:00",
                     published_at="2026-09-24T07:00:00+08:00", content="旧消息的持续影响仍待判断。")
    second = _collect(database, config_id, revision, slot="2026-09-26T20:00:00+08:00",
                      published_at="2026-09-24T07:00:00+08:00", content="旧消息的持续影响仍待判断。")
    assert len(first) == len(second) == 1
    assert first[0]["documentId"] == second[0]["documentId"]
    assert first[0]["revision"] == second[0]["revision"] == 1
    assert first[0]["publishedAt"] == second[0]["publishedAt"] == "2026-09-24T07:00:00+08:00"
    assert first[0]["fetchedAt"] != second[0]["fetchedAt"]


def test_equivalent_publication_timezone_spelling_is_same_source_revision(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    first = _collect(database, config_id, revision, slot="2026-09-26T08:00:00+08:00",
                     published_at="2026-09-26T07:00:00+08:00", content="相同事实。")
    second = _collect(database, config_id, revision, slot="2026-09-26T20:00:00+08:00",
                      published_at="2026-09-25T23:00:00Z", content="相同事实。")
    assert first[0]["documentId"] == second[0]["documentId"]
    assert first[0]["revision"] == second[0]["revision"] == 1
    assert first[0]["contentSha256"] == second[0]["contentSha256"]


def test_new_content_is_a_new_revision_even_with_same_publication_time(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    first = _collect(database, config_id, revision, slot="2026-09-26T08:00:00+08:00",
                     published_at="2026-09-26T07:00:00+08:00", content="订单仍在谈判。")
    second = _collect(database, config_id, revision, slot="2026-09-26T20:00:00+08:00",
                      published_at="2026-09-26T07:00:00+08:00", content="更新：公司公告谈判已经终止。")
    assert first[0]["documentId"] == second[0]["documentId"]
    assert first[0]["revision"] == 1 and second[0]["revision"] == 2
    assert first[0]["contentSha256"] != second[0]["contentSha256"]


def test_uncertain_publication_is_never_replaced_with_collection_clock(tmp_path):
    database, config_id, revision = _configured(tmp_path)
    refs = _collect(database, config_id, revision, slot="2026-09-26T08:00:00+08:00",
                    published_at="时间待核", content="发布时间不明的原始材料。")
    assert len(refs) == 1
    assert refs[0]["publishedAt"] is None and refs[0]["fetchedAt"] is not None


def _next_evening(first, tmp_path, monkeypatch):
    from neckline.k10 import pipeline
    database = first["dbPath"]
    run_id, run_rev, exec_id, exec_rev, _, _ = first["bindings"]
    at = datetime(2026, 9, 27, 22, tzinfo=timezone(timedelta(hours=8)))
    class BusinessClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)
    monkeypatch.setattr(cli, "datetime", BusinessClock)
    next_task = _cli("enqueue", "--db", str(database), "--kind", "evening",
        "--trading-day", "2026-09-27", "--config-id", run_id,
        "--config-revision", str(run_rev), "--execution-config-id", exec_id,
        "--execution-config-revision", str(exec_rev))
    result = run_once(db_path=database, task_id=next_task, worker_id="next-evening-fixture",
        lease_for=timedelta(minutes=5), clock=lambda: at, require_b76_contract=True,
        handlers={"evening_scan": lambda context: pipeline.production_scan_handler(
            context, tushare_token=None, parquet_dir=tmp_path / "parquet", now=lambda: at)})
    assert result is not None and result.status == "completed"
    task = store.task_execution_input(task_id=next_task, db_path=database)
    return store.get_scan(scan_id=task["checkpoint"]["scanId"], db_path=database)


def test_next_evening_does_not_research_already_decided_flash_or_article_body(tmp_path, monkeypatch):
    from tests.test_b92_report_loopback import _make_evening

    first = _make_evening(tmp_path, monkeypatch)
    database = first["dbPath"]
    first_task = store.task_execution_input(task_id=first["eveningTaskId"], db_path=database)
    first_scan = store.get_scan(scan_id=first_task["checkpoint"]["scanId"], db_path=database)
    consumed = {(ref["documentId"], ref["revision"])
                for ref in first_scan["coverage"]["collectedInputConsumption"]["terminalRefs"]}
    body = first["collectionDocumentRef"]
    assert (body["documentId"], body["revision"]) in consumed
    prior_calls = len(first["transport"].calls)
    scan = _next_evening(first, tmp_path, monkeypatch)
    assert scan["coverage"]["collectedInput"]["inputDocumentRefs"] == []
    assert len(first["transport"].calls) == prior_calls


def test_late_collected_old_message_reaches_next_evening_without_an_age_gate(tmp_path, monkeypatch):
    from tests.test_b92_report_loopback import _make_evening, _FlashReportTransport
    from neckline.k10.windows import SHANGHAI

    first = _make_evening(tmp_path, monkeypatch)
    collection_id, collection_rev = first["bindings"][4:]
    refs = _collect(first["dbPath"], collection_id, collection_rev,
        slot="2026-09-27T08:00:00+08:00", published_at="2026-09-20T07:00:00+08:00",
        content="较早公布的测试合作仍在履行，尚需判断当前意义。")
    assert len(refs) == 1
    # At the model transport boundary, return a legitimate no-new-event
    # judgment for this source. The program must still show its full text to
    # that model; an age gate must not make the judgment for it.
    monkeypatch.setattr(_FlashReportTransport, "morning_document_ids", {refs[0]["documentId"]})
    scan = _next_evening(first, tmp_path, monkeypatch)
    frozen = scan["coverage"]["collectedInput"]["inputDocumentRefs"]
    assert [(ref["documentId"], ref["revision"]) for ref in frozen] == [
        (refs[0]["documentId"], refs[0]["revision"])]
    assert datetime.fromisoformat(frozen[0]["publishedAt"]).astimezone(SHANGHAI).date().isoformat() == "2026-09-20"
    assert datetime.fromisoformat(frozen[0]["fetchedAt"]).astimezone(SHANGHAI).date().isoformat() == "2026-09-27"
    assert scan["coverage"]["inputDocumentRefs"]
    assert scan["coverage"]["discoveryIssues"] == []
    assert ("understand:morning-no-new-development", refs[0]["documentId"]) in first["transport"].calls
