"""申万 2021 版行业分类接入单测(V2.5.0 S2,PROJECT_PLAN §6 S2 验收)。

⛔ **不联网、⛔ 不落工作库**(AGENTS.md):全部走假 fetcher + `tmp_path` 临时库。
真 token 的实测数字见 PROJECT_PLAN §4.4(L1 31 / L2 134 / L3 346、2 页拿全 5897 只、
覆盖率 100%),那些是**已实测的事实,⛔ 不要重测**;本文件锁的是**代码在那些事实下
的行为**,尤其是**翻页**。

本文件最要紧的一条:🔴 **`index_member_all` 单次 3000 行封顶,不翻页会静默少拿一半票**
(§12 坑 5)。接口超限时不报错、只少给 —— 少拿的那一半会变成「这些票查无行业归属」,
而下游会把它读成「这只票没有行业」。故本文件同时锁**翻页拿全**与**截断被拒**两面。
"""

from __future__ import annotations

import logging
import sqlite3

import pytest

from neckline.data import sw_industry as sw
from neckline.data.tushare_client import TushareResult


# ══════════════════════════════════════════════════════════════════════════
# 假数据工厂:形状逐字对齐 §4.4 实测的字段名
# ══════════════════════════════════════════════════════════════════════════

def _classify_rows(level: str, n: int):
    """一层分类。`801125.SI`(白酒Ⅱ)固定放在 L2 的第一条。"""
    if level == "L2":
        rows = [{"index_code": sw.BAIJIU_L2_CODE, "industry_name": sw.BAIJIU_L2_NAME,
                 "level": "L2", "parent_code": "801120.SI", "src": "SW2021"}]
        n -= 1
    else:
        rows = []
    for i in range(n):
        rows.append({"index_code": f"{level}{i:04d}.SI", "industry_name": f"{level}行业{i}",
                     "level": level, "parent_code": None if level == "L1" else "801120.SI",
                     "src": "SW2021"})
    return rows


def _fake_classify(counts=(31, 134, 346)):
    by_level = dict(zip(("L1", "L2", "L3"), counts))

    def fetcher(level: str = "", src: str = "SW2021"):
        assert src == "SW2021", "⛔ 只认 2021 版(2014 老版实测返 0 行)"
        return TushareResult.success(_classify_rows(level, by_level[level]))

    return fetcher


def _member_rows(start: int, count: int):
    return [{"ts_code": f"{600000 + start + i}.SH", "name": f"票{start + i}",
             "l1_code": "801120.SI", "l1_name": "食品饮料",
             "l2_code": sw.BAIJIU_L2_CODE, "l2_name": sw.BAIJIU_L2_NAME,
             "l3_code": "801125001.SI", "l3_name": "白酒",
             "in_date": "20210101", "out_date": None, "is_new": "N"}
            for i in range(count)]


def _fake_members(total: int, page_limit: int = 3000, holes: tuple = ()):
    """`index_member_all` 的替身。每次调用记一笔到 `fetcher.calls`,供断言「真的翻了几页」。

    `holes` = 把第几号(全局序号)那几行的 `l2_code` 抹空 —— 半残快照的形状。"""
    def fetcher(limit: int = page_limit, offset: int = 0):
        fetcher.calls.append((limit, offset))
        rows = _member_rows(offset, max(0, min(limit, total - offset)))
        for i, r in enumerate(rows, start=offset):
            if i in holes:
                r["l2_code"] = ""
        return TushareResult.success(rows)

    fetcher.calls = []
    return fetcher


# ══════════════════════════════════════════════════════════════════════════
# 1. 🔴 翻页(本片的核心)
# ══════════════════════════════════════════════════════════════════════════

class TestPagination:
    def test_two_pages_bring_back_every_member(self):
        """§4.4 实测形状:5897 只 / 3000 一页 → **恰好 2 页**,一只不少。"""
        f = _fake_members(total=5897)
        res = sw.fetch_members(f)
        assert res.ok, res.reason
        rows, pages = res.data
        assert len(rows) == 5897, "⛔ 少拿 = 下游把「没拉到」读成「这票没有行业」"
        assert pages == 2
        assert f.calls == [(3000, 0), (3000, 3000)], "翻页的 offset 必须是 0 / 3000"

    def test_a_single_page_call_would_have_lost_almost_half_the_market(self):
        """反向证明翻页的必要性:只取第一页 = 静默丢 2897 只(§12 坑 5)。

        ⛔ 这条不是凑数 —— 它是「为什么必须翻页」这句话的机器判据。"""
        f = _fake_members(total=5897)
        first_page = f(limit=3000, offset=0)
        assert len(first_page.data) == 3000
        assert 5897 - 3000 == 2897

    def test_exactly_one_full_page_still_asks_for_the_next_one(self):
        """边界:总数恰好 = 页上限时**不能**就此收手 —— 满页说明"可能还有"。"""
        f = _fake_members(total=3000)
        res = sw.fetch_members(f)
        rows, pages = res.data
        assert len(rows) == 3000
        assert pages == 2, "满页必须再探一页才敢说取尽"

    def test_short_first_page_stops_immediately(self):
        f = _fake_members(total=42)
        res = sw.fetch_members(f)
        rows, pages = res.data
        assert (len(rows), pages) == (42, 1)

    def test_duplicate_ts_codes_across_pages_are_collapsed(self):
        """分页边界重复行不该变成两条归属(每只票恰好一个 L1/L2/L3,§4.4)。"""
        def fetcher(limit: int = 3000, offset: int = 0):
            if offset == 0:
                return TushareResult.success(_member_rows(0, 3000))
            return TushareResult.success(_member_rows(2999, 5))   # 与上一页重叠 1 行
        res = sw.fetch_members(fetcher)
        rows, _ = res.data
        assert len(rows) == 3004
        assert len({r["ts_code"] for r in rows}) == 3004

    def test_never_ending_full_pages_fail_loudly_instead_of_truncating(self):
        """接口行为若变了(永远满页)→ **报错停手**,⛔ 不静默截断。"""
        def fetcher(limit: int = 3000, offset: int = 0):
            return TushareResult.success(_member_rows(offset, limit))
        res = sw.fetch_members(fetcher)
        assert res.ok is False
        assert "不静默截断" in res.reason

    def test_a_failing_page_fails_the_whole_fetch(self):
        """第二页挂了 → 整次失败。⛔ 不返回"半份成分表"(半份 = 覆盖率事故)。"""
        def fetcher(limit: int = 3000, offset: int = 0):
            if offset == 0:
                return TushareResult.success(_member_rows(0, 3000))
            return TushareResult.fail("限频: 每分钟最多访问该接口 500 次")
        res = sw.fetch_members(fetcher)
        assert res.ok is False and "offset=3000" in res.reason


# ══════════════════════════════════════════════════════════════════════════
# 2. 分类表:只认 SW2021,逐层拿到 31/134/346
# ══════════════════════════════════════════════════════════════════════════

class TestClassify:
    def test_three_levels_are_fetched_and_merged(self):
        res = sw.fetch_classify(_fake_classify())
        assert res.ok
        rows = res.data
        assert len(rows) == 31 + 134 + 346
        by_level = {}
        for r in rows:
            by_level[r["level"]] = by_level.get(r["level"], 0) + 1
        assert by_level == {"L1": 31, "L2": 134, "L3": 346}, "§4.4 实测值"

    def test_src_is_pinned_to_sw2021(self):
        seen = []

        def fetcher(level: str = "", src: str = ""):
            seen.append(src)
            return TushareResult.success(_classify_rows(level, 3))
        sw.fetch_classify(fetcher)
        assert set(seen) == {"SW2021"}, "⛔ 2014 老版 src='SW' 实测返 0 行"

    def test_zero_rows_names_the_known_2014_trap(self):
        """一层返 0 行 → 失败,且 reason 点名那个已知坑(⛔ 不静默当成"这层没有")。"""
        res = sw.fetch_classify(lambda level="", src="": TushareResult.success([]))
        assert res.ok is False and "2014 老版" in res.reason


# ══════════════════════════════════════════════════════════════════════════
# 3. 落库 / 读取 / 覆盖率(全部走临时库,⛔ 不碰工作库)
# ══════════════════════════════════════════════════════════════════════════

class TestPersistence:
    @pytest.fixture
    def db(self, tmp_path):
        return tmp_path / "sw.db"

    def test_refresh_round_trip(self, db):
        stats = sw.refresh(db_path=db,
                           classify_fetcher=_fake_classify(),
                           member_fetcher=_fake_members(total=5897))
        assert stats.ok, stats.reason
        assert stats.level_counts == {"L1": 31, "L2": 134, "L3": 346}
        assert (stats.member_rows, stats.member_pages) == (5897, 2)
        assert sw.member_count(db) == 5897

    def test_snapshot_replaces_rather_than_accumulates(self, db):
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=100))
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=80))
        assert sw.member_count(db) == 80, "全量快照替换,⛔ 不累加"

    def test_empty_snapshot_is_refused(self, db):
        """⛔ 空覆盖会把「今天没拉到」变成「这些票查无行业」。"""
        with pytest.raises(ValueError, match="拒绝用空快照覆盖"):
            sw.save_snapshot([], [], db_path=db)

    def test_a_member_row_with_a_hole_in_it_is_named_not_silently_dropped(self, db, caplog):
        """🔴 复审 L6:`ts_code` / l1 / l2 / l3 任一为空的成员上一版是**静默 continue**。

        它只在 `pack._market_readings` 的 `missing_sw` 计数里间接可见 —— 隔了一层,
        看到的人不知道是这里丢的。§4.4 实测覆盖率 100%,所以丢行是**数据事故**;
        `save_snapshot` 只拒绝**空**快照,拦不住「少了 200 只」这种半残快照。
        """
        rows = _member_rows(0, 5)
        rows[2]["l2_code"] = ""                    # 归属不全 → 落不进库
        with caplog.at_level(logging.WARNING):
            n_cls, n_mem, dropped = sw.save_snapshot(
                _classify_rows("L2", 3), rows, db_path=db)
        assert n_mem == 4 and dropped == [rows[2]["ts_code"]]
        assert any("归属不全" in r.getMessage() for r in caplog.records), "丢行必须打 WARNING"

    def test_a_half_broken_snapshot_makes_the_refresh_not_ok(self, db):
        """半残快照 → `ok=False` + `reason` 点名,⛔ 不是一句「拉完了」。"""
        stats = sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                           member_fetcher=_fake_members(total=10, holes=(3,)))
        assert stats.ok is False
        assert "归属不全" in stats.reason
        assert stats.member_dropped == 1 and len(stats.member_dropped_codes) == 1
        assert "丢 1 只" in stats.summary()

    def test_out_date_empty_means_currently_effective(self, db):
        rows = _member_rows(0, 2)
        rows[1]["out_date"] = "20240101"          # 已调出
        sw.save_snapshot(_classify_rows("L2", 3), rows, db_path=db)
        with sqlite3.connect(db) as conn:
            flags = dict(conn.execute(
                "SELECT ts_code, is_current FROM sw_industry_member").fetchall())
        assert flags[rows[0]["ts_code"]] == 1
        assert flags[rows[1]["ts_code"]] == 0
        assert rows[1]["ts_code"] not in sw.load_l2_map(db), "调出的票不进当前归属表"

    def test_coverage_is_total_when_every_code_is_mapped(self, db):
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=500))
        codes = [r["ts_code"] for r in _member_rows(0, 500)]
        covered, total, missing = sw.coverage(codes, db_path=db)
        assert (covered, total, missing) == (500, 500, []), "§4.4 实测覆盖率 100%"

    def test_coverage_reports_the_missing_codes_by_name(self, db):
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=10))
        covered, total, missing = sw.coverage(["600000.SH", "999999.SZ"], db_path=db)
        assert covered == 1 and total == 2 and missing == ["999999.SZ"]

    def test_l2_map_is_keyed_by_code_not_name(self, db):
        """§12 坑 6:名称会变、代码不变。归属查询以 `ts_code` 进、`l2_code` 出。"""
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=3))
        m = sw.load_l2_map(db)
        assert m["600000.SH"] == (sw.BAIJIU_L2_CODE, sw.BAIJIU_L2_NAME)


# ══════════════════════════════════════════════════════════════════════════
# 4. 自检
# ══════════════════════════════════════════════════════════════════════════

class TestVerify:
    @pytest.fixture
    def db(self, tmp_path):
        return tmp_path / "sw.db"

    def test_clean_snapshot_has_no_problems(self, db):
        sw.refresh(db_path=db, classify_fetcher=_fake_classify(),
                   member_fetcher=_fake_members(total=200))
        assert sw.verify(db) == []

    def test_missing_baijiu_code_is_a_problem(self, db):
        """白酒Ⅱ **代码**不在表里 = K9 第一层第 2 条排除项排不掉 → 真问题。"""
        cls = [r for r in _classify_rows("L2", 5) if r["index_code"] != sw.BAIJIU_L2_CODE]
        sw.save_snapshot(cls, _member_rows(0, 5), db_path=db)
        problems = sw.verify(db)
        assert any(sw.BAIJIU_L2_CODE in p for p in problems)

    def test_renamed_baijiu_only_warns(self, db, caplog):
        """名称变了只告警不阻断:判据按代码走(§5.4.3 校验 2 同款处置)。"""
        cls = _classify_rows("L1", 2) + _classify_rows("L2", 2) + _classify_rows("L3", 2)
        for r in cls:
            if r["index_code"] == sw.BAIJIU_L2_CODE:
                r["industry_name"] = "白酒(改名了)"
        sw.save_snapshot(cls, _member_rows(0, 5), db_path=db)
        with caplog.at_level("WARNING"):
            assert sw.verify(db) == []
        assert "名称会变" in caplog.text

    def test_duplicate_membership_rows_are_named_not_a_cryptic_sql_error(self, db):
        """同票两行 → 点名是哪几只并拒绝落库,⛔ 不抛一句看不出所以然的 UNIQUE 约束错。

        §4.4 实测「每只恰好 1 个 L1/L2/L3」;真出现重复 = 接口口径变了,是要人看的事。"""
        rows = _member_rows(0, 2)
        rows[1]["ts_code"] = rows[0]["ts_code"]
        with pytest.raises(ValueError, match="多行归属"):
            sw.save_snapshot(_classify_rows("L2", 2), rows, db_path=db)
