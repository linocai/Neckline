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

## [ERR-20261011-002] gnu_find_printf_on_macos

**Logged**: 2026-10-11T16:10:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Used GNU `find -printf` on macOS while inventorying reviewer evidence.

### Error
```
find: -printf: unknown primary or operator
```

### Context
The host uses BSD find; the reviewer evidence inventory was otherwise read-only.

### Suggested Fix
Use BSD `find -print` with `stat -f` for file metadata.

### Metadata
- Reproducible: yes
- Related Files: /tmp/neckline-b102-evening-review-20261011/findings-current.json

---

## [ERR-20261011-003] shell_cleanup_guard

**Logged**: 2026-10-11T17:34:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
The command policy rejected a release cleanup command that used shell `rm` syntax.

### Error
```
Rejected: rm -f style commands are not permitted. Use a safer approach
```

### Context
The intended paths were the B103 release temporary directory and verified recovery check files. No deletion occurred in the rejected command.

### Resolution
Re-ran cleanup through Python with exact inventoried paths, open-handle checks, and post-delete existence/size verification.

### Metadata
- Reproducible: yes
- Related Files: /Users/linotsai/Lino/releases/Neckline/v3.7.0-b103-release-20261011

---

## [ERR-20261011-004] doc_check_wrong_workdir

**Logged**: 2026-10-11T17:35:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
A post-release documentation check was run from `Backend/`, so root documentation paths were not found.

### Error
```
wc: PROJECT_PLAN.md: open: No such file or directory
```

### Resolution
Re-ran the check from the repository root and kept backend test commands explicitly scoped to `Backend/`.

### Metadata
- Reproducible: yes
- Related Files: PROJECT_PLAN.md, README.md, archive/v3.7.0-b103_execution.md
