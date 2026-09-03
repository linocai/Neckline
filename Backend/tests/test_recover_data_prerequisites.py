from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "recover_data_prerequisites.py"
    spec = importlib.util.spec_from_file_location("recover_data_prerequisites_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recovery_never_invokes_evening_chain(monkeypatch):
    mod = _module()
    day = date(2026, 8, 21)
    monkeypatch.setattr(mod, "official_is_trading_day", lambda _: True)
    monkeypatch.setattr(mod, "trading_days_between", lambda *_: [day])
    states = [False, True]
    seen_versions = []
    monkeypatch.setattr(
        mod.readiness,
        "preflight",
        lambda _, **kwargs: (
            seen_versions.append(kwargs.get("pack_version"))
            or type("R", (), {"ready": states.pop(0)})()
        ),
    )
    called = []
    monkeypatch.setattr(mod, "run_daily_update", lambda d: called.append(d) or 0)

    assert mod.main(["--through", "20260821"]) == 0
    assert called == [day]
    assert seen_versions == ["fp-4", "fp-4"]
    assert not hasattr(mod, "run_evening_chain")


def test_scheduled_recovery_checks_only_today(monkeypatch):
    mod = _module()
    today = date(2026, 8, 21)
    monkeypatch.setattr(mod, "official_is_trading_day", lambda _: True)
    monkeypatch.setattr(
        mod,
        "trading_days_between",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not scan history")),
    )
    seen = []
    monkeypatch.setattr(
        mod.readiness,
        "preflight",
        lambda day, **kwargs: (
            seen.append((day, kwargs.get("pack_version")))
            or type("R", (), {"ready": False})()
        ),
    )
    assert mod.recovery_days(today) == [today]
    assert seen == [(today, "fp-4")]


def test_closed_day_is_a_clean_noop(monkeypatch):
    mod = _module()
    monkeypatch.setattr(mod, "official_is_trading_day", lambda _: False)
    monkeypatch.setattr(
        mod.readiness,
        "preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not check")),
    )
    assert mod.recovery_days(date(2026, 8, 22)) == []


def test_sunday_scheduled_recovery_targets_the_immediately_preceding_friday():
    mod = _module()
    assert mod.scheduled_recovery_date(date(2026, 8, 16)) == date(2026, 8, 14)
    assert mod.scheduled_recovery_date(date(2026, 8, 17)) == date(2026, 8, 17)


def test_explicit_historical_recovery_scans_only_the_latest_sixty_trading_days(monkeypatch):
    mod = _module()
    days = [date(2026, 1, 1)] * 61
    monkeypatch.setattr(mod, "official_is_trading_day", lambda _: True)
    monkeypatch.setattr(mod, "trading_days_between", lambda *_: days)
    monkeypatch.setattr(
        mod.readiness,
        "preflight",
        lambda _, **kwargs: type("R", (), {"ready": kwargs["pack_version"] != "fp-4"})(),
    )
    assert len(mod.recovery_days(date(2026, 8, 21), start=date(2026, 1, 1))) == 60


def test_recovery_uses_the_selective_daily_retry(monkeypatch):
    mod = _module()
    captured = []

    class Result:
        returncode = 0

    monkeypatch.setattr(mod.subprocess, "run", lambda argv, **kwargs: captured.append((argv, kwargs)) or Result())
    assert mod.run_daily_update(date(2026, 8, 21)) == 0
    assert captured[0][0][-2:] == ["20260821", "--retry-incomplete"]
    assert captured[0][1] == {"check": False}
