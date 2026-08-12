"""关注池组装(plan 阶段3 §2.4 的前置步骤;**V2-⑧-A 改组**,**V2.4.0 P0 缩编**)。
哨兵每拍只对一个有限、可解释的「关注池」批量拉价,不对全市场 ~5900 只票逐分钟
轮询——理由见 `retreat.py` 模块头注释(免费源持续高频全市场轮询的限流/稳定性
代价 vs 关注池已覆盖真正需要盯的票)。

🔴 **V2.4.0 P0 缩编:两份「纪律测量样本」的池位整体删除**

    退潮哨兵已退役(判断权撤销,见 `sentinel/engine.py` 模块头),它专用的两份
    测量样本随之删除 —— **它们进池的唯一理由就是给退潮判级当分母**:

    · `mainline_sample`(④ 机械种子按 `crc32` 取前 K 的配额切片)= 「主线板块跳水」;
    · `breadth_extra`(昨日涨停股)= 炸板率 / 跌停家数的**市场宽度代理样本**。

    连同它们的配额机器(`MANDATORY_POOL_RESERVE` / 两个 `*_QUOTA_FLOOR` /
    `_measurement_budget` / `_mainline_quota`)一并删除 —— 没有测量样本就没有
    「剩余池位怎么分」这个问题。
    ⚠ **`WatchTarget.invalidation_spec` 同批摘除**:那是**通用盘中证伪**的全局常量
    spec(`sentinel/invalidation.py`),随证伪哨兵退役断链。🔴 **⛔ 别与卡上的
    `invalidation_spec`(D0 冻结的判断失效位置,交易资格四件套之一)搞混** —— 同名
    不同物,后者一行未动。

    ⚠ **如实登记的连带影响**(不是 bug,是缩编的必然代价):`wu.codes` 由约 200 只
    降到**上界 29 只**,而 `auction/collect.py::build_watchlist` 把 `wu.codes` 当作
    ① 第 5 组「竞价强势股」的取样域、② 板块对照股(`SECTOR_PEER_MIN=3`)的取样域
    —— 两者的样本随之显著变小,「对照不足」从常态变成近乎必然。竞价层照常出报告
    (`rel_to_sector` 如实落 `None` + 原因码,本就是它既有的诚实降级路径)。

**现组成(V2.4.0 P0 起)**::

    持仓 ∪ **T1/T2 篮子成员**(D0 冻结的前两档,`baskets`/`basket_members`)
         ∪ **相关板块基准指数**

三处**刻意**的取舍,别当成疏漏:

    · **自选池已整链删除**(V2-⑬-11 执行完毕,裁定 #9-a):`neckline/watchlist.py`
      与 `report/watchlist_check.py` 已物理删除、`watchlist` 表停写留档;⑧-A 留下的
      两个恒空字段 `watchlist_codes`/`watchlist_candidates` 随之一并删除,
      `engine.py`/`precall.py` 的 `wu.candidates + wu.watchlist_candidates` 同步改写。
    · **候选已删**(V2-⑬-1 执行完毕):V1 候选榜与 `report.candidates.Candidate` 数据类
      全部退役。**证伪哨兵**曾把判定对象换成 T1/T2 篮子成员(`targets`),而它自己已于
      **V2.4.0 P0 退役** —— `targets` 因此**不再有纪律消费方**,降级为「今天盯着哪些
      篮子成员」的观测/日志位(码 / 名 / 所属篮子),⛔ 不许再给它接任何判定。
      ⚠ **买点哨兵(`sentinel/entry.py`)更早一批退役**:它 100% 由 K1 的 per-code
      `entry_spec`(platform_high / ma10 / breakout_vol_expand)驱动,V2 没有「单票买点
      计划」这个概念,给篮子成员现编一个买点 = 发明策略(§3.8 禁)。已如实登记。
    · **「相关板块 ETF/指数」本版落地为板块基准指数**(见 `BOARD_BENCHMARK_INDEX`):
      本项目**没有** ETF 成分/映射数据源(TuShare 600 元档未含,`ths_index` 是同花顺
      板块指数、免费实时源不认它的代码),硬编一份 ETF 清单等于凭空发明。指数是
      **可得且可核对**的那一半,已如实登记(⑧ 完工记录)。

**容量(≤ `breadth_cap`,默认 200;V2.4.0 P0 起池子只剩一段)**:

    【有界必需项,无条件全进】持仓(三仓制 ≤3)+ T1/T2 篮子成员(≤7 篮 × ≤3 = ≤21)
    + 板块基准指数(≤5)= **上界 29**,本来就有界。内部优先序 `持仓 > 成员 > 指数`
    (真要挤〔`breadth_cap` 被调得极小〕先挤没有纪律消费方的指数)。

⛔ **`breadth_cap` 一字不动(仍 200)** —— 它现在是一道**永远够用的上界闸**(必需项
上界 29 ≪ 200),留着是因为「关注池有没有硬上界」这件事本身要留;抬它会改盘中轮询
量与限流风险面(⑧-G-D 明文,守门单测锁死),⛔ 也别顺手把它调小去"贴合"29 ——
篮子超发时那道闸就是唯一的兜底。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import polars as pl

from neckline.calendar import prev_trading_day, trading_days_between
from neckline.data.board import Board, classify
from neckline.data.limit_derived import resolve_exempt_days
from neckline.data.market_data import load_stock_basic, scan_table_range
from neckline.selection.basket_store import BasketRef, load_baskets_for_date
from neckline.sentinel.positions import Position, load_open_positions

logger = logging.getLogger(__name__)

# 关注池总上限(见模块头「容量」)。V2.4.0 P0 起必需项上界 29 ≪ 200,它成了一道
# **永远够用的兜底闸**;⛔ 数值一字不动(守门单测锁死)。
DEFAULT_BREADTH_CAP = 200
# 计算前5日均量时的自然日回溯窗口(足够覆盖5个交易日,含长假缓冲)。
_VOLUME_LOOKBACK_DAYS = 15

# ⛔ V2.4.0 P0:池位配额三常量(`MANDATORY_POOL_RESERVE` /
# `MAINLINE_SLICE_QUOTA_FLOOR` / `PREV_LIMIT_UP_QUOTA_FLOOR`)随两份退潮测量样本
# 一并删除 —— 它们分的是"必需项之外还剩几个位",而现在没有第二段要分位子了。

# 盘中会被盯的篮子档位(T1/T2 = V2.1 起的全部现役档位)。
# **历史说明**:V2 时代有 T3 且它不进盘中池,那是**容量取舍**,不是"T3 不重要"——
# T3 篮在 EOD 那一拍照样被判。V2.1-② T3 全链退役后本元组的**值一字未动**(本就是
# `(1, 2)`),但含义从"三档里挑两档进池"变成"现役两档全进池"。
# ⚠ 历史日期回放时,库里的 tier=3 篮子按此不进盘中池 —— 与当年行为一致,刻意保持。
INTRADAY_BASKET_TIERS: Tuple[int, ...] = (1, 2)

# 「相关板块 ETF/指数」的可得落地 = **板块基准指数**(见模块头注释:没有 ETF 映射
# 数据源,不硬编清单)。代码取自 `scripts/backfill.py::INDEX_CODES` 同一批(本项目
# 追踪的指数),主板按交易所分沪深两支。
BOARD_BENCHMARK_INDEX: Dict[Board, str] = {
    Board.GEM: "399006.SZ",     # 创业板指
    Board.STAR: "000688.SH",    # 科创50
    Board.BSE: "899050.BJ",     # 北证50
}
MAIN_BOARD_INDEX_SH = "000001.SH"   # 上证综指
MAIN_BOARD_INDEX_SZ = "399001.SZ"   # 深证成指


@dataclass(frozen=True)
class WatchTarget:
    """今天盯着的一只 T1/T2 篮子成员(码 + 名 + 所属篮子)。

    ⛔ **V2.4.0 P0 起它没有纪律消费方** —— 原唯一消费方「证伪哨兵」已退役,
    `invalidation_spec` 字段随之摘除(那是**通用盘中证伪**的全局常量 spec,
    🔴 与卡上 D0 冻结的「判断失效位置」同名不同物)。现在它只是观测 / 日志位:
    冒烟脚本打一行「今天盯了几只成员」。⛔ 不许再给它接任何判定 —— 想在盘中拿它
    重算什么,就是把刚撤销的判断权换个名字接回来。"""
    ts_code: str
    name: str
    basket_key: str = ""        # 来自哪个篮子(审计/文案用,不参与判定)


@dataclass
class WatchUniverse:
    trade_date: date            # 哨兵运行的这一天(今天)
    report_date: date           # prev_trading_day(trade_date)——篮子理应来自这天
    report_found: bool          # 该日报告是否真的生成过(找不到不代表篮子也没有)
    targets: List[WatchTarget]  # 今天盯着的 T1/T2 篮子成员(观测位,无纪律消费方)
    positions: List[Position]
    # ⛔ V2.4.0 P0:`breadth_extra_codes` / `breadth_extra_needed` /
    # `breadth_extra_payload()`(昨日涨停宽度代理样本)与 `mainline_sample` /
    # `mainline_codes`(主线跳水配额切片)**五处一并删除** —— 它们只服务退潮判级。
    # —— V2-⑧-A 新增两类来源 ————————————————————————————————————————————
    baskets: List[BasketRef] = field(default_factory=list)      # D0 的 T1/T2 篮子(含成员)
    basket_codes: List[str] = field(default_factory=list)       # 上面那些篮子的成员代码(去重)
    index_codes: List[str] = field(default_factory=list)        # 相关板块基准指数(只进存拍/语境)
    codes: List[str] = field(default_factory=list)  # 去重后全部关注代码(拉价用)


def load_watch_universe(
    trade_date: date,
    *,
    breadth_cap: int = DEFAULT_BREADTH_CAP,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> WatchUniverse:
    """组装 `trade_date` 这一天的关注池。`db_path`/`parquet_dir` 均可覆盖,供单测
    注入隔离环境。无报告 / 无持仓 / 无篮子都不报错——空关注池是合法状态(如刚
    部署当天尚未跑过 `scripts/report.py`),调用方(engine.py/precall.py)据此
    优雅跳过对应哨兵。
    """
    from neckline.report import store

    report_date = prev_trading_day(trade_date)
    report = store.load_report(report_date, db_path=db_path)

    positions = load_open_positions(db_path=db_path)

    # —— V2-⑧-A:D0(= report_date)冻结的 T1/T2 篮子成员 ——————————————————
    baskets = _load_intraday_baskets(report_date, db_path=db_path)
    basket_codes: List[str] = []
    for b in baskets:
        for code in b.member_codes:
            if code not in basket_codes:
                basket_codes.append(code)

    # 今天盯着的 T1/T2 篮子成员(名称查不到就退回代码,不猜)。**观测位,无判定**。
    names = load_stock_meta(basket_codes, db_path=db_path) if basket_codes else {}
    basket_of: Dict[str, str] = {}
    for b in baskets:
        for code in b.member_codes:
            basket_of.setdefault(code, b.basket_key)
    targets = [
        WatchTarget(
            ts_code=code,
            name=(names[code].name if code in names else code),
            basket_key=basket_of.get(code, ""),
        )
        for code in basket_codes
    ]

    # —— 【有界必需项】持仓 > T1/T2 成员 > 板块指数,无条件全进(理由见模块头)——
    # V2.4.0 P0 起这就是关注池的**全部**;`breadth_cap` 只是兜底闸(29 ≪ 200)。
    index_codes = _related_index_codes(
        list(dict.fromkeys(basket_codes + [p.ts_code for p in positions])), db_path=db_path
    )
    mandatory: List[str] = []
    seen = set()
    for c in [p.ts_code for p in positions] + basket_codes + index_codes:
        if c not in seen:
            seen.add(c)
            mandatory.append(c)
    if len(mandatory) > breadth_cap:
        mandatory = mandatory[:breadth_cap]
        seen = set(mandatory)
        index_codes = [c for c in index_codes if c in seen]

    codes: List[str] = list(mandatory)

    return WatchUniverse(
        trade_date=trade_date,
        report_date=report_date,
        report_found=report is not None,
        targets=targets,
        positions=positions,
        baskets=baskets,
        basket_codes=basket_codes,
        index_codes=index_codes,
        codes=codes,
    )


# ⛔ V2.4.0 P0:`_measurement_budget` / `_mainline_quota` / `_derive_mainline_sample`
# 三个池位配额函数随两份退潮测量样本一并删除。


def _load_intraday_baskets(report_date: date, *, db_path: Optional[Path]) -> List[BasketRef]:
    """D0 的 T1/T2 篮子。读库失败 / 无篮子 → 空列表(**合法状态**:V2 引擎还没跑过、
    或今日无篮子达到定档标准);⛔ 绝不因此掀翻整个关注池 —— 哨兵拿不到篮子也还要
    盯持仓。"""
    try:
        return load_baskets_for_date(report_date, tiers=INTRADAY_BASKET_TIERS, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 读 %s 的 T1/T2 篮子失败,本拍关注池不含篮子成员",
                       report_date, exc_info=True)
        return []


def _related_index_codes(codes: List[str], *, db_path: Optional[Path]) -> List[str]:
    """这批票**所在板块**的基准指数(去重、排序,**确定性**)。板块判定唯一源
    `load_stock_meta`(→ `data/board.classify`),⛔ 不自己写前缀正则。查无元数据的
    代码跳过(既判不了板块,也就给不出"相关指数",不猜)。"""
    if not codes:
        return []
    try:
        meta = load_stock_meta(codes, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[universe] 查板块元数据失败,本拍关注池不含板块指数", exc_info=True)
        return []
    out: set = set()
    for code, m in meta.items():
        if m.board == Board.MAIN:
            out.add(MAIN_BOARD_INDEX_SH if code.upper().endswith(".SH") else MAIN_BOARD_INDEX_SZ)
        elif m.board in BOARD_BENCHMARK_INDEX:
            out.add(BOARD_BENCHMARK_INDEX[m.board])
    return sorted(out)


# ⛔ V2.4.0 P0:`_load_prev_limit_up_codes`(D0 全部涨停股,按 `crc32` 升序)删除 ——
# 它的唯一消费方是退潮宽度代理样本。⚠ 那条「截取顺序不得与被测量的量相关」的采样
# 纪律**没有失效**,只是本仓暂时没有第二个消费方;要再取样先读 `sentinel/mainline.py`
# 模块头(`crc_rank` 仍是全项目 crc32 排序的唯一实现)。


def load_prev5_avg_volume(
    codes: List[str], as_of: date, parquet_dir: Optional[Path] = None
) -> Dict[str, float]:
    """`codes` 各自的前5个交易日平均成交量(手,EOD `daily.vol` 口径),供
    `intraday.intraday_vol_ratio` 的基准输入。`as_of` 通常是哨兵运行的今天——
    基准取"今天之前"的5个交易日,不含今天(今天尚未收盘,数据本就还没有)。

    一次批量 scan(不逐票查询),codes 数量在数百量级时开销可忽略。数据不足5天
    (如新股)→ 用实际可用天数的均值;完全无历史 → 该票不出现在返回 dict 里
    (调用方按 `.get(code)` 处理缺失,自然走 `intraday_vol_ratio` 的 no_base 分支)。
    """
    if not codes:
        return {}
    end = prev_trading_day(as_of)
    start = end - timedelta(days=_VOLUME_LOOKBACK_DAYS)
    df = scan_table_range("daily", start, end, parquet_dir=parquet_dir)
    if df.is_empty():
        return {}
    df = df.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "trade_date", "vol"])
    out: Dict[str, float] = {}
    for (code,), sub in df.group_by(["ts_code"]):
        vols = sub.sort("trade_date")["vol"].to_list()[-5:]
        if vols:
            out[code] = sum(vols) / len(vols)
    return out


@dataclass
class StockMeta:
    ts_code: str
    name: str
    board: Board
    is_st: bool
    list_date: Optional[date]


def load_stock_meta(codes: List[str], db_path: Optional[Path] = None) -> Dict[str, StockMeta]:
    """`ts_code -> StockMeta`,供退潮/持仓哨兵算涨跌停价(`limit_derived.
    compute_intraday_limit_prices` 需要 board/is_st/trade_date)与展示名称用。

    `is_st` 直接读 `stock_basic.name`(当前名称)前缀——不做历史 as-of 判定
    (那是回测/报告的关切,盘中哨兵只关心"现在"),但要求 `stock_basic` 是最新的
    (`scripts/daily_update.py` 每交易日盘后刷新 `bootstrap_metadata()`,见该脚本)。
    """
    if not codes:
        return {}
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return {}
    sb = sb.filter(pl.col("ts_code").is_in(codes))
    out: Dict[str, StockMeta] = {}
    for row in sb.iter_rows(named=True):
        name = row["name"] or row["ts_code"]
        is_st = name.strip().strip("*").upper().startswith("ST")
        out[row["ts_code"]] = StockMeta(
            ts_code=row["ts_code"],
            name=name,
            board=classify(row["market"], row["ts_code"]),
            is_st=is_st,
            list_date=row["list_date"],
        )
    return out


def is_new_stock_exempt(meta: StockMeta, trade_date: date) -> bool:
    """该票在 `trade_date` 是否仍处于新股涨跌幅豁免窗口(见
    `neckline.data.limit_derived.resolve_exempt_days`)。`list_date` 未知 → 保守按
    "已过豁免期"处理(不放过涨跌停判定;宁可误判老股为受限,不放过真正该防的新股)。

    **性能/日志噪音坑(施工中实测踩到,已修)**:A 股大量主板老股 `list_date` 在
    2015 年之前(`neckline.calendar` 的 trade_cal DB 覆盖默认从 2015-01-01 起,
    见 `scripts/init_calendar.py`)——若直接对每只票调用
    `trading_days_between(list_date, trade_date)` 算精确交易日数,老股会命中
    "查询早于DB覆盖范围"分支,退化为**逐自然日** `is_trading_day` 判断 + 每天
    一条 warning 日志,几十年区间循环下来极慢且刷屏。**先用自然日差做廉价预筛**
    ——超过30自然日(远大于任何板块的5交易日豁免窗口上限)必然已过豁免期,直接
    返回 False,不进入昂贵的精确计算;只有"看起来像最近一个月内上市"的票才会
    走精确的 `trading_days_between`(此时区间必然很小,即使意外落在DB覆盖范围
    外,回退成本也可忽略)。
    """
    if meta.list_date is None or meta.list_date > trade_date:
        return False
    if (trade_date - meta.list_date).days > 30:
        return False
    exempt_days = resolve_exempt_days(meta.board, meta.list_date)
    days_since_listing = len(trading_days_between(meta.list_date, trade_date))
    return 0 < days_since_listing <= exempt_days


__all__ = [
    "WatchTarget",
    "WatchUniverse",
    "load_watch_universe",
    "load_prev5_avg_volume",
    "StockMeta",
    "load_stock_meta",
    "is_new_stock_exempt",
    "DEFAULT_BREADTH_CAP",
    "INTRADAY_BASKET_TIERS",
    "BOARD_BENCHMARK_INDEX",
    "MAIN_BOARD_INDEX_SH",
    "MAIN_BOARD_INDEX_SZ",
]
