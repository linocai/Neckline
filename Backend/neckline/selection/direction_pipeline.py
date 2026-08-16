"""V2.4.2 bounded selection orchestration.

This module deliberately owns only the new direction stages.  It returns deep
reasoning material to the existing mechanical whitelist/six-gate/Tier/card
stages; it does not duplicate any of their judgement rules.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from neckline.llm.base import ChatMessage, LLMProvider
from neckline.llm.json_block import split_narrative_and_reference_json
from neckline.llm.prompt_context import date_anchor_line
from neckline.llm.router import TASK_DEEP_REASON, TASK_DIRECTION_TRIAGE
from neckline.scan.seeds import DriverSeed
from neckline.selection.deep_queue import (
    DeepQueue,
    DirectionPipelineConfig,
    DirectionPipelineConfigError,
    build_deep_queue,
    build_mechanical_shortlist,
)
from neckline.selection.deep_reason import DeepReasonResult, parse_deep_reason
from neckline.selection.deep_research import request_for
from neckline.selection.direction_brief import DirectionBrief, build_briefs
from neckline.selection.direction_inventory import build_inventory
from neckline.selection.direction_merge import merge_directions
from neckline.selection.direction_triage import disposition_map, parse_triage_response
from neckline.selection import run_store


_TRIAGE_SYSTEM = (
    "你只做方向初读。只返回 JSON，directions 中每项有 directionId、"
    "disposition(deep|normal|reserve|unfit) 与一句 reason。不得搜索、不得生成篮子或价格计划。"
)
_REASON_SYSTEM = (
    "你只对给定的已检索方向作完整判断，不联网，只返回 JSON {directions:[...]}。"
    "输入中的每个 directionId 必须恰好返回一次。每项先返回 directionId、"
    "decision(candidate|not_candidate|uncertain)、decisionReason。证据不足必须选 uncertain，"
    "不要凑篮子。not_candidate/uncertain 到此即可。candidate 还必须返回：name、driver、"
    "driver_kind(theme|policy|event|commodity|overseas|rotation|limit_cluster)、why_now、"
    "common_trait、persistence、strengthen_and_invalidate、evidence_conflicts(无矛盾填空串)、"
    "engine_code(C|Z|Y)、narrative、market_check、sector_check、members 和可选 card_material。"
    "market_check/sector_check 以及每个成员的 position_check/core_check 形状均为"
    "{verdict:ok|weak|unfit|unknown,support:[],counter_evidence:[],missing:[],reason:''}。"
    "members 必须为 1 到 3 只且只能从该方向 brief.memberCodes 选择；每项必须含 ts_code、"
    "role(leader|core|elastic)、reason、primary_claim(yes|no|unsure)、"
    "primary_claim_reason、position_check、core_check。不得返回或猜测 seed_keys，"
    "种子身份由服务端绑定。不得使用推荐买入、目标价或收益承诺措辞。"
)


@dataclass(frozen=True)
class DirectionPipelineOutcome:
    """Completed staged work before existing gates/tiering publication."""

    selection_state: str
    selection_state_text: str
    run_id: Optional[str] = None
    briefs: Tuple[DirectionBrief, ...] = ()
    proposals: Tuple[Mapping[str, Any], ...] = ()
    deep_material_by_direction_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    research_by_direction_id: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    notes: Tuple[str, ...] = ()

    @property
    def terminal(self) -> bool:
        return self.selection_state == "unavailable"


def _trade_date(value: Any) -> str:
    return value.strftime("%Y%m%d")


def _usage(result: Any) -> Mapping[str, Any]:
    return {
        "prompt_tokens": getattr(result, "prompt_tokens", None),
        "completion_tokens": getattr(result, "completion_tokens", None),
        "total_tokens": getattr(result, "total_tokens", None),
        "raw_usage": getattr(result, "raw_usage", {}),
        "usage_unavailable": getattr(result, "usage_unavailable", True),
    }


def _record_call(
    run_id: str, task: str, batch_no: int, provider: Optional[LLMProvider], result: Any,
    started: float, *, enable_search: bool, db_path: Optional[Path],
) -> Tuple[int, Optional[int]]:
    usage = _usage(result)
    call_id = run_store.record_llm_call(
        run_id, task=task, batch_no=batch_no,
        provider=getattr(result, "provider", None) or getattr(provider, "name", None),
        model=getattr(result, "model", None) or getattr(provider, "model", None),
        enable_search=enable_search, wall_ms=max(0, int((time.monotonic() - started) * 1000)),
        usage=usage, db_path=db_path,
    )
    total = usage["total_tokens"]
    if bool(usage["usage_unavailable"]) or isinstance(total, bool) or not isinstance(total, int):
        return call_id, None
    return call_id, total


def _json_payload(content: Any) -> Any:
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return None
    _narrative, payload = split_narrative_and_reference_json(content)
    if payload is not None:
        return payload
    try:
        return json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _finish_unavailable(
    run_id: Optional[str], text: str, reason: str, *, db_path: Optional[Path], notes: Sequence[str],
) -> DirectionPipelineOutcome:
    if run_id:
        run_store.finish_run(
            run_id, selection_state="unavailable", text=text, stop_reason=reason,
            totals={"notes": list(notes)}, db_path=db_path,
        )
    return DirectionPipelineOutcome("unavailable", text, run_id=run_id, notes=tuple(notes))


def _reason_items(payload: Any) -> Tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        payload = payload.get("directions", [payload])
    if not isinstance(payload, list):
        return ()
    return tuple(item for item in payload if isinstance(item, Mapping))


def run_direction_pipeline(
    trade_date: Any,
    seeds: Sequence[DriverSeed],
    *,
    config: Optional[Mapping[str, Any]],
    triage_provider: Optional[LLMProvider],
    research_client: Optional[Any],
    reason_provider: Optional[LLMProvider],
    db_path: Optional[Path] = None,
    transport: Optional[Any] = None,
    search_transport: Optional[Any] = None,
    qualification_callback: Optional[Callable[[Sequence[Mapping[str, Any]], Mapping[str, Mapping[str, Any]]], int]] = None,
    allowed_members_by_seed: Optional[Mapping[str, Sequence[str]]] = None,
    reason_context_builder: Optional[Callable[[DirectionBrief, Mapping[str, Any]], str]] = None,
    budget_mode: str = "enforce",
) -> DirectionPipelineOutcome:
    """Run all visible directions through low-cost triage and bounded deep work.

    Absence of an approved versioned configuration is a public ``unavailable``
    condition, never permission to revive the legacy first-20 route.  Provider
    token usage is mandatory: a missing usage record stops later selection calls.
    """
    # This write-path must be able to surface an auditable ``unavailable`` run
    # on a newly initialized database as well. Read-only consumers never call
    # this initializer; the additive schema hook is only reached by selection
    # execution.
    from neckline.db import init_schema

    init_schema(db_path)
    try:
        cfg = DirectionPipelineConfig.from_mapping(config or {})
    except DirectionPipelineConfigError as exc:
        # Even a rejected configuration is a real run attempt.  Persisting its
        # explicit ``unconfigured`` identity makes the public unavailable state
        # auditable without fabricating any operational threshold.
        date_s = _trade_date(trade_date)
        audit_config = {"version": "unconfigured", "provided": dict(config or {})}
        run_id = run_store.create_run(date_s, audit_config, db_path=db_path)
        return _finish_unavailable(
            run_id, "今日选股配置尚未确认，保留上一份已完成结果。", "configuration_unavailable",
            db_path=db_path, notes=(f"direction_pipeline_config:{exc}",),
        )

    if budget_mode not in {"enforce", "observe_only"}:
        raise ValueError("budget_mode must be enforce or observe_only")
    date_s = _trade_date(trade_date)
    inventory = build_inventory(seeds)
    merged = merge_directions(build_briefs(inventory.directions), policy=cfg.cross_seed_merge_policy)
    audit_config = dict(config or {})
    audit_config["runtimeBudgetMode"] = budget_mode
    run_id = run_store.create_run(date_s, audit_config, db_path=db_path)
    for brief in merged.directions:
        run_store.add_direction(
            run_id, direction_id=brief.direction_id, ordinal=brief.ordinal,
            seed_keys=(brief.seed_key,), brief=brief.public_dict(), merge_status=brief.merge_status,
            db_path=db_path,
        )
        run_store.add_event(run_id, "visible", direction_id=brief.direction_id, db_path=db_path)
        run_store.add_event(run_id, "merged", direction_id=brief.direction_id,
                            reason=brief.merge_status, db_path=db_path)

    if not merged.directions:
        return DirectionPipelineOutcome("complete", "", run_id=run_id, briefs=())
    if triage_provider is None or research_client is None or reason_provider is None:
        return _finish_unavailable(
            run_id, "今日选股关键研究服务不可用，保留上一份已完成结果。", "provider_unavailable",
            db_path=db_path, notes=("direction_pipeline_provider_unavailable",),
        )

    def allowed_members(brief: DirectionBrief) -> Tuple[str, ...]:
        source = (
            allowed_members_by_seed.get(brief.seed_key, ())
            if allowed_members_by_seed is not None else brief.member_codes
        )
        return tuple(dict.fromkeys(str(code).strip() for code in source if str(code).strip()))

    def public_brief(brief: DirectionBrief) -> Mapping[str, Any]:
        value = dict(brief.public_dict())
        value["memberCodes"] = list(allowed_members(brief))
        return value

    shortlist_eligible = tuple(brief for brief in merged.directions if allowed_members(brief))
    mechanical = build_mechanical_shortlist(shortlist_eligible, cfg)
    shortlisted_ids = set(mechanical.direction_ids)
    shortlisted = tuple(item for item in merged.directions if item.direction_id in shortlisted_ids)
    shortlist_reason = {entry.direction_id: entry.coverage_reason for entry in mechanical.entries}
    for brief in merged.directions:
        if brief.direction_id in shortlisted_ids:
            run_store.add_event(
                run_id, "mechanical_shortlisted", direction_id=brief.direction_id,
                reason=shortlist_reason.get(brief.direction_id, "fill:mechanical_order"), db_path=db_path,
            )
        else:
            reserve_reason = (
                "no_eligible_members" if not allowed_members(brief)
                else f"outside_shortlist:{cfg.mechanical_shortlist_limit}"
            )
            run_store.update_direction_disposition(
                run_id, brief.direction_id, final_disposition="mechanical_reserve", db_path=db_path,
            )
            run_store.add_event(
                run_id, "mechanical_reserve", direction_id=brief.direction_id,
                reason=reserve_reason, db_path=db_path,
            )

    disposition: Dict[str, str] = {}
    tokens_used = 0

    def token_budget_exhausted() -> bool:
        # The user removed the aggregate wall-clock cutoff on 2026-08-16.
        # Per-request network timeouts/retries still prevent a hung call; the
        # selection-level stop now measures only provider-reported Tokens.
        return budget_mode == "enforce" and tokens_used >= cfg.selection_token_budget
    for batch_no, start in enumerate(range(0, len(shortlisted), cfg.triage_batch_size), start=1):
        batch = shortlisted[start:start + cfg.triage_batch_size]
        started = time.monotonic()
        try:
            result = triage_provider.chat(
                [ChatMessage(role="system", content=_TRIAGE_SYSTEM),
                 ChatMessage(role="user", content=date_anchor_line(trade_date) + "\n" + json.dumps({"directions": [public_brief(b) for b in batch]}, ensure_ascii=False))],
                enable_search=False, transport=transport,
            )
        except Exception as exc:
            for brief in batch:
                disposition[brief.direction_id] = "reserve"
                run_store.update_direction_disposition(run_id, brief.direction_id, triage_disposition="reserve", db_path=db_path)
                run_store.add_event(run_id, "triage_reserve", direction_id=brief.direction_id,
                                    reason=f"triage_call_failed:{type(exc).__name__}", batch_no=batch_no, db_path=db_path)
            return _finish_unavailable(
                run_id, "今日方向初读服务异常，保留上一份已完成结果。", "triage_provider_unavailable",
                db_path=db_path, notes=(f"triage_call_failed:{type(exc).__name__}",),
            )
        call_id, tokens = _record_call(run_id, TASK_DIRECTION_TRIAGE, batch_no, triage_provider, result, started,
                                       enable_search=False, db_path=db_path)
        if tokens is None:
            return _finish_unavailable(run_id, "今日选股用量无法核验，保留上一份已完成结果。", "usage_unavailable",
                                       db_path=db_path, notes=("triage_usage_unavailable",))
        tokens_used += tokens
        parsed = parse_triage_response(_json_payload(getattr(result, "content", None)), batch)
        if parsed.malformed or any(item.status == "retryable" for item in parsed.decisions):
            for item in parsed.decisions:
                run_store.update_direction_disposition(
                    run_id, item.direction_id, triage_disposition="reserve",
                    final_disposition="contract_error", db_path=db_path,
                )
                run_store.add_event(
                    run_id, "triage_contract_error", direction_id=item.direction_id,
                    reason=item.reason, batch_no=batch_no, llm_call_id=call_id, db_path=db_path,
                )
            return _finish_unavailable(
                run_id, "今日方向初读格式异常，保留上一份已完成结果。", "triage_contract_error",
                db_path=db_path, notes=("triage_contract_error",),
            )
        for item in parsed.decisions:
            disposition[item.direction_id] = item.disposition
            run_store.update_direction_disposition(run_id, item.direction_id, triage_disposition=item.disposition, db_path=db_path)
            run_store.add_event(run_id, f"triage_{item.disposition}", direction_id=item.direction_id,
                                reason=item.reason, batch_no=batch_no, llm_call_id=call_id, db_path=db_path)

    if token_budget_exhausted():
        return DirectionPipelineOutcome("partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。", run_id=run_id,
                                        briefs=merged.directions, notes=("selection_budget_exhausted_before_deep",))

    proposals = []
    material: Dict[str, Mapping[str, Any]] = {}
    research: Dict[str, Mapping[str, Any]] = {}
    queued: set[str] = set()
    batch_no = 0
    fill_round = 0
    terminal_state = "complete"
    terminal_text = ""
    valid_reason_decisions = 0
    research_failures = 0
    while True:
        queue: DeepQueue = build_deep_queue(
            shortlisted, disposition, cfg, already_queued=queued, queue_round=fill_round,
            limit=None if fill_round == 0 else cfg.fill_batch_size,
            enforce_total_limit=True,
        )
        if not queue.entries:
            break
        queued.update(queue.direction_ids)
        for item in queue.entries:
            run_store.add_event(run_id, "deep_queued", direction_id=item.direction_id, reason=item.coverage_reason,
                                fill_round=fill_round, db_path=db_path)
        brief_by_id = {item.direction_id: item for item in shortlisted}
        # Complete one small research/reason cohort before starting the next.
        # Search/reason one small cohort, then immediately run the real
        # mechanical/gate/Tier qualification preview. Once enough publishable
        # baskets exist, later queued directions are not researched merely to
        # consume a preallocated slot.
        target_reached = False
        for index in range(0, len(queue.entries), cfg.deep_reason_batch_size):
            if token_budget_exhausted():
                terminal_state, terminal_text = "partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。"
                break
            group_entries = queue.entries[index:index + cfg.deep_reason_batch_size]
            researched = []
            for item in group_entries:
                if token_budget_exhausted():
                    terminal_state, terminal_text = "partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。"
                    break
                brief = brief_by_id[item.direction_id]
                batch_no += 1
                started = time.monotonic()
                request = request_for(
                    direction_id=brief.direction_id, label=brief.label, brief=public_brief(brief)
                )
                try:
                    searched = research_client.search(request.query, transport=search_transport)
                except Exception as exc:
                    research_failures += 1
                    run_store.add_event(run_id, "research_unavailable", direction_id=brief.direction_id,
                                        reason=f"research_call_failed:{type(exc).__name__}", batch_no=batch_no,
                                        fill_round=fill_round, db_path=db_path)
                    continue
                search_call_id = run_store.record_search_call(
                    run_id, direction_id=brief.direction_id, batch_no=batch_no,
                    provider=str(getattr(research_client, "provider", "tavily")),
                    query=request.query,
                    search_depth=str(getattr(research_client, "search_depth", "basic")),
                    result_count=len(getattr(searched, "hits", ()) or ()),
                    credits=getattr(searched, "credits", None),
                    status="ok" if bool(getattr(searched, "ok", False)) else str(getattr(searched, "reason", "failed")),
                    wall_ms=int(getattr(searched, "wall_ms", max(0, int((time.monotonic() - started) * 1000)))),
                    request_id=getattr(searched, "request_id", None), db_path=db_path,
                )
                if not bool(getattr(searched, "ok", False)):
                    research_failures += 1
                    run_store.add_event(
                        run_id, "research_unavailable", direction_id=brief.direction_id,
                        reason=f"search_call:{search_call_id}:{getattr(searched, 'reason', 'failed')}",
                        batch_no=batch_no, fill_round=fill_round, db_path=db_path,
                    )
                    continue
                payload = searched.evidence_payload()
                research[brief.direction_id] = payload
                researched.append(brief)
                run_store.add_event(
                    run_id, "research_complete", direction_id=brief.direction_id,
                    reason=f"search_call:{search_call_id}", batch_no=batch_no, fill_round=fill_round,
                    db_path=db_path,
                )
            if terminal_state == "partial":
                break
            if not researched:
                continue
            if token_budget_exhausted():
                terminal_state, terminal_text = "partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。"
                break
            group = researched
            batch_no += 1
            started = time.monotonic()
            try:
                result = reason_provider.chat(
                    [ChatMessage(role="system", content=_REASON_SYSTEM),
                     ChatMessage(role="user", content=date_anchor_line(trade_date) + "\n" + json.dumps({"directions": [
                         {
                             "directionId": brief.direction_id,
                             "brief": public_brief(brief),
                             "research": research.get(brief.direction_id, {}),
                             "mechanicalContext": (
                                 reason_context_builder(brief, research.get(brief.direction_id, {}))
                                 if reason_context_builder is not None else ""
                             ),
                         }
                         for brief in group
                     ]}, ensure_ascii=False))],
                    enable_search=False, transport=transport,
                )
            except Exception as exc:
                for brief in group:
                    run_store.update_direction_disposition(
                        run_id, brief.direction_id, final_disposition="reasoning_unavailable", db_path=db_path,
                    )
                    run_store.add_event(run_id, "reasoning_unavailable", direction_id=brief.direction_id,
                                        reason=f"reason_call_failed:{type(exc).__name__}", batch_no=batch_no,
                                        fill_round=fill_round, db_path=db_path)
                return _finish_unavailable(
                    run_id, "今日深度判断服务异常，保留上一份已完成结果。", "reasoning_provider_unavailable",
                    db_path=db_path, notes=(f"reason_call_failed:{type(exc).__name__}",),
                )
            call_id, tokens = _record_call(run_id, TASK_DEEP_REASON, batch_no, reason_provider, result, started,
                                           enable_search=False, db_path=db_path)
            if tokens is None:
                return _finish_unavailable(run_id, "今日选股用量无法核验，保留上一份已完成结果。", "usage_unavailable",
                                           db_path=db_path, notes=("reason_usage_unavailable",))
            tokens_used += tokens
            reason_items = _reason_items(_json_payload(getattr(result, "content", None)))
            returned_ids = [str(node.get("directionId", node.get("direction_id", ""))).strip() for node in reason_items]
            expected_ids = {brief.direction_id for brief in group}
            if len(returned_ids) != len(expected_ids) or set(returned_ids) != expected_ids:
                for brief in group:
                    run_store.update_direction_disposition(
                        run_id, brief.direction_id, final_disposition="contract_error", db_path=db_path,
                    )
                    run_store.add_event(
                        run_id, "reasoning_contract_error", direction_id=brief.direction_id,
                        reason="reason_batch_identity_mismatch", batch_no=batch_no,
                        fill_round=fill_round, llm_call_id=call_id, db_path=db_path,
                    )
                return _finish_unavailable(
                    run_id, "今日深度判断格式异常，保留上一份已完成结果。", "reasoning_contract_error",
                    db_path=db_path, notes=("reason_batch_identity_mismatch",),
                )
            by_id = dict(zip(returned_ids, reason_items))
            for brief in group:
                raw = by_id.get(brief.direction_id)
                try:
                    reason = parse_deep_reason(
                        raw, direction_id=brief.direction_id, seed_key=brief.seed_key,
                        allowed_member_codes=allowed_members(brief),
                    )
                except ValueError as exc:
                    run_store.update_direction_disposition(
                        run_id, brief.direction_id, final_disposition="contract_error", db_path=db_path,
                    )
                    run_store.add_event(run_id, "reasoning_contract_error", direction_id=brief.direction_id,
                                        reason=f"reason_malformed:{exc}", batch_no=batch_no,
                                        fill_round=fill_round, llm_call_id=call_id, db_path=db_path)
                    return _finish_unavailable(
                        run_id, "今日深度判断格式异常，保留上一份已完成结果。", "reasoning_contract_error",
                        db_path=db_path, notes=(f"reason_malformed:{exc}",),
                    )
                valid_reason_decisions += 1
                if not reason.is_candidate:
                    transition = f"deep_{reason.decision}"
                    run_store.update_direction_disposition(
                        run_id, brief.direction_id, final_disposition=transition, db_path=db_path,
                    )
                    run_store.add_event(
                        run_id, transition, direction_id=brief.direction_id,
                        reason=reason.decision_reason, batch_no=batch_no, fill_round=fill_round,
                        llm_call_id=call_id, db_path=db_path,
                    )
                    continue
                proposals.append(reason.to_legacy_proposal())
                card_material = raw.get("card_material", raw.get("cardMaterial", {}))
                material[brief.direction_id] = {
                    "narrative": reason.narrative,
                    "card_material": card_material if isinstance(card_material, Mapping) else {},
                }
                run_store.add_event(run_id, "reasoning_complete", direction_id=brief.direction_id,
                                    reason=reason.decision_reason, batch_no=batch_no,
                                    fill_round=fill_round, llm_call_id=call_id, db_path=db_path)
            # The aggregate entry supplies the real mechanical/gate/Tier
            # preview. Evaluate after every completed cohort so sufficient
            # baskets stop later expensive work immediately.
            try:
                qualified = (
                    qualification_callback(tuple(proposals), research)
                    if qualification_callback is not None else len(proposals)
                )
            except Exception as exc:
                run_store.add_event(
                    run_id, "qualification_unavailable",
                    reason=f"qualification_failed:{type(exc).__name__}", fill_round=fill_round,
                    db_path=db_path,
                )
                return _finish_unavailable(
                    run_id, "今日机械判定服务异常，保留上一份已完成结果。",
                    "qualification_unavailable", db_path=db_path,
                    notes=(f"qualification_failed:{type(exc).__name__}",),
                )
            run_store.add_event(
                run_id, "fill_evaluated", reason=(
                    f"qualified:{qualified}/target:{cfg.sufficient_candidate_count}"
                ), fill_round=fill_round, db_path=db_path,
            )
            if qualified >= cfg.sufficient_candidate_count:
                target_reached = True
                for skipped in queue.entries[index + len(group_entries):]:
                    run_store.update_direction_disposition(
                        run_id, skipped.direction_id, final_disposition="deep_not_needed", db_path=db_path,
                    )
                    run_store.add_event(
                        run_id, "deep_not_needed", direction_id=skipped.direction_id,
                        reason=f"qualified:{qualified}/target:{cfg.sufficient_candidate_count}",
                        fill_round=fill_round, db_path=db_path,
                    )
                break
        if terminal_state == "partial":
            break
        if target_reached:
            break
        fill_round += 1
        if fill_round > cfg.max_fill_rounds or len(queued) >= cfg.max_total_deep:
            break

    if terminal_state == "complete" and queued and valid_reason_decisions == 0:
        return _finish_unavailable(
            run_id, "今日深度研究服务未取得可判断材料，保留上一份已完成结果。",
            "deep_research_unavailable", db_path=db_path,
            notes=("no_valid_deep_reason_decision",),
        )
    if terminal_state == "complete" and research_failures:
        terminal_state = "partial"
        terminal_text = "部分方向未取得联网研究资料，当前只展示已完成判断的方向。"

    return DirectionPipelineOutcome(
        terminal_state, terminal_text, run_id=run_id, briefs=merged.directions,
        proposals=tuple(proposals), deep_material_by_direction_id=material,
        research_by_direction_id=research,
        notes=(f"direction_pipeline_run:{run_id}",
               f"direction_pipeline_shortlist:{len(shortlisted)}",
               f"direction_pipeline_deep:{len(queued)}",
               f"selection_budget_mode:{budget_mode}"),
    )


__all__ = ["DirectionPipelineOutcome", "run_direction_pipeline"]
