# Project learnings

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
