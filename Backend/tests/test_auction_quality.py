"""竞价读数的**七项校验**与**有界双源核验**(`neckline/auction/quality.py`)。

🔴 **本文件是取回件,⛔ 不是新写的**:V2.4.0 的验收套件
`tests/test_v240_p2_auction.py`(905 行 / 10 条编号用例)随 K8 auction 包一并删除,
而 S8 把 `quality.py` 本身取回来了、**测试没有取回**。复审 R2-01 点名这件事:
613 行的读数校验层**零测试覆盖**,于是 R2-02 那处「两条判据自相矛盾」一直没人发现。

取的是**与 K9 仍然相干**的那一半(原件 `git show eaca2d1^:Backend/tests/
test_v240_p2_auction.py`):

| 原编号 | 内容 | 这里 |
|---|---|---|
| P2.1 七项 | 逐项各一例 + 时间三项零容差 | `TestSevenChecks` |
| 验收 1 | 新浪返昨日、腾讯返今日 → 用腾讯 + 记来源降级 | `TestPrimaryStaleBackupFresh` |
| 验收 2 | 两源都返昨日 → `insufficient` | `TestBothSourcesStale` |
| 验收 3 | 时间戳解不出 → **不得判 `ok`** | `TestUnparseableTimestamp` |
| 验收 6 | **双源一边触发失效位、一边不触发 → `conflict`** | `TestSourceConflict` |
| 守门 | 🔴 零容差(用户裁定 #2)⛔ 全层不许出现秒级容差常量 | `TestZeroToleranceGuards` |

⛔ **没有整份照搬 K8 语义**:原件里 `MemberScript` / 篮子 / `clamp_verdict` /
`VERDICT_*` / 分域(验收 4/5)/ LLM 降级(验收 7/8/9/10)那几组的钩子在 K9 里
根本不存在 —— 验收 6 的钩子从 K8 的**失效位**换成 K9 的 **`rejection_of`**
(「两源分别代入同一份 D0 冻结预案,**放弃**分支的结论一不一致」)。

⚠ 本层的身份:回答**「这条读数能不能当作今天 9:25 那一刻的集合竞价结果」**。
⛔ 它不回答任何市场问题。纯函数:零 IO / 零 DB / 零 LLM。
"""

from __future__ import annotations

import ast
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from neckline import auction
from neckline.auction import (
    CONFLICT_DIRECTION_OPPOSITE,
    CONFLICT_IDENTITY_MISMATCH,
    CONFLICT_REJECTION_DISAGREE,
    DQ_DEGRADED,
    DQ_INSUFFICIENT,
    DQ_OK,
    QF_CONFLICT,
    QF_DEGRADED,
    QF_FRESH,
    QF_INSUFFICIENT,
)
from neckline.auction import quality as aq
from neckline.data.realtime import DualQuote, Quote

_REPO = Path(__file__).resolve().parent.parent

D1 = date(2026, 8, 11)
NOW = datetime(2026, 8, 11, 9, 26, 30)


# ══════════════════════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════════════════════

def _q(code: str = "600000.SH", *, price: float = 10.5, pre_close: float = 10.0,
       open_: Optional[float] = None, ts: str = "2026-08-11 09:25:03",
       source: str = "sina", volume: float = 5000.0, amount: float = 52500.0) -> Quote:
    """一份竞价读数。⚠ 默认 `open == price`(9:25 撮合价即当前价)。"""
    o = price if open_ is None else open_
    return Quote(code=code, name=code, price=price, pre_close=pre_close, open=o,
                 high=max(price, o), low=min(price, o), volume=volume, amount=amount,
                 ts=ts, source=source)


def _q926(code: str = "600000.SH", **kw) -> Quote:
    """🔴 **9:26 那一拍的真实形状:源还没发开盘价**(`open=0`)。

    ⚠ 这个前提是代码自己两处写死的判断(`auction/__init__.py::QF_DEGRADED`
    的说明 + `test_auction_checklist.py` 全部 9:26 夹具的 `open_=0.0` 默认值),
    仓内**还没有实盘证据**。上产第一周要记一天 9:26 的 `open` 分布 ——
    见 PROJECT_PLAN §13.1。
    """
    kw.setdefault("open_", 0.0)
    price = kw.get("price", 10.5)
    return Quote(code=code, name=code, price=price,
                 pre_close=kw.get("pre_close", 10.0), open=0.0, high=0.0, low=0.0,
                 volume=5000.0, amount=52500.0,
                 ts=kw.get("ts", "2026-08-11 09:25:03"),
                 source=kw.get("source", "sina"))


def _dual(primary: Optional[Quote] = None, backup: Optional[Quote] = None) -> DualQuote:
    code = (primary or backup).code if (primary or backup) else ""
    return DualQuote(code=code, primary=primary, backup=backup)


def _check(q: Optional[Quote], *, code: str = "600000.SH", role: str = "primary",
           trade_date: date = D1, captured_at: datetime = NOW):
    return aq.validate_quote(q, code=code, role=role, trade_date=trade_date,
                             captured_at=captured_at)


# ══════════════════════════════════════════════════════════════════════════
# P2.1 七项校验(逐项各一例;🔴 时间三项零容差)
# ══════════════════════════════════════════════════════════════════════════

class TestSevenChecks:
    def test_a_normal_auction_quote_passes_all_seven(self):
        c = _check(_q())
        assert (c.status, c.errors) == (auction.QS_FRESH, ())
        assert c.ok and c.usable

    def test_a_source_that_did_not_pull_anything_is_not_a_failed_check(self):
        """「**没拉到**」与「拉到了但不合格」是两件事,⛔ 不许折平。"""
        assert _check(None) is None

    def test_check1_code_mismatch_is_malformed(self):
        """① 代码与市场映射:拿回来的必须**就是**要的那一只。
        ⚠ 与「指数被前缀启发式拉成另一只股票」那条老坑同源 —— 真出现时读数
        **完全正常**,只有代码对不上。"""
        c = _check(_q("600519.SH"), code="600000.SH")
        assert c.status == auction.QS_MALFORMED
        assert aq.ERR_CODE_MISMATCH in c.errors
        assert not c.usable

    def test_check2_yesterdays_cached_quote_is_wrong_trade_date(self):
        """② 源日期 == D1。上一交易日的缓存行情价 / 量 / 额全齐,只有 `ts` 里
        那个日期不对 —— 它长得跟正常读数一模一样。"""
        c = _check(_q(ts="2026-08-10 09:25:03"))
        assert c.status == auction.QS_WRONG_TRADE_DATE
        assert not c.usable

    def test_check3_future_timestamp_is_zero_tolerance(self):
        """③ 🔴 **零容差**(2026-08-12 用户裁定 #2,⛔ 不是工程侧默认值)。
        **一秒**都算偏差;⛔ 施工侧不许自己定 1s / 3s / 5s。"""
        one_second_late = _q(ts="2026-08-11 09:26:31")   # captured_at = 09:26:30
        c = _check(one_second_late)
        assert c.status == auction.QS_FUTURE_TIMESTAMP
        assert aq.ERR_FUTURE_TIMESTAMP in c.errors

    def test_check3_source_time_earlier_than_capture_is_normal(self):
        """🔴 裁定 #2 的**另一半**:原文是「源时间**不晚于**本地抓取时间」——
        源时间**早于**抓取时刻是正常的,⛔ 别把它也判成偏差。"""
        assert _check(_q(ts="2026-08-11 09:25:00")).ok

    def test_check3_an_aware_captured_at_does_not_blow_up_the_whole_snapshot(self):
        """⚠ `captured_at` 两种都可能(naive / aware)。裸 `>` 撞上 aware 值会抛
        `TypeError`,而 `resolve_dual` 的循环没包 try/except → **整层静默零落库**。
        边界处归一,且**按 `CN_TZ` 换算后**再剥时区(直接 `replace` 会差 8 小时,
        整份快照全判 `future_timestamp`)。"""
        from neckline.calendar import CN_TZ

        aware = datetime(2026, 8, 11, 9, 26, 30, tzinfo=CN_TZ)
        assert _check(_q(), captured_at=aware).ok
        assert _check(_q(), captured_at=aware.astimezone(
            __import__("datetime").timezone.utc)).ok

    def test_check4_before_final_auction_is_rejected(self):
        """④ 可接受区间 `[09:25:00, captured_at]`。早于 9:25 = 不是最终撮合结果。
        ⚠ 那个 9:25 是**交易所制度**给的时刻,单一源在 `auction/__init__.py`。"""
        c = _check(_q(ts="2026-08-11 09:24:59"))
        assert c.status == auction.QS_BEFORE_FINAL_AUCTION
        assert (aq.AUCTION_RESULT_TIME_START.hour,
                aq.AUCTION_RESULT_TIME_START.minute) == (9, 25)
        assert aq.AUCTION_RESULT_TIME_START is auction.AUCTION_RESULT_TIME_START

    def test_check5_price_and_preclose_are_fatal_but_open_is_not(self):
        """⑤ 必要字段**拆两档**:现价 / 前收盘是竞价涨跌幅的分子分母(缺了整条
        读数都算不出)= 致命;开盘价只被失效位判定用,而那一项本来就有自己的
        第三态 = 非致命。
        🔴 ⛔ 别"简化"回一档 —— 那会因为一个当时用不上的字段把好的价 / 量 / 额
        一起扔掉。**这一条正是 R2-02 那处自相矛盾的另一半**。"""
        no_open = _check(_q926())
        assert no_open.status == auction.QS_REQUIRED_FIELD_MISSING
        assert aq.ERR_OPEN_PRICE_MISSING in no_open.errors
        assert no_open.usable is True                    # 非致命:读数照用
        assert no_open.ok is False                       # 但七项没全过

        no_price = _check(Quote(code="600000.SH", name="x", price=0.0, pre_close=0.0,
                                open=0.0, high=0.0, low=0.0, volume=0.0, amount=0.0,
                                ts="2026-08-11 09:25:03", source="sina"))
        assert aq.ERR_REQUIRED_FIELD_MISSING in no_price.errors
        assert no_price.usable is False                  # 致命

    def test_open_price_missing_is_the_only_non_fatal_error(self):
        """🔴 把这条判据钉死:`_FATAL_ERRORS` = 全部错误码 **减去** 开盘价缺失。
        再加一个非致命项就是在放宽「这条读数能不能用」,必须是一次自觉行为。"""
        assert set(aq.VALIDATION_ERROR_CODES) - aq._FATAL_ERRORS == \
            {aq.ERR_OPEN_PRICE_MISSING}

    def test_check6_price_relation_must_be_internally_consistent(self):
        """⑥ 价格关系一致。⚠ **诚实边界**:两个免费源里只有腾讯自带涨跌幅字段,
        而 `Quote` 从来没有携带过它 —— 本项落在派生式所依赖的那组价格关系上。"""
        bad = Quote(code="600000.SH", name="x", price=99.0, pre_close=10.0, open=10.5,
                    high=11.0, low=10.0, volume=1.0, amount=1.0,
                    ts="2026-08-11 09:25:03", source="sina")
        c = _check(bad)
        assert c.status == auction.QS_MALFORMED
        assert aq.ERR_PRICE_RELATION in c.errors

    def test_check6_zero_high_low_is_absence_not_a_violation(self):
        """⚠ 9:25 之前某些源的 high/low 是 0 —— 那是「没有」,不是「违反」。"""
        assert _check(_q926()).errors == (aq.ERR_OPEN_PRICE_MISSING,)

    def test_check7_negative_volume_or_amount_is_malformed(self):
        """⑦ 单位转换后非异常负数。⚠ `0` 是合法的(竞价可以一手没成交)。"""
        assert aq.ERR_NEGATIVE_VOLUME in _check(_q(volume=-1.0)).errors
        assert aq.ERR_NEGATIVE_AMOUNT in _check(_q(amount=-1.0)).errors
        assert _check(_q(volume=0.0, amount=0.0)).ok

    def test_all_failed_checks_are_kept_not_just_the_first(self):
        """一条读数可以同时踩中好几项 —— `errors` 里**全都留着**,`status` 只是主因。"""
        c = _check(_q(ts="2026-08-10 09:24:00", volume=-3.0))
        assert set(c.errors) >= {aq.ERR_WRONG_TRADE_DATE, aq.ERR_BEFORE_FINAL_AUCTION,
                                 aq.ERR_NEGATIVE_VOLUME}

    def test_parse_quote_ts_keeps_the_raw_string_untouched(self):
        """🔴 逐字:「原始字符串继续保留」—— 本函数只**派生**,`Quote.ts` 一个字不改。"""
        q = _q(ts="2026-08-11 09:25:03")
        c = _check(q)
        assert q.ts == "2026-08-11 09:25:03"
        assert c.ts_raw == q.ts and c.ts_parsed == "2026-08-11 09:25:03"
        assert aq.parse_quote_ts("不是时间") is None
        assert aq.parse_quote_ts("") is None
        assert aq.parse_quote_ts(None) is None


# ══════════════════════════════════════════════════════════════════════════
# 验收 1 / 2 / 3 —— 主备源与时间戳
# ══════════════════════════════════════════════════════════════════════════

class TestPrimaryStaleBackupFresh:
    """1. 新浪返昨日、腾讯返今日:**用腾讯**,记录来源降级。"""

    def test_backup_wins_and_source_degradation_is_recorded(self):
        d = _dual(_q(price=9.0, ts="2026-08-10 09:25:03"),
                  _q(price=11.0, source="tencent"))
        chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert chosen is not None and chosen.source == "tencent" and chosen.price == 11.0
        assert qq.freshness == QF_FRESH
        assert qq.source_degraded is True      # 🔴 「记录来源降级」,⛔ 不静默换源
        assert qq.chosen_role == auction.QUOTE_ROLE_BACKUP

    def test_both_source_readings_are_kept_not_only_the_winner(self):
        """🔴 逐字:「两个来源的原始读数**全部留存**」。"""
        d = _dual(_q(price=9.0, ts="2026-08-10 09:25:03"),
                  _q(price=11.0, source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert {c.role for c in qq.checks} == {"primary", "backup"}
        assert {c.price for c in qq.checks} == {9.0, 11.0}
        assert aq.ERR_WRONG_TRADE_DATE in qq.errors   # 备源赢了,主源的问题照样留痕
        assert qq.status == auction.QS_FRESH          # `status` 报的是胜出那一侧


class TestBothSourcesStale:
    """2. 两源都返昨日:`insufficient`。"""

    def test_both_stale_makes_the_code_insufficient(self):
        d = _dual(_q(ts="2026-08-10 09:25:03"),
                  _q(ts="2026-08-10 09:25:03", source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert qq.freshness == QF_INSUFFICIENT
        assert qq.usable is False

    def test_an_unusable_reading_is_still_returned_never_silently_dropped(self):
        """🔴 ⛔ 不许把不合格的读数悄悄扔掉:扔掉 = 那一格看起来像「没抓到」,
        而真相是「抓到了一份昨天的」—— 两者的排障方向完全相反。"""
        d = _dual(_q(price=7.77, ts="2026-08-10 09:25:03"))
        chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert chosen is not None and chosen.price == 7.77
        assert qq.freshness == QF_INSUFFICIENT
        assert [c.price for c in qq.checks] == [7.77]

    def test_no_reading_at_all_records_nothing_rather_than_a_fake_failure(self):
        """🔴 一条读数都没有 → `status == ""` =「本次没记这一位」。
        ⛔ 不许兜底成 `timestamp_unparseable`:那是在报告一次**证明没发生过**的
        校验失败,而这一位会顺着核对表进 `k9_d1_verdicts` 的冻结审计行。"""
        _chosen, qq = aq.resolve_dual("600000.SH", _dual(), trade_date=D1, captured_at=NOW)
        assert qq.checks == () and qq.status == ""
        assert qq.freshness == QF_INSUFFICIENT and qq.chosen_role is None


class TestUnparseableTimestamp:
    """3. 时间戳无法解析:**不得判 `ok`**。"""

    def test_unparseable_timestamp_is_never_ok(self):
        assert _check(_q(ts="")).status == auction.QS_TIMESTAMP_UNPARSEABLE
        assert _check(_q(ts="20260811092503")).status == auction.QS_TIMESTAMP_UNPARSEABLE
        assert _check(_q(ts="没有时间")).usable is False

    def test_the_second_accepted_format_is_defensive_not_an_invitation(self):
        """⚠ 秒位缺失是**防御性**的次要形状。⛔ 别再往下加更宽松的:
        解析越宽容,「时间戳解不出」这个真信号就越容易被吞掉。"""
        assert aq._TS_FORMATS == ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


# ══════════════════════════════════════════════════════════════════════════
# 验收 6 —— 🔴 跨源结论性冲突(**原第 6 条,钩子换成 K9 的 `rejection_of`**)
# ══════════════════════════════════════════════════════════════════════════

def _rejection_below(line: float):
    """三态钩子:这一源的读数会不会触发「放弃」(跌破 `line`)。

    ⚠ 三态 —— `None` = **判不了**(读不到价)。K9 侧的真实实现是
    `checklist.rejection_triggered`(它吃 `evaluate_branch` 的 Kleene 三值)。
    """
    def _hook(q: Quote) -> Optional[bool]:
        price = getattr(q, "price", None)
        if not price:
            return None
        return float(price) < line
    return _hook


class TestSourceConflict:
    """6. 双源一边触发「放弃」、一边不触发 → `conflict`,**不能高置信输出**。"""

    def test_rejection_disagreement_is_a_conflict(self):
        hit = _q(price=9.0)                                  # 跌破 9.5 → 放弃
        miss = _q(price=10.5, source="tencent")              # 没跌破 → 不放弃
        assert aq.detect_conflict(hit, miss, rejection_of=_rejection_below(9.5)) == \
            CONFLICT_REJECTION_DISAGREE

    def test_one_side_undetermined_is_not_a_conflict(self):
        """🔴 三态不许折平:「一边判不了」⛔ 不是「两边看法不同」
        (`CLAUDE.md`「三态字段:`is not None` 会把它们折平」的同一个坑)。"""
        hit = _q(price=9.0)
        blind = Quote(code="600000.SH", name="x", price=0.0, pre_close=10.0, open=0.0,
                      high=0.0, low=0.0, volume=0.0, amount=0.0,
                      ts="2026-08-11 09:25:03", source="tencent")
        hook = _rejection_below(9.5)
        assert hook(hit) is True and hook(blind) is None
        assert aq.detect_conflict(hit, blind, rejection_of=hook) is None

    def test_no_hook_means_this_comparison_was_not_made(self):
        """⚠ 钩子为 `None`(调用方压根没给)= **本次不做这一类比较**,
        ⛔ 不是「没冲突」。"""
        # ⚠ 两边**同方向**(都在跌),把「方向相反」那一类排除掉,单独看放弃这一类。
        hit, miss = _q(price=9.6), _q(price=9.9, source="tencent")
        assert aq.detect_conflict(hit, miss) is None
        assert aq.detect_conflict(hit, miss, rejection_of=_rejection_below(9.8)) == \
            CONFLICT_REJECTION_DISAGREE

    def test_direction_opposite_and_identity_mismatch(self):
        up = _q(price=11.0, pre_close=10.0)
        down = _q(price=9.0, pre_close=10.0, source="tencent")
        assert aq.detect_conflict(up, down) == CONFLICT_DIRECTION_OPPOSITE
        # 一边平开一边涨 **不算** 方向相反(那是幅度差,不是结论差)
        flat = _q(price=10.0, pre_close=10.0, source="tencent")
        assert aq.detect_conflict(up, flat) is None
        other = _q("600519.SH", price=11.0, pre_close=99.0, source="tencent")
        assert aq.detect_conflict(up, other) == CONFLICT_IDENTITY_MISMATCH

    def test_identity_is_reported_before_everything_else(self):
        """③ 身份不一致 → 其余几类的比较**全部失去意义**,故排在最前。"""
        a = _q(price=11.0, pre_close=10.0)
        b = _q("600519.SH", price=9.0, pre_close=10.0, source="tencent")
        assert aq.detect_identity_conflict(a, b) is True
        assert aq.detect_conflict(a, b, rejection_of=_rejection_below(9.5)) == \
            CONFLICT_IDENTITY_MISMATCH

    def test_single_source_is_never_reported_as_no_conflict(self):
        """🔴 只有一源 = 没有第二个读数可以打架 —— ⛔ 那不是「已核对无冲突」。"""
        assert aq.detect_conflict(_q(), None) is None
        _chosen, qq = aq.resolve_dual("600000.SH", _dual(_q()),
                                      trade_date=D1, captured_at=NOW)
        assert qq.conflict is None and len(qq.checks) == 1
        assert qq.cross_verified is False


# ══════════════════════════════════════════════════════════════════════════
# 🔴 R2-02:**跨源核验在 9:26 那一拍必须真的能触发**
# ══════════════════════════════════════════════════════════════════════════
#
# 复审实测的反例(CE-4):
#     两源同码同日、一源报 9.0 一源报 11.0、放弃线 9.5(结论明确相反),
#     open=0(9:26 的正常状态)→ cross_verified=False  conflict=None
#     同一组读数,唯一区别是 open>0 → cross_verified=True  conflict=rejection_disagree
#
# 根因是**同一个模块里两条判据打架**:`_FATAL_ERRORS` 明确把 `open_price_missing`
# 排除在致命之外(理由:9:26 那一拍本来就还没有开盘价),而 `_is_cross_verified`
# 却要求「七项全过」。后果:S8 新造的 `rejection_disagree` 在它**专为之而生**的
# 那一拍里是死代码。
# ══════════════════════════════════════════════════════════════════════════

class TestCrossVerificationAtTheAuctionTick:
    def test_two_sources_that_disagree_at_0926_do_produce_a_conflict(self):
        """🔴 CE-4 原样复现:`open=0`(9:26 的正常状态)下必须报出冲突。"""
        d = _dual(_q926(price=9.0), _q926(price=11.0, source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW,
                                      rejection_of=_rejection_below(9.5))
        assert qq.cross_verified is True, "9:26 那一拍结构上没做过对拍 —— 死代码回来了"
        assert qq.conflict == CONFLICT_REJECTION_DISAGREE
        assert qq.freshness == QF_CONFLICT, "冲突被 `degraded` 盖住了 —— 消费方读的是它"

    def test_the_criterion_is_usable_not_ok(self):
        """判别式的**单一源**是 `_is_cross_verified`,判据 = `usable`(无致命项)。

        ⛔ 不是 `ok`(七项全过)—— 一条读数只要能被拿去**求值**,就一定能被拿去
        与另一源**对拍**;对拍的要求比求值更低,不是更高。
        """
        both_no_open = [_check(_q926()), _check(_q926(source="tencent"), role="backup")]
        assert all(c.usable and not c.ok for c in both_no_open)
        assert aq._is_cross_verified(both_no_open) is True

    def test_a_fatal_reading_still_means_there_was_nothing_to_compare(self):
        """⚠ 反向自检:**致命项**照旧使「没得比」—— ⛔ 这条修复不许把
        「一源是昨天的缓存」也讲成「对拍过了」。"""
        d = _dual(_q926(price=9.0),
                  _q926(price=11.0, source="tencent", ts="2026-08-10 09:25:03"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW,
                                      rejection_of=_rejection_below(9.5))
        assert qq.cross_verified is False
        assert qq.conflict is None, "`None` 的含义是「没得比」,⛔ 不是「比过了没冲突」"

    def test_cross_verified_is_written_into_the_frozen_row(self):
        """落库留痕:展示层据此决定「说不说『已交叉核验』」。
        ⚠ 老行读不到这一键时**当 False**(保守方向:不声称核验过)。"""
        d = _dual(_q926(price=10.4), _q926(price=10.5, source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW,
                                      rejection_of=_rejection_below(9.5))
        payload = qq.to_dict()
        assert payload["cross_verified"] is True and payload["conflict"] is None
        assert len(payload["checks"]) == 2


# ══════════════════════════════════════════════════════════════════════════
# 样本域三态(**结构性判据,⛔ 不是百分比**)
# ══════════════════════════════════════════════════════════════════════════

class TestDomainQuality:
    def _qq(self, code: str, freshness: str) -> aq.QuoteQuality:
        return aq.QuoteQuality(ts_code=code, freshness=freshness)

    def test_an_empty_domain_is_insufficient_not_ok(self):
        """「没有可判的东西」与「判过了都好」必须分得开。"""
        assert aq.domain_quality([], {}, in_window=True) == DQ_INSUFFICIENT

    def test_all_fresh_in_window_is_ok(self):
        m = {"a": self._qq("a", QF_FRESH), "b": self._qq("b", QF_FRESH)}
        assert aq.domain_quality(["a", "b"], m, in_window=True) == DQ_OK
        assert aq.domain_quality(["a", "b"], m, in_window=False) == DQ_DEGRADED

    def test_one_insufficient_code_degrades_the_domain(self):
        m = {"a": self._qq("a", QF_FRESH), "b": self._qq("b", QF_INSUFFICIENT)}
        assert aq.domain_quality(["a", "b"], m, in_window=True) == DQ_DEGRADED

    def test_every_code_insufficient_is_insufficient(self):
        m = {"a": self._qq("a", QF_INSUFFICIENT)}
        assert aq.domain_quality(["a"], m, in_window=True) == DQ_INSUFFICIENT

    def test_worse_of_orders_ok_degraded_insufficient(self):
        assert aq.worse_of(DQ_OK, DQ_DEGRADED) == DQ_DEGRADED
        assert aq.worse_of(DQ_DEGRADED, DQ_INSUFFICIENT) == DQ_INSUFFICIENT
        assert aq.worse_of(DQ_OK, DQ_OK) == DQ_OK


# ══════════════════════════════════════════════════════════════════════════
# 🔴 守门:零容差(用户裁定 #2)—— 靠自觉就会失守的那一类
# ══════════════════════════════════════════════════════════════════════════

class TestZeroToleranceGuards:
    """🔴 **用户裁定 #2**(2026-08-12):「竞价时间戳先执行**零容差**:源时间与本机
    存在任何偏差即降级为中性。若实盘出现误判,再由我确认容差秒数,**施工 Agent
    不得自行设定**。」

    ⚠ 本组**只看真代码,不剥 docstring 里的说明** —— 裁定原文就写在 docstring 里,
    把它算成违规 = 逼人删掉自己要说的话。
    """

    def _code_only(self, path: Path) -> str:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return "\n".join(
            ast.dump(n) for n in ast.walk(tree)
            if isinstance(n, (ast.Compare, ast.BinOp, ast.Assign, ast.Call)))

    def test_no_second_level_slack_anywhere_in_the_auction_layer(self):
        """⛔ 全 `neckline/auction/**` 的真代码里不许出现任何「给几秒富余」的常量。"""
        for path in sorted((_REPO / "neckline" / "auction").rglob("*.py")):
            code_only = self._code_only(path)
            for banned in ("timedelta(seconds", "TOLERANCE", "SLACK_SEC", "GRACE_SEC"):
                assert banned not in code_only, \
                    f"{path.name} 的真代码里出现了容差:{banned}"

    def test_the_landing_point_is_a_bare_strict_comparison(self):
        """正面钉死:落点就是 `src > captured_at` 这一条**裸**比较。"""
        src = (_REPO / "neckline" / "auction" / "quality.py").read_text(encoding="utf-8")
        assert "if src > captured_at:" in src

    def test_tolerance_cannot_be_smuggled_in_through_the_call_site(self):
        """🔵 只扫 `quality.py`、只禁四个字面名是不够的 —— `collect.py` 往
        `captured_at` 上**加几秒**再传进去,同样是给容差。裁定 #2 原话是
        「施工 Agent **不得自行设定**」,那就得覆盖**调用点**。

        判据:传给**做时间比较的那两个函数**的 `captured_at=` 必须是裸名字或裸属性。
        ⚠ 只盯这两个入口 —— `…captured_at=x.isoformat()` 那种是**格式化落库**,
        与判据无关,一把梭会把它一起判违规。
        """
        offenders: List[str] = []
        for path in sorted((_REPO / "neckline" / "auction").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name not in ("validate_quote", "resolve_dual"):
                    continue
                for kw in node.keywords:
                    if kw.arg == "captured_at" and not isinstance(
                            kw.value, (ast.Name, ast.Attribute)):
                        offenders.append(f"{path.name}:{node.lineno} → {ast.dump(kw.value)[:60]}")
        assert offenders == [], (
            "有人在调用点给 `captured_at` 加了表达式(= 偷偷加容差,裁定 #2 明令禁止):\n"
            + "\n".join(offenders))

    def test_the_call_site_detector_actually_detects(self, tmp_path: Path):
        """⛔ 守门自己要能红 —— 给一个真加了容差的诱饵,判据必须抓到。"""
        bait = tmp_path / "bait.py"
        bait.write_text(
            "def f(q, code, captured_at):\n"
            "    return validate_quote(q, code=code, role='primary',\n"
            "                          captured_at=captured_at + timedelta(seconds=3))\n",
            encoding="utf-8")
        tree = ast.parse(bait.read_text(encoding="utf-8"))
        hits = [
            kw for node in ast.walk(tree) if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "validate_quote"
            for kw in node.keywords
            if kw.arg == "captured_at" and not isinstance(kw.value, (ast.Name, ast.Attribute))
        ]
        assert len(hits) == 1, "诱饵没被抓到 —— 这条守门是纸糊的"


# ══════════════════════════════════════════════════════════════════════════
# 补齐分支(⚠ 这几条是**行覆盖的洞**,不是新语义)
# ══════════════════════════════════════════════════════════════════════════

class TestRemainingBranches:
    def test_a_non_numeric_field_reads_as_absent_not_as_zero(self):
        """`_f()`:转不成 float → `None`(= 没发),⛔ 不是 0。"""
        bad = Quote(code="600000.SH", name="x", price="不是数", pre_close=10.0,
                    open=10.0, high=10.0, low=10.0, volume=1.0, amount=1.0,
                    ts="2026-08-11 09:25:03", source="sina")
        c = _check(bad)
        assert c.price is None
        assert aq.ERR_REQUIRED_FIELD_MISSING in c.errors

    def test_high_below_low_is_malformed(self):
        """⑥ 的另一半:区间本身就是反的。"""
        bad = Quote(code="600000.SH", name="x", price=10.5, pre_close=10.0, open=10.5,
                    high=9.0, low=11.0, volume=1.0, amount=1.0,
                    ts="2026-08-11 09:25:03", source="sina")
        assert aq.ERR_PRICE_RELATION in _check(bad).errors

    def test_identity_conflict_covers_preclose_and_trade_date(self):
        """③ 身份三条:代码 / 前收盘 / 交易日,任一不一致就是「不是同一只 / 同一天」。"""
        a = _q(pre_close=10.0)
        assert aq.detect_identity_conflict(a, _q(pre_close=11.0, source="tencent")) is True
        assert aq.detect_identity_conflict(
            a, _q(ts="2026-08-10 09:25:03", source="tencent")) is True
        assert aq.detect_identity_conflict(a, _q(source="tencent")) is False
        # 只有一源 → 无从判身份(⛔ 不是「身份一致」)
        assert aq.detect_identity_conflict(a, None) is False

    def test_src_ts_reports_the_winning_sides_timestamp(self):
        d = _dual(_q(price=9.0, ts="2026-08-10 09:25:03"),
                  _q(price=11.0, ts="2026-08-11 09:25:07", source="tencent"))
        _chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert qq.chosen_role == auction.QUOTE_ROLE_BACKUP
        assert qq.src_ts == "2026-08-11 09:25:07"
        assert aq.QuoteQuality(ts_code="x", freshness=QF_INSUFFICIENT).src_ts is None

    def test_backup_only_paths(self):
        """备源单边的三条落点:可用 / 可用且冲突 / 致命。"""
        # 主源致命(昨日缓存)、备源缺开盘价 → 用备源 + 降级
        d = _dual(_q(ts="2026-08-10 09:25:03"), _q926(source="tencent"))
        chosen, qq = aq.resolve_dual("600000.SH", d, trade_date=D1, captured_at=NOW)
        assert chosen.source == "tencent"
        assert (qq.freshness, qq.source_degraded) == (QF_DEGRADED, True)
        assert qq.cross_verified is False

        # 两源都致命 → `insufficient`,胜出侧记备源
        d2 = _dual(None, _q(ts="2026-08-10 09:25:03", source="tencent"))
        chosen2, qq2 = aq.resolve_dual("600000.SH", d2, trade_date=D1, captured_at=NOW)
        assert chosen2 is not None and qq2.freshness == QF_INSUFFICIENT
        assert qq2.chosen_role == auction.QUOTE_ROLE_BACKUP and qq2.source_degraded is True
