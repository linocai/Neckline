"""V2-⑭-B 新端点契约(篮子 / 计划继承 / 建仓快照 / 画像 / 策略包 / 评价)。

**本文件最要紧的一条**:`GET /baskets/{id}/card` 的**两个 404 reason 必须分得开** ——
`basket_not_found`(系统丢了篮子)vs `card_not_ready`(篮子在、卡还没生成)。
合并成一个就把「没有」和「没看」混了;而 404 的客户端 fallback 是「持仓已清」,
新 reason 不加 `mapReason` case 就会显示成那句驴唇不对马嘴的话(v1.4 `watchlist`
的 `not_found` 有案底)。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.db import connection
from neckline.selection.basket_store import save_basket_card

D0 = date(2026, 7, 23)


def _card(codes) -> dict:
    return {
        "spec_version": "card-1", "version": 1, "basket_key": "k1",
        "trade_date": D0.strftime("%Y%m%d"), "name": "固态电池",
        "driver": "固态电池装车", "driver_kind": "theme", "evidence": [],
        "evidence_status": "ok", "why_now": "公告落地",
        "members": [{"ts_code": c, "name": "名", "role_llm": "leader", "role_mech": "leader",
                     "role_conflict": 0, "reason": "r", "is_primary": 1, "industry": "电池",
                     "industry_lift": 1.2, "lift_reason": "", "primary_reason": "",
                     "rs_rank": 1, "k4_tag": None, "mech": {},
                     "entry_zone": None, "entry_zone_clamp": "absent",
                     "entry_zone_unavailable_reason": "夹逼拒收",
                     "max_chase": None, "max_chase_clamp": "absent",
                     "max_chase_unavailable_reason": "夹逼拒收",
                     "exit_reference": None, "exit_reference_clamp": "absent",
                     "exit_reference_unavailable_reason": "夹逼拒收",
                     "tags": [], "tags_absent": []} for c in codes],
        "role_conflicts": [], "tier": 1, "rank_in_tier": 1, "rank_mech": 1, "mech_score": 0.7,
        "tier_breakdown": {"driver_freshness": 0.8}, "tier_reason": "驱动新鲜",
        "scripts": None, "scripts_unavailable_reason": "本次未生成竞价剧本(disabled)",
        "verification_spec": {"members_up_ratio": 0.5}, "verification_text": None,
        "invalidation_spec": {}, "invalidation_text": None, "risks": [],
        "disclaimer": "以上为参考、非指令。",
        "fingerprint": {"stop_pct": 0.05, "pack_version": "K4-pack-v1",
                        "verification_ruleset_version": "vr-1"},
        "discipline_labels": [], "narrative": "", "llm_stage": "disabled",
        "degraded": True, "notes": [],
    }


def _seed_basket(db, codes, *, tier=1, key="k1", with_card=True, with_tier_history=True) -> int:
    with connection(db) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), key, "固态电池", "固态电池装车", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for c in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, c, "leader", "leader", 0, "r", 1, "2026-08-02T00:00:00+08:00"),
            )
        if with_tier_history:
            conn.execute(
                "INSERT INTO tier_history (trade_date, basket_id, tier, mech_score,"
                " mech_breakdown_json, rank_in_tier, rank_mech, llm_rank_delta, llm_reason,"
                " pack_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (D0.strftime("%Y%m%d"), bid, tier, 0.7, '{"driver_freshness": 0.8}', 1, 1, 0,
                 None, "K4-pack-v1", "2026-08-02T00:00:00+08:00"),
            )
    if with_card:
        save_basket_card(bid, _card(codes), db_path=db)
    return bid


# ══════════════════════════════════════════════════════════════════════════
# GET /baskets
# ══════════════════════════════════════════════════════════════════════════

class TestListBaskets:
    def test_lists_by_date_with_card_and_tier_history(self, client, AUTH, api_env):
        _seed_basket(api_env.db_path, ["600001.SH"])
        body = client.get(f"/api/v1/baskets?date={D0:%Y%m%d}", headers=AUTH).json()
        assert body["tradeDate"] == D0.strftime("%Y%m%d") and len(body["items"]) == 1
        item = body["items"][0]
        assert item["tier"] == 1 and item["memberCodes"] == ["600001.SH"]
        assert item["card"]["driverKind"] == "theme"
        assert item["tierHistory"]["mechScore"] == 0.7
        # 维度名是语义标识符,原样透传(⛔ 不 camel 化)
        assert item["tierHistory"]["mechBreakdown"] == {"driver_freshness": 0.8}

    def test_score_card_rides_on_tier_history_not_on_the_basket(self, client, AUTH, api_env):
        """V2.1-④ live 路径的两条契约(⑦ 照此接线,⛔ 别猜):

        ① 百分制住 `tierHistory.scorePercent` / `.scoreContributions` —— **分数是定档
           留痕的属性**,那才是它的家;
        ② `BasketOut.scorePercent` 在这条路上**刻意为 null**(它是"报告快照"那条路
           的 B 类字段)。两处都填 = 同一份响应里放两个必须永远一致的副本。
        **客户端读法**:`basket.scorePercent ?? basket.tierHistory?.scorePercent`。"""
        bid = _seed_basket(api_env.db_path, ["600001.SH"], with_tier_history=False)
        with connection(api_env.db_path) as conn:
            conn.execute(
                "INSERT INTO tier_history (trade_date, basket_id, tier, mech_score,"
                " mech_breakdown_json, rank_in_tier, rank_mech, llm_rank_delta, llm_reason,"
                " pack_version, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (D0.strftime("%Y%m%d"), bid, 1, 0.725,
                 '{"dims": {"leader_clarity": 1.0, "sector_strength": 0.8},'
                 ' "weights": {"leader_clarity": 0.25, "sector_strength": 0.3},'
                 ' "contrib": {"leader_clarity": 0.25, "sector_strength": 0.24},'
                 ' "flags": ["leader_clarity_missing"], "neutral_filled_weight": 0.25}',
                 1, 1, 0, None, "K4-pack-v1", "2026-08-02T00:00:00+08:00"),
            )
        item = client.get(f"/api/v1/baskets?date={D0:%Y%m%d}", headers=AUTH).json()["items"][0]
        th = item["tierHistory"]
        assert th["scorePercent"] == 72.5
        assert [c["dim"] for c in th["scoreContributions"]] == ["leader_clarity", "sector_strength"]
        assert th["scoreContributions"][0]["label"] == "龙头清晰度"
        assert th["scoreContributions"][0]["neutralFilled"] is True
        assert item["scorePercent"] is None and item["scoreContributions"] == []

    def test_basket_without_tier_history_has_no_score_anywhere(self, client, AUTH, api_env):
        """无定档留痕 → `tierHistory=null`,两个新键在两处都取不到值。
        ⛔ 不许退化成 0 分(那是"这一篮很差"这个实质性判断)。"""
        _seed_basket(api_env.db_path, ["600001.SH"], with_tier_history=False)
        item = client.get(f"/api/v1/baskets?date={D0:%Y%m%d}", headers=AUTH).json()["items"][0]
        assert item["tierHistory"] is None and item["scorePercent"] is None

    def test_empty_day_is_200_with_empty_list_not_404(self, client, AUTH, api_env):
        """「今日无篮子达到定档标准」是**合法输出**(⑥-b-B),不是找不到。"""
        r = client.get("/api/v1/baskets?date=20260101", headers=AUTH)
        assert r.status_code == 200 and r.json()["items"] == []

    def test_tier_filter(self, client, AUTH, api_env):
        _seed_basket(api_env.db_path, ["600001.SH"], tier=1, key="k1")
        _seed_basket(api_env.db_path, ["600002.SH"], tier=3, key="k3")
        got = client.get(f"/api/v1/baskets?date={D0:%Y%m%d}&tier=3", headers=AUTH).json()
        assert [i["basketKey"] for i in got["items"]] == ["k3"]

    def test_bad_date_degrades_to_empty_not_4xx(self, client, AUTH, api_env):
        r = client.get("/api/v1/baskets?date=notadate", headers=AUTH)
        assert r.status_code == 200 and r.json()["items"] == []


# ══════════════════════════════════════════════════════════════════════════
# GET /baskets/{id} 与 /card —— 两个 404 reason 必须分得开
# ══════════════════════════════════════════════════════════════════════════

class TestBasketDetailAndCard:
    def test_basket_not_found_reason(self, client, AUTH, api_env):
        r = client.get("/api/v1/baskets/999", headers=AUTH)
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "basket_not_found"

    def test_basket_without_card_is_200_with_card_not_ready_reason(self, client, AUTH, api_env):
        """⚠ **有篮子无卡不是 404**:事务 1 与事务 2 分开,这是合法中间态。
        照返 200 + `card=null` + `cardUnavailableReason`。"""
        _seed_basket(api_env.db_path, ["600001.SH"], with_card=False)
        body = client.get("/api/v1/baskets/1", headers=AUTH).json()
        assert body["card"] is None and body["cardUnavailableReason"] == "card_not_ready"

    def test_card_endpoint_returns_the_frozen_card(self, client, AUTH, api_env):
        _seed_basket(api_env.db_path, ["600001.SH"])
        body = client.get("/api/v1/baskets/1/card", headers=AUTH).json()
        assert body["basketKey"] == "k1" and body["specVersion"] == "card-1"
        assert body["members"][0]["tsCode"] == "600001.SH"
        # 夹逼拒收 → 值 null + 原因非空,⛔ 不许拿 0 顶
        m = body["members"][0]
        assert m["entryZone"] is None and m["entryZoneUnavailableReason"] == "夹逼拒收"
        assert m["maxChase"] is None and m["exitReference"] is None
        assert body["disclaimer"], "固定文案单一源必须原样下发"

    def test_card_404_reason_is_card_not_ready_when_basket_exists(self, client, AUTH, api_env):
        """🔴 **本文件最要紧的一条**:篮子在、卡没生成 → `card_not_ready`,
        **不是** `basket_not_found` —— 后者会让用户以为系统丢了篮子。"""
        _seed_basket(api_env.db_path, ["600001.SH"], with_card=False)
        r = client.get("/api/v1/baskets/1/card", headers=AUTH)
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "card_not_ready"

    def test_card_404_reason_is_basket_not_found_when_basket_missing(self, client, AUTH, api_env):
        r = client.get("/api/v1/baskets/999/card", headers=AUTH)
        assert r.status_code == 404
        assert r.json()["detail"]["reason"] == "basket_not_found"

    def test_the_two_card_reasons_are_different_strings(self):
        """守门:两个码合并成一个就把「没有」和「没看」混了。"""
        from neckline.api.app import REASON_BASKET_NOT_FOUND, REASON_CARD_NOT_READY

        assert REASON_BASKET_NOT_FOUND != REASON_CARD_NOT_READY


# ══════════════════════════════════════════════════════════════════════════
# B1(2026-08-04 planner 裁定,小审 🔵 B-3):卡**损坏** = 500 + `card_corrupt`,
# ⛔ 不是 404、⛔ 不许降格成 `card_not_ready`。
#
# 决定性理由:卡是冻结件、`INSERT OR IGNORE` 永不覆盖 → **坏了就是永久坏的**;
# 客户端若当 `card_not_ready` 处理就会永远重试、界面永远显示「卡还没生成」而那张卡
# 这辈子不会来 = 静默永久失败。
# ══════════════════════════════════════════════════════════════════════════

def _corrupt_card_json(db, basket_id: int, raw: str) -> None:
    """把冻结卡的 `card_json` 直接改坏(**只在测试库里**造事故现场;生产侧这张表
    是冻结件,应用代码没有任何 UPDATE 路径)。"""
    with connection(db) as conn:
        conn.execute("UPDATE basket_cards SET card_json=? WHERE basket_id=?", (raw, basket_id))


class TestCorruptCardIsNotCardNotReady:
    @pytest.mark.parametrize("raw,why", [
        ("{这不是 JSON", "json.loads 抛错"),
        ("[1, 2, 3]", "顶层不是对象"),
        ('{"spec_version": "card-1", "basket_key": "k1"}', "顶层一个内容键都没有"),
        ("{}", "空对象"),
    ])
    def test_corrupt_card_returns_500_card_corrupt(self, client, AUTH, api_env, raw, why):
        _seed_basket(api_env.db_path, ["600001.SH"])
        _corrupt_card_json(api_env.db_path, 1, raw)
        r = client.get("/api/v1/baskets/1/card", headers=AUTH)
        assert r.status_code == 500, f"{why}:期望 500 得 {r.status_code}"
        assert r.json()["detail"]["reason"] == "card_corrupt"

    def test_missing_card_row_is_still_404_card_not_ready(self, client, AUTH, api_env):
        """分界线的另一侧一个字节都不许动:**根本没有行** = 仍是 404 `card_not_ready`。"""
        _seed_basket(api_env.db_path, ["600001.SH"], with_card=False)
        r = client.get("/api/v1/baskets/1/card", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "card_not_ready"

    def test_good_card_is_unaffected(self, client, AUTH, api_env):
        """防误伤:完好的卡照常 200(必需键判据不许把好卡判成坏卡)。"""
        _seed_basket(api_env.db_path, ["600001.SH"])
        assert client.get("/api/v1/baskets/1/card", headers=AUTH).status_code == 200

    def test_list_endpoint_reports_corrupt_reason_not_not_ready(self, client, AUTH, api_env):
        """列表/详情里卡只是内嵌可选字段 → 照返 200,但 reason 必须是 `card_corrupt`
        (⛔ 降格成 `card_not_ready` 就把数据事故说成了等待中)。"""
        _seed_basket(api_env.db_path, ["600001.SH"])
        _corrupt_card_json(api_env.db_path, 1, "{坏了")
        body = client.get("/api/v1/baskets/1", headers=AUTH).json()
        assert body["card"] is None and body["cardUnavailableReason"] == "card_corrupt"

    def test_store_flags_corruption_and_never_rebuilds(self, api_env, caplog):
        """检测点在 store(唯一一处),且**只报不修**:⛔ 不补全、不跳过坏字段;
        日志级别是 **ERROR** 不是 WARNING(冻结件损坏是真数据事故,必须有人看见)。"""
        import logging

        from neckline.selection.basket_store import load_basket_card

        _seed_basket(api_env.db_path, ["600001.SH"])
        _corrupt_card_json(api_env.db_path, 1, '{"spec_version": "card-1"}')
        with caplog.at_level(logging.ERROR):
            row = load_basket_card(1, db_path=api_env.db_path)
        assert row is not None                       # 行还在(不是"没有卡")
        assert row["card"] is None and row["card_corrupt"] is True
        assert "一个内容键都没有" in row["card_corrupt_reason"]
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    def test_healthy_card_is_not_flagged(self, api_env):
        from neckline.selection.basket_store import load_basket_card

        _seed_basket(api_env.db_path, ["600001.SH"])
        row = load_basket_card(1, db_path=api_env.db_path)
        assert row["card_corrupt"] is False and row["card_corrupt_reason"] is None

    def test_content_key_rule_is_any_of_not_all_of(self):
        """判据本身要看住(误判代价不对称:判错成损坏 = 用户看不到一张其实好好的卡,
        而且不可自愈):**三个内容键有其一即可**,⛔ 不许改成"都得有" —— 各消费方要的
        不是同一批键(⑧ 只读两份 spec、⑩ 只读 members),"都得有"会把合法局部卡判成
        损坏;也⛔ 不收身份键/展示键、更不许加"新版本才有"的键(卡是冻结件)。"""
        from neckline.selection.basket_store import CARD_CONTENT_KEYS, _decode_card_json

        assert set(CARD_CONTENT_KEYS) == {"members", "verification_spec", "invalidation_spec"}
        assert set(CARD_CONTENT_KEYS) <= set(_card(["600001.SH"]).keys())
        for cosmetic in ("spec_version", "basket_key", "trade_date", "name", "disclaimer"):
            assert cosmetic not in CARD_CONTENT_KEYS, cosmetic
        # 只带其中一个键的**局部卡**(⑧ / ⑩ 各自的合法用法)不许被判成损坏
        for k in CARD_CONTENT_KEYS:
            card, why = _decode_card_json('{"%s": {}}' % k)
            assert card is not None and why is None, f"只带 {k} 的局部卡被误判成损坏"


class TestVerificationAndReview:
    def test_verification_not_evaluated_is_200_not_404(self, client, AUTH, api_env):
        """「今天还没判过」≠「判了是 unclear」≠「没有这个篮子」——三态分开。"""
        _seed_basket(api_env.db_path, ["600001.SH"])
        body = client.get("/api/v1/baskets/1/verification?date=20260724", headers=AUTH).json()
        assert body["notEvaluated"] is True and body["rows"] == []

    def test_verification_404_for_missing_basket(self, client, AUTH, api_env):
        r = client.get("/api/v1/baskets/999/verification", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "basket_not_found"

    def test_review_404_reuses_not_found_for_missing_review(self, client, AUTH, api_env):
        """复用既有 reason 字符串,客户端 `mapReason` 已有 case —— CLAUDE.md 明文:
        复用不需要新 case,只有全新字符串才需要。"""
        _seed_basket(api_env.db_path, ["600001.SH"])
        r = client.get("/api/v1/baskets/1/review?date=20260724", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


# ══════════════════════════════════════════════════════════════════════════
# 计划继承 / 建仓快照
# ══════════════════════════════════════════════════════════════════════════

class TestPositionPlans:
    def _open(self, client, AUTH):
        return client.post("/api/v1/positions", headers=AUTH, json={
            "code": "600001.SH", "buy_price": 10.0, "qty": 100,
        }).json()["position_id"]

    def test_v1_plan_exists_right_after_open(self, client, AUTH, api_env):
        pid = self._open(client, AUTH)
        items = client.get(f"/api/v1/positions/{pid}/plans", headers=AUTH).json()["items"]
        assert [i["version"] for i in items] == [1]
        # 无来源篮子 → `available=False` + 原因,**行照落**(⛔ 不省略整条记录)
        assert items[0]["plan"]["available"] is False
        assert items[0]["plan"]["reason"] == "no_source_basket"

    def test_create_new_version_does_not_touch_v1(self, client, AUTH, api_env):
        pid = self._open(client, AUTH)
        r = client.post(f"/api/v1/positions/{pid}/plans", headers=AUTH,
                        json={"plan": {"available": True, "risks": ["自定"]}, "note": "我改的"})
        assert r.status_code == 201 and r.json()["version"] == 2
        items = client.get(f"/api/v1/positions/{pid}/plans", headers=AUTH).json()["items"]
        assert [i["version"] for i in items] == [1, 2]
        assert items[0]["plan"]["available"] is False, "v1 原判不许被新版本改写"

    def test_arming_flags_are_recomputed_server_side(self, client, AUTH, api_env):
        """⑪-D-B 闸②:客户端把 `exit_reference_armed` 写成 True 也没用,
        服务端拿真实成交价重过一遍闸 —— 否则"写个新版本"就成了绕开红线闸的后门。"""
        pid = self._open(client, AUTH)
        body = client.post(f"/api/v1/positions/{pid}/plans", headers=AUTH, json={"plan": {
            "exit_reference": {"low": 9.0, "high": 9.5},   # 低于成交价 10.0 → 不该武装
            "exit_reference_armed": True,
        }}).json()
        assert body["plan"]["exit_reference_armed"] is False
        assert body["plan"]["exit_reference_armed_reason"]
        assert body["plan"]["exit_reference_armed_note"]

    def test_no_base_plan_is_400_with_its_own_reason(self, client, AUTH, api_env):
        r = client.post("/api/v1/positions/424242/plans", headers=AUTH, json={"plan": {}})
        assert r.status_code == 400 and r.json()["detail"]["reason"] == "no_base_plan"

    def test_entry_snapshot_round_trip(self, client, AUTH, api_env):
        pid = self._open(client, AUTH)
        body = client.get(f"/api/v1/positions/{pid}/entry-snapshot", headers=AUTH).json()
        assert body["positionId"] == pid and body["tsCode"] == "600001.SH"
        # ⑩ 范围内未采集的四项如实列出,⛔ 别把"没采"读成"没有"
        assert "not_captured" in body["snapshot"]

    def test_entry_snapshot_404_reuses_not_found(self, client, AUTH, api_env):
        r = client.get("/api/v1/positions/424242/entry-snapshot", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"


# ══════════════════════════════════════════════════════════════════════════
# 画像 / 策略包 / 评价
# ══════════════════════════════════════════════════════════════════════════

class TestProfileAndPacks:
    def test_profile_never_computed_says_so(self, client, AUTH, api_env):
        """`asOf` 为空 = **该期从未算过**,⛔ 不是"算出来是空的"。"""
        for path in ("/api/v1/profile/preference", "/api/v1/profile/capability"):
            body = client.get(path, headers=AUTH).json()
            assert body["available"] is False and body["unavailableReason"]
            assert body["items"] == []

    def test_profile_reads_back_latest_period(self, client, AUTH, api_env):
        from neckline.profile.preference import PreferenceRow
        from neckline.profile.store import save_preference

        save_preference("20260731", [PreferenceRow(
            dimension="industry", value="电池", share=0.4, sample_n=3,
            window_start="20260501", window_end="20260731", confidence="low",
        )], api_env.db_path)
        body = client.get("/api/v1/profile/preference", headers=AUTH).json()
        assert body["available"] is True and body["asOf"] == "20260731"
        row = body["items"][0]
        # 每项必带样本量 / 时间范围 / 置信度(⑫-B 硬要求)
        assert row["sampleN"] == 3 and row["confidence"] == "low"
        assert row["windowStart"] and row["windowEnd"]

    def test_packs_list_and_detail(self, client, AUTH, api_env):
        assert client.get("/api/v1/packs", headers=AUTH).json()["items"] == []
        r = client.get("/api/v1/packs/nope", headers=AUTH)
        assert r.status_code == 404 and r.json()["detail"]["reason"] == "not_found"

    def test_packs_contract_carries_no_discipline_params(self, client, AUTH, api_env):
        """🔴 §五 红线 6:策略包与纪律章程是**两条版本线**,包里不许出现纪律参数。"""
        from neckline.api.schemas import PackOut

        assert "stopPct" not in PackOut.model_fields
        assert "takeProfitRetrace" not in PackOut.model_fields

    def test_eval_weekly_never_500s(self, client, AUTH, api_env):
        """评价是审计件:样本窗未就绪 / 算炸了都要 200 + 可读原因,⛔ 不 500。"""
        r = client.get("/api/v1/eval/weekly?week=20260724", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert "available" in body
        if not body["available"]:
            assert body["unavailableReason"]


def test_every_new_endpoint_requires_a_token(client):
    for path in ("/api/v1/baskets", "/api/v1/baskets/1", "/api/v1/baskets/1/card",
                 "/api/v1/baskets/1/verification", "/api/v1/baskets/1/review",
                 "/api/v1/positions/1/plans", "/api/v1/positions/1/entry-snapshot",
                 "/api/v1/profile/preference", "/api/v1/profile/capability",
                 "/api/v1/packs", "/api/v1/packs/x", "/api/v1/eval/weekly"):
        assert client.get(path).status_code in (401, 403), path
