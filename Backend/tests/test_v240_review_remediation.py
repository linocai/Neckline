"""🔴 **V2.4.0 独立复审整改的守门**(任务书 = `archive/review报告/REVIEW_V2.4.0_20260812.md`)。

复审级别分布 🔴 2 · 🟡 8 · 🔵 15,本文件锁住其中**有机器判据**的那些。
两条 🔴 都不是"算错了",而是这一版立项要根治的那一族病**在别处复发**:

| 条 | 病 | 本文件对应 |
|---|---|---|
| 🔴-1 | 删聚合页面时没清点它承载的全部事件流 → 四类仍在写的提醒失去 App 内唯一落点 | `TestPositionAlertWhitelist` + `TestPositionAlertsEndToEnd`(= 🟡-6) |
| 🔴-2 | 客户端把「从未交叉核验」讲成「两源已交叉核验」 | `TestCrossVerifiedIsNotFaked` + `TestCrossSourceCopyIsConditional` |
| 🟡-4 | 冻结卡里把旧称呼永久写死 + 卡上宣传两条不存在的机械纪律 | `TestDisciplineCopyFollowsCharter` |
| 🟡-7 | 一个读数都没有时伪造 `timestamp_unparseable` | `TestQuoteStatusThirdState` |
| 🟡-8 | aware `captured_at` 让竞价层整层静默零落库 | `TestAwareCapturedAtDoesNotKillTheLayer` |
| 🔵-11 | 版号守门只断集合不断个数 | `TestReviewBlueItems::test_marketing_version_count` |
| 🔵-14 | `Networking/` 根上出现 DTO 时守门看不见 | `TestReviewBlueItems::test_no_decodable_outside_models_dir` |

🔴 **贯穿全篇的那条纪律**:「**没有**」≠「**不满足**」≠「**持平**」≠「**没判**」。
本版两条 🔴 都是它的复发 —— 所以这里的断言全都成对写(**正面:该说的说了** +
**反面:不该说的一个字都没说**),⛔ 不许只锁一边。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.client_sources import CLIENT, models_text, type_block

_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _ROOT / "neckline" / "api" / "app.py"
_AUCTION_CARD = CLIENT / "Views" / "AuctionCardView.swift"
_BASKET_CARD_VIEW = CLIENT / "Views" / "BasketCardView.swift"


# ══════════════════════════════════════════════════════════════════════════
# 🔴-1 持仓提醒白名单(用户裁定 A:四类全收)
# ══════════════════════════════════════════════════════════════════════════

class TestPositionAlertWhitelist:
    """🔴 **双向锁**:四类都在 **且** 退役两类一个都不许混进来。

    单锁一边都不够 —— 只锁"四类都在"会漏掉"顺手把 retreat 也放进来";
    只锁"退役两类不在"会漏掉"哪天有人把 circuit 从白名单里拿掉"。
    """

    def test_whitelist_is_exactly_the_four_ruled_categories(self):
        """用户裁定 A(2026-08-12)逐字:`{holding, attention, circuit, precall}`。"""
        from neckline.api import app

        assert app._POSITION_ALERT_SENTINELS == frozenset(
            {"holding", "attention", "circuit", "precall"})

    def test_retired_sentinels_can_never_enter(self):
        """🔴 **白名单不是黑名单**:`retreat` / `invalidation` 是 P0 退役掉的那两类,
        它们**结构上进不来**;这条把"进不来"变成一个可被机器检查的事实。"""
        from neckline.api import app

        assert app._RETIRED_ALERT_SENTINELS == frozenset({"retreat", "invalidation"})
        assert not (app._POSITION_ALERT_SENTINELS & app._RETIRED_ALERT_SENTINELS)

    def test_filter_is_a_membership_test_not_an_exclusion(self):
        """源码级:过滤那一行必须是「**in 白名单**」,⛔ 不是「not in 黑名单」——
        写成排除法的话,日后再退役一类就会静默漏进来。"""
        src = _APP_PY.read_text(encoding="utf-8")
        body = src.split("def _today_position_alerts(", 1)[1].split("\ndef ", 1)[0]
        assert 'not in _POSITION_ALERT_SENTINELS' in body
        assert "_RETIRED_ALERT_SENTINELS" not in body, \
            "过滤器⛔ 不许用退役表当黑名单(那张表只做反向断言用)"

    @pytest.mark.parametrize("key,level", [
        ("stop_approach", "critical"), ("sector_dive", "warn"),
        ("take_profit", "info"), ("exit_reference", "info"),
        # 🔴 复审 🔴-1 补的四条
        ("position_low_open", "critical"), ("consecutive_stops", "warn"),
        ("decoupled", "warn"), ("basket7", "warn"),
    ])
    def test_every_reachable_event_key_has_a_level(self, key: str, level: str):
        """⚠ `basket<id>` 是**动态键**,精确 dict 逮不到 → 必须走前缀分支。"""
        from neckline.api import app

        assert app._position_alert_level(key) == level

    def test_unknown_event_key_stays_neutral(self):
        """未登记 → `info`(如实中性:⛔ 不冒充紧急,也⛔ 不吞掉)。"""
        from neckline.api import app

        assert app._position_alert_level("something_new_next_year") == "info"

    def test_the_four_writers_are_all_still_writing(self):
        """**正面存在性**:这四类今天确实还在写 —— 否则"补了落点"只是补了个空壳。
        (⛔ 不按词扫,按**常量**与**写入点所在文件**扫。)"""
        from neckline import positions_entry
        from neckline.sentinel import precall

        assert positions_entry.CONSECUTIVE_STOPS_SENTINEL == "circuit"
        assert positions_entry.CONSECUTIVE_STOPS_EVENT_KEY == "consecutive_stops"
        assert precall.EVENT_POS_LOW_OPEN == "position_low_open"
        engine = (_ROOT / "neckline" / "sentinel" / "engine.py").read_text(encoding="utf-8")
        assert 'record_pushed(\n                trade_date, "attention"' in engine


class TestPositionAlertLabelsOnTheClient:
    """服务端多发一个 event_key,客户端少一条映射 = 界面上印一串英文码。"""

    @pytest.mark.parametrize("key", [
        "position_low_open", "consecutive_stops", "decoupled",
    ])
    def test_new_event_keys_have_a_human_label(self, key: str):
        block = _decl_block(models_text(), "func nkPositionAlertLabel(")
        assert f'case "{key}":' in block, f"客户端缺 {key} 的展示层换算"

    def test_dynamic_basket_key_is_handled_by_prefix(self):
        """`basket<篮子 id>` ⛔ 不许写死成 `basket12`。"""
        block = _decl_block(models_text(), "func nkPositionAlertLabel(")
        assert 'hasPrefix("basket")' in block
        assert not re.search(r'case "basket\d', block)

    def test_unknown_key_is_passed_through(self):
        """未识别值**原样返回** —— ⛔ 不静默吞掉一条真实提醒。"""
        block = _decl_block(models_text(), "func nkPositionAlertLabel(")
        assert "return raw" in block


# ══════════════════════════════════════════════════════════════════════════
# 🟡-6 P0.5+ 新通道的**行为**测试(此前全仓零条真的打过 `/positions`)
# ══════════════════════════════════════════════════════════════════════════

class TestPositionAlertsEndToEnd:
    """🔴 复审 🟡-6:`_today_position_alerts` 的过滤条件、level 映射、`app.py` 的挂载
    —— 任何一处改坏,4251 条测试照样全绿,而用户看到的是「今天没有提醒」。
    这一组是**唯一**真的 seed 事件再打端点的用例。"""

    @staticmethod
    def _open(client, AUTH, code="600519.SH"):
        return client.post("/api/v1/positions", headers=AUTH, json={
            "code": code, "buy_price": 100.0, "qty": 100}).json()["position_id"]

    def test_all_four_categories_reach_the_position_card(self, client, AUTH, api_env):
        from neckline import dedup

        self._open(client, AUTH)
        today = date.today()
        for sentinel, key, body in (
            ("holding", "stop_approach", "现价已逼近亏损警戒线"),
            ("precall", "position_low_open", "集合竞价开盘已跌破亏损警戒线"),
            ("circuit", "consecutive_stops", "连续 3 笔以止损离场"),
            ("attention", "decoupled", "从跟随板块转为独立弱势"),
            ("attention", "basket7", "同篮成员集体转弱"),
        ):
            dedup.record_pushed(today, sentinel, "600519.SH", key,
                                payload={"body": body}, db_path=api_env.db_path)

        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        got = {a["eventKey"]: a["level"] for a in h["alerts"]}
        assert got == {
            "stop_approach": "critical", "position_low_open": "critical",
            "consecutive_stops": "warn", "decoupled": "warn", "basket7": "warn",
        }
        bodies = {a["eventKey"]: a["verdict"] for a in h["alerts"]}
        assert bodies["position_low_open"] == "集合竞价开盘已跌破亏损警戒线"

    def test_retired_rows_in_the_db_never_leak_through(self, client, AUTH, api_env):
        """🔴 库里**照旧留着**历史 `retreat` / `invalidation` 行(P0.5「不删历史行」),
        但它们**一条都不许出现在这个通道上**。"""
        from neckline import dedup

        self._open(client, AUTH)
        today = date.today()
        for sentinel in ("retreat", "invalidation"):
            dedup.record_pushed(today, sentinel, "600519.SH", "whatever",
                                payload={"body": "退役的旧行"}, db_path=api_env.db_path)
        dedup.record_pushed(today, "holding", "600519.SH", "stop_approach",
                            payload={"body": "真提醒"}, db_path=api_env.db_path)

        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        assert [a["eventKey"] for a in h["alerts"]] == ["stop_approach"]

    def test_market_level_and_index_scoped_rows_match_no_position(self, client, AUTH, api_env):
        """`market_shock`(scope 空)与 `sector_bid_fade`(scope = 指数码)匹配不到任何
        持仓 —— **结构性**不出现,⚠ 已如实登记 §七 P1-81(⛔ 不在这里给它们编落点)。"""
        from neckline import dedup

        self._open(client, AUTH)
        today = date.today()
        dedup.record_pushed(today, "attention", "", "shock",
                            payload={"body": "大盘突变"}, db_path=api_env.db_path)
        dedup.record_pushed(today, "attention", "000001.SH", "fade",
                            payload={"body": "板块承接消失"}, db_path=api_env.db_path)

        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        assert h["alerts"] == []

    def test_alerts_are_scoped_to_their_own_position(self, client, AUTH, api_env):
        """⛔ 不是「盘中动态页换了个地方」:只画**该持仓自己的**事件。"""
        from neckline import dedup

        self._open(client, AUTH, "600519.SH")
        self._open(client, AUTH, "000001.SZ")
        dedup.record_pushed(date.today(), "holding", "600519.SH", "stop_approach",
                            payload={"body": "茅台的提醒"}, db_path=api_env.db_path)

        by_code = {h["code"]: h["alerts"]
                   for h in client.get("/api/v1/positions", headers=AUTH).json()["holdings"]}
        assert [a["eventKey"] for a in by_code["600519.SH"]] == ["stop_approach"]
        assert by_code["000001.SZ"] == []

    def test_no_alerts_is_an_empty_array_not_a_fabricated_all_clear(self, client, AUTH):
        """「今天没有提醒」与「一切正常」是两回事 —— 空数组,⛔ 不合成一条「暂无异常」。"""
        self._open(client, AUTH)
        h = client.get("/api/v1/positions", headers=AUTH).json()["holdings"][0]
        assert h["alerts"] == []


# ══════════════════════════════════════════════════════════════════════════
# 🔴-2 「跨源冲突为空」到底是比过了还是没得比
# ══════════════════════════════════════════════════════════════════════════

def _q(code="600519.SH", *, ts="2026-08-12 09:25:03", price=10.0, pre_close=10.0,
       open_=10.0, source="sina", volume=100.0, amount=1000.0):
    from neckline.data.realtime import Quote

    return Quote(code=code.split(".")[0], name="测试", price=price, pre_close=pre_close,
                 open=open_, high=max(price, open_), low=min(price, open_),
                 volume=volume, amount=amount, ts=ts, source=source)


_D1 = date(2026, 8, 12)
_CAPTURED = datetime(2026, 8, 12, 9, 26, 30)


class TestCrossVerifiedIsNotFaked:
    """🔴 `conflict is None` 有**两种**成因,`cross_verified` 才分得开它们。"""

    def test_both_sources_valid_counts_as_verified(self):
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q(),
                                       backup=_q(source="tencent")),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.cross_verified is True and qq.conflict is None
        assert qq.to_dict()["cross_verified"] is True

    def test_backup_missing_is_not_verified(self):
        """**备源整体失败的早晨** —— 这正是复审给的失败场景。"""
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q(), backup=None),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.freshness == "fresh"          # 读数本身是好的
        assert qq.conflict is None               # 但这只说明"没得比"
        assert qq.cross_verified is False        # 🔴 ⛔ 不许讲成"已核验"

    def test_backup_present_but_invalid_is_not_verified(self):
        """备源回来了、但读数不合格(如带的是**上一交易日**的时间戳)——
        `detect_conflict` 压根没跑过。"""
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        stale = _q(source="tencent", ts="2026-08-11 09:25:03")
        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q(), backup=stale),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.conflict is None and qq.cross_verified is False
        assert len(qq.checks) == 2, "两源读数都要留痕(K8 §二十)"

    def test_no_quote_at_all_is_not_verified(self):
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        _, qq = resolve_dual("999999.SZ", DualQuote(code="999999.SZ"),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.cross_verified is False and qq.checks == ()

    def test_the_predicate_has_exactly_one_implementation(self):
        """🔴 判别式单一源:`resolve_dual` 触发 `detect_conflict` 的那个 `if` 必须
        **调 `_is_cross_verified`**,⛔ 不许在那里再写一遍条件(两处各写一遍 =
        「有没有对拍过」与「对拍出没出冲突」迟早各说各话)。"""
        src = (_ROOT / "neckline" / "auction" / "quality.py").read_text(encoding="utf-8")
        body = src.split("def resolve_dual(", 1)[1].split("\ndef ", 1)[0]
        assert "if _is_cross_verified(checks):" in body
        assert "cp.ok and cb.ok" not in body, "对拍条件被抄了第二份"

    def test_api_passes_it_through_without_re_deriving(self):
        """服务端 shaping ⛔ 不许用 `checks` 重推一遍(那就是第二份判别式);
        老行没这一键 → `False`(保守方向)。"""
        from neckline.api.app import _shape_auction_quality_details

        rows = _shape_auction_quality_details({
            "600519.SH": {"ts_code": "600519.SH", "freshness": "fresh",
                          "cross_verified": True, "checks": []},
            "000001.SZ": {"ts_code": "000001.SZ", "freshness": "fresh", "checks": [
                {"role": "primary", "status": "fresh"},
                {"role": "backup", "status": "fresh"}]},   # 老行:没有 cross_verified 键
        })
        got = {r.tsCode: r.crossVerified for r in rows}
        assert got == {"600519.SH": True, "000001.SZ": False}


class TestCrossSourceCopyIsConditional:
    """🔴 守门**必须跨到屏幕这一层** —— 上一版正是「Python 侧五处说对了、Swift 一句
    无条件断言把它推翻」而全绿。"""

    def test_the_unconditional_claim_is_gone(self):
        code = _AUCTION_CARD.read_text(encoding="utf-8")
        assert "两源已交叉核验)。" not in code, \
            "那句无条件的正面断言又回来了(复审 🔴-2)"

    def test_the_row_branches_on_the_server_flag(self):
        block = _decl_block(_AUCTION_CARD.read_text(encoding="utf-8"),
                            "private var crossSourceRow: some View {")
        assert "crossVerifiedCount" in block and "hasDualSourceLedger" in block
        # 四种状态各说各的话
        assert "不能高置信输出" in block                    # ① 有冲突
        assert "生成于双源核验上线之前" in block             # ② 老报告
        assert "一只都没完成两源对拍" in block               # ③ 没得比
        assert "全部完成两源对拍" in block                   # ④ 全对拍过

    def test_the_never_verified_branch_says_it_is_not_an_all_clear(self):
        """③ 那一支必须**说出口**「不是已核对无冲突」,⛔ 不许只说"为空"。"""
        block = _decl_block(_AUCTION_CARD.read_text(encoding="utf-8"),
                            "private var crossSourceRow: some View {")
        never = [ln for ln in block.splitlines() if "一只都没完成两源对拍" in ln][0]
        assert "⛔ 不是「已核对无冲突」" in never

    def test_client_only_counts_never_re_derives(self):
        """客户端只**数**服务端那一位,⛔ 不自己判"两条 checks 都 fresh 就算核验过"。"""
        block = _decl_block(models_text(), "var crossVerifiedCount: Int")
        assert "filter(\\.crossVerified)" in block
        assert "checks" not in block and "fresh" not in block

    def test_dto_decodes_the_new_key_with_a_conservative_default(self):
        body = type_block("AuctionQualityDetail")
        assert "crossVerified = try c.decodeIfPresent(Bool.self, forKey: .crossVerified) ?? false" in body
        assert "case conflict, crossVerified, errors, checks" in body

    def test_server_and_client_agree_on_the_key(self):
        schemas = (_ROOT / "neckline" / "api" / "schemas.py").read_text(encoding="utf-8")
        server = schemas.split("class AuctionQualityDetailOut(BaseModel):", 1)[1] \
                        .split("\nclass ", 1)[0]
        assert "crossVerified: bool = False" in server
        assert "crossVerified" in type_block("AuctionQualityDetail")


# ══════════════════════════════════════════════════════════════════════════
# 🟡-4 纪律称呼随章程派生(冻结卡永不回填 → 上产后修不回来)
# ══════════════════════════════════════════════════════════════════════════

class TestDisciplineCopyFollowsCharter:
    def test_card_label_uses_the_single_source(self):
        from neckline.selection.basket_card import discipline_labels
        from neckline.strategy import charter_copy

        assert charter_copy.stop_line_label(True) in discipline_labels(0.05, None, advisory=True)[0]
        assert charter_copy.stop_line_label(False) in discipline_labels(0.05, 0.08)[0]

    def test_to_card_json_derives_advisory_from_the_frozen_charter(self):
        """🔴 冻结卡是 `INSERT OR IGNORE`、**永不回填** —— 线名必须在**写进去那一刻**
        就是对的。判据取的是**这张卡当时那版**章程(⛔ 不是"现役")。"""
        src = (_ROOT / "neckline" / "selection" / "basket_card.py").read_text(encoding="utf-8")
        body = src.split("def to_card_json(", 1)[1].split("\n    def ", 1)[0]
        assert "brain.stop_is_advisory(" in body
        assert "self.charter_version" in body and "self.loss_warning_action" in body

    def test_k8_charter_card_never_says_the_old_name(self):
        from neckline.selection.basket_card import discipline_labels

        labels = discipline_labels(0.05, None, advisory=True)
        assert labels[0] == "章程亏损警戒线 −5.0%"
        assert "止损" not in labels[0], "K8 口径下⛔ 不许再印「止损」"

    def test_historical_charter_still_says_the_old_truth(self):
        """两向都说真话:强制条件单口径(老章程)照旧叫「止损线」。"""
        from neckline.selection.basket_card import discipline_labels

        assert discipline_labels(0.05, 0.08) == ["章程止损线 −5.0%", "回落止盈 8.0%"]

    def test_stale_price_note_no_longer_presumes_a_time_exit_rule(self):
        """🔴 **最终 DoD 第 15 条的第三处**(复审只点名了两处,整改时顺带逮到)。

        `PositionsView` 停牌 / 无行情那一支写着「**时间退出判向挂起** = 停牌期间不推进
        D 计数、不触发离场判定」—— 而 `v2.3-k8` 的 `max_hold_days = nil`,**根本没有
        时间退出这项纪律**,也就没有"判向"可挂起。老章程下它仍是真话 →
        ⛔ 不是删掉,而是按**既有唯一判据** `hasTimeExitRule` 分档
        (⛔ 不新立一套判据、⛔ 无条款时不补一句新话 —— 头部
        `timeExitDisclosure` 已经在说「本版无机械时间退出」了)。"""
        code = (CLIENT / "Views" / "PositionsView.swift").read_text(encoding="utf-8")
        lines = [ln for ln in code.splitlines()
                 if "时间退出判向挂起" in ln and "Text" not in ln and "//" not in ln]
        assert len(lines) == 1, "那句话不止一处 / 找不到了"
        block = _decl_block(code, "if position.hasTimeExitRule {")
        assert "时间退出判向挂起" in block, "⛔ 那句话必须被 `hasTimeExitRule` 挡住"
        # 反向:无条款那一支⛔ 不许再出现「时间退出」四个字
        else_part = block.split("} else {", 1)[1] if "} else {" in block else ""
        assert else_part and "时间退出" not in else_part

    def test_basket_card_view_no_longer_advertises_absent_disciplines(self):
        """🔴 **最终 DoD 第 15 条**:当前 K8 路径不再宣传机械回落止盈与时间退出。
        `v2.3-k8` 的 `take_profit_retrace=None` / `max_hold_days=None` —— 那句
        「该不该走由持仓纪律(止损 / 回落止盈 / 时间退出)管」宣传了三条,其中两条是空的。"""
        code = _BASKET_CARD_VIEW.read_text(encoding="utf-8")
        line = [ln for ln in code.splitlines()
                if "失效说的是" in ln and "Text(" in ln]
        assert len(line) == 1, "那句话不止一处 / 找不到了"
        assert "回落止盈" not in line[0] and "时间退出" not in line[0]
        assert "纪律标签" in line[0], "得指向这张卡自己冻结的那份,⛔ 不是干脆不说"


# ══════════════════════════════════════════════════════════════════════════
# 🟡-7 一个读数都没有 ≠ 一次证明没发生过的校验失败
# ══════════════════════════════════════════════════════════════════════════

class TestQuoteStatusThirdState:
    def test_empty_checks_reports_no_record_not_a_fake_failure(self):
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        _, qq = resolve_dual("999999.SZ", DualQuote(code="999999.SZ"),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.checks == () and qq.errors == ()
        assert qq.status == "", "⛔ 不许伪造一个 `timestamp_unparseable`"

    def test_a_real_unparseable_timestamp_still_reports_it(self):
        """**反向**:真的解不出时间戳时,那个码照旧要报出来(⛔ 别把真信号一起吞掉)。"""
        from neckline.auction import QS_TIMESTAMP_UNPARSEABLE
        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q(ts="集合竞价 合成")),
                             trade_date=_D1, captured_at=_CAPTURED)
        assert qq.status == QS_TIMESTAMP_UNPARSEABLE

    def test_client_already_has_a_label_for_the_empty_state(self):
        """`""` 在客户端已有正确标签「本次未记录」——⛔ 不需要新枚举值。"""
        assert 'case "": return "本次未记录"' in models_text()


# ══════════════════════════════════════════════════════════════════════════
# 🟡-8 aware `captured_at` 曾让竞价层整层静默零落库
# ══════════════════════════════════════════════════════════════════════════

class TestAwareCapturedAtDoesNotKillTheLayer:
    def test_aware_captured_at_no_longer_raises(self):
        """本仓房规有两套写法并存(`datetime.now()` 与 `datetime.now(CN_TZ)`),
        而 `resolve_dual` 那个循环**没有包 try/except** —— 抛出去就是每天早晨静默零落库。"""
        from neckline.auction.quality import resolve_dual
        from neckline.calendar import CN_TZ
        from neckline.data.realtime import DualQuote

        aware = _CAPTURED.replace(tzinfo=CN_TZ)
        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q()),
                             trade_date=_D1, captured_at=aware)
        assert qq.freshness == "fresh"

    def test_aware_and_naive_give_the_identical_verdict(self):
        """归一必须**等价**,⛔ 不是"能跑就行":同一时刻两种写法结论逐位相同。"""
        from neckline.auction.quality import resolve_dual
        from neckline.calendar import CN_TZ
        from neckline.data.realtime import DualQuote

        dual = DualQuote(code="600519.SH", primary=_q(), backup=_q(source="tencent"))
        _, naive = resolve_dual("600519.SH", dual, trade_date=_D1, captured_at=_CAPTURED)
        _, aware = resolve_dual("600519.SH", dual, trade_date=_D1,
                                captured_at=_CAPTURED.replace(tzinfo=CN_TZ))
        assert naive.to_dict() == aware.to_dict()

    def test_utc_aware_is_converted_not_just_stripped(self):
        """🔴 ⛔ **不是** `replace(tzinfo=None)`:那会把一个 UTC 时刻当北京时刻用
        (差 8 小时 → 整份快照全判 `future_timestamp`)。"""
        from datetime import timezone

        from neckline.auction.quality import resolve_dual
        from neckline.data.realtime import DualQuote

        utc = (_CAPTURED - timedelta(hours=8)).replace(tzinfo=timezone.utc)   # 同一时刻
        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=_q()),
                             trade_date=_D1, captured_at=utc)
        assert qq.freshness == "fresh"
        assert "future_timestamp" not in qq.errors

    def test_zero_tolerance_is_untouched(self):
        """⚠ 归一**不许**顺手放宽零容差(用户裁定 #2):源时间晚于抓取时刻仍然当场判失败。"""
        from neckline.auction.quality import resolve_dual
        from neckline.calendar import CN_TZ
        from neckline.data.realtime import DualQuote

        future = _q(ts="2026-08-12 09:26:31")     # 比 captured_at 晚 1 秒
        _, qq = resolve_dual("600519.SH",
                             DualQuote(code="600519.SH", primary=future),
                             trade_date=_D1, captured_at=_CAPTURED.replace(tzinfo=CN_TZ))
        assert "future_timestamp" in qq.errors


# ══════════════════════════════════════════════════════════════════════════
# 🔵 若干条(有机器判据的那些)
# ══════════════════════════════════════════════════════════════════════════

class TestReviewBlueItems:
    def test_marketing_version_count(self):
        """🔵-11:`test_every_marketing_version_in_pbxproj_is_the_same` 只断集合、
        不断个数 —— 某次生成把 project 级那处整个丢掉,它仍绿(正是 P4.4 要堵的
        盲点换了个形状)。这里补个数。"""
        pbx = (_ROOT.parent / "App" / "Neckline.xcodeproj" / "project.pbxproj").read_text(
            encoding="utf-8")
        hits = re.findall(r"MARKETING_VERSION = ([0-9.]+);", pbx)
        assert len(hits) == 4, f"pbxproj 的 MARKETING_VERSION 应恰好 4 处,实得 {len(hits)}"
        assert len(set(hits)) == 1

    def test_no_new_dto_file_escapes_the_models_scan_domain(self):
        """🔵-14:`models_text()` 的扫描域只有 `Networking/Models/` —— DTO 若落在
        `Networking/` 根上,35 处用它的守门**看不见**它。`CLAUDE.md` 已写规矩,
        此前**没有机器判据**,补上。

        ⚠ **判据不是"根上不许有 Decodable"**(复审建议的那句话直译过来是错的):
        `APIClient.swift` 里那十几个 `private struct XxxResponse: Decodable` 是
        **信封类型**,与解它的客户端同处一文件是刻意的,搬进 `Models/` 反而不对。
        真正要防的是「**多出来一个谁也没注意到的文件**」→ 判据改成:根上的
        `.swift` 必须在这张**带理由的名单**里,新增一个当场红,逼人当面决定它
        属不属于扫描域。"""
        from tests.client_sources import NETWORKING

        allowed = {
            "APIClient.swift",   # 端点调用面 + 私有信封类型(`networking_swift_text()` 覆盖)
            "AppConfig.swift",   # 基址 / token 读取,零 DTO
        }
        found = {p.name for p in NETWORKING.glob("*.swift")}     # 只看根,不递归
        assert found == allowed, (
            f"`Networking/` 根上的文件集合变了:{sorted(found)}(名单 {sorted(allowed)})。\n"
            f"新增 DTO 必须落在 `Networking/Models/` 下,否则它不在 `models_text()` 的"
            f"扫描域里,一堆**缺席断言**会在它身上静默失明。")

    def test_every_models_file_is_covered_by_a_sentinel(self):
        """同族的另一半:`Models/` 里每一份都得有哨兵 —— 否则新增一份 DTO 文件后,
        `_assert_sentinels()` 覆盖不到它,「读到的东西比预期少」又会变回静默的。"""
        from tests.client_sources import _SENTINELS, model_files

        assert {p.name for p in model_files()} == set(_SENTINELS)

    def test_git_diff_check_guard_covers_staged_changes(self):
        """🔵-12:`git diff --check` 漏 `--cached` —— 暂存区里的空白错误照样溜过去。"""
        src = (_ROOT / "tests" / "test_v240_p4_release.py").read_text(encoding="utf-8")
        assert '"--cached"' in src, "`git diff --check` 守门仍未覆盖暂存区"

    def test_batch_id_shape_is_locked(self):
        """🔵-4:`uuid4().hex[:12]` 的 `12` 此前无出处、也没有守门锁格式。
        ⚠ 它**不是阈值**(不参与任何比较)—— 但一个没被锁住的形状迟早会漂,
        而 `batch_id` 是「这一批到底切了哪四条线」的唯一联结键。"""
        from neckline.selection import pack

        assert pack._BATCH_ID_HEX_LEN == 12
        assert pack._BATCH_ID_RE.match("set-0123456789ab")
        assert not pack._BATCH_ID_RE.match("set-0123")
        src = (_ROOT / "neckline" / "selection" / "pack.py").read_text(encoding="utf-8")
        assert "uuid.uuid4().hex[:_BATCH_ID_HEX_LEN]" in src, "长度⛔ 不许再写死在 f-string 里"

    def test_version_guard_reads_the_version_instead_of_hardcoding_it(self):
        """🔵-10:把 `"2.4.0"` 写死在守门里 → 以后每次升版都得改守门测试,
        而"为变绿改守门"正是 P4.5 禁止的习惯。"""
        src = (_ROOT / "tests" / "test_v240_p4_release.py").read_text(encoding="utf-8")
        assert "app_mod.VERSION" in src or "from neckline.api.app import VERSION" in src


# ══════════════════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════════════════

_SWIFT_DECL_RE = re.compile(r"\n(?:    )?(?:private |internal |public )?"
                            r"(?:@ViewBuilder\s+)?(?:var|func|struct|enum) ")


def _decl_block(text: str, decl: str) -> str:
    """从某个声明切到**下一个同层声明**为止。

    ⚠ 与 `test_v240_p3_frontend.py::_decl_slice` 同一条纪律:⛔ 别拿 `// MARK:`
    当锚点(剥过注释的文本里 MARK 行已经没了,切出来会是"到文件尾" = 断言静默
    退化成"整份文件里有没有")。
    """
    i = text.find(decl)
    assert i >= 0, f"声明不见了:{decl!r}"
    m = _SWIFT_DECL_RE.search(text, i + len(decl))
    return text[i:m.start()] if m else text[i:]
