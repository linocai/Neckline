"""引擎 API 兼容版本(plan §五 V2-③,单一源)。

策略包 manifest 必须声明它是为哪个 `engine_api_version` 编写的。引擎版本一旦发生
不兼容变化(原语签名改变、白名单收紧等),本文件的 `ENGINE_API_VERSION` 才跟着改
——**改这个数字是有意的破坏性信号**,不是随手加一。**不兼容 → 拒绝激活、fail
loud**(`scripts/activate_pack.py` 闸 2),不静默降级、不"尽量兼容"。

**V2.2.0 `1 → 2`(🔴 裁定,plan §五 ① 原文,理由写死)**:按 ③-K7 定下的判定
规则(「旧包原样重新校验仍通过 **且** `get_active_pack()` 对旧包行为逐位不变」→
才不 bump):第一条成立(schema 只加不减),**第二条不成立** ——
`get_active_pack()` 语义已改为取 `line_code='V'`(骨架线)现役行,`K7-pack-v1` /
`K4-pack-v1` 是 `LEGACY` 行,**不再会被返回**。故必须 bump。

**代价(如实登记,⛔ 不粉饰)**:`is_compatible()` 是逐位相等判据 →
`K4-pack-v1` / `K7-pack-v1` 两个回滚锚**当场作废**,任何激活尝试被闸 2 硬拒。
**这正是我们要的**:V2.2 是换心脏,那两个包在新引擎下**确实**不能用;让闸说
真话,好过留一条自己都不信的回滚绳。两行**留在表里不删**(留档 + 查证 + 历史
报告的 `pack_version` 指纹仍指得到)。⛔ **全项目自此不许再出现「回滚 = 激活
旧包」的写法** —— 回滚绳一律是「代码 commit 回滚 + DB 双备份还原」(plan §五
〇b-2 / ⑦ 写死)。
"""

from __future__ import annotations

from typing import Any, Mapping

# 单一源:任何要判断"包与引擎是否兼容"的代码都读这个常量,不在别处另定义一份。
# V2.2.0 起 = 2(1 → 2 的判定依据与代价见模块头,⛔ 不是随手加一)。
ENGINE_API_VERSION: int = 2


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
