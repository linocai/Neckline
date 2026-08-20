"""客户端 Swift 源码的**唯一读取入口**。

🔴 **为什么必须收口到一处**:客户端 DTO 拆在 `Networking/Models/*.swift` 五份里。
守门单测里有一批是**缺席断言**(「某个退役字段搜不到」)—— 缺席断言遇上「读不到那个
文件」会**静默变成真**:文件路径写错、DTO 被挪进新文件、新增一个不在扫描域里的
`.swift`,守门统统照样全绿。这是拆分引入的唯一新风险面。

**本模块的对策是三条,缺一不可**:

  1. `networking_swift_text()` 把 `Networking/` **整棵子树**的 `.swift` 拼起来 ——
     新增 DTO 文件自动进扫描域,⛔ 不需要谁记得去改十个地方;
  2. 每次读取都跑 `_assert_sentinels()`:五份 DTO 文件各留一个**哨兵类型**,
     少一个当场红 —— 「读到的东西比预期少」必须是**响的**,不是静默的;
  3. `type_block()` 用**行首 + 词边界**定位类型块(⛔ 不用裸 `split`)——
     `CLAUDE.md` 记着的同前缀陷阱:`struct BasketEvidence` 排在 `struct Basket`
     之前时,`split("struct Basket")` 会切到邻居身上,**断言照样绿、守的却是另一个类型**。

⚠ **本文件不是测试模块**(文件名不以 `test_` 开头,pytest 不收集它);
它是 `tests` 包里的工具模块,用 `from tests.client_sources import ...` 引入。

⚠ **V2.5.0 S12 重建**:S1 随 33 条 K8 路由一起删掉了本模块与
`test_contract_crosscheck.py`(当时客户端仍在调那些已删路由,重做归 S12)。
哨兵表因此换成了本版的五份 DTO 文件。⛔ 改动 DTO 归属(把某个类型挪到别的文件)时
**同步改这张表**,别删条目。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

_ROOT = Path(__file__).resolve().parent.parent
CLIENT = _ROOT.parent / "App" / "Neckline"
CLIENT_ROOT = _ROOT.parent / "App"
NETWORKING = CLIENT / "Networking"
MODELS_DIR = NETWORKING / "Models"
API_CLIENT = NETWORKING / "APIClient.swift"

#: 🔴 五份 DTO 文件各留一个**只可能出现在它里面**的哨兵类型。
#: 少一个 = 扫描域缺了一块 = 所有缺席断言在那一块上失明 → 当场报红。
_SENTINELS: Dict[str, str] = {
    "SharedModels.swift": "enum NKJSON",
    "K9Models.swift": "struct SelectionSnapshot",
    "CheckListModels.swift": "enum ChecklistVerdict",
    "ScoreboardModels.swift": "struct CoverageSnapshot",
    "ReviewModels.swift": "struct ReviewBindery",
}

#: 顶层声明的起始形状(用来给类型块收尾)。
_TOP_LEVEL_DECL = re.compile(
    r"^(?:struct|enum|final class|class|extension|protocol|func|actor)\s", re.M)


def model_files() -> List[Path]:
    """DTO 文件清单(排序稳定,便于拼接结果可复现)。"""
    return sorted(MODELS_DIR.rglob("*.swift"))


def _assert_sentinels() -> None:
    """哨兵自检:每份文件都在、且各自那个只可能出现在它里面的类型也在。"""
    present = {p.name for p in model_files()}
    missing_files = sorted(set(_SENTINELS) - present)
    assert not missing_files, (
        f"DTO 文件缺失:{missing_files} —— 扫描域少了一块,所有缺席断言会在那一块上失明。"
        f"若确实是**改名 / 合并**,请同步改 `tests/client_sources.py::_SENTINELS`。"
    )
    for name, sentinel in _SENTINELS.items():
        text = (MODELS_DIR / name).read_text(encoding="utf-8")
        assert sentinel in text, (
            f"`{name}` 里找不到哨兵 `{sentinel}` —— 类型被挪走了?"
            f"请同步改 `_SENTINELS`(⛔ 别删条目,那等于把哨兵拆了)。"
        )


def models_text() -> str:
    """五份 DTO 文件拼接(**只有 DTO**,不含 `APIClient.swift`)。"""
    _assert_sentinels()
    return "\n".join(p.read_text(encoding="utf-8") for p in model_files())


def networking_swift_text() -> str:
    """`Networking/` 子树全部 `.swift` 拼接(含 `APIClient.swift` / `AppConfig.swift`)。

    ⚠ **拼的是整棵子树、不是只有 `Models/`**:有几条断言要同时看 DTO 与
    `APIClient` 的调用面。真要只看 DTO,用 `models_text()`。
    """
    _assert_sentinels()
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in sorted(NETWORKING.rglob("*.swift")))


def client_swift_files() -> List[Path]:
    """`App/` 下**全部** `.swift`(含 `NecklineTests/`)。

    ⚠ 调用面对拍要扫全仓,⛔ 不能只锚 `APIClient.swift` —— 日后任何 View / Model
    直接拼 URL 都必须被机器抓到,不再依赖"人核实过一次就假设永远成立"。
    """
    return sorted(CLIENT_ROOT.rglob("*.swift"))


def type_block(name: str, *, text: str | None = None) -> str:
    """取 `struct|enum <name>` **这一个**类型的源码块(到下一个顶层声明为止)。

    🔴 **行首 + 词边界**定位(⛔ 不用裸 `split`):`\\b` 保证 `Playbook` 不会切到
    `PlaybookLevels` 头上 —— 切错块的后果是**绿灯守着错的类型**。
    """
    body = models_text() if text is None else text
    m = re.search(rf"^(?:struct|enum|final class|class) {re.escape(name)}\b", body, re.M)
    if m is None:
        return ""
    rest = body[m.end():]
    nxt = _TOP_LEVEL_DECL.search(rest)
    return body[m.start():m.end() + (nxt.start() if nxt else len(rest))]


def strip_comments(text: str, markers: tuple = ("#", "//", "///")) -> str:
    """剥掉整行注释再判。

    **这不是洁癖,是登记过的坑**:一条纪律总要写出它禁止的那个词才解释得清,
    把说明算进命中会逼着后来者删注释去凑绿 —— **一个对自己的注释报警的闸门等于
    没有闸门**(同 `tests/guard_scan.py` 的体例)。
    """
    keep = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(m) for m in markers):
            continue
        keep.append(line)
    return "\n".join(keep)


__all__ = [
    "CLIENT", "CLIENT_ROOT", "NETWORKING", "MODELS_DIR", "API_CLIENT",
    "model_files", "models_text", "networking_swift_text", "client_swift_files",
    "type_block", "strip_comments",
]
