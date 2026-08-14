# V2.4.1 macOS 选股工作台独立 Review

> Review date: 2026-08-13
> Reviewer scope: current uncommitted diff on `main`, `PROJECT_PLAN.md`, `archive/施工图/V2.4.1_执行计划_20260813.md`, macOS screenshot matrix under `/tmp/neckline-v241-shots.URm8ng/`, and directly related App/Backend code paths.
> Authority: current user decisions in the active thread override duplicated or conflicting planning notes.

## Findings

### P1 — Info-card backend display contract can 500 when a stock has absent tags

- File: `Backend/neckline/report/info_card.py:366`
- Trigger: `InfoCard.to_public_dict()` is called with `tags_absent` containing a known tag code, e.g. the info-card endpoint for a member where `build_member_tags()` returns `warn_streak_top`.
- Evidence:
  - `InfoCard.to_public_dict()` builds `tagAbsences` with `mt.tag_label(code)` / `mt.ALL_TAG_CODES` at lines 366–368.
  - The only new `mt` import is inside `InfoCardKlineBar.to_public_dict()` at lines 115–117, so it is local to the wrong method and not visible in `InfoCard.to_public_dict()`.
  - Direct proof command:
    ```bash
    cd Backend && .venv/bin/python - <<'PY'
    from neckline.report.info_card import InfoCard
    from neckline.selection import member_tags as mt
    card = InfoCard(code='600000.SH', name='测试', trade_date='20260813', kline_available=False, tags_absent=[mt.TAG_WARN_STREAK_TOP])
    try:
        card.to_public_dict()
    except Exception as exc:
        print(type(exc).__name__, exc)
    PY
    ```
    Output: `NameError name 'mt' is not defined`.
- Impact: the additive v2.4.1 info-card contract breaks exactly on the path it was introduced for: labeled absent tags. A member detail click can fail server-side instead of rendering the embedded information card.
- Minimal fix: move/import `member_tags as mt` in `InfoCard.to_public_dict()` or module scope, remove the unused kline-level import, and add a backend regression test that constructs/serves an info card with `tags_absent`.

### P1 — macOS toolbar removed data freshness, but Today Market does not render a replacement

- Files:
  - `App/Neckline/Components/NKToolbar.swift:58`
  - `App/Neckline/Views/BasketDailyView.swift:1955`
- Trigger: open macOS Selection → default Today Market.
- Evidence:
  - `NKToolbar` now renders only refresh/settings at lines 58–60; the previous freshness badge is no longer in the toolbar.
  - `MacTodayMarketPage` renders the page header, `MarketRegimeStrip`, and sentiment metrics at lines 1955–1980, but never reads `model.report.dataFreshness`.
  - The screenshot `/tmp/neckline-v241-shots.URm8ng/market.png` shows no readable data condition / freshness status.
- Impact: the user no longer has a visible answer to “这份选股数据是不是齐、是不是滞后”。This contradicts the v2.4.1 plan’s Today Market contract: “Market status, sentiment, sector context, readable data condition.”
- Minimal fix: add a compact, humanized data-condition block to `MacTodayMarketPage` using existing `DataFreshness` fields. Keep raw “⑤/数据新鲜度” audit wording out of the default layer.

### P1 — iOS auction report behavior changed even though this release is macOS-only

- Files:
  - `App/Neckline/Views/BasketDailyView.swift:143`
  - `App/Neckline/Views/AuctionCardView.swift:203`
  - `App/Neckline/Views/AuctionCardView.swift:611`
  - `App/Neckline/Views/AuctionCardView.swift:540`
- Trigger: on iOS, tap the auction summary card; it still presents `AuctionReportPage(model:payload:)` through the sheet at `BasketDailyView.swift:143–145`.
- Evidence:
  - `AuctionReportPage` was modified in shared code, not a macOS-only wrapper.
  - `AuctionVerdictCard` now defaults `expanded = true` at line 611; before this diff it defaulted to `NKQA.expandDisclosures`, normally collapsed.
  - `riskSummary(_:)` at lines 540–554 replaces risk body text for all platforms, not just macOS.
- Impact: iOS report reading density, default expansion behavior, and risk wording change in a release explicitly constrained to macOS UI. This violates the execution plan’s non-goal: “iOS/iPad layout changes” are out of scope and iOS routes/sheets should be preserved.
- Minimal fix: split the macOS embedded report presentation from the existing iOS sheet presentation, or gate the new report hierarchy/default expansion/risk summarization behind `embedded` and/or `#if os(macOS)`.

### P2 — Info-card QA/deep-link hook no longer selects a visible macOS destination

- Files:
  - `App/Neckline/App/AppModel.swift:714`
  - `App/Neckline/Views/BasketDailyView.swift:2097`
- Trigger: launch macOS with only `NECKLINE_INITIAL_INFOCARD_CODE=<member code>`.
- Evidence:
  - The hook at lines 714–719 only calls `openInfoCard(...)`.
  - The new macOS workbench renders embedded info-card content only inside the selected basket/member detail when `request.code == member.tsCode` at lines 2097–2099.
  - The hook does not set `selectionDestination = .basket(b.basketId)` and there is no model-level selected member code, so a non-first member cannot be made visible by this hook.
- Impact: the established QA/deep-link hook can load data without any visible UI effect, and cannot prove the third-or-later member detail path without extra manual interaction.
- Minimal fix: when resolving `NECKLINE_INITIAL_INFOCARD_CODE`, also select the containing basket and provide a macOS member-selection initial value/state so the requested member is the visible detail.

### P2 — Shared market unavailable state still shows engineering-style warning in user-facing mode

- File: `App/Neckline/Components/SharedUI.swift:893`
- Trigger: market regime unavailable; shown in the positions screenshot `/tmp/neckline-v241-shots.URm8ng/positions.png`.
- Evidence:
  - `MarketRegimeStrip` correctly hides raw `regimeReason` when `userFacing == true`, but `unavailable` still always renders `⛔ 「没取到」不等于「今天没什么特别的」` at lines 893–894.
- Impact: the original raw machine expression is gone, but the default user layer still carries a system/audit warning tone instead of a simple availability and next-action message. This partially misses the user’s “行情状态乱码” complaint for Positions.
- Minimal fix: render only “市场状态暂不可用，请稍后刷新。” in user-facing mode; move the stronger “缺数不等于正常” statement to an explicit technical/audit disclosure.

### P2 — Auction risk section can repeat generic “模型补充” rows without actionable distinction

- Files:
  - `App/Neckline/Networking/Models/AuctionModels.swift:107`
  - `App/Neckline/Views/AuctionCardView.swift:515`
  - `App/Neckline/Views/AuctionCardView.swift:552`
- Trigger: auction payload contains multiple `llm_note` or unknown risk kinds.
- Evidence:
  - `nkAuctionRiskKindLabel("llm_note")` returns `模型补充` at line 108.
  - The default branch of `riskSummary(_:)` returns the same generic sentence for every unrecognized/LLM note at lines 552–553.
  - Screenshot `/tmp/neckline-v241-shots.URm8ng/auction-final.png` shows three repeated rows: `模型补充 发现一项需要留意的异常，详细依据可在技术附录查看。`
- Impact: the report is visually calmer, but the risk section still fails the “让报告成为报告” direction for these rows; it shows repeated source/process labels instead of distinct user-readable risk information.
- Minimal fix: de-duplicate identical summaries, rename the source-neutral chip to something like `补充提示`, and either preserve a short sanitized distinction from `risk.text` or aggregate repeated generic risks into one row.

### P2 — Sidebar OUT summary points to “技术说明” without an actual user path or chief reason

- File: `App/Neckline/Views/BasketDailyView.swift:1845`
- Trigger: macOS Selection sidebar with non-empty `outCandidates`.
- Evidence:
  - The sidebar renders `另有 N 只未进入篮子，原因见技术说明。` at lines 1845–1848.
  - In the new workbench this text is not a button/disclosure and does not provide the “首要原因” requested by the plan.
- Impact: the sidebar is much cleaner than before, but this line leaves the user with a dead reference and less information than the agreed “主要因为市场环境不匹配” style.
- Minimal fix: compute the same compact chief-reason sentence used by the old stats summary, or remove “见技术说明” unless a real technical entry is available from that location.

## Verification performed

- Read:
  - `AGENTS.md`
  - `PROJECT_PLAN.md`
  - `archive/施工图/V2.4.1_执行计划_20260813.md`
  - current `git status` / `git diff`
  - directly related App/Backend source and tests
- Inspected screenshots:
  - `/tmp/neckline-v241-shots.URm8ng/market.png`
  - `/tmp/neckline-v241-shots.URm8ng/basket-final.png`
  - `/tmp/neckline-v241-shots.URm8ng/auction-final.png`
  - `/tmp/neckline-v241-shots.URm8ng/intel-final.png`
  - `/tmp/neckline-v241-shots.URm8ng/basket-narrow.png`
  - `/tmp/neckline-v241-shots.URm8ng/positions.png`
- Commands:
  - `git diff --check` — passed
  - `cd Backend && .venv/bin/python -m pytest -q tests/test_selection_member_tags.py::test_public_member_contract_adds_labels_for_absent_tags tests/test_v233_auction_llm.py::test_user_risk_note_uses_display_name_not_internal_key` — passed, 2 tests
  - manual `InfoCard(... tags_absent=[...]).to_public_dict()` proof — failed with `NameError name 'mt' is not defined`

## Residual risk / not covered

- I did not rerun the full backend suite or full macOS `xcodebuild test` because the review already found P1 repair items. `PROJECT_PLAN.md` records that the pre-review full gate had previously passed.
- I did not audit `archive/` beyond the current execution plan.
- iOS was reviewed statically at the shared component boundary; no iOS simulator screenshot was taken.

## Suggested repair order

1. Fix `InfoCard.to_public_dict()` `mt` scope and add the missing regression test.
2. Restore macOS-visible data freshness / data condition in Today Market.
3. Contain auction-report changes to macOS or explicitly preserve the old iOS sheet behavior.
4. Repair the QA/deep-link member detail hook.
5. Clean up the P2 copy issues in shared market unavailable, auction repeated risks, and sidebar OUT summary.

Current review status: **not ready for release-candidate version/build bump** until P1 findings are repaired and affected gates are rerun.

---

## Repair re-review — 2026-08-13 20:48

Reviewer scope: repair diff after Builder handoff, screenshots under `/tmp/neckline-v241-repair-shots.JmnEKy/`, and targeted commands below.

### Current findings

未发现新的 P0/P1/P2 可报告问题。

### Original finding closure check

| Original finding | Status | Evidence |
| --- | --- | --- |
| P1 — Info-card `tagAbsences` can 500 | Closed | `Backend/neckline/report/info_card.py:63` now imports `member_tags as mt` at module scope; `InfoCard.to_public_dict()` uses it at `Backend/neckline/report/info_card.py:366`. Regression test added at `Backend/tests/test_info_card.py:510`. |
| P1 — Today Market lost data freshness/status | Closed | `MacTodayMarketPage` now renders `dataCondition` immediately after user-facing market regime at `App/Neckline/Views/BasketDailyView.swift:1977`; copy is built from `DataFreshness` at `App/Neckline/Views/BasketDailyView.swift:2019`. Screenshot `market.png` shows “数据状态 / 市场、行业和扫描数据均已就绪。” |
| P1 — Auction shared changes affected iOS | Closed | `AuctionReportPage` now gates the new right-pane order behind `isMacPresentation` at `App/Neckline/Views/AuctionCardView.swift:217`; non-mac keeps the original sheet order at `App/Neckline/Views/AuctionCardView.swift:225`. `isMacPresentation` is `embedded` only on macOS at `App/Neckline/Views/AuctionCardView.swift:237`. iOS risk text and default expansion are preserved by `displayRisks` at `App/Neckline/Views/AuctionCardView.swift:573` and `AuctionVerdictCard` at `App/Neckline/Views/AuctionCardView.swift:682`. |
| P2 — Info-card deep-link did not select visible basket/member | Closed | `selectionMemberCode` exists at `App/Neckline/App/AppModel.swift:238`; `NECKLINE_INITIAL_INFOCARD_CODE` now routes through `openInfoCardForSelection` at `App/Neckline/App/AppModel.swift:716`; that method selects the basket and member at `App/Neckline/App/AppModel.swift:803`. `MacBasketDetailPage.selectInitialMember()` consumes the requested member at `App/Neckline/Views/BasketDailyView.swift:2193`. Screenshot `deep-link-third.png` shows the third member “九洲药业” selected and rendered. |
| P2 — Shared unavailable market state still used audit/engineering wording | Closed | In user-facing mode, `MarketRegimeStrip.unavailable` now shows only simple availability copy and hides the stronger audit warning at `App/Neckline/Components/SharedUI.swift:886`. Screenshot `positions.png` shows “行情状态暂不可用 / 市场状态暂不可用，请稍后刷新。” |
| P2 — Auction risks repeated generic “模型补充” rows | Closed | macOS `displayRisks` now renames `llm_note` to “补充提示”, deduplicates by summary, and appends same-kind counts at `App/Neckline/Views/AuctionCardView.swift:573`. Screenshot `auction.png` shows one aggregated “补充提示” row with “另有 2 项同类提示”. |
| P2 — Sidebar OUT summary pointed to dead “技术说明” | Closed | Sidebar OUT text now computes a chief reason from `nkGateOrder` and emits “另有 N 只未进入篮子，主要因为…。” at `App/Neckline/Views/BasketDailyView.swift:1951`. Screenshot `market.png` shows the repaired copy. |

### Verification performed after repair

- `git diff --check` — passed
- `cd Backend && .venv/bin/python -m pytest -q tests/test_info_card.py::test_info_card_public_payload_labels_absent_member_tags` — passed, 1 test
- `cd App && xcodebuild test -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' -only-testing:NecklineTests/AppModelTests/testInfoCardSelectionTargetsContainingBasketAndMember` — passed, 1 test
- Inspected repair screenshots:
  - `/tmp/neckline-v241-repair-shots.JmnEKy/market.png`
  - `/tmp/neckline-v241-repair-shots.JmnEKy/auction.png`
  - `/tmp/neckline-v241-repair-shots.JmnEKy/deep-link-third.png`
  - `/tmp/neckline-v241-repair-shots.JmnEKy/positions.png`

### Residual risk / not covered after repair

- iOS was re-checked statically at the shared component boundary; I did not take a fresh iOS simulator screenshot. The repaired code now has explicit non-mac branches preserving the old sheet order and raw risk/reason display.
- The review relied on Builder’s reported full gates for broad coverage: Backend 3981 passed, macOS 230 passed / 10 skipped, Debug build, temporary DB smoke, and diff check. I reran targeted proof commands for the two highest-risk repaired paths.
- Some old raw/audit strings still exist in legacy iOS or non-user-facing disclosure paths; that is outside this macOS-only V2.4.1 review scope and not a release blocker.

Repair re-review status: **ready to return to release-candidate preparation** from a review standpoint. No unresolved P0/P1/P2 findings remain in the repaired V2.4.1 scope.

---

## Release-candidate addendum — 2026-08-13 20:55

- Metadata synchronized through `App/scripts/prepare_release_candidate.sh`: client `2.4.1` / Build `2`; Backend `VERSION = "v2.4.1"`; generated `pbxproj` contains four `MARKETING_VERSION = 2.4.1` entries and two `CURRENT_PROJECT_VERSION = 2` entries.
- Verification: Backend `3982 passed`; macOS `230 passed, 10 skipped`; Debug and Release builds passed; temporary-DB API smoke passed and `/health` returned `v2.4.1`; both working-tree and cached `git diff --check` passed.
- Local signed artifact: `/tmp/neckline-v241-rc.qrDQ1q/Neckline-v2.4.1-b2.xcarchive` (36 MB). Archive plist confirms `CFBundleShortVersionString=2.4.1`, `CFBundleVersion=2`; `codesign --verify --deep --strict` passed.
- Commit SHA / rollback anchor before commit: `b3e3d8189f040bc6826916be979d5ff3082b9d64`. If later committed, rollback is a revert of the V2.4.1 commit(s) and a rebuild of v2.4.0 Build 1; no schema reversal is required.
- Boundary: archive only. No install, deployment, upload, notarization, tag, commit, push, or distribution action was performed.

---

## Production release addendum — 2026-08-14 08:33–08:43 CST

The RC boundary above was preserved until the user explicitly authorized deployment, release, package replacement, and source publication.

- **Preflight and rollback:** production was confirmed at `deploy@114.66.0.38:/opt/neckline` behind `https://nk.linotsai.top`. The pre-release source and systemd units are preserved under `/opt/neckline-release-backups/v2.4.1-pre-20260814-083622/`. Two database backups were created at `/opt/neckline/data/neckline.db.bak-v241-20260814-083622` and `/opt/neckline/data/neckline.db.cpbak-v241-20260814-083622`; both retained owner `neckline:neckline`, mode `0600`, and passed SQLite integrity checks. The copy backup hash matched the live database at backup time.
- **Consolidation cleanup:** the first post-consolidation production sync intentionally removed legacy client/docs/research/backtest code from the Backend deployment. The obsolete weekly research timer was disabled, removed from systemd, and preserved in the rollback directory; active repository and installed production units now agree at eight units.
- **Backend:** code sync and ownership self-check passed. `neckline.service` restarted at 08:39 CST with a new PID, `NRestarts=0`, and exit status 0. Direct and public health returned `v2.4.1`; authenticated positions returned 200 over both routes; unauthenticated protected access returned 401. SQLite integrity/ownership remained correct, no systemd unit failed, no new service warning appeared, and the NPM routing regression passed.
- **macOS:** the original application is preserved at `/Users/linotsai/Lino/app_backups/Neckline-2.4.0-b1-20260814-083622-original.app`. The RC product was copied to `/Applications/Neckline.app`; version `2.4.1 (2)`, universal architectures, strict code signature, and executable hash equality with the RC were verified before launch. The replacement app launched successfully.
- **iOS:** signed Release archive `/tmp/neckline-v241-ios/Neckline-iOS-v2.4.1-b2.xcarchive` was produced with the existing development profile and passed strict code-sign verification. The archive reports version `2.4.1 (2)`. No iOS device was touched and no upload was performed, per user instruction.
- **Source publication:** after documentation and guard verification, the release is committed directly to `main`, annotated as `v2.4.1`, and pushed to `origin`. The pre-release rollback anchor is `b3e3d8189f040bc6826916be979d5ff3082b9d64`.

Production release status: **deployed and verified**. No unresolved P0/P1/P2 review finding remains.
