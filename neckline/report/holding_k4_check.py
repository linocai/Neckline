"""持仓 K4 每日体检(plan §五 v1.3-②,需求 7)。K4 红黄牌此前只用于**买前安检**;
用户定案「持仓不是冷冻的」——牌须每日对**持仓票**重算并派发警示。本模块 = 16:35 EOD
报告管线里对每只 open 持仓在当日面板上重算 K4 advisory 命中(同「自选体检
`watchlist_check.py`」姿势,只把输入域换成持仓),并算好 **D5 收盘净浮盈**(供 v1.3-①
留的 precall net_float_provider seam)。

**判据源:DB `strategy_versions` 的 K4 行 `rule_json["k4_advisory"]`(is_active=0,只读)。**
⚠ advisory 里的 `expr` 是**人读字符串/规格档**(如 `"turnover_rate > 10"`、`"行业强度
(top20%中位数)连续≥4天成员"`),**不是可执行代码**——本模块写**可执行 polars 镜像**
逐条对齐 advisory 语义。**镜像与 advisory 文字的对应关系(逐条)**:

    advisory 码                     | advisory expr(规格档,DB)                          | 本模块 polars 镜像(可执行)
    -------------------------------|---------------------------------------------------|------------------------------------------------
    A1_turnover_gt_10              | turnover_rate > 10                                | pl.col("turnover_rate") > _A1_TURNOVER_HI(=10)
    A2_theme_persist_ge_4          | 行业强度(top20%中位数)连续≥4天成员                | 概念板块 board_age(sectors.py)≥ _A2_PERSIST_MIN(=4)★
    A3_belowyear_limitup           | TREND_BELOW & is_limit_up                         | _trend_below_expr() & pl.col("is_limit_up")
    A3b_belowyear_bigvol(派发放量) | (雷区地图 3-⑤:年线下 ret1d≥5%×量比≥2=派发)★★     | _trend_below_expr() & _dispatch_bigred_expr()(量比 vol/vol_ma5≥2)
    B1_volume_stacking             | vol_above_ma20_cnt3≥2 & ret_1d≥5% & vol>vol_ma20×1.5 | _big_red_expr() & ~_trend_below_expr()(年线上才算普通堆积)
    B2_dual_golden_cross           | MACD多头(DIF>DEA) & KDJ多头(K>D)=双金叉态          | state4 == "①双金叉态"(_add_macd_kdj 镜像)
    B3_theme_persist_2_3           | 行业强度连续2-3天成员                              | 2 ≤ board_age ≤ 3 ★
    B4_chase_strong_red            | close>ma20 & ret_1d>5%                             | (close>ma20) & (ret_1d > _B4_UP=5%)

    ★ 题材持续天数镜像口径说明:advisory A2/B3 用 `research/k4p_h6_theme.py` 的「行业
      (stock_basic.industry top20% 中位数)连续强度日」;本模块复用报告已算好的**概念板块
      board_age**(`report/sectors.py::_add_board_age`,= 板块指数连续站上 MA20 的交易日数,
      经 member_map 取持仓票所属热门板块的最大值)——两者都是「题材持续天数」代理,**均依赖
      概念/行业成分(K2 成分洞)= 弱证据**,故 A2/B3 一律标 `constituent`(参考)、**不单独
      触发强警示 APNs**(§2.4「证伪只用价量结构」+ 用户 2026-07-26 拍板)。复用报告 board_age
      而非重建 industry 持续性管线,守「同码不重写」,且免在持仓管线里重算全市场行业中位数。
    ★★ A3b 是 STRATEGY_LAB Backlog「诱多做局反向哨兵」并入本需求(2026-07-26 立项)——
      年线下放量大阳(`ret1d≥5%×量比≥2`,数字口径雷区地图 3-⑤:事后 3 日 −1.04%)与年线下
      涨停(A3)同为「派发/诱多」强价量信号。plan §五 v1.3-②-B 把「放量大阳」记作
      `B1_volume_stacking` 且列在强警示,与 B1 也列普通警示的表面矛盾,唯一自洽解 = **年线下 → 派发
      (强,推 APNs);年线上 → 普通堆积(只进看板)**,由 `_trend_below_expr()` 闸分级(与雷区地图
      「放量大阳只在年线下为负」证据一致)。**⚠ A3b 的量能门槛贴雷区地图 3-⑤ 实测口径(量比≥2)、
      不套 B1 的 cnt3 堆积条件,B1 贴 DB advisory 原文(×1.5),两者刻意分叉**——强警示要推锁屏,
      门槛必须 = 实测证据集合、不能比证据更宽(详见 `_A3B_VOLUME_RATIO_HI` 常量注释)。

**阈值单一源(§3.8,同 `sentinel/circuit.py` 常量 vs advisory `circuit_breaker` 文字先例)**:
判据阈值 = 本模块命名常量(可执行镜像),advisory 文字 = 规格档(策略线档案)。**改阈值须
同改两处**(DB advisory 文字 + 本模块常量)。DB advisory 读到则用其 `evidence` 文字诚实透出
研究依据;K4 行缺失(隔离测试库)→ 用模块 `_FALLBACK_EVIDENCE` 兜底,镜像判据照跑不崩。

**分级(plan §五 v1.3-②-B)**:
    · **强警示(level=strong,→ 第六类 APNs + 看板)**:A1 换手>10 / A3 年线下涨停 /
      A3b 年线下放量大阳(三者 evidence_strength=price_volume,价量硬数据)。A2 题材≥4天
      level=strong 但 evidence_strength=constituent(弱证据/参考)→ **不单独触发 APNs**。
    · **普通警示(level=normal,→ 只进看板/报告卡)**:B1 量能堆积 / B2 双金叉 / B4 追强大红
      (price_volume) + B3 题材2-3天(constituent/参考)。
    · **第六类 APNs 门槛 `has_strong`** = 命中含「level=strong ∧ evidence_strength=price_volume」。

**net_float(v1.3-① seam)**:D5 收盘净浮盈 = 现价(EOD 面板 close,前复权锚点在 trade_date
故 = 当日原始收盘,见 `features.apply_qfq` docstring)×qty − buy_price×qty − buy_fees(实录) −
估算卖出费(`fees.estimate_net_float`,买入费读 `positions.buy_fees`)。停牌/无 EOD 数据 → None
(precall 侧退保守判非浮盈)。持久化 + precall 读取见 `neckline.report.holding_store` 与
`sentinel/precall.py`。

**系统永不代交易动作**(§3.8):本模块只算命中/警示,不触发任何下单/撤单/改止损。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import polars as pl

from neckline.data.adjust import apply_qfq
from neckline.fees import estimate_net_float
from neckline.report.sectors import SectorScore, sector_hot_lookup
from neckline.sentinel.positions import Position, d_count
from neckline.sentinel.precall import classify_time_exit
from neckline.strategy.features import add_features, merge_daily_basic, merge_limit_features
from neckline.strategy.momentum import MomentumConfig

# —— 阈值命名常量(可执行镜像单一源;镜像 research/k4_assembly.py 判决口径,改阈值同改 DB advisory)——
_A1_TURNOVER_HI = 10.0    # A1:换手 >10%(turnover_rate 单位为百分数,H2)
_A2_PERSIST_MIN = 4       # A2:题材持续 ≥4 天(H6;本模块用概念板块 board_age 代理)
_BIGRED_UP = 0.05         # A3b/B1 放量大阳共用:当日涨幅 ≥5%
# ⚠ A3b(年线下派发,**强警示,推 APNs**)与 B1(年线上普通堆积,**只进看板**)的量能门槛
# 故意分叉——**这正是「可执行镜像 vs 人读规格档」允许分叉、但分叉必须写明理由的地方**:
#   · A3b `_A3B_VOLUME_RATIO_HI`=2.0:贴 STRATEGY_LAB 雷区地图 3-⑤ 的**实测证据口径**
#     ——那个「年线下放量大阳事后 3 日 −1.04%」是在「ret1d≥5% × 量比(vol/vol_ma5)≥2」下测出的。
#     强警示要推到用户锁屏,可信度挂在证据上:门槛必须 = 实测口径,不能比证据更宽(否则多报的
#     那部分无证据支撑)。故 A3b 单用量比≥2、**不套 B1 的 cnt3 堆积条件**(证据口径里没有 cnt3)。
#   · B1 `_B1_MULT`=1.5 / `_B1_CNT3`=2:贴 DB advisory 的 **B1_volume_stacking 原文形态**
#     (`cnt3≥2 & vol>vol_ma20×1.5`,H1「堆积后再放量」)。B1 只进看板不推送,宽一点无害,且那是
#     advisory 的原文。**勿把这两个阈值「统一」回同一个——分叉是刻意的,不是笔误。**
_A3B_VOLUME_RATIO_HI = 2.0  # A3b:量比 vol/vol_ma5 ≥2(= 面板 vol_ratio_5,贴雷区地图 3-⑤ 实测口径)
_B1_MULT = 1.5            # B1(年线上普通堆积)专用:vol > vol_ma20×1.5(DB advisory 原文形态)
_B1_CNT3 = 2             # B1 专用:前 3 交易日放量天数 ≥2(vol_above_ma20_cnt3,advisory 原文)
_B3_PERSIST_LO, _B3_PERSIST_HI = 2, 3   # B3:题材持续 2-3 天(认可题材=接盘侧)
_B4_UP = 0.05             # B4:追强大红,close>ma20 & ret_1d>5%

# 前复权价列(与 features.py 同一组,本地常量避免 import 私名)
_QFQ_PRICE_COLS = ("open", "high", "low", "close", "pre_close")
# ma250 需 250 交易日 + slope 需再前 20 → 取 ~420 自然日(≈275 交易日)前置缓冲,保证 EOD 行 ma250 非空。
_LOOKBACK_CALENDAR_DAYS = 420
_MACD_WARMUP_BARS = 34    # MACD DEA(EMA9 of DIF,DIF~EMA26)冷启动去偏,镜像 k4p_h4_cross

# 评估状态码复用 precall 单一源(不新造字面量)。
from neckline.sentinel.precall import HOLDING as _HOLDING  # noqa: E402

# advisory 缺读(隔离测试库无 K4 行)时的证据兜底文字(镜像 research 判决摘要;生产恒读 DB)。
_FALLBACK_EVIDENCE: Dict[str, str] = {
    "A1_turnover_gt_10": "换手>10% 次日跌停 3.37%,九倍于低换手(<5%)",
    "A2_theme_persist_ge_4": "题材持续≥4天 -1.07%,次日跌停 1.04%(过热/接盘区)",
    "A3_belowyear_limitup": "年线下涨停=诱多域,2026 -3.96%、左尾肥",
    "B1_volume_stacking": "堆积后再放量拉升像诱多末端,次日跌停 3.7×",
    "B2_dual_golden_cross": "双金叉态四态垫底(-0.40%),左尾最肥、次日跌停最高",
    "B3_theme_persist_2_3": "题材持续2-3天 -0.59%,劣于域基线(认可题材=接盘侧)",
    "B4_chase_strong_red": "追强大红 close>ma20>5% -1.43%,追强诱多",
}
# A3b(派发放量大阳)非 DB advisory 码,证据源 = STRATEGY_LAB 雷区地图 3-⑤(Backlog 立项数字)。
_A3B_EVIDENCE = "年线下放量大阳(ret1d≥5%×量比≥2)事后 3 日 -1.04%,诱多做局(雷区地图 3-⑤)"

# 分级/证据强度定义(单一源;code -> (label, level, evidence_strength))。level=strong ∧
# evidence_strength=price_volume 才触发第六类 APNs(has_strong);A2 strong 但 constituent 不触发。
_HIT_META: Dict[str, tuple] = {
    "A1_turnover_gt_10":     ("换手率 >10%(过热放量,接盘区)", "strong", "price_volume"),
    "A3_belowyear_limitup":  ("年线下涨停(疑似诱多做局派发)", "strong", "price_volume"),
    "A3b_belowyear_bigvol":  ("年线下放量大阳(疑似派发做局)", "strong", "price_volume"),
    "A2_theme_persist_ge_4": ("题材持续≥4天(过热/接盘;成分类参考)", "strong", "constituent"),
    "B1_volume_stacking":    ("量能堆积大涨(诱多末端形态)", "normal", "price_volume"),
    "B2_dual_golden_cross":  ("双金叉态(四态垫底,左尾最肥)", "normal", "price_volume"),
    "B3_theme_persist_2_3":  ("题材持续2-3天(认可题材=接盘侧;成分类参考)", "normal", "constituent"),
    "B4_chase_strong_red":   ("追强大红(close>ma20 且涨>5%)", "normal", "price_volume"),
}


@dataclass
class HoldingK4Hit:
    code: str
    label: str
    level: str               # strong | normal
    evidence: str            # advisory 证据口径(DB 读到用 DB,否则兜底)
    evidence_strength: str   # price_volume | constituent

    def public_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HoldingK4Item:
    position_id: int
    ts_code: str
    name: str
    has_data: bool
    d_count: int
    close: float = 0.0
    net_float: Optional[float] = None
    time_exit_state: str = _HOLDING
    max_hold_effective: int = 5
    hits: List[HoldingK4Hit] = field(default_factory=list)
    has_strong: bool = False       # 含「强价量证据」命中 → 触发第六类 APNs 派发警报门槛
    scenario_review: bool = False  # 关联决策日志(via position_id)含非空情景树待每日对照

    def strong_price_volume_labels(self) -> List[str]:
        """触发第六类 APNs 的强价量命中文案(题材类弱证据不计入,守 §2.4)。"""
        return [h.label for h in self.hits if h.level == "strong" and h.evidence_strength == "price_volume"]

    def hits_public(self) -> List[Dict[str, Any]]:
        return [h.public_dict() for h in self.hits]


# —— 判据 polars 表达式(镜像,逐条对齐 advisory,见模块头对照表)————————————————————

def _trend_below_expr() -> pl.Expr:
    """TREND_BELOW(年线下降势域,镜像 k4_assembly.py / k4p_h7_bounce):ma250 非空 且
    ~(close>ma250 且 ma250 上行)。缺 ma250(次新/数据不足)→ 非年线下(不妄判派发)。"""
    return pl.col("ma250").is_not_null() & ~(
        (pl.col("close") > pl.col("ma250")) & pl.col("ma250_slope_up")
    )


def _oneword_expr() -> pl.Expr:
    """一字板(涨/跌停且无振幅,镜像 k4p_common.oneword_event_expr):放量大阳排除一字(不可交易)。"""
    return (pl.col("is_limit_up") | pl.col("is_limit_down")) & (pl.col("high") == pl.col("low"))


def _dispatch_bigred_expr() -> pl.Expr:
    """A3b **年线下派发放量大阳**的价量形态——**贴 STRATEGY_LAB 雷区地图 3-⑤ 的实测证据口径**
    `ret1d≥5% × 量比(vol/vol_ma5)≥2`(那个「事后 3 日 −1.04%」正是在此口径下测出)。强警示要推
    锁屏,门槛 = 实测证据集合:**单用量比≥2、不套 B1 的 cnt3 堆积条件**(证据口径里没有 cnt3),
    量比用面板 `vol_ratio_5`(= vol/vol_ma5,`features.add_features` 已算)。非一字(不可交易剔除)。"""
    return (
        (pl.col("ret_1d") >= _BIGRED_UP)
        & (pl.col("vol_ratio_5") >= _A3B_VOLUME_RATIO_HI)
        & ~_oneword_expr()
    )


def _big_red_expr() -> pl.Expr:
    """B1 **年线上普通堆积**的价量形态(镜像 advisory B1_volume_stacking 原文 / k4_assembly B1_stack):
    前 3 日放量天数≥2 & 当日涨≥5% & 量>vol_ma20×1.5 & 非一字。**只进看板不推送**,故沿用 advisory
    原文口径(×1.5),与 A3b 的实测口径(量比≥2)刻意分叉,见 `_A3B_VOLUME_RATIO_HI` 常量注释。"""
    return (
        (pl.col("vol_above_ma20_cnt3") >= _B1_CNT3)
        & (pl.col("ret_1d") >= _BIGRED_UP)
        & (pl.col("vol") > pl.col("vol_ma20") * _B1_MULT)
        & ~_oneword_expr()
    )


def _add_hit_columns(panel: pl.DataFrame) -> pl.DataFrame:
    """在持仓面板上加各价量类命中布尔列(题材类 A2/B3 由 board_age 另算,不在此)。
    A3b/B1 由 `_trend_below_expr()` 闸分级:年线下放量大阳(量比≥2 实测口径)=A3b 派发(强);
    年线上量能堆积(advisory ×1.5 原文口径)=B1(普通)。两者量能门槛刻意分叉(见常量注释)。"""
    trend_below = _trend_below_expr()
    return panel.with_columns(
        (pl.col("turnover_rate") > _A1_TURNOVER_HI).fill_null(False).alias("_hit_A1"),
        (trend_below & pl.col("is_limit_up")).fill_null(False).alias("_hit_A3"),
        (trend_below & _dispatch_bigred_expr()).fill_null(False).alias("_hit_A3b"),
        (_big_red_expr() & ~trend_below).fill_null(False).alias("_hit_B1"),
        (pl.col("state4") == "①双金叉态").fill_null(False).alias("_hit_B2"),
        ((pl.col("close") > pl.col("ma20")) & (pl.col("ret_1d") > _B4_UP)).fill_null(False).alias("_hit_B4"),
    )


# —— K4 专属特征镜像(生产面板未算,本模块补;逐条对齐研究定义)————————————————————

def _add_macd_kdj(df: pl.DataFrame) -> pl.DataFrame:
    """逐票 MACD 多头态 / KDJ 多头态 + 四态标签 `state4`。**逐字镜像
    `research/k4p_h4_cross.py::add_macd_kdj`**(MACD 12/26/9 EMA adjust=False;KDJ 9/3/3
    RSV→K/D ewm α=1/3;warmup 34 去冷启动)——只取 B2「①双金叉态」判据。"""
    df = df.sort(["ts_code", "trade_date"])
    over = "ts_code"
    ema12 = pl.col("close").ewm_mean(span=12, adjust=False).over(over)
    ema26 = pl.col("close").ewm_mean(span=26, adjust=False).over(over)
    df = df.with_columns((ema12 - ema26).alias("_dif"))
    df = df.with_columns(pl.col("_dif").ewm_mean(span=9, adjust=False).over(over).alias("_dea"))
    rmin = pl.col("low").rolling_min(9, min_samples=9).over(over)
    rmax = pl.col("high").rolling_max(9, min_samples=9).over(over)
    rng = rmax - rmin
    rsv = pl.when(rng > 0).then((pl.col("close") - rmin) / rng * 100).otherwise(50.0)
    df = df.with_columns(rsv.alias("_rsv"))
    df = df.with_columns(pl.col("_rsv").ewm_mean(alpha=1 / 3, adjust=False, ignore_nulls=True).over(over).alias("_k"))
    df = df.with_columns(pl.col("_k").ewm_mean(alpha=1 / 3, adjust=False, ignore_nulls=True).over(over).alias("_d"))
    bar_idx = pl.col("trade_date").cum_count().over(over)
    warm = bar_idx >= _MACD_WARMUP_BARS
    kdj_ready = pl.col("_k").is_not_null() & pl.col("_d").is_not_null()
    df = df.with_columns(
        pl.when(warm).then(pl.col("_dif") > pl.col("_dea")).otherwise(None).alias("macd_bull"),
        pl.when(warm & kdj_ready).then(pl.col("_k") > pl.col("_d")).otherwise(None).alias("kdj_bull"),
    )
    both_ready = pl.col("macd_bull").is_not_null() & pl.col("kdj_bull").is_not_null()
    state = (
        pl.when(~both_ready).then(None)
        .when(pl.col("macd_bull") & pl.col("kdj_bull")).then(pl.lit("①双金叉态"))
        .when(pl.col("macd_bull") & ~pl.col("kdj_bull")).then(pl.lit("②仅MACD"))
        .when(~pl.col("macd_bull") & pl.col("kdj_bull")).then(pl.lit("③仅KDJ"))
        .otherwise(pl.lit("④双空头态"))
    )
    return df.with_columns(state.alias("state4"))


def _add_k4_features(df: pl.DataFrame) -> pl.DataFrame:
    """在持仓面板上叠加 K4 判据需要、生产 `add_features` 未算的列:ma250 + slope(镜像
    `research/k3_panel.py`)、vol_above_ma20_cnt3(镜像 `k4p_common.add_k4p_features`)、
    MACD/KDJ state4。"""
    df = df.sort(["ts_code", "trade_date"])
    over = "ts_code"
    df = df.with_columns(pl.col("close").rolling_mean(250, min_samples=250).over(over).alias("ma250"))
    df = df.with_columns((pl.col("ma250") > pl.col("ma250").shift(20).over(over)).alias("ma250_slope_up"))
    is_vol_above = (pl.col("vol") > pl.col("vol_ma20")).cast(pl.Int64)
    df = df.with_columns(
        is_vol_above.rolling_sum(3, min_samples=3).over(over).shift(1).over(over).alias("vol_above_ma20_cnt3")
    )
    return _add_macd_kdj(df)


# —— 持仓面板装配(held-stocks-only,内存友好:≤3 仓,不载全市场 250 日)——————————————

# 原始表区间加载器签名:`(codes, start, end, table, parquet_dir) -> pl.DataFrame`。
# ② 持仓体检(≤3 仓)默认走 `_load_codes_table`(逐票 get_stock_history,内存友好);
# ③ 候选情报管线(全板块数千只 universe)注入 bulk 全市场 loader(见
# `report/intel_candidates.py::_bulk_load_codes_table`)——**两条 I/O 路径共用下方
# `_build_holding_feature_panel` 的同一份特征/判据装配**(qfq→add_features→merge→
# `_add_k4_features`→`_add_hit_columns`),阈值单一源(本模块命名常量),只有原始
# 表 I/O 方式不同(plan §五 v1.3-③-C3「性能坑」二选一之(a):复用判据表达式 + 换
# 全市场面板 I/O;两处镜像一致性由 `tests/test_intel_candidates.py` 直接对拍)。
TableLoader = Callable[[List[str], date, date, str, Optional[Path]], "pl.DataFrame"]


def _load_codes_table(
    codes: List[str], start: date, end: date, table: str, parquet_dir: Optional[Path]
) -> pl.DataFrame:
    """按 code 逐票取区间(≤3 仓,循环 get_stock_history 免载全市场)。空 → 空 DataFrame。"""
    from neckline.data.market_data import get_stock_history

    frames = [
        f for c in codes
        if not (f := get_stock_history(c, start, end, table=table, parquet_dir=parquet_dir)).is_empty()
    ]
    return pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()


def _build_holding_feature_panel(
    codes: List[str],
    trade_date: date,
    parquet_dir: Optional[Path],
    *,
    load_fn: Optional[TableLoader] = None,
) -> pl.DataFrame:
    """持仓/候选票当日 EOD 特征面板(含 K4 专属列)。**只载相关票 ~420 自然日历史**
    (ma250 需 250 交易日),复用生产 `add_features`/`merge_limit_features`/
    `merge_daily_basic` + 本模块 `_add_k4_features`。返回仅 trade_date 当日行(每票 ≤1
    行);查无该日行的票(停牌/未上市)不在返回集,调用方按缺行判 has_data=False。

    `load_fn`:原始表区间加载器(默认 `_load_codes_table` 逐票循环,② 持仓用);③ 候选
    情报管线注入 bulk 全市场 loader(数千只 universe 逐票循环会很慢)——**特征/判据
    装配对两者完全相同**,仅 I/O 不同(见 `TableLoader` 注释)。"""
    loader: TableLoader = load_fn or _load_codes_table
    load_start = trade_date - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
    daily = loader(codes, load_start, trade_date, "daily", parquet_dir)
    if daily.is_empty():
        return pl.DataFrame()
    adj = loader(codes, load_start, trade_date, "adj_factor", parquet_dir)
    if not adj.is_empty():
        merged = daily.join(
            adj.select(["ts_code", "trade_date", "adj_factor"]), on=["ts_code", "trade_date"], how="left"
        )
        adjusted = apply_qfq(merged, price_cols=_QFQ_PRICE_COLS)
        qfq_cols = [f"{c}_qfq" for c in _QFQ_PRICE_COLS]
        daily = adjusted.drop(list(_QFQ_PRICE_COLS)).rename(dict(zip(qfq_cols, _QFQ_PRICE_COLS)))
    panel = add_features(daily)
    panel = merge_limit_features(panel, loader(codes, load_start, trade_date, "limit_derived", parquet_dir))
    panel = merge_daily_basic(panel, loader(codes, load_start, trade_date, "daily_basic", parquet_dir))
    panel = _add_k4_features(panel)
    panel = _add_hit_columns(panel)
    return panel.filter(pl.col("trade_date") == trade_date)


# —— advisory 读取(DB 单一源,不抄常量;缺读兜底不崩)————————————————————————————

def _load_k4_evidence(db_path: Optional[Path]) -> Dict[str, str]:
    """读 DB `strategy_versions` K4 行 `k4_advisory` 的各码 `evidence` 文字(诚实透出研究
    依据)。K4 行缺失(隔离测试库)/ 结构异常 → 空 dict,调用方落 `_FALLBACK_EVIDENCE`。
    **只读 K4 行的 evidence 文字,判据阈值仍住本模块命名常量(镜像),见模块头。**"""
    from neckline.strategy import brain

    try:
        v = brain.get_version("K4", db_path=db_path)
    except Exception:  # noqa: BLE001  隔离库读失败不崩
        return {}
    if v is None:
        return {}
    adv = (v.rule or {}).get("k4_advisory") or {}
    out: Dict[str, str] = {}
    for section in ("hard_cut", "avoid_flag"):
        for code, spec in (adv.get(section) or {}).items():
            if isinstance(spec, dict) and spec.get("evidence"):
                out[code] = str(spec["evidence"])
    return out


def load_k4_sections(db_path: Optional[Path] = None) -> Dict[str, str]:
    """读 DB `strategy_versions` K4 行 `k4_advisory` 的**分区归属** `{advisory 码: 'hard_cut'
    | 'avoid_flag'}`(plan §五 v1.3-③-C3-③:hard_cut 命中→拦截出池、avoid_flag 命中→打标
    保留)。**单一事实源 = DB**(不抄常量);K4 行缺失(隔离测试库)/ 结构异常 → 空 dict,
    调用方对空 dict 的兜底策略见 `intel_candidates._DEFAULT_SECTION`。

    ⚠ 真实 DB(2026-07-26):hard_cut={A1_turnover_gt_10, A2_theme_persist_ge_4,
    A3_belowyear_limitup, A4_base_hygiene}、avoid_flag={B1_volume_stacking,
    B2_dual_golden_cross, B3_theme_persist_2_3, B4_chase_strong_red}。其中 A4_base_hygiene
    = base_universe+非次新,候选管线 ② 已前置强制满足、`_evaluate_hits` 也不产 A4 命中,
    故 A4 在候选侧永不触发(见 intel_candidates docstring)。合成派发码 A3b_belowyear_bigvol
    **不在 DB**(证据源=雷区地图 3-⑤),其归属由调用方按 `_DEFAULT_SECTION` 决定。"""
    from neckline.strategy import brain

    try:
        v = brain.get_version("K4", db_path=db_path)
    except Exception:  # noqa: BLE001  隔离库读失败不崩
        return {}
    if v is None:
        return {}
    adv = (v.rule or {}).get("k4_advisory") or {}
    out: Dict[str, str] = {}
    for section in ("hard_cut", "avoid_flag"):
        for code in (adv.get(section) or {}):
            out[str(code)] = section
    return out


def _theme_persist_days(code: str, member_map: Dict[str, List[str]], hot: Dict[str, SectorScore]) -> int:
    """持仓票的题材持续天数代理 = 其所属**热门**概念板块中 board_age 的最大值(不在热榜的
    板块视作未持续=0)。**弱证据(概念板块成分,K2 成分洞)**,见模块头对照表 ★。"""
    boards = member_map.get(code, [])
    return max((hot[b].board_age for b in boards if b in hot), default=0)


def _evaluate_hits(
    row: Optional[Dict[str, Any]], persist_days: int, evidence: Dict[str, str]
) -> List[HoldingK4Hit]:
    """一只持仓的 K4 命中列表。价量类(A1/A3/A3b/B1/B2/B4)读面板已算的 `_hit_*` 布尔;
    题材类(A2/B3)由 board_age 判(弱证据)。命中项按 `_HIT_META` 附 level/evidence_strength。"""
    hits: List[HoldingK4Hit] = []

    def _emit(code: str) -> None:
        label, level, strength = _HIT_META[code]
        ev = evidence.get(code) or _FALLBACK_EVIDENCE.get(code, "")
        if code == "A3b_belowyear_bigvol":
            ev = _A3B_EVIDENCE  # 非 DB advisory 码,证据源=雷区地图 3-⑤
        hits.append(HoldingK4Hit(code=code, label=label, level=level, evidence=ev, evidence_strength=strength))

    if row is not None:
        # 价量类命中(强价量证据 A1/A3/A3b + 普通 B1/B2/B4)。
        if row.get("_hit_A1"):
            _emit("A1_turnover_gt_10")
        if row.get("_hit_A3"):
            _emit("A3_belowyear_limitup")
        if row.get("_hit_A3b"):
            _emit("A3b_belowyear_bigvol")
        if row.get("_hit_B1"):
            _emit("B1_volume_stacking")
        if row.get("_hit_B2"):
            _emit("B2_dual_golden_cross")
        if row.get("_hit_B4"):
            _emit("B4_chase_strong_red")
    # 题材持续天数(弱证据/参考;A2≥4=强级别但不触发 APNs、B3=2-3 普通)。
    if persist_days >= _A2_PERSIST_MIN:
        _emit("A2_theme_persist_ge_4")
    elif _B3_PERSIST_LO <= persist_days <= _B3_PERSIST_HI:
        _emit("B3_theme_persist_2_3")
    return hits


def build_holding_k4_check(
    trade_date: date,
    rule: Dict[str, Any],
    positions: List[Position],
    *,
    sector_scores: Optional[List[SectorScore]] = None,
    member_map: Optional[Dict[str, List[str]]] = None,
    scenario_position_ids: Optional[Set[int]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[HoldingK4Item]:
    """持仓 K4 每日体检 I/O 入口(角色对应 `watchlist_check.build_watchlist_check`)。对每只
    open 持仓在当日 EOD 面板上重算 K4 advisory 命中 + 算好 D5 收盘净浮盈 + 两档时间退出态。

    `rule` = 现役策略 `brain.get_active().rule`(cfg 读 stop_pct/max_hold_days/max_hold_days_profit
    /time_exit_only_if_unprofitable,单一源)。`positions` = open 持仓列表。`sector_scores`/
    `member_map` = 报告已算好的板块强度/成分(题材持续天数复用,不重建 industry 管线)。
    `scenario_position_ids` = 有非空情景树待对照的 position_id 集合(②-D「挑出来」,勾选仍走
    既有 scenario-outcome 端点)。空持仓 → 空列表(不建面板,省 I/O)。"""
    if not positions:
        return []
    cfg = MomentumConfig(**rule["config"])
    codes = list(dict.fromkeys(p.ts_code for p in positions))
    panel = _build_holding_feature_panel(codes, trade_date, parquet_dir)
    rows_by_code: Dict[str, Dict[str, Any]] = (
        {r["ts_code"]: r for r in panel.to_dicts()} if not panel.is_empty() else {}
    )
    evidence = _load_k4_evidence(db_path)
    hot = sector_hot_lookup(sector_scores or [])
    member_map = member_map or {}
    scenario_ids = scenario_position_ids or set()
    names = _resolve_names(codes, db_path)

    out: List[HoldingK4Item] = []
    for p in positions:
        row = rows_by_code.get(p.ts_code)
        buy = _parse_buy_date(p.buy_date)
        d = d_count(buy, trade_date) if buy is not None else 0
        has_data = row is not None
        close = float(row.get("close") or 0.0) if row else 0.0
        # D5 收盘净浮盈(扣双边费,buy_fees 实录 + 估算卖出费);无 EOD 价 → None(保守判非浮盈)。
        net_float = (
            estimate_net_float(close, p.qty, p.buy_price, buy_fees=p.buy_fees)
            if close > 0 else None
        )
        state, eff = classify_time_exit(d, cfg, net_float)
        persist_days = _theme_persist_days(p.ts_code, member_map, hot)
        hits = _evaluate_hits(row, persist_days, evidence)
        has_strong = any(h.level == "strong" and h.evidence_strength == "price_volume" for h in hits)
        out.append(HoldingK4Item(
            position_id=p.id, ts_code=p.ts_code, name=names.get(p.ts_code, p.ts_code),
            has_data=has_data, d_count=d, close=close, net_float=net_float,
            time_exit_state=state, max_hold_effective=eff,
            hits=hits, has_strong=has_strong, scenario_review=(p.id in scenario_ids),
        ))
    return out


def _parse_buy_date(buy_date: str) -> Optional[date]:
    from datetime import datetime
    try:
        return datetime.strptime(buy_date, "%Y%m%d").date()
    except (ValueError, TypeError):
        return None


def _resolve_names(codes: List[str], db_path: Optional[Path]) -> Dict[str, str]:
    """从 stock_basic 补名(展示用);查不到 → 调用方回 code。复用候选评分同一取名口径。"""
    from neckline.report.candidates import _load_stock_names
    try:
        return _load_stock_names(codes, db_path)
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "HoldingK4Hit",
    "HoldingK4Item",
    "build_holding_k4_check",
    "load_k4_sections",
]
