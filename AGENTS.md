# Neckline working rules

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- The live engine is **K9** (facts → four recall channels → mechanical ranking → quota → explain → playbook → next-morning check → scorecards). The K8 chain (driver seeds, directions, baskets, six gates, tiers, cards) is **retired and deleted**. Do not preserve dead runtime code, tables, routes, settings, data files, compatibility shims, or UI placeholders merely for historical traceability; Git history is the archive. Do not reintroduce K8 concepts from old comments or archived documents.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, configuration, deployment units, data directory, and Python tests.
- `archive/`: reserved for temporary artifacts the user explicitly asks to retain. Keep it empty by default;
  Git history is the archive for retired material.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: the single authoritative plan — current state, settled rulings, observation items, and next work.

## Working rules

- Run backend commands from `Backend/` and app commands from `App/`.
- Keep the repository root limited to the six documented visible entries.
- Add current operational facts to `README.md` or `PROJECT_PLAN.md`. Delete superseded detail once its live contract
  is preserved; do not accumulate release diaries, review logs, migration handoffs, or duplicate plans.
- When the user retires a product capability, deletion is the default: remove its producers, consumers, routes, settings, tests, stored artifacts, and compatibility mappings once the migration boundary is verified. Retention requires an explicit user ruling; do not invent a preservation requirement.
- Treat `Backend/data/`, `.env`, credentials, production databases, and market-data artifacts as local or operational state. Never commit them.
- Tests must use temporary databases or explicit read-only snapshots. Never let a test fall back to the working database.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.
- Read helpers must not execute DDL. `init_schema()` is a controlled write entry point: API startup, an explicit write command, or a release-migration step against a confirmed, backed-up target. A GET is never a migration trigger.
- The strategy layer has **no default values**. If the parameter pack is missing or invalid, the report says "今天没跑成 · 参数未配置" and no listing is produced. Never introduce a fallback, a sample value, or a "just for now" number — a default that ships is a strategy change nobody was told about.
- Rulings recorded in `PROJECT_PLAN.md` are settled. Do not reopen them mid-build. Anything genuinely undecided
  must be recorded as 事实 / 选项 / 影响面 / 倾向 — and 倾向 is not a decision.

## Verification

```bash
cd Backend
.venv/bin/python -m pytest -q

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

**改了任何 `.swift` 就必须跑上面三条 `xcodebuild`，一条都不能省。一个平台的构建不能
替另一个平台作证；只跑 SwiftPM 也不能覆盖 View 层。能不分叉就不分叉，纯数据与纯逻辑
尽量放在平台条件编译之外。**

For research-engine changes, work and test in `/Users/linotsai/Lino/whynotme`.
