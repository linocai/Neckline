"""引擎 API 兼容版本单测(plan §五 V2-③)。"""

from __future__ import annotations

from neckline.selection import engine_api


def test_engine_api_version_is_2():
    """V2.2-① 反向守门:`ENGINE_API_VERSION == 2`(`get_active_pack()` 语义改为取
    骨架线现役行 → 判定规则「对旧包行为逐位不变」不成立 → 必须 bump;理由全文见
    `engine_api.py` 模块头)。⛔ 谁想改回 1 来"救"K4/K7 两个老包,先读那段代价
    登记 —— 老回滚绳是刻意剪断的。"""
    assert engine_api.ENGINE_API_VERSION == 2


def test_is_compatible_true_when_equal():
    assert engine_api.is_compatible({"engine_api_version": 2}) is True


def test_is_compatible_false_when_mismatched():
    assert engine_api.is_compatible({"engine_api_version": 1}) is False   # V2.2-① 反向守门原文
    assert engine_api.is_compatible({"engine_api_version": 3}) is False
    assert engine_api.is_compatible({"engine_api_version": 0}) is False


def test_is_compatible_false_when_missing_or_wrong_type():
    assert engine_api.is_compatible({}) is False
    assert engine_api.is_compatible({"engine_api_version": "2"}) is False   # 字符串"2"≠整数2
    assert engine_api.is_compatible({"engine_api_version": None}) is False


def test_is_compatible_rejects_bool_and_float_despite_numeric_equality():
    """Python 里 `2.0 == 2` 为 `True`(bool 同理是 int 子类)——裸 `==` 会让畸形
    manifest(`engine_api_version: 2.0` /布尔)意外判"兼容"。`is_compatible`
    显式排除这两类,只认真正的 `int`。"""
    assert engine_api.is_compatible({"engine_api_version": True}) is False
    assert engine_api.is_compatible({"engine_api_version": 2.0}) is False
