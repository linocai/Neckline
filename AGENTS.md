# Neckline working rules

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- The live engine is **K9** (facts → four recall channels → mechanical ranking → quota → explain → playbook → next-morning check → scorecards). The K8 chain (driver seeds, directions, baskets, six gates, tiers, cards) is **retired and deleted**. Do not preserve dead runtime code, tables, routes, settings, data files, compatibility shims, or UI placeholders merely for historical traceability; Git history is the archive. Do not reintroduce K8 concepts from old comments or archived documents.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, packs, deployment units, data directory, and Python tests.
- `archive/`: historical plans, reviews, handoffs, retired packs, and design references. Archived material is not current instruction.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: the single authoritative plan — current state, rulings, slices, release checklist, backlog, change log.
- `.learnings/LEARNINGS.md`: hardened lessons. They must not regress (notably LRN-20260816-001, the two-date report contract).

## Working rules

- Run backend commands from `Backend/` and app commands from `App/`.
- Keep the repository root limited to the six documented visible entries.
- Add current operational facts to `README.md` or `PROJECT_PLAN.md`; move superseded detail to `archive/` instead of growing another root document.
- When the user retires a product capability, deletion is the default: remove its producers, consumers, routes, settings, tests, stored artifacts, and compatibility mappings once the migration boundary is verified. Retention requires an explicit user ruling; do not invent a preservation requirement.
- Treat `Backend/data/`, `.env`, credentials, production databases, and market-data artifacts as local or operational state. Never commit them.
- Tests must use temporary databases or explicit read-only snapshots. Never let a test fall back to the working database.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.
- Read helpers must not execute DDL. `init_schema()` is a controlled write entry point: API startup, an explicit write command, or a release-migration step against a confirmed, backed-up target. A GET is never a migration trigger.
- The strategy layer has **no default values**. If the parameter pack is missing or invalid, the report says "今天没跑成 · 参数未配置" and no listing is produced. Never introduce a fallback, a sample value, or a "just for now" number — a default that ships is a strategy change nobody was told about.
- Rulings recorded in `PROJECT_PLAN.md` §2 are settled. Do not reopen them mid-build. Anything genuinely undecided goes to §13.1 as 事实 / 选项 / 影响面 / 倾向 — and 倾向 is not a decision.
- Backlog numbers are append-only. Before claiming one, `grep -rn "§13.1-B" Backend/tests/` — a number cited in a test message is already taken.

## Verification

```bash
cd Backend
.venv/bin/python -m pytest -q

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'generic/platform=iOS Simulator' build
xcodebuild -project Neckline.xcodeproj -scheme Neckline build-for-testing -destination 'generic/platform=iOS Simulator'
```

**改了任何 `.swift` 就必须跑上面三条 `xcodebuild`,一条都不能省。**

- 这不是保险起见,是登记过的事故:V2.5.0 的验收口径只写了 macOS,而
  `Push/PushManager.swift` 整份住在 `#if os(iOS)` 里、`Views/ReviewView.swift`
  有一支在 `#else` 里 —— `-destination 'platform=macOS'` **一行都不编译它们**。
  于是 6 条编译错(引用裁定 11 已删的板块)一路绿到发版前,靠独立复审才发现。
- **一个平台的构建不能替另一个平台作证**:平台分叉的代码只有跑两个平台才暴露。
  只跑 SwiftPM package 的 build/test 更不行(不暴露 View 层问题)。
- 顺手的结构性做法:**能不分叉就不分叉**。纯数据 / 纯逻辑(如推送落点表)放在
  `#if` 外面,两个平台都编译它,漂移当场就红。

For research-engine changes, work and test in `/Users/linotsai/Lino/whynotme`.
