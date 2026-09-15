# Neckline working rules

Global workflow authority: `/Users/linotsai/.codex/AGENTS.md`. Follow its current role, version, record-budget and release conventions; project-specific production safeguards remain below.

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.
- Production is **K10-v2 / Neckline 3.3.0 release clients Build 69 / backend Build 74 / internal Schema 9 (public report Schema 8)**, backend release set **v3.3.0-b74**, since 2026-09-15. K9 and the earlier K8 chains are retired from active production and this worktree; do not reintroduce their runtime code, tables, routes, settings, compatibility shims, or UI placeholders. The explicit offline V3 migration owns the legacy deletion boundary; Git history is the archive.
- K10-v2 is a pure stock selector. Complete trade plans, buy/sell price confirmation, holding/exit policy and profit settlement are retired, not pending prerequisites. Track every formally published candidate over its fixed D1/D2 window. The approved publication, selection, overlap and evaluation rules live in `PROJECT_PLAN.md`; never infer a new opportunity from a refreshed card or reset its window after a user action.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, configuration, deployment units, data directory, and Python tests.
- `archive/`: version execution records explicitly linked by PROJECT_PLAN.md, plus user-approved design references. Records hold detailed contracts, evidence and handoffs; retired runtime code stays deleted, with Git history as its archive.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: the single authoritative plan — current state, settled rulings, observation items, and next work.

## Working rules

- **Current production state (2026-09-16 00:10 CST):** B74 `be15db7`已部署并发布GitHub，客户端仍B69；9月16日00:10恢复9月15日晚报原task，968完成检查点、931外呼审计、冻结输入和03:00:18截止保留。该HTTP400输入唯一一次恢复再次被内容风控拒绝（Content Exists Risk），usage未知；只读已发现新浪摘要夹入无关排行榜，准备B75清理导航内容；旧失败审计不得误当新增故障，维护期间无新增失败推送。报告/最终推送尚未完成，心跳每2分钟继续，Xcode暂停。 API/worker/four timers active+enabled, control open (`user_authorized_after_b70_live_smoke`). See `archive/v3.3.0-b69_execution.md` section 16.4.
- **September 13 evening incident:** Original task `task_00437ea0ac27c8601b8f5711494dc801` / scan `scan_0cfbef8493c21a2d0ed62bee9992caf5` is failed at attempt 42, stopped at 23:49:36 after Tavily quota refusal. Of 86 events, 68 reached final research states, 3 have legitimate pending-verification conclusions, and 15 failed verification execution. All 30 title batches, 2 reconciliations, 57 bodies and 23,633,932 recorded model tokens remain. B67's original reread bug is fixed and passed in production. B68 recognizes 432/433 as quota failures and gives definitively failed verification one count/input-bound retry only through explicit same-task recovery; ordinary restart cannot renew it. Original frozen input, paid history and deadline (September 14 03:00:13 CST) remain unchanged. No report/cards published; latest failure push accepted for both devices. The new key passed the September 15 isolated smoke and new scheduled production is enabled; the now-expired original task still has no recovery/extension authorization. Preserve its identity, window, budget, deadline and ledger, and never create a replacement for it automatically. See section 17.
- **Current released baseline:** 3.3.0 / both client releases Build 69 / backend Build 74. Research uses action-specific visible evidence, versioned local reads, shared source facts and interruption-safe query closure; recommendation roles and historical identity are separated. B71 price-reference DTO and prior recovery/fulltext fixes remain included. Follow `/Users/linotsai/Lino/whynotme/K10.md`: fixed 1,089-company universe, no 80/40 fulltext caps, labeled-unverified recommendation and daily company selection.
- **Provider authorization boundary (2026-09-13):** User now authorizes routine production, including scheduled reports, normal analysis, market/evaluation maintenance and notifications. Morning runs at 09:00 Asia/Shanghai on each exchange trading day; evening runs at 21:00 on the immediately preceding natural day. This supersedes the old routine-scheduling pause. Isolate pre-cutover queued work; never restore/replay the user-deleted September 10 report or resend its notification. Do not make unrelated provider/balance/permission probes. Ordinary tests remain isolated/network-denied; any real-provider smoke needs explicit user authorization. The September 15 authorized smoke is completed and cleaned; it is not standing authorization for extra probes. Profiles retain draft/provenance status.


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
- B74恢复集仅保留B73/B74（恢复B72/B73代码），清理时258,086,007字节；B74相同pre/post gzip共用inode，恢复B73已在当前Schema9验证。B73集的B72旧代码只读多位置证据的代表位置，不能自动回退后宣称信息完整。禁止旧库覆盖新写。B74本地临时5,050,215字节→0，远端上传/演练副本已回收；清报告专用快照仍待正式报告完成可读后删除。
- The report-cleanup recovery snapshot is `/opt/neckline/data/archive/report-cleanup-20260913/pre-cleanup.db.gz`; retain only until the first post-cleanup scheduled report completes and is readable, then remove this cleanup-only snapshot. After service restoration, never restore this whole DB over new writes; any recovery requires a reviewed, scoped restoration. B73/B74 release rollback sets remain separate.
- Future release closure must remove rehearsal/restore-check databases and their sidecars, check redundant pre/post copies for reuse/compression, and retire superseded recovery points after verifying the retained set. Cleanup applies on success and after closed failures; retained incident data needs a reason and deletion condition. Per-release cleanup and snapshot deduplication are executed; shared release-rollback retention and fixture lifecycle tooling remain tracked work. This does not reintroduce the retired off-host backup feature.

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

- **2026-09-10 B60 用户纠正：** 模型夹带池外公司属于局部可清理线索，过滤该代码／纯池外问题并继续有效内容；不得因此使整批标题或整份报告失败。保持标题覆盖、来源引用、证据真实性和付费检查点不变。用户已授权快修发布后恢复今晚同一冻结任务。
