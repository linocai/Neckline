"""V2.4.0 **P2 验收**:竞价数据可靠性与 LLM 降级语义(施工图 §五 P2,10 条逐条)。

P2 修的四个病(审计规格 P2 目标):
  ① 上一交易日的缓存行情可能被当作今天的竞价数据;
  ② 一只**无关**指数缺失导致整篮强制中性;
  ③ 「跨源冲突为空」其实从未交叉核验过;
  ④ 晚间 LLM 故障被表现成「今天没有篮子」。

**验收编号与施工图 §五「P2 验收用例(10 条)」1:1 对齐**(类名带编号,便于逐条核对):

  1  新浪返昨日、腾讯返今日:用腾讯,记录主源过期
  2  两源都返昨日:critical `insufficient`
  3  时间戳无法解析:**不得判 `ok`**
  4  成员数据完整、无关指数缺失:critical `ok` + context `degraded`,**不夹中性**
  5  成员对应指数缺失:critical `degraded`,confirm/veto **被夹中性**
  6  双源一边触发失效位、一边不触发:`conflict`,不能高置信输出
  7  LLM provider 缺席:报告显示「未解释」,**不是「零篮子」**
  8  LLM 成功返回空数组:合法显示零篮子
  9  竞价 LLM 缺席:机械数据与失效警报**仍正常落库**
  10 v2.3.3 历史竞价记录**可正常解码**

另加三组守门(它们守的是"靠自觉就会失守"的那类):
  · 🔴 **零容差**(用户裁定 #2)—— ⛔ 全仓不许出现任何秒级容差常量;
  · 🔴 **`get_quotes()` 行为逐位不变**(P2.2 只**新增**双源路径,⛔ 没有改写老的);
  · 🔴 **未解释 seed 不是第四种候选状态**(K8 §十 末句)。
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from neckline import auction
from neckline.auction import (
    CLAMPED_BY_DATA_QUALITY,
    CONFLICT_INVALIDATION_DISAGREE,
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    QF_CONFLICT,
    QF_FRESH,
    QF_INSUFFICIENT,
    VERDICT_CONFIRM,
    VERDICT_NEUTRAL,
    VERDICT_PENDING_EXPLANATION,
)
from neckline.auction import collect as ac
from neckline.auction import llm as al
from neckline.auction import mech as am
from neckline.auction import pipeline as ap
from neckline.auction import quality as aq
from neckline.auction import store as astore
from neckline.sentinel.precall import MemberScript
from neckline.sentinel.quotes import DualQuote, Quote

from tests.conftest import insert_stock_basic
from tests.test_v233_auction_mech import _card_json, _seed_basket

_REPO = Path(__file__).resolve().parent.parent

D1 = date(2026, 8, 11)
D0 = date(2026, 8, 10)
NOW = datetime(2026, 8, 11, 9, 26, 30)
IDX = ac.MARKET_INDEX_CODES


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def _q(code: str, *, price: float = 10.5, pre_close: float = 10.0, open_: Optional[float] = None,
       ts: str = "2026-08-11 09:25:03", source: str = "sina",
       volume: float = 5000.0, amount: float = 52500.0) -> Quote:
    """一份竞价读数。⚠ 竞价阶段 `open == price`(9:25 撮合价即当前价)。"""
    o = price if open_ is None else open_
    return Quote(code=code, name=code, price=price, pre_close=pre_close, open=o,
                 high=max(price, o), low=min(price, o), volume=volume, amount=amount,
                 ts=ts, source=source)


def _dual(primary: Optional[Quote] = None, backup: Optional[Quote] = None) -> DualQuote:
    code = (primary or backup).code if (primary or backup) else ""
    return DualQuote(code=code, primary=primary, backup=backup)


def _snap(env, *, duals: Dict[str, DualQuote], now: datetime = NOW) -> ac.AuctionSnapshot:
    """⚠ **必须注入 `now_fn`**:`captured_at` 取的是真正拉完价那一刻。"""
    return ac.collect_auction_snapshot(
        D1, now, db_path=env.db_path, parquet_dir=env.parquet_dir,
        dual_quotes_fn=lambda cs: {c: duals.get(c, DualQuote(code=c)) for c in cs},
        now_fn=lambda: now,
    )


def _world(env, *, codes=("600000.SH",), key="k1", engine=("C", "C1")):
    insert_stock_basic(env, [{"ts_code": c, "name": c, "market": "主板"} for c in codes])
    bid = _seed_basket(env, list(codes), key=key, card=_card_json(list(codes)),
                       engine_code=engine[0], engine_version=engine[1])
    return bid


def _check(q: Optional[Quote], *, code: str = "600000.SH", role: str = "primary",
           trade_date: date = D1, captured_at: datetime = NOW):
    return aq.validate_quote(q, code=code, role=role, trade_date=trade_date,
                             captured_at=captured_at)


# ══════════════════════════════════════════════════════════════════════════
# P2.1 七项校验(逐项各一例;🔴 时间三项零容差)
# ══════════════════════════════════════════════════════════════════════════

class TestSevenChecks:
    def test_a_normal_auction_quote_passes_all_seven(self):
        c = _check(_q("600000.SH"))
        assert (c.status, c.errors) == (auction.QS_FRESH, ())
        assert c.ok and c.usable

    def test_check1_code_mismatch_is_malformed(self):
        """① 代码与市场映射:拿回来的必须**就是**要的那一只。
        ⚠ 这一项与「指数被前缀启发式拉成另一只股票」那条老坑同源 ——
        真出现时读数**完全正常**,只有代码对不上。"""
        c = _check(_q("600519.SH"), code="600000.SH")
        assert c.status == auction.QS_MALFORMED
        assert aq.ERR_CODE_MISMATCH in c.errors

    def test_check2_yesterdays_cached_quote_is_wrong_trade_date(self):
        """② 源日期 == D1 —— **本版要修的第 ① 个病**。上一交易日的缓存行情
        价 / 量 / 额全齐,只有 `ts` 里那个日期不对。"""
        c = _check(_q("600000.SH", ts="2026-08-10 09:25:03"))
        assert c.status == auction.QS_WRONG_TRADE_DATE
        assert not c.usable

    def test_check3_future_timestamp_is_zero_tolerance(self):
        """③ 🔴 **零容差**(2026-08-12 用户裁定 #2,⛔ 不是工程侧默认值)。
        **一秒**都算偏差;⛔ build 不许自己定 1s/3s/5s。"""
        one_second_late = _q("600000.SH", ts="2026-08-11 09:26:31")   # captured_at = 09:26:30
        c = _check(one_second_late)
        assert c.status == auction.QS_FUTURE_TIMESTAMP
        assert aq.ERR_FUTURE_TIMESTAMP in c.errors

    def test_check3_source_time_earlier_than_capture_is_normal(self):
        """🔴 裁定 #2 的**另一半**:K8 原文是「源时间**不晚于**本地抓取时间」——
        源时间**早于**抓取时刻是正常的,⛔ 别把它也判成偏差。"""
        c = _check(_q("600000.SH", ts="2026-08-11 09:25:00"))
        assert c.ok

    def test_check4_before_final_auction_is_rejected(self):
        """④ 可接受区间 `[09:25:00, captured_at]`。早于 9:25 = 不是最终撮合结果。
        ⚠ 那个 9:25 是 **K8 给的边界**,单一源复用 `capture.AUCTION_CAPTURE_START`。"""
        c = _check(_q("600000.SH", ts="2026-08-11 09:24:59"))
        assert c.status == auction.QS_BEFORE_FINAL_AUCTION
        assert aq.AUCTION_RESULT_TIME_START.hour == 9
        assert aq.AUCTION_RESULT_TIME_START.minute == 25

    def test_check5_price_and_preclose_are_fatal_but_open_is_not(self):
        """⑤ 必要字段**拆两档**:现价 / 前收盘是竞价涨跌幅的分子分母(缺了整条
        读数都算不出)= 致命;开盘价只被失效位判定用,而那一项本来就有自己的
        第三态(`no_open_price`)= 非致命。
        🔴 ⛔ 别"简化"回一档 —— 那会因为一个用不上的字段把好的价 / 量 / 额一起扔掉。"""
        no_open = _check(_q("600000.SH", open_=0.0))
        assert no_open.status == auction.QS_REQUIRED_FIELD_MISSING
        assert aq.ERR_OPEN_PRICE_MISSING in no_open.errors
        assert no_open.usable is True                    # 非致命:读数照用

        no_price = _check(Quote(code="600000.SH", name="x", price=0.0, pre_close=0.0, open=0.0,
                                high=0.0, low=0.0, volume=0.0, amount=0.0,
                                ts="2026-08-11 09:25:03", source="sina"))
        assert aq.ERR_REQUIRED_FIELD_MISSING in no_price.errors
        assert no_price.usable is False                  # 致命

    def test_check6_price_relation_must_be_internally_consistent(self):
        """⑥ 价格关系一致。⚠ **诚实边界**:两个免费源里只有腾讯自带涨跌幅字段,
        而 `Quote` 从来没有携带过它 —— 本项落在派生式所依赖的那组价格关系上。"""
        bad = Quote(code="600000.SH", name="x", price=99.0, pre_close=10.0, open=10.5,
                    high=11.0, low=10.0, volume=1.0, amount=1.0,
                    ts="2026-08-11 09:25:03", source="sina")
        c = _check(bad)
        assert c.status == auction.QS_MALFORMED
        assert aq.ERR_PRICE_RELATION in c.errors

    def test_check7_negative_volume_or_amount_is_malformed(self):
        """⑦ 单位转换后非异常负数。⚠ `0` 是合法的(竞价可以一手没成交)。"""
        assert aq.ERR_NEGATIVE_VOLUME in _check(_q("600000.SH", volume=-1.0)).errors
        assert aq.ERR_NEGATIVE_AMOUNT in _check(_q("600000.SH", amount=-1.0)).errors
        assert _check(_q("600000.SH", volume=0.0, amount=0.0)).ok

    def test_all_failed_checks_are_kept_not_just_the_first(self):
        """一条读数可以同时踩中好几项 —— `errors` 里**全都留着**,`status` 只是主因。"""
        c = _check(_q("600000.SH", ts="2026-08-10 09:24:00", volume=-3.0))
        assert set(c.errors) >= {aq.ERR_WRONG_TRADE_DATE, aq.ERR_BEFORE_FINAL_AUCTION,
                                 aq.ERR_NEGATIVE_VOLUME}

    def test_parse_quote_ts_keeps_the_raw_string_untouched(self):
        """🔴 §五 P2.1 逐字:「原始字符串继续保留」—— 派生字段另开,`Quote.ts` 一个字不改。"""
        q = _q("600000.SH", ts="2026-08-11 09:25:03")
        c = _check(q)
        assert q.ts == "2026-08-11 09:25:03"
        assert c.ts_raw == q.ts
        assert c.ts_parsed == "2026-08-11 09:25:03"
        assert aq.parse_quote_ts("不是时间") is None
        assert aq.parse_quote_ts("") is None


# ══════════════════════════════════════════════════════════════════════════
# 验收 1 / 2 / 3 —— 主备源与时间戳
# ══════════════════════════════════════════════════════════════════════════

class TestAcceptance1PrimaryStaleBackupFresh:
    """1. 新浪返昨日、腾讯返今日:**用腾讯**,记录主源过期。"""

    def test_backup_wins_and_source_degradation_is_recorded(self):
        d = _dual(_q("600000.SH", price=9.0, ts="2026-08-10 09:25:03"),
                  _q("600000.SH", price=11.0, ts="2026-08-11 09:25:03", source="tencent"))
        chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert chosen is not None and chosen.source == "tencent" and chosen.price == 11.0
        assert qq.freshness == QF_FRESH
        assert qq.source_degraded is True          # 🔴 K8:「记录来源降级」,⛔ 不静默换源
        assert qq.chosen_role == auction.QUOTE_ROLE_BACKUP

    def test_both_source_readings_are_kept_not_only_the_winner(self):
        """🔴 K8 §二十 逐字:「两个来源的原始读数**全部留存**」。"""
        d = _dual(_q("600000.SH", price=9.0, ts="2026-08-10 09:25:03"),
                  _q("600000.SH", price=11.0, ts="2026-08-11 09:25:03", source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert {c.role for c in qq.checks} == {"primary", "backup"}
        assert {c.price for c in qq.checks} == {9.0, 11.0}
        assert aq.ERR_WRONG_TRADE_DATE in qq.errors     # 备源赢了,主源的问题照样留痕


class TestAcceptance2BothSourcesStale:
    """2. 两源都返昨日:critical `insufficient`。"""

    def test_both_stale_makes_the_code_insufficient(self):
        d = _dual(_q("600000.SH", ts="2026-08-10 09:25:03"),
                  _q("600000.SH", ts="2026-08-10 09:25:03", source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert qq.freshness == QF_INSUFFICIENT
        assert qq.usable is False

    def test_critical_domain_goes_insufficient_end_to_end(self, isolated_env):
        _world(isolated_env)
        stale = _q("600000.SH", ts="2026-08-10 09:25:03")
        duals = {"600000.SH": _dual(stale, _q("600000.SH", ts="2026-08-10 09:25:03",
                                              source="tencent"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        b = mech.baskets[0]
        # ⚠ **逐票**这一格是 `insufficient`;**样本域**里还有一支能用的市场指数
        #    → 域级读数是 `degraded`(`quality_of` 的既定判据:一条可用读数都没有
        #    才判 insufficient)。两者是不同层级,⛔ 别当成对不上。
        assert snap.quote_quality["600000.SH"].freshness == QF_INSUFFICIENT
        assert b.critical_quality == DQ_DEGRADED
        assert b.members[0].data_quality == DQ_INSUFFICIENT   # 逐票行:「中性｜数据不足」
        # 🔴 那份不合格的读数**没有被悄悄扔掉**:它在逐票账里逐字留着。
        assert snap.quote_quality["600000.SH"].checks
        assert "600000.SH" in snap.invalid and "600000.SH" not in snap.missing

    def test_whole_critical_domain_stale_is_insufficient(self, isolated_env):
        """关键域里**一条可用读数都没有**(成员与它的市场基准都返昨日)→ `insufficient`。"""
        _world(isolated_env)
        stale = "2026-08-10 09:25:03"
        duals = {"600000.SH": _dual(_q("600000.SH", ts=stale))}
        duals.update({c: _dual(_q(c, price=10.1, ts=stale)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        assert mech.baskets[0].critical_quality == DQ_INSUFFICIENT

    def test_stale_reading_never_becomes_todays_gap_pct(self, isolated_env):
        """🔴 **本版第 ① 个病的正面钉死**:昨天的收盘价⛔ 不许被派生成今天的竞价涨跌幅。"""
        _world(isolated_env)
        duals = {"600000.SH": _dual(_q("600000.SH", price=13.0, ts="2026-08-10 09:25:03"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        assert snap.gap_of("600000.SH") is None
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        r = mech.baskets[0].members[0]
        assert r.gap_pct is None and r.auction_price is None
        # 「没抓到」与「抓到了一份不能用的」**两个不同的原因码**(⛔ 不折平)
        assert r.hit_invalidation_undetermined_reason == auction.UNDET_QUOTE_INVALID


class TestAcceptance3UnparseableTimestamp:
    """3. 时间戳无法解析:**不得判 `ok`**。"""

    def test_unparseable_timestamp_is_never_ok(self, isolated_env):
        assert _check(_q("600000.SH", ts="")).status == auction.QS_TIMESTAMP_UNPARSEABLE
        assert _check(_q("600000.SH", ts="20260811092503")).status == \
            auction.QS_TIMESTAMP_UNPARSEABLE
        _world(isolated_env)
        duals = {"600000.SH": _dual(_q("600000.SH", ts="没有时间"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        assert mech.baskets[0].critical_quality != DQ_OK


# ══════════════════════════════════════════════════════════════════════════
# 验收 4 / 5 —— 🔴 分域的**正反两例**(P2.3 的全部意义所在)
# ══════════════════════════════════════════════════════════════════════════

class TestAcceptance4And5DomainSplit:
    """4. 成员数据完整、**无关**指数缺失 → critical `ok` + context `degraded`,**不夹中性**。
    5. 成员**对应**指数缺失 → critical `degraded`,confirm/veto **被夹中性**。

    这两条合起来就是 V2.4.0 要修的第 ② 个病:域太宽,什么都算「关键」。
    """

    def _mech(self, env, *, drop: tuple = ()):
        _world(env)                                     # 600000.SH 主板沪 → 市场基准 000001.SH
        duals = {"600000.SH": _dual(_q("600000.SH"))}
        for c in IDX:
            if c in drop:
                continue
            duals[c] = _dual(_q(c, price=10.1))
        snap = _snap(env, duals=duals)
        return am.build_mech(snap, db_path=env.db_path, parquet_dir=env.parquet_dir)

    def test_case4_unrelated_index_missing_does_not_clamp(self, isolated_env):
        # 丢掉创业板指 —— 这只沪市主板票**根本用不到它**
        mech = self._mech(isolated_env, drop=("399006.SZ",))
        b = mech.baskets[0]
        assert b.critical_quality == DQ_OK, "无关指数缺失⛔ 不许把关键域拉下水"
        assert b.context_quality == DQ_DEGRADED
        v, by = al.clamp_verdict(
            al.BasketFields(basket_key=b.basket_key, verdict=VERDICT_CONFIRM), b)
        assert (v, by) == (VERDICT_CONFIRM, None), "上下文域降级⛔ 不许夹逼结论"

    def test_case5_the_members_own_index_missing_does_clamp(self, isolated_env):
        # 丢掉上证综指 —— 这**正是**这只票实际使用的市场基准
        mech = self._mech(isolated_env, drop=("000001.SH",))
        b = mech.baskets[0]
        assert b.critical_quality == DQ_DEGRADED
        v, by = al.clamp_verdict(
            al.BasketFields(basket_key=b.basket_key, verdict=VERDICT_CONFIRM), b)
        assert (v, by) == (VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)

    def test_the_domain_detail_names_what_is_missing(self, isolated_env):
        """🔴 只报一个三态不够 —— **缺了什么必须点名**,否则模型与用户都判断不出
        「这次缺的到底重不重要」,而那恰恰是拆域的全部意义。"""
        mech = self._mech(isolated_env, drop=("000001.SH",))
        d = mech.baskets[0].quality_detail
        assert "000001.SH" in d["critical"]["missing"]
        assert "000001.SH" in d["critical"]["components"][auction.CRIT_MARKET_BENCHMARK]

    def test_insufficient_sector_peers_do_not_enter_the_critical_domain(self, isolated_env):
        """🔴 **对照不足 ⛔ 不进关键域**(P1-78:关注池缩编后「对照不足」近乎必然)——
        「压根没用上任何板块基准」与「用到的那个缺了」是两件事。"""
        mech = self._mech(isolated_env)
        b = mech.baskets[0]
        assert b.members[0].rel_to_sector_source == auction.SECTOR_BENCH_UNAVAILABLE
        assert b.quality_detail["critical"]["components"][auction.CRIT_SECTOR_BENCHMARK] == []
        assert b.critical_quality == DQ_OK

    def test_frozen_anchor_is_part_of_the_critical_domain(self, isolated_env):
        """④ K8 §二十 逐字:「D0 失效判断所需的**冻结锚**」也在关键域里。
        ⚠ 这是相对 v2.3.3 **新增**的夹逼触发面(有篮无卡 / 卡上没冻结失效位),
        刻意保守 —— 失效判断做不了就不该给高置信结论。"""
        insert_stock_basic(isolated_env, [{"ts_code": "600000.SH", "name": "x", "market": "主板"}])
        # ⚠ 空卡 = 有篮无卡(合法中间态)→ 拿不到冻结失效位
        _seed_basket(isolated_env, ["600000.SH"], key="k1", card={},
                     engine_code="C", engine_version="C1")
        duals = {"600000.SH": _dual(_q("600000.SH"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        b = mech.baskets[0]
        anchors = b.quality_detail["critical"]["components"][auction.CRIT_FROZEN_ANCHOR]
        assert [a["ts_code"] for a in anchors] == ["600000.SH"]
        assert b.critical_quality == DQ_DEGRADED


# ══════════════════════════════════════════════════════════════════════════
# 验收 6 —— 跨源结论性冲突
# ══════════════════════════════════════════════════════════════════════════

class TestAcceptance6SourceConflict:
    """6. 双源一边触发失效位、一边不触发:`conflict`,**不能高置信输出**。"""

    def test_invalidation_disagreement_is_a_conflict(self):
        script = MemberScript(ts_code="600000.SH", basket_key="k1", ref_close=10.0, stop_line=9.5)
        hit = _q("600000.SH", price=9.0, open_=9.0)              # 开在失效位下方
        miss = _q("600000.SH", price=10.5, open_=10.5, source="tencent")
        assert aq.detect_conflict(
            hit, miss,
            invalidation_of=lambda q: am.hit_invalidation_tristate(script, q)[0],
        ) == CONFLICT_INVALIDATION_DISAGREE

    def test_one_side_undetermined_is_not_a_conflict(self):
        """🔴 三态不许折平:「一边判不了」⛔ 不是「两边看法不同」
        (`CLAUDE.md`「三态字段:`is not None` 会把它们折平」的同一个坑)。"""
        script = MemberScript(ts_code="600000.SH", basket_key="k1", ref_close=10.0, stop_line=9.5)
        # ⚠ 两边**同方向**(都在跌),把「方向相反」那一类排除掉,单独看失效位这一类。
        hit = _q("600000.SH", price=9.0, open_=9.0)          # 开在失效位下方 → True
        no_open = _q("600000.SH", price=9.6, open_=0.0, source="tencent")  # 开盘价没发 → None
        assert am.hit_invalidation_tristate(script, hit)[0] is True
        assert am.hit_invalidation_tristate(script, no_open)[0] is None
        assert aq.detect_conflict(
            hit, no_open,
            invalidation_of=lambda q: am.hit_invalidation_tristate(script, q)[0],
        ) is None

    def test_direction_opposite_and_identity_mismatch(self):
        up = _q("600000.SH", price=11.0, pre_close=10.0)
        down = _q("600000.SH", price=9.0, pre_close=10.0, source="tencent")
        assert aq.detect_conflict(up, down) == auction.CONFLICT_DIRECTION_OPPOSITE
        # 一边平开一边涨 **不算** 方向相反(那是幅度差,不是结论差)
        flat = _q("600000.SH", price=10.0, pre_close=10.0, source="tencent")
        assert aq.detect_conflict(up, flat) is None
        other = _q("600519.SH", price=11.0, pre_close=99.0, source="tencent")
        assert aq.detect_conflict(up, other) == auction.CONFLICT_IDENTITY_MISMATCH

    def test_conflict_clamps_the_verdict_to_neutral(self, isolated_env):
        """`conflict` → 关键域至少 `degraded` → 闸 1 夹成中性(⛔ 不能高置信输出)。"""
        _world(isolated_env)
        duals = {"600000.SH": _dual(_q("600000.SH", price=11.0, pre_close=10.0),
                                    _q("600000.SH", price=9.0, pre_close=10.0,
                                       source="tencent"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        assert snap.quote_quality["600000.SH"].freshness == QF_CONFLICT
        assert "600000.SH" in snap.conflicts
        mech = am.build_mech(snap, db_path=isolated_env.db_path,
                             parquet_dir=isolated_env.parquet_dir)
        b = mech.baskets[0]
        assert b.critical_quality != DQ_OK
        v, by = al.clamp_verdict(
            al.BasketFields(basket_key=b.basket_key, verdict=VERDICT_CONFIRM), b)
        assert (v, by) == (VERDICT_NEUTRAL, CLAMPED_BY_DATA_QUALITY)
        assert any(r["kind"] == auction.RISK_SOURCE_CONFLICT for r in mech.market.risks)

    def test_single_source_is_never_reported_as_no_conflict(self):
        """🔴 只有一源 = 没有第二个读数可以打架 —— ⛔ 那不是「已核对无冲突」。"""
        assert aq.detect_conflict(_q("600000.SH"), None) is None
        _chosen, qq = aq.resolve_dual("600000.SH", _dual(_q("600000.SH")),
                                      trade_date=D1, captured_at=NOW)
        assert qq.conflict is None and len(qq.checks) == 1


# ══════════════════════════════════════════════════════════════════════════
# 验收 9 —— 竞价 LLM 缺席:机械数据与失效警报仍正常落库
# ══════════════════════════════════════════════════════════════════════════

class TestAcceptance9MechanicalLandsWithoutLLM:
    def test_no_provider_still_writes_mechanical_rows_and_new_columns(self, isolated_env):
        _world(isolated_env)
        # 开在冻结失效位下方 → 命中 D0 明确失效位(独立警报通道)
        duals = {"600000.SH": _dual(_q("600000.SH", price=9.0, open_=9.0))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        res = ap.run_auction_pipeline(
            NOW, db_path=isolated_env.db_path, parquet_dir=isolated_env.parquet_dir,
            dual_quotes_fn=lambda cs: {c: duals.get(c, DualQuote(code=c)) for c in cs},
            now_fn=lambda: NOW, deadline=datetime(2026, 8, 11, 9, 29),
            provider=None, provider_factory=lambda: None,
        )
        assert res.ran and res.llm_stage == auction.LLM_NO_PROVIDER
        assert res.hit_invalidation_codes == ["600000.SH"]
        report = astore.load_report(D1, db_path=isolated_env.db_path)
        assert report is not None
        # 🔴 P2.4 的四个新列在**第一次机械落库**时就写进去了
        assert isinstance(report["quote_quality_json"], dict)
        assert report["quote_quality_json"]["600000.SH"]["freshness"] == QF_FRESH
        vr = astore.load_verdicts(D1, db_path=isolated_env.db_path)[0]
        assert vr["critical_data_quality"] in (DQ_OK, DQ_DEGRADED, DQ_INSUFFICIENT)
        assert vr["context_data_quality"] is not None
        assert vr["quality_detail_json"]["critical"]["codes"]
        assert vr["verdict"] == VERDICT_PENDING_EXPLANATION
        assert vr["hit_invalidation_json"] == ["600000.SH"]


# ══════════════════════════════════════════════════════════════════════════
# 验收 10 —— v2.3.3 历史竞价记录可正常解码
# ══════════════════════════════════════════════════════════════════════════

class TestAcceptance10OldRowsStillDecode:
    def test_v233_shaped_rows_read_back_with_nulls_and_never_default_to_ok(self, isolated_env):
        """🔴 老行的四个新列是 NULL = 「**旧版本未细分**」——
        ⛔ 不得默认成正常(施工图 §五 P2.3 逐字)。"""
        from neckline.db import init_schema

        init_schema(isolated_env.db_path)
        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            # 逐字模拟 v2.3.3 的写法:**不写**四个新列
            conn.execute(
                "INSERT INTO auction_reports (trade_date, d0_date, source, captured_at, "
                "requested_codes, fetched_codes, missing_codes_json, conflict_codes_json, "
                "data_quality, index_gaps_json, market_anchors_json, risks_json, "
                "manual_note_attached, llm_stage, baskets_covered, notes_json, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("20260811", "20260810", "sina", "2026-08-11T09:26:30", 4, 4, "[]", "[]",
                 "ok", "{}", "[]", "[]", 0, "ok", 1, "[]", "t", "t"),
            )
            conn.execute(
                "INSERT INTO auction_verdicts (basket_id, trade_date, d0_date, basket_key, "
                "name, covered_tier, engine_code, engine_version, skeleton_version, "
                "regime_at_d0, data_quality, members_json, sector_sync_json, "
                "rel_strength_json, history_json, hit_invalidation_json, "
                "plan_consistency_json, verdict, reasons_json, llm_fields_json, "
                "manual_note_attached, llm_stage, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (1, "20260811", "20260810", "k1", "篮一", 1, "C", "C1", "K8-V0.7",
                 None, "ok", "[]", "{}", "{}", "{}", "[]", "{}", "confirm", "[]", "{}",
                 0, "ok", "t", "t"),
            )
            conn.commit()
        finally:
            conn.close()

        report = astore.load_report("20260811", db_path=isolated_env.db_path)
        assert report is not None and report["quote_quality_json"] is None
        assert not report.get("_corrupt_columns")        # NULL ⛔ 不是「读不出」
        vr = astore.load_verdicts("20260811", db_path=isolated_env.db_path)[0]
        assert vr["critical_data_quality"] is None
        assert vr["context_data_quality"] is None
        assert vr["quality_detail_json"] is None

    def test_old_member_json_without_the_new_keys_still_shapes(self):
        """老 `members_json`(V2.4.0 之前冻的)没有 7 个新键 → 空串 / `None`,
        ⛔ 客户端据此说「本次未记录」,**不许渲染成「校验通过」**。"""
        from neckline.api.app import _shape_auction_member

        row = _shape_auction_member({"ts_code": "600000.SH", "data_quality": "ok"})
        assert row.quoteFreshness == "" and row.quoteStatus == ""
        assert row.quoteSource is None and row.quoteTimestamp is None
        assert row.sourceDegraded is False and row.sourceConflict is None
        assert row.validationErrors == []


# ══════════════════════════════════════════════════════════════════════════
# 验收 7 / 8 —— P2.5「正式空结果」与「系统缺席」严格分开
# ══════════════════════════════════════════════════════════════════════════

def _seed_stage(env, *, reason_stage: str, search_stage: str = "ok", notes=(),
                baskets=(), seed_count=None, seed_summary=""):
    from types import SimpleNamespace

    from neckline.selection.basket_stage_handoff import save_stage_handoff

    save_stage_handoff(D0, SimpleNamespace(
        search_stage=search_stage, reason_stage=reason_stage, baskets=list(baskets),
        notes=list(notes), seed_count=seed_count, seed_summary=seed_summary,
    ), db_path=env.db_path)


class TestAcceptance7And8SelectionStage:
    """7. LLM provider 缺席:报告显示「**未解释**」,**不是「零篮子」**。
    8. LLM 成功返回空数组:合法显示零篮子。"""

    def test_case7_provider_absent_says_unexplained_and_keeps_seed_summary(self, isolated_env):
        from neckline.report import basket_daily as bd

        _seed_stage(isolated_env, reason_stage="no_provider", search_stage="no_provider",
                    seed_count=7, seed_summary="热点行业 3 · 异动概念 2 · 涨停簇 1 · 异动簇 1")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is False
        assert "选股解释未完成" in (out.baskets_unavailable_reason or "")
        assert "今天没有机会" in (out.baskets_unavailable_reason or "")   # 明确否掉这句话
        assert out.selection_stage == "no_provider"
        assert out.selection_unavailable_reason == "no_provider"
        assert out.unexplained_seed_count == 7
        assert "热点行业 3" in (out.unexplained_seed_summary or "")
        pub = out.to_public_dict()
        assert pub["selectionStage"] == "no_provider"
        assert pub["unexplainedSeedCount"] == 7

    def test_case8_legal_empty_result_is_not_dressed_as_absence(self, isolated_env):
        from neckline.report import basket_daily as bd

        _seed_stage(isolated_env, reason_stage="ok", search_stage="ok", seed_count=5)
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets_available is True          # 跑过了、模型明说没有 → **合法空结果**
        assert out.baskets_unavailable_reason is None
        assert out.selection_stage == "ok"
        assert out.selection_unavailable_reason is None
        # 🔴 引擎跑过的日子⛔ 不下发 seed 留痕(否则会把用户往"其实还是有机会"上引)
        assert out.unexplained_seed_count is None

    def test_seed_count_none_is_never_dressed_up_as_zero(self, isolated_env):
        """⚠ 老行 / ⑤ 早返回 → `None` = **当时没记这一位**,
        ⛔ 不拿 `0` 冒充「一个种子都没有」。"""
        from neckline.report import basket_daily as bd

        _seed_stage(isolated_env, reason_stage="parse_failed")
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.selection_unavailable_reason == "parse_failed"
        assert out.unexplained_seed_count is None
        assert out.unexplained_seed_summary is None

    def test_unexplained_seeds_are_not_a_fourth_candidate_state(self, isolated_env):
        """🔴 K8 §十 末句:「未解释候选**不是第四种候选状态**」——
        ⛔ 不冒充 T2 / OUT、⛔ 不进定档流程。"""
        from neckline.report import basket_daily as bd

        _seed_stage(isolated_env, reason_stage="no_provider", seed_count=9)
        out = bd.build_basket_daily(D0, db_path=isolated_env.db_path, with_exec_hints=False)
        assert out.baskets == []
        assert out.out_candidates == []
        assert out.dropped == []
        pub = out.to_public_dict()
        assert pub["baskets"] == [] and pub["outCandidates"] == []


# ══════════════════════════════════════════════════════════════════════════
# 🔴 三组守门(靠自觉就会失守的那类)
# ══════════════════════════════════════════════════════════════════════════

class TestGuards:
    def test_zero_tolerance_no_second_level_slack_anywhere_in_the_auction_layer(self):
        """🔴 **用户裁定 #2**(2026-08-12):「竞价时间戳先执行**零容差**……
        若实盘出现误判,再由我确认容差秒数,**施工 Agent 不得自行设定**。」

        判据 = 时间比较必须是**裸的严格比较**,⛔ 全层不许出现任何"给几秒富余"的
        常量或表达式。⚠ 本条**只剥 `#` 行注释,不剥 docstring** —— 裁定原文就写在
        docstring 里,那是留给下一个人的说明,把它算成违规 = 逼人删掉自己要说的话。
        """
        src = (_REPO / "neckline" / "auction" / "quality.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 只看**真代码**(剥 docstring 常量)——注释与说明里出现 "1s/3s/5s" 是允许的
        body_src: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Compare, ast.BinOp, ast.Assign)):
                body_src.append(ast.dump(node))
        joined = "\n".join(body_src)
        for banned in ("timedelta(seconds", "TOLERANCE", "SLACK_SEC", "GRACE_SEC"):
            assert banned not in joined, f"竞价时间判据里出现了容差:{banned}"
        # 正面钉死:落点就是 `src > captured_at` 这一条裸比较
        assert "if src > captured_at:" in src

    def test_zero_tolerance_cannot_be_smuggled_in_through_the_call_site(self):
        """🔵 **复审 🔵-3:守门此前只扫 `quality.py` 一个文件、只禁四个字面名** ——
        `collect.py` 往 `captured_at` 上**加几秒**再传进去,同样是给容差,而守门全绿。
        裁定 #2 原话是「施工 Agent **不得自行设定**」容差秒数,那就得覆盖**调用点**。

        判据两条:① 全 `neckline/auction/**` 的**真代码**里不许出现
        `timedelta(seconds=` / `total_seconds() >` 这类"给富余"的表达式;
        ② `captured_at` **传进 `validate_quote` / `resolve_dual` 时必须是裸名字**,
        ⛔ 不许是任何算术表达式。"""
        import ast as _ast

        auction_dir = _REPO / "neckline" / "auction"
        for path in sorted(auction_dir.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            tree = _ast.parse(src)
            code_only = "\n".join(
                _ast.dump(n) for n in _ast.walk(tree)
                if isinstance(n, (_ast.Compare, _ast.BinOp, _ast.Assign, _ast.Call))
            )
            for banned in ("timedelta(seconds", "TOLERANCE", "SLACK_SEC", "GRACE_SEC"):
                assert banned not in code_only, \
                    f"{path.name} 的真代码里出现了容差:{banned}"
            # ② 调用点:传给**做时间比较的那两个函数**的 `captured_at=` 必须是裸名字
            #    或裸属性。⚠ 只盯这两个入口 —— `MarketMech(captured_at=…isoformat())`
            #    那种是**格式化落库**,与判据无关,一把梭会把它一起判违规。
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, _ast.Attribute) else getattr(fn, "id", "")
                if name not in ("validate_quote", "resolve_dual"):
                    continue
                for kw in node.keywords:
                    if kw.arg != "captured_at":
                        continue
                    assert isinstance(kw.value, (_ast.Name, _ast.Attribute)), (
                        f"{path.name}:{node.lineno} 给 `{name}(captured_at=)` 传了一个表达式 "
                        f"({_ast.dump(kw.value)[:80]}) —— 那是在调用点偷偷加容差,"
                        f"用户裁定 #2 明令「施工 Agent 不得自行设定」。")

    def test_get_quotes_behaviour_is_untouched_by_the_dual_source_addition(self):
        """🔴 §五 P2.2:「普通上下文股票**仍走**『主源失败才降备源』(现役
        `get_quotes` 行为,**一字不动**)」。P2.2 是**新增**一条路径,⛔ 不是改写老的。"""
        from neckline.sentinel import quotes as qs

        src = Path(qs.__file__).read_text(encoding="utf-8")
        body = src.split("def get_quotes(")[1].split("def get_quote(")[0]
        # 老路径的两个特征:先拉 sina;只对 **missing** 的那批降备源
        assert "_fetch_sina(batch, transport)" in body
        assert "missing = [s for s in batch if sym_to_code[s] not in result]" in body
        assert "_fetch_tencent(missing, transport)" in body
        # 新路径**两源都拉**(⛔ 别把它写成"只在缺票时拉备源")
        dual = src.split("def get_quotes_dual(")[1]
        assert "_fetch_sina(batch, transport)" in dual and "_fetch_tencent(batch, transport)" in dual

    def test_the_dual_path_never_requests_quotes_one_code_at_a_time(self):
        """🔴 §五 P2.2:「**双源批量并行**,⛔ 不允许逐票网络请求」
        (9:26 那一刻的限流面必须可控)。判据 = 仍走既有 `_CHUNK_SIZE` 分块。"""
        from neckline.sentinel import quotes as qs

        dual = Path(qs.__file__).read_text(encoding="utf-8").split("def get_quotes_dual(")[1]
        assert "_chunks(symbols, _CHUNK_SIZE)" in dual
        assert "get_quote(" not in dual

    def test_the_four_new_columns_are_mechanical_only(self):
        """🔴 §3.14-E:P2.4 的四个新列**全部是机械冻结列**,
        ⛔ 一个都不许进 `LLM_UPDATABLE_*_COLUMNS`。"""
        new_cols = {"quote_quality_json", "critical_data_quality",
                    "context_data_quality", "quality_detail_json"}
        whitelist = set(astore.LLM_UPDATABLE_REPORT_COLUMNS) | set(
            astore.LLM_UPDATABLE_VERDICT_COLUMNS)
        assert not (new_cols & whitelist)
        assert new_cols <= (set(astore._REPORT_COLUMNS) | set(astore._VERDICT_COLUMNS))

    def test_gate1_is_the_only_gate_whose_source_changed(self):
        """🔴 §五 P2.3:「闸 2(Z 线同向强势 ≤1)/ 闸 3(Y 线否决条件)**一字不动**」。
        判据 = 闸 2 仍按**线码**判(⛔ 别改回枚举版本号),闸 3 仍要求三个布尔全 `True`。"""
        src = Path(al.__file__).read_text(encoding="utf-8")
        gate = src.split("def clamp_verdict(")[1]
        assert 'getattr(mech, "critical_quality", None) != DQ_OK' in gate
        assert 'line == "Z"' in gate and 'line == "Y"' in gate
        assert '== "Z1"' not in gate and '== "Y1"' not in gate
        assert "fields.driver_negative is True" in gate

    def test_domain_quality_and_snapshot_quality_agree(self, isolated_env):
        """`quality.domain_quality` 与 `AuctionSnapshot.quality_of` 是**同一套判据的
        两个入口** —— 两处必须逐位一致(⛔ 否则就是两份事实源)。"""
        _world(isolated_env)
        duals = {"600000.SH": _dual(_q("600000.SH"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        snap = _snap(isolated_env, duals=duals)
        for domain in ([], ["600000.SH"], list(IDX), ["600000.SH", "999999.SZ"]):
            assert snap.quality_of(domain) == aq.domain_quality(
                domain, dict(snap.quote_quality), in_window=snap.captured_in_window)

    def test_quote_freshness_labels_are_all_translated_client_side(self):
        """新加的四族枚举码**会直接进契约** —— 少一个就会在界面上印出机器标识符
        (`CLAUDE.md` 连踩三次的那类 bug)。"""
        from tests.client_sources import models_text
        models = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读

        def cases(fn: str) -> set:
            body = models.split(f"func {fn}(")[1].split("\n}")[0]
            return set(re.findall(r'case "([^"]*)":', body))

        assert set(auction.QUOTE_FRESHNESS_CODES) <= cases("nkAuctionQuoteFreshnessLabel")
        assert set(auction.CONFLICT_CODES) <= cases("nkAuctionConflictLabel")
        assert set(aq.VALIDATION_ERROR_CODES) <= cases("nkAuctionValidationErrorLabel")

    def test_old_reports_never_default_to_normal_on_the_client(self):
        """🔴 施工图 §五 P2.3 逐字:「旧报告没有这些字段时显示『旧版本未细分』,
        ⛔ **不得默认成正常**」。判据落在展示层换算与色调两处。"""
        from tests.client_sources import models_text
        models = models_text()   # V2.4.0 P3.7:DTO 已拆六份,统一入口读
        fn = models.split("func nkAuctionDomainQualityLabel(")[1].split("\n}")[0]
        assert "旧版本未细分" in fn
        view = (_REPO / "client" / "Neckline" / "Views" / "AuctionCardView.swift").read_text(
            encoding="utf-8")
        tone = view.split("func nkAuctionDomainTone(")[1].split("\n}")[0]
        assert "return .warn" in tone and ".neutral" not in tone.split("guard")[1].split("\n")[0]

    def test_selection_stage_verdict_has_exactly_one_implementation(self):
        """🔴 §五 P2.5:「判读逻辑的唯一实现仍是
        `selection/basket_stage_handoff.py::stage_verdict`,⛔ 不许在 `report/` 或
        `api/` 再推一遍」。"""
        offenders = []
        for pkg in ("report", "api"):
            for p in sorted((_REPO / "neckline" / pkg).rglob("*.py")):
                text = p.read_text(encoding="utf-8")
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    # 「再推一遍」= 自己按 reason_stage 判 engine_ran
                    if isinstance(node, ast.Compare) and any(
                            isinstance(c, ast.Constant) and c.value in ("no_provider",
                                                                        "budget_exhausted",
                                                                        "parse_failed")
                            for c in node.comparators):
                        offenders.append((str(p.relative_to(_REPO)), node.lineno))
        assert offenders == [], f"段状态判读被抄了第二份:{offenders}"


# ══════════════════════════════════════════════════════════════════════════
# 🔴 迁移专项(P2.4:**仅可空增量列**,幂等,老行读回不炸)
# ══════════════════════════════════════════════════════════════════════════

class TestMigration:
    _NEW = {
        "auction_reports": ["quote_quality_json"],
        "auction_verdicts": ["critical_data_quality", "context_data_quality",
                             "quality_detail_json"],
        "basket_stage_handoff": ["seed_count", "seed_summary"],
    }

    def _cols(self, db: Path, table: str) -> Dict[str, Any]:
        conn = sqlite3.connect(str(db))
        try:
            return {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        finally:
            conn.close()

    def test_every_new_column_is_nullable_and_has_no_default(self, isolated_env):
        """🔴 **仅可空增量列**,且**不给 DEFAULT**:老行读回来必须是 NULL =
        「这一版还没有这个概念」,⛔ 不是「正常」/「一个种子都没有」。"""
        from neckline.db import init_schema

        init_schema(isolated_env.db_path)
        for table, cols in self._NEW.items():
            info = self._cols(isolated_env.db_path, table)
            for c in cols:
                assert c in info, f"{table}.{c} 没建出来"
                _cid, _name, _type, notnull, dflt, _pk = info[c]
                assert notnull == 0, f"{table}.{c} 不该是 NOT NULL"
                assert dflt is None, f"{table}.{c} 不该有 DEFAULT(NULL 才表达『没记』)"

    def test_migration_is_idempotent(self, isolated_env):
        """幂等:连跑三次 `init_schema`,列集合逐位不变、`integrity_check` 仍 ok。"""
        from neckline.db import init_schema

        init_schema(isolated_env.db_path)
        before = {t: sorted(self._cols(isolated_env.db_path, t)) for t in self._NEW}
        init_schema(isolated_env.db_path)
        init_schema(isolated_env.db_path)
        after = {t: sorted(self._cols(isolated_env.db_path, t)) for t in self._NEW}
        assert before == after
        conn = sqlite3.connect(str(isolated_env.db_path))
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()

    def test_the_migration_only_adds_columns(self):
        """🔴 P2.4 红线:**仅可空增量列** —— ⛔ 不改既有列语义、⛔ 不删列、
        ⛔ 不 DROP 表、⛔ 不回填历史行。判据 = 本版新增的迁移条目全部是
        `_COLUMN_MIGRATIONS`(纯 `ALTER TABLE ADD COLUMN`),且没有任何回填语句。"""
        from neckline import db as dbmod

        src = Path(dbmod.__file__).read_text(encoding="utf-8")
        registered = {(t, c) for t, c, _ddl in dbmod._COLUMN_MIGRATIONS}
        for table, cols in self._NEW.items():
            for c in cols:
                assert (table, c) in registered, f"{table}.{c} 没登记进 _COLUMN_MIGRATIONS"
        for banned in ("DROP TABLE auction", "DROP COLUMN",
                       "UPDATE auction_reports SET quote_quality_json",
                       "UPDATE auction_verdicts SET critical_data_quality"):
            assert banned not in src, f"迁移里出现了破坏性动作:{banned}"


# ══════════════════════════════════════════════════════════════════════════
# 端点侧:七个新字段真的到得了客户端
# ══════════════════════════════════════════════════════════════════════════

class TestContractReachesTheClient:
    def test_auction_endpoint_carries_the_domain_split(self, client, AUTH, api_env):
        env = api_env
        _world(env)
        duals = {"600000.SH": _dual(_q("600000.SH"))}
        duals.update({c: _dual(_q(c, price=10.1)) for c in IDX})
        ap.run_auction_pipeline(
            NOW, db_path=env.db_path, parquet_dir=env.parquet_dir,
            dual_quotes_fn=lambda cs: {c: duals.get(c, DualQuote(code=c)) for c in cs},
            now_fn=lambda: NOW, deadline=datetime(2026, 8, 11, 9, 29),
            provider=None, provider_factory=lambda: None,
        )
        r = client.get("/api/v1/auction?date=20260811", headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        ds = body["dataStatus"]
        assert ds["criticalDataQuality"] in ("ok", "degraded", "insufficient")
        assert ds["contextDataQuality"] is not None
        assert isinstance(ds["qualityDetails"], list)
        assert isinstance(ds["invalidCodes"], list)
        b = body["baskets"][0]
        assert b["criticalDataQuality"] is not None
        assert b["qualityDetail"]["critical"]["codes"]
        m = b["members"][0]
        assert m["quoteFreshness"] == "fresh"
        assert m["quoteSource"] == "sina"
        assert m["quoteTimestamp"] == "2026-08-11 09:25:03"

    def test_old_report_shows_the_domain_split_as_unknown_not_as_ok(self, client, AUTH, api_env):
        """🔴 老报告 → 两个分域字段 `null` → 客户端说「旧版本未细分」。
        ⛔ 服务端**不许**在这里补一个 `ok`。"""
        env = api_env
        from neckline.db import init_schema

        init_schema(env.db_path)
        conn = sqlite3.connect(str(env.db_path))
        try:
            conn.execute(
                "INSERT INTO auction_reports (trade_date, d0_date, source, captured_at, "
                "requested_codes, fetched_codes, missing_codes_json, conflict_codes_json, "
                "data_quality, index_gaps_json, market_anchors_json, risks_json, "
                "manual_note_attached, llm_stage, baskets_covered, notes_json, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("20260811", "20260810", "sina", "2026-08-11T09:26:30", 4, 4, "[]", "[]",
                 "ok", "{}", "[]", "[]", 0, "ok", 0, "[]", "t", "t"),
            )
            conn.commit()
        finally:
            conn.close()
        body = client.get("/api/v1/auction?date=20260811", headers=AUTH).json()
        assert body["dataStatus"]["criticalDataQuality"] is None
        assert body["dataStatus"]["contextDataQuality"] is None
        assert body["dataStatus"]["qualityDetails"] == []
