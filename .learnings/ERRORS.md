# Project errors

## [ERR-20260824-004] zsh verification variable

**Logged**: 2026-08-24T21:12:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
A final source scan tried to assign to zsh's read-only `status` variable.

### Error
`zsh: read-only variable: status`

### Context
- Read-only K9-v2 residual scan; no file, database, or external state was changed.

### Suggested Fix
Use a task-specific name such as `search_rc` for captured exit codes.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-24T21:12:00+08:00
- **Notes**: The scan was rerun with `search_rc` and completed cleanly.

---

## [ERR-20260824-013] release inspection used root-relative paths from Backend

**Logged**: 2026-08-24T23:12:00+08:00
**Priority**: low
**Status**: resolved
**Area**: release

### Summary
A release metadata inspection ran from `Backend/` but used repository-root-prefixed paths, so the files were not found.

### Error
`No such file or directory`

### Context
- Read-only inspection only; no files changed.

### Suggested Fix
Use `../App`, `tests/...`, and `config/...` from the Backend working directory, or run from repository root.

### Metadata
- Reproducible: yes
- Related Files: App/project.yml, Backend/tests/test_v250_s14_release_gate.py

### Resolution
- **Resolved**: 2026-08-24T23:15:00+08:00
- **Notes**: The same cwd mistake recurred once on the asset-directory rename. The rename was rerun relative to `App/`; future release commands use paths relative to their declared working directory.

---

## [ERR-20260824-012] full-suite K9-v2 release-contract drift

**Logged**: 2026-08-24T23:09:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
The first full suite after formal parameter integration found four stale construction-phase expectations: one morning fixture assumed two selected codes, two release gates still expected no production package/old example schema, and one enum guard still expected the old nine-value D1 reference set.

### Error
`4 failed, 1127 passed`

### Context
- Focused K9-v2, scorecard, and migration tests had passed; production was not mutated.

### Suggested Fix
Make the morning invariant fixture independent of shortlist cardinality, update release gates to the approved package hash/schema, and add the formal 10:00 reference to the closed enum expectation.

### Metadata
- Reproducible: yes
- Related Files: Backend/tests/test_auction_checklist.py, Backend/tests/test_v250_s14_release_gate.py, Backend/tests/test_v250_s8_auction_guard.py

### Resolution
- **Resolved**: 2026-08-24T23:17:00+08:00
- **Notes**: Updated all four stale expectations, moved the release to Build 16 with a fresh app-icon asset identity, and passed the 12 focused release/auction guard tests.

---

## [ERR-20260824-011] incomplete production-copy rehearsal database

**Logged**: 2026-08-24T23:02:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: release

### Summary
The first production-copy transfer exceeded the command yield and left a partial local SQLite file; the migration correctly refused it as malformed.

### Error
`[k9-v2-migration] REFUSED: database disk image is malformed`

### Context
- The source was an online SQLite backup in remote `/tmp`; production business state was not changed.
- The partial local file was 59 MiB while the remote snapshot was about 103 MiB.

### Suggested Fix
Run the large transfer as a resumable/polled session, verify source/destination SHA-256 and SQLite integrity before rehearsal.

### Metadata
- Reproducible: yes
- Related Files: Backend/scripts/migrate_k9_v2.py

### Resolution
- **Resolved**: 2026-08-24T23:05:00+08:00
- **Notes**: Repeated the snapshot transfer as a polled session, matched remote/local SHA-256 `e5da985c…7894`, and verified `PRAGMA integrity_check=ok` before retrying the rehearsal.

---

## [ERR-20260824-010] production inventory unquoted table identifier

**Logged**: 2026-08-24T22:52:00+08:00
**Priority**: low
**Status**: resolved
**Area**: release

### Summary
The read-only inventory interpolated every SQLite table name without identifier quoting; a legacy table named `table` caused a syntax error.

### Error
`sqlite3.OperationalError: near "table": syntax error`

### Context
- The database was opened with `mode=ro`; integrity had already returned `ok` and no production state changed.

### Suggested Fix
Restrict the inventory to the explicit K9/related table allowlist and quote identifiers defensively.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/db.py

### Resolution
- **Resolved**: 2026-08-24T22:55:00+08:00
- **Notes**: Restricted the follow-up to an explicit table allowlist and quoted identifiers. The retry completed in read-only mode and confirmed all related production counts.

---

## [ERR-20260824-001] exec_command

**Logged**: 2026-08-24T10:25:00+08:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
Two verification commands used repository-root paths while already running from `App/` or `Backend/`.

### Error
The relative paths resolved below the selected working directory and the checks exited before doing work.

### Context
- The first icon rename check prefixed `App/` while its working directory was already `App/`.
- The first release guard check prefixed `App/` and `Backend/` while its working directory was already `Backend/`.

### Suggested Fix
Resolve paths against the declared working directory before running a multi-step gate, and inspect the exit code when a command produces no output.

### Metadata
- Reproducible: yes
- Related Files: App/project.yml, Backend/tests/test_v250_s14_release_gate.py

### Resolution
- **Resolved**: 2026-08-24T10:26:00+08:00
- **Notes**: Both commands were rerun with working-directory-relative paths and passed.

---

## [ERR-20260824-003] K9-v2 targeted regression

**Logged**: 2026-08-24T21:02:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
The first P1/P3 identity regression made the synthetic P1 launch disappear because the relaxed P3 fixture defined one hot day as a persistent-hot identity.

### Error
The K9-v2 channel test lost its P1 candidate, which also reduced the explain-layer reserve list below the two rows required by the multi-round backfill test.

### Context
- Targeted K9-v2, playbook, scorecard, and report regression run.
- Production data and configuration were not touched.

### Suggested Fix
Keep the production identity rule strict: any genuinely persistent-hot identity belongs to P3. Synthetic fixtures that intend to model a first launch must require at least two hot days before calling the stock persistent.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/k9/channels/p1_breakout.py, Backend/tests/k9_env.py

### Resolution
- **Resolved**: 2026-08-24T21:03:00+08:00
- **Notes**: The relaxed P3 fixture now requires two hot days; the focused 13-test regression passed without weakening P1/P3 disjointness.

---

## [ERR-20260824-002] Build 15 targeted verification

**Logged**: 2026-08-24T10:47:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
The first Build 15 checks exposed a wrong working-directory prefix, a nonexistent color token, and a platform-branch placement error.

### Error
The source search resolved `App/Neckline` below an `App/` working directory, then the first compilation rejected `NK.good` because the established positive color token is `NK.up`. The first full iOS build then proved the macOS-only scoreboard reveal call had accidentally been patched into the iOS branch.

### Context
- Targeted macOS AppModel test during the 10:00 visible-state correction.
- No production app, server, report, or data was touched.

### Suggested Fix
Resolve paths against the declared working directory and reuse the existing design-token vocabulary before compiling.

### Metadata
- Reproducible: yes
- Related Files: App/Neckline/Views/CheckListView.swift, App/Neckline/Components/DesignTokens.swift

### Resolution
- **Resolved**: 2026-08-24T10:48:00+08:00
- **Notes**: Corrected the path, replaced `NK.good` with `NK.up`, and moved the scoreboard reveal call from the iOS branch to the macOS branch; the affected gates were rerun from the start.

---

## [ERR-20260824-005] remote SQLite inventory quoting

**Logged**: 2026-08-24T22:02:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
A read-only production inventory embedded a Python heredoc inside a doubly quoted SSH command, allowing local zsh glob parsing to intercept SQL punctuation before SSH ran.

### Error
`zsh:1: no matches found: name=?,(name,)).fetchone()`

### Context
- The intended operation was a read-only schema and recent-run inventory against the production SQLite database.
- The failure occurred locally before the remote command executed; no server state was touched.

### Suggested Fix
Send non-trivial remote Python through SSH standard input (`ssh host python -`) rather than nesting a heredoc inside shell quotes.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-24T22:03:00+08:00
- **Notes**: Subsequent remote database inventories use standard-input script transport and avoid local shell interpretation.

---

## [ERR-20260824-006] production inventory schema assumption

**Logged**: 2026-08-24T22:07:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
The corrected read-only production inventory ordered `k9_reports` by `created_at`, but the deployed V2.5.2 table names that timestamp `generated_at`.

### Error
`sqlite3.OperationalError: no such column: created_at`

### Context
- The query had already printed schemas, counts, and the two target K9-v1 runs before reaching the bad report ordering clause.
- The database was opened with SQLite URI `mode=ro`; no production state changed.

### Suggested Fix
Inspect deployed table columns before composing diagnostic ordering clauses, or order reports by the confirmed `generated_at` field.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/db.py

### Resolution
- **Resolved**: 2026-08-24T22:08:00+08:00
- **Notes**: The follow-up inventory uses deployed schema metadata and `generated_at`.

---

## [ERR-20260824-007] apply_patch same-path replacement

**Logged**: 2026-08-24T22:26:00+08:00
**Priority**: low
**Status**: resolved
**Area**: backend

### Summary
The patch tool rejected a single patch that tried to delete and add `params.py` at the same path.

### Error
`apply_patch verification failed: invalid patch: multiple operations target .../params.py`

### Context
- The intended change was a full-file rewrite of the K9-v2 parameter contract.
- Patch verification failed before any file content changed.

### Suggested Fix
Perform the deletion and addition as two separate `apply_patch` operations, or use one update hunk.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/k9/params.py

### Resolution
- **Resolved**: 2026-08-24T22:27:00+08:00
- **Notes**: The full-file replacement was split into separate atomic patch operations.

---

## [ERR-20260824-008] P2 patch context typo

**Logged**: 2026-08-24T22:34:00+08:00
**Priority**: low
**Status**: resolved
**Area**: backend

### Summary
A P2 contract patch contained an accidental escape marker inside the expected context line, so the patch did not match.

### Error
`apply_patch verification failed: Failed to find expected lines ... p2_rebound.py`

### Context
- Patch verification failed before modifying the P2 implementation.

### Suggested Fix
Use smaller exact hunks copied from the current file and avoid combining replacement markers with context text.

### Metadata
- Reproducible: no
- Related Files: Backend/neckline/k9/channels/p2_rebound.py

### Resolution
- **Resolved**: 2026-08-24T22:35:00+08:00
- **Notes**: The P2 changes were reapplied as small exact hunks.

---

## [ERR-20260824-009] approved-parameter targeted regression

**Logged**: 2026-08-24T22:38:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
The first regression after wiring the approved package found one stale P2 field reference and scorecard fixtures still using the superseded D1 open-price contract.

### Error
`AttributeError: 'P2Tier' object has no attribute 'min_drawdown_pct'`

### Context
- Focused K9-v2, scorecard, explanation, and report tests.
- Most failures were cascading from the P2 exception; production was not touched.

### Suggested Fix
Use `min_drawdown_from_window_high_pct` consistently and update scorecard fixtures/readings to freeze `last_valid_trade_at_10_00`.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/k9/channels/p2_rebound.py, Backend/tests/test_scorecard_listing.py

### Resolution
- **Resolved**: 2026-08-24T22:48:00+08:00
- **Notes**: Updated the stale P2 field, moved the D1 fixture to the frozen 10:00 reference, and made the P3 synthetic market satisfy both formal daily-heat components. Focused K9-v2 and scorecard regression now reports 12 passed.

---

## [ERR-20260824-014] prediction provenance release gate

**Logged**: 2026-08-24T23:52:00+08:00
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
The closed-production release audit found that K9-v2 prediction rows froze the strategy and label versions but omitted the parameter-package and fact-pack identities required by the settled plan.

### Error
`sqlite3.OperationalError: no such column: params_package_version`

### Context
- The API and all timers were still stopped; the incomplete Day 1 state was never exposed.
- `k9_runs` and `k9_reports` had the correct `K9-v2 / k9-params-20260824-v2-r1 / fp-3` identity, but `k9_predictions` could not prove the same lineage row by row.
- The first migration test checked empty table names/counts but not the prediction provenance columns.

### Suggested Fix
Store `params_package_version`, `pack_id`, and `pack_version` on every prediction cohort row; make the production cutover verify those columns; add schema, open-day, settlement, and migration regressions; restore the closed production state before replaying the fixed cutover.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/db.py, Backend/neckline/scorecard/listing.py, Backend/scripts/migrate_k9_v2.py, Backend/tests/test_scorecard_listing.py, Backend/tests/test_schema_current.py, Backend/tests/test_k9_v2_migration.py

### Resolution
- **Resolved**: 2026-08-25T00:35:00+08:00
- **Notes**: Prediction rows now freeze parameter-package, pack ID, pack version, strategy version, and label-contract provenance. Migration and schema release guards cover every field; the closed first cutover was fully restored before the corrected run. Released in `fd75bf5` / `v2.6.0-b16-r2`.

---

## [ERR-20260824-015] combined patch stale test context

**Logged**: 2026-08-24T23:53:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
A combined provenance patch used the wrong migration-test module alias in its expected context and was rejected atomically.

### Error
`apply_patch verification failed: Failed to find expected lines in Backend/tests/test_k9_v2_migration.py: result = migration.apply(`

### Context
- The test imports the script as `MIGRATION`, not `migration`.
- Patch verification failed before any file changed.

### Suggested Fix
Inspect the narrow target range first and apply the implementation and test changes in separate exact hunks.

### Metadata
- Reproducible: yes
- Related Files: Backend/tests/test_k9_v2_migration.py

### Resolution
- **Resolved**: 2026-08-24T23:54:00+08:00
- **Notes**: Reapplied as two exact patches; focused provenance and migration tests pass (8 passed).

---

## [ERR-20260825-001] pending Day 1 counted as settled score day

**Logged**: 2026-08-25T00:52:00+08:00
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
The closed-production API audit found that `load_scorecard()` selected every prediction date, so a fully pending Day 1 appeared as one settled batch even though every metric denominator was zero.

### Error
`settledDays=1` while all 20 final-cohort rows had `path_state='pending'` and `evaluable=0`.

### Context
- The API and timers were still stopped, so the misleading display was never exposed.
- The active observation queue was correctly 20 and all score numerators/denominators were empty.
- Existing tests checked the post-D2 settled state but not the Day 1 pre-settlement display contract.

### Suggested Fix
Select scorecard dates only from final-cohort rows whose `path_state` is no longer `pending`; keep `activeQueueCount` as the separate observation-queue measure; regress `settledDays=0`, `latestD0Date=null`, and empty score rows before D2.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/scorecard/listing.py, Backend/tests/test_scorecard_listing.py, App/Neckline/Views/ScoreboardView.swift

### Resolution
- **Resolved**: 2026-08-25T00:54:00+08:00
- **Notes**: Scorecard dates now come only from non-pending final-cohort rows. Day 1 exposes `activeQueueCount=20` separately while `settledDays=0`, `latestD0Date=null`, and score rows remain empty. Released in `676d20e` / `v2.6.0-b16-r3`.

---

## [ERR-20260825-002] shell expression embedded in JavaScript template

**Logged**: 2026-08-25T00:10:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tooling

### Summary
A remote release command containing the shell expression `${API_TOKEN:-}` was embedded directly in a JavaScript template string and failed to parse before any remote command ran.

### Error
`SyntaxError: Missing } in template expression`

### Context
- No production action occurred because JavaScript parsing failed first.
- Shell parameter syntax and JavaScript interpolation syntax collided.

### Suggested Fix
Use a single-quoted JavaScript string or escape the dollar sign when the command must contain shell parameter expansion.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-25T00:12:00+08:00
- **Notes**: Reissued the remote command with quoting that kept shell expansion entirely on the remote side.

---

## [ERR-20260825-003] fixed health file collided with existing permissions

**Logged**: 2026-08-25T00:40:00+08:00
**Priority**: low
**Status**: resolved
**Area**: release

### Summary
One closed cutover check wrote to a fixed `/tmp/neckline-health.json` path that already existed with incompatible permissions, so validation stopped after the service had started and before timers were enabled.

### Error
`Permission denied: /tmp/neckline-health.json`

### Context
- The API was not publicly exposed as complete and timers remained stopped.
- Production data was intact.

### Suggested Fix
Pipe health output directly into the validator or use a unique `mktemp` path instead of a shared fixed filename.

### Metadata
- Reproducible: environment-dependent
- Related Files: none

### Resolution
- **Resolved**: 2026-08-25T00:43:00+08:00
- **Notes**: Replaced the fixed temporary file with pipe-based validation, reran all release assertions, and only then enabled timers.

---

## [ERR-20260825-004] production verification assumed stale API field names

**Logged**: 2026-08-25T00:58:00+08:00
**Priority**: low
**Status**: resolved
**Area**: release

### Summary
Final public verification initially assumed internal names (`paramsVersion`, `activeQueue`, `factPackSchemaVersion`) instead of the shipped DTO names (`paramsPackageVersion`, `activeQueueCount`, `packVersion`). It also assumed a nonexistent prediction `status` column.

### Error
`KeyError: 'paramsVersion'` and `sqlite3.OperationalError: no such column: status`

### Context
- These were read-only verification failures after a healthy deployment.
- The public endpoint correctly required authentication; the first script also guessed `NECKLINE_API_TOKEN` instead of the configured `API_TOKEN` name.

### Suggested Fix
Inspect response keys and SQLite schema before writing final release assertions; do not infer public DTO names from internal model names.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/api/app.py, Backend/neckline/db.py

### Resolution
- **Resolved**: 2026-08-25T01:00:00+08:00
- **Notes**: Corrected the assertions to the live DTO and schema, then verified K9-v2, the approved parameter package, fp-3, 20 stocks, active queue 20, settled days 0, and listing score denominator 0 through public HTTPS.

---

## [ERR-20260825-005] Developer ID build blocked on legacy Keychain ACL

**Logged**: 2026-08-25T01:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: macOS

### Summary
The Developer ID-signed macOS distribution build launched but its main thread blocked in `SecItemCopyMatching` while reading the token created by the prior Apple Development-signed installation, so the SwiftUI window did not finish initializing.

### Error
`errAETimeout` while the sampled main thread waited in `TokenKeychain.load() -> SecItemCopyMatching`

### Context
- The packaged distribution artifact itself passed strict Developer ID verification.
- The issue affected only continuity with this machine's existing Keychain item after replacing Build 15.

### Suggested Fix
Keep the distribution artifact Developer ID-signed, but sign the installed local copy with the same Apple Development identity as the prior app when preserving the existing Keychain ACL. Treat a signing-identity transition as a mandatory live-launch gate.

### Metadata
- Reproducible: machine/keychain-dependent
- Related Files: App/Neckline/Config/AppConfig.swift

### Resolution
- **Resolved**: 2026-08-25T01:01:00+08:00
- **Notes**: Re-signed only `/Applications/Neckline.app` with the prior Apple Development identity. Keychain access resumed without reconfiguration; the real UI then showed the 20-stock K9-v2 Day 1 list and the empty score baseline. The release-directory macOS package remains Developer ID-signed.

---
