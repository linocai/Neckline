"""KX 库迁移（v1 -> v2）与名单同步。假 KX 是 MockTransport，不联网。"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from neckline.kx import store
from neckline.kx.board import BoardUnavailable, build_board
from neckline.morning import db
from tests.kx_support import READ_KEY, VERSION, build, make_members, run


# ------------------------------------------------------------------ 库迁移

def _make_v1(path):
    """按版本 1 的结构建库（只有 DDL，没有 KX 表），带一份有内容的数据。"""
    conn = sqlite3.connect(path)
    conn.executescript(db.DDL)
    conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
    conn.execute("PRAGMA user_version=1")
    conn.execute("INSERT INTO devices VALUES ('abcd','ios','2026-10-19T00:00:00+00:00','2026-10-19T00:00:00+00:00')")
    conn.execute("""INSERT INTO reports(report_id, trade_date, window_start, window_end, status, config_version,
                    config_sha256, started_at, available_at, content_json)
                    VALUES ('r1','2026-10-19','a','b','available','screening-v2','0','2026-10-19T00:00:00+00:00',
                            '2026-10-19T00:01:00+00:00','{"entries":[]}')""")
    conn.commit()
    conn.close()


def test_v1_database_upgrades_in_place_and_keeps_data(tmp_path):
    path = tmp_path / "v1.db"
    _make_v1(path)
    assert db.SCHEMA_VERSION == 2
    with pytest.raises(db.NotReady):  # 只读路径不升级，也不执行 DDL
        with db.read(path):
            pass
    with pytest.raises(db.NotReady):
        with db.write(path):
            pass
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'kx_%'").fetchall()
    conn.close()

    db.init_schema(path)
    with db.read(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("PRAGMA application_id").fetchone()[0] == db.APPLICATION_ID
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"kx_pool", "kx_picks", "kx_daily_stats"} <= tables
        assert conn.execute("SELECT token FROM devices").fetchone()[0] == "abcd"
        assert db.latest_report(conn)["report_id"] == "r1"
        assert db.latest_report(conn)["content"] == {"entries": []}
    with db.write(path) as conn:
        db.upsert_device(conn, token="ef01", platform="ios", at=datetime(2026, 10, 20, tzinfo=timezone.utc))
        store.replace_pool(conn, version="v", members=[store.Member("600000", "浦发银行", "银行", 0)],
                           verified_at=datetime(2026, 10, 20, tzinfo=timezone.utc))
    with db.read(path) as conn:
        assert db.device_tokens(conn) == ["abcd", "ef01"]
        assert store.load_pool(conn).version == "v"
    db.init_schema(path)  # 已是当前版本：什么都不改，数据仍在
    with db.read(path) as conn:
        assert store.load_pool(conn).version == "v" and db.device_tokens(conn) == ["abcd", "ef01"]


def test_a_failed_upgrade_rolls_back_as_a_whole(tmp_path, monkeypatch):
    path = tmp_path / "v1.db"
    _make_v1(path)
    monkeypatch.setattr(db, "KX_DDL", "CREATE TABLE kx_pool (code TEXT); THIS IS NOT SQL;")
    with pytest.raises(sqlite3.OperationalError):
        db.init_schema(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'kx_%'").fetchall()
    assert conn.execute("SELECT count(*) FROM devices").fetchone()[0] == 1
    conn.close()
    monkeypatch.undo()
    db.init_schema(path)  # 修好之后可以重来
    with db.read(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("version", [0, 3])
def test_init_schema_refuses_other_versions_and_foreign_databases(tmp_path, version):
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.executescript(db.DDL)
    conn.execute(f"PRAGMA application_id={db.APPLICATION_ID}")
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()
    with pytest.raises(db.NotReady):
        db.init_schema(path)


def test_a_fresh_database_is_created_at_version_two(tmp_path):
    path = tmp_path / "fresh.db"
    db.init_schema(path)
    with db.read(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM kx_pool").fetchone()[0] == 0


# ------------------------------------------------------------------ 名单同步

def _board(harness):
    return run(build_board(harness.services, harness.path))


def test_first_sync_validates_stores_and_serves_the_pool(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    board = _board(harness)
    assert board["pool"] == {"version": VERSION, "count": 45, "checkedAt": "2026-10-20T10:30:00+08:00",
                             "stale": False, "reason": None}
    assert len({row["code"] for row in board["rows"]}) == 45
    assert harness.kx.count("/kx/health") == 1 and harness.kx.count(f"/kx/read/{READ_KEY}") == 1
    with db.read(harness.path) as conn:
        snapshot = store.load_pool(conn)
    assert snapshot.version == VERSION and len(snapshot.members) == 45
    assert snapshot.members[0] == store.Member("000000", "股票00", "行业0", 0)


def test_every_industry_tag_is_kept_and_repeats_are_dropped(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=3)

    def tags(body):
        body["rows"][0]["sw_l2"] = [{"code": "801010.SI", "name": "白酒"}, {"code": "801011.SI", "name": "食品"},
                                    {"code": "801012.SI", "name": "白酒"}]
    harness.kx.page_patch = _break(tags)
    board = _board(harness)
    assert {row["code"]: row["industry"] for row in board["rows"]}["000000"] == "白酒、食品"


def test_health_is_cached_for_sixty_seconds_and_only_a_version_change_pulls_the_list(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    _board(harness)
    harness.clock.advance(59)
    _board(harness)
    assert harness.kx.count("/kx/health") == 1  # 60 秒内不重复查
    harness.clock.advance(2)
    board = _board(harness)
    assert harness.kx.count("/kx/health") == 2 and harness.kx.count(f"/kx/read/{READ_KEY}") == 1  # 版本没变：不拉名单
    assert board["pool"]["checkedAt"] == "2026-10-20T10:31:01+08:00" and board["pool"]["stale"] is False

    # 换池：版本变了，下一次健康检查之后第一次请求就切到新名单，旧代码不再出现
    harness.kx.version = "kx-20261024"
    harness.kx.members = make_members(45)[:40] + [("600900", "新股票", "行业9")]
    harness.mairui.members = harness.kx.members
    harness.tushare.members = harness.kx.members
    harness.clock.advance(61)
    board = _board(harness)
    assert harness.kx.count(f"/kx/read/{READ_KEY}") == 2
    assert board["pool"]["version"] == "kx-20261024" and board["pool"]["count"] == 41
    codes = {row["code"] for row in board["rows"]}
    assert "600900" in codes and "600044" not in codes and len(codes) == 41
    _board(harness)
    assert harness.kx.count(f"/kx/read/{READ_KEY}") == 2


def test_unreachable_kx_keeps_the_previous_list_and_says_it_was_not_checked(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    _board(harness)
    harness.clock.advance(120)
    harness.kx.down = True
    board = _board(harness)
    assert len(board["rows"]) == 45
    assert board["pool"]["stale"] is True and board["pool"]["reason"] == "KX 暂时连不上。"
    assert board["pool"]["checkedAt"] == "2026-10-20T10:30:00+08:00"  # 最后一次核对成功的时间，不是现在
    calls = harness.kx.count("/kx/health")
    harness.clock.advance(10)
    _board(harness)
    assert harness.kx.count("/kx/health") == calls  # 连不上也有 15 秒的短缓存，不会每次请求都去敲
    harness.clock.advance(10)
    harness.kx.down = False
    board = _board(harness)
    assert board["pool"]["stale"] is False and board["pool"]["checkedAt"] == "2026-10-20T10:32:20+08:00"


def test_a_pool_that_was_never_synced_gives_no_board(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.kx.down = True
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    assert caught.value.reason == "pool_unverified" and "KX 暂时连不上" in caught.value.message
    assert harness.mairui.calls == [] and harness.tushare.calls == []  # 没有名单就不取行情
    monkeypatch.delenv("KX_READ_KEY")
    harness.kx.down = False
    harness.clock.advance(100)
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    assert "没有配置 KX 只读页面的访问码" in caught.value.message


def _break(change):
    def patch(body):
        change(body)
        return body
    return patch


@pytest.mark.parametrize("label,change", [
    ("重复代码", lambda body: body["rows"].__setitem__(1, dict(body["rows"][0]))),
    ("代码格式", lambda body: body["rows"][2].__setitem__("stock_code", "12345")),
    ("代码含字母", lambda body: body["rows"][2].__setitem__("stock_code", "60A000")),
    ("缺行业", lambda body: body["rows"][3].__setitem__("sw_l2", [])),
    ("缺名称", lambda body: body["rows"][3].__setitem__("name", " ")),
    ("数量对不上", lambda body: body["rows"].pop()),
    ("pool.count 对不上", lambda body: body["pool"].__setitem__("count", 44)),
    ("版本对不上", lambda body: body["pool"].__setitem__("version", "other")),
    ("状态异常", lambda body: body.__setitem__("status", "unavailable")),
    ("没有 rows", lambda body: body.pop("rows")),
])
def test_invalid_pool_documents_are_rejected_and_the_old_list_stays(tmp_path, monkeypatch, label, change):
    harness = build(tmp_path, monkeypatch)
    _board(harness)
    harness.kx.version = "kx-20261024"
    harness.kx.page_patch = _break(change)
    harness.clock.advance(61)
    board = _board(harness)
    assert board["pool"]["version"] == VERSION and board["pool"]["stale"] is True, label
    with db.read(harness.path) as conn:
        assert store.load_pool(conn).version == VERSION


def test_a_never_synced_pool_with_a_bad_document_stays_unverified(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.kx.page_patch = _break(lambda body: body["pool"].__setitem__("count", 3))
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    assert caught.value.reason == "pool_unverified"
    with db.read(harness.path) as conn:
        assert store.load_pool(conn) is None


def test_bad_health_bodies_are_not_trusted(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.kx.health_patch = lambda body: {**body, "pool_count": True}
    with pytest.raises(BoardUnavailable):
        _board(harness)
    harness.kx.health_patch = lambda body: {**body, "status": "unavailable"}
    harness.clock.advance(20)
    with pytest.raises(BoardUnavailable):
        _board(harness)


def test_kx_pending_page_is_retried_and_never_taken_for_a_list(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.kx.pending = 2
    board = _board(harness)
    assert board["pool"]["stale"] is False and len(board["rows"]) == 45
    assert harness.kx.count(f"/kx/read/{READ_KEY}") == 3 and harness.sleeps == [3.0, 3.0]


def test_a_page_that_stays_pending_is_a_failed_sync_and_is_not_hammered(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    harness.kx.pending = 99
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    assert "还在取数" in caught.value.message and harness.kx.count(f"/kx/read/{READ_KEY}") == 4
    with db.read(harness.path) as conn:
        assert store.load_pool(conn) is None
    harness.clock.advance(5)
    with pytest.raises(BoardUnavailable):
        _board(harness)
    assert harness.kx.count(f"/kx/read/{READ_KEY}") == 4  # 失败后 15 秒内不再敲 KX
    harness.kx.pending = 0
    harness.clock.advance(20)
    assert len(_board(harness)["rows"]) == 45


def test_replacing_the_pool_is_atomic(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    _board(harness)
    original = store.replace_pool

    def broken(conn, **kwargs):
        original(conn, **kwargs)
        raise RuntimeError("写到一半出错")

    harness.kx.version = "kx-20261024"
    monkeypatch.setattr(store, "replace_pool", broken)
    harness.clock.advance(61)
    with pytest.raises(RuntimeError):
        _board(harness)
    with db.read(harness.path) as conn:
        snapshot = store.load_pool(conn)
    assert snapshot.version == VERSION and len(snapshot.members) == 45  # 整体回滚，旧名单完好


def test_the_read_key_never_reaches_logs_or_responses(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    harness = build(tmp_path, monkeypatch)
    harness.kx.down = True
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    harness.kx.down = False
    harness.clock.advance(100)
    board = _board(harness)
    assert READ_KEY not in caught.value.message and READ_KEY not in str(board) and READ_KEY not in caplog.text
    assert "linotsai.top" not in caught.value.message


def test_a_malformed_read_key_is_treated_as_not_configured(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch)
    monkeypatch.setenv("KX_READ_KEY", "../../etc/passwd" + "x" * 30)
    with pytest.raises(BoardUnavailable) as caught:
        _board(harness)
    assert "没有配置 KX 只读页面的访问码" in caught.value.message and harness.kx.count(f"/kx/read/{READ_KEY}") == 0


def test_settings_accept_only_well_formed_values(monkeypatch):
    from neckline.kx import settings
    assert settings.base_url() == "https://linotsai.top"
    for bad in ("http://linotsai.top", "https://a.test/kx", "https://u:p@a.test", "https://a.test?x=1", "ftp://a.test"):
        monkeypatch.setenv("KX_BASE_URL", bad)
        assert settings.base_url() == "https://linotsai.top", bad
    monkeypatch.setenv("KX_BASE_URL", "https://kx.example.test/")
    assert settings.base_url() == "https://kx.example.test"
    for bad_key in ("a" * 31, "a" * 129, "a/b" + "c" * 40, "中" * 40, ""):
        monkeypatch.setenv("KX_READ_KEY", bad_key)
        assert settings.read_key() is None, bad_key
    monkeypatch.setenv("KX_READ_KEY", "A_b-" + "9" * 28)
    assert settings.read_key() == "A_b-" + "9" * 28
    monkeypatch.setenv("KX_PICKS_TOKEN", " " + "t" * 16 + " ")
    assert settings.picks_token() == "t" * 16
    monkeypatch.setenv("KX_PICKS_TOKEN", "t" * 15)
    assert settings.picks_token() is None
