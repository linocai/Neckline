"""现行通知契约：只允许盘后报告与次日竞价核对。"""

from __future__ import annotations

import ast
import inspect

import pytest

from neckline import notify_kinds as nk
from neckline.api import notify
from neckline.push import apns


def test_whitelist_is_exactly_the_two_live_kinds():
    assert nk.ALL_KINDS == ("report_ready", "precall")
    assert set(nk.LEVEL_OF_KIND) == set(nk.ALL_KINDS)
    assert set(nk.KIND_LABEL) == set(nk.ALL_KINDS)


def test_levels_and_categories_are_exactly_two():
    assert nk.LEVELS == ("important", "digest")
    assert nk.CATEGORY_OF_LEVEL == {
        "important": "NKIMPORTANT", "digest": "NKDIGEST",
    }
    assert apns.CATEGORY_IMPORTANT is nk.CATEGORY_IMPORTANT
    assert apns.CATEGORY_DIGEST is nk.CATEGORY_DIGEST
    assert not hasattr(apns, "CATEGORY_IMMEDIATE")


def test_live_kind_assignment():
    assert nk.level_of(nk.KIND_REPORT_READY) == nk.LEVEL_DIGEST
    assert nk.level_of(nk.KIND_PRECALL) == nk.LEVEL_IMPORTANT


def test_unregistered_kind_raises_not_defaults():
    with pytest.raises(ValueError):
        nk.level_of("retreat")
    with pytest.raises(ValueError):
        nk.category_of("")


def test_kinds_of_level_partitions_all_kinds():
    seen = [kind for level in nk.LEVELS for kind in nk.kinds_of_level(level)]
    assert sorted(seen) == sorted(nk.ALL_KINDS)


def test_notify_has_no_second_fanout_path():
    tree = ast.parse(inspect.getsource(notify))
    callers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                   and sub.func.id == "_fanout" for sub in ast.walk(node)):
                callers.add(node.name)
    assert callers == {"push_event"}
