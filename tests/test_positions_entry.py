"""持仓开平仓系统自动记录编排层单测(PROJECT_PLAN §五 V2-⑩,`neckline.positions_entry`)。

覆盖:①`find_source_basket_member` 当日现役卡查找(命中/查无篮子/有篮子无卡三态);
②`build_inherited_plan`/`evaluate_entry_deviation` 计划继承与偏离提示纯函数;
③`record_buy`/`record_sell` 端到端编排(entry_snapshots 冻结 + position_plans
version=1 + user_actions 自动记账);④`create_position_plan_version` 新版本不改
原卡(单测锁死,⑩-B 验收);⑤守门:本模块对 `baskets`/`basket_members`/
`tier_history`/`basket_cards` 四张表零写入(AST 扫描);⑥CLI(`scripts/positions.py`)
与 API 共用同一份编排,行为一致。
"""

from __future__ import annotations

import ast
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from neckline import positions_entry as pe
from neckline import user_actions
from neckline.db import connection, init_schema
from neckline.sentinel import positions as pos_store
from tests.conftest import business_days, insert_trade_cal

pytestmark = pytest.mark.usefixtures("isolated_env")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _seed_basket(
    db_path,
    *,
    trade_date: str,
    ts_code: str = "600001.SH",
    with_card: bool = True,
    entry_zone: Optional[Dict[str, Any]] = None,
    max_chase: Optional[float] = 11.0,
    exit_reference: Optional[Dict[str, Any]] = None,
    tier: int = 1,
    is_primary: int = 1,
) -> int:
    """裸 SQL 铺一个 D0 篮子(+ 可选卡),模拟 ⑥/⑦ 已冻结的产物。返回 `basket_id`。"""
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier, "
            "pack_version, engine_api_version, charter_version, via, evidence_status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (trade_date, f"key-{trade_date}-{ts_code}", "示例篮子", "示例驱动", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", now),
        )
        basket_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech, role_conflict, "
            "reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (basket_id, ts_code, "leader", "leader", 0, "示例理由", is_primary, now),
        )
        if with_card:
            card_json = {
                "members": [{
                    "ts_code": ts_code,
                    "entry_zone": entry_zone if entry_zone is not None else {"low": 9.5, "high": 10.5, "why": "示例"},
                    "entry_zone_clamp": "ok",
                    "max_chase": max_chase,
                    "max_chase_clamp": "ok",
                    "exit_reference": exit_reference if exit_reference is not None else {"low": 12.0, "high": 13.0},
                    "exit_reference_clamp": "ok",
                    "industry": "半导体", "industry_lift": 1.8, "k4_tag": "H12",
                }],
                "verification_spec": {"require": ["close_at_or_above_ref"], "min_members_hit": 1},
                "invalidation_spec": {"any_of": ["close_below_stop_line"], "min_members_hit": 1},
                "risks": ["示例风险一", "示例风险二"],
            }
            conn.execute(
                "INSERT INTO basket_cards (basket_id, version, card_json, stop_pct, "
                "take_profit_retrace, charter_version, pack_version, engine_api_version, created_at) "
                "VALUES (?,1,?,?,?,?,?,?,?)",
                (basket_id, json.dumps(card_json, ensure_ascii=False), 0.05, 0.08, "v1.3.3",
                 "K4-pack-v1", 1, now),
            )
    return basket_id


@pytest.fixture
def calendar_days(isolated_env):
    """三个连续交易日:dates[0]=D0(篮子冻结日),dates[1]=D+1(买入日)。"""
    dates = business_days(date(2026, 8, 3), 5)
    insert_trade_cal(isolated_env, dates)
    return dates


# ══════════════════════════════════════════════════════════════════════════
# find_source_basket_member:三态(命中 / 查无 / 有篮子无卡)
# ══════════════════════════════════════════════════════════════════════════

class TestFindSourceBasketMember:
    def test_hits_basket_and_card(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        basket_id = _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))
        src = pe.find_source_basket_member("600001.SH", buy_date, db_path=isolated_env.db_path)
        assert src is not None
        assert src.basket_id == basket_id
        assert src.tier == 1
        assert src.role == "leader"
        assert src.card is not None
        assert src.member_entry["entry_zone"] == {"low": 9.5, "high": 10.5, "why": "示例"}

    def test_no_basket_returns_none(self, isolated_env, calendar_days):
        buy_date = calendar_days[1]
        assert pe.find_source_basket_member("600099.SH", buy_date, db_path=isolated_env.db_path) is None

    def test_basket_without_card_is_legal_intermediate_state(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"), with_card=False)
        src = pe.find_source_basket_member("600001.SH", buy_date, db_path=isolated_env.db_path)
        assert src is not None
        assert src.card is None and src.card_version is None

    def test_wrong_date_member_not_matched(self, isolated_env, calendar_days):
        """篮子冻结日不是 `buy_date` 的上一交易日 → 查无(不是"随便哪天都算")。"""
        wrong_day = calendar_days[2]   # 不是 buy_date 的上一交易日
        _seed_basket(isolated_env.db_path, trade_date=wrong_day.strftime("%Y%m%d"))
        buy_date = calendar_days[1]
        assert pe.find_source_basket_member("600001.SH", buy_date, db_path=isolated_env.db_path) is None


# ══════════════════════════════════════════════════════════════════════════
# build_inherited_plan / evaluate_entry_deviation(纯函数)
# ══════════════════════════════════════════════════════════════════════════

class TestBuildInheritedPlan:
    def test_no_source_is_honestly_empty(self):
        plan, basket_id, card_version = pe.build_inherited_plan(None)
        assert plan["available"] is False and plan["reason"] == "no_source_basket"
        assert basket_id is None and card_version is None
        assert plan["entry_zone"] is None and plan["verification_spec"] is None

    def test_card_not_ready_is_honestly_empty_but_keeps_basket_id(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"), with_card=False)
        src = pe.find_source_basket_member("600001.SH", buy_date, db_path=isolated_env.db_path)
        plan, basket_id, card_version = pe.build_inherited_plan(src)
        assert plan["available"] is False and plan["reason"] == "card_not_ready"
        assert basket_id == src.basket_id and card_version is None

    def test_full_card_populates_five_items(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))
        src = pe.find_source_basket_member("600001.SH", buy_date, db_path=isolated_env.db_path)
        plan, basket_id, card_version = pe.build_inherited_plan(src)
        assert plan["available"] is True
        assert plan["entry_zone"] == {"low": 9.5, "high": 10.5, "why": "示例"}
        assert plan["max_chase"] == 11.0
        assert plan["exit_reference"] == {"low": 12.0, "high": 13.0}
        assert plan["verification_spec"]["min_members_hit"] == 1
        assert plan["invalidation_spec"]["any_of"] == ["close_below_stop_line"]
        assert plan["risks"] == ["示例风险一", "示例风险二"]
        assert basket_id == src.basket_id and card_version == 1


class TestEvaluateEntryDeviation:
    def test_no_entry_zone_returns_none_not_false(self):
        assert pe.evaluate_entry_deviation(10.0, {"entry_zone": None}) is None

    def test_within_zone_no_notice(self):
        assert pe.evaluate_entry_deviation(10.0, {"entry_zone": {"low": 9.5, "high": 10.5}}) is None

    def test_exactly_on_boundary_no_notice(self):
        assert pe.evaluate_entry_deviation(10.5, {"entry_zone": {"low": 9.5, "high": 10.5}}) is None
        assert pe.evaluate_entry_deviation(9.5, {"entry_zone": {"low": 9.5, "high": 10.5}}) is None

    def test_below_zone_returns_notice(self):
        notice = pe.evaluate_entry_deviation(9.0, {"entry_zone": {"low": 9.5, "high": 10.5}})
        assert notice is not None and "盈亏结构已变" in notice

    def test_above_zone_returns_notice(self):
        notice = pe.evaluate_entry_deviation(15.0, {"entry_zone": {"low": 9.5, "high": 10.5}})
        assert notice is not None


# ══════════════════════════════════════════════════════════════════════════
# record_buy / record_sell:端到端编排
# ══════════════════════════════════════════════════════════════════════════

class TestRecordBuyWithSourceBasket:
    def test_freezes_snapshot_and_plan_within_zone_no_deviation(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        basket_id = _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))

        result = pe.record_buy("600001.SH", 10.0, 100, buy_date, db_path=isolated_env.db_path)
        assert result.position_id >= 1
        assert result.source_basket_key is not None
        assert result.tier == 1 and result.role == "leader"
        assert result.plan_available is True
        assert result.plan_deviation_notice is None

        # entry_snapshots 冻结一行
        with connection(isolated_env.db_path) as conn:
            row = conn.execute(
                "SELECT ts_code, trade_date, basket_id, card_version, tier, role, snapshot_json "
                "FROM entry_snapshots WHERE position_id=?", (result.position_id,),
            ).fetchone()
        assert row is not None
        assert row[0] == "600001.SH" and row[2] == basket_id and row[3] == 1
        assert row[4] == 1 and row[5] == "leader"
        snap = json.loads(row[6])
        assert snap["basket"]["found"] is True
        assert snap["basket"]["k4_tag"] == "H12"
        assert snap["buy_price"] == 10.0 and snap["qty"] == 100

        # position_plans version=1
        plan_row = pe.latest_position_plan(result.position_id, db_path=isolated_env.db_path)
        assert plan_row["version"] == 1
        assert plan_row["source_basket_id"] == basket_id
        assert plan_row["plan"]["available"] is True
        assert plan_row["plan"]["entry_zone"] == {"low": 9.5, "high": 10.5, "why": "示例"}

        # user_actions 自动落 buy
        actions = user_actions.list_actions(kind="buy", position_id=result.position_id, db_path=isolated_env.db_path)
        assert len(actions) == 1
        assert actions[0]["payload"]["buy_price"] == 10.0

    def test_deviation_notice_when_price_outside_entry_zone(self, isolated_env, calendar_days):
        d0, buy_date = calendar_days[0], calendar_days[1]
        _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))
        result = pe.record_buy("600001.SH", 15.0, 100, buy_date, db_path=isolated_env.db_path)
        assert result.plan_deviation_notice is not None
        assert "盈亏结构已变" in result.plan_deviation_notice


class TestRecordBuyIndependent:
    def test_no_source_basket_is_honestly_flagged(self, isolated_env, calendar_days):
        buy_date = calendar_days[1]
        result = pe.record_buy("600099.SH", 10.0, 100, buy_date, db_path=isolated_env.db_path)
        assert result.source_basket_key is None
        assert result.tier is None and result.role is None
        assert result.plan_available is False
        assert result.plan_deviation_notice is None   # 没法比较,不是"未偏离"

        with connection(isolated_env.db_path) as conn:
            row = conn.execute(
                "SELECT basket_id, card_version, tier, role, snapshot_json FROM entry_snapshots "
                "WHERE position_id=?", (result.position_id,),
            ).fetchone()
        assert row[0] is None and row[1] is None and row[2] is None and row[3] is None
        snap = json.loads(row[4])
        assert snap["basket"] == {"found": False, "reason": "no_matching_basket_member"}

        plan_row = pe.latest_position_plan(result.position_id, db_path=isolated_env.db_path)
        assert plan_row["plan"]["available"] is False
        assert plan_row["plan"]["reason"] == "no_source_basket"
        assert plan_row["source_basket_id"] is None

    def test_three_fields_only_still_succeeds(self, isolated_env, calendar_days):
        """⑩-A 验收:开仓只传三字段(code/price/qty,buy_date 走缺省今天的等价物
        ——这里显式传 calendar 里的交易日)即成功。"""
        buy_date = calendar_days[1]
        result = pe.record_buy("600001.SH", 8.88, 300, buy_date, db_path=isolated_env.db_path)
        assert result.position_id >= 1
        position = pos_store.get_position(result.position_id, db_path=isolated_env.db_path)
        assert position.buy_price == 8.88 and position.qty == 300


class TestCreatePositionPlanVersion:
    def test_new_version_does_not_touch_original_card_or_v1_plan(self, isolated_env, calendar_days):
        """⑩-B 验收硬要求:新版本不修改原始篮子卡(单测锁死)。"""
        d0, buy_date = calendar_days[0], calendar_days[1]
        basket_id = _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))
        with connection(isolated_env.db_path) as conn:
            card_before = conn.execute(
                "SELECT card_json FROM basket_cards WHERE basket_id=? AND version=1", (basket_id,)
            ).fetchone()[0]
            basket_before = dict(zip(
                ["id", "trade_date", "basket_key", "name", "driver", "driver_kind", "tier"],
                conn.execute(
                    "SELECT id, trade_date, basket_key, name, driver, driver_kind, tier "
                    "FROM baskets WHERE id=?", (basket_id,),
                ).fetchone(),
            ))

        result = pe.record_buy("600001.SH", 10.0, 100, buy_date, db_path=isolated_env.db_path)
        v1 = pe.latest_position_plan(result.position_id, db_path=isolated_env.db_path)
        assert v1["version"] == 1

        new_version_id = pe.create_position_plan_version(
            result.position_id, {"available": True, "note": "用户手改计划"},
            note="手动调整离场参考", db_path=isolated_env.db_path,
        )
        assert new_version_id >= 1

        plans = pe.list_position_plans(result.position_id, db_path=isolated_env.db_path)
        assert [p["version"] for p in plans] == [1, 2]
        assert plans[0]["plan"]["entry_zone"] == {"low": 9.5, "high": 10.5, "why": "示例"}   # v1 原样不变
        assert plans[1]["plan"] == {"available": True, "note": "用户手改计划"}
        assert plans[1]["note"] == "手动调整离场参考"
        assert plans[1]["source_basket_id"] == plans[0]["source_basket_id"]   # 承袭同一来源

        # 原始篮子卡与 baskets 行逐字节不变
        with connection(isolated_env.db_path) as conn:
            card_after = conn.execute(
                "SELECT card_json FROM basket_cards WHERE basket_id=? AND version=1", (basket_id,)
            ).fetchone()[0]
            basket_after = dict(zip(
                ["id", "trade_date", "basket_key", "name", "driver", "driver_kind", "tier"],
                conn.execute(
                    "SELECT id, trade_date, basket_key, name, driver, driver_kind, tier "
                    "FROM baskets WHERE id=?", (basket_id,),
                ).fetchone(),
            ))
        assert card_after == card_before
        assert basket_after == basket_before

    def test_missing_v1_raises(self, isolated_env):
        with pytest.raises(ValueError):
            pe.create_position_plan_version(9999, {"available": False}, db_path=isolated_env.db_path)


class TestRecordSell:
    def test_records_sell_user_action_and_closes_position(self, isolated_env, calendar_days):
        buy_date = calendar_days[1]
        result = pe.record_buy("600001.SH", 10.0, 100, buy_date, db_path=isolated_env.db_path)
        ok = pe.record_sell(
            result.position_id, 11.0, calendar_days[2],
            close_reason="TARGET_ZONE_REACHED", db_path=isolated_env.db_path,
        )
        assert ok is True
        position = pos_store.get_position(result.position_id, db_path=isolated_env.db_path)
        assert position.status == pos_store.STATUS_CLOSED
        assert position.close_reason == "TARGET_ZONE_REACHED"

        actions = user_actions.list_actions(kind="sell", position_id=result.position_id, db_path=isolated_env.db_path)
        assert len(actions) == 1
        assert actions[0]["payload"]["sell_price"] == 11.0
        assert actions[0]["payload"]["qty"] == 100
        assert actions[0]["payload"]["close_reason"] == "TARGET_ZONE_REACHED"

    def test_nonexistent_position_returns_false(self, isolated_env, calendar_days):
        assert pe.record_sell(9999, 10.0, calendar_days[1], db_path=isolated_env.db_path) is False


# ══════════════════════════════════════════════════════════════════════════
# 守门:本模块对篮子四表零写入(⑩-E 信息互通边界)
# ══════════════════════════════════════════════════════════════════════════

_POSITIONS_ENTRY_FILE = Path(__file__).resolve().parent.parent / "neckline" / "positions_entry.py"
_BASKET_TABLES = ("baskets", "basket_members", "tier_history", "basket_cards")
_EXEC_METHOD_NAMES = {"execute", "executemany", "executescript"}
_WRITE_VERBS = ("INSERT", "UPDATE", "DELETE")


def _sql_literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            parts.append(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "")
        return "".join(parts)
    return None


def test_positions_entry_never_writes_to_basket_tables():
    """⑩-E 守门:`neckline/positions_entry.py` 对 `baskets`/`basket_members`/
    `tier_history`/`basket_cards` 四张表零写入(只读——`find_source_basket_member`
    与 `basket_store.load_basket_card` 都是 SELECT)。AST 扫描而非纯文本 grep,
    避免被本文件大量说明性 docstring 提到的表名字面量误伤。"""
    tree = ast.parse(_POSITIONS_ENTRY_FILE.read_text(encoding="utf-8"), filename=str(_POSITIONS_ENTRY_FILE))
    hits: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
        if name not in _EXEC_METHOD_NAMES or not node.args:
            continue
        sql = _sql_literal(node.args[0])
        if sql is None:
            continue
        upper = sql.upper()
        for table in _BASKET_TABLES:
            for verb in _WRITE_VERBS:
                if f"{verb} {table.upper()}" in upper or f"{verb} INTO {table.upper()}" in upper or f"{verb} FROM {table.upper()}" in upper:
                    hits.append((node.lineno, sql[:80]))
    assert not hits, f"positions_entry.py 出现对篮子表的写入调用点(应零写入):{hits}"


# ══════════════════════════════════════════════════════════════════════════
# CLI 与 API 共用同一份编排(scripts/positions.py)
# ══════════════════════════════════════════════════════════════════════════

def test_cli_add_wires_through_positions_entry(isolated_env, monkeypatch, calendar_days):
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import positions as cli
    import neckline.db as db_mod

    monkeypatch.setattr(db_mod, "settings", isolated_env)
    d0, buy_date = calendar_days[0], calendar_days[1]
    basket_id = _seed_basket(isolated_env.db_path, trade_date=d0.strftime("%Y%m%d"))

    import argparse
    args = argparse.Namespace(
        ts_code="600001.SH", buy_price=10.0, qty=100, buy_date=buy_date.strftime("%Y%m%d"), note=None,
    )
    assert cli.cmd_add(args) == 0

    positions = pos_store.load_open_positions(db_path=isolated_env.db_path)
    assert len(positions) == 1
    with connection(isolated_env.db_path) as conn:
        row = conn.execute(
            "SELECT basket_id FROM entry_snapshots WHERE position_id=?", (positions[0].id,)
        ).fetchone()
    assert row is not None and row[0] == basket_id
