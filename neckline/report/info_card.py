"""信息卡与考卷同构(plan §五 v1.4-④,需求 8 第 3 点 + `archive/交接_考官Agent_盲选
训练规格_20260726.md` §三)。**第〇原则**:考卷信息集 = 实盘情报包信息集,严格同构
——考卷给什么信息,实盘就该给什么。**数据不可得如实缺省,禁止硬凑**,这条比"多给一
个字段"重要得多——本模块每一路数据源都有独立的 `*Available`/`unavailableReason`
表达,绝不用 `0`/`[]`/空字符串冒充"有数据但无内容"(§3.8)。

**信息卡九件套**(对齐考官规格 §三 + 需求 8 第 3 点):① K 线序列(60 日,前复权,
复用 `data.adjust.apply_qfq`)② RS 线(相对大盘 `strategy.features.SSE_INDEX`,60 日,
起点归一 100)③ 行业分歧线(相对**行业成员中位数合成指数**,60 日,起点归一 100;
**口径偏差显式登记**——考官线用申万二级指数,本模块用成员中位数合成,§三.7 已把
"成员中位数合成"列为登记口径的合法实现)④ 快照数值 ⑤ 红黄牌(复用 ③ 已算好的
`k4_flags` + `sections` 分区 + DB `evidence` 文字,不重算)⑥ 温和带标注(当日涨幅
∈[2%,3%])⑦ 消息面摘要(复用 `news_alerts`,扫描域仅持仓〔V2-⑬-11 起自选池已删〕,本版不
扩域)⑧ 龙虎榜摘要(复用 `data.top_list`)⑨ 市场语境(复用情绪仪表盘 +
`strategy.features.market_state_labels`,报告级构件,不逐候选重算)。

**两条消费路径,刻意分离**(不要把二者的数据来源搞混):
    · `build_info_card()` —— **`GET /report/{date}/info-card/{code}` endpoint 用**,
      单只、按需、**服务端现算**(读 EOD 面板,不落库)。除 `k4_flags`(明确要求"不
      重算",见下)外,行业强度/消息面域/龙虎榜/大盘语境全部**独立于报告生成过程
      现读现算**——这是"服务端现算"的字面含义,不依赖报告生成时是否成功跑过某一步。
    · `attach_info_card_summaries()` —— **`pipeline.py` 报告生成时批量调用**,给
      当日候选 20 只每只补一份**摘要**(不含 60 日序列)挂 `Candidate.info_card_summary`
      → 落 `reports.candidates_json` → `CandidateOut.infoCard`(供列表页直接展示,免
      逐只发请求,plan §五 v1.4-④-B)。这条路径**复用候选生成时已经算好的
      `Candidate.raw`(K4 特征面板行)与 `Candidate.intel_rank`(行业强度)**,不二次
      读 parquet——同一份数据,只是消费时机不同(生成时 vs 查看时),不是两套口径。

**红黄牌"不重算"的字面意思**:命中码本身(`Candidate.k4_flags`,由
`report/intel_candidates.py` 在候选生成时算好)必须原样传入 `build_info_card`,本模块
只负责把已知的码 decorate 成人读文案(`holding_k4_check.describe_hits`),绝不重新
跑一遍 K4 判据表达式。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import polars as pl

from neckline.calendar import trading_days_between
from neckline.data.adjust import apply_qfq
from neckline.data.market_data import day_file_exists, get_stock_history, resolve_stock_names
from neckline.data.top_list import top_list_lookup
from neckline.report import holding_k4_check
from neckline.report.industry_strength import (
    IndustryStrength,
    industry_strength_lookup,
    load_industry_map,
    stock_industry_rank,
    stock_persist_days,
)
from neckline.report.industry_strength_store import (
    industry_strength_status,
    load_industry_median_series,
    load_industry_strength,
)
from neckline.report.sentiment import compute_sentiment
from neckline.strategy.features import (
    SSE_INDEX,
    add_features,
    market_state_labels,
    merge_daily_basic,
    merge_limit_features,
)

logger = logging.getLogger(__name__)

# —— 常量(单一源)——————————————————————————————————————————————————————
DISPLAY_WINDOW_TRADING_DAYS = 60   # K 线/RS 线/行业分歧线的展示窗口(考官规格 §三.1/2/3)
_WINDOW_CALENDAR_BUFFER_DAYS = 130  # 取"最近 N 个交易日"时往回扫的自然日缓冲(>60个交易日足够)
_PANEL_LOOKBACK_CALENDAR_DAYS = 420  # 单票特征面板前置缓冲(ma250 需 250 交易日,镜像
                                     # `holding_k4_check._LOOKBACK_CALENDAR_DAYS` 同一取值)
TOP_LIST_LOOKBACK_TRADING_DAYS = 5  # 龙虎榜"近 N 个交易日"回看窗口(plan §五 v1.4-④-A-8)

# 温和带(exec_hint C2 口径,plan §五 v1.4-④-A-6;⑤-A 的 C2 提示消费同一常量,不重开一份)。
MILD_BAND_RANGE = (0.02, 0.03)

INDUSTRY_DIVERGENCE_NOTE = "行业线=行业成员中位数合成,非申万官方指数"
NEWS_DOMAIN_UNAVAILABLE_REASON = "本票不在消息面扫描域(仅持仓)"

_QFQ_PRICE_COLS = ("open", "high", "low", "close", "pre_close")


def is_mild_band(ret_1d: Optional[float]) -> bool:
    """当日涨幅是否落入温和带 `MILD_BAND_RANGE`(=[2%,3%])。`ret_1d=None`(无当日
    数据)→ `False`(保守,不冒充"确认不在温和带"以外的任何更强主张——本函数只是一个
    布尔标注,没有配套的 available 位,缺数据时"不是"是唯一诚实的默认)。"""
    if ret_1d is None:
        return False
    lo, hi = MILD_BAND_RANGE
    return lo <= ret_1d <= hi


# ======================================================================
#  数据结构(domain dataclass;`to_public_dict()` = camelCase JSON 边界,
#  同 `report/news_alerts.py` / `report/holding_k4_check.py` 既有体例)
# ======================================================================

@dataclass
class InfoCardKlineBar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    vol: float
    ma20: Optional[float] = None
    ma250: Optional[float] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date, "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "vol": self.vol,
            "ma20": self.ma20, "ma250": self.ma250,
        }


@dataclass
class InfoCardIndexPoint:
    """RS 线 / 行业分歧线 / 大盘指数化线共用的一个点(起点归一 100)。"""
    trade_date: str
    value: float

    def to_public_dict(self) -> Dict[str, Any]:
        return {"tradeDate": self.trade_date, "value": self.value}


@dataclass
class InfoCardSnapshot:
    vol_ratio5: Optional[float] = None            # vol/vol_ma5(量比)
    turnover_rate: Optional[float] = None          # 换手率(百分数,如 5.2 = 5.2%)
    industry_rank: Optional[int] = None            # ② 行业强度当日排名(1=最强);None=未参与排名
    # ② 行业强度持续天数。**`None` ≠ 0**(v1.4-⑩-E):`None` = 行业强度表当日无数据
    # (「没看」,`industry_strength_daily` 未就绪 / 该行业当日成员数不足);`0` = 评了、
    # 不是强度日(「看了,没有」)。客户端据此展示「不可用」而非「0 天」。
    industry_persist_days: Optional[int] = None
    above_ma250: Optional[bool] = None             # 年线上/下;ma250 未就绪(<250交易日历史)→ None
    dist_from_ma250_pct: Optional[float] = None    # close/ma250-1(小数,非百分数)
    dist_from_high20d_pct: Optional[float] = None  # close/high_20d-1(≤0)
    consec_limit_up_days: int = 0                  # 连板数

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "volRatio5": self.vol_ratio5, "turnoverRate": self.turnover_rate,
            "industryRank": self.industry_rank, "industryPersistDays": self.industry_persist_days,
            "aboveMa250": self.above_ma250, "distFromMa250Pct": self.dist_from_ma250_pct,
            "distFromHigh20dPct": self.dist_from_high20d_pct, "consecLimitUpDays": self.consec_limit_up_days,
        }


@dataclass
class InfoCardK4Flag:
    code: str
    label: str
    level: str               # strong | normal
    section: str              # hard_cut(红牌)| avoid_flag(黄牌,客户端展示层换算,同 board 惯例)
    evidence_strength: str   # price_volume | constituent
    evidence: str

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "label": self.label, "level": self.level, "section": self.section,
            "evidenceStrength": self.evidence_strength, "evidence": self.evidence,
        }


@dataclass
class InfoCardNewsItem:
    category: str
    summary: str
    source: str

    def to_public_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "summary": self.summary, "source": self.source}


@dataclass
class InfoCardNews:
    scanned: bool
    items: List[InfoCardNewsItem] = field(default_factory=list)
    unavailable_reason: Optional[str] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned, "items": [i.to_public_dict() for i in self.items],
            "unavailableReason": self.unavailable_reason,
        }


@dataclass
class InfoCardTopList:
    on_list_today: bool = False
    reason: Optional[str] = None
    net_amount: Optional[float] = None
    net_rate: Optional[float] = None
    lookback_days_covered: int = 0   # 近 N 日回看窗口里,本地已落盘、真能判定的天数(≤ N)
    lookback_hit_days: int = 0       # 上述已覆盖天数里,命中上榜的天数

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "onListToday": self.on_list_today, "reason": self.reason,
            "netAmount": self.net_amount, "netRate": self.net_rate,
            "lookbackDaysCovered": self.lookback_days_covered, "lookbackHitDays": self.lookback_hit_days,
        }


@dataclass
class InfoCardMarket:
    """市场语境(报告级构件,plan §五 v1.4-④-A-9;每次调用现算一份,不逐候选缓存)。"""
    index_code: str = SSE_INDEX
    index_line: List[InfoCardIndexPoint] = field(default_factory=list)
    limit_up_count: int = 0
    limit_down_count: int = 0
    above_ma20: Optional[bool] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "indexCode": self.index_code, "indexLine": [p.to_public_dict() for p in self.index_line],
            "limitUpCount": self.limit_up_count, "limitDownCount": self.limit_down_count,
            "aboveMa20": self.above_ma20,
        }


@dataclass
class InfoCard:
    code: str
    name: str
    trade_date: str
    kline_available: bool
    kline: List[InfoCardKlineBar] = field(default_factory=list)
    kline_unavailable_reason: Optional[str] = None
    rs_available: bool = False
    rs_line: List[InfoCardIndexPoint] = field(default_factory=list)
    rs_benchmark: str = SSE_INDEX
    rs_unavailable_reason: Optional[str] = None
    industry_divergence_available: bool = False
    industry_divergence_line: List[InfoCardIndexPoint] = field(default_factory=list)
    industry: str = ""
    industry_divergence_note: str = INDUSTRY_DIVERGENCE_NOTE
    industry_divergence_unavailable_reason: Optional[str] = None
    snapshot: InfoCardSnapshot = field(default_factory=InfoCardSnapshot)
    k4_flags: List[InfoCardK4Flag] = field(default_factory=list)
    mild_band: bool = False
    news: InfoCardNews = field(default_factory=lambda: InfoCardNews(scanned=False))
    top_list: InfoCardTopList = field(default_factory=InfoCardTopList)
    market: InfoCardMarket = field(default_factory=InfoCardMarket)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "tradeDate": self.trade_date,
            "klineAvailable": self.kline_available,
            "kline": [b.to_public_dict() for b in self.kline],
            "klineUnavailableReason": self.kline_unavailable_reason,
            "rsAvailable": self.rs_available,
            "rsLine": [p.to_public_dict() for p in self.rs_line],
            "rsBenchmark": self.rs_benchmark, "rsUnavailableReason": self.rs_unavailable_reason,
            "industryDivergenceAvailable": self.industry_divergence_available,
            "industryDivergenceLine": [p.to_public_dict() for p in self.industry_divergence_line],
            "industry": self.industry, "industryDivergenceNote": self.industry_divergence_note,
            "industryDivergenceUnavailableReason": self.industry_divergence_unavailable_reason,
            "snapshot": self.snapshot.to_public_dict(),
            "k4Flags": [f.to_public_dict() for f in self.k4_flags],
            "mildBand": self.mild_band,
            "news": self.news.to_public_dict(),
            "topList": self.top_list.to_public_dict(),
            "market": self.market.to_public_dict(),
        }


@dataclass
class InfoCardSummary:
    """紧凑摘要(不含 60 日序列 / 红黄牌明细,挂 `CandidateOut.infoCard`,plan §五
    v1.4-④-B)。`k4Flags` 不在此重复——`CandidateOut` 顶层已有 `k4Flags`(码列表)。"""
    snapshot: InfoCardSnapshot = field(default_factory=InfoCardSnapshot)
    mild_band: bool = False
    news: InfoCardNews = field(default_factory=lambda: InfoCardNews(scanned=False))
    top_list: InfoCardTopList = field(default_factory=InfoCardTopList)

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "snapshot": self.snapshot.to_public_dict(), "mildBand": self.mild_band,
            "news": self.news.to_public_dict(), "topList": self.top_list.to_public_dict(),
        }


# ======================================================================
#  共享内部计算(build_info_card 与 attach_info_card_summaries 都调这些)
# ======================================================================

def _recent_trading_days(end: date, n: int) -> List[date]:
    """`end` 往前数最近 `n` 个交易日,升序(最早在前,`end` 在最后,若 `end` 非交易日
    则最后一个是它之前最近的交易日)。可用交易日不足 `n` 个 → 返回全部可用的(如实
    缺省,不补齐)。"""
    days = trading_days_between(end - timedelta(days=_WINDOW_CALENDAR_BUFFER_DAYS), end)
    if not days:
        return []
    return days[-n:]


def _normalize_to_100(dates: List[date], values: List[Optional[float]]) -> List[InfoCardIndexPoint]:
    """起点(首个非 None 值)归一 100 的指数化序列(RS 线/行业分歧线/大盘线共用,
    考官规格 §三.1/2 "价格指数化,起点=100"的落地)。基准值缺失或为 0(除零)→ 空列表
    (调用方按"该线不可用"处理,不产出半截/错误缩放的线)。"""
    base = None
    for v in values:
        if v is not None:
            base = v
            break
    if not base:   # 全 None,或找到的首个非 None 值恰好是 0(除零)→ 均不可用
        return []
    out: List[InfoCardIndexPoint] = []
    for d, v in zip(dates, values):
        if v is None:
            continue
        out.append(InfoCardIndexPoint(trade_date=d.strftime("%Y%m%d"), value=round(v / base * 100.0, 3)))
    return out


def _load_single_code_panel(code: str, trade_date: date, parquet_dir: Optional[Path]) -> pl.DataFrame:
    """单票特征面板(前复权 + `add_features` + 涨停基因 + daily_basic + ma250),覆盖
    `[trade_date - 420自然日, trade_date]`——**不**过滤到某一天,供 K 线 60 日窗口切片
    与"当日快照行"共用同一份数据。ma250 镜像 `holding_k4_check._add_k4_features`
    的公式(`rolling_mean(250, min_samples=250)`),本函数只需要均线本身,不需要
    K4 判据用到的 MACD/KDJ/涨停命中列,故不复用该模块整份面板装配(避免不必要的
    跨模块耦合),只独立小面板加这一列。"""
    load_start = trade_date - timedelta(days=_PANEL_LOOKBACK_CALENDAR_DAYS)
    daily = get_stock_history(code, load_start, trade_date, table="daily", parquet_dir=parquet_dir)
    if daily.is_empty():
        return daily
    adj = get_stock_history(code, load_start, trade_date, table="adj_factor", parquet_dir=parquet_dir)
    if not adj.is_empty():
        merged = daily.join(
            adj.select(["ts_code", "trade_date", "adj_factor"]), on=["ts_code", "trade_date"], how="left"
        )
        adjusted = apply_qfq(merged, price_cols=_QFQ_PRICE_COLS)
        qfq_cols = [f"{c}_qfq" for c in _QFQ_PRICE_COLS]
        daily = adjusted.drop(list(_QFQ_PRICE_COLS)).rename(dict(zip(qfq_cols, _QFQ_PRICE_COLS)))
    panel = add_features(daily)
    limit_derived = get_stock_history(code, load_start, trade_date, table="limit_derived", parquet_dir=parquet_dir)
    panel = merge_limit_features(panel, limit_derived)
    daily_basic = get_stock_history(code, load_start, trade_date, table="daily_basic", parquet_dir=parquet_dir)
    panel = merge_daily_basic(panel, daily_basic)
    panel = panel.sort("trade_date")
    panel = panel.with_columns(pl.col("close").rolling_mean(250, min_samples=250).alias("ma250"))
    return panel


def _row_at(panel: pl.DataFrame, trade_date: date) -> Optional[Dict[str, Any]]:
    if panel is None or panel.is_empty():
        return None
    hit = panel.filter(pl.col("trade_date") == trade_date)
    if hit.is_empty():
        return None
    return hit.to_dicts()[0]


def _build_kline(window_panel: Optional[pl.DataFrame]) -> Tuple[List[InfoCardKlineBar], bool, Optional[str]]:
    if window_panel is None or window_panel.is_empty():
        return [], False, "该股窗口内无K线数据(停牌/未上市/数据缺口)"
    bars: List[InfoCardKlineBar] = []
    for r in window_panel.sort("trade_date").iter_rows(named=True):
        bars.append(InfoCardKlineBar(
            trade_date=r["trade_date"].strftime("%Y%m%d"),
            open=float(r["open"]), high=float(r["high"]), low=float(r["low"]), close=float(r["close"]),
            vol=float(r["vol"]) if r.get("vol") is not None else 0.0,
            ma20=(round(float(r["ma20"]), 4) if r.get("ma20") is not None else None),
            ma250=(round(float(r["ma250"]), 4) if r.get("ma250") is not None else None),
        ))
    return bars, True, None


def _build_snapshot(
    row: Optional[Dict[str, Any]], industry_rank: Optional[int],
    industry_persist_days: Optional[int],
) -> InfoCardSnapshot:
    """快照数值(plan §五 v1.4-④-A-4)。`row=None`(当日无 EOD 行)→ 价量类字段全
    None/0,`industryRank`/`industryPersistDays` 仍原样带(它们是独立于当日 K 线的
    行业口径,即便个股当日缺行也不影响)。"""
    if not row:
        return InfoCardSnapshot(
            vol_ratio5=None, turnover_rate=None, industry_rank=industry_rank,
            industry_persist_days=industry_persist_days, above_ma250=None,
            dist_from_ma250_pct=None, dist_from_high20d_pct=None, consec_limit_up_days=0,
        )
    close = row.get("close")
    ma250 = row.get("ma250")
    above_ma250: Optional[bool] = None
    dist_ma250: Optional[float] = None
    if close is not None and ma250:
        above_ma250 = bool(close > ma250)
        dist_ma250 = round(close / ma250 - 1, 4)
    dist_high20d = row.get("dist_from_high_20d")
    return InfoCardSnapshot(
        vol_ratio5=(round(row["vol_ratio_5"], 3) if row.get("vol_ratio_5") is not None else None),
        turnover_rate=(round(row["turnover_rate"], 2) if row.get("turnover_rate") is not None else None),
        industry_rank=industry_rank, industry_persist_days=industry_persist_days,
        above_ma250=above_ma250, dist_from_ma250_pct=dist_ma250,
        dist_from_high20d_pct=(round(dist_high20d, 4) if dist_high20d is not None else None),
        consec_limit_up_days=int(row.get("consec_limit_up_days") or 0),
    )


def _build_rs_line(
    window_panel: Optional[pl.DataFrame], idx_states: pl.DataFrame,
) -> Tuple[bool, List[InfoCardIndexPoint], Optional[str]]:
    """RS 线(相对大盘,plan §五 v1.4-④-A-2):个股/`SSE_INDEX` 收盘比值,起点归一 100。
    `idx_states` = `market_state_labels` 已算好的窗口内 `sse_close`(与「大盘语境」
    共用同一次调用,不重复读 index_daily,见 `build_info_card`)。"""
    if window_panel is None or window_panel.is_empty():
        return False, [], "该股窗口内无K线数据,无法计算相对强度"
    if idx_states.is_empty():
        return False, [], f"大盘指数({SSE_INDEX})窗口内无数据"
    joined = (
        window_panel.select(["trade_date", "close"])
        .join(idx_states.select(["trade_date", "sse_close"]), on="trade_date", how="inner")
        .sort("trade_date")
    )
    if joined.is_empty():
        return False, [], "个股与大盘指数窗口内无重叠交易日"
    dates = joined["trade_date"].to_list()
    ratio = [s / i for s, i in zip(joined["close"].to_list(), joined["sse_close"].to_list())]
    line = _normalize_to_100(dates, ratio)
    if not line:
        return False, [], "相对强度基准日数据异常"
    return True, line, None


def _build_industry_divergence(
    code: str,
    industry: str,
    industry_rank: Optional[int],
    window_panel: Optional[pl.DataFrame],
    window_start: date,
    trade_date: date,
    db_path: Optional[Path],
    industry_ready: bool = True,
) -> Tuple[bool, List[InfoCardIndexPoint], Optional[str]]:
    """行业分歧线(plan §五 v1.4-④-A-3):个股/**行业成员中位数合成指数**比值,起点
    归一 100。**未达标行业如实标注,不硬凑**(交接要求原话)——`industry` 空串(该股
    无 `stock_basic.industry`)或 `industry_rank is None`(② 判定"当日成员<5,不参与
    排名",与 ③ 排序键 `industry_rank=None` 同一语义)→ 直接不可用,**不调用**取数
    (样本不足时就不该假装能合成出一条可信的线)。

    **三档不可用理由,刻意分开写、不许混成一句**(v1.4-⑩-E):
      ① 该股无行业分类(`stock_basic.industry` 缺失);
      ② **行业样本不足**(该行业当日成员数 < `_MIN_MEMBERS`)= 「看了,不够格」;
      ③ **行业强度数据未就绪**(`industry_strength_daily` 表当日/整窗无行)= 「**没看**」。
    ②③ 混成一句就是拿「没看」冒充「没有」(§3.8 硬要求)。`industry_ready=False` 时优先
    判 ③ —— 表没数据的时候 `industry_rank` 必然是 None,不先判 ③ 就会误报成 ②。"""
    if not industry:
        return False, [], "该股无行业分类(stock_basic.industry缺失)"
    if not industry_ready:
        fresh = industry_strength_status(trade_date, db_path=db_path)
        return False, [], f"行业强度数据未就绪(最新至 {fresh.latest_label()})"
    if industry_rank is None:
        return False, [], f"行业样本不足({industry}当日成员数不足,分歧线缺省)"
    if window_panel is None or window_panel.is_empty():
        return False, [], "该股窗口内无K线数据,无法计算行业分歧线"
    # v1.4-⑩-E:读预计算表(不过滤 `industry_rank IS NULL` —— 该口径本就不受 `_MIN_MEMBERS`
    # 约束,这是 ⑩-A「落全部行业」的直接兑现),不再走 `industry_strength` 里那个走全 glob
    # 的现算参考实现(守门单测 grep 本文件断言它的名字不出现,故此处只描述、不点名)。
    med = load_industry_median_series(industry, window_start, trade_date, db_path=db_path)
    if not med:
        fresh = industry_strength_status(trade_date, db_path=db_path)
        return False, [], f"行业强度数据未就绪(最新至 {fresh.latest_label()})"
    med_by_date = {r["trade_date"]: r["median_ret"] for r in med}
    stock_rows = window_panel.sort("trade_date").select(["trade_date", "close"]).to_dicts()
    if not stock_rows:
        return False, [], "该股窗口内无K线数据,无法计算行业分歧线"
    # 逐日累乘合成行业指数(基准 100,起点日 T-59 本身不吃当天收益——它就是"day 0")。
    # 窗口内某日行业中位数缺口(数据缺口/成员数临时跌破阈值)按 0(当日不涨不跌)处理,
    # 不让单日缺口打断整条线——取数侧(`load_industry_median_series` 及其现算参考实现)
    # 已在 docstring 声明"是否把缺口当0是调用方策略",这里就是那个策略选择。
    idx_val = 100.0
    dates: List[date] = []
    ratio: List[float] = []
    for i, r in enumerate(stock_rows):
        d = r["trade_date"]
        if i > 0:
            idx_val = idx_val * (1 + med_by_date.get(d, 0.0))
        dates.append(d)
        ratio.append(r["close"] / idx_val if idx_val else float("nan"))
    line = _normalize_to_100(dates, ratio)
    if not line:
        return False, [], "行业分歧线基准日数据异常"
    return True, line, None


def _build_market_context(
    trade_date: date, idx_states: pl.DataFrame, parquet_dir: Optional[Path],
) -> InfoCardMarket:
    """市场语境(plan §五 v1.4-④-A-9):大盘 60 日指数化形态 + 当日涨跌停家数 + 大盘
    MA20 上下。涨跌停家数复用**情绪仪表盘**(`report/sentiment.py`);指数化线与
    MA20 上下复用同一次 `market_state_labels` 调用(见 `build_info_card`),不重算。
    情绪仪表盘异常不得掀翻整张信息卡(降级为 0,同 pipeline.py 对可选情报输入的
    保险丝惯例)。"""
    limit_up = limit_down = 0
    try:
        sentiment = compute_sentiment(trade_date, parquet_dir=parquet_dir)
        limit_up, limit_down = sentiment.limit_up_count, sentiment.limit_down_count
    except Exception:  # noqa: BLE001 —— 市场语境的涨跌停家数异常不得连带信息卡整体失败
        logger.warning("信息卡·市场语境:情绪仪表盘计算异常,涨跌停家数降级为0", exc_info=True)

    if idx_states.is_empty():
        return InfoCardMarket(index_code=SSE_INDEX, index_line=[], limit_up_count=limit_up,
                               limit_down_count=limit_down, above_ma20=None)
    dates = idx_states["trade_date"].to_list()
    closes = idx_states["sse_close"].to_list()
    line = _normalize_to_100(dates, closes)
    above_list = idx_states["sse_above_ma"].to_list()
    above_ma20 = bool(above_list[-1]) if above_list and above_list[-1] is not None else None
    return InfoCardMarket(index_code=SSE_INDEX, index_line=line, limit_up_count=limit_up,
                           limit_down_count=limit_down, above_ma20=above_ma20)


def _k4_flags_detail(codes: List[str], db_path: Optional[Path]) -> List[InfoCardK4Flag]:
    """红黄牌明细(plan §五 v1.4-④-A-5:"复用 ③ 已算好的 k4_flags + sections 分区 +
    DB evidence 文字,不重算")。`codes` 必须是**已经生成好**的候选 `k4_flags`(见
    `build_info_card` 的 `k4_flags` 参数,由调用方从当日报告存档原样传入)。"""
    if not codes:
        return []
    hits = holding_k4_check.describe_hits(codes, db_path)
    sections = holding_k4_check.load_k4_sections(db_path)
    return [
        InfoCardK4Flag(
            code=h.code, label=h.label, level=h.level,
            section=sections.get(h.code, holding_k4_check.K4_DEFAULT_SECTION),
            evidence_strength=h.evidence_strength, evidence=h.evidence,
        )
        for h in hits
    ]


def _news_summary_for_code(
    code: str,
    trade_date: date,
    domain_codes: Set[str],
    *,
    items: Optional[List[Dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> InfoCardNews:
    """消息面摘要(plan §五 v1.4-④-A-7)。**扫描域仅持仓(V2-⑬-11 起自选池已删),
    本版不扩域**——`domain_codes` 由调用方给出(`build_info_card` 默认取当前持仓,
    `attach_info_card_summaries` 由 pipeline.py 传入该次报告生成时的同一份域)。
    不在域内 → 如实标 `scanned=False` + 交接要求原话的理由文案,**不臆造"扫了没有"**。
    `items=None`(未提供,独立调用场景)→ 现读 `news_alerts` 表(该日已扫描完成后
    才会有数据,`build_info_card` 走这条);`items` 非空(pipeline.py 报告生成过程中,
    该日尚未落库)→ 直接用内存里刚扫描出的列表,避免读到"还没来得及写入"的旧库存量。"""
    if code not in domain_codes:
        return InfoCardNews(scanned=False, items=[], unavailable_reason=NEWS_DOMAIN_UNAVAILABLE_REASON)
    src = items
    if src is None:
        from neckline.report.news_alerts_store import load_news_alerts

        src = load_news_alerts(trade_date, db_path=db_path)
    code_items = [
        InfoCardNewsItem(category=it.get("category", ""), summary=it.get("summary", ""), source=it.get("source", ""))
        for it in src if it.get("ts_code") == code
    ]
    return InfoCardNews(scanned=True, items=code_items, unavailable_reason=None)


def _default_news_domain(db_path: Optional[Path]) -> Set[str]:
    """`build_info_card` 独立调用(未传 `news_domain_codes`)时的缺省扫描域 = 当前
    持仓(与 `pipeline.py::build_report` 传给 `build_news_alerts` 的域同一构造方式)。

    ⚠ **V2-⑬-11**:原本是「持仓 ∪ 自选」,自选池整链已按裁定 #9-a 删除 → 只剩持仓。
    ⑭-A 把篮子成员接进 `build_news_alerts` 的次级域时,这里要同步扩(两处必须同域,
    否则信息卡会对着一批"其实扫过"的票说"不在扫描域")。"""
    from neckline.sentinel.positions import load_open_positions

    return {p.ts_code for p in load_open_positions(db_path=db_path)}


def _load_lookback_top_lists(
    trade_date: date,
    *,
    n: int = TOP_LIST_LOOKBACK_TRADING_DAYS,
    parquet_dir: Optional[Path] = None,
    t0_top_list: Optional[Dict[str, dict]] = None,
) -> List[Tuple[date, Optional[Dict[str, dict]]]]:
    """近 `n` 个交易日(含 T0)的龙虎榜快照,`[(交易日, {ts_code:行} 或 None)]`
    升序。**T0 现拉现落盘**(`fetch_if_missing=True`,除非调用方已传入
    `t0_top_list` 复用 pipeline.py 已算好的一份);**T0 之前的历史日只读本地已落盘
    的日文件,不为凑齐而回补历史**(plan §五 v1.4-④-A-8 原话:"那是配额黑洞")——
    某天本地没有该文件 → 该天记 `None`(调用方据此算 `lookbackDaysCovered`,区分
    "查过确认没上榜" vs "压根没查到这天的数据")。"""
    days = _recent_trading_days(trade_date, n)
    out: List[Tuple[date, Optional[Dict[str, dict]]]] = []
    for d in days:
        if d == trade_date:
            lut = t0_top_list if t0_top_list is not None else top_list_lookup(
                d, parquet_dir=parquet_dir, fetch_if_missing=True
            )
            out.append((d, lut))
        elif day_file_exists("top_list", d, parquet_dir=parquet_dir):
            out.append((d, top_list_lookup(d, parquet_dir=parquet_dir, fetch_if_missing=False)))
        else:
            out.append((d, None))
    return out


def _top_list_summary_for_code(
    code: str, lookback: List[Tuple[date, Optional[Dict[str, dict]]]],
) -> InfoCardTopList:
    covered = sum(1 for _, lut in lookback if lut is not None)
    hits = sum(1 for _, lut in lookback if lut is not None and code in lut)
    t0_lut = lookback[-1][1] if lookback else None
    row = t0_lut.get(code) if t0_lut else None
    if row is None:
        return InfoCardTopList(
            on_list_today=False, reason=None, net_amount=None, net_rate=None,
            lookback_days_covered=covered, lookback_hit_days=hits,
        )
    return InfoCardTopList(
        on_list_today=True, reason=row.get("reason"),
        net_amount=(round(row["net_amount"], 1) if row.get("net_amount") is not None else None),
        net_rate=(round(row["net_rate"], 2) if row.get("net_rate") is not None else None),
        lookback_days_covered=covered, lookback_hit_days=hits,
    )


# ======================================================================
#  两条消费路径入口
# ======================================================================

def build_info_card(
    trade_date: date,
    code: str,
    *,
    k4_flags: List[str],
    name: Optional[str] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    industry_map: Optional[Dict[str, str]] = None,
    news_items: Optional[List[Dict[str, Any]]] = None,
    news_domain_codes: Optional[Set[str]] = None,
    top_list_t0: Optional[Dict[str, dict]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> InfoCard:
    """完整信息卡(单只,`GET /report/{date}/info-card/{code}` endpoint 用,plan §五
    v1.4-④-B)。**服务端现算,不落库**——除 `k4_flags`(§硬要求"不重算",调用方须从
    当日报告存档原样传入)外,其余全部独立于报告生成过程重新读 parquet/DB 现算,
    保证本端点自包含、可对任意历史交易日单独调用(只要该日数据已落地)。

    `industry_scores`/`industry_map`/`news_items`/`news_domain_codes`/`top_list_t0`
    均为可选的"调用方已算好,别再重算一遍"注入点(同 `build_intel_candidates`/
    `build_holding_k4_check` 的既有姿势)——`None` 时各自独立现算(现读 `news_alerts`
    表 + 现取持仓域 + 现拉 T0 龙虎榜),不依赖任何报告生成期的中间状态。
    """
    if name is None:
        name = resolve_stock_names([code], db_path).get(code, code)

    window_days = _recent_trading_days(trade_date, DISPLAY_WINDOW_TRADING_DAYS)
    window_start = window_days[0] if window_days else trade_date

    panel = _load_single_code_panel(code, trade_date, parquet_dir)
    window_panel = (
        panel.filter((pl.col("trade_date") >= window_start) & (pl.col("trade_date") <= trade_date))
        if not panel.is_empty() else panel
    )
    kline, kline_available, kline_reason = _build_kline(window_panel)
    t0_row = _row_at(panel, trade_date)

    industry_of = industry_map if industry_map is not None else load_industry_map(db_path)
    # v1.4-⑩(§七 P0-23):读预计算表,不再现算(现算 = 全历史扫描,本端点在生产上
    # **永不返回**,且跑在常驻服务内会把哨兵拖进内存回收死循环)。
    industry_hot = industry_strength_lookup(
        industry_scores if industry_scores is not None
        else load_industry_strength(trade_date, db_path=db_path)
    )
    # 表缺当日行 → `industry_hot` 空。**这是「没看」,不是「没有」**:此时 rank/persist
    # 一律**如实缺省(None)**,不写 0 —— 0 会被读成「评了、持续 0 天」(§3.8)。
    industry_ready = bool(industry_hot)
    industry_rank = stock_industry_rank(code, industry_of, industry_hot) if industry_ready else None
    persist_days = stock_persist_days(code, industry_of, industry_hot) if industry_ready else None
    industry_name = industry_of.get(code) or ""

    snapshot = _build_snapshot(t0_row, industry_rank, persist_days)
    mild = is_mild_band((t0_row or {}).get("ret_1d"))

    idx_states = (
        market_state_labels(window_start, trade_date, ma_window=20, parquet_dir=parquet_dir)
        if window_days else pl.DataFrame()
    )
    rs_available, rs_line, rs_reason = _build_rs_line(window_panel, idx_states)
    market = _build_market_context(trade_date, idx_states, parquet_dir)
    div_available, div_line, div_reason = _build_industry_divergence(
        code, industry_name, industry_rank, window_panel, window_start, trade_date, db_path,
        industry_ready=industry_ready,
    )

    k4_details = _k4_flags_detail(k4_flags, db_path)

    domain = news_domain_codes if news_domain_codes is not None else _default_news_domain(db_path)
    news = _news_summary_for_code(code, trade_date, domain, items=news_items, db_path=db_path)

    lookback = _load_lookback_top_lists(trade_date, parquet_dir=parquet_dir, t0_top_list=top_list_t0)
    top_list_summary = _top_list_summary_for_code(code, lookback)

    return InfoCard(
        code=code, name=name, trade_date=trade_date.strftime("%Y%m%d"),
        kline_available=kline_available, kline=kline, kline_unavailable_reason=kline_reason,
        rs_available=rs_available, rs_line=rs_line, rs_benchmark=SSE_INDEX, rs_unavailable_reason=rs_reason,
        industry_divergence_available=div_available, industry_divergence_line=div_line,
        industry=industry_name, industry_divergence_unavailable_reason=div_reason,
        snapshot=snapshot, k4_flags=k4_details, mild_band=mild,
        news=news, top_list=top_list_summary, market=market,
    )


__all__ = [
    "DISPLAY_WINDOW_TRADING_DAYS",
    "TOP_LIST_LOOKBACK_TRADING_DAYS",
    "MILD_BAND_RANGE",
    "INDUSTRY_DIVERGENCE_NOTE",
    "NEWS_DOMAIN_UNAVAILABLE_REASON",
    "is_mild_band",
    "InfoCardKlineBar",
    "InfoCardIndexPoint",
    "InfoCardSnapshot",
    "InfoCardK4Flag",
    "InfoCardNewsItem",
    "InfoCardNews",
    "InfoCardTopList",
    "InfoCardMarket",
    "InfoCard",
    "InfoCardSummary",
    "build_info_card",
]
