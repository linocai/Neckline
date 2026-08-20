"""持仓 K4 每日体检(plan §五 v1.3-②,需求 7)。K4 红黄牌此前只用于**买前安检**;
用户定案「持仓不是冷冻的」——牌须每日对**持仓票**重算并派发警示。本模块 = 16:35 EOD
报告管线里对每只 open 持仓在当日面板上重算 K4 advisory 命中(同「自选体检
`watchlist_check.py`」姿势,只把输入域换成持仓),并算好 **D5 收盘净浮盈** + **两档时间
退出判向的唯一定格点**(审计 🔴-1,见下)。

**判据源:DB `strategy_versions` 的 K4 行 `rule_json["k4_advisory"]`(is_active=0,只读)。**
⚠ advisory 里的 `expr` 是**人读字符串/规格档**(如 `"turnover_rate > 10"`、`"行业强度
(top20%中位数)连续≥4天成员"`),**不是可执行代码**——本模块写**可执行 polars 镜像**
逐条对齐 advisory 语义。**镜像与 advisory 文字的对应关系(逐条)**:

    advisory 码                     | advisory expr(规格档,DB)                          | 本模块 polars 镜像(可执行)
    -------------------------------|---------------------------------------------------|------------------------------------------------
    A1_turnover_gt_10              | turnover_rate > 10                                | pl.col("turnover_rate") > _A1_TURNOVER_HI(=10)
    A2_theme_persist_ge_4          | 行业强度(top20%中位数)连续≥4天成员                | industry_strength.industry_persist_days ≥ _A2_PERSIST_MIN(=4)★
    A3_belowyear_limitup           | TREND_BELOW & is_limit_up                         | _trend_below_expr() & pl.col("is_limit_up")
    A3b_belowyear_bigvol(派发放量) | (雷区地图 3-⑤:年线下 ret1d≥5%×量比≥2=派发)★★     | _trend_below_expr() & _dispatch_bigred_expr()(量比 vol/vol_ma5≥2)
    B1_volume_stacking             | vol_above_ma20_cnt3≥2 & ret_1d≥5% & vol>vol_ma20×1.5 | _big_red_expr() & ~_trend_below_expr()(年线上才算普通堆积)
    B2_dual_golden_cross           | MACD多头(DIF>DEA) & KDJ多头(K>D)=双金叉态          | state4 == "①双金叉态"(_add_macd_kdj 镜像)
    B3_theme_persist_2_3           | ⛔ **已退役(V2-⑯-I)** —— 不再产生任何新命中     | (无判据;仅 `describe_hits` 回显历史快照)
    B4_chase_strong_red            | close>ma20 & ret_1d>5%                             | (close>ma20) & (ret_1d > _B4_UP=5%)

    ★ 题材持续天数镜像口径说明(**v1.4-② 已回归规格档,v1.3-② 的 board_age 代理到此
      作废**):advisory A2/B3 原文是「行业(stock_basic.industry top20% 中位数)连续
      强度日」,对齐 `research/k4p_h6_theme.py`。v1.3-② 上线时曾借用**概念板块**
      `board_age`(`report/sectors.py::_add_board_age`,板块指数连续站上 MA20 的交易日
      数,经 member_map 取持仓票所属热门板块的最大值)当代理——board_age 与
      advisory/H6 审计的「行业」不是同一个量(概念板块多对多、行业一对一),v1.3-②
      模块头当时就登记了这处刻意分叉。v1.4-②(`report/industry_strength.py`)把
      「行业强度」做成唯一源,A2/B3 判据改读 `industry_strength.stock_persist_days`
      (该票 `stock_basic.industry` 当日的持续强度日天数),不再绕道概念板块——理由是
      需求 8 排序键要求「只用审计过方向的量」,H6 审计的正是行业口径,拿 board_age
      代理当排序键输入会让排序键与它自称对齐的审计证据自相矛盾(见 `intel_candidates.py`
      ③ 排序键消费方注释)。**证据强度分级不变**:A2/B3 仍是成分类判据(行业分类是
      静态当前快照,回填偏差同 research 已声明,比价量硬数据弱一档但不是"未经审计"),
      故一律仍标 `constituent`(参考)、**不单独触发强警示 APNs**(§2.4「证伪只用价量
      结构」+ 用户 2026-07-26 拍板,这条判决不受本次换源影响)。
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
      (price_volume)。~~B3 题材2-3天~~ **已于 V2-⑯-I 退役**,见下方 `RETIRED_HIT_CODES`。
    · **第六类 APNs 门槛 `has_strong`** = 命中含「level=strong ∧ evidence_strength=price_volume」。

**net_float(v1.3-① seam)**:D5 收盘净浮盈 = 现价(EOD 面板 close,前复权锚点在 trade_date
故 = 当日原始收盘,见 `features.apply_qfq` docstring)×qty − buy_price×qty − buy_fees(实录,
缺则按默认费率估) − 估算卖出费(`fees.estimate_net_float`,买入费读 `positions.buy_fees`)。
停牌/无 EOD 数据 → None(保守判非浮盈)。**本模块的这一处 EOD close 口径,是全系统唯一
用于两档时间退出判向的净浮盈来源**(审计 🔴-1 前 precall/`GET /positions` 各算各的,已收敛)。

**两档时间退出「D5 判一次定格」(审计 🔴-1,2026-07-27 用户拍板方案 A)**:本模块是**唯一
定格点** —— 首次遇到某持仓 `d_count ≥ max_hold_days` 时用当日 EOD 净浮盈判一次向
(`classify_time_exit`),写死进 `holding_eod_check.time_exit_locked_*` 三列;此后每天(含本
模块自己)一律 `resolve_time_exit` 读定格值,**不再用当日最新净浮盈重判**。理由:①回测验证过
的规则才是能守的规则(引擎 `momentum.py::_time_exit_reason` 就是判一次定格);②堵死「D5 判该
走→用户没走→D6 转浮盈→D7 系统改口豁免」这条违纪被事后合法化的路。D15 硬上限仍按 d_count 判。
持久化 + 三个消费点见 `neckline.report.holding_store` / `sentinel/precall.py` / `api/app.py`。

**系统永不代交易动作**(§3.8):本模块只算命中/警示,不触发任何下单/撤单/改止损。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

import polars as pl

from neckline.data.adjust import apply_qfq
from neckline.fees import estimate_net_float_detail
from neckline.report.industry_strength import IndustryStrength, industry_strength_lookup, stock_persist_days
from neckline.sentinel.positions import Position, d_count
from neckline.sentinel.precall import (
    PROFIT_EXEMPT,
    TIME_EXIT_NEXT_DAY,
    classify_time_exit,
    is_two_tier_time_exit,
    resolve_time_exit,
)
from neckline.data.panel import add_features, merge_daily_basic, merge_limit_features
from neckline.strategy.momentum_config import MomentumConfig

logger = logging.getLogger(__name__)

# —— 阈值命名常量(可执行镜像单一源;镜像 research/k4_assembly.py 判决口径,改阈值同改 DB advisory)——
_A1_TURNOVER_HI = 10.0    # A1:换手 >10%(turnover_rate 单位为百分数,H2)
_A2_PERSIST_MIN = 4       # A2:题材持续 ≥4 天(H6;v1.4-② 起读 industry_strength,不再用 board_age 代理)
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
_B4_UP = 0.05             # B4:追强大红,close>ma20 & ret_1d>5%

# —— 已退役的 advisory 码(V2-⑯-I,2026-08-04;K7 需求 3 / H11 证据)——————————————
# **B3「题材持续 2-3 天」黄牌停用**:H11 证据下 2-3 天(发酵态)在当前 regime 是最优
# 注意力段,原「被认可 = 接盘侧」的判决主要由样本内年份驱动。正向偏好改由**排序侧**
# 承接(K7-pack 的 `industry_stage_score` 五态序),⛔ 不在 advisory 里造第三种牌。
# ⛔ **A2 红牌(≥4 天硬回避)不动** —— 过热态双尾最重,H11 再确认。
#
# **镜像与 DB 讲同一句话**:DB 侧同批把 `strategy_versions` K4 行的
# `k4_advisory.avoid_flag.B3_theme_persist_2_3` 摘除(`scripts/oneoff/retire_k4_b3.py`,
# diff 全文落 `archive/K4_advisory_B3退役_20260804.md`);本模块侧 `_evaluate_hits`
# **不再发射它**。
# ⚠ **`_HIT_META` / `_FALLBACK_EVIDENCE` 里的 B3 条目刻意保留、不删**:`describe_hits()`
# 是**历史快照回显**入口(`report/info_card.py` 拿冻结在老报告里的 `k4_flags` 码列表来
# decorate),删了会让退役之前生成的历史卡片**掉标签**(未知码静默跳过)。保留 = 老快照
# 照常显示,新命中一个也不会产生。⛔ 别把 B3 加回 `_evaluate_hits`。
RETIRED_HIT_CODES = frozenset({"B3_theme_persist_2_3"})

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
    # ⛔ 已退役(V2-⑯-I,见 `RETIRED_HIT_CODES`):**只服务 `describe_hits` 的历史快照回显**,
    #    `_evaluate_hits` 永不发射它。文案逐字保留 —— 老报告里冻的就是这句。
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
    # `has_data=False` = **当日无 EOD 行**(停牌 / 数据缺口)→ v1.4-①-B 起该票**整份体检跳过**
    # (`hits` 恒空)并由本字段对外标 `dataUnavailable`。**空牌 ≠ 体检过了没问题**:「没体检」
    # 与「体检过没命中」必须能分开(§3.8),故这一位要落库、要透到客户端。
    has_data: bool
    d_count: int
    close: float = 0.0
    net_float: Optional[float] = None
    time_exit_state: str = _HOLDING
    # ⚠ **V2.2-⑤:`None` = 章程无时间退出条款**(`v2.2-k8`,`max_hold_days=None`)——
    # 没有"有效硬上限"这回事。⛔ 不拿 5 或 0 顶上(§3.11-E 否决哨兵位的同一种病);
    # 落库侧对应写 `NULL`(见 `report/holding_store.save_holding_eod_checks`)。
    max_hold_effective: Optional[int] = 5
    # —— 两档时间退出「D5 判一次定格」三件(审计 🔴-1,2026-07-27 用户拍板方案 A)——
    # 定格发生在**首次** d_count ≥ max_hold_days 的那一天(用当日 EOD 收盘净浮盈判一次),
    # 此后每天原样带过来(每行自描述判向来源)。单档现役 K1 恒为 None(无定格概念)。
    time_exit_locked_state: Optional[str] = None       # profit_exempt | time_exit_next_day | None
    time_exit_locked_date: Optional[str] = None        # 'YYYYMMDD' 定格发生日
    time_exit_locked_net_float: Optional[float] = None  # 定格所用的当日 EOD 净浮盈
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
    """在持仓面板上加各价量类命中布尔列(题材类 A2/B3 由 `industry_strength.stock_persist_days`
    另算,不在此)。
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


# K4 安检「DB 里查不到该码归属」时的保守缺省档:当**黄牌**(标一下),不当红牌
# (硬剔)—— 不在 DB 之外自造硬剔判据。⚠ **V2-⑬-1 前住在已删除的
# `report/intel_candidates.py`**,随该模块删除搬到这里(信息卡 `info_card.py` 是
# 现存的唯一消费方,而它本来就 import 本模块的 `describe_hits`)。
# `selection/member_hygiene.py::_K4_DEFAULT_SECTION` 是 V2 选股侧的同值副本
# (两条链路各自独立演进,**刻意不互相 import**,见该处注释)。
K4_DEFAULT_SECTION = "avoid_flag"


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


def load_k4_intel_order(db_path: Optional[Path] = None) -> List[str]:
    """读 DB `strategy_versions` K4 行 `k4_advisory.intel_order` 声明的**展示优先级**,
    归一成 advisory 码前缀序列(v1.4 review 契约线 🟡-1:该节此前零消费方)。

    真实 DB 里这一节写的是**人读短标签**(`["B2双金叉","A1换手","B4追强","B3题材23",
    "B1堆积","A3年线下涨停","A2题材≥4天"]`),不是 advisory 码 —— 故取每条的**前导码
    前缀**(`B2` / `A1` / …)当键,与 `A1_turnover_gt_10` 这类码的 `_` 前一段精确相等
    才算命中。**必须精确相等,不能 `startswith`**:合成码 `A3b_belowyear_bigvol` 的前缀
    是 `A3b`,若用 `startswith("A3")` 它会悄悄占走 A3 的名次。

    节缺失 / K4 行缺失 / 结构异常 / 条目不是字符串 → **空列表**,调用方据此**原样保留
    发射序**(= v1.4 之前的现行行为)。**这是展示序,不参与任何判定**(拦截看
    `load_k4_sections`、黄牌数看严格 `avoid_flag` 计数,两者与顺序无关)。"""
    import re

    from neckline.strategy import brain

    try:
        v = brain.get_version("K4", db_path=db_path)
    except Exception:  # noqa: BLE001  隔离库读失败不崩
        return []
    if v is None:
        return []
    raw = ((v.rule or {}).get("k4_advisory") or {}).get("intel_order")
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        m = re.match(r"^([A-Za-z]+\d+[a-z]?)", entry.strip())
        if m and m.group(1) not in out:
            out.append(m.group(1))
    return out


def order_codes_for_display(
    codes: Sequence[str], intel_order: Optional[Sequence[str]] = None,
    *, db_path: Optional[Path] = None,
) -> List[str]:
    """把 K4 命中码按 DB `intel_order` 声明的优先级排**展示序**(契约线 🟡-1)。

    规则(定死,稳定确定性):
      · 在 `intel_order` 里声明过的码按声明次序在前;
      · **没声明过的码**(如不在 DB 的合成码 `A3b_belowyear_bigvol`)排在其后,
        并**保持传入的原次序**(= `_evaluate_hits` 的发射序)—— Python 稳定排序保证;
      · `intel_order` 为空(节缺失/老库/隔离测试库)→ **原样返回**,逐位等于现行行为。

    ⚠ **纯展示**:调用方拿它排 `k4_flags` / `k4Advisory` / 信息卡红黄牌的**呈现顺序**,
    判定(拦不拦、黄牌几张)一概不看顺序。`intel_order` 未显式传时才回库读一次。"""
    order = list(intel_order) if intel_order is not None else load_k4_intel_order(db_path)
    if not order:
        return list(codes)
    return sorted(codes, key=lambda c: _display_rank(c, order))


def _display_rank(code: str, order: Sequence[str]) -> int:
    token = code.split("_", 1)[0]
    return order.index(token) if token in order else len(order)


def sort_hits_for_display(
    hits: Sequence[HoldingK4Hit], intel_order: Optional[Sequence[str]] = None,
    *, db_path: Optional[Path] = None,
) -> List[HoldingK4Hit]:
    """`order_codes_for_display` 的 `HoldingK4Hit` 版(同一把排序键,不另写一份规则)。"""
    order = list(intel_order) if intel_order is not None else load_k4_intel_order(db_path)
    if not order:
        return list(hits)
    return sorted(hits, key=lambda h: _display_rank(h.code, order))


def describe_hits(codes: List[str], db_path: Optional[Path] = None) -> List[HoldingK4Hit]:
    """把**已经算好**的 K4 命中码列表(如 `Candidate.k4_flags`)decorate 成完整
    `HoldingK4Hit`(label/level/evidence/evidence_strength)——供 v1.4-④ 信息卡
    "红黄牌:复用③已算好的 k4_flags + sections 分区 + DB evidence 文字,不重算"
    (plan §五 v1.4-④-A-5)使用。**不重新判定命中**:命中码本身的产生仍是
    `_evaluate_hits`(读当日 EOD 面板 + 题材持续天数)的职责,本函数只是把「已经知道
    命中了哪些码」这件事翻译成人读文案 + 证据强度分级——纯静态元数据查找(`_HIT_META`
    + DB evidence 文字),零 I/O 之外只有一次 `_load_k4_evidence`(读 K4 advisory 行)。
    未知码(不在 `_HIT_META` 里)静默跳过(防御性;理论不应发生——`k4_flags` 只会含
    本模块 `_emit` 产出过的码)。`codes` 为空 → 空列表,**不触发 `_load_k4_evidence`
    的 DB 读取**(无事可做时不必付一次 I/O,调用方无需自己先判空)。"""
    if not codes:
        return []
    evidence = _load_k4_evidence(db_path)
    out: List[HoldingK4Hit] = []
    for code in codes:
        meta = _HIT_META.get(code)
        if meta is None:
            continue
        label, level, strength = meta
        ev = evidence.get(code) or _FALLBACK_EVIDENCE.get(code, "")
        if code == "A3b_belowyear_bigvol":
            ev = _A3B_EVIDENCE  # 非 DB advisory 码,证据源=雷区地图 3-⑤(同 `_evaluate_hits` 口径)
        out.append(HoldingK4Hit(code=code, label=label, level=level, evidence=ev, evidence_strength=strength))
    return out


def _evaluate_hits(
    row: Optional[Dict[str, Any]], persist_days: int, evidence: Dict[str, str]
) -> List[HoldingK4Hit]:
    """一只持仓的 K4 命中列表。价量类(A1/A3/A3b/B1/B2/B4)读面板已算的 `_hit_*` 布尔;
    题材类(A2/B3)由 `persist_days` 判(调用方经 `industry_strength.stock_persist_days`
    算好传入,弱证据/成分类,见模块头对照表 ★)。命中项按 `_HIT_META` 附 level/
    evidence_strength。"""
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
    # 题材持续天数(弱证据/参考;A2≥4=强级别但不触发 APNs)。
    # ⚠ **2-3 天(B3)那一档已于 V2-⑯-I 退役,这里刻意没有 `elif` 分支** —— 发酵态
    # 由排序侧 `industry_stage_score` 正向承接,不再打黄牌。见 `RETIRED_HIT_CODES`。
    if persist_days >= _A2_PERSIST_MIN:
        _emit("A2_theme_persist_ge_4")
    return hits


def _resolve_time_exit_with_lock(
    d: int,
    cfg: MomentumConfig,
    net_float: Optional[float],
    prior_lock: Optional[Dict[str, Any]],
    trade_date: date,
    *,
    data_unavailable: bool = False,
) -> tuple:
    """两档时间退出的 **16:35 权威计算**(唯一定格点,审计 🔴-1 / 用户拍板方案 A)。

    返回 `(state, max_hold_effective, locked_state, locked_date, locked_net_float)`。

    · **单档(现役 K1)**:直接 `classify_time_exit`(不涉净浮盈),定格三件恒 None ——
      与审计前逐位相同(K1 行为回归护栏)。
    · **两档 + 已有定格**(`prior_lock`,库里读回):`resolve_time_exit` 读定格值,定格三件
      **原样带过来**(不重判、不改写;`net_float` 当日仍照算落库,那是审计/展示量)。
    · **两档 + 尚无定格 + d ≥ max_hold_days**:**就是定格时刻** —— 用当日 EOD 收盘净浮盈
      `classify_time_exit` 判一次,把判向 + 判定日 + 判定所用净浮盈写死。
      ⚠ 若因 EOD 管线断跑而错过了 D5、首个 d≥5 的观测落在 D6/D7,则如实按**首个可得 EOD**
      定格(不假装发生在 D5,同 `pending_track` overshoot 的诚实姿势)。
      `HARD_CAP_EXIT`(d 已 ≥ 硬上限却从未定格,异常长尾)不写定格 —— 硬上限本就按 d_count
      无条件判,不需要也不该被定格。
    · **两档 + 尚无定格 + d < max_hold_days**:HOLDING,定格三件仍 None。
    · **v1.4-①-B `data_unavailable`(当日无 EOD 行,§七 P0-2)+ 尚无定格**:判向**挂起**
      (`SUSPENDED_HOLD`),**这一天不定格** —— 停牌当日根本没有收盘价,拿不到判向所需的
      净浮盈,硬判等于凭空定一个「一次性、不可回头」的向。**复牌当日**才用复牌当日 EOD
      正常定格(届时若 `d_count` 已越过 `max_hold_days`,就在复牌当日定格,由 ⑥-C 标注
      「定格于 D{n},晚于 D{5} {k} 天」)。**已有定格则定格值优先**,停牌不撤回既有判向。
      单档(现役 K1 之外的老 config)同样走这条挂起分支——理由相同,`resolve_time_exit`
      内已统一处理。
    """
    if data_unavailable and not prior_lock:
        state, eff = resolve_time_exit(d, cfg, None, data_unavailable=True)
        return state, eff, None, None, None
    if not is_two_tier_time_exit(cfg):
        state, eff = classify_time_exit(d, cfg, net_float)
        return state, eff, None, None, None
    if prior_lock:
        state, eff = resolve_time_exit(d, cfg, prior_lock.get("state"))
        return (state, eff, prior_lock.get("state"),
                prior_lock.get("date"), prior_lock.get("net_float"))
    state, eff = classify_time_exit(d, cfg, net_float)
    if state in (PROFIT_EXEMPT, TIME_EXIT_NEXT_DAY):
        return state, eff, state, trade_date.strftime("%Y%m%d"), net_float
    return state, eff, None, None, None


def build_holding_k4_check(
    trade_date: date,
    rule: Dict[str, Any],
    positions: List[Position],
    *,
    industry_scores: Optional[List[IndustryStrength]] = None,
    industry_map: Optional[Dict[str, str]] = None,
    scenario_position_ids: Optional[Set[int]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[HoldingK4Item]:
    """持仓 K4 每日体检 I/O 入口(角色对应 `watchlist_check.build_watchlist_check`)。对每只
    open 持仓在当日 EOD 面板上重算 K4 advisory 命中 + 算好 D5 收盘净浮盈 + 两档时间退出态。

    `rule` = 现役策略 `brain.get_active().rule`(cfg 读 stop_pct/max_hold_days/max_hold_days_profit
    /time_exit_only_if_unprofitable,单一源)。`positions` = open 持仓列表。`industry_scores`/
    `industry_map` = 报告已算好的行业强度(`industry_strength.compute_industry_strength`)/
    `stock_basic.industry` 映射(v1.4-② 起题材持续天数唯一源,均为 None 时视作"该票不参与
    排名"= persist 恒 0,不在本函数内部自算——由调用方〔pipeline.py〕算好一次传入,同 v1.3-②
    `sector_scores`/`member_map` 的既有姿势,避免持仓/候选/问询三处各自重算一遍全市场行业中位数)。
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
    intel_order = load_k4_intel_order(db_path)      # 展示序,整批读一次(同 evidence 姿势)
    industry_hot = industry_strength_lookup(industry_scores or [])
    industry_of = industry_map or {}
    scenario_ids = scenario_position_ids or set()
    names = _resolve_names(codes, db_path)
    # 已定格判向(审计 🔴-1):有则本次只带过来、绝不重判(读侧单一通道 holding_store)。
    from neckline.report.holding_store import locked_time_exit_map
    prior_locks = locked_time_exit_map(db_path=db_path)

    out: List[HoldingK4Item] = []
    for p in positions:
        row = rows_by_code.get(p.ts_code)
        buy = _parse_buy_date(p.buy_date)
        d = d_count(buy, trade_date) if buy is not None else 0
        has_data = row is not None
        close = float(row.get("close") or 0.0) if row else 0.0
        # D5 收盘净浮盈(扣双边费,buy_fees 实录/缺则按默认费率估 + 估算卖出费);无 EOD 价 →
        # None(保守判非浮盈)。**这是全系统唯一用于时间退出判向的净浮盈口径(EOD close)**。
        net_float, buy_fee_estimated = (
            estimate_net_float_detail(close, p.qty, p.buy_price, buy_fees=p.buy_fees)
            if close > 0 else (None, False)
        )
        state, eff, lock_state, lock_date, lock_nf = _resolve_time_exit_with_lock(
            d, cfg, net_float, prior_locks.get(p.id), trade_date,
            data_unavailable=not has_data,
        )
        # 审计 🔵-7:定格判向是**一次性、不可回头**的决定,若它建立在「买入费用估的」净浮盈上
        # 必须留痕(日志显式标注),否则事后无从知道这单的判向掺了多少估算成分。
        if buy_fee_estimated and lock_date == trade_date.strftime("%Y%m%d"):
            logger.warning(
                "持仓 #%s %s 于 D%s 定格时间退出判向=%s,但 buy_fees 未补录、买入费按默认费率"
                "估算(净浮盈 %.2f 含估算成分)——如判向贴近盈亏平衡线,请补录实付买入费后复核。",
                p.id, p.ts_code, d, lock_state, net_float if net_float is not None else float("nan"),
            )
        persist_days = stock_persist_days(p.ts_code, industry_of, industry_hot)
        # v1.4-①-B:当日无 EOD 行 → **整份体检跳过**(连题材类 A2/B3 也不判),由
        # `has_data=False` 对外标 `dataUnavailable`。**不静默产出空牌** —— 空牌的语义是
        # 「体检过了没问题」,与「今天压根没体检」必须能分开(§3.8)。
        # 展示序按 DB `intel_order`(v1.4 review 契约线 🟡-1);**判定不看顺序**:下一行的
        # `has_strong` 是 any(...)、拦截/计数在候选侧走集合,排序前后逐位相同。
        hits = sort_hits_for_display(
            _evaluate_hits(row, persist_days, evidence), intel_order) if has_data else []
        has_strong = any(h.level == "strong" and h.evidence_strength == "price_volume" for h in hits)
        out.append(HoldingK4Item(
            position_id=p.id, ts_code=p.ts_code, name=names.get(p.ts_code, p.ts_code),
            has_data=has_data, d_count=d, close=close, net_float=net_float,
            time_exit_state=state, max_hold_effective=eff,
            time_exit_locked_state=lock_state, time_exit_locked_date=lock_date,
            time_exit_locked_net_float=lock_nf,
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
    """从 stock_basic 补名(展示用);查不到 → 调用方回 code。走「按代码查中文名」唯一实现。"""
    from neckline.data.market_data import resolve_stock_names
    try:
        return resolve_stock_names(codes, db_path)
    except Exception:  # noqa: BLE001
        return {}


__all__ = [
    "RETIRED_HIT_CODES",
    "HoldingK4Hit",
    "HoldingK4Item",
    "build_holding_k4_check",
    "load_k4_sections",
    "load_k4_intel_order",
    "order_codes_for_display",
    "sort_hits_for_display",
    "describe_hits",
]
