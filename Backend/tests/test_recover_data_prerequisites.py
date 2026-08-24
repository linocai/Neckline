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
    monkeypatch.setattr(mod.readiness, "preflight", lambda _: type("R", (), {"ready": states.pop(0)})())
    called = []
    monkeypatch.setattr(mod, "run_daily_update", lambda d: called.append(d) or 0)

    assert mod.main(["--through", "20260821"]) == 0
    assert called == [day]
    assert not hasattr(mod, "run_evening_chain")


def test_recovery_scans_only_the_latest_sixty_trading_days(monkeypatch):
    mod = _module()
    days = [date(2026, 1, 1)] * 61
    monkeypatch.setattr(mod, "official_is_trading_day", lambda _: True)
    monkeypatch.setattr(mod, "trading_days_between", lambda *_: days)
    monkeypatch.setattr(mod.readiness, "preflight", lambda _: type("R", (), {"ready": False})())
    assert len(mod.recovery_days(date(2026, 8, 21))) == 60
