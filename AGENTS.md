# Neckline working rules

Apple 开发环境遵循 `/Users/linotsai/.codex/AGENTS.md` 的「Apple 开发基线（2026-09-20 用户确认）」：自用 App 最低 OS 27.0，共用 iPhone 18 Pro／iOS 27「主模拟器」；旧版本记录的 26.x 要求仅作历史。

Global workflow authority: `/Users/linotsai/.codex/AGENTS.md`. Follow its current role, version, record-budget and release conventions; project-specific production safeguards remain below.

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- Production backend is **K10-v2 / Neckline 3.6.1 Build95 / internal and public Schema10**, backend-only release set **v3.6.1-b95**, runtime `f5db9c3631e147f1b7940830625263a87818ac71`, since 2026-09-29. Mac installed/iOS prepared artifacts remain Build93. B92 is the fresh business-data boundary; K9/K8 and pre-B92 historical runtime compatibility are retired. Git history and version records preserve engineering history, not a runtime data fallback.
- K10-v2 is a pure stock selector. Complete trade plans, buy/sell price confirmation, holding/exit policy and profit settlement are retired, not pending prerequisites. Track every formally published candidate over its fixed D1/D2 window. The approved publication, selection, overlap and evaluation rules live in `PROJECT_PLAN.md`; never infer a new opportunity from a refreshed card or reset its window after a user action.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, configuration, deployment units, data directory, and Python tests.
- `archive/`: version execution records explicitly linked by PROJECT_PLAN.md, plus user-approved design references. Records hold detailed contracts, evidence and handoffs; retired runtime code stays deleted, with Git history as its archive.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: the single authoritative plan — current state, settled rulings, observation items, and next work.

## Working rules

- **B92全新数据起点（2026-09-26用户裁决）：** B92前报告、任务、原件、账本、行情、缓存及旧数据恢复集全部退役，不得读取、迁移、恢复或当作上下文。2026-09-27 B93发布已切换新库，旧生产与本地业务存储已清除。固定1,089家公司资料/策略从已批准静态输入重新登记；新起点以后历史可按具体问题使用，无消息年龄硬门槛。
- **当前发布与绑定：** B95标题协调容错快修已上线，用户明确不用重跑旧任务；Mac/iOS制品与安装状态见PROJECT_PLAN及`archive/v3.6.1-b92_execution.md`第12节。新库run/execution/collection revision均为1，固定策略快照`k10-v2-20260909`，执行与报告契约绑定B92；不得照抄旧B82 revision2。`DB_PATH=K10_DB_PATH`与`PARQUET_DIR=K10_PARQUET_DIR`必须指向同一新起点存储。交易日历重新取得，当前覆盖2026–2027。
- **运行状态（2026-09-28 17:26用户明确恢复）：** 采集与报告control均open/user_opened；晨晚报、采集及两行情timer active/enabled，API/worker active。正常生产资讯、研究、核验、行情及完成/失败通知已获授权。旧9月22专用心跳保持PAUSED，不恢复旧任务。模型、Tavily和金十凭据已重新登记并核验加载；TuShare/鉴权/APNs独立凭据保留，新客户端重新注册设备。
- **外呼边界：** 正常定时生产已恢复，不扩大到额外供应商余额/权限探针、测试推送或旧任务恢复。普通测试仍为隔离库与确定性transport。采集自然日08:00/20:00，晚报对应交易日前一自然日21:00，晨报交易日08:30/09:20截止；不补跑错过的旧窗口。
- 历史B81–B89运行与故障仅见`archive/v3.5.1-b82_execution.md`；其保留旧业务和恢复旧任务条款已被全新起点裁决取代。资料仍保留draft/provenance状态，未排序材料不冒充正式推荐或D1/D2样本。

- Run backend commands from `Backend/` and app commands from `App/`.
- Keep the repository root limited to the six documented visible entries.
- Keep current operations in README.md and current control state in PROJECT_PLAN.md. Move detailed version contracts, failures, validation and deployment evidence into the one linked archive version record, following the global control/fact/evidence split. Do not duplicate current plans or grow parallel review diaries.
- When the user retires a product capability, deletion is the default: remove its producers, consumers, routes, settings, tests, stored artifacts, and compatibility mappings once the migration boundary is verified. Retention requires an explicit user ruling; do not invent a preservation requirement.
- Treat `Backend/data/`, `.env`, credentials, production databases, and market-data artifacts as local or operational state. Never commit them.
- Tests must use temporary databases or explicit read-only snapshots. Never let a test fall back to the working database.
- Native QA must reuse `/tmp/neckline-v3-qa/macos` and `/tmp/neckline-v3-qa/ios`, and the existing shared simulator named “主模拟器”; do not create a project-specific simulator. Keep at most one running QA instance per platform. Close the previous instance before relaunching and remove superseded QA bundles/build copies after verification; preserve the installed production client. Isolated QA must set `NK_DISABLE_PERSISTENT_CREDENTIALS=1` and use only temporary process credentials.
- Before any simulator install/test, pass `NK_BUNDLE_SUFFIX` as an explicit `xcodebuild` build setting and verify the built Info.plist plus xctestrun host bundle ID equals the intended QA ID. An environment variable alone is not proof. If it equals the production ID, stop before installation. Record the simulator app inventory first; preserve data containers before any necessary recovery.
- Native visual acceptance must compare actual populated and empty screens with the approved images in `archive/Neckline_V3_界面参考/`. Adopt their white cards, blue accents, restrained typography and deliberate navigation; successful rendering is not visual acceptance. K10-v2 product logic takes precedence over retired functions depicted in the references; pixel matching is not required.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.
- Release readiness checks must verify the returned configuration state before any scan exists, using the explicitly bound runtime config. HTTP 200 alone is insufficient; scan history and arbitrary latest stored revisions must not substitute for the active binding.
- Deploy and restore must preserve the production root directory's verified owner/group/mode; never inherit private staging directory permissions through synchronization. Readiness probes must validate each endpoint's actual DTO envelope, not assumed shared fields.
- K10 cross-module acceptance must include actual FastAPI responses produced from an isolated database decoded by the current Swift models, plus populated native screens. Cover morning updates, withdrawn opportunities, overlapping hits, missing-data counts and analysis revisions; hand-written client fixtures and passing backend tests alone cannot prove the user can read the report or the correct result.
- Scheduled-flow regressions must enter through the real CLI enqueue and worker/handler boundary with deterministic transports. Do not normalize fixture timestamps before that boundary: test equivalent timezone spellings, source delays, uncertain publication times, producer failures and client read failures as separate cases. A passing legacy suite is not evidence that a newly reported scenario is fixed.
- Discovery acceptance for 3.2.0 is offline only: prove all-title audit, cross-batch event merging, stock-pool-scoped retrieval, evidence reuse, no per-title search, removal of full-text quotas, bounded execution concurrency and recovery of only affected steps. Use frozen local inputs and deterministic provider responses; do not add a real-provider acceptance requirement. Track repeated input and stage usage without equating synthetic counts or cache hits with real savings. APNs retry tests use isolated transports; no live push during this upgrade.
- Review-driven repairs must preserve each confirmed reproduction as a regression before closing it. Verify producer → persistence → API → client semantics where applicable, including interruption between durable writes; do not weaken the scenario or its business assertions merely to make the suite pass.
- Task-producer regressions must use the real returned task IDs and let the producer create its own execution binding and checkpoints. Manual binding or state repair is allowed only for prerequisites outside the entry point being tested; otherwise a green worker test can hide a broken user or scheduled entry point.
- Deterministic producer/worker regressions must pass the frozen business clock explicitly at the production handler boundary; do not rely on a default parameter captured at import time. Keep lease time on a consistent current clock, and assert terminal B76 flows leave no started／running／unknown external attempt.
- Task status and collection-coverage reads must project only required checkpoint fields, never deserialize full paid replies or document-ref trees; serialize large JSON projections within the process and verify memory under concurrent reads. Keep exact receipts intact for no-network replay.
- Read helpers must not execute DDL. `init_schema()` is a controlled write entry point: API startup, an explicit write command, or a release-migration step against a confirmed, backed-up target. A GET is never a migration trigger.
- Daily research excludes prospectus bodies across search, extraction, restored caches and final model packets. Bind every new verification request to a necessary current-event question; background long documents require a direct question-bound locator. Preserve coherent evidence and exact paid replies; identical wire input must not be rebilled after parser changes, restart or receipt export/restore.
- Company retrieval consumes business values with real Latin token boundaries, never serialized JSON keys. A shortened request must retain a durable visible-source manifest; the complete local database is not the model evidence whitelist.
- Model-output resilience: discard out-of-pool optional hints, collapse repeated source selections, and ignore unused merge hints without invalidating usable selections. Derivable bookkeeping belongs to the system. Preserve paid rejected replies privately for exact-input revalidation; invalid evidence or unknown source references must never become invented facts. A fixed/restarted task is not completion: verify the actual report and its requested delivery.
- The strategy layer has **no default values**. If the parameter pack is missing or invalid, the report says "今天没跑成 · 参数未配置" and no listing is produced. Never introduce a fallback, a sample value, or a "just for now" number — a default that ships is a strategy change nobody was told about.
- Never show a bare `vN` on strategy-bearing UI where system, strategy, contract, and append-only revision versions coexist. Name the namespace explicitly (for example `K10-v1.4` and `分析第 1 版`), and verify those labels on the exact detail/history screen before release.
- Opportunity home prioritizes current opportunities. Expiry notices, ended recommendations and obsolete risks belong in default-collapsed history; never let them occupy the first screen above current cards. Keep current risk/withdrawal notices visible and preserve the original records. Native acceptance must verify the first screen with populated history, not merely that every record can render. Use the fixed D2 deadline to archive ended-window notices; withdrawal is a retained lifecycle fact and does not become an expiry label.
- Rulings recorded in `PROJECT_PLAN.md` are settled. Do not reopen them mid-build. Anything genuinely undecided
  must be recorded as 事实 / 选项 / 影响面 / 倾向 — and 倾向 is not a decision.

## Temporary artifacts and backup cleanup

- User retired optional off-host/S3 automatic backup on 2026-09-13. Its scripts, configuration and timer are removed; do not recreate them. Release rollback snapshots and local Schema recovery are separate and remain required.
- A task is not complete until its local user-temp, `/tmp` and relevant cloud artifacts are inventoried, ownership/open handles checked, disposable files removed, and before/after usage plus retained recovery points verified. Include pytest batches, `mkdtemp` isolation directories, reviewer/reproducer databases, QA copies and SQLite WAL/SHM files. Never delete other projects' shared pytest directories by prefix alone.
- Successful tests should not retain their full databases: use pytest `-o tmp_path_retention_policy=failed` pending the tracked fixture/config cleanup. Closed reproductions retain regression source and concise evidence, not every database copy. Preserve independent writable databases; reduce fixture payloads without weakening full-pool and real CLI/worker/API coverage.
- 当前数据恢复集为`/opt/neckline/releases/v3.6.1-b95/recovery`：保留切换前已校验的新起点快照（含用户新设置、最新付费回执与晨报检查点），B95不可变后端制品在上级目录。B94/B93不可变代码包保留作代码回退；B94旧快照与B93发布时空起点快照已被替代并回收。旧B81/B82/B89数据恢复集已清除；任何时候不得回灌旧历史。新业务写入后不得用发布时快照整库覆盖，需先核对新写与外呼状态，必要时前向修复。

- Future release closure must remove rehearsal/restore-check databases and their sidecars, check redundant pre/post copies for reuse/compression, and retire superseded recovery points after verifying the retained set. Cleanup applies on success and after closed failures; retained incident data needs a reason and deletion condition. Per-release cleanup and snapshot deduplication are executed; shared release-rollback retention and fixture lifecycle tooling remain tracked work. This does not reintroduce the retired off-host backup feature.

## Verification

- Environment-gated native acceptance must verify the actual XCTest result counts and skip reasons. A successful xcodebuild exit is not acceptance when the intended tests were skipped; pass required inputs through the test runner configuration and require positive passed counts with zero skipped target cases.

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

- **2026-09-10 B60 用户纠正：** 模型夹带池外公司属于局部可清理线索，过滤该代码／纯池外问题并继续有效内容；不得因此使整批标题或整份报告失败。保持标题覆盖、来源引用、证据真实性和付费检查点不变。用户已授权快修发布后恢复今晚同一冻结任务。
