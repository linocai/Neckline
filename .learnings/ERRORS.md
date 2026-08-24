# Project errors

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
