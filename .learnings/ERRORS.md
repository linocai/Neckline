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
