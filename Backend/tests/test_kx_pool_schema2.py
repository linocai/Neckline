"""KX 名单 schema 2 与 3：解析、与 schema 1 的兼容与切换、热了第几天（heatDay）的算法。全部离线（MockTransport、临时库）。"""
from __future__ import annotations

from datetime import date

import pytest

from neckline.kx import store
from neckline.kx.board import build_board
from neckline.kx.days import heat_days
from neckline.kx.source import Health, KxError, parse_pool
from neckline.morning import db
from tests.kx_support import FIRST_SEEN, READ_KEY, VERSION, at, build, run

HEALTH = Health("kx-20261026-001", 3)


def _themed(code: str, name: str, theme: str, first_seen: str, **extra) -> dict:
    """schema 2 与 3 的行：没有 buyable，带 session_phase 等看板不读的字段。"""
    row = {"stock_code": code, "name": name, "theme": theme, "first_seen": first_seen, "heat_day": 99,
           "price": 1.0, "session_phase": None, "quote_status": "ok", "warnings": [], "under_200": True,
           "source_time": "2026-10-29 10:30:00"}
    row.update(extra)
    return row


def _body(schema, rows=None, **pool):
    """KX 只读页面 JSON 的最小形状。schema 为 None 时 pool 里不写 schema_version。"""
    base = {"stock_code": "600001", "name": "邯郸钢铁"}
    rows = rows if rows is not None else [
        {**base, "sw_l2": [{"code": "801010.SI", "name": "钢铁"}], "theme": "忽略我", "first_seen": "垃圾", "buyable": "x"}
        if schema in (None, 1) else
        _themed("600001", "邯郸钢铁", "钢铁复苏", "2026-10-19", heat_day=None),
        {"stock_code": "000001", "name": "平安银行", "sw_l2": [{"code": "x", "name": "银行"}]}
        if schema in (None, 1) else
        _themed("000001", "平安银行", "银行修复", "2026-10-16", heat_day=3, sw_l2="schema 2 不看这一项"),
        {"stock_code": "300001", "name": "特锐德", "sw_l2": [{"code": "x", "name": "电力设备"}]}
        if schema in (None, 1) else
        _themed("300001", "特锐德", "充电桩", "2026-10-20"),
    ]
    meta = {"status": "ok", "version": "kx-20261026-001", "count": 3, **pool}
    if schema is not None:
        meta["schema_version"] = schema
    return {"status": "ok", "pool": meta, "rows": rows, "refresh": {"result": "updated"}}


def _bad(body, *, code="kx_bad_pool"):
    with pytest.raises(KxError) as caught:
        parse_pool(body, HEALTH)
    assert caught.value.code == code
    return caught.value


# ------------------------------------------------------------------ 解析

def test_schema_1_pool_is_parsed_with_industries_and_no_schema_2_fields():
    for schema in (1, None):  # 缺失视为 1
        document = parse_pool(_body(schema), HEALTH)
        assert document.version == "kx-20261026-001"
        assert document.members == (store.Member("600001", "邯郸钢铁", "钢铁", 0),
                                    store.Member("000001", "平安银行", "银行", 1),
                                    store.Member("300001", "特锐德", "电力设备", 2))
        # schema 1 的行里就算有 theme / first_seen / buyable（垃圾值）也不读
        assert all((m.theme, m.first_seen) == (None, None) for m in document.members)
        assert not hasattr(document.members[0], "buyable")


@pytest.mark.parametrize("schema", [2, 3])
def test_schema_2_and_3_share_one_parser_and_never_read_heat_day_or_buyable(schema):
    document = parse_pool(_body(schema), HEALTH)
    assert document.members == (
        store.Member("600001", "邯郸钢铁", None, 0, "钢铁复苏", date(2026, 10, 19)),
        store.Member("000001", "平安银行", None, 1, "银行修复", date(2026, 10, 16)),
        store.Member("300001", "特锐德", None, 2, "充电桩", date(2026, 10, 20)))
    assert not hasattr(document.members[0], "heat_day")
    assert not hasattr(document.members[0], "buyable")


@pytest.mark.parametrize("schema", [2, 3])
@pytest.mark.parametrize("buyable", [None, True, False, 1, 0, "true", "x", [], {}])
def test_any_buyable_value_on_a_themed_row_is_ignored(schema, buyable):
    body = _body(schema)
    for row in body["rows"]:
        row["buyable"] = buyable
    document = parse_pool(body, HEALTH)
    assert [member.theme for member in document.members] == ["钢铁复苏", "银行修复", "充电桩"]
    bare = parse_pool(_body(schema), HEALTH)
    assert document.members == bare.members


def test_schema_2_text_is_trimmed_and_the_theme_may_be_exactly_eighty_characters():
    body = _body(2)
    body["rows"][0]["theme"] = "  " + "题" * 80 + "  "
    body["rows"][0]["name"] = " 邯郸钢铁 "
    member = parse_pool(body, HEALTH).members[0]
    assert member.theme == "题" * 80 and member.name == "邯郸钢铁"


@pytest.mark.parametrize("value", [0, 4, -1, "3", "2", "1", 3.0, 2.0, 1.0, True, False, None, [3], {"v": 3}])
def test_an_unknown_or_malformed_schema_version_is_rejected(value):
    for schema in (1, 2, 3):
        body = _body(schema)
        body["pool"]["schema_version"] = value
        _bad(body)


@pytest.mark.parametrize("label,change", [
    ("题材缺失", lambda row: row.pop("theme")),
    ("题材为空", lambda row: row.__setitem__("theme", "")),
    ("题材全空白", lambda row: row.__setitem__("theme", " \t ")),
    ("题材不是字符串", lambda row: row.__setitem__("theme", ["钢铁"])),
    ("题材为 null", lambda row: row.__setitem__("theme", None)),
    ("题材 81 字", lambda row: row.__setitem__("theme", "题" * 81)),
    ("first_seen 缺失", lambda row: row.pop("first_seen")),
    ("first_seen 为 null", lambda row: row.__setitem__("first_seen", None)),
    ("first_seen 为数字", lambda row: row.__setitem__("first_seen", 20261019)),
    ("first_seen 无分隔", lambda row: row.__setitem__("first_seen", "20261019")),
    ("first_seen 月份越界", lambda row: row.__setitem__("first_seen", "2026-13-01")),
    ("first_seen 不是真实日期", lambda row: row.__setitem__("first_seen", "2026-02-30")),
    ("first_seen 带时间", lambda row: row.__setitem__("first_seen", "2026-10-19T08:00:00")),
    ("first_seen 不补零", lambda row: row.__setitem__("first_seen", "2026-1-9")),
    ("first_seen 周编号", lambda row: row.__setitem__("first_seen", "2026-W43-1")),
    ("名称为空", lambda row: row.__setitem__("name", "  ")),
    ("名称不是字符串", lambda row: row.__setitem__("name", 600001)),
    ("代码格式", lambda row: row.__setitem__("stock_code", "60001")),
    ("代码含字母", lambda row: row.__setitem__("stock_code", "60A001")),
    ("代码不是字符串", lambda row: row.__setitem__("stock_code", 600001)),
])
@pytest.mark.parametrize("schema", [2, 3])
def test_invalid_schema_2_rows_are_rejected(schema, label, change):
    for index in range(3):  # 任何一行坏了整份都拒收
        body = _body(schema)
        change(body["rows"][index])
        _bad(body)


def test_schema_2_row_that_is_not_an_object_or_a_duplicate_code_is_rejected():
    body = _body(2)
    body["rows"][1] = "600001"
    _bad(body)
    body = _body(2)
    body["rows"][1]["stock_code"] = body["rows"][0]["stock_code"]
    _bad(body)


def test_schema_1_rows_still_need_an_industry_and_ignore_schema_2_fields():
    for label, change in (("缺行业", lambda row: row.pop("sw_l2")), ("行业为空", lambda row: row.__setitem__("sw_l2", [])),
                          ("行业名为空", lambda row: row.__setitem__("sw_l2", [{"code": "x", "name": " "}])),
                          ("缺名称", lambda row: row.__setitem__("name", ""))):
        body = _body(1)
        change(body["rows"][0])
        _bad(body)
        body = _body(None)
        change(body["rows"][2])
        _bad(body)


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_version_and_count_must_match_the_health_check_for_both_schemas(schema):
    body = _body(schema)
    body["pool"]["version"] = "other"
    _bad(body, code="kx_pool_changed")
    body = _body(schema)
    body["pool"]["count"] = 2
    _bad(body, code="kx_pool_changed")
    body = _body(schema)
    body["rows"].pop()
    _bad(body)  # rows 条数与 pool.count 不一致
    body = _body(schema)
    body["pool"]["count"] = True
    _bad(body, code="kx_pool_changed")
    for broken in ({"status": "unavailable"}, {"status": None}):
        body = {**_body(schema), **broken}
        _bad(body)
    body = _body(schema)
    body.pop("rows")
    _bad(body)
    _bad([])


# ------------------------------------------------------------------ 看板：schema 2 与 schema 1 ↔ 2 的切换

def _board(harness):
    return run(build_board(harness.services, harness.path))


def _by_code(board):
    return {row["code"]: row for row in board["rows"]}


@pytest.mark.parametrize("schema", [2, 3])
def test_a_themed_pool_is_served_with_theme_first_seen_and_heat_day(tmp_path, monkeypatch, schema):
    harness = build(tmp_path, monkeypatch, count=6)  # 2026-10-20 周二 10:30，行情日就是今天
    harness.kx.schema = schema
    harness.kx.first_seen = {"000000": "2026-10-20", "000001": "2026-10-16", "000002": "2026-10-17"}
    board = _board(harness)
    assert "schemaVersion" not in board["pool"] and "buyable" not in board["rows"][0]
    assert board["pool"]["count"] == 6 and board["pool"]["stale"] is False
    rows = _by_code(board)
    assert {code: (row["theme"], row["firstSeen"], row["heatDay"], row["industry"], row["sessionPhase"])
            for code, row in rows.items() if code in ("000000", "000001", "000002", "000003")} == {
        "000000": ("题材0", "2026-10-20", 1, None, None),      # 今天才进：第 1 天
        "000001": ("题材1", "2026-10-16", 3, None, None),      # 周五、周一、周二：第 3 天
        "000002": ("题材2", "2026-10-17", 2, None, None),      # 周六进的：从周一起算，周一、周二
        "000003": ("题材3", FIRST_SEEN, 2, None, None)}
    assert all(row["heatDay"] != 99 for row in rows.values())  # KX 页面里的 heat_day 一概不读
    assert all(row["quoteStatus"] == "ok" for row in rows.values())  # 行情、日线统计照常
    with db.read(harness.path) as conn:
        snapshot = store.load_pool(conn)
        raw = conn.execute("SELECT industry, theme, first_seen, buyable FROM kx_pool WHERE code='000001'").fetchone()
    assert snapshot.members[1] == store.Member("000001", "股票01", None, 1, "题材1", date(2026, 10, 16))
    assert tuple(raw) == ("", "题材1", "2026-10-16", None)


def test_the_pool_switches_from_schema_1_to_schema_3_and_back(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=6)
    first = _board(harness)
    assert "schemaVersion" not in first["pool"]
    assert all(row["industry"] and (row["theme"], row["firstSeen"], row["heatDay"], row["sessionPhase"]) ==
               (None, None, None, None) for row in first["rows"])
    assert {row["industry"] for row in first["rows"]} == {"行业0", "行业1", "行业2", "行业3"}

    # KX 换成 schema 3 的新版本：成员不变也是新版本，下一次健康检查之后第一次请求就切换
    harness.kx.version, harness.kx.schema = "kx-20261029-001", 3
    harness.clock.advance(61)
    second = _board(harness)
    assert second["pool"]["version"] == "kx-20261029-001" and "schemaVersion" not in second["pool"]
    assert harness.kx.count(f"/kx/read/{READ_KEY}") == 2
    assert [row["code"] for row in second["rows"]] == [row["code"] for row in first["rows"]]  # 排序规则没变
    assert all(row["industry"] is None and row["theme"].startswith("题材") and "buyable" not in row and
               row["firstSeen"] == FIRST_SEEN and row["heatDay"] == 2 for row in second["rows"])
    harness.clock.advance(5)
    assert _board(harness)["pool"]["version"] == "kx-20261029-001" and harness.kx.count(f"/kx/read/{READ_KEY}") == 2

    # 回退：KX 写回 schema 1，行业回来，题材又是 null
    harness.kx.version, harness.kx.schema = "kx-20261027-001", 1
    harness.clock.advance(61)
    third = _board(harness)
    assert "schemaVersion" not in third["pool"]
    assert {row["industry"] for row in third["rows"]} == {"行业0", "行业1", "行业2", "行业3"}
    assert all((row["theme"], row["firstSeen"], row["heatDay"]) == (None, None, None) for row in third["rows"])


def test_recommendations_survive_a_switch_to_schema_2(tmp_path, monkeypatch):
    from tests.test_kx_picks import _put
    harness = build(tmp_path, monkeypatch, count=6)
    _put(harness, ["000003"], "2026-10-20T10:00:00")
    assert _board(harness)["rows"][0]["code"] == "000003"
    harness.kx.version, harness.kx.schema = "kx-20261026-001", 2
    harness.clock.advance(61)
    board = _board(harness)
    assert board["rows"][0]["code"] == "000003" and board["rows"][0]["highlight"] == "today"
    assert board["rows"][0]["theme"] == "题材3"


def test_a_stale_schema_2_pool_keeps_its_theme_while_kx_is_unreachable(tmp_path, monkeypatch):
    harness = build(tmp_path, monkeypatch, count=5)
    harness.kx.schema = 2
    _board(harness)
    harness.clock.advance(120)
    harness.kx.down = True
    board = _board(harness)
    assert board["pool"]["stale"] is True and "schemaVersion" not in board["pool"]
    assert all(row["theme"] and row["heatDay"] == 2 for row in board["rows"])


@pytest.mark.parametrize("label,change", [
    ("schema 版本未知", lambda body: body["pool"].__setitem__("schema_version", 4)),
    ("题材为空", lambda body: body["rows"][1].__setitem__("theme", "")),
    ("first_seen 不合法", lambda body: body["rows"][2].__setitem__("first_seen", "2026-02-30")),
    ("重复代码", lambda body: body["rows"].__setitem__(1, dict(body["rows"][0]))),
])
def test_a_bad_schema_2_document_is_rejected_and_the_old_list_stays(tmp_path, monkeypatch, label, change):
    harness = build(tmp_path, monkeypatch)
    _board(harness)  # 先是 schema 1 的老名单
    harness.kx.version, harness.kx.schema = "kx-20261026-001", 2

    def patch(body):
        change(body)
        return body
    harness.kx.page_patch = patch
    harness.clock.advance(61)
    board = _board(harness)
    assert board["pool"]["version"] == VERSION and "schemaVersion" not in board["pool"], label
    assert board["pool"]["stale"] is True and "没有采用" in board["pool"]["reason"]
    with db.read(harness.path) as conn:
        snapshot = store.load_pool(conn)
    assert snapshot.version == VERSION and snapshot.members[0].theme is None and len(snapshot.members) == 45


def test_a_database_upgraded_from_v2_serves_its_schema_1_pool_until_kx_changes_it(tmp_path, monkeypatch):
    """B122 线上的库升级到 v3 后：旧名单（schema 1）照常出看板，题材三项为 null；KX 换成 schema 2 后切换。"""
    harness = build(tmp_path, monkeypatch, count=5)
    _board(harness)  # 名单与健康检查一致，库里是 schema 1 的名单
    with db.write(harness.path) as conn:  # 抹掉三列，模拟升级前由 B122 写入的行
        conn.execute("UPDATE kx_pool SET theme=NULL, first_seen=NULL, buyable=NULL")
    harness.clock.advance(61)
    board = _board(harness)
    assert "schemaVersion" not in board["pool"] and board["pool"]["stale"] is False
    assert all(row["industry"] and row["theme"] is None for row in board["rows"])
    harness.kx.version, harness.kx.schema = "kx-20261029-001", 3
    harness.clock.advance(61)
    switched = _board(harness)
    assert switched["pool"]["version"] == "kx-20261029-001"
    assert all(row["theme"] for row in switched["rows"])
    with db.read(harness.path) as conn:
        assert {row[0] for row in conn.execute("SELECT buyable FROM kx_pool")} == {None}


def test_syncing_schema_3_clears_buyable_left_by_an_older_build(tmp_path, monkeypatch):
    """旧库里 kx_pool.buyable 有值；同步到 schema 3 后这一列全部写成空。"""
    harness = build(tmp_path, monkeypatch, count=4)
    harness.kx.schema = 2
    _board(harness)
    with db.write(harness.path) as conn:
        conn.execute("UPDATE kx_pool SET buyable=1")
        conn.execute("UPDATE kx_pool SET buyable=0 WHERE position=0")
        assert {row[0] for row in conn.execute("SELECT buyable FROM kx_pool")} == {0, 1}
    harness.kx.version, harness.kx.schema = "kx-20261029-001", 3
    harness.clock.advance(61)
    _board(harness)
    with db.read(harness.path) as conn:
        stored = conn.execute("SELECT buyable FROM kx_pool").fetchall()
    assert stored and {row[0] for row in stored} == {None}


def test_heat_day_follows_the_quote_day_not_the_wall_clock(tmp_path, monkeypatch):
    # 周六打开：行情日是周五 10-16；周一进池的第 5 天
    harness = build(tmp_path, monkeypatch, count=3, now=at("2026-10-17", "11:00"), day="2026-10-16")
    harness.kx.schema = 2
    harness.kx.first_seen = {"000000": "2026-10-12", "000001": "2026-10-16", "000002": "2026-10-17"}
    board = _board(harness)
    assert board["session"]["tradeDate"] == "2026-10-16"
    assert {code: row["heatDay"] for code, row in _by_code(board).items()} == {
        "000000": 5, "000001": 1, "000002": None}  # 周六才进的，行情日（周五）之前不算


def test_heat_day_is_null_when_the_pool_was_changed_before_the_quote_day_starts(tmp_path, monkeypatch):
    # 周一 09:00：KX 08:00 刚换了名单，今天新进的 first_seen 是今天，行情日还是上周五
    harness = build(tmp_path, monkeypatch, count=2, now=at("2026-10-19", "09:00"), day="2026-10-16")
    harness.kx.schema = 3
    harness.kx.first_seen = {"000000": "2026-10-19", "000001": "2026-10-15"}
    board = _board(harness)
    assert board["session"]["tradeDate"] == "2026-10-16"
    assert {code: row["heatDay"] for code, row in _by_code(board).items()} == {"000000": None, "000001": 2}


def test_heat_day_uses_today_from_0915(tmp_path, monkeypatch):
    # 周一 09:15：行情日换成当天。09:00 时同一只还按上周五算（见上一个测试）。
    harness = build(tmp_path, monkeypatch, count=2, now=at("2026-10-19", "09:15"))
    harness.kx.schema = 3
    harness.kx.first_seen = {"000000": "2026-10-19", "000001": "2026-10-15"}
    board = _board(harness)
    assert board["session"]["tradeDate"] == "2026-10-19"
    assert board["stats"]["asOf"] == "2026-10-16"
    assert {code: row["heatDay"] for code, row in _by_code(board).items()} == {"000000": 1, "000001": 3}


# ------------------------------------------------------------------ heatDay 算法

D = date.fromisoformat


@pytest.mark.parametrize("first_seen,end,expected", [
    ("2026-10-20", "2026-10-20", 1),    # 同一天：第 1 天
    ("2026-10-19", "2026-10-20", 2),    # 周一到周二
    ("2026-10-16", "2026-10-19", 2),    # 跨周末：周五、周一
    ("2026-10-16", "2026-10-20", 3),
    ("2026-10-12", "2026-10-16", 5),    # 整周
    ("2026-10-09", "2026-10-19", 7),    # 10-09、12、13、14、15、16、19（10-10 是调休补班的周六，休市）
    ("2026-09-24", "2026-09-28", 2),    # 中秋 9/25–9/27 休市
    ("2026-09-30", "2026-10-08", 2),    # 国庆 10/1–10/7 休市
    ("2026-10-17", "2026-10-19", 1),    # first_seen 是周六：从下一个交易日（周一）起算
    ("2026-10-17", "2026-10-20", 2),
    ("2026-10-18", "2026-10-19", 1),    # 周日
    ("2026-10-01", "2026-10-08", 1),    # first_seen 在节假日：从节后第一个交易日起算
    ("2026-10-10", "2026-10-12", 1),    # 调休补班的周六：股市休市
    ("2026-10-21", "2026-10-20", None),  # 晚于行情日
    ("2026-10-24", "2026-10-23", None),  # 周六进的、行情日是周五：起算日（下周一）晚于行情日
    ("2026-10-02", "2026-10-02", None),  # 起算日（10-08）晚于「行情日」
])
def test_heat_days_counts_trading_days_inclusive(first_seen, end, expected):
    assert heat_days(D(first_seen), D(end)) == expected


def test_heat_days_is_null_when_the_calendar_has_a_gap():
    assert heat_days(D("2024-12-30"), D("2026-10-20")) is None      # first_seen 的年份不在静态休市表里
    assert heat_days(D("2026-12-29"), D("2027-01-05")) is None      # 数到一半跨进没覆盖的年份
    assert heat_days(D("2026-12-30"), D("2026-12-31")) == 2         # 表内的最后两天正常


def test_heat_days_matches_a_brute_force_count_over_a_whole_year():
    from datetime import timedelta
    from neckline.morning.schedule import is_trading_day
    days = [D("2026-01-01") + timedelta(days=n) for n in range(365)]
    trading = [day for day in days if is_trading_day(day)]
    for end in (D("2026-10-20"), D("2026-12-31")):
        for first_seen in days:
            expected = len([day for day in trading if first_seen <= day <= end]) or None
            assert heat_days(first_seen, end) == expected, (first_seen, end)
