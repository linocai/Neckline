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
