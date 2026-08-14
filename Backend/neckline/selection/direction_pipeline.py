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
from neckline.llm.router import TASK_DEEP_REASON, TASK_DIRECTION_TRIAGE, TASK_DRIVER_SEARCH
from neckline.scan.seeds import DriverSeed
from neckline.selection.deep_queue import (
    DeepQueue,
    DirectionPipelineConfig,
    DirectionPipelineConfigError,
    build_deep_queue,
)
from neckline.selection.deep_reason import DeepReasonResult, parse_deep_reason
from neckline.selection.direction_brief import DirectionBrief, build_briefs
from neckline.selection.direction_inventory import build_inventory
from neckline.selection.direction_merge import merge_directions
from neckline.selection.direction_triage import disposition_map, parse_triage_response
from neckline.selection import run_store


_TRIAGE_SYSTEM = (
    "你只做方向初读。只返回 JSON，directions 中每项有 directionId、"
    "disposition(deep|normal|reserve|unfit) 与一句 reason。不得搜索、不得生成篮子或价格计划。"
)
_RESEARCH_SYSTEM = "为一个已进入深研队列的机械方向检索当日证据；只返回简短 JSON 证据。"
_REASON_SYSTEM = (
    "你只对给定已检索方向作完整结构化判断。返回 JSON {directions:[...]}; 每项必须包含 "
    "directionId,name,driver,driver_kind,why_now,seed_keys,members,narrative，并可含现有六关输入和 card_material。"
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
    research_provider: Optional[LLMProvider],
    reason_provider: Optional[LLMProvider],
    db_path: Optional[Path] = None,
    transport: Optional[Any] = None,
    qualification_callback: Optional[Callable[[Sequence[Mapping[str, Any]], Mapping[str, Mapping[str, Any]]], int]] = None,
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

    date_s = _trade_date(trade_date)
    inventory = build_inventory(seeds)
    merged = merge_directions(build_briefs(inventory.directions), policy=cfg.cross_seed_merge_policy)
    run_id = run_store.create_run(date_s, dict(config or {}), db_path=db_path)
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
    if triage_provider is None or research_provider is None or reason_provider is None:
        return _finish_unavailable(
            run_id, "今日选股关键研究服务不可用，保留上一份已完成结果。", "provider_unavailable",
            db_path=db_path, notes=("direction_pipeline_provider_unavailable",),
        )

    disposition: Dict[str, str] = {}
    started_all = time.monotonic()
    tokens_used = 0
    for batch_no, start in enumerate(range(0, len(merged.directions), cfg.triage_batch_size), start=1):
        batch = merged.directions[start:start + cfg.triage_batch_size]
        started = time.monotonic()
        try:
            result = triage_provider.chat(
                [ChatMessage(role="system", content=_TRIAGE_SYSTEM),
                 ChatMessage(role="user", content=date_anchor_line(trade_date) + "\n" + json.dumps({"directions": [b.public_dict() for b in batch]}, ensure_ascii=False))],
                enable_search=False, transport=transport,
            )
        except Exception as exc:  # record the retryable batch state, never disappear it
            for brief in batch:
                disposition[brief.direction_id] = "reserve"
                run_store.update_direction_disposition(run_id, brief.direction_id, triage_disposition="reserve", db_path=db_path)
                run_store.add_event(run_id, "triage_reserve", direction_id=brief.direction_id,
                                    reason=f"triage_call_failed:{type(exc).__name__}", batch_no=batch_no, db_path=db_path)
            continue
        call_id, tokens = _record_call(run_id, TASK_DIRECTION_TRIAGE, batch_no, triage_provider, result, started,
                                       enable_search=False, db_path=db_path)
        if tokens is None:
            return _finish_unavailable(run_id, "今日选股用量无法核验，保留上一份已完成结果。", "usage_unavailable",
                                       db_path=db_path, notes=("triage_usage_unavailable",))
        tokens_used += tokens
        parsed = parse_triage_response(_json_payload(getattr(result, "content", None)), batch)
        for item in parsed.decisions:
            disposition[item.direction_id] = item.disposition
            run_store.update_direction_disposition(run_id, item.direction_id, triage_disposition=item.disposition, db_path=db_path)
            run_store.add_event(run_id, f"triage_{item.disposition}", direction_id=item.direction_id,
                                reason=item.reason, batch_no=batch_no, llm_call_id=call_id, db_path=db_path)

    if time.monotonic() - started_all >= cfg.selection_wall_seconds or tokens_used >= cfg.selection_token_budget:
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
    while True:
        queue: DeepQueue = build_deep_queue(
            merged.directions, disposition, cfg, already_queued=queued, queue_round=fill_round,
            limit=None if fill_round == 0 else cfg.fill_batch_size,
        )
        if not queue.entries:
            break
        queued.update(queue.direction_ids)
        for item in queue.entries:
            run_store.add_event(run_id, "deep_queued", direction_id=item.direction_id, reason=item.coverage_reason,
                                fill_round=fill_round, db_path=db_path)
        brief_by_id = {item.direction_id: item for item in merged.directions}
        researched = []
        for item in queue.entries:
            if time.monotonic() - started_all >= cfg.selection_wall_seconds or tokens_used >= cfg.selection_token_budget:
                terminal_state, terminal_text = "partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。"
                break
            brief = brief_by_id[item.direction_id]
            batch_no += 1
            started = time.monotonic()
            try:
                result = research_provider.chat(
                    [ChatMessage(role="system", content=_RESEARCH_SYSTEM),
                     ChatMessage(role="user", content=date_anchor_line(trade_date) + "\n" + json.dumps({"directionId": brief.direction_id, "label": brief.label, "brief": brief.public_dict()}, ensure_ascii=False))],
                    enable_search=True, search_query=brief.label, transport=transport,
                )
            except Exception as exc:
                run_store.add_event(run_id, "research_unavailable", direction_id=brief.direction_id,
                                    reason=f"research_call_failed:{type(exc).__name__}", batch_no=batch_no,
                                    fill_round=fill_round, db_path=db_path)
                continue
            call_id, tokens = _record_call(run_id, TASK_DRIVER_SEARCH, batch_no, research_provider, result, started,
                                           enable_search=True, db_path=db_path)
            if tokens is None:
                return _finish_unavailable(run_id, "今日选股用量无法核验，保留上一份已完成结果。", "usage_unavailable",
                                           db_path=db_path, notes=("research_usage_unavailable",))
            tokens_used += tokens
            payload = _json_payload(getattr(result, "content", None))
            research[brief.direction_id] = payload if isinstance(payload, Mapping) else {"raw": getattr(result, "content", "")}
            researched.append(brief)
            run_store.add_event(run_id, "research_complete", direction_id=brief.direction_id, batch_no=batch_no,
                                fill_round=fill_round, llm_call_id=call_id, db_path=db_path)
        if terminal_state == "partial" or not researched:
            break
        for index in range(0, len(researched), cfg.deep_reason_batch_size):
            if time.monotonic() - started_all >= cfg.selection_wall_seconds or tokens_used >= cfg.selection_token_budget:
                terminal_state, terminal_text = "partial", "今日深度研究已按预算结束，当前展示已完成判断的方向。"
                break
            group = researched[index:index + cfg.deep_reason_batch_size]
            batch_no += 1
            started = time.monotonic()
            try:
                result = reason_provider.chat(
                    [ChatMessage(role="system", content=_REASON_SYSTEM),
                     ChatMessage(role="user", content=date_anchor_line(trade_date) + "\n" + json.dumps({"directions": [
                         {"directionId": brief.direction_id, "brief": brief.public_dict(), "research": research.get(brief.direction_id, {})}
                         for brief in group
                     ]}, ensure_ascii=False))],
                    enable_search=False, transport=transport,
                )
            except Exception as exc:
                for brief in group:
                    run_store.add_event(run_id, "reasoning_unavailable", direction_id=brief.direction_id,
                                        reason=f"reason_call_failed:{type(exc).__name__}", batch_no=batch_no,
                                        fill_round=fill_round, db_path=db_path)
                continue
            call_id, tokens = _record_call(run_id, TASK_DEEP_REASON, batch_no, reason_provider, result, started,
                                           enable_search=False, db_path=db_path)
            if tokens is None:
                return _finish_unavailable(run_id, "今日选股用量无法核验，保留上一份已完成结果。", "usage_unavailable",
                                           db_path=db_path, notes=("reason_usage_unavailable",))
            tokens_used += tokens
            by_id = {str(node.get("directionId", node.get("direction_id", ""))): node for node in _reason_items(_json_payload(getattr(result, "content", None)))}
            for brief in group:
                raw = by_id.get(brief.direction_id)
                if raw is None:
                    run_store.add_event(run_id, "reasoning_unavailable", direction_id=brief.direction_id,
                                        reason="reason_omitted", batch_no=batch_no, fill_round=fill_round, db_path=db_path)
                    continue
                try:
                    reason = parse_deep_reason(raw, direction_id=brief.direction_id)
                except ValueError as exc:
                    run_store.add_event(run_id, "reasoning_unavailable", direction_id=brief.direction_id,
                                        reason=f"reason_malformed:{exc}", batch_no=batch_no, fill_round=fill_round, db_path=db_path)
                    continue
                proposals.append(reason.to_legacy_proposal())
                card_material = raw.get("card_material", raw.get("cardMaterial", {}))
                material[brief.direction_id] = {
                    "narrative": reason.narrative,
                    "card_material": card_material if isinstance(card_material, Mapping) else {},
                }
                run_store.add_event(run_id, "reasoning_complete", direction_id=brief.direction_id,
                                    batch_no=batch_no, fill_round=fill_round, llm_call_id=call_id, db_path=db_path)
        if terminal_state == "partial":
            break
        # The aggregate entry can supply the real mechanical/gate/Tier preview.
        # Without it this compatibility primitive only knows whether full
        # reasoning parsed; production aggregation always provides the callback.
        qualified = (
            qualification_callback(tuple(proposals), research)
            if qualification_callback is not None else len(proposals)
        )
        run_store.add_event(
            run_id, "fill_evaluated", reason=(
                f"qualified:{qualified}/target:{cfg.sufficient_candidate_count}"
            ), fill_round=fill_round, db_path=db_path,
        )
        if qualified >= cfg.sufficient_candidate_count:
            break
        fill_round += 1
        if fill_round > cfg.max_fill_rounds or len(queued) >= cfg.max_total_deep:
            break

    return DirectionPipelineOutcome(
        terminal_state, terminal_text, run_id=run_id, briefs=merged.directions,
        proposals=tuple(proposals), deep_material_by_direction_id=material,
        research_by_direction_id=research,
        notes=(f"direction_pipeline_run:{run_id}", f"direction_pipeline_deep:{len(queued)}"),
    )


__all__ = ["DirectionPipelineOutcome", "run_direction_pipeline"]
