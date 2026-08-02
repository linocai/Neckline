"""引擎 API 兼容版本单测(plan §五 V2-③)。"""

from __future__ import annotations

from neckline.selection import engine_api


def test_engine_api_version_is_1():
    assert engine_api.ENGINE_API_VERSION == 1


def test_is_compatible_true_when_equal():
    assert engine_api.is_compatible({"engine_api_version": 1}) is True


def test_is_compatible_false_when_mismatched():
    assert engine_api.is_compatible({"engine_api_version": 2}) is False
    assert engine_api.is_compatible({"engine_api_version": 0}) is False


def test_is_compatible_false_when_missing_or_wrong_type():
    assert engine_api.is_compatible({}) is False
    assert engine_api.is_compatible({"engine_api_version": "1"}) is False   # 字符串"1"≠整数1
    assert engine_api.is_compatible({"engine_api_version": None}) is False


def test_is_compatible_rejects_bool_and_float_despite_numeric_equality():
    """Python 里 `True == 1` 与 `1.0 == 1` 均为 `True`——裸 `==` 会让畸形
    manifest(`engine_api_version: true` / `1.0`)意外判"兼容"。`is_compatible`
    显式排除这两类,只认真正的 `int`。"""
    assert engine_api.is_compatible({"engine_api_version": True}) is False
    assert engine_api.is_compatible({"engine_api_version": 1.0}) is False
