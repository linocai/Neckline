"""⑪-A 四监测 + 同篮合并敞口单测(plan §五 V2-⑪-A;蓝图 5.4 / 6.2)。

覆盖三件事:
    ① 四条监测各自的**触发 / 不触发 / 数据不足**三态(尤其是「样本不足 ≠ 篮子健康」);
    ② 同篮合并敞口按来源篮子归并、按「不同标的 ≥2」才算主题集中;
    ③ 守门:本模块对篮子四表**零写入**,且不含任何交易动作。
"""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path

import pytest

from neckline.data.board import Board
from neckline.db import connection
from neckline.sentinel import attention as att
from neckline.sentinel.positions import Position
from neckline.sentinel.quotes import Quote
from neckline.sentinel.universe import StockMeta

pytestmark = pytest.mark.usefixtures("isolated_env")

D0 = date(2026, 7, 30)
TODAY = date(2026, 7, 31)


def _q(code, price, pre_close=10.0, high=None, low=None, name="") -> Quote:
    return Quote(code=code, name=name or code, price=price, pre_close=pre_close,
                 open=pre_close, high=high if high is not None else max(price, pre_close),
                 low=low if low is not None else min(price, pre_close),
                 volume=1000.0, amount=1_000_000.0, ts="", source="test")


def _pos(pid, code, buy_price=10.0, qty=1000) -> Position:
    return Position(id=pid, ts_code=code, buy_price=buy_price, qty=qty,
                    buy_date=TODAY.strftime("%Y%m%d"), status="open",
                    sell_price=None, sell_date=None, note=None)


def _meta(code, board=Board.MAIN) -> StockMeta:
    return StockMeta(ts_code=code, name=code, board=board, is_st=False, list_date=None)


def _seed_basket(env, codes, *, key="k1", name="AI 算力", driver="算力扩产", tier=1) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), key, name, driver, "theme", tier,
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


def _link_entry_snapshot(env, position_id: int, basket_id: int, tier: int = 1) -> None:
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id,"
            " card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (position_id, "x", TODAY.strftime("%Y%m%d"), basket_id, 1, tier, "core", "{}",
             "2026-07-31T00:00:00+08:00"),
        )


def _src(basket_id=1, members=("600001.SH", "600002.SH", "600003.SH")) -> att.PositionSource:
    return att.PositionSource(
        position_id=1, basket_id=basket_id, basket_key="k1", basket_name="AI 算力",
        driver="算力扩产", tier=1, member_codes=tuple(members), link_source="entry_snapshot",
    )


# ══════════════════════════════════════════════════════════════════════════
# 基础量:缺数据一律 None,不是 0
# ══════════════════════════════════════════════════════════════════════════

def test_intraday_return_missing_quote_is_none_not_zero():
    assert att.intraday_return(None) is None
    assert att.intraday_return(_q("x", 10.0, pre_close=0.0)) is None
    assert att.intraday_return(_q("x", 9.0, pre_close=10.0)) == pytest.approx(-0.1)


def test_retrace_from_day_high():
    assert att.retrace_from_day_high(_q("x", 9.0, 10.0, high=10.0)) == pytest.approx(0.1)
    assert att.retrace_from_day_high(None) is None


# ══════════════════════════════════════════════════════════════════════════
# ① 同篮成员集体转弱
# ══════════════════════════════════════════════════════════════════════════

class TestBasketPeersWeak:
    def test_triggers_when_half_of_peers_are_down_and_mean_is_weak(self):
        src = _src()
        quotes = {
            "600000.SH": _q("600000.SH", 9.9),
            "600001.SH": _q("600001.SH", 9.5),    # -5%
            "600002.SH": _q("600002.SH", 9.6),    # -4%
            "600003.SH": _q("600003.SH", 9.95),   # -0.5%
        }
        a = att.check_basket_peers_weak(_pos(1, "600000.SH"), src, quotes)
        assert a is not None
        assert a.kind == "basket_peers_weak"
        assert a.metrics["sample_n"] == 3 and a.metrics["weak_n"] == 2
        assert "AI 算力" in a.what_happened
        assert "算力扩产" in a.plan_touched      # 「触碰了哪条计划」有实质内容

    def test_no_trigger_when_only_one_peer_crashes(self):
        """一只暴跌拉着均值过线也不算「集体」——占比那一条挡住它。"""
        src = _src()
        quotes = {
            "600001.SH": _q("600001.SH", 8.0),    # -20%
            "600002.SH": _q("600002.SH", 10.1),
            "600003.SH": _q("600003.SH", 10.2),
        }
        assert att.check_basket_peers_weak(_pos(1, "600000.SH"), src, quotes) is None

    def test_insufficient_sample_is_silence_not_health(self):
        """只有一只同篮成员有行情 → 不判(**「没有样本」不是「篮子健康」**)。"""
        src = _src()
        quotes = {"600001.SH": _q("600001.SH", 8.0)}
        assert att.check_basket_peers_weak(_pos(1, "600000.SH"), src, quotes) is None

    def test_coverage_is_reported_honestly(self):
        src = _src(members=("600001.SH", "600002.SH", "600003.SH", "600004.SH", "600005.SH"))
        quotes = {c: _q(c, 9.5) for c in ("600001.SH", "600002.SH")}
        a = att.check_basket_peers_weak(_pos(1, "600000.SH"), src, quotes)
        assert a is not None
        assert a.metrics["sample_n"] == 2 and a.metrics["peer_total"] == 5
        assert "2/5" in a.what_happened


# ══════════════════════════════════════════════════════════════════════════
# ② 板块(基准指数)承接消失
# ══════════════════════════════════════════════════════════════════════════

class TestSectorBidFade:
    def test_triggers_on_fall_plus_retrace(self):
        a = att.check_sector_bid_fade("399006.SZ", _q("399006.SZ", 9.85, 10.0, high=10.2),
                                      holders=["300001.SZ"])
        assert a is not None and a.kind == "sector_bid_fade"
        assert "不是板块 ETF" in a.what_happened      # ⑧-A / P4-35 的诚实标注

    def test_no_trigger_when_falling_without_retrace(self):
        """低开阴跌(没有冲高回落)不是「突然失去承接」。"""
        assert att.check_sector_bid_fade("399006.SZ", _q("399006.SZ", 9.85, 10.0, high=9.85),
                                         holders=[]) is None

    def test_no_trigger_when_retraced_but_still_green(self):
        """从大涨回到小涨 = 高位整理,不是承接消失。"""
        assert att.check_sector_bid_fade("399006.SZ", _q("399006.SZ", 10.3, 10.0, high=10.6),
                                         holders=[]) is None

    def test_missing_quote_is_silence(self):
        assert att.check_sector_bid_fade("399006.SZ", None, holders=[]) is None


# ══════════════════════════════════════════════════════════════════════════
# ③ 持仓从跟随转独立弱势
# ══════════════════════════════════════════════════════════════════════════

class TestHoldingDecoupled:
    def test_triggers_when_self_dives_while_reference_holds(self):
        a = att.check_holding_decoupled(
            _pos(1, "600000.SH"), _q("600000.SH", 9.6, 10.0),   # -4%
            ref_ret=0.005, ref_label="同篮其它成员均值", ref_sample=3,
        )
        assert a is not None and a.kind == "holding_decoupled"
        assert a.metrics["gap"] == pytest.approx(-0.045)

    def test_no_trigger_when_the_whole_environment_is_down(self):
        """板块自己也在跌 → 这是「跟着跌」,不是「独立弱势」(两回事,别混)。"""
        assert att.check_holding_decoupled(
            _pos(1, "600000.SH"), _q("600000.SH", 9.6, 10.0),
            ref_ret=-0.04, ref_label="同篮其它成员均值", ref_sample=3,
        ) is None

    def test_no_reference_means_no_judgement_not_zero_reference(self):
        """缺参照 → 不判;**⛔ 不拿 0 当参照**(那等于偷偷假设大盘平盘)。"""
        assert att.check_holding_decoupled(
            _pos(1, "600000.SH"), _q("600000.SH", 9.0, 10.0),
            ref_ret=None, ref_label="", ref_sample=None,
        ) is None


# ══════════════════════════════════════════════════════════════════════════
# ④ 大盘突变
# ══════════════════════════════════════════════════════════════════════════

class TestMarketShock:
    def test_triggers_on_deep_index_fall(self):
        a, reason = att.check_market_shock({"000001.SH": _q("000001.SH", 9.7, 10.0)},
                                           position_count=2)
        assert reason is None and a is not None and a.kind == "market_shock"
        assert a.metrics["index"] == "000001.SH"

    def test_triggers_on_sharp_retrace_that_turns_red(self):
        a, _r = att.check_market_shock(
            {"399001.SZ": _q("399001.SZ", 9.94, 10.0, high=10.12)}, position_count=1)
        assert a is not None

    def test_no_position_means_not_evaluated(self):
        """蓝图口径:大盘突变问的是「影响**全部持仓**」,空仓时不打扰。"""
        a, reason = att.check_market_shock({"000001.SH": _q("000001.SH", 9.0, 10.0)},
                                           position_count=0)
        assert a is None and reason == "no_open_position"

    def test_no_broad_index_quote_is_reported_as_unavailable(self):
        """纯创业板持仓的日子池里可能一支宽基都没有 → 如实标「没看」,不是「没事」。"""
        a, reason = att.check_market_shock({"399006.SZ": _q("399006.SZ", 8.0, 10.0)},
                                           position_count=1)
        assert a is None and reason == "no_broad_index_quote"

    def test_calm_market_is_none_reason_none(self):
        """判了、没事 —— reason 也是 None(与「没判」区分开)。"""
        a, reason = att.check_market_shock({"000001.SH": _q("000001.SH", 10.02, 10.0)},
                                           position_count=1)
        assert a is None and reason is None


# ══════════════════════════════════════════════════════════════════════════
# 同篮合并敞口(蓝图 6.2)
# ══════════════════════════════════════════════════════════════════════════

class TestMergedExposure:
    def test_two_different_codes_in_one_basket_is_theme_concentration(self):
        positions = [_pos(1, "600001.SH", 10.0, 1000), _pos(2, "600002.SH", 20.0, 500)]
        sources = {1: _src(), 2: _src()}
        groups = att.compute_merged_exposure(
            positions, {"600001.SH": _q("600001.SH", 11.0), "600002.SH": _q("600002.SH", 21.0)},
            sources, total_capital=120000.0,
        )
        assert len(groups) == 1
        g = groups[0]
        assert g.theme_concentration is True
        assert g.cost_amount == pytest.approx(20000.0)
        assert g.market_amount == pytest.approx(21500.0)
        assert g.market_partial is False
        assert g.cost_share_of_total == pytest.approx(20000.0 / 120000.0, abs=1e-4)

    def test_two_tranches_of_same_code_is_not_theme_concentration(self):
        """同一只票分批建的两笔本来就没人会误以为分散 —— 归组但不标主题集中。"""
        positions = [_pos(1, "600001.SH"), _pos(2, "600001.SH")]
        groups = att.compute_merged_exposure(positions, {}, {1: _src(), 2: _src()})
        assert len(groups) == 1 and groups[0].theme_concentration is False

    def test_missing_quote_marks_partial_not_cost_as_market(self):
        """缺行情的那笔**不拿成本冒充市值**,整组标 partial。"""
        positions = [_pos(1, "600001.SH"), _pos(2, "600002.SH")]
        groups = att.compute_merged_exposure(
            positions, {"600001.SH": _q("600001.SH", 11.0)}, {1: _src(), 2: _src()})
        assert groups[0].market_partial is True

    def test_single_position_basket_is_not_a_group(self):
        assert att.compute_merged_exposure([_pos(1, "600001.SH")], {}, {1: _src()}) == []

    def test_positions_without_source_are_not_merged(self):
        assert att.compute_merged_exposure(
            [_pos(1, "600001.SH"), _pos(2, "600002.SH")], {}, {}) == []


# ══════════════════════════════════════════════════════════════════════════
# 来源篮子关联(两条来源都要能查到、都要如实标)
# ══════════════════════════════════════════════════════════════════════════

class TestLoadPositionSources:
    def test_from_entry_snapshot(self, isolated_env):
        bid = _seed_basket(isolated_env, ["600001.SH", "600002.SH"])
        _link_entry_snapshot(isolated_env, 7, bid)
        got = att.load_position_sources([_pos(7, "600001.SH")], db_path=isolated_env.db_path)
        assert got[7].basket_id == bid and got[7].link_source == "entry_snapshot"
        assert got[7].member_codes == ("600001.SH", "600002.SH")

    def test_falls_back_to_position_plan(self, isolated_env):
        bid = _seed_basket(isolated_env, ["600001.SH"])
        with connection(isolated_env.db_path) as conn:
            conn.execute(
                "INSERT INTO position_plans (position_id, version, source_basket_id,"
                " source_card_version, plan_json, note, created_at) VALUES (?,?,?,?,?,?,?)",
                (9, 1, bid, 1, "{}", None, "2026-07-31T00:00:00+08:00"),
            )
        got = att.load_position_sources([_pos(9, "600001.SH")], db_path=isolated_env.db_path)
        assert got[9].basket_id == bid and got[9].link_source == "position_plan"

    def test_unlinked_position_is_absent_not_invented(self, isolated_env):
        got = att.load_position_sources([_pos(11, "600001.SH")], db_path=isolated_env.db_path)
        assert got == {}


# ══════════════════════════════════════════════════════════════════════════
# 编排
# ══════════════════════════════════════════════════════════════════════════

class TestEvaluateAttention:
    def test_no_positions_short_circuits_with_reasons(self, isolated_env):
        r = att.evaluate_attention(TODAY, [], {}, {}, db_path=isolated_env.db_path)
        assert r.alerts == []
        assert r.unavailable["all"] == "no_open_position"
        assert r.unavailable["market_shock"] == "no_open_position"

    def test_end_to_end_basket_weak_with_merged_exposure_note(self, isolated_env):
        bid = _seed_basket(isolated_env, ["600001.SH", "600002.SH", "600003.SH"])
        _link_entry_snapshot(isolated_env, 1, bid)
        with connection(isolated_env.db_path) as conn:
            conn.execute(
                "INSERT INTO entry_snapshots (position_id, ts_code, trade_date, basket_id,"
                " card_version, tier, role, snapshot_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (2, "600002.SH", TODAY.strftime("%Y%m%d"), bid, 1, 1, "core", "{}",
                 "2026-07-31T00:00:00+08:00"),
            )
        positions = [_pos(1, "600001.SH"), _pos(2, "600002.SH")]
        quotes = {
            "600001.SH": _q("600001.SH", 9.9),
            "600002.SH": _q("600002.SH", 9.5),
            "600003.SH": _q("600003.SH", 9.4),
        }
        meta = {c: _meta(c) for c in ("600001.SH", "600002.SH", "600003.SH")}
        r = att.evaluate_attention(TODAY, positions, quotes, meta, db_path=isolated_env.db_path)
        kinds = {a.kind for a in r.alerts}
        assert "basket_peers_weak" in kinds
        weak = [a for a in r.alerts if a.kind == "basket_peers_weak"][0]
        assert "同篮合并敞口" in weak.merged_exposure_note
        assert len(r.merged_exposure) == 1

    def test_defaults_whitelist_is_registered(self):
        """阈值白名单必须与常量一一对得上(⑦-b 体例:工程默认要在一处可审计)。"""
        assert att.ATTENTION_DEFAULTS["peer_weak_ret"] == att.PEER_WEAK_RET
        assert att.ATTENTION_DEFAULTS["market_shock_ret"] == att.MARKET_SHOCK_RET
        assert len(att.ATTENTION_DEFAULTS) == 13


# ══════════════════════════════════════════════════════════════════════════
# 守门:零写入 / 零交易动作
# ══════════════════════════════════════════════════════════════════════════

_TRADE_WORDS = ("place_order", "submit_order", "cancel_order", "下单", "撤单",
                "buy_order", "sell_order", "broker_api")


def _module_src(mod) -> str:
    return Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8")


def test_attention_never_writes_to_any_table():
    """AST 守门:本模块的 SQL 全是 SELECT —— 对 `baskets`/`basket_members`/
    `basket_cards`/`tier_history`/`positions` 零写入(承 ⑩-E 同一条纪律:持仓侧
    不回头改选股侧已冻结的历史信息;监测更是只读旁路)。"""
    src = _module_src(att)
    lowered = src.lower()
    for verb in ("insert into", "update ", "delete from", "drop table", "alter table"):
        assert verb not in lowered, f"attention.py 不该出现写 SQL:{verb}"


def test_attention_has_no_trading_action():
    src = _module_src(att)
    for w in _TRADE_WORDS:
        # 「不代下单」这类**否定句**允许出现在注释里;这里只查可执行代码
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            assert w not in stripped or "不" in stripped, f"疑似交易动作:{line}"
