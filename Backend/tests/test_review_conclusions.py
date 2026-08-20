"""S11 结论存档的行为判据(架构 §六 第 3 件事,PROJECT_PLAN §5.9)。

锁三件事:
  ① **append-only**:改一次 = 新版本,⛔ 老版本一个字不动;
  ② **「还没写」不是「这周没问题」**:查不到时返回 `None` 而不是一份空结论;
  ③ **校验一次列全 + ⛔ 不静默截断**:超长正文当场拒绝(截掉的恰恰是结尾那句结论,
     而用户会以为存进去了)。
"""

from __future__ import annotations

import pytest

from neckline.review import conclusions as C


@pytest.fixture
def db(isolated_env):
    return isolated_env.db_path


class TestAppendOnly:
    def test_second_save_creates_a_new_version_and_leaves_v1_untouched(self, db):
        v1 = C.save("2026-W34", "本周三笔全在追高", "追高三次,两次当天就套。", db_path=db)
        v2 = C.save("2026-W34", "改口径:是追高不是选错", "复看之后改判。", db_path=db)
        assert (v1.version, v2.version) == (1, 2)

        versions = C.load_versions("2026-W34", db_path=db)
        assert [c.version for c in versions] == [1, 2]
        assert versions[0].title == "本周三笔全在追高"
        assert versions[0].body == "追高三次,两次当天就套。"      # v1 一个字没动
        assert C.load_latest("2026-W34", db_path=db).version == 2

    def test_the_module_has_no_update_or_delete_path(self):
        """🔴 结构性:模块里根本没有那两条 SQL(⛔ 不是靠谁记得别写)。
        文本判据在守门单测里另有一条更严的(掐掉 docstring 后扫)。"""
        assert not hasattr(C, "update")
        assert not hasattr(C, "delete")


class TestAbsenceIsNotCleanliness:
    def test_never_written_returns_none_not_an_empty_conclusion(self, db):
        assert C.load_latest("2026-W01", db_path=db) is None
        assert C.load_versions("2026-W01", db_path=db) == []

    def test_next_version_starts_at_one(self, db):
        assert C.next_version("2026-W02", db_path=db) == 1


class TestValidation:
    @pytest.mark.parametrize("week", ["2026W34", "26-W34", "", "2026-w34", "2026-W3"])
    def test_bad_week_key_is_rejected(self, db, week):
        with pytest.raises(C.ConclusionInvalid) as exc:
            C.save(week, "t", "b", db_path=db)
        assert "ISO 周" in str(exc.value)

    def test_empty_title_or_body_is_rejected(self, db):
        with pytest.raises(C.ConclusionInvalid) as exc:
            C.save("2026-W34", "   ", "   ", db_path=db)
        msg = str(exc.value)
        assert "title" in msg and "body" in msg, "⛔ 校验必须一次列全,不是抛第一个"

    def test_oversized_body_is_refused_not_truncated(self, db):
        """⛔ 不静默截断:截掉的恰恰是结尾那句结论,而用户以为存进去了。"""
        with pytest.raises(C.ConclusionInvalid) as exc:
            C.save("2026-W34", "t", "x" * (C.MAX_BODY_CHARS + 1), db_path=db)
        assert "不静默截断" in str(exc.value)
        assert C.load_latest("2026-W34", db_path=db) is None, "拒绝之后不许留半行"

    def test_too_many_tags_is_rejected(self, db):
        with pytest.raises(C.ConclusionInvalid):
            C.save("2026-W34", "t", "b", tags=[f"t{i}" for i in range(C.MAX_TAGS + 1)],
                   db_path=db)


class TestRetrieval:
    def _seed(self, db):
        C.save("2026-W32", "追高", "两笔追高,当天套住。", tags=["追高"], db_path=db)
        C.save("2026-W33", "等回踩", "改成等回踩,一笔没进。", tags=["纪律"], db_path=db)
        C.save("2026-W34", "行业选错", "方向对了票选错。", tags=["选票"], db_path=db)
        C.save("2026-W34", "行业选错(改)", "再看一遍,是方向也不对。", tags=["方向"],
               db_path=db)

    def test_list_latest_gives_one_row_per_week_newest_first(self, db):
        self._seed(db)
        rows = C.list_latest(limit=10, db_path=db)
        assert [c.week for c in rows] == ["2026-W34", "2026-W33", "2026-W32"]
        assert rows[0].version == 2, "同一周只出最新版"

    def test_search_matches_title_body_and_tags(self, db):
        self._seed(db)
        assert [c.week for c in C.search("追高", db_path=db)] == ["2026-W32"]
        assert [c.week for c in C.search("回踩", db_path=db)] == ["2026-W33"]
        assert [c.week for c in C.search("方向", db_path=db)] == ["2026-W34"]

    def test_empty_query_falls_back_to_the_latest_list(self, db):
        """「搜了个空串」与「搜了但没搜到」不是一回事。"""
        self._seed(db)
        assert len(C.search("   ", db_path=db)) == 3
        assert C.search("这四个字不可能命中", db_path=db) == []

    def test_broken_tags_json_reads_back_as_empty_not_a_crash(self, db):
        import sqlite3

        C.save("2026-W35", "t", "b", tags=["a"], db_path=db)
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("UPDATE review_conclusions SET tags_json='{oops' WHERE week=?",
                         ("2026-W35",))
            conn.commit()
        finally:
            conn.close()
        got = C.load_latest("2026-W35", db_path=db)
        assert got is not None and got.tags == () and got.body == "b"
