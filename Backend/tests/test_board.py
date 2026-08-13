"""板块分类单测(plan 0.4b)。"""

from __future__ import annotations

from neckline.data.board import Board, classify, classify_by_code


class TestClassifyByCode:
    def test_star_688_689(self):
        assert classify_by_code("688981.SH") == Board.STAR
        assert classify_by_code("689009.SH") == Board.STAR  # CDR,继承 LinoN 教训:勿漏 689

    def test_gem_covers_subsegments(self):
        # 继承 LinoN 教训:板块整段正则,勿枚举精确子段(旧写法 "300" 会漏 301/302)
        assert classify_by_code("300750.SZ") == Board.GEM
        assert classify_by_code("301051.SZ") == Board.GEM
        assert classify_by_code("302132.SZ") == Board.GEM

    def test_bse_prefixes(self):
        assert classify_by_code("830799.BJ") == Board.BSE
        assert classify_by_code("430047.BJ") == Board.BSE
        assert classify_by_code("920099.BJ") == Board.BSE

    def test_main_default(self):
        assert classify_by_code("600519.SH") == Board.MAIN
        assert classify_by_code("000001.SZ") == Board.MAIN
        assert classify_by_code("900901.SH") == Board.MAIN  # B股,默认落主板机制

    def test_order_star_before_bse_prefix(self):
        # 688/689 若被 "8" 前缀 fallback 先吞会误判成 BSE,顺序必须 STAR 优先
        assert classify_by_code("688001.SH") != Board.BSE


class TestClassifyPrefersMarketField:
    def test_market_field_used_when_present(self):
        assert classify("主板", "300750.SZ") == Board.MAIN  # 字段优先于前缀(理论不会真出现这种矛盾数据,但测契约)
        assert classify("创业板", "300750.SZ") == Board.GEM
        assert classify("科创板", "688001.SH") == Board.STAR
        assert classify("北交所", "920099.BJ") == Board.BSE

    def test_fallback_to_code_when_market_missing(self):
        assert classify(None, "688001.SH") == Board.STAR
        assert classify("", "300750.SZ") == Board.GEM
        assert classify("未知板块", "600519.SH") == Board.MAIN
