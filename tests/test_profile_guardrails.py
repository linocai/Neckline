"""⑫-B 守门:画像不得反向影响客观 Tier(plan §五 V2-⑫-B,蓝图 4.4 禁令)。

`neckline/selection/` 与 `neckline/scan/` 全目录 grep 零 `profile_` / `neckline.profile`
引用——这是**唯一方向**的强约束(画像可以读选股产出,选股绝不能读画像),体例照
`tests/test_scan_layer_guardrails.py` 的纯文本扫描(画像包尚小、无 docstring 大量
提及自身名字的风险,纯文本 grep 足够,不需要 AST)。

反方向(画像模块本身零写入 `baskets`/`basket_members`/`tier_history`/
`basket_cards`)已由全局既有测试 `tests/test_positions_entry.py::
test_positions_side_never_writes_to_basket_tables` 覆盖(它扫描全部
`neckline/*.py`,`neckline/profile/` 新增文件天然纳入,不必在本文件重复一份)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SELECTION_DIR = _PROJECT_ROOT / "neckline" / "selection"
_SCAN_DIR = _PROJECT_ROOT / "neckline" / "scan"

_BANNED_SUBSTRINGS = ("profile_", "neckline.profile")


def _py_files(d: Path):
    return sorted(d.rglob("*.py")) if d.exists() else []


@pytest.mark.parametrize("directory", [_SELECTION_DIR, _SCAN_DIR])
def test_selection_and_scan_never_reference_profile_package(directory: Path):
    files = _py_files(directory)
    assert files, f"{directory} 下没有找到任何 .py 文件,检查测试路径是否过期"
    hits = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for needle in _BANNED_SUBSTRINGS:
            if needle in text:
                hits.append((str(path.relative_to(_PROJECT_ROOT)), needle))
    assert not hits, f"选股/扫描层出现了对画像模块的引用(蓝图 4.4 禁令):{hits}"
