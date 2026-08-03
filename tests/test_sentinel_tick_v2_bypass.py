"""V2-⑪ 两条新旁路在 `run_tick` 里的接线单测(⑪-A 四监测 / ⑪-C NL 临时提醒)。

这份文件专测**接线**(判定逻辑分别在 `test_sentinel_attention.py` /
`test_sentinel_custom.py`),重点是那条最贵的红线:

    **旁路炸了 ⇒ 四哨兵与熔断一行不受影响。**

外加防重(同一监测当日只推一次)、台账落 `sentinel_events`、命中后 `fired_count`
递增、以及推送走的是**按 kind 的 APNs 三级**而不是既有 `channels`。
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from tests.conftest import business_days, insert_stock_basic, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

from neckline import custom_alerts as ca
from neckline.db import connection
from neckline.sentinel import attention as att
from neckline.sentinel import custom as cu
from neckline.sentinel.channels import PushChannel
from neckline.sentinel.dedup import already_pushed, load_events_for_date
from neckline.sentinel.engine import reset_retreat_process_state, run_tick
from neckline.sentinel.positions import open_position
from neckline.sentinel.quotes import Quote

pytestmark = pytest.mark.usefixtures("isolated_env")


class _CapturingChannel(PushChannel):
    name = "capture"

    def __init__(self):
        self.messages = []

    def send(self, title, body, *, level="info", transport=None):
        self.messages.append((title, body, level))
        return True


class _FakeNotifier:
    """假的 APNs 措辞层(替 `neckline.api.notify`),记录每次调用。"""

    def __init__(self):
        self.attention = []
        self.custom = []

    def push_attention_alert(self, kind, title, what_happened, **kw):
        self.attention.append({"kind": kind, "title": title, "body": what_happened, **kw})
        return type("O", (), {"sent": 1, "skipped_reason": "", "kind": kind})()

    def push_custom_alert(self, alert_id, subject, condition_text, **kw):
        self.custom.append({"alertId": alert_id, "subject": subject,
                            "condition": condition_text, **kw})
        return type("O", (), {"sent": 1, "skipped_reason": "", "kind": "custom_alert"})()


def _q(code, price, pre_close=10.0, high=None, volume=60000.0) -> Quote:
    return Quote(code=code.split(".")[0], name=code, price=price, pre_close=pre_close,
                 open=pre_close, high=high if high is not None else max(price, pre_close),
                 low=min(price, pre_close), volume=volume,
                 amount=price * volume * 100, ts="", source="sina")


class _TickEnv:
    """`isolated_env`(frozen `Settings`)的可写包装:透传全部字段,另挂本文件要用的
    `report_day`/`today` 两个日期(不改 conftest 的共享夹具)。"""

    def __init__(self, env, report_day, today):
        self._env = env
        self.report_day = report_day
        self.today = today

    def __getattr__(self, name):
        return getattr(self._env, name)


@pytest.fixture
def tick_env(isolated_env):
    """一个能跑通 `run_tick` 的最小环境:交易日历 + 历史 + 现役章程 + stock_basic。"""
    days = business_days(date(2026, 7, 1), 30)
    report_day, today = days[-2], days[-1]
    insert_trade_cal(isolated_env, days)
    codes = ["600001.SH", "600002.SH", "600003.SH"]
    for d in days:
        if d >= today:
            continue
        write_daily_fixture(isolated_env, "daily", d, [
            {"ts_code": c, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
             "pre_close": 10.0, "vol": 200000.0, "amount": 10000.0} for c in codes
        ])
    seed_active_rule_v1(isolated_env)
    insert_stock_basic(isolated_env, [
        {"ts_code": c, "name": c, "market": "主板"} for c in codes
    ])
    reset_retreat_process_state()
    return _TickEnv(isolated_env, report_day, today)


def _seed_basket_with_members(env, d0: date, codes) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), "k1", "AI 算力", "算力扩产", "theme", 1,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-07-30T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "core", None, 0, "理由", 1, "2026-07-30T00:00:00+08:00"),
            )
    return bid


def _link(env, position_id: int, basket_id: int, code: str, day: date) -> None:
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id,"
            " card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, code, day.strftime("%Y%m%d"), basket_id, 1, 1, "core", "{}",
             "2026-07-31T00:00:00+08:00"),
        )


# ══════════════════════════════════════════════════════════════════════════
# ⑪-A 四监测接线
# ══════════════════════════════════════════════════════════════════════════

class TestAttentionBypassWiring:
    def _setup_weak_basket(self, env):
        today, d0 = env.today, env.report_day
        pid = open_position("600001.SH", 10.0, 1000, today, db_path=env.db_path)
        bid = _seed_basket_with_members(env, d0, ["600001.SH", "600002.SH", "600003.SH"])
        _link(env, pid, bid, "600001.SH", today)
        return pid, bid

    def _quotes_fn(self):
        def fn(codes):
            out = {
                "600001.SH": _q("600001.SH", 9.9),
                "600002.SH": _q("600002.SH", 9.5),    # -5%
                "600003.SH": _q("600003.SH", 9.4),    # -6%
            }
            return {c: out[c] for c in codes if c in out}
        return fn

    def test_fires_pushes_via_apns_kind_and_records_ledger(self, tick_env):
        self._setup_weak_basket(tick_env)
        notifier = _FakeNotifier()
        cap = _CapturingChannel()
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, channels=[cap], db_path=tick_env.db_path,
                     parquet_dir=tick_env.parquet_dir, quotes_fn=self._quotes_fn(),
                     notifier=notifier)
        kinds = {a["kind"] for a in notifier.attention}
        assert "basket_peers_weak" in kinds
        assert any(x.startswith("basket_peers_weak:") for x in r.attention_alerts)
        # 台账落 sentinel_events(⑪-B:冷却/去重/防重沿用该表)
        events = load_events_for_date(tick_env.today, db_path=tick_env.db_path)
        assert any(e["sentinel"] == "attention" for e in events)
        # ⛔ 新 kind 不混进既有四哨兵的 channels 通道
        assert all("同篮" not in title for title, _b, _l in cap.messages)

    def test_dedupes_on_the_second_tick(self, tick_env):
        self._setup_weak_basket(tick_env)
        now = datetime.combine(tick_env.today, time(10, 30))
        n1 = _FakeNotifier()
        run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                 quotes_fn=self._quotes_fn(), notifier=n1)
        n2 = _FakeNotifier()
        r2 = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                      quotes_fn=self._quotes_fn(), notifier=n2)
        assert n2.attention == []              # 同一事件当日不轰炸第二次
        assert r2.skipped_duplicate >= 1

    def test_merged_exposure_is_exposed_on_the_tick_result(self, tick_env):
        today, d0 = tick_env.today, tick_env.report_day
        p1 = open_position("600001.SH", 10.0, 1000, today, db_path=tick_env.db_path)
        p2 = open_position("600002.SH", 10.0, 1000, today, db_path=tick_env.db_path)
        bid = _seed_basket_with_members(tick_env, d0, ["600001.SH", "600002.SH", "600003.SH"])
        _link(tick_env, p1, bid, "600001.SH", today)
        _link(tick_env, p2, bid, "600002.SH", today)
        now = datetime.combine(today, time(10, 30))
        r = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                     quotes_fn=self._quotes_fn(), notifier=_FakeNotifier())
        assert len(r.merged_exposure) == 1
        assert r.merged_exposure[0].theme_concentration is True

    def test_unavailable_reasons_are_reported(self, tick_env):
        """空仓 → 四监测如实标「没看」而不是「没事」。"""
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                     quotes_fn=lambda codes: {}, notifier=_FakeNotifier())
        assert r.attention_unavailable.get("all") == "no_open_position"


# ══════════════════════════════════════════════════════════════════════════
# ⑪-C NL 提醒接线
# ══════════════════════════════════════════════════════════════════════════

class TestCustomAlertBypassWiring:
    def test_hit_pushes_marks_fired_and_records_ledger(self, tick_env):
        # ⚠ `expires_at` 显式给远期:本文件的 `tick_env` 用的是**合成交易日**
        # (business_days 从 2026-07-01 数,落在真实"今天"之后),而非 persist 的提醒
        # 默认止于**创建当日**收盘 —— 不给到期时刻的话,这一拍会先被 expire_due 翻掉
        # (那正是收盘失效该有的行为,已在 test_sentinel_custom.py 单独覆盖)。
        a = ca.create_alert(
            rule={"conditions": [{"metric": "price", "op": "<=", "value": 9.95}]},
            nl_text="跌到 9.95 叫我", ts_code="600001.SH",
            expires_at="2099-01-01T15:00:00+08:00", db_path=tick_env.db_path,
        )
        open_position("600001.SH", 10.0, 1000, tick_env.today, db_path=tick_env.db_path)
        notifier = _FakeNotifier()
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                     quotes_fn=lambda codes: {"600001.SH": _q("600001.SH", 9.9)},
                     notifier=notifier)
        assert r.custom_alert_hits == [a.id]
        assert len(notifier.custom) == 1
        pushed = notifier.custom[0]
        assert "现价 ≤ 9.95 元" in pushed["condition"]
        # 行情延迟披露在命中推送里再说一遍(确认卡上答应过用户的那句)
        assert "延迟" in pushed["quote_delay_note"]
        # fired_count 递增 → 默认 max_fires=1 时不会有第二次
        assert ca.get_alert(a.id, db_path=tick_env.db_path).fired_count == 1
        assert already_pushed(tick_env.today, cu.SENTINEL_NAME, "600001.SH",
                              f"alert{a.id}#1", db_path=tick_env.db_path) is True

    def test_second_tick_does_not_fire_again(self, tick_env):
        ca.create_alert(rule={"conditions": [{"metric": "price", "op": "<=", "value": 9.95}]},
                        nl_text="x", ts_code="600001.SH",
                        expires_at="2099-01-01T15:00:00+08:00", db_path=tick_env.db_path)
        now = datetime.combine(tick_env.today, time(10, 30))
        qf = lambda codes: {"600001.SH": _q("600001.SH", 9.9)}  # noqa: E731
        run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                 quotes_fn=qf, notifier=_FakeNotifier())
        n2 = _FakeNotifier()
        r2 = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                      quotes_fn=qf, notifier=n2)
        assert r2.custom_alert_hits == [] and n2.custom == []


# ══════════════════════════════════════════════════════════════════════════
# 🔴 最贵的那条红线:旁路炸了,纪律分支毫发无损
# ══════════════════════════════════════════════════════════════════════════

class TestBypassFailuresNeverTouchDiscipline:
    def _boom_quotes(self):
        return lambda codes: {"600001.SH": _q("600001.SH", 9.0)}   # -10%,持仓哨兵该报警

    def test_attention_explosion_does_not_break_holding_sentinel(self, tick_env, monkeypatch):
        open_position("600001.SH", 10.0, 1000, tick_env.today, db_path=tick_env.db_path)
        monkeypatch.setattr(att, "evaluate_attention",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("旁路炸了")))
        cap = _CapturingChannel()
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, channels=[cap], db_path=tick_env.db_path,
                     parquet_dir=tick_env.parquet_dir, quotes_fn=self._boom_quotes(),
                     notifier=_FakeNotifier())
        assert r.holding_alerts and r.holding_alerts[0].alerts        # 持仓哨兵照常判、照常推
        assert any("持仓提醒" in t for t, _b, _l in cap.messages)
        assert r.attention_alerts == []                               # 旁路自己空着

    def test_custom_alert_explosion_does_not_break_holding_sentinel(self, tick_env, monkeypatch):
        open_position("600001.SH", 10.0, 1000, tick_env.today, db_path=tick_env.db_path)
        monkeypatch.setattr(cu, "evaluate_alerts",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("旁路炸了")))
        cap = _CapturingChannel()
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, channels=[cap], db_path=tick_env.db_path,
                     parquet_dir=tick_env.parquet_dir, quotes_fn=self._boom_quotes(),
                     notifier=_FakeNotifier())
        assert r.holding_alerts and r.holding_alerts[0].alerts
        assert r.custom_alert_hits == []

    def test_notifier_explosion_does_not_break_the_tick(self, tick_env):
        """连 APNs 措辞层自己炸掉,主循环也要活着回来。"""
        class _Boom:
            def push_attention_alert(self, *a, **k):
                raise RuntimeError("APNs 炸了")

            def push_custom_alert(self, *a, **k):
                raise RuntimeError("APNs 炸了")

        open_position("600001.SH", 10.0, 1000, tick_env.today, db_path=tick_env.db_path)
        now = datetime.combine(tick_env.today, time(10, 30))
        r = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                     quotes_fn=self._boom_quotes(), notifier=_Boom())
        assert r.holding_alerts                                       # 纪律判定照旧
        assert r.skipped_non_trading is False

    def test_bypasses_do_not_change_retreat_or_entry_paths(self, tick_env):
        """旁路存在与否,退潮 / 买点 / 证伪三条路径的结果一模一样(结构性对拍)。"""
        now = datetime.combine(tick_env.today, time(10, 30))
        qf = lambda codes: {"600001.SH": _q("600001.SH", 9.9)}  # noqa: E731
        r = run_tick(now, db_path=tick_env.db_path, parquet_dir=tick_env.parquet_dir,
                     quotes_fn=qf, notifier=_FakeNotifier())
        assert r.retreat_active is False and r.retreat_alert is None
        assert r.entry_signals == [] and r.invalidation_signals == []
