from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import pytest

from neckline.k10.discovery import DiscoveryDocument, prepare_document_for_analysis
from neckline.k10.prefilter import TemplateRulePack, audit_payload, prefilter_documents


def _document(document_id: str, text: str, *, revision: int = 1, matter=None) -> DiscoveryDocument:
    metadata = {"prefilterMatter": matter} if matter is not None else {}
    return prepare_document_for_analysis(DiscoveryDocument(
        document_id=document_id,
        revision=revision,
        published_at="2026-09-08T12:00:00+00:00",
        fetched_at="2026-09-08T12:01:00+00:00",
        original_text=text,
        excerpt=None,
        metadata=metadata,
    ))


def _pack(*rules):
    return {
        "templateId": "approved-news-v1",
        "revision": "1",
        "contentSha256": sha256(b"approved template").hexdigest(),
        "approvalState": "approved",
        "rules": list(rules),
    }


def _rule(rule_id: str, action: str, regex: str, *, extractor: str | None = None):
    result = {
        "ruleId": rule_id,
        "revision": "1",
        "action": action,
        "auditReason": f"{rule_id}_reason",
        "match": {"allPatterns": [{"patternId": f"{rule_id}_p", "regex": regex}]},
    }
    if extractor is not None:
        result["matterExtract"] = {"pattern": extractor, "groups": {
            "subject": "subject", "object": "object", "date": "date", "amount": "amount", "stage": "stage",
        }}
    return result


def _matter(subject="甲公司", object="乙项目", date="2026-09-08", amount="10亿元", stage="签约"):
    return {"subject": subject, "object": object, "date": date, "amount": amount, "stage": stage}


def test_missing_template_is_not_configured_and_creates_no_admissible_package():
    result = prefilter_documents([_document("d1", "普通新闻")], rule_pack=None)

    assert result.state == "not_configured"
    assert result.packages == ()
    assert result.counts["input"] == 1


def test_exact_and_normalized_duplicates_keep_all_source_refs_and_are_order_stable():
    documents = [
        _document("d2", "  甲公司 乙项目 2026-09-08 10亿元 签约  ", matter=_matter()),
        _document("d1", "甲公司 乙项目 2026-09-08 10亿元 签约", matter=_matter()),
        _document("d3", "甲公司 乙项目 2026-09-08 10亿元 签约", matter=_matter()),
    ]
    pack = _pack(_rule("defer-all", "defer", ".+"))

    result = prefilter_documents(documents, rule_pack=pack)
    reversed_result = prefilter_documents(list(reversed(documents)), rule_pack=pack)

    assert result.counts["exactDuplicates"] == 1
    assert result.counts["normalizedDuplicates"] == 2
    assert len(result.packages) == 1
    assert result.packages[0].members[0].source_refs == (("d1", 1), ("d2", 1), ("d3", 1))
    assert result.packages[0].package_id == reversed_result.packages[0].package_id
    assert result.packages[0].member_hash == reversed_result.packages[0].member_hash


def test_only_complete_verified_matter_fields_can_group_documents():
    complete = _matter()
    missing_stage = dict(complete)
    missing_stage.pop("stage")
    pack = _pack(_rule("defer-all", "defer", ".+"))
    result = prefilter_documents([
        _document("d1", "第一份不同来源", matter=complete),
        _document("d2", "第二份不同来源", matter=complete),
        _document("d3", "第三份不同来源", matter=missing_stage),
        _document("d4", "同题材但没有可验证字段"),
    ], rule_pack=pack)

    assert len(result.packages) == 3
    grouped = [package for package in result.packages if len(package.members) == 2]
    assert len(grouped) == 1
    assert {member.source_refs[0][0] for member in grouped[0].members} == {"d1", "d2"}


def test_protected_correction_is_not_folded_into_old_matter_and_keeps_relation():
    pack = _pack(
        _rule("correction", "protect", "更正|否认|取消"),
        _rule("defer-all", "defer", ".+"),
    )
    result = prefilter_documents([
        _document("old", "原公告", matter=_matter()),
        _document("new", "更正 原公告金额", matter=_matter(amount="12亿元", stage="更正")),
    ], rule_pack=pack)

    assert len(result.packages) == 2
    protected = next(package for package in result.packages if package.action == "protect")
    assert len(protected.related_package_ids) == 1
    assert protected.related_package_ids[0] != protected.package_id


def test_unknown_is_visible_defer_and_protect_overrides_exclude():
    pack = _pack(
        _rule("exclude-template", "exclude", "例行公告"),
        _rule("correction", "protect", "更正"),
    )
    result = prefilter_documents([
        _document("unknown", "没有匹配模板的资料"),
        _document("protected", "例行公告更正"),
        _document("excluded", "例行公告"),
    ], rule_pack=pack)

    by_document = {audit.document_id: audit for audit in result.audits}
    assert by_document["unknown"].action == "defer"
    assert by_document["unknown"].reason == "unknown_template"
    assert by_document["protected"].action == "protect"
    assert by_document["excluded"].action == "exclude"
    assert {package.action for package in result.packages} == {"defer", "protect"}
    assert result.counts["rule:unknown_template"] == 1
    assert result.counts["rule:correction"] == 1


def test_rule_pack_rejects_unapproved_or_forbidden_exclude_selector():
    rule = _rule("exclude", "exclude", "公告")
    forbidden = _pack({**rule, "category": "news"})
    with pytest.raises(ValueError, match="禁止条件"):
        TemplateRulePack.from_mapping(forbidden)
    unapproved = _pack(rule)
    unapproved["approvalState"] = "draft"
    with pytest.raises(ValueError, match="未获批准"):
        TemplateRulePack.from_mapping(unapproved)


def test_config_form_integer_revision_and_inherited_rule_revision_are_accepted():
    pack = _pack(_rule("defer", "defer", ".+"))
    pack["revision"] = 2
    pack["rules"][0].pop("revision")
    parsed = TemplateRulePack.from_mapping(pack)
    assert parsed.revision == parsed.rules[0].revision == "2"


def test_named_extractor_requires_all_five_fields_and_audit_is_text_safe():
    extractor = r"主体=(?P<subject>[^;]+);对象=(?P<object>[^;]+);日期=(?P<date>[^;]+);金额=(?P<amount>[^;]+);阶段=(?P<stage>[^;]+)"
    pack = _pack(_rule("fact", "defer", "主体=", extractor=extractor))
    result = prefilter_documents([
        _document("d1", "主体=甲;对象=乙;日期=2026-09-08;金额=10亿元;阶段=签约"),
        _document("d2", "主体=甲;对象=乙;日期=2026-09-08;金额=10亿元;阶段=签约"),
    ], rule_pack=pack)

    assert len(result.packages) == 1
    payload = audit_payload(result)
    assert payload["counts"]["input"] == 2
    assert "原文" not in str(payload)
    bad = _pack(_rule("bad", "defer", ".+", extractor=r"(?P<subject>x)"))
    with pytest.raises(ValueError, match="缺少命名分组"):
        TemplateRulePack.from_mapping(bad)
