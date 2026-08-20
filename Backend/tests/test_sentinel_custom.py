"""⑪-C 确定性执行器单测(`neckline/sentinel/custom.py`)。

⑪ 验收条款点名的三件事在这里:**命中一次即冷却 / 收盘失效 / `persist=1` 跨日存活**;
外加三值判定(数据不可得 ≠ 条件不满足)与「**永不自动交易**」grep 守门。
"""

from __future__ import annotations

import ast
import inspect
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from neckline import custom_alerts as ca
from neckline.calendar import CN_TZ
from neckline.sentinel import custom as cu
from neckline.dedup import record_pushed
from neckline.sentinel.positions import Position
from neckline.data.realtime import Quote

pytestmark = pytest.mark.usefixtures("isolated_env")

CODE = "600519.SH"
TODAY = date(2026, 7, 31)
MIDDAY = datetime(2026, 7, 31, 10, 45, tzinfo=CN_TZ)


@pytest.fixture
def frozen_clock(monkeypatch):
    """把**三处时间源**一起钉在 `MIDDAY`,返回那个时刻(A7,2026-08-04)。

    **为什么必须三处一起冻**:冷却那条用例要同时摆布「上次命中在什么时候」与
    「现在几点」,而这两件事分别由三个各自取墙钟的地方决定 ——
    ① `custom_alerts._now_utc()`(提醒的 `created_at` → `created_trade_day` →
    **创建当日 15:00 自动失效**)、② `dedup.record_pushed()` 落的 `pushed_at`
    (= `last_fired_at`,冷却的起点)、③ 调用方传给 `evaluate_alerts` 的 `now`。
    只冻第三个不够:老写法拿 `datetime.now(CN_TZ)` 当 now、再 `+700s` 试冷却到期,
    **北京时间 14:48:20 之后跑就必然越过 15:00 收盘线**,提醒先被判 `expired`、
    当拍不再响 —— 每天傍晚的全量跑都带这一条"已知红"(判定线/契约线两份审计报告
    都点过名)。冻住之后任何时段跑都绿,且断言的仍是同一件事。

    ⛔ 不改生产代码来迁就测试:`record_pushed` 不加 `pushed_at` 形参,这里 patch
    模块级 `datetime` 名字(测试内 monkeypatch,进程外零影响)。"""
    import neckline.dedup as dedup_mod

    frozen_utc = MIDDAY.astimezone(timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_utc.astimezone(tz) if tz is not None else MIDDAY.replace(tzinfo=None)

    monkeypatch.setattr(ca, "_now_utc", lambda: frozen_utc.isoformat(timespec="seconds"))
    monkeypatch.setattr(dedup_mod, "datetime", _FrozenDatetime)
    return MIDDAY


def _q(code, price, pre_close=10.0, high=None, volume=1000.0) -> Quote:
    return Quote(code=code, name="示例", price=price, pre_close=pre_close, open=pre_close,
                 high=high if high is not None else max(price, pre_close),
                 low=min(price, pre_close), volume=volume, amount=price * volume * 100,
                 ts="", source="test")


def _pos(pid, code, buy_price=10.0, qty=1000) -> Position:
    return Position(id=pid, ts_code=code, buy_price=buy_price, qty=qty,
                    buy_date="20260730", status="open", sell_price=None, sell_date=None, note=None)


def _mk(env, conds, *, logic=ca.LOGIC_ALL, code=CODE, **kw) -> ca.CustomAlert:
    return ca.create_alert(rule={"logic": logic, "conditions": list(conds)},
                           nl_text="测试提醒", ts_code=code, db_path=env.db_path, **kw)


def _ctx(now=MIDDAY, quotes=None, positions=(), prev5=None, members=None) -> cu.MetricContext:
    return cu.MetricContext(
        now=now, quotes=dict(quotes or {}), avg_cost=cu._avg_cost_map(positions),
        prev5_avg_volume=dict(prev5 or {}), basket_members=dict(members or {}),
    )


# ══════════════════════════════════════════════════════════════════════════
# 比较器 / 指标取值
# ══════════════════════════════════════════════════════════════════════════

def test_compare_uses_eps_tolerance():
    """浮点毛刺不该漏判边界(CLAUDE.md `_EPS` 纪律)。"""
    assert cu.compare(0.08 - 0.02, ca.OP_GE, 0.06) is True     # 二进制下略小于 0.06
    assert cu.compare(15.0, ca.OP_LE, 15.0) is True
    assert cu.compare(15.0, ca.OP_LT, 15.0) is False


class TestMetricValue:
    def test_price_and_chg_pct(self):
        ctx = _ctx(quotes={CODE: _q(CODE, 9.5)})
        assert cu.metric_value("price", {}, CODE, ctx) == 9.5
        assert cu.metric_value("chg_pct", {}, CODE, ctx) == pytest.approx(-0.05)

    def test_vs_cost_needs_a_position(self):
        no_pos = _ctx(quotes={CODE: _q(CODE, 9.0)})
        assert cu.metric_value("vs_cost", {}, CODE, no_pos) is None     # 没持仓 → 不猜成本
        with_pos = _ctx(quotes={CODE: _q(CODE, 9.0)}, positions=[_pos(1, CODE, 10.0)])
        assert cu.metric_value("vs_cost", {}, CODE, with_pos) == pytest.approx(-0.1)

    def test_vs_cost_is_quantity_weighted(self):
        ctx = _ctx(quotes={CODE: _q(CODE, 12.0)},
                   positions=[_pos(1, CODE, 10.0, 1000), _pos(2, CODE, 14.0, 3000)])
        # 加权成本 = (10*1000 + 14*3000) / 4000 = 13
        assert cu.metric_value("vs_cost", {}, CODE, ctx) == pytest.approx(12.0 / 13.0 - 1)

    def test_from_day_high_is_non_positive(self):
        ctx = _ctx(quotes={CODE: _q(CODE, 9.6, high=10.0)})
        assert cu.metric_value("from_day_high", {}, CODE, ctx) == pytest.approx(-0.04)

    def test_volume_ratio_none_in_early_session(self):
        early = datetime(2026, 7, 31, 9, 45, tzinfo=CN_TZ)     # 开盘 15 分钟,早盘窗口
        ctx = _ctx(now=early, quotes={CODE: _q(CODE, 10.0)}, prev5={CODE: 500.0})
        assert cu.metric_value("volume_ratio", {}, CODE, ctx) is None

    def test_volume_ratio_none_without_base(self):
        ctx = _ctx(quotes={CODE: _q(CODE, 10.0)}, prev5={})
        assert cu.metric_value("volume_ratio", {}, CODE, ctx) is None

    def test_volume_ratio_ok_midday(self):
        ctx = _ctx(quotes={CODE: _q(CODE, 10.0, volume=1000.0)}, prev5={CODE: 500.0})
        assert cu.metric_value("volume_ratio", {}, CODE, ctx) is not None

    def test_index_chg_pct_uses_ref(self):
        ctx = _ctx(quotes={"000001.SH": _q("000001.SH", 9.8)})
        assert cu.metric_value("index_chg_pct", {"ref": "000001.SH"}, None, ctx) == pytest.approx(-0.02)

    def test_basket_weak_ratio_needs_min_sample(self):
        one = _ctx(quotes={"600001.SH": _q("600001.SH", 9.0)},
                   members={CODE: ("600001.SH",)})
        assert cu.metric_value("basket_weak_ratio", {}, CODE, one) is None    # 样本不足 ≠ 占比 0
        two = _ctx(quotes={"600001.SH": _q("600001.SH", 9.0), "600002.SH": _q("600002.SH", 10.1)},
                   members={CODE: ("600001.SH", "600002.SH")})
        assert cu.metric_value("basket_weak_ratio", {}, CODE, two) == pytest.approx(0.5)


# ══════════════════════════════════════════════════════════════════════════
# 三值判定
# ══════════════════════════════════════════════════════════════════════════

class TestThreeValuedLogic:
    def test_all_true(self):
        rule = ca.normalize_rule({"conditions": [
            {"metric": "price", "op": "<=", "value": 10.0},
            {"metric": "chg_pct", "op": "<=", "value": -0.01},
        ]}, ts_code=CODE)
        v, _vals = cu.evaluate_rule(rule, CODE, _ctx(quotes={CODE: _q(CODE, 9.5)}))
        assert v is True

    def test_all_with_one_definite_false_is_false_even_if_another_is_unknown(self):
        """`all` 下有一条铁定不成立 → 整条 False,不必等缺的那条(逻辑上确定)。"""
        rule = ca.normalize_rule({"conditions": [
            {"metric": "price", "op": "<=", "value": 5.0},      # 铁定不成立
            {"metric": "volume_ratio", "op": ">=", "value": 2.0},  # 无基准 → 不可得
        ]}, ts_code=CODE)
        v, _ = cu.evaluate_rule(rule, CODE, _ctx(quotes={CODE: _q(CODE, 9.5)}))
        assert v is False

    def test_all_with_unknown_is_none_not_false(self):
        """缺数据 → `None`(不判),**不是** False(「没看」不等于「没有」)。"""
        rule = ca.normalize_rule({"conditions": [
            {"metric": "price", "op": "<=", "value": 10.0},
            {"metric": "volume_ratio", "op": ">=", "value": 2.0},
        ]}, ts_code=CODE)
        v, _ = cu.evaluate_rule(rule, CODE, _ctx(quotes={CODE: _q(CODE, 9.5)}))
        assert v is None

    def test_any_true_short_circuits_unknown(self):
        rule = ca.normalize_rule({"logic": "any", "conditions": [
            {"metric": "price", "op": "<=", "value": 10.0},
            {"metric": "volume_ratio", "op": ">=", "value": 2.0},
        ]}, ts_code=CODE)
        v, _ = cu.evaluate_rule(rule, CODE, _ctx(quotes={CODE: _q(CODE, 9.5)}))
        assert v is True

    def test_missing_quote_gives_none(self):
        rule = ca.normalize_rule({"conditions": [{"metric": "price", "op": "<=", "value": 10.0}]},
                                 ts_code=CODE)
        v, _ = cu.evaluate_rule(rule, CODE, _ctx(quotes={}))
        assert v is None


# ══════════════════════════════════════════════════════════════════════════
# 一拍编排:命中 / 冷却 / 次数上限 / 生效窗 / 到期
# ══════════════════════════════════════════════════════════════════════════

class TestEvaluateAlerts:
    def test_hit(self, isolated_env):
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}])
        r = cu.evaluate_alerts(MIDDAY, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert [h.alert.id for h in r.hits] == [a.id]
        assert r.hits[0].event_key == f"alert{a.id}#1"
        assert "现价 ≤ 9.60 元" in r.hits[0].condition_text

    def test_max_fires_one_means_no_second_alarm(self, isolated_env):
        """默认首次命中后不重复轰炸(安全要求 2)。"""
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}])
        ca.mark_fired(a.id, db_path=isolated_env.db_path)
        r = cu.evaluate_alerts(MIDDAY, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.hits == [] and r.skipped[a.id] == "max_fires_reached"

    def test_cooldown_blocks_second_hit_and_expires(self, isolated_env, frozen_clock):
        """⚠ 走**冻结时钟**(`frozen_clock` fixture,理由见其 docstring):老写法用
        `datetime.now(CN_TZ)+700s`,北京时间 14:48 之后跑会越过 15:00 收盘线、提醒先
        被判失效 → 每晚必红。冻住之后任何时段跑都绿,断言的还是同一件事。"""
        now = frozen_clock
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}],
                max_fires=0, cooldown_seconds=600)
        record_pushed(TODAY, cu.SENTINEL_NAME, CODE, f"alert{a.id}#1",
                      payload={}, db_path=isolated_env.db_path)
        ca.mark_fired(a.id, db_path=isolated_env.db_path)
        # 刚推完 → 冷却中
        r = cu.evaluate_alerts(now, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.hits == [] and r.skipped[a.id] == "cooldown"
        # 冷却过后 → 再次可命中(700s > cooldown 600s,且仍在收盘前)
        later = now + timedelta(seconds=700)
        assert later.time() < time(15, 0)                    # 冻结时钟真的没越过收盘线
        r2 = cu.evaluate_alerts(later, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                                db_path=isolated_env.db_path)
        assert [h.alert.id for h in r2.hits] == [a.id]
        assert r2.hits[0].event_key == f"alert{a.id}#2"     # 序号递增,台账每次一行

    def test_outside_active_window(self, isolated_env):
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}], active_from="13:30")
        r = cu.evaluate_alerts(MIDDAY, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.hits == [] and r.skipped[a.id] == "outside_active_window"

    def test_expires_at_close_and_does_not_fire_after(self, isolated_env):
        """收盘自动失效(安全要求 3):过了创建当日 15:00 → 翻 expired 且当拍不再响。"""
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}])
        day = ca.created_trade_day(a)
        after_close = datetime.combine(day, __import__("datetime").time(15, 1), tzinfo=CN_TZ)
        r = cu.evaluate_alerts(after_close, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.expired_ids == [a.id] and r.hits == []
        assert ca.get_alert(a.id, db_path=isolated_env.db_path).status == ca.STATUS_EXPIRED

    def test_persist_survives_to_the_next_day(self, isolated_env):
        """`persist=1` 跨日存活(安全要求 3 的例外分支)。"""
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}], persist=True)
        day = ca.created_trade_day(a)
        next_day = datetime.combine(day + timedelta(days=1),
                                    __import__("datetime").time(10, 30), tzinfo=CN_TZ)
        r = cu.evaluate_alerts(next_day, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.expired_ids == [] and [h.alert.id for h in r.hits] == [a.id]

    def test_not_met_and_insufficient_data_are_different_reasons(self, isolated_env):
        a1 = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 5.0}])
        a2 = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 5.0}], code="600000.SH")
        r = cu.evaluate_alerts(MIDDAY, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.skipped[a1.id] == "not_met"                  # 判了,不满足
        assert r.skipped[a2.id] == "insufficient_data"        # 没判(拉不到行情)

    def test_cancelled_alert_is_not_evaluated(self, isolated_env):
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}])
        ca.cancel_alert(a.id, db_path=isolated_env.db_path)
        r = cu.evaluate_alerts(MIDDAY, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert r.hits == [] and a.id not in r.skipped

    def test_naive_now_is_read_as_beijing_time(self, isolated_env):
        """调用方传 naive `datetime.now()`(哨兵主循环就是这么传的)→ 按北京时间读。"""
        a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}], active_from="10:00")
        naive = datetime(2026, 7, 31, 10, 45)
        r = cu.evaluate_alerts(naive, quotes={CODE: _q(CODE, 9.5)}, positions=[],
                               db_path=isolated_env.db_path)
        assert [h.alert.id for h in r.hits] == [a.id]

    def test_combined_market_and_stock_condition(self, isolated_env):
        """组合条件端到端:个股价格 + 大盘涨跌幅(蓝图 5.6 的「组合」)。"""
        a = _mk(isolated_env, [
            {"metric": "price", "op": "<=", "value": 9.6},
            {"metric": "index_chg_pct", "op": "<=", "value": -0.01, "ref": "000001.SH"},
        ])
        quotes = {CODE: _q(CODE, 9.5), "000001.SH": _q("000001.SH", 9.85)}
        r = cu.evaluate_alerts(MIDDAY, quotes=quotes, positions=[], db_path=isolated_env.db_path)
        assert [h.alert.id for h in r.hits] == [a.id]
        # 大盘没跌够 → 不命中
        quotes["000001.SH"] = _q("000001.SH", 9.99)
        r2 = cu.evaluate_alerts(MIDDAY, quotes=quotes, positions=[], db_path=isolated_env.db_path)
        assert r2.hits == []


def test_subject_text_prefers_name(isolated_env):
    a = _mk(isolated_env, [{"metric": "price", "op": "<=", "value": 9.6}])
    assert cu.subject_text(a, {CODE: _q(CODE, 9.5)}) == f"示例({CODE})"
    market = _mk(isolated_env, [{"metric": "index_chg_pct", "op": "<=", "value": -0.02,
                                 "ref": "000001.SH"}], code=None)
    assert cu.subject_text(market, {}) == "大盘"


# ══════════════════════════════════════════════════════════════════════════
# 守门:永不自动交易(⑪ 验收条款点名的 grep 守门)
# ══════════════════════════════════════════════════════════════════════════

_FORBIDDEN_SYMBOLS = (
    "place_order", "submit_order", "cancel_order", "create_order", "send_order",
    "buy_order", "sell_order", "broker", "trade_api", "下单", "撤单", "改止损",
)


def test_custom_executor_has_zero_trading_calls():
    """`sentinel/custom.py` **零下单调用**(§3.8 + ⑪-C 验收)。逐行扫可执行代码,
    注释里的否定表述(如「⛔ 永不自动交易」)放行。"""
    src = Path(inspect.getsourcefile(cu)).read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for sym in _FORBIDDEN_SYMBOLS:
            assert sym not in stripped or "不" in stripped or "⛔" in stripped, \
                f"疑似交易动作:{line}"


def test_custom_executor_imports_no_trading_module():
    """AST 守门:import 名单里不许出现任何券商 / 下单模块(本项目没有那种模块,
    这条断言是为了保证以后也不会有人往这里加)。"""
    src = Path(inspect.getsourcefile(cu)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    for m in mods:
        assert not any(bad in m for bad in ("order", "broker", "trade_api")), m


def test_custom_executor_never_reads_nl_text_for_judgement():
    """§2.8-C 第 2 条:LLM 自由文本不进哨兵判据 —— 本模块只在**措辞**函数
    (`subject_text` 之外无)里碰 `nl_text`,判定路径一个字都不看。"""
    src = Path(inspect.getsourcefile(cu)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    judging = {"evaluate_rule", "metric_value", "compare", "evaluate_alerts"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in judging:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    assert sub.attr != "nl_text", f"{node.name} 不该读 nl_text"
