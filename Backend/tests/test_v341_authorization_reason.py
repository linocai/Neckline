"""R09: real analysis producer, metered HTTP failure, worker, storage and API."""
from datetime import datetime, timedelta
import json
import sqlite3

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from neckline.api.k10 import create_router
from neckline.k10 import runtime, store
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.worker import run_once
from tests.k10_v302_fixture import build_fixture
from tests.test_k10_api import _freeze_k10_clocks


def test_authorization_refusal_keeps_its_reason_through_real_worker_and_api(tmp_path, monkeypatch):
    path = tmp_path / "authorization.sqlite"
    ids = build_fixture(path)
    at = "2026-08-31T01:20:00+00:00"
    _freeze_k10_clocks(monkeypatch, at)
    app = FastAPI()
    app.include_router(create_router(lambda: path, lambda: None, lambda: tmp_path / "parquet"))
    with TestClient(app) as client:
        created = client.post(f"/api/v1/k10/company-windows/{ids['primaryWindowId']}/analysis-requests",
                              json={"kind": "user_question", "question": "核对公开业务进展", "sourceRefs": [],
                                    "idempotencyKey": "authorization-regression"})
        assert created.status_code == 200, created.text
        task_id = created.json()["analysisJobId"]
        revision = created.json()["revision"]
        assert store.task_execution_profile(task_id=task_id, db_path=path) is not None
        calls = []

        def refuse(request):
            calls.append(request.url.path)
            return httpx.Response(401, json={"error": {"message": "private-provider-diagnostic"}})

        original_client = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
            **{**kwargs, "transport": httpx.MockTransport(refuse)}))
        provider = MeteredProvider(ledger_db=path, ledger_task=task_id, api_key="offline-key",
                                   model="deepseek-flash", name="fixture", use_streaming=False,
                                   api_url="https://api.deepseek.com/chat/completions", read_timeout=1)
        resolver = lambda **_: ProviderResolution("configured", provider, "fixture", None)
        task = run_once(db_path=path, task_id=task_id, worker_id="authorization-test",
                        lease_for=timedelta(minutes=5), clock=lambda: datetime.fromisoformat(at),
                        require_b76_contract=True,
                        handlers={"analysis": runtime.production_analysis_handler(provider_resolver=resolver)})
        assert task is not None and task.status == "failed"
        assert calls == ["/chat/completions"]
        with sqlite3.connect(path) as conn:
            attempts = conn.execute("SELECT state,error_code FROM k10_external_attempts WHERE task_id=?", (task_id,)).fetchall()
            assert attempts == [("failed", "provider_authorization_failed")]
            contents = [json.loads(row[0]) for row in conn.execute("SELECT content_json FROM k10_analysis_revisions")]
            failures = [item for item in contents if item.get("providerErrorCode") == "provider_authorization_failed"]
            assert len(failures) == 1
            assert failures[0]["error"] == "模型服务鉴权或权限校验失败，任务已停止"
        response = client.get(f"/api/v1/k10/company-windows/{ids['primaryWindowId']}/analysis-chain")
        assert response.status_code == 200, response.text
        item = next(row for row in response.json()["items"] if row["revision"] == revision)
        assert item["job"]["status"] == "failed"
        assert len(item["analyses"]) == 1
        assert item["analyses"][0]["error"] == "模型服务鉴权或权限校验失败，任务已停止"
        assert "余额不足" not in json.dumps(item, ensure_ascii=False)
        assert "private-provider-diagnostic" not in response.text
