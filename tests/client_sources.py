"""客户端 Swift 源码的**唯一读取入口**(V2.4.0 P3.7 拆分之后)。

🔴 **为什么必须收口到一处**:`Networking/Models.swift`(5633 行)已按 P3.7 拆成
`Networking/Models/{Shared,Basket,Report,Position,Review,Auction}Models.swift`。
十个守门单测此前**按文件名**读它,其中多条是**缺席断言**(「某个退役字段搜不到」)
—— 缺席断言遇上「读不到那个文件」会**静默变成真**:文件路径写错、DTO 被挪进新文件、
新增一个不在扫描域里的 `.swift`,守门统统照样全绿。这是拆分引入的唯一新风险面。

**本模块的对策是三条,缺一不可**:

  1. `networking_swift_text()` 把 `Networking/` **整棵子树**的 `.swift` 拼起来 ——
     新增 DTO 文件自动进扫描域,⛔ 不需要谁记得去改十个地方;
  2. 每次读取都跑 `_assert_sentinels()`:六份拆出来的文件各留一个**哨兵类型**,
     少一个当场红 —— 「读到的东西比预期少」必须是**响的**,不是静默的;
  3. `type_block()` 沿用既有的 `split("struct <Name>")` 取块法,并保留
     `CLAUDE.md` 记的**同前缀陷阱**(`BasketEvidence` / `BasketEvidenceItem`)
     的处理方式,⛔ 不因为换了读取入口就换一套取块规则。

⚠ **本文件不是测试模块**(文件名不以 `test_` 开头,pytest 不收集它);
它是 `tests` 包里的工具模块,用 `from tests.client_sources import ...` 引入
(同 `from tests.conftest import seed_active_rule_v1` 的既有姿势)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

_ROOT = Path(__file__).resolve().parent.parent
CLIENT = _ROOT / "client" / "Neckline"
NETWORKING = CLIENT / "Networking"
MODELS_DIR = NETWORKING / "Models"

# 🔴 六份拆分件各留一个**只可能出现在它里面**的哨兵类型。
# 少一个 = 扫描域缺了一块 = 所有缺席断言在那一块上失明 → 当场报红。
# ⚠ 改动 DTO 归属(把某个类型挪到别的文件)时,**同步改这张表**,别删条目。
_SENTINELS: Dict[str, str] = {
    "SharedModels.swift": "enum NKJSON",
    "BasketModels.swift": "struct BasketCard",
    "ReportModels.swift": "struct ReportSnapshot",
    "PositionModels.swift": "struct Position",
    "ReviewModels.swift": "struct MarketRegime",
    "AuctionModels.swift": "struct AuctionPayload",
}


def model_files() -> List[Path]:
    """拆分后的 DTO 文件清单(排序稳定,便于拼接结果可复现)。"""
    return sorted(MODELS_DIR.rglob("*.swift"))


def networking_swift_text() -> str:
    """`Networking/` 子树全部 `.swift` 拼接(含 `APIClient.swift` / `AppConfig.swift`)。

    ⚠ **拼的是整棵子树、不是只有 `Models/`**:调用方里有几条断言要同时看
    DTO 与 `APIClient` 的调用面(如「客户端调用面 ⊆ 服务端路由面」)。
    真要只看 DTO,用 `models_text()`。
    """
    _assert_sentinels()
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(NETWORKING.rglob("*.swift")))


def models_text() -> str:
    """只拼 DTO 六份(`Networking/Models/*.swift`)。"""
    _assert_sentinels()
    return "\n".join(p.read_text(encoding="utf-8") for p in model_files())


def _assert_sentinels() -> None:
    present = {p.name: p.read_text(encoding="utf-8") for p in model_files()}
    missing = [f"{name}({sentinel})" for name, sentinel in _SENTINELS.items()
               if sentinel not in present.get(name, "")]
    assert not missing, (
        "客户端 DTO 扫描域缺块 —— 守门会因此在这些块上**静默失明**:" + ", ".join(missing)
        + "\n(P3.7 拆分件被改名 / 被挪走 / 哨兵类型被删?先修这里,别改断言。)"
    )


def type_block(name: str, *, text: str | None = None) -> str:
    """取 `struct <name>` **这一个**类型的源码块(到下一个顶层声明为止)。

    ⚠ **同前缀陷阱**(`CLAUDE.md` 明写):`struct BasketEvidence` 是
    `struct BasketEvidenceItem` 的前缀,裸 `split("struct BasketEvidence")` 会切错块。
    故这里按 `struct <name>:` / `struct <name> ` / `struct <name>{` 三种收尾形态匹配,
    ⛔ 别退回裸前缀匹配。
    """
    src = models_text() if text is None else text
    for tail in (f"struct {name}:", f"struct {name} ", f"struct {name}{{"):
        i = src.find(tail)
        if i >= 0:
            break
    else:
        raise AssertionError(f"客户端找不到 struct {name}")
    rest = src[i:]
    # 下一个顶层声明 = 行首(零缩进)的 struct/enum/final class/extension/func/let/var
    for marker in ("\nstruct ", "\nenum ", "\nfinal class ", "\nclass ",
                   "\nextension ", "\nfunc ", "\nlet ", "\nvar "):
        j = rest.find(marker, 1)
        if j > 0:
            rest = rest[:j]
    return rest
