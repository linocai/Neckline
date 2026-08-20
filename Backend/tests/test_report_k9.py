"""K9 报告链单测(V2.5.0 S7,PROJECT_PLAN §6 S7 验收 + §5.10)。

| # | 验收 | section |
|---|---|---|
| 1 | 三态各一份渲染快照,**首行可辨** | ① |
| 2 | 🔴 参数未配置那份**仍有**方向背景 + 市场事实 + 覆盖率 | ② |
| 3 | 两层视图:默认视图 + **默认折叠**的结构化完整版 | ③ |
| 4 | 晚间链段序 `facts → k9 → explain → playbook → report`;逐段状态四态分开 | ④ |
| 5 | APNs:三态 → 推送文案是**全映射**;`not_run` ⛔ 不许推「选股已就绪」 | ⑤ |

周日排程 / 休市跳过 / 同日已生成跳过 三条契约见 `test_weekend_report_schedule.py`
(🔴 双日期契约的唯一机器守门,LRN-20260816-001)。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from neckline.report import evening as evening_mod
from neckline.report import pipeline as pipeline_mod
from neckline.report import store as report_store
from neckline.report.state import ReportState
from tests import k9_env


@pytest.fixture
def market(isolated_env):
    day = k9_env.seed(isolated_env)
    return isolated_env, day


def _params_file(env, tmp_path, **overrides):
    import json as _json

    target = tmp_path / "k9-params.json"
    target.write_text(_json.dumps(k9_env.raw_params(**overrides), ensure_ascii=False),
                      encoding="utf-8")
    return target


def _chain(env, day, *, params_path=None, segments=None, save=True):
    return evening_mod.run_evening_chain(
        day, report_date=day,
        segments=segments or evening_mod.CHAIN_SEGMENTS,
        k9_params_path=params_path, db_path=env.db_path,
        parquet_dir=env.parquet_dir, save=save,
    )


# ══════════════════════════════════════════════════════════════════════════
# ① 三态各一份,首行可辨
# ══════════════════════════════════════════════════════════════════════════

class TestThreeStates:
    def test_has_list_when_everything_ran(self, market, tmp_path):
        env, day = market
        res = _chain(env, day, params_path=_params_file(env, tmp_path))
        bundle = res.bundle
        assert bundle.state is ReportState.HAS_LIST
        assert bundle.markdown.splitlines()[0].startswith("# 今天有这些 · ")
        assert bundle.listing_size == len(bundle.listing) > 0

    def test_empty_when_the_run_produced_nothing(self, market, tmp_path):
        """「今天没有」= 跑通了、结果是空的 —— 这个结论**可以被信任**。"""
        env, day = market
        # 把四个通道全部卡死 → 候选为空 → 清单 0 只
        path = _params_file(
            env, tmp_path,
            **{"channels.p1.strict.ampMaxPct": 0.001,
               "channels.p1.relaxed.ampMaxPct": 0.001,
               "channels.p2.strict.normDropMin": 0.999,
               "channels.p2.relaxed.normDropMin": 0.999,
               "channels.p3.strict.flatBand": 1e-9,
               "channels.p3.relaxed.flatBand": 1e-9,
               "channels.p4.strict.lagRankGap": 0.999,
               "channels.p4.relaxed.lagRankGap": 0.999})
        res = _chain(env, day, params_path=path)
        assert res.bundle.state is ReportState.EMPTY
        assert res.bundle.markdown.splitlines()[0] == "# 今天没有"
        assert res.bundle.listing_size == 0, "⛔ 不是 None —— 它跑通了"

    def test_not_run_when_params_are_missing(self, market):
        """🔴 裁定 5:参数未配置 → `not_run`,⛔ **不是** `empty`。"""
        env, day = market
        res = _chain(env, day, params_path=None)
        bundle = res.bundle
        assert bundle.state is ReportState.NOT_RUN
        assert bundle.markdown.splitlines()[0].startswith("# 今天没跑成 · ")
        assert bundle.listing_size is None, "⛔ 不是 0 —— 清单根本没算出来"
        assert any("参数未配置" in g for g in bundle.gaps)

    def test_not_run_when_the_pack_was_never_frozen(self, isolated_env, tmp_path):
        """事实包未冻结(数据未到齐)同样是「今天没跑成」,且**逐条列出缺口**。"""
        env = isolated_env
        from tests.conftest import insert_trade_cal

        day = date(2024, 4, 30)
        insert_trade_cal(env, [day])
        bundle = pipeline_mod.build_report(
            day, report_date=day,
            db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert bundle.state is ReportState.NOT_RUN
        assert any("事实包未冻结" in g for g in bundle.gaps)

    def test_the_three_headlines_are_mutually_distinguishable(self, market, tmp_path):
        env, day = market
        # ⚠ 次序有意义:先跑「没参数」那一份 —— 一旦策略层跑过一次,那天就**确实**
        # 有清单了(见下面那条冒烟抓到的用例)。
        not_run = _chain(env, day, params_path=None).bundle
        has_list = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        assert has_list.headline != not_run.headline
        assert "今天没跑成" in not_run.headline
        assert "今天没跑成" not in has_list.headline

    def test_a_report_only_run_reports_the_listing_the_strategy_layer_already_made(
        self, market, tmp_path,
    ):
        """🔴 **本片冒烟当场抓到的 bug**(已修):`neckline-report.service` 只跑
        `--segments report`,它**不拿也不该拿**参数包路径。报告若自己再 load 一次
        参数文件,就会在策略层明明跑出 5 只的日子里宣布「今天没跑成」,同时又把
        那 5 只落进库 —— 一份自相矛盾的报告。

        权威是 `k9_runs` 那一行:**报告描述的是这一天,不是这一次调用。**
        """
        env, day = market
        made = _chain(env, day, params_path=_params_file(env, tmp_path),
                      segments=["facts", "k9"])
        assert made.status["k9"] == evening_mod.STATUS_OK

        report_only = _chain(env, day, params_path=None, segments=["report"]).bundle
        assert report_only.state is ReportState.HAS_LIST
        assert report_only.listing_size == made.stats["k9"]["seated"]
        assert report_only.gaps == (), "跑成了的日子⛔ 不许挂着「参数未配置」的缺口"
        assert report_only.params_package_version == "k9-params-fixture"


# ══════════════════════════════════════════════════════════════════════════
# ② 参数未配置的日子照样发报告(§5.10)
# ══════════════════════════════════════════════════════════════════════════

def test_a_not_run_report_still_carries_market_facts_and_the_coverage_ruler(
    market, tmp_path,
):
    """🔴 §5.10:清单段标「今天没跑成 · 参数未配置」,而**方向背景、市场事实、
    覆盖率成绩线照常呈现**。日节奏不断,尺子照跑。"""
    env, day = market
    from neckline.scorecard import coverage as coverage_mod

    coverage_mod.refresh_day(day, parquet_dir=env.parquet_dir, db_path=env.db_path)

    bundle = _chain(env, day, params_path=None).bundle
    assert bundle.state is ReportState.NOT_RUN
    md = bundle.markdown
    assert "## 方向背景" in md
    assert "## 市场事实" in md and "涨停" in md
    assert "## 覆盖率(尺子)" in md
    assert bundle.coverage is not None
    assert bundle.market.get("limitMap")


def test_the_direction_section_says_not_wired_instead_of_inventing_one(market):
    """⚠ `facts/direction_llm.py` 尚未建 → 报告如实写「未接入」,
    ⛔ 不编一段方向解读(架构 §八:它不参与任何机械决策)。"""
    env, day = market
    bundle = _chain(env, day, params_path=None).bundle
    assert bundle.direction is None
    assert "未接入" in bundle.markdown


# ══════════════════════════════════════════════════════════════════════════
# ③ 两层视图
# ══════════════════════════════════════════════════════════════════════════

class TestTwoLayerView:
    def test_the_structured_full_version_is_collapsed_by_default(self, market, tmp_path):
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        assert "<details>" in bundle.markdown
        assert "结构化完整版" in bundle.markdown

    def test_the_structured_payload_round_trips_as_json(self, market, tmp_path):
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        blob = bundle.markdown.split("```json\n")[1].split("\n```")[0]
        assert json.loads(blob)["tradeDate"] == day.isoformat()

    def test_the_report_records_which_pack_and_which_params_it_ran_on(
        self, market, tmp_path,
    ):
        """架构 §3.1:报告永远记得自己跑在哪版事实包 + 哪版参数上。"""
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        assert bundle.pack_version == "fp-2"
        assert bundle.params_package_version == "k9-params-fixture"
        row = report_store.load_k9_report(day, db_path=env.db_path)
        assert row["pack_id"] == bundle.pack_id
        assert row["params_package_version"] == "k9-params-fixture"

    def test_the_listing_section_discloses_that_news_screening_has_not_run(
        self, market, tmp_path,
    ):
        """§5.5:解释层**没跑**(分段跑里没有它)→ 报告如实说这份清单还没过消息面。

        ⚠ **V2.5.0 S9 起解释层已经建好**,所以这条测试改成「分段跑里不带 explain」
        —— 那才是「没跑过」的真实情形。清单是否过了消息面,权威是
        `k9_runs.listing_finalized_by` 那一列。"""
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path),
                        segments=["facts", "k9", "report"]).bundle
        assert "尚未经过消息面剔除" in bundle.markdown

    def test_unverified_news_is_never_reported_as_clean(self, market, tmp_path):
        """🔴 解释层跑了、但**一个 provider 都没有** → 五只全是 `unverified`。

        报告必须**如实说「没查成」**,⛔ 不许因为「解释层跑过了」就让读者以为
        消息面已经核实过 —— 「没看」冒充「看过了没事」正是本仓栽过三次的那族病。"""
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        assert "消息面未核实" in bundle.markdown
        assert "不等于确认无消息" in bundle.markdown
        # 清单确实过了解释层那一遍(剔除 + 补位的编排),所以那句「尚未经过消息面
        # 剔除」不该再出现 —— 两句话说的是**两件不同的事**。
        assert "尚未经过消息面剔除" not in bundle.markdown

    def test_a_stock_without_a_frozen_playbook_says_so(self, market, tmp_path):
        """没有 provider → 一份预案都冻不成。报告必须逐只说「明早核对不了」,
        ⛔ 不许沉默(沉默会被读成「预案在,只是没印出来」)。"""
        env, day = market
        bundle = _chain(env, day, params_path=_params_file(env, tmp_path)).bundle
        assert "没有冻结预案" in bundle.markdown


# ══════════════════════════════════════════════════════════════════════════
# ④ 晚间链
# ══════════════════════════════════════════════════════════════════════════

class TestEveningChain:
    def test_the_segment_order_is_the_new_one(self):
        assert evening_mod.CHAIN_SEGMENTS == (
            "facts", "k9", "explain", "playbook", "report")

    def test_explain_finalises_the_listing_and_playbook_reports_its_failure(
        self, market, tmp_path,
    ):
        """**V2.5.0 S9/S10 起两段真的会跑**(此前它们是 `not_built`)。

        🔴 没有 provider 的环境里:
            · `explain` 段照样跑完(消息面全 `unverified`、资料全缺席),
              **清单由它定稿** → `listing_finalized_by='explain'`;
            · `playbook` 段**一份都冻不成 → `failed`**,⛔ 不给它一个 `ok`
              (没有预案 = 明早那两拍核对不了任何一只)。"""
        from neckline.k9 import store as k9_store

        env, day = market
        res = _chain(env, day, params_path=_params_file(env, tmp_path))
        assert res.status["explain"] == evening_mod.STATUS_OK
        assert res.status["playbook"] == evening_mod.STATUS_FAILED
        run = k9_store.load_run(day, db_path=env.db_path)
        assert run["listing_finalized_by"] == k9_store.FINALIZED_BY_EXPLAIN
        assert res.stats["explain"]["news"]["unverified"] == res.stats["explain"]["seated"]
        assert res.stats["playbook"]["frozen"] == 0

    def test_segments_only_pick_what_runs_never_the_order(self, market, tmp_path):
        env, day = market
        res = _chain(env, day, params_path=_params_file(env, tmp_path),
                     segments=["report", "facts"])
        assert res.status["k9"] == evening_mod.STATUS_SKIPPED
        assert res.status["facts"] == evening_mod.STATUS_OK
        assert res.status["report"] == evening_mod.STATUS_OK

    def test_an_unknown_segment_is_refused(self, market):
        env, day = market
        with pytest.raises(ValueError, match="未知段名"):
            _chain(env, day, segments=["facts", "basket"])

    def test_a_failing_segment_never_costs_us_the_report(self, market, tmp_path, monkeypatch):
        """**每段各自包保险丝**:某段炸了,报告照出、缺席如实披露。"""
        env, day = market

        def boom(*a, **k):
            raise RuntimeError("人造故障")

        monkeypatch.setattr(evening_mod, "_run_k9", boom)
        res = _chain(env, day, params_path=_params_file(env, tmp_path))
        assert res.status["k9"] == evening_mod.STATUS_FAILED
        assert res.bundle is not None
        assert any("人造故障" in n for n in res.notes)

    def test_the_facts_segment_never_refreezes_an_existing_pack(self, market):
        """§5.3.2 纪律 3:同 `(trade_date, pack_version)` ⛔ 不许覆盖。"""
        env, day = market
        res = _chain(env, day, segments=["facts"])
        assert res.status["facts"] == evening_mod.STATUS_OK
        assert res.stats["facts"]["frozen"] == "already"

    def test_the_coverage_wiring_lives_in_the_orchestrator(self, market, tmp_path):
        """🔴 尺子不许 import 被量的东西:接线住编排器,数据走 `k9_disposition`。"""
        env, day = market
        _chain(env, day, params_path=_params_file(env, tmp_path))
        listing, dispositions = evening_mod.coverage_inputs(
            day, db_path=env.db_path, parquet_dir=env.parquet_dir)
        # 当日的 D−1 还没有清单 → 两个都是 None(⛔ 不是空集合冒充「查过了」)
        assert listing is None and dispositions is None

    def test_coverage_switches_from_null_to_a_real_number_once_a_listing_exists(
        self, market, tmp_path,
    ):
        """🔴 S4 登记的那句「清单开始产出的次日**自动接上**」的端到端证据。

        跑 D−1 的策略层 → D 那天的覆盖率就从 `NULL`(昨天还没有清单)变成一个
        **真数字**。⛔ 两者不可互换:NULL 说的是「没得比」,0.0 说的是「比过了、
        一只都没覆盖到」。
        """
        from neckline.calendar import prev_trading_day
        from neckline.scorecard import coverage as coverage_mod

        env, day = market
        params = _params_file(env, tmp_path)

        before = coverage_mod.refresh_day(
            day, parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert before.coverage_all is None, "昨天还没有清单 → NULL(⛔ 不是 0)"

        d_1 = prev_trading_day(day)
        _chain(env, d_1, params_path=params)          # D−1 的清单 + disposition
        listing, dispositions = evening_mod.coverage_inputs(
            day, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert listing is not None and listing.trade_date == d_1
        assert dispositions, "D−1 的全市场 disposition 应该有了"

        after = coverage_mod.refresh_day(
            day, listing=listing, dispositions=dispositions,
            parquet_dir=env.parquet_dir, db_path=env.db_path)
        assert after.coverage_all is not None, "有了昨日清单就必须出一个真数字"
        assert 0.0 <= after.coverage_all <= 1.0


# ══════════════════════════════════════════════════════════════════════════
# ⑤ APNs:三态 → 文案是全映射
# ══════════════════════════════════════════════════════════════════════════

def test_the_push_state_map_covers_exactly_the_three_report_states():
    from scripts import evening as evening_script

    assert set(evening_script._PUSH_STATE) == {s.value for s in ReportState}


def test_a_not_run_day_never_pushes_selection_is_ready():
    """🔴 「今天没跑成」的日子推一句「选股已就绪」,是这条链上最容易犯的一次谎。"""
    from scripts import evening as evening_script

    assert evening_script._PUSH_STATE["not_run"] == "unavailable"
    assert evening_script._PUSH_STATE["empty"] is None
    assert evening_script._PUSH_STATE["has_list"] is None


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 🔴 R2-04:**半途失败⛔ 不许渲染成「今天没有」**
# ══════════════════════════════════════════════════════════════════════════
#
# 架构 §3.5 设计三态的**全部理由**就是「空清单可以被信任」;裁定 5 逐字区分
# 「今天没有 = 跑通了、结果为空」与「今天没跑成 = 系统没工作」。
#
# 复审 CE-6 的反例:`k9/run.py::persist` 是 `save_run` → `save_channel_hits`
# → `save_listing` 三步,中间炸掉留下「运行账有行(`seated_count=2`)、清单零行」,
# 而报告渲染成 `state=empty / headline=今天没有 / gaps=()` —— 推送还走
# `_PUSH_STATE["empty"]` 的**正常文案**。库里唯一自相矛盾的证据没人比对。
# ══════════════════════════════════════════════════════════════════════════

class TestAHalfWrittenRunIsNeverRenderedAsAnEmptyDay:
    def test_a_run_ledger_that_disagrees_with_the_listing_table_is_not_run(
            self, market, tmp_path):
        """🔴 复审 CE-6 的原样复现:运行账说 N 只、清单表零行。"""
        from neckline.k9 import store as k9_store

        env, day = market
        res = _chain(env, day, params_path=_params_file(env, tmp_path))
        assert res.bundle.state is ReportState.HAS_LIST
        seated = res.bundle.listing_size
        assert seated > 0

        # —— 把清单表清空,运行账原样留着 = `save_listing` 那一步炸掉的现场 ——
        from neckline.db import connection
        with connection(env.db_path) as conn:
            conn.execute("DELETE FROM k9_listing_entries WHERE trade_date=?",
                         (day.strftime("%Y%m%d"),))
        assert k9_store.load_listing(day, db_path=env.db_path) == []
        assert k9_store.load_run(day, db_path=env.db_path)["seated_count"] == seated

        bundle = pipeline_mod.build_report(
            day, report_date=day, db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert bundle.state is ReportState.NOT_RUN, "半途失败被渲染成了「今天没有」"
        assert bundle.markdown.splitlines()[0].startswith("# 今天没跑成 · ")
        assert any("运行账与清单表对不上" in g for g in bundle.gaps), bundle.gaps
        assert f"k9_runs 说 {seated} 只" in "".join(bundle.gaps)
        # ⚠ 首行之外,清单段也必须说这句话(⛔ 不许只在 gaps 里悄悄记一笔)。
        assert "⚠ 这是**系统没工作**,不是「今天没有」。" in bundle.markdown

    def test_a_k9_segment_that_blew_up_is_reported_even_though_a_run_row_exists(
            self, market, tmp_path, monkeypatch):
        """🔴 上游段**自己说了**它炸了 —— 报告⛔ 不许把这句话丢掉。

        「报告不猜别人的失败原因」⛔ 不要求把别人**说了**的原因扔了。
        """
        env, day = market
        # 先跑一次完整链把运行账 + 清单落好(= 那天确实有一份运行账)。
        _chain(env, day, params_path=_params_file(env, tmp_path))

        # 再跑一次:k9 段在**落库之后**炸掉(最难发现的那种半途失败)。
        from neckline.k9 import run as k9_run

        real = k9_run.run_k9

        def boom(*a, **kw):
            real(*a, **kw)
            raise RuntimeError("save_channel_hits 挂了")

        monkeypatch.setattr(k9_run, "run_k9", boom)
        res = _chain(env, day, params_path=_params_file(env, tmp_path),
                     segments=("k9", "report"))
        assert res.status["k9"] == evening_mod.STATUS_FAILED
        assert res.bundle.state is ReportState.NOT_RUN
        assert any("k9 段失败" in g for g in res.bundle.gaps), res.bundle.gaps

    def test_a_genuinely_empty_day_is_still_trusted(self, market, tmp_path):
        """⚠ 反向自检:真的跑通、真的没有票的日子 **仍然**是「今天没有」。

        ⛔ 这条修复不许把「可信的空」也一起打成「没跑成」—— 那是另一个方向的谎话。
        """
        env, day = market
        path = _params_file(
            env, tmp_path,
            **{"channels.p1.strict.ampMaxPct": 0.001,
               "channels.p1.relaxed.ampMaxPct": 0.001,
               "channels.p2.strict.normDropMin": 0.999,
               "channels.p2.relaxed.normDropMin": 0.999,
               "channels.p3.strict.flatBand": 1e-9,
               "channels.p3.relaxed.flatBand": 1e-9,
               "channels.p4.strict.lagRankGap": 0.999,
               "channels.p4.relaxed.lagRankGap": 0.999})
        res = _chain(env, day, params_path=path)
        assert res.bundle.state is ReportState.EMPTY
        assert res.bundle.gaps == ()
        assert res.bundle.listing_size == 0


def test_upstream_gaps_and_upstream_failures_are_two_different_doors():
    """🔴 两个口子⛔ 不许合并(R2-04)。

    · `upstream_gaps`  = 「k9 段**为什么没跑**」 → 关于**这一次调用**,
      只在没有运行账时采纳(分段跑 `--segments report` 时曾因此误报「今天没跑成」);
    · `upstream_failures` = 「某段**跑了、炸了**」 → 关于**这一天**,恒采纳。
    """
    import inspect

    sig = inspect.signature(pipeline_mod.build_report)
    assert "upstream_gaps" in sig.parameters and "upstream_failures" in sig.parameters


def test_explain_and_playbook_failures_do_not_hide_a_usable_listing():
    """⚠ 只有 `k9` 段的失败会翻成「今天没跑成」。

    explain / playbook 炸掉时清单本身仍然成立,而它们的缺席**各自已有诚实披露**
    (`listing_finalized_by='k9'` / 逐只那句「没有冻结预案」)。把它们也翻成
    「今天没跑成」会把一份**可用**的清单整段藏起来。
    """
    res = evening_mod.EveningChainResult(trade_date=date(2026, 8, 20),
                                         report_date=date(2026, 8, 20))
    res.status = {s: evening_mod.STATUS_FAILED for s in evening_mod.CHAIN_SEGMENTS}
    res.status[evening_mod.SEG_K9] = evening_mod.STATUS_OK
    assert evening_mod._upstream_failures(res) == []
    res.status[evening_mod.SEG_K9] = evening_mod.STATUS_FAILED
    assert len(evening_mod._upstream_failures(res)) == 1
