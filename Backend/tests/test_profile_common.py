"""⑫-B 画像面板装配单测(`neckline/profile/common.py`)。

覆盖:`confidence_for` 三档边界;`classify_entry_style` 四分类;`load_buy_contexts`
只读 `positions`/`entry_snapshots`/`position_plans`/`stock_basic` 拼面板正确
(含来源篮子命中/独立买入/计划未就绪三态、行业查无时不臆造)。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from neckline.db import connection, init_schema
from neckline.profile import common as pc
from tests.conftest import insert_stock_basic

pytestmark = pytest.mark.usefixtures("isolated_env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TestConfidenceFor:
    def test_below_min_is_low(self):
        assert pc.confidence_for(0) == pc.CONFIDENCE_LOW
        assert pc.confidence_for(pc.MIN_SAMPLE_N - 1) == pc.CONFIDENCE_LOW

    def test_between_min_and_medium_is_medium(self):
        assert pc.confidence_for(pc.MIN_SAMPLE_N) == pc.CONFIDENCE_MEDIUM
        assert pc.confidence_for(pc.MEDIUM_SAMPLE_N - 1) == pc.CONFIDENCE_MEDIUM

    def test_at_or_above_medium_is_high(self):
        assert pc.confidence_for(pc.MEDIUM_SAMPLE_N) == pc.CONFIDENCE_HIGH
        assert pc.confidence_for(pc.MEDIUM_SAMPLE_N * 10) == pc.CONFIDENCE_HIGH


class TestClassifyEntryStyle:
    def test_no_zone_is_no_reference(self):
        assert pc.classify_entry_style(10.0, None) == pc.ENTRY_STYLE_NO_REFERENCE
        assert pc.classify_entry_style(10.0, {}) == pc.ENTRY_STYLE_NO_REFERENCE
        assert pc.classify_entry_style(10.0, {"low": None, "high": 11}) == pc.ENTRY_STYLE_NO_REFERENCE

    def test_within_zone(self):
        assert pc.classify_entry_style(10.0, {"low": 9.5, "high": 10.5}) == pc.ENTRY_STYLE_WITHIN_ZONE
        # 边界值(含端点)也算区间内
        assert pc.classify_entry_style(9.5, {"low": 9.5, "high": 10.5}) == pc.ENTRY_STYLE_WITHIN_ZONE
        assert pc.classify_entry_style(10.5, {"low": 9.5, "high": 10.5}) == pc.ENTRY_STYLE_WITHIN_ZONE

    def test_chased_above(self):
        assert pc.classify_entry_style(11.0, {"low": 9.5, "high": 10.5}) == pc.ENTRY_STYLE_CHASED_ABOVE

    def test_below_zone(self):
        assert pc.classify_entry_style(9.0, {"low": 9.5, "high": 10.5}) == pc.ENTRY_STYLE_BELOW_ZONE


# ══════════════════════════════════════════════════════════════════════════
# load_buy_contexts:裸 SQL 铺三张表 + stock_basic
# ══════════════════════════════════════════════════════════════════════════

def _insert_position(
    db_path: Path, *, ts_code="600001.SH", buy_price=10.0, qty=1000, buy_date="20260710",
    status="closed", sell_price: Optional[float] = 11.0, sell_date: Optional[str] = "20260715",
    buy_fees: Optional[float] = 15.0, sell_fees: Optional[float] = 16.0,
) -> int:
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, sell_price, "
            "sell_date, buy_fees, sell_fees, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts_code, buy_price, qty, buy_date, status, sell_price, sell_date,
             buy_fees, sell_fees, now, now),
        )
        return int(cur.lastrowid)


def _insert_entry_snapshot(db_path: Path, position_id: int, ts_code: str, trade_date: str, *,
                           basket_id: Optional[int] = None, tier: Optional[int] = None,
                           role: Optional[str] = None) -> None:
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id, "
            "card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, ts_code, trade_date, basket_id, None, tier, role, "{}", now),
        )


def _insert_plan(db_path: Path, position_id: int, entry_zone: Optional[dict], *, version: int = 1) -> None:
    now = _now()
    plan = {"available": entry_zone is not None, "entry_zone": entry_zone}
    with connection(db_path) as conn:
        conn.execute(
            "INSERT INTO position_plans (position_id, version, source_basket_id, "
            "source_card_version, plan_json, note, created_at) VALUES (?,?,?,?,?,?,?)",
            (position_id, version, None, None, json.dumps(plan, ensure_ascii=False), None, now),
        )


class TestLoadBuyContexts:
    def test_window_filters_by_buy_date(self, isolated_env):
        env = isolated_env
        insert_stock_basic(env, [{"ts_code": "600001.SH", "industry": "半导体"}])
        _insert_position(env.db_path, ts_code="600001.SH", buy_date="20260710")
        _insert_position(env.db_path, ts_code="600001.SH", buy_date="20260801")   # 窗口外
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert len(ctx) == 1
        assert ctx[0].buy_date == "20260710"

    def test_empty_window_returns_empty_list(self, isolated_env):
        assert pc.load_buy_contexts("20260701", "20260731", db_path=isolated_env.db_path) == []

    def test_joins_industry_role_tier_and_entry_zone(self, isolated_env):
        env = isolated_env
        insert_stock_basic(env, [{"ts_code": "600001.SH", "industry": "半导体"}])
        pid = _insert_position(env.db_path, ts_code="600001.SH", buy_price=10.0)
        _insert_entry_snapshot(env.db_path, pid, "600001.SH", "20260710",
                               basket_id=7, tier=1, role="leader")
        _insert_plan(env.db_path, pid, {"low": 9.5, "high": 10.5})
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert len(ctx) == 1
        c = ctx[0]
        assert c.industry == "半导体" and c.theme_value == "半导体"
        assert c.basket_id == 7 and c.tier == 1 and c.tier_label == "T1"
        assert c.role == "leader" and c.role_value == "leader"
        assert c.entry_style == pc.ENTRY_STYLE_WITHIN_ZONE
        assert c.net_pnl == pytest.approx((11.0 - 10.0) * 1000 - 15.0 - 16.0)

    def test_independent_buy_has_no_basket_and_falls_back_gracefully(self, isolated_env):
        env = isolated_env
        pid = _insert_position(env.db_path, ts_code="600002.SH")
        # 没有 entry_snapshots / position_plans 行(独立买入的合法中间态)
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert len(ctx) == 1
        c = ctx[0]
        assert c.basket_id is None and c.tier is None and c.role is None
        assert c.role_value == pc.ROLE_INDEPENDENT
        assert c.tier_label == pc.TIER_INDEPENDENT
        assert c.entry_style == pc.ENTRY_STYLE_NO_REFERENCE
        assert c.industry is None and c.theme_value == "(未知行业)"

    def test_open_position_has_no_net_pnl(self, isolated_env):
        env = isolated_env
        _insert_position(env.db_path, ts_code="600001.SH", status="open",
                         sell_price=None, sell_date=None)
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert ctx[0].status == "open"
        assert ctx[0].net_pnl is None   # 未平仓不给一个会变的数字贴结论标签

    def test_missing_fees_default_to_zero_in_net_pnl(self, isolated_env):
        env = isolated_env
        _insert_position(env.db_path, ts_code="600001.SH", buy_price=10.0, qty=100,
                         sell_price=11.0, buy_fees=None, sell_fees=None)
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert ctx[0].net_pnl == pytest.approx((11.0 - 10.0) * 100)

    def test_deterministic_ordering(self, isolated_env):
        env = isolated_env
        _insert_position(env.db_path, ts_code="600002.SH", buy_date="20260712")
        _insert_position(env.db_path, ts_code="600001.SH", buy_date="20260710")
        ctx = pc.load_buy_contexts("20260701", "20260731", db_path=env.db_path)
        assert [c.buy_date for c in ctx] == ["20260710", "20260712"]
