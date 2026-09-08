"""B37 pre-model screening integration: deterministic and provider-free."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from neckline.k10.discovery import (
    CandidateComparison,
    CompanyMappingDraft,
    DiscoveryDocument,
    EvidenceRef,
    EventComparison,
    EventDraft,
    Verification,
    run_discovery,
)
from neckline.k10.universe import CompanyMetadata


NOW = datetime(2026, 9, 8, 13, tzinfo=timezone.utc)


def _configuration() -> dict:
    return json.loads((Path(__file__).parents[1] / "neckline/config/k10-v1.4.json").read_text())


def _document(document_id: str, text: str, *, matter=None) -> DiscoveryDocument:
    metadata = {"prefilterMatter": matter} if matter is not None else {}
    return DiscoveryDocument(document_id, 1, NOW.isoformat(), NOW.isoformat(), text, None, metadata)


def _matter(subject="甲", object="项目", date="2026-09-08", amount="10亿元", stage="签约") -> dict:
    return {"subject": subject, "object": object, "date": date, "amount": amount, "stage": stage}


def _pack(*rules) -> dict:
    return {
        "templateId": "screening-approved", "revision": 1,
        "contentSha256": sha256(b"screening-approved").hexdigest(), "approvalState": "approved",
        "rules": list(rules),
    }


def _rule(rule_id: str, action: str, regex: str) -> dict:
    return {
        "ruleId": rule_id, "revision": 1, "action": action, "auditReason": rule_id,
        "match": {"allPatterns": [{"patternId": rule_id + "-p", "regex": regex}]},
    }


def _limits(max_documents=4) -> dict:
    return {"maxDocuments": max_documents, "maxKeyPassageCharacters": 1000, "fullTextEnabled": False}


class _Metadata:
    def lookup(self, *, company_code, as_of):
        return CompanyMetadata(company_code, "chinext", False, "801080.SI", as_of)


class _PackageModel:
    def __init__(self) -> None:
        self.understood = []
        self.verified = []

    def register_documents(self, *, documents):
        pass

    def understand(self, *, document):
        self.understood.append(document)
        refs = tuple(EvidenceRef(row["documentId"], row["revision"])
                     for row in document.metadata["memberRefs"])
        key = "contrary" if document.metadata["screeningAction"] == "protect" else "new-" + document.document_id[-6:]
        return (EventDraft(key, "initial", "confirmed", key, "disclosure", {}, refs),)

    def map_companies(self, *, event, verification):
        code = "300001.SZ" if event.canonical_key == "contrary" else "300002.SZ"
        return (CompanyMappingDraft(code, "initial", event.source_refs, {}, "fixture"),)

    def compare_event(self, *, event, verification, mappings):
        code = mappings[0].company_code
        return EventComparison("比较", {
            code: CandidateComparison("公司比较", {
                "role": "primary", "priorityReason": "资料", "gap": "差异",
                "rankChangeConditions": "反证", "twoDayReason": "两日",
            }, event.source_refs, 1),
        }, event.source_refs)

    def classify_opportunity(self, *, event, verification, mapping, comparison, previous):
        return {"kind": "initial", "relatedOpportunityId": None, "reason": "首发",
                "newFacts": "新增", "changedJudgment": None, "twoDayReason": "两日"}

    def prioritize(self, *, candidates):
        return tuple((candidate.event.canonical_key, candidate.mapping.company_code) for candidate in candidates)


class _AdmissionClosed(RuntimeError):
    code = "pending_budget"


class _BudgetBlockedModel(_PackageModel):
    def understand(self, *, document):
        self.understood.append(document)
        raise _AdmissionClosed("budget exhausted")


class _CompareBudgetBlockedModel(_PackageModel):
    def __init__(self) -> None:
        super().__init__()
        self.compare_calls = 0

    def compare_event(self, *, event, verification, mappings):
        self.compare_calls += 1
        raise _AdmissionClosed("comparison budget exhausted")


class _FullTextBlockedModel(_PackageModel):
    def understand(self, *, document):
        self.understood.append(document)
        self._requested = {document.evidence_ref}
        error = RuntimeError("full text denied")
        error.code = "full_text_disabled"
        raise error

    def full_text_requested(self, *, document):
        return document.evidence_ref in getattr(self, "_requested", set())


def _run(documents, *, model=None, pack=None, limits=None, checkpoint=None):
    model = model or _PackageModel()
    verified = []
    run = run_discovery(
        documents=documents, configuration=_configuration(), model=model,
        verify=lambda event: (verified.append(event.canonical_key) or Verification("verified", "核验", event.source_refs)),
        metadata=_Metadata(), cutoff_at=NOW, screening_rule_pack=pack,
        priority_order=["publishedMajorContrary", "changedKnownFact", "newEvent"],
        package_limits=limits or _limits(), package_checkpoint=checkpoint, require_screening=True,
    )
    return run, model, verified


def test_screening_runs_one_understand_for_complete_matter_package_and_keeps_real_refs():
    checkpoints = []
    run, model, _ = _run([
        _document("source-a", "甲项目签约的第一份资料", matter=_matter()),
        _document("source-b", "甲项目签约的第二份资料", matter=_matter()),
    ], pack=_pack(_rule("all-defer", "defer", ".+")), checkpoint=checkpoints.append)

    assert run.state == "completed"
    assert len(model.understood) == 1
    assert model.understood[0].document_id.startswith("pkg_")
    assert run.document_counts["received"] == 2
    assert run.document_counts["packages"] == 1
    assert {ref.document_id for ref in run.events[0].source_refs} == {"source-a", "source-b"}
    assert all(not ref.document_id.startswith("pkg_") for ref in run.events[0].source_refs)
    manifest = next(row for row in checkpoints if row["stage"] == "screening_manifest")
    assert manifest["state"] == "frozen"
    assert manifest["result"]["counts"]["packages"] == 1
    packaged = [row for row in checkpoints if row["stage"] == "screening" and row["state"] == "frozen"]
    assert packaged[0]["memberHash"]
    assert {row["documentId"] for row in packaged[0]["memberRefs"]} == {"source-a", "source-b"}


def test_missing_screening_template_closes_before_model_or_verification():
    model = _PackageModel()
    run, _, verified = _run([_document("source-a", "资料")], model=model, pack=None)

    assert run.state == "not_configured"
    assert model.understood == []
    assert verified == []
    assert run.document_counts["screeningNotConfigured"] == 1


def test_package_over_explicit_member_limit_stays_pending_without_understand_call():
    run, model, verified = _run([
        _document("source-a", "第一份资料", matter=_matter()),
        _document("source-b", "第二份资料", matter=_matter()),
    ], pack=_pack(_rule("all-defer", "defer", ".+")), limits=_limits(max_documents=1))

    assert run.state == "partial"
    assert model.understood == []
    assert verified == []
    assert run.document_counts["packageLimitPending"] == 1
    assert [issue.code for issue in run.issues] == ["package_limit_pending"]


def test_explicit_priority_puts_protected_counterevidence_before_new_event():
    run, _, verified = _run([
        _document("old-corrected", "更正已披露事项", matter=_matter(amount="12亿元", stage="更正")),
        _document("new-source", "新的独立事项", matter=_matter(subject="丙", object="新项目")),
    ], pack=_pack(_rule("correction", "protect", "更正")))

    assert run.state == "completed"
    assert verified[0] == "contrary"
    assert run.events[0].canonical_key == "contrary"
    assert run.document_counts["templateDeferred"] == 1  # unmatched stays queued, never silently excluded


def test_budget_pending_closes_package_admission_without_probingly_understanding_the_rest():
    model = _BudgetBlockedModel()
    run, _, verified = _run([
        _document("a", "资料甲"), _document("b", "资料乙"), _document("c", "资料丙"),
    ], model=model, pack=_pack(_rule("all-defer", "defer", ".+")))

    assert len(model.understood) == 1
    assert verified == []
    assert run.state == "partial"
    assert run.document_counts["pendingBudget"] == 3
    assert run.document_counts["understandPending"] == 3
    assert run.issues[0].code == "pending_budget"


def test_comparison_budget_pending_is_visible_and_stops_later_event_provider_stages():
    model = _CompareBudgetBlockedModel()
    run, _, verified = _run([
        _document("a", "资料甲"), _document("b", "资料乙"),
    ], model=model, pack=_pack(_rule("all-defer", "defer", ".+")))

    assert run.state == "partial"
    assert model.compare_calls == 1
    assert verified == [run.events[0].canonical_key]
    assert run.document_counts["pendingBudget"] == 1
    assert run.document_counts["eventFailed"] == 0
    assert run.issues[-1].stage == "compare"
    assert run.issues[-1].code == "pending_budget"


def test_disabled_explicit_full_text_request_is_pending_and_counted_not_model_failure():
    model = _FullTextBlockedModel()
    run, _, verified = _run([_document("a", "资料甲"), _document("b", "资料乙")], model=model,
                             pack=_pack(_rule("all-defer", "defer", ".+")))

    assert len(model.understood) == 1
    assert verified == []
    assert run.document_counts["fullTextRequested"] == 1
    assert run.document_counts["fullTextCompleted"] == 0
    assert run.document_counts["understandFailed"] == 0
    assert run.document_counts["pendingBudget"] == 0
    assert run.issues[0].code == "full_text_disabled"
