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
