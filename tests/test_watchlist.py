"""自选池 CRUD + 同花顺 txt 互转/对账单测(plan §五 v1.1-C.1/C.4)。"""

from __future__ import annotations

import pytest

from neckline.watchlist import (
    MAX_WATCHLIST_SIZE,
    SOURCE_INQUIRY,
    WatchlistFullError,
    add_watchlist,
    export_ths_txt,
    get_watchlist_item,
    list_watchlist,
    list_watchlist_codes,
    parse_ths_txt,
    reconcile_ths,
    remove_watchlist,
    set_pinned,
)

pytestmark = pytest.mark.usefixtures("isolated_env")


class TestCrud:
    def test_add_and_list(self, isolated_env):
        item = add_watchlist("600001.SH", name="示例甲", db_path=isolated_env.db_path)
        assert item.ts_code == "600001.SH" and item.name == "示例甲" and item.pinned is False
        assert item.source == "manual"
        items = list_watchlist(db_path=isolated_env.db_path)
        assert [i.ts_code for i in items] == ["600001.SH"]

    def test_add_normalizes_bare_code(self, isolated_env):
        """加自选传裸代码 / 带后缀均能归一(复用 `review.parse.normalize_ts_code`,
        不新造正则)。"""
        item = add_watchlist("600001", db_path=isolated_env.db_path)
        assert item.ts_code == "600001.SH"

    def test_add_source_defaults_and_custom(self, isolated_env):
        item = add_watchlist("600002.SH", source=SOURCE_INQUIRY, db_path=isolated_env.db_path)
        assert item.source == "inquiry"

    def test_add_invalid_source_falls_back_to_manual(self, isolated_env):
        item = add_watchlist("600002.SH", source="bogus", db_path=isolated_env.db_path)
        assert item.source == "manual"

    def test_get_missing_returns_none(self, isolated_env):
        assert get_watchlist_item("999999.SH", db_path=isolated_env.db_path) is None

    def test_remove_existing_and_missing(self, isolated_env):
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        assert remove_watchlist("600001.SH", db_path=isolated_env.db_path) is True
        assert list_watchlist(db_path=isolated_env.db_path) == []
        assert remove_watchlist("600001.SH", db_path=isolated_env.db_path) is False  # 已删,再删返 False

    def test_set_pinned_existing_and_missing(self, isolated_env):
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        assert set_pinned("600001.SH", True, db_path=isolated_env.db_path) is True
        assert get_watchlist_item("600001.SH", db_path=isolated_env.db_path).pinned is True
        assert set_pinned("600001.SH", False, db_path=isolated_env.db_path) is True
        assert get_watchlist_item("600001.SH", db_path=isolated_env.db_path).pinned is False
        assert set_pinned("999999.SH", True, db_path=isolated_env.db_path) is False

    def test_readd_existing_code_updates_note_not_duplicate(self, isolated_env):
        add_watchlist("600001.SH", name="旧名", note="旧备注", db_path=isolated_env.db_path)
        item = add_watchlist("600001.SH", name="新名", note="新备注", db_path=isolated_env.db_path)
        assert item.name == "新名" and item.note == "新备注"
        assert len(list_watchlist(db_path=isolated_env.db_path)) == 1

    def test_list_watchlist_codes_matches_list_watchlist(self, isolated_env):
        add_watchlist("600001.SH", db_path=isolated_env.db_path)
        add_watchlist("600002.SH", db_path=isolated_env.db_path)
        assert set(list_watchlist_codes(db_path=isolated_env.db_path)) == {"600001.SH", "600002.SH"}


class TestMaxSizeEnforcement:
    """≤30 上限服务端硬校验(任务拍板「超限 422」——422 转换在 API 层,本模块只负责
    抛 `WatchlistFullError`)。"""

    def test_adding_30th_succeeds_31st_raises(self, isolated_env):
        for i in range(MAX_WATCHLIST_SIZE):
            add_watchlist(f"{600000 + i:06d}.SH", db_path=isolated_env.db_path)
        assert len(list_watchlist(db_path=isolated_env.db_path)) == MAX_WATCHLIST_SIZE
        with pytest.raises(WatchlistFullError):
            add_watchlist("999999.SH", db_path=isolated_env.db_path)
        # 拒绝后仍是 30(未越界写入半条记录)
        assert len(list_watchlist(db_path=isolated_env.db_path)) == MAX_WATCHLIST_SIZE

    def test_readd_existing_code_at_full_capacity_does_not_raise(self, isolated_env):
        """已满 30 时,对**已存在**的代码重新调用 `add_watchlist`(改备注等)不应
        被当成"新增"拒绝——只有真正的新增才占额度、才可能报满。"""
        for i in range(MAX_WATCHLIST_SIZE):
            add_watchlist(f"{600000 + i:06d}.SH", db_path=isolated_env.db_path)
        item = add_watchlist("600000.SH", note="改备注", db_path=isolated_env.db_path)
        assert item.note == "改备注"
        assert len(list_watchlist(db_path=isolated_env.db_path)) == MAX_WATCHLIST_SIZE

    def test_remove_then_add_succeeds_after_full(self, isolated_env):
        for i in range(MAX_WATCHLIST_SIZE):
            add_watchlist(f"{600000 + i:06d}.SH", db_path=isolated_env.db_path)
        remove_watchlist("600000.SH", db_path=isolated_env.db_path)
        add_watchlist("999999.SH", db_path=isolated_env.db_path)   # 腾出位置后可加
        assert len(list_watchlist(db_path=isolated_env.db_path)) == MAX_WATCHLIST_SIZE


class TestThsTxtParsing:
    def test_parse_bare_codes_one_per_line(self):
        text = "600000\n000001\n300750\n"
        assert parse_ths_txt(text.encode("utf-8")) == ["600000.SH", "000001.SZ", "300750.SZ"]

    def test_parse_codes_with_market_suffix(self):
        text = "600000.SH\n000001.SZ\n920100.BJ\n"
        assert parse_ths_txt(text.encode("utf-8")) == ["600000.SH", "000001.SZ", "920100.BJ"]

    def test_parse_skips_blank_lines_and_unrecognized_lines(self):
        text = "600000\n\n   \n某些说明性文字,非代码行\n000001\n"
        assert parse_ths_txt(text.encode("utf-8")) == ["600000.SH", "000001.SZ"]

    def test_parse_dedupes_preserving_first_seen_order(self):
        text = "600000\n000001\n600000\n"
        assert parse_ths_txt(text.encode("utf-8")) == ["600000.SH", "000001.SZ"]

    def test_parse_gbk_encoded_file(self):
        """同花顺 PC 端(Windows)真实导出编码未经活体验证,保守兼容 GBK。"""
        text = "600000\n000001\n"
        assert parse_ths_txt(text.encode("gbk")) == ["600000.SH", "000001.SZ"]

    def test_parse_utf8_bom_file(self):
        text = "600000\n000001\n"
        assert parse_ths_txt(text.encode("utf-8-sig")) == ["600000.SH", "000001.SZ"]

    def test_parse_empty_bytes_returns_empty_list(self):
        assert parse_ths_txt(b"") == []

    def test_parse_code_with_trailing_name_tab_separated(self):
        """兼容"代码+制表符+名称"这类未经活体验证前无法排除的真实变体——只取
        行首 6 位数字,忽略其后内容。"""
        text = "600000\t浦发银行\n000001\t平安银行\n"
        assert parse_ths_txt(text.encode("utf-8")) == ["600000.SH", "000001.SZ"]


class TestThsExport:
    def test_export_matches_native_ts_code_format(self):
        text = export_ths_txt(["600000.SH", "000001.SZ", "920100.BJ"])
        assert text == "600000.SH\n000001.SZ\n920100.BJ\n"

    def test_export_empty_list_returns_empty_string(self):
        assert export_ths_txt([]) == ""

    def test_round_trip_export_then_parse(self):
        codes = ["600000.SH", "000001.SZ", "300750.SZ", "920100.BJ"]
        text = export_ths_txt(codes)
        assert parse_ths_txt(text.encode("utf-8")) == codes


class TestReconcile:
    def test_only_in_ths_only_in_neckline_and_both(self):
        ths = ["600000.SH", "000001.SZ", "300750.SZ"]
        neckline = ["000001.SZ", "300750.SZ", "600519.SH"]
        diff = reconcile_ths(ths, neckline)
        assert diff["onlyInThs"] == ["600000.SH"]
        assert diff["onlyInNeckline"] == ["600519.SH"]
        assert diff["both"] == ["000001.SZ", "300750.SZ"]

    def test_identical_sets_yields_empty_diffs(self):
        codes = ["600000.SH", "000001.SZ"]
        diff = reconcile_ths(codes, codes)
        assert diff["onlyInThs"] == [] and diff["onlyInNeckline"] == []
        assert diff["both"] == sorted(codes)   # reconcile_ths 三个列表均排序输出

    def test_empty_both_sides(self):
        diff = reconcile_ths([], [])
        assert diff == {"onlyInThs": [], "onlyInNeckline": [], "both": []}
