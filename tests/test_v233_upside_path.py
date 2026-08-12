"""V2.3.3 批 ① 守门:卡 #6「次日强 / 平 / 弱三剧本」→「预期上涨路径」。

**这批为什么要一条全仓守门**:`upside_script` 是交易资格四件套第 1 件,而四件齐是
**T1 的必要条件**(`selection/tier.py::enforce_plan_completeness`)。三剧本直接删 =
每个篮子都缺件 = 系统再也出不了 T1,而且**编译不报错、单测也不一定红**
(`_upside_path_present()` 只是返回 `False`,一路静默降档)。所以这里把两件事钉成
机器判据:

  1. **老卡键 `scripts` 只准出现在三个声明过理由的兼容点**(冻结卡 `INSERT OR IGNORE`
     永不回填新键 → 今天开仓读的可能是昨天冻的 v3 卡);多出第四处 = 有人在写侧
     又长出了一个三剧本。
  2. **客户端一个 `scripts` 符号都不许剩**(服务端两键已停发,〇-6)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "neckline"
_CLIENT = _REPO / "client" / "Neckline"

#: 允许读老卡 `scripts` 键的**全部**位置 + 各自的理由(⛔ 加第四处必须连理由一起写)。
_SCRIPTS_COMPAT_SITES = {
    "neckline/selection/basket_card.py":
        "`_upside_path_present()` 的 OR 兜底:v3 老卡只有 scripts 三格,只读新键会让"
        "昨天冻的那批篮子今天开仓时全部『缺上涨判断』= 凭空多一条假警示。",
    "neckline/review/basket_review.py":
        "日复盘 ① 取文本那一步的老卡回退(`scripts[branch]`);"
        "`script_branch()` 的强/平/弱**机械分档**是「当天竞价实际落在哪一档」的描述,"
        "不是剧本,V2.3.3 一字未动。",
    "neckline/review/trade_clock.py":
        "③ 预期路径的三路 OR:`entry_plan_json` 是开仓当时冻住的历史快照,"
        "库里同时存在 upside_script / upside_path / scripts 三种形状。",
}


def _string_constants(path: Path):
    """文件里**非 docstring** 的字符串常量 `(行号, 值)`。

    抹掉 docstring 的理由同 `test_selection_basket_card._docstring_free`:禁令本身
    就写在注释与 docstring 里,裸文本 grep 会把「写明禁止」当成「违反禁止」。
    """
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def test_legacy_scripts_key_is_read_only_at_the_declared_compat_sites():
    found = {
        str(p.relative_to(_REPO))
        for p in sorted(_PKG.rglob("*.py"))
        for _lineno, value in _string_constants(p)
        if value in ("scripts", "scripts_unavailable_reason", "scriptsUnavailableReason")
    }
    assert found == set(_SCRIPTS_COMPAT_SITES), (
        "老卡 `scripts` 键的读取点变了。新增一处必须连**为什么这里非读老卡不可**一起写进 "
        f"_SCRIPTS_COMPAT_SITES;多出来的:{sorted(found - set(_SCRIPTS_COMPAT_SITES))};"
        f"消失的:{sorted(set(_SCRIPTS_COMPAT_SITES) - found)}"
    )


def test_no_module_reads_a_card_by_the_three_branch_keys():
    """⛔ 全仓零处再按 `strong`/`flat`/`weak` **三键成组**读卡。

    ⚠ 白名单只有 `review/basket_review.py` 那一处 —— 它的 `script_branch()` 出的是
    「当天竞价落在强/平/弱哪一档」的**机械分档**(阈值 `AUCTION_STRONG_GAP` /
    `AUCTION_WEAK_GAP`),是对**当天行情**的描述,与卡上那段文字无关,故 V2.3.3 一字未动。
    """
    offenders = []
    for p in sorted(_PKG.rglob("*.py")):
        rel = str(p.relative_to(_REPO))
        per_line: dict[int, set] = {}
        for lineno, value in _string_constants(p):
            if value in ("strong", "flat", "weak"):
                per_line.setdefault(lineno, set()).add(value)
        # 「三键成组」= 同一行里出现两个及以上分支名(三剧本的读法必然成组;
        # 而 `level == "strong"` / 涨跌平计数里的 `"flat"` 都是孤立出现)。
        grouped = [ln for ln, vals in per_line.items() if len(vals) >= 2]
        if grouped and rel != "neckline/review/basket_review.py":
            offenders.append((rel, grouped))
    assert offenders == [], f"这些地方还在按三剧本分支键读卡:{offenders}"


def test_client_has_no_scripts_symbol_left():
    """客户端 ⛔ 零 `scripts` / `BasketScripts` 符号(服务端两键已停发,〇-6)。

    ⚠ 只看**代码**:`//` 注释行里那句「BasketScripts 已删除」的留痕不算违规。
    """
    offenders = []
    for p in sorted(_CLIENT.rglob("*.swift")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if re.search(r"\bBasketScripts\b|\bscriptsUnavailableReason\b|\.scripts\b", line):
                offenders.append((str(p.relative_to(_REPO)), i, stripped[:80]))
    assert offenders == [], f"客户端还留着三剧本符号:{offenders}"


@pytest.mark.parametrize("piece,label", [("upside_script", "上涨判断(预期上涨路径)")])
def test_the_piece_code_string_itself_never_changed(piece, label):
    """🔴 〇-2:判据码 `upside_script` **字符串一字不改** —— 它已写进历史
    `position_plans.plan_json` 与 `trade_clock.entry_plan_json`,改了会让旧行**假装缺件**。
    换的只有中文标签。"""
    from neckline.selection import basket_card as bc

    assert bc.TRADE_PLAN_PIECES[0] == piece
    assert bc.TRADE_PLAN_PIECE_LABELS[piece] == label
