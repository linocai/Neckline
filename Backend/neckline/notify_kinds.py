"""K10 APNs notification kinds and their user-facing delivery levels."""

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

# APNs categories describe delivery behavior only.  They deliberately do not
# encode an investment action and remain usable by the low-level APNs sender.
KIND_K10_EVENING = "k10_evening"
KIND_K10_MORNING = "k10_morning"
KIND_K10_ANALYSIS = "k10_analysis"
KIND_K10_FAILURE = "k10_failure"
ALL_KINDS: Tuple[str, ...] = (
    KIND_K10_EVENING,
    KIND_K10_MORNING,
    KIND_K10_ANALYSIS,
    KIND_K10_FAILURE,
)
LEVEL_OF_KIND: Dict[str, str] = {
    KIND_K10_EVENING: LEVEL_DIGEST,
    KIND_K10_MORNING: LEVEL_IMPORTANT,
    KIND_K10_ANALYSIS: LEVEL_DIGEST,
    KIND_K10_FAILURE: LEVEL_IMPORTANT,
}
KIND_LABEL: Dict[str, str] = {
    KIND_K10_EVENING: "K10 晚间机会更新",
    KIND_K10_MORNING: "K10 晨间变化更新",
    KIND_K10_ANALYSIS: "K10 分析完成",
    KIND_K10_FAILURE: "K10 任务需要查看",
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
    "KIND_K10_EVENING", "KIND_K10_MORNING", "KIND_K10_ANALYSIS", "KIND_K10_FAILURE",
    "ALL_KINDS", "LEVEL_OF_KIND", "KIND_LABEL",
    "DEFAULT_ENABLED", "level_of", "category_of", "kinds_of_level",
]
