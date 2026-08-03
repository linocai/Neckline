"""V2-⑧-C / ⑧-C2 篮子验证状态机(plan §五 V2-⑧ 验收逐条)。

覆盖:四态各一;append-only(只 INSERT,没有 UPDATE/DELETE 路径);⑧-C2 六条 ——
①盘中与 EOD 同一份 spec(同价代入两条路径逐位相同)②锚取 D0 冻结值(改"当前"
MA20 / 跌停 / 现役 `stop_pct` → 判定不变)③状态未变不落行、变了才落 + EOD 必落一行
④「当前状态」三路读法 ⑤`falsified` 当日终态不撤回、`verified` 可翻 ⑥无卡 →
`unclear` + `no_card`;另加「缺数据两侧都不计 + `member_data_missing`」。
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path

import pytest

from tests.conftest import business_days, insert_trade_cal, seed_active_rule_v1, write_daily_fixture

from neckline.db import connection
from neckline.selection import basket_card as bc
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import save_basket_card
from neckline.sentinel import basket_verify as bv
from neckline.sentinel import basket_verify_store as bvs

pytestmark = pytest.mark.usefixtures("isolated_env")

_REPO = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# 夹具:一张最小但**真实**的冻结卡(spec 由 ⑦ 的 builder 生成,不手搓)
# ══════════════════════════════════════════════════════════════════════════

def _mech(code: str, *, close=10.0, ma20=9.2, limit_down=9.0, stop_price=9.5) -> bc.MemberMech:
    return bc.MemberMech(ts_code=code, name=code, close=close, ma20=ma20,
                         limit_up=11.0, limit_down=limit_down, stop_price=stop_price)


def _card_json(d0: date, mechs, *, stop_pct=0.05) -> dict:
    """只装 ⑧ 会读的两份 spec(其余卡面项与本块判定无关)。"""
    return {
        "spec_version": bc.CARD_SPEC_VERSION,
        "verification_spec": bc.build_verification_spec("bk", d0, mechs),
        "invalidation_spec": bc.build_invalidation_spec("bk", d0, mechs, stop_pct=stop_pct),
        "fingerprint": {"stop_pct": stop_pct,
                        "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
    }


def _obs(code: str, price, low=None) -> bv.MemberObservation:
    return bv.MemberObservation(ts_code=code, price=price, low=low if low is not None else price)


def _seed_basket(env, d0: date, codes, *, tier=1, key="k1", name="测试篮",
                 mechs=None, with_card=True, stop_pct=0.05) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), key, name, "某共同驱动", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        basket_id = int(cur.lastrowid)
        for code in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, code, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
    if with_card:
        ms = mechs if mechs is not None else [_mech(c) for c in codes]
        save_basket_card(basket_id, _card_json(d0, ms, stop_pct=stop_pct),
                         stop_pct=stop_pct, db_path=env.db_path)
    return basket_id


# ══════════════════════════════════════════════════════════════════════════
# 四态(纯函数层,喂真 spec)
# ══════════════════════════════════════════════════════════════════════════

def _verdict(mechs, obs, stop_pct=0.05) -> bv.BasketVerdict:
    d0 = date(2026, 7, 24)
    v = bc.build_verification_spec("bk", d0, mechs)
    iv = bc.build_invalidation_spec("bk", d0, mechs, stop_pct=stop_pct)
    return bv.evaluate_specs(v, iv, obs)


def test_state_verified_all_members_hold():
    mechs = [_mech("600000.SH"), _mech("600001.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5),
                           "600001.SH": _obs("600001.SH", 10.1)})
    assert out.state == vr.STATE_VERIFIED and out.verify_hits == 2 and out.min_members_hit == 1


def test_state_partial_half_of_three():
    """n=3 → `min_members_hit=2`;只有 1 只命中 → `partial`(四态里唯一承接它的格子)。"""
    mechs = [_mech(c) for c in ("600000.SH", "600001.SH", "600002.SH")]
    out = _verdict(mechs, {
        "600000.SH": _obs("600000.SH", 10.5),     # 验证命中
        "600001.SH": _obs("600001.SH", 9.8),      # < 收盘但守住 MA20 → 中间地带
        "600002.SH": _obs("600002.SH", 9.7),
    })
    assert out.state == vr.STATE_PARTIAL and out.verify_hits == 1 and out.invalidate_hits == 0


def test_state_unclear_when_nobody_hits_either_side():
    mechs = [_mech(c) for c in ("600000.SH", "600001.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 9.8),
                           "600001.SH": _obs("600001.SH", 9.9)})
    assert out.state == vr.STATE_UNCLEAR and out.verify_hits == 0 and out.invalidate_hits == 0


def test_state_falsified_below_stop_line():
    mechs = [_mech("600000.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 9.4)})   # ≤ 止损线 9.5
    assert out.state == vr.STATE_FALSIFIED and out.invalidate_hits == 1


def test_state_falsified_on_limit_down_touch_even_if_recovered():
    """触及跌停即算(不要求收在跌停):现价拉回 9.9,但当日最低摸到 9.0。"""
    mechs = [_mech("600000.SH")]
    out = _verdict(mechs, {"600000.SH": bv.MemberObservation("600000.SH", price=9.9, low=9.0)})
    assert out.state == vr.STATE_FALSIFIED


def test_falsified_wins_when_both_sides_reach_threshold():
    mechs = [_mech(c) for c in ("600000.SH", "600001.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5),      # 验证命中
                           "600001.SH": _obs("600001.SH", 9.4)})      # 失效命中
    assert out.verify_hits == 1 and out.invalidate_hits == 1
    assert out.state == vr.STATE_FALSIFIED      # 门槛都是 1 → 证伪优先


def test_missing_member_data_counts_for_neither_side():
    """停牌 / 数据缺口 → 该成员两侧都不计,并如实标 `member_data_missing`。
    ⛔ 「查不到」不许算成失效。"""
    mechs = [_mech(c) for c in ("600000.SH", "600001.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5)})
    assert out.state == vr.STATE_VERIFIED       # 门槛 1,活着的那只命中就够
    assert out.evidence[bv.FLAG_MEMBER_DATA_MISSING] == ["600001.SH"]
    assert out.observed_members == 1
    missing_row = [m for m in out.evidence["members"] if m["ts_code"] == "600001.SH"][0]
    assert missing_row["verify"] is None and missing_row["invalidate"] is None


def test_all_missing_yields_unclear_not_falsified():
    mechs = [_mech(c) for c in ("600000.SH", "600001.SH")]
    out = _verdict(mechs, {})
    assert out.state == vr.STATE_UNCLEAR and out.invalidate_hits == 0
    assert out.evidence[bv.FLAG_MEMBER_DATA_MISSING] == ["600000.SH", "600001.SH"]


def test_spec_level_null_condition_is_not_a_failure_but_blocks_the_and():
    """判定线审计 🟡-1(2026-08-03,`verify_ruleset_v2`):卡里 MA20 算不出(null)时,
    那一条**不判**(⛔ 不当成"不满足"),但 ⑦-b-B 的「两条 AND」**不许因此降格成单条**
    —— 该成员验证侧整体不下结论、不计命中,并如实标 `spec_levels_partial`。

    修之前:`judged` 只收集可判条件、`all()` 在子集上取真 → 只要收盘 ≥ D0 收盘就白送
    一个验证命中,`flags` 一片空白,`min_hit=1` 的篮子能仅凭这只"半判"成员进 `verified`。
    """
    mechs = [_mech("600000.SH", ma20=None)]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5)})
    assert out.state == vr.STATE_UNCLEAR and out.verify_hits == 0
    row = out.evidence["members"][0]
    assert row["verify_conditions"][vr.COND_HOLDS_MA20] is None
    assert row["verify_conditions"][vr.COND_CLOSE_AT_OR_ABOVE_REF] is True
    assert row["verify"] is None, "半判 → 该侧不下结论(不是 False,也不是 True)"
    assert bv.FLAG_SPEC_LEVELS_PARTIAL in row["flags"]
    assert out.evidence[bv.FLAG_SPEC_LEVELS_PARTIAL] == ["600000.SH"]


def test_partial_levels_still_allow_a_decisive_negative():
    """三值逻辑不是「有 null 就整侧不判」:某条**已经确定不满足**时,AND 侧照样给
    `False`(定论就是定论)——这条与上一条一起,证明修法两边都不冤枉。"""
    mechs = [_mech("600000.SH", ma20=None)]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 9.8)})   # < D0 收盘 10.0
    row = out.evidence["members"][0]
    assert row["verify"] is False and out.verify_hits == 0
    assert row["verify_conditions"][vr.COND_CLOSE_AT_OR_ABOVE_REF] is False
    assert out.state == vr.STATE_UNCLEAR


def test_invalidation_side_is_symmetric_under_partial_levels():
    """失效侧对称核查:OR 侧在「已判的全 False、还有一条判不了」时同样**不下结论**
    (`None`),⛔ 不许把"判不了"读成"没失效"。计数上两者都不加分,但证据里必须分得开
    —— ⑨ 靠它区分「确实没破位」与「今天根本判不了」。"""
    mechs = [_mech("600000.SH", ma20=None)]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 9.8)})
    row = out.evidence["members"][0]
    # 止损线 9.5 / 跌停 9.0 都判得了且都没触发,复合条件因 ma20=null 判不了 → 整侧 None
    assert row["invalidate_conditions"][vr.COND_CLOSE_BELOW_STOP_LINE] is False
    assert row["invalidate_conditions"][vr.COND_BELOW_REF_AND_MA20] is None
    assert row["invalidate"] is None and out.invalidate_hits == 0


def test_all_levels_null_keeps_the_old_missing_flag():
    """两侧**一条都判不了** → 仍是原来的 `spec_levels_missing`(不是新 partial 位):
    「这张卡这只票压根没锚」与「锚缺了一半」是两件事,别合并。"""
    mechs = [_mech("600000.SH", close=None, ma20=None, limit_down=None, stop_price=None)]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5)})
    row = out.evidence["members"][0]
    assert bv.FLAG_SPEC_LEVELS_MISSING in row["flags"]
    assert bv.FLAG_SPEC_LEVELS_PARTIAL not in row["flags"]
    assert bv.FLAG_SPEC_LEVELS_PARTIAL not in out.evidence


def test_ruleset_version_records_both_card_and_engine():
    """🟡-1 bump 之后:证据里同时记「卡上冻的条件集版本」与「判定代码当下的版本」——
    跨版本那几天(老卡 × 新读法)⑨ 才不会把成绩记错层而无从察觉。"""
    mechs = [_mech("600000.SH")]
    out = _verdict(mechs, {"600000.SH": _obs("600000.SH", 10.5)})
    assert out.evidence["ruleset_version"] == vr.VERIFICATION_RULESET_VERSION
    assert out.evidence["ruleset_version_engine"] == vr.VERIFICATION_RULESET_VERSION


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ①:盘中与 EOD 同一份 spec —— 同一价代入两条路径,判定逐位相同
# ══════════════════════════════════════════════════════════════════════════

def test_intraday_and_eod_paths_agree_bit_for_bit(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])

    class _Q:      # 盘中 Quote 的鸭子替身(只用 price/low/volume/amount/source)
        price, low, volume, amount, source = 10.4, 10.0, 100.0, 1000.0, "sina"

    intra = bv.run_intraday_verification(
        d1, {"600000.SH": _Q()}, attempted_codes=["600000.SH"],
        now=datetime.combine(d1, datetime.min.time()).replace(hour=10), db_path=isolated_env.db_path)

    # EOD:同一个价格写进 daily 面板,走另一条路径
    write_daily_fixture(isolated_env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 10.0, "high": 10.6, "low": 10.0, "close": 10.4,
         "pre_close": 10.0, "vol": 100.0, "amount": 1000.0},
    ])
    eod = bv.run_eod_verification(d1, db_path=isolated_env.db_path,
                                  parquet_dir=isolated_env.parquet_dir)
    assert intra.states[basket_id] == eod.states[basket_id] == vr.STATE_VERIFIED

    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    intra_row = [r for r in rows if r.source == "intraday"][0]
    eod_row = [r for r in rows if r.source == "eod"][0]
    for key in ("verify_hits", "invalidate_hits", "min_members_hit", "members"):
        assert intra_row.evidence[key] == eod_row.evidence[key], key


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ②:锚一律取 D0 冻结值(改"当前"的三个数,判定不变)
# ══════════════════════════════════════════════════════════════════════════

def test_anchors_come_from_the_frozen_card_not_current_config(isolated_env):
    """卡冻的是 `stop_pct=0.05`(止损线 9.5)。把**现役章程**改成 0.20(线会是 8.0)后
    再判 9.4:仍判 `falsified` —— 证明读的是卡,不是"当前现役 config"。"""
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"], stop_pct=0.05)

    seed_active_rule_v1(isolated_env, extra_config={"stop_pct": 0.20})

    class _Q:
        price, low, volume, amount, source = 9.4, 9.4, 1.0, 1.0, "sina"

    res = bv.run_intraday_verification(d1, {"600000.SH": _Q()}, attempted_codes=["600000.SH"],
                                       db_path=isolated_env.db_path)
    assert res.states[basket_id] == vr.STATE_FALSIFIED
    row = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)[0]
    hit = row.evidence["members"][0]["invalidate_conditions"]
    assert hit[vr.COND_CLOSE_BELOW_STOP_LINE] is True


def test_ma20_and_limit_down_anchors_are_frozen_too(isolated_env):
    """MA20 与跌停价同理:盘中根本算不出"今日 MA20",卡里冻的是多少就按多少判。

    这里刻意把 MA20(9.8)放在止损线(9.5)**之上**,好让复合条件成为唯一起作用的
    那一条 —— 判定翻转就必然发生在**卡里冻的那个 MA20** 上,而不是别的锚。
    """
    d0 = date(2026, 7, 24)
    mechs = [_mech("600000.SH", ma20=9.8, limit_down=9.0)]
    v = bc.build_verification_spec("bk", d0, mechs)
    iv = bc.build_invalidation_spec("bk", d0, mechs, stop_pct=0.05)
    assert v["members"][0][vr.COND_HOLDS_MA20] == 9.8
    assert iv["members"][0][vr.COND_BELOW_REF_AND_MA20] == {"ref_close": 10.0, "ma20": 9.8}
    assert iv["members"][0][vr.COND_LIMIT_DOWN_TOUCH] == 9.0
    # 9.9:低于 D0 收盘但守住冻结 MA20 → 中间地带,不判证伪
    assert bv.evaluate_specs(v, iv, {"600000.SH": _obs("600000.SH", 9.9)}).state == vr.STATE_UNCLEAR
    # 9.7:两条同时成立 → 证伪(且仍在止损线 9.5 之上,证明是 MA20 那条在起作用)
    assert bv.evaluate_specs(v, iv, {"600000.SH": _obs("600000.SH", 9.7)}).state == vr.STATE_FALSIFIED


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ③:状态未变不落行、变了才落;EOD 必落一行
# ══════════════════════════════════════════════════════════════════════════

def test_unchanged_state_writes_no_new_row_and_eod_always_writes(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])

    class _Q:
        price, low, volume, amount, source = 10.4, 10.2, 1.0, 1.0, "sina"

    for _ in range(3):
        bv.run_intraday_verification(d1, {"600000.SH": _Q()}, attempted_codes=["600000.SH"],
                                     db_path=isolated_env.db_path)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert len(rows) == 1 and rows[0].state == vr.STATE_VERIFIED   # 三拍同态 → 只落一行

    class _Q2:      # 状态变了(跌回中间地带)→ 追加一行
        price, low, volume, amount, source = 9.8, 9.7, 1.0, 1.0, "sina"

    bv.run_intraday_verification(d1, {"600000.SH": _Q2()}, attempted_codes=["600000.SH"],
                                 db_path=isolated_env.db_path)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert [r.state for r in rows] == [vr.STATE_VERIFIED, vr.STATE_UNCLEAR]

    # EOD:即便与最后一拍同态,也必须落一行(当日定论记录)
    write_daily_fixture(isolated_env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 10.0, "high": 10.0, "low": 9.7, "close": 9.8,
         "pre_close": 10.0, "vol": 1.0, "amount": 1.0},
    ])
    bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert len(rows) == 3 and rows[-1].source == "eod" and rows[-1].state == vr.STATE_UNCLEAR


def test_basket_not_in_watch_pool_gets_no_row_at_all(isolated_env):
    """一个成员都没被拉过价(如 T3 篮不进盘中池)→ **一行都不落**,当前状态读法要能
    落到 `not_evaluated`(「还没判」≠「判了是 unclear」)。"""
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])
    res = bv.run_intraday_verification(d1, {}, attempted_codes=["600999.SH"],
                                       db_path=isolated_env.db_path)
    assert res.skipped_not_observed == 1 and res.evaluated == 0
    assert bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path) == []
    assert bvs.current_state(basket_id, d1, db_path=isolated_env.db_path).not_evaluated is True


def test_attempted_but_no_quote_is_evaluated_as_unclear_with_missing_flag(isolated_env):
    """在池里但没拉到行情 → 照判、落行,标 `member_data_missing`(那是"看了没拿到")。"""
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])
    res = bv.run_intraday_verification(d1, {}, attempted_codes=["600000.SH"],
                                       db_path=isolated_env.db_path)
    assert res.evaluated == 1
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert len(rows) == 1 and rows[0].state == vr.STATE_UNCLEAR
    assert rows[0].evidence[bv.FLAG_MEMBER_DATA_MISSING] == ["600000.SH"]


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ④:「当前状态」三路读法
# ══════════════════════════════════════════════════════════════════════════

def test_current_state_three_ways(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])

    # (1) 都没有 → unclear + not_evaluated
    cur = bvs.current_state(basket_id, d1, db_path=isolated_env.db_path)
    assert cur.state == vr.STATE_UNCLEAR and cur.not_evaluated and not cur.provisional
    assert cur.label == "今日尚未判定"

    # (2) 只有 intraday → 盘中暂态
    class _Q:
        price, low, volume, amount, source = 10.4, 10.2, 1.0, 1.0, "sina"

    bv.run_intraday_verification(d1, {"600000.SH": _Q()}, attempted_codes=["600000.SH"],
                                 db_path=isolated_env.db_path)
    cur = bvs.current_state(basket_id, d1, db_path=isolated_env.db_path)
    assert cur.state == vr.STATE_VERIFIED and cur.provisional and not cur.not_evaluated
    assert cur.source == "intraday" and "盘中暂态" in cur.label

    # (3) 有 EOD → 取 EOD(且不再是暂态)
    write_daily_fixture(isolated_env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 10.0, "high": 10.0, "low": 9.6, "close": 9.7,
         "pre_close": 10.0, "vol": 1.0, "amount": 1.0},
    ])
    bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    cur = bvs.current_state(basket_id, d1, db_path=isolated_env.db_path)
    assert cur.source == "eod" and not cur.provisional and cur.state == vr.STATE_UNCLEAR


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ⑤:falsified 当日终态不撤回;verified 可翻
# ══════════════════════════════════════════════════════════════════════════

def test_falsified_is_latched_for_the_day(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])

    class _Bad:
        price, low, volume, amount, source = 9.4, 9.4, 1.0, 1.0, "sina"

    class _Good:
        price, low, volume, amount, source = 10.8, 9.4, 1.0, 1.0, "sina"

    bv.run_intraday_verification(d1, {"600000.SH": _Bad()}, attempted_codes=["600000.SH"],
                                 db_path=isolated_env.db_path)
    res = bv.run_intraday_verification(d1, {"600000.SH": _Good()}, attempted_codes=["600000.SH"],
                                       db_path=isolated_env.db_path)
    assert res.skipped_latched == 1
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert [r.state for r in rows] == [vr.STATE_FALSIFIED]      # 没有第二行,也没被改写

    # EOD 那一行照落,内容仍是 falsified;本次重算结果如实留在 latched_over 里
    write_daily_fixture(isolated_env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 10.0, "high": 11.0, "low": 10.5, "close": 10.9,
         "pre_close": 10.0, "vol": 1.0, "amount": 1.0},
    ])
    bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert [r.state for r in rows] == [vr.STATE_FALSIFIED, vr.STATE_FALSIFIED]
    assert rows[-1].source == "eod" and rows[-1].evidence["latched"] is True
    assert rows[-1].evidence["latched_over"]["verify_hits"] == 1
    assert bvs.current_state(basket_id, d1, db_path=isolated_env.db_path).state == vr.STATE_FALSIFIED


def test_verified_is_not_terminal_and_can_flip(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])

    class _Good:
        price, low, volume, amount, source = 10.8, 10.5, 1.0, 1.0, "sina"

    class _Bad:
        price, low, volume, amount, source = 9.4, 9.4, 1.0, 1.0, "sina"

    bv.run_intraday_verification(d1, {"600000.SH": _Good()}, attempted_codes=["600000.SH"],
                                 db_path=isolated_env.db_path)
    bv.run_intraday_verification(d1, {"600000.SH": _Bad()}, attempted_codes=["600000.SH"],
                                 db_path=isolated_env.db_path)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    assert [r.state for r in rows] == [vr.STATE_VERIFIED, vr.STATE_FALSIFIED]


# ══════════════════════════════════════════════════════════════════════════
# ⑧-C2 ⑥:无卡不判(unclear + no_card,⛔ 不拿默认条件顶上)
# ══════════════════════════════════════════════════════════════════════════

def test_basket_without_card_is_unclear_with_no_card_reason(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"], with_card=False)

    class _Bad:      # 就算价格惨不忍睹,也不许判 falsified —— 没有 spec 就没有判据
        price, low, volume, amount, source = 1.0, 1.0, 1.0, 1.0, "sina"

    res = bv.run_intraday_verification(d1, {"600000.SH": _Bad()}, attempted_codes=["600000.SH"],
                                       db_path=isolated_env.db_path)
    assert res.states[basket_id] == vr.STATE_UNCLEAR
    row = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)[0]
    assert row.evidence["reason"] == bv.REASON_NO_CARD
    assert "members" not in row.evidence      # 没判过任何成员,不伪造证据


def test_evaluate_card_none_is_no_card():
    out = bv.evaluate_card(None, {})
    assert out.state == vr.STATE_UNCLEAR and out.reason == bv.REASON_NO_CARD


# ══════════════════════════════════════════════════════════════════════════
# append-only 守门:只 INSERT,没有 UPDATE / DELETE 路径
# ══════════════════════════════════════════════════════════════════════════

def test_store_has_no_update_or_delete_statement():
    src = (_REPO / "neckline" / "sentinel" / "basket_verify_store.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    sqls = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    for sql in sqls:
        low = sql.lower()
        assert "update basket_verification" not in low
        assert "delete from basket_verification" not in low
        assert "drop table" not in low


def test_rows_only_grow_never_replaced(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"])
    prices = [10.8, 9.8, 10.8, 9.8]
    for p in prices:
        q = type("Q", (), {"price": p, "low": p, "volume": 1.0, "amount": 1.0, "source": "sina"})()
        bv.run_intraday_verification(d1, {"600000.SH": q}, attempted_codes=["600000.SH"],
                                     db_path=isolated_env.db_path)
    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    # 四拍两两交替 → 四行状态流水全留着(「曾经 verified 后来 unclear」本身是审计对象)
    assert [r.state for r in rows] == [vr.STATE_VERIFIED, vr.STATE_UNCLEAR,
                                       vr.STATE_VERIFIED, vr.STATE_UNCLEAR]
    assert [r.id for r in rows] == sorted(r.id for r in rows)


# ══════════════════════════════════════════════════════════════════════════
# 语义红线:证伪不接任何持仓动作 / 不推送
# ══════════════════════════════════════════════════════════════════════════

# 禁入清单(判定线审计 🟡-5,2026-08-03 修):原清单里的 `neckline.push.notify`
# **不存在** —— 真实布局是 APNs 层 `neckline/push/apns.py` + 措辞/扇出层
# `neckline/api/notify.py`,守门扫的是**空靶**:谁往 `basket_verify.py` 里加一句
# `from neckline.api import notify` 把 falsified 接进推送,这条单测都不会挂。
# 现清单按真实模块名逐个列全,并由下面的反向存在性断言钉住(防再次锁空靶)。
_PUSH_AND_POSITION_BANNED = (
    "neckline.sentinel.channels",     # 推送通道(Bark/APNs 发送口)
    "neckline.api.notify",            # 推送措辞与扇出层
    "neckline.push.apns",             # APNs 直发层
    "neckline.notify_kinds",          # kind 白名单(basket_falsified 无 kind 是红线的一半)
    "neckline.sentinel.positions",    # 持仓台账读写
    "neckline.sentinel.holding",      # 持仓纪律判定
    "neckline.positions_entry",       # 开平仓编排(会写 positions/entry_snapshots/plans)
)


def test_banned_modules_actually_exist():
    """反向存在性断言:禁入清单里的模块名**必须真实存在**。守门的意义是防未来,
    锁一个不存在的模块名等于什么都没锁(🟡-5 的病根),故先证明靶子是真的。"""
    import importlib.util

    missing = [m for m in _PUSH_AND_POSITION_BANNED if importlib.util.find_spec(m) is None]
    assert not missing, f"禁入清单锁了不存在的模块(空靶):{missing}"


def test_verification_never_touches_positions_or_push_channels():
    """⑦-b / ⑧-C2 红线:篮子 `falsified` **不接任何持仓动作、不进推送**。"""
    for name in ("basket_verify.py", "basket_verify_store.py"):
        src = (_REPO / "neckline" / "sentinel" / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                # `from neckline.api import notify` 的 module 是 `neckline.api`,
                # 被 import 的名字才是 `notify` —— 只看 module 会漏掉这种写法。
                imported.update(f"{node.module}.{a.name}" for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for banned in _PUSH_AND_POSITION_BANNED:
            assert banned not in imported, f"{name} 不该 import {banned}"
