"""解释层与预案层(V2.5.0 S9/S10,PROJECT_PLAN §6 S9/S10 验收 + §5.5/§5.6)。

| # | 验收 | section |
|---|---|---|
| 1 | 输入按 `ts_code` 升序(位次不从列表顺序泄漏) | ① |
| 2 | 消息面三态;`unverified` ⛔ 不许折成 clean / excluded | ② |
| 3 | 剔除 → 补位 → 定稿全流程;每步进审计;补到目标数量或后备池耗尽 | ③ |
| 4 | 四骨架逐条对上 K9 §6.3;骨架机械、数值 LLM | ④ |
| 5 | LLM 返回带自由文本评价键 → 拒绝;缺键(空成功)→ 拒绝;未知 MetricRef → 拒绝冻结 | ⑤ |
| 6 | 用户修改产生**新版本**、原版本不变 | ⑥ |

结构性守门(字段集冻结 / AST)见 `test_v250_s9_s10_guard.py`。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from neckline.explain import aggregate as explain_aggregate
from neckline.explain import input as explain_input
from neckline.explain import news_exclusion as news_mod
from neckline.explain import store as explain_store
from neckline.k9 import run as k9_run
from neckline.k9 import store as k9_store
from neckline.llm.base import LLMResult
from neckline.llm.news_scan import (
    CATEGORY_BLOWUP,
    CATEGORY_REDUCTION,
    NewsScanResult,
)
from neckline.playbook import fill as playbook_fill
from neckline.playbook import skeleton as skeleton_mod
from neckline.playbook import store as pb_store
from neckline.playbook.model import (
    Bar,
    MetricRef,
    Op,
    PlaybookInput,
    PlaybookInvalid,
)
from neckline.report import evening as evening_mod
from tests import k9_env


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

class FakeProvider:
    """一个**可编排**的假 provider:按调用次序吐出预置内容。"""

    name = "fake"
    model = "fake-1"

    def __init__(self, contents: List[str]) -> None:
        self._contents = list(contents)
        self.calls: List[Any] = []

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls.append(messages)
        content = self._contents.pop(0) if self._contents else self._contents_fallback()
        return LLMResult(ok=True, content=content, provider=self.name, model=self.model)

    def _contents_fallback(self) -> str:
        return ""


def _explain_content(**over: str) -> str:
    block = {"company": "一家示例公司", "industryContext": "行业中位偏强",
             "position": "位于近期平台上沿", "recent": "近五日震荡走高",
             "klineComment": "小阳线连续,量能温和"}
    block.update(over)
    return "这是一段自由叙述。\n\n```json\n" + json.dumps(block, ensure_ascii=False) + "\n```"


def _fill_content(pattern: str = "p1", **over: Any) -> str:
    values: Dict[str, Any] = {"firstResistance": 11.0, "secondResistance": 12.0,
                              "invalidation": 9.5}
    if pattern == "p1":
        values.update({"maxGapUpPct": 5.0, "first30FloorPrice": 10.2,
                       "rejectPrice": 9.8})
    elif pattern == "p2":
        values.update({"minOpenPrice": 10.0, "rejectPrice": 9.6})
    elif pattern == "p3":
        values.update({"maxGapUpPct": 5.0, "first30FloorPrice": 10.2,
                       "rejectPrice": 9.8})
    else:
        values.update({"first30FloorPrice": 10.2, "rejectPrice": 9.8})
    values.update(over)
    return "看图说明。\n\n```json\n" + json.dumps(values, ensure_ascii=False) + "\n```"


@pytest.fixture
def listed(isolated_env, tmp_path):
    """铺好市场 + 跑一遍策略层,拿到一份非空清单 **外加后备票**。

    ⚠ `quota.max=3` 是为了让合成市场的 5 个候选里有 2 只落到 `reserve`
    —— 后备补位没有后备票就测不了。**这是夹具条件,⛔ 不是标定值。**"""
    day = k9_env.seed(isolated_env)
    params = k9_env.params(isolated_env, tmp_path)
    # 生产参数入口不允许改 B17 固定配额；这里直接构造一个小容量对象，只为制造
    # 后备票来测解释层补位算法，不是在测试参数包能否接受另一个配额。
    params = replace(params, quota=replace(params.quota, min=1, max=2))
    result, run_id = k9_run.run_k9(day, params=params,
                                   parquet_dir=isolated_env.parquet_dir,
                                   db_path=isolated_env.db_path)
    return isolated_env, day, result, run_id, params


# ══════════════════════════════════════════════════════════════════════════
# ① 双盲:输入按 `ts_code` 升序
# ══════════════════════════════════════════════════════════════════════════

class TestExplainInput:
    def test_inputs_come_back_sorted_regardless_of_the_order_given(self, listed):
        """🔴 **排序位次会从列表顺序泄漏**(§5.2 边界③ 第 3 条)——
        交给解释层的序列一律按 `ts_code` 升序,乱序传入也拿到同一个序列。"""
        env, day, result, _run_id, _p = listed
        codes = [e.ts_code for e in result.shortlist.entries]
        a = explain_input.build_inputs(day, codes, sessions=5,
                                       parquet_dir=env.parquet_dir, db_path=env.db_path)
        b = explain_input.build_inputs(day, list(reversed(codes)), sessions=5,
                                       parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert [i.ts_code for i in a] == sorted(codes)
        assert [i.ts_code for i in a] == [i.ts_code for i in b]

    def test_input_carries_facts_not_strategy_products(self, listed):
        env, day, result, _run_id, _p = listed
        items = explain_input.build_inputs(
            day, [result.shortlist.entries[0].ts_code], sessions=5,
            parquet_dir=env.parquet_dir, db_path=env.db_path)
        it = items[0]
        assert it.close is not None and it.sw_l2_name
        assert len(it.bars) == 5 and isinstance(it.bars[0], Bar)

    def test_a_missing_frozen_pack_costs_one_bar_not_a_crash(self, listed):
        """某天没冻结 → 少一根 K 线,⛔ 不补一根近似的、也不崩。"""
        env, day, result, _run_id, _p = listed
        items = explain_input.build_inputs(
            day, [result.shortlist.entries[0].ts_code], sessions=500,
            parquet_dir=env.parquet_dir, db_path=env.db_path)
        # 合成市场只有 70 个交易日 —— 要 500 根就只能拿到实际有的那些。
        assert 0 < len(items[0].bars) <= 70


# ══════════════════════════════════════════════════════════════════════════
# ② 消息面三态
# ══════════════════════════════════════════════════════════════════════════

class TestNewsExclusion:
    def test_four_categories_exactly(self):
        """K9 §二 末段逐字:爆雷 / 减持 / 立案 / 监管。"""
        assert {news_mod.CATEGORY_LABEL[c] for c in news_mod.NewsCategory} == {
            "爆雷", "减持", "立案", "监管"}
        assert len(news_mod.NewsCategory) == 4

    def test_a_hit_excludes(self):
        def _scan(ts_code, name, *, provider, transport=None):
            return NewsScanResult(ts_code=ts_code, provider="p", model="m",
                                  hits=[(CATEGORY_BLOWUP, "计提大额减值")])
        v = news_mod.screen([("600001.SH", "甲")], provider=object(), scan_fn=_scan)[0]
        assert v.state is news_mod.NewsState.EXCLUDED
        assert v.category is news_mod.NewsCategory.BLOWUP and v.excluded

    def test_reduction_is_a_real_category_now(self):
        def _scan(ts_code, name, *, provider, transport=None):
            return NewsScanResult(ts_code=ts_code, provider="p", model="m",
                                  hits=[(CATEGORY_REDUCTION, "实控人拟减持 2%")])
        v = news_mod.screen([("600001.SH", "甲")], provider=object(), scan_fn=_scan)[0]
        assert v.category is news_mod.NewsCategory.REDUCTION and v.excluded

    def test_degraded_scan_is_unverified_not_clean(self):
        """🔴 **没查成 ⛔ 不许当成干净** —— 那是「没看」冒充「看过了没事」。"""
        def _scan(ts_code, name, *, provider, transport=None):
            return NewsScanResult(ts_code=ts_code, provider="p", model="m",
                                  degraded=True, degrade_reason="调用超时")
        v = news_mod.screen([("600001.SH", "甲")], provider=object(), scan_fn=_scan)[0]
        assert v.state is news_mod.NewsState.UNVERIFIED
        assert v.excluded is False              # ⛔ 也不许当成命中
        assert "超时" in v.reason

    def test_no_provider_means_unverified_for_everyone(self):
        vs = news_mod.screen([("600001.SH", "甲"), ("600002.SH", "乙")], provider=None)
        assert all(v.state is news_mod.NewsState.UNVERIFIED for v in vs)
        assert news_mod.summarize(vs) == {"clean": 0, "excluded": 0, "unverified": 2}

    def test_one_stock_blowing_up_never_takes_down_the_batch(self):
        def _scan(ts_code, name, *, provider, transport=None):
            if ts_code == "600001.SH":
                raise RuntimeError("boom")
            return NewsScanResult(ts_code=ts_code, provider="p", model="m", hits=[])
        vs = news_mod.screen([("600001.SH", "甲"), ("600002.SH", "乙")],
                             provider=object(), scan_fn=_scan)
        assert vs[0].state is news_mod.NewsState.UNVERIFIED
        assert vs[1].state is news_mod.NewsState.CLEAN

    def test_screen_is_three_wide_ordered_and_audited_once_per_ticket(self, monkeypatch, tmp_path):
        """P2：联网 worker 固定三并发；异常隔离，审计仍在主线程一票一次。"""
        running = 0
        peak = 0
        calls: List[str] = []
        lock = threading.Lock()
        recorded: List[str] = []

        def _scan(ts_code, name, *, provider, record_usage=True, **_kwargs):
            nonlocal running, peak
            assert record_usage is False
            with lock:
                calls.append(ts_code)
                running += 1
                peak = max(peak, running)
            try:
                time.sleep(0.02)
                if ts_code == "600003.SH":
                    raise RuntimeError("one ticket only")
                return NewsScanResult(
                    ts_code=ts_code, provider="p", model="m", hits=[],
                    usage_result=LLMResult(ok=True, provider="p", model="m",
                                           tavily_credits=1), usage_duration_ms=20)
            finally:
                with lock:
                    running -= 1

        monkeypatch.setattr(news_mod, "scan_news_for_code", _scan)
        monkeypatch.setattr("neckline.llm.usage.record",
                            lambda **kw: recorded.append(kw["result"].provider if kw.get("result") else kw["outcome"]))
        items = [(f"60000{i}.SH", str(i)) for i in range(1, 7)]
        verdicts = news_mod.screen(items, provider=object(), db_path=tmp_path / "audit.db")

        assert [v.ts_code for v in verdicts] == [code for code, _ in items]
        assert verdicts[2].state is news_mod.NewsState.UNVERIFIED
        assert sorted(calls) == [code for code, _ in items]
        assert 1 < peak <= 3
        assert len(recorded) == len(items)


# ══════════════════════════════════════════════════════════════════════════
# ③ 剔除 → 补位 → 定稿
# ══════════════════════════════════════════════════════════════════════════

class TestBackfillOrchestration:
    def _run(self, listed, *, hit_codes, monkeypatch=None):
        env, day, result, run_id, params = listed
        hits = set(hit_codes)
        seen: List[str] = []

        def _scan(ts_code, name, *, provider, transport=None):
            seen.append(ts_code)
            if ts_code in hits:
                return NewsScanResult(ts_code=ts_code, provider="p", model="m",
                                      hits=[(CATEGORY_BLOWUP, "示例利空")])
            return NewsScanResult(ts_code=ts_code, provider="p", model="m", hits=[])

        import neckline.explain.news_exclusion as nm
        monkeypatch.setattr(nm, "scan_news_for_code", _scan)
        monkeypatch.setattr("neckline.llm.factory.get_provider",
                            lambda task=None, **kw: object())
        handoff = evening_mod._K9Handoff(result=result, run_id=run_id, params=params)
        status, stats = evening_mod._run_explain(
            day, result=handoff, db_path=env.db_path, parquet_dir=env.parquet_dir,
            provider=FakeProvider([_explain_content()] * 40))
        return env, day, status, stats, seen

    def test_exclusion_backfill_and_finalisation(self, listed, monkeypatch):
        """剔除 → 补位 → 定稿全流程,每一步进审计。"""
        env, day, result, _run_id, _p = listed
        victim = result.shortlist.entries[0].ts_code
        assert result.shortlist.reserve, "夹具得有后备票"
        backup = result.shortlist.reserve[0].ts_code
        env, day, status, stats, _seen = self._run(
            listed, hit_codes=[victim], monkeypatch=monkeypatch)
        assert status == evening_mod.STATUS_OK
        assert stats["excluded"] == 1 and stats["backfilled"] == 1

        codes = k9_store.load_listing_codes(day, db_path=env.db_path)
        assert victim not in codes and backup in codes

        audit = explain_store.load_audit(day, db_path=env.db_path)
        actions = [(a["action"], a["ts_code"]) for a in audit]
        assert ("excluded", victim) in actions
        assert ("backfilled", backup) in actions
        # 🔴 定稿归解释层。
        run = k9_store.load_run(day, db_path=env.db_path)
        assert run["listing_finalized_by"] == k9_store.FINALIZED_BY_EXPLAIN
        assert run["seated_count"] == len(codes)

    def test_excluded_stock_is_marked_in_the_disposition(self, listed, monkeypatch):
        """覆盖率归因要拿到「被消息面剔除」那一档答案(§5.4.8 的 `news_excluded`)。"""
        env, day, result, _run_id, _p = listed
        victim = result.shortlist.entries[0].ts_code
        env, day, _s, _st, _seen = self._run(listed, hit_codes=[victim],
                                             monkeypatch=monkeypatch)
        frame = k9_store.load_disposition(day, parquet_dir=env.parquet_dir)
        row = frame.filter(frame["ts_code"] == victim).to_dicts()[0]
        assert row["news_excluded"] == 1 and row["seated"] == 0

    def test_backfill_continues_until_the_original_target_is_restored(
            self, listed, monkeypatch):
        """第一只补位仍被剔除时，必须继续取下一名，不能按轮数截断。"""
        env, day, result, _run_id, _p = listed
        reserve = list(result.shortlist.reserve)
        assert len(reserve) >= 2, "夹具至少需要两只后备票来证明跨轮补位"
        original_size = len(result.shortlist.entries)
        victim = result.shortlist.entries[0].ts_code
        hit = [victim, reserve[0].ts_code]
        env, day, status, stats, _seen = self._run(
            listed, hit_codes=hit, monkeypatch=monkeypatch)
        assert status == evening_mod.STATUS_OK
        assert stats["roundsUsed"] == 2
        assert stats["backfilled"] == 2
        codes = set(k9_store.load_listing_codes(day, db_path=env.db_path))
        assert len(codes) == original_size
        assert reserve[1].ts_code in codes
        assert not ({victim, reserve[0].ts_code} & codes)

    def test_listing_shrinks_only_after_the_reserve_is_exhausted(self, listed, monkeypatch):
        """后备池每只都命中排除项时才允许如实缩短，且所有后备票都必须被筛查。"""
        env, day, result, _run_id, _p = listed
        original_size = len(result.shortlist.entries)
        victim = result.shortlist.entries[0].ts_code
        reserve_codes = [e.ts_code for e in result.shortlist.reserve]
        assert reserve_codes
        env, day, status, stats, seen = self._run(
            listed, hit_codes=[victim, *reserve_codes], monkeypatch=monkeypatch)
        assert status == evening_mod.STATUS_OK
        assert stats["backfilled"] == len(reserve_codes)
        assert stats["roundsUsed"] == len(reserve_codes)
        codes = k9_store.load_listing_codes(day, db_path=env.db_path)
        assert len(codes) == original_size - 1
        assert set(reserve_codes) <= set(seen)
        assert victim not in codes and not (set(reserve_codes) & set(codes))

    def test_everything_on_the_final_listing_was_actually_screened(
            self, listed, monkeypatch):
        """🔴 上限**⛔ 不许**把一只没过消息面的票留在清单上。

        「把 break 挪到补位之后」是最直觉的改法,但它会在补位之后立刻收工 ——
        补进来那几只的爆雷 / 减持 / 立案 / 监管**没有人查过**,却带着空的
        `news_state` 进了清单。那是这一层存在的全部意义的反面。
        """
        env, day, result, _run_id, _p = listed
        victim = result.shortlist.entries[0].ts_code
        env, day, _status, _stats, seen = self._run(
            listed, hit_codes=[victim], monkeypatch=monkeypatch)
        codes = set(k9_store.load_listing_codes(day, db_path=env.db_path))
        assert codes <= set(seen), (
            f"这几只没过消息面就进了清单:{sorted(codes - set(seen))}")
        notes = explain_store.load_notes(day, db_path=env.db_path)
        assert codes <= set(notes)
        assert all(notes[c]["news_state"] for c in codes)

    def test_explain_refuses_to_guess_a_reserve_list(self, listed):
        """分段跑(本进程没跑过策略层)→ 如实报「拿不到本日策略层产物」,
        ⛔ 不去猜一个后备名单出来。"""
        env, day, _r, _run_id, _p = listed
        status, stats = evening_mod._run_explain(
            day, result=None, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert status == evening_mod.STATUS_EMPTY
        assert stats["reason"] == "no_k9_result"


# ══════════════════════════════════════════════════════════════════════════
# ④ 四骨架(K9 §6.3 逐条)
# ══════════════════════════════════════════════════════════════════════════

class TestSkeletons:
    def test_p1_matches_k9_63(self):
        """成立:高开幅度 ≤ [A]% 且 前 30 分钟最低价 ≥ [B];放弃:跌破 [C]。"""
        confirm, reject = skeleton_mod.skeleton_for("p1").build(
            {"maxGapUpPct": 5.0, "first30FloorPrice": 10.2, "rejectPrice": 9.8})
        assert [(c.lhs, c.op, c.rhs) for c in confirm.all] == [
            (MetricRef.GAP_PCT, Op.LE, 5.0),
            (MetricRef.FIRST30_LOW, Op.GE, 10.2)]
        assert [(c.lhs, c.op, c.rhs) for c in reject.all] == [
            (MetricRef.FIRST30_LOW, Op.LT, 9.8)]

    def test_p2_never_asks_the_llm_for_yesterdays_low(self):
        """「前 30 分钟不创昨日新低」的右边是**另一个 MetricRef**,零 LLM。"""
        confirm, _reject = skeleton_mod.skeleton_for("p2").build(
            {"minOpenPrice": 10.0, "rejectPrice": 9.6})
        assert confirm.all[1].rhs is MetricRef.PREV_LOW
        assert "prevLow" not in skeleton_mod.required_keys("p2")

    def test_p3_has_its_own_hot_contest_skeleton(self):
        """P3 成立同时检查透支线与强博弈关键位；放弃检查结构失效位。"""
        confirm, reject = skeleton_mod.skeleton_for("p3").build(
            {"maxGapUpPct": 7.0, "first30FloorPrice": 10.0, "rejectPrice": 9.5})
        assert [(c.lhs, c.op, c.rhs) for c in confirm.all] == [
            (MetricRef.GAP_PCT, Op.LE, 7.0),
            (MetricRef.FIRST30_LOW, Op.GE, 10.0),
        ]
        assert [(c.lhs, c.op, c.rhs) for c in reject.all] == [
            (MetricRef.FIRST30_LOW, Op.LT, 9.5),
        ]

    def test_p4_ambush_confirmation_never_demands_strength(self):
        """P4 的 D1 成立只要求资金进场后价格不破位。"""
        confirm, _ = skeleton_mod.skeleton_for("p4").build(
            {"first30FloorPrice": 10.0, "rejectPrice": 9.5})
        assert all(c.lhs is MetricRef.FIRST30_LOW for c in confirm.all)

    def test_a_missing_slot_is_refused(self):
        with pytest.raises(PlaybookInvalid, match="缺槽位"):
            skeleton_mod.skeleton_for("p1").build({"maxGapUpPct": 5.0})

    def test_an_unknown_pattern_has_no_skeleton(self):
        with pytest.raises(PlaybookInvalid):
            skeleton_mod.skeleton_for("p9")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 填值的严格校验
# ══════════════════════════════════════════════════════════════════════════

def _pb_input(pattern: str = "p1") -> PlaybookInput:
    return PlaybookInput(
        ts_code="600001.SH", name="示例甲", patterns=(pattern,),
        primary_pattern=pattern, sw_l2_name="半导体",
        close=10.0, prev_close=9.9, high=10.3, low=9.8,
        bars=(Bar(trade_date="20240429", open=9.9, high=10.3, low=9.8,
                  close=10.0, vol=1000.0),),
    )


class TestFillValidation:
    def test_free_text_evaluation_key_is_refused(self):
        """🔴 §5.2 边界④ 第 2 条:出现任何自由文本评价键 → **校验拒绝**
        (⛔ 不是「忽略多余的」—— 忽略等于默许它下次塞得更多)。"""
        content = _fill_content("p1")
        block = json.loads(content.split("```json")[1].split("```")[0])
        block["comment"] = "这只票很有想象空间"
        values, why = playbook_fill.validate_fill("p1", block)
        assert values == {} and "schema 之外的键" in why and "comment" in why

    def test_empty_success_is_a_failure(self):
        """🔴 §12 坑 13:模型返回了、但键没给全 → **判失败**,
        ⛔ 不冻结半份预案。"""
        values, why = playbook_fill.validate_fill(
            "p1", {"firstResistance": 11.0, "secondResistance": 12.0})
        assert values == {} and "缺数值键" in why

    def test_a_string_value_is_refused(self):
        block = json.loads(_fill_content("p1").split("```json")[1].split("```")[0])
        block["rejectPrice"] = "约 9.8 元"
        values, why = playbook_fill.validate_fill("p1", block)
        assert values == {} and "不是数值" in why

    def test_level_ordering_is_enforced_at_freeze_time(self):
        """失效位 < 第一压力位 < 第二压力位 —— 次序错了⛔ 不静默接受。"""
        res = playbook_fill.fill_one(
            _pb_input(), trade_date=date(2024, 4, 29),
            provider=FakeProvider([_fill_content("p1", invalidation=11.5)]))
        assert res.ok is False and "价位" in res.reason

    def test_a_good_fill_freezes_a_complete_playbook(self):
        res = playbook_fill.fill_one(
            _pb_input(), trade_date=date(2024, 4, 29),
            provider=FakeProvider([_fill_content("p1")]))
        assert res.ok and res.playbook is not None
        pb = res.playbook
        assert pb.levels.first_resistance == 11.0
        assert len(pb.branches) == 2
        assert pb.default == "观察"
        assert set(pb.metrics_used()) <= set(MetricRef)

    def test_no_provider_freezes_nothing(self):
        res = playbook_fill.fill_one(_pb_input(), trade_date=date(2024, 4, 29),
                                     provider=None)
        assert res.ok is False and res.playbook is None


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 用户修改 = 新版本,原版本不变
# ══════════════════════════════════════════════════════════════════════════

class TestUserEdit:
    #: 🔴 **冻结闸的窗口**(R2-03):`POST …/playbook` 只在 D0 收盘到 D1 零点之间开着。
    #: 这些用例改的是 `day` 这一天的预案,所以「今天」必须**就是 D0**。
    #: ⚠ 靠 monkeypatch `app._today()` 注入 —— ⛔ 端点上没有、也不许有可以从请求里
    #: 传日期的口子(那等于把闸门开在闸外面)。
    @pytest.fixture(autouse=True)
    def _pin_today_to_d0(self, monkeypatch):
        from neckline.api import app as app_mod

        monkeypatch.setattr(app_mod, "_today", lambda: date(2024, 4, 29))

    def _seed_listing(self, api_env, day: date) -> None:
        from neckline.k9.contract import Entry, Pattern, SeatKind, Shortlist, Tier

        entry = Entry(ts_code="600001.SH", name="示例甲", sw_l2_code="801080.SI",
                      sw_l2_name="半导体", patterns=(Pattern.P1,),
                      primary_pattern=Pattern.P1, tier=Tier.STRICT, rank=1,
                      seat_kind=SeatKind.FLOOR, score=0.9,
                      industry_heat_score=0.5, pattern_strength_score=0.8,
                      relay_score=0.0)
        sl = Shortlist(strategy="K9", strategy_version="K9-v2",
                       label_contract_version="d2-v1",
                       scoring_contract={"touchThresholdU": 0.03, "riskLineL": 0.03,
                                         "d1Reference": "open",
                                         "matchedBaseline": "industryMedian"},
                       params_version="v", pack_version="fp-3",
                       pack_id="pid", trade_date=day, entries=(entry,), reserve=(),
                       tier_used=Tier.STRICT, strict_candidates=1, relaxed_candidates=1,
                       channel_counts={}, capacity_short=False, absent_patterns=(),
                       dropped_by_heat_absent=())
        k9_store.save_run(run_id="r1", shortlist=sl, boundary_counts={},
                          over_strict=False, relaxed_streak=0,
                          listing_finalized_by=k9_store.FINALIZED_BY_EXPLAIN,
                          db_path=api_env.db_path)
        k9_store.save_listing(run_id="r1", shortlist=sl, db_path=api_env.db_path)

    def test_editing_appends_a_version_and_leaves_the_first_alone(
        self, api_env, client, AUTH,
    ):
        """🔴 K9 §6.4:用户改动写 **append-only 新版本**,⛔ 不覆盖原冻结版本。"""
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        first = playbook_fill.fill_one(
            _pb_input(), trade_date=day,
            provider=FakeProvider([_fill_content("p1")])).playbook
        pb_store.save(first, db_path=api_env.db_path)

        body = {"firstResistance": 11.5, "secondResistance": 12.5, "invalidation": 9.4,
                "maxGapUpPct": 3.0, "first30FloorPrice": 10.4, "rejectPrice": 9.7}
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH/playbook",
                        headers=AUTH, json=body)
        assert r.status_code == 200 and r.json()["version"] == 2
        assert r.json()["playbook"]["source"] == "user"

        versions = pb_store.load_versions(day, "600001.SH", db_path=api_env.db_path)
        assert [v.version for v in versions] == [1, 2]
        # 🔴 原版本一个字没动。
        assert versions[0].levels.first_resistance == 11.0
        assert versions[0].source == "llm"
        assert versions[1].levels.first_resistance == 11.5
        # 两拍读的是最新版。
        latest = pb_store.load_latest(day, db_path=api_env.db_path)["600001.SH"]
        assert latest.version == 2

    def test_editing_refuses_a_free_text_key(self, api_env, client, AUTH):
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        body = {"firstResistance": 11.5, "secondResistance": 12.5, "invalidation": 9.4,
                "maxGapUpPct": 3.0, "first30FloorPrice": 10.4, "rejectPrice": 9.7,
                "note": "我觉得还能再涨"}
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH/playbook",
                        headers=AUTH, json=body)
        assert r.status_code == 422 and "note" in r.json()["detail"]

    def test_editing_refuses_a_broken_level_ordering(self, api_env, client, AUTH):
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        body = {"firstResistance": 11.5, "secondResistance": 12.5, "invalidation": 12.0,
                "maxGapUpPct": 3.0, "first30FloorPrice": 10.4, "rejectPrice": 9.7}
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH/playbook",
                        headers=AUTH, json=body)
        assert r.status_code == 422

    def test_editing_a_stock_not_on_the_listing_is_404(self, api_env, client, AUTH):
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600999.SH/playbook",
                        headers=AUTH, json={})
        assert r.status_code == 404

    def test_editing_after_d1_has_started_is_refused_with_a_reason(
        self, api_env, client, AUTH, monkeypatch,
    ):
        """🔴 **R2-03 的第一道锁**:D1 一开始就不许再改这一天的预案。

        裁定 10 说「三分支判定的唯一权威是 10:00 结算拍」;若 D1 早上还能改预案,
        那一拍代入的就可以是一份**在看过竞价之后**才写下的条件 ——
        复审实测过 9:27 待观察 → 9:45 改版 → 10:01 `confirmed` 这条路径。

        ⛔ 拒绝必须**说出原因**(不是静默忽略:静默会让用户以为改成功了)。
        """
        from neckline.api import app as app_mod

        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        first = playbook_fill.fill_one(
            _pb_input(), trade_date=day,
            provider=FakeProvider([_fill_content("p1")])).playbook
        pb_store.save(first, db_path=api_env.db_path)

        body = {"firstResistance": 99.0, "secondResistance": 99.5, "invalidation": 1.0,
                "maxGapUpPct": 99.0, "first30FloorPrice": 0.1, "rejectPrice": 0.2}
        # —— D1 早上 9:45(复审 CE-5 那一刻)——
        monkeypatch.setattr(app_mod, "_today", lambda: date(2024, 4, 30))
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH/playbook",
                        headers=AUTH, json=body)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert "已在 D0 冻结" in detail and "2024-04-30" in detail
        # 🔴 **一个字都没写进去**(⛔ 不是「写了但不生效」)。
        assert [v.version for v in
                pb_store.load_versions(day, "600001.SH", db_path=api_env.db_path)] == [1]

    def test_editing_tomorrows_listing_is_still_allowed(self, api_env, client, AUTH):
        """⚠ 反向自检:闸门管的是「**今天要核对的那一份**」,⛔ 不是「预案从此不能改」。"""
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        first = playbook_fill.fill_one(
            _pb_input(), trade_date=day,
            provider=FakeProvider([_fill_content("p1")])).playbook
        pb_store.save(first, db_path=api_env.db_path)
        body = {"firstResistance": 11.5, "secondResistance": 12.5, "invalidation": 9.4,
                "maxGapUpPct": 3.0, "first30FloorPrice": 10.4, "rejectPrice": 9.7}
        r = client.post(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH/playbook",
                        headers=AUTH, json=body)
        assert r.status_code == 200 and r.json()["version"] == 2

    def test_stock_detail_reports_each_missing_piece_honestly(
        self, api_env, client, AUTH,
    ):
        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        r = client.get(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["explain"] is None          # 解释层没跑过 → null,⛔ 不是空壳
        assert body["playbook"] is None         # 没冻预案 → null
        assert body["entry"]["primaryPattern"] == "p1"

    def test_the_slots_to_edit_come_from_the_server_never_from_the_client(
        self, api_env, client, AUTH,
    ):
        """🔴 **V2.5.0 S12**:“改预案要填哪几个数”由**服务端下发**
        (`playbookSlots`,唯一源 `playbook/skeleton.py`)。

        ⛔ 客户端硬编一份键表 = 第二份事实源,必然漂 —— 而漂的后果是**静默**的:
        用户改完点提交拿一个英文 422,而界面上的表单一路是绿的。
        ⚠ 槽位**只有数值**(`kind ∈ {price, percent}`),⛔ 没有“理由”“评价”这类键。
        """
        from neckline.playbook import skeleton as skeleton_mod

        day = date(2024, 4, 29)
        self._seed_listing(api_env, day)
        r = client.get(f"/api/v1/selection/{day:%Y%m%d}/stock/600001.SH", headers=AUTH)
        slots = r.json()["playbookSlots"]
        # 键集**逐字等于**服务端那一份(就是 `POST` 要求的那一份)。
        assert [s["key"] for s in slots] == list(skeleton_mod.required_keys("p1"))
        assert {s["kind"] for s in slots} <= set(skeleton_mod.KINDS)
        for s in slots:
            assert s["label"] and s["hint"]
            assert set(s) == {"key", "kind", "label", "hint"}, "槽位只有这四个字段"


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ 逐只摘要(V2.5.0 S12:§5.11 今日清单要的那四样)
# ═══════════════════════════════════════════════════════════════════════════

class TestSelectionStocks:
    """`/selection/{date}` 的 `stocks[]`:形态标注 / **上方机械空间** / 三个价位 /
    三分支预案摘要 —— §5.11 逐字要的那四样。

    🔴 **三样缺席各自如实标**:`upsideRoomMechPct = null` = 本形态不看这一项
    (⛔ 不是“上方没有空间”);`playbook = null` = 明早核对不了它;
    `newsState = null` = 解释层没跑过这一只(与 `"unverified"` “查过没查成”不是一回事)。
    """

    def _seed_report(self, env, day: date) -> None:
        from neckline.report import store as report_store

        report_store.save_k9_report(
            trade_date=day, report_date=day, state="has_list", headline="今天有这些 · 1 只",
            gaps=[], markdown="", structured={}, strategy="K9", strategy_version="K9-v2",
            params_package_version="k9-params-fixture", pack_id="pid", pack_version="fp-3",
            listing_size=1, strict_count=1, relaxed_count=0, db_path=env.db_path)

    def test_each_missing_piece_is_reported_on_its_own(self, api_env, client, AUTH):
        day = date(2024, 4, 29)
        TestUserEdit()._seed_listing(api_env, day)
        self._seed_report(api_env, day)

        r = client.get(f"/api/v1/selection/{day:%Y%m%d}", headers=AUTH)
        assert r.status_code == 200
        stocks = r.json()["stocks"]
        assert len(stocks) == 1
        one = stocks[0]
        assert one["tsCode"] == "600001.SH"
        assert one["patterns"] == ["p1"] and one["primaryPattern"] == "p1"
        assert one["tier"] == "strict" and one["seatKind"] == "floor"
        # 没跑过召回记录 → 机械空间**缺席**。🔴 ⛔ 不许被写成 0。
        assert one["upsideRoomMechPct"] is None
        assert one["playbook"] is None      # 没冻预案 → 明早核对不了它
        assert one["newsState"] is None     # 解释层没跑过这一只
        assert one["explainOk"] is None

    def test_the_upside_room_is_read_back_with_the_right_sign(self, api_env, client, AUTH):
        """K9-v2 只由 P1 保存并反读上方机械空间原值。

        ⚠ 它与预案层的**第一压力位**是两个量,⛔ 永不互相顶替。
        """
        from neckline.k9 import store as k9_store
        from neckline.k9.contract import ChannelHit, Pattern, Tier

        day = date(2024, 4, 29)
        TestUserEdit()._seed_listing(api_env, day)
        self._seed_report(api_env, day)
        k9_store.save_channel_hits(
            run_id="r1", trade_date=day,
            hits=(ChannelHit(ts_code="600001.SH", pattern=Pattern.P1, tier=Tier.STRICT,
                             strength={"upsideRoomFar": 0.1234}),),
            seated_codes=["600001.SH"], strategy_version="K9-v2", db_path=api_env.db_path)

        r = client.get(f"/api/v1/selection/{day:%Y%m%d}", headers=AUTH)
        assert r.json()["stocks"][0]["upsideRoomMechPct"] == pytest.approx(0.1234)

        room = k9_store.load_upside_room_mech(day, codes=["600001.SH"], db_path=api_env.db_path)
        assert room["600001.SH"] == pytest.approx(0.1234)

    def test_the_hot_path_query_is_bounded_by_the_listing(self, api_env):
        """🔴 **热路径不允许全表扫**:`k9_channel_hits` 是 append-only 的**全部**
        召回记录(四通道 × 两档 × 数百只),而本函数跑在常驻服务的 API 热路径上。
        `codes` 是**必填**的,且过滤在 SQL 里做(§12 坑 1)。"""
        import inspect

        from neckline.k9 import store as k9_store

        sig = inspect.signature(k9_store.load_upside_room_mech)
        assert sig.parameters["codes"].default is inspect.Parameter.empty, (
            "`codes` 必须是必填关键字 —— 给它一个默认值就等于默许全表扫")
        src = inspect.getsource(k9_store.load_upside_room_mech)
        assert "ts_code IN (" in src, "过滤必须在 SQL 里做,⛔ 不是全捞回进程再筛"
        assert k9_store.load_upside_room_mech(
            date(2024, 4, 29), codes=[], db_path=api_env.db_path) == {}


# ══════════════════════════════════════════════════════════════════════════
# 资料聚合的降级
# ══════════════════════════════════════════════════════════════════════════

class TestAggregate:
    def _input(self) -> explain_input.ExplainInput:
        return explain_input.ExplainInput(
            ts_code="600001.SH", name="示例甲", sw_l2_code="801080.SI",
            sw_l2_name="半导体", board="MAIN", close=10.0, prev_close=9.9,
            ret_1d=0.01, amp_1d=0.02, turnover_rate=1.5, volume_ratio=1.2,
            circ_mv=1e6, sw_l2_median_ret=0.002, rel_strength_1d=0.008,
            bars=(Bar(trade_date="20240429", open=9.9, high=10.3, low=9.8,
                      close=10.0, vol=1000.0),))

    def test_a_good_call_fills_every_key(self):
        note = explain_aggregate.aggregate_one(
            self._input(), provider=FakeProvider([_explain_content()]))
        assert note.llm_ok and note.kline_comment
        assert set(note.profile) == {"company", "industryContext", "position", "recent"}

    def test_empty_success_is_a_failure(self):
        """🔴 §12 坑 13:五个键没给全 → `llm_ok=False`,
        ⛔ 不留一份「有结构、没内容」的记录冒充跑通了。"""
        note = explain_aggregate.aggregate_one(
            self._input(), provider=FakeProvider([_explain_content(klineComment="")]))
        assert note.llm_ok is False and "缺键" in note.reason

    def test_no_provider_means_no_network_call(self):
        note = explain_aggregate.aggregate_one(self._input(), provider=None)
        assert note.llm_ok is False and "provider" in note.reason

    def test_the_material_never_mentions_rank_or_score(self):
        """双盲的**运行时**证据:喂给模型的材料里连「第几名」都拼不出来。"""
        prov = FakeProvider([_explain_content()])
        explain_aggregate.aggregate_one(self._input(), provider=prov)
        material = prov.calls[0][1].content
        for banned in ("排名", "第 1 名", "rank", "score", "seat", "形态", "通道"):
            assert banned not in material, f"材料里泄漏了 `{banned}`"

    def test_parallel_aggregate_keeps_audit_writes_on_main_thread(self, monkeypatch, tmp_path):
        calls: List[str] = []
        monkeypatch.setattr("neckline.llm.usage.record",
                            lambda **_kw: calls.append(threading.current_thread().name))
        items = [self._input(), replace(self._input(), ts_code="600002.SH")]
        notes = explain_aggregate.aggregate(
            items, provider=FakeProvider([_explain_content(), _explain_content()]),
            trade_date=date(2024, 4, 29), db_path=tmp_path / "audit.db")
        assert [n.ts_code for n in notes] == [i.ts_code for i in items]
        assert calls == ["MainThread", "MainThread"]

    def test_audit_write_failure_does_not_discard_parallel_aggregate_results(self, monkeypatch, tmp_path):
        monkeypatch.setattr("neckline.llm.usage.record",
                            lambda **_kw: (_ for _ in ()).throw(RuntimeError("locked")))
        notes = explain_aggregate.aggregate(
            [self._input()], provider=FakeProvider([_explain_content()]),
            trade_date=date(2024, 4, 29), db_path=tmp_path / "audit.db")
        assert len(notes) == 1 and notes[0].llm_ok


class TestParallelPlaybookPersistence:
    def test_versions_audit_and_saves_stay_on_main_thread(self, monkeypatch, tmp_path):
        item_a = _pb_input()
        item_b = replace(item_a, ts_code="600002.SH")
        threads: List[str] = []
        saved: List[str] = []
        monkeypatch.setattr(playbook_fill, "build_inputs", lambda *_a, **_kw: [item_a, item_b])
        monkeypatch.setattr(pb_store, "load_latest", lambda *_a, **_kw: {})
        monkeypatch.setattr(pb_store, "next_version",
                            lambda *_a, **_kw: threads.append(threading.current_thread().name) or 1)
        monkeypatch.setattr(pb_store, "save",
                            lambda pb, **_kw: saved.append(pb.ts_code) or pb.version)
        monkeypatch.setattr("neckline.llm.usage.record",
                            lambda **_kw: threads.append(threading.current_thread().name))
        stats = playbook_fill.fill_for_listing(
            date(2024, 4, 29),
            [{"ts_code": item_a.ts_code}, {"ts_code": item_b.ts_code}],
            provider=FakeProvider([_fill_content("p1"), _fill_content("p1")]),
            db_path=tmp_path / "fill.db")
        assert stats["frozen"] == 2 and sorted(saved) == [item_a.ts_code, item_b.ts_code]
        assert threads == ["MainThread", "MainThread", "MainThread", "MainThread"]

    def test_audit_write_failure_does_not_prevent_other_playbooks_from_freezing(self, monkeypatch, tmp_path):
        item_a = _pb_input()
        item_b = replace(item_a, ts_code="600002.SH")
        saved: List[str] = []
        monkeypatch.setattr(playbook_fill, "build_inputs", lambda *_a, **_kw: [item_a, item_b])
        monkeypatch.setattr(pb_store, "load_latest", lambda *_a, **_kw: {})
        monkeypatch.setattr(pb_store, "next_version", lambda *_a, **_kw: 1)
        monkeypatch.setattr(pb_store, "save", lambda pb, **_kw: saved.append(pb.ts_code) or pb.version)
        monkeypatch.setattr("neckline.llm.usage.record",
                            lambda **_kw: (_ for _ in ()).throw(RuntimeError("locked")))
        stats = playbook_fill.fill_for_listing(
            date(2024, 4, 29),
            [{"ts_code": item_a.ts_code}, {"ts_code": item_b.ts_code}],
            provider=FakeProvider([_fill_content("p1"), _fill_content("p1")]),
            db_path=tmp_path / "fill.db")
        assert stats["frozen"] == 2 and sorted(saved) == [item_a.ts_code, item_b.ts_code]
