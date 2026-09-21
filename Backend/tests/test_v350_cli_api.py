"""B78 real CLI/worker/API acceptance with deterministic, network-denied replies.

The 1,089-member universe is unchanged.  A small title corpus crosses batch
boundaries; this tests user results, not the old sequence of model stages.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import socket
import sqlite3
from typing import Any

import httpx
import pytest

from neckline.k10.delivery import REPORT_DELIVERY_CONTRACT
from . import v340_acceptance_fixture as base


TITLE_COUNT = 130
EVENT_COUNT = 3
SCENARIOS = ("complete", "partial", "materials", "empty", "zero")


class DirectRoundTransport(base.DeterministicTransport):
    def respond(self, request):
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
                "companyAssessments": [{"companyCode": code, "role": "primary", "rank": 1,
                    "summary": "关联清楚，订单待核", "priorityReason": "与本次事件直接相关",
                    "gap": "尚未确认订单", "rankChangeConditions": "公司公开披露",
                    "twoDayReason": "观察公开信息变化", "evidenceDisclosure": {
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
                if self._number_from_title(row["title"]) < self.selected_event_count),
                key=lambda row: self._number_from_title(row["title"]))
            return self._ok({"selectionComplete": True, "reviewedCount": len(payload["items"]),
                "selected": [{"i": row["i"], "selectedRank": index + 1,
                              "reason": "新增事件值得核验"} for index, row in enumerate(selected)], "merged": []})
        if "candidates" in payload and isinstance(payload.get("output"), dict) and "choices" in payload["output"]:
            if self.refusal_event == 1:
                codes = {row["companyCode"] for row in payload["candidates"]}
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
            assert material["schemaVersion"] == 9 and material["reportId"] == report_id
        assert client.get("/api/v1/k10/v2/reports/does-not-exist").status_code == 404
        assert client.get("/api/v1/k10/v2/reports/does-not-exist/materials").status_code == 404
    return envelope, material, readiness.json(), configuration


def generate_acceptance(root: Path, monkeypatch, *, scenario: str) -> Acceptance:
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
            **({"selected_event_count": EVENT_COUNT} | options[scenario]))
        assert flow.task_status == ("failed" if scenario == "materials" else "completed"), {
            "taskStatus": flow.task_status, "taskId": flow.task_id,
            "calls": flow.calls, "gatewayTrace": flow.gateway_trace,
        }
        assert flow.calls.get("titleReview", 0) == 0
        assert not any(key.startswith("forbidden:") for key in flow.calls), flow.calls
        assert not ({"research:plan_research", "research:assess_and_decide", "research:compare_companies"} & set(flow.calls))
    report, materials, readiness, configuration = read_actual_api(database)
    assert report["schemaVersion"] == 9
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
        assert delivery["outcome"] == {"partial": "partial", "materials": "failed"}.get(scenario, "complete"), {
            "scenario": scenario, "delivery": delivery,
            "calls": {} if flow is None else flow.calls,
        }
        if scenario == "materials":
            assert value["availableAt"] is None and value["eveningCards"] == []
            assert value["materials"]["state"] == "available" and materials["items"]
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


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_b78_real_cli_worker_api_user_results(tmp_path, monkeypatch, scenario):
    generate_acceptance(tmp_path, monkeypatch, scenario=scenario)
