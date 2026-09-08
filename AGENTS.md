# Neckline working rules

Global workflow authority: `/Users/linotsai/.codex/AGENTS.md`. Follow its current role, version, record-budget and release conventions; project-specific production safeguards remain below.

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- Production runs **K10-v1.4 / Neckline 3.1.0 Build 53 / Schema 7**, backend release set **v3.1.0-b53**, since 2026-09-09. K9 and the earlier K8 chains are retired from active production and this worktree; do not reintroduce their runtime code, tables, routes, settings, compatibility shims, or UI placeholders. The explicit offline V3 migration owns the legacy deletion boundary; Git history is the archive.
- K10-v1.4 is a pure stock selector. Complete trade plans, buy/sell price confirmation, holding/exit policy and profit settlement are retired, not pending prerequisites. Track every formally published candidate over its fixed D1/D2 window. The approved publication, selection, overlap and evaluation rules live in `PROJECT_PLAN.md`; never infer a new opportunity from a refreshed card or reset its window after a user action.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, configuration, deployment units, data directory, and Python tests.
- `archive/`: version execution records explicitly linked by PROJECT_PLAN.md, plus user-approved design references. Records hold detailed contracts, evidence and handoffs; retired runtime code stays deleted, with Git history as its archive.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: the single authoritative plan — current state, settled rulings, observation items, and next work.

## Working rules

- **Current production state (2026-09-09 02:02 CST):** B53 is deployed; the same user-authorized September 8 evening task resumed with all 67 selected bodies and 13 supplemental bodies preserved. All 66 investigations are complete; final classification and publication are resuming after repairing the parallel aggregation container and paused-checkpoint recovery. Incomplete finalization must fail without publication. Resume closes a persisted assessment before new query planning; exhausted fulltext capacity blocks new requests, and denied empty results do not trigger repeated assessment. Research semantics are validated before completed caching; rejected historical derivatives remain auditable. Switch open and worker active, evening/morning timers await final report verification. Current task keeps execution revision 2 plus recorded runtimeRepair (32768 investigation and finalization output tokens; original start +21600 seconds); future schedules bind revision 4. Never recover the retired 2,472-article incident batch. See `PROJECT_PLAN.md`.
- **Settled 3.1.0 rulings:** Unverified rumors may be compared and formally recommended normally, with an explicit “未核实” label preserved through publication, detail, analysis and history; do not silently require the rumor to become verified first. Other K10 identity, exclusion, comparison and evaluation rules still apply. Do not reserve a separate full-text quota: use the existing 80/40 limit and actual remaining capacity. These user decisions supersede the two pending items in the strategy handoff document.

- Run backend commands from `Backend/` and app commands from `App/`.
- Keep the repository root limited to the six documented visible entries.
- Keep current operations in README.md and current control state in PROJECT_PLAN.md. Move detailed version contracts, failures, validation and deployment evidence into the one linked archive version record, following the global control/fact/evidence split. Do not duplicate current plans or grow parallel review diaries.
- When the user retires a product capability, deletion is the default: remove its producers, consumers, routes, settings, tests, stored artifacts, and compatibility mappings once the migration boundary is verified. Retention requires an explicit user ruling; do not invent a preservation requirement.
- Treat `Backend/data/`, `.env`, credentials, production databases, and market-data artifacts as local or operational state. Never commit them.
- Tests must use temporary databases or explicit read-only snapshots. Never let a test fall back to the working database.
- Native QA must reuse `/tmp/neckline-v3-qa/macos` and `/tmp/neckline-v3-qa/ios`, and the existing shared simulator named “主模拟器”; do not create a project-specific simulator. Keep at most one running QA instance per platform. Close the previous instance before relaunching and remove superseded QA bundles/build copies after verification; preserve the installed production client. Isolated QA must set `NK_DISABLE_PERSISTENT_CREDENTIALS=1` and use only temporary process credentials.
- Before any simulator install/test, pass `NK_BUNDLE_SUFFIX` as an explicit `xcodebuild` build setting and verify the built Info.plist plus xctestrun host bundle ID equals the intended QA ID. An environment variable alone is not proof. If it equals the production ID, stop before installation. Record the simulator app inventory first; preserve data containers before any necessary recovery.
- Native visual acceptance must compare actual populated and empty screens with the approved images in `archive/Neckline_V3_界面参考/`. Adopt their white cards, blue accents, restrained typography and deliberate navigation; successful rendering is not visual acceptance. K10-v1.4 product logic takes precedence over retired functions depicted in the references; pixel matching is not required.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.
- Release readiness checks must verify the returned configuration state before any scan exists, using the explicitly bound runtime config. HTTP 200 alone is insufficient; scan history and arbitrary latest stored revisions must not substitute for the active binding.
- Deploy and restore must preserve the production root directory's verified owner/group/mode; never inherit private staging directory permissions through synchronization. Readiness probes must validate each endpoint's actual DTO envelope, not assumed shared fields.
- K10 cross-module acceptance must include actual FastAPI responses produced from an isolated database decoded by the current Swift models, plus populated native screens. Cover morning updates, withdrawn opportunities, overlapping hits, missing-data counts and analysis revisions; hand-written client fixtures and passing backend tests alone cannot prove the user can read the report or the correct result.
- Scheduled-flow regressions must enter through the real CLI enqueue and worker/handler boundary with deterministic transports. Do not normalize fixture timestamps before that boundary: test equivalent timezone spellings, source delays, uncertain publication times, producer failures and client read failures as separate cases. A passing legacy suite is not evidence that a newly reported scenario is fixed.
- Discovery acceptance must first use the frozen incident volume offline to prove all-title audit, frozen global selection, zero pre-selection body calls and bounded concurrency. The approved isolated live validation is one latest window with all titles processed and at most 80 evening / 40 morning real article bodies; no total cost/call budget is required, and it must stop before body calls on a title-contract or quality failure. Never repeat an unrestricted full-corpus paid test followed by production replay. Report offline performance and real-provider evidence separately. APNs checks verify readable/signable credentials and durable retry behaviour when such execution is authorized.
- Review-driven repairs must preserve each confirmed reproduction as a regression before closing it. Verify producer → persistence → API → client semantics where applicable, including interruption between durable writes; do not weaken the scenario or its business assertions merely to make the suite pass.
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
