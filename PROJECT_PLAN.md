# Neckline project plan

> Updated: 2026-08-14. This is the only current control plane. Historical work is under `archive/`.

## 1. Current goal and release boundary

V2.4.2 passed independent review → repair → RC and was released after the user's explicit 2026-08-14
authorization. Production runs V2.4.2 with the balanced pipeline package; macOS currently runs Build 3.
Build 5 is a tested hotfix RC pending production deployment and macOS replacement; iOS remains a user
handoff and will not be installed. Continue directly on `main`; do not create a branch.

The baseline is `v2.4.1` Build 2. Its record is
[V2.4.1 execution record](archive/施工图/V2.4.1_执行计划_20260813.md).
`Backend/V2.4.2_BACKEND_MEMO.md` is user-provided discussion authority for this cycle only. Builder reads
it on startup, absorbs its binding rules into code/tests, then deletes it. It is untracked and must not be
committed as a second plan.

## 2. Binding V2.4.2 decisions

- Scope is the backend basket chain, audit/persistence/API contracts, SOP, and only additive Swift
  compatibility plus honest status UI. V2.4.1 selection information architecture stays intact.
- Direction visibility is not a cost quota. All `DriverSeed`s become queryable, deterministic
  `DirectionBrief`s. Normalize/merge/brief creation makes **zero LLM calls**.
- Remove `MAX_SEEDS_AGGREGATED=20` as aggregate-entry eligibility. `20` survives only as the configured
  initial deep-research maximum.
- New batch triage is short JSON, `enable_search=false`, and exactly
  `deep|normal|reserve|unfit` plus a short reason. Missing input is server-clamped to `reserve`.
- Malformed/absent triage batches are retryable reserve/unavailable records, never disappearance or `unfit`.
- Queue selection is deterministic: triage, existing mechanical order, industry, seed type, and mechanical
  potential C/Z/Y applicability. Record each coverage choice and fill round.
- Search and full reasoning run only for queued directions. Fill stops at configured sufficiency, exhausted
  reserves, or an exhausted wall/token budget.
- The six-gate definition, thresholds, enforcement, existing T1≤2/T2≤5 capacities, and research boundary
  remain unchanged.
- `tier.py` is deterministic only: make no new `TASK_TIER_RANK` call. `basket_card.py` mechanically
  assembles/freezes from full reasoning: make no new `TASK_SCRIPT` call.
- Full reasoning is the sole producer of basket narrative, members/roles, engine claim, gate-side evidence,
  price-plan candidates, risks, and human card material. Whitelist, clamps, gates, Tier, and spec assembly
  remain mechanical authorities.
- Every selection LLM call records task, batch, model/provider, search flag, wall duration,
  prompt/completion/total tokens, raw usage, and `usage_unavailable`. Never infer tokens from characters.
  Review accounting remains independent.

## 3. Confirmed facts and guarded configuration

- Directly use only confirmed facts: `deep_initial_limit=20`, current Tier capacities, existing mechanical
  and gate rules, and no Tier/card repeat LLM calls.
- The user approved production package `Backend/config/direction-pipeline.v2.4.2-balanced.json`
  (`v2.4.2-balanced-r1`) on 2026-08-14: initial/max deep `20/32`, triage/deep/fill batches `8/2/4`,
  qualified target `7`, industry/seed-kind/potential-CZY coverage `6/4/2`, Token/wall stops
  `350000/1500`, at most `3` fill rounds, normal before reserve, and identity-only merge.
- Outside that approved versioned package, do not invent production defaults or silently alter its
  batch, coverage, queue, merge, Token, wall-time, or fill values. `triage_concurrency=1` records the
  current serial executor and must not be presented as implemented parallelism.
- Versioned `direction_pipeline` explicitly validates every queue, coverage, and budget field. Missing/invalid
  config returns `selectionState=unavailable`, preserves the prior published snapshot, and never falls back
  to the legacy 20-seed path. Test fixtures supply every value explicitly.
- Until a cross-seed merge policy is confirmed, only input-guaranteed duplicate seed identity removal runs.
  Other apparent duplicates remain separate briefs with `merge_policy_unconfigured`; do not add fuzzy,
  member-overlap, alias, or hidden numeric rules.
- Without authorization to estimate tokens, `usage_unavailable` is a budget-accounting fault: save that call,
  start no later calls in its selection phase, and terminate `unavailable` rather than claim compliance.

## 4. Target contract, trace, and publication

```text
all seeds → DirectionBrief (mechanical) → batch triage → covered deep queue
         → deep search → deep reason → existing six gates → deterministic Tier
         → mechanical BasketCard → atomic published snapshot
```

- Add `selection_runs`: UUID `run_id`, trade date, version/config snapshot or fingerprint, lifecycle and
  publication state, start/end/stop reason, budget snapshots, and totals.
- Add `selection_directions`: composite run/direction identity, source seeds, brief, merge/triage/final
  disposition. Add append-only `selection_direction_events` and `selection_llm_calls`; events reference
  calls and record transition, reason, batch/fill round, and timestamp.
- Shared states distinguish empty from absent:
  `visible → merged → triage_* → deep_queued → research_* → reasoning_* → gate_* →`
  `tiered|capacity_overflow → basket_frozen`, with explicit unavailable/retry causes.
- Long mechanical/LLM work writes run audit progress outside SQLite transactions. One short publish
  transaction writes terminal basket/tier/card/report facts and marks one run published.
- `partial` may publish only fully reasoned and gated baskets after budget stop. `processing` and
  `unavailable` never replace an earlier complete snapshot. Later same-day runs stay separately auditable
  and do not overwrite published frozen facts.
- `baskets`, `basket_members`, `gate_evaluations`, `out_candidates`, `tier_history`, and `basket_cards`
  remain final-fact stores. Do not backfill/rewrite historical cards, Tier rows, or legacy handoffs.
- Add optional report/basket-daily `selectionState` (`processing|complete|partial|unavailable`) and
  `selectionStateText`; add optional card `generationSource=deep_reason`.
- When deep reasoning has required card material, emit `llmStage=ok` and `degraded=false`. Deterministic
  Tier writes retain `rankInTier`, `rankMech`, `llmRankDelta=0`, `llmReason=null`; history is unchanged.

## 5. Executable build slices and ownership

1. **Contracts/persistence — Backend core.** Own `db.py`, new `selection/{run_store,direction_inventory,
   direction_merge,direction_brief,direction_triage,deep_queue,deep_research,deep_reason}.py`, and focused
   fixtures/tests. First define DTOs, JSON/config schemas, migrations/indexes, append-only guards,
   deterministic IDs, and read APIs. Migrations are additive/idempotent and use temporary DBs only.
2. **LLM metering/routing — Backend LLM.** Own `llm/{base,openai_compat,router,factory,budget}.py` and tests.
   Normalize usage for ordinary/tool-loop replies; add triage/deep-reason routes; isolate triage from search;
   add real-token plus wall ledgers. Keep deprecated routes readable but remove their new-flow use.
3. **Pipeline — Backend selection.** Own `selection/{aggregate,gates,tier,basket_card,basket_store,
   engine_api}.py`, `scan/seeds.py`, and selection tests. Make `aggregate.py` a thin compatibility entry;
   route all seeds through inventory/triage/queue; use deep search only for queues; consume deep reason once;
   preserve six-gate behavior; make Tier/card pure downstream mechanics.
4. **Publish/report/API — Backend integration.** Own `report/{evening,basket_daily,store}.py`,
   `api/{app,schemas,stores}.py`, handoff readers, and API/report tests. Stage/publish atomically; overlay
   live `processing` on the latest completed snapshot without changing its frozen content; make run audit
   authenticated/non-default; keep old response shapes when additions are absent.
5. **Client compatibility — App.** Own `Networking/Models/BasketModels.swift`,
   `Views/BasketDailyView.swift`, and only needed `BasketCardView.swift` compatibility. Hand-decode optional
   additions; show one concise processing/partial/unavailable status notice and none for complete. Keep
   tokens, run IDs, batches, prompts, merge data, and potential engines off default pages.
6. **SOP — Backend operations.** Own `scripts/daily_update.py`, `report/evening.py`,
   `deploy/neckline-{daily,scan,basket,report}.service`, timers, README operator text, and verified one-offs.
   Keep `daily → scan → basket → review/report` and three evening services. `evening.py --segments` is the
   recovery entry; redirect/remove unused daily backdoors; archive a one-off only after no unit/import uses it.
   Measure batch/fill peak wall time and memory before proposing a unit-limit change.

## 6. Required verification matrix

- **Unit/config:** >20 seeds all receive a brief and terminal route; zero inventory LLM calls; exact
  merge/no-policy behavior; triage search/JSON/missing clamps; malformed retry; deterministic coverage/fill;
  initial 20; every missing-config path; actual/missing usage; wall/token exhaustion; independent review ledger.
- **Selection regression:** deep research only for queues; no `TASK_TIER_RANK`/`TASK_SCRIPT`; full reason
  survives whitelist/clamps/gates; every six-gate semantic test stays green; deterministic Tier/capacity;
  complete cards are not falsely degraded.
- **Data/publication:** fresh/legacy migration, repeated `init_schema`, same-day run-ID separation,
  append-only events/calls, pre-publish failure preserving prior report, transaction rollback leaving no
  partial facts, unchanged legacy-card/handoff decode, and temporary-DB/read-only-snapshot discipline.
- **API/client:** V2.4.2 server with V2.4.1 client; old frozen payload with new client; all four states;
  processing shows prior complete snapshot; partial shows valid baskets plus one notice; unavailable is not
  presented as no opportunity; no Tier LLM badge; optional generation source is safely ignored.
- Run `cd Backend && .venv/bin/python -m pytest -q`, authenticated temporary-DB API smoke, then
  `cd App && xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build`.
  Capture macOS screenshots for all four states and old/new-card compatibility; capture equivalent iOS
  simulator states if the existing test target supports them.

## 7. Review, repair, RC, and rollback

1. **Released (2026-08-14).** The fourth independent review approved the repaired
   V2.4.2 selection pipeline (P0/P1/P2=0). Local RC metadata is `v2.4.2` Build 3; migration/recovery,
   temporary-DB backend/API, and macOS/iOS build gates have passed. Same-day published generations isolate
   basket, member, Tier, card, gate, OUT, and dropped-handoff facts; failed replacements retain the prior
   published snapshot. Selection/API/report readers no longer initiate schema migration: un-migrated stores
   are read-probed as legacy/empty, while schema writes are restricted to explicit startup/write/RC boundaries.
   The prior local-db incident was schema-only with no business rows; it was not restored or modified.
2. A separate reviewer inspects diff, migrations, task-call traces, temporary-DB publish rollback, API
   payloads, and screenshots against sections 2–6. Record only actionable severity-ranked findings in
   `archive/review报告/`; it is evidence, not a competing plan.
3. Builder repairs every confirmed blocking/high finding, reruns affected tests, then the backend suite,
   macOS build, temporary-DB smoke, and screenshots.
4. RC gate rehearses migration and rollback: pre-upgrade SQLite backup, source/release artifact anchor,
   `integrity_check`, unit syntax, explicit production-target confirmation, and known-good `v2.4.1` source.
   Production was backed up, explicitly migrated, deployed and verified at
   `deploy@114.66.0.38:/opt/neckline`; public health reports `v2.4.2`, the API has zero restarts, and both
   timers are active. Rollback artifacts are under `/opt/neckline-release-backups/v2.4.2-pre-20260814-132448/`
   plus the two `neckline.db.*-v242-20260814-132448` backups. macOS V2.4.1 is preserved under
   `/Users/linotsai/Lino/app_backups/`; no iOS device was touched.

## 8. Milestone index and backlog

- **Now:** production V2.4.2 uses approved balanced package `v2.4.2-balanced-r1`; the basket service is pinned to
  that versioned file. Build 5 interleaves configured two-direction search/reason cohorts so a later wall-budget
  stop preserves completed deep work, and makes macOS show honest partial/unavailable empty states. Its gates are
  Backend 4009 passed/19 skipped, macOS 231 passed/10 existing skips, iOS Debug build and signed macOS archive.
- **Next:** run the balanced package for the first
  3–5 trading days, review actual `selection_llm_calls` Token totals, direction counts, fill rounds and stop
  reasons before proposing `r2`; do not tune from one day or replace the file in place.
- **Later:** V2.4.3 product-wide consistency review after the V2.4.2 observation window.

## 9. V2.4.3 observation inbox

- During the first 3–5 V2.4.2 trading days, classify each user report before changing code.
- **Hotfix now:** crash, blocked core action, materially misleading output, missing/stale production result,
  data-integrity risk, or a defect that prevents normal selection/holding/review use. Reproduce narrowly,
  make the smallest safe fix on `main`, run the affected gate, and verify the deployed result.
- **Record for V2.4.3:** wording, spacing, visual hierarchy, optional convenience, or other friction that does
  not prevent a correct decision. Record the page, state, evidence/screenshot, user impact and desired outcome;
  do not accumulate piecemeal UI patches during the observation window.
- A six-gate/threshold/Tier change, new or changed LLM call, pipeline budget/config change, schema rewrite,
  or production-data repair is never an informal hotfix. It requires an explicit decision and rollback plan.
- **Hotfix 2026-08-14:** V2.4.2 Build 4 changes the primary icon asset-set name from `AppIcon` to
  `AppIconV242`, forcing iOS notification chrome to stop reusing the pre-upgrade icon cache. The artwork and
  notification logic are unchanged; iOS is packaged for user installation and macOS Build 3 stays installed.
- **Hotfix 2026-08-14:** the first production balanced run read all 190 directions and spent 125,315 measured
  Tokens, but exhausted its wall budget after 13 searches before starting any deep reasoning, publishing a
  misleading zero-basket partial result. Build 5 processes configured deep cohorts end-to-end and exposes the
  server's selection-state notice in the macOS workbench; no budget, threshold, gate or LLM-call type changes.
- Build numbers are monotonic installable-build identifiers, not reserved in advance. This client hotfix
  consumes Build 5, so V2.4.3 is expected to start at Build 6.
