"""哨兵事件防重台账单测(plan 阶段3 工程要求)。核心断言:① 同一 key 推过后
`already_pushed` 返回 True;② 落 SQLite(不是内存态)——新开一条独立连接(模拟
进程重启)依然能查到,验证「进程重启不重复推当日已推事件」。"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.sentinel.dedup import already_pushed, count_pushed_today, record_pushed

pytestmark = pytest.mark.usefixtures("isolated_env")

D = date(2026, 7, 20)


class TestAlreadyPushed:
    def test_false_before_any_push(self, isolated_env):
        assert already_pushed(D, "entry", "600519.SH", "trigger", db_path=isolated_env.db_path) is False

    def test_true_after_record(self, isolated_env):
        record_pushed(D, "entry", "600519.SH", "trigger", db_path=isolated_env.db_path)
        assert already_pushed(D, "entry", "600519.SH", "trigger", db_path=isolated_env.db_path) is True

    def test_survives_simulated_process_restart(self, isolated_env):
        """落 SQLite,不是进程内存态——重新用同一 db_path 建新连接查询(模拟哨兵
        脚本重启后重新扫描到同一事件),仍应命中。"""
        record_pushed(D, "retreat", "", "brake", db_path=isolated_env.db_path)
        # 不复用任何已打开的连接/缓存对象,纯粹传 db_path 重新查询
        assert already_pushed(D, "retreat", "", "brake", db_path=isolated_env.db_path) is True

    def test_different_event_key_is_independent(self, isolated_env):
        """持仓哨兵同票的三种事件互不抑制——推过 stop_approach 不影响 sector_dive
        仍能独立推送。"""
        record_pushed(D, "holding", "600519.SH", "stop_approach", db_path=isolated_env.db_path)
        assert already_pushed(D, "holding", "600519.SH", "stop_approach", db_path=isolated_env.db_path) is True
        assert already_pushed(D, "holding", "600519.SH", "sector_dive", db_path=isolated_env.db_path) is False

    def test_different_trade_date_is_independent(self, isolated_env):
        """跨日不去重——今天推过的事件,明天同样条件应该能再推一次(不是"曾经推过
        就永远不推了")。"""
        record_pushed(D, "entry", "600519.SH", "trigger", db_path=isolated_env.db_path)
        tomorrow = date(2026, 7, 21)
        assert already_pushed(tomorrow, "entry", "600519.SH", "trigger", db_path=isolated_env.db_path) is False

    def test_double_record_is_idempotent_not_error(self, isolated_env):
        """INSERT OR IGNORE——重复记同一事件不报错(哨兵主循环里"先判断再记"之间
        理论上不会真正并发,但防御性幂等总是更安全)。"""
        record_pushed(D, "invalidation", "600519.SH", "trigger", db_path=isolated_env.db_path)
        record_pushed(D, "invalidation", "600519.SH", "trigger", db_path=isolated_env.db_path)
        assert count_pushed_today(D, "invalidation", db_path=isolated_env.db_path) == 1


class TestCountPushedToday:
    def test_counts_across_sentinels_when_unfiltered(self, isolated_env):
        record_pushed(D, "entry", "A.SH", "trigger", db_path=isolated_env.db_path)
        record_pushed(D, "holding", "B.SH", "stop_approach", db_path=isolated_env.db_path)
        assert count_pushed_today(D, db_path=isolated_env.db_path) == 2
        assert count_pushed_today(D, "entry", db_path=isolated_env.db_path) == 1

    def test_zero_when_nothing_pushed(self, isolated_env):
        assert count_pushed_today(D, db_path=isolated_env.db_path) == 0
