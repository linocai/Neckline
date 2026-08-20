"""守门单测共用的**源码扫描器**(V2.5.0 S8/S9/S10)。

⛔ 不是测试文件(不以 `test_` 开头,pytest 不收集)。

**为什么单起一个文件**:S8 与 S9/S10 两组守门都要「这个词不许出现在这个包里」这类
判据,而扫描器本身写错的方式很隐蔽 —— 一个扫不到东西的闸门等于没有闸门,
而它永远是绿的。两份各自漂移的扫描器比一份写错的更糟,所以只留一份,
并且每个判据都自带一条**自检**(先断言它扫得到该扫到的东西)。

🔴 **两类判据,别混用**:

    `code_without_docstrings()`  —— 文本判据。扫「源码里不许出现这个字符串」。
        ⚠ 必须先掐掉 docstring 与注释:一条纪律总要**写出**它禁止的那个词才解释
        得清,把说明算进命中会逼着后来者删注释去凑绿 —— 那正好是反的。

    `attribute_reads()` / `subscript_keys()` —— **AST 判据**。扫「这段代码有没有
        真的去**读**那个字段」。⚠ 一个装着 `("rank", "score")` 的**常量元组**
        (比如字段黑名单本身)在文本判据下会误伤,在 AST 判据下不会 ——
        「声明了什么不许有」与「真的去读了它」是两件事。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


def imports(path: Path) -> Set[str]:
    """一个文件 import 了哪些模块(含 `from x import y` 的 `x.y`)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def imports_any(path: Path, prefix: str) -> List[str]:
    """这个文件 import 了 `prefix` 或它的子模块吗(返回命中的模块名)。"""
    return [m for m in sorted(imports(path))
            if m == prefix or m.startswith(prefix + ".")]


def _docstring_lines(tree: ast.AST) -> Set[int]:
    """所有 docstring 占的行号(1 起,闭区间展开)。

    ⚠ 走**行号**而不是 `ast.get_docstring()` 的返回值去替换文本:后者是**清洗过**
    的(去缩进、去首尾空白),对缩进的函数 docstring 根本匹配不上原文 ——
    那会让扫描器悄悄少掐掉一段,而它不会报错,只会误伤。
    """
    out: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            start = first.lineno
            end = getattr(first, "end_lineno", start) or start
            out.update(range(start, end + 1))
    return out


def code_without_docstrings(path: Path) -> str:
    """源码去掉 docstring 与整行注释后的正文。"""
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    skip = _docstring_lines(tree)
    kept = [
        ln for i, ln in enumerate(src.splitlines(), start=1)
        if i not in skip and not ln.lstrip().startswith("#")
    ]
    return "\n".join(kept)


def attribute_reads(path: Path) -> Set[str]:
    """这个文件**读**过哪些属性名(`x.foo` 里的 `foo`)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def subscript_keys(path: Path) -> Set[str]:
    """这个文件用**字符串常量**下标取过哪些键(`x["foo"]` 里的 `foo`)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: Set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) \
                and isinstance(n.slice.value, str):
            out.add(n.slice.value)
    return out


def touched_names(path: Path) -> Set[str]:
    """**读过的属性 ∪ 用过的字符串键**。

    这就是「这段代码有没有真的去碰那个字段」的判据 —— 一个只是被列进黑名单常量的
    名字不会命中(那是**声明**,不是**读取**)。
    """
    return attribute_reads(path) | subscript_keys(path)


def touched_any(paths: Iterable[Path], banned: Iterable[str]) -> List[str]:
    """哪些文件真的碰了黑名单里的名字(返回 `文件名 → 名字` 的可读清单)。"""
    bad = set(banned)
    hits: List[str] = []
    for p in sorted(paths):
        for name in sorted(touched_names(p) & bad):
            hits.append(f"{p.name} → {name}")
    return hits


__all__ = [
    "imports", "imports_any", "code_without_docstrings",
    "attribute_reads", "subscript_keys", "touched_names", "touched_any",
]
