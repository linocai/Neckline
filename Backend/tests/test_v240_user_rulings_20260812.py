"""守门:**2026-08-12 用户五条裁定 + 一条日期口径更正**的机器判据。

本文件锁的是**裁定本身**,不是某一段实现细节 —— 每一条断言旁边都写清它对应裁定
原文的哪一句。⛔ 改断言之前先回去看裁定原文(§五 V2.4.0 D 节 / §七 五条结案段);
裁定要改,那是**新的一次拍板**,不是把守门放宽。

裁定索引:
  ① P1-78  竞价取样域与 `wu.codes` 彻底分开(独立观察池,K8 三层顺序,不设上限)
  ② P1-81  `sector_bid_fade` / `market_shock` 统一落「组合环境提醒」(黄色、只给证据)
  ③        `_POSITION_ALERT_LEVEL` 四条确认 + 两条新的(全部 `warn`)
  ④ P3-80  删除「已在券商挂 -5% 条件单」复选框
  ⑤ P3-32  主篮子归属由策略判断,lift 只作辅助证据,无法确定标「归属待确认」
  ⑥ P4-67  正式投入使用时间 = 2026-08-17,15 个交易日的验证往后推
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from tests.client_sources import CLIENT, models_text, networking_swift_text, type_block

ROOT = Path(__file__).resolve().parent.parent
NECKLINE = ROOT / "neckline"


def _text(rel: str) -> str:
    if rel.startswith("App/"):
        return (ROOT.parent / "App" / rel.removeprefix("App/")).read_text(encoding="utf-8")
    if rel == "PROJECT_PLAN.md":
        return (ROOT.parent / rel).read_text(encoding="utf-8")
    return (ROOT / rel).read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """剥掉 docstring 与 `#` 行注释 —— 「**符号零引用**」类判据必须先剥。

    🔴 出处 `CLAUDE.md`:一条写着「这个组件已删除」的注释是留给下一个人的说明,
    把它算成引用 = **逼注释绕开自己要说的名字**。本文件的裁定原文正好逐字引用了
    `wu.codes` / `ths_member` 这些被禁的名字 —— 不剥就每条都误报。
    ⚠ **顺序不可颠倒**:必须**先**按行号挖掉 docstring、**再**去 `#` 注释;
    反过来会先把 docstring 里的 Markdown `#` 标题打烂,导致挖不干净(施工中真踩)。
    """
    tree = ast.parse(src)
    kill: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            kill.update(range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1))
    lines = [("" if i + 1 in kill else re.sub(r"#.*$", "", ln))
             for i, ln in enumerate(src.splitlines())]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# 裁定 ① —— 竞价独立观察池(§七 P1-78)
# ══════════════════════════════════════════════════════════════════════════

class TestRuling1ObservationPool:
    """「`wu.codes` 继续保持最多 29 只,只服务持仓与关注提醒。竞价另建独立观察池,
    按 K8 顺序取数:同一主驱动 → 同题材方向 → 传统行业,完整取样、分批请求,
    不设 29 只上限。……竞价强势股只在该独立观察池内排序,并明确标注观察范围,
    不冒充全市场排名。」"""

    def test_observation_module_never_reads_the_intraday_watch_pool(self):
        """🔴 **结构性保证**:观察池模块**零引用** `wu.codes` / 关注池组装函数 ——
        「把退潮样本接回关注池」正是裁定要拆开的那件事。"""
        src = _strip_comments(_text("neckline/auction/observation.py"))
        for forbidden in ("wu.codes", "load_watch_universe", "WatchUniverse", "breadth_cap"):
            assert forbidden not in src, f"观察池模块引用了关注池的东西:{forbidden}"

    def test_theme_layer_is_recorded_not_implemented_and_ths_member_never_enters(self):
        """🔴 **张力 (a) 的机器判据**:K8 第 ② 层「同题材方向」与已拍板裁定 #1
        (题材域永不产出、⛔ `ths_member` 不得参与判定)冲突 —— 本版**保留三层结构、
        第二层如实标 `theme_domain_not_implemented` 并跳到行业层**。
        ⛔ `ths_member` 一行都不许进 `auction/**`。"""
        from neckline.auction import observation as obs
        from neckline.selection.core_metrics import DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED

        # 两侧同一个原因码字面量 —— 按它 grep 要能把选股侧与竞价侧一起捞出来。
        assert obs.THEME_LAYER_UNIMPLEMENTED == DOMAIN_FALLBACK_THEME_NOT_IMPLEMENTED
        assert obs.ObservationPool().theme_layer_status == obs.THEME_LAYER_UNIMPLEMENTED
        for p in sorted((NECKLINE / "auction").rglob("*.py")):
            assert "ths_member" not in _strip_comments(p.read_text(encoding="utf-8")), p

    def test_pool_layers_follow_k8_order_and_industry_layer_is_complete(self):
        """三层顺序 = 篮子成员 → 驱动域 → 行业域;行业层取该行业**全部**成分股
        (⛔ 不截断)。"""
        from neckline.auction.observation import build_observation_pool
        from neckline.selection.basket_store import BasketRef

        industry_of_all = {f"6000{i:02d}.SH": "半导体" for i in range(1, 31)}
        industry_of_all["600099.SH"] = "白酒"          # 无关行业,⛔ 不该进池
        b = BasketRef(basket_id=1, trade_date="20260812", basket_key="k1", name="n", tier=1,
                      member_codes=("600001.SH",))
        card = {"members": [{"ts_code": "600001.SH",
                             "core_metrics": {"comparison_domain": "driver",
                                              "peer_codes": ["600200.SH", "600201.SH"]}}]}
        pool = build_observation_pool([b], d0_date=date(2026, 8, 12),
                                      industry_of_all=industry_of_all,
                                      card_loader=lambda _bid, db_path=None: card)
        assert pool.member_codes == ("600001.SH",)
        assert pool.driver_codes == ("600200.SH", "600201.SH")
        # 行业层 = 半导体全部 30 只减去已在前两段的那一只 → 29 只;白酒那只不在。
        assert len(pool.industry_codes) == 29
        assert "600099.SH" not in pool.codes
        # 🔴 「完整取样」:上界只受该行业成分股数限制,⛔ 没有任何截断常量。
        assert pool.size == 1 + 2 + 29
        assert pool.industries == ("半导体",)

    def test_pool_has_no_invented_truncation_constant(self):
        """🔴 **红线 1**:观察池模块里**一个新阈值都不许有**(K8 没给「取前 N 只」
        这个数)。模块级数值字面量必须为空。"""
        tree = ast.parse(_text("neckline/auction/observation.py"))
        found = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant) \
                            and isinstance(node.value.value, (int, float)) \
                            and not isinstance(node.value.value, bool):
                        found.append(t.id)
        assert not found, f"观察池模块出现了模块级数值常量(裁定 ① 明令不许发明截断数):{found}"

    def test_anchor_sampling_domain_is_the_pool_not_the_watchlist(self):
        """🔴 「竞价强势股**只在该独立观察池内**排序」—— 取样循环必须迭代
        `observation.codes`,⛔ 不是 `requested`、更不是 `wu.codes`。"""
        src = _strip_comments(_text("neckline/auction/collect.py"))
        assert "for code in observation.codes:" in src
        # 反向:取样那一段(`anchors` 循环到 `anchors.sort` 为止)里 ⛔ 一个 `wu.codes`
        # 都不许有。⚠ **抓取清单**那一段仍然读 `wu.codes` 是对的(持仓票要有报价),
        # 所以这里刻意只切取样段,⛔ 别把整个函数一把梭 —— 那会把一件对的事判成错的。
        sampling = src.split("anchors: List[Tuple[float, str]] = []")[1].split("anchors.sort")[0]
        assert "wu.codes" not in sampling, "竞价强势股的取样域仍在读 `wu.codes`(裁定 ① 要拆开的正是它)"

    def test_sector_peer_domain_is_the_pool_not_requested(self):
        """板块对照股取样域(`snap.industry_of`)只由**观察池**派生 ——
        `requested` 里还有持仓票,把它们算成对照股 = 拿「我手上这几只」当行业走势。"""
        src = _strip_comments(_text("neckline/auction/collect.py"))
        assert "_observed = set(observation.codes)" in src
        assert "industry_of = {c: ind for c, ind in _all_industry.items() if c in _observed}" in src

    def test_scope_note_is_shipped_and_carries_no_markdown(self):
        """🔴 「**明确标注观察范围**,不冒充全市场排名」—— 范围自述必须随产物下发,
        且**不许有 Markdown**(它作为 `String` 下发,`Text(String)` 不解析)。"""
        from neckline.auction.observation import ObservationPool

        pool = ObservationPool(codes=("600001.SH", "600002.SH"),
                               member_codes=("600001.SH",), industry_codes=("600002.SH",),
                               industries=("半导体",))
        note = pool.scope_note
        assert "**" not in note and "__" not in note
        assert "不是全市场竞价排行" in note
        assert "2 只" in note
        # 空池也要说清「没有观察池」,⛔ 不许留白。
        assert "没有建立观察池" in ObservationPool().scope_note

    def test_scope_note_reaches_the_client_contract(self):
        from neckline.api.schemas import AuctionOut
        assert "observationScopeNote" in AuctionOut.model_fields
        assert "observationScopeNote" in type_block("AuctionPayload")

    def test_watch_universe_upper_bound_unchanged(self):
        """「`wu.codes` **继续保持最多 29 只**」—— 关注池的构成一行未动。"""
        src = _strip_comments(_text("neckline/sentinel/universe.py"))
        assert "DEFAULT_BREADTH_CAP = 200" in src
        assert "observation" not in src, "关注池反向引用了竞价观察池(⛔ 裁定 ① 要拆开的正是这个)"


# ══════════════════════════════════════════════════════════════════════════
# 裁定 ② / ③ —— 组合环境提醒
# ══════════════════════════════════════════════════════════════════════════

class TestRuling2And3PortfolioAlerts:
    """「两类提醒统一落在持仓页顶部的「组合环境提醒」。`sector_bid_fade` 按板块展示,
    并列出受影响持仓;`market_shock` 作为全组合提醒。二者均使用黄色提醒,只提供环境
    证据,不给交易指令,不影响选股等级和交易资格,也不重复塞进单票详情。」"""

    def test_only_two_kinds_and_both_are_warn(self):
        from neckline import notify_kinds
        from neckline.api import app as app_mod

        assert app_mod._PORTFOLIO_ONLY_KINDS == frozenset({
            notify_kinds.KIND_SECTOR_BID_FADE, notify_kinds.KIND_MARKET_SHOCK})
        # 裁定 ③ 逐字:两者统一按 `warn` 展示。
        assert set(app_mod._PORTFOLIO_ALERT_LEVEL.values()) == {"warn"}
        assert app_mod._PORTFOLIO_SCOPE_KIND[notify_kinds.KIND_SECTOR_BID_FADE] == "sector"
        assert app_mod._PORTFOLIO_SCOPE_KIND[notify_kinds.KIND_MARKET_SHOCK] == "portfolio"

    def test_position_alert_levels_are_the_four_the_user_confirmed(self):
        """裁定 ③ 逐字确认的四条:`position_low_open = critical`;
        `consecutive_stops = warn`;`decoupled = warn`;`basket* = warn`。**一个字节不动。**"""
        from neckline.api import app as app_mod
        assert app_mod._POSITION_ALERT_LEVEL["position_low_open"] == "critical"
        assert app_mod._POSITION_ALERT_LEVEL["consecutive_stops"] == "warn"
        assert app_mod._POSITION_ALERT_LEVEL["decoupled"] == "warn"
        assert app_mod._position_alert_level("basket12") == "warn"
        # 原有四条同样一字未动。
        assert app_mod._POSITION_ALERT_LEVEL["stop_approach"] == "critical"
        assert app_mod._POSITION_ALERT_LEVEL["sector_dive"] == "warn"
        assert app_mod._POSITION_ALERT_LEVEL["take_profit"] == "info"
        assert app_mod._POSITION_ALERT_LEVEL["exit_reference"] == "info"

    def test_ruling_text_is_quoted_in_source(self):
        """裁定不是工程侧默认值 —— 源码里必须写明它的出处(⛔ 不许只留一个数)。"""
        src = _text("neckline/api/app.py")
        assert "用户裁定 ③" in src and "用户裁定 ②" in src

    def test_never_reenters_the_per_position_channel(self):
        """🔴 「⛔ 也不重复塞进单票详情」= 单票通道里按 kind 显式排除,守门锁写法。"""
        src = _strip_comments(_text("neckline/api/app.py"))
        body = src.split("def _today_position_alerts")[1].split("def _portfolio_kind_of")[0]
        assert "_PORTFOLIO_ONLY_KINDS" in body and "continue" in body

    def test_env_alerts_never_touch_selection_grade_or_trade_eligibility(self):
        """🔴 「不影响选股等级和交易资格」——**结构性保证**:这两个 kind 的字面量
        在选股与竞价两条判定链路里**零出现**。"""
        scopes = [NECKLINE / "selection", NECKLINE / "auction", NECKLINE / "eval"]
        hits = []
        for scope in scopes:
            for p in sorted(scope.rglob("*.py")):
                t = p.read_text(encoding="utf-8")
                if "sector_bid_fade" in t or "market_shock" in t:
                    hits.append(str(p.relative_to(ROOT)))
        assert not hits, f"组合环境提醒的 kind 出现在判定链路里:{hits}"

    def test_affected_positions_are_three_state(self):
        """🔴 「并列出受影响持仓」+ 第三态:`affectedRecorded=False` = **本次未记录**,
        ⛔ 不许折平成「没有受影响的持仓」。"""
        from neckline.api.schemas import PortfolioAlertOut
        m = PortfolioAlertOut(kind="sector_bid_fade")
        assert m.affectedCodes == [] and m.affectedRecorded is False
        assert set(PortfolioAlertOut.model_fields) == {
            "kind", "scopeKind", "scopeCode", "verdict", "ts", "level",
            "affectedCodes", "affectedRecorded",
        }

    def test_client_renders_three_states_and_branches_on_scope_kind(self):
        src = (CLIENT / "Views" / "PositionsView.swift").read_text(encoding="utf-8")
        assert "portfolioEnvSection" in src
        # 双端各挂一次(`CLAUDE.md`:改双端共用件⛔ 别只改一个平台的调用点)。
        assert src.count("            portfolioEnvSection") + src.count("                    portfolioEnvSection") >= 2
        assert 'a.scopeKind == "sector"' in src          # 先按 scopeKind 分支
        assert "受影响持仓:本次未记录" in src            # 第三态说出口
        assert "受影响:当前全部持仓" in src              # 全组合那一类
        assert "不是交易指令" in src                      # 「只提供环境证据,不给交易指令」

    def test_portfolio_alert_dto_hand_writes_init_from_decoder(self):
        block = type_block("PortfolioAlert")
        assert "init(from decoder: Decoder) throws" in block
        assert "decodeIfPresent" in block

    def test_sector_fade_body_carries_no_markdown(self):
        """🔴 这条文案自裁定 ② 起**真的会下发给客户端** → ⛔ 里面不许有 Markdown。"""
        src = _text("neckline/sentinel/attention.py")
        body = src.split("def check_sector_bid_fade")[1].split("def check_holding_decoupled")[0]
        what = body.split("what_happened=(")[1].split("),")[0]
        assert "**" not in what


# ══════════════════════════════════════════════════════════════════════════
# 裁定 ④ —— 删除「已在券商挂 -5% 条件单」复选框
# ══════════════════════════════════════════════════════════════════════════

class TestRuling4StopOrderCheckboxRemoved:
    """「现役版本删除该复选框。」"""

    def test_client_checkbox_and_local_ledger_are_physically_gone(self):
        """🔴 **物理删**,⛔ 不留一个恒 false 的位(P0 那条纪律的同一条)。"""
        assert not (CLIENT / "Components" / "NKStopOrderLedger.swift").exists()
        views = (CLIENT / "Views" / "PositionsView.swift").read_text(encoding="utf-8")
        code = "\n".join(l for l in views.splitlines() if not l.strip().startswith("//"))
        for gone in ("brokerOrderCard", "NKStopOrderLedger", "syncStopOrder",
                     "@State private var stopOrderChecked", "还没勾 —— 这一票已破线"):
            assert gone not in code, f"裁定 ④ 要求删掉的东西还在:{gone}"

    def test_project_no_longer_references_the_deleted_file(self):
        pbx = _text("App/Neckline.xcodeproj/project.pbxproj")
        assert "NKStopOrderLedger.swift" not in pbx, "⛔ 删 `.swift` 之后必须 `xcodegen generate`"

    def test_server_key_is_kept_not_deleted(self):
        """🔴 红线:服务端**只停采不删**(已装老客户端是 `try c.decode` 硬解码)。"""
        from neckline.api.schemas import PositionOut
        assert "stopOrderChecked" in PositionOut.model_fields
        assert PositionOut.model_fields["stopOrderChecked"].default is False

    def test_client_dto_decodes_the_stopped_key_defensively(self):
        """两版淘汰的第一步:客户端把它改成 `decodeIfPresent`,下一版服务端才可删键。"""
        block = type_block("Position", text=models_text())
        assert "decodeIfPresent(Bool.self, forKey: .stopOrderChecked)" in block
        assert "try c.decode(Bool.self, forKey: .stopOrderChecked)" not in block

    def test_weekly_reconciliation_never_read_it(self):
        """🔴 **对账侧受了什么影响 = 零影响**,而且这是**可查的事实**:
        `review/**` 从来没有出现过这个字段(§七 P3-80 原文那句「它是对账的输入」
        是一句从未兑现的承诺,已就地订正)。"""
        for p in sorted((NECKLINE / "review").rglob("*.py")):
            t = p.read_text(encoding="utf-8")
            assert "stop_order" not in t and "stopOrderChecked" not in t, p
        # 止损纪律判定的输入只有三样:回合盈亏 / 章程 stop_pct / advisory。
        from neckline.review import reconcile
        sig = reconcile.classify_stop_discipline.__doc__ or ""
        assert "stopOrderChecked" not in sig

    def test_no_db_column_ever_existed(self):
        """没有列 = 没有历史行要保留 —— 「历史行只读保留」在这一条上是**空操作**,
        如实登记(⛔ 不假装做了数据迁移)。"""
        db_src = _text("neckline/db.py")
        assert "stop_order" not in db_src


# ══════════════════════════════════════════════════════════════════════════
# 裁定 ⑤ —— 主篮子归属由策略判断
# ══════════════════════════════════════════════════════════════════════════

class TestRuling5PrimaryAttribution:
    """「主归属依次根据:当前直接驱动 → 股票在该方向的代表性 → 板块与核心协同 →
    预期路径匹配度,由策略判断确定。小簇和大概念没有天然优先级。……Lift 仅作辅助证据;
    样本不少于 5 只时才计算,但不得单独决定主篮子。无法确定时标记「归属待确认」,
    技术兜底结果不得影响策略等级。」"""

    def test_lift_no_longer_decides_attribution(self):
        """🔴 `highest_lift` 这条路径**不再产出**(常量保留只为读旧卡)。"""
        src = _strip_comments(_text("neckline/selection/aggregate.py"))
        body = src.split("def assign_primary")[1].split("\ndef ")[0]
        assert "PRIMARY_REASON_LIFT" not in body, "assign_primary 仍在按 lift 定归属"

    def test_min_lift_sample_size_is_the_user_number_five(self):
        """「样本不少于 **5** 只时才计算」—— 用户给的那个数,⛔ 不发明第二个。"""
        from neckline.selection import aggregate as ag
        assert ag.MIN_LIFT_SAMPLE_SIZE == 5

    def test_four_ordered_factors_are_in_the_prompt(self):
        """「由策略判断确定」= 交 LLM,四条顺序**逐字**进 prompt。"""
        from neckline.selection.aggregate import BASKET_REASON_SYSTEM_PROMPT as P
        for phrase in ("当前直接驱动", "代表性", "协同", "预期路径",
                       "小簇和大概念没有天然优先级", "primary_claim"):
            assert phrase in P, phrase
        assert "不许单独拿它决定主篮子" in P

    def test_pending_state_has_its_own_reason_codes(self):
        """🔴 「无法确定时标记「归属待确认」」+ `None` 只许承载一种含义 →
        每个待确认必配一个可查原因码。"""
        from neckline.selection import aggregate as ag
        assert ag.PRIMARY_STATUS_PENDING == "pending_confirmation"
        assert set(ag.PRIMARY_PENDING_REASONS) == {"no_primary_claim", "multiple_primary_claims"}

    def test_technical_fallback_never_affects_tier(self):
        """🔴 **「技术兜底结果不得影响策略等级」= 结构性保证**:定档模块
        (`selection/tier.py`)对主归属四个字段**零引用**。"""
        src = _strip_comments(_text("neckline/selection/tier.py"))
        for sym in ("is_primary", "primary_status", "primary_reason", "industry_lift"):
            assert sym not in src, f"定档路径读了主归属字段:{sym}"

    def test_gate_repair_marks_pending(self):
        """成员出篮后的 `_repair_primary` 同样是技术兜底 → 必须标待确认。"""
        src = _strip_comments(_text("neckline/selection/gates.py"))
        body = src.split("def _repair_primary")[1].split("\ndef ")[0]
        assert "PRIMARY_STATUS_PENDING" in body and "PRIMARY_PENDING_NO_CLAIM" in body

    def test_card_and_client_carry_the_third_state(self):
        from neckline.selection.basket_card import MemberCardEntry
        assert "primary_status" in MemberCardEntry.__dataclass_fields__
        assert "primary_pending_reason" in MemberCardEntry.__dataclass_fields__
        txt = models_text()
        assert "primaryStatus" in txt and "primaryPendingReason" in txt
        assert "enum NKPrimaryStatus" in txt
        assert "归属待确认" in txt
        # 老卡没有这一键 → 「未记录确认状态」,⛔ 不猜成已确认。
        assert "主归属(未记录确认状态)" in txt

    def test_client_never_paints_pending_as_confirmed(self):
        """三处渲染点全部改成三态(⛔ 不许还有裸 `NKChip(text: "主归属", tone: .good)`)。"""
        for rel in ("Components/NKMemberCard.swift", "Views/BasketCardView.swift",
                    "Views/InfoCardView.swift"):
            src = (CLIENT / rel).read_text(encoding="utf-8")
            assert 'NKChip(text: "主归属", tone: .good)' not in src, rel
            assert "NKPrimaryStatus" in src, rel


# ══════════════════════════════════════════════════════════════════════════
# 裁定 ⑥ —— 日期口径更正
# ══════════════════════════════════════════════════════════════════════════

class TestRuling6GoLiveDate:
    """「本 app 正式投入使用时间 = 2026-08-17(周一),15 个交易日的验证往后推。」"""

    GO_LIVE = date(2026, 8, 17)
    NTH = 15

    def test_fifteenth_trading_day_from_go_live(self):
        """🔴 用**真实交易日历**算(⛔ 不数自然日、⛔ 不口算)。"""
        from neckline.calendar import is_trading_day

        assert is_trading_day(self.GO_LIVE), "2026-08-17 不是交易日?先查日历表"
        days, cur = [], self.GO_LIVE
        while len(days) < self.NTH:
            if is_trading_day(cur):
                days.append(cur)
            cur += timedelta(days=1)
        assert days[-1] == date(2026, 9, 4), days[-1]

    def test_plan_records_the_recomputed_date_and_the_new_anchor(self):
        # 当前 PROJECT_PLAN 是短控制面；该裁定属于 V2.4.0，随历史计划归档保存。
        plan = (ROOT.parent / "archive/交接与日志/PROJECT_PLAN_v2.4.0_legacy_20260813.md").read_text(encoding="utf-8")
        assert "2026-09-04" in plan
        assert "2026-08-17" in plan
        # 🔴 旧日期 `2026-08-26` **可以留在原文里**(稳定 ID 与原文一律保留不删),
        # 但**必须就地标明它已被本裁定取代** —— 否则下一个人照着它办事。
        p467 = plan.split("[P4-67]")[1].split("\n- **[")[0]
        assert "2026-09-04" in p467 and "2026-08-17" in p467
        if "2026-08-26" in p467:
            assert "已被本裁定取代" in p467, "旧日期还在,却没标明它已作废"
