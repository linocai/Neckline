"""守门单测共用的**源码扫描器**(V2.5.0 S8/S9/S10 起;S15 收敛为**唯一一份**)。

⛔ 不是测试文件(不以 `test_` 开头,pytest 不收集)。它自己的自检在
`tests/test_v250_scanner_guard.py` —— **一个扫不到东西的闸门等于没有闸门,
而它永远是绿的**。

🔴 **本文件是 import 判据的唯一实现**。2026-08-21 的三路复审查到:S1/S3/S4/S5/S6
五份守门各抄了一份 `_imported_modules()`,其中四份写着

    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:

—— `not node.level` 把**相对 import 整类跳过**,于是 `from ..llm import factory`
一行就能穿过 G2/G3/G4/G5/G7/G18/G19/G21 全部八条「结构性锁死」的边界,且穿过去之后
测试是绿的。第五份(S1)收了相对 import 但收成 `"..sentinel"` 这种原样字符串,
与前缀 `neckline.sentinel` 同样对不上,同样零命中。
本文件把相对 import **解析成绝对模块名**再交出去,五份抄本已全部删除、改 import 这里。

🔴 **两类判据,别混用**:

    `code_without_docstrings()`  —— 文本判据。扫「源码里不许出现这个字符串」。
        ⚠ 必须先掐掉 docstring 与注释:一条纪律总要**写出**它禁止的那个词才解释
        得清,把说明算进命中会逼着后来者删注释去凑绿 —— 那正好是反的。

    `attribute_reads()` / `subscript_keys()` —— **AST 判据**。扫「这段代码有没有
        真的去**读**那个字段」。⚠ 一个装着 `("rank", "score")` 的**常量元组**
        (比如字段黑名单本身)在文本判据下会误伤,在 AST 判据下不会 ——
        「声明了什么不许有」与「真的去读了它」是两件事。

    `imports()` —— **import 判据**。扫「这个文件依赖了谁」。它收三类写法:
        绝对 import、**相对 import(解析成绝对名)**、**动态 import**
        (`importlib.import_module("x")` / `__import__("x")`,含字面量拼接)。
        ⚠ 模块名不是字面量的动态 import 谁也扫不到 —— 那种写法由
        `opaque_dynamic_imports()` 单独报出来,并由一条全仓守门断言它为空。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

#: 动态 import 的两个入口。`importlib.import_module` 与 `import_module` 裸名调用
#: 都算(`from importlib import import_module` 是常见写法)。
#:
#: ⚠ **这是一份黑名单**,会漏 `imp` / `pkgutil.resolve_name` / `runpy.run_module` /
#: `exec("import x")` 这些别的路子。⛔ 别指望把它补全 —— Python 变出一个模块的方法
#: 数不清。真正兜住这一类的是白名单那条:
#: `test_v250_scanner_guard.py::test_the_only_way_to_reach_a_module_is_an_import_statement`
#: —— 「`neckline/**` 与 `scripts/**` 里取模块的唯一合法写法是 `import` 语句」,
#: 动态 import 机制**整类**不许出现。本表留着,是为了在**别人的**扫描域(比如
#: `tests/`)里也能看见那两种最常见的写法。
_DYNAMIC_IMPORT_FUNCS = {"import_module", "__import__"}


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - 仓里有语法错本身就是硬故障
        raise AssertionError(f"{path} 语法错误:{exc}") from exc


# ══════════════════════════════════════════════════════════════════════════
# 模块路径解析:相对 import 必须先变成绝对模块名,判据才够得着它
# ══════════════════════════════════════════════════════════════════════════

def module_parts(path: Path) -> List[str]:
    """一个 `.py` 文件的**绝对模块路径**分段。

    走「一路往上,只要目录里还有 `__init__.py` 就算在包里」—— 这与 Python 自己
    确定包边界的方式一致,⛔ 不写死 `Backend/` 这种路径常量(写死了,哪天目录一挪
    扫描器就静默解析错,而它不会报错)。

    · `neckline/k9/ranking.py`      → `['neckline', 'k9', 'ranking']`
    · `neckline/search/__init__.py` → `['neckline', 'search']`(包自己)
    · `scripts/evening.py`          → `['evening']`(`scripts/` 不是包)
    """
    path = path.resolve()
    parts: List[str] = [] if path.stem == "__init__" else [path.stem]
    d = path.parent
    while (d / "__init__.py").exists():
        parts.append(d.name)
        d = d.parent
    parts.reverse()
    return parts


def package_parts(path: Path) -> List[str]:
    """该文件所在**包**的分段(相对 import 的 `level=1` 落点)。"""
    parts = module_parts(path)
    if path.resolve().stem == "__init__":
        return parts                     # `__init__.py` 自己就是那个包
    return parts[:-1]


def resolve_relative(path: Path, level: int, module: Optional[str]) -> Optional[str]:
    """把 `from ..llm import factory` 里的 `..llm` 解析成 `neckline.llm`。

    `level` 超出包深度(`from ... import` 写在只有两层包的模块里)= 这行 import
    在运行期本来就会炸,解析不出来 —— 返回 `None`,由调用方原样留痕,
    ⛔ 不静默丢弃(静默丢弃正是这个扫描器过去八条边界一起失明的原因)。
    """
    pkg = package_parts(path)
    if level > len(pkg):
        return None
    base = pkg[:len(pkg) - level + 1]
    if module:
        base = base + module.split(".")
    return ".".join(base) if base else None


def literal_str(node: ast.AST) -> Optional[str]:
    """能在**不执行代码**的前提下算出来的字符串常量,否则 `None`。

    收字面量、`'why' + 'notme'` 这类常量拼接、以及全常量的 f-string ——
    这三种正是「用一点小花招把模块名藏起来」最省事的写法。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = literal_str(node.left)
        right = literal_str(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.JoinedStr):
        out: List[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.append(value.value)
            elif (isinstance(value, ast.FormattedValue) and value.conversion in (-1, None)
                    and value.format_spec is None):
                # `f"why{'not'}me"` —— 插值位塞的还是个常量,照样算得出来。
                inner = literal_str(value.value)
                if inner is None:
                    return None
                out.append(inner)
            else:
                return None
        return "".join(out)
    return None


def _dynamic_import_arg(node: ast.Call) -> Optional[ast.AST]:
    """这是不是一次动态 import;是的话返回**模块名那个实参**的 AST 节点。"""
    func = node.func
    if isinstance(func, ast.Attribute):
        name = func.attr
    elif isinstance(func, ast.Name):
        name = func.id
    else:
        return None
    if name not in _DYNAMIC_IMPORT_FUNCS or not node.args:
        return None
    return node.args[0]


def _from_import_names(path: Path, node: ast.ImportFrom) -> Set[str]:
    if node.level:
        base = resolve_relative(path, node.level, node.module)
        if base is None:
            # 解析不出来的相对 import ⛔ 不许被静默丢掉;原样留痕,
            # 并由 `unresolvable_relative_imports()` + 一条全仓守门当场点名。
            return {"." * node.level + (node.module or "")}
    else:
        base = node.module
    if not base:
        return set()
    out = {base}
    out.update(f"{base}.{a.name}" for a in node.names)
    return out


def imports(path: Path) -> Set[str]:
    """一个文件依赖了哪些模块。

    含 `from x import y` 的 `x` 与 `x.y`、**相对 import(已解析成绝对名)**、
    以及**动态 import 的字面量目标**。
    """
    tree = _parse(path)
    out: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            out |= _from_import_names(path, node)
        elif isinstance(node, ast.Call):
            arg = _dynamic_import_arg(node)
            if arg is None:
                continue
            target = literal_str(arg)
            if target is None:
                continue                 # 不透明 —— 由下面那个函数单独报
            if target.startswith("."):
                stripped = target.lstrip(".")
                level = len(target) - len(stripped)
                resolved = resolve_relative(path, level, stripped or None)
                out.add(resolved or target)
            else:
                out.add(target)
    return out


def unresolvable_relative_imports(path: Path) -> List[str]:
    """`level` 超出包深度、解析不成绝对名的相对 import(`文件:行` 清单)。

    这类写法在运行期本来就会 `ImportError`,但更要紧的是:**扫描器够不着它**,
    于是所有 import 型判据对它失明。一条全仓守门断言它恒空。
    """
    out: List[str] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, ast.ImportFrom) and node.level:
            if resolve_relative(path, node.level, node.module) is None:
                out.append(f"{path.name}:{node.lineno} "
                           f"{'.' * node.level}{node.module or ''}")
    return out


def opaque_dynamic_imports(path: Path) -> List[str]:
    """模块名**不是字面量**的动态 import(`文件:行` 清单)。

    `importlib.import_module(name)` 里的 `name` 只有运行期才知道 —— 任何静态判据
    都够不着它。一条全仓守门断言它恒空:要动态 import,模块名就得写成能被看见的
    常量(拼接也行,`literal_str()` 算得出来)。
    """
    out: List[str] = []
    for node in ast.walk(_parse(path)):
        if not isinstance(node, ast.Call):
            continue
        arg = _dynamic_import_arg(node)
        if arg is not None and literal_str(arg) is None:
            out.append(f"{path.name}:{node.lineno} 动态 import 的模块名不是字面量")
    return out


def imports_any(path: Path, prefix: str) -> List[str]:
    """这个文件 import 了 `prefix` 或它的子模块吗(返回命中的模块名)。"""
    return [m for m in sorted(imports(path))
            if m == prefix or m.startswith(prefix + ".")]


def import_hits(paths: Iterable[Path], prefixes: Iterable[str],
                root: Optional[Path] = None) -> List[str]:
    """哪些文件 import 了这批前缀里的任何一个(返回 `文件 → 模块` 的可读清单)。

    这是五份抄本各自 `_hits()` 的**唯一**实现。`root` 只影响报错文案里的路径写法。
    """
    wanted = tuple(prefixes)
    hits: List[str] = []
    for path in sorted(paths):
        label = str(path.relative_to(root)) if root else path.name
        for mod in sorted(imports(path)):
            if any(mod == p or mod.startswith(p + ".") for p in wanted):
                hits.append(f"{label} → {mod}")
    return hits


# ══════════════════════════════════════════════════════════════════════════
# 文本判据 / AST 字段判据
# ══════════════════════════════════════════════════════════════════════════

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


def string_constants(path: Path) -> List[Tuple[int, str]]:
    """AST 里所有**非 docstring** 的字符串常量 `(行号, 值)`。

    这是「源码里不许出现这句 SQL」这类判据的**正确形状**:
      · 注释天然不在 AST 里 —— ⛔ 不会因为有人写了 `# ⛔ 不许 DELETE FROM baskets`
        就把这条纪律自己判红;
      · docstring 显式排除 —— 同一条理由;
      · **相邻字面量已被解析器折成一个常量**,跨行拼的
        `"INSERT OR IGNORE INTO x "  "(a,b) VALUES (?,?)"` 不会被切碎(而按行 grep
        的判据会:它只看得见前半句,后半句里的表名就丢了)。
    """
    tree = _parse(path)
    doc_ids: Set[int] = set()
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
            doc_ids.add(id(first.value))
    out: List[Tuple[int, str]] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in doc_ids):
            out.append((node.lineno, node.value))
    return out


def attribute_reads(path: Path) -> Set[str]:
    """这个文件**读**过哪些属性名(`x.foo` 里的 `foo`)。"""
    tree = _parse(path)
    return {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}


def subscript_keys(path: Path) -> Set[str]:
    """这个文件用**字符串常量**下标取过哪些键(`x["foo"]` 里的 `foo`)。"""
    tree = _parse(path)
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


# ══════════════════════════════════════════════════════════════════════════
# 调用图:「这个函数(经本包内的调用)会不会走到某个函数」
# ══════════════════════════════════════════════════════════════════════════

def called_names(node: ast.AST) -> Set[str]:
    """一段 AST 里**直接调用**过的函数名(`f()` 的 `f`、`x.f()` 的 `f`)。"""
    out: Set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            out.add(func.attr)
        elif isinstance(func, ast.Name):
            out.add(func.id)
    return out


def module_functions(path: Path) -> List[Tuple[str, ast.AST]]:
    """文件里所有(含嵌套在类里的)函数定义 `(名字, 节点)`。"""
    out: List[Tuple[str, ast.AST]] = []
    for node in ast.walk(_parse(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node))
    return out


def reaches(path: Path, entry_prefixes: Iterable[str], target: str,
            label: Optional[str] = None) -> List[str]:
    """哪些以 `entry_prefixes` 开头的函数,**经本文件内的调用链**走得到 `target`。

    只在**单个文件**内做闭包(跨文件的调用图不做:那需要真的解析别名与
    重导出,而本仓的读路径与它的 `init_schema` 调用一向在同一份 store 里)。
    返回 `文件:行 函数名 → 路径` 的可读清单。
    """
    funcs = dict(module_functions(path))
    direct = {name: called_names(node) for name, node in funcs.items()}

    def walk(name: str, seen: Set[str]) -> Optional[List[str]]:
        if name in seen:
            return None
        seen = seen | {name}
        calls = direct.get(name, set())
        if target in calls:
            return [name, target]
        for nxt in sorted(calls):
            if nxt in funcs:
                deeper = walk(nxt, seen)
                if deeper:
                    return [name] + deeper
        return None

    out: List[str] = []
    for name, node in sorted(funcs.items(), key=lambda kv: getattr(kv[1], "lineno", 0)):
        if not name.startswith(tuple(entry_prefixes)):
            continue
        chain = walk(name, set())
        if chain:
            out.append(f"{label or path.name}:{getattr(node, 'lineno', 0)} "
                       f"{' → '.join(chain)}")
    return out


__all__ = [
    "module_parts", "package_parts", "resolve_relative", "literal_str",
    "imports", "imports_any", "import_hits",
    "unresolvable_relative_imports", "opaque_dynamic_imports",
    "code_without_docstrings", "string_constants",
    "attribute_reads", "subscript_keys", "touched_names", "touched_any",
    "called_names", "module_functions", "reaches",
]
