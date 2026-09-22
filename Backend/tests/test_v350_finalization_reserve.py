"""B78 morning derives admission and final-order boundaries from frozen requests."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, time, timedelta
from io import StringIO
import socket
import sqlite3

from neckline.k10 import pipeline, store
from neckline.k10.cli import main as cli_main
from neckline.k10.metering import MeteredProvider
from neckline.k10.providers import ProviderResolution
from neckline.k10.windows import SHANGHAI
from neckline.k10.worker import run_once
from . import v340_acceptance_fixture as base
from .test_v350_cli_api import DirectRoundTransport, explicit_bindings


def test_morning_reserves_final_order_after_closing_new_research(tmp_path, monkeypatch):
    """A settled first round still gets one global rank after the next is refused."""
    # Seed the preceding trading day so morning publication can derive D0/D1/D2.
    day = base.DAY + timedelta(days=1)
    cutoff = datetime.combine(day, time(8, 30), tzinfo=SHANGHAI)
    deadline = cutoff.replace(hour=9, minute=20)
    db = tmp_path / "reserve.sqlite"
    config_id, config_revision, execution_id, execution_revision = base.seed_database(
        db, trading_day=day - timedelta(days=1), fixture_now=cutoff - timedelta(hours=1),
    )
    configuration = store.read_run_config(config_id=config_id, revision=config_revision, db_path=db)["payload"]
    # This entry point test needs exactly one in-flight direct round. Freeze a
    # valid execution revision with serial deep reads, then update the owned
    # isolated strategy binding before CLI enqueue creates the task binding.
    execution_payload = dict(store.read_execution_config(
        config_id=execution_id, revision=execution_revision, db_path=db,
    )["payload"])
    execution_payload["discovery"] = {**execution_payload["discovery"], "deepReadConcurrency": 1}
    execution_revision = store.append_execution_config(
        config_id=execution_id, payload=execution_payload, created_at=(cutoff - timedelta(minutes=2)).isoformat(), db_path=db,
    )
    execution = store.read_execution_config(config_id=execution_id, revision=execution_revision, db_path=db)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT content_json FROM k10_v2_strategy_snapshots WHERE snapshot_id=?", ("k10-v2-20260909",)).fetchone()
        content = __import__("json").loads(row[0])
        content.update({"executionConfigId": execution_id, "executionConfigRevision": execution_revision,
                        "executionSha256": execution["contentSha256"]})
        conn.execute(
            "UPDATE k10_v2_strategy_snapshots SET execution_config_id=?,execution_config_revision=?,content_json=? WHERE snapshot_id=?",
            (execution_id, execution_revision, __import__("json").dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")), "k10-v2-20260909"),
        )
    envelope = pipeline._morning_finalization_reserve(configuration=configuration, execution_profile=execution)
    finalization_at = deadline - envelope
    research_closeout_at = finalization_at - envelope
    # This is derived from the frozen request/retry envelope. The first direct
    # round is admitted one second before closeout; after it settles, the
    # business clock reaches the exact final-order boundary.
    business_clock = [research_closeout_at - timedelta(seconds=1)]

    class CloseAfterFirstRoundTransport(DirectRoundTransport):
        advanced = False

        def respond(self, request):
            payload = self._packet(request)
            # The second event is denied at the derived closeout boundary.  It
            # must name a different, known title-level company so its honest
            # exclusion cannot erase the first admitted company's final order.
            title_items = payload.get("items")
            if ("inputCount" not in payload and isinstance(title_items, list) and title_items
                    and all(isinstance(row, dict) and isinstance(row.get("title"), str) for row in title_items)):
                self._record("titleBatch")
                rows = []
                for index, row in enumerate(title_items):
                    number = self._title_number(row)
                    rows.append({
                        "i": index, "status": "candidate", "matterKey": f"matter-{number}",
                        "stageKey": "new", "reason": "标题含新事件",
                        "companyCodes": [self.company_codes[number]] if number < 2 else [],
                    })
                return self._ok({"items": rows})
            response = super().respond(request)
            if payload.get("action") == "research_round" and not self.advanced:
                self.advanced = True
                business_clock[0] = finalization_at
            return response

    monkeypatch.setattr(base, "TITLE_COUNT", 3)
    monkeypatch.setattr(base, "DeterministicTransport", CloseAfterFirstRoundTransport)
    transport, _ = base.install_offline_transports(
        monkeypatch, refusal_event=None, selected_event_count=2, fixture_run_at=business_clock[0],
    )
    monkeypatch.setattr(pipeline, "_now", lambda: business_clock[0])
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    provider = MeteredProvider(ledger_db=db, ledger_task="discovery", api_key="fixture",
        model="deepseek-v4-pro", name="fixture", api_url="https://fixture.invalid/v1/chat/completions",
        read_timeout=1, use_streaming=False)
    monkeypatch.setattr(pipeline, "resolve_deepseek_v4_pro",
                        lambda **_kwargs: ProviderResolution("configured", provider, "fixture", None))
    output = StringIO()
    with redirect_stdout(output):
        assert cli_main(["enqueue", "--db", str(db), "--kind", "morning", "--trading-day", day.isoformat(),
            "--config-id", config_id, "--config-revision", str(config_revision),
            "--execution-config-id", execution_id, "--execution-config-revision", str(execution_revision)]) == 0
    task_id = output.getvalue().strip()

    def handler(context):
        # Lease ownership stays on the live worker clock. The production handler
        # receives the explicit business clock that the real producer froze.
        return pipeline.production_scan_handler(
            replace(context, clock=lambda: business_clock[0]), tushare_token="fixture-token",
            parquet_dir=tmp_path / "parquet", now=lambda: business_clock[0],
        )

    task = run_once(db_path=db, task_id=task_id, worker_id="b78-reserve", lease_for=timedelta(minutes=5),
                    handlers={"morning_scan": handler}, clock=lambda: datetime.now(SHANGHAI),
                    require_b76_contract=True)
    assert task is not None and task.status == "completed"
    assert sum(name == "research:research_round" for name, _event in transport.calls) == 1
    assert sum(name == "prioritize" for name, _event in transport.calls) == 1
    with sqlite3.connect(db) as conn:
        event = conn.execute(
            "SELECT status,safe_error_code FROM k10_execution_item_checkpoints "
            "WHERE task_id=? AND item_kind='event' AND stage<>'research_input_boundary' ORDER BY item_key", (task_id,),
        ).fetchall()
        assert sorted(event) == [("completed", None), ("failed", "morning_closeout_reserve")]
        assert conn.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
    with base.actual_api(db, **explicit_bindings(db)) as client:
        response = client.get("/api/v1/k10/v2/reports/latest?window=morning")
        response.raise_for_status()
        report = response.json()["report"]
    assert report["status"] == "partial" and report["availableAt"]
    assert [card["companyCode"] for card in report["addedCards"]] == ["300002.SZ"]
    assert report["delivery"]["outcome"] == "partial"
    assert any(gap["reasonCode"] == "morning_closeout_reserve" for gap in report["delivery"]["gaps"])
    assert datetime.fromisoformat(report["deliveryDeadlineAt"]) == deadline.astimezone(report_tz := datetime.fromisoformat(report["deliveryDeadlineAt"]).tzinfo)
