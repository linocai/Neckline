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
    monkeypatch.setattr("neckline.scan.regime_store.refresh_market_regime",
                        lambda *a, **k: {"days": 1, "rows": 1, "failed": 0})
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

    def test_unrequested_segments_are_marked_skipped_not_ok(self, chain_stubs, isolated_env):
        res = ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False,
                                   db_path=isolated_env.db_path)
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

    def test_regime_failure_does_not_fail_the_scan_segment(self, chain_stubs, monkeypatch):
        """V2.2-②:行情状态批算挂在 SEG_SCAN 里但**独立保险丝**(照 ④b 行业阶段
        先例)—— 它炸了扫描段照常 ok、种子照产、链照走。"""
        monkeypatch.setattr("neckline.scan.regime_store.refresh_market_regime",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("regime 炸了")))
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_SCAN] == ev.STATUS_OK
        assert res.bundle == "BUNDLE"

    def test_landing_failure_does_not_fail_the_scan_segment(self, chain_stubs, monkeypatch):
        """V2.2-③-C:落地起跳批算挂在 SEG_SCAN 里但**独立保险丝**(照 ② 行情状态
        同款)—— 它炸了扫描段照常 ok、链照走;该日缺行由读侧如实披露。"""
        monkeypatch.setattr("neckline.scan.landing_store.refresh_landing_metrics",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("landing 炸了")))
        res = ev.run_evening_chain(D, use_llm=False)
        assert res.status[ev.SEG_SCAN] == ev.STATUS_OK
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

    def test_dropped_is_none_when_basket_segment_was_not_requested(self, chain_stubs, isolated_env):
        """没跑 ⑥ 且跨进程交接表当天也无行(⑤⑥ 从未跑过)→ `None`(③b 如实标
        "本段未取得"),⛔ 不是 `[]`。V2-⑯-D 补记后,这条同时是"交接表无行"分支的
        回归锁——完整的跨进程状态矩阵见 `TestDroppedCrossProcessHandoff`。"""
        ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False,
                             db_path=isolated_env.db_path)
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


class TestDroppedCrossProcessHandoff:
    """V2-⑯-D 补记(2026-08-04 定向小修):⑤⑥⑦(`neckline-basket.service`)与
    「⑨+报告」(`neckline-report.service`)拆进两个独立进程后,报告段独立跑时必须
    能跨进程读回 ⑥ 的结果——`basket_dropped_handoff` 表是唯一落点。

    **两次独立调用模拟 seg2/seg3**:第一次直接调真实写入口 `save_dropped_handoff`
    (= seg2 进程里 `_run_basket_segment` 落表那一刻的真实效果,不经
    `run_evening_chain` 编排),第二次调用真实的 `run_evening_chain(segments=
    ['report'])`(= seg3 独立进程的真实调用形状)——两次只共享磁盘上同一个
    sqlite 文件,不共享任何进程内内存状态,`chain_stubs` 只桩掉 LLM/批算重活,
    交接表读写走的是本文件测的真代码。"""

    def test_seg3_alone_reads_back_overflow_that_seg2_wrote(self, chain_stubs, isolated_env):
        from neckline.selection.basket_dropped_handoff import save_dropped_handoff
        from neckline.selection.tier import DroppedBasket

        save_dropped_handoff(D, [
            DroppedBasket("k1", "capacity_overflow", 0.81),
            DroppedBasket("k2", "below_quality_line", 0.22),
        ], db_path=isolated_env.db_path)

        ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False,
                             db_path=isolated_env.db_path)
        assert [(d.basket_key, d.reason, d.mech_score) for d in chain_stubs.dropped_seen] == [
            ("k1", "capacity_overflow", 0.81), ("k2", "below_quality_line", 0.22),
        ]

    def test_seg3_alone_reads_back_zero_overflow_as_empty_list_not_none(
        self, chain_stubs, isolated_env
    ):
        """跑了、零溢出 ≠ 没跑——空列表也是"取得了"的答案,不能退化成 `None`。"""
        from neckline.selection.basket_dropped_handoff import save_dropped_handoff

        save_dropped_handoff(D, [], db_path=isolated_env.db_path)
        ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False,
                             db_path=isolated_env.db_path)
        assert chain_stubs.dropped_seen == []

    def test_seg3_alone_before_seg2_ever_ran_stays_honestly_none(
        self, chain_stubs, isolated_env
    ):
        """交接表当天无行(seg2 还没跑过,或跑了但 ⑤ 零产出)——报告必须如实标
        "未取得",⛔ 不许猜。"""
        ev.run_evening_chain(D, segments=[ev.SEG_REPORT], use_llm=False,
                             db_path=isolated_env.db_path)
        assert chain_stubs.dropped_seen is None

    def test_attempted_and_failed_this_run_is_not_rescued_by_a_stale_handoff_row(
        self, chain_stubs, isolated_env, monkeypatch
    ):
        """⚠ 关键防回归:哪怕交接表里躺着今天早些时候写的旧数据,只要**本次调用**
        确实尝试跑了 SEG_BASKET 且炸了,结果就必须原样是 `None`——不许被表里的
        旧数据"救回来"(那会把"这次失败"讲成"这次成功",一个编造出来的市场结论;
        见 `_run_basket_segment` 与 `run_evening_chain` 里"炸了就是 None 不是回退
        查表"的既定纪律)。"""
        from neckline.selection.basket_dropped_handoff import save_dropped_handoff
        from neckline.selection.tier import DroppedBasket

        save_dropped_handoff(D, [DroppedBasket("stale", "capacity_overflow", 0.5)],
                             db_path=isolated_env.db_path)
        monkeypatch.setattr(ev, "_run_basket_segment",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("篮子段炸了")))
        ev.run_evening_chain(D, use_llm=False, db_path=isolated_env.db_path)
        assert chain_stubs.dropped_seen is None

    def test_single_process_full_chain_never_consults_the_handoff_table(
        self, chain_stubs, isolated_env
    ):
        """单进程整链跑法(`SEG_BASKET` 恒在 `wanted` 里)绝不查表——哪怕表里躺着
        与本次内存结果不同的数据,也必须原样用本次内存结果(行为逐字节不变,
        单进程路径不受本次补记影响)。"""
        from neckline.selection.basket_dropped_handoff import save_dropped_handoff
        from neckline.selection.tier import DroppedBasket

        save_dropped_handoff(D, [DroppedBasket("stale", "capacity_overflow", 0.5)],
                             db_path=isolated_env.db_path)
        ev.run_evening_chain(D, use_llm=False, db_path=isolated_env.db_path)  # 默认全链
        # `chain_stubs` 的 `_basket` 桩返回 basket_key="k9",与表里的 "stale" 不同——
        # 断言必须命中内存结果,证明查表分支未被触发。
        assert [d.basket_key for d in chain_stubs.dropped_seen] == ["k9"]


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


class TestStageHandoffWriteSide:
    """§七 P0-39(2026-08-05 生产实打):⑤ 的**段状态**必须落表,报告 ③ 节才分得清
    「跑了、真没够格的篮子」与「引擎压根没跑成」。

    ⚠ **落点位置是本条的要害**:⑤ 缺席时 `_run_basket_segment` 在
    `if not result.baskets` 就早返回了 —— 留痕写在那句**之后**就永远记不下缺席这件事,
    而缺席恰恰是唯一需要它的场景。本类正面钉死这个顺序。
    """

    def _seg(self, isolated_env, result, monkeypatch):
        from neckline.selection import aggregate as agg

        monkeypatch.setattr(agg, "aggregate_baskets", lambda *a, **k: result)
        return ev._run_basket_segment(
            D, seed_set=None, db_path=isolated_env.db_path, parquet_dir=None, use_llm=False,
            search_provider=None, reason_provider=None, tier_provider=None, card_provider=None,
            transport=None, ledger=None, stats={}, notes=[],
        )

    def _stub(self, *, reason="no_provider", search="no_provider", notes=()):
        class _AggR:
            baskets = ()
            rejected = ()
            hygiene_rejected = ()
            search_stage = search
            reason_stage = reason

        _AggR.notes = tuple(notes)
        return _AggR()

    def test_absent_reason_stage_is_recorded_even_though_five_returns_early(
        self, isolated_env, monkeypatch
    ):
        from neckline.selection.basket_stage_handoff import load_stage_verdict

        assert self._seg(isolated_env, self._stub(), monkeypatch) is None  # ③b 仍是"没跑 ⑥"
        v = load_stage_verdict(D, db_path=isolated_env.db_path)
        assert v is not None, "⑤ 缺席时一行都没落 = P0-39 原样复发"
        assert v.reason_stage == "no_provider" and v.engine_ran is False

    def test_engine_ran_with_zero_baskets_is_recorded_as_ran(self, isolated_env, monkeypatch):
        """跑完了、提案全被机械闸拦下 → 零篮子是**真结论**,③ 节照旧可以那么写。"""
        from neckline.selection.basket_stage_handoff import load_stage_verdict

        self._seg(isolated_env, self._stub(reason="ok", search="ok"), monkeypatch)
        assert load_stage_verdict(D, db_path=isolated_env.db_path).engine_ran is True

    def test_notes_are_carried_so_the_aggregate_fuse_is_not_read_as_no_seeds(
        self, isolated_env, monkeypatch
    ):
        from neckline.selection.basket_stage_handoff import load_stage_verdict

        self._seg(isolated_env,
                  self._stub(reason="no_seeds", search="no_seeds",
                             notes=("aggregate_failed:KeyError",)), monkeypatch)
        v = load_stage_verdict(D, db_path=isolated_env.db_path)
        assert v.engine_ran is False and v.reason_code == "aggregate_failed:KeyError"

    def test_segment_blowup_overwrites_a_stale_row_instead_of_inheriting_it(
        self, chain_stubs, isolated_env, monkeypatch
    ):
        """⚠ 关键防回归(同 ③b 那条):今天早些时候 ⑤ 跑成过、表里躺着 `ok`;本次
        SEG_BASKET 整段炸了 —— **本次的明确结论是"没跑成"**,不许沿用旧行让报告
        ③ 节继续讲"今天市场上没有够格的篮子"。"""
        from neckline.selection.basket_stage_handoff import load_stage_verdict, save_stage_handoff

        save_stage_handoff(D, self._stub(reason="ok", search="ok"), db_path=isolated_env.db_path)
        monkeypatch.setattr(ev, "_run_basket_segment",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("篮子段炸了")))
        ev.run_evening_chain(D, use_llm=False, db_path=isolated_env.db_path)
        v = load_stage_verdict(D, db_path=isolated_env.db_path)
        assert v.engine_ran is False and v.reason_code == "segment_failed:RuntimeError"


def test_evening_module_is_the_only_place_that_calls_scan_write_entrypoints():
    """结构性守门:批算写入口只许出现在本链模块里 —— `report/pipeline.py` 与
    `report/basket_daily.py` 属在线路径,由 `test_scan_layer_guardrails.py` 把关。
    这条是它的正面:确认那条链**真的**在这里,不是被谁悄悄搬走了。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "neckline" / "report" / "evening.py").read_text(
        encoding="utf-8")
    for name in ("refresh_limit_clusters", "refresh_corr_matrix", "refresh_leader_structure"):
        assert name in src, f"{name} 不在晚间链里 —— 扫描层批算是不是被搬走了?"
