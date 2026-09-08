from __future__ import annotations

from neckline.k10.analysis import run_debate
from neckline.llm.base import LLMResult


class Provider:
    def __init__(self):
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return LLMResult(ok=True, content="分析完成", provider="fixture", model="fixture")


def _context():
    disclosure = {
        "verificationStatus": "unverified", "isRumor": True, "originStatus": "unknown",
        "originEvidenceRef": None, "unverifiedReasons": ["无独立来源确认"],
        "conditionalAnalysis": "仅在公司确认订单传闻后才支持两日判断。",
    }
    return {
        "observationId": "obs-rumor", "cutoffAt": "2026-09-08T21:00:00+08:00",
        "candidate": {"candidateId": "candidate-rumor", "comparison": {"differences": {"evidenceDisclosure": disclosure}}},
        "event": {"eventId": "event-rumor", "revision": 1, "headline": "传闻", "kind": "rumor",
                  "facts": {}, "sourceRefs": [{"documentId": "doc-rumor", "revision": 1}]},
        "frozenEvidenceRefs": [{"documentId": "doc-rumor", "revision": 1}], "mappings": [],
        "documents": [{"documentId": "doc-rumor", "revision": 1, "contentSha256": "a",
                       "publishedAt": "2026-09-08T20:00:00+08:00", "publishedPrecision": "exact",
                       "fetchedAt": "2026-09-08T20:01:00+08:00", "fetchVersion": "fixture",
                       "originalText": "未经证实的订单传闻"}],
    }


def test_pro_and_con_receive_the_same_frozen_unverified_disclosure_without_upgrading_it():
    provider = Provider()
    result = run_debate(context=_context(), cutoff_at="2026-09-08T21:00:00+08:00",
                        config={"configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
                                "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
                                "modelRoutes": {"analysis": "deepseek-v4-pro"},
                                "taskPolicies": {"analysis": {"maxAttempts": 1, "modelMaxAttempts": 1, "timeoutSeconds": 1, "costLimit": None}}},
                        provider=provider)
    assert result.status == "completed"
    for messages, _ in provider.calls:
        assert "unverified" in messages[-1].content
        assert "unknown" in messages[-1].content
        assert "未核传闻" in messages[-1].content
    assert result.pro.input_lineage["evidenceDisclosure"]["verificationStatus"] == "unverified"
    assert result.con.input_lineage["evidenceDisclosure"] == result.pro.input_lineage["evidenceDisclosure"]
