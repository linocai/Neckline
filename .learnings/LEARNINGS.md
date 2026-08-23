# Project learnings

## [LRN-20260822-001] correction

**Logged**: 2026-08-22T12:00:00+08:00
**Priority**: critical
**Status**: promoted
**Area**: architecture

### Summary

Retired Neckline capabilities are deleted by default; the agent must not invent a historical-retention requirement.

### Details

The agent repeatedly treated retired K8 and concept-board code as material that should remain readable or archived,
even though the user had not requested that policy. This kept dead routes, settings, mappings, data readers, and UI
placeholders alive and made the repository harder to reason about. The user explicitly overruled that assumption:
when a product capability is retired, its obsolete runtime surface should be removed. Git history already preserves
the old implementation.

### Suggested Action

For every retirement, remove the full connected surface: producers, consumers, routes, settings, schemas, tests,
stored artifacts, compatibility mappings, and UI. Keep anything only when the user explicitly identifies a live
operational or legal retention need.

### Metadata

- Source: user_feedback
- Related Files: AGENTS.md, PROJECT_PLAN.md, Backend/neckline, App/Neckline
- Tags: retirement, deletion, k8, compatibility, scope-control

---

## [LRN-20260816-001] correction

**Logged**: 2026-08-16T17:58:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary

Sunday reports use the Sunday publication date as the report date; the preceding Friday is only the market-data
cutoff date.

### Details

The user’s request to use Friday’s close meant “read Friday’s EOD facts and weekend news,” not “publish a Friday-
dated report on Sunday.” Conflating these dates makes the report title, notification, and visible report identity
wrong even when all underlying market calculations correctly use Friday.

### Suggested Action

Carry two explicit dates through the evening/report boundary: `report_date` for title, notification, and visible
identity; `trade_date` for EOD reads, baskets, gates, cards, audit keys, and historical detail routes. On Sunday,
set `report_date=Sunday` and `trade_date=the immediately preceding Friday`.

### Metadata

- Source: user_feedback
- Related Files: Backend/scripts/evening.py, Backend/neckline/report/pipeline.py, Backend/neckline/report/render.py
- Tags: report-date, trade-date, sunday-schedule

### Resolution

- **Resolved**: 2026-08-16T18:04:00+08:00
- **Notes**: Implemented as an explicit report-date/trade-date contract in the report pipeline, API, push payload,
  macOS display, repository documentation, and regression tests.

---

## [LRN-20260816-002] architecture

**Logged**: 2026-08-16T20:00:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary

When a later LLM stage is retired, every downstream field it used to own must become an explicit required output
of the surviving call, with the exact mechanical anchors and a validator that rejects empty-success states.

### Suggested Action

For consolidated LLM calls, verify prompt schema, input sufficiency, parser contract, post-clamp completeness,
and end-to-end frozen output together. Never treat “is a mapping” as evidence that required semantic material was
actually generated.

### Metadata

- Source: production_regression
- Related Files: Backend/neckline/selection/direction_pipeline.py, Backend/neckline/selection/deep_reason.py,
  Backend/neckline/selection/basket_card.py
- Tags: llm-contract, empty-success, frozen-card, mechanical-clamp

---
