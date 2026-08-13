# Errors

## [ERR-20260813-005] stale-path-scan-shell-quoting

**Logged**: 2026-08-13T15:29:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tooling

### Summary

A read-only stale-path scan did not run because its shell pattern mixed quote
styles incorrectly.

### Error

```text
zsh:3: unmatched "
```

### Resolution

Split the scan into simple `rg` invocations with single-quoted patterns, then
reran compilation and the application import check successfully.

---

## [ERR-20260813-004] scan-cluster-idempotency-test-compares-audit-clock

**Logged**: 2026-08-13T15:25:00+08:00
**Priority**: low
**Status**: resolved
**Area**: test

### Summary

The full Backend suite exposed a timing-dependent assertion: two idempotent
refreshes were compared including `computed_at`, even though that audit column is
intentionally regenerated on every invocation.

### Error

```text
FAILED tests/test_scan_cluster.py::test_refresh_twice_is_deterministic_and_idempotent
```

### Resolution

Aligned the assertion with the existing bulk-vs-day-by-day contract by excluding
`computed_at` while continuing to compare every business column and row count.

---

## [ERR-20260813-003] snapshot-export-script-missing-source-bootstrap

**Logged**: 2026-08-13T14:20:00+08:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary

The new snapshot exporter compiled but failed when executed as a file because its package root was not on `sys.path`.

### Error

```text
ModuleNotFoundError: No module named 'neckline'
```

### Resolution

Added the same source-checkout bootstrap used by the other Backend scripts, then reran the real export.

---

## [ERR-20260813-002] cache-cleanup-command-rejected

**Logged**: 2026-08-13T14:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary

An exact cleanup of three now-empty research package directories was rejected because it used `rm -rf`.

### Resolution

The cache-only directories were moved to an explicit temporary recovery location instead of being deleted.

---

## [ERR-20260731-001] sqlite-readonly-strategy-query

**Logged**: 2026-07-31T00:00:00+08:00
**Priority**: low
**Status**: resolved
**Area**: data

### Summary
只读核对 `strategy_versions` 时错误假定表中存在自增 `id` 字段。

### Error
```
no such column: id
```

### Context
- 查询目标：`data/neckline.db` 的现役策略版本。
- 实际 schema 以 `version` 为主键，没有 `id`。

### Suggested Fix
先读取 `.schema strategy_versions`，随后仅按已确认字段查询并以 `created_at` 或 `version` 排序。

### Metadata
- Reproducible: yes
- Related Files: data/neckline.db

### Resolution
- **Resolved**: 2026-07-31T00:00:00+08:00
- **Notes**: 改用 schema 中存在的字段重新执行只读查询。

---
