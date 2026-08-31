# Project learnings

## [LRN-20260831-005] best_practice

**Logged**: 2026-08-31T20:29:15+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
End-state equality does not validate the boundary semantics of reconstructed temporal history.

### Details
The first SW history export matched the independent current snapshot on its final day but still interpreted `out_date` incorrectly. A 60-day fp-4 dry-run exposed the defect as a one-day spike from 5 to 73 missing memberships on 2026-06-30. Raw intervals then proved that 68 old assignments ended on June 30 and their replacements began July 1, so the exit date is the final included date.

### Suggested Action
Before freezing reconstructed interval data, validate at least one real transition with adjacent dates, compare discontinuity counts, and require a no-overlap contract. Keep the pre-write database backup until the full temporal dry-run passes, not merely until the import succeeds.

### Metadata
- Source: production_validation
- Related Files: Backend/scripts/export_sw_industry_history.py, Backend/tests/test_export_sw_industry_history.py
- Tags: temporal-data, interval-boundary, SW2021, preflight, rollback
- See Also: ERR-20260831-021

### Resolution
- **Resolved**: 2026-08-31T20:29:15+08:00
- **Notes**: The corrected inclusive interval artifact passed an isolated import and a production 60-day fp-4 dry-run before any fact pack was frozen.

---

## [LRN-20260831-004] correction

**Logged**: 2026-08-31T20:07:26+08:00
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
Do not turn a bounded new-listing eligibility check into a requirement for the exchange's full historical calendar.

### Details
K9-v3 only needs to prove that a stock has traded for at least the approved `newListingTradingDays` threshold, currently 40. The fp-4 implementation instead computed an exact lifetime trading-day count for every listed stock. That caused a 1990 listing to require calendar coverage back to 1990 even though the existing 2015–2026 calendar already proves the stock is far older than 40 trading days. Treating the resulting failure as a business-data requirement was incorrect; it is an over-strict implementation boundary.

### Suggested Action
Use the explicit strategy threshold and recent official calendar to prove eligibility. For listings older than the calendar coverage boundary, record or evaluate a conservative lower-bound/eligibility fact rather than demanding an exact lifetime count or silently fabricating one. Preserve fail-closed behavior only when the threshold cannot actually be proved.

Before asking the user to source or mutate data, restate the business question in its smallest sufficient form and challenge any dependency whose scale is wildly disproportionate to that question. A technical exception is evidence about an implementation path, not proof of a business requirement.

### Metadata
- Source: user_feedback
- Related Files: Backend/neckline/facts/v4.py, Backend/neckline/k9/v3_run.py, Backend/config/k9-params.json
- Tags: K9-v3, fp-4, trading-calendar, listing-age, fail-closed

### Resolution
- **Resolved**: 2026-08-31T20:30:36+08:00
- **Notes**: Reclassified the failure as an implementation defect, removed the exact lifetime count, and proved the 40-day rule from recent frozen history. Production then froze 60 fp-4 days and generated the trusted empty 2026-08-31 report.

---

## [LRN-20260831-003] correction

**Logged**: 2026-08-31T19:48:20+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
An automation suggestion card is not proof that a scheduled follow-up was created or executed.

### Details
The 19:30 Neckline verification did not run automatically because the retry used suggested-create mode. The assistant later found no matching persisted automation and had to perform the production read-only audit manually after the user asked for the missing report.

### Suggested Action
For every promised follow-up, verify the persisted automation record and its active schedule. Report a suggestion as a suggestion, never as a scheduled execution.

### Metadata
- Source: user_feedback
- Related Files: none
- Tags: automation, follow-up, verification, reliability
- See Also: ERR-20260831-019

### Resolution
- **Resolved**: 2026-08-31T19:48:20+08:00
- **Notes**: Corrected the record, completed the missed production audit manually, and will distinguish proposed tasks from persisted active tasks.

---

## [LRN-20260831-002] correction

**Logged**: 2026-08-31T12:30:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
Do not equate adding an iOS device to Neckline with registering a new Apple development device.

### Details
The user identified the target as `Caeieo`, which was already paired, already included in the Build 19 provisioning profile, and already had Neckline 2.7.0 (19) installed. The remaining access check is the app's Keychain API token and automatic APNs backend registration, not UDID enrollment or app re-signing.

### Suggested Action
Before proposing signing work, inspect the local device list, compare the target UDID with the embedded provisioning profile, and query the installed app version. Present Apple signing, Neckline API access, and APNs device registration as three separate states.

### Metadata
- Source: user_feedback
- Related Files: App/Neckline/Networking/AppConfig.swift, App/Neckline/Push/PushManager.swift
- Tags: iOS, provisioning, device-access, keychain, APNs

### Resolution
- **Resolved**: 2026-08-31T12:31:00+08:00
- **Notes**: Verified `Caeieo` is included in the current three-device profile and already runs Build 19; then bootstrapped the production credential into iOS Keychain over Wi-Fi, restored the official app, and confirmed its production device registration without changing signing or enrollment.

---

## [LRN-20260824-001] best_practice

**Logged**: 2026-08-24T10:35:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Scheduled backend state changes need an explicit cross-platform client refresh contract.

### Details
The 10:00 settlement was written correctly on the server, but macOS kept the snapshot loaded when the board was first opened. A current-tab manual refresh and an iOS lifecycle refresh made the two clients appear inconsistent. Time-sensitive state cannot depend on tab navigation, first-load caching, or a coincidental app activation.

### Suggested Action
For each scheduled server-side transition, define its authoritative timezone and window, poll only the lightweight read endpoint during that window, stop only on a complete current-day snapshot, and provide an activation catch-up on every supported platform. Never trigger the producer, a report rerun, or a push from this client refresh path.

### Metadata
- Source: user_feedback
- Related Files: App/Neckline/App/AppModel.swift, App/Neckline/App/RootView.swift, App/Neckline/App/NecklineApp.swift
- Tags: macOS, iOS, settlement, refresh, lifecycle

### Resolution
- **Resolved**: 2026-08-24T10:35:00+08:00
- **Notes**: V2.5.2 Build 14 adds the shared 10:00 refresh window, activation catch-up, completion guard, and dual-platform tests/build gates.

---

## [LRN-20260831-001] correction

**Logged**: 2026-08-31T11:39:00+08:00
**Priority**: critical
**Status**: resolved
**Area**: infra

### Summary
Read `/Users/linotsai/Lino/NB_info.md` before every NB cloud operation; the old `114.66.0.38` target is retired.

### Details
The release-readiness audit followed stale repository examples and repeatedly tried SSH against `114.66.0.38`. The authoritative NB record had already documented the provider IP change to `114.66.2.205`, the new ED25519 fingerprint, and successful `deploy` authentication. The public business domain staying unchanged did not imply the host IP stayed unchanged.

### Suggested Action
Treat `NB_info.md` as the first source for NB identity, IP, fingerprint, network topology, and current recovery notes. Verify its recorded fingerprint before adding a new known-host entry, then update any stale project-local examples immediately.

### Metadata
- Source: user_feedback
- Related Files: /Users/linotsai/Lino/NB_info.md, Backend/scripts/sync_code.sh, Backend/scripts/sync_data.sh, App/Neckline/Networking/AppConfig.swift, README.md, PROJECT_PLAN.md
- Tags: NB, SSH, source-authority, deployment

### Resolution
- **Resolved**: 2026-08-31T11:39:00+08:00
- **Notes**: Verified the new host fingerprint, added the exact ED25519 key, logged in as `deploy`, and replaced active Neckline references to the retired IP.

---

## [LRN-20260825-004] correction

**Logged**: 2026-08-25T09:24:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
Do not display a bare `v1` for an append-only playbook revision on a K9-v2 screen.

### Details
The K9-v2 stock-detail page correctly loaded the new Day 1 playbook, but the card rendered only `v1`. That value is the first append-only revision of this stock's playbook, not the K9 strategy version. Because the interface did not name either namespace, the user reasonably read it as a stale K9-v1 version and the release appeared mixed.

### Suggested Action
Display the report's explicit strategy identity (`K9-v2`) beside an explicitly named revision (`预案第 1 版`), use the same wording in version history, and add a contract test that prevents a bare `vN` label. Release QA must follow the exact user path into the stock detail card rather than stopping at the listing and score pages.

### Metadata
- Source: user_feedback
- Related Files: App/Neckline/Views/StockDetailView.swift, App/Neckline/Networking/Models/K9Models.swift, App/NecklineTests/K9ContractTests.swift
- Tags: version-namespace, playbook, K9-v2, macOS, iOS, release-gate
- See Also: LRN-20260824-003
- Promoted: AGENTS.md

### Resolution
- **Resolved**: 2026-08-25T09:36:00+08:00
- **Commit/Tag**: `bad45b6` / `v2.6.0-b18`
- **Notes**: Build 17 fixed the detail card, but exact-path UI verification exposed the same ambiguous label in the checklist. Build 18 now uses one shared revision label across checklist, detail, and history, and the installed UI was verified on all affected paths.

---

## [LRN-20260824-003] correction

**Logged**: 2026-08-24T10:44:00+08:00
**Priority**: critical
**Status**: resolved
**Area**: frontend

### Summary
A synchronized model is not a fixed cross-platform UI until the same user-visible path is verified on both clients.

### Details
Build 14 fetched the 10:00 verdict snapshot on macOS, and model tests plus HTTP 200 responses passed. The macOS scoreboard nevertheless defaulted to its hidden listing-score subsection while iOS rendered all scoreboard cards in one scroll. The user's actual selection/checklist screen also had no visible signal that settlement had completed. The release was therefore technically synchronized but still functionally stale from the Mac user's point of view.

### Suggested Action
For cross-platform refresh fixes, verify the exact screen and interaction the user follows on every platform. A release gate must cover data arrival, navigation/default selection, and visible content; do not infer UI success from endpoint success or hidden model state.

### Metadata
- Source: user_feedback
- Related Files: App/Neckline/Views/ScoreboardView.swift, App/Neckline/Views/CheckListView.swift, App/Neckline/App/AppModel.swift
- Tags: macOS, iOS, refresh, navigation, visible-state
- See Also: LRN-20260824-001, LRN-20260824-002

### Resolution
- **Resolved**: 2026-08-24T10:56:00+08:00
- **Notes**: V2.5.2 Build 15 adds the visible macOS settlement banner and direct verdict navigation, defaults a completed scoreboard to verdicts without stealing an actively selected coverage page, and was verified in the installed `/Applications/Neckline.app` by following the exact user path to the 2026-08-24 counts (11 confirmed, 3 rejected, 6 observed, 0 undecided).

---

## [LRN-20260824-002] correction

**Logged**: 2026-08-24T10:39:00+08:00
**Priority**: high
**Status**: resolved
**Area**: infra

### Summary
Do not claim a macOS package is switched until the running executable and user launch target are verified.

### Details
Copying a new bundle into `/Applications` and reading its `Info.plist` proves only the file on disk. It does not prove that the process the user is looking at came from that bundle, that Dock points there, or that another copy was not launched. The original release handoff reported the stronger conclusion before collecting those three facts.

### Suggested Action
Every macOS package replacement must verify all of: the installed bundle version, the live process executable path, the Dock or LaunchServices target, and the in-app version row. Report the exact installation path in the handoff.

### Metadata
- Source: user_feedback
- Related Files: README.md, PROJECT_PLAN.md
- Tags: macOS, release, package, verification
- See Also: LRN-20260824-001

### Resolution
- **Resolved**: 2026-08-24T10:39:00+08:00
- **Notes**: Verified the live process and Dock both target `/Applications/Neckline.app`, the bundle is `2.5.2 (14)`, and brought that running app to the foreground; future release handoffs must include this evidence.

---
