"""R7 receipt-only recovery regressions using the real metered adapter and SQLite."""
from __future__ import annotations

import json
import sqlite3
from hashlib import sha256

import httpx
import pytest

from neckline.k10 import store
from neckline.k10.metering import provider_spend_context
from neckline.k10.research_store import create_research_snapshot, mark_research_round_failed
from neckline.k10.title_triage import (
    TitleDTO, TitleTriageProtocolError, TitleTriageResult, normalize_reconcile_result, reconcile_request_spec,
)
from neckline.llm.base import ChatMessage
from tests.test_v330_b69 import _receipt_provider, _restarted_receipt_provider
from tests.test_v341_review_repairs import _NOW, _research_adapter
from tests.test_v306_pipeline import _setup as _title_setup


def _authorize_failed_research_round(tmp_path, monkeypatch):
    """Create a real paid B81 receipt, then freeze the authorised semantic recovery."""
    db_path, adapter, snapshot, packet, calls = _research_adapter(tmp_path, monkeypatch)
    create_research_snapshot(snapshot=snapshot, db_path=db_path)
    original = adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)
    assert original.action == "research_round" and calls == ["/chat"]
    operation, item_key, _item, original_digest, _ledger_key, _row = adapter._research_operation_target(
        snapshot=snapshot, action="research_round", evidence_packet=packet,
    )
    assert adapter.reject_research_result(
        snapshot=snapshot, action="research_round", evidence_packet=packet,
        safe_error_code="investigation_result_invalid",
    )
    failed_snapshot = mark_research_round_failed(
        snapshot_id=snapshot.snapshot_id, expected_revision=snapshot.revision,
        input_packet=packet, safe_error_code="investigation_result_invalid",
        updated_at=_NOW, db_path=db_path,
    )
    with sqlite3.connect(db_path) as connection:
        checkpoint = json.loads(connection.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id='receipt-recovery'"
        ).fetchone()[0])
        checkpoint["recoveryAuthorized"] = {"failedModelInputSha256": [original_digest]}
        connection.execute(
            "UPDATE k10_tasks SET checkpoint_json=? WHERE task_id='receipt-recovery'",
            (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    return db_path, adapter, snapshot, packet, calls, operation, item_key, original_digest


@pytest.mark.parametrize(
    ("operation", "checkpoint_stage", "spend_stage"),
    (("understand", "understand", "lightweight"), ("map", "map", "map")),
)
@pytest.mark.parametrize("has_external_attempt", (False, True))
def test_paused_recovery_only_admits_first_post_when_exact_external_operation_is_absent(
    tmp_path, monkeypatch, operation, checkpoint_stage, spend_stage, has_external_attempt,
):
    """A paused non-title operation must not reopen a metered wire by stage mismatch.

    ``execution_paused`` means no socket only when its exact operation key has
    no external-attempt row.  The durable provider uses a spend-stage namespace
    (``lightweight`` for understand), so this exercises the mismatch that used
    to make a paid understand attempt look absent.  The semantic branch is
    subsequently receipt-only/recovery-blocked by the existing R7 proof gate;
    this focused assertion keeps the pre-wire admission decision exact.
    """
    db_path, adapter, _snapshot, _packet, _calls = _research_adapter(tmp_path, monkeypatch)
    item_key = "source-1@1:structural:fixture" if operation == "understand" else "event-1"
    item = {"fixture": operation, "scope": "exact"}
    digest, ledger_key, _row = adapter._research_checkpoint(
        operation=operation, stage=checkpoint_stage, item_key=item_key, item=item,
    )
    store.record_execution_checkpoint(
        task_id="receipt-recovery", item_kind="document" if operation == "understand" else "event",
        item_key=ledger_key, stage="model:" + operation, input_sha256=digest, status="failed",
        attempt_count=1, network_attempt_count=0, repair_attempt_count=0, elapsed_ms=0,
        input_tokens=None, output_tokens=None, result=None, safe_error_code="execution_paused",
        safe_error_ref=ledger_key, updated_at=_NOW, db_path=db_path,
    )
    with sqlite3.connect(db_path) as connection:
        checkpoint = json.loads(connection.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id='receipt-recovery'"
        ).fetchone()[0])
        checkpoint["recoveryAuthorized"] = {"failedModelInputSha256": [digest]}
        connection.execute(
            "UPDATE k10_tasks SET checkpoint_json=? WHERE task_id='receipt-recovery'",
            (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
    external_item_key = f"{operation}:{item_key}:{digest}"
    if has_external_attempt:
        attempt = store.begin_model_external_attempt(
            task_id="receipt-recovery", stage=spend_stage, item_key=external_item_key,
            attempt_key="b82-paused-" + operation, input_sha256=digest,
            reuse_scope_sha256="a" * 64, started_at=_NOW, db_path=db_path,
        )
        assert attempt["state"] == "started"

    recovered, _new_digest, _new_key, _new_row = adapter._recovery_target(
        operation=operation, stage=checkpoint_stage, item_key=item_key, item=item,
        eligible=lambda code: code == "execution_paused",
    )
    if has_external_attempt:
        assert recovered["authorizedSemanticRecoveryOf"] == digest
        assert "authorizedPausedBeforeExternalAttemptOf" not in recovered
        with sqlite3.connect(db_path) as connection:
            assert connection.execute(
                "SELECT stage,item_key,state FROM k10_external_attempts"
            ).fetchall() == [(spend_stage, external_item_key, "started")]
    else:
        assert recovered["authorizedPausedBeforeExternalAttemptOf"] == digest
        assert "authorizedSemanticRecoveryOf" not in recovered


def _append_newest_bad_but_hashed_receipt(
    *, db_path, operation: str, item_key: str, digest: str, include_metadata: bool,
) -> str:
    """Model a later answered reply: raw JSON is bad, immutable receipt hashes are valid.

    A provider normally prevents duplicate same-wire POSTs.  This fixture models
    an already durable historical retry record, then proves recovery evaluates
    both records instead of trusting its newest insertion.
    """
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        original_attempt = connection.execute(
            "SELECT * FROM k10_external_attempts WHERE task_id=? AND stage='investigation'",
            ("receipt-recovery",),
        ).fetchone()
        assert original_attempt is not None
        original_receipt = connection.execute(
            "SELECT * FROM k10_model_response_receipts WHERE attempt_id=?",
            (original_attempt["attempt_id"],),
        ).fetchone()
        assert original_receipt is not None

        payload = json.loads(original_receipt["payload_json"])
        assert payload.get("replayMetadata") == {
            "rendererRevision": "k10-investigation-v1", "repairFeedback": None,
        }
        if not include_metadata:
            # Simulate an actual B81 first response. It predates B82's optional
            # replay metadata, so only the frozen v1 renderer/wire/scope can
            # prove it. A historical repair without that feedback stays
            # unverifiable.
            payload.pop("replayMetadata")
            original_payload, original_payload_sha = store._receipt_payload(payload)
            connection.execute(
                "UPDATE k10_model_response_receipts SET payload_json=?,payload_sha256=? WHERE attempt_id=?",
                (original_payload, original_payload_sha, original_attempt["attempt_id"]),
            )
        payload["rawResponses"][0]["choices"][0]["message"]["content"] = "{not-json"
        canonical_payload, payload_sha = store._receipt_payload(payload)

        attempt_id = "r7-newest-semantic-invalid"
        attempt_columns = [row[1] for row in connection.execute("PRAGMA table_info(k10_external_attempts)")]
        attempt_values = {column: original_attempt[column] for column in attempt_columns}
        attempt_values["attempt_id"] = attempt_id
        attempt_values["attempt_key"] = "r7-newest-semantic-invalid"
        names = ",".join(attempt_columns)
        placeholders = ",".join("?" for _ in attempt_columns)
        connection.execute(
            f"INSERT INTO k10_external_attempts({names}) VALUES({placeholders})",
            tuple(attempt_values[column] for column in attempt_columns),
        )

        receipt_columns = [row[1] for row in connection.execute("PRAGMA table_info(k10_model_response_receipts)")]
        receipt_values = {column: original_receipt[column] for column in receipt_columns}
        receipt_values["attempt_id"] = attempt_id
        receipt_values["payload_json"] = canonical_payload
        receipt_values["payload_sha256"] = payload_sha
        names = ",".join(receipt_columns)
        placeholders = ",".join("?" for _ in receipt_columns)
        connection.execute(
            f"INSERT INTO k10_model_response_receipts({names}) VALUES({placeholders})",
            tuple(receipt_values[column] for column in receipt_columns),
        )

    receipts = store.load_model_response_receipts_for_operation(
        task_id="receipt-recovery", stage="investigation",
        item_key=f"{operation}:{item_key}:{digest}", db_path=db_path,
    )
    assert [receipt["attemptId"] for receipt in receipts] == [attempt_id, original_attempt["attempt_id"]]
    assert receipts[0]["payload"]["rawResponses"][0]["choices"][0]["message"]["content"] == "{not-json"
    return str(original_attempt["attempt_id"])


@pytest.mark.parametrize("include_metadata", [False, True])
def test_failed_research_recovery_scans_newest_bad_receipt_then_older_exact_reply_without_post(
    tmp_path, monkeypatch, include_metadata,
):
    db_path, adapter, snapshot, packet, calls, operation, item_key, digest = _authorize_failed_research_round(
        tmp_path, monkeypatch,
    )
    original_attempt_id = _append_newest_bad_but_hashed_receipt(
        db_path=db_path, operation=operation, item_key=item_key, digest=digest,
        include_metadata=include_metadata,
    )

    recovered = adapter.advance_research_round(snapshot=snapshot, evidence_packet=packet)

    assert recovered.action == "research_round"
    assert calls == ["/chat"]  # both candidate receipts were locally replayed
    with sqlite3.connect(db_path) as connection:
        attempts = connection.execute(
            "SELECT attempt_id,state FROM k10_external_attempts WHERE task_id='receipt-recovery' ORDER BY rowid"
        ).fetchall()
    assert attempts == [(original_attempt_id, "succeeded"), ("r7-newest-semantic-invalid", "succeeded")]


def test_receipt_only_recovery_rejects_cross_task_wire_and_scope_without_post(tmp_path):
    db_path, provider = _receipt_provider(tmp_path)
    posts: list[bytes] = []
    messages = [ChatMessage(role="user", content="frozen R7 identity")]
    kwargs = {"enable_search": False, "model_options": {"maxTokens": 128}}

    def answered(request: httpx.Request) -> httpx.Response:
        posts.append(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="doc", attempt=1):
        assert provider.chat(messages, **kwargs, transport=httpx.MockTransport(answered)).ok
    with sqlite3.connect(db_path) as connection:
        request_sha, scope_sha = connection.execute(
            "SELECT request_sha256,reuse_scope_sha256 FROM k10_model_response_receipts"
        ).fetchone()
        config_id, config_revision = connection.execute(
            "SELECT execution_config_id,execution_config_revision FROM k10_task_execution_bindings "
            "WHERE task_id='receipt-task'"
        ).fetchone()

    store.enqueue_task(task_id="other-task", kind="evening_scan", idempotency_key="other-task",
                       input_version="fixture", input_cutoff_at="2026-09-13T13:00:00+00:00", payload={}, budget={},
                       created_at="2026-09-13T13:00:00+00:00", db_path=db_path)
    store.bind_task_execution(task_id="other-task", execution_config_id=config_id,
                              execution_config_revision=config_revision, binding_kind="scheduled",
                              bound_at="2026-09-13T13:00:00+00:00", db_path=db_path)
    before_posts = list(posts)
    assert store.begin_model_external_attempt(
        task_id="other-task", stage="understand", item_key="doc", attempt_key="other-task-recovery",
        input_sha256=request_sha, reuse_scope_sha256=scope_sha,
        started_at="2026-09-20T04:10:00+00:00", db_path=db_path, receipt_only=True,
    )["state"] == "receipt_missing"
    assert store.begin_model_external_attempt(
        task_id="receipt-task", stage="understand", item_key="doc", attempt_key="different-wire-recovery",
        input_sha256="f" * 64, reuse_scope_sha256=scope_sha,
        started_at="2026-09-20T04:10:00+00:00", db_path=db_path, receipt_only=True,
    )["state"] == "receipt_missing"
    assert store.load_model_response_receipts_for_operation(
        task_id="receipt-task", stage="understand", item_key="doc", db_path=db_path,
        expected_request_sha256=request_sha, expected_reuse_scope_sha256="0" * 64,
    ) == ()
    assert posts == before_posts


def test_unknown_fee_outcome_blocks_receipt_recovery_without_post(tmp_path):
    db_path, provider = _receipt_provider(tmp_path)
    posts: list[bytes] = []
    messages = [ChatMessage(role="user", content="outcome and fee remain unknown")]
    kwargs = {"enable_search": False, "model_options": {"maxTokens": 128}}

    def uncertain(request: httpx.Request):
        posts.append(request.content)
        raise httpx.ReadTimeout("response lost after dispatch", request=request)

    with provider_spend_context(provider=provider, task_id="receipt-task", stage="understand", item_key="original", attempt=1):
        first = provider.chat(messages, **kwargs, transport=httpx.MockTransport(uncertain))
    assert first.error_code == "provider_request_outcome_unknown" and first.usage_unavailable

    restarted = _restarted_receipt_provider(db_path)
    with provider_spend_context(provider=restarted, task_id="receipt-task", stage="understand", item_key="recovery", attempt=1):
        blocked = restarted.chat(
            messages, **kwargs,
            transport=httpx.MockTransport(lambda _request: (_ for _ in ()).throw(AssertionError("fresh POST"))),
        )
    assert blocked.error_code == "provider_request_outcome_unknown" and blocked.usage_unavailable
    assert len(posts) == 1
    assert store.external_attempt_summary(task_id="receipt-task", db_path=db_path)["started"] == 1
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM k10_model_response_receipts").fetchone()[0] == 0


def _title_raw_value() -> dict[str, object]:
    return {
        "selectionComplete": True, "reviewedCount": 1,
        "selected": [{"i": 0, "selectedRank": 1, "reason": "新增实质进展"}], "merged": [],
    }


def _title_contract(binding):
    item = TitleDTO("article-0000", 1, "fixture", _NOW, "公司披露新增项目进展")
    results = (TitleTriageResult(
        item.document_id, item.revision, "candidate", "article-0000", "new", "新增实质进展",
    ),)
    policy = binding["payload"]["discovery"]["titleTriagePolicy"]
    instruction, payload = reconcile_request_spec((item,), results, 1, policy, compact_output=False)
    instruction += "同一发布的不同细节在本次全局归并中一并判断；筛选理由不是新增事实依据。纠正、否认及重大反证不得被普通正面消息吞并。"

    def validate(value):
        return normalize_reconcile_result(value, (item,), results, 1)

    return instruction, payload, validate, validate(_title_raw_value())


def _replace_receipt_payload(*, db_path, attempt_id: str, payload: dict[str, object]) -> None:
    canonical, digest = store._receipt_payload(payload)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE k10_model_response_receipts SET payload_json=?,payload_sha256=? WHERE attempt_id=?",
            (canonical, digest, attempt_id),
        )


def _prepare_b81_title_recovery_with_b82_repair(tmp_path, monkeypatch):
    """Create an old metadata-free first wire and one B82 repair receipt.

    The first raw reply is valid but a former semantic validator rejects it,
    which causes the real metered adapter to make its one repair request.  The
    first payload is then reduced to the actual B81 receipt shape while the
    repair keeps the B82 renderer/feedback provenance it needs for exact wire
    reconstruction.
    """
    db_path = tmp_path / "r7-title-recovery.sqlite"
    _documents, binding, model = _title_setup(db_path, count=1, duplicate=False)
    assert "titleReconcileContractVersion" not in binding["payload"]["discovery"]
    model._allow_failed_research_resume = True
    instruction, payload, validate, expected = _title_contract(binding)
    posts: list[bytes] = []
    client = httpx.Client

    def answered(request: httpx.Request) -> httpx.Response:
        posts.append(request.content)
        return httpx.Response(200, json={
            "choices": [{"message": {"role": "assistant", "content": json.dumps(_title_raw_value())},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(**{**kwargs, "transport": httpx.MockTransport(answered)}))
    validations = 0

    def reject_once(value):
        nonlocal validations
        validations += 1
        if validations == 1:
            error = TitleTriageProtocolError("历史语义校验误判")
            error.code = "title_json_contract_invalid"
            raise error
        return validate(value)

    assert model.run_title_operation(
        stage="titleReconcile", instruction=instruction, payload=payload, validate=reject_once,
    ) == expected
    assert len(posts) == 2
    item_key = sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    item = {"instruction": instruction, "payload": payload}
    original_digest, ledger_key, row = model._research_checkpoint(
        operation="titleReconcile", stage="titleReconcile", item_key=item_key, item=item,
    )
    assert row is not None and row[0] == "completed"
    assert store.reject_completed_execution_checkpoint(
        task_id="titles", item_kind="global", item_key=ledger_key, stage="model:titleReconcile",
        input_sha256=original_digest, safe_error_code="title_json_contract_invalid", updated_at=_NOW,
        db_path=db_path,
    )
    with sqlite3.connect(db_path) as connection:
        checkpoint = json.loads(connection.execute(
            "SELECT checkpoint_json FROM k10_tasks WHERE task_id='titles'"
        ).fetchone()[0])
        checkpoint["recoveryAuthorized"] = {"failedModelInputSha256": [original_digest]}
        connection.execute(
            "UPDATE k10_tasks SET checkpoint_json=? WHERE task_id='titles'",
            (json.dumps(checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
        rows = connection.execute(
            "SELECT r.attempt_id,r.request_sha256,r.payload_json FROM k10_model_response_receipts r "
            "WHERE r.task_id='titles' AND r.stage='titleReconcile' ORDER BY r.rowid"
        ).fetchall()
    assert len(rows) == 2 and rows[0][1] != rows[1][1]
    first_attempt, repair_attempt = rows[0][0], rows[1][0]
    first_payload, repair_payload = json.loads(rows[0][2]), json.loads(rows[1][2])
    assert first_payload.pop("replayMetadata") == {
        "rendererRevision": "k10-title-reconcile-v1", "repairFeedback": None,
    }
    assert repair_payload["replayMetadata"]["rendererRevision"] == "k10-title-reconcile-v1"
    assert isinstance(repair_payload["replayMetadata"]["repairFeedback"], dict)
    _replace_receipt_payload(db_path=db_path, attempt_id=first_attempt, payload=first_payload)
    return {
        "dbPath": db_path, "model": model, "instruction": instruction, "payload": payload,
        "posts": posts, "firstAttempt": first_attempt, "repairAttempt": repair_attempt,
        "repairPayload": repair_payload, "originalDigest": original_digest, "validate": validate,
        "expected": expected,
    }


def test_title_recovery_proves_b81_first_and_b82_repair_then_falls_back_from_newest_bad_reply(tmp_path, monkeypatch):
    state = _prepare_b81_title_recovery_with_b82_repair(tmp_path, monkeypatch)
    repair = state["repairPayload"]
    repair["rawResponses"][0]["choices"][0]["message"]["content"] = json.dumps({"unexpected": True})
    _replace_receipt_payload(db_path=state["dbPath"], attempt_id=state["repairAttempt"], payload=repair)

    recovered = state["model"].run_title_operation(
        stage="titleReconcile", instruction=state["instruction"], payload=state["payload"], validate=state["validate"],
    )

    assert recovered == state["expected"]
    assert len(state["posts"]) == 2
    with sqlite3.connect(state["dbPath"]) as connection:
        rows = connection.execute(
            "SELECT status,input_sha256 FROM k10_execution_item_checkpoints "
            "WHERE task_id='titles' AND stage='model:titleReconcile' ORDER BY updated_at"
        ).fetchall()
    assert any(row[0] == "failed" and row[1] == state["originalDigest"] for row in rows)
    assert any(row[0] == "completed" and row[1] != state["originalDigest"] for row in rows)


def test_title_recovery_rejects_unproven_b82_repair_without_post(tmp_path, monkeypatch):
    state = _prepare_b81_title_recovery_with_b82_repair(tmp_path, monkeypatch)
    repair = state["repairPayload"]
    repair.pop("replayMetadata")
    _replace_receipt_payload(db_path=state["dbPath"], attempt_id=state["repairAttempt"], payload=repair)

    with pytest.raises(Exception) as rejected:
        state["model"].run_title_operation(
            stage="titleReconcile", instruction=state["instruction"], payload=state["payload"], validate=state["validate"],
        )

    assert getattr(rejected.value, "code", None) == "provider_response_receipt_unverifiable"
    assert len(state["posts"]) == 2
