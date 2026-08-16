# Neckline project plan

> Updated: 2026-08-16. This is the only current control plane. Historical work is under `archive/`.

## 1. Current goal and release boundary

V2.4.2 Build 7 is released and is now in its 3–5 trading-day observation window. The 2026-08-14 report was
explicitly backfilled on 2026-08-16 after the bounded pipeline fix. Weekday scheduling now runs Monday–Thursday
after close and defers Friday's report to Sunday evening so weekend information can enter research. Do not rerun
a production report without a new explicit user instruction. Continue directly on `main`; do not create a branch.

Build 7 is released; backend hotfix commit `09bd991` is on `main` with `4046 passed / 19 registered skips`,
focused contract/publication regressions, Python compile, and `git diff --check` green. Production now runs
`v2.4.2-balanced-r3`: the selection wall-clock cutoff is removed, undated Tavily evidence is preserved honestly,
concept codes are resolved before search, and real qualification is checked after every deep cohort. The hotfix
was deployed without starting or regenerating a report. The signed macOS `2.4.2 (7)` app remains installed and
the iOS IPA remains user-installable.

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
  reserves, or the explicit Token/direction/fill limits; elapsed wall time is measured but is not a stop.
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
- The user approved the repaired production package `Backend/config/direction-pipeline.v2.4.2-balanced.json`
  (`v2.4.2-balanced-r3`) on 2026-08-16: all directions visible, mechanical shortlist `48`, initial/max deep
  `20/30`, triage/deep/fill batches `8/2/5`,
  qualified target `7`, industry/seed-kind/potential-CZY coverage `6/4/2`, Token/wall stops
  `350000/none`, at most `2` fill rounds, normal before reserve, and identity-only merge. Qualification is
  re-evaluated after each completed two-direction cohort so sufficient candidates stop later expensive work.
- Outside that approved versioned package, do not invent production defaults or silently alter its
  batch, coverage, queue, merge, Token, or fill values. `triage_concurrency=1` records the
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
   `/Users/linotsai/Lino/app_backups/`; no iOS device was touched. Build 5 additionally has source/file/online-DB
   backups under `/opt/neckline-release-backups/v2.4.2-b5-hotfix-pre-20260814-172520/`, and the replaced macOS
   Build 3 is under `/Users/linotsai/Lino/app_backups/v2.4.2-b3-pre-b5-20260814-175936/`.
5. **Build 6 released (2026-08-14).** Commit `4fbf60d` is on `main`; production source/schema were backed up
   under `/opt/neckline-release-backups/v2.4.2-b6-tavily-pre-20260814-191829/` before migration and restart.
   `neckline.service` is active with zero restarts and public health at `https://nk.linotsai.top` reports
   `v2.4.2`. The signed macOS `2.4.2 (6)` app is installed, with Build 5 preserved under
   `/Users/linotsai/Lino/app_backups/v2.4.2-b5-pre-b6-20260814-192100/`. The user-installable iOS artifact is
   `/tmp/neckline-v242-b6-rc.T9P3Fv/iOS-export/Neckline.ipa` (SHA-256
   `c2a1300e6fd86ac5416d8b70f5cb72562ef4ffc00da70fa22829cd009fce878e`); no iOS device was touched.
6. **Build 7 released (2026-08-16).** Commit `74bef27` is on `main`. Before sync, production source was
   archived under `/opt/neckline-release-backups/v2.4.2-b7-pipeline-pre-20260816-160112/` and two consistent
   SQLite backups were written as `neckline.db.bak{,2}-v242-b7-20260816-160112`; both share SHA-256
   `e2f08273d17cdda94c15ab5f2029055b4b2d4dfcb193efafdc1d7193dee8607c` and pass `integrity_check`.
   Production source hashes match the commit, the API is active with `NRestarts=0`, public health is 200,
   the database/report/latest-selection anchors are unchanged, and the existing daily/evening timers remain
   scheduled for the next trading day. The signed macOS `2.4.2 (7)` app is installed with Build 6 preserved
   under `/Users/linotsai/Lino/app_backups/v2.4.2-b6-pre-b7-20260816-160512/`. Persistent Release artifacts are
   under `/Users/linotsai/Lino/app_builds/Neckline-v2.4.2-b7-20260816-160112/`; the iOS IPA SHA-256 is
   `7f0b6c816b7dd225f9e5a83d6abd3229c2f997b65e56271190d2208df5617748`. No iOS device or report task was
   touched.
7. **Friday-to-Sunday schedule and authorized backfill (2026-08-16).** Commits `cf2faf6` and `c223221` are on
   `main`. The evening timer runs Mon–Thu at 16:35 and Sunday at 19:00 Asia/Shanghai; Sunday targets the preceding
   Friday, non-trading targets exit cleanly, and a same-local-day report guard prevents duplicate LLM/APNs work.
   Pre-schedule source/unit backups are under
   `/opt/neckline-release-backups/v2.4.2-sunday-schedule-pre-20260816-161839/`. Before the explicitly authorized
   20260814 rerun, two consistent SQLite backups were written as
   `/opt/neckline/data/neckline.db.bak{,2}-rerun-20260814-20260816-162149`; both pass `integrity_check` and share
   SHA-256 `e2f08273d17cdda94c15ab5f2029055b4b2d4dfcb193efafdc1d7193dee8607c`.
8. **Balanced r3 hotfix deployed without rerun (2026-08-16).** Commit `09bd991` is on `main`; the replaced
   selection/config/unit files are archived under
   `/opt/neckline-release-backups/v2.4.2-r3-no-wall-pre-20260816-175222/`. Deployed hashes match the commit,
   `neckline-basket.service` reports `TimeoutStartUSec=infinity`, the package validates as
   `v2.4.2-balanced-r3`, public health remains `v2.4.2`, and the API remains active with `NRestarts=0`.
   The production database retained the same size, mtime, and `neckline:neckline` owner across deployment;
   basket/scan/report/target units stayed inactive. No report, LLM task, APNs notification, DB migration,
   application build, or device installation ran.

## 8. Milestone index and backlog

- **Released:** Build 6 keeps the approved balanced package and six-gate/Tier rules unchanged, removes native-provider
  search routing from the V2.4.2 direction deep-research path, and gives that path one Tavily-only adapter with
  separate credit accounting. Deep research reuses
  the search result for the reasoning call instead of paying for a redundant search LLM call. Provider routing
  is explicit-default-first; only enabled/keyed Providers are eligible, and deleting, disabling or clearing a
  Provider atomically removes its references. The one-off `--observe-selection-cost` mode additionally disables
  the Token cutoff while preserving measured usage, per-call timeout/retry, candidate sufficiency and
  pool-exhaustion stops; scheduled services never enable it.
- **Next:** collect 3–5 trading days of the deployed `r3` package and review actual
  `selection_llm_calls` Token totals,
  shortlist/deep counts, fill rounds and stop reasons before any further tuning; never initiate a report rerun
  without explicit user authorization. Route the remaining C4 post-selection news scan through Tavily before
  treating its current-news coverage as equivalent to direction deep research.
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
  server's selection-state notice in the macOS workbench. Its first live repair run proved the new ordering with
  two deep-reason batches, then exposed provider HTTP handling that did not distinguish rate limits from account
  errors; Build 5 retries only 1302/1305 and stops immediately on balance error 1113. No budget, threshold, gate
  or LLM-call type changes. Report-ready APNs now says selection is partial/unavailable/processing instead of
  claiming it is ready. Today's rerun remains unavailable until the upstream GLM balance is restored.
- **Hotfix 2026-08-14:** V2.4.2 Build 6 adds the Tavily-only search boundary, separate search-credit audit,
  DeepSeek-capable default routing, macOS Tavily credential entry, and the explicit one-off cost-observation
  switch. It does not change the balanced package, six gates, Tier capacity, or scheduled budget enforcement.
- **Build 6 production observation (2026-08-14):** the one explicitly authorized unrestricted run
  `3344faad-7b2e-44fc-995d-994c3906d44c` read all 190 directions and triaged them as 35 deep / 55 normal /
  66 reserve / 34 unfit. It deep-queued all 156 eligible directions across fill rounds 0–34 and stopped with
  `no_gated_baskets`, not a Token or wall-time cutoff. Selection used 102 measured LLM calls and 1,221,048
  Tokens: triage 24 calls / 154,015; deep reasoning 78 calls / 1,067,033. Selection wall time was 2h43m52s;
  the complete basket/review/report unit took 2h46m33s, exited 0, wrote the 20260814 report at 22:10:50 CST,
  and sent one report APNs (`sent=1`, `failed=0`). Tavily made 156 calls, charged 109 Basic credits, and returned
  523 results; 155 calls were OK and one one-character query (`铜`) returned HTTP 400 without charge.
- **High-priority follow-up from that observation:** all 155 reasoned directions were rejected by the existing
  mechanical gate and the remaining direction had unavailable research, so the published generation correctly
  contains zero baskets/cards. Production warnings show DeepSeek frequently substituted concept codes/names or
  member symbols for the exact mechanical `seed_keys`, and sometimes omitted member position/core judgments.
  This is an output-contract integration defect, not evidence that 1.22M Tokens are an appropriate steady-state
  budget. Also, three post-selection review/report calls still passed `enable_search=true` directly to DeepSeek
  and received zero native search hits; their Token usage is not in `selection_llm_calls`. Route every genuinely
  networked post-selection task through the same Tavily boundary and extend usage audit before the next cost
  conclusion. Do not rerun, loosen gates, or change the balanced package without a new explicit user decision.
- **Released Build 7 hotfix (2026-08-16):** `v2.4.2-balanced-r2` keeps all directions visible but sends only
  48 mechanically ordered/coverage-preserving directions to cheap triage, then researches 20 initially and at
  most 30 after two five-direction fill rounds. Observation mode disables only Token/wall stops; it can no
  longer remove direction/fill caps. Deep decisions are explicitly `candidate|not_candidate|uncertain`; the
  server binds the exact seed key and presented-member whitelist, requires the existing market/sector/member
  check shapes, and treats contract/provider failures as unavailable rather than gate rejects. A partial run
  with zero publishable baskets preserves the previous valid snapshot. Tavily queries now always retain the
  direction label and add fixed A-share research context, so one-character labels are not rejected. This changes
  no six-gate/Tier threshold; the release deployment did not trigger a production rerun.
- **Build 7 bounded production observation (2026-08-16, report date 20260814):** the explicitly authorized
  rerun read all 190 directions, mechanically shortlisted 48, triaged them as 11 deep / 21 normal / 13 reserve /
  3 unfit, queued 20, and completed research/reasoning for 12 before the configured 1500-second selection wall
  stop. It published a valid `partial` generation with one T2 `芯片概念` basket: 中瓷电子、华正新材、天洋新材.
  Selection used 12 measured LLM calls / 216,804 Tokens (triage 30,506; deep reason 186,298) and 12 Tavily calls /
  11 Basic credits. The full chain ran 29m23s, atomically wrote the report at 16:52:17 CST, passed database/API
  verification, and sent one report APNs (`sent=1`, `failed=0`). The scheduled Sunday guard then proved it would
  skip the same report at 19:00. Direction research used current Tavily results, but C4 still requested DeepSeek
  native search and received zero hits; fixing C4 requires only the post-selection report path, not another full
  selection rerun.
- **Deployed r3 selection correction (2026-08-16):** remove the aggregate wall-clock stop and the systemd
  90-minute basket timeout; preserve sourced Tavily results whose publisher omitted a date as T2-eligible
  evidence; resolve mechanical concept codes to readable concept names before search; and re-run the unchanged
  mechanical/six-gate/Tier qualification after every two completed directions, stopping later deep work once
  seven publishable candidates exist. The 350,000 Token stop and the explicit 48/20/30 direction/fill bounds
  remain. Deployment did not run the pipeline or alter the existing report snapshot.
- Build numbers are monotonic installable-build identifiers, not reserved in advance. This backend hotfix
  consumes Build 7, so V2.4.3 is expected to start at Build 8.
