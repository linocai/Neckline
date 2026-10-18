"""推送分级。category 只描述怎么响，不编码投资动作。"""

from __future__ import annotations

from typing import Dict, Tuple

LEVEL_IMPORTANT = "important"
LEVEL_DIGEST = "digest"
LEVELS: Tuple[str, ...] = (LEVEL_IMPORTANT, LEVEL_DIGEST)

CATEGORY_IMPORTANT = "NKIMPORTANT"
CATEGORY_DIGEST = "NKDIGEST"
CATEGORY_OF_LEVEL: Dict[str, str] = {
    LEVEL_IMPORTANT: CATEGORY_IMPORTANT,
    LEVEL_DIGEST: CATEGORY_DIGEST,
}
LEVEL_LABEL: Dict[str, str] = {
    LEVEL_IMPORTANT: "需要查看",
    LEVEL_DIGEST: "信息汇总",
}


__all__ = [
    "LEVEL_IMPORTANT", "LEVEL_DIGEST", "LEVELS",
    "CATEGORY_IMPORTANT", "CATEGORY_DIGEST", "CATEGORY_OF_LEVEL", "LEVEL_LABEL",
]
