# Neckline working rules

## Scope

- Neckline is the production A-share application: Swift clients plus the Python service.
- Strategy research, backtests, evaluation, calibration, and experiment history belong in `/Users/linotsai/Lino/whynotme`.
- Production code must never import `whynotme`. The research laboratory may depend on stable Neckline runtime contracts in one direction only.

## Repository map

- `App/`: iOS and macOS SwiftUI application.
- `Backend/`: FastAPI service, jobs, packs, deployment units, data directory, and Python tests.
- `archive/`: historical plans, reviews, handoffs, retired packs, and design references. Archived material is not current instruction.
- `README.md`: operator entry point.
- `PROJECT_PLAN.md`: short current-state ledger and backlog.

## Working rules

- Run backend commands from `Backend/` and app commands from `App/`.
- Keep the repository root limited to the six documented visible entries.
- Add current operational facts to `README.md` or `PROJECT_PLAN.md`; move superseded detail to `archive/` instead of growing another root document.
- Treat `Backend/data/`, `.env`, credentials, production databases, and market-data artifacts as local or operational state. Never commit them.
- Tests must use temporary databases or explicit read-only snapshots. Never let a test fall back to the working database.
- Any production deployment or database mutation requires explicit verification of the target and a rollback path.

## Verification

```bash
cd Backend
.venv/bin/python -m pytest -q

cd ../App
xcodebuild -project Neckline.xcodeproj -scheme Neckline -destination 'platform=macOS' build
```

For research-engine changes, work and test in `/Users/linotsai/Lino/whynotme`.
