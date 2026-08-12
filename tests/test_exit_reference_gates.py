"""V2-⑪-D 两道机械 sanity 闸(PROJECT_PLAN §五 ⑪-D,2026-08-03 planner 裁定)。

闸①(卡生成,`selection/basket_card.py::clamp_exit_reference`):`exit_low > D0 close`。
闸②(开仓武装,`positions_entry.py`):继承的 `exit_low ≤ 实际成交价` → 该票
`take_profit` kind **不武装**(⛔ 只是不武装 —— 不改计划、不阻断开仓、不影响其他 kind)。

两道闸**零发明阈值**:闸① 靠定义(压力位在现价之上),闸② 靠成交价本身。守门断言
另外锁死「⛔ 不夹涨跌停、⛔ 不加上界」两条 ⑦ 原决定没有被这次改动带走。
"""

from __future__ import annotations

import inspect
import json
from datetime import date, datetime, time

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal

from neckline import positions_entry as pe
from neckline.db import connection
from neckline.selection import basket_card as bc
from neckline.sentinel.engine import _load_exit_references
from neckline.sentinel.positions import open_position

pytestmark = pytest.mark.usefixtures("isolated_env")


def _mech(close=10.0, limit_up=11.0, limit_down=9.0):
    return bc.MemberMech(ts_code="600001.SH", name="甲", close=close,
                         limit_up=limit_up, limit_down=limit_down)


def _entry_of(item: dict, mech: bc.MemberMech) -> dict:
    """跑一遍真正的 `build_basket_card`,取该成员在卡上的那一节 —— 断言的是**卡面
    最终形态**,不是夹逼函数的返回值(闸要证明的是"根本走不到推送",不是"函数返回
    了个码")。"""
    class _M:
        ts_code = "600001.SH"
        name = "甲"
        role_llm = "core"
        role_mech = None
        role_conflict = 0
        reason = "r"
        is_primary = 1

    class _B:
        basket_key = "k1"
        trade_date = "20260723"
        members = (_M(),)

    card = bc.build_basket_card(
        _B(), date(2026, 7, 23), payload={"entries": [{"ts_code": "600001.SH", **item}]},
        mechs={"600001.SH": mech}, llm_stage=bc.LLM_OK, with_tags=False,
    )
    return card.to_card_json()["members"][0]


# ══════════════════════════════════════════════════════════════════════════
# 闸①:卡生成时 exit_low > D0 close
# ══════════════════════════════════════════════════════════════════════════

class TestCardGate:
    def test_exit_low_below_close_is_rejected_with_precise_reason(self):
        m = _entry_of({"exit_low": 9.5, "exit_high": 12.0}, _mech(close=10.0))
        assert m["exit_reference"] is None
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE
        assert "不高于当日收盘价" in m["exit_reference_unavailable_reason"]

    def test_exit_low_equal_to_close_is_rejected_boundary(self):
        """边界等值:`exit_low == close` → 拦(plan 要求的是**严格**高于;压力位不能
        就是现价本身)。"""
        m = _entry_of({"exit_low": 10.0, "exit_high": 12.0}, _mech(close=10.0))
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE

    def test_epsilon_boundary_just_above_close_is_still_rejected(self):
        """容差内的"高一点点"仍算不高于(与项目 `_EPS` 体例同向:边界从严)。"""
        m = _entry_of({"exit_low": 10.0 + 1e-12, "exit_high": 12.0}, _mech(close=10.0))
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE

    def test_absurd_low_exit_is_stopped_at_card_time_never_reaches_push(self):
        """reviewer 那个极端例的回归:LLM 给 `exit_low=0.01` → **卡生成期就被拦**,
        根本不会经 ⑩ 继承成为 APNs `take_profit` 的触发位置。"""
        m = _entry_of({"exit_low": 0.01, "exit_high": 0.02}, _mech(close=10.0))
        assert m["exit_reference"] is None
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE

    def test_missing_close_is_a_separate_state_not_conflated(self):
        """「没有收盘价可比」与「比过了不合格」是两件事,分两个码(项目一贯的
        「没有 ≠ 不满足」纪律)。"""
        m = _entry_of({"exit_low": 13.0, "exit_high": 15.0},
                      _mech(close=None, limit_up=None, limit_down=None))
        assert m["exit_reference"] is None
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_NO_CLOSE
        assert m["exit_reference_clamp"] != bc.EXIT_CLAMP_REJECTED_NOT_ABOVE_CLOSE

    def test_malformed_still_takes_priority_over_close_check(self):
        m = _entry_of({"exit_low": 15.0, "exit_high": 13.0}, _mech(close=10.0))
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_REJECTED_MALFORMED

    def test_absent_is_still_absent_not_rejected(self):
        m = _entry_of({}, _mech(close=10.0))
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_ABSENT

    # —— ⑦ 原决定守门:这次改动**只加下界**,不许顺手加别的 ————————————
    def test_guard_still_not_clamped_to_limit_band(self):
        """压力位高于次日涨停仍正常落卡(⑦ 原决定:压力位可能几个交易日后才到)。"""
        m = _entry_of({"exit_low": 13.0, "exit_high": 15.0}, _mech(close=10.0, limit_up=11.0))
        assert m["exit_reference"] == {"low": 13.0, "high": 15.0}
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_OK

    def test_guard_no_upper_bound_invented(self):
        """`exit_high` 荒谬地高只会永不触发(无假推送、无伤害)→ ⛔ 不许发明上限。"""
        m = _entry_of({"exit_low": 13.0, "exit_high": 9_999_999.0}, _mech(close=10.0))
        assert m["exit_reference"] == {"low": 13.0, "high": 9999999.0}
        assert m["exit_reference_clamp"] == bc.EXIT_CLAMP_OK

    def test_guard_close_param_has_no_default(self):
        """闸是红线,签名不留"忘了传就静默关闸"的口子。"""
        sig = inspect.signature(bc.clamp_exit_reference)
        assert sig.parameters["close"].default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            bc.clamp_exit_reference({"low": 1.0, "high": 2.0})  # type: ignore[call-arg]


# ══════════════════════════════════════════════════════════════════════════
# 闸②:开仓武装判定(纯函数)
# ══════════════════════════════════════════════════════════════════════════

class TestArmingGate:
    def test_exit_low_above_buy_price_is_armed(self):
        armed, reason = pe.evaluate_exit_reference_arming({"low": 13.0, "high": 15.0}, 10.0)
        assert armed is True and reason is None

    def test_exit_low_below_buy_price_is_not_armed(self):
        armed, reason = pe.evaluate_exit_reference_arming({"low": 13.0, "high": 15.0}, 14.0)
        assert armed is False and reason == pe.ARM_REASON_BELOW_ENTRY_PRICE
        assert pe.exit_reference_arm_note(reason) == "离场参考低于你的成本,本票不做触达提醒"

    def test_exit_low_equal_buy_price_is_not_armed_boundary(self):
        armed, reason = pe.evaluate_exit_reference_arming({"low": 13.0, "high": 15.0}, 13.0)
        assert armed is False and reason == pe.ARM_REASON_BELOW_ENTRY_PRICE

    def test_no_exit_reference_is_its_own_reason(self):
        armed, reason = pe.evaluate_exit_reference_arming(None, 10.0)
        assert armed is False and reason == pe.ARM_REASON_NO_EXIT_REFERENCE

    def test_user_mute_wins_over_a_perfectly_good_number(self):
        armed, reason = pe.evaluate_exit_reference_arming(
            {"low": 13.0, "high": 15.0}, 10.0, muted=True)
        assert armed is False and reason == pe.ARM_REASON_USER_MUTED


# ══════════════════════════════════════════════════════════════════════════
# 闸② 端到端:开仓 → position_plans → 哨兵旁路 E 读武装态
# ══════════════════════════════════════════════════════════════════════════

def _seed_basket(db_path, d0: date, *, exit_low: float, exit_high: float):
    card = {
        "members": [{
            "ts_code": "600001.SH",
            "entry_zone": {"low": 9.5, "high": 10.5, "why": "示例"},
            "entry_zone_clamp": "ok",
            "max_chase": 11.0, "max_chase_clamp": "ok",
            "exit_reference": {"low": exit_low, "high": exit_high},
            "exit_reference_clamp": "ok",
        }],
        "verification_spec": {"min_members_hit": 1},
        "invalidation_spec": {"any_of": ["close_below_stop_line"]},
        "risks": ["风险一"],
    }
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), "k1", "篮k1", "驱动", "theme", 1, "K4-pack-v1", 1,
             "v1.3.3", "auto", "ok", "2026-07-23T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
            " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (bid, "600001.SH", "core", None, 0, "理由", 1, "2026-07-23T00:00:00+08:00"),
        )
        conn.execute(
            "INSERT INTO basket_cards (basket_id, version, card_json, stop_pct,"
            " take_profit_retrace, charter_version, pack_version, engine_api_version, created_at)"
            " VALUES (?,1,?,?,?,?,?,?,?)",
            (bid, json.dumps(card, ensure_ascii=False), 0.05, 0.08, "v1.3.3",
             "K4-pack-v1", 1, "2026-07-23T00:00:00+08:00"),
        )
    return bid


class TestArmingEndToEnd:
    @pytest.fixture()
    def days(self, isolated_env):
        ds = business_days(date(2026, 7, 1), 30)
        insert_trade_cal(isolated_env, ds)
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "甲", "market": "主板"}])
        return ds

    def test_buy_below_exit_low_arms_the_kind(self, isolated_env, days):
        d0, buy_day = days[-2], days[-1]
        _seed_basket(isolated_env.db_path, d0, exit_low=13.0, exit_high=15.0)
        r = pe.record_buy("600001.SH", 10.0, 100, buy_day, db_path=isolated_env.db_path)
        plan = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        assert plan["exit_reference_armed"] is True
        assert plan["exit_reference_armed_reason"] is None
        assert plan["exit_reference_armed_note"] is None
        assert _load_exit_references([r.position_id], isolated_env.db_path) == {
            r.position_id: (13.0, 15.0)}

    def test_buy_above_exit_low_does_not_arm_but_keeps_the_plan_intact(self, isolated_env, days):
        """⛔ 只是不武装:计划正文(离场参考本身)**原样保留**、开仓照常成功。"""
        d0, buy_day = days[-2], days[-1]
        _seed_basket(isolated_env.db_path, d0, exit_low=13.0, exit_high=15.0)
        r = pe.record_buy("600001.SH", 14.0, 100, buy_day, db_path=isolated_env.db_path)
        assert r.position_id >= 1                       # 开仓没被阻断
        plan = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        assert plan["exit_reference"] == {"low": 13.0, "high": 15.0}   # 计划没被改
        assert plan["entry_zone"] == {"low": 9.5, "high": 10.5, "why": "示例"}
        assert plan["risks"] == ["风险一"]
        assert plan["exit_reference_armed"] is False
        assert plan["exit_reference_armed_reason"] == pe.ARM_REASON_BELOW_ENTRY_PRICE
        assert plan["exit_reference_armed_note"] == "离场参考低于你的成本,本票不做触达提醒"
        # 哨兵旁路 E 拿不到这只票的区间 → 该 kind 本票不发
        assert _load_exit_references([r.position_id], isolated_env.db_path) == {}

    def test_engine_read_is_fail_closed_on_legacy_rows_without_arm_key(self, isolated_env, days):
        """缺武装位的老行 = **没过闸**,不是"过了但没写" → 不武装(fail-closed)。"""
        _, buy_day = days[-2], days[-1]
        pid = open_position("600001.SH", 10.0, 100, buy_day, db_path=isolated_env.db_path)
        legacy = {"available": True, "exit_reference": {"low": 13.0, "high": 15.0},
                  "exit_reference_clamp": "ok"}
        with connection(isolated_env.db_path) as conn:
            conn.execute(
                "INSERT INTO position_plans (position_id, version, source_basket_id,"
                " source_card_version, plan_json, note, created_at) VALUES (?,1,?,?,?,?,?)",
                (pid, None, None, json.dumps(legacy, ensure_ascii=False), None,
                 "2026-07-23T00:00:00+08:00"),
            )
        assert _load_exit_references([pid], isolated_env.db_path) == {}

    def test_new_plan_version_reevaluates_the_gate_and_inherits_the_mute(self, isolated_env, days):
        """写个新版本不能成为绕开闸② 的后门;用户静音位承袭上一版。"""
        d0, buy_day = days[-2], days[-1]
        _seed_basket(isolated_env.db_path, d0, exit_low=13.0, exit_high=15.0)
        r = pe.record_buy("600001.SH", 10.0, 100, buy_day, db_path=isolated_env.db_path)

        # 用户把离场参考改到成交价以下 → 新版本重新过闸,不武装
        pe.create_position_plan_version(
            r.position_id, {"available": True, "exit_reference": {"low": 9.0, "high": 9.5}},
            db_path=isolated_env.db_path,
        )
        plan = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        assert plan["exit_reference_armed"] is False
        assert plan["exit_reference_armed_reason"] == pe.ARM_REASON_BELOW_ENTRY_PRICE

        # 静音 → 再写一版合法区间,静音仍在(承袭),武装态仍为 False
        pe.set_exit_reference_muted(r.position_id, True, db_path=isolated_env.db_path)
        pe.create_position_plan_version(
            r.position_id, {"available": True, "exit_reference": {"low": 13.0, "high": 15.0}},
            db_path=isolated_env.db_path,
        )
        plan = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        assert plan["exit_reference_muted"] is True
        assert plan["exit_reference_armed"] is False
        assert plan["exit_reference_armed_reason"] == pe.ARM_REASON_USER_MUTED

        # 取消静音 → 重新武装(数字本来就合法)
        pe.set_exit_reference_muted(r.position_id, False, db_path=isolated_env.db_path)
        plan = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        assert plan["exit_reference_muted"] is False
        assert plan["exit_reference_armed"] is True
        assert _load_exit_references([r.position_id], isolated_env.db_path) == {
            r.position_id: (13.0, 15.0)}

    def test_mute_switch_does_not_touch_the_plan_body(self, isolated_env, days):
        d0, buy_day = days[-2], days[-1]
        _seed_basket(isolated_env.db_path, d0, exit_low=13.0, exit_high=15.0)
        r = pe.record_buy("600001.SH", 10.0, 100, buy_day, db_path=isolated_env.db_path)
        before = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        pe.set_exit_reference_muted(r.position_id, True, db_path=isolated_env.db_path)
        after = pe.latest_position_plan(r.position_id, db_path=isolated_env.db_path)["plan"]
        body_keys = ("entry_zone", "max_chase", "exit_reference", "verification_spec",
                     "invalidation_spec", "risks", "source_basket_key")
        assert {k: after[k] for k in body_keys} == {k: before[k] for k in body_keys}


# ══════════════════════════════════════════════════════════════════════════
# 豁免前提③:文案本身(§2.8-C-3「缺一即豁免失效」)
# ══════════════════════════════════════════════════════════════════════════

class TestExemptionPremiseThree:
    def test_copy_states_it_is_a_plan_reference_not_a_take_profit_line(self):
        """§2.8-C-3 前提③ 原文要求文案「必须点明**这是你计划里的参考位、不是止盈线**」,
        后半句还点名了回落止盈那条机械纪律;⑪-D 接线时核对发现原文案缺后半句 → 曾经补齐。

        🔴 **V2.4.0 P3.2 取代**:`v2.3-k8` 已经没有那条机械纪律,继续断言那半句出现
        就是在守护一句谎话 —— 版本裁定已把前提③ 后半句改写为「离场参考是计划参考、
        不是止盈信号」(全文见 `PROJECT_PLAN.md` §五 V2.4.0 P3.2 与 §2.8-C-3 正文标注)。
        前提①②④ 一字不变、仍然缺一即豁免失效,本例继续把"新"前提③ 锁成机器断言。

        ⚠ **断言的是语义三件、不是某一条常量出现**:事件文案走施工图 P3.2 的逐字骨架
        (`charter_copy.exit_reference_reached_copy`)——「**你计划中的**离场参考区间」
        答「这是你计划里的参考位」、「**这不是止盈信号**」答「不是止盈线」、
        「**是否离场由你判断**」答「纯告知不指令」。三件缺一即豁免失效。"""
        from tests.test_sentinel_holding import _position, _quote   # 复用既有构造

        from neckline.sentinel.holding import check_exit_reference_reached

        text = check_exit_reference_reached(_position(), _quote(14.0), 13.0, 15.0)
        assert "你计划中的离场参考区间" in text     # ① 这是你计划里的参考位
        assert "这不是止盈信号" in text             # ② 不是止盈线
        assert "是否离场由你判断" in text           # ③ 纯告知,决定权在用户
        # 🔴 旧措辞一句都不许残留(两句并存 = 用户一屏看到两种纪律说法)
        assert "纪律仍是回落止盈" not in text
        assert "回落止盈" not in text
        assert "你计划里的参考位" not in text
        for banned in ("建议", "该卖", "推荐", "目标价"):
            assert banned not in text

    def test_copy_still_carries_the_actual_numbers(self):
        """🔴 **逐字骨架 ≠ 丢掉数字**:⑪-B 三句式要求「讲清发生了什么」——
        现价与区间上下沿是这条立即级推送唯一可核对的事实,⛔ 不许为了"照抄骨架"
        把它们省掉(省掉之后用户收到的是一句无法验证的空话)。"""
        from tests.test_sentinel_holding import _position, _quote

        from neckline.sentinel.holding import check_exit_reference_reached

        text = check_exit_reference_reached(_position(), _quote(14.0), 13.0, 15.0)
        assert "14.00" in text and "13.00" in text and "15.00" in text


# ══════════════════════════════════════════════════════════════════════════
# 「不影响其他 kind」——闸② 只掐 take_profit 这一条
# ══════════════════════════════════════════════════════════════════════════

class TestOtherKindsUnaffected:
    def test_stop_approach_still_fires_when_take_profit_is_disarmed(self, isolated_env):
        from tests.test_sentinel_tick_v2_bypass import _FakeNotifier, _q   # 复用既有替身
        from neckline.sentinel.engine import run_tick

        days = business_days(date(2026, 7, 1), 30)
        insert_trade_cal(isolated_env, days)
        insert_stock_basic(isolated_env, [{"ts_code": "600001.SH", "name": "甲", "market": "主板"}])
        d0, today = days[-2], days[-1]
        _seed_basket(isolated_env.db_path, d0, exit_low=13.0, exit_high=15.0)
        # 成交价 14 > exit_low 13 → take_profit 不武装;同时现价 9.0 已破 −5% 止损线
        pe.record_buy("600001.SH", 14.0, 100, today, db_path=isolated_env.db_path)

        n = _FakeNotifier()
        r = run_tick(datetime.combine(today, time(10, 30)), db_path=isolated_env.db_path,
                     parquet_dir=isolated_env.parquet_dir,
                     quotes_fn=lambda codes: {"600001.SH": _q("600001.SH", 9.0)},
                     notifier=n)
        assert r.exit_reference_hits == []                       # take_profit 不武装 → 不发
        kinds = [e["kind"] for e in n.holding_risk]
        assert "stop_approach" in kinds                          # 其他 kind 照常
        assert "take_profit" not in kinds
