"""K10 local operational entrypoints never use hidden paths or unbounded retries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scripts import daily_update


ROOT = Path(__file__).resolve().parents[1]


def _prepare_daily(monkeypatch, *, calendar: bool | None, token: str | None = "configured"):
    monkeypatch.setattr(daily_update, "settings", replace(daily_update.settings, tushare_token=token))
    monkeypatch.setattr(daily_update, "ensure_data_dirs", lambda: None)
    monkeypatch.setattr(daily_update, "init_schema", lambda: None)
    monkeypatch.setattr(daily_update, "reset_cache", lambda: None)
    monkeypatch.setattr(daily_update, "official_is_trading_day", lambda _target: calendar)


def test_non_trading_day_is_a_noop_even_without_a_source_token(monkeypatch):
    _prepare_daily(monkeypatch, calendar=False, token=None)
    assert daily_update.main(["20260906"]) == 0


def test_missing_official_calendar_still_fails(monkeypatch):
    _prepare_daily(monkeypatch, calendar=None)
    assert daily_update.main(["20260907"]) == 1


def test_failed_market_partition_returns_nonzero_for_bounded_retry(monkeypatch):
    _prepare_daily(monkeypatch, calendar=True)
    monkeypatch.setattr(daily_update.backfill, "bootstrap_metadata", lambda: None)
    monkeypatch.setattr(daily_update, "_day_tables_for_run", lambda *_args, **_kwargs: ["daily"])
    monkeypatch.setattr(
        daily_update.backfill,
        "backfill_day_tables",
        lambda *_args, **_kwargs: {"daily": {"fetched": 0, "skipped": 0, "failed": 1, "rows": 0}},
    )
    assert daily_update.main(["20260907"]) == 1


def test_k10_units_share_the_deployed_root_and_explicit_runtime_paths():
    deploy = ROOT / "deploy"
    for name in ("neckline-k10-worker.service", "neckline-k10-evening.service", "neckline-k10-morning.service"):
        text = (deploy / name).read_text(encoding="utf-8")
        assert "User=neckline" in text and "Group=neckline" in text
        assert "WorkingDirectory=/opt/neckline" in text
        assert "EnvironmentFile=/opt/neckline/.env" in text
        assert "EnvironmentFile=/etc/neckline/k10.env" in text
    market = (deploy / "neckline-market-update.service").read_text(encoding="utf-8")
    retry = (deploy / "neckline-market-retry.service").read_text(encoding="utf-8")
    assert 'export DB_PATH="$K10_DB_PATH" PARQUET_DIR="$K10_PARQUET_DIR"' in market
    assert "--retry-incomplete" in retry
    retry_timer = (deploy / "neckline-market-retry.timer").read_text(encoding="utf-8")
    assert "19:30:00" in retry_timer and "20:30:00" in retry_timer


def test_cutover_units_do_not_backfill_morning_and_require_an_explicit_first_evening_start():
    deploy = ROOT / "deploy"
    evening = (deploy / "neckline-k10-evening.service").read_text(encoding="utf-8")
    morning_timer = (deploy / "neckline-k10-morning.timer").read_text(encoding="utf-8")
    api = (deploy / "neckline.service").read_text(encoding="utf-8")
    assert "K10_INITIAL_BOOTSTRAP_CUTOFF" in evening
    assert "--bootstrap-cutoff" in evening
    assert "source_bootstrap/not_configured" in evening
    assert "Persistent=false" in morning_timer
    assert "morning loop" not in api
    assert "FastAPI only" in api
