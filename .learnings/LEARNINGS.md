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
