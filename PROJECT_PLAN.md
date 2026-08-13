# Neckline project plan

> Updated: 2026-08-13. This file records current state only. Historical construction logs and release audits are in `archive/`.

## 一、Current goal

Maintain Neckline as a small, production-only application repository. Keep offline strategy research and backtesting in whynotme, with an explicit one-way runtime contract and no production dependency on research code.

## 二、Current state

- Production release line: `v2.4.0`.
- Active strategy contract: K8 / `K8-V0.8`, engines `C2`, `Z2`, `Y2`, charter `v2.3-k8`.
- Client: SwiftUI iOS and macOS under `App/`.
- Service: FastAPI, SQLite, Parquet, scheduled jobs, and systemd definitions under `Backend/`.
- Backtest, evaluation, research panel, capability analysis, and weekly calibration code have moved to `/Users/linotsai/Lino/whynotme`.

## 三、Architecture boundary

```text
Neckline/Backend ── exports SQLite snapshot + read-only artifact contract ──▶ whynotme
Neckline/App     ◀─ consumes production API ────────────────────────────────┘
```

- Neckline does not import whynotme.
- whynotme tasks require an explicit snapshot database path.
- Research output may be read by Neckline as versioned JSON/Markdown artifacts; it does not become an online rule automatically.
- Strategy or pack changes still require user confirmation and the existing versioned activation gates.

## 四、Completed consolidation

- Root reduced to `AGENTS.md`, `App/`, `archive/`, `Backend/`, `PROJECT_PLAN.md`, and `README.md`.
- Legacy `CLAUDE.md`, the former oversized plan, design handoff, and release remediation notes archived.
- `client/` renamed to `App/`; Python service, scripts, tests, packs, deployment files, and local data consolidated under `Backend/`.
- `neckline/backtest`, `neckline/eval`, `neckline/research`, backtest strategies, research scripts, and their tests migrated to whynotme.
- Production API and review screens now consume only the shared lightweight research-artifact contract.

## 五、Next engineering priorities

1. Keep the v2.4.0 production behavior stable while the new repository layout settles.
2. Validate deployment scripts from `Backend/` against a non-production target before the next release.
3. Continue frontend reduction and usability work only when backed by current screenshots or explicit user direction.
4. Let whynotme own all new experiments, calibration reports, and backtest history.

## 六、Release discipline

- Backend: full Python suite plus API smoke.
- App: project generation consistency, macOS build/test, and iOS build/test where a simulator is available.
- Cross-boundary: import scan proves Backend has zero `whynotme`, backtest, evaluation, or research imports.
- Deployment: verify paths, unit files, schema migration on a copy, and rollback before touching production.

## 七、Backlog

- **[P3-32] 主归属 lift 与小簇/大概念对照**：继续积累分层证据；用户确认前不改当前规则。
- **[P3-34] 工程重解读与首版阈值审计**：关注 `engineering_v1`、`platform_days` 与 C2/Z2/Y2 分层表现；只出建议，不自动改包。
- **[P3-37] 退潮主线跳水灵敏度**：积累真实触发与误报样本，无论正负都进入复盘。
- **[P3-49] 位置关读数口径**：等待足够样本后再判断窗口与读数是否需要调整。
- **[P3-51] 主归属无法确定样本**：跟踪“归属待确认”比例及其后续表现。
- **[P4-67] 正式投入与验证窗口**：正式投入使用锚点为 `2026-08-17`；第 15 个交易日重算为 `2026-09-04`。旧日期 `2026-08-26` 已被本裁定取代。

## 八、Verification record

- Pre-migration Neckline baseline: `4358 passed, 3 skipped`.
- Research repository after migration: `338 passed, 3 skipped`.
- Final production-only Backend suite: `3975 passed`.
- App: macOS build succeeded; macOS tests `228 passed, 10 skipped` (real-backend integration cases); iOS Simulator build succeeded.
- Snapshot boundary: a real SQLite snapshot was exported to whynotme and consumed by weekly calibration without an implicit production-database fallback.
