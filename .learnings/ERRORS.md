## [ERR-20260914-001] pytest_temp_setup

**Logged**: 2026-09-14T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
The command policy rejects recursive deletion used only to reset a pytest batch directory.

### Error
`rm -f style commands are not permitted. Use a safer approach`

### Suggested Fix
Create a new unique basetemp directory for each test batch and clean only verified files through the release cleanup path.

### Metadata
- Reproducible: yes
- Related Files: Backend/tests

---

## [ERR-20260914-002] macos_tmp_alias_cleanup_guard

**Logged**: 2026-09-14T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
Cleanup must not reject the macOS `/tmp` to `/private/tmp` alias as a path escape.

### Error
The first B69 cleanup receipt run compared the literal `/tmp` path with its resolved path and stopped before deletion.

### Resolution
The release cleanup script now treats the root as a fixed literal, verifies each direct child remains under it, checks `lsof`, and removes only the inventoried children.

---
