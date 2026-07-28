"""交割单解析单测(plan 4D.1 验收:两格式解析/零宽空格/价格反推/未知行降级)。"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO
from typing import Dict, List

import openpyxl
import pytest

from neckline.review.parse import (
    NameIndex,
    build_name_index,
    clean_str,
    normalize_ts_code,
    parse_workbook,
)

from .conftest import insert_namechange, insert_stock_basic


def _workbook_bytes(sheets: Dict[str, List[List[object]]]) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


FORMAT1_HEADER = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用", "发生金额", "资金余额", "备注"]
FORMAT2_HEADER = [
    "交易日期", "券商来源", "证券代码", "证券名称", "业务名称", "成交价格", "成交数量",
    "成交金额", "佣金", "印花税", "过户费", "清算费(B股)", "发生金额", "资金余额",
    "股份余额", "股东代码", "币种", "合同编号",
]


@pytest.fixture
def seeded_names(isolated_env):
    insert_stock_basic(isolated_env, [
        {"ts_code": "600519.SH", "name": "贵州茅台", "market": "主板", "list_date": date(2001, 8, 27)},
        {"ts_code": "300750.SZ", "name": "宁德时代", "market": "创业板", "list_date": date(2018, 6, 11)},
    ])
    insert_namechange(isolated_env, [
        {"ts_code": "600519.SH", "name": "贵州茅台", "start_date": date(2006, 10, 9)},
        {"ts_code": "300750.SZ", "name": "宁德时代", "start_date": date(2018, 6, 11)},
    ])
    return isolated_env


# —— 格式一(手机截图整理,无代码列,价格反推)——————————————————————————————

def test_format1_buy_sell_price_derivation(seeded_names):
    data = _workbook_bytes({
        "对账单": [
            ["说明：交割记录整理"],
            [],
            FORMAT1_HEADER,
            [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
            [datetime(2026, 7, 16), "国泰君安", "证券卖出清算", "贵州茅台", 100, 0, 15.0, 142485.0, 242485.0, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.sheet_formats["对账单"] == "format1"
    assert len(result.trades) == 2
    buy, sell = result.trades
    assert buy.side == "buy" and buy.ts_code == "600519.SH" and buy.qty == 100
    assert buy.price == pytest.approx(1500.0)   # (150015-15)/100
    assert buy.fee == pytest.approx(15.0)
    assert sell.side == "sell" and sell.price == pytest.approx(1424.7)  # (142485-15)/100


def test_format1_skips_non_trade_rows(seeded_names):
    data = _workbook_bytes({
        "对账单": [
            FORMAT1_HEADER,
            [datetime(2026, 7, 14), "国泰君安", "银证转入", "", 0, 0, 0.0, 50000.0, 50000.0, ""],
            [datetime(2026, 7, 15), "国泰君安", "红股派息", "贵州茅台", 10, 110, 0.0, 0.0, 50000.0, ""],
            [datetime(2026, 7, 15), "国泰君安", "股息红利个人所得税扣款", "贵州茅台", 0, 110, 0.0, -1.0, 49999.0, ""],
            [datetime(2026, 7, 15), "国泰君安", "利息归本", "", 0, 0, 0.0, 0.5, 49999.5, ""],
            [datetime(2026, 7, 15), "国泰君安", "银证转出", "", 0, 0, 0.0, -1000.0, 48999.5, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades == []
    assert result.skip_counts.get("银证转入") == 1
    assert result.skip_counts.get("红股派息") == 1
    assert result.skip_counts.get("股息红利个人所得税扣款") == 1
    assert result.skip_counts.get("利息归本") == 1
    assert result.skip_counts.get("银证转出") == 1
    assert result.warnings == []   # 全是已知的"跳过"行,不应产生警告噪音


def test_format1_missing_fee_column_skips_not_silently_zero(seeded_names):
    """回归测试:格式一「手续费」对应列(默认列名"费用")若压根没找到实际列(非
    col_map 覆盖场景),不能悄悄按 0 兜底继续反推价格——那样会算出一个看似合理
    实则错误的价格(施工期实测踩过:150015/100=1500.15 而非正确的 1500.0)。
    必须当硬性缺列处理,跳过该行并给出明确警告。"""
    custom_header = ["交易日期", "券商来源", "业务名称", "证券名称", "成交数量", "股份余额", "费用合计", "发生金额", "资金余额", "备注"]
    data = _workbook_bytes({
        "对账单": [
            custom_header,
            [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades == []
    assert any("手续费" in w.message and "跳过" in w.message for w in result.warnings)


def test_format1_unresolvable_name_warns_not_crashes(seeded_names):
    data = _workbook_bytes({
        "对账单": [
            FORMAT1_HEADER,
            [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "查无此票ABC", 100, 100, 15.0, -15015.0, 100000.0, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades == []
    assert len(result.warnings) == 1
    assert "无法反查到代码" in result.warnings[0].message


def test_format1_unknown_business_name_warns(seeded_names):
    data = _workbook_bytes({
        "对账单": [
            FORMAT1_HEADER,
            [datetime(2026, 7, 14), "国泰君安", "莫名其妙类型", "贵州茅台", 100, 100, 15.0, -15015.0, 100000.0, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades == []
    assert "未知业务名称" in result.warnings[0].message


# —— 格式二(桌面导出,有代码列,零宽空格坑)——————————————————————————————

def test_format2_buy_sell_with_zero_width_space(seeded_names):
    zw = "​"
    data = _workbook_bytes({
        "成交流水": [
            ["交割单导出"],
            FORMAT2_HEADER,
            [datetime(2026, 7, 14), "华泰证券", f"{zw}600519", f"{zw}贵州茅台", "证券买入",
             1500.0, 100, 150000.0, 15.0, 0, 0.1, 0, -150015.1, 100000.0, 100, "A001", "CNY", "C001"],
            [datetime(2026, 7, 16), "华泰证券", "600519", "贵州茅台", "证券卖出",
             1420.0, 100, 142000.0, 14.2, 142.0, 0.1, 0, 141843.7, 241843.7, 0, "A001", "CNY", "C002"],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.sheet_formats["成交流水"] == "format2"
    assert len(result.trades) == 2
    buy, sell = result.trades
    assert buy.ts_code == "600519.SH"       # 零宽空格已 strip,代码补 .SH 后缀
    assert buy.name == "贵州茅台"
    assert buy.price == pytest.approx(1500.0)
    assert buy.fee == pytest.approx(15.1)   # 佣金15+过户费0.1
    assert sell.fee == pytest.approx(156.3)  # 14.2+142.0+0.1


def test_format2_skips_designated_trading_and_bank_transfer(seeded_names):
    data = _workbook_bytes({
        "成交流水": [
            FORMAT2_HEADER,
            [datetime(2026, 7, 14), "华泰证券", "600519", "贵州茅台", "指定交易",
             0, 0, 0, 0, 0, 0, 0, 0, 100000.0, 0, "A001", "CNY", "C001"],
            [datetime(2026, 7, 14), "华泰证券", "600519", "贵州茅台", "银行转证券",
             0, 0, 0, 0, 0, 0, 0, 50000.0, 150000.0, 0, "A001", "CNY", "C002"],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades == []
    assert result.skip_counts.get("指定交易") == 1
    assert result.skip_counts.get("银行转证券") == 1


def test_format2_bse_code_suffix(seeded_names):
    insert_stock_basic(seeded_names, [{"ts_code": "920117.BJ", "name": "某北交所票", "market": "北交所"}])
    data = _workbook_bytes({
        "成交流水": [
            FORMAT2_HEADER,
            [datetime(2026, 7, 14), "华泰证券", "920117", "某北交所票", "证券买入",
             10.0, 1000, 10000.0, 5.0, 0, 0.1, 0, -10005.1, 100000.0, 1000, "A001", "CNY", "C001"],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.trades[0].ts_code == "920117.BJ"


# —— 表头探测 / 多 sheet / 未知格式 ————————————————————————————————————

def test_instruction_sheet_without_header_is_skipped(seeded_names):
    data = _workbook_bytes({
        "说明": [["本文件为示例说明，非交易数据"], ["联系方式：xxx"]],
        "对账单": [FORMAT1_HEADER, [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""]],
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert "skipped:no_header" in result.sheet_formats["说明"]
    assert result.sheet_formats["对账单"] == "format1"
    assert len(result.trades) == 1


def test_header_row_detected_after_leading_title_rows(seeded_names):
    """任务指令：数据 sheet 头部有 1-3 行标题/说明。"""
    data = _workbook_bytes({
        "对账单": [
            ["交割单整理"],
            ["制表日期：2026-07-20"],
            [],
            FORMAT1_HEADER,
            [datetime(2026, 7, 14), "国泰君安", "证券买入清算", "贵州茅台", 100, 100, 15.0, -150015.0, 100000.0, ""],
        ]
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert len(result.trades) == 1


def test_unknown_format_sheet_skipped_with_warning(seeded_names):
    data = _workbook_bytes({
        "怪表": [["交易日期", "某某", "某某2"], [datetime(2026, 7, 14), "x", "y"]],
    })
    result = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert result.sheet_formats["怪表"] == "skipped:unknown_format"
    assert any("不匹配已知的两种券商格式" in w.message for w in result.warnings)


def test_invalid_workbook_bytes_raises_value_error():
    with pytest.raises(ValueError):
        parse_workbook(b"not an xlsx file at all", "bad.xlsx")


# —— col_map 可配字段映射(4D 验收:「改 review_col_map 能吃另一种列名」)————————

def test_col_map_override_remaps_columns(seeded_names):
    """表头探测锚点固定为「交易日期」(任务指令原文,不受 col_map 影响);col_map
    remap 的是**其它**规范字段的实际列名——这里模拟"券商原始格式"把代码/名称/
    费用列改了名(如"证券编号"/"证券简称"/"手续费用"),内置默认列名("证券代码"等)
    找不到时解析失败,配上 col_map 后能正确吃到这套列名。"""
    custom_header = [
        "交易日期", "券商", "证券编号", "证券简称", "方向", "价格", "数量", "总额",
        "手续费用", "印花税", "过户费", "B股清算费", "净额", "余额", "股份", "股东", "币种", "合同",
    ]
    data = _workbook_bytes({
        "成交流水": [
            custom_header,
            [datetime(2026, 7, 14), "华泰证券", "600519", "贵州茅台", "证券买入",
             1500.0, 100, 150000.0, 15.0, 0, 0.1, 0, -150015.1, 100000.0, 100, "A001", "CNY", "C001"],
        ]
    })
    # 未覆盖 → 内置默认列名("证券代码"等)在自定义表头里找不到 → 判 format2(靠"成交价格"?
    # 不,此处两个主判据"证券代码"/"成交价格"均不在表头 → 判不出格式,整 sheet 跳过。
    baseline = parse_workbook(data, "t.xlsx", db_path=seeded_names.db_path)
    assert baseline.trades == []

    col_map = {
        "代码": "证券编号",
        "名称": "证券简称",
        "买卖方向": "方向",
        "成交价": "价格",
        "成交数量": "数量",
        "成交金额": "总额",
        "手续费": "手续费用",
        "印花税": "印花税",
        "过户费": "过户费",
    }
    result = parse_workbook(data, "t.xlsx", col_map=col_map, db_path=seeded_names.db_path)
    assert len(result.trades) == 1
    assert result.trades[0].ts_code == "600519.SH"
    assert result.trades[0].price == pytest.approx(1500.0)


# —— 辅助函数单测 ————————————————————————————————————————————————————

def test_clean_str_strips_zero_width_and_whitespace():
    assert clean_str("​600519​ ") == "600519"
    assert clean_str(None) == ""
    assert clean_str(600519) == "600519"


@pytest.mark.parametrize("raw,expected", [
    ("600519", "600519.SH"),
    ("000001", "000001.SZ"),
    ("300750", "300750.SZ"),
    ("688981", "688981.SH"),
    ("920117", "920117.BJ"),
    ("430047", "430047.BJ"),
    ("600519.sh", "600519.SH"),
    ("​600519", "600519.SH"),
])
def test_normalize_ts_code(raw, expected):
    assert normalize_ts_code(raw) == expected


def test_name_index_resolves_historical_name(isolated_env):
    """`namechange` 按 as-of 日期解析——旧名在生效区间内应解析到同一代码。"""
    insert_stock_basic(isolated_env, [{"ts_code": "000001.SZ", "name": "平安银行", "market": "主板"}])
    insert_namechange(isolated_env, [
        {"ts_code": "000001.SZ", "name": "深发展A", "start_date": date(1991, 4, 3), "end_date": date(2012, 8, 1)},
        {"ts_code": "000001.SZ", "name": "平安银行", "start_date": date(2012, 8, 2)},
    ])
    idx: NameIndex = build_name_index(isolated_env.db_path)
    assert idx.resolve("深发展A", date(2010, 1, 1)) == "000001.SZ"
    assert idx.resolve("平安银行", date(2020, 1, 1)) == "000001.SZ"
    assert idx.resolve("深发展A", date(2020, 1, 1)) is None   # 该名称在该日期已失效
    assert idx.resolve("不存在的名字", date(2020, 1, 1)) is None


# —— v1.4-⑥-A:成交**时刻**(可选)——————————————————————————————————————————
#    周复盘按成交时刻逐笔判章程,故解析层把「交易日期」单元格里**确实带的**时刻取出来;
#    两家已知格式实际都只到日期粒度 → 恒 None,由 reconcile 按该日收盘时刻兜底。

def test_trade_time_none_when_date_only(seeded_names):
    data = _workbook_bytes({
        "对账单": [
            FORMAT2_HEADER,
            [date(2026, 7, 27), "券商B", "600519", "贵州茅台", "证券买入", 1500.0, 100,
             150000.0, 45.0, 0.0, 1.5, 0.0, -150046.5, 100000.0, 100, "A1", "CNY", "C1"],
        ]
    })
    res = parse_workbook(data, db_path=seeded_names.db_path)
    assert res.trades[0].trade_date == date(2026, 7, 27)
    assert res.trades[0].trade_time is None          # 只有日期 → 不猜时刻


def test_trade_time_midnight_datetime_is_not_a_time(seeded_names):
    """datetime 单元格但时分秒全 0(格式一常见)= "只有日期",不能当成"凌晨 00:00 成交"
    —— 那是 A 股不存在的时刻,当真会把当日成交整体推到章程切换之前。"""
    data = _workbook_bytes({
        "对账单": [
            FORMAT2_HEADER,
            [datetime(2026, 7, 27, 0, 0, 0), "券商B", "600519", "贵州茅台", "证券买入", 1500.0, 100,
             150000.0, 45.0, 0.0, 1.5, 0.0, -150046.5, 100000.0, 100, "A1", "CNY", "C1"],
        ]
    })
    res = parse_workbook(data, db_path=seeded_names.db_path)
    assert res.trades[0].trade_time is None


def test_trade_time_taken_when_present(seeded_names):
    """真带时刻(datetime 单元格 / 字符串两路)→ 原样取出,逐笔判章程用真时刻。"""
    data = _workbook_bytes({
        "对账单": [
            FORMAT2_HEADER,
            [datetime(2026, 7, 27, 10, 30, 15), "券商B", "600519", "贵州茅台", "证券买入", 1500.0, 100,
             150000.0, 45.0, 0.0, 1.5, 0.0, -150046.5, 100000.0, 100, "A1", "CNY", "C1"],
            ["2026-07-27 14:05:00", "券商B", "600519", "贵州茅台", "证券卖出", 1520.0, 100,
             152000.0, 45.6, 152.0, 1.5, 0.0, 151800.9, 251800.9, 0, "A1", "CNY", "C2"],
        ]
    })
    res = parse_workbook(data, db_path=seeded_names.db_path)
    from datetime import time
    assert [t.trade_time for t in res.trades] == [time(10, 30, 15), time(14, 5)]
    assert all(t.trade_date == date(2026, 7, 27) for t in res.trades)
