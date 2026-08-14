# Neckline project plan

> Updated: 2026-08-14. This is the current control plane. Historical work is under `archive/`.

## 1. Current goal

Operate **v2.4.1 Build 2** as the released macOS-first selection workbench, then begin the v2.4.2 backend cycle. The completed executable record is [V2.4.1 execution record](archive/施工图/V2.4.1_执行计划_20260813.md).

## 2. Current state

- Production release line is `v2.4.1` / Build 2. The Backend was deployed and the local macOS application was replaced on 2026-08-14; public health reports `v2.4.1`.
- The signed iOS archive is ready for the user to install. Codex did not install to an iPhone/iPad, upload to App Store Connect, or notarize/distribute through a third party.
- Client: SwiftUI iOS and macOS in `App/`. Service: FastAPI in `Backend/`.
- Strategy research, calibration, evaluations, and backtests remain in `/Users/linotsai/Lino/whynotme`; Neckline never imports it.

## 3. V2.4.1 binding decisions

- macOS only. Keep iPhone/iPad structure and behavior intact unless a shared DTO must decode an additive field.
- Work directly on `main`; do not create a branch for this release.
- Scope is the selection workbench: its sidebar, basket/member detail, Today Market, auction report, and Today Intel.
- Sidebar = navigation, overview, and basket/member selection. The right pane = complete content. Its explicit destinations are **Today Market**, **Auction Report**, **a Basket**, and **Today Intel**.
- A default destination must be visibly selected; do not fall back to a basket without matching sidebar state.
- Every basket member is visible in a vertically scalable selector. Selecting one changes the detail below it.
- Remove the basket price scale bar. Keep the three price references in selected-member detail.
- Replace raw `industry_lift` with a human-comprehensible daily industry rank when the existing metrics provide it; raw lift is audit-only.
- No new LLM call, prompt pass, or client-side generation. Reuse existing fields with deterministic human fallbacks.
- User-facing default/detail layers must not expose internal keys, process stages, raw rule expressions, pack/engine versions, score internals, or project-document references. Audit data remains behind an explicit technical entry.
- The auction report is a right-pane report, not a modal. Its user body is conclusions, baskets, member performance, risks, and manual observations; data-quality machinery moves to a folded technical appendix.
- Today Intel is a right-pane destination, not a sidebar disclosure. It remains intelligence, not a stock-picking signal.
- Holdings receives only the shared market-status humanization. Review is out of scope.
- Release metadata is fixed at marketing version `2.4.1` and build number `2`; client and server marketing versions are synchronized. The subsequent production deployment was explicitly authorized on 2026-08-14.

## 4. Delivery sequence and gates

1. **Build:** implement the execution record in focused app and additive-contract slices; preserve iOS and all selection logic.
2. **Build verification:** run targeted unit/contract tests, macOS tests/build, backend suite, API smoke against a temporary/read-only target, and the screenshot matrix.
3. **Independent review:** compare the diff and running macOS UI to the binding decisions; record findings in `archive/review报告/REVIEW_V2.4.1_20260813.md`.
4. **Repair:** fix every confirmed blocking/high-severity review finding, then rerun affected tests and all release gates.
5. **Release-candidate gate:** apply version/build through the single release helper, regenerate Xcode project, build/archive locally, and verify the signed artifact.
6. **Release:** back up production and the installed app, deploy/restart/verify Backend, replace and launch the macOS app, produce the signed iOS archive without installing it, then commit/tag/push from `main`.

## 5. Architecture boundary

```text
Neckline/Backend -- production API and frozen snapshots --> App (SwiftUI)
Neckline/Backend -- read-only snapshot/artifact contract --> whynotme
```

- v2.4.1 may make only additive display-contract changes in Backend: human basket names in auction risks and labeled tag-absence data.
- No selection scoring, gate, data-processing, database schema, LLM-budget, or deployment change belongs in this release.
- Historical frozen payloads must continue to decode; client fallbacks must never show an internal key or code in the default layer.

## 6. Current backlog after v2.4.1

- **v2.4.2:** backend logic, data contracts, stability, and engineering improvements.
- **v2.4.3:** end-to-end product review and final consistency pass.
- Existing research backlog remains in the prior historical record; it is not active v2.4.1 scope.

## 7. Verification record

- RC metadata: `v2.4.1` / Build `2`, synchronized by `App/scripts/prepare_release_candidate.sh` across both `project.yml` marketing values, generated `pbxproj`, and Backend health version.
- Final gate: Backend `3982 passed` (temporary-test data only); macOS `230 passed, 10 skipped`; Debug and Release builds succeeded. Temporary-database API smoke passed (`DB_PATH=/tmp/neckline_smoke_*.db`) and returned `v2.4.1`; no production database was written.
- Backend deployment: production target `deploy@114.66.0.38:/opt/neckline`; service restarted at 2026-08-14 08:39 CST with a new PID, zero restart count, no failed units, no new journal warnings, SQLite `integrity_check=ok`, and successful direct/public authenticated API smoke. Public unauthenticated protected access correctly returned 401; the NPM routing regression baseline passed.
- Production rollback: source and retired weekly-unit backup `/opt/neckline-release-backups/v2.4.1-pre-20260814-083622/`; two verified database backups `/opt/neckline/data/neckline.db.bak-v241-20260814-083622` and `/opt/neckline/data/neckline.db.cpbak-v241-20260814-083622`. The obsolete `neckline-weekly.timer` was disabled and removed after its migrated research workflow was confirmed outside Neckline.
- macOS installation: `/Applications/Neckline.app` is signed, universal, version `2.4.1 (2)`, byte-matched to `/tmp/neckline-v241-rc.qrDQ1q/Neckline-v2.4.1-b2.xcarchive`, and launched successfully. The exact prior app is preserved at `/Users/linotsai/Lino/app_backups/Neckline-2.4.0-b1-20260814-083622-original.app`.
- iOS handoff: signed archive `/tmp/neckline-v241-ios/Neckline-iOS-v2.4.1-b2.xcarchive` (version `2.4.1 (2)`, strict code-sign verification passed). No iOS device was modified.
- Pre-release rollback anchor: `b3e3d8189f040bc6826916be979d5ff3082b9d64`. The release commit and annotated `v2.4.1` tag are the durable source release markers.
