"""Push delivery levels shared by the APNs sender."""

from __future__ import annotations

from neckline import notify_kinds as nk
from neckline.push import apns


def test_levels_and_categories_are_exactly_two():
    assert nk.LEVELS == ("important", "digest")
    assert nk.CATEGORY_OF_LEVEL == {
        "important": "NKIMPORTANT", "digest": "NKDIGEST",
    }
    assert nk.LEVEL_LABEL["important"] == "需要查看"
    assert apns.CATEGORY_IMPORTANT is nk.CATEGORY_IMPORTANT
    assert apns.CATEGORY_DIGEST is nk.CATEGORY_DIGEST
