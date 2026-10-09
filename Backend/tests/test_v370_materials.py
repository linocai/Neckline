"""Material delivery regressions at the current B92 producer boundary."""
from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from hashlib import sha256

import pytest

from neckline.k10 import store, v2_store
from neckline.k10.materials import partition_materials, project_material
from neckline.k10.schema import write_connection

from tests import test_b92_report_loopback as b92


def _long_relation_evening(tmp_path, monkeypatch):
    original = b92._FlashReportTransport.respond

    def long_relation(self, request):
        response = original(self, request)
        message = json.loads(request.content)["messages"][-1]["content"]
        if "<untrusted-k10-evidence>" in message:
            packet = self._packet(request)
            if packet.get("action") == "research_round":
                value = json.loads(response.json()["choices"][0]["message"]["content"])
                mappings = value.get("conclusion", {}).get("companyMappings", [])
                if mappings:
                    mappings[0]["inference"]["relation"] = "关" * 501
                    return self._ok(value)
        return response

    monkeypatch.setattr(b92._FlashReportTransport, "respond", long_relation)
    return b92._make_evening(tmp_path, monkeypatch)


def _api(flow):
    run_id, run_rev, exec_id, exec_rev, _, _ = flow["bindings"]
    return b92.base.actual_api(flow["dbPath"], config_id=run_id, config_revision=run_rev,
                               execution_id=exec_id, execution_revision=exec_rev)


def _assert_current_binding(flow):
    task = store.task_execution_input(task_id=flow["eveningTaskId"], db_path=flow["dbPath"])
    with sqlite3.connect(flow["dbPath"]) as conn:
        payload = json.loads(conn.execute("SELECT payload_json FROM k10_tasks WHERE task_id=?",
                                         (flow["eveningTaskId"],)).fetchone()[0])
    assert payload["runtimeContract"]["research"] == "k10-research-3.6.1-b92"
    execution = task["executionProfile"]["payload"]["discovery"]
    assert execution["reportInputContract"] == "k10-collected-input-3.6.1-b92"
    assert execution["investigationPromptContractRevision"] == "k10-research-3.6.1-b92"


def _rows(flow):
    with sqlite3.connect(flow["dbPath"]) as conn:
        return conn.execute("SELECT * FROM k10_v2_report_materials WHERE report_id=? ORDER BY material_id",
                            (flow["eveningReportId"],)).fetchall()


def _insert_copy(flow, material_id, *, field=None, value=None):
    row = list(_rows(flow)[-1])
    row[1] = material_id
    fields = ["report_id", "material_id", "event_id", "event_title", "facts_json",
              "company_relations_json", "uncertainties_json", "source_refs_json", "as_of"]
    if field is not None:
        row[fields.index(field)] = value
    with sqlite3.connect(flow["dbPath"]) as conn:
        conn.execute("INSERT INTO k10_v2_report_materials VALUES(?,?,?,?,?,?,?,?,?)", row)


def test_b92_material_relation_501_characters_remains_readable(tmp_path, monkeypatch):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    _assert_current_binding(flow)
    with _api(flow) as client:
        report = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}").json()["report"]
        response = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials")
        assert response.status_code == 200
        payload = response.json()
    assert report["eveningCards"]
    assert payload["items"]
    assert any(row["relation"] == "关" * 501
               for item in payload["items"] for row in item["companyRelations"])


def test_b92_material_bad_items_all_bad_page_and_later_pages_are_read_only(tmp_path, monkeypatch):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    _assert_current_binding(flow)
    _insert_copy(flow, "000-json", field="facts_json", value="{broken-private-text")
    _insert_copy(flow, "001-source", field="source_refs_json", value=json.dumps([
        {"documentId": "never-collected", "revision": 1}]))
    before = sha256(flow["dbPath"].read_bytes()).hexdigest()
    writes_before = None
    with sqlite3.connect(flow["dbPath"]) as conn:
        writes_before = conn.execute("SELECT status FROM k10_v2_report_runs WHERE report_id=?",
                                    (flow["eveningReportId"],)).fetchone()[0]
    with _api(flow) as client:
        url = f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials"
        first = client.get(url, params={"limit": 2}).json()
        repeat = client.get(url, params={"limit": 2}).json()
        next_page = client.get(url, params={"limit": 1, "cursor": first["page"]["nextCursor"]}).json()
        final = client.get(url, params={"limit": 1, "cursor": next_page["page"]["nextCursor"]}).json()
        invalid = client.get(url, params={"cursor": "foreign-material"})
    assert first == repeat, "GET must generate stable read gaps"
    assert first["items"] == [] and first["page"]["nextCursor"] == "001-source"
    assert [gap["unitId"] for gap in first["readGaps"]] == ["000-json", "001-source"]
    assert {gap["reasonCode"] for gap in first["readGaps"]} == {
        "material_projection_invalid", "material_source_unavailable"}
    assert "broken-private-text" not in json.dumps(first)
    assert all(gap["sourceRefs"] == [] and gap["companyScopeKnown"] is False
               for gap in first["readGaps"])
    assert len(next_page["items"]) == len(final["items"]) == 1
    assert final["page"]["nextCursor"] is None
    assert not next_page["readGaps"] and not final["readGaps"]
    assert invalid.status_code == 422
    assert sha256(flow["dbPath"].read_bytes()).hexdigest() == before
    with sqlite3.connect(flow["dbPath"]) as conn:
        assert conn.execute("SELECT status FROM k10_v2_report_runs WHERE report_id=?",
                            (flow["eveningReportId"],)).fetchone()[0] == writes_before


@pytest.mark.parametrize("field,value", [
    ("event_title", "   "), ("facts_json", "[1]"),
    ("company_relations_json", '[{"companyCode":"000001.SZ"}]'),
    ("uncertainties_json", "[true]"), ("as_of", "not-a-date"),
])
def test_b92_bad_derived_material_does_not_hide_valid_sibling(tmp_path, monkeypatch, field, value):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    _insert_copy(flow, "000-bad", field=field, value=value)
    with _api(flow) as client:
        payload = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials").json()
    assert len(payload["items"]) == 2
    assert len(payload["readGaps"]) == 1 and payload["readGaps"][0]["unitId"] == "000-bad"
    assert payload["page"]["nextCursor"] is None


def test_b92_material_cursor_advances_past_bad_last_scanned_item(tmp_path, monkeypatch):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    _insert_copy(flow, "000-valid")
    _insert_copy(flow, "001-invalid", field="facts_json", value="false")
    with _api(flow) as client:
        url = f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials"
        first = client.get(url, params={"limit": 2}).json()
        second = client.get(url, params={"limit": 2, "cursor": first["page"]["nextCursor"]}).json()
    assert [item["materialId"] for item in first["items"]] == ["000-valid"]
    assert first["page"]["nextCursor"] == "001-invalid"
    assert first["readGaps"][0]["unitId"] == "001-invalid"
    assert len(second["items"]) == 2 and second["page"]["nextCursor"] is None


@pytest.mark.parametrize("metadata", ['{"title":123}', '{broken-display-metadata', '[]'])
def test_b92_source_display_metadata_isolated_to_its_material_consumers(tmp_path, monkeypatch, metadata):
    """Keep the independent review's two-material production reproduction."""
    from fastapi.testclient import TestClient

    flow = _long_relation_evening(tmp_path, monkeypatch)
    _assert_current_binding(flow)
    with _api(flow) as client:
        before = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials").json()
    assert len(before["items"]) == 2
    ref = before["items"][0]["sourceRefs"][0]
    affected_key = (ref["documentId"], ref["revision"])
    independent = [item for item in before["items"]
                   if affected_key not in {(source["documentId"], source["revision"])
                                           for source in item["sourceRefs"]}]
    assert independent, "The fault must leave an independently sourced material"
    with sqlite3.connect(flow["dbPath"]) as conn:
        conn.execute("UPDATE k10_source_document_versions SET metadata_json=? "
                     "WHERE document_id=? AND revision=?", (metadata, *affected_key))
    unchanged = sha256(flow["dbPath"].read_bytes()).hexdigest()
    with TestClient(_api(flow).app, raise_server_exceptions=False) as client:
        response = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials")
    assert response.status_code == 200
    payload = response.json()
    assert [item["materialId"] for item in payload["items"]] == [item["materialId"] for item in independent]
    assert len(payload["readGaps"]) == 1
    assert payload["readGaps"][0]["unitId"] == before["items"][0]["materialId"]
    assert payload["readGaps"][0]["companyScopeKnown"] is False
    assert payload["readGaps"][0]["sourceRefs"] == []
    assert "broken-display-metadata" not in json.dumps(payload)
    assert sha256(flow["dbPath"].read_bytes()).hexdigest() == unchanged


def test_b92_bad_source_metadata_shared_consumers_keep_paging(tmp_path, monkeypatch):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    with _api(flow) as client:
        url = f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials"
        before = client.get(url).json()
    affected = before["items"][0]
    ref = affected["sourceRefs"][0]
    _insert_copy(flow, "000-shared-consumer", field="facts_json", value=json.dumps([
        {"text": "依赖同一原件的展示事实", "sourceRefs": [
            {"documentId": ref["documentId"], "revision": ref["revision"]}]}]))
    with sqlite3.connect(flow["dbPath"]) as conn:
        conn.execute("UPDATE k10_source_document_versions SET metadata_json=? WHERE document_id=? AND revision=?",
                     ('{bad-display', ref["documentId"], ref["revision"]))
    unchanged = sha256(flow["dbPath"].read_bytes()).hexdigest()
    with _api(flow) as client:
        first = client.get(url, params={"limit": 2}).json()
        final = client.get(url, params={"cursor": first["page"]["nextCursor"], "limit": 2}).json()
    assert not first["items"] and len(first["readGaps"]) == 2
    assert {gap["unitId"] for gap in first["readGaps"]} == {
        "000-shared-consumer", affected["materialId"]}
    assert first["page"]["nextCursor"] == affected["materialId"]
    assert len(final["items"]) == 1 and not final["readGaps"]
    assert final["page"]["nextCursor"] is None
    assert sha256(flow["dbPath"].read_bytes()).hexdigest() == unchanged


def test_material_source_projection_does_not_swallow_database_or_identity_errors():
    from neckline.api.k10 import _material_source_reference_map

    class BrokenDatabase:
        def execute(self, *args):
            raise sqlite3.OperationalError("database cannot be read")

    with pytest.raises(sqlite3.OperationalError, match="database cannot be read"):
        _material_source_reference_map(BrokenDatabase(), [{"documentId": "source", "revision": 1}])
    with pytest.raises(KeyError):
        _material_source_reference_map(BrokenDatabase(), [{"documentId": "source"}])


def test_b92_material_writer_and_api_share_full_narrative_projection(tmp_path, monkeypatch):
    flow = _long_relation_evening(tmp_path, monkeypatch)
    with _api(flow) as client:
        material = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials").json()["items"][0]
    # Canonical persisted references are identities. HTTP hydration metadata is
    # deliberately never written back to a derived material's evidence list.
    for refs in [material["sourceRefs"], *(fact["sourceRefs"] for fact in material["facts"]),
                 *(relation["sourceRefs"] for relation in material["companyRelations"])]:
        refs[:] = [{"documentId": ref["documentId"], "revision": ref["revision"]} for ref in refs]
    material["eventTitle"] = "题" * 1001
    material["facts"][0]["text"] = "事" * 4001
    invalid = deepcopy(material)
    invalid["materialId"] = "bad-material"
    invalid["companyRelations"][0]["sourceRefs"] = [{"documentId": "unseen", "revision": 1}]
    with write_connection(flow["dbPath"]) as conn:
        gaps = v2_store.write_report_materials(conn, report_id=flow["eveningReportId"],
            materials=[invalid, material], result_available_at=b92.RUN_AT.isoformat(), delivery_deadline_at=None)
    assert len(gaps) == 1 and gaps[0]["unitId"] == "bad-material"

    def forbidden(*args, **kwargs):
        raise AssertionError("Material GET attempted schema initialization or a writer")

    monkeypatch.setattr("neckline.k10.schema.initialize_schema", forbidden)
    monkeypatch.setattr(v2_store, "write_connection", forbidden)
    with _api(flow) as client:
        report = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}").json()["report"]
        payload = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials").json()
    assert report["materials"]["count"] == 1
    assert payload["items"][0]["eventTitle"] == "题" * 1001
    assert payload["items"][0]["facts"][0]["text"] == "事" * 4001
    assert payload["items"][0]["companyRelations"][0]["relation"] == "关" * 501
    assert not payload["readGaps"]


def test_material_projection_preserves_unbounded_narrative_and_filters_unsafe_sources():
    ref = {"documentId": "source", "revision": 1}
    material = {"materialId": "material", "eventId": "event", "eventTitle": "题" * 1001,
        "facts": [{"text": "事" * 4001, "sourceRefs": [ref]}],
        "companyRelations": [{"companyCode": "000001.SZ", "companyName": "测试公司",
                              "relation": "关" * 501, "sourceRefs": [ref]}],
        "uncertainties": ["尚待核验"], "sourceRefs": [ref], "asOf": b92.RUN_AT.isoformat()}
    assert project_material(material, source_keys={("source", 1)}) == material
    invalid = deepcopy(material)
    invalid["materialId"] = "bad-material"
    invalid["facts"][0]["sourceRefs"] = [{"documentId": "unseen", "revision": 1}]
    valid, gaps = partition_materials(report_id="report", materials=[invalid, material],
                                      source_keys={("source", 1)})
    assert valid == [material] and len(gaps) == 1
    assert gaps[0]["reasonCode"] == "material_source_unavailable"


def test_b92_new_projection_failure_is_partial_with_independent_cards(tmp_path, monkeypatch):
    original = b92.pipeline._safe_report_materials

    def one_bad_projection(**kwargs):
        materials = original(**kwargs)
        materials[0]["facts"] = [{"text": "bad derived field", "sourceRefs": [
            {"documentId": "unknown-source", "revision": 1}]}]
        return materials

    monkeypatch.setattr(b92.pipeline, "_safe_report_materials", one_bad_projection)
    flow = b92._make_evening(tmp_path, monkeypatch)
    with _api(flow) as client:
        report = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}").json()["report"]
        materials = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials").json()
    assert report["status"] == "partial" and report["eveningCards"]
    assert any(gap["reasonCode"] == "material_source_unavailable"
               for gap in report["delivery"]["gaps"])
    assert len(materials["items"]) == report["materials"]["count"] == 1
    assert not materials["readGaps"]


def generate_material_api_artifacts(root, monkeypatch):
    """Export actual current-entry responses for the shared Swift/native QA."""
    root.mkdir(parents=True, exist_ok=True)
    work = root / "work"
    work.mkdir()
    flow = _long_relation_evening(work, monkeypatch)
    _assert_current_binding(flow)
    url = f"/api/v1/k10/v2/reports/{flow['eveningReportId']}/materials"
    with _api(flow) as client:
        report = client.get(f"/api/v1/k10/v2/reports/{flow['eveningReportId']}").json()
        first = client.get(url, params={"limit": 1}).json()
        ref = first["items"][0]["sourceRefs"][0]
        original = client.get(f"/api/v1/k10/documents/{ref['documentId']}",
                              params={"revision": ref["revision"]}).json()
    # A derived-only corruption is a read fault, outside the production entry
    # being accepted above. No execution binding/checkpoint is manually fixed.
    bad_id = first["page"]["nextCursor"] + "-bad"
    _insert_copy(flow, bad_id, field="facts_json", value="not-json")
    with _api(flow) as client:
        next_page = client.get(url, params={"limit": 2, "cursor": first["page"]["nextCursor"]}).json()
        all_bad = client.get(url, params={"limit": 1, "cursor": first["page"]["nextCursor"]}).json()
        after_bad = client.get(url, params={"limit": 2, "cursor": all_bad["page"]["nextCursor"]}).json()
    assert next_page["items"] and next_page["readGaps"]
    assert not all_bad["items"] and all_bad["readGaps"] and all_bad["page"]["nextCursor"]
    payloads = {"report.json": report, "materials-first.json": first,
                "materials-next.json": next_page, "materials-all-bad.json": all_bad,
                "materials-after-bad.json": after_bad, "original.json": original}
    empty_root = root / "empty-work"
    empty_root.mkdir()
    with monkeypatch.context() as empty_patch:
        b92.test_collected_input_report_runs_without_any_provider_credential(empty_root, empty_patch)
        empty_db = empty_root / "b92-minimal.sqlite"
        run_id, run_rev, exec_id, exec_rev = b92.base.active_bindings(empty_db)
        with b92.base.actual_api(empty_db, config_id=run_id, config_revision=run_rev,
                                execution_id=exec_id, execution_revision=exec_rev) as client:
            empty_report = client.get("/api/v1/k10/v2/reports/latest?window=evening").json()
            empty = client.get(f"/api/v1/k10/v2/reports/{empty_report['report']['reportId']}/materials").json()
    assert not empty["items"] and not empty["readGaps"] and empty["page"]["nextCursor"] is None
    payloads.update({"report-empty.json": empty_report, "materials-empty.json": empty})
    for name, value in payloads.items():
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return {"paths": [str(root / name) for name in payloads],
            "temporary": [str(work), str(empty_root)], "reportId": flow["eveningReportId"]}
