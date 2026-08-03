"""`GET /report/{date}/info-card/{code}` 端点单测(plan §五 v1.4-④-B)。

域计算逻辑(K 线/RS 线/行业分歧线/快照/消息面/龙虎榜/市场语境的具体正确性)已在
`tests/test_info_card.py` 逐项覆盖;本文件只测**端点层**职责——404 两个 reason、
`code` 归一化比对、`k4_flags`/`name` 原样从存档传给 `build_info_card`(不重算)、
响应体到 `InfoCardOut` 的往返不丢字段。用 monkeypatch 把 `build_info_card` 换成
可控替身,隔离端点逻辑与信息卡内部计算逻辑。
"""

from __future__ import annotations

from datetime import date

import pytest

import neckline.report.info_card as info_card_mod
from neckline.report import store as report_store


def _candidate(ts_code: str, name: str, *, k4_flags=None) -> dict:
    return {
        "ts_code": ts_code, "name": name, "close": 10.0, "score": 88.0, "rank": 1,
        "board": "MAIN", "pattern_tags": [], "hot_sectors": [], "sector_names": [],
        "entry_plan": "", "stop_loss": "", "target": "", "invalidation_text": "",
        "invalidation_spec": {}, "entry_spec": {},
        "k4_flags": k4_flags or [],
    }


def _seed_report(db, d: date, *, k4_flags=None):
    report_store.save_report(
        d, strategy_version="v1.4.0",
        sentiment={"trade_date": d.strftime("%Y%m%d")}, sectors=[],
        candidates=[_candidate("600001.SH", "示例甲", k4_flags=k4_flags)],
        markdown="# 报告", db_path=db,
    )


def _fake_card() -> info_card_mod.InfoCard:
    return info_card_mod.InfoCard(
        code="600001.SH", name="示例甲", trade_date="20260717",
        kline_available=True,
        kline=[info_card_mod.InfoCardKlineBar(
            trade_date="20260717", open=10.0, high=10.5, low=9.8, close=10.25, vol=100000.0,
            ma20=10.1, ma250=None,
        )],
        rs_available=True,
        rs_line=[info_card_mod.InfoCardIndexPoint(trade_date="20260717", value=102.5)],
        industry_divergence_available=False,
        industry="小众行业",
        industry_divergence_unavailable_reason="行业样本不足(小众行业当日成员数不足,分歧线缺省)",
        snapshot=info_card_mod.InfoCardSnapshot(vol_ratio5=1.1, turnover_rate=5.0),
        k4_flags=[info_card_mod.InfoCardK4Flag(
            code="A1_turnover_gt_10", label="换手率 >10%(过热放量,接盘区)", level="strong",
            section="hard_cut", evidence_strength="price_volume", evidence="换手>10%次日跌停3.37%",
        )],
        mild_band=True,
        news=info_card_mod.InfoCardNews(scanned=False, unavailable_reason="候选不在消息面扫描域(仅持仓+自选)"),
        top_list=info_card_mod.InfoCardTopList(on_list_today=False, lookback_days_covered=3),
        market=info_card_mod.InfoCardMarket(limit_up_count=42, limit_down_count=3, above_ma20=True),
    )


def test_info_card_bad_date_404(client, AUTH):
    r = client.get("/api/v1/report/abc/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "report_not_found"


def test_info_card_no_report_that_day_404(client, AUTH):
    r = client.get("/api/v1/report/20200101/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "report_not_found"


def test_info_card_code_not_in_report_404(client, AUTH, api_env):
    _seed_report(api_env.db_path, date(2026, 7, 17))
    r = client.get("/api/v1/report/20260717/info-card/999999.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "code_not_in_report"


def test_info_card_happy_path_shapes_full_payload(client, AUTH, api_env, monkeypatch):
    _seed_report(api_env.db_path, date(2026, 7, 17), k4_flags=["A1_turnover_gt_10"])
    captured = {}

    def _fake_build_info_card(trade_date, code, *, k4_flags, name=None, **kwargs):
        captured["trade_date"] = trade_date
        captured["code"] = code
        captured["k4_flags"] = k4_flags
        captured["name"] = name
        return _fake_card()

    monkeypatch.setattr(info_card_mod, "build_info_card", _fake_build_info_card)

    r = client.get("/api/v1/report/20260717/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 200
    body = r.json()

    # 端点把当日存档里的 k4_flags/name 原样传给 build_info_card(不重算)。
    assert captured["trade_date"] == date(2026, 7, 17)
    assert captured["code"] == "600001.SH"
    assert captured["k4_flags"] == ["A1_turnover_gt_10"]
    assert captured["name"] == "示例甲"

    # 响应体逐路到位(往返不丢字段)。
    assert body["code"] == "600001.SH" and body["name"] == "示例甲"
    assert body["klineAvailable"] is True
    assert body["kline"][0]["close"] == pytest.approx(10.25)
    assert body["rsAvailable"] is True
    assert body["rsBenchmark"] == "000001.SH"
    assert body["rsLine"][0]["value"] == pytest.approx(102.5)
    assert body["industryDivergenceAvailable"] is False
    assert "样本不足" in body["industryDivergenceUnavailableReason"]
    assert body["industryDivergenceNote"] == "行业线=行业成员中位数合成,非申万官方指数"
    assert body["snapshot"]["volRatio5"] == pytest.approx(1.1)
    assert body["k4Flags"][0]["code"] == "A1_turnover_gt_10"
    assert body["k4Flags"][0]["section"] == "hard_cut"
    assert body["mildBand"] is True
    assert body["news"]["scanned"] is False
    assert body["news"]["unavailableReason"] == "候选不在消息面扫描域(仅持仓+自选)"
    assert body["topList"]["lookbackDaysCovered"] == 3
    assert body["market"]["limitUpCount"] == 42
    assert body["market"]["aboveMa20"] is True


def test_info_card_bare_code_normalizes_to_ts_code(client, AUTH, api_env, monkeypatch):
    """`code` 路径参数支持裸 6 位(不带交易所后缀),经 `normalize_ts_code` 归一后
    与存档里的 `ts_code` 比对——同 `positions`/`decision_log` 写入通道的归一惯例。"""
    _seed_report(api_env.db_path, date(2026, 7, 17))
    monkeypatch.setattr(info_card_mod, "build_info_card", lambda *a, **kw: _fake_card())

    r = client.get("/api/v1/report/20260717/info-card/600001", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["code"] == "600001.SH"


def test_info_card_requires_auth(client):
    r = client.get("/api/v1/report/20260717/info-card/600001.SH")
    assert r.status_code == 401


# ======================================================================
#  V2-⑬-N:信息卡保留改造 —— 数据来源由「候选快照」换成「篮子成员」
# ======================================================================

def _seed_basket_with_card(db, d: date, codes, *, tier: int = 1, key: str = "k1",
                           k4_tag_of=None, roles=None):
    """种一个 D0 篮子 + 用 `basket_card` **本尊**产出的卡(不手拼 JSON,键名一改就红)。"""
    from neckline.db import connection
    from neckline.selection import basket_card as bc
    from neckline.selection.basket_store import save_basket_card

    roles = roles or {}
    k4_tag_of = k4_tag_of or {}
    with connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.strftime("%Y%m%d"), key, "机器人执行器", "人形机器人量产提速", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for i, c in enumerate(codes):
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, roles.get(c, ("core", "core"))[0], roles.get(c, ("core", "core"))[1],
                 1 if roles.get(c, ("core", "core"))[0] != roles.get(c, ("core", "core"))[1] else 0,
                 f"{c} 的入篮理由", 1 if i == 0 else 0, "2026-08-02T00:00:00+08:00"),
            )
    mechs = [bc.MemberMech(ts_code=c, name=f"名{c[:6]}", close=10.0 + i, ma20=9.5,
                           limit_up=11.0, limit_down=9.0, stop_price=9.5)
             for i, c in enumerate(codes)]
    members = [
        bc.MemberCardEntry(
            ts_code=c, name=f"名{c[:6]}",
            role_llm=roles.get(c, ("core", "core"))[0], role_mech=roles.get(c, ("core", "core"))[1],
            role_conflict=roles.get(c, ("core", "core"))[0] != roles.get(c, ("core", "core"))[1],
            reason=f"{c} 的入篮理由", is_primary=(i == 0),
            industry="通用设备", industry_lift=3.1, lift_reason="", primary_reason=None,
            rs_rank=10 + i, k4_tag=k4_tag_of.get(c), mech=m,
        )
        for i, (c, m) in enumerate(zip(codes, mechs))
    ]
    card = bc.BasketCard(
        version=1, basket_key=key, trade_date=d.strftime("%Y%m%d"), next_trade_date=None,
        name="机器人执行器", driver="人形机器人量产提速", driver_kind="theme",
        evidence=(), evidence_status="ok", why_now="订单落地时点靠近",
        members=tuple(members), tier=tier, rank_in_tier=1, rank_mech=1, mech_score=80.0,
        tier_breakdown={}, tier_reason="", tier_note="", scripts={},
        verification_spec=bc.build_verification_spec(key, d, mechs),
        invalidation_spec=bc.build_invalidation_spec(key, d, mechs, stop_pct=0.05),
    ).to_card_json()
    save_basket_card(bid, card, db_path=db)
    return bid


def test_info_card_reads_basket_member_not_candidate_snapshot(client, AUTH, api_env, monkeypatch):
    """⑬-N:候选榜已删 → 端点改在 **D0 篮子成员**里找这只票;`k4_flags` 取卡里冻结的
    `members[].k4_tag`,**不重算**。"""
    d = date(2026, 7, 17)
    report_store.save_report(d, strategy_version="v2.0.0", sentiment={}, sectors=[],
                             candidates=[], markdown="# 报告", db_path=api_env.db_path)
    _seed_basket_with_card(api_env.db_path, d, ["600001.SH", "600002.SH"],
                           k4_tag_of={"600001.SH": "avoid_flag"})

    captured = {}

    def fake_build(trade_date, code, **kw):
        captured["k4_flags"] = list(kw.get("k4_flags") or [])
        captured["code"] = code
        return _fake_card()

    monkeypatch.setattr(info_card_mod, "build_info_card", fake_build)
    r = client.get(f"/api/v1/report/{d.strftime('%Y%m%d')}/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 200
    assert captured["code"] == "600001.SH"
    assert captured["k4_flags"] == ["avoid_flag"], "K4 标须取卡里冻结的那一份,不重算"


def test_info_card_404_when_code_neither_in_basket_nor_in_historical_snapshot(client, AUTH, api_env):
    """既不在任何篮子里、历史快照里也没有 → 404 `code_not_in_report`
    (**复用既有 reason 字符串**,客户端 `mapReason` 已有 case,不需要新 case)。"""
    d = date(2026, 7, 17)
    report_store.save_report(d, strategy_version="v2.0.0", sentiment={}, sectors=[],
                             candidates=[], markdown="# 报告", db_path=api_env.db_path)
    _seed_basket_with_card(api_env.db_path, d, ["600001.SH"])
    r = client.get(f"/api/v1/report/{d.strftime('%Y%m%d')}/info-card/600009.SH", headers=AUTH)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "code_not_in_report"


def test_info_card_still_serves_pre_v2_historical_report_candidates(client, AUTH, api_env, monkeypatch):
    """⑬-1 **之前**生成的历史报告(`candidates_json` 里还有真候选)仍能看信息卡 ——
    这是「读历史」的合法路径,不是把候选榜接回来(那天根本没有篮子)。"""
    d = date(2026, 7, 17)
    _seed_report(api_env.db_path, d, k4_flags=["A3_belowyear_limitup"])
    captured = {}

    def fake_build(trade_date, code, **kw):
        captured["k4_flags"] = list(kw.get("k4_flags") or [])
        captured["name"] = kw.get("name")
        return _fake_card()

    monkeypatch.setattr(info_card_mod, "build_info_card", fake_build)
    r = client.get(f"/api/v1/report/{d.strftime('%Y%m%d')}/info-card/600001.SH", headers=AUTH)
    assert r.status_code == 200
    assert captured["k4_flags"] == ["A3_belowyear_limitup"]
    assert captured["name"] == "示例甲"


def test_info_card_basket_block_carries_driver_role_and_peers(api_env):
    """⑬-N 三块:①所属篮子与共同驱动 ②本票角色(含对拍分歧)③与同篮其他成员的对比。"""
    from neckline.report.info_card import build_basket_context

    d = date(2026, 7, 17)
    _seed_basket_with_card(api_env.db_path, d, ["600001.SH", "600002.SH", "600003.SH"],
                           roles={"600001.SH": ("core", "follower")})
    ctx = build_basket_context("600001.SH", d, db_path=api_env.db_path)
    assert ctx.available is True
    assert ctx.driver == "人形机器人量产提速" and ctx.name == "机器人执行器"
    assert ctx.tier == 1 and ctx.is_primary is True
    # ② 对拍分歧如实标(LLM 说 core、机械说 follower)
    assert ctx.role_llm == "core" and ctx.role_mech == "follower" and ctx.role_conflict is True
    assert "入篮理由" in ctx.role_reason
    # ③ 同篮其他成员(不含自己),数值取自卡里冻结的成员节
    assert {p.ts_code for p in ctx.peers} == {"600002.SH", "600003.SH"}
    assert all(p.close is not None and p.rs_rank is not None for p in ctx.peers)


def test_info_card_basket_two_unavailable_reasons_are_distinct(api_env):
    """「不在任何篮子里」与「在篮子里但卡没生成」**必须分得开**(⑦ 的
    `basket_not_found` vs `card_not_ready` 同一条纪律)。"""
    from neckline.db import connection
    from neckline.report.info_card import (
        BASKET_CARD_NOT_READY_REASON, BASKET_NOT_A_MEMBER_REASON, build_basket_context,
    )

    d = date(2026, 7, 17)
    # ① 无任何篮子 → 不在篮子里
    a = build_basket_context("600001.SH", d, db_path=api_env.db_path)
    assert a.available is False and a.unavailable_reason == BASKET_NOT_A_MEMBER_REASON
    # ② 有篮子、无卡 → 卡未生成(⛔ 不拿 basket_members 裸行顶上冒充冻结件)
    with connection(api_env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (d.strftime("%Y%m%d"), "k9", "无卡篮", "驱动", "theme", 1,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        conn.execute(
            "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
            " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (int(cur.lastrowid), "600001.SH", "core", None, 0, "r", 1, "2026-08-02T00:00:00+08:00"),
        )
    b = build_basket_context("600001.SH", d, db_path=api_env.db_path)
    assert b.available is False and b.unavailable_reason == BASKET_CARD_NOT_READY_REASON
    assert BASKET_NOT_A_MEMBER_REASON != BASKET_CARD_NOT_READY_REASON


def test_info_card_tags_are_bit_for_bit_identical_to_the_basket_card_tags(api_env, monkeypatch):
    """**⑬-N-K7 交叉断言(plan 点名)**:同一票同一天,信息卡与篮子卡的标签集合**逐位
    相同** —— 因为两侧读的是 `member_tags.tags_for_members` 同一个入口、同一份文案模板。
    ⛔ 信息卡侧禁重写判据,这条测试就是防线。"""
    from neckline.report.info_card import build_member_tags
    from neckline.selection import basket_card as bc
    from neckline.selection import member_tags as mt

    d = date(2026, 7, 17)
    code = "600001.SH"
    row = {
        "ts_code": code, "limitup_count_20d": 3, "ret_20d": 0.4,
        "dist_from_high_20d": -0.15, "ret_1d": -0.01, "consec_limit_up_days": 1,
    }
    monkeypatch.setattr(mt, "load_tag_panel_rows", lambda codes, td, **kw: {code: row})
    monkeypatch.setattr(mt, "streak_top_flags", lambda codes, td, **kw: {code: False})

    # 篮子卡侧(⑦ 的真实装配路径)
    batch = mt.tags_for_members([code], d, db_path=api_env.db_path)
    card_res = batch.get(code)
    card_entry = bc.MemberCardEntry(
        ts_code=code, name="示例甲", role_llm="core", role_mech="core", role_conflict=False,
        reason="r", is_primary=True, industry="通用设备", industry_lift=3.1,
        lift_reason="", primary_reason=None, rs_rank=1, k4_tag=None,
        mech=bc.MemberMech(ts_code=code, name="示例甲", close=10.0, ma20=9.5,
                           limit_up=11.0, limit_down=9.0, stop_price=9.5),
        tags=card_res.tags, tags_absent=card_res.absent,
    ).to_dict()

    # 信息卡侧(⑬-N 的路径)
    info_tags, info_absent = build_member_tags(code, d, db_path=api_env.db_path)

    assert [t["code"] for t in card_entry["tags"]] == [t.code for t in info_tags]
    assert [t["label"] for t in card_entry["tags"]] == [t.label for t in info_tags]
    assert [t["tone"] for t in card_entry["tags"]] == [t.tone for t in info_tags]
    assert [t["text"] for t in card_entry["tags"]] == [t.text for t in info_tags]
    assert list(card_entry["tags_absent"]) == list(info_absent)
    # 「参考、非指令」后缀不许被信息卡侧截断
    for t in info_tags:
        assert t.text.endswith(mt.REFERENCE_ONLY_SUFFIX)
