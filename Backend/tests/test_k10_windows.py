from datetime import date, datetime, timedelta

import pytest

from neckline.k10.windows import SHANGHAI, evening_window, morning_cutoff, morning_window


def test_evening_excludes_exactly_2100_and_morning_includes_both_boundaries():
    d0 = date(2026, 9, 6)
    d1 = date(2026, 9, 7)  # Monday follows the Sunday evening report.
    evening = evening_window(
        trading_day=d0,
        source_success_watermark=datetime(2026, 9, 4, 8, tzinfo=SHANGHAI),
    )
    morning = morning_window(observation_day=d1)

    at_2100 = datetime(2026, 9, 6, 21, tzinfo=SHANGHAI)
    at_0900 = datetime(2026, 9, 7, 9, tzinfo=SHANGHAI)
    assert evening.contains(at_2100) is False
    assert morning.contains(at_2100) is True
    assert morning.contains(at_0900) is True
    assert morning.contains(at_0900 + timedelta(microseconds=1)) is False


def test_morning_cutoff_is_fixed_even_if_task_runs_late():
    assert morning_cutoff(date(2026, 9, 7)) == datetime(2026, 9, 7, 9, tzinfo=SHANGHAI)


def test_trading_days_are_explicit_not_calendar_guessed():
    with pytest.raises(TypeError, match="date"):
        morning_window(observation_day=datetime(2026, 9, 7, 0, tzinfo=SHANGHAI))
    with pytest.raises(TypeError, match="date"):
        evening_window(trading_day=datetime(2026, 9, 4, 0, tzinfo=SHANGHAI), source_success_watermark=None)


def test_unknown_precision_never_gets_silently_assigned_to_a_window():
    window = morning_window(observation_day=date(2026, 9, 7))
    with pytest.raises(ValueError, match="带时区"):
        window.contains(datetime(2026, 9, 7, 8, 59))
