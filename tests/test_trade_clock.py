"""V2.2-④-B 交易时钟(`neckline/review/trade_clock.py`)。

覆盖 plan ④-B 点名的五条:开仓建行 · 部分平仓不结案 · 全平结案 · 八项齐 ·
`manual_note` 追加不改既有行。外加两条本块的红线:**上涨效率只出比值不下结论**、
**用户说明⛔ 不做 LLM 代猜**(§七 P3-28)。
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import pytest

from neckline.db import connection
from neckline.review import trade_clock as tc

from .conftest import source_code_only

pytestmark = pytest.mark.usefixtures("isolated_env")

_ROOT = Path(__file__).resolve().parent.parent
_MODULE = _ROOT / "neckline" / "review" / "trade_clock.py"

_PLAN = {
    "available": True, "driver": "固态电池放量",
    "entry_zone": {"low": 9.0, "high": 10.0}, "max_chase": 10.5,
    "exit_reference": {"low": 12.0, "high": 13.0},
    "upside_script": {"strong": "冲高"}, "invalidation": {"level": 8.5},
    "reason": "龙头位置好",
}


def _open(env, *, ts_code="A.SZ", buy_price=9.5, qty=1000, buy_date="20260805",
          basket_id=None, plan=None) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO positions (ts_code, buy_price, qty, buy_date, status, created_at,"
            " updated_at) VALUES (?,?,?,?,'open','t','t')",
            (ts_code, buy_price, qty, buy_date))
        pid = int(cur.lastrowid)
        conn.execute("INSERT INTO position_plans (position_id, version, plan_json, created_at)"
                     " VALUES (?,1,?,'t')",
                     (pid, json.dumps(plan if plan is not None else _PLAN)))
        if basket_id is not None:
            conn.execute("INSERT INTO entry_snapshots (position_id, ts_code, trade_date,"
                         " basket_id, snapshot_json, created_at) VALUES (?,?,?,?,'{}','t')",
                         (pid, ts_code, buy_date, basket_id))
    return pid


def _close(env, pid, *, sell_price=12.5, sell_date="20260812", reason="TAKE_PROFIT"):
    with connection(env.db_path) as conn:
        conn.execute("UPDATE positions SET status='closed', sell_price=?, sell_date=?,"
                     " close_reason=?, updated_at='t' WHERE id=?",
                     (sell_price, sell_date, reason, pid))


# ══════════════════════════════════════════════════════════════════════════
# 启动条件 = 实际买入
# ══════════════════════════════════════════════════════════════════════════

class TestStartCondition:
    def test_a_real_buy_creates_exactly_one_clock(self, isolated_env):
        env = isolated_env
        pid = _open(env, basket_id=7)
        res = tc.sync_from_positions("20260806", db_path=env.db_path)
        assert res.opened == 1 and res.running == 1
        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        assert clock["status"] == tc.STATUS_RUNNING
        assert clock["opened_on"] == "20260805"        # 开仓日 = buy_date,不是对账当天
        assert clock["basket_id"] == 7
        assert clock["entry_plan"]["driver"] == "固态电池放量"
        assert clock["final"] is None                  # 运行中恒 NULL

    def test_no_position_no_clock(self, isolated_env):
        res = tc.sync_from_positions("20260806", db_path=isolated_env.db_path)
        assert (res.opened, res.closed, res.running) == (0, 0, 0)
        assert tc.load_trade_clock(1, db_path=isolated_env.db_path) is None

    def test_manual_buy_without_a_basket_is_legal(self, isolated_env):
        env = isolated_env
        pid = _open(env, basket_id=None, plan={"available": False})
        tc.sync_from_positions("20260806", db_path=env.db_path)
        assert tc.load_trade_clock(pid, db_path=env.db_path)["basket_id"] is None

    def test_sync_is_idempotent(self, isolated_env):
        env = isolated_env
        _open(env)
        a = tc.sync_from_positions("20260806", db_path=env.db_path)
        b = tc.sync_from_positions("20260806", db_path=env.db_path)
        assert (a.opened, b.opened) == (1, 0)
        assert b.events == 0            # 同日 daily_check 不重复
        with connection(env.db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM trade_clock").fetchone()[0] == 1

    def test_entry_plan_is_the_v1_snapshot_not_the_latest_version(self, isolated_env):
        """K8 §十五「原始快照只增不改」——对账要的是**开仓当时**那份计划。"""
        env = isolated_env
        pid = _open(env)
        with connection(env.db_path) as conn:
            conn.execute("INSERT INTO position_plans (position_id, version, plan_json,"
                         " created_at) VALUES (?,2,?,'t')",
                         (pid, json.dumps({"driver": "用户后来改的"})))
        tc.sync_from_positions("20260806", db_path=env.db_path)
        assert tc.load_trade_clock(pid, db_path=env.db_path)["entry_plan"]["driver"] \
            == "固态电池放量"

    def test_daily_check_accumulates_one_per_day(self, isolated_env):
        env = isolated_env
        pid = _open(env)
        for day in ("20260806", "20260807", "20260807"):
            tc.sync_from_positions(day, db_path=env.db_path)
        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        kinds = [e["kind"] for e in tc.list_events(clock["id"], db_path=env.db_path)]
        assert kinds.count(tc.KIND_D1_OPEN) == 1
        assert kinds.count(tc.KIND_DAILY_CHECK) == 2      # 20260807 那两次只落一条

    def test_buy_day_gets_d1_open_only_not_also_a_daily_check(self, isolated_env):
        """K8 §十四 时间节点:**D1 买入日**记入场三件,**D2 及以后**才是每日跟踪 ——
        同一天两条讲同一件事会让按 kind 计数重复。"""
        env = isolated_env
        pid = _open(env, buy_date="20260805")
        tc.sync_from_positions("20260805", db_path=env.db_path)
        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        assert [e["kind"] for e in tc.list_events(clock["id"], db_path=env.db_path)] \
            == [tc.KIND_D1_OPEN]


# ══════════════════════════════════════════════════════════════════════════
# 结案 = 全部离场
# ══════════════════════════════════════════════════════════════════════════

class TestClosing:
    def test_still_open_means_still_running(self, isolated_env):
        env = isolated_env
        pid = _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        res = tc.sync_from_positions("20260807", db_path=env.db_path)
        assert res.closed == 0
        assert tc.load_trade_clock(pid, db_path=env.db_path)["status"] == tc.STATUS_RUNNING

    def test_one_of_two_lots_closed_does_not_close_the_other(self, isolated_env):
        """「部分平仓」在本项目的形状 = 同票两笔仓平掉一笔:**另一笔的时钟照跑**
        (外键是 `position_id`,一笔仓一个时钟 —— ⛔ 不按 ts_code 归并)。"""
        env = isolated_env
        a, b = _open(env), _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        _close(env, a)
        res = tc.sync_from_positions("20260813", db_path=env.db_path)
        assert res.closed == 1 and res.running == 1
        assert tc.load_trade_clock(a, db_path=env.db_path)["status"] == tc.STATUS_CLOSED
        assert tc.load_trade_clock(b, db_path=env.db_path)["status"] == tc.STATUS_RUNNING

    def test_full_exit_closes_with_all_eight_items(self, isolated_env):
        env = isolated_env
        pid = _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        _close(env, pid)
        tc.sync_from_positions("20260813", db_path=env.db_path)

        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        assert clock["status"] == tc.STATUS_CLOSED and clock["closed_on"] == "20260812"
        final = clock["final"]
        assert [k for k in tc.FINAL_ITEM_KEYS if k in final] == list(tc.FINAL_ITEM_KEYS)
        assert len(tc.FINAL_ITEM_KEYS) == 8
        assert final["entry_price_position"]["in_entry_zone"] is True    # 9.5 ∈ [9.0,10.0]
        assert final["entry_price_position"]["above_max_chase"] is False
        assert final["target_zone_handling"]["reached_target"] is True   # 12.5 ≥ 12.0
        assert final["stop_after_invalidation"]["close_reason"] == "TAKE_PROFIT"
        kinds = [e["kind"] for e in tc.list_events(clock["id"], db_path=env.db_path)]
        assert kinds[-1] == tc.KIND_CLOSE

    def test_closed_clock_is_not_reclosed_on_the_next_sync(self, isolated_env):
        env = isolated_env
        pid = _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        _close(env, pid)
        tc.sync_from_positions("20260813", db_path=env.db_path)
        again = tc.sync_from_positions("20260814", db_path=env.db_path)
        assert again.closed == 0
        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        assert sum(1 for e in tc.list_events(clock["id"], db_path=env.db_path)
                   if e["kind"] == tc.KIND_CLOSE) == 1

    def test_buy_outside_the_zone_is_reported_not_hidden(self, isolated_env):
        env = isolated_env
        pid = _open(env, buy_price=11.0)               # 高于区间上沿,也高于最高追价
        tc.sync_from_positions("20260806", db_path=env.db_path)
        _close(env, pid, sell_price=9.0, reason="STOP_LOSS")
        tc.sync_from_positions("20260813", db_path=env.db_path)
        final = tc.load_trade_clock(pid, db_path=env.db_path)["final"]
        assert final["entry_price_position"]["in_entry_zone"] is False
        assert final["entry_price_position"]["above_max_chase"] is True
        assert final["target_zone_handling"]["reached_target"] is False


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 上涨效率:只出比值,⛔ 不下结论
# ══════════════════════════════════════════════════════════════════════════

class TestUpsideEfficiency:
    def test_ratio_is_recent_over_baseline(self):
        out = tc.upside_efficiency([0.04, 0.04, 0.04, 0.01, 0.01, 0.01])
        assert out["available"] is True
        assert out["baseline"] == pytest.approx(0.04)
        assert out["recent"] == pytest.approx(0.01)
        assert out["ratio"] == pytest.approx(0.25)

    def test_short_holding_is_unavailable_not_zero(self):
        out = tc.upside_efficiency([0.02, 0.02])
        assert out["available"] is False and out["ratio"] is None
        assert "不足" in out["unavailable_reason"]

    def test_zero_baseline_is_none_not_infinity(self):
        out = tc.upside_efficiency([0.0, 0.0, 0.0, 0.05, 0.05, 0.05])
        assert out["ratio"] is None          # ⛔ 不拿 0 做分母、也不填一个 inf

    def test_it_never_says_efficiency_dropped(self):
        """🔴 K8 §十三:上涨效率下降 → **保留主观换股权,不设机械规则**。"""
        out = tc.upside_efficiency([0.05, 0.05, 0.05, -0.02, -0.02, -0.02])
        assert set(out) == {"available", "source", "unavailable_reason", "window",
                            "observations", "baseline", "recent", "ratio", "note"}
        assert "⛔ 不设阈值" in out["note"]
        assert out["source"] == "engineering_v1"

    def test_module_never_pushes_or_touches_holdings_discipline(self):
        """⑥ 只进复盘与展示:⛔ 不触发持仓动作、⛔ 不进推送。

        ⚠ 扫的是**剥掉注释与 docstring 的代码**(`source_code_only`)—— 模块头正
        写着「⛔ 不进推送」这句话,裸 grep 会被自己的护栏注释绊住(CLAUDE.md ⑰ 教训)。"""
        code = source_code_only(_MODULE)
        for banned in ("notify", "push_", "close_position", "sentinel.holding"):
            assert banned not in code, f"交易时钟碰了 {banned} —— 它只记录,不动作"


# ══════════════════════════════════════════════════════════════════════════
# K8 §十五 用户主观说明(§七 P3-28 的落点)
# ══════════════════════════════════════════════════════════════════════════

class TestUserNote:
    def test_note_appends_and_never_rewrites(self, isolated_env):
        env = isolated_env
        pid = _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        clock = tc.load_trade_clock(pid, db_path=env.db_path)
        before = tc.list_events(clock["id"], db_path=env.db_path)

        tc.append_user_note(pid, "板块情绪转弱,我先减一半", event_date="20260807",
                            db_path=env.db_path)
        tc.append_user_note(pid, "第二条", event_date="20260808", db_path=env.db_path)
        after = tc.list_events(clock["id"], db_path=env.db_path)

        assert after[:len(before)] == before          # 既有行逐位不变
        notes = [e for e in after if e["kind"] == tc.KIND_MANUAL_NOTE]
        assert [n["user_note"] for n in notes] == ["板块情绪转弱,我先减一半", "第二条"]

    def test_note_on_a_position_without_a_clock_is_none_not_a_crash(self, isolated_env):
        assert tc.append_user_note(999, "无主之说明", db_path=isolated_env.db_path) is None

    @pytest.mark.parametrize("bad", ["", "   ", "x" * (tc.USER_NOTE_MAX_CHARS + 1)])
    def test_empty_or_overlong_fails_loud_instead_of_being_truncated(self, isolated_env, bad):
        """⛔ 不静默截断:截断会把用户写的话改掉一半还装作收下了。"""
        env = isolated_env
        pid = _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        with pytest.raises(tc.UserNoteError):
            tc.append_user_note(pid, bad, db_path=env.db_path)

    def test_coverage_makes_sparsity_visible(self, isolated_env):
        env = isolated_env
        a, _b = _open(env), _open(env)
        tc.sync_from_positions("20260806", db_path=env.db_path)
        assert tc.note_coverage(db_path=env.db_path)["coverage"] == 0.0
        tc.append_user_note(a, "一条", db_path=env.db_path)
        tc.append_user_note(a, "又一条", db_path=env.db_path)
        cov = tc.note_coverage(db_path=env.db_path)
        assert (cov["trades"], cov["with_note"], cov["notes"]) == (2, 1, 2)
        assert cov["coverage"] == 0.5

    def test_coverage_with_no_trades_is_unavailable_not_zero(self, isolated_env):
        cov = tc.note_coverage(db_path=isolated_env.db_path)
        assert cov["available"] is False and cov["coverage"] is None

    def test_no_llm_guessing_anywhere(self):
        """§七 P3-28 纪律不变:候选解法 ②(LLM 代猜标签)**仍然不做**。

        ⚠ 判据是 **import 面 + 调用面**,不是"源码里出不出现 llm 三个字母" ——
        本模块的运行期文案里就写着「⛔ 不做 LLM 代猜」这句话给用户看,
        按字面 grep 会被自己的诚实披露绊住(CLAUDE.md ⑰ 教训)。"""
        tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "chat" not in calls
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert not [m for m in imported if m.startswith("neckline.llm")], \
            "交易时钟 import 了 LLM 包 —— P3-28 的候选解法 ②(代猜标签)本版不做"


# ══════════════════════════════════════════════════════════════════════════
# 与交割单的关系:没有关系(§七 P3-38)
# ══════════════════════════════════════════════════════════════════════════

def test_never_matches_against_the_broker_statement():
    """外键是 `position_id`(确定性),⛔ 与交割单侧不做任何近似匹配(§七 P3-38)。

    ⚠ 同上,扫**代码**不扫注释:模块头正解释着「与 RoundTrip 不做近似匹配」。"""
    code = source_code_only(_MODULE)
    for banned in ("RoundTrip", "review.parse", "reconcile"):
        assert banned not in code, f"交易时钟碰了交割单侧 {banned}(§七 P3-38 明令不许)"


def test_append_only_events_have_no_rewrite_path():
    text = source_code_only(_MODULE).upper()
    for verb in ("UPDATE TRADE_CLOCK_EVENTS", "DELETE FROM TRADE_CLOCK_EVENTS",
                 "INSERT OR REPLACE INTO TRADE_CLOCK_EVENTS"):
        assert verb not in text


def test_sync_never_raises_on_a_broken_row(isolated_env, monkeypatch):
    """一笔炸不连坐其余(同链的保险丝哲学)。"""
    env = isolated_env
    _open(env)
    monkeypatch.setattr(tc, "_entry_plan_snapshot",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("炸")))
    res = tc.sync_from_positions("20260806", db_path=env.db_path)
    assert res.opened == 0 and res.notes
