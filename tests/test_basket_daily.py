"""V2-⑭-A 篮子日报视图模型 + 五段渲染(plan §五 V2-⑭-A 验收逐条)。

核心断言(每条对应 plan 的一句硬要求):
· **五段结构顺序定死**:① 市场语境 → ② 持仓体检 → ③ 今日篮子 → ③b 未定档 → ④ 昨日复盘 → ⑤ 新鲜度;
· **③ 各档全部可空是合法输出**,空档位如实写出、不隐藏;
· **V2.1-② 读侧宽容**:`by_tier()` 按数据实际档位构造 —— 回放 V2 老快照时 **T3 篮子照常显示**,
  新报告不出幽灵 T3(两条互为对照,少一条就只证明了一半);
· **③b 两个原因码永不合并**,且**零溢出时这一节仍在**;
· **「没有」与「没看」分开**:`available=false` 与「空列表 + available=true」讲不同的话;
· **有篮子无卡是合法中间态**(`card_not_ready`),⛔ 不把整篮从报告里抹掉;
· **snake→camel 转换点唯一**,且**嵌套语义键原样透传**(维度名不许被 camel 化)。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from tests.conftest import business_days, insert_trade_cal

from neckline.db import connection
from neckline.report import basket_daily as bd
from neckline.report.render import render_markdown
from neckline.report.sentiment import SentimentDashboard
from neckline.selection.basket_store import save_basket_card

pytestmark = pytest.mark.usefixtures("isolated_env")

D0 = date(2026, 7, 23)
D1 = date(2026, 7, 24)


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def _card_json(codes, *, tier=1, key="k1", name="固态电池") -> dict:
    return {
        "spec_version": "card-1", "version": 1, "basket_key": key,
        "trade_date": D0.strftime("%Y%m%d"), "next_trade_date": D1.strftime("%Y%m%d"),
        "name": name, "driver": "固态电池装车", "driver_kind": "theme",
        "evidence": [{"claim": "某车企定点", "source": "公告", "date": "20260722", "url": ""}],
        "evidence_status": "ok", "why_now": "昨日装车公告落地",
        "members": [
            {"ts_code": c, "name": f"名{i}", "role_llm": "leader" if i == 0 else "core",
             "role_mech": "leader" if i == 0 else "elastic",
             "role_conflict": 0 if i == 0 else 1, "reason": "理由",
             "is_primary": 1 if i == 0 else 0, "industry": "电池",
             "industry_lift": 1.4, "lift_reason": "富集", "primary_reason": "主归属",
             "rs_rank": i + 1, "k4_tag": None, "mech": {"close": 10.0},
             "entry_zone": {"low": 9.5, "high": 10.2, "why": "回踩"},
             "entry_zone_clamp": "ok", "entry_zone_unavailable_reason": None,
             "max_chase": 3.0, "max_chase_clamp": "ok", "max_chase_unavailable_reason": None,
             "exit_reference": {"low": 11.0, "high": 12.0}, "exit_reference_clamp": "ok",
             "exit_reference_unavailable_reason": None,
             "tags": [], "tags_absent": []}
            for i, c in enumerate(codes)
        ],
        "role_conflicts": [c for i, c in enumerate(codes) if i > 0],
        "tier": tier, "rank_in_tier": 1, "rank_mech": 1, "mech_score": 0.72,
        # ⚠ **维度名是语义标识符**:`driver_freshness` 与现役包的权重键逐字对应,
        # 转 camel 会把它改名 —— 本 fixture 就是那条断言的证据。
        "tier_breakdown": {"driver_freshness": 0.8, "leader_clarity": 0.6},
        "tier_reason": "驱动新鲜", "tier_note": None,
        "scripts": {"strong": "强开看能否承接", "flat": "平开看量", "weak": "弱开先看"},
        "scripts_unavailable_reason": None,
        "verification_spec": {"members_up_ratio": 0.5}, "verification_text": "半数成员翻红即验证",
        "invalidation_spec": {"low_open_pct": -0.02}, "invalidation_text": "整体低开 2% 失效",
        "risks": ["拥挤度已高"], "disclaimer": "以上为参考、非指令。",
        "fingerprint": {"stop_pct": 0.05, "take_profit_retrace": 0.08,
                        "charter_version": "v1.3.3", "pack_version": "K4-pack-v1",
                        "engine_api_version": 1, "verification_ruleset_version": "vr-1"},
        "discipline_labels": ["止损 -5%"], "narrative": "这一篮的逻辑是……",
        "llm_stage": "ok", "degraded": False, "notes": [],
    }


def _seed_basket(env, codes, *, tier=1, key="k1", name="固态电池", card="auto") -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), key, name, "固态电池装车", "theme", tier,
             "K4-pack-v1", 1, "v1.3.3", "auto", "ok", "2026-08-02T00:00:00+08:00"),
        )
        bid = int(cur.lastrowid)
        for i, code in enumerate(codes):
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (bid, code, "core", "leader" if i == 0 else "elastic", 0, "理由",
                 1 if i == 0 else 0, "2026-08-02T00:00:00+08:00"),
            )
    if card == "auto":
        save_basket_card(bid, _card_json(codes, tier=tier, key=key, name=name),
                         db_path=env.db_path)
    elif card is not None:
        save_basket_card(bid, card, db_path=env.db_path)
    return bid


class _Dropped:
    """⑥ `TierResult.dropped` 的鸭子替身(只有三个字段,同真件)。"""

    def __init__(self, basket_key, reason, mech_score):
        self.basket_key, self.reason, self.mech_score = basket_key, reason, mech_score


def _seed_stage(env, *, reason_stage="ok", search_stage="ok", baskets=0, notes=(),
                trade_date=D0) -> None:
    """落一行 ⑤ 段状态(§七 P0-39)。**零篮子时 ③ 节说什么话全看这一行** ——
    没有它 = 我们不知道引擎跑没跑 = 「本段未取得」。"""
    from neckline.selection.basket_stage_handoff import save_stage_handoff

    save_stage_handoff(trade_date, SimpleNamespace(
        search_stage=search_stage, reason_stage=reason_stage,
        baskets=tuple(range(baskets)), notes=tuple(notes),
    ), db_path=env.db_path)


def _render(bdaily=None, **kw):
    s = SentimentDashboard(
        trade_date=D0, limit_up_count=1, limit_down_count=0, zaban_count=0, zaban_rate=0.0,
        max_consec_limit_up=1, prev_limit_up_premium_avg=0.01, prev_limit_up_sample=3,
        position_quota="半额", quota_reason="中性",
    )
    return render_markdown(trade_date=D0, strategy_version="v1.3.3",
                           generated_at="2026-08-02T08:00:00+00:00",
                           sentiment=s, sectors=[], basket_daily=bdaily, **kw)


# ══════════════════════════════════════════════════════════════════════════
# snake → camel 唯一转换点
# ══════════════════════════════════════════════════════════════════════════

class TestCardCamelConversion:
    def test_top_level_and_member_keys_become_camel(self):
        out = bd.card_to_public_dict(_card_json(["600001.SH"]))
        assert out["basketKey"] == "k1" and out["driverKind"] == "theme"
        assert out["whyNow"] and out["evidenceStatus"] == "ok"
        m = out["members"][0]
        assert m["tsCode"] == "600001.SH" and m["roleLlm"] == "leader"
        assert m["entryZoneClamp"] == "ok" and m["exitReferenceUnavailableReason"] is None

    def test_int_flags_become_real_bools(self):
        """`role_conflict`/`is_primary` 在冻结件里是 0/1 整数;Swift 的 `Bool` 解
        `0`/`1` 会直接失败 —— 转换点必须做这件实事,不是美化。"""
        out = bd.card_to_public_dict(_card_json(["600001.SH", "600002.SH"]))
        assert out["members"][0]["isPrimary"] is True
        assert out["members"][0]["roleConflict"] is False
        assert out["members"][1]["roleConflict"] is True

    def test_semantic_nested_keys_are_passed_through_untouched(self):
        """⛔ **`tier_breakdown` 的键是五维维度名**(与现役包权重键逐字对应)、
        `verification_spec` 是喂 ⑧ 哨兵的结构化条件 —— camel 化会把语义标识符改名。"""
        out = bd.card_to_public_dict(_card_json(["600001.SH"]))
        assert out["tierBreakdown"] == {"driver_freshness": 0.8, "leader_clarity": 0.6}
        assert out["verificationSpec"] == {"members_up_ratio": 0.5}
        assert out["invalidationSpec"] == {"low_open_pct": -0.02}

    def test_fingerprint_keys_are_field_names_so_they_do_convert(self):
        out = bd.card_to_public_dict(_card_json(["600001.SH"]))
        assert out["fingerprint"]["packVersion"] == "K4-pack-v1"
        assert out["fingerprint"]["verificationRulesetVersion"] == "vr-1"

    def test_missing_key_stays_missing_not_none(self):
        """冻结快照:老卡没有的键**不出现**,⛔ 不补一个 `null`(那会把「这版卡没有
        这个概念」讲成「有这个概念但值是空」)。"""
        out = bd.card_to_public_dict({"basket_key": "k1", "members": []})
        assert "tierReason" not in out and "driver" not in out

    def test_none_card_stays_none(self):
        assert bd.card_to_public_dict(None) is None


# ══════════════════════════════════════════════════════════════════════════
# ③ 今日篮子 / ③b 未定档 / ④ 昨日复盘
# ══════════════════════════════════════════════════════════════════════════

class TestBuildBasketDaily:
    def test_loads_today_baskets_with_frozen_card(self, isolated_env):
        _seed_basket(isolated_env, ["600001.SH", "600002.SH"])
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True and len(out.baskets) == 1
        b = out.baskets[0]
        assert b.tier == 1 and b.card is not None and b.card_version == 1
        assert b.card_unavailable_reason is None
        assert out.pack_version == "K4-pack-v1"

    def test_basket_without_card_is_a_legal_intermediate_state(self, isolated_env):
        """事务 1 与事务 2 分开 → 「有篮子、无卡」合法。⛔ 不许因此把整篮抹掉。"""
        _seed_basket(isolated_env, ["600001.SH"], card=None)
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert len(out.baskets) == 1
        assert out.baskets[0].card is None
        assert out.baskets[0].card_unavailable_reason == "card_not_ready"

    def test_no_baskets_is_available_true_with_empty_list(self, isolated_env):
        """「今天真没有篮子」= `available=True` + 空列表(合法输出,⑥-b-B)。

        ⚠ **§七 P0-39 起这句话有前提**:必须有一行 ⑤ 段状态证明引擎跑过。本测试
        原先不落这一行也断言 `True` —— 那正是被生产打出来的那个 bug 的镜像。"""
        _seed_stage(isolated_env, reason_stage="ok")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True and out.baskets == []

    def test_dropped_none_means_not_computed_not_zero_overflow(self, isolated_env):
        """**`dropped=None` = 本次没跑 ⑥**(读历史 / 只出报告)→ 如实标未取得,
        ⛔ 不许被读成「今日零溢出」。"""
        out = bd.build_basket_daily(D0, dropped=None, db_path=isolated_env.db_path,
                                    with_exec_hints=False)
        assert out.dropped_available is False and out.dropped_unavailable_reason
        assert out.dropped == []

    def test_dropped_empty_sequence_means_computed_and_zero_overflow(self, isolated_env):
        out = bd.build_basket_daily(D0, dropped=[], db_path=isolated_env.db_path,
                                    with_exec_hints=False)
        assert out.dropped_available is True and out.dropped == []

    def test_dropped_two_reason_codes_are_kept_apart(self, isolated_env):
        _seed_basket(isolated_env, ["600001.SH"], key="k1")
        out = bd.build_basket_daily(
            D0, dropped=[_Dropped("k9", "capacity_overflow", 0.71),
                         _Dropped("k8", "below_quality_line", 0.11)],
            db_path=isolated_env.db_path, with_exec_hints=False,
        )
        assert {d.reason for d in out.dropped} == {"capacity_overflow", "below_quality_line"}
        pub = out.to_public_dict()["droppedBaskets"]
        assert all("basketId" not in d for d in pub), "溢出篮没进 baskets 表,不许给 id"


class TestZeroBasketHonesty:
    """§七 P0-39:`baskets` 零行有**两种相反成因**,③ 节必须讲成两句不同的话。

    2026-08-05 生产实打:`llm_providers` 配置不全 → ⑤ `no_provider` 全缺席 →
    `baskets` 零行,而报告照样输出「今天没有共同驱动清晰、成员结构够格的篮子」
    这句**实质性市场判断** —— 系统其实什么都没判。本类逐条钉死四态。

    ⚠ 判据锚在「⑤ 的段状态行」,**不是**「读表成功」—— 后者只证明表读得出来。
    """

    _LEGAL_OUTPUT_SENTENCE = "今日无篮子达到定档标准"

    def _section(self, env, **kw) -> str:
        daily = bd.build_basket_daily(D0, db_path=env.db_path, with_exec_hints=False, **kw)
        md = _render(daily)
        return md.split("## ③ 今日篮子")[1].split("### ③b")[0]

    # —— 态 1:引擎没跑(no_provider)————————————————————————————————
    def test_no_provider_is_not_dressed_up_as_a_market_verdict(self, isolated_env):
        _seed_stage(isolated_env, reason_stage="no_provider", search_stage="no_provider")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "no_provider" in (out.baskets_unavailable_reason or "")
        assert "未运行" in (out.baskets_unavailable_reason or "")
        section = self._section(isolated_env)
        assert "本段未取得" in section
        assert self._LEGAL_OUTPUT_SENTENCE not in section, (
            "引擎没跑却讲「今天没有够格的篮子」= 把缺席伪装成市场判断(P0-39 本体)"
        )
        assert "共同驱动清晰" not in section

    # —— 态 2:预算尽 ————————————————————————————————————————————
    def test_budget_exhausted_is_reported_with_its_own_reason_code(self, isolated_env):
        _seed_stage(isolated_env, reason_stage="budget_exhausted", search_stage="ok")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "budget_exhausted" in (out.baskets_unavailable_reason or ""), (
            "原因码必须如实带出 —— 「没跑」的各种成因语义不合并"
        )
        section = self._section(isolated_env)
        assert "本段未取得" in section and self._LEGAL_OUTPUT_SENTENCE not in section

    # —— 态 3:跑了、今天真没有够格的篮子 ————————————————————————————
    def test_engine_ran_and_found_nothing_keeps_the_legal_output_wording(self, isolated_env):
        _seed_stage(isolated_env, reason_stage="ok", search_stage="ok")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True and out.baskets_unavailable_reason is None
        section = self._section(isolated_env)
        assert self._LEGAL_OUTPUT_SENTENCE in section and "不是故障" in section
        assert "本段未取得" not in section

    # —— 态 4:正常有篮子 ————————————————————————————————————————
    def test_baskets_present_is_available_regardless_of_the_stage_row(self, isolated_env):
        """有篮子 = 引擎跑过的**活证据**(篮子就是它产出的)——段状态行缺失也不许
        把一份有篮子的报告标成「未取得」。"""
        _seed_basket(isolated_env, ["600001.SH"], tier=1)
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True and len(out.baskets) == 1
        section = self._section(isolated_env)
        assert "固态电池" in section and "本段未取得" not in section
        assert self._LEGAL_OUTPUT_SENTENCE not in section

    # —— 第五种:压根没有段状态行 = 我们不知道 ————————————————————————
    def test_missing_stage_row_is_not_obtained_not_no_baskets(self, isolated_env):
        """读历史报告 / 只出报告(`scripts/report.py` 回放)拿不到段状态 → 如实标
        未取得,⛔ 不许拿「不知道」冒充「知道没有」(同 ③b 在历史回放时的姿势)。"""
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "本报告未取得" in (out.baskets_unavailable_reason or "")
        section = self._section(isolated_env)
        assert "本段未取得" in section and self._LEGAL_OUTPUT_SENTENCE not in section

    def test_aggregate_crash_default_stage_is_not_mistaken_for_no_seeds(self, isolated_env):
        """⑤ 自己那道保险丝返回的是**默认字段值**(`reason_stage=no_seeds`)——光看
        段状态会被读成「跑了、今天真没种子」。notes 里的 `aggregate_failed:*` 才是真相。"""
        _seed_stage(isolated_env, reason_stage="no_seeds", search_stage="no_seeds",
                    notes=("aggregate_failed:ValueError",))
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "aggregate_failed:ValueError" in (out.baskets_unavailable_reason or "")

    def test_zero_seeds_with_an_active_pack_is_a_real_market_verdict(self, isolated_env):
        """④ 扫描层跑过、当日零种子 = 「今日无热点 → 今日无篮子」,既有合法输出。"""
        _seed_stage(isolated_env, reason_stage="no_seeds", search_stage="no_seeds",
                    notes=("empty_seed_set",))
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True and out.baskets_unavailable_reason is None

    def test_no_active_pack_is_a_config_gap_not_a_market_verdict(self, isolated_env):
        _seed_stage(isolated_env, reason_stage="no_seeds", search_stage="no_seeds",
                    notes=("no_active_pack_or_seed_set",))
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "no_active_pack" in (out.baskets_unavailable_reason or "")

    def test_dropped_three_states_still_work_independently(self, isolated_env):
        """③b 既有三态不许被 ③ 的改动带偏(两段各判各的)。"""
        _seed_stage(isolated_env, reason_stage="no_provider")
        out = bd.build_basket_daily(D0, dropped=[], db_path=isolated_env.db_path,
                                    with_exec_hints=False)
        assert out.baskets_available is False        # ③ 没跑
        assert out.dropped_available is True         # ③b 明确跑了、零溢出
        out2 = bd.build_basket_daily(D0, dropped=None, db_path=isolated_env.db_path,
                                     with_exec_hints=False)
        assert out2.dropped_available is False


class TestPublicSnapshot:
    def test_snapshot_is_camel_and_carries_all_three_availability_flags(self, isolated_env):
        _seed_basket(isolated_env, ["600001.SH"])
        pub = bd.build_basket_daily(D0, dropped=[], db_path=isolated_env.db_path,
                                    with_exec_hints=False).to_public_dict()
        for key in ("basketsAvailable", "droppedBasketsAvailable", "reviewsAvailable"):
            assert key in pub
        assert pub["droppedBaskets"] == []
        assert pub["baskets"][0]["basketKey"] == "k1"

    def test_old_report_snapshot_reads_back_as_not_looked(self):
        """老报告(建于 `basket_daily_json` 列之前)→ 三段全 `available=false`,
        ⛔ 不冒充「那天没有篮子」。"""
        out = bd.basket_daily_from_snapshot({})
        assert out["basketsAvailable"] is False and out["basketsUnavailableReason"]
        assert out["droppedBasketsAvailable"] is False
        assert out["reviewsAvailable"] is False

    def test_empty_basket_daily_marks_all_three_segments_absent(self):
        out = bd.empty_basket_daily(D0, "装配异常")
        assert not out.baskets_available and not out.dropped_available and not out.reviews_available
        assert out.notes == ["装配异常"]


# ══════════════════════════════════════════════════════════════════════════
# 五段渲染
# ══════════════════════════════════════════════════════════════════════════

class TestFiveSectionOrder:
    def test_sections_appear_in_the_order_the_plan_pins_down(self):
        md = _render()
        idx = [md.index(h) for h in (
            "## ① 情绪与市场语境", "## ② 持仓体检", "## ③ 今日篮子",
            "### ③b 今日未定档篮子", "## ④ 昨日篮子复盘", "## ⑤ 数据新鲜度与降级披露",
        )]
        assert idx == sorted(idx), f"五段顺序被打乱:{idx}"

    def test_title_is_the_basket_daily_not_the_old_post_close_report(self):
        assert _render().startswith("# Neckline 篮子日报")

    def test_semantic_red_line_disclaimer_is_at_the_top(self):
        """§2.8-C:排序 / Tier = 注意力优先级,不是收益预测。"""
        md = _render()
        assert "注意力优先级,不是收益预测" in md
        assert "参考件、非指令" in md


class TestTodayBasketsSection:
    def _daily(self, isolated_env, **kw):
        return bd.build_basket_daily(D0, db_path=isolated_env.db_path,
                                     with_exec_hints=False, **kw)

    def test_empty_tiers_are_shown_not_hidden(self, isolated_env):
        """空档位如实显示。V2.1-②:现役档位只剩 T1/T2 → **不再出现幽灵的
        「今日 T3 为空」**(那句话在两档时代是假话:T3 不是"今天空",是已取消)。"""
        _seed_basket(isolated_env, ["600001.SH"], tier=1)
        md = _render(self._daily(isolated_env))
        assert "今日 T2 为空" in md
        assert "T3" not in md

    def test_historical_t3_baskets_still_render_on_replay(self, isolated_env):
        """🔴 **本块最该有的一条**(V2.1-② 硬约束「历史 T3 回放不许消失」):

        `reports.basket_daily_json` 是**冻结快照**,V2 时代的老报告里躺着 tier=3 的
        篮子。读侧(`BasketDaily.by_tier()` / `render._tier_title`)若写死两档,
        回放老报告时那些篮子会**静默消失** —— 用户看不出、日志里也没有痕迹。
        本条把「读侧宽容」钉成机器判据:T3 分组照出、篮子名照出、卡照渲染。"""
        _seed_basket(isolated_env, ["600001.SH"], tier=1, key="k1", name="现役T1篮")
        _seed_basket(isolated_env, ["600003.SH"], tier=3, key="k3", name="历史T3篮")
        daily = self._daily(isolated_env)
        assert sorted(daily.by_tier()) == [1, 3]                  # 按数据实际档位构造
        assert [b.name for b in daily.by_tier()[3]] == ["历史T3篮"]
        md = _render(daily)
        assert "### T3" in md and "历史T3篮" in md
        assert "### T1" in md and "现役T1篮" in md
        assert "今日 T2 为空" in md                                # 现役空档仍如实披露

    def test_by_tier_is_built_from_data_not_from_a_hardcoded_tuple(self, isolated_env):
        """反向:只有 T1 时**不许**凭空多出一个 T2/T3 的空键 —— `by_tier()` 的键
        就是数据里实际出现的档位(渲染层再决定要不要为现役空档补一句披露)。"""
        _seed_basket(isolated_env, ["600001.SH"], tier=1)
        assert list(self._daily(isolated_env).by_tier()) == [1]

    def test_all_tiers_empty_is_stated_as_a_legal_output(self, isolated_env):
        """⚠ §七 P0-39:这句「合法输出」**只有在 ⑤ 真跑过时**才准说,故先落段状态行。"""
        _seed_stage(isolated_env, reason_stage="ok")
        md = _render(self._daily(isolated_env))
        assert "今日无篮子达到定档标准" in md
        assert "不是故障" in md

    def test_segment_absent_says_not_obtained_not_no_baskets(self):
        md = _render(bd.empty_basket_daily(D0, "读库炸了"))
        section = md.split("## ③ 今日篮子")[1].split("### ③b")[0]
        assert "本段未取得" in section
        assert "「未取得」≠「今日无篮子」" in section

    def test_card_fields_render_with_reference_not_instruction_wording(self, isolated_env):
        _seed_basket(isolated_env, ["600001.SH", "600002.SH"])
        md = _render(self._daily(isolated_env))
        assert "固态电池装车" in md and "为什么是现在" in md
        assert "参考件、非指令" in md
        assert "离场参考区间**不是止盈线**" in md
        # 对拍分歧两说并存
        assert "两说并存" in md

    def test_no_forbidden_recommendation_wording(self, isolated_env):
        """§2.8-C 语义红线:UI 禁「推荐买入 / 建议买入 / 看好 / 值得买」。"""
        _seed_basket(isolated_env, ["600001.SH"])
        md = _render(self._daily(isolated_env))
        for banned in ("推荐买入", "建议买入", "值得买"):
            assert banned not in md, f"语义红线词「{banned}」不许出现在报告里"

    def test_card_not_ready_basket_still_shows_up(self, isolated_env):
        _seed_basket(isolated_env, ["600001.SH"], card=None)
        md = _render(self._daily(isolated_env))
        assert "本篮的卡还没生成" in md
        assert "600001.SH" in md

    def test_corrupt_card_says_damaged_not_not_ready(self, isolated_env):
        """B1(2026-08-04 裁定):卡**有行但读不出** → 报告如实写「数据损坏,已记录待
        排查」,⛔ **不许降级成「卡还没生成」** —— 那张卡是冻结件、不会自动重建,写成
        「还没生成」等于叫人白等一张永远不来的卡。"""
        bid = _seed_basket(isolated_env, ["600001.SH"])
        with connection(isolated_env.db_path) as conn:      # 只在测试库里造事故现场
            conn.execute("UPDATE basket_cards SET card_json='{坏了' WHERE basket_id=?", (bid,))
        views = bd.load_today_baskets(D0, db_path=isolated_env.db_path)
        assert views[0].card is None and views[0].card_unavailable_reason == "card_corrupt"
        md = _render(self._daily(isolated_env))
        assert "本篮卡数据损坏,已记录待排查" in md
        assert "本篮的卡还没生成" not in md
        assert "600001.SH" in md                            # 整篮不许因此从报告里消失


class TestDroppedSection:
    def test_section_exists_even_with_zero_overflow(self, isolated_env):
        md = _render(bd.build_basket_daily(D0, dropped=[], db_path=isolated_env.db_path,
                                           with_exec_hints=False))
        assert "### ③b 今日未定档篮子" in md
        assert "今日无未定档篮子" in md

    def test_two_reason_codes_are_never_merged_into_one_sentence(self, isolated_env):
        daily = bd.build_basket_daily(
            D0, dropped=[_Dropped("k9", "capacity_overflow", 0.71),
                         _Dropped("k8", "below_quality_line", 0.11)],
            db_path=isolated_env.db_path, with_exec_hints=False,
        )
        md = _render(daily)
        section = md.split("### ③b 今日未定档篮子")[1].split("## ④")[0]
        assert "capacity_overflow" in section and "below_quality_line" in section
        assert "今天机会多到装不下" in section
        assert "今天没什么好货" in section
        assert "未入选" not in section, "⛔ 两个原因码不许被合并成一句「未入选」"

    def test_not_computed_says_so_instead_of_pretending_zero(self, isolated_env):
        md = _render(bd.build_basket_daily(D0, dropped=None, db_path=isolated_env.db_path,
                                           with_exec_hints=False))
        section = md.split("### ③b 今日未定档篮子")[1].split("## ④")[0]
        assert "本段未取得" in section
        assert "「未取得」≠「今日无未定档篮子」" in section


class TestReviewSection:
    def test_absent_reviews_do_not_read_as_all_clear(self, isolated_env):
        md = _render(bd.build_basket_daily(D0, db_path=isolated_env.db_path,
                                           with_exec_hints=False))
        section = md.split("## ④ 昨日篮子复盘")[1].split("## ⑤")[0]
        assert "今日无昨日篮子可复盘" in section
        assert "别把这句读成" in section

    def test_review_renders_nine_mech_items_with_unavailable_reasons(self, isolated_env):
        bid = _seed_basket(isolated_env, ["600001.SH"])
        from neckline.review.basket_review import BasketReview
        from neckline.review.basket_review_store import save_review

        mech = {
            "meta": {"basket_id": bid, "basket_key": "k1", "name": "固态电池", "tier": 1,
                     "d0": D0.strftime("%Y%m%d"), "review_date": D0.strftime("%Y%m%d")},
            "close_rs": {"available": True, "excess_median": 0.012, "index_code": "000001.SH",
                         "index_ret": 0.004, "outperformers": 1},
            "mfe_mae": {"available": False, "unavailable_reason": "当日既无存拍也无 EOD 行情"},
        }
        save_review(BasketReview(basket_id=bid, basket_key="k1", name="固态电池", tier=1,
                                 review_date=D0, d0=D0, depth="full",
                                 mech=mech, llm_text="今天这一篮跑赢了大盘。"),
                    db_path=isolated_env.db_path)
        daily = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert daily.reviews_available and len(daily.reviews) == 1
        md = _render(daily)
        section = md.split("## ④ 昨日篮子复盘")[1].split("## ⑤")[0]
        assert "收盘相对强度" in section and "超额中位" in section
        assert "算不出" in section and "当日既无存拍也无 EOD 行情" in section
        # §2.7:LLM 解释原文整段呈现
        assert "今天这一篮跑赢了大盘。" in section

    def test_historical_brief_t3_review_still_renders(self, isolated_env):
        """V2.1-② 硬约束的另一半:**历史 `depth='brief'` 的 T3 复盘行照常渲染**。

        `basket_review_daily` 是**每日一行、写下去就冻住**的表(⛔ 无 UPDATE 路径),
        V2 时代的 T3 简评行永远是 `tier=3` + `depth='brief'`。渲染层若按现役档位或
        现役 depth 过滤,这些行会静默消失 —— 那是"删了历史"。"""
        bid = _seed_basket(isolated_env, ["600003.SH"], tier=3, key="k3", name="历史T3篮")
        from neckline.review.basket_review import BasketReview
        from neckline.review.basket_review_store import save_review

        mech = {
            "meta": {"basket_id": bid, "basket_key": "k3", "name": "历史T3篮", "tier": 3,
                     "d0": D0.strftime("%Y%m%d"), "review_date": D0.strftime("%Y%m%d")},
            "close_rs": {"available": True, "excess_median": -0.004, "index_code": "000001.SH",
                         "index_ret": 0.004, "outperformers": 0},
        }
        save_review(BasketReview(basket_id=bid, basket_key="k3", name="历史T3篮", tier=3,
                                 review_date=D0, d0=D0, depth="brief",
                                 mech=mech, llm_text="当时这一篮只做了简评。"),
                    db_path=isolated_env.db_path)
        daily = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert [(r.tier, r.depth) for r in daily.reviews] == [(3, "brief")]
        md = _render(daily)
        section = md.split("## ④ 昨日篮子复盘")[1].split("## ⑤")[0]
        assert "历史T3篮" in section
        assert "当时这一篮只做了简评。" in section


class TestFreshnessSection:
    def test_three_independent_failures_are_listed_separately(self):
        from neckline.report.industry_strength_store import IndustryStrengthFreshness
        from neckline.report.sectors import SectorDataFreshness
        from neckline.scan.freshness import ScanLayerFreshness

        md = _render(
            None,
            sector_freshness=SectorDataFreshness(sector_data_date="20260720", lag_days=3, stale=True),
            industry_freshness=IndustryStrengthFreshness(latest_date="20260723", lag_days=0, stale=False),
            scan_freshness=ScanLayerFreshness(latest_date="", lag_days=-1, stale=True),
        )
        assert "板块数据过期告警" in md
        assert "市场扫描层未就绪" in md
        # 行业强度那条不 stale → 顶部不告警,但 ⑤ 表里仍有一行
        section = md.split("## ⑤ 数据新鲜度与降级披露")[1]
        assert "概念板块" in section and "行业强度" in section and "市场扫描层" in section

    def test_absent_segments_are_summarised_in_the_disclosure_section(self):
        md = _render(bd.empty_basket_daily(D0, "装配异常"))
        section = md.split("## ⑤ 数据新鲜度与降级披露")[1]
        assert "本报告本次没看到的东西" in section
        assert "今日篮子" in section and "未定档篮子" in section and "昨日复盘" in section
