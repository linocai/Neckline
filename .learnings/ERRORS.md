# Project errors

## [ERR-20260831-020] K9-v3 D0 demanded a lifetime trading calendar for a 40-day rule

**Logged**: 2026-08-31T20:30:00+08:00
**Priority**: critical
**Status**: in_progress
**Area**: backend

### Summary
The first production K9-v3 D0 failed because fp-4 tried to compute every stock's exact lifetime trading-day count, even though the approved boundary only asks whether a listing is at least 40 trading days old.

### Error
`交易日历目标库未完整覆盖区间:1990-12-19~2026-08-31`

### Context
- The production calendar already covers 2015-01-01 through 2026-12-31, which is more than sufficient to prove the 40-day threshold for old listings.
- Interpreting the exception as a need to source a 1990–2014 calendar confused an implementation dependency with a strategy requirement.
- The correct first response was to reduce the proof to the minimum sufficient frozen history, not ask for decades of irrelevant data.

### Suggested Fix
Keep `list_date` as the frozen source fact and compare it with the oldest of the approved number of recent fp-4 trading days. Fail closed only when those recent frozen days are genuinely unavailable.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/facts/v4.py, Backend/neckline/k9/v3_run.py
- See Also: LRN-20260831-004

### Resolution
- **Resolved**: pending production verification
- **Notes**: The bounded implementation and regression tests are complete; production replay and report verification remain.

---

## [ERR-20260831-019] Anchored heartbeat rejected in immediate-create mode

**Logged**: 2026-08-31T13:34:22+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Creating a one-time heartbeat with a timezone-qualified `DTSTART` was rejected when the automation used immediate-create mode.

### Error
The automation API reported that immediate creates cannot include `DTSTART` because local wall-clock times may be converted to UTC.

### Context
- The intended follow-up was anchored to 2026-08-31 19:30 Asia/Shanghai.
- No production service, database, queue, or timer was touched.

### Suggested Fix
When a follow-up must actually run, do not treat suggested-create mode as execution. After creating or proposing an automation, verify that a matching automation record exists before promising that follow-up will occur.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T19:48:20+08:00
- **Notes**: The suggested-create retry only rendered a suggestion card and did not create an automation. This was discovered when no matching automation record existed at 19:43. The production verification was then completed manually; future follow-ups must verify persisted automation state.

---

## [ERR-20260831-018] CoreDevice preference-copy assumptions failed during iOS access bootstrap

**Logged**: 2026-08-31T13:29:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Trying to prove the iOS bootstrap through the app Preferences container was unreliable because the expected plist was not exposed at the assumed CoreDevice path.

### Error
CoreDevice copy returned error 7000 for the guessed Preferences plist path, and a copied Preferences directory contained no readable plist.

### Context
- The failures were read-only and did not alter device or production state.
- App installation, signing, and wireless launch were already healthy.

### Suggested Fix
For a one-time credential bootstrap, have the temporary app write a non-secret source marker into its own temporary container, copy that exact file back, then relaunch without environment injection and require the marker to report `keychain`.

### Metadata
- Reproducible: device/OS-dependent
- Related Files: App/Neckline/Networking/AppConfig.swift

### Resolution
- **Resolved**: 2026-08-31T13:28:00+08:00
- **Notes**: The marker reported `keychain` both after the injected launch and after a launch with no injected credential. The official Build 19 was then restored and independently updated its production iOS device registration.

---

## [ERR-20260831-017] Empty selection response does not carry strategyVersion

**Logged**: 2026-08-31T12:18:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
A production API assertion assumed every V3 response included `strategyVersion`, but the empty selection DTO intentionally contains only report-state fields.

### Error
`KeyError: 'strategyVersion'`

### Context
- The authenticated API was healthy and returned the expected empty state.
- The failed assertion was read-only and did not change production.

### Suggested Fix
Inspect current response keys before release assertions and verify strategy identity on the package endpoints that formally carry that field.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/api/app.py
- See Also: ERR-20260825-004, ERR-20260831-013

### Resolution
- **Resolved**: 2026-08-31T12:19:00+08:00
- **Notes**: Verified `selection/latest` as an empty `not_run` report and separately verified both active and settled package DTOs as empty `K9-v3` queues.

---

## [ERR-20260831-016] Computer Use bundle identifier matched archived Neckline copies

**Logged**: 2026-08-31T12:16:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
The macOS live-launch check targeted the bundle identifier, which was ambiguous because release archives and backups retain the same identifier.

### Error
`Ambiguous app identifier 'top.linotsai.neckline'`

### Context
- The formal installed target was `/Applications/Neckline.app`.
- No UI action occurred during the failed lookup.

### Suggested Fix
Target the absolute installed application path for Neckline release UI checks.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T12:16:00+08:00
- **Notes**: Retried with `/Applications/Neckline.app`; the real Build 19 window and K9-v3 empty queue were visible and responsive.

---

## [ERR-20260831-015] Pipefail treated early signature parsing as install failure

**Logged**: 2026-08-31T12:14:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
An `awk` parser exited after the first codesign authority line while `pipefail` was active, causing the successful local install to be classified as failed and rolled back.

### Error
`codesign | awk` returned a non-zero pipeline status after the consumer closed early.

### Context
- The rollback trap restored and relaunched V2.6.0 Build 18 exactly as intended.
- No half-installed application remained.

### Suggested Fix
Capture the complete `codesign` output first and parse it without early pipeline termination.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T12:15:00+08:00
- **Notes**: Re-ran the recoverable replacement with full-output parsing; Build 19 installed, strictly verified, and launched.

---

## [ERR-20260831-014] Release install repeated JavaScript interpolation hazard

**Logged**: 2026-08-31T12:13:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
The macOS replacement command repeated the `${...}` interpolation mistake already seen in the cloud backup command.

### Error
`ReferenceError: old_version is not defined`

### Context
- JavaScript rejected the command before the shell ran.
- The existing application was not quit, moved, or modified.

### Suggested Fix
Avoid braced shell variables inside JavaScript template literals; use unbraced shell variables or ordinary JavaScript strings.

### Metadata
- Reproducible: yes
- Related Files: none
- See Also: ERR-20260831-012

### Resolution
- **Resolved**: 2026-08-31T12:14:00+08:00
- **Notes**: Removed braced shell interpolation from the command before retrying.

---

## [ERR-20260831-013] Release validation guessed nonexistent K9-v3 table names

**Logged**: 2026-08-31T12:11:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
A post-deployment read-only check used hand-written generic table names instead of the names declared by the current database schema.

### Error
`missing: [k9_batch_items, k9_batches, k9_intraday_readings, k9_score_package_items, k9_score_packages]`

### Context
- The production database had already passed `integrity_check` and the service was healthy.
- The failed step was read-only and did not alter service or database state.

### Suggested Fix
Derive release assertions from `neckline/db.py` or another authoritative contract rather than inventing plausible storage names.

### Metadata
- Reproducible: yes
- Related Files: Backend/neckline/db.py

### Resolution
- **Resolved**: 2026-08-31T12:12:00+08:00
- **Notes**: Re-ran the validation against all 13 K9-v3 table names declared in the current schema; every table was present and database integrity remained `ok`.

---

## [ERR-20260831-012] JavaScript template interpolation consumed remote shell variables

**Logged**: 2026-08-31T12:08:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
A remote backup script embedded in a JavaScript template literal exposed a shell `${...}` expression to JavaScript interpolation before the command could run.

### Error
`ReferenceError: release_stamp is not defined`

### Context
- The command was being assembled for the pre-deployment NB Cloud backup.
- Evaluation failed locally before the shell command ran, so production state was unchanged.

### Suggested Fix
Pass multiline shell commands to the execution tool through ordinary JavaScript strings or explicitly escape every `${...}` intended for the remote shell.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T12:09:00+08:00
- **Notes**: Rebuilt the command with a raw JavaScript string so all remote shell variables remain intact.

---

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

## [ERR-20260831-007] NB cloud SSH reset during release-readiness audit

**Logged**: 2026-08-31T11:20:00+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
The connection reset came from using the retired NB address, not from a failure of the current SSH service.

### Error
`kex_exchange_identification: read: Connection reset by peer`

### Context
- Attempted a BatchMode read-only audit against `deploy@114.66.0.38`.
- `https://nk.linotsai.top/api/v1/health` still returned `{"status":"ok","version":"v2.6.0"}`.
- No production write, service action, or database mutation was attempted.

### Suggested Fix
Read `/Users/linotsai/Lino/NB_info.md` before NB operations and verify the recorded host fingerprint before trusting a changed endpoint.

### Metadata
- Reproducible: yes
- Related Files: Backend/scripts/sync_code.sh, Backend/scripts/migrate_k9_v2.py

### Resolution
- **Resolved**: 2026-08-31T11:38:28+08:00
- **Notes**: Verified the documented ED25519 fingerprint for `114.66.2.205`, added the current host key, and authenticated successfully as `deploy` on `ser657204219523`.

---

## [ERR-20260831-008] Nested shell quoting corrupted read-only SQLite audit

**Logged**: 2026-08-31T11:22:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
A Python one-liner wrapped in shell single quotes contained its own single-quoted join delimiter, so the shell removed that text and Python received invalid syntax.

### Error
`SyntaxError: f-string: invalid syntax` at `(,.join(selected))`

### Context
- Read-only local release-readiness inventory; no database write occurred.
- Git checks later in the same shell invocation still ran because the command was not guarded by `set -e`.

### Suggested Fix
Use a quoted here-document for multi-line Python diagnostics and run dependent checks as separate commands.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T11:22:00+08:00
- **Notes**: Replaced the fragile one-liner with a quoted here-document.

---

## [ERR-20260831-009] Local backend env had no reusable production API token

**Logged**: 2026-08-31T11:25:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
An authenticated public-API audit could not run because the local backend `.env` did not contain a usable `API_TOKEN` value.

### Error
`local API_TOKEN unavailable`

### Context
- The script deliberately refused to print or guess credentials.
- Public unauthenticated health remained available and healthy on V2.6.0.

### Suggested Fix
Use the authorized NB host session or the installed App's existing authenticated session for production business-state verification; never expose a token in command output.

### Metadata
- Reproducible: yes
- Related Files: Backend/.env, Backend/neckline/api/deps.py

### Resolution
- **Resolved**: 2026-08-31T11:25:00+08:00
- **Notes**: Stopped the credential path and retained the missing authenticated evidence as a release blocker.

---

## [ERR-20260831-010] Installed Neckline state inspection timed out

**Logged**: 2026-08-31T11:27:00+08:00
**Priority**: medium
**Status**: pending
**Area**: macOS

### Summary
Computer Use could see `/Applications/Neckline.app` running but timed out while retrieving its UI state, so the installed client's authenticated session could not substitute for the unavailable production API/SSH audit.

### Error
`Computer Use server error -10005: timeoutReached`

### Context
- The exact installed app path was used; a bundle-ID retry was ambiguous because several archived/debug copies share the identifier.
- No click, credential action, re-signing, or Keychain change was performed.

### Suggested Fix
During the release window, verify the existing App can launch and read production before replacement; if it blocks, inspect the Keychain signing-identity transition documented in the related prior incident.

### Metadata
- Reproducible: unknown
- Related Files: App/Neckline/Networking/AppConfig.swift
- See Also: ERR-20260825-005

---

## [ERR-20260831-011] Chrome screenshot helper reused a missing Node binding

**Logged**: 2026-08-31T11:35:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
The Chrome Computer Use inspection obtained app state but failed while emitting its screenshot because the filesystem helper binding was not present in the current JavaScript session.

### Error
`fs is not defined`

### Context
- Read-only inspection for an existing cloud-console session.
- No browser click, form submission, or cloud action occurred.

### Suggested Fix
Initialize screenshot helper imports in the same persistent session before using them; do not assume bindings from an earlier Computer Use run survived browser-runtime setup.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T11:35:00+08:00
- **Notes**: Reinitialized the helpers before retrying the read-only state capture.

---

## [ERR-20260831-004] Swift flatMap inferred the wrong closure element type

**Logged**: 2026-08-31T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
A recursive JSON presentation helper used `guard ... else { return [] }` inside `Dictionary.keys.flatMap`, and Swift inferred the closure as returning one field instead of an array.

### Error
`cannot convert value of type '[Any]' to closure result type 'K9ReadableField'`

### Context
- The failure appeared in the required macOS Xcode build after adding readable K9-v3 contract presentation.
- The ambiguous empty-array branch was unnecessary because the key came directly from the same dictionary.

### Suggested Fix
Avoid optional lookup and empty-array returns inside this `flatMap`; force the dictionary value established by the key iteration, or give the closure an explicit `[K9ReadableField]` return type.

### Metadata
- Reproducible: yes
- Related Files: App/Neckline/Networking/Models/SharedModels.swift

### Resolution
- **Resolved**: 2026-08-31T00:00:00+08:00
- **Notes**: Replaced the ambiguous guard branch with a guaranteed dictionary lookup and reran the Xcode build.

---

## [ERR-20260831-005] Node REPL screenshot helper relied on a non-persistent local binding

**Logged**: 2026-08-31T09:52:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
A follow-up Computer Use screenshot reused `fs` from a prior Node REPL snippet, but that binding had not been created in persistent top-level scope.

### Error
`fs is not defined`

### Context
- The UI click completed before image emission failed; no business data or external state changed.
- The visual inspection was immediately resumed from a fresh app-state read.

### Suggested Fix
Create screenshot helper module bindings explicitly at top level in the persistent REPL before reusing them across calls.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-31T09:52:00+08:00
- **Notes**: Imported fresh `fs2` and `url2` bindings and completed both iOS and macOS visual reads.

---

## [ERR-20260831-006] Visual mock server unintentionally activated real integration tests

**Logged**: 2026-08-31T09:57:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
The iOS test suite ran while the visual-only HTTP fixture was listening on the normal dev port, so integration tests treated it as the real backend and exercised endpoints that the narrow fixture did not implement.

### Error
`IntegrationSmokeTests` failed for checklist, review overview, and settings round-trip.

### Context
- All isolated unit tests and all three required builds had passed.
- The failures were environmental cross-talk between two validation phases, not production assertions.

### Suggested Fix
Stop visual fixtures before running integration-aware suites, and restart them only for GUI inspection.

### Metadata
- Reproducible: yes
- Related Files: App/NecklineTests/IntegrationSmokeTests.swift

### Resolution
- **Resolved**: 2026-08-31T09:57:00+08:00
- **Notes**: Stopped the visual server and reran the iOS suite in its intended clean environment.

---

## [ERR-20260830-001] Debug candidate UI inspection timed out during launch

**Logged**: 2026-08-30T00:00:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: macOS

### Summary
Computer Use timed out while launching the freshly built V2.7.0 Build 19 macOS candidate for exact-screen verification.

### Error
`Computer Use server error -10005: timeoutReached`

### Context
- The macOS, iOS, and test builds had already succeeded.
- A previous release showed the same launch symptom when a differently signed candidate blocked on the existing Keychain ACL.
- No production app or data was changed.

### Suggested Fix
Inspect the candidate process, signature, and launch log first; if it is the known Keychain ACL transition, verify a recoverable local copy signed with the established Apple Development identity instead of treating build success as UI success.

### Metadata
- Reproducible: environment-dependent
- Related Files: App/Neckline/Config/AppConfig.swift
- See Also: ERR-20260825-005

### Resolution
- **Resolved**: 2026-08-30T00:00:00+08:00
- **Notes**: The candidate and installed production app shared one bundle identifier and were running simultaneously. After temporarily stopping the installed app, Computer Use addressed the candidate by its full path and completed the visual checks. The candidate was then stopped and `/Applications/Neckline.app` was reopened.

---

## [ERR-20260830-002] Build 19 visual QA found an endless empty-state spinner and literal date expressions

**Logged**: 2026-08-30T00:00:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
The real macOS candidate compiled and passed tests, but the no-package checklist kept a progress indicator forever and three scoreboard date labels rendered source-like expressions instead of interpolated values.

### Error
`checklistLoading` stayed true when the report had no batch ID; `ScoreboardView` used `(NKFmt...)` and `(p.candidateCount)` without Swift interpolation backslashes.

### Context
- Found by launching V2.7.0 Build 19 and navigating the exact selection, checklist, scoreboard, and version screens.
- Static contract tests did not exercise the empty no-batch refresh transition or assert rendered date strings.

### Suggested Fix
Always settle checklist loading when no batch exists, correct all date/count interpolation, and add regression tests for the no-batch state and rendered label helpers.

### Metadata
- Reproducible: yes
- Related Files: App/Neckline/App/AppModel.swift, App/Neckline/Views/ScoreboardView.swift

### Resolution
- **Resolved**: 2026-08-30T00:00:00+08:00
- **Notes**: Builder settled every no-batch/no-client/failure loading path, moved scoreboard labels into tested pure formatters, reran all backend and three App build gates, and the second real macOS launch showed the explicit no-package empty state without a spinner.

---

## [ERR-20260825-006] Build 17 release guards retained Build 16 assumptions

**Logged**: 2026-08-25T09:26:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
The first full backend gate for the Build 17 client patch found the retired Build 16 icon set still present and a release test still freezing build number 16.

### Error
`2 failed, 1130 passed`: multiple `.appiconset` directories and expected build `16` versus actual `17`.

### Context
- The failures occurred locally before commit, signing, installation, or deployment.
- The new icon configuration itself was already consistently generated as `AppIconV260B17`.

### Suggested Fix
Delete the retired Build 16 icon set from the live asset catalog, add it to the explicit old-name guard, and advance the release-version assertion to Build 17.

### Metadata
- Reproducible: yes
- Related Files: App/Neckline/Resources/Assets.xcassets, Backend/tests/test_v250_s12_app_guard.py, Backend/tests/test_v250_s14_release_gate.py

### Resolution
- **Resolved**: 2026-08-25T09:27:00+08:00
- **Notes**: Retired the Build 16 asset set, promoted it into the old-name guard, advanced the release assertion to Build 17, and regenerated the Xcode project. The full backend gate then passed with 1132 tests.

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

## [ERR-20260830-003] Visual-review cleanup command rejected as unsafe

**Logged**: 2026-08-30T23:04:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
A combined visual-review cleanup command was rejected because it contained an `rm -rf` form, even though every target was an explicit task-created `/tmp` directory.

### Error
`Rejected: rm -f style commands are not permitted. Use a safer approach`

### Context
- The command also contained read-only verification steps, so none of the combined operations ran.
- Production and workspace data were not touched; the isolated server had already been stopped.

### Suggested Fix
Keep cleanup separate from verification, avoid force-recursive deletion in tool commands, and remove only exact task-created paths with `find <exact-path> -depth -delete` after validating the targets.

### Metadata
- Reproducible: yes
- Related Files: none

### Resolution
- **Resolved**: 2026-08-30T23:04:00+08:00
- **Notes**: Reran simulator-state and workspace verification separately, then removed the three exact task-created `/tmp` paths with `find -depth -delete`.

---
