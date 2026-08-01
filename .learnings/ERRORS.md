# Errors

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
