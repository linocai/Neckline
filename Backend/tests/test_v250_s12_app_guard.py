"""V2.5.0 **S12 App 重做**的结构性守门(PROJECT_PLAN §5.11 / §6 S12 验收)。

| 组 | 断言 |
|---|---|
| A · 退役零残留 | 持仓 / 篮子 / 六关 / Tier / 双时钟 / 情报 / 盘中看板 在 `App/` 下零命中(**代码行**,注释留痕不算) |
| B · 三板块 IA | `AppTab` 恰好 `选股 / 成绩 / 复盘 + 设置`;⛔ 240px 玻璃侧栏没被加回来 |
| C · 视图与模型清单 | §5.11 逐条:该删的文件不在、该有的文件在 |
| D · 图标改名 | `AppIconV250` 四处同步(⚠ 与 `test_v240_p4_release.py` 分工:那边锁**当前**值三处,这边锁「旧名零残留」) |

🔴 **为什么"注释留痕不算"**:一条纪律总要写出它禁止的那个词才解释得清
(「⛔ 不许把持仓加回来」)。把说明算进命中会逼着后来者删注释去凑绿 ——
**一个对自己的注释报警的闸门等于没有闸门**(同 `tests/guard_scan.py` 与
`tests/client_sources.py::strip_comments` 的体例)。

⚠ **本文件只管 App**;后端那一侧的退役守门在 `test_v250_s1_retirement_guard.py`,
契约对拍在 `test_contract_crosscheck.py`。⛔ 三份各管各的,别互相抄。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
import yaml

from tests.client_sources import CLIENT, CLIENT_ROOT, strip_comments

_ASSET_CATALOG = CLIENT / "Resources" / "Assets.xcassets"
_PROJECT_YML = CLIENT_ROOT / "project.yml"
_PBXPROJ = CLIENT_ROOT / "Neckline.xcodeproj" / "project.pbxproj"


def _code_lines() -> Dict[Path, List[Tuple[int, str]]]:
    """`App/` 下每个 `.swift` 的**代码行**(剥掉整行注释与 doc comment)。"""
    out: Dict[Path, List[Tuple[int, str]]] = {}
    for p in sorted(CLIENT_ROOT.rglob("*.swift")):
        kept = []
        for i, line in enumerate(strip_comments(p.read_text(encoding="utf-8")).splitlines(), 1):
            kept.append((i, line))
        out[p] = kept
    return out


def _hits(needle: str) -> List[str]:
    """`needle` 在代码行里的命中。

    🔴 **按标识符边界匹配**(⛔ 不是裸子串):裸子串会把 `paramsPackageVersion`
    判成 `Pack` 的命中 —— 那是**假阳性**,而假阳性会逼着后来者把守门放宽,
    最后连真的都拦不住。边界 = 前后不是字母 / 数字 / 下划线。
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])")
    found = []
    for path, lines in _code_lines().items():
        for _, line in lines:
            # 行内尾注释同样剥掉(`foo()  // 说明里提到持仓`)。
            code = line.split("//", 1)[0]
            if pattern.search(code):
                found.append(f"{path.name}: {line.strip()}")
    return found


# ══════════════════════════════════════════════════════════════════════════
# A. 退役零残留
# ══════════════════════════════════════════════════════════════════════════

#: 随 K8 与持仓板块一起退役的**标识符**。⛔ 任何一个出现在代码行里 = 有人接回来了。
#:
#: ⚠ 只收**标识符**、不收中文词:中文会误伤给用户看的说明句
#: (「⚠ 与系统的清单成绩完全隔离」里就有「成绩」二字);标识符是机器判据。
_RETIRED_IDENTIFIERS = (
    # 持仓板块(裁定 11 整块下线)
    "PositionsView", "PositionModels", "PositionExtras", "PositionQuota",
    "PositionAlert", "EntrySuggestion", "fetchPositions", "openPosition", "closePosition",
    # K8 篮子链
    "BasketDailyView", "BasketCardView", "BasketModels", "BasketDaily", "BasketMember",
    "BasketGates", "BasketVerification", "fetchBaskets", "fetchBasketCard",
    "nkGateOrder", "nkGateKind", "GateLightBar", "GateGrid",
    # K8 报告 / 情报 / 盘中看板 / 行情状态
    "ReportSnapshot", "SentimentSnapshot", "SectorSnapshot", "IntelSection",
    "InfoCardView", "InfoCard", "DataFreshness", "MarketRegime", "fetchBoard",
    # 双时钟复盘 / 画像 / 策略包 / 提醒
    "SelectionClock", "TradeClock", "Profile", "Pack", "CustomAlert", "AlertDraft",
    "ReviewHandoff", "IterationSuggestion",
    # K8 竞价确认层
    "AuctionCardView", "AuctionModels", "AuctionPayload", "AuctionVerdict",
)


@pytest.mark.parametrize("name", _RETIRED_IDENTIFIERS)
def test_retired_client_identifiers_are_gone_from_code(name: str):
    """🔴 退役标识符在 `App/` 的**代码行**里零命中(注释留痕不算,见模块头)。"""
    hits = _hits(name)
    assert not hits, f"`{name}` 已随 K8 / 持仓板块退役,却仍出现在代码里:{hits}"


def test_the_retirement_scanner_is_not_scanning_an_empty_tree():
    """闸自己的守门:扫描域真的有东西(⛔ 一个扫空树的闸恒绿)。"""
    files = _code_lines()
    assert len(files) >= 15, f"只扫到 {len(files)} 个 Swift 文件 —— 扫描域怕是错了"
    # 反面自检:现役标识符**必须**扫得到(证明剥注释没把代码一起剥掉)。
    assert _hits("SelectionSnapshot"), "扫描器连现役类型都找不到 —— 判据失效"
    assert _hits("ChecklistVerdict")


def test_the_intraday_self_observe_copy_died_with_the_basket_screen():
    """V2.4.0 P0 留下的那句盘中提示随选股页整页重做而消失。

    ⚠ 它当年的落点是「今日篮子页面 D0 预案区域之下,**有且仅有一次**」——
    那个页面已经不存在了。⛔ 别把它挪到新页上"接着用":那句话讲的是 K8 的盘中
    证伪与全局刹车,K9 之下**根本没有那两件东西**,留着是在解释一个不存在的机制。
    ⚠ K9 的等价物是**服务端下发**的核对表脚注(`CHECKLIST_FOOTNOTE`),
    ⛔ 客户端不另写一句。
    """
    assert not _hits("intradaySelfObserve")
    tokens = (CLIENT / "Components" / "DesignTokens.swift").read_text(encoding="utf-8")
    assert "enum NKCopy" not in tokens, "`NKCopy` 只装那一句盘中提示,它没了就该整个删掉"


# ══════════════════════════════════════════════════════════════════════════
# B. 三板块 IA(裁定 11)
# ══════════════════════════════════════════════════════════════════════════

def test_app_tab_is_exactly_the_three_boards_plus_settings():
    """🔴 **选股 / 成绩 / 复盘 + 设置沉底**(裁定 11)。

    `rawValue` 是 `NECKLINE_INITIAL_TAB` QA 钩子的契约 —— 客户端单测
    (`AppModelTests::testAppTabRawValuesAreTheQAHookContract`)锁同一份,
    这里再锁一次是因为**两端都得对得上**:钩子由 Swift 消费,而截图 / 部署脚本
    在仓库这一侧。
    """
    src = (CLIENT / "App" / "AppModel.swift").read_text(encoding="utf-8")
    m = re.search(r"enum AppTab: String[^{]*\{\s*\n\s*case ([^\n]+)", src)
    assert m, "找不到 `enum AppTab` 的 case 行 —— 守门锚错了地方"
    cases = [c.strip() for c in m.group(1).split(",")]
    assert cases == ["selection", "scoreboard", "review", "settings"], (
        f"三板块 IA 变了:{cases}(裁定 11 定死 选股 / 成绩 / 复盘 + 设置沉底)")
    # **设置排最后** —— 它是入口不是板块。
    assert cases[-1] == "settings"


def test_the_240px_glass_sidebar_is_not_back():
    """⛔ **别把 240px 玻璃侧栏加回来**(V2.3 已删,§5.11 逐字)。

    它与工具栏胶囊是**同一组导航的两种形态**,并存 = 两套导航。
    判据取「240 这个宽度 + 侧栏语义的标识符」两条,⛔ 不靠人眼扫。
    """
    for name in ("NavigationSplitView", "NavigationView", "sidebarWidth", "glassSidebar"):
        assert not _hits(name), f"macOS 导航壳里出现了 `{name}` —— ⛔ 侧栏不许加回来"
    root = (CLIENT / "App" / "RootView.swift").read_text(encoding="utf-8")
    assert "NKToolbar(model: model)" in root, "macOS 壳必须是 50px 统一工具栏"
    # 列表栏 376 是**板块内部**的两栏骨架,⛔ 别与被删的 240 侧栏混为一谈。
    assert "static var listWidth: CGFloat { 376 }" in root


def test_the_three_boards_each_answer_a_different_question():
    """三板块的工具栏胶囊恰好三枚(设置是右端的齿轮,⛔ 不做成第四枚胶囊)。"""
    toolbar = (CLIENT / "Components" / "NKToolbar.swift").read_text(encoding="utf-8")
    m = re.search(r"private let tabs: \[AppTab\] = \[([^\]]+)\]", toolbar)
    assert m, "找不到工具栏胶囊清单"
    pills = [p.strip().lstrip(".") for p in m.group(1).split(",")]
    assert pills == ["selection", "scoreboard", "review"], f"工具栏胶囊变了:{pills}"
    assert "settings" not in pills, "🔴 设置是入口不是板块 —— ⛔ 别做成第四枚胶囊"


# ══════════════════════════════════════════════════════════════════════════
# C. §5.11 的文件清单
# ══════════════════════════════════════════════════════════════════════════

#: §5.11 明令删除 / 改名的文件(**必须不在**)。
_MUST_BE_GONE = (
    "Views/PositionsView.swift", "Views/PositionExtras.swift",
    "Networking/Models/PositionModels.swift",
    "Views/BasketDailyView.swift", "Views/BasketCardView.swift",
    "Views/AuctionCardView.swift", "Views/InfoCardView.swift",
    "Views/IntelSectionView.swift", "Views/ReviewWorkbenchView.swift",
    "Networking/Models/BasketModels.swift", "Networking/Models/AuctionModels.swift",
    "Networking/Models/ReportModels.swift",
    "Components/NKGateViews.swift", "Components/NKMemberCard.swift",
)

#: §5.11 的目标文件(**必须在**)。
_MUST_EXIST = (
    "Views/SelectionView.swift", "Views/StockDetailView.swift",
    "Views/CheckListView.swift", "Views/ScoreboardView.swift", "Views/ReviewView.swift",
    "Networking/Models/K9Models.swift", "Networking/Models/CheckListModels.swift",
    "Networking/Models/ScoreboardModels.swift",
    # 保留件(§5.11「保留 Components/」)
    "Components/DesignTokens.swift", "Components/SharedUI.swift",
    "Components/NKFormKit.swift", "Components/NKToolbar.swift",
    "Components/NKDisclosure.swift",
)


@pytest.mark.parametrize("rel", _MUST_BE_GONE)
def test_retired_client_files_are_physically_gone(rel: str):
    assert not (CLIENT / rel).exists(), f"{rel} 应已删除(§5.11)"


@pytest.mark.parametrize("rel", _MUST_EXIST)
def test_s12_client_files_exist(rel: str):
    assert (CLIENT / rel).exists(), f"{rel} 是 §5.11 的产出,必须存在"


def test_every_swift_file_is_wired_into_the_generated_project():
    """🔴 **新增 / 删除 `.swift` 之后必须 `xcodegen generate`**。

    pbxproj 是**显式文件引用** —— 忘了重跑生成器的后果是静默的:新文件不参与编译
    (编译期看起来"少了个类型"),旧文件的引用悬空(工程打不开)。
    ⛔ 别只手改生成后的 pbxproj:下次 `xcodegen generate` 会原样冲掉。
    """
    pbx = _PBXPROJ.read_text(encoding="utf-8")
    for p in sorted(CLIENT_ROOT.rglob("*.swift")):
        assert p.name in pbx, f"`{p.name}` 不在 pbxproj 里 —— 忘了 `xcodegen generate`?"
    # 反向:pbxproj 里不许还引着已经删掉的文件。
    for rel in _MUST_BE_GONE:
        assert Path(rel).name not in pbx, (
            f"pbxproj 还引着已删的 `{Path(rel).name}` —— 忘了 `xcodegen generate`?")


# ══════════════════════════════════════════════════════════════════════════
# D. 图标改名(⚠ 与 `test_v240_p4_release.py` 分工见模块头)
# ══════════════════════════════════════════════════════════════════════════

def test_the_old_icon_name_is_gone_from_every_one_of_the_four_places():
    """🔴 **iOS 通知栏会缓存主 App 图标** —— 每版换 asset-set 名就是为了让那个缓存失效。

    改名要同步**四处**:`project.yml` / asset 目录名 / 守门常量 / 重生成的 pbxproj。
    本条只锁「旧名零残留」;**当前**值等于哪一个由
    `test_v240_p4_release.py::_EXPECTED_PRIMARY_ICON` 锁 —— ⛔ 两处不重复写死同一个串。
    """
    from tests.test_v240_p4_release import _EXPECTED_PRIMARY_ICON  # noqa: PLC0415

    old = "AppIconV242"
    assert _EXPECTED_PRIMARY_ICON != old, "V2.5.0 必须换一个新的 asset-set 名"
    assert not (_ASSET_CATALOG / f"{old}.appiconset").exists(), "旧 asset 目录还在"
    assert old not in _PROJECT_YML.read_text(encoding="utf-8").split("# ", 1)[0] or True
    # 逐处扫**代码 / 配置**(注释里作为改名沿革留痕是允许的,见模块头)。
    yml = _PROJECT_YML.read_text(encoding="utf-8")
    yml_code = "\n".join(ln for ln in yml.splitlines() if not ln.lstrip().startswith("#"))
    assert old not in yml_code, f"`project.yml` 的配置行里还有 {old}"
    assert old not in _PBXPROJ.read_text(encoding="utf-8"), f"pbxproj 里还有 {old}"
    # 图稿本身不变:新目录里那几张 png 一张不少。
    icon_set = _ASSET_CATALOG / f"{_EXPECTED_PRIMARY_ICON}.appiconset"
    pngs = sorted(p.name for p in icon_set.glob("*.png"))
    assert pngs == ["icon_1024.png", "icon_128.png", "icon_16.png", "icon_256.png",
                    "icon_32.png", "icon_512.png", "icon_64.png"], (
        f"改名不该动图稿,实得 {pngs}")


def test_only_one_appicon_name_exists_in_the_asset_catalog():
    """asset 目录里只许有**一个** `.appiconset` —— 留着旧的那个,
    `ASSETCATALOG_COMPILER_APPICON_NAME` 指哪一个就成了要现场推理的题。"""
    sets = sorted(p.name for p in _ASSET_CATALOG.glob("*.appiconset"))
    assert len(sets) == 1, f"asset 目录里有多个 appiconset:{sets}"


def test_project_yml_and_pbxproj_agree_on_the_icon_name():
    from tests.test_v240_p4_release import _EXPECTED_PRIMARY_ICON  # noqa: PLC0415

    data = yaml.safe_load(_PROJECT_YML.read_text(encoding="utf-8"))
    target = data["targets"]["Neckline"]["settings"]["base"]
    assert target["ASSETCATALOG_COMPILER_APPICON_NAME"] == _EXPECTED_PRIMARY_ICON
    # Debug + Release 各一处。
    assert _PBXPROJ.read_text(encoding="utf-8").count(
        f"ASSETCATALOG_COMPILER_APPICON_NAME = {_EXPECTED_PRIMARY_ICON};") == 2


# ══════════════════════════════════════════════════════════════════════════
# E. 文案 / 落点闸的**扫描域**:域由内容决定,⛔ 不由文件名写死
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 2026-08-21 复审实测:三条「已锁死」的客户端纪律都只扫**一个** View 文件,
# 把串挪到隔壁就绕过 ——
#   · CE24 短串「已触发成立」写进 `Networking/Models/CheckListModels.swift` → 全绿
#     (那条闸只扫 `Views/CheckListView.swift`);
#   · CE26 `func nkSum(_ a:, _ b:) { a + b }` → 全绿(那条闸是关键字黑名单);
#   · CE27 `extension AppModel { var settled: [Any] { verdicts } }` → 全绿
#     (那条闸只查字面 `model.verdicts`)。
# 三条当下都没有真违规,但「已锁死」这句话比实际强度高一档。
#
# 本组的判据形状:**域由内容决定**(谁碰了 `ChecklistVerdict`,谁就在核对表面上)、
# **允许面而不是禁止面**(终值只许出现在这四个文件里,新加一屏自动落进禁区)。
# ⚠ `test_contract_crosscheck.py` 里那三条窄版本是本组的**子集**,归那份文件的
# 主人处置;⛔ 本文件不去改它。
# ══════════════════════════════════════════════════════════════════════════


def _scan_swift(text: str) -> Tuple[str, List[Tuple[int, str]]]:
    """一次扫过 Swift 源码,分出「只剩代码的正文」与「字符串字面量清单」。

    🔴 **必须一次扫完,⛔ 不许先剥注释再剥字符串(或反过来)**:
      · 先剥注释 → `"https://x"` 里的 `//` 会被当成注释起点,后半句代码没了;
      · 先剥字符串 → `// 说明里写了 "成立"` 这种注释里的字面量会被当成真字面量。
    返回的 `code_only` **保持行数不变**(注释与字面量抹成空白),行号才对得上。
    """
    out: List[str] = []
    literals: List[Tuple[int, str]] = []
    i, n, line = 0, len(text), 1
    while i < n:
        ch = text[i]
        if ch == "\n":
            out.append("\n")
            line += 1
            i += 1
        elif text.startswith("//", i):                      # 行注释
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif text.startswith("/*", i):                      # 块注释(Swift 可嵌套)
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth += 1
                    j += 2
                elif text.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            chunk = text[i:j]
            out.append("".join("\n" if c == "\n" else " " for c in chunk))
            line += chunk.count("\n")
            i = j
        elif text.startswith('"""', i):                     # 多行字符串
            j = text.find('"""', i + 3)
            j = n if j == -1 else j + 3
            chunk = text[i:j]
            literals.append((line, chunk[3:-3] if len(chunk) >= 6 else ""))
            out.append("".join("\n" if c == "\n" else " " for c in chunk))
            line += chunk.count("\n")
            i = j
        elif ch == '"':                                     # 普通字符串
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                if text[j] == "\n":
                    break
                buf.append(text[j])
                j += 1
            j = min(j + 1, n)
            literals.append((line, "".join(buf)))
            out.append(" " * (j - i))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out), literals


def _swift_files() -> List[Path]:
    return sorted(CLIENT.rglob("*.swift"))


def test_the_swift_scanner_separates_code_from_comments_and_literals():
    """扫描器自检 —— 三种「藏一藏」的写法都不许骗过它。"""
    sample = (
        'let a = "有成立两个字"          // 注释里也写了 "成立"\n'
        'let url = "https://x/y"        // 这里的 // 不是注释起点\n'
        '/* 块注释里的 "成立" */ let b = 1\n'
    )
    code, literals = _scan_swift(sample)
    values = [s for _ln, s in literals]
    assert values == ["有成立两个字", "https://x/y"], values
    assert "let url" in code and "https" not in code
    assert "注释" not in code
    assert code.count("\n") == sample.count("\n"), "行号对不上了"


# ── E1 · 裁定 10:核对表面上⛔ 没有「成立」段 ────────────────────────────

#: 段名 / 徽标的长度上界。⚠ 超过它的一律视为整句说明(判据是**长度**,不是关键词
#: 黑名单 —— 想把「成立」做成一枚徽标,怎么写都过不去)。
_LABEL_MAX = 20

#: 唯一允许与「成立」短串同行的标识符:D0 冻结预案的**成立分支条件**。
#: ⚠ 那不是终值(复审逐字确认过):它是 §5.11 要的「三分支预案摘要」的一支,
#: 而裁定 10 禁的是 9:29 那一拍**判出**「成立」。
_CONFIRM_BRANCH_TOKENS = ("confirmBranch", "branch(named:")


def _checklist_surface() -> List[Path]:
    """核对表面 = **代码里提到 checklist 的每一个文件**(大小写不敏感)。

    🔴 域由**内容**决定,⛔ 不写死文件名:CE24 的全部花招就是「把串挪到隔壁那个
    没被点名的文件」。这么定义之后,谁新加一个核对表相关的文件,谁自动进禁区。
    ⚠ 判据故意宽:进域的文件多一个不亏 —— 真正区分合法与不合法的是下面那条
    「短串 vs 整句」加上 `_CONFIRM_BRANCH_TOKENS` 那条豁免,不是域的边界。
    """
    out = []
    for p in _swift_files():
        code, _ = _scan_swift(p.read_text(encoding="utf-8"))
        if re.search(r"(?i)check\s*list", code):
            out.append(p)
    return out


def test_the_checklist_surface_reaches_past_the_view_file():
    """扫描域自检:域里必须**同时**有视图和模型 —— 只有一个文件 = CE24 还能绕。"""
    names = {p.name for p in _checklist_surface()}
    assert {"CheckListView.swift", "CheckListModels.swift"} <= names, names


def test_no_confirmed_label_anywhere_on_the_checklist_surface():
    """🔴 **裁定 10**:9:29 那一拍**结构上**判不出「成立」,这张表只有两段。

    「成立」⛔ 不许出现在核对表面上的任何**段名 / 徽标 / 枚举取值**;只允许出现在
    **解释它为什么不存在**的整句说明里,或标注 D0 预案的**成立分支条件**。
    """
    offenders: List[str] = []
    for path in _checklist_surface():
        text = path.read_text(encoding="utf-8")
        code, literals = _scan_swift(text)
        code_lines = code.splitlines()
        for lineno, value in literals:
            if "成立" not in value or len(value) > _LABEL_MAX:
                continue
            line = code_lines[lineno - 1] if lineno <= len(code_lines) else ""
            if any(tok in line for tok in _CONFIRM_BRANCH_TOKENS):
                continue          # 预案的成立**分支条件**,不是终值
            offenders.append(f"{path.name}:{lineno} {value!r}")
    assert offenders == [], (
        "核对表面上出现了带「成立」的**短串**(段名 / 徽标的形状):\n"
        + "\n".join(offenders))


def test_the_checklist_surface_still_explains_why_there_is_no_confirmed_segment():
    """正向:⛔ 不许只是沉默地少一段 —— 必须有一句话说清为什么。"""
    said = False
    for path in _checklist_surface():
        _code, literals = _scan_swift(path.read_text(encoding="utf-8"))
        assert len(literals) >= 5, f"{path.name} 一个字面量都没扫到?扫描器怕是失效了"
        if any("成立" in s and len(s) > _LABEL_MAX for s in (v for _l, v in literals)):
            said = True
    assert said, "核对表面上没有任何一句解释「为什么这里没有成立段」"


# ── E2 · 裁定 10 的落点:终值只许出现在成绩面 ──────────────────────────

#: 🔴 **允许面**(⛔ 不是禁止面):10:00 结算终值只许被这四个文件碰。
#: 禁止面写法(「这三个 View 里不许有」)的毛病是**新加一屏就自动合规** ——
#: CE27 正是钻这个空子:在 `SelectionView.swift` 里加一层
#: `extension AppModel { var settled: [Any] { verdicts } }`,字面 `model.verdicts`
#: 零命中,而终值照样上了选股首屏。
_VERDICT_ALLOWED_FILES = {
    "AppModel.swift",          # 状态容器:它得存着
    "APIClient.swift",         # 取数
    "ScoreboardModels.swift",  # 成绩板块的 DTO
    "ScoreboardView.swift",    # 成绩板块的呈现 —— 唯一的落点
}

#: 终值这件事的**标识符面**:字段名 + 两个 DTO 类型名。
_VERDICT_TOKENS = ("verdicts", "K9VerdictsSnapshot", "K9VerdictRow")


def test_the_settlement_verdicts_are_confined_to_the_scoreboard():
    """🔴 **裁定 10 的落点**:三分支终值只出现在**成绩**板块。

    ⛔ 它不进选股首屏 —— 那一屏回答的是「今天该细看哪几只 / 明早哪几只已经死了」,
    把终值摆上去会让人在 9:30 之前就以为系统已经判了成立。
    """
    offenders: List[str] = []
    for path in _swift_files():
        if path.name in _VERDICT_ALLOWED_FILES:
            continue
        code, _ = _scan_swift(path.read_text(encoding="utf-8"))
        for i, line in enumerate(code.splitlines(), 1):
            for token in _VERDICT_TOKENS:
                if re.search(rf"(?<![A-Za-z0-9_]){token}(?![A-Za-z0-9_])", line):
                    offenders.append(f"{path.name}:{i} {token} — {line.strip()}")
    assert offenders == [], (
        "10:00 结算终值漏到成绩板块之外了(裁定 10):\n" + "\n".join(offenders))


def test_the_verdict_allowlist_is_not_vacuous():
    """允许面自检:名单里的文件得真的存在,且成绩板块**确实**在呈现终值 ——
    否则这条闸可能是在守一个空集。"""
    names = {p.name for p in _swift_files()}
    assert _VERDICT_ALLOWED_FILES <= names, _VERDICT_ALLOWED_FILES - names
    code, _ = _scan_swift(
        (CLIENT / "Views" / "ScoreboardView.swift").read_text(encoding="utf-8"))
    assert "verdicts" in code, "成绩板块不再呈现三分支终值了?那这条闸守的是空集"


# ── E3 · G13:行业分 / 选票分永不合并 ──────────────────────────────────

#: 直白的合计口径命名(大小写不敏感)。这是第一道线,⛔ 不是唯一一道。
_COMBINED_NAMES = (
    "combinedScore", "totalScore", "overallScore", "sumScore", "aggregateScore",
    "industryPlusPick", "合计分", "综合分", "总分",
)

#: 两个分数的**标识符形状**。⚠ 判据写在字段出现**之前** —— S17 才落
#: `scorecard/listing.py` 的那两个数,那天这个检测器已经在位了。
_INDUSTRY_SCORE_RE = r"(?:[A-Za-z0-9_.]*[Ii]ndustry[A-Za-z0-9_.]*[Ss]core[A-Za-z0-9_.]*|行业分)"
_PICK_SCORE_RE = r"(?:[A-Za-z0-9_.]*[Pp]ick[A-Za-z0-9_.]*[Ss]core[A-Za-z0-9_.]*|选票分)"


def _combining_expressions(code: str) -> List[Tuple[int, str]]:
    """同一个表达式里把两个分数**算到一起**的写法(任意方向、任意算符)。

    这就是 CE26 要的那条「AST-lite」判据:关键字黑名单只挡得住取了坏名字的合计,
    `func nkSum(_ a:, _ b:) { a + b }` 这种取个中性名字的挡不住;
    而「两个分数出现在同一个算术表达式里」是**合并这件事本身**的形状。
    """
    pair = [
        rf"{_INDUSTRY_SCORE_RE}\s*[-+*/]\s*{_PICK_SCORE_RE}",
        rf"{_PICK_SCORE_RE}\s*[-+*/]\s*{_INDUSTRY_SCORE_RE}",
    ]
    out: List[Tuple[int, str]] = []
    for i, line in enumerate(code.splitlines(), 1):
        for pattern in pair:
            if re.search(pattern, line):
                out.append((i, line.strip()))
                break
    return out


def test_the_combining_detector_actually_detects():
    """扫描器自检 —— 中性命名的合计也要看得见。"""
    assert _combining_expressions("let t = row.industryScore + row.pickScore")
    assert _combining_expressions("func nkSum(_ x: S) -> D { x.pickScore + x.industryScore }")
    assert _combining_expressions("let d = a.industryScore - a.pickScore")
    # 反向:两栏分别呈现⛔ 不许被判红。
    assert not _combining_expressions(
        "NKStatCell(industryScore); Divider(); NKStatCell(pickScore)")


def test_neither_the_scoreboard_view_nor_its_models_offer_a_combined_score():
    """🔴 **G13**:行业分低是**方向层**的问题,行业分高而选票分低是**选票参数**的
    问题 —— 两者吃的药完全不同,所以两栏永不合并(K9 §八 口径原文)。

    ⚠ 扫描域是 `Views/` + `Networking/Models/` 全体(原来只有 `ScoreboardView.swift`
    加一个 model);判据是「命名黑名单 ∪ 同一表达式里两个分数相加」。
    """
    offenders: List[str] = []
    for path in _swift_files():
        if path.parent.name not in ("Views", "Models"):
            continue
        code, _ = _scan_swift(path.read_text(encoding="utf-8"))
        lowered = code.lower()
        for banned in _COMBINED_NAMES:
            if banned.lower() in lowered:
                offenders.append(f"{path.name}: 出现了合计口径命名 `{banned}`")
        for lineno, line in _combining_expressions(code):
            offenders.append(f"{path.name}:{lineno} 两个分数被算到一起 — {line}")
    assert offenders == [], "成绩板块出现了合计口径:\n" + "\n".join(offenders)


def test_the_two_columns_are_both_still_there():
    """正向:两栏确实都在(⛔ 「没有合计」不许靠删掉其中一栏来满足)。"""
    models = (CLIENT / "Networking" / "Models" / "ScoreboardModels.swift").read_text(
        encoding="utf-8")
    _code, literals = _scan_swift(models)
    values = {s for _l, s in literals}
    assert "行业分" in values and "选票分" in values, "两栏的栏名不见了"
