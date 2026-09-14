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
