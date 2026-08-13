"""涨跌停衍生表自算(plan 0.4b)。TuShare 600 元档无 `limit_list`(15000 积分),
从 `daily`(o/h/l/c/pre_close)+ `stock_basic`(板块/上市日)+ `namechange`(ST 状态
历史)推导涨停/跌停/炸板/连板,替代该接口。

规则来源(2026-07-19 施工时网搜确认,非本表编造;详见各常量旁注):
    · 创业板注册制改革 2020-08-24 生效:存量 + 新股同步执行 20%(此前 10%)。
    · 科创板/创业板注册制新股:上市前 5 个交易日不设涨跌幅限制,第 6 个交易日起
      恢复板块正常幅度。创业板旧(审批制,2020-08-24 前上市)新股:仅首日不限。
    · 北交所 / 主板新股:仅上市首日不设涨跌幅限制,次日起恢复板块正常幅度。
    · 科创板 / 创业板 ST 股:涨跌幅与板块正常股票一致(20%),**不降为 5%**——
      该统一早于本表最新一次改革(下条),整个回测区间(2020-至今)均适用。
    · 主板 ST/*ST:5% → 10% 新规 **2026-07-06 生效**(沪深交易所修订《交易规则》,
      2026-04 征求意见、7/6 正式实施)。回测区间跨过此分界线,按 trade_date 分段。
    · 北交所 ST:涨跌幅保持 30% 不变(不随主板新规调整,与主板/创业板/科创板不同)。
    · 涨停/跌停价 = round(pre_close×(1±幅度), 2),四舍五入(非 Python 内置 banker's
      rounding)——本模块用**整数分**精确整数运算实现,规避浮点二进制误差在千分位
      造成的 round-half 误判(施工时用 Decimal 基准做了 79200 组网格核对,误差 0)。

输出:仅落涨停 / 跌停 / 炸板命中行(§3.3 存储论证:全量落一表则 850 万行/年代价
太大,衍生表设计为**稀疏表**,只存"有信号"的行,约百万行量级)。
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import List, Optional, Tuple

import polars as pl

from neckline.data.board import Board, classify

# —— 制度分界日(常量单一源,禁止各处漂移;见模块顶部注释来源)——————————————

GEM_REFORM_DATE = date(2020, 8, 24)      # 创业板注册制改革:10%→20%
MAIN_ST_REFORM_DATE = date(2026, 7, 6)   # 主板 ST 涨跌幅:5%→10%

# 板块正常涨跌幅(基点,万分之一;整数运算避免浮点误差)
_PCT_BP_STAR = 2000       # 科创板 20%
_PCT_BP_GEM_NEW = 2000    # 创业板(注册制后)20%
_PCT_BP_GEM_OLD = 1000    # 创业板(注册制前)10%
_PCT_BP_BSE = 3000        # 北交所 30%
_PCT_BP_MAIN = 1000       # 主板(非 ST,或 ST 新规后)10%
_PCT_BP_MAIN_ST = 500     # 主板 ST(2026-07-06 前)5%
_PCT_BP_GEM_OLD_ST = 500  # 创业板(注册制前)ST 5%(与彼时主板同机制)

# 新股涨跌幅豁免窗口(第几个交易日起恢复限制;list_date 记为第 1 天)
_EXEMPT_DAYS_5 = 5   # 科创板 / 创业板注册制新股
_EXEMPT_DAYS_1 = 1   # 主板 / 北交所 / 创业板审批制(旧)新股


# —— 标量镜像(plan 阶段3 §2.4「盘中涨跌停判定用现价对涨跌停价,复用 limit_derived
#    的幅度规则算当日涨跌停价」)——————————————————————————————————————————
#
# 上面 `compute_limit_derived` 是全市场向量化(polars)EOD 批算,盘中哨兵每拍只需
# 对「关注池」(候选 + 持仓 + 昨日涨停股,数十到数百只)逐票算一次涨跌停价,不值得
# 为几百行套一次 polars DataFrame。以下两个纯 Python 标量函数与向量化版本共用
# 同一组常量(`GEM_REFORM_DATE`/`MAIN_ST_REFORM_DATE`/`_PCT_BP_*`/`_EXEMPT_DAYS_*`,
# 定义于本模块顶部),分支顺序与 `compute_limit_derived` 的 `pl.when()` 链逐条对应
# ——单测(`tests/test_limit_derived.py`)对拍两者在同一批 (board, is_st, trade_date)
# 组合上必须给出相同结果,防止未来任一处改动导致两条路径漂移。

def resolve_limit_pct(board: Board, is_st: bool, trade_date: date) -> float:
    """单票涨跌幅比例(如 0.10 = 10%)。分支顺序与 `compute_limit_derived` 的
    `pct_bp` `pl.when()` 链一致:STAR 恒 20%;GEM 按注册制改革日再分 ST/非 ST；
    BSE 恒 30%；其余(MAIN)按 ST + 主板新规日分 5%/10%。"""
    if board == Board.STAR:
        bp = _PCT_BP_STAR
    elif board == Board.GEM:
        if trade_date >= GEM_REFORM_DATE:
            bp = _PCT_BP_GEM_NEW
        elif is_st:
            bp = _PCT_BP_GEM_OLD_ST
        else:
            bp = _PCT_BP_GEM_OLD
    elif board == Board.BSE:
        bp = _PCT_BP_BSE
    elif is_st and trade_date < MAIN_ST_REFORM_DATE:
        bp = _PCT_BP_MAIN_ST
    else:
        bp = _PCT_BP_MAIN
    return bp / 10000.0


def resolve_exempt_days(board: Board, list_date: Optional[date]) -> int:
    """新股涨跌幅豁免窗口天数(第几个交易日起恢复限制)。判据与
    `compute_limit_derived` 的 `exempt_days` 分支一致:是否豁免 5 天看的是
    **上市当日**是否已在注册制之后(`list_date >= GEM_REFORM_DATE`),不是
    `trade_date`——同一只股票的豁免窗口长度终身不变。`list_date` 缺失(未知上市日)
    时保守按 1 天处理(尽快恢复限制判定,不长期误判为"仍在豁免期")。"""
    if board == Board.STAR:
        return _EXEMPT_DAYS_5
    if board == Board.GEM and list_date is not None and list_date >= GEM_REFORM_DATE:
        return _EXEMPT_DAYS_5
    return _EXEMPT_DAYS_1


def _round_half_up_scalar(price: float, pct: float, sign: int) -> float:
    """price × (1 ± pct),四舍五入到 2 位小数(`Decimal.ROUND_HALF_UP`)。与向量化
    版本 `_round_half_up_cents` 的整数分算法等价(同为 round-half-up 到分位),
    标量场景下用 `Decimal` 更直白,不必再套整数分技巧。"""
    p = Decimal(str(price))
    factor = Decimal("1") + sign * Decimal(str(pct))
    return float((p * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_intraday_limit_prices(
    pre_close: float, board: Board, is_st: bool, trade_date: date
) -> Tuple[Optional[float], Optional[float]]:
    """由 `pre_close` 算当日涨跌停价(标量,供盘中哨兵逐票用)。`pre_close<=0`
    (停牌/无效数据)→ `(None, None)`,调用方据此跳过该票的涨跌停判定。

    注意:本函数不处理新股豁免窗口(是否当前处于豁免期是「另一件事」,见
    `resolve_exempt_days` + `neckline.calendar.trading_days_between`),调用方
    (如退潮哨兵的市场宽度统计)若需要豁免语义,自行先判断再决定是否调用本函数。
    """
    if pre_close <= 0:
        return None, None
    pct = resolve_limit_pct(board, is_st, trade_date)
    up = _round_half_up_scalar(pre_close, pct, +1)
    down = _round_half_up_scalar(pre_close, pct, -1)
    return up, down


def _round_half_up_cents(price_cents: pl.Expr, pct_bp: pl.Expr, sign: int) -> pl.Expr:
    """price_cents × (10000 ± pct_bp) / 10000,四舍五入到整数分。全程整数运算。

    sign=+1 算涨停价,sign=-1 算跌停价。等价于 Decimal(ROUND_HALF_UP) 到 2 位小数
    (施工时已用 79200 组价格×幅度网格核对与 Decimal 基准零误差,浮点 trick 会有
    约 0.4%~1.7% 网格误判,故不用 `(x*100+0.5).floor()` 的浮点近似写法)。
    """
    factor = 10000 + sign * pct_bp
    return (price_cents * factor + 5000) // 10000


def _to_cents(col: str) -> pl.Expr:
    """价格列(元,2 位小数)→ 整数分。原始值已是"干净"2 位小数,四舍五入到整数
    只是消除浮点表示噪声(~1e-13 量级),不涉及四舍五入到分位的实质误判风险。"""
    return (pl.col(col) * 100).round(0).cast(pl.Int64)


def _build_code_lookup(all_codes: pl.Series, stock_basic: pl.DataFrame) -> pl.DataFrame:
    """去重 ts_code → board/list_date 查找表(逐票分类一次,非逐行,~5900 行量级)。"""
    codes_df = pl.DataFrame({"ts_code": all_codes.unique()})
    merged = codes_df.join(
        stock_basic.select(["ts_code", "market", "list_date"]), on="ts_code", how="left"
    )
    boards = [
        classify(m, c).value
        for m, c in zip(merged["market"].to_list(), merged["ts_code"].to_list())
    ]
    return merged.with_columns(pl.Series("board", boards)).select(["ts_code", "board", "list_date"])


def _build_calendar_ordinal(calendar_days: List[date]) -> pl.DataFrame:
    """交易日 → 全局序号(0-based),供"上市第 N 个交易日"向量化计算用。`.unique()`
    防御调用方传入重复日期(理论上 `load_trade_cal_days()` 不会重复,这里只是不让
    脏输入导致 join 意外 fan-out)。"""
    return pl.DataFrame({"trade_date": sorted(set(calendar_days))}).with_row_index("trade_ord")


def _is_st_name(name_col: str = "name") -> pl.Expr:
    """名称是否 ST/*ST 前缀(去掉领头 "*" 后判 "ST" 开头,同时吃住两种写法)。"""
    return pl.col(name_col).str.strip_chars("*").str.starts_with("ST")


def compute_limit_derived(
    daily: pl.DataFrame,
    stock_basic: pl.DataFrame,
    namechange: pl.DataFrame,
    calendar_days: List[date],
) -> pl.DataFrame:
    """核心推导(plan 0.4b)。

    参数:
        daily: 至少含 ts_code(str)/trade_date(Date)/open/high/low/close/pre_close(f64)。
               传入范围决定"连板"计数窗口——backfill 传全量,daily_update 传尾部
               窗口(如近 15 交易日)保证跨批次连板计数正确。
        stock_basic: ts_code/market/list_date(Date)。
        namechange: ts_code/name/start_date(Date)/end_date(Date,可空=沿用至今)。
        calendar_days: 覆盖 daily 全部 trade_date 的交易日列表(neckline.calendar 模块给)。

    返回:稀疏表,仅 is_limit_up / is_limit_down / is_zaban 命中行,列见模块 docstring。
    """
    if daily.is_empty():
        return _empty_result()

    daily = daily.sort(["ts_code", "trade_date"])

    # —— 板块 + 上市日(小表逐票分类,再 join 回大表)——
    code_lookup = _build_code_lookup(daily["ts_code"], stock_basic)
    df = daily.join(code_lookup, on="ts_code", how="left")
    df = df.with_columns(pl.col("board").fill_null(Board.MAIN.value))

    # —— 上市第 N 个交易日(向量化:trade_ord - list_ord + 1)——
    cal_ord = _build_calendar_ordinal(calendar_days)
    df = df.join(cal_ord, on="trade_date", how="left")  # -> trade_ord
    list_ord = cal_ord.rename({"trade_date": "list_date", "trade_ord": "list_ord"})
    df = df.join(list_ord, on="list_date", how="left")
    df = df.with_columns(
        pl.when(pl.col("list_ord").is_not_null() & pl.col("trade_ord").is_not_null())
        .then(pl.col("trade_ord") - pl.col("list_ord") + 1)
        .otherwise(9999)  # 未知上市日 → 视为早已过豁免窗口(保守:按正常涨跌幅算)
        .alias("days_since_listing")
    )

    # —— ST 状态(as-of join:该 ts_code 在 trade_date 当天生效的名称)——
    nc = namechange.sort(["ts_code", "start_date"]).with_columns(_is_st_name().alias("is_st_name"))
    df = df.join_asof(
        nc.select(["ts_code", "start_date", "is_st_name"]),
        left_on="trade_date",
        right_on="start_date",
        by="ts_code",
        strategy="backward",
    )
    df = df.with_columns(pl.col("is_st_name").fill_null(False))

    # —— 涨跌幅基点(整数,万分之一)+ 豁免窗口天数,按板块 + 日期分段 ——————
    df = df.with_columns(
        pl.when(pl.col("board") == Board.STAR.value)
        .then(_PCT_BP_STAR)
        .when((pl.col("board") == Board.GEM.value) & (pl.col("trade_date") >= GEM_REFORM_DATE))
        .then(_PCT_BP_GEM_NEW)
        .when(
            (pl.col("board") == Board.GEM.value)
            & (pl.col("trade_date") < GEM_REFORM_DATE)
            & pl.col("is_st_name")
        )
        .then(_PCT_BP_GEM_OLD_ST)
        .when(pl.col("board") == Board.GEM.value)
        .then(_PCT_BP_GEM_OLD)
        .when(pl.col("board") == Board.BSE.value)
        .then(_PCT_BP_BSE)
        .when(pl.col("is_st_name") & (pl.col("trade_date") < MAIN_ST_REFORM_DATE))
        .then(_PCT_BP_MAIN_ST)
        .otherwise(_PCT_BP_MAIN)
        .alias("pct_bp")
    )

    df = df.with_columns(
        pl.when(pl.col("board") == Board.STAR.value)
        .then(_EXEMPT_DAYS_5)
        .when((pl.col("board") == Board.GEM.value) & (pl.col("list_date") >= GEM_REFORM_DATE))
        .then(_EXEMPT_DAYS_5)
        .otherwise(_EXEMPT_DAYS_1)
        .alias("exempt_days")
    )
    df = df.with_columns((pl.col("days_since_listing") <= pl.col("exempt_days")).alias("is_exempt"))

    # —— 整数分精确算涨跌停价(见模块 docstring 的浮点误差核对说明)——————————
    df = df.with_columns(
        [
            _to_cents("pre_close").alias("_pre_close_cents"),
            _to_cents("close").alias("_close_cents"),
            _to_cents("high").alias("_high_cents"),
        ]
    )
    df = df.with_columns(
        [
            _round_half_up_cents(pl.col("_pre_close_cents"), pl.col("pct_bp"), +1).alias("_limit_up_cents"),
            _round_half_up_cents(pl.col("_pre_close_cents"), pl.col("pct_bp"), -1).alias("_limit_down_cents"),
        ]
    )

    df = df.with_columns(
        [
            (~pl.col("is_exempt") & (pl.col("_close_cents") == pl.col("_limit_up_cents"))).alias("is_limit_up"),
            (~pl.col("is_exempt") & (pl.col("_close_cents") == pl.col("_limit_down_cents"))).alias("is_limit_down"),
            (
                ~pl.col("is_exempt")
                & (pl.col("_high_cents") == pl.col("_limit_up_cents"))
                & (pl.col("_close_cents") < pl.col("_limit_up_cents"))
            ).alias("is_zaban"),
        ]
    )

    # —— 连板计数(同 ts_code 内连续 is_limit_up=True 的行数;停牌无行不计入判断,
    #     即"停牌不打断连板"口径,见模块 docstring)——————————————————————————
    df = df.with_columns(
        (
            pl.col("is_limit_up") != pl.col("is_limit_up").shift(1).over("ts_code").fill_null(False)
        )
        .cum_sum()
        .over("ts_code")
        .alias("_streak_grp")
    )
    df = df.with_columns(
        pl.when(pl.col("is_limit_up"))
        .then(pl.col("is_limit_up").cum_sum().over(["ts_code", "_streak_grp"]))
        .otherwise(0)
        .alias("consec_limit_up_days")
    )

    df = df.with_columns(
        pl.when(pl.col("is_limit_up"))
        .then(pl.lit("limit_up"))
        .when(pl.col("is_limit_down"))
        .then(pl.lit("limit_down"))
        .when(pl.col("is_zaban"))
        .then(pl.lit("zaban"))
        .otherwise(None)
        .alias("status")
    )

    hit = df.filter(pl.col("is_limit_up") | pl.col("is_limit_down") | pl.col("is_zaban"))

    out = hit.select(
        [
            "ts_code",
            "trade_date",
            "board",
            "status",
            (pl.col("pct_bp") / 10000.0).alias("limit_pct"),
            (pl.col("_limit_up_cents") / 100.0).alias("limit_up_price"),
            (pl.col("_limit_down_cents") / 100.0).alias("limit_down_price"),
            "is_limit_up",
            "is_limit_down",
            "is_zaban",
            "consec_limit_up_days",
        ]
    ).sort(["trade_date", "ts_code"])
    return out


def _empty_result() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "ts_code": pl.Utf8,
            "trade_date": pl.Date,
            "board": pl.Utf8,
            "status": pl.Utf8,
            "limit_pct": pl.Float64,
            "limit_up_price": pl.Float64,
            "limit_down_price": pl.Float64,
            "is_limit_up": pl.Boolean,
            "is_limit_down": pl.Boolean,
            "is_zaban": pl.Boolean,
            "consec_limit_up_days": pl.Int64,
        }
    )


__all__ = [
    "compute_limit_derived",
    "GEM_REFORM_DATE",
    "MAIN_ST_REFORM_DATE",
    "resolve_limit_pct",
    "resolve_exempt_days",
    "compute_intraday_limit_prices",
]
