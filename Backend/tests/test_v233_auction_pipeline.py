"""V2.3.3 批 ④:编排(9:26 起跑 · **9:29 硬截止** · 当日防重 · 推送门槛)。

最要命的两条在这里被正面钉死:
  1. **注入一个 sleep 很久的假 provider** → 硬截止之前返回、表里 `pending_explanation`,
     **且之后再等,表内容一字不变**(迟到的结论被丢弃);
  2. **窗口外调用零落库**(⛔ 事后不许补跑 —— 补跑会拿 9:30 之后的价格冒充 9:26 那一刻)。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time as _time
from datetime import date, datetime, time
from typing import Any, Dict, List

import pytest

from neckline.auction import (
    LLM_NO_PROVIDER,
    LLM_OK,
    LLM_PENDING_EXPLANATION,
    VERDICT_CONFIRM,
    VERDICT_NEUTRAL,
    VERDICT_PENDING_EXPLANATION,
)
from neckline.auction import collect as ac
from neckline.auction import pipeline as ap
from neckline.auction import store as astore
from neckline.llm.base import LLMResult
from neckline.sentinel.dedup import already_pushed

from tests.conftest import insert_stock_basic
from tests.test_v233_auction_mech import _card_json, _q, _seed_basket

D1 = date(2026, 8, 11)
D0 = date(2026, 8, 10)
IN_WINDOW = datetime(2026, 8, 11, 9, 26, 30)
DEADLINE = datetime(2026, 8, 11, 9, 29, 0)


class _Provider:
    """假 provider。`sleep` > 0 时模拟"9:29 到了还没回"。"""

    name, model = "stub", "stub-model"

    def __init__(self, keys: List[str], *, sleep: float = 0.0, verdict: str = "confirm",
                 strong: Any = None, negatives: Any = None):
        self._keys, self._sleep, self._verdict = list(keys), sleep, verdict
        self._strong, self._neg = strong, negatives
        self.calls = 0
        self.finished = threading.Event()

    @property
    def sleep_sec(self) -> float:
        """这个假 provider 会睡多久 —— 硬截止用例拿它当上界(⛔ 别再另写一个魔数)。"""
        return self._sleep

    def chat(self, messages, *, enable_search=True, search_query=None, transport=None):
        self.calls += 1
        if self._sleep:
            _time.sleep(self._sleep)
        payload = {"market": {"overview": "指数普遍高开", "anchors_note": "锚点不取得资格"},
                   "baskets": [{"basket_key": k, "verdict": self._verdict,
                                "reasons": ["理由一"],
                                "auction_strong_codes": self._strong,
                                "driver_negative": (self._neg or {}).get("driver"),
                                "sector_core_negative": (self._neg or {}).get("sector"),
                                "candidate_negative": (self._neg or {}).get("candidate"),
                                "evidence_conflict": False, "members": []}
                               for k in self._keys],
                   "risks": ["模型补的一条风险"]}
        content = "一段叙述。\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        self.finished.set()
        return LLMResult(ok=True, content=content, provider=self.name, model=self.model)


def _world(env, *, engine=("C", "C1"), codes=("600000.SH",), price=10.5, key="k1"):
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"} for c in codes])
    bid = _seed_basket(env, list(codes), key=key, card=_card_json(list(codes)),
                       engine_code=engine[0], engine_version=engine[1])
    quotes = {c: _q(c, price=price) for c in codes}
    quotes.update({c: _q(c, price=10.1) for c in ac.MARKET_INDEX_CODES})
    return bid, quotes


def _run(env, quotes, **kw):
    return ap.run_auction_pipeline(
        kw.pop("now", IN_WINDOW), db_path=env.db_path, parquet_dir=env.parquet_dir,
        quotes_fn=lambda cs: {c: quotes[c] for c in cs if c in quotes},
        deadline=kw.pop("deadline", DEADLINE),
        now_fn=kw.pop("now_fn", lambda: IN_WINDOW),
        **kw,
    )


def _rows(db_path, sql):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# ④-A 窗口与防重
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("t,expect", [
    (time(9, 25, 59), False), (time(9, 26, 0), True), (time(9, 28, 59), True),
    (time(9, 29, 0), False), (time(9, 45), False),
])
def test_auction_window_is_left_closed_right_open_926_to_929(t, expect):
    assert ap.is_auction_window(datetime.combine(D1, t)) is expect


def test_non_trading_day_is_never_in_window():
    assert ap.is_auction_window(datetime(2026, 8, 9, 9, 26, 30)) is False   # 周日


def test_out_of_window_call_writes_absolutely_nothing(isolated_env):
    """🔴 ⛔ **事后不许补跑**(K8「报告发出后结束、不持续观察 9:30 以后的价格」):
    补跑会拿 9:30 之后的价格冒充 9:26 那一刻的判断。"""
    env = isolated_env
    _bid, quotes = _world(env)
    res = _run(env, quotes, now=datetime.combine(D1, time(9, 45)))
    assert res.ran is False and res.skipped_reason == ap.SKIP_NOT_WINDOW
    # ⚠ 没跑过 → 推送门槛恒 False:光看「llm_stage 非 ok」会把"根本没跑"读成
    # "跑了但 LLM 没回"而推一条(§七 P0-39 同款病的推送版)。
    assert res.should_push is False
    assert _rows(env.db_path, "SELECT COUNT(*) FROM auction_reports")[0][0] == 0
    assert _rows(env.db_path, "SELECT COUNT(*) FROM auction_verdicts")[0][0] == 0
    assert already_pushed(D1, ap.AUCTION_SENTINEL, "", ap.EVENT_TICK,
                          db_path=env.db_path) is False


def test_three_calls_in_the_window_run_only_once(isolated_env):
    """③ 同一天在窗口内调用三次 → 只跑一次(dedup);推送门槛也只可能被判一次。"""
    env = isolated_env
    _bid, quotes = _world(env)
    p = _Provider(["k1"])
    first = _run(env, quotes, provider=p)
    assert first.ran is True
    for _ in range(2):
        again = _run(env, quotes, provider=p)
        assert again.ran is False and again.skipped_reason == ap.SKIP_ALREADY_RAN
    assert p.calls == 1
    assert _rows(env.db_path, "SELECT COUNT(*) FROM auction_reports")[0][0] == 1


def test_market_level_tick_row_has_empty_ts_code_so_it_stays_off_the_board(isolated_env):
    """⚠ `sentinel='auction'` 的台账行 `ts_code` 为空 = 市场级标记 →
    `api/app.py::board` 的**既有**过滤天然把它挡在盘中看板事件列表之外
    (同 `capture`/`precall` 的 tick 标记先例,⛔ 本版不改看板一行)。"""
    env = isolated_env
    _bid, quotes = _world(env)
    _run(env, quotes, provider=_Provider(["k1"]))
    rows = _rows(env.db_path,
                 "SELECT sentinel, ts_code, event_key FROM sentinel_events "
                 "WHERE sentinel='auction'")
    assert rows == [("auction", "", "tick")]


# ══════════════════════════════════════════════════════════════════════════
# ④-B 🔴 9:29 硬截止 + 迟到的结论一律丢弃
# ══════════════════════════════════════════════════════════════════════════

def test_slow_provider_is_cut_off_at_the_deadline_and_its_late_result_is_discarded(isolated_env):
    """🔴 验收 ①:注入一个 **sleep 很久**的假 provider → 硬截止之前返回、表里
    `llm_stage='pending_explanation'`,**且之后再等,表内容一字不变**。

    ⚠ 那条调用还在跑是正常的(流式墙钟无固定上限是刻意的);拦住它的是**双保险**:
    ① 主线程不等它;② `store.finalize_*` 的幂等 `WHERE llm_stage='pending'` ——
    结案后那一行已经不是 pending,写不进去。
    """
    env = isolated_env
    _bid, quotes = _world(env)
    slow = _Provider(["k1"], sleep=1.5)
    t0 = _time.monotonic()
    # 余量 0.2s:模拟"9:26 起跑,离 9:29 只剩一点点"
    res = _run(env, quotes, provider=slow,
               deadline=IN_WINDOW.replace(second=30, microsecond=200000))
    # ⚠ 判据是「**没等那 1.5s 睡完**」,余量刻意留宽(复审 🔵-14:原先 `< 1.0` 只比
    # sleep 短 0.5s,并行 / 负载高时理论上会抖成间歇红 —— 而这条用例要钉的是
    # 「不等它」这个**结构性**事实,不是墙钟精度)。
    elapsed = _time.monotonic() - t0
    assert elapsed < slow.sleep_sec, f"硬截止必须**不等它**(等了 {elapsed:.2f}s)"
    assert res.deadline_hit is True
    assert res.llm_stage == LLM_PENDING_EXPLANATION
    assert res.pending == 1 and res.confirm == 0

    snapshot = _rows(env.db_path,
                     "SELECT verdict, verdict_raw, clamped_by, llm_stage, reasons_json "
                     "FROM auction_verdicts")
    rep = _rows(env.db_path, "SELECT market_overview, llm_stage FROM auction_reports")
    assert snapshot == [(VERDICT_PENDING_EXPLANATION, None, None, LLM_PENDING_EXPLANATION, "[]")]
    assert rep == [(None, LLM_PENDING_EXPLANATION)]

    # 等那条迟到的调用真的跑完,再看一次 —— **一个字都不许变**。
    assert slow.finished.wait(timeout=5.0)
    _time.sleep(0.2)
    assert _rows(env.db_path,
                 "SELECT verdict, verdict_raw, clamped_by, llm_stage, reasons_json "
                 "FROM auction_verdicts") == snapshot
    assert _rows(env.db_path, "SELECT market_overview, llm_stage FROM auction_reports") == rep


def test_deadline_already_past_skips_the_call_entirely(isolated_env):
    """余量 ≤ 0(硬截止已过、但抓取窗口还开着)→ **压根不发起调用**,直接结案。

    ⚠ 时钟取 9:28:55:仍在 `[9:26, 9:29)` 内(拉价照跑、机械段照落),而注入的
    `deadline` 已经在它之前 → `remaining <= 0`。**⛔ 别再拿 9:29:00 当这条的时钟** ——
    复审 🟡-2 之后那一刻属于「窗口已关」,是另一条完全不同的路径(零落库,见下一条)。
    """
    env = isolated_env
    _bid, quotes = _world(env)
    p = _Provider(["k1"])
    late_in_window = IN_WINDOW.replace(minute=28, second=55)
    res = _run(env, quotes, provider=p, now_fn=lambda: late_in_window,
               deadline=IN_WINDOW.replace(minute=28, second=0))
    assert p.calls == 0
    assert res.deadline_hit is True and res.llm_stage == LLM_PENDING_EXPLANATION
    # 机械段照常落库(K8:LLM 不可用时机械层继续输出数据报告和明确失效警报)
    rep = astore.load_report(D1, db_path=env.db_path)
    assert rep is not None and rep["requested_codes"] > 0 and rep["risks_json"] is not None


def test_window_closed_before_the_fetch_writes_nothing_at_all(isolated_env):
    """🔴 复审 🟡-2:这一拍的名义时刻在窗口内,但**真到拉价那一刻**已经越窗
    (precall + capture + 组清单吃掉了几分钟)→ **一条价都不拉、零落库、零 LLM**。

    ⚠ 与 `not_auction_window` 分成两个码:那条是"排程就没进窗口",这条是"慢了" ——
    混成一个,部署次日查 journal 分不出是哪种(而处置完全不同)。
    ⚠ 也**不落「当日已跑」标记**:今天压根没跑成,下一拍要是还在窗口内应当能干净重跑。
    """
    env = isolated_env
    _bid, quotes = _world(env)
    p = _Provider(["k1"])
    res = _run(env, quotes, provider=p, now_fn=lambda: datetime(2026, 8, 11, 9, 31, 0))
    assert res.ran is False
    assert res.skipped_reason == ap.SKIP_WINDOW_CLOSED
    assert p.calls == 0
    assert astore.load_report(D1, db_path=env.db_path) is None
    assert astore.load_verdicts(D1, db_path=env.db_path) == []
    assert already_pushed(D1, ap.AUCTION_SENTINEL, "", ap.EVENT_TICK,
                          db_path=env.db_path) is False


def test_mechanical_report_lands_before_the_llm_no_matter_what(isolated_env):
    """🔴 「机械报告必须先落库、LLM 结论后落库」做成**结构性保证**:
    provider 抛异常 / 没有 provider 时,机械段与「命中 D0 失效位」照样在库里。"""
    env = isolated_env
    _bid, quotes = _world(env, price=9.4)      # 9.4 < 冻结失效位 9.5 → 命中
    res = _run(env, quotes, provider=None, provider_factory=lambda: None)
    assert res.llm_stage == LLM_NO_PROVIDER, "「没有 provider」与「9:29 到了没回」是两回事"
    v = astore.load_verdicts(D1, db_path=env.db_path)[0]
    assert v["hit_invalidation_json"] == ["600000.SH"], "明确失效警报是独立通道,⛔ 不受 LLM 缺席影响"
    assert v["verdict"] == VERDICT_PENDING_EXPLANATION


# ══════════════════════════════════════════════════════════════════════════
# ④-B 正常路径 + 三道闸在编排里真的生效
# ══════════════════════════════════════════════════════════════════════════

def test_happy_path_lands_verdicts_and_records_the_clamp_account(isolated_env):
    """② 注入正常假 provider → `llm_stage='ok'`、`verdict` 落库。"""
    env = isolated_env
    _bid, quotes = _world(env)
    res = _run(env, quotes, provider=_Provider(["k1"], strong=["600000.SH", "600001.SH"]))
    assert res.llm_stage == LLM_OK and res.confirm == 1
    v = astore.load_verdicts(D1, db_path=env.db_path)[0]
    assert v["verdict"] == VERDICT_CONFIRM and v["verdict_raw"] == VERDICT_CONFIRM
    assert v["clamped_by"] is None and v["reasons_json"] == ["理由一"]
    assert v["llm_fields_json"]["basket_key"] == "k1"
    rep = astore.load_report(D1, db_path=env.db_path)
    assert rep["market_overview"] == "指数普遍高开" and rep["llm_stage"] == LLM_OK
    assert any(r.get("kind") == "llm_note" for r in rep["risks_json"])


def test_z1_single_strong_is_clamped_inside_the_pipeline_and_logged_as_a_risk(isolated_env):
    """闸 2 在**编排里**真的生效,而且夹逼**进了小报告第 4 块**(⛔ 不许静默丢弃)。"""
    env = isolated_env
    _bid, quotes = _world(env, engine=("Z", "Z1"))
    res = _run(env, quotes, provider=_Provider(["k1"], strong=["600000.SH"]))
    assert res.neutral == 1 and res.confirm == 0
    v = astore.load_verdicts(D1, db_path=env.db_path)[0]
    assert (v["verdict"], v["verdict_raw"], v["clamped_by"]) == (
        VERDICT_NEUTRAL, VERDICT_CONFIRM, "clamped_by_single_strong")
    assert v["manual_note_attached"] == 1, "被夹逼过 = K8 的「临界标的」→ 挂小纸条"
    rep = astore.load_report(D1, db_path=env.db_path)
    assert any(r.get("kind") == "verdict_clamped" for r in rep["risks_json"])
    assert rep["manual_note_attached"] == 1


def test_degraded_data_quality_clamps_everything_to_neutral(isolated_env):
    """闸 1 在编排里:三支指数一只都没抓到 → 篮级数据质量非 ok → 一律中性。"""
    env = isolated_env
    _bid, quotes = _world(env)
    for c in ac.MARKET_INDEX_CODES:
        quotes.pop(c)
    res = _run(env, quotes, provider=_Provider(["k1"]))
    assert res.neutral == 1
    v = astore.load_verdicts(D1, db_path=env.db_path)[0]
    assert v["clamped_by"] == "clamped_by_data_quality"


# ══════════════════════════════════════════════════════════════════════════
# ④-D 推送门槛
# ══════════════════════════════════════════════════════════════════════════

def test_a_quiet_morning_pushes_nothing(isolated_env):
    """🔴 ⛔ **不许"平静的早晨也发一条"**(同 `PrecallResult.should_push_summary`
    的既定纪律;V2.2-⑤-B 已取消过一次"必发豁免",⛔ 别以别的形式加回来)。"""
    env = isolated_env
    _bid, quotes = _world(env)
    res = _run(env, quotes, provider=_Provider(["k1"], strong=["600000.SH", "600001.SH"]))
    assert (res.veto, res.hit_invalidation_codes, res.llm_stage) == (0, [], LLM_OK)
    assert res.should_push is False


@pytest.mark.parametrize("scenario", ["veto", "hit", "llm_down"])
def test_push_threshold_fires_on_each_of_the_three_triggers(isolated_env, scenario):
    env = isolated_env
    if scenario == "hit":
        _bid, quotes = _world(env, price=9.4)
        p = _Provider(["k1"], strong=["a", "b"])
    elif scenario == "veto":
        _bid, quotes = _world(env)
        p = _Provider(["k1"], verdict="veto")
    else:
        _bid, quotes = _world(env)
        p = None
    res = _run(env, quotes, provider=p, provider_factory=(None if p else (lambda: None)))
    assert res.should_push is True


def test_push_wording_never_says_buy_and_always_carries_the_k8_caveat():
    """文案纪律:⛔ 不得出现「建议买入 / 可以买」;K8 §二十 那句「不等于买入指令」**恒带**。"""
    from neckline.api import notify

    body_holder: Dict[str, str] = {}

    def _fake_push_event(kind, title, body, *, db_path=None, transport=None):
        body_holder["kind"], body_holder["title"], body_holder["body"] = kind, title, body
        return notify.NotifyOutcome(sent=0, failed=0, skipped_reason="test")

    orig = notify.push_event
    notify.push_event = _fake_push_event          # noqa: SLF001 —— 测措辞层,不测扇出
    try:
        notify.push_auction_summary({"confirm": 1, "neutral": 2, "veto": 1,
                                     "hit_invalidation": 3, "llm_stage": "ok"})
        body = body_holder["body"]
        assert "1 篮确认、2 篮中性、1 篮否决" in body
        assert "3 只命中 D0 失效位" in body
        assert "竞价结论只说明竞价反映出的信息,不等于买入指令。" in body
        assert "本次 LLM 未给出解释" not in body
        for banned in ("建议买入", "可以买", "推荐买点", "必涨", "目标价"):
            assert banned not in body, banned
        # 🔴 kind 复用 KIND_PRECALL(⛔ 零新 kind)
        from neckline.notify_kinds import KIND_PRECALL

        assert body_holder["kind"] == KIND_PRECALL

        notify.push_auction_summary({"confirm": 0, "neutral": 0, "veto": 0,
                                     "hit_invalidation": 0, "llm_stage": "pending_explanation"})
        assert "本次 LLM 未给出解释,已按『待解释』记录" in body_holder["body"]
    finally:
        notify.push_event = orig


def test_no_new_notify_kind_was_introduced():
    """〇-5 那条拍板的收益:`ALL_KINDS` 是冻结元组,本版**零新 kind**。"""
    from neckline import notify_kinds

    assert "auction" not in notify_kinds.ALL_KINDS


# ══════════════════════════════════════════════════════════════════════════
# ④-C 哨兵循环接入(只加一个同级分支,不改轮询节奏)
# ══════════════════════════════════════════════════════════════════════════

def test_sentinel_loop_mounts_the_auction_bypass_without_touching_the_poll_cadence():
    """⛔ 不改 `_SENTINEL_PREOPEN_POLL_SEC`、⛔ 不改现有两条旁路一行;
    竞价分支必须有**独立 try/except**(旁路成败互不影响)。"""
    import inspect

    from neckline.api import app as app_mod

    src = inspect.getsource(app_mod._sentinel_loop)
    assert app_mod._SENTINEL_PREOPEN_POLL_SEC == 30
    assert "auction_pipeline.is_auction_window(now)" in src
    assert "auction_pipeline.run_auction_pipeline" in src
    assert "notify.push_auction_summary" in src
    # 竞价分支自己的 try/except(整个 preopen 分支里现在有三个独立 try)
    assert src.count("except Exception:") >= 3
    assert "竞价确认层异常(已吞,竞价层是旁路)" in src
    # 顺序:precall → capture → auction
    assert (src.index("run_precall_tick") < src.index("run_auction_capture")
            < src.index("run_auction_pipeline"))


def test_no_new_systemd_unit_was_added():
    """🔴 〇-3:竞价层跑在常驻哨兵进程里,`deploy/` 与 nk 的 **10 个 unit 1:1** 必须维持
    —— 那个 1:1 本身就是防误装闸门。"""
    from pathlib import Path

    deploy = Path(__file__).resolve().parent.parent / "deploy"
    units = sorted(p.name for p in deploy.glob("neckline*"))
    assert not any("auction" in u for u in units), f"竞价层不许有自己的 unit:{units}"
    assert len([u for u in units if u.endswith((".service", ".timer", ".target"))]) == 8
