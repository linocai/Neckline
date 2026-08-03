"""V2-⑭-A 16:35 晚间编排链(`neckline/report/evening.py`)。

核心断言(逐条对应 plan 的硬要求):
· **顺序定死**:⑧ 验证拍 → ④ 扫描 → ⑤⑥⑦ 篮子 → ⑨ 复盘 → 报告;
  `segments` 只挑跑哪几段,**乱序传参不会得到乱序执行**;
· **⑧ 排在拉数之后、扫描层之前**(位置定死,⑭-A 明令);
· **每段各自包保险丝**:任一段炸了链继续走、报告照出;**唯独报告段炸了要往上抛**
  (那才是真的没有报告,退出码必须非零);
· **`dropped` 的三态**:跑了有溢出 / 跑了零溢出(`[]`)/ 没跑(`None`)—— ⛔ 不许合并;
· **段状态四态**:ok / empty(跑了没东西,合法)/ failed(跑了炸了)/ skipped(没要)。

⚠ 本文件**不重测** ⑤⑥⑦⑧⑨ 各自的领域逻辑(那些在各自的测试文件里),只测**编排**。
"""

from __future__ import annotations

from datetime import date

import pytest

from neckline.report import evening as ev

pytestmark = pytest.mark.usefixtures("isolated_env")

D = date(2026, 7, 24)


class _Rec:
    """记录调用顺序的探针。"""

    def __init__(self):
        self.calls = []


@pytest.fixture
def chain_stubs(monkeypatch):
    """把五段各自替换成探针,只留编排逻辑本身受测(**不碰真实批算**)。"""
    rec = _Rec()

    class _V:
        evaluated, rows_written = 2, 2
        skipped_unchanged = skipped_latched = skipped_not_observed = 0

    def _verify(trade_date, **kw):
        rec.calls.append("verify")
        return _V()

    def _cluster(days, **kw):
        rec.calls.append("scan")
        return {"rows": 3}

    class _Seeds:
        def all_seeds(self):
            return ("s1",)

        def counts(self):
            return {"hot_industry": 1}

    def _seeds(trade_date, **kw):
        return _Seeds()

    def _basket(trade_date, **kw):
        rec.calls.append("basket")
        kw["stats"]["basket"] = {"baskets": 1, "cards": 1, "dropped": 1}
        return [_D("k9", "capacity_overflow", 0.7)]

    class _R:
        reviews, rows_inserted, rows_existing, llm_called, notes = [1], 1, 0, 0, []

    def _review(trade_date, **kw):
        rec.calls.append("review")
        return _R()

    def _report(trade_date, **kw):
        rec.calls.append("report")
        rec.dropped_seen = kw.get("dropped_baskets")
        return "BUNDLE"

    monkeypatch.setattr("neckline.sentinel.basket_verify.run_eod_verification", _verify)
    monkeypatch.setattr("neckline.scan.cluster.refresh_limit_clusters", _cluster)
    monkeypatch.setattr("neckline.scan.corr.refresh_corr_matrix", lambda *a, **k: {"rows": 1})
    monkeypatch.setattr("neckline.scan.leader.refresh_leader_structure", lambda *a, **k: {"rows": 1})
    monkeypatch.setattr("neckline.scan.stage.refresh_industry_stage", lambda *a, **k: {"rows": 1})
    monkeypatch.setattr("neckline.scan.seeds.generate_seeds", _seeds)
    monkeypatch.setattr("neckline.review.basket_review.review_day", _review)
    monkeypatch.setattr(ev, "_run_basket_segment", _basket)
    monkeypatch.setattr(ev, "build_report", _report)
    return rec


class _D:
    def __init__(self, basket_key, reason, mech_score):
        self.basket_key, self.reason, self.mech_score = basket_key, reason, mech_score


class TestOrderIsPinnedDown:
    def test_segments_run_in_the_plan_order(self, chain_stubs):
        ev.run_evening_chain(D, use_llm=False)
        assert chain_stubs.calls == ["verify", "scan", "basket", "review", "report"]

    def test_eod_verification_runs_before_the_scan_layer(self, chain_stubs):
        """⑭-A 位置定死:⑧ 判的是**昨日冻的卡**在**今日**收盘的表现,吃刚拉到的今日
        EOD —— 与今晚要生成的新篮子无关。放最前面既符合因果,又保证后面某段炸了
        昨日的定论行也已经落好。"""
        ev.run_evening_chain(D, use_llm=False)
        assert chain_stubs.calls.index("verify") < chain_stubs.calls.index("scan")

    def test_shuffled_segments_argument_does_not_shuffle_execution(self, chain_stubs):
        ev.run_evening_chain(D, segments=["report", "basket", "verify"], use_llm=False)
        assert chain_stubs.calls == ["verify", "basket", "report"]

    def test_unrequested_segments_are_marked_skipped_not_ok(self, chain_stubs):
        res = ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False)
        assert res.status[ev.SEG_VERIFY] == ev.STATUS_SKIPPED
        assert res.status[ev.SEG_SCAN] == ev.STATUS_SKIPPED
        assert res.status[ev.SEG_REPORT] == ev.STATUS_OK


class TestFusesPerSegment:
    def test_verification_failure_does_not_stop_the_chain(self, chain_stubs, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("验证拍炸了")

        monkeypatch.setattr("neckline.sentinel.basket_verify.run_eod_verification", _boom)
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_VERIFY] == ev.STATUS_FAILED
        assert chain_stubs.calls == ["scan", "basket", "review", "report"]
        assert res.bundle == "BUNDLE"
        assert any("验证拍" in n for n in res.notes)

    def test_scan_failure_does_not_stop_the_chain(self, chain_stubs, monkeypatch):
        monkeypatch.setattr("neckline.scan.cluster.refresh_limit_clusters",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("扫描炸了")))
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_SCAN] == ev.STATUS_FAILED
        assert res.bundle == "BUNDLE"

    def test_review_failure_does_not_stop_the_report(self, chain_stubs, monkeypatch):
        monkeypatch.setattr("neckline.review.basket_review.review_day",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("复盘炸了")))
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_REVIEW] == ev.STATUS_FAILED
        assert res.bundle == "BUNDLE"

    def test_report_failure_is_re_raised_because_that_really_is_no_report(
        self, chain_stubs, monkeypatch
    ):
        """⚠ 唯一不吞的一段:报告本身炸了 = 真的没有报告。异常必须往上抛,
        否则 systemd 那侧会看到一个"成功"的退出码,而当天根本没有报告
        (`ExecMainStatus` 才是部署验收的判据,不是 timer 跑过了)。"""
        monkeypatch.setattr(ev, "build_report",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("报告炸了")))
        with pytest.raises(RuntimeError, match="报告炸了"):
            ev.run_evening_chain(D, use_llm=False)


class TestDroppedThreeStates:
    def test_dropped_is_forwarded_to_the_report_when_tier_ran(self, chain_stubs):
        ev.run_evening_chain(D, use_llm=False)
        assert [d.reason for d in chain_stubs.dropped_seen] == ["capacity_overflow"]

    def test_dropped_is_none_when_basket_segment_was_not_requested(self, chain_stubs):
        """没跑 ⑥ → `None`(③b 如实标"本段未取得"),⛔ 不是 `[]`。"""
        ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False)
        assert chain_stubs.dropped_seen is None

    def test_dropped_is_none_when_basket_segment_blew_up(self, chain_stubs, monkeypatch):
        """⚠ 炸了也是 `None` 不是 `[]` —— `[]` 的意思是「⑥ 跑过、今天零溢出」,
        把失败说成零溢出就是在编造一个市场结论。"""
        monkeypatch.setattr(ev, "_run_basket_segment",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("篮子段炸了")))
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_BASKET] == ev.STATUS_FAILED
        assert res.dropped_baskets is None and chain_stubs.dropped_seen is None

    def test_empty_dropped_list_survives_as_empty_list(self, chain_stubs, monkeypatch):
        def _basket(trade_date, **kw):
            kw["stats"]["basket"] = {"baskets": 1, "cards": 1, "dropped": 0}
            return []

        monkeypatch.setattr(ev, "_run_basket_segment", _basket)
        ev.run_evening_chain(D, use_llm=False)
        assert chain_stubs.dropped_seen == []


class TestEmptyIsNotFailure:
    def test_no_seeds_marks_scan_empty_and_says_it_is_legal(self, chain_stubs, monkeypatch):
        monkeypatch.setattr("neckline.scan.seeds.generate_seeds", lambda *a, **k: None)
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_SCAN] == ev.STATUS_EMPTY
        assert res.ok(ev.SEG_SCAN), "「今日无种子」是合法输出,不是失败"
        assert any("合法输出" in n for n in res.notes)

    def test_no_baskets_marks_basket_empty(self, chain_stubs, monkeypatch):
        def _basket(trade_date, **kw):
            kw["stats"]["basket"] = {"baskets": 0, "cards": 0}
            return None

        monkeypatch.setattr(ev, "_run_basket_segment", _basket)
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_BASKET] == ev.STATUS_EMPTY and res.ok(ev.SEG_BASKET)


def test_evening_module_is_the_only_place_that_calls_scan_write_entrypoints():
    """结构性守门:批算写入口只许出现在本链模块里 —— `report/pipeline.py` 与
    `report/basket_daily.py` 属在线路径,由 `test_scan_layer_guardrails.py` 把关。
    这条是它的正面:确认那条链**真的**在这里,不是被谁悄悄搬走了。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "neckline" / "report" / "evening.py").read_text(
        encoding="utf-8")
    for name in ("refresh_limit_clusters", "refresh_corr_matrix", "refresh_leader_structure"):
        assert name in src, f"{name} 不在晚间链里 —— 扫描层批算是不是被搬走了?"
