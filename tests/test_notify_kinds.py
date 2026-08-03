"""通知三级 × N kind 注册表守门(plan §五 V2-⑪-B / D5)。

这份文件就是 ⑪-B 点名要求的那条**结构守门单测**(把 V1 的「六类白名单」断言换成
「三级 × N kind」并重新锁死)。它的作用不是覆盖率,是**让"悄悄加一条推送"变得不
可能** —— 任何 kind 的增删都会打挂这里,必须过一次人眼(且按 plan 纪律需用户拍板)。
"""

from __future__ import annotations

import pytest

from neckline import notify_kinds as nk
from neckline.api import notify
from neckline.push import apns


def test_kind_whitelist_is_exactly_fourteen():
    """**精确集合**断言(不是 `>=`、不是子集):14 个 kind,一个不多一个不少。

    6 个来自 V1 六类迁移,1 个 `custom_alert`(⑪-C),4 个来自 ⑪-A 新增四监测,
    3 个来自 2026-08-03 用户拍板(`stop_approach`/`take_profit`/`sector_dive`,
    持仓哨兵既有事件升级立即级)。⚠ `basket_falsified` **刻意不在这里**:⑪-B 的
    例举里有它,但 ⑦-b / ⑧-C2 的语义红线写死「篮子 falsified ⛔ 不进推送」,两份
    权威冲突时本块取保守方向,已在 `notify_kinds` 模块头与完工记录登记,等裁定。"""
    assert set(nk.ALL_KINDS) == {
        "report_ready", "retreat", "precall", "d5exit", "circuit", "holding_alert",
        "custom_alert",
        "basket_peers_weak", "sector_bid_fade", "holding_decoupled", "market_shock",
        "stop_approach", "take_profit", "sector_dive",
    }
    assert len(nk.ALL_KINDS) == len(set(nk.ALL_KINDS)) == 14
    assert "basket_falsified" not in nk.ALL_KINDS


def test_every_kind_has_exactly_one_level_and_a_label():
    assert set(nk.LEVEL_OF_KIND) == set(nk.ALL_KINDS)
    assert set(nk.KIND_LABEL) == set(nk.ALL_KINDS)
    assert set(nk.LEVEL_OF_KIND.values()) <= set(nk.LEVELS)


def test_levels_are_exactly_three_with_d5_category_literals():
    assert nk.LEVELS == ("immediate", "important", "digest")
    assert nk.CATEGORY_OF_LEVEL == {
        "immediate": "NKIMMEDIATE", "important": "NKIMPORTANT", "digest": "NKDIGEST",
    }


def test_blueprint_level_assignment():
    """分级归属对照蓝图 5.5 —— 改归属会打挂这里(它不是实现细节,是产品决策)。"""
    assert nk.level_of(nk.KIND_CUSTOM_ALERT) == nk.LEVEL_IMMEDIATE   # 蓝图逐字:自定义价格条件
    assert nk.level_of(nk.KIND_RETREAT) == nk.LEVEL_IMMEDIATE        # 重大交易风险
    assert nk.level_of(nk.KIND_CIRCUIT) == nk.LEVEL_IMMEDIATE        # 重大交易风险
    assert nk.level_of(nk.KIND_REPORT_READY) == nk.LEVEL_DIGEST      # 盘后汇总
    for k in (nk.KIND_PRECALL, nk.KIND_D5EXIT, nk.KIND_HOLDING_ALERT,
              nk.KIND_BASKET_PEERS_WEAK, nk.KIND_SECTOR_BID_FADE,
              nk.KIND_HOLDING_DECOUPLED, nk.KIND_MARKET_SHOCK):
        assert nk.level_of(k) == nk.LEVEL_IMPORTANT


def test_holding_three_events_are_immediate():
    """2026-08-03 用户拍板:蓝图 5.5 逐字点名「逼近或触发止损」「快速跳水」——持仓
    哨兵既有三事件均为立即级(不是重要不紧急,不是盘后汇总)。"""
    for k in (nk.KIND_STOP_APPROACH, nk.KIND_TAKE_PROFIT, nk.KIND_SECTOR_DIVE):
        assert nk.level_of(k) == nk.LEVEL_IMMEDIATE
        assert nk.category_of(k) == nk.CATEGORY_IMMEDIATE
    # kind 串沿用 sentinel_events.event_key 既有字面量,不另拟新名。
    assert nk.KIND_STOP_APPROACH == "stop_approach"
    assert nk.KIND_TAKE_PROFIT == "take_profit"
    assert nk.KIND_SECTOR_DIVE == "sector_dive"


def test_four_new_monitors_all_present():
    """⑪-A 四监测必须各有一个 kind(缺一个 = 那条监测没法通知 = 白做)。"""
    for k in ("basket_peers_weak", "sector_bid_fade", "holding_decoupled", "market_shock"):
        assert k in nk.ALL_KINDS


def test_unregistered_kind_raises_not_defaults():
    """白名单不开后门:未登记 kind 抛 `ValueError`,**不给默认级别兜底**——
    有兜底的话任何拼错的串都会静默变成一条真推送。"""
    with pytest.raises(ValueError):
        nk.level_of("nope")
    with pytest.raises(ValueError):
        nk.category_of("")


def test_apns_aliases_match_single_source():
    """`push/apns.py` 只是别名,不是第二份定义(两处漂了就会两种 category)。"""
    assert apns.CATEGORY_IMMEDIATE is nk.CATEGORY_IMMEDIATE
    assert apns.CATEGORY_IMPORTANT is nk.CATEGORY_IMPORTANT
    assert apns.CATEGORY_DIGEST is nk.CATEGORY_DIGEST


def test_kinds_of_level_partitions_all_kinds():
    seen = []
    for lv in nk.LEVELS:
        seen.extend(nk.kinds_of_level(lv))
    assert sorted(seen) == sorted(nk.ALL_KINDS)      # 每个 kind 恰好落在一级里


def test_legacy_column_map_covers_the_six_v1_kinds():
    """V1 六列 → V2 kind 的迁移映射必须齐(缺一个 = 用户关过的那类推送被悄悄打开)。"""
    assert set(nk.LEGACY_COLUMN_OF_KIND) == {
        nk.KIND_REPORT_READY, nk.KIND_RETREAT, nk.KIND_PRECALL,
        nk.KIND_D5EXIT, nk.KIND_CIRCUIT, nk.KIND_HOLDING_ALERT,
    }
    assert set(nk.LEGACY_COLUMN_OF_KIND.values()) == {
        "push_report", "push_retreat", "push_precall",
        "push_d5exit", "push_circuit", "push_holding_alert",
    }


def test_notify_has_no_second_fanout_path():
    """`api/notify.py` 的扇出**只有 `push_event` 一条**:所有对外措辞函数的正文里都
    只调它,没有谁绕过 kind 闸门直接 `_fanout`。"""
    import ast
    import inspect

    src = inspect.getsource(notify)
    tree = ast.parse(src)
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "_fanout":
                    callers.add(node.name)
    assert callers == {"push_event"}, f"_fanout 只能由 push_event 调用,实际:{callers}"
