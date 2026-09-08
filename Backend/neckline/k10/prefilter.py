"""Deterministic, offline K10 source prefiltering and evidence packages.

This module deliberately has no provider, model, database, or discovery-pipeline
dependency.  It makes the *pre-model* decision auditable, but does not decide
which deferred material may spend a model budget; that admission belongs to the
execution-budget boundary.

Only a frozen, explicitly approved rule pack can produce a ready result.  An
unknown item is a visible ``defer`` item, never an implicit include or a silent
exclusion.  Matter packages are intentionally conservative: unrelated reports
can share a theme, title, or company and still must not be merged unless all of
subject, object, date, amount, and stage are supplied by an approved extractor
or an upstream factual source field.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence


_MATTER_FIELDS = ("subject", "object", "date", "amount", "stage")
_ACTIONS = frozenset({"exclude", "defer", "protect"})
_FORBIDDEN_EXCLUDE_SELECTORS = frozenset({"category", "benefit", "length", "growth_board"})


def _digest(*parts: str) -> str:
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _normalized_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _string(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _normalized_text(value)


def _revision(value: Any) -> str:
    """Keep revision identity stable while accepting config's positive integer form."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int) and value > 0:
        return str(value)
    return _string(value)


def _ref_payload(document: Any) -> tuple[str, int]:
    document_id = getattr(document, "document_id", None)
    revision = getattr(document, "revision", None)
    if not isinstance(document_id, str) or not document_id or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError("资料缺少有效 document_id/revision")
    return document_id, revision


def _analysis_text(document: Any) -> str:
    value = getattr(document, "analysis_text", None)
    if not isinstance(value, str):
        value = getattr(document, "original_text", None)
    if not isinstance(value, str):
        value = getattr(document, "excerpt", None)
    return _normalized_text(value)


def _metadata(document: Any) -> Mapping[str, Any]:
    value = getattr(document, "metadata", None)
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class TemplatePattern:
    pattern_id: str
    regex: str

    def __post_init__(self) -> None:
        if not self.pattern_id or not self.regex:
            raise ValueError("模板匹配缺少 patternId 或 regex")
        try:
            re.compile(self.regex)
        except re.error as exc:
            raise ValueError(f"模板正则无效: {self.pattern_id}") from exc


@dataclass(frozen=True)
class MatterExtractor:
    """An approved named-capture extractor for conservative matter grouping."""

    pattern: str
    groups: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.pattern:
            raise ValueError("资料包提取器缺少 pattern")
        if set(self.groups) != set(_MATTER_FIELDS) or any(not value for value in self.groups.values()):
            raise ValueError("资料包提取器必须声明 subject/object/date/amount/stage 分组")
        try:
            compiled = re.compile(self.pattern)
        except re.error as exc:
            raise ValueError("资料包提取器正则无效") from exc
        missing = set(self.groups.values()).difference(compiled.groupindex)
        if missing:
            raise ValueError(f"资料包提取器 pattern 缺少命名分组: {', '.join(sorted(missing))}")

    def extract(self, text: str) -> "MatterKey | None":
        match = re.search(self.pattern, text)
        if match is None:
            return None
        return MatterKey.from_mapping({field: match.group(group) for field, group in self.groups.items()})


@dataclass(frozen=True)
class TemplateRule:
    rule_id: str
    revision: str
    action: str
    audit_reason: str
    all_patterns: tuple[TemplatePattern, ...]
    matter_extractor: MatterExtractor | None = None

    def __post_init__(self) -> None:
        if not self.rule_id or not self.revision or not self.audit_reason:
            raise ValueError("模板规则缺少 ruleId/revision/auditReason")
        if self.action not in _ACTIONS:
            raise ValueError("模板规则 action 必须是 exclude/defer/protect")
        if not self.all_patterns:
            raise ValueError("模板规则必须有显式 allPatterns")

    def match(self, text: str) -> tuple[str, ...] | None:
        matched: list[str] = []
        for pattern in self.all_patterns:
            if re.search(pattern.regex, text) is None:
                return None
            matched.append(pattern.pattern_id)
        return tuple(matched)


@dataclass(frozen=True)
class TemplateRulePack:
    template_id: str
    revision: str
    content_sha256: str
    approval_state: str
    rules: tuple[TemplateRule, ...]

    def __post_init__(self) -> None:
        if not self.template_id or not self.revision:
            raise ValueError("模板包缺少 templateId/revision")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("模板包 contentSha256 无效")
        if self.approval_state != "approved":
            raise ValueError("模板包未获批准")
        if not self.rules:
            raise ValueError("模板包不能为空")
        ids = [rule.rule_id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("模板规则 ruleId 重复")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TemplateRulePack":
        if not isinstance(value, Mapping):
            raise ValueError("模板包必须是对象")
        raw_rules = value.get("rules")
        if not isinstance(raw_rules, Sequence) or isinstance(raw_rules, (str, bytes)):
            raise ValueError("模板包 rules 必须是数组")
        rules: list[TemplateRule] = []
        pack_revision = _revision(value.get("revision"))
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, Mapping):
                raise ValueError("模板规则必须是对象")
            raw_match = raw_rule.get("match")
            if not isinstance(raw_match, Mapping) or set(raw_match).difference({"allPatterns"}):
                raise ValueError("模板规则 match 只允许 allPatterns")
            raw_patterns = raw_match.get("allPatterns")
            if not isinstance(raw_patterns, Sequence) or isinstance(raw_patterns, (str, bytes)):
                raise ValueError("模板规则 allPatterns 必须是数组")
            patterns: list[TemplatePattern] = []
            for raw_pattern in raw_patterns:
                if not isinstance(raw_pattern, Mapping) or set(raw_pattern).difference({"patternId", "regex"}):
                    raise ValueError("模板 pattern 只允许 patternId/regex")
                patterns.append(TemplatePattern(_string(raw_pattern.get("patternId")), _string(raw_pattern.get("regex"))))
            raw_extractor = raw_rule.get("matterExtract")
            extractor: MatterExtractor | None = None
            if raw_extractor is not None:
                if not isinstance(raw_extractor, Mapping) or set(raw_extractor) != {"pattern", "groups"}:
                    raise ValueError("matterExtract 必须精确包含 pattern/groups")
                groups = raw_extractor.get("groups")
                if not isinstance(groups, Mapping):
                    raise ValueError("matterExtract.groups 必须是对象")
                extractor = MatterExtractor(
                    _string(raw_extractor.get("pattern")),
                    {key: _string(groups.get(key)) for key in _MATTER_FIELDS},
                )
            # Selector forms are rejected instead of being silently treated as regexes.
            # In particular, the four prohibited fields must never become a hard-exclude.
            if raw_rule.get("action") == "exclude" and any(selector in raw_rule for selector in _FORBIDDEN_EXCLUDE_SELECTORS):
                raise ValueError("禁止条件不能作为 exclude 模板选择器")
            rules.append(TemplateRule(
                rule_id=_string(raw_rule.get("ruleId")), revision=_revision(raw_rule.get("revision")) or pack_revision,
                action=_string(raw_rule.get("action")), audit_reason=_string(raw_rule.get("auditReason")),
                all_patterns=tuple(patterns), matter_extractor=extractor,
            ))
        return cls(
            template_id=_string(value.get("templateId")), revision=pack_revision,
            content_sha256=_string(value.get("contentSha256")), approval_state=_string(value.get("approvalState")),
            rules=tuple(rules),
        )


@dataclass(frozen=True)
class MatterKey:
    subject: str
    object: str
    date: str
    amount: str
    stage: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "MatterKey | None":
        if not isinstance(value, Mapping):
            return None
        parts = tuple(_string(value.get(name)).casefold() for name in _MATTER_FIELDS)
        if not all(parts):
            return None
        return cls(*parts)

    @property
    def stable_key(self) -> str:
        return _digest(*tuple(getattr(self, name) for name in _MATTER_FIELDS))

    @property
    def relation_key(self) -> str:
        # A correction can legitimately change date/amount/stage.  The relation is
        # deliberately weaker than a merge key and is only an audit link.
        return _digest(self.subject, self.object)


@dataclass(frozen=True)
class PrefilterAudit:
    document_id: str
    revision: int
    raw_sha256: str
    normalized_sha256: str
    action: str
    reason: str
    rule_id: str | None
    matched_pattern_ids: tuple[str, ...]
    duplicate_of: tuple[str, int] | None
    matter: MatterKey | None


@dataclass(frozen=True)
class PackageMember:
    document: Any
    source_refs: tuple[tuple[str, int], ...]
    raw_sha256: str
    normalized_sha256: str
    action: str
    rule_id: str | None
    matter: MatterKey | None


@dataclass(frozen=True)
class EvidencePackage:
    package_id: str
    member_hash: str
    action: str
    matter: MatterKey | None
    members: tuple[PackageMember, ...]
    related_package_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrefilterResult:
    state: str  # ready | not_configured
    template_id: str | None
    template_revision: str | None
    audits: tuple[PrefilterAudit, ...]
    packages: tuple[EvidencePackage, ...]
    counts: Mapping[str, int]


@dataclass(frozen=True)
class _ContentGroup:
    canonical: Any
    source_refs: tuple[tuple[str, int], ...]
    raw_sha256: str
    normalized_sha256: str
    action: str
    reason: str
    rule_id: str | None
    matched_pattern_ids: tuple[str, ...]
    matter: MatterKey | None


def _document_matter(document: Any, rules: Sequence[TemplateRule], text: str) -> MatterKey | None:
    metadata_matter = MatterKey.from_mapping(_metadata(document).get("prefilterMatter"))
    if metadata_matter is not None:
        return metadata_matter
    for rule in rules:
        if rule.matter_extractor is None:
            continue
        matter = rule.matter_extractor.extract(text)
        if matter is not None:
            return matter
    return None


def _decision(text: str, rules: Sequence[TemplateRule]) -> tuple[str, str, str | None, tuple[str, ...]]:
    """Return an explicit action; protect outranks defer, which outranks exclude."""
    matches: list[tuple[TemplateRule, tuple[str, ...]]] = []
    for rule in rules:
        matched = rule.match(text)
        if matched is not None:
            matches.append((rule, matched))
    for action in ("protect", "defer", "exclude"):
        for rule, matched in matches:
            if rule.action == action:
                return action, rule.audit_reason, rule.rule_id, matched
    return "defer", "unknown_template", None, ()


def prefilter_documents(
    documents: Sequence[Any], *, rule_pack: TemplateRulePack | Mapping[str, Any] | None,
) -> PrefilterResult:
    """Deduplicate frozen documents and create conservative, model-free packages.

    ``None`` or an invalid/unapproved pack is an explicit ``not_configured`` result;
    it creates no package that an integration could accidentally send to a provider.
    Invalid *supplied* packs raise so a configuration writer cannot conceal a broken
    production template behind an empty result.
    """
    if rule_pack is None:
        return PrefilterResult("not_configured", None, None, (), (), {
            "input": len(documents), "exactDuplicates": 0, "normalizedDuplicates": 0,
            "exclude": 0, "defer": 0, "protect": 0, "packages": 0,
        })
    pack = rule_pack if isinstance(rule_pack, TemplateRulePack) else TemplateRulePack.from_mapping(rule_pack)
    raw_first: dict[str, tuple[str, int]] = {}
    normalized_first: dict[str, tuple[str, int]] = {}
    audits: list[PrefilterAudit] = []
    group_by_normalized: dict[str, _ContentGroup] = {}
    exact_duplicates = 0
    normalized_duplicates = 0
    for document in documents:
        ref = _ref_payload(document)
        text = _analysis_text(document)
        raw = getattr(document, "original_text", None)
        if not isinstance(raw, str):
            raw = text
        raw_sha = _digest(raw)
        normalized_sha = _digest(text)
        action, reason, rule_id, pattern_ids = _decision(text, pack.rules)
        matter = _document_matter(document, pack.rules, text)
        duplicate_of = raw_first.get(raw_sha)
        if duplicate_of is not None:
            exact_duplicates += 1
        else:
            raw_first[raw_sha] = ref
        if normalized_sha in normalized_first:
            normalized_duplicates += 1
        else:
            normalized_first[normalized_sha] = ref
        audits.append(PrefilterAudit(
            document_id=ref[0], revision=ref[1], raw_sha256=raw_sha, normalized_sha256=normalized_sha,
            action=action, reason=reason, rule_id=rule_id, matched_pattern_ids=pattern_ids,
            duplicate_of=duplicate_of, matter=matter,
        ))
        existing = group_by_normalized.get(normalized_sha)
        if existing is None:
            group_by_normalized[normalized_sha] = _ContentGroup(
                canonical=document, source_refs=(ref,), raw_sha256=raw_sha, normalized_sha256=normalized_sha,
                action=action, reason=reason, rule_id=rule_id, matched_pattern_ids=pattern_ids, matter=matter,
            )
            continue
        # Exact/normalized duplicates retain every frozen source ref.  A protected
        # duplicate cannot lose its protected status merely because an earlier copy
        # had a weaker template classification.
        actions = {existing.action, action}
        combined_action = "protect" if "protect" in actions else "defer" if "defer" in actions else "exclude"
        group_by_normalized[normalized_sha] = _ContentGroup(
            canonical=existing.canonical, source_refs=tuple(sorted(set(existing.source_refs + (ref,)))),
            raw_sha256=existing.raw_sha256, normalized_sha256=existing.normalized_sha256,
            action=combined_action,
            reason=existing.reason if combined_action == existing.action else reason,
            rule_id=existing.rule_id if combined_action == existing.action else rule_id,
            matched_pattern_ids=existing.matched_pattern_ids if combined_action == existing.action else pattern_ids,
            matter=existing.matter or matter,
        )

    groups = tuple(sorted(group_by_normalized.values(), key=lambda item: item.normalized_sha256))
    package_groups: dict[tuple[str, str], list[_ContentGroup]] = {}
    for group in groups:
        if group.action == "exclude":
            continue
        if group.action == "protect":
            # Preserve a correction/denial as its own package.  Its relation key is
            # attached below; merging would make the conflicting update disappear.
            key = ("protect", group.normalized_sha256)
        elif group.matter is not None:
            key = ("matter", group.matter.stable_key)
        else:
            key = ("unknown", group.normalized_sha256)
        package_groups.setdefault(key, []).append(group)

    provisional: list[EvidencePackage] = []
    for (kind, key), members in sorted(package_groups.items()):
        ordered = tuple(sorted(members, key=lambda item: item.normalized_sha256))
        action = "protect" if any(member.action == "protect" for member in ordered) else "defer"
        matter = ordered[0].matter if kind == "matter" else ordered[0].matter
        member_hash = _digest(*(
            member.normalized_sha256 + ":" + ",".join(f"{item[0]}@{item[1]}" for item in member.source_refs)
            for member in ordered
        ))
        package_id = "pkg_" + _digest(pack.template_id, pack.revision, kind, key)[:32]
        provisional.append(EvidencePackage(
            package_id=package_id, member_hash=member_hash, action=action, matter=matter,
            members=tuple(PackageMember(
                document=member.canonical, source_refs=member.source_refs,
                raw_sha256=member.raw_sha256, normalized_sha256=member.normalized_sha256,
                action=member.action, rule_id=member.rule_id, matter=member.matter,
            ) for member in ordered),
        ))
    by_relation: dict[str, list[str]] = {}
    for package in provisional:
        if package.matter is not None:
            by_relation.setdefault(package.matter.relation_key, []).append(package.package_id)
    packages = tuple(EvidencePackage(
        package_id=package.package_id, member_hash=package.member_hash, action=package.action,
        matter=package.matter, members=package.members,
        related_package_ids=tuple(sorted(item for item in by_relation.get(package.matter.relation_key, ()) if item != package.package_id)) if package.action == "protect" and package.matter is not None else (),
    ) for package in provisional)
    counts = {
        "input": len(documents), "exactDuplicates": exact_duplicates,
        "normalizedDuplicates": normalized_duplicates,
        "exclude": sum(audit.action == "exclude" for audit in audits),
        "defer": sum(audit.action == "defer" for audit in audits),
        "protect": sum(audit.action == "protect" for audit in audits),
        "packages": len(packages),
    }
    for rule in pack.rules:
        counts[f"rule:{rule.rule_id}"] = sum(audit.rule_id == rule.rule_id for audit in audits)
    counts["rule:unknown_template"] = sum(audit.rule_id is None for audit in audits)
    return PrefilterResult("ready", pack.template_id, pack.revision, tuple(audits), packages, counts)


def audit_payload(result: PrefilterResult) -> Mapping[str, Any]:
    """Safe aggregate for offline evidence; documents and source text never leave it."""
    return {
        "state": result.state,
        "templateId": result.template_id,
        "templateRevision": result.template_revision,
        "counts": dict(result.counts),
        "packageActions": {
            "defer": sum(package.action == "defer" for package in result.packages),
            "protect": sum(package.action == "protect" for package in result.packages),
        },
        "memberCount": sum(len(package.members) for package in result.packages),
        "ruleCounts": {
            key.removeprefix("rule:"): value
            for key, value in result.counts.items()
            if key.startswith("rule:")
        },
    }


def screening_manifest_payload(result: PrefilterResult) -> Mapping[str, Any]:
    """Immutable text-free manifest for durable task audit before model admission."""
    if result.state != "ready":
        raise ValueError("未配置筛分不能写入资料包清单")
    def matter_payload(matter: MatterKey | None) -> Mapping[str, str] | None:
        if matter is None:
            return None
        return {field: getattr(matter, field) for field in _MATTER_FIELDS}
    return {
        "templateId": result.template_id, "templateRevision": result.template_revision,
        "counts": dict(result.counts),
        "packages": [{
            "packageId": package.package_id, "memberHash": package.member_hash,
            "action": package.action, "matter": matter_payload(package.matter),
            "memberRefs": [{"documentId": ref[0], "revision": ref[1]}
                           for member in package.members for ref in member.source_refs],
            "relatedPackageIds": list(package.related_package_ids),
        } for package in result.packages],
        "audit": [{
            "documentId": audit.document_id, "revision": audit.revision,
            "rawSha256": audit.raw_sha256, "normalizedSha256": audit.normalized_sha256,
            "action": audit.action, "reason": audit.reason, "ruleId": audit.rule_id,
            "matchedPatternIds": list(audit.matched_pattern_ids),
            "duplicateOf": ({"documentId": audit.duplicate_of[0], "revision": audit.duplicate_of[1]}
                            if audit.duplicate_of else None),
            "matter": matter_payload(audit.matter),
        } for audit in result.audits],
    }
