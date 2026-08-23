"""现行通知白名单：只保留盘后报告与次日竞价核对表。"""

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
    LEVEL_IMPORTANT: "重要不紧急",
    LEVEL_DIGEST: "盘后汇总",
}

KIND_REPORT_READY = "report_ready"
KIND_PRECALL = "precall"
ALL_KINDS: Tuple[str, ...] = (KIND_REPORT_READY, KIND_PRECALL)
LEVEL_OF_KIND: Dict[str, str] = {
    KIND_REPORT_READY: LEVEL_DIGEST,
    KIND_PRECALL: LEVEL_IMPORTANT,
}
KIND_LABEL: Dict[str, str] = {
    KIND_REPORT_READY: "盘后报告就绪",
    KIND_PRECALL: "竞价核对表",
}
DEFAULT_ENABLED = True


def level_of(kind: str) -> str:
    try:
        return LEVEL_OF_KIND[kind]
    except KeyError:
        raise ValueError(f"未登记的通知 kind={kind!r};合法取值:{ALL_KINDS}") from None


def category_of(kind: str) -> str:
    return CATEGORY_OF_LEVEL[level_of(kind)]


def kinds_of_level(level: str) -> Tuple[str, ...]:
    return tuple(k for k in ALL_KINDS if LEVEL_OF_KIND[k] == level)


__all__ = [
    "LEVEL_IMPORTANT", "LEVEL_DIGEST", "LEVELS",
    "CATEGORY_IMPORTANT", "CATEGORY_DIGEST", "CATEGORY_OF_LEVEL", "LEVEL_LABEL",
    "KIND_REPORT_READY", "KIND_PRECALL", "ALL_KINDS", "LEVEL_OF_KIND", "KIND_LABEL",
    "DEFAULT_ENABLED", "level_of", "category_of", "kinds_of_level",
]
