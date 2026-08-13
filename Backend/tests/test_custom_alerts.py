"""⑪-C 临时提醒的**规则白名单 / 确认卡 / 台账**单测(plan §五 V2-⑪-C;蓝图 5.6)。

重点在**安全四条**能不能被代码兑现:相同提醒去重、首次命中不轰炸(默认 max_fires=1)、
收盘自动失效(除非 persist)、行情延迟披露是确认卡的必选项。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from neckline import custom_alerts as ca
from neckline.calendar import CN_TZ

pytestmark = pytest.mark.usefixtures("isolated_env")

CODE = "600519.SH"


def _rule(*conds, logic=ca.LOGIC_ALL):
    return {"logic": logic, "conditions": list(conds)}


# ══════════════════════════════════════════════════════════════════════════
# 白名单校验:六类条件各一 + 一条组合(⑪ 验收条款点名的 golden 面)
# ══════════════════════════════════════════════════════════════════════════

class TestNormalizeRuleGolden:
    def test_price(self):
        out = ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": 15}), ts_code=CODE)
        assert out["conditions"] == [{"metric": "price", "op": "<=", "value": 15.0}]
        assert out["schema_version"] == ca.RULE_SCHEMA_VERSION

    def test_chg_pct(self):
        out = ca.normalize_rule(_rule({"metric": "chg_pct", "op": "<=", "value": -0.05}), ts_code=CODE)
        assert out["conditions"][0]["metric"] == "chg_pct"

    def test_vs_cost(self):
        out = ca.normalize_rule(_rule({"metric": "vs_cost", "op": "<=", "value": -0.03}), ts_code=CODE)
        assert out["conditions"][0]["metric"] == "vs_cost"

    def test_from_day_high(self):
        out = ca.normalize_rule(_rule({"metric": "from_day_high", "op": "<=", "value": -0.04}), ts_code=CODE)
        assert out["conditions"][0]["value"] == pytest.approx(-0.04)

    def test_volume_ratio(self):
        out = ca.normalize_rule(_rule({"metric": "volume_ratio", "op": ">=", "value": 2.5}), ts_code=CODE)
        assert out["conditions"][0]["op"] == ">="

    def test_index_chg_pct_market_level(self):
        """大盘级提醒(ts_code=None)只能用指数条件,`ref` 必填。"""
        out = ca.normalize_rule(
            _rule({"metric": "index_chg_pct", "op": "<=", "value": -0.02, "ref": "000001.sh"}),
            ts_code=None,
        )
        assert out["conditions"][0]["ref"] == "000001.SH"

    def test_basket_weak_ratio(self):
        out = ca.normalize_rule(
            _rule({"metric": "basket_weak_ratio", "op": ">=", "value": 0.5}), ts_code=CODE)
        assert out["conditions"][0]["metric"] == "basket_weak_ratio"

    def test_combination_of_price_and_index(self):
        """蓝图 5.6 明写要支持「大盘或篮子条件的**组合**」。"""
        out = ca.normalize_rule(
            _rule({"metric": "price", "op": "<=", "value": 15.0},
                  {"metric": "index_chg_pct", "op": "<=", "value": -0.01, "ref": "000001.SH"},
                  logic=ca.LOGIC_ANY),
            ts_code=CODE,
        )
        assert out["logic"] == "any" and len(out["conditions"]) == 2

    def test_all_six_families_covered_by_this_class(self):
        """自检:白名单里每个 metric 上面都有一条 golden(加 metric 忘了加测就挂)。"""
        covered = {"price", "chg_pct", "vs_cost", "from_day_high", "volume_ratio",
                   "index_chg_pct", "basket_weak_ratio"}
        assert covered == set(ca.ALL_METRICS)


class TestNormalizeRuleRejects:
    def test_unknown_metric(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "rsi", "op": "<=", "value": 30}), ts_code=CODE)

    def test_unknown_op(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "price", "op": "~=", "value": 1}), ts_code=CODE)

    def test_percentage_written_as_whole_number_is_rejected_not_guessed(self):
        """模型把 −5% 写成 `-5` → **拒收**,⛔ 不替它猜成 −0.05(猜错就是另一条提醒)。"""
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "chg_pct", "op": "<=", "value": -5}), ts_code=CODE)

    def test_stock_scoped_metric_on_market_level_alert(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": 15}), ts_code=None)

    def test_index_condition_without_ref(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "index_chg_pct", "op": "<=", "value": -0.02}), ts_code=None)

    def test_from_day_high_must_be_non_positive(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "from_day_high", "op": "<=", "value": 0.05}), ts_code=CODE)

    def test_too_many_conditions(self):
        conds = [{"metric": "price", "op": "<=", "value": float(i + 1)} for i in range(ca.MAX_CONDITIONS + 1)]
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule(*conds), ts_code=CODE)

    def test_empty_conditions(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule(), ts_code=CODE)

    def test_nan_value(self):
        with pytest.raises(ca.RuleValidationError):
            ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": float("nan")}), ts_code=CODE)


def test_canonical_text_is_key_order_independent():
    """去重比的是规范化文本 —— 键序 / 整数写法不同不该被当成两条不同的提醒。"""
    a = ca.normalize_rule({"conditions": [{"value": 15, "op": "<=", "metric": "price"}]}, ts_code=CODE)
    b = ca.normalize_rule({"logic": "all", "conditions": [{"metric": "price", "op": "<=", "value": 15.0}]},
                          ts_code=CODE)
    assert ca.canonical_rule_text(a) == ca.canonical_rule_text(b)


def test_describe_rule_is_generated_from_structure():
    """用户看到的那句话由**结构化规则**生成 —— 说的和哨兵做的是同一份东西。"""
    r = ca.normalize_rule(
        _rule({"metric": "price", "op": "<=", "value": 15.0},
              {"metric": "chg_pct", "op": "<=", "value": -0.05}), ts_code=CODE)
    text = ca.describe_rule(r)
    assert "现价 ≤ 15.00 元" in text and "且" in text and "-5.00%" in text


# ══════════════════════════════════════════════════════════════════════════
# 确认卡七项
# ══════════════════════════════════════════════════════════════════════════

class TestConfirmationCard:
    def test_has_all_seven_items_with_mandatory_disclosures(self):
        r = ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": 15.0}), ts_code=CODE)
        card = ca.build_confirmation_card(rule=r, ts_code=CODE, name="贵州茅台", active_from="13:30")
        d = card.to_dict()
        for key in ("subject", "condition", "active_window", "notify_limit", "expiry",
                    "quote_delay_disclosure", "no_auto_trade"):
            assert d[key], f"确认卡缺项:{key}"
        # ⑥⑦ 是固定文案、必选项(蓝图 5.6 安全要求),内容也要对得上
        assert d["quote_delay_disclosure"] == ca.QUOTE_DELAY_DISCLOSURE
        assert "延迟" in d["quote_delay_disclosure"] and "中断" in d["quote_delay_disclosure"]
        assert d["no_auto_trade"] == ca.NO_AUTO_TRADE_DISCLOSURE
        assert "不自动交易" in d["no_auto_trade"]
        assert "贵州茅台" in d["subject"] and "13:30" in d["active_window"]

    def test_default_expiry_says_close_of_day(self):
        r = ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": 15.0}), ts_code=CODE)
        card = ca.build_confirmation_card(rule=r, ts_code=CODE)
        assert "收盘" in card.expiry and "15:00" in card.expiry
        assert "只通知一次" in card.notify_limit        # 默认 max_fires=1

    def test_persist_expiry_wording(self):
        r = ca.normalize_rule(_rule({"metric": "price", "op": "<=", "value": 15.0}), ts_code=CODE)
        card = ca.build_confirmation_card(rule=r, ts_code=CODE, persist=True)
        assert "长期有效" in card.expiry

    def test_market_level_subject(self):
        r = ca.normalize_rule(
            _rule({"metric": "index_chg_pct", "op": "<=", "value": -0.02, "ref": "000001.SH"}),
            ts_code=None)
        card = ca.build_confirmation_card(rule=r, ts_code=None)
        assert "大盘" in card.subject


# ══════════════════════════════════════════════════════════════════════════
# 台账 / 去重 / 失效
# ══════════════════════════════════════════════════════════════════════════

class TestStore:
    def test_create_and_read_back(self, isolated_env):
        a = ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                            nl_text="跌到 15 通知我", ts_code=CODE, db_path=isolated_env.db_path)
        assert a.id > 0 and a.status == ca.STATUS_ACTIVE and a.max_fires == 1
        assert ca.get_alert(a.id, db_path=isolated_env.db_path).nl_text == "跌到 15 通知我"

    def test_create_revalidates_even_if_caller_says_it_checked(self, isolated_env):
        with pytest.raises(ca.RuleValidationError):
            ca.create_alert(rule=_rule({"metric": "nope", "op": "<=", "value": 1}),
                            nl_text="x", ts_code=CODE, db_path=isolated_env.db_path)

    def test_duplicate_detection_ignores_wording(self, isolated_env):
        """相同提醒去重(安全要求 1):措辞不同、规则相同 → 认定重复。"""
        r = _rule({"metric": "price", "op": "<=", "value": 15.0})
        first = ca.create_alert(rule=r, nl_text="跌到 15 叫我", ts_code=CODE,
                                db_path=isolated_env.db_path)
        dup = ca.find_duplicate({"conditions": [{"value": 15, "op": "<=", "metric": "price"}]},
                                CODE, db_path=isolated_env.db_path)
        assert dup is not None and dup.id == first.id

    def test_different_threshold_is_not_duplicate(self, isolated_env):
        ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                        nl_text="a", ts_code=CODE, db_path=isolated_env.db_path)
        assert ca.find_duplicate(_rule({"metric": "price", "op": "<=", "value": 14.0}),
                                 CODE, db_path=isolated_env.db_path) is None

    def test_cancelled_alert_does_not_block_recreation(self, isolated_env):
        a = ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                            nl_text="a", ts_code=CODE, db_path=isolated_env.db_path)
        ca.cancel_alert(a.id, db_path=isolated_env.db_path)
        assert ca.find_duplicate(_rule({"metric": "price", "op": "<=", "value": 15.0}),
                                 CODE, db_path=isolated_env.db_path) is None

    def test_cancel_keeps_the_row(self, isolated_env):
        a = ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                            nl_text="a", ts_code=CODE, db_path=isolated_env.db_path)
        ca.cancel_alert(a.id, db_path=isolated_env.db_path)
        got = ca.get_alert(a.id, db_path=isolated_env.db_path)
        assert got is not None and got.status == ca.STATUS_CANCELLED   # 留痕,不物理删

    def test_update_partial(self, isolated_env):
        a = ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                            nl_text="a", ts_code=CODE, db_path=isolated_env.db_path)
        upd = ca.update_alert(a.id, max_fires=3, db_path=isolated_env.db_path)
        assert upd.max_fires == 3 and upd.rule == a.rule       # 未传的字段不动

    def test_update_missing_returns_none(self, isolated_env):
        assert ca.update_alert(999, max_fires=2, db_path=isolated_env.db_path) is None


class TestExpiry:
    def _mk(self, env, **kw):
        return ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                               nl_text="a", ts_code=CODE, db_path=env.db_path, **kw)

    def test_non_persist_expires_at_close_of_creation_day(self, isolated_env):
        a = self._mk(isolated_env)
        day = ca.created_trade_day(a)
        assert ca.effective_expiry(a) == datetime.combine(
            day, __import__("datetime").time(15, 0), tzinfo=CN_TZ)
        before = datetime.combine(day, __import__("datetime").time(14, 59), tzinfo=CN_TZ)
        after = datetime.combine(day, __import__("datetime").time(15, 0), tzinfo=CN_TZ)
        assert ca.is_expired_at(a, before) is False
        assert ca.is_expired_at(a, after) is True

    def test_persist_survives_close(self, isolated_env):
        a = self._mk(isolated_env, persist=True)
        assert ca.effective_expiry(a) is None
        far = datetime.now(CN_TZ) + timedelta(days=30)
        assert ca.is_expired_at(a, far) is False

    def test_explicit_expires_at_wins(self, isolated_env):
        a = self._mk(isolated_env, persist=True, expires_at="2026-08-05T10:00:00+08:00")
        assert ca.effective_expiry(a).date() == date(2026, 8, 5)

    def test_expire_due_flips_status_idempotently(self, isolated_env):
        a = self._mk(isolated_env)
        day = ca.created_trade_day(a)
        after = datetime.combine(day, __import__("datetime").time(15, 1), tzinfo=CN_TZ)
        assert ca.expire_due(after, db_path=isolated_env.db_path) == [a.id]
        assert ca.expire_due(after, db_path=isolated_env.db_path) == []   # 幂等
        assert ca.get_alert(a.id, db_path=isolated_env.db_path).status == ca.STATUS_EXPIRED

    def test_list_alerts_never_writes(self, isolated_env):
        """读路径不改库:到期行在 list 里仍是 active(由哨兵那一拍去翻)。"""
        a = self._mk(isolated_env)
        rows = ca.list_alerts(status=ca.STATUS_ACTIVE, db_path=isolated_env.db_path)
        assert [r.id for r in rows] == [a.id]
        assert ca.get_alert(a.id, db_path=isolated_env.db_path).status == ca.STATUS_ACTIVE


class TestActiveWindow:
    def _mk(self, env, **kw):
        return ca.create_alert(rule=_rule({"metric": "price", "op": "<=", "value": 15.0}),
                               nl_text="a", ts_code=CODE, db_path=env.db_path, **kw)

    def test_before_window_start(self, isolated_env):
        a = self._mk(isolated_env, active_from="13:30")
        assert ca.in_active_window(a, datetime(2026, 7, 31, 10, 0, tzinfo=CN_TZ)) is False
        assert ca.in_active_window(a, datetime(2026, 7, 31, 13, 30, tzinfo=CN_TZ)) is True

    def test_after_window_end(self, isolated_env):
        a = self._mk(isolated_env, active_to="11:00")
        assert ca.in_active_window(a, datetime(2026, 7, 31, 11, 0, tzinfo=CN_TZ)) is False

    def test_unparsable_window_treated_as_unset_not_never(self, isolated_env):
        """写坏的 'HH:MM' 不该让提醒永不生效(那是静默失效,最坏的一种)。"""
        a = self._mk(isolated_env, active_from="下午")
        assert ca.in_active_window(a, datetime(2026, 7, 31, 10, 0, tzinfo=CN_TZ)) is True
