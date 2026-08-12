"""🔴 **V2.4.0 P0「删除至少 80%」的机器判据**(PROJECT_PLAN §五 P0.7 七条,逐条落成守门)。

审计规格 P0.7 末段点了**两种典型的假完成**:
    ① 只隐藏前端、后台仍每分钟判断;
    ② 只把「证伪」改名「风险」而触发与动作不变。
本文件就是防这两种的 —— 它**不测行为好坏,只测「那条链路还在不在」**:

| # | 判据 | 本文件对应用例 |
|---|---|---|
| 1 | 生产判断删除 100% | `TestProductionChainDetached`(AST 扫 `engine.py` 调用点数 = 0) |
| 2 | 交易动作语义删除 100% | `TestRetiredCopyIsGoneEverywhere`(全仓 + 全客户端文案扫描) |
| 3 | 主动推送删除 100% | `TestPushRetired`(`push_retreat_brake` 零引用 + `RETIRED_KINDS`) |
| 4 | 专用前端删除 100% | `TestClientBoardSurfaceGone` |
| 5 | 专用轮询删除 100% | `TestNoDedicatedBoardPolling` |
| 6 | 跑多拍不长新行 | `tests/test_sentinel_engine.py::TestP0RetiredIntradayJudgements`(行数差 = 0) |
| 7 | 历史兼容允许保留 | `TestHistoryCompatIsKept`(表 / 端点 / DTO 仍在,只是断链) |

🔴 **三处「同名不同物」是本批最大的误删风险,守门也必须分辨**(⛔ 不按词扫):
`invalidation` / `retreat` / `board` 各有三义,本文件**按文件与符号**断言,
并对「明令保留的那两义」加**正面存在性断言**(见 `TestPreservedCapabilitiesIntact`)——
只扫「不该有的没有」会漏掉「该有的被误删了」。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List

import pytest

from neckline import notify_kinds

_ROOT = Path(__file__).resolve().parent.parent
_NECKLINE = _ROOT / "neckline"
_CLIENT = _ROOT / "client" / "Neckline"
_SCRIPTS = _ROOT / "scripts"
_ENGINE = _NECKLINE / "sentinel" / "engine.py"
# V2.4.0 P3.7:`Models.swift` 已拆六份 → 统一入口(带哨兵自检)。
from tests.client_sources import models_text as _models_text


def _py_sources(*roots: Path) -> List[Path]:
    out: List[Path] = []
    for root in roots:
        out.extend(sorted(p for p in root.rglob("*.py")))
    return out


def _swift_sources() -> List[Path]:
    return sorted(p for p in _CLIENT.rglob("*.swift"))


def _strip_swift_comments(text: str) -> str:
    """去掉 Swift 行注释(`//` 起到行尾),保留代码。

    🔴 **只给「符号零引用」那一族判据用**(P0.7 #4/#5:`RetreatBrakeBar` 等在**视图层
    零引用**)—— 一条写着「这个组件已删除」的注释是**留给下一个人的说明**,不是引用;
    把它算成命中,等于逼着退役说明去绕开自己要说的那个名字,那就是
    `CLAUDE.md`「一个对自己的注释报警的闸门等于没有闸门」的翻版。
    ⚠ **文案扫描(判据 #2)刻意不剥注释** —— 施工图那条明写「注释与 docstring 里的
    历史叙述必须一并改写或删除,⛔ 不许靠豁免名单」,两族判据口径**刻意相反**。
    ⚠ 简化实现:本仓 Swift 源里没有含 `//` 的字符串字面量(URL 走 `AppConfig` 常量),
    故按行切足够;真出现了,受害的方向是**误判为注释 → 漏掉一个引用**,
    所以下面对每个符号**另加一条「整份文本里的出现次数 ≥ 剥注释后的次数」**的自检。
    """
    out = []
    for line in text.splitlines():
        idx = line.find("//")
        out.append(line if idx < 0 else line[:idx])
    return "\n".join(out)


def _called_names(tree: ast.AST) -> List[str]:
    """本树里所有被调用的名字(`f(...)` 与 `mod.f(...)` 都取末段名)。"""
    names: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Name):
            names.append(fn.id)
        elif isinstance(fn, ast.Attribute):
            names.append(fn.attr)
    return names


# ══════════════════════════════════════════════════════════════════════════
# 判据 1:生产判断删除 100%
# ══════════════════════════════════════════════════════════════════════════

class TestProductionChainDetached:
    """P0.7 #1 —— **判据不是「文件在哪」,是「生产入口有没有它」**(§3.14-A)。"""

    _BANNED_CALLS = (
        "check_invalidation",            # 通用盘中证伪(义 ①)
        "evaluate_retreat",              # 退潮判级
        "record_retreat_metrics",        # 退潮逐拍台账写入
        "compute_breadth_snapshot",      # 退潮宽度快照
        "load_prev_tick_triggered",      # 退潮持续性判据
        "load_same_time_zaban_baseline",  # 退潮同时段基线
        "push_retreat_brake",            # 退潮 APNs
    )

    def test_run_tick_never_calls_invalidation_or_retreat(self):
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        called = _called_names(tree)
        for name in self._BANNED_CALLS:
            assert called.count(name) == 0, f"engine.py 仍在调用已退役的 {name}"

    def test_engine_imports_nothing_from_the_retired_modules(self):
        """⛔ 连 import 都不许留 —— 留着 import 就是给"顺手接回来"留门。"""
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        retired = {"invalidation", "retreat", "retreat_store", "mainline"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tail = node.module.rsplit(".", 1)[-1]
                assert tail not in retired, f"engine.py 仍 import {node.module}"
                if node.module.endswith("sentinel"):
                    assert not (retired & {a.name for a in node.names}), \
                        f"engine.py 仍从 sentinel 包里 import {[a.name for a in node.names]}"
            elif isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.rsplit(".", 1)[-1] not in retired, f"engine.py 仍 import {a.name}"

    def test_watch_universe_no_longer_assembles_retreat_only_samples(self):
        """P0.4-8:关注池**本身仍活着**,删的只是两份退潮专用样本位。"""
        from neckline.sentinel import universe

        for gone in ("MANDATORY_POOL_RESERVE", "MAINLINE_SLICE_QUOTA_FLOOR",
                     "PREV_LIMIT_UP_QUOTA_FLOOR", "_measurement_budget",
                     "_mainline_quota", "_derive_mainline_sample",
                     "_load_prev_limit_up_codes"):
            assert not hasattr(universe, gone), f"universe.py 仍有退潮专用件 {gone}"
        # 正面:关注池的三段来源与上限**必须还在**(⛔ 这一刀切歪 = 竞价层与持仓哨兵一起哑)
        assert universe.DEFAULT_BREADTH_CAP == 200
        for kept in ("load_watch_universe", "WatchUniverse", "WatchTarget",
                     "BOARD_BENCHMARK_INDEX", "INTRADAY_BASKET_TIERS"):
            assert hasattr(universe, kept), f"关注池必需件 {kept} 被误删"

    def test_capture_bypass_stays_dependency_free_of_the_retired_judgements(self):
        """P0.4-10:原始分钟存拍继续跑,且**与盘中判决在代码依赖上分离**。"""
        tree = ast.parse((_NECKLINE / "sentinel" / "capture.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "invalidation" not in node.module and "retreat" not in node.module

    def test_tick_result_carries_no_retired_observation_slots(self):
        """⛔ 不许留一个恒 False 的位 —— 那是「前端隐藏、后台仍在判」的温床。"""
        from neckline.sentinel.engine import TickResult

        fields = set(TickResult.__dataclass_fields__)
        for gone in ("retreat_active", "retreat_alert", "retreat_warning",
                     "breadth_snapshot", "invalidation_signals"):
            assert gone not in fields, f"TickResult 仍带退役观测位 {gone}"
        # 正面:持仓哨兵与三条旁路的观测位一个都不许少(P0.3 明令保留)
        for kept in ("holding_alerts", "exit_reference_hits", "captured_ticks",
                     "basket_states", "custom_alert_hits", "attention_alerts"):
            assert kept in fields, f"保留能力的观测位 {kept} 被误删"


# ══════════════════════════════════════════════════════════════════════════
# 判据 2:交易动作语义删除 100%
# ══════════════════════════════════════════════════════════════════════════

class TestRetiredCopyIsGoneEverywhere:
    """P0.7 #2 —— **注释与 docstring 里的历史叙述也必须改写或删除,⛔ 不许靠豁免名单**。

    ⚠ 扫描域 = `neckline/**` + `client/Neckline/**` + `scripts/**`
    (排除 `archive/` 与 `tests/`:归档是历史留痕;测试里要引用这几句才能断言它们没了)。
    """

    # 「今日计划已作废」多一个「已」字,精确匹配会漏 —— 用正则把那个字设成可选。
    _BANNED = (
        "剔除勿进",
        "今日计划(?:已)?作废",
        "禁止开新仓",
        "禁开新仓",
        "运行正常 · 无退潮刹车",
        "退潮红色刹车已触发",
    )

    # 🔴 **`退潮刹车` 单独一条、且**剥注释后**扫,理由与上面五句刻意不同**:
    # 它是**退役件自己的名字**,退役说明("`RetreatBrakeBar` 已删除"、"随退潮判级退役")
    # 绕不开它;而上面那几句是**交易动作语义**,连历史叙述都必须改写(P0.7 #2 原文)。
    # ⚠ 判据因此收窄成「**界面文案里没有**」= 剥掉行注释后的 Swift 代码里零命中。
    # ⛔ 别把它并进上面那个 `_BANNED` 元组 —— 那会逼退役注释去绕开自己要说的名字。
    def test_retreat_brake_is_no_longer_a_ui_string(self):
        hits = []
        for path in _swift_sources():
            code = _strip_swift_comments(path.read_text(encoding="utf-8"))
            for i, line in enumerate(code.splitlines(), 1):
                if "退潮刹车" in line:
                    hits.append(f"{path.relative_to(_ROOT)}:{i}: {line.strip()}")
        assert hits == [], f"「退潮刹车」仍是界面文案:{hits}"

    def test_the_falsification_word_is_deliberately_not_banned(self):
        """🔴 **反向守门:`证伪` 二字⛔ 不许被一刀切掉**(〇c 那张表 + P0.6-7 明写
        「`证伪` 要分辨」)。它至少有四个互不相干的用法,其中三个是**明令保留**的:
        ① D0 卡的「验证与失效条件」段(措辞上刻意避开"证伪"二字,但概念仍在);
        ② 篮子验证四态的 `falsified` =「驱动假设已证伪」(⑦-b/⑧,与盘中证伪无关);
        ③ 清仓原因码 `INVALIDATION` =「证伪离场」;
        ④ **P0.2 那条小提示本身**就写着「不作盘中证伪或全局刹车」。
        本条正面断言 ②③④ 都还在 —— 只扫「不该有的没有」会漏掉「该有的被误删了」。"""
        models = _models_text()
        assert "驱动假设已证伪" in models
        assert "证伪离场" in models
        assert _P0_NOTICE in (_CLIENT / "Components" / "DesignTokens.swift").read_text(encoding="utf-8")

    @pytest.mark.parametrize("pattern", _BANNED)
    def test_retired_action_semantics_appear_nowhere(self, pattern):
        hits = []
        for path in _py_sources(_NECKLINE, _SCRIPTS) + _swift_sources():
            text = path.read_text(encoding="utf-8")
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(_ROOT)}:{line}")
        assert hits == [], f"「{pattern}」仍存活于:{hits}"


# ══════════════════════════════════════════════════════════════════════════
# P0.2:唯一保留的那条小提示(**恰好一次**,且形态不许升级)
# ══════════════════════════════════════════════════════════════════════════

_P0_NOTICE = "盘中请自行结合分时判断;系统保留 D0 预案,不作盘中证伪或全局刹车。"


class TestSingleIntradayNotice:
    def test_notice_literal_appears_exactly_once_in_the_whole_client(self):
        """P0.2 / P0.8 用例 13:**全客户端出现次数恰为 1**(文案单一源 `NKCopy`)。
        ⛔ 复制第二份 = 两处会漂;⛔ 拼接 = `Text("a" + "b")` 那条 Markdown 坑。"""
        hits = []
        for path in _swift_sources():
            n = path.read_text(encoding="utf-8").count(_P0_NOTICE)
            if n:
                hits.append((str(path.relative_to(_ROOT)), n))
        assert sum(n for _, n in hits) == 1, f"出现次数应恰为 1,实际:{hits}"
        assert hits[0][0].endswith("DesignTokens.swift"), \
            f"文案应住在 `NKCopy`(DesignTokens.swift),实际在 {hits[0][0]}"

    def test_notice_is_rendered_once_per_platform_and_only_on_the_baskets_page(self):
        """落点 = 今日篮子页面,**双端各一次**(iOS `iosBody` / macOS `listColumn`);
        ⛔ 其它页面不重复。"""
        users = []
        for path in _swift_sources():
            code = _strip_swift_comments(path.read_text(encoding="utf-8"))
            n = code.count("intradayNoticeRow")
            if n:
                users.append((path.name, n))
        # 只许出现在 BasketDailyView:两处调用点(双端各一)+ 一处定义 = 3
        assert users == [("BasketDailyView.swift", 3)], users

    def test_notice_is_plain_text_not_an_alert_card(self):
        """形态硬约束:普通辅助文字。⛔ 无底色 / 无图标 / 不可点击 / 无计数 / 无轮询。"""
        src = (_CLIENT / "Views" / "BasketDailyView.swift").read_text(encoding="utf-8")
        block = src.split("private var intradayNoticeRow: some View {", 1)[1].split("\n    }", 1)[0]
        for banned in ("Button", "onTapGesture", "NKChip", "Image(systemName",
                       "NK.down", "NK.amber", "background(", "Task", "sleep"):
            assert banned not in block, f"小提示里出现了 {banned} —— P0.2 明令禁止"
        assert "NKCopy.intradaySelfObserve" in block
        assert "NK.textTertiary" in block and "NKFont.caption" in block


# ══════════════════════════════════════════════════════════════════════════
# 判据 3:主动推送删除 100%
# ══════════════════════════════════════════════════════════════════════════

class TestPushRetired:
    def test_push_retreat_brake_has_no_caller_left(self):
        """P0.7 #3:`neckline/**` 里除**自身定义**与 `__all__` 外零引用。"""
        callers = []
        for path in _py_sources(_NECKLINE, _SCRIPTS):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if path == _NECKLINE / "api" / "notify.py":
                # 自身定义所在文件:只允许 `def push_retreat_brake` 与 `__all__` 字面量,
                # ⛔ 不允许有调用点。
                assert "push_retreat_brake" not in _called_names(tree)
                continue
            if "push_retreat_brake" in _called_names(tree):
                callers.append(str(path.relative_to(_ROOT)))
        assert callers == [], f"`push_retreat_brake` 仍被调用:{callers}"

    def test_retreat_kind_is_registered_as_retired(self):
        assert notify_kinds.KIND_RETREAT in notify_kinds.RETIRED_KINDS

    def test_all_kinds_tuple_is_untouched(self):
        """🔴 `ALL_KINDS` 是冻结元组 —— 退役走**加一张表**,⛔ 不是从它里面删
        (删了旧客户端下一次 `PUT /settings/push` 就缺键 422)。"""
        assert notify_kinds.KIND_RETREAT in notify_kinds.ALL_KINDS
        assert len(notify_kinds.ALL_KINDS) == 14

    def test_only_retreat_is_retired_this_version(self):
        """⛔ 防「顺手多退役一个」:本版退役集合精确等于 `{retreat}`。"""
        assert set(notify_kinds.RETIRED_KINDS) == {notify_kinds.KIND_RETREAT}


# ══════════════════════════════════════════════════════════════════════════
# 判据 4 / 5:专用前端与专用轮询删除 100%
# ══════════════════════════════════════════════════════════════════════════

class TestClientBoardSurfaceGone:
    def test_board_section_file_is_deleted(self):
        assert not (_CLIENT / "Views" / "BoardSection.swift").exists()

    @pytest.mark.parametrize("symbol", ["RetreatBrakeBar", "RetreatBrakeBanner", "BoardSection"])
    def test_retreat_and_board_views_have_no_call_site(self, symbol):
        """🔴 **义 ① 的视图层零引用**(剥掉注释后扫,理由见 `_strip_swift_comments`)。
        ⚠ 与 `nkBoardLabel`(上市板块,义 ②)/ `boardAge`(概念板块年龄,义 ③)无关
        —— 按符号扫,⛔ 不按词扫。"""
        hits = []
        for path in _swift_sources():
            code = _strip_swift_comments(path.read_text(encoding="utf-8"))
            for i, line in enumerate(code.splitlines(), 1):
                if symbol in line:
                    hits.append(f"{path.relative_to(_ROOT)}:{i}: {line.strip()}")
        assert hits == [], f"{symbol} 仍被引用于:{hits}"

    def test_app_model_has_no_board_state_or_loader(self):
        code = _strip_swift_comments(
            (_CLIENT / "App" / "AppModel.swift").read_text(encoding="utf-8"))
        for symbol in ("loadBoard", "retreatWarning", "var board:", "boardTask"):
            assert symbol not in code, f"AppModel 仍有 {symbol}"


class TestNoDedicatedBoardPolling:
    def test_no_sixty_second_client_loop(self):
        """P0.7 #5:全客户端不许再有 60 秒轮询(纳秒字面量)。"""
        hits = [str(p.relative_to(_ROOT)) for p in _swift_sources()
                if "60_000_000_000" in p.read_text(encoding="utf-8")]
        assert hits == [], f"仍有 60s 轮询:{hits}"

    def test_no_client_call_site_for_the_board_endpoint(self):
        """`APIClient.fetchBoard` 可以留(历史 fixture / 兼容解码用),但
        **现役视图与 AppModel 零调用**。⚠ 测试目录不在扫描域内(它就是要调它)。"""
        hits = []
        for path in _swift_sources():
            code = _strip_swift_comments(path.read_text(encoding="utf-8"))
            for i, line in enumerate(code.splitlines(), 1):
                if "fetchBoard()" in line and "func fetchBoard" not in line:
                    hits.append(f"{path.relative_to(_ROOT)}:{i}")
        assert hits == [], f"仍有 `/board` 调用点:{hits}"

    def test_comment_stripper_never_hides_a_real_reference(self):
        """`_strip_swift_comments` 的自检:剥注释后的文本必须是原文的**子集**,
        且**代码行数不变** —— 防它把整行(含代码)吃掉而让上面几条静默变绿。"""
        for path in _swift_sources():
            raw = path.read_text(encoding="utf-8")
            code = _strip_swift_comments(raw)
            assert len(code.splitlines()) == len(raw.splitlines()), path
            assert len(code) <= len(raw), path


# ══════════════════════════════════════════════════════════════════════════
# 判据 7:历史兼容允许保留(**反向守门:防"删过头"**)
# ══════════════════════════════════════════════════════════════════════════

class TestHistoryCompatIsKept:
    """P0.5 停产兼容 —— ⛔ 不破坏性删表删列删端点删 DTO。"""

    def test_retreat_metrics_table_is_not_dropped(self):
        from neckline.db import _SCHEMA  # noqa: PLC2701 —— 守门刻意读私有

        assert "CREATE TABLE IF NOT EXISTS retreat_metrics" in _SCHEMA

    def test_board_endpoint_still_exists(self):
        from neckline.api.app import app

        paths = {r.path for r in app.routes}
        assert "/api/v1/board" in paths

    def test_legacy_board_dtos_still_exist(self):
        from neckline.api import schemas

        for name in ("BoardOut", "BoardEventOut", "RetreatBrakeOut"):
            assert hasattr(schemas, name), f"LEGACY DTO {name} 被误删"

    def test_retired_modules_are_kept_on_disk_with_a_deprecation_banner(self):
        """§3.14-A:留文件、断链路。**文件必须还在,且模块头必须写明退役**
        —— 一个没有 `⛔ DEPRECATED` 抬头的退役件,下一个人会以为它还在跑。"""
        for name in ("invalidation", "retreat", "retreat_store", "mainline"):
            path = _NECKLINE / "sentinel" / f"{name}.py"
            assert path.exists(), f"{name}.py 被误删(它是回滚绳的一部分)"
            doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
            assert "DEPRECATED" in doc and "V2.4.0 P0" in doc, f"{name}.py 缺退役抬头"


# ══════════════════════════════════════════════════════════════════════════
# 🔴 反向守门:P0.3 明令保留的能力,一件都不许坏
# ══════════════════════════════════════════════════════════════════════════

class TestPreservedCapabilitiesIntact:
    """**只扫「不该有的没有」会漏掉「该有的被误删了」** —— 三处同名不同物的另两义
    在这里加正面存在性断言(〇c 那张表)。"""

    def test_card_frozen_invalidation_spec_is_untouched(self):
        """义 ②:卡上 D0 冻结的**判断失效位置**(K8 §十一 交易资格四件套第 4 件)。"""
        from neckline.selection import basket_card as bc

        assert hasattr(bc, "build_invalidation_spec")
        assert hasattr(bc, "INVALIDATE_SPEC_VERSION")

    def test_auction_hit_invalidation_is_untouched(self):
        """义 ③:竞价层命中 D0 失效位(三态:True 命中 / False 看过没命中 / None 判不出)。"""
        from neckline.auction.mech import BasketMech, MemberReading

        assert "hit_invalidation" in MemberReading.__dataclass_fields__
        assert "hit_invalidation_undetermined_reason" in MemberReading.__dataclass_fields__
        assert "hit_invalidation_codes" in BasketMech.__dataclass_fields__

    def test_listing_board_classification_is_untouched(self):
        """`board` 义 ②:上市板块 `MAIN/GEM/STAR/BSE`(⛔ 一行不动)。"""
        from neckline.data.board import Board, classify_by_code

        assert {b.value for b in Board} >= {"MAIN", "GEM", "STAR", "BSE"}
        assert classify_by_code("300001.SZ") is Board.GEM

    def test_take_profit_retrace_is_untouched(self):
        """`retreat` 义 ③:**回落止盈**(拼写相近、语义无关)—— 一行不动。"""
        from neckline.sentinel import holding

        assert hasattr(holding, "check_take_profit")

    def test_holding_sentinel_entrypoints_are_still_wired_into_run_tick(self):
        """P0.3 ③④⑤:持仓亏损警戒 / 离场参考 / 交易时钟所依赖的调用点仍在 `run_tick` 里。"""
        called = _called_names(ast.parse(_ENGINE.read_text(encoding="utf-8")))
        for kept in ("evaluate_holding", "check_exit_reference_reached",
                     "record_intraday_tick", "evaluate_attention", "evaluate_alerts"):
            assert kept in called, f"P0.3 保留能力的调用点 {kept} 被误删"

    def test_position_alerts_channel_exists(self):
        """P0.5+:持仓提醒的**新下发通道**必须在 —— 没有它,删 `/board` 页面就是
        静默弄丢有效提醒(P0.3 末段明令「先迁移再删页面」)。"""
        from neckline.api import schemas

        assert "alerts" in schemas.PositionOut.model_fields
        assert set(schemas.PositionAlertOut.model_fields) == {"eventKey", "verdict", "ts", "level"}
