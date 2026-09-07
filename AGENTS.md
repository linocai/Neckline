# Neckline working rules

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- Production runs **K10-v1.4 / Neckline 3.0.1 Build 32**, backend release set **v3.0.1-b32**, since 2026-09-07. K9 and the earlier K8 chains are retired from active production and this worktree; do not reintroduce their runtime code, tables, routes, settings, compatibility shims, or UI placeholders. The explicit offline V3 migration owns the legacy deletion boundary; Git history is the archive.
- K10-v1.4 is a pure stock selector. Complete trade plans, buy/sell price confirmation, holding/exit policy and profit settlement are retired, not pending prerequisites. Track every formally published candidate over its fixed D1/D2 window. The approved publication, selection, overlap and evaluation rules live in `PROJECT_PLAN.md`; never infer a new opportunity from a refreshed card or reset its window after a user action.

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
- Native QA must reuse `/tmp/neckline-v3-qa/macos` and `/tmp/neckline-v3-qa/ios`, with at most one running QA instance per platform. Close the previous instance before relaunching and remove superseded QA bundles/build copies after verification; preserve the installed production client. Isolated QA must set `NK_DISABLE_PERSISTENT_CREDENTIALS=1` and use only temporary process credentials.
- Native visual acceptance must compare actual populated and empty screens with the approved images in `archive/Neckline_V3_界面参考/`. Adopt their white cards, blue accents, restrained typography and deliberate navigation; successful rendering is not visual acceptance. K10-v1.4 product logic takes precedence over retired functions depicted in the references; pixel matching is not required.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.
- Release readiness checks must verify the returned configuration state before any scan exists, using the explicitly bound runtime config. HTTP 200 alone is insufficient; scan history and arbitrary latest stored revisions must not substitute for the active binding.
- K10 cross-module acceptance must include actual FastAPI responses produced from an isolated database decoded by the current Swift models, plus populated native screens. Cover morning updates, withdrawn opportunities, overlapping hits, missing-data counts and analysis revisions; hand-written client fixtures and passing backend tests alone cannot prove the user can read the report or the correct result.
- Read helpers must not execute DDL. `init_schema()` is a controlled write entry point: API startup, an explicit write command, or a release-migration step against a confirmed, backed-up target. A GET is never a migration trigger.
- The strategy layer has **no default values**. If the parameter pack is missing or invalid, the report says "今天没跑成 · 参数未配置" and no listing is produced. Never introduce a fallback, a sample value, or a "just for now" number — a default that ships is a strategy change nobody was told about.
- Never show a bare `vN` on strategy-bearing UI where system, strategy, contract, and append-only revision versions coexist. Name the namespace explicitly (for example `K10-v1.4` and `分析第 1 版`), and verify those labels on the exact detail/history screen before release.
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

- macOS 与 iOS 的 `xcodebuild archive` 若要并行，必须给两条命令传不同的
  `-derivedDataPath`；否则会争用同一 `XCBuildData/build.db`。没有隔离目录就串行归档。

For research-engine changes, work and test in `/Users/linotsai/Lino/whynotme`.
