"""V2.3.3 批 ②:`neckline/auction/` 的冻结抓取 + 机械层 + 两张表(**零 LLM**)。

本文件锁三件事:
  1. **公式与读数的来源**(§五 ②-D 那张表逐项):`gap_pct_of` 与
     `sentinel/capture.py` 那处**逐位相同**;失效位判据取 D0 冻结值、复用 `precall`
     的同一个纯函数;`plan_fit` 五态全部来自卡上冻结的区间。
  2. **零结论**:机械层产物里一个 `confirm`/`neutral`/`veto` 都不许有。
  3. **两阶段写的第一阶段**:机械段落库 + 同日重跑幂等(零新行、机械列逐位不变)。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
import pytest

from neckline.auction import (
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    LLM_PENDING,
    PLAN_FIT_ABOVE_MAX_CHASE,
    PLAN_FIT_ABOVE_ZONE_BELOW_CHASE,
    PLAN_FIT_BELOW_ZONE,
    PLAN_FIT_IN_ZONE,
    PLAN_FIT_UNKNOWN,
    RISK_INVALIDATION_UNDETERMINED,
    UNDET_ANCHOR_STALE,
    UNDET_NO_MEMBER_SCRIPT,
    UNDET_NO_OPEN_PRICE,
    UNDET_NO_STOP_LINE,
    VERDICT_PENDING_EXPLANATION,
)
from neckline.auction import collect as ac
from neckline.auction import mech as am
from neckline.auction import store as astore
from neckline.db import connection
from neckline.selection import basket_card as bc
from neckline.selection import verification_rules as vr
from neckline.selection.basket_store import save_basket_card
from neckline.data.realtime import Quote

from tests.conftest import insert_stock_basic

D1 = date(2026, 8, 11)
D0 = date(2026, 8, 10)
NOW = datetime(2026, 8, 11, 9, 26, 30)


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def _q(code, *, price=10.5, pre_close=10.0, volume=5000.0, amount=52500.0, source="sina") -> Quote:
    """一份竞价快照报价。⚠ 竞价阶段 `open == price`(集合竞价撮合价即当前价,同
    `scripts/smoke_precall.py::synthesize_auction_quote` 的既有口径)。"""
    return Quote(code=code, name=code, price=price, pre_close=pre_close, open=price,
                 high=price, low=price, volume=volume, amount=amount,
                 ts="2026-08-11 09:25:03", source=source)


def _mech_row(code, *, close=10.0, ma20=9.2, stop_price=9.5) -> bc.MemberMech:
    return bc.MemberMech(ts_code=code, name=code, close=close, ma20=ma20,
                         limit_up=11.0, limit_down=9.0, stop_price=stop_price)


def _card_json(codes, *, stop_pct=0.05, entry=None, max_chase=None, roles=None) -> dict:
    ms = [_mech_row(c) for c in codes]
    members = []
    for c in codes:
        m = {"ts_code": c, "name": c, "role_llm": (roles or {}).get(c, "leader"),
             "mech": _mech_row(c).to_dict()}
        if entry is not None:
            m["entry_zone"] = entry
        if max_chase is not None:
            m["max_chase"] = max_chase
        members.append(m)
    return {
        "spec_version": bc.CARD_SPEC_VERSION,
        "members": members,
        "verification_spec": bc.build_verification_spec("bk", D0, ms),
        "invalidation_spec": bc.build_invalidation_spec("bk", D0, ms, stop_pct=stop_pct),
        "fingerprint": {"stop_pct": stop_pct,
                        "verification_ruleset_version": vr.VERIFICATION_RULESET_VERSION},
    }


def _seed_basket(env, codes, *, tier=1, key="k1", name="测试篮", card=None,
                 engine_code="C", engine_version="C1", skeleton="K8-V0.7") -> int:
    with connection(env.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO baskets (trade_date, basket_key, name, driver, driver_kind, tier,"
            " pack_version, engine_api_version, charter_version, via, evidence_status,"
            " engine_code, engine_version, skeleton_version, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (D0.strftime("%Y%m%d"), key, name, "某共同驱动", "theme", tier,
             "K8-skeleton", 2, "v2.3-k8", "auto", "ok",
             engine_code, engine_version, skeleton, "2026-08-10T16:05:00+08:00"),
        )
        basket_id = int(cur.lastrowid)
        for code in codes:
            conn.execute(
                "INSERT INTO basket_members (basket_id, ts_code, role_llm, role_mech,"
                " role_conflict, reason, is_primary, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (basket_id, code, "leader", None, 0, "理由", 1, "2026-08-10T16:05:00+08:00"),
            )
    if card is not None:
        save_basket_card(basket_id, card, stop_pct=0.05, db_path=env.db_path)
    return basket_id


def _snapshot(env, quotes, *, now=NOW, **kw) -> ac.AuctionSnapshot:
    """⚠ **必须注入 `now_fn`**(V2.3.3 复审 🟡-2 之后):`captured_at` 取的是**真正拉完
    价那一刻的真实时钟**,拉价前还会用它复判一次窗口 —— 不注入,单测就会按跑测试的
    墙钟判"窗口已关"、一条价都不拉。缺省注入 = 让这一拍的名义时刻与真实时钟一致
    (同 `precall.run_precall_tick(now=…)` 的回放体例)。"""
    kw.setdefault("now_fn", lambda: now)
    return ac.collect_auction_snapshot(
        D1, now, db_path=env.db_path, parquet_dir=env.parquet_dir,
        quotes_fn=lambda codes: {c: quotes[c] for c in codes if c in quotes}, **kw,
    )


# ══════════════════════════════════════════════════════════════════════════
# ②-D `gap_pct` 的公式唯一源 + 与 capture.py 那处逐位对拍
# ══════════════════════════════════════════════════════════════════════════

_GAP_TABLE = [
    (10.5, 10.0), (10.0, 10.0), (9.0, 10.0),
    (None, 10.0), (10.5, None), (10.5, 0.0), (10.5, -1.0), (0.0, 10.0), (None, None),
]


@pytest.mark.parametrize("price,pre_close", _GAP_TABLE)
def test_gap_pct_matches_capture_module_bit_for_bit(price, pre_close):
    """🔴 §五 ②-D 登记的**刻意小重复**:依赖方向不许 `sentinel` 反向 import
    `auction`,所以 `capture.record_auction_snapshot` 里那一行一字不动;两处一致
    **由这条守门桥接**(同一张输入表,含 `None` / `0` / 负 `pre_close` 分支)。"""
    mine = ac.gap_pct_of(price, pre_close)
    # capture.py 那一行的表达式,逐字抄来做对照(⛔ 不 import 它的私有实现)。
    theirs = (price / pre_close - 1.0) if (price and pre_close and pre_close > 0) else None
    assert mine == theirs


def test_gap_pct_never_fakes_flat_open_when_pre_close_missing():
    """⛔ 不拿 0 冒充"平开" —— 「算不出」与「没涨没跌」是两件事。"""
    assert ac.gap_pct_of(10.5, None) is None
    assert ac.gap_pct_of(10.5, 0.0) is None


# ══════════════════════════════════════════════════════════════════════════
# ②-C 抓取清单:三支市场指数**显式并入**
# ══════════════════════════════════════════════════════════════════════════

def test_three_market_indices_are_always_requested_even_without_matching_board(isolated_env):
    """🔴 上证 + 深证 + 创业板三支**显式并入**,⛔ 不靠 `universe._related_index_codes`
    的「池里出现过该板块才加」逻辑(当天没有创业板票 → 创业板指就不在池里)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    for code in ac.MARKET_INDEX_CODES:
        assert code in snap.requested, code
    assert "399006.SZ" in snap.requested, "创业板指必须在清单里,即便当天一只创业板票都没有"


def test_collect_never_calls_the_universe_index_helper():
    """⛔ 不许调 / 不许改 `universe.py::_related_index_codes` —— 那个函数只按"关注池里
    出现过的板块"加指数,改它会**同时改掉哨兵与存拍**的关注池。竞价层**自己多要那三个
    码**,零副作用。"""
    import neckline.auction.collect as mod

    code = mod.__loader__.get_source(mod.__name__) or ""
    body = "\n".join(ln for ln in code.splitlines() if not ln.strip().startswith("#"))
    assert "_related_index_codes(" not in body


# ══════════════════════════════════════════════════════════════════════════
# ②-D 数据质量三态:**结构性判据,⛔ 不是百分比**
# ══════════════════════════════════════════════════════════════════════════

def test_data_quality_three_states_are_structural_not_percentage(isolated_env):
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"},
                             {"ts_code": "600001.SH", "name": "邯郸钢铁", "market": "主板"}])
    _seed_basket(env, ["600000.SH", "600001.SH"],
                 card=_card_json(["600000.SH", "600001.SH"]))
    all_codes = {c: _q(c) for c in ("600000.SH", "600001.SH", "000001.SH", "399001.SZ", "399006.SZ")}

    full = _snapshot(env, all_codes)
    assert full.quality_of(full.requested) == DQ_OK

    partial = _snapshot(env, {k: v for k, v in all_codes.items() if k != "600001.SH"})
    assert partial.quality_of(partial.requested) == DQ_DEGRADED

    empty = _snapshot(env, {})
    assert empty.quality_of(empty.requested) == DQ_INSUFFICIENT


def _stepping_clock(*stamps):
    """一次比一次晚的假时钟(最后一个值之后一直返回它)。用来模拟"拉价吃掉了几分钟"。"""
    seq = list(stamps)

    def _tick():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return _tick


def test_capture_time_is_the_moment_the_fetch_finished_not_the_tick(isolated_env):
    """🔴 复审 🟡-2:`captured_at` = **真正拉完价那一刻**,⛔ 不是轮询那一拍的 `now`。

    失败场景:某个早晨新浪超时、precall + capture 合计几分钟 → 循环 09:26:00 进分支、
    竞价层**实际在 9:30 之后**才拉到价(拿的是**开盘后**的价格),而库里写下
    `captured_at=09:26:00` + `captured_in_window=True` → 市场级 `data_quality='ok'`
    → 闸 1 不夹逼 → 一份「9:26 冻结」的报告堂而皇之地用开盘后价格下 `confirm`。
    """
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    codes = {c: _q(c) for c in ("600000.SH", "000001.SH", "399001.SZ", "399006.SZ")}

    # ① 拉价前还在窗内(9:28:50)、拉完已经 9:31 —— 报告照落,但**质量降级**。
    late = _snapshot(env, codes, now=NOW,
                     now_fn=_stepping_clock(datetime(2026, 8, 11, 9, 28, 50),
                                            datetime(2026, 8, 11, 9, 31, 0)))
    assert late.fetch_skipped_reason == ""          # 拉了(拉价前窗口还开着)
    assert late.captured_at == datetime(2026, 8, 11, 9, 31, 0)      # ⛔ 不是 NOW
    assert late.fetch_started_at == datetime(2026, 8, 11, 9, 28, 50)
    assert late.fetch_elapsed_sec == 130.0
    assert late.captured_in_window is False
    # 🔴 越窗必须被检测出来**并影响 data_quality** —— 这样闸 1 才夹得住。
    assert late.quality_of(late.requested) == DQ_DEGRADED

    # ② 正常的一拍:拉价前后都在窗内 → `ok`。
    good = _snapshot(env, codes, now=NOW,
                     now_fn=_stepping_clock(datetime(2026, 8, 11, 9, 26, 10),
                                            datetime(2026, 8, 11, 9, 26, 12)))
    assert good.captured_in_window is True
    assert good.quality_of(good.requested) == DQ_OK


def test_window_closed_before_the_fetch_means_zero_quotes_pulled(isolated_env):
    """🔴 复审 🟡-2 第一层:真到拉价那一刻窗口已关 → **一条价都不拉**(〇b-4)。

    ⚠ 与 ①「拉价跨过 9:29」分得开:那种是报告照落 + 降级,这种是**压根没跑成**。
    """
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    pulled: list = []

    def _fetch(cs):
        pulled.append(list(cs))
        return {}

    snap = ac.collect_auction_snapshot(
        D1, NOW, db_path=env.db_path, parquet_dir=env.parquet_dir,
        quotes_fn=_fetch, now_fn=lambda: datetime(2026, 8, 11, 9, 31, 0),
    )
    assert pulled == [], "窗口已关还去拉价 = 拿开盘后的价格冒充 9:26 那一刻"
    assert snap.fetch_skipped_reason == ac.SKIP_WINDOW_CLOSED
    assert snap.captured_in_window is False
    assert snap.quotes == {}


def test_empty_sample_domain_is_insufficient_not_ok(isolated_env):
    """「没有可判的东西」与「判过了都好」必须分得开(§七 P0-39 同款纪律)。"""
    env = isolated_env
    snap = _snapshot(env, {})
    assert snap.quality_of([]) == DQ_INSUFFICIENT


def test_source_is_unknown_when_nothing_was_fetched(isolated_env):
    """⛔ 一条都没抓到时不拿主源名冒充(「没抓到」不是「用的新浪」)。"""
    env = isolated_env
    assert _snapshot(env, {}).source == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# ②-D `plan_fit` 五态:全部来自**卡上冻结值**,零阈值
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("price,expect", [
    (9.9, PLAN_FIT_BELOW_ZONE),
    (10.0, PLAN_FIT_IN_ZONE),
    (10.2, PLAN_FIT_IN_ZONE),
    (10.3, PLAN_FIT_ABOVE_ZONE_BELOW_CHASE),
    (10.6, PLAN_FIT_ABOVE_MAX_CHASE),
])
def test_plan_fit_five_states_from_frozen_zone(price, expect):
    assert am.plan_fit_of(price, {"low": 10.0, "high": 10.2}, 10.5) == expect


def test_plan_fit_unknown_when_card_gave_nothing():
    """卡上没给区间(夹逼拒收 / LLM 没给)或价拿不到 → `unknown`,⛔ 不拿 `in_zone` 冒充。"""
    assert am.plan_fit_of(10.1, None, 10.5) == PLAN_FIT_UNKNOWN
    assert am.plan_fit_of(None, {"low": 10.0, "high": 10.2}, 10.5) == PLAN_FIT_UNKNOWN
    # 高于区间但卡上没给最高追价 → 「追不追得起」判不了,⛔ 不猜一个上限。
    assert am.plan_fit_of(10.4, {"low": 10.0, "high": 10.2}, None) == PLAN_FIT_UNKNOWN


# ══════════════════════════════════════════════════════════════════════════
# ②-D 失效位:判据取 D0 **冻结值**,复用 precall 的同一个纯函数
# ══════════════════════════════════════════════════════════════════════════

def test_hit_invalidation_uses_the_frozen_stop_line(isolated_env):
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    card = _card_json(["600000.SH"])           # stop_price=9.5 → close_below_stop_line=9.5
    _seed_basket(env, ["600000.SH"], card=card)
    snap = _snapshot(env, {"600000.SH": _q("600000.SH", price=9.4, pre_close=10.0)})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    b = mech.baskets[0]
    assert b.hit_invalidation_codes == ["600000.SH"]
    assert b.members[0].hit_invalidation is True


def test_anchor_stale_suppresses_both_judgements_and_says_so(isolated_env):
    """⚠ 锚失效(疑似除权除息)→ 该票 `hit_invalidation` / `gap_up_deviation` 一律
    `None`,**⛔ 不判**(同 `precall` 的既定纪律:那是**错的比较**)。
    `anchor_stale=True` 让「没判」与「判了没异常」分得开。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))   # ref_close = 10.0
    snap = _snapshot(env, {"600000.SH": _q("600000.SH", price=6.9, pre_close=7.0)})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    r = mech.baskets[0].members[0]
    assert r.anchor_stale is True
    assert r.hit_invalidation is None and r.gap_up_deviation is None
    # 🔴 「没判」必须**带原因码**(光有 None,读者还是只能猜)。
    assert r.hit_invalidation_undetermined_reason == UNDET_ANCHOR_STALE
    assert r.gap_up_deviation_undetermined_reason == UNDET_ANCHOR_STALE
    assert mech.baskets[0].hit_invalidation_codes == []


# ══════════════════════════════════════════════════════════════════════════
# 🔴 复审 🔴-1:「没判」是**第三态**,⛔ 不许折成 `False`「没问题」
# ══════════════════════════════════════════════════════════════════════════

def test_missing_stop_line_is_not_judged_rather_than_judged_clean(isolated_env):
    """🔴 卡上**没有**这只成员的 `close_below_stop_line` → `hit_invalidation` 必须是
    `None`(没判),⛔ **不是 `False`**(看过了、没触发)。

    失败场景:D0 那张卡里某成员因为 `MemberMech.stop_price` 取不到而没进
    `invalidation_spec.members`(`load_member_scripts` 仍会给它发一份 `stop_line=None`
    的 `MemberScript`)→ 竞价小报告对这只票明确说「未触发 D0 失效位」,
    而真相是**一个字都没核对过**。
    ⚠ 同一张卡上 `ref_close` 还在 → 高开偏离**照判**(两项各判各的,⛔ 别一起关掉)。
    """
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    card = _card_json(["600000.SH"])
    # 把这只票从 invalidation_spec.members 里摘掉(= 卡上没有它的失效位)
    card["invalidation_spec"]["members"] = []
    _seed_basket(env, ["600000.SH"], card=card)
    snap = _snapshot(env, {"600000.SH": _q("600000.SH", price=10.1, pre_close=10.0)})
    r = am.build_mech(snap, db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.hit_invalidation is None, "卡上没有这个价位 = 没判,⛔ 不是「没触发」"
    assert r.hit_invalidation_undetermined_reason == UNDET_NO_STOP_LINE
    assert r.gap_up_deviation is False          # ref_close 还在 → 这一项判了、没命中
    assert r.gap_up_deviation_undetermined_reason is None


def test_open_price_not_published_yet_leaves_both_undetermined(isolated_env):
    """🔴 `quote.open <= 0`(9:26 那一刻行情源还没发开盘价)→ **两项都没判**。

    `precall.judge_*` 里那句 `quote.open <= 0 → None` 就是为这件事写的;
    `is not None` 会把它折成 `False`「没问题」。
    """
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    q = Quote(code="600000.SH", name="浦发银行", price=10.5, pre_close=10.0, open=0.0,
              high=0.0, low=0.0, volume=5000.0, amount=52500.0,
              ts="2026-08-11 09:25:03", source="sina")
    mech = am.build_mech(_snapshot(env, {"600000.SH": q}), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    r = mech.baskets[0].members[0]
    assert r.hit_invalidation is None and r.gap_up_deviation is None
    assert r.hit_invalidation_undetermined_reason == UNDET_NO_OPEN_PRICE
    assert r.gap_up_deviation_undetermined_reason == UNDET_NO_OPEN_PRICE
    # 🔴 「没判」必须出现在**异常与风险**里 —— ⛔ 不许沉默(沉默 = 用户读成"没报警就没事")
    kinds = [x["kind"] for x in mech.market.risks]
    assert RISK_INVALIDATION_UNDETERMINED in kinds
    text = next(x["text"] for x in mech.market.risks
                if x["kind"] == RISK_INVALIDATION_UNDETERMINED)
    assert "没判" in text and "600000.SH" in text
    # 🔴 也必须出现在喂 LLM 的短摘要里(prompt 那条「标了没判的项照实当作未知」
    #    只有在摘要里真的写了「没判」时才生效)。
    assert "没判" in am.short_summary(mech)


def test_no_card_script_at_all_is_undetermined_with_its_own_reason(isolated_env):
    """有篮无卡(合法中间态)→ 两项都没判,原因码 `no_member_script`。
    ⛔ 不许因为"卡读不到"就让这两格看起来像"核对过了没事"。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=None)          # 有篮子、没卡
    r = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH")}),
                      db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.hit_invalidation is None and r.gap_up_deviation is None
    assert r.hit_invalidation_undetermined_reason == UNDET_NO_MEMBER_SCRIPT
    assert r.gap_up_deviation_undetermined_reason == UNDET_NO_MEMBER_SCRIPT


def test_a_normal_member_reads_false_not_none(isolated_env):
    """反向:一切齐全、真的没命中 → **`False`**(看过了、没事),原因码为空。
    ⚠ 没有这一条,上面几条可以靠"全都返回 None"作弊通过。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    r = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH", price=10.1, pre_close=10.0)}),
                      db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.hit_invalidation is False and r.gap_up_deviation is False
    assert r.hit_invalidation_undetermined_reason is None
    assert r.gap_up_deviation_undetermined_reason is None
    assert r.has_undetermined_invalidation is False


def test_history_reading_discloses_its_lookback_window(isolated_env):
    """🔴 复审 🔴-2b + 用户裁定 P3-69:`history_days_available` 被回看窗口封顶 ——
    **窗口必须自曝**,⛔ 不许当一个偷偷的判据。裁定后自曝的是两个数(20 交易日 /
    60 自然日上界)+ 一句文案。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    mech = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    h = mech.baskets[0].history
    assert h["history_lookback_trading_days"] == am.HISTORY_LOOKBACK_TRADING_DAYS == 20
    assert h["history_lookback_days"] == am.HISTORY_LOOKBACK_MAX_CALENDAR_DAYS == 60
    assert h["history_lookback_unit"] == "calendar_days"
    assert h["history_excludes_today"] is True
    assert "不是全史" in h["history_lookback_note"]
    assert "有效交易日" in h["history_lookback_note"]
    assert "**" not in h["history_lookback_note"], "下发给界面的文案⛔ 不许带 Markdown"
    # 喂 LLM 的短摘要里也得写明(⛔ 不能只写在契约里)
    summary = am.short_summary(mech)
    assert "有效交易日" in summary and "当日竞价不进入自身历史基线" in summary


def test_history_scans_the_parquet_once_for_all_baskets(isolated_env):
    """🔵-12:N 个篮子**只扫一次** `auction_snapshots`(这条路径跑在常驻
    `neckline.service` 里,P0-23 语境)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"}
                             for c in ("600000.SH", "600001.SH", "600002.SH")])
    _seed_basket(env, ["600000.SH"], key="k1", card=_card_json(["600000.SH"]))
    _seed_basket(env, ["600001.SH"], key="k2", card=_card_json(["600001.SH"]))
    _seed_basket(env, ["600002.SH"], key="k3", card=_card_json(["600002.SH"]))
    calls = []
    real = am.scan_history_index

    def _counting(codes, td, **kw):
        calls.append(list(codes))
        return real(codes, td, **kw)

    snap = _snapshot(env, {c: _q(c) for c in ("600000.SH", "600001.SH", "600002.SH")})
    am.scan_history_index = _counting
    try:
        mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    finally:
        am.scan_history_index = real
    assert len(mech.baskets) == 3
    assert len(calls) == 1, f"三个篮子扫了 {len(calls)} 次同一个区间"
    assert set(calls[0]) == {"600000.SH", "600001.SH", "600002.SH"}


def test_gap_up_deviation_reuses_the_precall_threshold_unchanged(isolated_env):
    """🔴 复用 `PRECALL_GAP_UP_INVALIDATE=0.03` **一字不改**(本版零新阈值)。"""
    from neckline.sentinel import precall

    assert precall.PRECALL_GAP_UP_INVALIDATE == 0.03
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))   # ref_close = 10.0
    snap = _snapshot(env, {"600000.SH": _q("600000.SH", price=10.4, pre_close=10.0)})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert mech.baskets[0].members[0].gap_up_deviation is True


# ══════════════════════════════════════════════════════════════════════════
# 🔴 机械层零结论
# ══════════════════════════════════════════════════════════════════════════

def test_mech_layer_never_produces_a_verdict(isolated_env):
    """K8 §二十 分工:机械层只出读数、**判定交 LLM**。产物里一个结论码都不许有。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    blob = json.dumps({
        "market": mech.market.__dict__,
        "baskets": [{**b.__dict__, "members": [r.to_dict() for r in b.members]}
                    for b in mech.baskets],
    }, ensure_ascii=False, default=str)
    for banned in ('"confirm"', '"veto"', '"neutral"'):
        assert banned not in blob, f"机械层产出里出现了结论码 {banned}"


def test_mech_module_source_has_no_verdict_constants():
    """AST 级:`mech.py` 里连结论码常量都不许 import(结论是 `llm.py` 的活)。"""
    import neckline.auction.mech as mod

    code = mod.__loader__.get_source(mod.__name__) or ""
    body = "\n".join(ln for ln in code.splitlines() if not ln.strip().startswith("#"))
    for banned in ("VERDICT_CONFIRM", "VERDICT_VETO", "clamp_verdict"):
        assert banned not in body, banned


# ══════════════════════════════════════════════════════════════════════════
# ②-E 两阶段写第一阶段 + 幂等
# ══════════════════════════════════════════════════════════════════════════

def _rows(db_path, sql):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_mechanical_stage_lands_first_with_pending_llm(isolated_env):
    """① 造一天含 2 篮 5 票 + 三支指数的合成竞价快照 → `auction_reports` 恰 1 行、
    `auction_verdicts` 恰 2 行,机械列齐、`llm_stage='pending'`、
    `verdict='pending_explanation'`。"""
    env = isolated_env
    codes_a = ["600000.SH", "600001.SH", "600002.SH"]
    codes_b = ["300001.SZ", "300002.SZ"]
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"} for c in codes_a]
                       + [{"ts_code": c, "name": c, "market": "创业板"} for c in codes_b])
    _seed_basket(env, codes_a, key="k1", card=_card_json(codes_a))
    _seed_basket(env, codes_b, key="k2", tier=2, card=_card_json(codes_b),
                 engine_code="Z", engine_version="Z1")
    quotes = {c: _q(c) for c in codes_a + codes_b + list(ac.MARKET_INDEX_CODES)}
    snap = _snapshot(env, quotes)
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert astore.save_mechanical(mech, db_path=env.db_path) is True

    rep = astore.load_report(D1, db_path=env.db_path)
    assert rep is not None
    assert rep["trade_date"] == "20260811" and rep["d0_date"] == "20260810"
    assert rep["llm_stage"] == LLM_PENDING and rep["market_overview"] is None
    assert rep["baskets_covered"] == 2 and rep["data_quality"] == DQ_OK
    assert set(rep["index_gaps_json"]) == set(ac.MARKET_INDEX_CODES)

    vs = astore.load_verdicts(D1, db_path=env.db_path)
    assert len(vs) == 2
    for v in vs:
        assert v["llm_stage"] == LLM_PENDING
        assert v["verdict"] == VERDICT_PENDING_EXPLANATION
        assert v["verdict_raw"] is None and v["clamped_by"] is None
        assert v["members_json"] and v["sector_sync_json"] and v["plan_consistency_json"]
    assert [v["engine_version"] for v in vs] == ["C1", "Z1"]
    assert all(v["skeleton_version"] == "K8-V0.7" for v in vs)


def test_same_day_rerun_is_idempotent_and_mechanical_columns_never_move(isolated_env):
    """② 同一天重跑 → **零新行、机械列逐位不变**(`INSERT OR IGNORE` 的幂等)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert astore.save_mechanical(mech, db_path=env.db_path) is True
    before = _rows(env.db_path,
                   "SELECT trade_date, source, captured_at, requested_codes, fetched_codes, "
                   "data_quality, index_gaps_json FROM auction_reports")
    before_v = _rows(env.db_path,
                     "SELECT basket_id, members_json, sector_sync_json, hit_invalidation_json "
                     "FROM auction_verdicts")

    # 第二次跑:换一份**内容不同**的快照,幂等必须让它一个字都写不进去。
    snap2 = _snapshot(env, {"600000.SH": _q("600000.SH", price=9.4)},
                      now=datetime(2026, 8, 11, 9, 28, 0))
    mech2 = am.build_mech(snap2, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert astore.save_mechanical(mech2, db_path=env.db_path) is False
    assert _rows(env.db_path, "SELECT COUNT(*) FROM auction_reports")[0][0] == 1
    assert _rows(env.db_path, "SELECT COUNT(*) FROM auction_verdicts")[0][0] == 1
    assert _rows(env.db_path,
                 "SELECT trade_date, source, captured_at, requested_codes, fetched_codes, "
                 "data_quality, index_gaps_json FROM auction_reports") == before
    assert _rows(env.db_path,
                 "SELECT basket_id, members_json, sector_sync_json, hit_invalidation_json "
                 "FROM auction_verdicts") == before_v


def test_zero_baskets_still_writes_a_row_so_ran_and_not_ran_stay_distinct(isolated_env):
    """🔴 §五 〇b-6:**当日无行 = 没跑过**(404);**有行但 `baskets_covered=0` =
    跑过了、D0 当天就没有 T1/T2 篮子**。⛔ 不许把两者混成一句「今天没有竞价报告」。"""
    env = isolated_env
    snap = _snapshot(env, {c: _q(c) for c in ac.MARKET_INDEX_CODES})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert astore.save_mechanical(mech, db_path=env.db_path) is True
    rep = astore.load_report(D1, db_path=env.db_path)
    assert rep is not None and rep["baskets_covered"] == 0
    assert astore.load_verdicts(D1, db_path=env.db_path) == []
    # 另一天压根没跑 → 无行(端点据此 404)
    assert astore.load_report(date(2026, 8, 12), db_path=env.db_path) is None


def test_no_card_is_a_legal_intermediate_state(isolated_env):
    """「有篮子无卡」是合法中间态:逐票读数照出,失效位与预案一致性判不了,
    ⛔ 不拿默认条件顶上,并在 notes 里如实标 `no_card`。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=None)
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    b = mech.baskets[0]
    assert "no_card" in b.notes
    assert b.members[0].gap_pct == pytest.approx(0.05)
    assert b.members[0].hit_invalidation is None
    assert b.members[0].plan_fit == PLAN_FIT_UNKNOWN


def test_short_summary_is_text_and_carries_the_proxy_sample_caveat(isolated_env):
    """机械层第 6 条职责:短摘要。⚠ 「竞价强势股是代理样本、不取得交易资格」必须
    在喂给 LLM 的资料里说出口(§五 ⑨-B-2)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"},
                             {"ts_code": "600009.SH", "name": "上海机场", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    # 600009.SH 进关注池只能靠持仓/涨停等来源;这里直接验空态那一支文案。
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    txt = am.short_summary(mech)
    assert "【数据状态】" in txt and "【市场对照指数竞价】" in txt
    assert "竞价强势股" in txt
    assert "600000.SH" in txt


def test_second_stage_writes_only_llm_columns_and_is_idempotent(isolated_env):
    """两阶段写的第二阶段:机械列**逐位不变**;`WHERE llm_stage='pending'` 让第二次
    finalize 一个字都写不进去(= 9:29 之后迟到的结论被丢弃的**结构性**那一半)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    bid = _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    snap = _snapshot(env, {"600000.SH": _q("600000.SH")})
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    astore.save_mechanical(mech, db_path=env.db_path)
    mech_cols = ("trade_date, d0_date, source, captured_at, requested_codes, fetched_codes, "
                 "missing_codes_json, conflict_codes_json, data_quality, index_gaps_json, "
                 "market_anchors_json, baskets_covered, created_at")
    before = _rows(env.db_path, f"SELECT {mech_cols} FROM auction_reports")
    before_v = _rows(env.db_path, "SELECT members_json, sector_sync_json, rel_strength_json, "
                                  "history_json, hit_invalidation_json, plan_consistency_json, "
                                  "data_quality, created_at FROM auction_verdicts")

    assert astore.finalize_report(D1, llm_stage="ok", market_overview="指数普遍高开。",
                                  manual_note_attached=True, llm_elapsed_ms=1234,
                                  db_path=env.db_path) is True
    assert astore.finalize_verdict(bid, verdict="neutral", verdict_raw="confirm",
                                   clamped_by="clamped_by_data_quality",
                                   reasons=["理由一"], llm_fields={"verdict": "confirm"},
                                   llm_stage="ok", db_path=env.db_path) is True

    assert _rows(env.db_path, f"SELECT {mech_cols} FROM auction_reports") == before
    assert _rows(env.db_path, "SELECT members_json, sector_sync_json, rel_strength_json, "
                              "history_json, hit_invalidation_json, plan_consistency_json, "
                              "data_quality, created_at FROM auction_verdicts") == before_v
    rep = astore.load_report(D1, db_path=env.db_path)
    assert rep["market_overview"] == "指数普遍高开。" and rep["llm_stage"] == "ok"
    assert rep["risks_json"], "risks=None 时必须**保留机械段那份**,⛔ 不许被空数组覆盖"
    v = astore.load_verdict_for_basket(bid, db_path=env.db_path)
    assert (v["verdict"], v["verdict_raw"], v["clamped_by"]) == (
        "neutral", "confirm", "clamped_by_data_quality")

    # 第二次 finalize:已经不是 pending 了 → 写不进去(迟到的结论被丢弃)。
    assert astore.finalize_report(D1, llm_stage="ok", market_overview="迟到的解释",
                                  db_path=env.db_path) is False
    assert astore.finalize_verdict(bid, verdict="veto", llm_stage="ok",
                                   db_path=env.db_path) is False
    assert astore.load_report(D1, db_path=env.db_path)["market_overview"] == "指数普遍高开。"
    assert astore.load_verdict_for_basket(bid, db_path=env.db_path)["verdict"] == "neutral"


def test_history_reports_days_available_without_any_sufficiency_threshold(isolated_env):
    """🔴 **用户裁定 P3-69(2026-08-12)之后语义换了一半**:「样本够不够」不再交 LLM,
    改成机械判据 `n ≥ 15` —— 但那个 15 是**用户拍板的**,不是工程侧翻译的。

    这条守门因此改成:① 零分区时如实报 `n=0` + 原因;② 门槛值恒等于裁定的 15;
    ③ 机械层**除三个裁定值外不许出现第四个天数门槛**。"""
    env = isolated_env
    out = am.load_history(["600000.SH"], D1, parquet_dir=env.parquet_dir)
    assert out["history_days_available"] == 0
    assert out.get("unavailable_reason")          # 开局无分区是**数据现实**,如实说
    assert out["history_min_sample_for_comparison"] == am.HISTORY_MIN_SAMPLE_FOR_COMPARISON == 15
    assert out["history_sample_sufficient"] is False
    src = am.__loader__.get_source(am.__name__) or ""
    body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    # 唯一允许的比较是那条裁定判据本身(`n >= HISTORY_MIN_SAMPLE_FOR_COMPARISON`);
    # ⛔ 不许对 `history_days_available` 这个键再写第二条门槛。
    assert "history_days_available >" not in body and "history_days_available <" not in body


# ══════════════════════════════════════════════════════════════════════════
# 🔴 用户裁定 P3-69(2026-08-12):历史对照窗口 = 最近 20 个有效交易日 /
#    最多回溯 60 个自然日 / n ≥ 15 才允许比较 / 当日不进基线
# ══════════════════════════════════════════════════════════════════════════

def _write_auction_snapshots(env, code: str, days, *, gap=0.01, volume=5000.0) -> None:
    """往 `auction_snapshots` 分区写几天历史竞价(⛔ 只写测试用 parquet_dir)。"""
    import polars as pl

    from neckline.data.market_data import write_table_day

    for d in days:
        write_table_day("auction_snapshots", d, pl.DataFrame([{
            "ts_code": code, "trade_date": d, "auction_price": 10.0,
            "auction_volume": volume, "auction_amount": 50000.0, "pre_close": 10.0,
            "gap_pct": gap, "captured_at": "2026-08-11 09:26:00",
        }]), parquet_dir=env.parquet_dir)


def test_history_window_is_the_three_numbers_the_user_ruled(isolated_env):
    """🔴 三个数**逐字**等于 2026-08-12 用户裁定:20 交易日 / 60 自然日上界 / n ≥ 15。
    ⛔ 工程侧一个都不许改(改了要重新拍板)。"""
    assert am.HISTORY_LOOKBACK_TRADING_DAYS == 20
    assert am.HISTORY_LOOKBACK_MAX_CALENDAR_DAYS == 60
    assert am.HISTORY_MIN_SAMPLE_FOR_COMPARISON == 15


def test_history_window_is_exactly_twenty_days_of_a_real_calendar(isolated_env):
    """🔵-5:下面那条老用例跑在**空 `trade_cal`** 上(走静态表 + 工作日近似),
    而且拿建窗口的同一个函数自证「每天都是交易日」—— 实现哪天退化成只返回 10 天,
    它照样绿。**这条钉死数量与内容**:往 DB 塞一段日历,断言窗口**恰好等于**
    「该日历里 D1 之前的最后 20 个交易日」(期望值独立构造,⛔ 不复用被测函数)。"""
    import neckline.calendar.trading_calendar as tc_mod

    from tests.conftest import insert_trade_cal

    env = isolated_env
    cur, opens = date(2026, 6, 1), []
    while cur <= date(2026, 8, 11):
        if cur.weekday() < 5:           # 测试日历:周一至周五开市(不含节假日表)
            opens.append(cur)
        cur += timedelta(days=1)
    insert_trade_cal(env, opens, range_start=date(2026, 5, 1), range_end=date(2026, 9, 30))
    tc_mod.reset_cache()

    window = am.history_window_days(D1)
    expected = [d for d in opens if d < D1][-am.HISTORY_LOOKBACK_TRADING_DAYS:]
    assert len(window) == am.HISTORY_LOOKBACK_TRADING_DAYS == 20
    assert window == expected, "窗口既不是 20 个,也不是日历里那 20 个"
    # 60 自然日仍是**上界**(裁定原文),20 个交易日回溯 ≈ 26 自然日,夹得住
    assert window[0] >= D1 - timedelta(days=am.HISTORY_LOOKBACK_MAX_CALENDAR_DAYS)


def test_history_window_counts_trading_days_not_calendar_days(isolated_env):
    """🔴 裁定原文:「**不使用自然日直接计数**」。窗口最多 20 个交易日,且每一天都得
    是交易日(走 `neckline.calendar`,⛔ 不许自己数自然日)。"""
    from neckline.calendar import is_trading_day

    window = am.history_window_days(D1)
    assert 0 < len(window) <= am.HISTORY_LOOKBACK_TRADING_DAYS
    assert all(is_trading_day(d) for d in window), "窗口里混进了非交易日"
    assert all(d < D1 for d in window), "🔴 当日竞价⛔ 不许进入自身历史基线"
    # 60 自然日是**回溯上界**:窗口最早的一天不得早于 D1 − 60 天
    assert window[0] >= D1 - timedelta(days=am.HISTORY_LOOKBACK_MAX_CALENDAR_DAYS)


def test_history_baseline_excludes_today_even_when_todays_partition_exists(isolated_env):
    """🔴 裁定原文:「**当日竞价不进入自身历史基线**」。

    **正面守门**:造一个**含当日分区**的库(回放 / 补跑时这完全可能),断言当日那一行
    ⛔ 不出现在基线里 —— 不许靠"今天还没落盘"这个巧合。"""
    env = isolated_env
    prior = [d for d in am.history_window_days(D1)][-3:]
    _write_auction_snapshots(env, "600000.SH", prior + [D1])
    out = am.load_history(["600000.SH"], D1, parquet_dir=env.parquet_dir)
    got_days = {r["trade_date"] for r in out["per_member"]["600000.SH"]}
    assert str(D1) not in got_days, f"当日({D1})混进了自身历史基线:{sorted(got_days)}"
    assert got_days == {str(d) for d in prior}
    assert out["history_days_available"] == len(prior)


def test_history_sample_below_fifteen_is_flagged_insufficient(isolated_env):
    """🔴 裁定:`n < 15` → 标「历史样本不足」+ **只展示原始值**。
    ⚠ 那句话必须**同时**出现在契约与喂 LLM 的短摘要里 —— 光留个布尔,prompt 那条
    纪律永远不生效(V2.3.3 复审 🔴-1 的同款教训)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    _write_auction_snapshots(env, "600000.SH", am.history_window_days(D1)[-14:])
    mech = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    h = mech.baskets[0].history
    assert h["history_days_available"] == 14
    assert h["history_sample_sufficient"] is False
    assert "历史样本不足" in h["history_insufficient_note"]
    assert "**" not in h["history_insufficient_note"], "下发给界面的文案⛔ 不许带 Markdown"
    summary = am.short_summary(mech)
    assert "本项样本不足" in summary and "不得据此做比较结论" in summary


def test_history_sample_at_fifteen_allows_comparison(isolated_env):
    """🔴 边界:`n == 15` **就是**「允许形成历史比较」那一侧(裁定原文 `n ≥ 15`)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    _write_auction_snapshots(env, "600000.SH", am.history_window_days(D1)[-15:])
    mech = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    h = mech.baskets[0].history
    assert h["history_days_available"] == 15
    assert h["history_sample_sufficient"] is True
    assert "history_insufficient_note" not in h
    assert "允许形成历史比较" in am.short_summary(mech)


def test_llm_prompt_states_the_insufficient_sample_rule(isolated_env):
    """🔴 prompt 里那条「样本不足只描述原始值」必须**与机械判据对上口径** ——
    现在是系统先判好、模型照办(⛔ 不再让模型自己判"够不够")。"""
    from neckline.auction.llm import AUCTION_SYSTEM_PROMPT

    assert "本项样本不足" in AUCTION_SYSTEM_PROMPT
    assert "不得据此做任何比较结论" in AUCTION_SYSTEM_PROMPT
    assert "系统已经替你判好了" in AUCTION_SYSTEM_PROMPT
    # 🔴 定向复审 🔴-1:判据是**逐票**的,prompt 必须点明「同一篮里两种标记会同时出现」
    assert "逐票" in AUCTION_SYSTEM_PROMPT
    assert "同一个篮子里两种标记会同时出现" in AUCTION_SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════════
# 🔴 定向复审 🔴-1 / 🟡-1(2026-08-12):`n` 是**逐票**的,不是全篮日期并集;
#    且「允许比较」必须**同时**给出可据以比较的历史读数
# ══════════════════════════════════════════════════════════════════════════

def _write_auction_history(env, spec) -> None:
    """多只票的历史竞价:`spec = {code: [(day, volume), …]}`。

    ⚠ **按天合并写一次**:`write_table_day` 是**整日文件覆盖写**,逐 code 各写一遍会让
    后一个 code 把前一个 code 当天那行冲掉(写这条守门时真踩过,20 天变 18 天)。"""
    import polars as pl

    from neckline.data.market_data import write_table_day

    by_day: dict = {}
    for code, items in spec.items():
        for d, volume in items:
            by_day.setdefault(d, []).append({
                "ts_code": code, "trade_date": d, "auction_price": 10.0,
                "auction_volume": float(volume), "auction_amount": 50000.0,
                "pre_close": 10.0, "gap_pct": 0.01, "captured_at": "2026-08-11 09:26:00",
            })
    for d, rows in sorted(by_day.items()):
        write_table_day("auction_snapshots", d, pl.DataFrame(rows), parquet_dir=env.parquet_dir)


def test_history_sample_is_counted_per_member_not_as_a_basket_wide_union(isolated_env):
    """🔴 **正面守门**(定向复审 🔴-1 逮到的后门):一个篮子里 `600519.SH` 有 20 天历史、
    `600000.SH` 只有 2 天 —— 原实现取**并集** → 篮级 `n=20` → 「允许形成历史比较」→
    模型可以对只有 2 天历史的那只说「明显放量」。

    现在:逐票各算各的;篮级取**最小值**;短摘要必须**点名**是哪只不够。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"},
                             {"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600519.SH", "600000.SH"],
                 card=_card_json(["600519.SH", "600000.SH"]))
    window = am.history_window_days(D1)
    old = window[-am.HISTORY_MIN_SAMPLE_FOR_COMPARISON:]          # 老面孔:够样本
    _write_auction_history(env, {
        "600519.SH": [(d, 5000.0) for d in old],
        "600000.SH": [(d, 5000.0) for d in window[-2:]],          # 今天才进池:2 天
    })
    mech = am.build_mech(_snapshot(env, {c: _q(c) for c in ("600519.SH", "600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    h = mech.baskets[0].history

    per = {e["ts_code"]: e for e in h["history_days_per_member"]}
    assert per["600519.SH"]["days_available"] == len(old) == 15
    assert per["600519.SH"]["sample_sufficient"] is True
    assert per["600000.SH"]["days_available"] == 2
    assert per["600000.SH"]["sample_sufficient"] is False, (
        "🔴 只有 2 天历史的票⛔ 不许被讲成「允许形成历史比较」")
    assert h["history_insufficient_codes"] == ["600000.SH"]
    # 篮级:取**最小值**(⛔ 不是并集的 20),且自曝这一点
    assert h["history_days_available"] == 2
    assert h["history_days_available_basis"] == "min_per_member"
    assert h["history_sample_sufficient"] is False

    # 🔴 短摘要里**看得出是哪只不够**(⛔ 不许只有一个篮级总数,人和模型都看不出来)
    summary = am.short_summary(mech)
    assert "600000.SH 2 天" in summary
    assert "600519.SH 15 天" in summary
    assert "不得据此做比较结论" in summary
    assert "允许形成历史比较" in summary, "样本够的那只⛔ 不该被另一只连坐"


def test_prompt_gives_the_actual_history_readings_not_just_a_permission_slip(isolated_env):
    """🟡-1:标了「允许形成历史比较」却**一个历史数字都不给**,模型只能沉默或编。

    够样本 → 摘要里必须出现窗口内的**对照读数**(最低 / 中位 / 最高);
    不够样本 → 必须出现**逐日原始值**(裁定 P3-69 原文:「只展示原始值」)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板"},
                             {"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600519.SH", "600000.SH"],
                 card=_card_json(["600519.SH", "600000.SH"]))
    window = am.history_window_days(D1)
    old = window[-am.HISTORY_MIN_SAMPLE_FOR_COMPARISON:]      # 15 天,量逐日不同
    _write_auction_history(env, {
        "600519.SH": [(d, 4000.0 + 100.0 * i) for i, d in enumerate(old)],
        "600000.SH": [(d, 7777.0) for d in window[-2:]],
    })
    mech = am.build_mech(_snapshot(env, {c: _q(c) for c in ("600519.SH", "600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    per = {e["ts_code"]: e for e in mech.baskets[0].history["history_days_per_member"]}
    stats = per["600519.SH"]["comparison_readings"]["auction_volume"]
    assert (stats["min"], stats["max"], stats["observed"]) == (4000.0, 5400.0, 15)
    assert stats["median"] == pytest.approx(4700.0)
    # ⚠ 样本不足的那只**不发**对照读数(⛔ 别给一个不许用的数)
    assert "comparison_readings" not in per["600000.SH"]

    summary = am.short_summary(mech)
    assert "窗口内历史读数" in summary and "中位 4700.00" in summary
    assert "逐日原始值" in summary and "7777.00" in summary


def test_a_member_with_zero_history_is_not_told_as_business_as_usual(isolated_env):
    """「一天都没有」与「有 2 天但不够」是两件事(「没有」≠「不满足」)。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "浦发银行", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    mech = am.build_mech(_snapshot(env, {"600000.SH": _q("600000.SH")}),
                         db_path=env.db_path, parquet_dir=env.parquet_dir)
    h = mech.baskets[0].history
    assert h["history_days_per_member"] == [
        {"ts_code": "600000.SH", "days_available": 0, "sample_sufficient": False}]
    assert "一条历史竞价快照都没有" in am.short_summary(mech)


def test_prompt_never_calls_a_market_index_the_sector_benchmark(isolated_env):
    """🟡-2:裁定 ④「⛔ 禁止使用市场指数代替板块基准」—— **计算侧本来就没用**,
    但改前 prompt 里管上证叫「板块基准指数」,在唯一读这些字的消费者眼里,
    那道禁令当场失效。**只改名,读数一个没动。**"""
    env = isolated_env
    peers = _seed_sector_peers(env)
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {c: _q(c) for c in ["600000.SH", "000001.SH", "399001.SZ", "399006.SZ"] + peers}
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    ss = _basket_by_key(mech, "k1").sector_sync
    assert "benchmarks" not in ss, "⛔ 别再留一个会被读成「板块基准」的裸键名"
    assert "000001.SH" in ss["listing_board_benchmarks"]
    assert "不是本次的「板块基准」" in ss["listing_board_benchmarks_note"]
    assert "**" not in ss["listing_board_benchmarks_note"], "下发文案⛔ 不许带 Markdown"

    summary = am.short_summary(mech)
    assert "板块基准指数 000001.SH" not in summary, "🔴 ⛔ 不许再管市场指数叫「板块基准指数」"
    assert "所属上市板块对照指数" in summary
    # prompt 的 system 段也得把这第三个东西说清楚
    from neckline.auction.llm import AUCTION_SYSTEM_PROMPT

    assert "所属上市板块对照指数" in AUCTION_SYSTEM_PROMPT
    assert "**不是**本次的板块基准" in AUCTION_SYSTEM_PROMPT


# ══════════════════════════════════════════════════════════════════════════
# 🔴 用户裁定 P3-70(2026-08-12):`rel_to_index` 与 `rel_to_sector` 分开计算,
#    禁止同源同值;⛔ 禁止用市场指数代替板块基准;三支指数等权平均正式停用
# ══════════════════════════════════════════════════════════════════════════

_INDEX_MAP_CASES = [
    ("600000.SH", "主板", "000001.SH"),      # 沪市主板 → 上证指数
    ("000002.SZ", "主板", "399001.SZ"),      # 深市主板 → 深证成指
    ("300001.SZ", "创业板", "399006.SZ"),    # 创业板 → 创业板指
    ("833171.BJ", "北交所", "899050.BJ"),    # 北交所 → 北证50
]


@pytest.mark.parametrize("code,market,want", _INDEX_MAP_CASES)
def test_rel_to_index_uses_the_four_rulings_market_index(isolated_env, code, market, want):
    """🔴 裁定的四条映射**逐条**照抄:沪主板 / 深主板 / 创业板 / 北交所。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": code, "name": code, "market": market}])
    _seed_basket(env, [code], card=_card_json([code]))
    quotes = {code: _q(code, price=10.06, pre_close=10.0)}                 # +0.60%
    for idx in ("000001.SH", "399001.SZ", "399006.SZ", "899050.BJ"):
        quotes[idx] = _q(idx, price=10.01, pre_close=10.0)                 # 各 +0.10%
    r = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.index_benchmark_code == want
    assert r.rel_to_index == pytest.approx(0.006 - 0.001, abs=1e-9)
    assert r.rel_to_index_reason is None


def test_star_board_gets_no_market_index_and_never_falls_back(isolated_env):
    """🔴 裁定:「**科创板按 K8 规则排除**」→ `None` + `board_excluded`,
    ⛔ **绝不许 fallback 到别的指数**(拿上证顶替 = 把"按规则不该有"讲成"是这么多")。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "688001.SH", "name": "华兴源创", "market": "科创板"}])
    _seed_basket(env, ["688001.SH"], card=_card_json(["688001.SH"]))
    quotes = {"688001.SH": _q("688001.SH", price=10.06, pre_close=10.0)}
    for idx in ("000001.SH", "399001.SZ", "399006.SZ", "000688.SH"):
        quotes[idx] = _q(idx, price=10.01, pre_close=10.0)
    r = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.rel_to_index is None, "科创板⛔ 不许算出一个市场相对强弱"
    assert r.index_benchmark_code is None, "⛔ 不许 fallback 到任何指数"
    assert r.rel_to_index_reason == "board_excluded"
    # 「没有」≠「持平」:短摘要必须把这句话说出口
    assert "科创板按 K8 基础股票池规则排除" in am.short_summary(
        am.build_mech(_snapshot(env, quotes), db_path=env.db_path, parquet_dir=env.parquet_dir))


def _seed_sector_peers(env, *, industry="半导体", n=3, market="主板", extra_members=()):
    """造 n 只**同行业板块对照股** + 被检验的那只篮内票(`600000.SH`)。

    ⚠ **取数域 = 盘中关注池**(裁定 P3-70 ② 的既定口径)—— 对照股必须真的在池里,
    否则 9:26 那一拍根本没有它们的报价。这里把它们放进**另一个 T1 篮子**(池的合法
    来源之一),既真实又不污染被检验篮子的成员集合。
    """
    rows = [{"ts_code": "600000.SH", "name": "篮内票", "market": market, "industry": industry}]
    rows += [{"ts_code": c, "name": c, "market": market, "industry": industry}
             for c in extra_members]
    peers = [f"60010{i}.SH" for i in range(n)]
    rows += [{"ts_code": c, "name": f"对照{c}", "market": market, "industry": industry}
             for c in peers]
    insert_stock_basic(env, rows)
    _seed_basket(env, peers, key="peers", name="对照篮", card=_card_json(peers))
    return peers


def _basket_by_key(mech, key: str):
    return next(b for b in mech.baskets if b.basket_key == key)


def test_rel_to_sector_uses_peer_median_and_differs_from_rel_to_index(isolated_env):
    """🔴 裁定核心:两条路径**分开计算,禁止同源同值**。

    造一个「市场指数 +0.10% / 三只同行业对照股 +2.00%/+3.00%/+4.00%」的早晨:
    板块基准取中位数 +3.00%,市场基准取上证 +0.10% —— 两个读数**必然不同**。
    """
    env = isolated_env
    peers = _seed_sector_peers(env)
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {
        "600000.SH": _q("600000.SH", price=10.50, pre_close=10.0),   # +5.00%
        "000001.SH": _q("000001.SH", price=10.01, pre_close=10.0),   # 上证 +0.10%
        "399001.SZ": _q("399001.SZ", price=10.01, pre_close=10.0),
        "399006.SZ": _q("399006.SZ", price=10.01, pre_close=10.0),
        peers[0]: _q(peers[0], price=10.20, pre_close=10.0),         # +2.00%
        peers[1]: _q(peers[1], price=10.30, pre_close=10.0),         # +3.00%(中位)
        peers[2]: _q(peers[2], price=10.40, pre_close=10.0),         # +4.00%
    }
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    r = _basket_by_key(mech, "k1").members[0]
    assert r.rel_to_sector_source == "peer_median"
    assert sorted(r.sector_peer_codes) == sorted(peers)
    assert r.sector_benchmark_gap_pct == pytest.approx(0.03, abs=1e-9)
    assert r.rel_to_sector == pytest.approx(0.05 - 0.03, abs=1e-9)
    assert r.rel_to_index == pytest.approx(0.05 - 0.001, abs=1e-9)
    assert r.rel_to_sector != r.rel_to_index, "🔴 ⛔ 禁止同源同值(裁定 P3-70)"
    assert r.industry == "半导体"


def test_sector_peers_exclude_the_baskets_own_members(isolated_env):
    """板块基准必须**独立于被检验的假设** —— 拿篮内同伴当"板块"等于自己给自己打分。"""
    env = isolated_env
    peers = _seed_sector_peers(env, extra_members=("600009.SH",))
    _seed_basket(env, ["600000.SH", "600009.SH"],
                 card=_card_json(["600000.SH", "600009.SH"]))
    quotes = {c: _q(c) for c in ["600000.SH", "600009.SH", "000001.SH", "399001.SZ",
                                 "399006.SZ"] + peers}
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    r = _basket_by_key(mech, "k1").members[0]
    assert "600009.SH" not in r.sector_peer_codes and "600000.SH" not in r.sector_peer_codes
    assert sorted(r.sector_peer_codes) == sorted(peers)


def test_rel_to_sector_returns_null_and_data_insufficient_below_three_peers(isolated_env):
    """🔴 裁定 ③:对照不足 → `null` + `data_insufficient`。⛔ 不是 0、⛔ 不是省略键。"""
    env = isolated_env
    peers = _seed_sector_peers(env, n=2)          # 只有 2 只 → 不足 3
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {c: _q(c) for c in ["600000.SH", "000001.SH", "399001.SZ", "399006.SZ"] + peers}
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    r = _basket_by_key(mech, "k1").members[0]
    assert r.rel_to_sector is None
    assert r.rel_to_sector_reason == "data_insufficient"
    assert r.rel_to_sector_source == "unavailable"
    assert r.rel_to_index is not None, "市场那条路照走(两条路径互不牵连)"
    assert r.to_dict()["rel_to_sector"] is None, "⛔ 省略键 / 填 0 都不许"


def test_no_industry_is_a_different_code_than_data_insufficient(isolated_env):
    """🔴 「没有」≠「不满足」:查不到行业口径(取数域本身缺)与「同行业只有 2 只」
    是两种成因,⛔ 不许折平成同一个码。"""
    env = isolated_env
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "篮内票", "market": "主板"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {c: _q(c) for c in ("600000.SH", "000001.SH", "399001.SZ", "399006.SZ")}
    r = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.rel_to_sector is None and r.rel_to_sector_reason == "no_industry"
    assert r.industry is None


def test_industry_map_unavailable_is_not_folded_into_no_industry(isolated_env, monkeypatch):
    """🔵-7:**整张行业表读不到**(系统缺席)与「这一只票没登记行业」是两种成因 ——
    改前两者发同一个码 `no_industry`,差别只在报告级 `notes` 里。按 P0-39 的纪律
    (系统缺席 ≠ 实质判断),它必须有自己的码。"""
    import neckline.report.industry_strength as isx

    env = isolated_env
    # 这只票在 `stock_basic` 里**登记了**行业 —— 所以 `no_industry` 在这里是错的答案
    insert_stock_basic(env, [{"ts_code": "600000.SH", "name": "篮内票",
                              "market": "主板", "industry": "半导体"}])
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))

    def _boom(*_a, **_kw):
        raise RuntimeError("行业表读失败")

    monkeypatch.setattr(isx, "load_industry_map", _boom)
    quotes = {c: _q(c) for c in ("600000.SH", "000001.SH", "399001.SZ", "399006.SZ")}
    snap = _snapshot(env, quotes)
    assert "industry_map_unavailable" in snap.notes, "报告级 note 仍要留下"
    r = am.build_mech(snap, db_path=env.db_path,
                      parquet_dir=env.parquet_dir).baskets[0].members[0]
    assert r.rel_to_sector is None
    assert r.rel_to_sector_reason == "industry_map_unavailable"
    assert r.rel_to_sector_reason != "no_industry", "⛔ 系统缺席不许讲成「这只票没有行业」"
    # 那句人话也得分开(⛔ 两种成因不许共用一句)
    assert "系统缺席" in am.short_summary(am.build_mech(
        snap, db_path=env.db_path, parquet_dir=env.parquet_dir))


def test_sector_benchmark_is_structurally_never_a_market_index(isolated_env):
    """🔴 裁定 ④:**⛔ 禁止使用市场指数代替板块基准** —— 结构性保证 + 正面守门。

    取样域 `snap.industry_of` 只由 `stock_basic` 派生 → 指数码根本进不去;再叠一层
    `snap.index_codes` 显式排除。这条断言把「基准来源恒不是那几支指数」钉死。
    """
    env = isolated_env
    peers = _seed_sector_peers(env)
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {c: _q(c) for c in ["600000.SH", "000001.SH", "399001.SZ", "399006.SZ",
                                 "899050.BJ", "000688.SH"] + peers}
    snap = _snapshot(env, quotes)
    banned = {"000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "899050.BJ"}
    assert not (set(snap.industry_of) & banned), "指数混进了板块对照股取样域"
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    for b in mech.baskets:
        for per in b.rel_strength["per_member"]:
            assert per["sector_index_code"] not in banned
            assert not (set(per["sector_peer_codes"]) & banned)
            assert per["sector_benchmark_source"] != "market_index"


def test_rel_strength_records_which_index_and_which_group_it_subtracted(isolated_env):
    """「相对强弱」必须看得见**减的是哪一支 / 哪一组** —— 否则它又变成一个说不清出处的数。"""
    env = isolated_env
    peers = _seed_sector_peers(env)
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {c: _q(c) for c in ["600000.SH", "000001.SH", "399001.SZ", "399006.SZ"] + peers}
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    b = _basket_by_key(mech, "k1")
    per = b.rel_strength["per_member"][0]
    assert per["ts_code"] == "600000.SH"
    assert per["index_benchmark_code"] == "000001.SH"      # 沪主板 → 上证(裁定映射)
    assert per["sector_benchmark_source"] == "peer_median"
    assert sorted(per["sector_peer_codes"]) == sorted(peers)
    assert b.rel_strength["sector_peer_min"] == 3
    assert b.rel_strength["sector_benchmark_sources"]["peer_median"] == 1


def test_three_index_equal_weight_average_is_gone_from_the_whole_repo():
    """🔴 裁定原文:「『三支指数等权平均』**正式停用**」。**反向守门**:整个 `auction/`
    包里不许再出现把多支指数平均成一个合成基准的写法。"""
    import ast
    from pathlib import Path

    pkg = Path(am.__file__).resolve().parent
    for p in sorted(pkg.rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(p))
        lines = text.splitlines()
        for node in ast.walk(tree):     # 剥掉 docstring(禁令本身写在注释/文档里)
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None) or []
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    c = body[0].value
                    for i in range(c.lineno - 1, (c.end_lineno or c.lineno)):
                        lines[i] = ""
        body_text = "\n".join(ln for ln in lines if not ln.strip().startswith("#"))
        for bad in ("sum(idx_gaps", "mean(index_gaps", "len(MARKET_INDEX_CODES",
                    "MARKET_INDEX_CODES)/", "/ 3.0", "/ 3)"):
            assert bad not in body_text, f"{p.name} 里出现了指数等权平均的痕迹:{bad}"


def test_rel_to_index_is_exactly_its_own_index_gap_subtracted(isolated_env):
    """🔵-4:上面那条反向守门禁的是几个**字面片段**(`sum(idx_gaps` / `/ 3.0` …)——
    有人写 `avg = sum(vals) / len(vals)` 重新引入等权平均,它**全绿**。

    这条是**语义判据**:逐票对拍 `rel_to_index == gap_pct − gap_of(这只票自己的那支指数)`,
    且三支指数**各不相同**(相同就退化成"随便减哪支都对",测了等于没测)。"""
    env = isolated_env
    codes = [("600000.SH", "主板"), ("000002.SZ", "主板"), ("300001.SZ", "创业板")]
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": m} for c, m in codes])
    _seed_basket(env, [c for c, _m in codes], card=_card_json([c for c, _m in codes]))
    quotes = {
        "600000.SH": _q("600000.SH", price=10.06, pre_close=10.0),   # +0.60%
        "000002.SZ": _q("000002.SZ", price=10.20, pre_close=10.0),   # +2.00%
        "300001.SZ": _q("300001.SZ", price=10.90, pre_close=10.0),   # +9.00%
        "000001.SH": _q("000001.SH", price=10.01, pre_close=10.0),   # 上证 +0.10%
        "399001.SZ": _q("399001.SZ", price=10.04, pre_close=10.0),   # 深成 +0.40%
        "399006.SZ": _q("399006.SZ", price=10.18, pre_close=10.0),   # 创业板 +1.80%
    }
    snap = _snapshot(env, quotes)
    idx_gaps = {c: snap.gap_of(c) for c in ("000001.SH", "399001.SZ", "399006.SZ")}
    assert len(set(idx_gaps.values())) == 3, "三支指数必须各不相同,否则这条测了等于没测"
    mech = am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)
    seen = set()
    for r in _basket_by_key(mech, "k1").members:
        assert r.index_benchmark_code is not None
        seen.add(r.index_benchmark_code)
        want = snap.gap_of(r.index_benchmark_code)
        assert r.index_benchmark_gap_pct == pytest.approx(want, abs=1e-9)
        # 🔴 这才是「单指数」的真判据:减的恰好是它自己那一支,⛔ 不是任何合成基准
        assert r.rel_to_index == pytest.approx(r.gap_pct - want, abs=1e-9)
    assert seen == set(idx_gaps), "三只票该各自减各自的那支指数"


def test_rel_to_index_is_one_index_not_an_average_on_a_divergent_morning(isolated_env):
    """🔴 「三支指数等权平均」停用的**行为守门**(原 🟡-6 那条改写):
    创业板 +1.8% / 上证 +0.1% / 深成 +0.4% 的分化日,一只**沪主板**票 +0.6% 必须被算成
    `+0.50%`(跑赢自己的基准),⛔ 不许被等权合成指数(+0.77%)讲成 `−0.17%` 跑输。"""
    env = isolated_env
    peers = _seed_sector_peers(env)
    _seed_basket(env, ["600000.SH"], card=_card_json(["600000.SH"]))
    quotes = {
        "600000.SH": _q("600000.SH", price=10.06, pre_close=10.0),   # +0.60%
        "000001.SH": _q("000001.SH", price=10.01, pre_close=10.0),   # 上证 +0.10%
        "399001.SZ": _q("399001.SZ", price=10.04, pre_close=10.0),   # 深证 +0.40%
        "399006.SZ": _q("399006.SZ", price=10.18, pre_close=10.0),   # 创业板 +1.80%
    }
    quotes.update({c: _q(c) for c in peers})
    mech = am.build_mech(_snapshot(env, quotes), db_path=env.db_path,
                         parquet_dir=env.parquet_dir)
    r = _basket_by_key(mech, "k1").members[0]
    assert r.index_benchmark_code == "000001.SH"
    assert r.rel_to_index == pytest.approx(0.006 - 0.001, abs=1e-9)
    assert r.rel_to_index > 0, "跑赢了自己的基准,⛔ 不许被合成指数讲成跑输"
