"""次日核对与 D1 结算(V2.5.0 S8,PROJECT_PLAN §6 S8 验收 + §5.7)。

| # | 验收 | section |
|---|---|---|
| 1 | 求值器:闭合枚举 / 三值 Kleene / 放弃优先 / 未知 MetricRef 拒绝冻结 | ① |
| 2 | 9:26 拍:窗口门 · 当日防重 · 窗口外零落库 · ⛔ 事后补跑被拒 · 9:29 未完成不发布 | ② |
| 3 | 🔴 `ChecklistVerdict` 恰好两个成员;「成立」在类型层面不存在(G20) | ③ |
| 4 | 🔴 10:00 结算拍:三分支 · 先到先定不改判 · 观察不进分子分母 · 零推送(G21) | ④ |
| 5 | D0 → D1 端到端 | ⑤ |
| 6 | API:`/checklist/{date}` 响应体无「成立」取值;`/scoreboard/verdicts/{date}` | ⑥ |

结构性守门(AST / 全仓扫描)见 `test_v250_s8_auction_guard.py`。
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta

import pytest

from neckline.auction import checklist as checklist_mod
from neckline.auction import collect as ac
from neckline.auction import pipeline as auction_pipeline
from neckline.auction import settle as settle_mod
from neckline.auction import store as auction_store
from neckline.data.realtime import DualQuote, Quote
from neckline.k9 import run as k9_run
from neckline.k9 import store as k9_store
from neckline.playbook import store as pb_store
from neckline.playbook.evaluate import (
    Truth,
    Verdict,
    evaluate_branch,
    evaluate_condition,
    settle_verdict,
)
from neckline.playbook.model import (
    Branch,
    BranchName,
    Condition,
    Levels,
    MetricRef,
    Op,
    Playbook,
    PlaybookInvalid,
    parse_playbook,
)
from tests import k9_env


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def make_playbook(
    ts_code: str, *, trade_date: str = "20240429", pattern: str = "p1",
    reject_below: float = 9.5, confirm_gap_max: float = 5.0,
    confirm_low_min: float = 10.5, version: int = 1,
) -> Playbook:
    """一份 p1 骨架的预案(数值是**夹具**,⛔ 不是标定值)。"""
    return Playbook(
        trade_date=trade_date, ts_code=ts_code, pattern=pattern,
        levels=Levels(first_resistance=11.0, second_resistance=12.0, invalidation=9.5),
        branches=(
            Branch(name=BranchName.CONFIRMED, all=(
                Condition(op=Op.LE, lhs=MetricRef.GAP_PCT, rhs=confirm_gap_max),
                Condition(op=Op.GE, lhs=MetricRef.FIRST30_LOW, rhs=confirm_low_min),
            )),
            Branch(name=BranchName.REJECTED, all=(
                Condition(op=Op.LT, lhs=MetricRef.FIRST30_LOW, rhs=reject_below),
            )),
        ),
        version=version, filled_by="fixture", filled_at="2024-04-29T15:30:00",
    )


def quote(code: str, *, price: float, pre_close: float = 10.0, ts: str,
          open_: float = 0.0, high: float = 0.0, low: float = 0.0,
          source: str = "sina") -> Quote:
    return Quote(
        code=code.split(".")[0], name="示例", price=price, pre_close=pre_close,
        open=open_, high=high, low=low, volume=1000.0, amount=10000.0,
        ts=ts, source=source,
    )


def dual_fn(quotes: dict):
    def _fn(codes):
        return {c: DualQuote(code=c, primary=quotes.get(c)) for c in codes}
    return _fn


@pytest.fixture
def d0d1(isolated_env, tmp_path):
    """铺好合成市场 → 在 **D0** 跑一遍策略层落清单 → 为清单上每只票冻一份预案。

    返回 `(env, d0, d1, codes)`。D1 = 合成日历的最后一个交易日,D0 = 它前一天。"""
    last = k9_env.seed(isolated_env)
    d1 = last
    from neckline.calendar import prev_trading_day
    d0 = prev_trading_day(d1)
    params = k9_env.params(isolated_env, tmp_path)
    result, _run_id = k9_run.run_k9(
        d0, params=params, parquet_dir=isolated_env.parquet_dir,
        db_path=isolated_env.db_path)
    codes = [e.ts_code for e in result.shortlist.entries]
    assert codes, "夹具必须能出一份非空清单"
    for c in codes:
        pb_store.save(make_playbook(c, trade_date=d0.strftime("%Y%m%d")),
                      db_path=isolated_env.db_path)
    return isolated_env, d0, d1, codes


def at(day: date, t: time) -> datetime:
    return datetime.combine(day, t)


# ══════════════════════════════════════════════════════════════════════════
# ① 求值器(唯一实现,两拍共用)
# ══════════════════════════════════════════════════════════════════════════

class TestEvaluator:
    def test_unknown_metric_ref_is_refused_at_freeze_time(self):
        """🔴 闭合枚举 = 求值器是全函数:未知 `MetricRef` **D0 当场拒绝冻结**,
        ⛔ 绝不让一个次日早上求不出值的条件被冻进去(§5.6.3)。"""
        raw = {
            "tsCode": "600001.SH", "pattern": "p1",
            "levels": {"firstResistance": 11.0, "secondResistance": 12.0,
                       "invalidation": 9.5},
            "branches": [
                {"name": "成立", "all": [{"op": ">=", "lhs": "vwap_30min", "rhs": 10.0}]},
                {"name": "放弃", "all": [{"op": "<", "lhs": "first30_low", "rhs": 9.5}]},
            ],
        }
        with pytest.raises(PlaybookInvalid) as e:
            parse_playbook(raw)
        assert "vwap_30min" in str(e.value)

    def test_natural_language_condition_is_refused(self):
        """⛔ 不许输出自然语言条件(架构 §3.4 硬约束)。"""
        raw = {
            "tsCode": "600001.SH", "pattern": "p1",
            "levels": {"firstResistance": 11.0, "secondResistance": 12.0,
                       "invalidation": 9.5},
            "branches": [
                {"name": "成立", "all": [{"op": ">=", "lhs": "first30_low",
                                          "rhs": "不破昨日平台"}]},
                {"name": "放弃", "all": [{"op": "<", "lhs": "first30_low", "rhs": 9.5}]},
            ],
        }
        with pytest.raises(PlaybookInvalid):
            parse_playbook(raw)

    def test_missing_reading_is_unknown_not_false(self):
        """🔴 三值:读不到 → `UNKNOWN`,⛔ 不是 `FALSE`。
        把「没判」折成「判过了、不成立」是本仓栽过三次的那族病。"""
        c = Condition(op=Op.GE, lhs=MetricRef.FIRST30_LOW, rhs=10.0)
        truth, trace = evaluate_condition(c, {MetricRef.FIRST30_LOW: None})
        assert truth is Truth.UNKNOWN and trace.lhs_value is None
        truth2, _ = evaluate_condition(c, {})
        assert truth2 is Truth.UNKNOWN

    def test_conjunction_is_kleene(self):
        """合取:有 `FALSE` → `FALSE`(即使还有读不到的项);否则有 `UNKNOWN` → `UNKNOWN`。"""
        b = Branch(name=BranchName.CONFIRMED, all=(
            Condition(op=Op.LE, lhs=MetricRef.GAP_PCT, rhs=5.0),
            Condition(op=Op.GE, lhs=MetricRef.FIRST30_LOW, rhs=10.5),
        ))
        assert evaluate_branch(b, {MetricRef.GAP_PCT: 9.0}).truth is Truth.FALSE
        out = evaluate_branch(b, {MetricRef.GAP_PCT: 1.0})
        assert out.truth is Truth.UNKNOWN and "first30_low" in out.missing
        assert evaluate_branch(
            b, {MetricRef.GAP_PCT: 1.0, MetricRef.FIRST30_LOW: 10.6}).truth is Truth.TRUE

    def test_rejection_beats_confirmation(self):
        """两条同时为真 → **放弃赢**(见 `evaluate.py` 模块头)。"""
        pb = make_playbook("600001.SH", reject_below=11.0, confirm_low_min=10.0,
                           confirm_gap_max=50.0)
        out = settle_verdict(pb, {MetricRef.GAP_PCT: 1.0, MetricRef.FIRST30_LOW: 10.5})
        assert out.rejected.truth is Truth.TRUE and out.confirmed.truth is Truth.TRUE
        assert out.verdict is Verdict.REJECTED

    def test_neither_branch_true_is_observed(self):
        """两条都不为真(含读不到)→ 观察。"""
        pb = make_playbook("600001.SH")
        assert settle_verdict(pb, {}).verdict is Verdict.OBSERVED


# ══════════════════════════════════════════════════════════════════════════
# ② 9:26 那一拍的窗口纪律
# ══════════════════════════════════════════════════════════════════════════

class TestAuctionWindowDiscipline:
    def test_outside_window_writes_nothing(self, d0d1):
        """🔴 窗口外调用**零落库**,⛔ 事后不许补跑 —— 补跑会拿 9:30 之后的价格
        冒充 9:26 那一刻的判断。"""
        env, d0, d1, codes = d0d1
        for t in (time(9, 25, 59), time(9, 29, 0), time(10, 30), time(15, 0)):
            res = auction_pipeline.run_checklist_tick(
                at(d1, t), db_path=env.db_path, parquet_dir=env.parquet_dir,
                quotes_fn=lambda cs: {},
            )
            assert res.ran is False
            assert res.skipped_reason == auction_pipeline.SKIP_NOT_WINDOW
        assert auction_store.load_checklist(d1, db_path=env.db_path) is None
        assert auction_store.load_verdicts(d1, db_path=env.db_path) == []

    def test_non_trading_day_is_refused(self, d0d1):
        env, d0, d1, codes = d0d1
        # 合成日历之外的**周日** —— 日历退化成工作日近似时它仍然不是交易日。
        sunday = d1 + timedelta(days=(6 - d1.weekday()) % 7 + 7)
        assert sunday.weekday() == 6
        res = auction_pipeline.run_checklist_tick(
            at(sunday, time(9, 27)), db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert res.ran is False and res.skipped_reason == auction_pipeline.SKIP_NOT_WINDOW

    def test_runs_once_a_day(self, d0d1):
        """当日防重:第二次调用 `already_ran`,且**不再落一份新表**。"""
        env, d0, d1, codes = d0d1
        qs = {c: quote(c, price=10.2, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        kw = dict(db_path=env.db_path, parquet_dir=env.parquet_dir,
                  quotes_fn=dual_fn(qs) and (lambda cs: {c: qs[c] for c in cs if c in qs}),
                  now_fn=lambda: at(d1, time(9, 26, 30)))
        first = auction_pipeline.run_checklist_tick(at(d1, time(9, 26, 10)), **kw)
        assert first.ran is True
        second = auction_pipeline.run_checklist_tick(at(d1, time(9, 27, 10)), **kw)
        assert second.ran is False
        assert second.skipped_reason == "already_ran"

    def test_window_closed_before_fetch_writes_nothing(self, d0d1):
        """名义时刻在窗口、**真到拉价那一刻已越窗** → 一条价都不拉、零落库。
        ⚠ 与「名义时刻就不在窗口」**分成两个码**:混起来查 journal 时分不出
        是排程错了还是慢了。"""
        env, d0, d1, codes = d0d1
        pulled = []

        def _quotes(cs):
            pulled.extend(cs)
            return {}

        res = auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=_quotes, now_fn=lambda: at(d1, time(9, 31, 0)),
        )
        assert res.ran is False
        assert res.skipped_reason == "window_closed_before_fetch"
        assert pulled == [], "越窗后⛔ 一条价都不许拉"
        assert auction_store.load_checklist(d1, db_path=env.db_path) is None

    def test_missing_the_929_deadline_publishes_nothing(self, d0d1):
        """🔴 9:29 硬截止:落库前用真实时钟再看一眼,过点了就**不发布**
        (记 `deadline_missed`,⛔ 不迟到发布)。"""
        env, d0, d1, codes = d0d1
        clock = iter([
            at(d1, time(9, 26, 30)),   # collect:拉价前复判
            at(d1, time(9, 26, 40)),   # collect:captured_at
            at(d1, time(9, 29, 30)),   # pipeline:硬截止复判 —— 过点了
        ])
        qs = {c: quote(c, price=10.2, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        res = auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: next(clock),
        )
        assert res.ran is False
        assert res.skipped_reason == auction_pipeline.SKIP_DEADLINE_MISSED
        assert auction_store.load_checklist(d1, db_path=env.db_path) is None
        # ⛔ 也没落「当日已跑」标记 —— 今天压根没跑成,下一拍还能干净重跑。
        from neckline.dedup import already_pushed
        assert not already_pushed(d1, auction_pipeline.AUCTION_SCOPE, "",
                                  auction_pipeline.EVENT_CHECKLIST, db_path=env.db_path)

    def test_no_listing_means_nothing_to_check(self, isolated_env, tmp_path):
        """昨天没有清单 = **可信的空**:零落库、零推送(⛔ 不是故障)。"""
        env = isolated_env
        last = k9_env.seed(env)
        res = auction_pipeline.run_checklist_tick(
            at(last, time(9, 26, 30)), db_path=env.db_path, parquet_dir=env.parquet_dir)
        assert res.ran is False and res.skipped_reason == "no_listing"
        assert res.should_push is False


# ══════════════════════════════════════════════════════════════════════════
# ③ 🔴 二值:「成立」在类型层面不存在(裁定 10 / G20)
# ══════════════════════════════════════════════════════════════════════════

class TestChecklistIsTwoValued:
    def test_enum_has_exactly_two_members(self):
        assert len(checklist_mod.ChecklistVerdict) == 2
        assert {v.value for v in checklist_mod.ChecklistVerdict} == {
            "rejected", "pending_open"}
        assert set(checklist_mod.CHECKLIST_SEGMENT_LABEL.values()) == {
            "已触发放弃", "待开盘后观察"}

    def test_auction_readings_cannot_answer_the_confirmation_branch(self, d0d1):
        """🔴 第二重锁:9:26 那一拍的读数表里**根本没有** `open_price` /
        `gap_pct` / `first30_high` —— 就算有人把成立分支拿去求值,
        也只会得到 `UNKNOWN`(K9 §6.3 四个成立分支全含「前 30 分钟」合取项)。"""
        env, d0, d1, codes = d0d1
        snap = ac.Snapshot(
            trade_date=d1, d0_date=d0, window=(time(9, 26), time(9, 29)),
            captured_at=at(d1, time(9, 26, 30)),
            prev_bars={codes[0]: ac.PrevBar(ts_code=codes[0], close=10.0, low=9.8, high=10.4)},
        )
        readings = checklist_mod.auction_readings(snap, codes[0])
        assert MetricRef.OPEN_PRICE not in readings
        assert MetricRef.GAP_PCT not in readings
        assert MetricRef.FIRST30_HIGH not in readings
        pb = make_playbook(codes[0])
        assert evaluate_branch(pb.confirmation_branch, readings).truth is Truth.UNKNOWN

    def test_checklist_output_has_only_two_segments(self, d0d1):
        env, d0, d1, codes = d0d1
        qs = {c: quote(c, price=9.0, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        res = auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)),
        )
        assert res.ran is True and res.rejected == len(codes)
        payload = res.checklist.to_dict()
        assert [s["verdict"] for s in payload["segments"]] == ["rejected", "pending_open"]
        assert "成立由 10:00 结算" in payload["footnote"]
        blob = json.dumps(payload, ensure_ascii=False)
        # 「成立」只准出现在那句脚注里,⛔ 不许作为任何一段的取值。
        assert blob.count("成立") == 1

    def test_breaking_the_level_at_auction_lands_in_the_rejected_segment(self, d0d1):
        env, d0, d1, codes = d0d1
        broken, intact = codes[0], codes[1] if len(codes) > 1 else codes[0]
        qs = {c: quote(c, price=(9.0 if c == broken else 10.2),
                       ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        res = auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)),
        )
        assert broken in [r.ts_code for r in res.checklist.rejected]
        if intact != broken:
            assert intact in [r.ts_code for r in res.checklist.pending_open]

    def test_auction_stage_only_ever_writes_the_rejected_final_value(self, d0d1):
        """🔴 第三重锁:9:26 的写路径收的是**二值枚举**,映射到终值的表里
        根本没有「成立」—— `Verdict.CONFIRMED` 在这条路上构造不出来。"""
        assert set(auction_store._AUCTION_FINAL.values()) == {Verdict.REJECTED, None}
        env, d0, d1, codes = d0d1
        qs = {c: quote(c, price=(9.0 if c == codes[0] else 10.2),
                       ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)))
        rows = auction_store.load_verdicts(d1, db_path=env.db_path)
        assert {r["verdict"] for r in rows} <= {"rejected", None}
        for r in rows:
            assert r["decided_stage"] in (auction_store.STAGE_AUCTION, None)


# ══════════════════════════════════════════════════════════════════════════
# ④ 🔴 10:00 结算拍(裁定 10:三分支判定的唯一权威)
# ══════════════════════════════════════════════════════════════════════════

def _settle(env, d1, codes, prices, *, t=time(10, 0, 30)):
    """跑一次结算拍。`prices[code] = (open, high, low, price)`。"""
    qs = {}
    for c in codes:
        o, h, lo, p = prices[c]
        qs[c] = quote(c, price=p, open_=o, high=h, low=lo,
                      ts=f"{d1:%Y-%m-%d} 10:00:01")
    return settle_mod.run_settle_tick(
        at(d1, time(10, 0, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
        quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
        now_fn=lambda: at(d1, t))


class TestSettleTick:
    def test_window_and_dedup_and_no_backfill(self, d0d1):
        env, d0, d1, codes = d0d1
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
        for t in (time(9, 59, 59), time(10, 5, 0), time(11, 0)):
            res = settle_mod.run_settle_tick(
                at(d1, t), db_path=env.db_path, parquet_dir=env.parquet_dir,
                quotes_fn=lambda cs: {})
            assert res.ran is False and res.skipped_reason == "not_window"
        assert auction_store.load_verdicts(d1, db_path=env.db_path) == []
        first = _settle(env, d1, codes, prices)
        assert first.ran is True and first.settled == len(codes)
        second = _settle(env, d1, codes, prices)
        assert second.ran is False and second.skipped_reason == "already_ran"

    def test_confirmed_comes_only_from_the_open30_stage(self, d0d1):
        """🔴 三分支的**唯一权威**是这一拍:`decided_stage='open30'`。"""
        env, d0, d1, codes = d0d1
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}   # 高开 2%、不破 10.5
        res = _settle(env, d1, codes, prices)
        assert res.confirmed == len(codes) and res.rejected == 0 and res.observed == 0
        rows = auction_store.load_verdicts(d1, db_path=env.db_path)
        assert all(r["verdict"] == "confirmed" for r in rows)
        assert all(r["decided_stage"] == "open30" for r in rows)
        assert all(r["auction_verdict"] is None for r in rows)   # 那一拍今天没跑

    def test_observed_when_neither_branch_fires(self, d0d1):
        """既不成立也不放弃 → 观察(⛔ 不进三个比率的分子分母 —— 那条由
        `scorecard` 侧读 `verdict='observed'` 时排除,这里先把终值判对)。"""
        env, d0, d1, codes = d0d1
        # 高开 2%(≤5 满足),但前 30 分钟最低 10.0 < 10.5 → 成立不成立;
        # 也没跌破 9.5 → 放弃不成立。
        prices = {c: (10.2, 10.6, 10.0, 10.1) for c in codes}
        res = _settle(env, d1, codes, prices)
        assert res.observed == len(codes)
        rows = auction_store.load_verdicts(d1, db_path=env.db_path)
        assert all(r["verdict"] == "observed" for r in rows)

    def test_auction_decided_rows_are_never_overturned(self, d0d1):
        """🔴 **先到先定**(裁定 10):9:29 判「放弃」的票,10:00 ⛔ 不改判。
        幂等靠 `WHERE decided_stage IS NULL`,不靠谁记得跳过。"""
        env, d0, d1, codes = d0d1
        dead = codes[0]
        alive = codes[1] if len(codes) > 1 else None
        qs = {c: quote(c, price=(9.0 if c == dead else 10.2),
                       ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)))
        before = {r["ts_code"]: dict(r) for r in
                  auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert before[dead]["decided_stage"] == "auction"
        assert before[dead]["verdict"] == "rejected"

        # 10:00 时那只已死的票**反弹回来了**(成立分支的读数全都满足)——
        # 它照样不许被改判成「成立」。
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
        res = _settle(env, d1, codes, prices)
        after = {r["ts_code"]: dict(r) for r in
                 auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert after[dead]["verdict"] == "rejected"
        assert after[dead]["decided_stage"] == "auction"
        assert after[dead]["settled_at"] is None
        assert after[dead]["auction_readings"] == before[dead]["auction_readings"]
        if alive is not None:
            assert after[alive]["decided_stage"] == "open30"
        assert res.unchanged == 1

    def test_the_settle_tick_uses_the_version_pinned_at_the_auction_tick(self, d0d1):
        """🔴 **R2-03 的第二道锁 —— 复审 CE-5 原样复现**。

            9:27 判「待开盘后观察」 → 9:45 改一版把成立门槛压到脚下 → 10:01 结算

        复审实测的结果是 `confirmed / open30`,而 `k9_d1_verdicts.playbook_version`
        仍记着 v1 —— 裁定 10 说的「三分支的唯一权威是 10:00 这一拍」被抽掉了分母:
        权威那一拍代入的是一份**在看过竞价之后**才写下的条件,且在账上查不出来。

        修完之后:结算拍代入的仍然是**账上钉死的那一版**,终值 ⛔ 不被改写后的
        条件带跑,而且「有人改过」这件事**说出来**(`res.notes`)。
        """
        env, d0, d1, codes = d0d1
        target = codes[0]

        # —— 9:27:竞价价 10.2,放弃分支(first30_low < 9.5)求不出 → 待观察 ——
        qs = {c: quote(c, price=10.2, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 27, 0)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 27, 10)))
        rows = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert rows[target]["verdict"] is None and rows[target]["decided_stage"] is None
        assert rows[target]["playbook_version"] == 1

        # —— 9:45:把成立门槛压到脚下(v2)——
        pb_store.save(
            make_playbook(target, trade_date=d0.strftime("%Y%m%d"),
                          confirm_gap_max=99.0, confirm_low_min=0.01, version=2),
            db_path=env.db_path)
        assert pb_store.load_latest(d0, db_path=env.db_path)[target].version == 2

        # —— 10:01:一组**按 v1 判不成立、按 v2 判成立**的读数 ——
        #    v1 要 first30_low >= 10.5;这里 low=10.0 → v1 不成立、v2 成立。
        prices = {c: (10.2, 10.6, 10.0, 10.1) for c in codes}
        res = _settle(env, d1, codes, prices)

        after = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert after[target]["verdict"] == "observed", (
            "结算拍代入了 9:26 之后才写下的 v2 —— 裁定 10 的分母被抽掉了")
        assert after[target]["decided_stage"] == "open30"
        # 账上记的版本 = 真正求值用的那一版(⛔ 两者不许对不上)。
        assert after[target]["playbook_version"] == 1
        # 🔴 「有人改过」必须**说出来**,⛔ 不静默按旧版跑过去。
        assert any(target in n and "9:26 之后被改写" in n for n in res.notes), res.notes

    def test_a_version_written_before_the_auction_tick_is_the_one_that_counts(self, d0d1):
        """⚠ 反向自检:D0 盘后**正常改的**那一版(9:26 之前就在库里)照常生效。

        闸门管的是「在看过今天的盘之后改」,⛔ 不是「预案从此不能改」——
        K9 §6.4 的「最终确认由我盘后逐只过目、可修改」必须仍然成立。
        """
        env, d0, d1, codes = d0d1
        target = codes[0]
        # D0 盘后:用户把成立门槛改宽(v2),**在 9:26 那一拍之前**就落库了。
        pb_store.save(
            make_playbook(target, trade_date=d0.strftime("%Y%m%d"),
                          confirm_gap_max=99.0, confirm_low_min=0.01, version=2),
            db_path=env.db_path)
        qs = {c: quote(c, price=10.2, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 27, 0)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 27, 10)))
        rows = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert rows[target]["playbook_version"] == 2, "9:26 那一拍就该记 v2"

        prices = {c: (10.2, 10.6, 10.0, 10.1) for c in codes}
        res = _settle(env, d1, codes, prices)
        after = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
        assert after[target]["verdict"] == "confirmed", "D0 盘后正常改的那一版没生效"
        assert after[target]["playbook_version"] == 2
        assert not [n for n in res.notes if "被改写" in n], res.notes

    def test_settle_merges_the_frozen_auction_readings(self, d0d1):
        """竞价那半从 9:26 冻结的读数取回来,⛔ 不重拉一个「现在的竞价价」。"""
        env, d0, d1, codes = d0d1
        qs = {c: quote(c, price=10.15, ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)))
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
        _settle(env, d1, codes, prices)
        rows = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
        got = rows[codes[0]]["open30_readings"]
        assert got["auction_price"] == pytest.approx(10.15)
        assert got["open_price"] == pytest.approx(10.5)
        assert got["first30_low"] == pytest.approx(10.5)

    def test_the_three_counts_only_count_rows_that_actually_landed(self, d0d1):
        """🔴 **R2-10**:三分支计数只数**真的被 UPDATE 到**的那些。

        从前 `settled` 取真实 rowcount、而 `confirmed/rejected/observed` 直接从
        `outcomes` 里数 —— 并发写(有人在 `undecided_codes` 与 `settle_verdicts`
        之间把行定案了)会让日志报出一个**没有落库的分布**。
        """
        env, d0, d1, codes = d0d1
        # 先让 9:26 那一拍把第一只判「放弃」并定案。
        dead = codes[0]
        qs = {c: quote(c, price=(9.0 if c == dead else 10.2),
                       ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
        auction_pipeline.run_checklist_tick(
            at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
            quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
            now_fn=lambda: at(d1, time(9, 26, 30)))

        # 10:00 给一组会判「成立」的读数 —— 已定案那只 ⛔ 改不动,
        # 于是它的 `confirmed` **不许**被计进去。
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
        res = _settle(env, d1, codes, prices)
        rows = auction_store.load_verdicts(d1, db_path=env.db_path)
        landed = [r for r in rows if r["decided_stage"] == "open30"]
        assert res.settled == len(landed)
        assert res.confirmed == sum(1 for r in landed if r["verdict"] == "confirmed")
        assert res.confirmed + res.rejected + res.observed == res.settled, (
            "计数之和与真的落库的行数对不上 —— 账上的数在描述一个没发生的分布")
        assert dead not in {r["ts_code"] for r in landed}

    def test_settle_pushes_nothing(self, d0d1, monkeypatch):
        """🔴 **G21:结算拍零推送** —— 跑一次结算后 APNs 调用计数 = 0(裁定 10)。"""
        from neckline.push import apns as apns_mod

        calls = []
        monkeypatch.setattr(apns_mod, "send_push",
                            lambda *a, **k: calls.append(a) or None, raising=False)
        env, d0, d1, codes = d0d1
        prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
        res = _settle(env, d1, codes, prices)
        assert res.ran is True
        assert calls == []
        assert not hasattr(res, "should_push")


# ══════════════════════════════════════════════════════════════════════════
# ⑤ D0 → D1 端到端
# ══════════════════════════════════════════════════════════════════════════

def test_d0_to_d1_end_to_end(d0d1):
    """D0 出清单 + 冻预案 → 9:26 两段核对表 → 10:00 三分支终值。"""
    env, d0, d1, codes = d0d1
    dead = codes[0]
    qs = {c: quote(c, price=(9.0 if c == dead else 10.2),
                   ts=f"{d1:%Y-%m-%d} 09:25:03") for c in codes}
    tick = auction_pipeline.run_checklist_tick(
        at(d1, time(9, 26, 10)), db_path=env.db_path, parquet_dir=env.parquet_dir,
        quotes_fn=lambda cs: {c: qs[c] for c in cs if c in qs},
        now_fn=lambda: at(d1, time(9, 26, 30)))
    assert tick.ran and tick.rejected == 1 and tick.pending_open == len(codes) - 1
    assert tick.should_push is True

    prices = {c: (10.5, 10.9, 10.5, 10.8) for c in codes}
    res = _settle(env, d1, codes, prices)
    rows = {r["ts_code"]: r for r in auction_store.load_verdicts(d1, db_path=env.db_path)}
    assert rows[dead]["decided_stage"] == "auction" and rows[dead]["verdict"] == "rejected"
    for c in codes[1:]:
        assert rows[c]["decided_stage"] == "open30" and rows[c]["verdict"] == "confirmed"
    assert res.settled == len(codes) - 1


# ══════════════════════════════════════════════════════════════════════════
# ⑥ API
# ══════════════════════════════════════════════════════════════════════════

class TestApi:
    def test_checklist_endpoint_has_no_confirmed_value(self, api_env, client, AUTH):
        """🔴 G20:`/checklist/{date}` 的响应体里**不存在「成立」取值**。"""
        d0, d1 = date(2024, 4, 29), date(2024, 4, 30)
        codes = ["600001.SH", "600002.SH"]
        cl = checklist_mod.build_checklist(
            ac.Snapshot(
                trade_date=d1, d0_date=d0, window=(time(9, 26), time(9, 29)),
                captured_at=at(d1, time(9, 26, 30)),
                prev_bars={c: ac.PrevBar(ts_code=c, close=10.0, low=9.8, high=10.4)
                           for c in codes},
            ),
            playbooks={c: make_playbook(c) for c in codes},
            listing_codes=codes)
        auction_store.save_checklist(cl, db_path=api_env.db_path)
        r = client.get(f"/api/v1/checklist/{d1:%Y%m%d}", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert [s["verdict"] for s in body["segments"]] == ["rejected", "pending_open"]
        assert json.dumps(body, ensure_ascii=False).count("成立") == 1   # 只在脚注里

    def test_checklist_404_means_the_tick_never_ran(self, api_env, client, AUTH):
        r = client.get("/api/v1/checklist/20240430", headers=AUTH)
        assert r.status_code == 404
        detail = r.json()["detail"]
        assert detail == (
            "2024年4月30日没有竞价核对表：2024年4月29日没有清单，"
            "今天没有要核对的东西。这是“没有”，不是“没跑成”。"
        )
        assert "**" not in detail and "20240430" not in detail

    def test_verdicts_endpoint_reports_the_decided_stage(self, api_env, client, AUTH):
        day = date(2024, 4, 30)
        auction_store.ensure_rows(
            day, d0_date=date(2024, 4, 29),
            rows=[{"ts_code": "600001.SH", "pattern": "p1", "playbook_version": 1}],
            db_path=api_env.db_path)
        pb = make_playbook("600001.SH")
        auction_store.settle_verdicts(
            day, [settle_verdict(pb, {MetricRef.GAP_PCT: 1.0, MetricRef.FIRST30_LOW: 10.9})],
            readings_by_code={"600001.SH": {"gap_pct": 1.0, "first30_low": 10.9}},
            db_path=api_env.db_path)
        r = client.get(f"/api/v1/scoreboard/verdicts/{day:%Y%m%d}", headers=AUTH)
        assert r.status_code == 200
        v = r.json()["verdicts"][0]
        assert v["verdict"] == "confirmed" and v["decidedStage"] == "open30"

    def test_verdicts_null_means_undecided_not_observed(self, api_env, client, AUTH):
        """⚠ `verdict=null` = **今天还没定案**,⛔ 不是「观察」。"""
        day = date(2024, 4, 30)
        auction_store.ensure_rows(
            day, d0_date=date(2024, 4, 29),
            rows=[{"ts_code": "600002.SH", "pattern": "p2", "playbook_version": 1}],
            db_path=api_env.db_path)
        r = client.get(f"/api/v1/scoreboard/verdicts/{day:%Y%m%d}", headers=AUTH)
        v = r.json()["verdicts"][0]
        assert v["verdict"] is None and v["decidedStage"] is None
