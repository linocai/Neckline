"""行业强度预计算表 `industry_strength_daily` 单测(plan §五 v1.4-⑩ 验收,§七 P0-23)。

覆盖:①**守门单测·禁用扫描**(四个在线文件不出现两个现算入口 + 日更路径真的不走
`scan_parquet`);②**三路等价**(全量算 ≡ 逐日递推 ≡ 落表读回,逐位相等)—— 这是
「表只是物化,不是第二套判据」的机器证明;③幂等 + 补跑自动向后延;④`NULL ≠ 0` 三列
语义;⑤口径指纹不匹配 → 视同缺行 + WARNING;⑥`verify` 三项自检(绿 + 每一项各自的红);
⑦bootstrap 两遍法与逐日路径逐位一致 + ⑩-D 退路(只回填最近 N 个交易日);⑧新鲜度三态
与 `dataFreshness` 三键契约;⑨**保险丝四态**(报告降级不崩且可复现 / 信息卡如实缺省 /
分歧线新 reason / 问询台 evidence 明说不可得)。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import polars as pl
import pytest

from neckline.report import industry_strength as ist
from neckline.report import industry_strength_store as store
from tests.conftest import (
    business_days,
    insert_stock_basic,
    insert_trade_cal,
    seed_industry_strength,
    write_daily_fixture,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ————————————————————————————————————————————————————————————————
# ① 守门单测:四个在线文件禁出现现算入口(plan §五 v1.4-⑩-B)
# ————————————————————————————————————————————————————————————————

_ONLINE_FILES = [
    "neckline/report/pipeline.py",
    "neckline/report/info_card.py",
    "neckline/report/intel_candidates.py",
    "neckline/api/inquiry.py",
]
_BANNED = ["compute_industry_strength", "industry_median_return_series"]


@pytest.mark.parametrize("rel", _ONLINE_FILES)
def test_online_paths_never_reference_full_scan_entrypoints(rel):
    """§七 P0-23 的**结构性防复发**:两个现算入口各自对 `daily` 做 `scan_parquet`
    (前者全历史 784 万行),在生产 2 vCPU/1.6G 上跑不完 —— 16:35 报告主链 / 信息卡端点 /
    问询台三处曾全部中招。本断言把「在线路径只读表」这条纪律钉死在文件层面:四个在线
    文件里**这两个名字一次都不许出现**(连注释里点名都不行 —— 照 ③-C `_SORT_KEY_INPUTS`
    白名单单测的体例,判据要机器可查,不留人肉裁量空间)。

    要现算?去 `report/industry_strength.py`(离线 / bootstrap / 对拍),别把它接回在线。"""
    text = (_PROJECT_ROOT / rel).read_text(encoding="utf-8")
    for name in _BANNED:
        assert name not in text, f"{rel} 出现了被禁的现算入口 {name}(P0-23:在线路径只许读表)"


def test_refresh_never_scans_all_partitions(isolated_env, monkeypatch):
    """日更**只读当日那一个分区**,绝不 `scan_parquet` 全 glob(1500+ 个 footer = P0-23
    的病根之一)。做法:把 `pl.scan_parquet` 换成一颗地雷 —— 日更路径一旦碰它就炸。"""
    dates = _seed_two_industries(isolated_env, n_days=3)

    def _mine(*a, **kw):
        raise AssertionError("日更路径不许调 scan_parquet(P0-23:只读当日一个分区)")

    monkeypatch.setattr(pl, "scan_parquet", _mine)
    stats = store.refresh_industry_strength(
        [dates[-1]], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    assert stats["days"] == 1 and stats["rows"] > 0


# ————————————————————————————————————————————————————————————————
# 合成市场夹具
# ————————————————————————————————————————————————————————————————

def _seed_two_industries(env, n_days: int = 12, start: date = date(2024, 1, 2)) -> List[date]:
    """3 个达标行业(各 6 只)+ 1 个样本不足行业(3 只)。收益剧本刻意让强度日在时间上
    交替出现(streak 有断有续),这样「持续天数」这一维才真的被测到,不是全 0 或全递增。"""
    dates = business_days(start, n_days)
    insert_trade_cal(env, dates)
    # 剧本让**每天各行业中位数互不相等**(day 0 也给真实 `pre_close`,不留 ret≡0 的退化
    # 开局)。⚠ **这不再是三路等价断言的前提**(review 🟢-8 陈旧注释修正,2026-07-29):
    # 并列早已由 `_day_local_table` 的确定性 tie-break 定序(先 `median_ret` 降序、再
    # `industry` 升序,专测 `test_rank_tie_break_is_deterministic_regardless_of_row_order`),
    # 三路无论并不并列都一致。保留互不相等只是让剧本读起来一眼看清谁强谁弱。
    scripts = {
        "甲行业": [0.03 if (i % 3 == 0 and i < n_days - 6) else 0.001 for i in range(n_days)],
        "乙行业": [0.02 if i >= n_days - 6 else 0.002 for i in range(n_days)],    # 末 6 日连续强
        "丙行业": [0.0005] * n_days,                                              # 常年最弱
        "样本不足行业": [0.04] * n_days,                                          # 涨最多但成员只有 3 只
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
    # day-0 各行业剧本收益互不相同(剧本可读性,不是等价断言的前提 —— 见上方注释)。
    assert len(set(scripts[i][0] for i in scripts)) == len(scripts)
    for i, d in enumerate(dates):
        rows = []
        for c, ind in codes:
            cur = closes[c][i]
            # day 0 也给真实 `pre_close`(按该行业当日剧本反推),不让首日 ret 全为 0。
            pre = closes[c][i - 1] if i > 0 else cur / (1 + scripts[ind][0])
            rows.append({"ts_code": c, "open": cur, "high": cur, "low": cur, "close": cur,
                         "pre_close": pre, "vol": 100000.0, "amount": 30000.0})
        write_daily_fixture(env, "daily", d, rows)
    insert_stock_basic(env, [
        {"ts_code": c, "industry": ind, "list_date": dates[0] - timedelta(days=800)}
        for c, ind in codes
    ])
    return dates


def _table_rows(db_path: Path) -> List[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM {store.TABLE} ORDER BY trade_date, industry"
        )]
    finally:
        conn.close()


def _full_panel(env, end: date) -> pl.DataFrame:
    """离线参考路径的输入面板(全量 ret_1d + 行业)。"""
    ret = ist._load_ret1d_panel(end, env.parquet_dir)
    industry_of = ist.load_industry_map(env.db_path)
    ind_map = pl.DataFrame({"ts_code": list(industry_of.keys()), "industry": list(industry_of.values())})
    return ret.join(ind_map, on="ts_code", how="inner")


# ————————————————————————————————————————————————————————————————
# ② 三路等价(表只是物化,不是第二套判据)
# ————————————————————————————————————————————————————————————————

def test_three_way_equivalence_full_vs_recurrence_vs_table(isolated_env):
    """**核心不变式**:①`_attach_persist(_day_local_table(panel))`(全量算)、②逐日调
    `next_persist_days` 递推、③`refresh_industry_strength` 落表后读回 —— 三者的
    `industry_rank` / `is_strength_day` / `persist_days` **逐位相等**。

    这三条路径分别是:①v1.4-② 时代的现算口径(锚点)、②日更增量的算法、③在线读侧看到
    的东西。三者相等 = 「预计算表只是缓存物化」这句话的机器证明;任何一条漂了,这个断言
    先炸,而不是等生产判据出错。"""
    dates = _seed_two_industries(isolated_env, n_days=12)

    # 路径①:全量算
    full = ist._attach_persist(ist._day_local_table(_full_panel(isolated_env, dates[-1]), ist._STRENGTH_QUANTILE))
    ref = {
        (r["trade_date"], r["industry"]): (r["industry_rank"], r["is_strength_day"], r["industry_persist_days"])
        for r in full.iter_rows(named=True)
    }

    # 路径②:逐日递推(纯 Python,只用当日量 + 上一评定日 streak)
    recurrence: Dict[tuple, tuple] = {}
    prev_by_industry: Dict[str, int] = {}
    for d in dates:
        day_local = ist._day_local_table(
            _full_panel(isolated_env, d).filter(pl.col("trade_date") == d), ist._STRENGTH_QUANTILE
        )
        for r in day_local.iter_rows(named=True):
            ind, is_str = r["industry"], r["is_strength_day"]
            val = ist.next_persist_days(prev_by_industry.get(ind), is_str)
            if val is not None:
                prev_by_industry[ind] = val
            recurrence[(d, ind)] = (r["industry_rank"], is_str, val)

    # 路径③:落表后读回
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    from_table = {
        (date(int(r["trade_date"][:4]), int(r["trade_date"][4:6]), int(r["trade_date"][6:])), r["industry"]):
        (r["industry_rank"], None if r["is_strength_day"] is None else bool(r["is_strength_day"]), r["persist_days"])
        for r in _table_rows(isolated_env.db_path)
    }

    assert set(ref) == set(recurrence) == set(from_table)
    assert len(ref) > 0
    for key in ref:
        assert ref[key] == recurrence[key] == from_table[key], f"三路不一致 @ {key}"
    # 熔断线:剧本确实产出了非平凡的 streak(有 >=2 天的连续强度日),不是空对空。
    assert max(v[2] or 0 for v in ref.values()) >= 2


def test_load_industry_strength_matches_offline_compute(isolated_env):
    """读侧返回集与 v1.4-② 现算入口**逐位同集**(只含 `industry_rank IS NOT NULL` 的行,
    按 rank 升序)—— 表切换对判据消费方完全透明。"""
    dates = _seed_two_industries(isolated_env, n_days=10)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)

    offline = ist.compute_industry_strength(
        dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    online = store.load_industry_strength(dates[-1], db_path=isolated_env.db_path)
    assert [s.industry for s in online] == [s.industry for s in offline]
    for a, b in zip(online, offline):
        assert a.industry_rank == b.industry_rank
        assert a.is_strength_day == b.is_strength_day
        assert a.persist_days == b.persist_days
        assert a.member_count == b.member_count
        assert a.median_ret == pytest.approx(b.median_ret, rel=1e-12)
    assert [s.industry_rank for s in online] == sorted(s.industry_rank for s in online)   # rank 升序


# ————————————————————————————————————————————————————————————————
# ③ 幂等 + 补跑自动向后延
# ————————————————————————————————————————————————————————————————

def test_refresh_is_idempotent_same_day_rerun(isolated_env):
    """同日重跑逐位相同,**不双计** streak(`prev` 查的是严格早于当日的行)。"""
    dates = _seed_two_industries(isolated_env, n_days=8)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    first = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]

    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    store.refresh_industry_strength([dates[-1]], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    second = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]
    assert first == second


def test_refresh_backfilling_history_extends_forward_to_table_max(isolated_env, caplog):
    """**补算历史日 D 时若库内存在 > D 的行**,那些行的 `persist_days` 会失真 → `refresh`
    自动把处理区间向后延到库内最大交易日。**不许静默只补一天。**

    造法:先整段跑一遍存基准,再把中间某天的行**抹成错的**(模拟"那天当时没算/算错"),
    然后只请求补那一天 —— 断言不仅那一天回正,**它之后每一天的 streak 也回正**。"""
    dates = _seed_two_industries(isolated_env, n_days=10)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    baseline = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]

    broken_day = dates[4].strftime("%Y%m%d")
    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(
            f"UPDATE {store.TABLE} SET is_strength_day=1, persist_days=99 WHERE trade_date>=?",
            (broken_day,),
        )
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.INFO, logger="neckline.report.industry_strength_store"):
        stats = store.refresh_industry_strength(
            [dates[4]], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
        )
    assert stats["days"] == len(dates) - 4                 # 4..末,全部重算(不是只补 1 天)
    assert "顺带重算至" in caplog.text
    after = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]
    assert after == baseline


# ————————————————————————————————————————————————————————————————
# ③b 向前补洞(v1.4 review 🟡-2):日更失败过一天以上 → 表 max 与目标日之间有缺口。
#     不补的话 `_prev_persist` 拿洞前那天当"昨天",streak **桥过缺口**(错数),而
#     `MAX(trade_date)` 照样是今天 → 新鲜度全绿 = 未披露的错数。
# ————————————————————————————————————————————————————————————————

def test_resolve_targets_fills_the_gap_forward(isolated_env):
    """判定线审计的原始探针:`_resolve_targets([0709], tbl_max=0704)` 从前只返回 `[0709]`
    (0707/0708 两个交易日的洞不进处理区间),现在必须把洞并进来。"""
    dates = business_days(date(2024, 1, 2), 8)
    insert_trade_cal(isolated_env, dates)
    got = store._resolve_targets([dates[5]], dates[2].strftime("%Y%m%d"))
    assert got == dates[3:6]                                    # 洞(3,4)+ 目标(5)
    # 反向(补历史)与无缺口两种既有形状不受影响
    assert store._resolve_targets([dates[5]], dates[4].strftime("%Y%m%d")) == [dates[5]]
    assert store._resolve_targets([dates[2]], dates[4].strftime("%Y%m%d")) == dates[2:5]


def test_refresh_fills_hole_and_streak_is_not_bridged(isolated_env, caplog):
    """**命门**:表停在 dates[5]、只请求补 dates[8] → 自动把 dates[6..7] 一起算。
    判据不是"补了几天",而是**整表与一次干净全量刷新逐位相同** —— streak 一旦桥过缺口,
    末段 `persist_days` 会整体偏小,这个逐位断言先炸。"""
    dates = _seed_two_industries(isolated_env, n_days=10)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    baseline = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]

    conn = sqlite3.connect(str(isolated_env.db_path))           # 造洞:抹掉 6..9 的行,表停在 5
    try:
        conn.execute(f"DELETE FROM {store.TABLE} WHERE trade_date>=?", (dates[6].strftime("%Y%m%d"),))
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.INFO, logger="neckline.report.industry_strength_store"):
        stats = store.refresh_industry_strength(
            [dates[8]], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
        )
    assert stats["days"] == 3 and stats["holes"] == []           # 6、7、8 三天都算了,补完无洞
    assert "缺口" in caplog.text
    got_rows = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]
    cutoff = dates[8].strftime("%Y%m%d")
    assert {r["trade_date"] for r in got_rows} >= {d.strftime("%Y%m%d") for d in dates[6:9]}
    assert got_rows == [r for r in baseline if r["trade_date"] <= cutoff]


def test_unfillable_hole_is_loud_and_not_reported_fresh(isolated_env, caplog):
    """**补不了就响亮失败**:洞那几天连 `daily` 分区都没有(补不动)→ ① `refresh` 返回
    `holes` 且打 ERROR 带补算命令;② **新鲜度不许报绿** —— `MAX(trade_date)` 是最新日、
    `lag_days == 0`,但 `industryStrengthStale` 必须是 `True`(错数不得冒充新鲜)。"""
    from neckline.data.market_data import day_file_path

    dates = _seed_two_industries(isolated_env, n_days=10)
    store.refresh_industry_strength(
        dates[:6], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    for d in (dates[6], dates[7]):                              # 分区消失 = 补不动
        day_file_path("daily", d, isolated_env.parquet_dir).unlink()

    with caplog.at_level(logging.ERROR, logger="neckline.report.industry_strength_store"):
        stats = store.refresh_industry_strength(
            [dates[8]], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
        )
    assert stats["holes"] == [dates[6].strftime("%Y%m%d"), dates[7].strftime("%Y%m%d")]
    assert "断口" in caplog.text and "scripts/industry_strength.py refresh" in caplog.text

    fresh = store.industry_strength_status(dates[8], db_path=isolated_env.db_path)
    assert fresh.lag_days == 0 and fresh.hole_days == 2
    assert fresh.stale is True, "有断口却报绿 = 拿错数冒充新鲜(review 🟡-2)"
    assert fresh.to_public_dict()["industryStrengthStale"] is True
    assert "断口" in fresh.note()
    # 契约不变:仍是三键,不因本修复多长出一个键(客户端已按三键解码)
    assert set(fresh.to_public_dict()) == {
        "industryStrengthDate", "industryStrengthLagDays", "industryStrengthStale"}


def test_table_tail_and_head_boundaries_are_not_holes(isolated_env):
    """**不许把边界当断口**(否则天天假警报):表尾还没落的今天 → 由 `lag_days` 如实披露;
    表头之前的远古 → 由保险丝披露。两者都不是「两头有数据、中间断一截」。"""
    dates = _seed_two_industries(isolated_env, n_days=8)
    store.refresh_industry_strength(
        dates[:5], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    fresh = store.industry_strength_status(dates[7], db_path=isolated_env.db_path)
    assert fresh.hole_days == 0 and fresh.lag_days == 3 and fresh.stale is True   # 落后,不是断口
    early = store.industry_strength_status(dates[0], db_path=isolated_env.db_path)
    assert early.hole_days == 0 and early.stale is False


# ————————————————————————————————————————————————————————————————
# ④ NULL ≠ 0 三列语义
# ————————————————————————————————————————————————————————————————

def test_thin_industry_lands_row_with_nulls_not_zeros(isolated_env):
    """落**全部**行业(`member_count >= 1`),未达标行业三列一律 **NULL**(「没评」),
    **不是 0**(「评了,没有」)。同一张表同时喂判据侧与 ④ 信息卡 60 日中位数序列。"""
    dates = _seed_two_industries(isolated_env, n_days=5)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    rows = [r for r in _table_rows(isolated_env.db_path) if r["trade_date"] == dates[-1].strftime("%Y%m%d")]
    by_ind = {r["industry"]: r for r in rows}
    assert set(by_ind) == {"甲行业", "乙行业", "丙行业", "样本不足行业"}     # 四个行业**都**落了行

    thin = by_ind["样本不足行业"]
    assert thin["member_count"] == 3
    assert thin["median_ret"] is not None                    # 中位数照算(信息卡分歧线要用)
    assert thin["industry_rank"] is None                     # 「没评」,不是 rank=0
    assert thin["is_strength_day"] is None
    assert thin["persist_days"] is None

    weak = by_ind["丙行业"]
    assert weak["industry_rank"] is not None                 # 「评了」
    assert weak["is_strength_day"] == 0                      # 「评了,不是强度日」
    assert weak["persist_days"] == 0                         # 0 ≠ NULL

    # 判据侧只看达标行(与现算返回集同集);信息卡序列侧看得到样本不足行业。
    assert "样本不足行业" not in {s.industry for s in store.load_industry_strength(dates[-1], db_path=isolated_env.db_path)}
    series = store.load_industry_median_series("样本不足行业", dates[0], dates[-1], db_path=isolated_env.db_path)
    assert len(series) == len(dates) and all(r["member_count"] == 3 for r in series)


def test_load_industry_median_series_matches_offline_reference(isolated_env):
    """表内 `median_ret` 与现算参考实现逐位一致(参考实现就是为了对拍才保留的)。"""
    dates = _seed_two_industries(isolated_env, n_days=6)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    offline = ist.industry_median_return_series(
        "甲行业", dates[0], dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    online = store.load_industry_median_series("甲行业", dates[0], dates[-1], db_path=isolated_env.db_path)
    assert [r["trade_date"] for r in online] == [r["trade_date"] for r in offline]
    for a, b in zip(online, offline):
        assert a["median_ret"] == pytest.approx(b["median_ret"], rel=1e-12)
        assert a["member_count"] == b["member_count"]


# ————————————————————————————————————————————————————————————————
# ⑤ 口径指纹
# ————————————————————————————————————————————————————————————————

def test_fingerprint_mismatch_is_treated_as_missing_with_warning(isolated_env, caplog):
    """口径指纹(`quantile`/`min_members`)与现行常量不等 → **视同缺行**(走保险丝)+
    WARNING「口径已变更,请重跑 bootstrap」。这条把「静默混着两种口径的行」变成一次
    响亮的降级。"""
    dates = _seed_two_industries(isolated_env, n_days=4)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    assert store.load_industry_strength(dates[-1], db_path=isolated_env.db_path)      # 改之前有行

    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"UPDATE {store.TABLE} SET quantile=0.70")     # 模拟"库里是旧口径算的"
        conn.commit()
    finally:
        conn.close()

    with caplog.at_level(logging.WARNING, logger="neckline.report.industry_strength_store"):
        out = store.load_industry_strength(dates[-1], db_path=isolated_env.db_path)
    assert out == []
    assert "口径已变更" in caplog.text and "bootstrap" in caplog.text


# ————————————————————————————————————————————————————————————————
# ⑥ verify 三项自检
# ————————————————————————————————————————————————————————————————

def test_verify_all_green_after_refresh(isolated_env):
    dates = _seed_two_industries(isolated_env, n_days=8)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    res = store.verify_industry_strength(dates[0], dates[-1], db_path=isolated_env.db_path)
    assert res["ok"] is True
    assert res["missing_days"] == [] and res["extra_days"] == []
    assert res["streak_mismatches"] == [] and res["bad_fingerprints"] == []
    assert res["days"] == len(dates)


def test_verify_catches_trading_day_hole(isolated_env):
    """①交易日无洞:表内 `trade_date` 集合必须等于 `trade_cal` 在该区间的交易日集合。"""
    dates = _seed_two_industries(isolated_env, n_days=8)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"DELETE FROM {store.TABLE} WHERE trade_date=?", (dates[3].strftime("%Y%m%d"),))
        conn.commit()
    finally:
        conn.close()
    res = store.verify_industry_strength(dates[0], dates[-1], db_path=isolated_env.db_path)
    assert res["ok"] is False
    assert res["missing_days"] == [dates[3].strftime("%Y%m%d")]


def test_verify_catches_streak_inconsistency(isolated_env):
    """②streak 自洽:由 `is_strength_day` 序列重算的连续天数必须等于库内 `persist_days`。"""
    dates = _seed_two_industries(isolated_env, n_days=8)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"UPDATE {store.TABLE} SET persist_days=42 WHERE trade_date=? AND industry='甲行业'",
                     (dates[-1].strftime("%Y%m%d"),))
        conn.commit()
    finally:
        conn.close()
    res = store.verify_industry_strength(dates[0], dates[-1], db_path=isolated_env.db_path)
    assert res["ok"] is False
    assert [(m["industry"], m["stored"]) for m in res["streak_mismatches"]] == [("甲行业", 42)]


def test_verify_catches_fingerprint_drift(isolated_env):
    """③口径指纹一致:全表 `quantile`/`min_members` 唯一且等于现行常量。"""
    dates = _seed_two_industries(isolated_env, n_days=5)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"UPDATE {store.TABLE} SET min_members=3 WHERE trade_date=?",
                     (dates[0].strftime("%Y%m%d"),))
        conn.commit()
    finally:
        conn.close()
    res = store.verify_industry_strength(dates[0], dates[-1], db_path=isolated_env.db_path)
    assert res["ok"] is False
    assert len(res["fingerprints"]) == 2 and res["bad_fingerprints"]


def test_verify_streak_uses_history_before_window(isolated_env):
    """②的历史依赖:窗口首日的 streak 依赖**更早**的历史,只看窗口会误判。断言窗口内
    的自检对一段"streak 已经跑到第 3 天"的开局照样绿(而不是把它当"应该从 1 开始")。"""
    dates = _seed_two_industries(isolated_env, n_days=12)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    res = store.verify_industry_strength(dates[-2], dates[-1], db_path=isolated_env.db_path)
    assert res["ok"] is True
    # 熔断线:窗口首日确实带着 >1 的 streak 进来(否则本用例证不到"用了窗口之前的历史")。
    rows = [r for r in _table_rows(isolated_env.db_path) if r["trade_date"] == dates[-2].strftime("%Y%m%d")]
    assert max((r["persist_days"] or 0) for r in rows) > 1


# ————————————————————————————————————————————————————————————————
# ⑦ bootstrap 两遍法 + ⑩-D 退路
# ————————————————————————————————————————————————————————————————

def test_bootstrap_two_pass_equals_daily_refresh_bit_for_bit(isolated_env):
    """**Pass1(按年块当日量)+ Pass2(纯表内 streak)** 与逐日增量路径**逐位一致**。
    这条锁的是:生产旁路 bootstrap 出来的表,与此后每天日更续上的表是同一个东西。"""
    dates = _seed_two_industries(isolated_env, n_days=12)

    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)
    by_daily = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]

    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"DELETE FROM {store.TABLE}")
        conn.commit()
    finally:
        conn.close()

    out = store.bootstrap_industry_strength(
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    assert out["years"] == [dates[0].year] and out["days"] == len(dates)
    by_bootstrap = [{k: v for k, v in r.items() if k != "computed_at"} for r in _table_rows(isolated_env.db_path)]
    assert by_bootstrap == by_daily


def test_bootstrap_pass1_only_leaves_persist_null(isolated_env):
    """Pass1 只落当日量,`persist_days` 留 **NULL**(不是 0)—— 「还没算」与「算了是 0」
    必须能分开;CLI 只跑 Pass1 时会 WARNING 提醒必须补跑 Pass2。"""
    dates = _seed_two_industries(isolated_env, n_days=6)
    store.bootstrap_pass1_year(
        dates[0].year, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    rows = _table_rows(isolated_env.db_path)
    assert rows and all(r["persist_days"] is None for r in rows)
    assert any(r["industry_rank"] is not None for r in rows)      # 当日量已经落好了

    store.bootstrap_pass2_streak(db_path=isolated_env.db_path)
    rated = [r for r in _table_rows(isolated_env.db_path) if r["is_strength_day"] is not None]
    assert rated and all(r["persist_days"] is not None for r in rated)


def test_bootstrap_recent_days_fallback_only_fills_window(isolated_env):
    """**⑩-D 退路**(首块超限时的写死方案):只回填最近 N 个交易日。早于起点的历史
    **表里就是没有**(→ 在线读侧走保险丝降级 + 显式标注),**不许静默按 0**。"""
    dates = _seed_two_industries(isolated_env, n_days=12)
    stats = store.bootstrap_recent_days(
        dates[-1], n=4, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path
    )
    assert stats["days"] == 4
    have = {r["trade_date"] for r in _table_rows(isolated_env.db_path)}
    assert have == {d.strftime("%Y%m%d") for d in dates[-4:]}
    # 起点之前的日子:读侧空列表(保险丝),不是 0 值行。
    assert store.load_industry_strength(dates[0], db_path=isolated_env.db_path) == []


# ————————————————————————————————————————————————————————————————
# ⑧ 新鲜度三态 + dataFreshness 三键契约
# ————————————————————————————————————————————————————————————————

def test_freshness_empty_table_is_unavailable_and_stale(isolated_env):
    """表空 → `lag_days = -1`(哨兵值,同 `SECTOR_LAG_UNKNOWN` 惯例)且 `stale=True`;
    公开契约里 `industryStrengthDate` 发 **null**(不是空串)。"""
    f = store.industry_strength_status(date(2024, 1, 10), db_path=isolated_env.db_path)
    assert f.lag_days == store.INDUSTRY_STRENGTH_LAG_UNKNOWN and f.unavailable and f.stale
    assert f.to_public_dict() == {
        "industryStrengthDate": None, "industryStrengthLagDays": -1, "industryStrengthStale": True,
    }
    assert f.latest_label() == "无数据"


def test_freshness_fresh_and_stale_no_tolerance(isolated_env):
    """**`lag_days > 0` 即 stale,不给容忍度**(与 `ths_daily` 结构性落后 1 日不同:
    行业强度用当日 `daily` 算,16:05 当天就该有)。"""
    dates = _seed_two_industries(isolated_env, n_days=6)
    store.refresh_industry_strength(dates, parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path)

    fresh = store.industry_strength_status(dates[-1], db_path=isolated_env.db_path)
    assert fresh.lag_days == 0 and fresh.stale is False and fresh.note() == ""
    assert fresh.to_public_dict() == {
        "industryStrengthDate": dates[-1].strftime("%Y%m%d"),
        "industryStrengthLagDays": 0, "industryStrengthStale": False,
    }

    conn = sqlite3.connect(str(isolated_env.db_path))
    try:
        conn.execute(f"DELETE FROM {store.TABLE} WHERE trade_date=?", (dates[-1].strftime("%Y%m%d"),))
        conn.commit()
    finally:
        conn.close()
    lagged = store.industry_strength_status(dates[-1], db_path=isolated_env.db_path)
    assert lagged.lag_days == 1 and lagged.stale is True         # 落后 1 天就 stale,零容忍
    assert "未就绪" in lagged.note()


def test_refresh_command_hint_is_single_source():
    """补算命令**原文**由单一函数给,日志里带的就是能直接敲的那一行。"""
    hint = store.refresh_command_hint(date(2026, 7, 28), date(2026, 7, 29))
    assert hint == "python scripts/industry_strength.py refresh --from 20260728 --to 20260729"


# ————————————————————————————————————————————————————————————————
# ⑨ 保险丝四态(plan §五 v1.4-⑩ 验收 ③)
# ————————————————————————————————————————————————————————————————

def test_fuse_report_degrades_without_crash_and_is_reproducible(isolated_env, monkeypatch):
    """保险丝态①:**表为空 → 报告照出、不崩,且同一批候选两次跑出同一序**(排序键①全部
    `None → +inf`,序退化成 `yellow_card_count → base_score → code`,**仍确定性可复现**)。
    同时:`dataFreshness` 里行业强度三键如实标未就绪,报告顶部有告警。"""
    from tests.conftest import seed_active_rule_v1, seed_synthetic_market
    import neckline.report.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "get_provider", lambda *a, **kw: None)
    dates = seed_synthetic_market(isolated_env, n_days=30, with_industry_strength=False)
    seed_active_rule_v1(isolated_env)

    b1 = pipeline_mod.build_report(
        dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
    )
    b2 = pipeline_mod.build_report(
        dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path, save=False,
    )
    assert [c.ts_code for c in b1.candidates] == [c.ts_code for c in b2.candidates]   # 可复现
    assert b1.industry_freshness.unavailable and b1.industry_freshness.stale
    assert "行业强度数据未就绪" in b1.markdown
    assert b1.industry_freshness.to_public_dict()["industryStrengthLagDays"] == -1


def test_fuse_a2_hard_cut_does_not_fire_when_table_missing(isolated_env):
    """保险丝态①之二:**降级方向 = 不拦(放行)**。A2(题材持续天数 ≥4)是 hard_cut,
    表缺行时 `stock_persist_days` 恒 0 → **不触发** —— 与 `intel_candidates` 既有
    「缺 DB 行 → 保守当 avoid_flag、不拦」同向(见项目 CLAUDE.md)。"""
    from neckline.report.industry_strength import industry_strength_lookup, stock_persist_days

    dates = _seed_two_industries(isolated_env, n_days=6)
    hot = industry_strength_lookup(store.load_industry_strength(dates[-1], db_path=isolated_env.db_path))
    assert hot == {}                                    # 表没喂
    industry_of = ist.load_industry_map(isolated_env.db_path)
    code = next(iter(industry_of))
    assert stock_persist_days(code, industry_of, hot) == 0      # 0 = A2 不触发 = 不拦


def test_fuse_info_card_snapshot_is_honestly_null_not_zero(isolated_env):
    """保险丝态②:信息卡 `industryRank`/`industryPersistDays` **如实缺省(null)**,
    **不写 0** —— 0 会被读成「评了、持续 0 天」(拿「没看」冒充「没有」)。"""
    from neckline.report import info_card as ic

    dates = _seed_two_industries(isolated_env, n_days=6)
    code = next(iter(ist.load_industry_map(isolated_env.db_path)))
    card = ic.build_info_card(
        dates[-1], code, k4_flags=[], news_domain_codes=set(), news_items=[],
        parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert card.snapshot.industry_rank is None
    assert card.snapshot.industry_persist_days is None                  # 不是 0
    assert card.snapshot.to_public_dict()["industryPersistDays"] is None
    # 分歧线走**新的第三档理由**,不许说成"行业样本不足"。
    assert card.industry_divergence_available is False
    assert "行业强度数据未就绪" in card.industry_divergence_unavailable_reason
    assert "样本不足" not in card.industry_divergence_unavailable_reason


def test_fuse_inquiry_evidence_states_data_not_available(isolated_env):
    """保险丝态③:问询台 `det.evidence` 必须**明说本次不可得**,
    **绝不静默按 0 输出「未命中 A2/B3」**(拿「没看」冒充「没有」)。"""
    from neckline.api import inquiry as inq
    from tests.conftest import seed_active_rule_v1

    dates = _seed_two_industries(isolated_env, n_days=6)
    seed_active_rule_v1(isolated_env)       # 无现役章程会在纪律核对处提前返回,材料根本不走到行业那一段
    code = next(iter(ist.load_industry_map(isolated_env.db_path)))
    det = inq.run_deterministic_checks(
        code, dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert det.industry_strength_unavailable
    joined = "".join(det.evidence)
    assert "行业强度数据未就绪" in joined
    assert "题材持续天数与 A2/B3 本次不可得" in joined


def test_inquiry_evidence_clean_when_table_ready(isolated_env):
    """反面:表喂上之后那条「不可得」告白**不该出现**(它是故障标记,不是常驻噪音)。"""
    from neckline.api import inquiry as inq
    from tests.conftest import seed_active_rule_v1

    dates = _seed_two_industries(isolated_env, n_days=6)
    seed_active_rule_v1(isolated_env)
    seed_industry_strength(isolated_env, dates)
    code = next(iter(ist.load_industry_map(isolated_env.db_path)))
    det = inq.run_deterministic_checks(
        code, dates[-1], parquet_dir=isolated_env.parquet_dir, db_path=isolated_env.db_path,
    )
    assert det.industry_strength_unavailable == ""
    assert "行业强度数据未就绪" not in "".join(det.evidence)
