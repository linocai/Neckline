"""exec_hint 产品化单测(plan §五 v1.4-⑤-A 验收,需求 8 末段)。

覆盖:①DB `k4_advisory.exec_hint` 读取——文字随 DB 变、K4 行缺失时兜底不崩、四码可
部分命中 DB 部分兜底;②四条触发镜像各一组单测(C1 强票/C2 温和带/C3 挂低单自觉·与
⑤-B 联动/C4 回调大红);③`attach_exec_hints` 端到端装配(零额外 parquet 读取,DB 文
字随 K4 行变化,多码并存);④语义红线自查(文案不含"建议买入"类表述)。
"""

from __future__ import annotations

from datetime import date

import neckline.report.exec_hint as eh
from neckline.report.candidates import Candidate
# v2.0.0(⑩-C):`decision_log` 表停写留档,`neckline.decision_log.create_decision`
# 已物理删除;`tests.conftest.insert_decision_log_row` 是同签名的裸 SQL fixture
# 替身,起别名保持下面全部既有调用点逐字不变(两者关键字参数集合恰好一致)。
from tests.conftest import insert_decision_log_row as create_decision

_TODAY = date.today()   # attach_exec_hints 按 trade_date 截断 decision_log(无前视偏差,
                         # 见 exec_hint._latest_decision docstring)——测试里的决策日志
                         # created_at 是"现在"(真实时钟),trade_date 必须 >= 今天才不会
                         # 被截断掉,故这里统一用 date.today() 而非任意历史日期。


def _candidate(ts_code: str, raw: dict) -> Candidate:
    """构造一个仅供 exec_hint 测试用的最小 Candidate(其余字段无关本模块判据)。"""
    return Candidate(
        ts_code=ts_code, name=ts_code, close=raw.get("close", 10.0), score=1.0, rank=1,
        board="MAIN", pattern_tags=[], hot_sectors=[], sector_names=[],
        entry_plan="", stop_loss="", target="", invalidation_text="", invalidation_spec={},
        raw=raw,
    )


def _seed_k4(db_path, exec_hint: dict) -> None:
    from neckline.strategy import brain

    brain.save_version(
        "K4", rule={"config": {}, "k4_advisory": {"exec_hint": exec_hint}},
        changelog="test K4 exec_hint", activate=False, db_path=db_path,
    )


# ————————————————————————————————————————————————————————————————
# 1) DB 读取(单一事实源,不抄常量;缺读兜底不崩;部分命中部分兜底)
# ————————————————————————————————————————————————————————————————

def test_load_k4_exec_hint_texts_reads_db(isolated_env):
    _seed_k4(isolated_env.db_path, {eh.C1_STRONG_MARKET_ORDER: "自定义C1文案XYZ"})
    texts = eh._load_k4_exec_hint_texts(isolated_env.db_path)
    assert texts == {eh.C1_STRONG_MARKET_ORDER: "自定义C1文案XYZ"}


def test_load_k4_exec_hint_texts_empty_when_no_k4_row(isolated_env):
    """隔离库无 K4 行 → 空 dict,不抛异常(调用方逐码 fallback)。"""
    assert eh._load_k4_exec_hint_texts(isolated_env.db_path) == {}


def test_hint_text_and_source_db_hit_vs_fallback(isolated_env):
    db_texts = {eh.C1_STRONG_MARKET_ORDER: "自定义C1"}
    text, source = eh._hint_text_and_source(eh.C1_STRONG_MARKET_ORDER, db_texts)
    assert (text, source) == ("自定义C1", "db")
    text2, source2 = eh._hint_text_and_source(eh.C4_NO_PULLBACK_BIGRED_MECHANICAL, db_texts)
    assert source2 == "fallback"
    assert text2 == eh._FALLBACK_HINT_TEXT[eh.C4_NO_PULLBACK_BIGRED_MECHANICAL]


def test_output_text_changes_when_db_text_changes(isolated_env):
    """验收硬要求:「改 DB 文字 → 输出跟着变」。"""
    _seed_k4(isolated_env.db_path, {eh.C1_STRONG_MARKET_ORDER: "版本一"})
    cand = _candidate("600001.SH", {"ret_1d": 0.06, "is_limit_up": False})
    eh.attach_exec_hints([cand], _TODAY, db_path=isolated_env.db_path)
    hit = next(h for h in cand.exec_hints if h["code"] == eh.C1_STRONG_MARKET_ORDER)
    assert hit["text"] == "版本一" and hit["source"] == "db"

    _seed_k4(isolated_env.db_path, {eh.C1_STRONG_MARKET_ORDER: "版本二·已更新"})
    cand2 = _candidate("600001.SH", {"ret_1d": 0.06, "is_limit_up": False})
    eh.attach_exec_hints([cand2], _TODAY, db_path=isolated_env.db_path)
    hit2 = next(h for h in cand2.exec_hints if h["code"] == eh.C1_STRONG_MARKET_ORDER)
    assert hit2["text"] == "版本二·已更新" and hit2["source"] == "db"


# ————————————————————————————————————————————————————————————————
# 2) 四条触发镜像(逐条独立单测)
# ————————————————————————————————————————————————————————————————

class TestC1StrongMarketOrder:
    def test_triggers_on_ret_1d_ge_5pct(self):
        assert eh._hit_c1({"ret_1d": 0.05, "is_limit_up": False}) is True
        assert eh._hit_c1({"ret_1d": 0.08, "is_limit_up": False}) is True

    def test_triggers_on_limit_up_even_if_ret_below_5pct(self):
        """涨停但 ret_1d 因故未达 5%(数据边界)时仍应触发——涨停本身就是"or"条件。"""
        assert eh._hit_c1({"ret_1d": 0.03, "is_limit_up": True}) is True

    def test_does_not_trigger_below_threshold(self):
        assert eh._hit_c1({"ret_1d": 0.049, "is_limit_up": False}) is False

    def test_no_row_does_not_trigger(self):
        assert eh._hit_c1(None) is False
        assert eh._hit_c1({}) is False


class TestC2MildRedLowVariance:
    def test_triggers_within_mild_band(self):
        assert eh._hit_c2({"ret_1d": 0.02}) is True
        assert eh._hit_c2({"ret_1d": 0.025}) is True
        assert eh._hit_c2({"ret_1d": 0.03}) is True   # 含右端点(is_mild_band 闭区间)

    def test_does_not_trigger_outside_band(self):
        assert eh._hit_c2({"ret_1d": 0.019}) is False
        assert eh._hit_c2({"ret_1d": 0.031}) is False
        assert eh._hit_c2({"ret_1d": 0.06}) is False

    def test_no_row_does_not_trigger(self):
        assert eh._hit_c2(None) is False

    def test_reuses_info_card_mild_band_range_not_reopened(self):
        """C2 复用 ④ 已定的 `info_card.MILD_BAND_RANGE`,不重开一份阈值——直接断言
        exec_hint 模块没有自己的 `_MILD_BAND`/`MILD_BAND_RANGE` 常量。"""
        assert not hasattr(eh, "MILD_BAND_RANGE")
        assert not hasattr(eh, "_MILD_BAND")


class TestC3LowLimitSelfAware:
    def test_no_decision_does_not_trigger(self):
        assert eh._hit_c3({"pre_close": 10.0}, None) is False

    def test_triggers_on_max_chase_pct_zero_or_negative(self, isolated_env):
        row = create_decision(
            ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=0.0,
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({"pre_close": 10.0}, row) is True
        row2 = create_decision(
            ts_code="600002.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-1.0,
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({"pre_close": 10.0}, row2) is True

    def test_triggers_on_planned_price_below_pre_close(self, isolated_env):
        row = create_decision(
            ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", planned_price=9.0,
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({"pre_close": 10.0}, row) is True

    def test_does_not_trigger_when_planned_price_at_or_above_pre_close(self, isolated_env):
        row = create_decision(
            ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", planned_price=10.0, max_chase_pct=3.0,
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({"pre_close": 10.0}, row) is False

    def test_does_not_trigger_when_both_fields_none(self, isolated_env):
        row = create_decision(
            ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE",
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({"pre_close": 10.0}, row) is False

    def test_missing_pre_close_falls_back_to_max_chase_pct_only(self, isolated_env):
        """`row` 无 `pre_close`(如当日无 EOD 行)时,`planned_price` 分支判不了,但
        `max_chase_pct` 分支不依赖价格数据,仍应独立生效。"""
        row = create_decision(
            ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
            thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-2.0,
            db_path=isolated_env.db_path,
        )
        assert eh._hit_c3({}, row) is True
        assert eh._hit_c3(None, row) is True


class TestC4NoPullbackBigredMechanical:
    def test_triggers_when_above_ma20_and_big_red(self):
        assert eh._hit_c4({"close": 11.0, "ma20": 10.0, "ret_1d": 0.05}) is True
        assert eh._hit_c4({"close": 11.0, "ma20": 10.0, "ret_1d": 0.08}) is True

    def test_does_not_trigger_below_ma20(self):
        assert eh._hit_c4({"close": 9.0, "ma20": 10.0, "ret_1d": 0.06}) is False

    def test_does_not_trigger_below_ret_threshold(self):
        assert eh._hit_c4({"close": 11.0, "ma20": 10.0, "ret_1d": 0.03}) is False

    def test_missing_data_does_not_trigger(self):
        assert eh._hit_c4({"close": 11.0, "ma20": None, "ret_1d": 0.06}) is False
        assert eh._hit_c4({}) is False
        assert eh._hit_c4(None) is False


class TestEvaluateCodesOrderingAndCoexistence:
    def test_c1_and_c4_can_coexist_fixed_order(self):
        """C1(强票市价)与 C4(回调大红机械层不做)概念独立、可同时成立(见模块头
        docstring);返回顺序固定 C1→C4,不依赖字典迭代顺序。"""
        row = {"ret_1d": 0.06, "is_limit_up": False, "close": 11.0, "ma20": 10.0}
        codes = eh._evaluate_codes(row, None)
        assert codes == [eh.C1_STRONG_MARKET_ORDER, eh.C4_NO_PULLBACK_BIGRED_MECHANICAL]

    def test_no_hits_returns_empty_list(self):
        row = {"ret_1d": 0.0, "is_limit_up": False, "close": 10.0, "ma20": 10.0, "pre_close": 10.0}
        assert eh._evaluate_codes(row, None) == []

    def test_c1_and_c2_mutually_exclusive_by_construction(self):
        """C1(ret_1d≥5%)与 C2(ret_1d∈[2%,3%])定义域不重叠,不需要额外互斥逻辑,
        这里只是确认这一自然结果(回归防呆)。"""
        strong = eh._evaluate_codes({"ret_1d": 0.06, "is_limit_up": False}, None)
        mild = eh._evaluate_codes({"ret_1d": 0.025, "is_limit_up": False}, None)
        assert eh.C2_MILD_RED_LOW_VARIANCE not in strong
        assert eh.C1_STRONG_MARKET_ORDER not in mild


# ————————————————————————————————————————————————————————————————
# 3) attach_exec_hints 端到端装配(零额外 parquet 读取,原地补 Candidate.exec_hints)
# ————————————————————————————————————————————————————————————————

def test_attach_exec_hints_writes_public_dicts_onto_candidates(isolated_env):
    cand = _candidate("600001.SH", {"ret_1d": 0.06, "is_limit_up": False, "close": 11.0, "ma20": 9.0})
    eh.attach_exec_hints([cand], _TODAY, db_path=isolated_env.db_path)
    codes = {h["code"] for h in cand.exec_hints}
    assert eh.C1_STRONG_MARKET_ORDER in codes
    assert eh.C4_NO_PULLBACK_BIGRED_MECHANICAL in codes
    for h in cand.exec_hints:
        assert set(h.keys()) == {"code", "text", "source"}
        assert h["source"] == "fallback"   # 隔离库无 K4 行


def test_attach_exec_hints_no_trigger_leaves_empty_list(isolated_env):
    cand = _candidate("600001.SH", {"ret_1d": -0.01, "is_limit_up": False, "close": 9.9, "ma20": 10.0})
    eh.attach_exec_hints([cand], _TODAY, db_path=isolated_env.db_path)
    assert cand.exec_hints == []


def test_attach_exec_hints_k4_row_missing_does_not_crash(isolated_env):
    """DB 缺该节(K4 行本就不存在,隔离库默认态)→ 镜像照跑不崩,全部落到
    `_FALLBACK_HINT_TEXT`。"""
    cand = _candidate("600001.SH", {"ret_1d": 0.07, "is_limit_up": False})
    eh.attach_exec_hints([cand], _TODAY, db_path=isolated_env.db_path)
    hit = next(h for h in cand.exec_hints if h["code"] == eh.C1_STRONG_MARKET_ORDER)
    assert hit["source"] == "fallback"
    assert hit["text"] == eh._FALLBACK_HINT_TEXT[eh.C1_STRONG_MARKET_ORDER]


def test_attach_exec_hints_c3_uses_latest_decision_for_that_code(isolated_env):
    """C3 与 ⑤-B 联动:该票关联决策日志的 `max_chase_pct`/`planned_price` 决定是否
    触发,且只看**最近一条**(第二条覆盖第一条的判定结果)。"""
    create_decision(
        ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
        thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=3.0,   # 不触发
        db_path=isolated_env.db_path,
    )
    cand = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand], _TODAY, db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE not in {h["code"] for h in cand.exec_hints}

    # 追加最近一条 max_chase_pct<=0 → 应改为触发(最近一条覆盖判定)
    create_decision(
        ts_code="600001.SH", why_buy="y", why_entry_price="y", invalidation="y",
        thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-1.0,
        db_path=isolated_env.db_path,
    )
    cand2 = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand2], _TODAY, db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE in {h["code"] for h in cand2.exec_hints}


def test_attach_exec_hints_c3_does_not_look_ahead_past_trade_date(isolated_env):
    """无前视偏差铁律(§3.8):重新生成一个**历史**交易日的报告时,不能捞到那天
    **之后**才创建的决策日志——否则历史回放会用到当时根本不存在的未来信息。

    `created_at` 直接传给 fixture(v2.0.0 起 `decision_log` 停写留档,已无
    `_now()` 可 monkeypatch——写死时间戳的效果与打桩 `_now()` 逐位相同)。"""
    create_decision(
        ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
        thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-1.0,   # 会触发 C3
        created_at="2026-07-25T09:00:00+00:00",
        db_path=isolated_env.db_path,
    )

    historical_trade_date = date(2026, 7, 20)   # 早于决策创建日(07-25)
    cand = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand], historical_trade_date, db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE not in {h["code"] for h in cand.exec_hints}

    # 同一份决策日志,report 日期改到决策创建日当天或之后 → 应正常触发(证明截断
    # 只挡"未来",不误伤"当天或更晚")。
    on_or_after = date(2026, 7, 25)
    cand2 = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand2], on_or_after, db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE in {h["code"] for h in cand2.exec_hints}


def test_attach_exec_hints_c3_truncation_uses_beijing_date_not_utc(isolated_env):
    """**v1.4 review 契约线 🟡-2(时区缝)**:`created_at` 落库是 UTC,而截断日是**交易日**
    (北京日)。北京 **T+1 00:00–07:59**(= UTC T 日 16:00–23:59)创建的决策,UTC 日期还停
    在 T —— 从前 T 日回放看得见它,等于读到了"当时还不存在"的决策。

    造法:决策创建于 UTC 2026-07-20T23:30(= **北京 07-21 07:30**,盘前预注册的现实时段)。
      · 回放 **07-20**(北京日)→ **不可见**(这条断言在修复前是红的:UTC 日期正是 07-20);
      · 回放 **07-21**(北京日)→ 可见(截断只挡未来,不误伤当天)。"""
    create_decision(
        ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
        thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=-1.0,   # 会触发 C3
        created_at="2026-07-20T23:30:00+00:00",
        db_path=isolated_env.db_path,
    )
    cand = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand], date(2026, 7, 20), db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE not in {h["code"] for h in cand.exec_hints}, \
        "北京 T+1 凌晨创建的决策在 T 日回放里可见 = 前视偏差(UTC/北京时区缝)"

    cand2 = _candidate("600001.SH", {"ret_1d": 0.0, "pre_close": 10.0})
    eh.attach_exec_hints([cand2], date(2026, 7, 21), db_path=isolated_env.db_path)
    assert eh.C3_LOW_LIMIT_SELF_AWARE in {h["code"] for h in cand2.exec_hints}


def test_list_decisions_date_filter_is_beijing_day(isolated_env):
    """同一条缝也在 `GET /decisions` 的 `from`/`to` 上(v1.2 起既有行为)——一并按北京日
    过滤。UTC 23:30 = 北京次日 07:30:该行属**次日**,`to=当日` 不应命中、`from=次日` 应命中。"""
    import neckline.decision_log as dl_mod

    row = create_decision(
        ts_code="600001.SH", why_buy="x", why_entry_price="x", invalidation="x",
        thesis_tags=[], playbook_tag="SWING_CHASE", max_chase_pct=None,
        created_at="2026-07-20T23:30:00+00:00",
        db_path=isolated_env.db_path,
    )
    assert dl_mod.created_at_cn_date(row.created_at) == "2026-07-21"
    ids = lambda **kw: [d.id for d in dl_mod.list_decisions(db_path=isolated_env.db_path, **kw)]
    assert ids(date_to="20260720") == []
    assert ids(date_from="20260721") == [row.id]
    assert ids(date_from="20260721", date_to="20260721") == [row.id]
    assert ids(date_from="20260720", date_to="20260720") == []


def test_attach_exec_hints_multiple_candidates_independent(isolated_env):
    strong = _candidate("600001.SH", {"ret_1d": 0.06, "is_limit_up": False})
    flat = _candidate("600002.SH", {"ret_1d": 0.0, "is_limit_up": False})
    eh.attach_exec_hints([strong, flat], _TODAY, db_path=isolated_env.db_path)
    assert eh.C1_STRONG_MARKET_ORDER in {h["code"] for h in strong.exec_hints}
    assert flat.exec_hints == []


# ————————————————————————————————————————————————————————————————
# 4) 语义红线自查(§硬要求:文案不含"建议买入"类表述)
# ————————————————————————————————————————————————————————————————

def test_fallback_texts_contain_no_forbidden_phrasing():
    forbidden = ("推荐买入", "建议买入", "看好", "值得买", "推荐买点")
    for code, text in eh._FALLBACK_HINT_TEXT.items():
        for phrase in forbidden:
            assert phrase not in text, f"{code} 兜底文案含违禁表述「{phrase}」:{text}"
