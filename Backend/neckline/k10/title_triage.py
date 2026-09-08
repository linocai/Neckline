"""Pure, title-only selection for the K10 discovery pipeline.

The title gate is intentionally narrower than discovery.  It accepts a sealed
DTO instead of a source document, so a caller cannot accidentally send article
text, excerpts, URLs, or an open metadata mapping to the title model.  Each
batch must account for every title it received.  Only after *all* batches have
passed that check may the global reconciler choose the real source articles
whose bodies are eligible for deep understanding.

This module owns no provider, database, or source text.  Its callbacks are
implemented by the pipeline/checkpoint boundary.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Mapping, Protocol, Sequence


_STATUSES = frozenset({"candidate", "uncertain", "same_matter", "no_value", "correction_or_denial"})
_DISPOSITIONS = frozenset({"selected", "merged", "not_selected", "no_value"})
_LIMITS = {"evening": 80, "morning": 40}


class TitleTriageProtocolError(ValueError):
    """A title-model response cannot safely be used to admit any article body."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        # Only fixed validation messages are used here. Persist a specific,
        # non-sensitive diagnostic instead of losing every failure under the
        # generic model_execution_invalid code. Output-contract errors may use
        # the configured single JSON repair; input errors remain terminal.
        if "未完整且唯一覆盖输入" in message:
            self.code = "title_json_coverage_invalid"
        elif "JSON 必须" in message:
            self.code = "title_json_root_invalid"
        elif "refIndex" in message or "合并目标" in message:
            self.code = "title_json_reference_invalid"
        elif "更正/否认不得" in message:
            self.code = "title_protected_merge_invalid"
        elif "标题结果" in message or "入选标题" in message or "未入选标题" in message or "合并标题" in message:
            self.code = "title_json_row_invalid"
        else:
            self.code = "title_protocol_invalid"


@dataclass(frozen=True, slots=True)
class TitleDTO:
    """The complete, non-extensible title-model input contract.

    Deliberately do not add convenience fields here.  Article body, excerpt,
    canonical URL, and source metadata are unavailable by construction.
    """

    document_id: str
    revision: int
    source_key: str
    published_at: str | None
    title: str

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise TitleTriageProtocolError("标题资料缺少 documentId")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise TitleTriageProtocolError("标题资料缺少有效 revision")
        if not isinstance(self.source_key, str) or not self.source_key:
            raise TitleTriageProtocolError("标题资料缺少 sourceKey")
        if self.published_at is not None and not isinstance(self.published_at, str):
            raise TitleTriageProtocolError("标题资料 publishedAt 无效")
        if not isinstance(self.title, str):
            raise TitleTriageProtocolError("标题资料 title 无效")

    @property
    def ref(self) -> tuple[str, int]:
        return (self.document_id, self.revision)

    def payload(self) -> dict[str, object]:
        """Return exactly the fields the title model may receive."""
        return {"documentId": self.document_id, "revision": self.revision,
                "sourceKey": self.source_key, "publishedAt": self.published_at,
                "title": self.title}


@dataclass(frozen=True, slots=True)
class TitleTriageResult:
    """One bounded, auditable judgement for one title in one model batch."""

    document_id: str
    revision: int
    status: str
    matter_key: str
    stage_key: str
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise TitleTriageProtocolError("标题结果缺少 documentId")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise TitleTriageProtocolError("标题结果 revision 无效")
        if self.status not in _STATUSES:
            raise TitleTriageProtocolError("标题结果 status 无效")
        if not isinstance(self.matter_key, str) or not self.matter_key:
            raise TitleTriageProtocolError("标题结果缺少 matterKey")
        if not isinstance(self.stage_key, str) or not self.stage_key:
            raise TitleTriageProtocolError("标题结果缺少 stageKey")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 280:
            raise TitleTriageProtocolError("标题结果 reason 无效")

    @property
    def ref(self) -> tuple[str, int]:
        return (self.document_id, self.revision)


@dataclass(frozen=True, slots=True)
class TitleSelectionItem:
    """Final disposition for a single deduplicated source article."""

    document_id: str
    revision: int
    disposition: str
    matter_key: str
    stage_key: str
    reason: str
    selected_rank: int | None = None
    merged_into: tuple[str, int] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.document_id, str) or not self.document_id:
            raise TitleTriageProtocolError("全局标题结果缺少 documentId")
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 1:
            raise TitleTriageProtocolError("全局标题结果 revision 无效")
        if self.disposition not in _DISPOSITIONS:
            raise TitleTriageProtocolError("全局标题结果 disposition 无效")
        if not isinstance(self.matter_key, str) or not self.matter_key:
            raise TitleTriageProtocolError("全局标题结果缺少 matterKey")
        if not isinstance(self.stage_key, str) or not self.stage_key:
            raise TitleTriageProtocolError("全局标题结果缺少 stageKey")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 280:
            raise TitleTriageProtocolError("全局标题结果 reason 无效")
        if self.disposition == "selected":
            if isinstance(self.selected_rank, bool) or not isinstance(self.selected_rank, int) or self.selected_rank < 1:
                raise TitleTriageProtocolError("入选标题缺少 selectedRank")
            if self.merged_into is not None:
                raise TitleTriageProtocolError("入选标题不能有 mergedInto")
        elif self.selected_rank is not None:
            raise TitleTriageProtocolError("未入选标题不能有 selectedRank")
        if self.disposition == "merged":
            if (not isinstance(self.merged_into, tuple) or len(self.merged_into) != 2
                    or not isinstance(self.merged_into[0], str) or not self.merged_into[0]
                    or isinstance(self.merged_into[1], bool) or not isinstance(self.merged_into[1], int)
                    or self.merged_into[1] < 1):
                raise TitleTriageProtocolError("合并标题缺少真实 mergedInto")
        elif self.merged_into is not None:
            raise TitleTriageProtocolError("非合并标题不能有 mergedInto")

    @property
    def ref(self) -> tuple[str, int]:
        return (self.document_id, self.revision)


@dataclass(frozen=True, slots=True)
class TitleSelection:
    state: str
    window_kind: str
    article_limit: int
    input_refs: tuple[tuple[str, int], ...]
    batch_results: tuple[TitleTriageResult, ...]
    items: tuple[TitleSelectionItem, ...]
    selected_refs: tuple[tuple[str, int], ...]
    manifest_hash: str

    def __post_init__(self) -> None:
        if self.state != "frozen":
            raise TitleTriageProtocolError("标题选择必须冻结后才可使用")
        if self.window_kind not in _LIMITS or self.article_limit != _LIMITS[self.window_kind]:
            raise TitleTriageProtocolError("标题选择 articleLimit 不符合窗口")
        if len(set(self.input_refs)) != len(self.input_refs):
            raise TitleTriageProtocolError("标题输入引用重复")
        if len(set(self.selected_refs)) != len(self.selected_refs) or len(self.selected_refs) > self.article_limit:
            raise TitleTriageProtocolError("标题入选文章数无效")
        if tuple(item.ref for item in sorted((item for item in self.items if item.disposition == "selected"),
                                             key=lambda item: int(item.selected_rank or 0))) != self.selected_refs:
            raise TitleTriageProtocolError("标题入选顺序与逐条结果不一致")


class BatchCall(Protocol):
    def __call__(self, items: tuple[TitleDTO, ...]) -> Sequence[TitleTriageResult]: ...


class ReconcileCall(Protocol):
    def __call__(self, items: tuple[TitleDTO, ...], results: tuple[TitleTriageResult, ...],
                 article_limit: int) -> Sequence[TitleSelectionItem]: ...


def title_batch_payload(items: Sequence[TitleDTO]) -> tuple[dict[str, object], ...]:
    """Serialization helper for a provider adapter; field whitelist is testable."""
    return tuple(item.payload() for item in items)


def _batch_request_context(items: Sequence[TitleDTO], policy: Mapping[str, object]) -> tuple[tuple[TitleDTO, ...], Mapping[str, object]]:
    frozen = _validate_items(items)
    if not isinstance(policy, Mapping) or not policy:
        raise TitleTriageProtocolError("标题初筛缺少已批准 policy")
    content = policy.get("content")
    if not isinstance(content, Mapping) or not content:
        raise TitleTriageProtocolError("标题初筛 policy.content 无效")
    return frozen, content


def _batch_payload(*, frozen: Sequence[TitleDTO], policy: Mapping[str, object], content: Mapping[str, object],
                   output: Mapping[str, object]) -> dict[str, object]:
    return {
        "policy": {key: policy[key] for key in ("policyId", "revision", "contentSha256") if key in policy},
        # The approved policy is an explicit part of every model request. Its
        # schema is validated by config/store; a hash alone cannot govern model
        # behavior after a policy revision.
        "policyContent": dict(content),
        "items": list(title_batch_payload(frozen)),
        "output": dict(output),
    }


def batch_request_spec(items: Sequence[TitleDTO], policy: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Build the indexed response contract for a new title-model request.

    Inputs remain sealed five-field TitleDTO objects. The model returns only a
    batch-local index and judgement fields, eliminating long document-id echoing
    as a source of incomplete coverage. ``i`` is never added to input items.
    """
    frozen, content = _batch_request_context(items, policy)
    operation = (
        "逐条理解输入的财经资讯标题。按输入 items 的 0 起始位置 i 每条返回一次；"
        "不得遗漏、重复、越界或改写 i。不可按栏目、利好词、篇幅、海外/上游或创业板代码硬排。"
        "转载且无新增事实标 same_matter；更正、取消、否认和可能构成重大反证标 correction_or_denial，"
        "绝不能当普通转载。标题含糊时标 uncertain；只在标题明显与股票事件理解无关时标 no_value。"
        "matterKey/stageKey 是稳定、简短的事项和阶段指纹，不得包含原文以外事实。"
    )
    return operation, _batch_payload(
        frozen=frozen, policy=policy, content=content,
        output={"items": [{"i": 0,
                            "status": "candidate|uncertain|same_matter|no_value|correction_or_denial",
                            "matterKey": "string", "stageKey": "string", "reason": "short string"}]},
    )


def _batch_result_from_mapping(raw: Mapping[str, object]) -> TitleTriageResult:
    return TitleTriageResult(
        raw.get("documentId") if isinstance(raw.get("documentId"), str) else "",
        raw.get("revision") if isinstance(raw.get("revision"), int) and not isinstance(raw.get("revision"), bool) else 0,
        raw.get("status") if isinstance(raw.get("status"), str) else "",
        raw.get("matterKey") if isinstance(raw.get("matterKey"), str) else "",
        raw.get("stageKey") if isinstance(raw.get("stageKey"), str) else "",
        raw.get("reason") if isinstance(raw.get("reason"), str) else "",
    )


def normalize_batch_result(raw: Mapping[str, object], items: Sequence[TitleDTO]) -> dict[str, object]:
    """Map either strict indexed output or a strict old checkpoint to real refs."""
    if not isinstance(raw, Mapping) or set(raw) != {"items"} or not isinstance(raw.get("items"), list):
        raise TitleTriageProtocolError("标题批次 JSON 必须只含 items 数组")
    frozen = _validate_items(items)
    rows = raw["items"]
    if any(not isinstance(row, Mapping) for row in rows):
        raise TitleTriageProtocolError("标题批次 items 必须是对象")
    indexed_keys = {"i", "status", "matterKey", "stageKey", "reason"}
    legacy_keys = {"documentId", "revision", "status", "matterKey", "stageKey", "reason"}
    row_keys = [set(row) for row in rows]
    if all(keys == indexed_keys for keys in row_keys):
        by_index: dict[int, Mapping[str, object]] = {}
        for row in rows:
            index = row.get("i")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(frozen):
                raise TitleTriageProtocolError("标题批次 i 越界或无效")
            if index in by_index:
                raise TitleTriageProtocolError("标题批次 i 重复")
            by_index[index] = row
        if set(by_index) != set(range(len(frozen))):
            raise TitleTriageProtocolError("标题批次 i 未完整覆盖输入")
        canonical_rows = [
            {"documentId": item.document_id, "revision": item.revision,
             "status": by_index[index]["status"], "matterKey": by_index[index]["matterKey"],
             "stageKey": by_index[index]["stageKey"], "reason": by_index[index]["reason"]}
            for index, item in enumerate(frozen)
        ]
    elif all(keys == legacy_keys for keys in row_keys):
        # Legacy rows are accepted only when they are already a full, exact
        # canonical checkpoint. This path is for cache lookup, never new output.
        canonical_rows = [dict(row) for row in rows]
    else:
        raise TitleTriageProtocolError("标题批次 items 必须统一使用完整索引或旧引用格式")
    parsed = _validate_batch(batch=frozen, results=tuple(_batch_result_from_mapping(row) for row in canonical_rows))
    return {"items": [{"documentId": result.document_id, "revision": result.revision,
                       "status": result.status, "matterKey": result.matter_key,
                       "stageKey": result.stage_key, "reason": result.reason} for result in parsed]}


def validate_batch_result(raw: Mapping[str, object], items: Sequence[TitleDTO]) -> tuple[TitleTriageResult, ...]:
    """Decode either strict protocol version and prove complete real-ref coverage."""
    canonical = normalize_batch_result(raw, items)
    return _validate_batch(batch=_validate_items(items),
                           results=tuple(_batch_result_from_mapping(row) for row in canonical["items"]))


def _global_participants(items: tuple[TitleDTO, ...], results: tuple[TitleTriageResult, ...]) -> tuple[tuple[int, TitleDTO, TitleTriageResult], ...]:
    by_ref = {result.ref: result for result in results}
    return tuple((index, item, by_ref[item.ref]) for index, item in enumerate(items)
                 if by_ref[item.ref].status != "no_value")


def reconcile_request_spec(items: Sequence[TitleDTO], batch_results: Sequence[TitleTriageResult], article_limit: int,
                           policy: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Return the global-only reconciliation request after every batch is valid."""
    frozen = _validate_items(items)
    parsed = _validate_batch(batch=frozen, results=batch_results)
    if article_limit not in _LIMITS.values():
        raise TitleTriageProtocolError("标题全局选择 articleLimit 无效")
    if not isinstance(policy, Mapping) or not policy:
        raise TitleTriageProtocolError("标题初筛缺少已批准 policy")
    operation = (
        "你在为 K10 选择值得深读的资讯：目标是寻找创业板未来两个交易日的消息驱动机会，"
        "并识别会推翻已有关注理由的重大反证。此处选择资料，不做最终股票推荐或交易判断。"
        "逐条比较标题所显示的新增实质事实、事实可能影响的经营/供需/政策环节、以及继续核查的价值。"
        "不能用新闻热度、公司名气、已经涨了多少、目标价、泛泛市场关注度代替新增事实。"
        "单纯行情回顾、旧概念梳理、主观预测、一般文字勘误或与投资判断无关的道歉，"
        "若标题没有新增实质事实，应让位于明确订单、合作变化、商业化进展、实质政策或重大反证；"
        "这必须基于标题语义，不能按栏目或关键词机械排除含有真实新事实的报道。"
        "来源主体在创业板外、海外或上游也可以入选，但必须存在合理的产业链影响路径，不能强行关联。"
        "更正/否认标签只代表不能当普通转载处理，不代表自动值得深读；"
        "须区分关键商业事实被否认与无关紧要的文字订正。已有关注对象的重大反证优先。"
        "使用全部标题及逐条初筛结果做一次全局事项合并和排序。必须审阅全部参与标题并声明 selectionComplete=true，"
        "reviewedCount 必须等于输入参与标题数量。"
        "同一事项的无新增事实转载只保留最合适的真实来源文章；更正、取消、否认、重大反证和独立新阶段"
        "不得作为普通转载合并。selected 最多 articleLimit 篇，按 selectedRank 从 1 连续编号；"
        "不够不凑数。只输出 selected 与 merged；其余已审阅标题由系统记录为未入选，不要重复输出。"
        "selected/merged 的 i 必须互斥；merged.into 必须指向 selected 的不同真实索引。"
        "输出协议额外要求：merged 的 i 和 into 两端 status 都必须不是 correction_or_denial。"
        "重复的更正报道可以只入选最合适的一篇，其余留在未入选补集中；不要把它们写进 merged。"
        "入选 reason 必须说明值得核查的新增事实和潜在影响，不得只复述标题或称市场关注度高。"
        "不要伪造标题没有的事实或已发布候选上下文，也不要输出 K10 的最终优先级分类。"
    )
    participants = _global_participants(frozen, parsed)
    content = policy.get("content")
    if not isinstance(content, Mapping) or not content:
        raise TitleTriageProtocolError("标题初筛 policy.content 无效")
    return operation, {
        "policy": {key: policy[key] for key in ("policyId", "revision", "contentSha256") if key in policy},
        "policyContent": dict(content),
        "articleLimit": article_limit,
        # Real source refs and verbose batch explanations stay in the local
        # ledger. Global comparison needs titles, provenance and semantic tags,
        # not another copy of long opaque IDs or the prior model's prose.
        "items": [{"i": index, "sourceKey": item.source_key, "publishedAt": item.published_at,
                   "title": item.title, "status": result.status, "matterKey": result.matter_key,
                   "stageKey": result.stage_key}
                  for index, item, result in participants],
        "output": {"selectionComplete": True, "reviewedCount": len(participants),
                   "selected": [{"i": 0, "selectedRank": 1, "reason": "short string"}],
                   "merged": [{"i": 1, "into": 0, "reason": "short string"}]},
    }


def normalize_reconcile_result(raw: Mapping[str, object], items: Sequence[TitleDTO],
                               batch_results: Sequence[TitleTriageResult], article_limit: int) -> dict[str, object]:
    """Normalize an explicit complete declaration to the canonical old ledger form.

    The current model response is deliberately compact: it proves it reviewed
    every global participant and only names retained/merged rows. Unnamed rows
    are a successful, explicit complement only after that proof passes. Strict
    canonical output remains readable for an already-completed old checkpoint.
    """
    frozen = _validate_items(items)
    results = _validate_batch(batch=frozen, results=batch_results)
    participants = _global_participants(frozen, results)
    by_index = {index: (item, result) for index, item, result in participants}
    canonical_keys = {"selected", "merged", "notSelected"}
    compact_keys = {"selectionComplete", "reviewedCount", "selected", "merged"}
    if not isinstance(raw, Mapping):
        raise TitleTriageProtocolError("全局标题 JSON 必须是对象")
    if set(raw) == canonical_keys:
        if any(not isinstance(raw.get(key), list) for key in canonical_keys):
            raise TitleTriageProtocolError("旧全局标题 checkpoint 数组无效")
        if (any(not isinstance(row, Mapping) or set(row) != {"i", "selectedRank", "reason"}
                for row in raw["selected"])
                or any(not isinstance(row, Mapping) or set(row) != {"i", "into", "reason"}
                       for row in raw["merged"])
                or any(isinstance(index, bool) or not isinstance(index, int) for index in raw["notSelected"])):
            raise TitleTriageProtocolError("旧全局标题 checkpoint 条目字段无效")
        canonical = {key: list(raw[key]) for key in canonical_keys}
    elif set(raw) == compact_keys:
        if raw.get("selectionComplete") is not True:
            raise TitleTriageProtocolError("全局标题未明确完成审阅")
        reviewed_count = raw.get("reviewedCount")
        if (isinstance(reviewed_count, bool) or not isinstance(reviewed_count, int)
                or reviewed_count != len(participants)):
            raise TitleTriageProtocolError("全局标题 reviewedCount 与参与标题不一致")
        selected, merged = raw.get("selected"), raw.get("merged")
        if not isinstance(selected, list) or not isinstance(merged, list):
            raise TitleTriageProtocolError("全局标题 selected/merged 必须是数组")
        selected_keys, merged_keys = {"i", "selectedRank", "reason"}, {"i", "into", "reason"}
        covered: set[int] = set()

        def indexed(row: object, expected_keys: set[str], *, key: str = "i") -> int:
            if not isinstance(row, Mapping) or set(row) != expected_keys:
                raise TitleTriageProtocolError("全局标题条目字段无效")
            value = row.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value not in by_index:
                raise TitleTriageProtocolError("全局标题输出含陌生 refIndex")
            return value

        for row in selected:
            index = indexed(row, selected_keys)
            if index in covered:
                raise TitleTriageProtocolError("全局标题输出 refIndex 重复")
            covered.add(index)
        for row in merged:
            index = indexed(row, merged_keys)
            target = indexed(row, merged_keys, key="into")
            if index in covered or index == target:
                raise TitleTriageProtocolError("全局标题合并 refIndex 无效")
            covered.add(index)
        canonical = {"selected": [dict(row) for row in selected], "merged": [dict(row) for row in merged],
                     "notSelected": sorted(set(by_index) - covered)}
    else:
        raise TitleTriageProtocolError("全局标题 JSON 字段无效")
    # Do not accept a compact declaration until every rank, merge target,
    # correction protection and 80/40 limit is checked by the canonical decoder.
    _decode_canonical_reconcile_result(canonical, frozen, results, article_limit)
    return canonical


def _decode_canonical_reconcile_result(raw: Mapping[str, object], items: Sequence[TitleDTO],
                                       batch_results: Sequence[TitleTriageResult], article_limit: int) -> tuple[TitleSelectionItem, ...]:
    """Strictly decode the canonical selected/merged/notSelected ledger form."""
    if (not isinstance(raw, Mapping) or set(raw) != {"selected", "merged", "notSelected"}
            or any(not isinstance(raw.get(key), list) for key in raw)):
        raise TitleTriageProtocolError("全局标题 JSON 必须只含 selected/merged/notSelected 数组")
    frozen = _validate_items(items)
    results = _validate_batch(batch=frozen, results=batch_results)
    participants = _global_participants(frozen, results)
    by_index = {index: (item, result) for index, item, result in participants}
    covered: set[int] = set()
    selection: list[TitleSelectionItem] = []

    def _index(row: Mapping[str, object], key: str = "i") -> int:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value not in by_index:
            raise TitleTriageProtocolError("全局标题输出含陌生 refIndex")
        return value

    for row in raw["selected"]:
        if not isinstance(row, Mapping):
            raise TitleTriageProtocolError("全局标题 selected 项必须是对象")
        index = _index(row)
        if index in covered:
            raise TitleTriageProtocolError("全局标题输出 refIndex 重复")
        rank = row.get("selectedRank")
        reason = row.get("reason")
        item, result = by_index[index]
        selection.append(TitleSelectionItem(item.document_id, item.revision, "selected", result.matter_key,
                                            result.stage_key, reason if isinstance(reason, str) else "", rank if isinstance(rank, int) and not isinstance(rank, bool) else None))
        covered.add(index)
    pending_merges: list[tuple[int, int, str]] = []
    for row in raw["merged"]:
        if not isinstance(row, Mapping):
            raise TitleTriageProtocolError("全局标题 merged 项必须是对象")
        index, target = _index(row), _index(row, "into")
        if index in covered or index == target:
            raise TitleTriageProtocolError("全局标题合并 refIndex 无效")
        reason = row.get("reason")
        if not isinstance(reason, str):
            raise TitleTriageProtocolError("全局标题合并缺少原因")
        pending_merges.append((index, target, reason))
        covered.add(index)
    for raw_index in raw["notSelected"]:
        if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index not in by_index:
            raise TitleTriageProtocolError("全局标题 notSelected 含陌生 refIndex")
        index = raw_index
        if index in covered:
            raise TitleTriageProtocolError("全局标题输出 refIndex 重复")
        item, result = by_index[index]
        reason = (result.reason.strip() + "；全局排序未入选")[:280]
        selection.append(TitleSelectionItem(item.document_id, item.revision, "not_selected", result.matter_key,
                                            result.stage_key, reason))
        covered.add(index)
    if covered != set(by_index):
        raise TitleTriageProtocolError("全局标题输出未完整覆盖候选 refIndex")
    selected_indices = {index for index, item, _ in participants
                        if any(output.ref == item.ref and output.disposition == "selected" for output in selection)}
    for index, target, reason in pending_merges:
        item, result = by_index[index]
        target_item, target_result = by_index[target]
        if index not in by_index or target not in selected_indices:
            raise TitleTriageProtocolError("标题合并目标必须是入选真实文章")
        if result.status == "correction_or_denial" or target_result.status == "correction_or_denial":
            raise TitleTriageProtocolError("更正/否认不得作为普通转载合并")
        selection.append(TitleSelectionItem(item.document_id, item.revision, "merged", result.matter_key,
                                            result.stage_key, reason, merged_into=target_item.ref))
    # no_value has already received its complete batch-level audit; reconstruct
    # it locally instead of requiring thousands of repetitive global rows.
    for item in frozen:
        result = {row.ref: row for row in results}[item.ref]
        if result.status == "no_value":
            selection.append(TitleSelectionItem(item.document_id, item.revision, "no_value", result.matter_key,
                                                result.stage_key, result.reason))
    return _validate_selection(items=frozen, results=results, selection_items=tuple(selection), article_limit=article_limit)


def validate_reconcile_result(raw: Mapping[str, object], items: Sequence[TitleDTO],
                              batch_results: Sequence[TitleTriageResult], article_limit: int) -> tuple[TitleSelectionItem, ...]:
    """Decode a compact complete declaration or a strict old checkpoint."""
    canonical = normalize_reconcile_result(raw, items, batch_results, article_limit)
    return _decode_canonical_reconcile_result(canonical, items, batch_results, article_limit)


def _review_context(items: Sequence[TitleDTO], batch_results: Sequence[TitleTriageResult],
                    proposed_selection: TitleSelection) -> tuple[tuple[TitleDTO, ...], tuple[TitleTriageResult, ...],
                                                                   tuple[TitleSelectionItem, ...], tuple[TitleSelectionItem, ...]]:
    frozen = _validate_items(items)
    results = _validate_batch(batch=frozen, results=batch_results)
    if not isinstance(proposed_selection, TitleSelection):
        raise TitleTriageProtocolError("标题终检缺少冻结拟选名单")
    if (proposed_selection.input_refs != tuple(item.ref for item in frozen)
            or proposed_selection.article_limit not in _LIMITS.values()
            or proposed_selection.batch_results != results):
        raise TitleTriageProtocolError("标题终检拟选名单与当前标题审计不一致")
    all_items = _validate_selection(items=frozen, results=results, selection_items=proposed_selection.items,
                                    article_limit=proposed_selection.article_limit)
    selected = tuple(sorted((item for item in all_items if item.disposition == "selected"),
                            key=lambda item: int(item.selected_rank or 0)))
    if tuple(item.ref for item in selected) != proposed_selection.selected_refs:
        raise TitleTriageProtocolError("标题终检拟选顺序无效")
    return frozen, results, all_items, selected


def review_request_spec(items: Sequence[TitleDTO], batch_results: Sequence[TitleTriageResult],
                        proposed_selection: TitleSelection, policy: Mapping[str, object]) -> tuple[str, dict[str, object]]:
    """Build the title-only final pruning request; it can keep or remove, never add."""
    frozen, results, _, selected = _review_context(items, batch_results, proposed_selection)
    if not isinstance(policy, Mapping) or not policy or not isinstance(policy.get("content"), Mapping):
        raise TitleTriageProtocolError("标题终检缺少已批准 policy")
    by_ref = {item.ref: item for item in frozen}
    result_by_ref = {result.ref: result for result in results}
    operation = (
        "对拟深读标题做终检，只能保留或删除，绝不能新增候选或补足名额。目标是 K10 创业板未来两日的新增实质事实。"
        "合并同次发布但只是不同细节的报道；不得因已有涨跌、目标价、无实质新增或泛泛传染说法保留。"
        "更正、否认和重大反证不得被原消息吞没。标题未明示的事实不能用 proposedReason 当作证据。"
        "必须 complete=true，并让 kept 与 removed 按 i 恰好覆盖全部拟选；removed.duplicateOf 只能指向 kept。"
    )
    return operation, {
        "operation": "titleSelectionReview",
        "policy": {key: policy[key] for key in ("policyId", "revision", "contentSha256") if key in policy},
        "policyContent": dict(policy["content"]),
        "items": [
            {"i": index, "sourceKey": by_ref[item.ref].source_key, "publishedAt": by_ref[item.ref].published_at,
             "title": by_ref[item.ref].title, "status": result_by_ref[item.ref].status,
             "proposedReason": item.reason}
            for index, item in enumerate(selected)
        ],
        "output": {"complete": True,
                   "kept": [{"i": 0, "reason": "short string"}],
                   "removed": [{"i": 1, "reason": "short string", "duplicateOf": None}]},
    }


def normalize_review_result(raw: Mapping[str, object], items: Sequence[TitleDTO],
                            batch_results: Sequence[TitleTriageResult], proposed_selection: TitleSelection) -> dict[str, object]:
    """Validate a complete final-pruning response without exposing real refs to it."""
    _, results, _, selected = _review_context(items, batch_results, proposed_selection)
    result_by_ref = {result.ref: result for result in results}
    status_by_index = {index: result_by_ref[item.ref].status for index, item in enumerate(selected)}
    if (not isinstance(raw, Mapping) or set(raw) != {"complete", "kept", "removed"}
            or raw.get("complete") is not True or not isinstance(raw.get("kept"), list)
            or not isinstance(raw.get("removed"), list)):
        raise TitleTriageProtocolError("标题终检 JSON 必须完整声明 complete/kept/removed")
    kept_keys, removed_keys = {"i", "reason"}, {"i", "reason", "duplicateOf"}
    covered: set[int] = set()
    kept: list[dict[str, object]] = []
    removed: list[dict[str, object]] = []

    def index(row: object, expected: set[str]) -> int:
        if not isinstance(row, Mapping) or set(row) != expected:
            raise TitleTriageProtocolError("标题终检条目字段无效")
        value = row.get("i")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value >= len(selected):
            raise TitleTriageProtocolError("标题终检含陌生 i")
        if value in covered:
            raise TitleTriageProtocolError("标题终检 i 重复")
        reason = row.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 280:
            raise TitleTriageProtocolError("标题终检理由无效")
        covered.add(value)
        return value

    for row in raw["kept"]:
        value = index(row, kept_keys)
        kept.append({"i": value, "reason": row["reason"]})
    kept_indices = {row["i"] for row in kept}
    for row in raw["removed"]:
        value = index(row, removed_keys)
        duplicate_of = row.get("duplicateOf")
        if duplicate_of is not None and (isinstance(duplicate_of, bool) or not isinstance(duplicate_of, int)):
            raise TitleTriageProtocolError("标题终检 duplicateOf 无效")
        removed.append({"i": value, "reason": row["reason"], "duplicateOf": duplicate_of})
    if covered != set(range(len(selected))):
        raise TitleTriageProtocolError("标题终检未完整覆盖拟选")
    for row in removed:
        duplicate_of = row["duplicateOf"]
        if duplicate_of is not None and duplicate_of not in kept_indices:
            raise TitleTriageProtocolError("标题终检 duplicateOf 必须指向 kept")
        # A correction/denial is a protected contrary fact. Calling it a duplicate
        # of an ordinary retained headline would silently let that original message
        # erase the contrary record. It may be independently removed for a stated
        # low-value reason, or deduplicated only against another protected kept row.
        if (duplicate_of is not None and status_by_index[int(row["i"])] == "correction_or_denial"
                and status_by_index[duplicate_of] != "correction_or_denial"):
            raise TitleTriageProtocolError("标题终检不得把反证并入普通原消息")
    return {"complete": True, "kept": sorted(kept, key=lambda row: int(row["i"])),
            "removed": sorted(removed, key=lambda row: int(row["i"]))}


def apply_title_review(items: Sequence[TitleDTO], batch_results: Sequence[TitleTriageResult],
                       proposed_selection: TitleSelection, review: Mapping[str, object]) -> tuple[TitleSelectionItem, ...]:
    """Apply a validated final review to the full audit, only reducing selected bodies."""
    frozen, results, original, selected = _review_context(items, batch_results, proposed_selection)
    normalized = normalize_review_result(review, frozen, results, proposed_selection)
    result_by_ref = {result.ref: result for result in results}
    kept_by_index = {int(row["i"]): str(row["reason"]) for row in normalized["kept"]}
    removed_by_ref = {selected[int(row["i"])].ref: row for row in normalized["removed"]}
    kept_ref_by_index = {index: selected[index].ref for index in kept_by_index}
    kept_rank = {ref: rank for rank, ref in enumerate((selected[index].ref for index in sorted(kept_by_index)), start=1)}
    kept_reason_by_ref = {selected[index].ref: reason for index, reason in kept_by_index.items()}

    def safe_merge(source_ref: tuple[str, int], target_ref: tuple[str, int]) -> bool:
        return (result_by_ref[source_ref].status != "correction_or_denial"
                and result_by_ref[target_ref].status != "correction_or_denial")

    def removed_target(ref: tuple[str, int]) -> tuple[tuple[str, int] | None, str]:
        row = removed_by_ref.get(ref)
        if row is None:
            return None, "终检未保留原拟选"
        target_index = row["duplicateOf"]
        target = kept_ref_by_index.get(target_index) if isinstance(target_index, int) and not isinstance(target_index, bool) else None
        return target, str(row["reason"])

    final: list[TitleSelectionItem] = []
    for item in original:
        result = result_by_ref[item.ref]
        if item.disposition == "no_value":
            final.append(item)
            continue
        if item.disposition == "selected":
            if item.ref in kept_rank:
                final.append(TitleSelectionItem(item.document_id, item.revision, "selected", item.matter_key, item.stage_key,
                                                kept_reason_by_ref[item.ref],
                                                selected_rank=kept_rank[item.ref]))
                continue
            target, reason = removed_target(item.ref)
            if target is not None and safe_merge(item.ref, target):
                final.append(TitleSelectionItem(item.document_id, item.revision, "merged", item.matter_key, item.stage_key,
                                                reason, merged_into=target))
            else:
                final.append(TitleSelectionItem(item.document_id, item.revision, "not_selected", item.matter_key,
                                                item.stage_key, reason))
            continue
        if item.disposition == "merged":
            assert item.merged_into is not None
            target = item.merged_into
            reason = item.reason
            if target not in kept_rank:
                target, reason = removed_target(target)
            if target is not None and safe_merge(item.ref, target):
                final.append(TitleSelectionItem(item.document_id, item.revision, "merged", item.matter_key, item.stage_key,
                                                reason, merged_into=target))
            else:
                final.append(TitleSelectionItem(item.document_id, item.revision, "not_selected", item.matter_key,
                                                item.stage_key, reason))
            continue
        final.append(item)
    return _validate_selection(items=frozen, results=results, selection_items=tuple(final),
                               article_limit=proposed_selection.article_limit)


def _validate_items(items: Sequence[TitleDTO]) -> tuple[TitleDTO, ...]:
    frozen = tuple(items)
    if any(not isinstance(item, TitleDTO) for item in frozen):
        raise TitleTriageProtocolError("标题初筛只接受 TitleDTO")
    refs = [item.ref for item in frozen]
    if len(set(refs)) != len(refs):
        raise TitleTriageProtocolError("标题输入 documentId/revision 重复")
    return frozen


def _validate_batch(*, batch: tuple[TitleDTO, ...], results: Sequence[TitleTriageResult]) -> tuple[TitleTriageResult, ...]:
    parsed = tuple(results)
    if any(not isinstance(result, TitleTriageResult) for result in parsed):
        raise TitleTriageProtocolError("标题批次结果类型无效")
    expected = {item.ref for item in batch}
    actual = [result.ref for result in parsed]
    if len(actual) != len(expected) or set(actual) != expected or len(set(actual)) != len(actual):
        raise TitleTriageProtocolError("标题批次结果未完整且唯一覆盖输入")
    return tuple(sorted(parsed, key=lambda result: result.ref))


def _validate_selection(*, items: tuple[TitleDTO, ...], results: tuple[TitleTriageResult, ...],
                        selection_items: Sequence[TitleSelectionItem], article_limit: int) -> tuple[TitleSelectionItem, ...]:
    parsed = tuple(selection_items)
    if any(not isinstance(item, TitleSelectionItem) for item in parsed):
        raise TitleTriageProtocolError("全局标题结果类型无效")
    expected = {item.ref for item in items}
    actual = [item.ref for item in parsed]
    if len(actual) != len(expected) or set(actual) != expected or len(set(actual)) != len(actual):
        raise TitleTriageProtocolError("全局标题结果未完整且唯一覆盖输入")
    results_by_ref = {result.ref: result for result in results}
    selected = [item for item in parsed if item.disposition == "selected"]
    ranks = [int(item.selected_rank or 0) for item in selected]
    if sorted(ranks) != list(range(1, len(selected) + 1)) or len(selected) > article_limit:
        raise TitleTriageProtocolError("全局标题入选排序或文章上限无效")
    selected_refs = {item.ref for item in selected}
    for item in parsed:
        result = results_by_ref[item.ref]
        if (item.matter_key, item.stage_key) != (result.matter_key, result.stage_key):
            raise TitleTriageProtocolError("全局标题结果改写了批次事项身份")
        if item.disposition == "merged":
            assert item.merged_into is not None
            if item.merged_into not in expected or item.merged_into == item.ref:
                raise TitleTriageProtocolError("标题合并目标不是真实不同输入")
            target = results_by_ref[item.merged_into]
            if result.status == "correction_or_denial" or target.status == "correction_or_denial":
                raise TitleTriageProtocolError("更正/否认不得作为普通转载合并")
            if item.merged_into not in selected_refs:
                raise TitleTriageProtocolError("标题合并目标必须是入选真实文章")
        if result.status == "correction_or_denial" and item.disposition == "merged":
            raise TitleTriageProtocolError("更正/否认不得被合并")
        if result.status == "no_value" and item.disposition != "no_value":
            raise TitleTriageProtocolError("批次标为 no_value 的标题必须保持 no_value")
    return tuple(sorted(parsed, key=lambda item: item.ref))


def _manifest_hash(*, window_kind: str, items: tuple[TitleDTO, ...], results: tuple[TitleTriageResult, ...],
                   selection: tuple[TitleSelectionItem, ...]) -> str:
    rows = [window_kind]
    rows.extend(f"I:{item.document_id}@{item.revision}:{item.source_key}:{item.published_at or ''}:{item.title}" for item in items)
    rows.extend(f"B:{result.document_id}@{result.revision}:{result.status}:{result.matter_key}:{result.stage_key}:{result.reason}" for result in results)
    rows.extend(f"S:{item.document_id}@{item.revision}:{item.disposition}:{item.selected_rank or 0}:{item.merged_into or ''}" for item in selection)
    return sha256("\x1f".join(rows).encode("utf-8")).hexdigest()


def triage_titles(
    items: Sequence[TitleDTO], *, window_kind: str, policy: Mapping[str, object],
    batch_call: BatchCall, reconcile_call: ReconcileCall,
    batch_concurrency: int,
    checkpoint: Callable[[Mapping[str, object]], None] | None = None,
) -> TitleSelection:
    """Understand every title, globally reconcile, and return frozen body admission.

    ``policy`` is deliberately opaque to this pure module: config/store bind
    its approved identity.  Requiring a non-empty mapping prevents a caller
    from silently invoking a title model without that binding.
    """
    if window_kind not in _LIMITS:
        raise TitleTriageProtocolError("标题初筛窗口必须是 evening 或 morning")
    if not isinstance(policy, Mapping) or not policy:
        raise TitleTriageProtocolError("标题初筛缺少已批准 policy")
    frozen = _validate_items(items)
    if not callable(batch_call):
        raise TitleTriageProtocolError("标题初筛缺少批次调用")
    if isinstance(batch_concurrency, bool) or not isinstance(batch_concurrency, int) or batch_concurrency < 1:
        raise TitleTriageProtocolError("标题批次并发数无效")
    # The batch size is a provider/request concern.  Callers deliberately pass
    # their already frozen batches through the callback one complete batch at a
    # time; this function never applies a Top-N or truncates an input sequence.
    # A policy may declare an engineering batch size, but it is not a selection limit.
    raw_batch_size = policy.get("batchSize")
    if isinstance(raw_batch_size, bool) or not isinstance(raw_batch_size, int) or raw_batch_size < 1:
        raise TitleTriageProtocolError("标题 policy batchSize 无效")
    batches = tuple(frozen[start:start + raw_batch_size] for start in range(0, len(frozen), raw_batch_size))

    def run_batch(index: int, batch: tuple[TitleDTO, ...]) -> tuple[int, tuple[TitleTriageResult, ...]]:
        return index, _validate_batch(batch=batch, results=batch_call(batch))

    # Every sealed title batch must succeed before any global ranking is allowed.
    # Results are retained by deterministic batch index, so completion timing
    # cannot alter the reconciliation input or persisted audit order. Checkpoints
    # also run on this caller thread after all batches are valid; the provider/cache
    # boundary owns each request's durable completion independently.
    completed: dict[int, tuple[TitleTriageResult, ...]] = {}
    executor = ThreadPoolExecutor(max_workers=min(batch_concurrency, max(1, len(batches))))
    pending: dict[object, int] = {}
    next_batch = 0
    failure: BaseException | None = None
    try:
        # This is deliberately a sliding window rather than submit-all. A failed
        # title request must not leave hundreds of already-queued paid requests
        # behind it. Running requests settle for their own durable receipts; work
        # which never started is cancelled and no global/body stage can follow.
        while next_batch < len(batches) and len(pending) < batch_concurrency:
            future = executor.submit(run_batch, next_batch, batches[next_batch])
            pending[future] = next_batch
            next_batch += 1
        while pending:
            done, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
            # Include every completion visible in this scheduling turn before
            # replenishing the window. Otherwise a sibling failure could already
            # be terminal while a successful sibling quietly starts another batch.
            done.update(future for future in pending if future.done())
            first_error: BaseException | None = None
            for future in done:
                index = pending.pop(future)
                try:
                    _, parsed = future.result()
                    completed[index] = parsed
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            if first_error is not None:
                failure = first_error
                for future in pending:
                    future.cancel()
                break
            while next_batch < len(batches) and len(pending) < batch_concurrency:
                future = executor.submit(run_batch, next_batch, batches[next_batch])
                pending[future] = next_batch
                next_batch += 1
    finally:
        # ``cancel_futures`` prevents the executor from beginning a queued title
        # request after a sibling failed. ``wait`` is intentional: in-flight
        # calls have a provider/store receipt boundary and must settle cleanly.
        executor.shutdown(wait=True, cancel_futures=True)
    if failure is not None:
        raise failure
    if len(completed) != len(batches):
        raise TitleTriageProtocolError("标题批次合并未完整覆盖输入")
    results: list[TitleTriageResult] = []
    for index, batch in enumerate(batches):
        parsed = completed[index]
        results.extend(parsed)
        if checkpoint is not None:
            checkpoint({"stage": "title_triage_batch", "state": "completed", "inputRefs": [
                {"documentId": item.document_id, "revision": item.revision} for item in batch],
                "resultRefs": [{"documentId": item.document_id, "revision": item.revision} for item in parsed]})
    ordered_results = tuple(sorted(results, key=lambda result: result.ref))
    if len(ordered_results) != len(frozen):
        raise TitleTriageProtocolError("标题批次合并未完整覆盖输入")
    article_limit = _LIMITS[window_kind]
    if not callable(reconcile_call):
        raise TitleTriageProtocolError("标题初筛缺少全局协调调用")
    reconciled = reconcile_call(frozen, ordered_results, article_limit)
    ordered_selection = _validate_selection(items=frozen, results=ordered_results,
                                            selection_items=reconciled, article_limit=article_limit)
    selected_refs = tuple(item.ref for item in sorted((item for item in ordered_selection if item.disposition == "selected"),
                                                       key=lambda item: int(item.selected_rank or 0)))
    selection = TitleSelection("frozen", window_kind, article_limit, tuple(item.ref for item in frozen),
                               ordered_results, ordered_selection, selected_refs,
                               _manifest_hash(window_kind=window_kind, items=frozen, results=ordered_results,
                                              selection=ordered_selection))
    if checkpoint is not None:
        checkpoint({"stage": "title_selection", "state": "frozen", "windowKind": window_kind,
                    "articleLimit": article_limit, "inputCount": len(frozen),
                    "selectedRefs": [{"documentId": ref[0], "revision": ref[1]} for ref in selected_refs],
                    "manifestHash": selection.manifest_hash})
    return selection


def title_from_document_fields(*, document_id: str, revision: int, source_key: str,
                               published_at: str | None, metadata: Mapping[str, object]) -> TitleDTO:
    """Construct the sealed DTO by reading only the source's title metadata key."""
    title = metadata.get("title") if isinstance(metadata, Mapping) else None
    if not isinstance(title, str):
        raise TitleTriageProtocolError("来源资料缺少标题，不能进入标题初筛")
    return TitleDTO(document_id, revision, source_key, published_at, title)


__all__ = [
    "BatchCall", "ReconcileCall", "TitleDTO", "TitleSelection", "TitleSelectionItem",
    "TitleTriageProtocolError", "TitleTriageResult", "apply_title_review", "batch_request_spec",
    "legacy_batch_request_spec", "normalize_batch_result", "normalize_reconcile_result", "normalize_review_result",
    "reconcile_request_spec", "review_request_spec", "title_batch_payload", "validate_batch_result", "validate_reconcile_result",
    "title_from_document_fields", "triage_titles",
]
