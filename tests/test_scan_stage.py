"""行业题材阶段六态状态机表 `industry_stage_daily` 单测(plan §五 V2-④b,K7 需求
1b)。

覆盖:①`decide_stage()` 六态判据(五态各一条正例 + persist 边界 1/3/4 + 涨停家数
边界 0/1 + 强度日判据缺数 / 涨停家数缺数 / persist 反常三类"缺数不猜");②近 N 日
回看窗口边界(`_load_recent_strength_flags`,T-2/T-3/T-5/T-6 四个精确边界);
③`_limit_up_counts_for_day` 三态(分区缺失 / 分区存在但零命中 / 有命中,及行业映射
缺失);④端到端叙事(真实价格 + 真实 `refresh_industry_strength` 写入路径产出
ignition→fermentation→overheat→divergence→ebb→none 完整生命周期,含一个未达标
"样本不足"行业落 `stage='none'` + 缺数原因);⑤"stage='none'" 与"表里没这一行"
两种"没有"分开;⑥确定性(同日两跑逐字节相同,排除 computed_at);⑦三路等价
(全量批算 ≡ 逐日 refresh ≡ 落表读回);⑧新鲜度三态;⑨读侧口径指纹漂移视同缺行;
⑩`verify_industry_stage` 三项自检(绿 + 三项各自的红);⑪守门:`industry_strength_
daily` 零改动 + 单日 refresh 不依赖其它年份分区(结构性防全历史扫描)。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from neckline.db import connection
from neckline.report.industry_strength_store import TABLE as STRENGTH_TABLE
from neckline.scan import stage
from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_industry_strength,
    write_daily_fixture,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# ① decide_stage() 六态判据:五态正例 + 边界 + 三类缺数
# ══════════════════════════════════════════════════════════════════════════

def _decide(**kw):
    kw.setdefault("recent_divergence_has_strength", False)
    kw.setdefault("recent_ebb_has_strength", False)
    return stage.decide_stage(**kw)


@pytest.mark.parametrize(
    "persist,expected",
    [(1, stage.IGNITION), (2, stage.FERMENTATION), (3, stage.FERMENTATION),
     (4, stage.OVERHEAT), (10, stage.OVERHEAT)],
)
def test_strength_day_persist_boundaries(persist, expected):
    """正例 + persist 边界:1→ignition,2/3→fermentation(上边界 3),4/10→overheat
    (下边界 4)。"""
    got, _ = _decide(is_strength_day=True, persist_days=persist, limit_up_count=None)
    assert got == expected


def test_strength_day_persist_zero_is_contradiction_insurance():
    """强度日=True 理论上 persist 恒 >=1(`next_persist_days` 语义);若源数据反常
    读到 0(不该发生),按缺数处理,不猜属于哪一态。"""
    got, reason = _decide(is_strength_day=True, persist_days=0, limit_up_count=None)
    assert got == stage.NONE_STAGE
    assert "反常" in reason


def test_strength_day_persist_missing_is_contradiction_insurance():
    got, reason = _decide(is_strength_day=True, persist_days=None, limit_up_count=None)
    assert got == stage.NONE_STAGE
    assert "反常" in reason


def test_divergence_positive_example():
    got, reason = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=2,
        recent_divergence_has_strength=True, recent_ebb_has_strength=True,
    )
    assert got == stage.DIVERGENCE
    assert "分歧回调" not in reason  # reason 是判据说明,不是中文态名复读
    assert "涨停 2 家" in reason


def test_ebb_positive_example():
    got, reason = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=0,
        recent_divergence_has_strength=False, recent_ebb_has_strength=True,
    )
    assert got == stage.EBB
    assert "零涨停" in reason


def test_none_positive_example_evaluated_and_genuinely_no_theme():
    """真正"评估完毕、确实无题材"的 none(不是缺数)。"""
    got, reason = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=5,
        recent_divergence_has_strength=False, recent_ebb_has_strength=False,
    )
    assert got == stage.NONE_STAGE
    assert "不满足分歧回调/退潮条件" in reason


def test_limit_up_count_boundary_zero_blocks_divergence_falls_to_ebb():
    """涨停家数边界:0 不满足 divergence(需 >=1),若同时满足 ebb 条件(近5日有强度
    且 ==0)则落 ebb——这正是①优先级从上往下、②divergence 检查失败后仍继续往下
    走的直接证明。"""
    got, _ = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=0,
        recent_divergence_has_strength=True, recent_ebb_has_strength=True,
    )
    assert got == stage.EBB


def test_limit_up_count_boundary_one_fires_divergence():
    got, _ = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=1,
        recent_divergence_has_strength=True, recent_ebb_has_strength=True,
    )
    assert got == stage.DIVERGENCE


def test_limit_up_count_one_does_not_satisfy_ebb_when_divergence_window_closed():
    """涨停家数=1 时 ebb 条件(==0)必然不满足;若 divergence 窗口也未命中,落 none。"""
    got, _ = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=1,
        recent_divergence_has_strength=False, recent_ebb_has_strength=True,
    )
    assert got == stage.NONE_STAGE


def test_limit_up_count_missing_is_honest_none_not_guessed():
    """涨停家数算不出(`limit_derived` 当日分区缺失)→ none,且 reason 点名"算不出"
    不是"零涨停"(两者语义不同,0 是真值)。"""
    got, reason = _decide(
        is_strength_day=False, persist_days=0, limit_up_count=None,
        recent_divergence_has_strength=True, recent_ebb_has_strength=True,
    )
    assert got == stage.NONE_STAGE
    assert "算不出" in reason and "不猜" in reason


def test_strength_day_judgment_missing_is_honest_none():
    """强度日判据本身缺数(`industry_strength_daily` 当日无该行业评级/无该行)→
    none,reason 点名"缺数",不是"不满足分歧回调/退潮条件"的同一句话。"""
    got, reason = _decide(
        is_strength_day=None, persist_days=None, limit_up_count=5,
        recent_divergence_has_strength=True, recent_ebb_has_strength=True, member_count=3,
    )
    assert got == stage.NONE_STAGE
    assert "缺数" in reason and "member_count=3" in reason


def test_six_codes_are_english_and_labels_cover_all():
    """库列值一律英文码,中文映射唯一源 = `STAGE_LABELS`,六态齐全。"""
    assert set(stage.STAGE_ORDER) == {
        stage.IGNITION, stage.FERMENTATION, stage.OVERHEAT,
        stage.DIVERGENCE, stage.EBB, stage.NONE_STAGE,
    }
    assert set(stage.STAGE_LABELS) == set(stage.STAGE_ORDER)
    assert all(code.isascii() and code.islower() for code in stage.STAGE_ORDER)


# ══════════════════════════════════════════════════════════════════════════
# ② 近 N 日回看窗口边界(读本表自己的历史)
# ══════════════════════════════════════════════════════════════════════════

def _insert_raw_stage_row(env, trade_date: date, industry: str, is_strength_day: bool) -> None:
    with connection(env.db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO industry_stage_daily "
            "(trade_date, industry, stage, is_strength_day, persist_days, limit_up_count, "
            " member_count, stage_reason, spec_fingerprint, computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                trade_date.strftime("%Y%m%d"), industry,
                stage.IGNITION if is_strength_day else stage.NONE_STAGE,
                1 if is_strength_day else 0, 1 if is_strength_day else 0, 0, 10,
                "fixture-seed", stage.SPEC_FINGERPRINT, "2024-01-01T00:00:00+00:00",
            ),
        )


def test_recent_flags_t2_boundary_in_both_windows(isolated_env):
    env = isolated_env
    days = business_days(date(2024, 1, 2), 10)
    insert_trade_cal(env, days)
    t = days[7]
    for d in days[:7]:
        _insert_raw_stage_row(env, d, "甲行业", is_strength_day=False)
    _insert_raw_stage_row(env, days[5], "甲行业", is_strength_day=True)   # T-2
    flags = stage._load_recent_strength_flags(["甲行业"], t, db_path=env.db_path)
    assert flags["甲行业"] == (True, True)


def test_recent_flags_t3_boundary_only_in_ebb_window(isolated_env):
    env = isolated_env
    days = business_days(date(2024, 1, 2), 10)
    insert_trade_cal(env, days)
    t = days[7]
    for d in days[:7]:
        _insert_raw_stage_row(env, d, "甲行业", is_strength_day=False)
    _insert_raw_stage_row(env, days[4], "甲行业", is_strength_day=True)   # T-3
    flags = stage._load_recent_strength_flags(["甲行业"], t, db_path=env.db_path)
    assert flags["甲行业"] == (False, True)


def test_recent_flags_t5_boundary_in_ebb_window(isolated_env):
    env = isolated_env
    days = business_days(date(2024, 1, 2), 10)
    insert_trade_cal(env, days)
    t = days[7]
    for d in days[:7]:
        _insert_raw_stage_row(env, d, "甲行业", is_strength_day=False)
    _insert_raw_stage_row(env, days[2], "甲行业", is_strength_day=True)   # T-5(恰好边界)
    flags = stage._load_recent_strength_flags(["甲行业"], t, db_path=env.db_path)
    assert flags["甲行业"] == (False, True)


def test_recent_flags_t6_boundary_outside_both_windows(isolated_env):
    env = isolated_env
    days = business_days(date(2024, 1, 2), 10)
    insert_trade_cal(env, days)
    t = days[7]
    for d in days[:7]:
        _insert_raw_stage_row(env, d, "甲行业", is_strength_day=False)
    _insert_raw_stage_row(env, days[1], "甲行业", is_strength_day=True)   # T-6(刚好出窗)
    flags = stage._load_recent_strength_flags(["甲行业"], t, db_path=env.db_path)
    assert flags["甲行业"] == (False, False)


def test_recent_flags_missing_history_rows_default_to_false_not_crash(isolated_env):
    """本表自己完全没有历史行(第一次 bootstrap 的最早几天)→ 两个旗标一律 False,
    不报错(存在性证据缺席按"不贡献"处理,不是全知)。"""
    env = isolated_env
    days = business_days(date(2024, 1, 2), 10)
    insert_trade_cal(env, days)
    flags = stage._load_recent_strength_flags(["甲行业", "乙行业"], days[7], db_path=env.db_path)
    assert flags == {"甲行业": (False, False), "乙行业": (False, False)}


# ══════════════════════════════════════════════════════════════════════════
# ③ `_limit_up_counts_for_day` 三态
# ══════════════════════════════════════════════════════════════════════════

def _limit_row(code: str, is_limit_up: bool) -> dict:
    return {
        "ts_code": code, "board": "MAIN", "status": "limit_up" if is_limit_up else None,
        "limit_pct": 0.10, "limit_up_price": 11.0, "limit_down_price": 9.0,
        "is_limit_up": is_limit_up, "is_limit_down": False, "is_zaban": False,
        "consec_limit_up_days": 1 if is_limit_up else 0,
    }


D0 = date(2024, 3, 4)


def test_limit_up_counts_file_missing_is_none(isolated_env):
    env = isolated_env
    industry_of = {"600001.SH": "半导体"}
    assert stage._limit_up_counts_for_day(D0, industry_of, env.parquet_dir) is None


def test_limit_up_counts_empty_industry_map_is_none(isolated_env):
    env = isolated_env
    write_daily_fixture(env, "limit_derived", D0, [_limit_row("600001.SH", True)])
    assert stage._limit_up_counts_for_day(D0, {}, env.parquet_dir) is None


def test_limit_up_counts_file_present_zero_hits_is_empty_dict(isolated_env):
    env = isolated_env
    write_daily_fixture(env, "limit_derived", D0, [_limit_row("600001.SH", False)])
    industry_of = {"600001.SH": "半导体"}
    counts = stage._limit_up_counts_for_day(D0, industry_of, env.parquet_dir)
    assert counts == {}
    # 调用方按 .get(industry, 0) 取零涨停,不是缺数
    assert counts.get("半导体", 0) == 0


def test_limit_up_counts_real_hits_grouped_by_industry(isolated_env):
    env = isolated_env
    write_daily_fixture(env, "limit_derived", D0, [
        _limit_row("600001.SH", True), _limit_row("600002.SH", True),
        _limit_row("600003.SH", False), _limit_row("600004.SH", True),
    ])
    industry_of = {"600001.SH": "半导体", "600002.SH": "半导体", "600003.SH": "半导体", "600004.SH": "白酒"}
    counts = stage._limit_up_counts_for_day(D0, industry_of, env.parquet_dir)
    assert counts == {"半导体": 2, "白酒": 1}


# ══════════════════════════════════════════════════════════════════════════
# ④ 端到端叙事:真实价格 + 真实 industry_strength_daily 写入路径 → 完整生命周期
# ══════════════════════════════════════════════════════════════════════════

def _seed_lifecycle_market(env, n_days: int = 12, start: date = date(2024, 1, 2)) -> List[date]:
    """`甲行业`(6 只)day0~3 连续 4 天强势(ignition→fermentation→fermentation→
    overheat),此后持续平淡;`乙行业`(6 只)/`丙行业`(6 只)全程平淡当基准;
    `样本不足行业`(3 只,< `_MIN_MEMBERS`)全程"上涨最多"但因成员不足永不参与
    强度评级——覆盖"strength 判据缺数"端到端场景。"""
    import polars as pl

    dates = business_days(start, n_days)
    insert_trade_cal(env, dates)
    scripts = {
        "甲行业": [0.08 if i < 4 else 0.0 for i in range(n_days)],
        "乙行业": [0.002] * n_days,
        "丙行业": [0.001] * n_days,
        "样本不足行业": [0.05] * n_days,
    }
    members = {"甲行业": 6, "乙行业": 6, "丙行业": 6, "样本不足行业": 3}
    codes: List[tuple] = []
    idx = 0
    for ind, cnt in members.items():
        for _ in range(cnt):
            codes.append((f"6{idx:05d}.SH", ind))
            idx += 1
    closes = {c: [10.0] for c, _ in codes}
    for i in range(1, n_days):
        for c, ind in codes:
            closes[c].append(closes[c][-1] * (1 + scripts[ind][i]))
    for i, d in enumerate(dates):
        rows = []
        for c, ind in codes:
            cur = closes[c][i]
            pre = closes[c][i - 1] if i > 0 else cur / (1 + scripts[ind][0])
            rows.append({"ts_code": c, "open": cur, "high": cur, "low": cur, "close": cur,
                         "pre_close": pre, "vol": 100000.0, "amount": 30000.0})
        write_daily_fixture(env, "daily", d, rows)
    insert_stock_basic(env, [
        {"ts_code": c, "industry": ind, "list_date": dates[0] - timedelta(days=800)}
        for c, ind in codes
    ])
    return dates


def _seed_limit_derived_for_lifecycle(env, dates: List[date], limit_up_days: Dict[int, List[str]]) -> None:
    """`limit_up_days = {day_index: [ts_code,...]}`;其余日子照样落一个"零涨停"占位
    行(区别于"分区不存在"—— ④b 需要能区分"文件缺失"与"文件存在但零命中")。"""
    placeholder = "699999.SH"
    for i, d in enumerate(dates):
        hits = limit_up_days.get(i, [])
        rows = [_limit_row(c, True) for c in hits] or [_limit_row(placeholder, False)]
        if hits:
            rows.append(_limit_row(placeholder, False))
        write_daily_fixture(env, "limit_derived", d, rows)


def test_end_to_end_lifecycle_ignition_through_none(isolated_env):
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=12)
    seed_industry_strength(env, dates)

    # 甲行业最后一个强度日 = dates[3](overheat)。dates[4]/[5] 落 divergence 窗口,
    # dates[6..8] 落 ebb 窗口但出 divergence 窗口,dates[9]+ 两个窗口皆出。
    hit_code = "600000.SH"  # 甲行业第一只(idx=0)
    _seed_limit_derived_for_lifecycle(env, dates, {4: [hit_code]})   # 仅 day4 有涨停

    stats = stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["missing_source"] == 0
    assert stats["rows"] == len(dates) * 4   # 4 个行业(含样本不足行业)× 12 天

    def stage_of(day_idx: int, industry: str = "甲行业") -> str:
        with connection(env.db_path) as conn:
            row = conn.execute(
                "SELECT stage FROM industry_stage_daily WHERE trade_date=? AND industry=?",
                (dates[day_idx].strftime("%Y%m%d"), industry),
            ).fetchone()
        assert row is not None, f"{industry} @ day{day_idx} 缺行"
        return row[0]

    assert stage_of(0) == stage.IGNITION
    assert stage_of(1) == stage.FERMENTATION
    assert stage_of(2) == stage.FERMENTATION
    assert stage_of(3) == stage.OVERHEAT
    assert stage_of(4) == stage.DIVERGENCE     # 近2日有强度(day3)且当日涨停>=1
    assert stage_of(5) == stage.EBB            # 近2日无强度(day3 已出窗),近5日仍有,当日零涨停
    assert stage_of(6) == stage.EBB
    assert stage_of(7) == stage.EBB
    assert stage_of(8) == stage.EBB            # day3 恰好是 day8 的 T-5,边界仍在窗内
    assert stage_of(9) == stage.NONE_STAGE     # day3 已出 5 日窗

    # 样本不足行业(3 只,<_MIN_MEMBERS):自始至终 stage='none',reason 点名"缺数"
    # 而不是"不满足分歧回调/退潮"——两种"none"必须能从 reason 分开。
    with connection(env.db_path) as conn:
        thin_rows = conn.execute(
            "SELECT stage, stage_reason FROM industry_stage_daily WHERE industry=?",
            ("样本不足行业",),
        ).fetchall()
    assert len(thin_rows) == len(dates)
    assert all(r[0] == stage.NONE_STAGE for r in thin_rows)
    assert all("缺数" in r[1] for r in thin_rows)


# ══════════════════════════════════════════════════════════════════════════
# ⑤ "stage='none'" 与"表里没这一行"两种"没有"分开
# ══════════════════════════════════════════════════════════════════════════

def test_missing_source_day_yields_no_rows_not_fabricated_none(isolated_env):
    """`industry_strength_daily` 当日一行都没有 → `industry_stage_daily` 当日也
    一行都不落(真缺行),**不是**给每个行业都灌一行 stage='none'。"""
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=6)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {})

    # 人为抹掉某一天的 industry_strength_daily(模拟该表当日更新失败)
    broken_day = dates[3].strftime("%Y%m%d")
    with connection(env.db_path) as conn:
        conn.execute(f"DELETE FROM {STRENGTH_TABLE} WHERE trade_date=?", (broken_day,))

    stats = stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["missing_source"] == 1

    empty = stage.load_industry_stage(dates[3], db_path=env.db_path)
    assert empty.is_empty()   # 真缺行:不是"查得到但全是 none"

    present = stage.load_industry_stage(dates[2], db_path=env.db_path)
    assert not present.is_empty()   # 相邻正常日照样有行(含可能的 'none' 行)


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 确定性 + ⑦ 三路等价
# ══════════════════════════════════════════════════════════════════════════

def _table_rows_excl_computed_at(db_path) -> List[tuple]:
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, industry, stage, is_strength_day, persist_days, limit_up_count, "
            f"member_count, stage_reason, spec_fingerprint FROM {stage.TABLE} "
            f"ORDER BY trade_date, industry"
        ).fetchall()
    return rows


def test_refresh_is_deterministic_same_day_rerun(isolated_env):
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=6)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {2: ["600000.SH"]})

    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    first = _table_rows_excl_computed_at(env.db_path)
    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    second = _table_rows_excl_computed_at(env.db_path)
    assert first == second and len(first) > 0


def test_three_way_equivalence_batch_vs_daily_vs_readback(isolated_env):
    """**核心不变式**:①一次性传全部交易日(模拟 bootstrap/批量)②逐日单独调用
    ③读回—— 三者落库结果逐位相同(排除 computed_at)。"""
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=10)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {4: ["600000.SH"], 7: ["600000.SH", "600001.SH"]})

    # 路径①:批量一次性
    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    batch_rows = _table_rows_excl_computed_at(env.db_path)

    with connection(env.db_path) as conn:
        conn.execute(f"DELETE FROM {stage.TABLE}")

    # 路径②:逐日单独调用(升序)
    for d in dates:
        stage.refresh_industry_stage([d], db_path=env.db_path, parquet_dir=env.parquet_dir)
    daily_rows = _table_rows_excl_computed_at(env.db_path)

    # 路径③:读回(load_industry_stage 逐日再拼一遍,证明读侧与直接查表一致)
    import polars as pl
    readback_rows = []
    for d in dates:
        df = stage.load_industry_stage(d, db_path=env.db_path)
        for r in df.sort("industry").iter_rows(named=True):
            readback_rows.append((
                r["trade_date"], r["industry"], r["stage"], r["is_strength_day"],
                r["persist_days"], r["limit_up_count"], r["member_count"],
                r["stage_reason"], r["spec_fingerprint"],
            ))

    assert len(batch_rows) > 0
    assert batch_rows == daily_rows == readback_rows


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 新鲜度三态
# ══════════════════════════════════════════════════════════════════════════

def test_freshness_empty_table_is_unavailable_and_stale(isolated_env):
    f = stage.industry_stage_status(date(2024, 1, 10), db_path=isolated_env.db_path)
    assert f.lag_days == stage.INDUSTRY_STAGE_LAG_UNKNOWN and f.unavailable and f.stale
    assert f.to_public_dict() == {
        "industryStageDate": None, "industryStageLagDays": -1, "industryStageStale": True,
    }


def test_freshness_fresh_and_stale_no_tolerance(isolated_env):
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=6)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {})
    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)

    fresh = stage.industry_stage_status(dates[-1], db_path=env.db_path)
    assert fresh.lag_days == 0 and fresh.stale is False and fresh.note() == ""

    with connection(env.db_path) as conn:
        conn.execute(f"DELETE FROM {stage.TABLE} WHERE trade_date=?", (dates[-1].strftime("%Y%m%d"),))
    lagged = stage.industry_stage_status(dates[-1], db_path=env.db_path)
    assert lagged.lag_days == 1 and lagged.stale is True   # 落后 1 天就 stale,零容忍
    assert "未就绪" in lagged.note()

    # 契约:三键,`industryStageDate` 缺省发 null 不是空串
    assert set(fresh.to_public_dict()) == {"industryStageDate", "industryStageLagDays", "industryStageStale"}


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 读侧口径指纹漂移视同缺行
# ══════════════════════════════════════════════════════════════════════════

def test_load_treats_stale_fingerprint_as_missing_with_warning(isolated_env, caplog):
    import logging

    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=6)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {})
    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert not stage.load_industry_stage(dates[-1], db_path=env.db_path).is_empty()

    with connection(env.db_path) as conn:
        conn.execute(f"UPDATE {stage.TABLE} SET spec_fingerprint='q=0.7|stale'")

    with caplog.at_level(logging.WARNING, logger="neckline.scan.stage"):
        out = stage.load_industry_stage(dates[-1], db_path=env.db_path)
    assert out.is_empty()
    assert "口径已变更" in caplog.text and "bootstrap" in caplog.text


# ══════════════════════════════════════════════════════════════════════════
# ⑩ verify_industry_stage 三项自检
# ══════════════════════════════════════════════════════════════════════════

def _seed_verify_baseline(env, n_days: int = 8):
    dates = _seed_lifecycle_market(env, n_days=n_days)
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {4: ["600000.SH"]})
    stage.refresh_industry_stage(dates, db_path=env.db_path, parquet_dir=env.parquet_dir)
    return dates


def test_verify_all_green_after_refresh(isolated_env):
    env = isolated_env
    dates = _seed_verify_baseline(env)
    res = stage.verify_industry_stage(dates[0], dates[-1], db_path=env.db_path)
    assert res["ok"] is True
    assert res["missing_days"] == [] and res["extra_days"] == []
    assert res["self_consistency_errors"] == [] and res["bad_fingerprints"] == []
    assert res["days"] == len(dates)


def test_verify_catches_trading_day_hole(isolated_env):
    env = isolated_env
    dates = _seed_verify_baseline(env)
    with connection(env.db_path) as conn:
        conn.execute(f"DELETE FROM {stage.TABLE} WHERE trade_date=?", (dates[3].strftime("%Y%m%d"),))
    res = stage.verify_industry_stage(dates[0], dates[-1], db_path=env.db_path)
    assert res["ok"] is False
    assert res["missing_days"] == [dates[3].strftime("%Y%m%d")]


def test_verify_catches_self_consistency_violation(isolated_env):
    env = isolated_env
    dates = _seed_verify_baseline(env)
    with connection(env.db_path) as conn:
        conn.execute(
            f"UPDATE {stage.TABLE} SET stage='overheat' WHERE trade_date=? AND industry='甲行业'",
            (dates[0].strftime("%Y%m%d"),),   # day0 真实是 ignition(persist=1),伪造成 overheat
        )
    res = stage.verify_industry_stage(dates[0], dates[-1], db_path=env.db_path)
    assert res["ok"] is False
    assert any("甲行业" in e for e in res["self_consistency_errors"])


def test_verify_catches_fingerprint_drift(isolated_env):
    env = isolated_env
    dates = _seed_verify_baseline(env)
    with connection(env.db_path) as conn:
        conn.execute(f"UPDATE {stage.TABLE} SET spec_fingerprint='q=0.9|drift' WHERE trade_date=?",
                     (dates[0].strftime("%Y%m%d"),))
    res = stage.verify_industry_stage(dates[0], dates[-1], db_path=env.db_path)
    assert res["ok"] is False
    assert res["bad_fingerprints"] == ["q=0.9|drift"]


# ══════════════════════════════════════════════════════════════════════════
# ⑪ 守门:industry_strength_daily 零改动 + 单日 refresh 不扫其它年份分区
# ══════════════════════════════════════════════════════════════════════════

def test_stage_module_never_writes_industry_strength_daily():
    """本块只读 `industry_strength_daily`,绝不写它(模块 docstring 明文)。纯文本
    扫描(同 `test_v2_schema_guard.py`「冻结」一节体例,足够且直接——不存在正当
    的 INSERT/UPDATE/DELETE 用法会被误伤)。"""
    text = (_PROJECT_ROOT / "neckline" / "scan" / "stage.py").read_text(encoding="utf-8")
    for forbidden in ("INSERT INTO industry_strength_daily", "UPDATE industry_strength_daily",
                       "DELETE FROM industry_strength_daily"):
        assert forbidden not in text


def test_single_day_refresh_does_not_need_other_years_partitions(isolated_env):
    """结构性防全历史扫描:目标交易日所在年份**之外**的 `limit_derived`/`daily`
    分区目录整个不存在时,单日 refresh 仍应正常工作(不依赖跨年份数据)。"""
    env = isolated_env
    dates = _seed_lifecycle_market(env, n_days=3, start=date(2026, 1, 5))
    seed_industry_strength(env, dates)
    _seed_limit_derived_for_lifecycle(env, dates, {})

    # 确认只有 2026 年目录存在(本测试的构造前提,不是断言 SUT 行为)
    assert sorted(p.name for p in (env.parquet_dir / "limit_derived").glob("year=*")) == ["year=2026"]

    stats = stage.refresh_industry_stage([dates[-1]], db_path=env.db_path, parquet_dir=env.parquet_dir)
    assert stats["rows"] > 0 and stats["missing_source"] == 0
