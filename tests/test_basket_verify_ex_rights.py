"""⑧-E 除权除息锚失效检测器(plan §五 V2-⑧-E,2026-08-02 planner 裁定;⑧ 完工记录
登记⑤的口径局限之补丁)。

背景:卡里的 D0 锚(`ref_close`)是前复权口径,观测侧(盘中 `Quote.pre_close`/`price`,
EOD `daily.pre_close`/`close`)是原始价 —— 两条路径本来互相一致,但某成员**恰在
D+1 除权除息**时,原始价按除权比例跳水而锚还停在 D0 标度,会被误判破位,且
`falsified` 当日终态不撤回、`min_members_hit=ceil(n/2)` 使小篮子(尤其 2 只成员)
单只误判就能拖累整篮。

覆盖 plan §五 V2-⑧-E 验收逐条:
    1. D+1 `pre_close ≠ 卡里 D0 收盘` 的成员 → 两侧都不计 + 原因码精确
    2. 2 只成员篮里一只锚失效、另一只落在⑦-b「中间地带」(非失效命中)→ 整篮
       `unclear` 而非 `falsified`(**回归场景**:若不排除锚失效成员,它单独就会
       被误判触发复合失效条件,足以把 `min_members_hit=1` 的 2 只成员篮打成
       `falsified` —— 用同一只成员的孤立单成员篮验证这个反事实)
    2b. 反向验证:锚失效成员被排除后,不拖累其余成员的正常判定(`verified` 照样
        成立,不因为一个数据故障就多罚一次)
    3. `_EPS` 容差生效:浮点毛刺(≪ EPS)不误触发,真实最小价差(1 分钱)必触发
    4. 盘中与 EOD 用同一个检测器:同一份数据(含 `pre_close`)两条路径判定逐位相同
    5. EOD 交叉确认两路:`adj_factor` 变了 → `member_ex_rights`(不报警);
       没变 → `anchor_mismatch`(报警);两天任一缺 `adj_factor` 行 → `anchor_
       unconfirmed`(不猜、不报警)
    6. 守门:⑦ 的卡形状(`CARD_SPEC_VERSION`/两个 spec 的 `spec_version`)一字未动
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytest

from tests.conftest import business_days, insert_trade_cal, write_daily_fixture

from neckline.db import connection
from neckline.selection import basket_card as bc
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import save_basket_card
from neckline.sentinel import basket_verify as bv
from neckline.sentinel import basket_verify_store as bvs

pytestmark = pytest.mark.usefixtures("isolated_env")


# ══════════════════════════════════════════════════════════════════════════
# 夹具(与 test_sentinel_basket_verify.py 同款,独立复制一份保持本文件自包含)
# ══════════════════════════════════════════════════════════════════════════

def _mech(code: str, *, close=10.0, ma20=9.2, limit_down=9.0, stop_price=9.5) -> bc.MemberMech:
    return bc.MemberMech(ts_code=code, name=code, close=close, ma20=ma20,
                         limit_up=11.0, limit_down=limit_down, stop_price=stop_price)


def _card_json(d0: date, mechs, *, stop_pct=0.05) -> dict:
    return {
        "spec_version": bc.CARD_SPEC_VERSION,
        "verification_spec": bc.build_verification_spec("bk", d0, mechs),
        "invalidation_spec": bc.build_invalidation_spec("bk", d0, mechs, stop_pct=stop_pct),
        "fingerprint": {"stop_pct": stop_pct,
                        "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
    }


def _seed_basket(env, d0: date, codes, *, mechs=None, stop_pct=0.05) -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d0.strftime("%Y%m%d"), "k1", "测试篮", "某共同驱动", "theme", 1,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        basket_id = int(cur.lastrowid)
        for code in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, code, "core", None, 0, "理由", 1, "2026-08-02T00:00:00+08:00"),
            )
    ms = mechs if mechs is not None else [_mech(c) for c in codes]
    save_basket_card(basket_id, _card_json(d0, ms, stop_pct=stop_pct),
                     stop_pct=stop_pct, db_path=env.db_path)
    return basket_id


def _verdict(mechs, obs, stop_pct=0.05) -> bv.BasketVerdict:
    d0 = date(2026, 7, 24)
    v = bc.build_verification_spec("bk", d0, mechs)
    iv = bc.build_invalidation_spec("bk", d0, mechs, stop_pct=stop_pct)
    return bv.evaluate_specs(v, iv, obs)


# ══════════════════════════════════════════════════════════════════════════
# 1. 单成员锚失效 → 两侧都不计 + 原因码精确
# ══════════════════════════════════════════════════════════════════════════

def test_anchor_mismatch_excludes_member_from_both_sides():
    """D0 收盘(卡里 `ref_close`)= 10.0;D+1 除权,`pre_close` 跳到 5.0(腰斩),
    观测价 5.05(经济上其实持平偏涨)。若不识别锚失效,拿 5.05 直接跟"10.0 那把
    尺"比,止损线 / 复合条件 / 跌停触及会**全部**误判成立(见下方
    `test_one_anchor_mismatch_does_not_falsify_two_member_basket` 的反证)。"""
    mechs = [_mech("600000.SH", close=10.0, ma20=9.5, limit_down=9.0, stop_price=9.5)]
    obs = {"600000.SH": bv.MemberObservation(
        ts_code="600000.SH", price=5.05, low=5.0, pre_close=5.0)}
    out = _verdict(mechs, obs)
    assert out.state == vr.STATE_UNCLEAR        # 唯一成员被排除 → 没人命中任何一侧
    assert out.verify_hits == 0 and out.invalidate_hits == 0
    assert out.observed_members == 0            # 排除同「missing」机制:不计入 observed
    assert out.evidence[bv.FLAG_ANCHOR_MISMATCH] == ["600000.SH"]
    assert bv.FLAG_MEMBER_DATA_MISSING not in out.evidence   # ⛔ 两种原因不许混

    row = out.evidence["members"][0]
    assert row["verify"] is None and row["invalidate"] is None
    assert row["flags"] == [bv.FLAG_ANCHOR_MISMATCH]
    assert row["pre_close"] == 5.0 and row["ref_close"] == 10.0
    assert row["price"] == 5.05                  # 原始观测价如实留档,供审计


# ══════════════════════════════════════════════════════════════════════════
# 2. 回归场景:一只锚失效 + 一只灰色地带 → 整篮 unclear(不是 falsified)
# ══════════════════════════════════════════════════════════════════════════

def test_one_anchor_mismatch_does_not_falsify_two_member_basket():
    """2 只成员篮(`min_members_hit = ceil(2/2) = 1`)。成员 A 除权,原始价腰斩;
    成员 B 锚正常,价格落在⑦-b 定案的"中间地带"(跌破 D0 收盘但守住 MA20,复合
    失效条件的两个子项只成立一个)—— 按⑦-b 定案本就不该判失效,`v_hit=i_hit=
    False`。**新逻辑排除 A → 整篮 unclear**;这正是 planner 点名的回归场景:若不
    排除 A,它会被误判触发复合失效条件,单独就够 `min_hit=1` 把整篮打成
    `falsified`(下方用孤立单成员篮验证这个反事实,证明"确有其事"而非假设)。
    """
    mech_a = _mech("A00001.SZ", close=10.0, ma20=9.5, limit_down=9.0, stop_price=9.5)
    mech_b = _mech("A00002.SZ", close=20.0, ma20=19.0, limit_down=18.0, stop_price=19.0)
    obs = {
        # A:除权腰斩。若不排除:5.05 < ref(10.0) 且 < ma20(9.5) → 复合失效命中;
        #   同时 <= stop_price(9.5)、low <= limit_down(9.0) → 三条同时误判成立。
        "A00001.SZ": bv.MemberObservation(ts_code="A00001.SZ", price=5.05, low=5.0, pre_close=5.0),
        # B:锚正常(pre_close == ref_close = 20.0)。19.5 跌破 ref(20.0) 但守住
        #   ma20(19.0)、未触碰止损线(19.0)/跌停(18.0)→ 复合条件两个子项只成立
        #   一个 → 不判失效,也够不上验证(⑦-b「中间地带」的本意)。
        "A00002.SZ": bv.MemberObservation(ts_code="A00002.SZ", price=19.5, low=19.2, pre_close=20.0),
    }
    out = _verdict([mech_a, mech_b], obs)
    assert out.min_members_hit == 1
    assert out.verify_hits == 0 and out.invalidate_hits == 0
    assert out.state == vr.STATE_UNCLEAR
    assert out.evidence[bv.FLAG_ANCHOR_MISMATCH] == ["A00001.SZ"]

    # 反证:成员 A 若不做锚检测(观测不带 pre_close),单独一只就够 min_hit=1
    # 把(哪怕只有它自己的)篮子打成 falsified —— 证明"不排除就会误判"确有其事,
    # 不是臆测的风险。
    naive_a_only = _verdict([mech_a], {
        "A00001.SZ": bv.MemberObservation(ts_code="A00001.SZ", price=5.05, low=5.0)
    })
    assert naive_a_only.state == vr.STATE_FALSIFIED


def test_one_anchor_mismatch_does_not_prevent_other_members_from_verifying():
    """反向验证(「一个成员锚失效不翻整篮」的另一半):3 只成员篮
    (`min_members_hit = ceil(3/2) = 2`),1 只锚失效被排除,剩下 2 只都正常验证
    命中 → 篮子仍能判 `verified`,不因为一个成员数据故障就整体降级。"""
    mechs = [_mech(c, close=10.0, ma20=9.2) for c in ("B1", "B2", "B3")]
    obs = {
        "B1": bv.MemberObservation(ts_code="B1", price=5.0, low=5.0, pre_close=5.0),      # 锚失效,排除
        "B2": bv.MemberObservation(ts_code="B2", price=10.5, low=10.3, pre_close=10.0),   # 验证命中
        "B3": bv.MemberObservation(ts_code="B3", price=10.2, low=10.1, pre_close=10.0),   # 验证命中
    }
    out = _verdict(mechs, obs)
    assert out.min_members_hit == 2
    assert out.verify_hits == 2 and out.invalidate_hits == 0
    assert out.state == vr.STATE_VERIFIED
    assert out.evidence[bv.FLAG_ANCHOR_MISMATCH] == ["B1"]


# ══════════════════════════════════════════════════════════════════════════
# 3. `_EPS` 容差:浮点毛刺不触发,真实最小价差(1 分钱)必触发
# ══════════════════════════════════════════════════════════════════════════

def test_eps_tolerance_ignores_float_noise_but_catches_real_gap():
    mechs = [_mech("600000.SH", close=10.0, ma20=9.5)]

    # 浮点毛刺(1e-10,远小于 vr.EPS=1e-9)→ 不触发,照常判定
    noisy = _verdict(mechs, {"600000.SH": bv.MemberObservation(
        ts_code="600000.SH", price=10.5, low=10.3, pre_close=10.0 + 1e-10)})
    assert bv.FLAG_ANCHOR_MISMATCH not in noisy.evidence
    assert noisy.state == vr.STATE_VERIFIED

    # A 股最小报价单位(1 分钱 = 0.01)的价差 → 必触发
    real_gap = _verdict(mechs, {"600000.SH": bv.MemberObservation(
        ts_code="600000.SH", price=10.5, low=10.3, pre_close=9.99)})
    assert real_gap.evidence[bv.FLAG_ANCHOR_MISMATCH] == ["600000.SH"]
    assert real_gap.state == vr.STATE_UNCLEAR    # 唯一成员被排除


def test_anchor_check_is_a_noop_when_pre_close_absent():
    """`pre_close=None`(老调用点 / 测试替身不传)→ 不触发检测,不是"判定锚有效"。
    ⛔「没有」不是「不匹配」——落回原本的判定路径,行为与 ⑧-E 之前完全一致。"""
    mechs = [_mech("600000.SH", close=10.0, ma20=9.5)]
    out = _verdict(mechs, {"600000.SH": bv.MemberObservation(
        ts_code="600000.SH", price=10.5, low=10.3)})   # 无 pre_close
    assert bv.FLAG_ANCHOR_MISMATCH not in out.evidence
    assert out.state == vr.STATE_VERIFIED


# ══════════════════════════════════════════════════════════════════════════
# 4. 盘中与 EOD 用同一个检测器:同一份数据(含 pre_close)两条路径判定逐位相同
# ══════════════════════════════════════════════════════════════════════════

def test_intraday_and_eod_share_the_same_detector_bit_for_bit(isolated_env):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(isolated_env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(isolated_env, d0, ["600000.SH"],
                             mechs=[_mech("600000.SH", close=10.0, ma20=9.5)])

    class _Q:      # 盘中 Quote 替身,pre_close 跳水(模拟除权)
        price, low, pre_close, volume, amount, source = 5.05, 5.0, 5.0, 100.0, 1000.0, "sina"

    intra = bv.run_intraday_verification(
        d1, {"600000.SH": _Q()}, attempted_codes=["600000.SH"],
        now=datetime.combine(d1, datetime.min.time()).replace(hour=10),
        db_path=isolated_env.db_path)

    write_daily_fixture(isolated_env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 5.0, "high": 5.1, "low": 5.0, "close": 5.05,
         "pre_close": 5.0, "vol": 100.0, "amount": 1000.0},
    ])
    eod = bv.run_eod_verification(d1, db_path=isolated_env.db_path,
                                  parquet_dir=isolated_env.parquet_dir)

    assert intra.states[basket_id] == eod.states[basket_id] == vr.STATE_UNCLEAR

    rows = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)
    intra_row = [r for r in rows if r.source == "intraday"][0]
    eod_row = [r for r in rows if r.source == "eod"][0]
    for key in ("verify_hits", "invalidate_hits", "min_members_hit", bv.FLAG_ANCHOR_MISMATCH):
        assert intra_row.evidence[key] == eod_row.evidence[key], key

    # members[] 逐位相同(EOD 额外多出的 confirm/adj_factor_* 是 EOD 专属交叉确认
    # 标注,核心判定字段之外的东西,不必也不应在盘中出现)。
    intra_member = intra_row.evidence["members"][0]
    eod_member = eod_row.evidence["members"][0]
    for key in ("ts_code", "price", "low", "pre_close", "ref_close", "verify", "invalidate", "flags"):
        assert intra_member[key] == eod_member[key], key


# ══════════════════════════════════════════════════════════════════════════
# 5. EOD 交叉确认:adj_factor 变了 → member_ex_rights(不报警);
#    没变 → anchor_mismatch(报警);两天任一缺行 → anchor_unconfirmed(不猜)
# ══════════════════════════════════════════════════════════════════════════

def _seed_ex_rights_day(env, *, adj_d0: float | None, adj_d1: float | None):
    days = business_days(date(2026, 7, 20), 5)
    insert_trade_cal(env, days)
    d0, d1 = days[-2], days[-1]
    basket_id = _seed_basket(env, d0, ["600000.SH"],
                             mechs=[_mech("600000.SH", close=10.0, ma20=9.5)])
    write_daily_fixture(env, "daily", d1, [
        {"ts_code": "600000.SH", "open": 5.0, "high": 5.1, "low": 5.0, "close": 5.05,
         "pre_close": 5.0, "vol": 100.0, "amount": 1000.0},
    ])
    if adj_d0 is not None:
        write_daily_fixture(env, "adj_factor", d0, [{"ts_code": "600000.SH", "adj_factor": adj_d0}])
    if adj_d1 is not None:
        write_daily_fixture(env, "adj_factor", d1, [{"ts_code": "600000.SH", "adj_factor": adj_d1}])
    return basket_id, d1


def test_eod_cross_check_confirms_real_ex_rights_no_warning(isolated_env, caplog):
    basket_id, d1 = _seed_ex_rights_day(isolated_env, adj_d0=1.0, adj_d1=2.0)   # 变了 → 真除权

    with caplog.at_level(logging.WARNING, logger="neckline.sentinel.basket_verify"):
        bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)

    row = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)[0]
    member = row.evidence["members"][0]
    assert member["confirm"] == bv.REASON_MEMBER_EX_RIGHTS
    assert member["adj_factor_d0"] == 1.0 and member["adj_factor_d1"] == 2.0
    assert row.state == vr.STATE_UNCLEAR         # 正常降级:仍然两侧都不计,不误判失效
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_eod_cross_check_flags_real_fault_with_warning(isolated_env, caplog):
    basket_id, d1 = _seed_ex_rights_day(isolated_env, adj_d0=1.0, adj_d1=1.0)   # 没变 → 真故障

    with caplog.at_level(logging.WARNING, logger="neckline.sentinel.basket_verify"):
        bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)

    row = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)[0]
    member = row.evidence["members"][0]
    assert member["confirm"] == bv.REASON_ANCHOR_MISMATCH
    assert row.state == vr.STATE_UNCLEAR         # ⛔ 仍不算失效命中,只是报警级别不同
    assert any(r.levelname == "WARNING" and "600000.SH" in r.message for r in caplog.records)


def test_eod_cross_check_unconfirmed_when_adj_factor_missing(isolated_env, caplog):
    """两天任一缺 `adj_factor` 行(此处两天都缺)→ confirm 不了,`anchor_unconfirmed`,
    ⛔ 不猜、不报警。"""
    basket_id, d1 = _seed_ex_rights_day(isolated_env, adj_d0=None, adj_d1=None)

    with caplog.at_level(logging.WARNING, logger="neckline.sentinel.basket_verify"):
        bv.run_eod_verification(d1, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir)

    row = bvs.list_rows(basket_id, d1, db_path=isolated_env.db_path)[0]
    member = row.evidence["members"][0]
    assert member["confirm"] == bv.REASON_ANCHOR_UNCONFIRMED
    assert member["adj_factor_d0"] is None and member["adj_factor_d1"] is None
    assert not any(r.levelname == "WARNING" for r in caplog.records)


# ══════════════════════════════════════════════════════════════════════════
# 6. 守门:⑦ 的卡形状与 spec_version 一字未动
# ══════════════════════════════════════════════════════════════════════════

def test_card_shape_and_spec_versions_untouched_by_ex_rights_detector():
    # V2.2-③:卡形状 v2 → v3(新增引擎归属三键,纯增量;两个 **spec** 版本一字未动
    # —— 本条守的是 ⑧ 吃的结构化 spec,不是卡顶层)。
    assert bc.CARD_SPEC_VERSION == "basket_card_v3"
    assert bc.VERIFY_SPEC_VERSION == "basket_verify_v2"
    assert bc.INVALIDATE_SPEC_VERSION == "basket_invalidate_v2"
    mechs = [_mech("600000.SH")]
    v = bc.build_verification_spec("bk", date(2026, 7, 24), mechs)
    iv = bc.build_invalidation_spec("bk", date(2026, 7, 24), mechs, stop_pct=0.05)
    # ⑧-E 是纯粹的观测侧(哨兵)检测器,不往 spec 里加任何新键。
    assert set(v.keys()) == {
        "spec_version", "ruleset_version", "basket_key", "trade_date", "next_trade_date",
        "member_count", "evaluable_members", "min_members_hit", "require", "conditions", "members",
    }
    assert set(iv.keys()) == {
        "spec_version", "ruleset_version", "basket_key", "trade_date", "next_trade_date",
        "member_count", "evaluable_members", "min_members_hit", "any_of", "stop_pct",
        "conditions", "members",
    }
