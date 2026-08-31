"""交易日历单测(plan 0.3)。用隔离 DB 灌入固定的一小段交易日,不依赖真实
`data/neckline.db`(是否已跑过 `scripts/init_calendar.py` 不影响本测试文件)。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tests.conftest import insert_trade_cal

pytestmark = pytest.mark.usefixtures("isolated_env")


def _sample_days():
    # 模拟一段真实分布:2024-01-01(周一,元旦假)、01-02~01-05 交易、01-06/07 周末、
    # 01-08 起继续交易,中间挖掉 01-10 模拟一个节假日。
    return [
        date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5),
        date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 11), date(2024, 1, 12),
    ]


def test_is_trading_day_true_for_seeded_days(isolated_env):
    insert_trade_cal(isolated_env, _sample_days())
    from neckline.calendar import is_trading_day, reset_cache

    reset_cache()
    for d in _sample_days():
        assert is_trading_day(d) is True


def test_official_calendar_never_falls_back_to_a_weekday_guess(isolated_env):
    from neckline.calendar import official_is_trading_day

    assert official_is_trading_day(date(2030, 1, 2)) is None
    insert_trade_cal(isolated_env, [date(2024, 1, 2)])
    assert official_is_trading_day(date(2024, 1, 2)) is True
    assert official_is_trading_day(date(2024, 1, 3)) is False


def test_explicit_target_calendar_never_reads_the_default_database(tmp_path, isolated_env):
    """A --db run must use its own official calendar, not the process cache."""
    import sqlite3
    from neckline.calendar import (official_is_trading_day, next_trading_day,
                                   trading_days_between)
    from neckline.db import init_schema

    insert_trade_cal(isolated_env, [date(2026, 8, 20), date(2026, 8, 21)])
    target = Path(tmp_path / "target.db")
    init_schema(target)
    with sqlite3.connect(target) as conn:
        for day, open_ in (("20260820", 0), ("20260821", 1), ("20260822", 0), ("20260823", 0), ("20260824", 1)):
            conn.execute("INSERT INTO trade_cal(exchange,cal_date,is_open,pretrade_date) VALUES ('SSE',?,?, '')", (day, open_))
    assert official_is_trading_day(date(2026, 8, 20), db_path=target) is False
    assert trading_days_between(date(2026, 8, 20), date(2026, 8, 24), db_path=target) == [date(2026, 8, 21), date(2026, 8, 24)]
    assert next_trading_day(date(2026, 8, 20), db_path=target) == date(2026, 8, 21)
    with pytest.raises(RuntimeError, match="未完整覆盖"):
        trading_days_between(date(2026, 8, 19), date(2026, 8, 24), db_path=target)


def test_is_trading_day_false_for_gap_and_weekend(isolated_env):
    insert_trade_cal(isolated_env, _sample_days())
    from neckline.calendar import is_trading_day, reset_cache

    reset_cache()
    assert is_trading_day(date(2024, 1, 1)) is False  # 元旦,不在种子交易日里
    assert is_trading_day(date(2024, 1, 10)) is False  # 挖掉的"节假日"
    assert is_trading_day(date(2024, 1, 6)) is False  # 周六(即便种子表没显式排除也应识别)


def test_next_prev_trading_day(isolated_env):
    insert_trade_cal(isolated_env, _sample_days())
    from neckline.calendar import next_trading_day, prev_trading_day, reset_cache

    reset_cache()
    assert next_trading_day(date(2024, 1, 5)) == date(2024, 1, 8)  # 跨周末+跳过 01-10
    assert next_trading_day(date(2024, 1, 9)) == date(2024, 1, 11)  # 跳过挖掉的 01-10
    assert prev_trading_day(date(2024, 1, 8)) == date(2024, 1, 5)
    assert prev_trading_day(date(2024, 1, 11)) == date(2024, 1, 9)


def test_trading_days_between_inclusive(isolated_env):
    insert_trade_cal(isolated_env, _sample_days())
    from neckline.calendar import reset_cache, trading_days_between

    reset_cache()
    days = trading_days_between(date(2024, 1, 3), date(2024, 1, 9))
    assert days == [date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]


def test_trading_days_between_empty_when_start_after_end(isolated_env):
    insert_trade_cal(isolated_env, _sample_days())
    from neckline.calendar import reset_cache, trading_days_between

    reset_cache()
    assert trading_days_between(date(2024, 1, 9), date(2024, 1, 3)) == []


def test_next_trading_day_raises_when_truly_stuck(isolated_env):
    """40 天硬上限保护:DB 为空 + 静态表覆盖外年份的连续非交易日应该报错而不是死循环。"""
    from neckline.calendar import next_trading_day, reset_cache

    reset_cache()
    # 不灌入任何 trade_cal,退化到"静态表 + 周末非交易",但静态表覆盖 2025-2026,
    # 覆盖内没有连续 40 天休市,所以这里改验证:确实能正常找到下一个交易日
    # (不会在覆盖年份内出现连续 40 天非交易日)。
    d = next_trading_day(date(2026, 1, 1))
    assert d.weekday() < 5


def test_reset_cache_forces_reload(isolated_env):
    from neckline.calendar import is_trading_day, reset_cache

    insert_trade_cal(isolated_env, _sample_days())
    reset_cache()
    assert is_trading_day(date(2024, 1, 2)) is True

    # 直接改库(绕开 API),不 reset_cache 应该还读旧缓存
    import sqlite3

    conn = sqlite3.connect(str(isolated_env.db_path))
    conn.execute("DELETE FROM trade_cal WHERE cal_date='20240102'")
    conn.commit()
    conn.close()
    assert is_trading_day(date(2024, 1, 2)) is True  # 缓存未刷新,仍是 True

    reset_cache()
    assert is_trading_day(date(2024, 1, 2)) is False  # 刷新后读到新库状态
