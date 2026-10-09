"""B90 real CLI/worker/API acceptance with deterministic, network-denied replies.

The 1,089-member universe is unchanged.  A small title corpus crosses batch
boundaries; this tests user results, not the old sequence of model stages.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import StringIO
import json
from pathlib import Path
import socket
import sqlite3
from typing import Any, ClassVar

import httpx
import pytest

from neckline.k10.delivery import REPORT_DELIVERY_CONTRACT
from neckline.k10 import morning_runtime
from . import v340_acceptance_fixture as base


TITLE_COUNT = 130
EVENT_COUNT = 3
SCENARIOS = ("complete", "partial", "materials", "empty", "zero")


class DirectRoundTransport(base.DeterministicTransport):
    # Only the two-report API loopback turns the second (morning-discovery)
    # title path into a normal zero.  The evening parent remains populated;
    # this keeps the test focused on frozen-parent review/API semantics rather
    # than reusing an older event's research receipt across two business
    # cutoffs.
    loopback_morning_discovery_zero: ClassVar[bool] = False

    def respond(self, request):
        # Morning review is deliberately a different wire shape from the
        # discovery protocol: it receives one frozen parent-company packet
        # and the independently frozen overnight documents.  Keep this reply
        # here, at the exact MeteredProvider boundary, so the B90 loopback
        # proves real review work rather than inserting a result row.
        wire = json.loads(request.content)
        system = wire["messages"][0]["content"]
        message = wire["messages"][-1]["content"]
        if "<untrusted-evidence>" in message:
            evidence = json.loads(message.split("<untrusted-evidence>\n", 1)[1].split("\n</untrusted-evidence>", 1)[0])
            independent = evidence.get("independentVerificationDocuments")
            reasons = evidence.get("parentReasons")
            company = reasons[0].get("companyCode") if isinstance(reasons, list) and reasons and isinstance(reasons[0], dict) else None
            if not isinstance(company, str) or not company:
                company = self.company_codes[0]
            if not independent:
                self._record("morningReviewSearch", company)
                return self._ok({
                    "action": "search", "question": "是否有公司披露直接反证前一晚理由？",
                    "query": f"离线晨报 {company} 独立核验", "rationale": "需要独立公司资料核验冻结理由。",
                })
            self._record("morningReview", company)
            return self._ok({
                "action": "conclude", "material": False,
                "reasonStatus": "current",
                "observationStatus": "current",
                "summary": "隔夜资料未显示足以改变昨晚关联判断的新事实。",
                "materialContraryEvidence": [],
            })
        payload = self._packet(request)
        action = payload.get("action")
        if action == "research_round":
            packet = payload["evidencePacket"]
            event = packet["event"]["canonicalKey"]
            self._record("research:research_round", event)
            if self.refusal_event is not None and base._event_number(event) == self.refusal_event:
                return httpx.Response(400, json={"error": {
                    "code": "invalid_request_error", "message": "Content Exists Risk",
                }})
            ref = self._ref(payload)
            code = self._company_for_event(event)
            return self._ok({
                "action": action,
                "conclusion": {
                    "researchStatus": "ready_for_comparison", "eventDisposition": "可比较",
                    "companyMappings": [{"companyCode": code, "affectedStage": "送样",
                        "relationEvidence": [ref], "inference": {"relation": "离线验收的公司事件关联"},
                        "uncertainty": "公司尚未公开确认订单"}],
                    "companyDispositions": [], "materialGaps": ["订单未获公司确认"],
                    "stopReason": "已有资料足以比较，明确保留未知", "resumeCondition": "公司新公告",
                },
                "comparison": {"summary": "仅为离线验收，送样不是订单。", "evidenceRefs": [ref],
                               "historicalAssessments": []},
                "companyAssessments": [{"companyCode": code, "recommendation": "recommend",
                    "analysisText": "消息显示送样进展，和该公司业务直接相关；订单仍待公司确认。", "sourceRefs": [ref],
                    "identity": {"kind": "initial", "relatedOpportunityId": None,
                                 "reason": "本次来源首次形成该公司的可读催化", "newFacts": "送样进展",
                                 "changedJudgment": None, "twoDayReason": "观察公司是否确认订单"},
                    "evidenceDisclosure": {
                        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
                        "originEvidenceRef": None, "unverifiedReasons": ["未有公司确认"],
                        "conditionalAnalysis": "公司确认后复核。",
                    }}],
            })
        if isinstance(action, str):
            self._record("forbidden:" + action)
            raise AssertionError(f"B78 attempted a retired mandatory research stage: {action}")
        if payload.get("operation") == "titleSelectionReview":
            self._record("forbidden:titleSelectionReview")
            raise AssertionError("B78 attempted the retired third title-model review")
        if "inputCount" in payload:
            self._record("titleGlobal")
            selected = sorted((row for row in payload["items"]
                if not type(self).loopback_morning_discovery_zero
                and self._number_from_title(row["title"]) < self.selected_event_count),
                key=lambda row: self._number_from_title(row["title"]))
            return self._ok({"selectionComplete": True, "reviewedCount": len(payload["items"]),
                "selected": [{"i": row["i"], "selectedRank": index + 1,
                              "reason": "新增事件值得核验"} for index, row in enumerate(selected)], "merged": []})
        if "companies" in payload and isinstance(payload.get("output"), dict) and "choices" in payload["output"]:
            if self.refusal_event == 1:
                codes = {row["companyCode"] for row in payload["companies"]}
                if codes != {self.company_codes[1]}:
                    self._record("forbidden:failed_company_in_final_ranking")
                    raise AssertionError("known failed company must be removed before final ranking")
        if "items" in payload and "inputCount" not in payload:
            # Supply the known association at the actual title-model boundary.
            # A later refusal cannot erase this prior scope and retain only A's
            # successful event. These are routing hints, not verified relations.
            self._record("titleBatch")
            return self._ok({"items": [
                {"i": index, "status": "candidate", "matterKey": f"matter-{self._title_number(row)}",
                 "stageKey": "new", "reason": "标题含新事件",
                 "companyCodes": [self._company_for_event(f"event-{self._number_from_title(row['title']):03d}")]
                    if self._number_from_title(row["title"]) < self.selected_event_count else []}
                for index, row in enumerate(payload["items"])
            ]})
        return super().respond(request)


@dataclass(frozen=True)
class Acceptance:
    database: Path
    report: dict[str, Any]
    materials: dict[str, Any] | None
    readiness: dict[str, Any]
    configuration: dict[str, Any]
    provenance: dict[str, Any]


@dataclass(frozen=True)
class B90Loopback:
    """Real B90 evening + next-morning producer output for API consumers."""

    database: Path
    evening_report_id: str
    morning_report_id: str
    bindings: dict[str, Any]


def explicit_bindings(database):
    """Use the explicitly bound fixture strategy, never whichever revision is latest."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT config_id,config_revision,execution_config_id,execution_config_revision "
                           "FROM k10_v2_strategy_snapshots WHERE snapshot_id=?", ("k10-v2-20260909",)).fetchone()
    if row is None:
        raise ValueError("isolated acceptance database lacks its explicit strategy binding")
    return dict(config_id=row[0], config_revision=row[1], execution_id=row[2], execution_revision=row[3])


def read_actual_api(database):
    binding = explicit_bindings(database)
    with base.actual_api(database, **binding) as client:
        configuration_response = client.get("/api/v1/k10/configuration")
        configuration_response.raise_for_status()
        configuration = configuration_response.json()
        assert configuration["configId"] == binding["config_id"]
        assert configuration["configRevision"] == binding["config_revision"]
        assert configuration["executionConfigId"] == binding["execution_id"]
        assert configuration["executionConfigRevision"] == binding["execution_revision"]
        assert configuration["scopes"] and all(item["state"] == "configured" for item in configuration["scopes"])
        response = client.get("/api/v1/k10/v2/reports/latest?window=evening")
        response.raise_for_status()
        envelope = response.json()
        readiness = client.get("/api/v1/k10/operations/readiness")
        readiness.raise_for_status()
        material = None
        report = envelope.get("report")
        if report:
            report_id = report["reportId"]
            exact = client.get(f"/api/v1/k10/v2/reports/{report_id}")
            assert exact.status_code == 200 and exact.json()["report"]["reportId"] == report_id
            response = client.get(f"/api/v1/k10/v2/reports/{report_id}/materials")
            response.raise_for_status()
            material = response.json()
            assert material["schemaVersion"] == 10 and material["reportId"] == report_id
        assert client.get("/api/v1/k10/v2/reports/does-not-exist").status_code == 404
        assert client.get("/api/v1/k10/v2/reports/does-not-exist/materials").status_code == 404
    return envelope, material, readiness.json(), configuration


def generate_acceptance(root: Path, monkeypatch, *, scenario: str,
                        trading_day=base.DAY, fixture_now=base.NOW,
                        fixture_run_at=base.RUN_AT) -> Acceptance:
    if scenario not in SCENARIOS:
        raise ValueError("unsupported B78 acceptance scenario")
    root.mkdir(parents=True, exist_ok=True)
    database = root / f"{scenario}.sqlite"
    if database.exists():
        raise ValueError("acceptance database already exists; refuse to overwrite")
    monkeypatch.setattr(base, "TITLE_COUNT", TITLE_COUNT)
    monkeypatch.setattr(base, "DeterministicTransport", DirectRoundTransport)
    monkeypatch.setattr(socket.socket, "connect", base._deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", base._deny_network)
    flow = None
    if scenario == "empty":
        base.seed_database(database)
    else:
        options = {"complete": {}, "partial": {"refusal_event": 1},
                   "materials": {"refusal_operation": "prioritize", "expect_handler_failure": True},
                   "zero": {"selected_event_count": 0}}
        flow = base.run_full_scale_flow(root, monkeypatch, name=scenario,
            **({"selected_event_count": EVENT_COUNT, "trading_day": trading_day,
                "fixture_now": fixture_now, "fixture_run_at": fixture_run_at} | options[scenario]))
        assert flow.task_status == "completed", {
            "taskStatus": flow.task_status, "taskId": flow.task_id,
            "calls": flow.calls, "gatewayTrace": flow.gateway_trace,
        }
        assert flow.calls.get("titleReview", 0) == 0
        assert not any(key.startswith("forbidden:") for key in flow.calls), flow.calls
        assert not ({"research:plan_research", "research:assess_and_decide", "research:compare_companies"} & set(flow.calls))
    report, materials, readiness, configuration = read_actual_api(database)
    assert report["schemaVersion"] == 10
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM k10_v2_universe_members").fetchone()[0] == 1089
        if flow:
            assert conn.execute("SELECT COUNT(*) FROM k10_task_execution_bindings WHERE task_id=?", (flow.task_id,)).fetchone()[0] == 1
            assert conn.execute("SELECT COUNT(*) FROM k10_external_attempts WHERE state IN ('started','unknown')").fetchone()[0] == 0
        if scenario in {"empty", "zero", "materials"}:
            assert conn.execute("SELECT COUNT(*) FROM k10_publication_samples").fetchone()[0] == 0
        if scenario == "empty":
            assert conn.execute("SELECT COUNT(*) FROM k10_scans").fetchone()[0] == 0
    value = report.get("report")
    if scenario == "empty":
        assert value is None
    else:
        delivery = value["delivery"]
        assert delivery["contractVersion"] == REPORT_DELIVERY_CONTRACT
        assert value["deliveryDeadlineAt"] is None
        assert delivery["counts"]["titleInput"] == TITLE_COUNT
        assert delivery["counts"]["titleProcessed"] == TITLE_COUNT
        assert delivery["outcome"] == {"partial": "partial", "materials": "partial"}.get(scenario, "complete"), {
            "scenario": scenario, "delivery": delivery,
            "calls": {} if flow is None else flow.calls,
        }
        if scenario == "materials":
            assert value["availableAt"] is None and value["eveningCards"] == []
            assert value["materials"]["state"] == "available" and materials["items"]
            assert value["resultAvailableAt"] and delivery["rankingScope"] == "none"
            assert value["discovery"]["outcome"] == "not_completed" and delivery["gaps"]
            for item in materials["items"]:
                assert not ({"rank", "cardId", "companyWindowId", "selection", "d1TradeDate", "d2TradeDate"} & set(item))
        else:
            assert value["availableAt"]
            assert bool(value["eveningCards"]) == (scenario != "zero")
        if scenario == "partial":
            assert delivery["gaps"]
            # Events 0/1 share A. A's failed dependency excludes both; B remains.
            assert {card["companyCode"] for card in value["eveningCards"]} == {flow.company_codes[1]}
    provenance = {"synthetic": True, "scenario": scenario, "database": str(database),
                  "taskId": None if flow is None else flow.task_id,
                  "scanId": None if flow is None else flow.scan_id,
                  "titleCount": 0 if flow is None else TITLE_COUNT, "poolCount": 1089,
                  "modelCalls": {} if flow is None else dict(flow.calls),
                  "network": "denied; deterministic model/search HTTP transports",
                  "entry": "actual CLI enqueue -> worker -> production handler -> SQLite -> FastAPI",
                  "bindings": explicit_bindings(database)}
    return Acceptance(database, report, materials, readiness, configuration, provenance)


def generate_b90_loopback(root: Path, monkeypatch, *, evening_day=base.DAY) -> B90Loopback:
    """Run the current CLI/worker path for an evening and its next morning.

    The companion API/Swift test consumes the returned database only through
    FastAPI.  This helper does not fabricate a morning scan, a review work
    item, or a report: the public CLI creates both execution bindings and the
    production worker drives both handlers against deterministic transports.
    """
    evening_run_at = datetime(evening_day.year, evening_day.month, evening_day.day, 22, 0, tzinfo=base.SHANGHAI)
    evening_now = datetime(evening_day.year, evening_day.month, evening_day.day, 12, 0, tzinfo=base.SHANGHAI)
    evening = generate_acceptance(root, monkeypatch, scenario="complete", trading_day=evening_day,
                                  fixture_now=evening_now, fixture_run_at=evening_run_at)
    database = evening.database
    evening_report = evening.report.get("report")
    assert isinstance(evening_report, dict)
    evening_report_id = evening_report.get("reportId")
    assert isinstance(evening_report_id, str) and evening_report_id

    monkeypatch.setattr(DirectRoundTransport, "loopback_morning_discovery_zero", True)

    morning_day = evening_day + timedelta(days=1)
    morning_run_at = datetime(morning_day.year, morning_day.month, morning_day.day, 8, 35, tzinfo=base.SHANGHAI)
    bindings = explicit_bindings(database)
    stdout = StringIO()
    with base.redirect_stdout(stdout):
        assert base.cli_main([
            "enqueue", "--db", str(database), "--kind", "morning", "--trading-day", morning_day.isoformat(),
            "--config-id", bindings["config_id"], "--config-revision", str(bindings["config_revision"]),
            "--execution-config-id", bindings["execution_id"],
            "--execution-config-revision", str(bindings["execution_revision"]),
        ]) == 0
    morning_task_id = stdout.getvalue().strip()
    assert morning_task_id.startswith("task_")

    # ``generate_acceptance`` has already installed the network-denied model
    # and source wires.  Each B90 channel gets its own provider wrapper: the
    # parent discovery and the independent frozen-parent review must not share
    # mutable usage/retry context merely because they use the same isolated
    # ledger and deterministic transport.
    def resolve_fixture_provider(**_kwargs):
        provider = base.MeteredProvider(
            ledger_db=database, ledger_task="discovery", api_key="fixture", model="deepseek-v4-pro", name="fixture",
            api_url="https://fixture.invalid/v1/chat/completions", read_timeout=1, use_streaming=False,
        )
        provider.max_attempts = 1
        return base.ProviderResolution("configured", provider, "fixture", None)

    monkeypatch.setattr(base.pipeline, "resolve_deepseek_v4_pro", resolve_fixture_provider)
    # The parent invokes the review through its production module boundary;
    # patch that resolver too so this remains a real review wire rather than a
    # fixture configuration fallback before any provider call.
    monkeypatch.setattr(morning_runtime, "resolve_deepseek_v4_pro", resolve_fixture_provider)
    parquet_dir = root / "parquet"
    handlers = base.pipeline.production_handlers(tushare_token="fixture-token", parquet_dir=parquet_dir)

    def morning_handler(context):
        return base.pipeline.production_scan_handler(
            context, tushare_token="fixture-token", parquet_dir=parquet_dir,
            now=lambda: morning_run_at,
        )

    handlers["morning_scan"] = morning_handler
    terminal = None
    for _pass in range(8):
        terminal = base.run_once(
            db_path=database, worker_id="b90-loopback", lease_for=timedelta(minutes=5), handlers=handlers,
            # The worker owns both the business deadline and the lease clock
            # for this isolated task.  Using the frozen 08:35 clock proves a
            # completed review rather than the host date merely forcing a
            # deadline fallback for this historic fixture date.
            clock=lambda: morning_run_at, require_b76_contract=True,
        )
        assert terminal is not None and terminal.task_id == morning_task_id
        if terminal.status != "queued":
            break
    else:
        raise AssertionError("B90 morning CLI task did not terminalize after eight worker passes")
    assert terminal is not None and terminal.status == "completed"

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT report_id,scan_id,status,available_at FROM k10_v2_report_runs "
            "WHERE window_kind='morning' ORDER BY created_at DESC,report_id DESC LIMIT 1"
        ).fetchone()
        unresolved = connection.execute(
            "SELECT COUNT(*) FROM k10_external_attempts WHERE task_id=? AND state IN ('started','running','unknown')",
            (morning_task_id,),
        ).fetchone()
        review_statuses = connection.execute(
            "SELECT status FROM k10_morning_review_work_items WHERE scan_id=? ORDER BY work_item_id",
            (row[1],) if row is not None else ("missing",),
        ).fetchall()
    assert row is not None and row[2] in {"completed", "partial"} and row[3] is not None
    assert unresolved == (0,)
    assert review_statuses and all(status == "completed" for (status,) in review_statuses), review_statuses
    morning_report_id = str(row[0])
    return B90Loopback(
        database=database,
        evening_report_id=evening_report_id,
        morning_report_id=morning_report_id,
        bindings=bindings,
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_b90_real_cli_worker_api_user_results(tmp_path, monkeypatch, scenario):
    generate_acceptance(tmp_path, monkeypatch, scenario=scenario)
