"""Regressions for the September 8 formal-run title protocol failure."""
from datetime import datetime, timedelta
import json
import sqlite3

import httpx
import pytest

from neckline.k10 import pipeline, store
from neckline.k10.cli import frozen_scan_input_sha256, recover_scan
from neckline.k10.title_triage import batch_request_spec, normalize_batch_result, TitleTriageProtocolError
from neckline.k10.worker import run_once
from neckline.llm.base import ChatMessage
from tests.test_b36_model_execution import provider, response
from tests.test_v306_title_triage import _items, POLICY
from tests.test_v306_pipeline import _setup, NOW
from tests.test_v310_pipeline_e2e import _run, _http_transport, RUN_AT


def _rows(count):
    return [{"i": i, "status": "candidate", "matterKey": f"matter-{i}",
             "stageKey": "new", "reason": "new fact"} for i in range(count)]


@pytest.mark.parametrize("count", [1, 64])
def test_exact_array_envelope_preserves_all_title_rows_and_metering(count):
    rows = _rows(count)
    wire = json.dumps(rows)
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
        response_format={"type": "json_object"}, json_array_key="items",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(content=wire))))
    assert result.ok and json.loads(result.content) == {"items": rows}
    normalized = normalize_batch_result(json.loads(result.content), _items(count))
    assert len(normalized["items"]) == count
    assert result.total_tokens == 150
    assert result.json_diagnostics["normalization"] == "single_array_envelope"
    assert result.raw_responses[0]["choices"][0]["message"]["content"] == wire


@pytest.mark.parametrize("rows", [_rows(63), _rows(64) + [_rows(1)[0]], [{**r, "i": 0} for r in _rows(64)]])
def test_array_normalization_does_not_relax_title_coverage(rows):
    with pytest.raises(TitleTriageProtocolError):
        normalize_batch_result({"items": rows}, _items(64))


@pytest.mark.parametrize("wire", ['[broken', '```json\n[]\n```', '42', '"private-output"'])
def test_invalid_roots_remain_safe_failures(wire, caplog):
    result = provider().chat([ChatMessage("user", "json")], enable_search=False,
        response_format={"type": "json_object"}, json_array_key="items",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response(content=wire))))
    assert not result.ok and result.error_code == "response_json_invalid"
    assert result.content == "" and result.total_tokens == 150
    assert "private-output" not in caplog.text
    assert result.json_diagnostics["contentLength"] == len(wire)


def test_same_task_title_recovery_reuses_completed_batch_and_only_retries_known_failed_batch(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    _, binding, model = _setup(path, count=0, duplicate=False)
    http_client = httpx.Client
    calls = []
    failing = True
    def respond(request):
        message = json.loads(request.content)["messages"][-1]["content"]
        payload = json.loads(message.split("<untrusted-k10-evidence>\n", 1)[1].split("\n</untrusted-k10-evidence>", 1)[0])
        calls.append(message)
        wire = "[broken" if failing and payload["items"][0]["title"] == "failed batch" else json.dumps(_rows(len(payload["items"])))
        return httpx.Response(200, json=response(content=wire))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: http_client(**{**kwargs, "transport": httpx.MockTransport(respond)}))
    first = _items(64)
    second = tuple(type(row)(row.document_id + "-next", row.revision, row.source_key, row.published_at,
                            "failed batch" if i == 0 else row.title) for i, row in enumerate(first))
    def invoke(wrapped, items):
        instruction, payload = batch_request_spec(items, POLICY)
        return wrapped.run_title_operation(stage="titleBatch", instruction=instruction, payload=payload,
                                           validate=lambda value: normalize_batch_result(value, items))
    complete = invoke(model, first)
    with pytest.raises(pipeline.PipelineError):
        invoke(model, second)
    assert len(calls) == 3
    assert "上次输出未通过校验" in calls[-1]
    failing = False
    resumed = pipeline._CheckpointedDiscoveryModel(base=model._base, task_id="titles", execution_profile=binding,
        cutoff_at=datetime.fromisoformat(NOW), db_path=path, leaseguard=None, allow_failed_research_resume=True)
    assert invoke(resumed, first) == complete and len(calls) == 3
    assert len(invoke(resumed, second)["items"]) == 64 and len(calls) == 4
    invoke(resumed, second)
    assert len(calls) == 4
    with sqlite3.connect(path) as conn:
        states = conn.execute("SELECT status,attempt_count FROM k10_execution_item_checkpoints WHERE stage='model:titleBatch'").fetchall()
    assert sorted(states) == [("completed", 1), ("completed", 1), ("failed", 2)]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE k10_execution_item_checkpoints SET status='running' WHERE status='completed'")
    with pytest.raises(pipeline.PipelineError) as unknown:
        invoke(resumed, first)
    assert unknown.value.code == "model_request_outcome_unknown" and len(calls) == 4


@pytest.mark.parametrize("legacy_partial", [False, True])
def test_real_cli_worker_recovers_frozen_failed_titles_and_publishes_same_scan(tmp_path, monkeypatch, legacy_partial):
    db, task_id, first, calls, gateway = _run(tmp_path, monkeypatch, title_response="invalid")
    assert first.status == "failed" and calls == ["titleBatch", "titleBatch"]
    checkpoint = store.task_execution_input(task_id=task_id, db_path=db)["checkpoint"]
    scan_id = checkpoint["scanId"]
    before = store.get_scan(scan_id=scan_id, db_path=db)
    assert before["status"] == "failed" and not gateway.search_paths
    digest = frozen_scan_input_sha256(scan_id=scan_id, db_path=db)
    if legacy_partial:
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE k10_scans SET status='partial' WHERE scan_id=?", (scan_id,))
    assert recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
                        confirmed_input_sha256=digest, now=RUN_AT) == task_id
    resumed_calls = _http_transport(monkeypatch, title_response="array")
    class NoRefetch:
        def __init__(self, **kwargs): pass
        def fetch_incremental(self, *_): pytest.fail("recovery refetched frozen titles")
    # Frozen recovery does not invoke any source adapters.
    monkeypatch.setattr(pipeline.TuShareMajorNewsAdapter, "fetch_incremental", NoRefetch.fetch_incremental)
    second = run_once(db_path=db, worker_id="recovery", lease_for=timedelta(minutes=5),
        handlers=pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=tmp_path / "parquet"), clock=lambda: RUN_AT)
    assert second.status == "completed"
    assert resumed_calls.count("titleBatch") == 1
    after = store.get_scan(scan_id=scan_id, db_path=db)
    assert after["coverage"]["inputDocumentRefs"] == before["coverage"]["inputDocumentRefs"]
    assert frozen_scan_input_sha256(scan_id=scan_id, db_path=db) == digest
    assert len(store.list_candidates(scan_id=scan_id, state="offered", db_path=db)) == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_batches WHERE scan_id=?", (scan_id,)).fetchone()[0] == 1
    with pytest.raises(RuntimeError):
        recover_scan(db_path=db, scan_id=scan_id, execution_config_id="b39-execution", execution_config_revision=1,
                     confirmed_input_sha256=digest, now=RUN_AT)
