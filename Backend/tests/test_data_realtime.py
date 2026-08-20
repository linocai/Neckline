"""盘中实时源单测(plan 阶段3 §2.4)。样例报文取自 LinoN 已验证的真源实测快照
(`/Users/linotsai/Lino/LinoN/backend/tests/test_realtime.py`,2026-06-18 收盘,
`603986` 兆易创新)——不重新造一份未经真实校验的报文,直接复用同一份真实样本。
ST 样例为合成(改名 *ST,验证板块正常股与 ST 的涨跌停幅度差异走向哪条分支,
但涨跌停价的实际计算已移交 `neckline.data.limit_derived`,本模块不再算)。

网络层用 `httpx.MockTransport` 免联网(姿势沿用 `neckline.llm.openai_compat`
已确立的 MockTransport 传统),不再像 LinoN 那样 monkeypatch 内部 `_fetch_*` 函数。
"""

from __future__ import annotations

import httpx

from neckline.data import realtime as q
from neckline.data.realtime import get_quote, get_quotes, to_symbol

# —— 真源样例(与 LinoN test_realtime.py 完全一致的真实报文,2026-06-18 收盘快照)——
SINA_BODY = (
    "兆易创新,594.000,586.040,629.000,644.640,586.600,628.980,629.000,"
    "59157318,37016387910.000,500,628.980,2700,628.900,200,628.890,1100,"
    "628.880,200,628.860,146318,629.000,200,629.310,400,629.470,700,629.900,"
    "1000,629.910,2026-06-18,15:00:00,00,"
)
TENC_BODY = (
    "1~兆易创新~603986~629.00~586.04~594.00~591573~323172~268402~628.98~5~"
    "628.90~27~628.89~2~628.88~11~628.86~2~629.00~1463~629.31~2~629.47~4~"
    "629.90~7~629.91~10~~20260618161400~42.96~7.33~644.64~586.60~"
    "629.00/591573/37016387910~591573~3701639~8.86~153.41~~644.64~586.60~"
    "9.90~4200.74~4409.93~17.82~644.64~527.44~1.36~-1439~625.73~75.45~267.59"
)
SINA_ST_BODY = (
    "*ST示例,10.000,10.000,9.500,10.500,9.500,9.490,9.500,"
    "1000000,9500000.000,500,9.490,0,0,0,0,0,0,0,0,1000,9.500,0,0,0,0,0,0,0,0,"
    "2026-06-18,15:00:00,00,"
)


def _sina_transport(body_by_symbol: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        text = "".join(f'var hq_str_{sym}="{body}";\n' for sym, body in body_by_symbol.items())
        return httpx.Response(200, content=text.encode("gbk"))

    return httpx.MockTransport(handler)


def _tencent_transport(body_by_symbol: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        text = "".join(f'v_{sym}="{body}";\n' for sym, body in body_by_symbol.items())
        return httpx.Response(200, content=text.encode("gbk"))

    return httpx.MockTransport(handler)


def _failover_transport(sina_body_by_symbol: dict, tencent_body_by_symbol: dict) -> httpx.MockTransport:
    """一个 transport 同时应付新浪(hq.sinajs.cn)与腾讯(qt.gtimg.cn)两个不同 URL,
    按 request.url.host 分流——`get_quotes` 内部两源各发一次请求,单个 transport
    需要能区分两次调用打给了谁。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "sinajs" in str(request.url):
            text = "".join(f'var hq_str_{sym}="{body}";\n' for sym, body in sina_body_by_symbol.items())
        else:
            text = "".join(f'v_{sym}="{body}";\n' for sym, body in tencent_body_by_symbol.items())
        return httpx.Response(200, content=text.encode("gbk"))

    return httpx.MockTransport(handler)


class TestToSymbol:
    def test_shanghai_main_board(self):
        assert to_symbol("600519") == "sh600519"

    def test_shenzhen_main_and_gem(self):
        assert to_symbol("000001") == "sz000001"
        assert to_symbol("300750") == "sz300750"

    def test_star_board_is_shanghai(self):
        assert to_symbol("688981") == "sh688981"
        assert to_symbol("689009") == "sh689009"  # 科创板 CDR,§3.7 黑名单坑教训:不能漏689

    def test_bse_prefixes_map_to_bj(self):
        assert to_symbol("920819") == "bj920819"
        assert to_symbol("830799") == "bj830799"
        assert to_symbol("430047") == "bj430047"

    def test_already_prefixed_returned_as_is(self):
        assert to_symbol("sh600519") == "sh600519"
        assert to_symbol("bj920819") == "bj920819"

    def test_ts_code_suffix_wins_for_indexes(self):
        """V2-⑧-A:关注池开始装**指数**代码,前缀启发式对它们会错得很安静 ——
        `000001.SH`(上证综指)按数字前缀会被判成 `sz000001`(平安银行),拉回来的
        是另一个标的。后缀优先修的就是这一类。"""
        assert to_symbol("000001.SH") == "sh000001"     # 上证综指,不是平安银行
        assert to_symbol("000688.SH") == "sh000688"     # 科创50 指数
        assert to_symbol("399006.SZ") == "sz399006"     # 创业板指
        assert to_symbol("399001.SZ") == "sz399001"     # 深证成指
        assert to_symbol("899050.BJ") == "bj899050"     # 北证50

    def test_suffix_change_is_a_noop_for_every_stock_code_shape(self):
        """阴性方向:**股票**代码带不带后缀,结果逐位相同(改动对既有路径零影响)。"""
        for bare, suffix in [("600519", "SH"), ("603986", "SH"), ("000001", "SZ"),
                             ("300750", "SZ"), ("688981", "SH"), ("689009", "SH"),
                             ("920819", "BJ"), ("830799", "BJ"), ("430047", "BJ")]:
            assert to_symbol(f"{bare}.{suffix}") == to_symbol(bare)


class TestParseSina:
    def test_normal_quote_unit_normalization(self):
        q_ = q._parse_sina("sh603986", SINA_BODY)
        assert q_ is not None
        assert q_.source == "sina"
        assert q_.code == "603986"
        assert q_.name == "兆易创新"
        assert q_.price == 629.0
        assert q_.pre_close == 586.04
        assert q_.open == 594.0
        assert q_.high == 644.64
        assert q_.low == 586.6
        # 单位归一:sina volume 股 → 手(÷100);amount 元原样
        assert q_.volume == round(59157318 / 100, 2)
        assert q_.amount == 37016387910.0
        assert q_.bid[0] == 628.98  # 量先价后:bid1 价在 index 12(11+0*2)
        assert q_.ask[0] == 629.0
        assert q_.ts == "2026-06-18 15:00:00"

    def test_st_name_parses_fine(self):
        """*ST 报文本身解析不受影响——涨跌停幅度分支已移交 limit_derived,
        Quote 层只管把名称/价格如实解析出来。"""
        q_ = q._parse_sina("sh600000", SINA_ST_BODY)
        assert q_ is not None
        assert "ST" in q_.name.upper()
        assert q_.pre_close == 10.0

    def test_empty_or_short_body_returns_none(self):
        assert q._parse_sina("sh603986", "") is None
        assert q._parse_sina("sh603986", "兆易创新,594") is None


class TestParseTencent:
    def test_normal_quote_unit_normalization(self):
        q_ = q._parse_tencent("sh603986", TENC_BODY)
        assert q_ is not None
        assert q_.source == "tencent"
        assert q_.code == "603986"
        assert q_.price == 629.0
        assert q_.pre_close == 586.04
        assert q_.open == 594.0
        assert q_.high == 644.64
        assert q_.low == 586.6
        # tencent volume 手原样;amount 万元 → 元(×1e4)
        assert q_.volume == 591573.0
        assert q_.amount == round(3701639 * 10000, 2)
        assert q_.bid[0] == 628.98  # 价先量后:bid1 价在 index 9
        assert q_.ask[0] == 629.0
        assert q_.ts == "2026-06-18 16:14:00"

    def test_short_body_returns_none(self):
        assert q._parse_tencent("sh603986", "1~~~") is None


class TestGetQuotesFailoverViaHttpMock:
    def test_sina_primary_wins(self):
        transport = _failover_transport({"sh603986": SINA_BODY}, {})
        out = get_quotes(["603986"], transport=transport)
        assert out["603986"].source == "sina"
        assert out["603986"].price == 629.0

    def test_tencent_fills_in_when_sina_missing(self):
        transport = _failover_transport({}, {"sh603986": TENC_BODY})
        out = get_quotes(["603986"], transport=transport)
        assert out["603986"].source == "tencent"

    def test_both_sources_down_returns_empty_not_crash(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(500))
        assert get_quotes(["603986"], transport=transport) == {}
        assert get_quote("603986", transport=transport) is None

    def test_batch_mixed_sources(self):
        transport = _failover_transport(
            {"sh603986": SINA_BODY},
            {"sz000001": TENC_BODY.replace("603986", "000001").replace("兆易创新", "平安银行")},
        )
        out = get_quotes(["603986", "000001"], transport=transport)
        assert set(out.keys()) == {"603986", "000001"}
        assert out["603986"].source == "sina"
        assert out["000001"].source == "tencent"

    def test_empty_codes_returns_empty_without_network(self):
        def handler(request):
            raise AssertionError("空列表不应发起网络请求")

        assert get_quotes([], transport=httpx.MockTransport(handler)) == {}


class TestChunking:
    def test_large_batch_split_into_multiple_requests(self, monkeypatch):
        """codes 超过 `_CHUNK_SIZE` 时应分块请求(仍是"批量",不是逐票请求)。"""
        calls = {"sina": 0}

        def fake_fetch_sina(symbols, transport):
            calls["sina"] += 1
            assert len(symbols) <= q._CHUNK_SIZE
            return {}

        monkeypatch.setattr(q, "_fetch_sina", fake_fetch_sina)
        monkeypatch.setattr(q, "_fetch_tencent", lambda symbols, transport: {})

        codes = [f"{600000 + i}" for i in range(q._CHUNK_SIZE + 50)]
        get_quotes(codes)
        assert calls["sina"] == 2  # 一批 _CHUNK_SIZE + 一批余数


class TestQuoteToDict:
    def test_to_dict_shape(self):
        q_ = q._parse_sina("sh603986", SINA_BODY)
        d = q_.to_dict()
        assert d["code"] == "603986"
        assert d["source"] == "sina"
        assert isinstance(d["bid"], list)
