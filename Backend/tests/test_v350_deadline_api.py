"""A real morning task remains readable while its paid request is in flight."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, time
from io import StringIO
import socket
import sqlite3
from threading import Event, Thread

from neckline.api import k10 as api
from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, explicit_bindings


def test_morning_inflight_request_has_readonly_0920_result_from_real_producer(tmp_path, monkeypatch):
    # A future business date keeps the live coordination lease independent of
    # the frozen source/report clock; the clock advances explicitly below.
    day = datetime.now(SHANGHAI).date() + timedelta(days=1)
    morning = datetime.combine(day, time(8, 30), tzinfo=SHANGHAI)
    deadline = morning.replace(hour=9, minute=20)
    business_now = [morning]
    db = tmp_path / "morning.sqlite"
    config, revision, execution, execution_revision = base.seed_database(
        db, trading_day=day, fixture_now=morning - timedelta(hours=1))
    entered, release = Event(), Event()

    class BlockedTitleTransport(DirectRoundTransport):
        def respond(self, request):
            packet = self._packet(request)
            if "items" in packet and "inputCount" not in packet:
                entered.set()
                if not release.wait(30):
                    raise AssertionError("test did not release its in-flight request")
            return super().respond(request)

    monkeypatch.setattr(base, "TITLE_COUNT", 2)
    monkeypatch.setattr(base, "DeterministicTransport", BlockedTitleTransport)
    base.install_offline_transports(monkeypatch, refusal_event=None,
        selected_event_count=0, fixture_run_at=morning)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    monkeypatch.setattr(pipeline, "_now", lambda: business_now[0])
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1, use_streaming=False)
    provider.max_attempts = 1
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **kwargs: ProviderResolution("configured", provider, "fixture", None))
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main(["enqueue", "--db", str(db), "--kind", "morning", "--trading-day", day.isoformat(),
            "--config-id", config, "--config-revision", str(revision),
            "--execution-config-id", execution, "--execution-config-revision", str(execution_revision)]) == 0
    task_id = output.getvalue().strip()
    task = store.get_task(task_id=task_id, db_path=db)
    binding = store.task_execution_input(task_id=task_id, db_path=db)
    assert task is not None and binding is not None
    assert datetime.fromisoformat(binding["inputCutoffAt"]) == morning
    assert datetime.fromisoformat(task.payload["deliveryDeadlineAt"]) == deadline
    results, faults = [], []

    def handle(context):
        try:
            return pipeline.production_scan_handler(context, tushare_token="fixture-token",
                parquet_dir=tmp_path / "parquet", now=lambda: business_now[0])
        except BaseException as exc:
            faults.append(exc)
            raise

    def execute():
        results.append(run_once(db_path=db, worker_id="b78-deadline", lease_for=timedelta(minutes=5),
            handlers={"morning_scan": handle}, clock=lambda: datetime.now(SHANGHAI), require_b76_contract=True))

    thread = Thread(target=execute, name="b78-deadline-test")
    thread.start()
    try:
        assert entered.wait(15), {"results": results, "faults": faults}
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state='started'").fetchone()[0] == 1
            version = conn.execute("PRAGMA data_version").fetchone()[0]

            class BusinessDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return business_now[0].astimezone(tz) if tz else business_now[0].replace(tzinfo=None)

            monkeypatch.setattr(api, "datetime", BusinessDateTime)
            with base.actual_api(db, **explicit_bindings(db)) as client:
                before = client.get("/api/v1/k10/v2/reports/latest?window=morning")
                assert before.status_code == 200
                initial = before.json()
                assert initial["schemaVersion"] == 9 and initial["reason"]["reason"] == "report_processing"
                business_now[0] = deadline
                response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
                assert response.status_code == 200
                due = response.json()
                assert due["reason"]["reason"] == "delivery_deadline_reached"
                report = due["report"]
                assert report["reportId"] == initial["report"]["reportId"]
                assert datetime.fromisoformat(report["deliveryDeadlineAt"]) == deadline
                assert report["availableAt"] is None
                assert report["eveningCards"] == report["updatedCards"] == report["addedCards"] == []
                assert client.get(f"/api/v1/k10/v2/reports/{report['reportId']}").json()["reason"] == due["reason"]
            assert conn.execute("PRAGMA data_version").fetchone()[0] == version
            assert conn.execute("SELECT count(*) FROM k10_publication_samples").fetchone()[0] == 0
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive()
    assert not faults, faults
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM k10_publication_samples").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
