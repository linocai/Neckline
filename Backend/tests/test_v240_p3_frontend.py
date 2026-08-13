"""🔴 **V2.4.0 P3「持仓语义修复与前端减法」的机器判据**(PROJECT_PLAN §五 P3.1–P3.6)。

P3 验收十条里有四条(4 / 7 / 8 / 9)**只有实拍看得见**,编译不报错、单测也测不出 ——
本文件负责的是**另外六条**里能被机器判死的那部分,以及「改完之后别悄悄漂回去」:

| 验收 | 判据 | 本文件对应用例 |
|---|---|---|
| 1 | 当前 K8 UI / 推送 / 新篮子卡中搜不到旧纪律措辞 | `TestRetiredCharterCopyIsGone` |
| 2 | K8 当前持仓不显示机械时间退出 | `TestTimeExitCopyIsCharterDerived` |
| 3 | 旧章程历史页面仍显示旧规则(**反向用例**) | `TestHistoricalCharterStillTellsTheTruth` |
| 5 | 篮子默认态不展示六关 / 原始 LLM / 完整证据链 | `TestBasketCardThreeLayers` |
| 6 | OUT / 未定档为空时不画大面积空卡 | `TestSelectionHomeInformationLayers` |
| 10 | 前端不因精简而删除历史 DTO 字段 | `TestNoDTOFieldWasDeleted` |

🔴 **两种扫描口径刻意相反**(V2.4.0 P0 立的规矩,写新守门前先分清):

  · **文案类**(`TestRetiredCharterCopyIsGone`)—— **连注释与 docstring 一起扫、零豁免
    名单**。理由:注释里的一句旧文案随时会被复制回字面量;所以退役说明⛔ 不许原样
    引用被删的那几句话(本批为此改写了 3 处历史叙述)。
  · **符号零引用类**(`TestSelectionHomeInformationLayers` 等)—— **先剥行注释**。
    一条写着「这个入口已删除」的注释是留给下一个人的说明,把它算成引用 =
    「一个对自己的注释报警的闸门等于没有闸门」。

⚠ **扫描域不含 `tests/` 与 `*.md`**:本文件自己必须写出那几句话才能搜它们;而
`PROJECT_PLAN.md` §2.8-C-3 / §五 P3.2 要**引用旧措辞**才讲得清版本裁定 ——
文档是给人读的历史留痕,不是会被下发到用户界面的字符串。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

import pytest

from neckline import notify_kinds
from neckline.strategy import charter_copy

_ROOT = Path(__file__).resolve().parent.parent
_NECKLINE = _ROOT / "neckline"
_CLIENT = _ROOT.parent / "App" / "Neckline"
_SCRIPTS = _ROOT / "scripts"

_BASKET_DAILY = _CLIENT / "Views" / "BasketDailyView.swift"
_BASKET_CARD = _CLIENT / "Views" / "BasketCardView.swift"
_AUCTION_CARD = _CLIENT / "Views" / "AuctionCardView.swift"
_POSITIONS = _CLIENT / "Views" / "PositionsView.swift"
_POSITION_EXTRAS = _CLIENT / "Views" / "PositionExtras.swift"
_APP_MODEL = _CLIENT / "App" / "AppModel.swift"
_ROOT_VIEW = _CLIENT / "App" / "RootView.swift"


# ══════════════════════════════════════════════════════════════════════════
# 扫描原语
# ══════════════════════════════════════════════════════════════════════════

def _sources() -> List[Path]:
    """扫描域 = 运行时 Python + 现役脚本 + 全客户端 Swift(⛔ 不含 tests / *.md)。"""
    out: List[Path] = []
    for root in (_NECKLINE, _SCRIPTS):
        out.extend(sorted(p for p in root.rglob("*.py")))
    out.extend(sorted(p for p in _CLIENT.rglob("*.swift")))
    return out


def _swift_code_only(path: Path) -> str:
    """剥掉 Swift 行注释(`//` 到行尾)—— **只给「符号零引用」族判据用**。

    ⚠ 简化实现同 `test_v240_p0_retirement_guard.py`:本仓 Swift 源里没有含 `//` 的
    字符串字面量。真出现了,受害方向是「误判成注释 → 漏掉一个引用」,故凡用它做
    零引用断言的地方,都另外断言了一条**正面存在性**(那个符号确实还在别处被用)。
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


_MEMBER_DECL_RE = re.compile(r"\n    (?:private )?(?:var|func|static) ")


def _decl_slice(code: str, decl: str) -> str:
    """从某个成员声明切到**下一个同层成员声明**为止。

    ⚠ **⛔ 别拿 `// MARK:` 当锚点**:凡用 `_swift_code_only()` 剥过注释的文本里,
    MARK 行已经没了 —— 那样切出来的是「从这里到文件尾」,断言会静默变成"整份文件里
    有没有",看起来还全绿(本文件初版正是这么错的,四条用例一起假绿)。
    """
    i = code.find(decl)
    assert i >= 0, f"声明不见了:{decl!r}"
    m = _MEMBER_DECL_RE.search(code, i + len(decl))
    return code[i:m.start()] if m else code[i:]


def _order_of(text: str, needles: List[str]) -> List[int]:
    """各 needle 在 text 里首次出现的位置;没出现 → -1(调用方自己断言 != -1)。"""
    return [text.find(n) for n in needles]


def _slice_between(text: str, start_marker: str, end_marker: str) -> str:
    """取两个锚点之间那一段(用来把「某个 view 的 body」从整份文件里切出来)。"""
    i = text.find(start_marker)
    assert i >= 0, f"锚点不见了:{start_marker!r}"
    j = text.find(end_marker, i + len(start_marker))
    assert j > i, f"结束锚点不见了:{end_marker!r}(在 {start_marker!r} 之后)"
    return text[i:j]


# ══════════════════════════════════════════════════════════════════════════
# 验收 1:旧纪律措辞在**当前 K8 路径**上一个字都搜不到(含注释,零豁免)
# ══════════════════════════════════════════════════════════════════════════

# 🔴 施工图 P3.1「禁止出现」四句 + §2.8-C-3 前提③ 的旧后半句。
# ⛔ **别往这个清单里加「止损线」** —— 那三个字在**老章程分支**里是真话
# (`advisory=False` 的文案逐字不变,见 `TestHistoricalCharterStillTellsTheTruth`),
# 用全仓字面量扫它等于把「历史说历史真话」这条一起扫掉。
_RETIRED_CHARTER_PHRASES = (
    "回落止盈才是纪律",
    "回落止盈独立生效",
    "时间退出照旧跑",
    "达到参考区间后由回落止盈接管",
    "纪律仍是回落止盈",
)


class TestRetiredCharterCopyIsGone:
    @pytest.mark.parametrize("phrase", _RETIRED_CHARTER_PHRASES)
    def test_phrase_absent_from_runtime_and_client(self, phrase: str):
        """**含注释与 docstring**:一句躺在注释里的旧文案随时会被复制回字面量。"""
        hits = [str(p.relative_to(_ROOT)) for p in _sources()
                if phrase in p.read_text(encoding="utf-8")]
        assert hits == [], f"旧纪律措辞「{phrase}」仍在:{hits}"

    def test_advisory_stop_copy_no_longer_says_the_old_action_phrase(self):
        """旧动作短语「离场决策在你」已被 K8.md §十九 的「触发后由你复核原判断」取代。

        ⚠ **这一条只扫用户看得到的那句**(客户端 `lossWarningDisclosure` 的函数体 +
        服务端 advisory 文案),⛔ 不做全仓字面量扫描 —— 旧短语**不在**施工图 P3.1 的
        「禁止出现」四句里(它不是谎话,只是没点名"复核"这个动作),注释里写
        「旧措辞 X 已换成 Y」是有价值的交接说明,不该被闸门逼着绕开自己要说的词。
        """
        body = _decl_slice(_client_models_text(), "var lossWarningDisclosure: String?")
        assert charter_copy.ADVISORY_ACTION_PHRASE in body
        assert "离场决策在你" not in body
        for rel in ("api/app.py", "sentinel/holding.py", "sentinel/precall.py"):
            src = (_NECKLINE / rel).read_text(encoding="utf-8")
            # 只看 f-string / 字面量那一面:旧短语若还在,一定跟在引号里
            assert '"离场决策在你' not in src and "离场决策在你(" not in src, rel

    def test_exit_reference_push_label_is_the_new_name(self):
        """P3.2:**内部 key 保留、对外统一显示**「离场参考提醒」。
        🔴 kind 串本身⛔ 不许改(旧客户端 PUT 会 422,同 P0 `RETIRED_KINDS` 那条纪律)。"""
        assert notify_kinds.KIND_TAKE_PROFIT == "take_profit"
        assert notify_kinds.KIND_TAKE_PROFIT in notify_kinds.ALL_KINDS
        assert notify_kinds.KIND_LABEL[notify_kinds.KIND_TAKE_PROFIT] == "离场参考提醒"
        assert notify_kinds.KIND_TAKE_PROFIT not in notify_kinds.RETIRED_KINDS


# ══════════════════════════════════════════════════════════════════════════
# 验收 2 / 3:文案由**那一行**的章程派生 —— 新章程说新话,旧章程仍说旧话
# ══════════════════════════════════════════════════════════════════════════

class TestTimeExitCopyIsCharterDerived:
    def test_no_mechanical_time_exit_copy_is_single_sourced(self):
        """「本版无机械时间退出 —— D 计数只作记录」是 K8.md §十三 逐字,单一源在
        `charter_copy`;服务端 `_today_action` 读它,⛔ 不各自拍一份措辞。"""
        assert charter_copy.TIME_EXIT_DISABLED_COPY == "本版无机械时间退出 —— D 计数只作记录"
        app_src = (_NECKLINE / "api" / "app.py").read_text(encoding="utf-8")
        assert "charter_copy.TIME_EXIT_DISABLED_COPY" in app_src

    def test_client_says_the_same_words(self):
        """客户端那句(`Position.timeExitDisclosure`)与服务端同一套词 ——
        它俩会同屏出现(横幅 + 卡底那行),两种说法就是「一屏两个名字」。"""
        models = _client_models_text()
        assert "本版无机械时间退出 —— D 计数只作记录" in models

    def test_k8_position_shows_no_d_cap(self):
        """验收 2 的行为面:`max_hold_days=None` → **不编一个 D 上限**。"""
        from neckline.api.app import _today_action

        text = _today_action(2, None, None, None, "holding")
        assert charter_copy.TIME_EXIT_DISABLED_COPY in text
        assert "/D" not in text          # ⛔ 没有 `D2/D5` 这种假上限
        assert re.search(r"D\d+\s*/", text) is None


class TestHistoricalCharterStillTellsTheTruth:
    """🔴 **反向用例:防「全局硬替换」**(施工图 P3.1 明写,K8.md §十三 末句)。

    一张 `v1.3.3` 的历史页面仍然要显示「止损线」「回落止盈 8%」「时间退出 D5」——
    那是它当时的真实规则。⛔ 不许因为今天的章程没有这些条款就把历史也改口。
    """

    def test_non_advisory_stop_copy_is_byte_for_byte_unchanged(self):
        from tests.test_sentinel_holding import _position, _quote   # 复用既有构造

        from neckline.sentinel.holding import check_stop_approach

        msg = check_stop_approach(_position(), _quote(9.4), 0.05)   # advisory 缺省 False
        assert "止损线" in msg and "券商条件单" in msg
        assert "亏损警戒线" not in msg and "触发后由你复核原判断" not in msg

    def test_retrace_label_keeps_the_number_when_the_charter_has_one(self):
        """历史章程配了 `take_profit_retrace` → 仍然显示那个百分比,
        ⛔ 不被「本版无机械回落止盈」盖掉。

        ⚠ **期望值第 0 项由「章程止损」改成「章程止损线」——被 V2.4.0 复审 🟡-4 取代**
        (施工纪律 4:旧断言必须显式说明为何被取代)。原因:线名此前是这里**手写的一句
        ad-hoc 文案**,与 `charter_copy.stop_line_label` 那个单一源没有关系;🟡-4 要求
        它随章程派生(`v2.3-k8` 下必须叫「亏损警戒线」),于是强制口径下它取
        `stop_line_label(False)` = 「止损线」。**语义一字未变**、多的只是那个「线」字,
        而它与全项目其余各处的叫法从此一致。⚠ 已冻结的历史卡是 `INSERT OR IGNORE`
        的快照,**一个字都不会被改**。"""
        from neckline.selection.basket_card import discipline_labels

        assert discipline_labels(0.05, 0.08) == ["章程止损线 −5.0%", "回落止盈 8.0%"]
        assert charter_copy.retrace_disabled_copy(0.08) is None
        # 正向:advisory 口径下改叫「亏损警戒线」,两者都出自同一个单一源。
        assert discipline_labels(0.05, 0.08, advisory=True)[0] == "章程亏损警戒线 −5.0%"

    def test_client_says_the_old_percentage_for_historical_charters(self):
        """🔴 **两向都说真话**(V2.4.0 P3 实拍逮到的缺口):老章程配了 8% 就把
        「回落止盈 8.0%」写出来,⛔ 不能因为今天的章程没有它就整句沉默 ——
        沉默会被读成「这项纪律不存在」,而那是**对历史撒谎**。"""
        block = _decl_slice(_client_models_text(), "var retraceRuleLine: String?")
        assert "takeProfitRetrace" in block and "回落止盈 " in block
        assert "retraceDisabledDisclosure" in block          # 未配置 → 走那一句
        assert "retraceState?.triggered == true" in block    # 已触发 → 让位给红字那句
        assert "position.retraceRuleLine" in _POSITIONS.read_text(encoding="utf-8")

    def test_client_hides_the_disabled_line_for_historical_charters(self):
        """客户端同款:`takeProfitRetrace` 非空 → 不画那句「本版无机械回落止盈」。"""
        block = _decl_slice(_client_models_text(), "var retraceDisabledDisclosure: String?")
        assert "hasMechanicalRetrace ? nil :" in block


# ══════════════════════════════════════════════════════════════════════════
# P3.1 / P3.2 单一源接线(服务端 → 客户端跨语言锚)
# ══════════════════════════════════════════════════════════════════════════

def _client_models_text() -> str:
    """客户端 DTO 全文。**P3.7 之后 `Models.swift` 已拆成 `Networking/Models/*.swift`**
    —— 统一走 `tests/client_sources.py`(带哨兵自检:扫描域缺一块当场红),
    ⛔ 不按单一文件名读(拆分后按文件名读会让缺席断言静默变成真)。"""
    from tests.client_sources import models_text
    return models_text()


class TestCharterCopySingleSource:
    def test_stop_line_naming_goes_through_charter_copy(self):
        """三处 advisory 文案(持仓端点 / 盘中 / 盘前)都读 `charter_copy`,
        ⛔ 不各自写死「亏损警戒线」。"""
        for rel in ("api/app.py", "sentinel/holding.py", "sentinel/precall.py"):
            src = (_NECKLINE / rel).read_text(encoding="utf-8")
            assert "charter_copy.stop_line_label(" in src, rel
            assert "charter_copy.stop_action_phrase(" in src, rel

    def test_exit_reference_event_copy_is_single_sourced(self):
        """事件文案唯一源 = `charter_copy.exit_reference_reached_copy`;
        `holding.py` 只判「触没触达」,⛔ 不自己拼措辞。"""
        src = (_NECKLINE / "sentinel" / "holding.py").read_text(encoding="utf-8")
        assert "charter_copy.exit_reference_reached_copy(" in src
        assert "已触达来源篮子的离场参考区间" not in src   # 旧拼法

    def test_exit_reference_disclosure_phrase_is_mirrored_in_the_client(self):
        """🔴 **跨语言锚**:Swift 读不到 Python,两边各写一份字面量 —— 这条把它们钉在
        一起(同 `stop_line_short_label`「两处故意各自实现一遍」的既有体例)。
        `EXIT_REFERENCE_DISCLOSURE` 是 §2.8-C-3 前提③ 改写后的那半句,
        **消失即豁免失效 = 这条 kind 不再被允许推送**。
        ⚠ 客户端那句是 `Text("…**不是止盈信号**…")`(字面量才解析 Markdown),
        比对前先把强调星号剥掉 —— ⛔ 别为了让守门好写就把界面上的加粗去掉。"""
        src = _POSITION_EXTRAS.read_text(encoding="utf-8").replace("*", "")
        assert charter_copy.EXIT_REFERENCE_DISCLOSURE in src

    def test_stop_scale_mark_label_follows_the_charter(self):
        """P3.1 文案表第 3 行(刻度尺标记):`NKStopScale` 的那根红刻度改由调用方给名,
        持仓页传的是**那一笔的**章程派生值。⚠ 缺省仍是老口径「止损」,历史图不动。"""
        scale = (_CLIENT / "Components" / "NKStopScale.swift").read_text(encoding="utf-8")
        assert 'var stopLabel: String = "止损"' in scale
        assert '"\\(stopLabel) \\(NKFmt.price(stop))"' in scale
        assert _client_models_text().count("var stopScaleMarkLabel") == 1
        assert "stopLabel: position.stopScaleMarkLabel" in _POSITIONS.read_text(encoding="utf-8")

    def test_take_profit_retrace_is_carried_on_the_position_contract(self):
        """P3.1:「有没有回落止盈这项纪律」必须是**独立字段**,
        ⛔ 不许拿 `retraceState.triggered == false` 反推(那只答"触发了没有")。"""
        from neckline.api.schemas import PositionOut

        assert "takeProfitRetrace" in PositionOut.model_fields
        assert PositionOut.model_fields["takeProfitRetrace"].default is None
        models = _client_models_text()
        assert "var takeProfitRetrace: Double? = nil" in models
        assert "var hasMechanicalRetrace: Bool { takeProfitRetrace != nil }" in models


# ══════════════════════════════════════════════════════════════════════════
# P3.3 选股首页信息层级(验收 6:空态不画大面积空卡)
# ══════════════════════════════════════════════════════════════════════════

class TestSelectionHomeInformationLayers:
    def test_ios_default_flow_leads_with_baskets(self):
        """默认首屏四件的**顺序**:市场状态 → 篮子 → 盘中提示 → 紧凑统计入口。
        🔴 篮子必须排在统计入口与研究材料之前(验收 4 的结构前提;
        「第一屏看不看得到」本身只有实拍算数)。"""
        body = _slice_between(_swift_code_only(_BASKET_DAILY),
                              "private var iosBody: some View", "#endif")
        pos = _order_of(body, ["marketStatusRow", "basketsSection", "intradayNoticeRow",
                               "compactStatsRow", "researchMaterialsDisclosure"])
        assert all(p >= 0 for p in pos), pos
        assert pos == sorted(pos), f"默认层段序漂了:{pos}"

    def test_moved_out_entries_are_not_in_the_main_flow(self):
        """② 持仓体检入口 / ④ 昨日复盘入口 → 各自 Tab;完整 IntelPackage → 折叠区。
        ⚠ **剥注释后**判零引用 —— 注释里写着「已删」是说明,不是引用。"""
        code = _swift_code_only(_BASKET_DAILY)
        assert "holdingCheckupPointer" not in code
        # ⚠ 只判那张**卡**没了;`reviewPointerCaption` **刻意保留** ——
        # macOS 列表栏的 `reviewReceiptRow` 仍在用它(删了会连坐另一个平台)。
        assert re.search(r"reviewPointer(?!Caption)", code) is None
        assert "reviewPointerCaption" in code
        # IntelPackage 一个字段没删,只是挪进折叠区:iOS 主信息流里不再直接挂它。
        ios = _decl_slice(code, "private var iosBody: some View")
        assert "IntelPackageView(" not in ios
        assert "researchMaterialsDisclosure" in ios
        disclosure = _decl_slice(code, "private var researchMaterialsDisclosure")
        assert "IntelPackageView(report: model.report)" in disclosure
        # ⚠ macOS `detailColumn` 的「今日概览」**回退态**里那一处刻意保留 ——
        # 它只在"今天一篮都没有"时才画(F 组:详情栏默认是 T1 第一篮的卡),
        # ⛔ 别把它一起删掉,那会让零篮子的早晨连情报件都看不到。
        assert code.count("IntelPackageView(") == 2

    def test_out_and_dropped_collapse_into_one_compact_row(self):
        """验收 6:OUT / 未定档默认只出**数量 + 首要原因**一行,点开才是完整清单。
        「首要原因」= `nkGateOrder` 最靠前的那一关(**零发明**,与成员级 OUT 主原因
        同一把尺)。"""
        code = _swift_code_only(_BASKET_DAILY)
        row = _decl_slice(code, "private var compactStatsRow")
        for sym in ("droppedSection", "outSection", "statsExpanded"):
            assert sym in row, sym
        reason = _decl_slice(code, "private var compactStatsPrimaryGateReason")
        assert "nkGateOrder.first(where:" in reason
        assert "nkGateLabel(" in reason

    def test_freshness_badge_keeps_three_states_apart(self):
        """P3.3 末条 / 验收 7:三态**不合并** —— 「数据齐」「数据 N」「没查到」;
        🔴 `dataFreshness == nil` ⛔ 不许并进「数据齐」(那是把没查到讲成没问题)。"""
        shared = (_CLIENT / "Components" / "SharedUI.swift").read_text(encoding="utf-8")
        badge = _slice_between(shared, "struct NKFreshnessBadge", "// MARK: - Toast")  # 未剥注释,MARK 仍在
        assert '"没查到"' in badge and '"数据齐"' in badge and '"数据 \\(n)"' in badge
        assert "guard let _ = freshness else { return (NK.textTertiary, \"没查到\") }" in badge
        # ⑤ 段本体**恒在**(在 degraded 分支之外),只是换了入口 → 弹层里仍是它。
        code = _swift_code_only(_BASKET_DAILY)
        assert "freshnessSection" in code
        assert "showFreshnessSheet" in code


# ══════════════════════════════════════════════════════════════════════════
# P3.4 篮子默认卡三层(验收 5)
# ══════════════════════════════════════════════════════════════════════════

class TestBasketCardThreeLayers:
    _AUDIT_ONLY = ("gatesCard", "scoreCard", "narrativeCard", "evidenceChainCard")

    def test_default_layer_has_exactly_the_five_blocks(self):
        code = _swift_code_only(_BASKET_CARD)
        default_layer = _slice_between(code, "private var content: some View", "} else {")
        for sym in ("titleBlock", "driverOneLineCard", "preferredMemberCard",
                    "whyNowCard", "primaryRiskCard"):
            assert sym in default_layer, sym

    @pytest.mark.parametrize("sym", _AUDIT_ONLY)
    def test_audit_material_does_not_occupy_the_default_layer(self, sym: str):
        """🔴 验收 5:六关宫格 / 机械评分 / LLM 原始叙述 / 完整证据链
        **不得继续占用默认决策层**(现役卡把 ⑥ 挤到第二屏的病灶正是它们)。"""
        code = _swift_code_only(_BASKET_CARD)
        default_layer = _slice_between(code, "private var content: some View", "} else {")
        assert sym not in default_layer, f"{sym} 又爬回默认层了"

    @pytest.mark.parametrize("sym", _AUDIT_ONLY)
    def test_audit_material_is_still_reachable(self, sym: str):
        """**正面存在性**:折叠 ≠ 删除 —— 四样东西都还在「审计」层里,一个字没少。"""
        code = _swift_code_only(_BASKET_CARD)
        audit = _decl_slice(code, "private func auditSection(")
        assert sym in audit, f"{sym} 从审计层也不见了 —— 折叠成了删除"

    def test_explain_layer_carries_the_five_k8_items(self):
        code = _swift_code_only(_BASKET_CARD)
        explain = _decl_slice(code, "private func explainDisclosure(")
        for sym in ("membersSection", "upsidePathCard", "strongestEvidenceBlock",
                    "counterEvidenceBlock", "verificationCard"):
            assert sym in explain, sym

    def test_card_not_ready_still_has_its_two_distinct_reason_codes(self):
        """⚠ 卡未就绪时默认层照旧,⛔ 不退化成空卡、⛔ 两个原因码不许合并
        (`card_not_ready` 404 vs `card_corrupt` 500,V2 B1 定案)。"""
        code = _BASKET_CARD.read_text(encoding="utf-8")
        assert "card_not_ready" in code or "cardNotReady" in code
        assert "card_corrupt" in code or "cardCorrupt" in code


# ══════════════════════════════════════════════════════════════════════════
# P3.5 竞价界面减法
# ══════════════════════════════════════════════════════════════════════════

class TestAuctionReduction:
    def test_verdicts_come_first_and_context_is_folded(self):
        # ⚠ 一份文件里有多个 `var body`(卡 / 页 / 逐篮结论卡各一个)——
        # 先切到 `AuctionReportPage` 这个 struct 再找,⛔ 别用全文首个匹配。
        page = _slice_between(_swift_code_only(_AUCTION_CARD),
                              "struct AuctionReportPage", "struct AuctionVerdictCard")
        body = _decl_slice(page, "var body: some View")
        pos = _order_of(body, ["basketsBlock", "risksBlock", "manualNoteBlock",
                               "marketBackgroundDisclosure", "dataAuditDisclosure"])
        assert all(p >= 0 for p in pos), pos
        assert pos == sorted(pos), f"竞价块序漂了:{pos}"

    def test_proxy_sample_note_is_still_visible(self):
        """⚠ **`proxySampleNote` 折叠可以、消失不行**(K8 要求的诚实披露,
        施工图 P3.5 明写「守门单测保留」)。"""
        code = _swift_code_only(_AUCTION_CARD)
        assert "payload.proxySampleNote" in code
        fold = _decl_slice(code, "private var marketBackgroundDisclosure")
        assert "payload.proxySampleNote" in fold

    def test_block_numbering_is_preserved_as_an_audit_anchor(self):
        """⚠ 块名与块序是**审计锚**,在展开层里原样保留 —— 变的只是默认先展示第 3 块。"""
        code = _AUCTION_CARD.read_text(encoding="utf-8")
        for sym in ("dataStatusBlock", "marketBlock", "basketsBlock",
                    "risksBlock", "manualNoteBlock"):
            assert sym in code, sym


# ══════════════════════════════════════════════════════════════════════════
# P3.6 客户端加载边界
# ══════════════════════════════════════════════════════════════════════════

class TestLoadingBoundaries:
    def test_four_board_level_refreshers_exist(self):
        code = _APP_MODEL.read_text(encoding="utf-8")
        for fn in ("func refreshSelection()", "func refreshPositions()",
                   "func refreshReview()", "func refreshSettings()",
                   "func ensureLoaded(", "func refresh(for tab: AppTab)"):
            assert fn in code, fn

    def test_selection_refresh_does_not_pull_positions(self):
        """🔴 施工图 P3.6:「选股页移除持仓入口后,⛔ 不再每次刷新选股都拉完整持仓
        和盘中看板」。"""
        sel = _decl_slice(_swift_code_only(_APP_MODEL), "func refreshSelection() async")
        assert "fetchPositions()" not in sel
        assert "fetchBoard" not in sel and "/board" not in sel
        for call in ("fetchReportLatest()", "fetchMarketRegime()", "fetchAuction()"):
            assert call in sel, call

    def test_positions_refresh_never_polls_the_retired_board_endpoint(self):
        """⛔ **P0 校正**:审计规格 P3.6 原文把 `board` 列在 `refreshPositions()` 里 ——
        那与 P0「客户端不再存在 `/board` 专用轮询」冲突,以 P0 为准。"""
        assert "fetchBoard" not in _swift_code_only(_APP_MODEL)

    def test_pull_to_refresh_is_scoped_per_tab(self):
        assert "await model.refreshSelection()" in _BASKET_DAILY.read_text(encoding="utf-8")
        assert "await model.refreshPositions()" in _POSITIONS.read_text(encoding="utf-8")
        root = _swift_code_only(_ROOT_VIEW)
        assert root.count("await model.ensureLoaded(model.view)") == 2   # iOS + macOS
        assert "await model.refresh()" not in root


# ══════════════════════════════════════════════════════════════════════════
# 验收 10:前端**不因精简而删除**历史 DTO 字段
# ══════════════════════════════════════════════════════════════════════════

class TestNoDTOFieldWasDeleted:
    """🔴 P3 是**纯展示层收敛**:被移出默认层的每一段,它的 DTO 属性都必须还在
    —— 「只折叠不删字段」这条红线的机器判据(施工图 〇-6)。"""

    _FOLDED_AWAY_FIELDS = (
        "var sentiment",          # ① 六格情绪仪表盘 → 市场状态行点开
        "var intel",              # 完整 IntelPackage → 折叠区「研究材料」
        "var dataFreshness",      # ⑤ 数据新鲜度 → 工具栏徽标
        "var droppedBaskets",     # ③b 未定档 → 统计入口点开
        "var outCandidates",      # ③b-2 OUT → 统计入口点开
        "var reviews",            # ④ 昨日复盘 → 复盘 Tab
        "var gates",              # 六关宫格 → 审计层
        "var scorePercent",       # 机械评分 → 审计层
        "var narrative",          # LLM 原始叙述 → 审计层
        "var evidence",           # 完整证据链 → 审计层
        "var tierBreakdown",      # 五维贡献 → 审计层
    )

    @pytest.mark.parametrize("decl", _FOLDED_AWAY_FIELDS)
    def test_field_survives_the_reduction(self, decl: str):
        assert decl in _client_models_text(), f"被折叠的段把它的字段也删了:{decl}"

    def test_server_still_sends_them(self):
        """服务端字段**一个不删**(〇-6):报告契约里那几项仍在 schema 上。"""
        from neckline.api.schemas import ReportOut

        for f in ("sentiment", "intel", "dataFreshness"):
            assert f in ReportOut.model_fields, f


# ══════════════════════════════════════════════════════════════════════════
# 设计令牌纪律(交接包 §四:⛔ 一个色令牌都不许新增)
# ══════════════════════════════════════════════════════════════════════════

class TestDesignTokenDiscipline:
    def test_no_new_hex_colors_outside_design_tokens(self):
        """⛔ 一个色令牌都不许新增 —— `Color(hex:)` 只许出现在 `DesignTokens.swift`
        与两处**既有**局部底色(V2.3 遗留,本批一处未加)。"""
        allowed = {"Components/DesignTokens.swift",
                   "Components/NKFormKit.swift", "Views/InfoCardView.swift"}
        hits = sorted({str(p.relative_to(_CLIENT))
                       for p in _CLIENT.rglob("*.swift")
                       if "Color(hex:" in _swift_code_only(p)})
        assert set(hits) <= allowed, f"新增了色值字面量:{sorted(set(hits) - allowed)}"

    def test_p3_new_text_uses_the_font_scale(self):
        """⛔ 视图里不写裸 `.system(size:)`(图标例外)—— P3 新增的首选成员名走
        `NKFont.metric`(交接包要的 `20/600` 恰好是这一档)。"""
        code = _BASKET_CARD.read_text(encoding="utf-8")
        assert "Text(m.name).font(NKFont.metric).tracking(-0.3)" in code
        # ⚠ 例外只有图标(`Image(systemName:).font(.system(size:))` 不属字阶)——
        # 故按「`Text(...)` 后面直接跟裸字号」判,⛔ 不是整份文件里搜 `.system(size:`。
        assert re.search(r"Text\([^\n]*\)\s*\n?\s*\.font\(\.system\(size:", code) is None
