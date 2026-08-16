# Project learnings

## [LRN-20260816-001] correction

**Logged**: 2026-08-16T17:58:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary

Sunday reports use the Sunday publication date as the report date; the preceding Friday is only the market-data
cutoff date.

### Details

The user’s request to use Friday’s close meant “read Friday’s EOD facts and weekend news,” not “publish a Friday-
dated report on Sunday.” Conflating these dates makes the report title, notification, and visible report identity
wrong even when all underlying market calculations correctly use Friday.

### Suggested Action

Carry two explicit dates through the evening/report boundary: `report_date` for title, notification, and visible
identity; `trade_date` for EOD reads, baskets, gates, cards, audit keys, and historical detail routes. On Sunday,
set `report_date=Sunday` and `trade_date=the immediately preceding Friday`.

### Metadata

- Source: user_feedback
- Related Files: Backend/scripts/evening.py, Backend/neckline/report/pipeline.py, Backend/neckline/report/render.py
- Tags: report-date, trade-date, sunday-schedule

### Resolution

- **Resolved**: 2026-08-16T18:04:00+08:00
- **Notes**: Implemented as an explicit report-date/trade-date contract in the report pipeline, API, push payload,
  macOS display, repository documentation, and regression tests.

---
