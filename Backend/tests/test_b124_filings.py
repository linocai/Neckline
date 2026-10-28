"""B124：公告 PDF 下载、读文本、读主要财务数字。

夹具是 pypdf 从真实巨潮公告抽出的文本，另有一份小于 120KB 的业绩快报 PDF。
下载测试只用 MockTransport，不访问网络。
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter

from neckline.filings.cli import main
from neckline.filings.fetch import fetch_pdf
from neckline.filings.financials import (
    filing_kind, parse_express, parse_forecast, parse_periodic, summary_text,
)
from neckline.filings.reader import headings, opening, read_pdf

FIX = Path(__file__).parent / "fixtures" / "filings"
PDF = "http://static.cninfo.com.cn/finalpage/2026-10-22/1225613658.PDF"


def text(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def yuan(actual: float | None, expected: float) -> None:
    assert actual == pytest.approx(expected, abs=0.02)


def pct(actual: float | None, expected: float) -> None:
    assert actual == pytest.approx(expected, abs=0.001)


class Gate:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_fetch_rejects_urls_outside_cninfo_static(tmp_path: Path) -> None:
    gate = Gate(httpx.Response(200, content=b"%PDF-1.7"))
    blocked = (
        "http://www.cninfo.com.cn/finalpage/2026-10-22/1.PDF",
        "http://static.cninfo.com.cn.evil/a.PDF",
        "https://evil.cninfo.com.cn/a.PDF",
        "http://static.cninfo.com.cn/finalpage/2026-10-22/1.txt",
        "ftp://static.cninfo.com.cn/a.PDF",
        "http://user:secret@static.cninfo.com.cn/a.PDF",
    )
    for url in blocked:
        got = fetch_pdf(url, cache_dir=tmp_path / "empty", max_bytes=1000, timeout_seconds=1,
                        transport=httpx.MockTransport(gate))
        assert got.error == "url_not_allowed"
        assert got.path is None and got.from_cache is False
    assert gate.calls == 0
    assert not (tmp_path / "empty").exists()


def test_fetch_caches_pdf_and_stops_when_too_large_or_not_pdf(tmp_path: Path) -> None:
    body = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF"
    gate = Gate(httpx.Response(200, content=body))
    cache = tmp_path / "cache"
    got = fetch_pdf(PDF, cache_dir=cache, max_bytes=1000, timeout_seconds=5, transport=httpx.MockTransport(gate))
    assert got.error is None and got.from_cache is False
    assert got.path is not None and got.path.name == "2026-10-22_1225613658.pdf"
    assert got.path.read_bytes() == body
    assert gate.calls == 1

    gate.response = httpx.Response(500, content=b"no")
    again = fetch_pdf(PDF, cache_dir=cache, max_bytes=1000, timeout_seconds=5, transport=httpx.MockTransport(gate))
    assert again.from_cache is True and again.path == got.path and again.error is None
    assert gate.calls == 1

    gate.response = httpx.Response(200, content=body)
    lower = fetch_pdf("https://static.cninfo.com.cn/finalpage/2026-08-28/1225521524.pdf",
                      cache_dir=cache, max_bytes=1000, timeout_seconds=5, transport=httpx.MockTransport(gate))
    assert lower.error is None and lower.path is not None and lower.path.name == "2026-08-28_1225521524.pdf"

    huge = Gate(httpx.Response(200, content=b"%PDF" + b"x" * 50))
    denied = fetch_pdf(PDF.replace("1225613658", "9"), cache_dir=cache, max_bytes=20, timeout_seconds=5,
                       transport=httpx.MockTransport(huge))
    assert denied.error == "too_large" and denied.path is None
    assert not list(cache.glob("*.part"))
    assert not (cache / "2026-10-22_9.pdf").exists()

    fake = Gate(httpx.Response(200, content=b"not a pdf"))
    parsed = fetch_pdf(PDF.replace("1225613658", "8"), cache_dir=cache, max_bytes=1000, timeout_seconds=5,
                       transport=httpx.MockTransport(fake))
    assert parsed.error == "not_pdf" and parsed.path is None


def test_fetch_reports_http_timeout_and_transport_errors(tmp_path: Path) -> None:
    cases = (
        (httpx.Response(404, content=b"missing"), "http_error"),
        (httpx.Response(502, content=b"bad"), "http_error"),
        (httpx.ReadTimeout("timed out"), "timeout"),
        (httpx.ConnectError("down"), "transport_error"),
    )
    for response, code in cases:
        gate = Gate(response)
        got = fetch_pdf(PDF, cache_dir=tmp_path / code, max_bytes=1000, timeout_seconds=1,
                        transport=httpx.MockTransport(gate))
        assert got.error == code and got.path is None and got.from_cache is False
        assert gate.calls == 1


def test_read_real_express_pdf_and_reject_unreadable() -> None:
    pdf = FIX / "express-jianxin.pdf"
    assert pdf.stat().st_size < 120 * 1024
    got = read_pdf(pdf, max_pages=5)
    assert got.error is None and got.page_count == 2 and got.truncated is False
    joined = "\n".join(got.pages)
    assert "建新股份" in joined and "营业总收入" in joined
    assert "一、2026 年前三季度主要财务数据和指标" in headings(joined)
    short = read_pdf(pdf, max_pages=1)
    assert short.truncated is True and short.error is None and "建新" in short.pages[0]
    assert opening(joined, 8) == joined[:8]
    assert opening(joined, 0) == ""


def test_read_blank_pdf_is_no_text_and_garbage_is_unreadable(tmp_path: Path) -> None:
    blank = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with blank.open("wb") as handle:
        writer.write(handle)
    got = read_pdf(blank, max_pages=3)
    assert got.error == "no_text" and got.page_count == 1 and got.truncated is False
    garbage = tmp_path / "bad.pdf"
    garbage.write_bytes(b"this is not a pdf")
    assert read_pdf(garbage, max_pages=2).error == "pdf_unreadable"


def test_headings_cover_common_levels() -> None:
    found = headings("一、主要财务数据\n（一）主要会计数据和财务指标\n第二节 公司简介\n1、交易概述\n正文 1.5 不是标题\n")
    assert found == ["一、主要财务数据", "（一）主要会计数据和财务指标", "第二节 公司简介", "1、交易概述"]
    assert "一、主要财务数据" in headings(text("q3-guangli.txt"))
    assert "（一）交易对价的支付情况" in headings(text("none-guanghetong.txt"))


def test_filing_kind_recognizes_reports_and_skips_non_body() -> None:
    assert filing_kind("光力科技：2026年第三季度报告") == "periodic"
    assert filing_kind("2026年三季度报告摘要") == "periodic"
    assert filing_kind("2026年半年度报告") == "periodic"
    assert filing_kind("2026年半年度报告摘要") == "periodic"
    assert filing_kind("2025年年度报告") == "periodic"
    assert filing_kind("2025年年度报告摘要") == "periodic"
    assert filing_kind("2026年第一季度报告") == "periodic"
    assert filing_kind("2026年一季度报告") == "periodic"
    assert filing_kind("杭州长川科技股份有限公司2026年前三季度业绩预告") == "forecast"
    assert filing_kind("河北建新化工股份有限公司2026年前三季度业绩快报") == "express"
    assert filing_kind("关于2026年半年度报告披露的提示性公告") is None
    assert filing_kind("2026年第一季度报告（更正后）") is None
    assert filing_kind("2025年年度报告（英文版）") is None
    assert filing_kind("2025年度内部控制审计报告") is None
    assert filing_kind("关于2025年年度报告问询函的回复") is None
    assert filing_kind("关于举行2025年年度报告网上业绩说明会的公告") is None
    assert filing_kind("关于2025年年度报告延期披露的公告") is None
    assert filing_kind("关于董事对2025年年度报告异议的说明") is None
    assert filing_kind("关于重大资产购买之标的资产完成过户的公告") is None
    assert filing_kind("关于出售控股子公司部分股权的公告") is None
    assert filing_kind("") is None


def test_parse_third_quarter_samples() -> None:
    samples = {
        "q3-guangli.txt": (
            (270833742.31, 57.39, 723535892.85, 57.29),
            (57230358.57, 404.65, 131399314.36, 259.80),
            (53427857.07, 424.48, 123299947.27, 490.17),
            (3802501.50, 8099367.09),
        ),
        "q3-yongqing.txt": (
            (152032590.32, 0.56, 529292040.09, 1.30),
            (37305990.62, 37.87, 124546205.32, 26.07),
            (21728298.14, 105.17, 90007324.31, 79.64),
            (15577692.48, 34538881.01),
        ),
        "q3-tongguang.txt": (
            (612261866.51, -10.24, 1853868782.74, -2.92),
            (-14414176.51, -199.80, -34016486.61, -205.86),
            (-13671205.72, -295.08, -33851915.57, -236.59),
            (-742970.79, -164571.04),
        ),
        "q3-hengguang.txt": (
            (454509206.97, 26.11, 1328768908.97, 19.85),
            (15998411.27, 272.90, 78531485.96, 4845.11),
            (14552549.01, 234.93, 75110345.05, 768.09),
            (1445862.26, 3421140.91),
        ),
    }
    for name, (revenue, net, deducted, nonrec) in samples.items():
        fig = parse_periodic(text(name))
        assert fig.period == "q3" and fig.missing == []
        for row, expected in ((fig.revenue, revenue), (fig.net_profit, net), (fig.deducted, deducted)):
            yuan(row.current, expected[0])
            pct(row.yoy, expected[1])
            yuan(row.ytd, expected[2])
            pct(row.ytd_yoy, expected[3])
            assert row.prior is None
        yuan(fig.non_recurring_current, nonrec[0])
        yuan(fig.non_recurring_ytd, nonrec[1])
        assert "未能读出" not in summary_text(fig)
    summary = summary_text(parse_periodic(text("q3-guangli.txt")))
    assert "营业收入 2.71 亿元（同比 +57.39%）" in summary
    assert "年初至报告期末 809.94 万元" in summary
    loss = summary_text(parse_periodic(text("q3-tongguang.txt")))
    assert "归属于上市公司股东的净利润 -1441.42 万元（同比 -199.80%）" in loss


def test_parse_half_year_annual_and_restated_first_quarter() -> None:
    linuo = parse_periodic(text("h1-linuo.txt"))
    assert linuo.period == "h1" and linuo.missing == []
    yuan(linuo.revenue.current, 480099613.04)
    yuan(linuo.revenue.prior, 406415807.46)
    pct(linuo.revenue.yoy, 18.13)
    yuan(linuo.net_profit.current, 8677667.36)
    yuan(linuo.net_profit.prior, 7189695.16)
    pct(linuo.net_profit.yoy, 20.70)
    yuan(linuo.deducted.current, 8128966.92)
    yuan(linuo.deducted.prior, 6819945.39)
    pct(linuo.deducted.yoy, 19.19)
    yuan(linuo.non_recurring_current, 548700.44)
    assert linuo.non_recurring_ytd is None
    assert "非经常性损益合计：54.87 万元" in summary_text(linuo)

    power = parse_periodic(text("h1-xindongli.txt"))
    assert power.period == "h1" and power.missing == []
    yuan(power.revenue.current, 119068362.75)
    yuan(power.revenue.prior, 96961184.39)
    pct(power.revenue.yoy, 22.80)
    yuan(power.net_profit.current, -5920267.23)
    yuan(power.net_profit.prior, -7286098.81)
    pct(power.net_profit.yoy, 18.75)
    yuan(power.deducted.current, -9002580.63)
    yuan(power.deducted.prior, -11986756.30)
    pct(power.deducted.yoy, 24.90)
    yuan(power.non_recurring_current, 3082313.40)

    haibo = parse_periodic(text("annual-haibo.txt"))
    assert haibo.period == "annual" and haibo.missing == []
    yuan(haibo.revenue.current, 233756722.76)
    yuan(haibo.revenue.prior, 419627045.02)
    assert haibo.revenue.prior != pytest.approx(415172353.93, abs=1)
    pct(haibo.revenue.yoy, -44.29)
    yuan(haibo.net_profit.current, 1780803.81)
    yuan(haibo.net_profit.prior, 23003575.38)
    pct(haibo.net_profit.yoy, -92.26)
    yuan(haibo.deducted.current, -4108129.24)
    yuan(haibo.deducted.prior, 9555807.62)
    pct(haibo.deducted.yoy, -142.99)
    yuan(haibo.non_recurring_current, 5888933.08)
    assert haibo.non_recurring_current != pytest.approx(13447767.76, abs=1)
    assert "上年 4.20 亿元，同比 -44.29%" in summary_text(haibo)

    liyuan = parse_periodic(text("annual-liyuan.txt"))
    assert liyuan.period == "annual" and liyuan.missing == []
    yuan(liyuan.revenue.current, 8866820810.38)
    yuan(liyuan.revenue.prior, 7820971608.02)
    pct(liyuan.revenue.yoy, 13.37)
    yuan(liyuan.net_profit.current, 171942473.63)
    yuan(liyuan.net_profit.prior, 98425340.30)
    pct(liyuan.net_profit.yoy, 74.69)
    yuan(liyuan.deducted.current, 168700541.96)
    yuan(liyuan.deducted.prior, 85698885.54)
    pct(liyuan.deducted.yoy, 96.85)
    yuan(liyuan.non_recurring_current, 3241931.67)

    maihe = parse_periodic(text("q1-maihe.txt"))
    assert maihe.period == "q1" and maihe.missing == []
    yuan(maihe.revenue.current, 256222554.64)
    yuan(maihe.revenue.prior, 342233428.75)
    pct(maihe.revenue.yoy, -25.13)
    yuan(maihe.net_profit.current, 18998123.10)
    yuan(maihe.net_profit.prior, 41086773.48)
    pct(maihe.net_profit.yoy, -53.76)
    yuan(maihe.deducted.current, 14908166.01)
    yuan(maihe.deducted.prior, 37011990.29)
    pct(maihe.deducted.yoy, -59.72)
    yuan(maihe.non_recurring_current, 4089957.09)
    assert "同比 -25.13%" in summary_text(maihe)


def test_parse_express_converts_wan_yuan() -> None:
    fig = parse_express(text("express-jianxin.txt"))
    assert fig.period == "2026年前三季度" and fig.missing == []
    yuan(fig.revenue.current, 57480.09 * 10000)
    yuan(fig.revenue.prior, 34417.84 * 10000)
    pct(fig.revenue.yoy, 67.01)
    yuan(fig.net_profit.current, 9895.98 * 10000)
    yuan(fig.net_profit.prior, 597.61 * 10000)
    pct(fig.net_profit.yoy, 1555.93)
    yuan(fig.deducted.current, 9803.44 * 10000)
    yuan(fig.deducted.prior, 485.00 * 10000)
    pct(fig.deducted.yoy, 1921.33)
    summary = summary_text(fig)
    assert "营业总收入 5.75 亿元（上年同期 3.44 亿元，同比 +67.01%）" in summary
    assert "9895.98 万元" in summary and "未能读出" not in summary


def test_parse_forecast_profit_loss_and_turnaround() -> None:
    profit = parse_forecast(text("forecast-changchuan.txt"))
    assert profit.period == "2026年1月1日至2026年9月30日" and profit.missing == []
    assert profit.net_profit.state == "盈利" and profit.deducted.state == "盈利"
    yuan(profit.net_profit.current.low, 171500 * 10000)
    yuan(profit.net_profit.current.high, 181500 * 10000)
    yuan(profit.net_profit.prior, 865401500)
    pct(profit.net_profit.yoy.low, 98.17)
    pct(profit.net_profit.yoy.high, 109.73)
    yuan(profit.deducted.current.low, 166700 * 10000)
    yuan(profit.deducted.current.high, 176700 * 10000)
    yuan(profit.deducted.prior, 788578400)
    pct(profit.deducted.yoy.low, 111.39)
    pct(profit.deducted.yoy.high, 124.07)
    assert "盈利 17.15 亿元至 18.15 亿元" in summary_text(profit)
    assert "+98.17%至+109.73%" in summary_text(profit)

    decline = parse_forecast(text("forecast-hangtian.txt"))
    assert decline.missing == [] and decline.net_profit.state == "盈利"
    yuan(decline.net_profit.current.low, 214736600)
    yuan(decline.net_profit.current.high, 292822600)
    yuan(decline.net_profit.prior, 650716900)
    pct(decline.net_profit.yoy.low, -67)
    pct(decline.net_profit.yoy.high, -55)
    yuan(decline.deducted.current.low, 197363900)
    yuan(decline.deducted.current.high, 246704800)
    yuan(decline.deducted.prior, 616762100)
    pct(decline.deducted.yoy.low, -68)
    pct(decline.deducted.yoy.high, -60)
    assert "-67.00%至-55.00%" in summary_text(decline)

    loss = parse_forecast(text("forecast-shenhao.txt"))
    assert loss.period == "2026年1月1日至2026年9月30日"
    assert loss.net_profit.state == "亏损" and loss.deducted.state == "亏损"
    yuan(loss.net_profit.current.low, -6000 * 10000)
    yuan(loss.net_profit.current.high, -5600 * 10000)
    yuan(loss.net_profit.prior, -128968300)
    yuan(loss.deducted.current.low, -7000 * 10000)
    yuan(loss.deducted.current.high, -6500 * 10000)
    yuan(loss.deducted.prior, -144378000)
    assert loss.net_profit.yoy.low is None and loss.deducted.yoy.high is None
    assert loss.missing == ["归母净利润同比下限", "归母净利润同比上限", "扣非净利润同比下限", "扣非净利润同比上限"]
    assert "亏损 5600.00 万元至 6000.00 万元" in summary_text(loss)

    turn = parse_forecast(text("forecast-dapuwei.txt"))
    assert turn.net_profit.state == "扭亏" and turn.deducted.state == "扭亏"
    yuan(turn.net_profit.current.low, 120000 * 10000)
    yuan(turn.net_profit.current.high, 135000 * 10000)
    yuan(turn.net_profit.prior, -353762000)
    yuan(turn.deducted.current.low, 119500 * 10000)
    yuan(turn.deducted.prior, -361488500)
    assert "扭亏 12.00 亿元至 13.50 亿元" in summary_text(turn)

    penghui = parse_forecast(text("forecast-penghui.txt"))
    assert penghui.net_profit.state == "扭亏"
    yuan(penghui.net_profit.current.low, 80000 * 10000)
    yuan(penghui.net_profit.current.high, 86600 * 10000)
    yuan(penghui.net_profit.prior, -88226700)
    yuan(penghui.deducted.current.low, 79200 * 10000)
    yuan(penghui.deducted.current.high, 85600 * 10000)
    yuan(penghui.deducted.prior, -159042300)

    xintian = parse_forecast(text("forecast-xintian.txt"))
    assert xintian.net_profit.state == "亏损"
    yuan(xintian.net_profit.current.low, -5080 * 10000)
    yuan(xintian.net_profit.current.high, -3680 * 10000)
    yuan(xintian.net_profit.prior, 11305300)
    yuan(xintian.deducted.current.low, -5200 * 10000)
    yuan(xintian.deducted.current.high, -3800 * 10000)
    yuan(xintian.deducted.prior, 8902700)

    mixed = parse_forecast(text("forecast-xinyan.txt"))
    assert mixed.net_profit.state == "扭亏" and mixed.deducted.state == "亏损"
    yuan(mixed.net_profit.current.low, 2600 * 10000)
    yuan(mixed.net_profit.current.high, 3300 * 10000)
    yuan(mixed.net_profit.prior, -168407800)
    assert mixed.net_profit.yoy.low is None
    yuan(mixed.deducted.current.low, -5500 * 10000)
    yuan(mixed.deducted.current.high, -4300 * 10000)
    yuan(mixed.deducted.prior, -150754400)
    pct(mixed.deducted.yoy.low, 63.52)
    pct(mixed.deducted.yoy.high, 71.48)
    assert mixed.missing == ["归母净利润同比下限", "归母净利润同比上限"]


def test_non_financial_and_truncated_text_leave_fields_missing() -> None:
    for name in ("none-guanghetong.txt", "none-kaichuang.txt"):
        raw = text(name)
        periodic = parse_periodic(raw)
        assert periodic.period is None and periodic.revenue.current is None
        assert periodic.net_profit.current is None and periodic.missing == ["报告类型"]
        forecast = parse_forecast(raw)
        assert forecast.net_profit.current.low is None and forecast.deducted.prior is None
        express = parse_express(raw)
        assert express.revenue.current is None and express.net_profit.current is None

    raw = text("q3-guangli.txt")
    cut = raw[:raw.find("归属于上市公司股东")]
    fig = parse_periodic(cut)
    assert fig.period == "q3"
    yuan(fig.revenue.current, 270833742.31)
    pct(fig.revenue.yoy, 57.39)
    yuan(fig.revenue.ytd, 723535892.85)
    assert fig.net_profit.current is None and fig.deducted.ytd is None
    assert fig.non_recurring_current is None
    assert "营业收入本报告期" not in fig.missing
    assert "归母净利润本报告期" in fig.missing
    assert "扣非净利润年初至报告期末同比" in fig.missing
    assert "非经常性损益合计本报告期" in fig.missing
    assert "未能读出" in summary_text(fig)
    assert "2.71 亿元" in summary_text(fig)


def test_cli_prints_local_pdf_and_rejects_other_hosts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIX / "express-jianxin.pdf"), "--cache-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "kind: express" in out
    assert "5.75 亿元" in out and "9895.98 万元" in out
    assert main(["http://example.com/a.PDF", "--cache-dir", str(tmp_path)]) == 0
    assert "error: url_not_allowed" in capsys.readouterr().out


def test_concurrent_downloads_of_the_same_pdf_do_not_collide(tmp_path: Path) -> None:
    """几条候选同时下载同一份公告：各用各的临时文件，都拿到完整文件，不出现虚假的下载失败。"""
    import threading
    import time

    url = "http://static.cninfo.com.cn/finalpage/2026-10-22/1225613658.PDF"
    body = b"%PDF-1.7\n" + b"x" * 4096

    def handler(_request: httpx.Request) -> httpx.Response:
        time.sleep(0.05)  # 让几条下载在途时重叠
        return httpx.Response(200, content=body)

    results = []

    def one() -> None:
        results.append(fetch_pdf(url, cache_dir=tmp_path, max_bytes=1_000_000, timeout_seconds=5,
                                 transport=httpx.MockTransport(handler)))

    threads = [threading.Thread(target=one) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(result.error is None and result.path is not None for result in results)
    assert {result.path for result in results} == {tmp_path / "2026-10-22_1225613658.pdf"}
    assert (tmp_path / "2026-10-22_1225613658.pdf").read_bytes() == body
    assert not list(tmp_path.glob(".*.part"))


FIXED_FORECAST = """证券代码：300001 证券简称：某某科技 公告编号：2026-050
某某科技股份有限公司 2026年前三季度业绩预告
一、本期业绩预计情况
1．业绩预告期间：2026年1月1日至2026年9月30日
2．预计的业绩：□亏损 □扭亏为盈 √同向上升 □同向下降
项目 本报告期 上年同期
归属于上市公司股东的净利润 盈利：3,000万元 盈利：1,500万元
比上年同期增长：100%
扣除非经常性损益后的净利润 盈利：2,500万元–3,500万元 盈利：1,000万元
"""


def test_fixed_value_forecast_reads_current_and_prior_in_column_order() -> None:
    """定值预告：本期是确定金额。不能把本期金额当成上年同期。"""
    figures = parse_forecast(FIXED_FORECAST)
    assert figures.net_profit.current.low == figures.net_profit.current.high == 30_000_000
    assert figures.net_profit.prior == 15_000_000 and figures.net_profit.yoy.low == 100
    assert figures.deducted.current.low == 25_000_000 and figures.deducted.prior == 10_000_000
    text = summary_text(figures)
    assert "盈利 3000.00 万元（上年同期 1500.00 万元，同比 +100.00%）" in text


def test_single_amount_without_a_range_is_left_missing() -> None:
    """只有一个金额又没有区间时，分不清是本期还是上年同期，留缺失，不猜。"""
    text = FIXED_FORECAST.replace("盈利：3,000万元 盈利：1,500万元", "盈利：1,500万元")
    figures = parse_forecast(text)
    assert figures.net_profit.current.low is None and figures.net_profit.prior is None


def test_q3_rows_with_not_applicable_yoy_still_read_the_amounts() -> None:
    """扭亏的三季报同比写「不适用」（首华燃气）：金额照读，同比留空，不能整行读不出。"""
    figures = parse_periodic(text("q3-shouhua-na.txt"))
    assert figures.period == "q3"
    assert figures.net_profit.current == 152_195_457.48 and figures.net_profit.yoy is None
    assert figures.net_profit.ytd == 338_935_489.71 and figures.net_profit.ytd_yoy is None
    assert figures.deducted.current == 151_556_875.03
    assert figures.revenue.yoy == 35.85
