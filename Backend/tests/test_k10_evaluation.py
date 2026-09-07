from __future__ import annotations

import pytest
from pathlib import Path
from threading import Event

from neckline.k10.evaluation import evaluate_company_window, evaluation_state
from neckline.k10.types import Task
from neckline.k10.worker import TaskContext


def _window(sample_class: str = "primary"):
    return {
        "companyWindowId": "window-1", "companyCode": "300001.SZ", "d1TradeDate": "20260908",
        "d2TradeDate": "20260909", "d2CloseAt": "2026-09-09T15:00:00+08:00", "sampleClass": sample_class,
        "selection": {"state": "selected", "actionIds": ["action-1"], "frozenAt": "2026-09-08T09:30:00+08:00"},
    }


def _fact(day: str, *, close: float, high: float, limit: float = 11.0, **extra):
    return {
        "factId": f"fact-{day}", "tradeDate": day, "revision": 1, "availability": "available", "open": 10.2,
        "high": high, "low": 9.9, "close": close, "preClose": 10.0, "limitUpPrice": limit,
        "sourceRefs": [{"source": "synthetic", "tradeDate": day}], **extra,
    }


def test_any_day_close_limit_is_primary_hit_and_first_touch_is_retained():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=10.8, high=10.9), _fact("20260909", close=11.0, high=11.0),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.primary_eligible is True
    assert result.close_limit_hit_any is True
    assert result.first_touch_day == "D2"
    assert result.d1_open_gap == pytest.approx(0.02)
    assert evaluation_state(result) == "completed"


def test_touch_without_close_and_overlapping_window_is_tracked_but_not_primary():
    result = evaluate_company_window(window=_window("overlap"), market_facts=[
        _fact("20260908", close=10.8, high=11.0), _fact("20260909", close=10.7, high=10.9),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.primary_eligible is False
    assert result.close_limit_hit_any is False
    assert result.first_touch_day == "D1"
    assert result.gaps == ()


def test_missing_or_sparse_limit_data_is_incomplete_not_a_false_non_hit():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=11.0, high=11.0, limit=None),
        {"tradeDate": "20260909", "revision": 1, "availability": "suspended", "sourceRefs": []},
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.primary_eligible is False
    assert result.close_limit_hit_any is None
    assert result.d1["closeLimitUp"] is None
    assert result.d2["availability"] == "suspended"
    assert {gap["reason"] for gap in result.gaps} == {"limit_data_unavailable", "suspended"}
    assert evaluation_state(result) == "incomplete"


def test_not_due_does_not_enter_primary_denominator_even_with_early_d1_seal():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=11.0, high=11.0), _fact("20260909", close=10.8, high=10.9),
    ], as_of="2026-09-09T10:00:00+08:00")
    assert result.close_limit_hit_any is True
    assert result.primary_eligible is False
    assert evaluation_state(result) == "pending"


def test_partial_d1_hit_and_unknown_first_touch_are_preserved_outside_denominator():
    result = evaluate_company_window(window=_window(), market_facts=[
        {"factId":"fact-d2","tradeDate":"20260909","revision":1,"availability":"available","open":10.2,"high":11.0,"low":9.9,"close":10.8,"preClose":10.0,"limitUpPrice":11.0,"sourceRefs":[]},
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.primary_eligible is False
    assert result.first_touch_day is None and result.first_touch_status == "unknown_due_to_d1"
    assert result.known_touch_days == ("D2",)


def test_anomalous_ohlc_and_ex_right_cross_day_change_are_not_silently_scored():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=10.8, high=11.2),
        _fact("20260909", close=10.7, high=10.9),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.d1["availability"] == "anomaly" and result.primary_eligible is False
    assert result.d1_open_gap is None
    assert all(value is None for value in result.d1_price_changes.values())
    clean = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=10.8, high=10.9, adjFactor=1.0), _fact("20260909", close=10.7, high=10.9, adjFactor=2.0),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert clean.d2_price_changes["close"] == pytest.approx((10.7 * 2 / 10.2) - 1)
    assert clean.comparability == "adjusted_comparable"
    assert clean.window_price_changes["high"] == pytest.approx(10.9 * 2 / 10.2 - 1)
    assert clean.window_price_changes["low"] == pytest.approx(9.9 / 10.2 - 1)


def test_field_check_conflict_is_anomaly_and_keeps_audit_without_deriving_metrics():
    conflicted = _fact("20260908", close=10.8, high=10.9, metadata={
        "anomalyReason": "cross_source_conflict:high",
        "fieldChecks": [{"field": "high", "state": "conflict", "reason": "same_day_post_close_sources_disagree",
                         "sourceValues": [{"source": "tushare.daily", "value": 10.9, "observedAt": "2026-09-08T16:00:00+08:00"},
                                          {"source": "realtime.sina", "value": 10.9, "observedAt": "2026-09-08T15:01:00+08:00"},
                                          {"source": "realtime.tencent", "value": 10.8, "observedAt": "2026-09-08T15:01:00+08:00"}]}],
    })
    result = evaluate_company_window(window=_window(), market_facts=[
        conflicted, _fact("20260909", close=11.0, high=11.0),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.d1["availability"] == "anomaly"
    assert result.d1["anomalyReason"] == "cross_source_conflict:high"
    assert result.d1["fieldChecks"][0]["state"] == "conflict"
    assert result.d1_price_changes == {"high": None, "low": None, "close": None}
    # The independently complete D2 hit remains observable, but the primary
    # denominator rejects the two-day record because D1 is anomalous.
    assert result.close_limit_hit_any is True and not result.primary_eligible


def test_two_day_price_changes_use_d1_open_not_prior_close():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", close=10.5, high=11.0),
        _fact("20260909", close=10.8, high=11.0, adjFactor=1.0),
    ], as_of="2026-09-09T16:00:00+08:00")
    # Without both factors, cross-day values are intentionally unknown.
    assert result.d1_price_changes == {"high": pytest.approx(11 / 10.2 - 1), "low": pytest.approx(9.9 / 10.2 - 1), "close": pytest.approx(10.5 / 10.2 - 1)}
    assert result.d2_price_changes["close"] is None and result.comparability == "unknown"


def test_runtime_persists_only_a_frozen_window_result(monkeypatch, tmp_path):
    from neckline.k10 import evaluation_runtime
    calls = []
    config = {"configVersion": "k10-v1.4", "universe": "chinext", "excludeBaijiu": True,
              "marketCollection": {"retryIntervalSeconds": 300, "retryUntilMinutesAfterClose": 120},
              "hardExclusions": {"approved": True, "board": "chinext", "priceLimit": "none", "st": "exclude", "swL2Exclusions": ["801125.SI"]},
              "modelRoutes": {}, "taskPolicies": {"evaluation": {"maxAttempts": 1, "costLimit": None}},
              "evaluationPolicy": {"version": "k10-evaluation-v1.4", "selectionFreeze": "d1_open_0930", "window": "d1_d2", "primaryMetric": "close_limit_up_any_d1_d2"}}
    monkeypatch.setattr(evaluation_runtime.store, "read_run_config", lambda **_: {"payload": config})
    monkeypatch.setattr(evaluation_runtime.store, "list_company_windows", lambda **_: [_window()])
    monkeypatch.setattr(evaluation_runtime.store, "latest_market_day_facts", lambda **_: [_fact("20260908", close=10.8, high=10.9), _fact("20260909", close=11.0, high=11.0)])
    monkeypatch.setattr(evaluation_runtime.store, "append_company_window_evaluation", lambda **kwargs: calls.append(kwargs) or 1)
    task = Task("eval-1", "evaluate_company_window", "running", 1, "worker", None, {"companyWindowId": "window-1", "companyCode": "300001.SZ", "d0TradeDate": "20260907", "d1TradeDate": "20260908", "d2TradeDate": "20260909", "configId": "cfg", "configRevision": 1})
    result = evaluation_runtime.evaluation_handler(TaskContext(task, {}, {}, "v", "2026-09-09T16:00:00+08:00", Path(tmp_path / "isolated.db"), Event()), clock=lambda: __import__("datetime").datetime.fromisoformat("2026-09-09T16:00:00+08:00"))
    assert result.status == "completed" and result.checkpoint["result"]["closeLimitHitAny"] is True
    assert calls[0]["company_window_id"] == "window-1"


def test_market_collection_rejects_future_or_unclosed_target_without_fetch(monkeypatch, tmp_path):
    from neckline.k10 import evaluation_runtime
    task = Task("market-1", "collect_market_day_fact", "running", 1, "worker", None, {"companyCode": "300001.SZ", "tradeDate": "2026-09-09"})
    monkeypatch.setattr(evaluation_runtime, "fetch_market_day_fact", lambda **_: (_ for _ in ()).throw(AssertionError("must not fetch")))
    context = TaskContext(task, {}, {}, "v", "2026-09-09T09:00:00+08:00", Path(tmp_path / "isolated.db"), Event())
    result = evaluation_runtime.market_day_fact_handler(context, clock=lambda: __import__("datetime").datetime.fromisoformat("2026-09-09T14:59:00+08:00"))
    assert result.status == "failed" and result.stage == "market_not_closed"


def test_future_close_is_not_visible_and_missing_price_reference_does_not_erase_limit_facts():
    facts = [_fact("20260908", close=10.8, high=10.9, preClose=None),
             _fact("20260909", close=11.0, high=11.0)]
    pending = evaluate_company_window(window=_window(), market_facts=facts,
                                     as_of="2026-09-09T14:59:00+08:00")
    assert pending.d2["close"] is None
    assert pending.close_limit_hit_any is None and not pending.primary_eligible
    complete = evaluate_company_window(window=_window(), market_facts=facts,
                                      as_of="2026-09-09T15:00:00+08:00")
    assert complete.primary_eligible and complete.close_limit_hit_any
    assert complete.d1_open_gap is None


def test_two_day_extremes_include_d2_and_high_precision_equal_factors():
    result = evaluate_company_window(window=_window(), market_facts=[
        _fact("20260908", open=10, close=10.5, high=11, adjFactor=1.23456789),
        _fact("20260909", open=10.5, close=11, high=12, low=9, limit=12, adjFactor=1.23456789),
    ], as_of="2026-09-09T16:00:00+08:00")
    assert result.comparability == "raw_comparable"
    assert result.d1_price_changes["close"] == pytest.approx(0.05)
    assert result.d2_price_changes["close"] == pytest.approx(0.10)
    assert result.window_price_changes["high"] == pytest.approx(0.20)
    assert result.window_price_changes["low"] == pytest.approx(-0.10)


def test_future_sessions_are_pending_not_source_gaps():
    from neckline.api.k10 import _market_day
    result = evaluate_company_window(window=_window(), market_facts=[],
                                     as_of="2026-09-07T21:00:00+08:00")
    assert evaluation_state(result) == "pending"
    assert result.gaps == ()
    assert _market_day(result.d1) is None and _market_day(result.d2) is None
