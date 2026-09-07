"""K10 notification kind contract shared by settings, outbox, and APNs."""

from __future__ import annotations

import pytest

from neckline import notify_kinds as nk
from neckline.push import apns


def test_whitelist_is_exactly_the_four_k10_kinds():
    assert nk.ALL_KINDS == ("k10_evening", "k10_morning", "k10_analysis", "k10_failure")
    assert set(nk.LEVEL_OF_KIND) == set(nk.ALL_KINDS)
    assert set(nk.KIND_LABEL) == set(nk.ALL_KINDS)


def test_levels_and_categories_are_exactly_two():
    assert nk.LEVELS == ("important", "digest")
    assert nk.CATEGORY_OF_LEVEL == {
        "important": "NKIMPORTANT", "digest": "NKDIGEST",
    }
    assert apns.CATEGORY_IMPORTANT is nk.CATEGORY_IMPORTANT
    assert apns.CATEGORY_DIGEST is nk.CATEGORY_DIGEST
def test_k10_kind_assignment_and_partition():
    assert nk.level_of(nk.KIND_K10_EVENING) == nk.LEVEL_DIGEST
    assert nk.level_of(nk.KIND_K10_MORNING) == nk.LEVEL_IMPORTANT
    assert nk.level_of(nk.KIND_K10_ANALYSIS) == nk.LEVEL_DIGEST
    assert nk.level_of(nk.KIND_K10_FAILURE) == nk.LEVEL_IMPORTANT
    seen = [kind for level in nk.LEVELS for kind in nk.kinds_of_level(level)]
    assert sorted(seen) == sorted(nk.ALL_KINDS)


def test_unregistered_kind_raises_not_defaults():
    with pytest.raises(ValueError):
        nk.level_of("retired-kind")
    with pytest.raises(ValueError):
        nk.category_of("")
