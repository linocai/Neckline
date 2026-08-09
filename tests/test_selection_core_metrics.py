"""V2.2-③-C2 核心关机械读数层 `neckline/selection/core_metrics.py` 单测
(🔴 2026-08-09 用户裁定 #12:核心关退出机械闸,机械侧**只算读数、零判定**;
判定移交 LLM,那一半在 `test_selection_gates.py::TestRulingTwelveMachineCriteria`)。

覆盖:
· 行业域名次三项(20 日 RS 名次 / 分位 / 当日涨跌幅名次)+ 成交额占比逐项数值正确性
  (独立 Python oracle 交叉验证,不复用被测代码的计算逻辑);
· 🔴 **两个分母都必须出现**(`industry_member_count` 与 `industry_rs_ranked_count_20d`)
  —— 没有它们,「第 3 名」是 3/8 还是 3/80 完全没法读(裁定 #12 的 🔴 分母条款);
· 名次的**确定性 tie-break**(收益并列 → `ts_code` 升序;CLAUDE.md 立过规:
  `rank(ordinal)` 的并列由行序打散 = 不确定性);
· **缺数不猜**:行业未映射 / 当日停牌无行 / 20 日窗口不足 / 行业只有 1 只可比
  / `limit_derived` 分区缺失,逐项断言缺的是哪个键、原因码是什么(⛔ 不填 0);
· 🔴 **`limit_derived` 稀疏表语义**:票不在命中行 = **确定的 0**(⛔ 不是缺数),
  只有分区文件不存在才是「不知道」—— 返工时真踩过的坑,正反双向锁死;
· 簇内补充读数的两种「没有」**语义相反不许合并**(`not_in_cluster` = 确定事实 /
  `cluster_data_unavailable` = 不知道),且**缺席不挡任何档**;
· 🔴 零阈值守门:模块里不出现任何「及格线」形状(⛔ 含「行业内前 X%」),
  也不出现被否掉的容量类量(市值 / 流通盘 / 换手…);
· 反向守门:零 import `sentinel` / `report.score_display` / `selection.gates`;零写库。
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
from typing import Dict, List

import polars as pl
import pytest

from neckline.selection import core_metrics as cm
from tests.conftest import insert_stock_basic, insert_trade_cal, write_daily_fixture

CM_SRC = Path(cm.__file__).read_text(encoding="utf-8")

# 20 个交易日(2024-04-08 是判定日 D0;`RS_WINDOW_DAYS=20` 需要恰好 20 根 bar)。
_DAYS: List[date] = [
    date(2024, 3, 11), date(2024, 3, 12), date(2024, 3, 13), date(2024, 3, 14),
    date(2024, 3, 15), date(2024, 3, 18), date(2024, 3, 19), date(2024, 3, 20),
    date(2024, 3, 21), date(2024, 3, 22), date(2024, 3, 25), date(2024, 3, 26),
    date(2024, 3, 27), date(2024, 3, 28), date(2024, 3, 29), date(2024, 4, 1),
    date(2024, 4, 2), date(2024, 4, 3), date(2024, 4, 7), date(2024, 4, 8),
]
D0 = _DAYS[-1]
assert len(_DAYS) == cm.RS_WINDOW_DAYS


# ══════════════════════════════════════════════════════════════════════════
# 夹具:一个两行业的小市场,收益率逐票写死(oracle 直接照抄这些数算)
# ══════════════════════════════════════════════════════════════════════════

# code → (行业, 每日收益率〔20 天恒定〕, D0 成交额)
_MARKET: Dict[str, tuple] = {
    "600001.SH": ("半导体", 0.010, 3000.0),
    "600002.SH": ("半导体", 0.005, 2000.0),
    "600003.SH": ("半导体", -0.002, 1000.0),
    "600004.SH": ("半导体", 0.005, 4000.0),     # 与 600002 收益并列 → 考 tie-break
    "600009.SH": ("白酒", 0.020, 5000.0),
    "600010.SH": ("白酒", 0.001, 1500.0),
}


def _seed_market(env, *, days: List[date] = None, market: Dict[str, tuple] = None,
                 skip_d0: tuple = ()) -> None:
    """铺 `stock_basic`(行业映射)+ 逐日 `daily` 分区。

    `skip_d0` 里的票在 **D0 当天不落行**(模拟停牌);收益率恒定使 20 日累计收益
    = `(1+r)**20 − 1`,oracle 无需重算价格路径。"""
    days = days or _DAYS
    market = market or _MARKET
    insert_stock_basic(env, [{"ts_code": c, "name": c, "industry": ind}
                             for c, (ind, _r, _a) in market.items()])
    insert_trade_cal(env, days)
    prices = {c: 10.0 for c in market}
    for d in days:
        rows = []
        for code, (_ind, ret, amt) in market.items():
            pre = prices[code]
            close = round(pre * (1.0 + ret), 6)
            prices[code] = close
            if d == days[-1] and code in skip_d0:
                continue
            rows.append({"ts_code": code, "trade_date": d, "open": pre, "high": close,
                         "low": pre, "close": close, "pre_close": pre,
                         "change": close - pre, "pct_chg": ret * 100.0,
                         "vol": 1000.0, "amount": amt if d == days[-1] else 100.0})
        write_daily_fixture(env, "daily", d, rows)


def _write_limit_derived(env, days: List[date], limit_up_by_day: Dict[date, List[str]]) -> None:
    """写 `limit_derived` **稀疏**分区:只落命中行(与生产写侧同口径)。
    ⚠ 某日给空列表 = 该日**分区存在但零命中行**(全市场都没涨停),与「分区不存在」
    是两回事 —— 后者靠不调用本函数(不落那天的文件)来模拟。"""
    for d in days:
        codes = limit_up_by_day.get(d, [])
        rows = [{"ts_code": c, "trade_date": d, "is_limit_up": True,
                 "is_limit_down": False, "is_zaban": False,
                 "limit_pct": 0.1, "limit_up_price": 11.0, "limit_down_price": 9.0}
                for c in codes]
        if not rows:
            rows = [{"ts_code": "999999.SZ", "trade_date": d, "is_limit_up": False,
                     "is_limit_down": True, "is_zaban": False,
                     "limit_pct": 0.1, "limit_up_price": 11.0, "limit_down_price": 9.0}]
        write_daily_fixture(env, "limit_derived", d, rows)


def _run(env, codes: List[str]) -> cm.CoreMetricsResult:
    return cm.compute_core_metrics(D0, codes, db_path=env.db_path,
                                   parquet_dir=env.parquet_dir)


# ══════════════════════════════════════════════════════════════════════════
# 1. 行业域读数的数值正确性 + 🔴 分母
# ══════════════════════════════════════════════════════════════════════════

class TestIndustryReadings:
    def test_ranks_shares_and_both_denominators(self, isolated_env):
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS, {})
        res = _run(env, ["600001.SH", "600003.SH", "600009.SH"])
        assert res.available is True

        m = res.metrics["600001.SH"]
        # 🔴 两个分母都必须给(没有它们「第 1 名」读不出是 1/4 还是 1/40)
        assert m["industry_member_count"] == 4
        assert m["industry_rs_ranked_count_20d"] == 4
        # 半导体四只 20 日收益 1.0% > 0.5%(两只并列)> −0.2% → 600001 名次 1
        assert m["industry_rs_rank_20d"] == 1
        assert m["industry_ret_rank_1d"] == 1
        assert m["industry_rs_pct_20d"] == pytest.approx(1.0)
        # 成交额占比 = 3000 / (3000+2000+1000+4000)
        assert m["industry_amount_share"] == pytest.approx(0.3)
        assert res.missing["600001.SH"].get("industry_member_count") is None

        worst = res.metrics["600003.SH"]
        assert worst["industry_rs_rank_20d"] == 4
        assert worst["industry_rs_pct_20d"] == pytest.approx(0.0)
        assert worst["industry_amount_share"] == pytest.approx(0.1)

        # 另一个行业的分母是它自己那一群(⛔ 不是全市场)
        baijiu = res.metrics["600009.SH"]
        assert baijiu["industry_member_count"] == 2
        assert baijiu["industry_rs_rank_20d"] == 1
        assert baijiu["industry_amount_share"] == pytest.approx(5000 / 6500)

    def test_tie_is_broken_deterministically_by_ts_code(self, isolated_env):
        """并列必须有**确定性 tie-break**(CLAUDE.md 立过规:`rank(ordinal)` 的并列
        由行序打散,行序随取数方式变 → 同一天算出两种名次)。600002 与 600004 收益
        完全相同 → 按 `ts_code` 升序,600002 在前。"""
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS, {})
        first = _run(env, ["600002.SH", "600004.SH"])
        second = _run(env, ["600004.SH", "600002.SH"])   # 换个提问顺序,结果必须一样
        for res in (first, second):
            assert res.metrics["600002.SH"]["industry_rs_rank_20d"] == 2
            assert res.metrics["600004.SH"]["industry_rs_rank_20d"] == 3

    def test_industry_with_a_single_comparable_member_has_no_percentile(self, isolated_env):
        """行业里只有 1 只可比 → 分位**无定义**(分母 n−1 = 0),如实标原因码,
        ⛔ 不返回 0 也不返回 1(那会把"没法算"讲成"最弱/最强")。"""
        env = isolated_env
        _seed_market(env, market={"600009.SH": ("白酒", 0.02, 5000.0)})
        _write_limit_derived(env, _DAYS, {})
        res = _run(env, ["600009.SH"])
        m, miss = res.metrics["600009.SH"], res.missing["600009.SH"]
        assert m["industry_rs_rank_20d"] == 1 and m["industry_member_count"] == 1
        assert "industry_rs_pct_20d" not in m
        assert miss["industry_rs_pct_20d"] == cm.REASON_INDUSTRY_TOO_SMALL


# ══════════════════════════════════════════════════════════════════════════
# 2. 缺数不猜(每一类都断言缺的是哪个键 + 原因码,⛔ 不填 0)
# ══════════════════════════════════════════════════════════════════════════

class TestMissingIsNeverZero:
    def test_unmapped_industry_marks_every_industry_reading(self, isolated_env):
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS, {})
        res = _run(env, ["600001.SH", "888888.SZ"])
        miss = res.missing["888888.SZ"]
        for key in cm.INDUSTRY_METRIC_KEYS:
            assert miss[key] == cm.REASON_INDUSTRY_UNMAPPED
        # ⚠ 但**逐票读数照给**:连板高度只需要这一只票自己的 `limit_derived` 行,
        # 行业查不到不该连累它(三类读数分界的意义就在这)。
        assert set(res.metrics["888888.SZ"]) == set(cm.STOCK_METRIC_KEYS)
        assert res.metrics["888888.SZ"]["consec_limit_up_days"] == 0

    def test_suspended_stock_keeps_group_denominators_but_loses_its_own_rank(self, isolated_env):
        """当日无 `daily` 行(停牌):**关于"这一群"的事实照给**(分母),它自己的
        名次与占比如实标缺 —— ⛔ 不猜一个名次出来。"""
        env = isolated_env
        _seed_market(env, skip_d0=("600002.SH",))
        _write_limit_derived(env, _DAYS, {})
        res = _run(env, ["600002.SH"])
        m, miss = res.metrics["600002.SH"], res.missing["600002.SH"]
        assert m["industry_member_count"] == 3          # 停牌那只不在当日分母里
        assert miss["industry_ret_rank_1d"] == cm.REASON_NO_DAILY_ROW
        assert miss["industry_amount_share"] == cm.REASON_NO_DAILY_ROW
        assert "industry_ret_rank_1d" not in m and "industry_amount_share" not in m

    def test_short_history_loses_the_20d_rank_only(self, isolated_env):
        """20 日窗口凑不满 → 20 日名次与分位缺(`insufficient_history`),
        当日名次照给。⚠ 分母也随之变小,**两个分母因此必须分开给**。"""
        env = isolated_env
        _seed_market(env)
        # 新票:只有最后 5 天有行情
        late = _DAYS[-5:]
        insert_stock_basic(env, [{"ts_code": "600005.SH", "name": "新票", "industry": "半导体"}])
        for d in late:
            existing = pl.read_parquet(
                env.parquet_dir / "daily" / f"year={d.year}" / f"{d:%Y%m%d}.parquet")
            new_row = pl.DataFrame([{
                "ts_code": "600005.SH", "trade_date": d, "open": 10.0, "high": 10.1,
                "low": 9.9, "close": 10.1, "pre_close": 10.0, "change": 0.1,
                "pct_chg": 1.0, "vol": 100.0, "amount": 500.0}])
            write_daily_fixture(env, "daily", d,
                                pl.concat([existing, new_row], how="vertical_relaxed")
                                .to_dicts())
        _write_limit_derived(env, _DAYS, {})
        res = _run(env, ["600005.SH"])
        m, miss = res.metrics["600005.SH"], res.missing["600005.SH"]
        assert miss["industry_rs_rank_20d"] == cm.REASON_INSUFFICIENT_HISTORY
        assert miss["industry_rs_pct_20d"] == cm.REASON_INSUFFICIENT_HISTORY
        assert "industry_rs_rank_20d" not in m
        assert m["industry_ret_rank_1d"] >= 1           # 当日名次照给
        assert m["industry_member_count"] == 5          # 当日分母含它
        assert m["industry_rs_ranked_count_20d"] == 4   # 20 日分母不含它

    def test_no_daily_partitions_at_all_degrades_honestly(self, isolated_env):
        env = isolated_env
        insert_stock_basic(env, [{"ts_code": "600001.SH", "name": "甲", "industry": "半导体"}])
        insert_trade_cal(env, _DAYS)
        res = _run(env, ["600001.SH"])
        assert res.available is False
        assert res.missing["600001.SH"]["industry_rs_rank_20d"] == (
            cm.REASON_DAILY_DATA_UNAVAILABLE)
        assert res.metrics["600001.SH"] == {}


# ══════════════════════════════════════════════════════════════════════════
# 3. 🔴 `limit_derived` 稀疏表语义(返工时真踩过,正反双向锁死)
# ══════════════════════════════════════════════════════════════════════════

class TestConsecLimitUpSparseTable:
    def test_absent_row_means_a_certain_zero_not_a_data_gap(self, isolated_env):
        """票不在稀疏表命中行里 = **「那天没涨停」的确定事实** → `0`,
        ⛔ **不是** `metrics_missing`。全市场每天 98% 的票都是这个情形,把它当缺数
        会让 `metrics_missing` 的信噪比彻底垮掉。"""
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS, {})            # 每天都有分区、都零命中
        res = _run(env, ["600001.SH"])
        assert res.metrics["600001.SH"]["consec_limit_up_days"] == 0
        assert "consec_limit_up_days" not in res.missing["600001.SH"]

    def test_counts_consecutive_days_backwards_from_d0(self, isolated_env):
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS, {
            _DAYS[-1]: ["600001.SH"], _DAYS[-2]: ["600001.SH"],
            _DAYS[-3]: ["600001.SH"],
            _DAYS[-5]: ["600001.SH"],                    # 断了一天,⛔ 不该被数进来
        })
        res = _run(env, ["600001.SH"])
        assert res.metrics["600001.SH"]["consec_limit_up_days"] == 3

    def test_missing_partition_on_d0_is_the_only_real_unknown(self, isolated_env):
        """真正的「不知道」只有一种:分区**文件本身不存在**。"""
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, _DAYS[:-1], {})        # D0 的分区不落
        res = _run(env, ["600001.SH"])
        assert "consec_limit_up_days" not in res.metrics["600001.SH"]
        assert res.missing["600001.SH"]["consec_limit_up_days"] == (
            cm.REASON_LIMIT_DATA_UNAVAILABLE)

    def test_gap_while_still_counting_reports_unknown_not_a_lower_bound(self, isolated_env):
        """回看途中撞上缺分区、而计数**还在继续** → 真值只是个下界 → 照样标
        `limit_data_unavailable`(⛔ 不把下界当事实报出去)。"""
        env = isolated_env
        _seed_market(env)
        _write_limit_derived(env, [_DAYS[-1], _DAYS[-2]],
                             {_DAYS[-1]: ["600001.SH"], _DAYS[-2]: ["600001.SH"]})
        res = _run(env, ["600001.SH"])
        assert "consec_limit_up_days" not in res.metrics["600001.SH"]
        assert res.missing["600001.SH"]["consec_limit_up_days"] == (
            cm.REASON_LIMIT_DATA_UNAVAILABLE)


# ══════════════════════════════════════════════════════════════════════════
# 4. 簇内补充读数:两种「没有」语义相反,且缺席不挡任何档
# ══════════════════════════════════════════════════════════════════════════

class TestClusterSupplement:
    def test_present_cluster_fills_all_three(self):
        metrics: Dict[str, object] = {}
        missing: Dict[str, str] = {}
        cm.merge_cluster_supplement(metrics, missing, rs_rank=1, amount_share=0.4,
                                    size=5, cluster_available=True, in_cluster=True)
        assert metrics == {"cluster_rs_rank": 1, "cluster_amount_share": 0.4,
                           "cluster_size": 5}
        assert missing == {}

    def test_not_in_cluster_is_a_fact_not_a_data_gap(self):
        """🔴 裁定 12-a:「今天不在任何涨停簇」是**确定事实**(而且是绝大多数票的
        常态)—— 与「簇表没取到」是两个相反的东西,⛔ 不许合并成一个原因码。"""
        metrics: Dict[str, object] = {}
        missing: Dict[str, str] = {}
        cm.merge_cluster_supplement(metrics, missing, rs_rank=None, amount_share=None,
                                    size=None, cluster_available=True, in_cluster=False)
        assert metrics == {}
        assert set(missing) == set(cm.CLUSTER_METRIC_KEYS)
        assert all(v == cm.REASON_NOT_IN_CLUSTER for v in missing.values())

    def test_cluster_table_unavailable_is_a_different_reason(self):
        metrics: Dict[str, object] = {}
        missing: Dict[str, str] = {}
        cm.merge_cluster_supplement(metrics, missing, rs_rank=None, amount_share=None,
                                    size=None, cluster_available=False, in_cluster=False)
        assert all(v == cm.REASON_CLUSTER_DATA_UNAVAILABLE for v in missing.values())
        assert cm.REASON_NOT_IN_CLUSTER != cm.REASON_CLUSTER_DATA_UNAVAILABLE


# ══════════════════════════════════════════════════════════════════════════
# 5. 🔴 零阈值 / 零容量类量 / 反向守门
# ══════════════════════════════════════════════════════════════════════════

def test_module_declares_no_pass_line_and_no_capacity_metric():
    """🔴 裁定 12-b / 12-c 的机器判据(读数层那一半):

    · **零及格线**:模块里不许出现任何名字像门槛的常量(`*_MIN` / `*_MAX` /
      `*_THRESHOLD` / `*_PCT_*`),⛔ 含被点名否决的「行业内前 X%」;
    · **零容量类量**:读数键名与标签里不许出现市值 / 流通盘 / 换手 / 承接 ——
      用户在「龙头 vs K8 §五-4 的容量核心」之间选的是**龙头**,那是被否掉的一半。
    """
    tree = ast.parse(CM_SRC)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    upper = t.id.upper()
                    for banned in ("_MIN", "_MAX", "_THRESHOLD", "_PCTILE", "_LINE"):
                        # `_MIN_RANKABLE`(分位的数学下限 2)是唯一豁免:它是"分母不能
                        # 为 0"的定义,不是及格线 —— 白名单单列,⛔ 别扩。
                        assert not upper.endswith(banned) or t.id == "_MIN_RANKABLE", t.id
    blob = " ".join(f"{k} {label}" for _g, items in cm.CORE_METRIC_GROUPS
                    for k, label in items)
    for banned in ("市值", "流通", "换手", "承接", "容量", "mv", "float_share"):
        assert banned not in blob, banned


def test_reverse_import_guard_and_no_writes():
    """反向守门:读数层零 import `sentinel` / `report.score_display` /
    `selection.gates`(gates 反过来**也不许** import 本模块,方向单一 —— 读数由 ⑤
    随成员带进来);且本模块**一行都不写库**(只读)。"""
    tree = ast.parse(CM_SRC)
    mods: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    banned = ("neckline.sentinel", "neckline.report.score_display",
              "neckline.selection.gates")
    for m in mods:
        assert not any(m == b or m.startswith(b + ".") for b in banned), m
    for sql in ("INSERT", "UPDATE", "DELETE", "CREATE TABLE", "write_table_day"):
        assert sql not in CM_SRC, sql


def test_metric_key_groups_cover_the_plan_reading_table():
    """plan §五 ③-C2 读数表逐行在场(⛔ 少一项都不行 —— 尤其那个 🔴 分母)。"""
    keys = set(cm.CORE_METRIC_KEYS)
    assert keys == {
        "industry_member_count", "industry_rs_ranked_count_20d",
        "industry_rs_rank_20d", "industry_rs_pct_20d", "industry_ret_rank_1d",
        "consec_limit_up_days", "industry_amount_share",
        "cluster_rs_rank", "cluster_amount_share", "cluster_size",
    }
    assert len(cm.CORE_METRIC_KEYS) == len(keys)        # ⛔ 键名不许重复
