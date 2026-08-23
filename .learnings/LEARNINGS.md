# Project learnings

## [LRN-20260823-001] correction

**Logged**: 2026-08-23T15:38:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary

K9 的成绩口径高于后来冲突的施工裁定：观察与未结算样本不进入成立率分子或分母。

### Details

V2.5.0 的 B22 曾把“全部正式清单”作为成立率分母，这与 K9 §八及新架构的明确原文冲突。
用户在 V2.5.1 Build 12 快修中重新裁定“按 K9”，同时确认消息面剔除后的补位只要后备池
尚未用尽，就必须继续补到原目标数量；轮数上限不能截断这项产品承诺。

### Suggested Action

成绩单只以 `confirmed + rejected` 为成立率分母；`observed` 与缺失终值均排除。删除
`maxBackfillRounds` 配置和实现，补位循环只由“达到原目标数量”或“后备池耗尽”终止，
并用具名回归测试锁住两条口径。

### Metadata

- Source: user_feedback
- Related Files: PROJECT_PLAN.md, Backend/neckline/scorecard/listing.py,
  Backend/neckline/report/evening.py
- Tags: k9, scorecard, observed, backfill, source-authority
- Promoted: PROJECT_PLAN.md

---

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
