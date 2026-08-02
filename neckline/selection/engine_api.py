"""引擎 API 兼容版本(plan §五 V2-③,单一源)。

策略包 manifest 必须声明它是为哪个 `engine_api_version` 编写的。引擎版本一旦发生
不兼容变化(原语签名改变、白名单收紧等),本文件的 `ENGINE_API_VERSION` 才跟着改
——**改这个数字是有意的破坏性信号**,不是随手加一。**不兼容 → 拒绝激活、fail
loud**(`scripts/activate_pack.py` 闸 2),不静默降级、不"尽量兼容"。
"""

from __future__ import annotations

from typing import Any, Mapping

# 单一源:任何要判断"包与引擎是否兼容"的代码都读这个常量,不在别处另定义一份。
ENGINE_API_VERSION: int = 1


def is_compatible(manifest: Mapping[str, Any]) -> bool:
    """`manifest.engine_api_version` 必须与引擎现版本**逐位相等**——不做「>=/<=」
    宽容比较:引擎 API 版本表达的是"原语签名与白名单这一整套约定",版本之间没有
    "更兼容"的偏序关系,只有"是不是同一套"。**类型也必须是 `int`**(显式排除
    `bool`——Python 里 `True == 1`,裸 `==` 会让 `engine_api_version: true` 这种
    畸形 manifest 意外判"兼容";也排除 `float`,`1.0 == 1` 同款陷阱),不满足则
    视为不兼容。"""
    v = manifest.get("engine_api_version")
    return isinstance(v, int) and not isinstance(v, bool) and v == ENGINE_API_VERSION


__all__ = ["ENGINE_API_VERSION", "is_compatible"]
