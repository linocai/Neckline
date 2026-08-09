"""落地起跳位置关:全市场逐票四态判定唯一实现(plan §五 V2.2-③-C,K8 §二)。

每个交易日盘后对**当日有 `daily` 行的每只票**判定
**下落 / 落地待确认 / 支撑确认起跳 / 高位加速** 四态之一(判据缺数落 `none`),
落 `landing_state_daily`(EOD 预计算落表、在线只读,P0-23;§七 P4-50 登记的本项目
第二条「全市场逐票 × 多年回看」批算路径)。本模块只做「算」:纯判定
`decide_landing()` + 全市场批量特征装配 `compute_landing_states()`;落表/读表在
`landing_store.py`。**本模块零写库**(全部 SQL 只读)。

🔴 **雷区对照(plan §五 ③-C 原文一字不省;防后人当新发现,也防后人当禁令删掉;
只作"知情登记"、不作"否决依据"——§七 P3-49 用户裁定 #10)**:

- 判据 1+2+4 的形状 = **K3-B2 臂③「升势回撤 + 启动确认」**,那一臂被判「**确认信号 = 死猫跳顶点,比直接买更差**」(`research/k3_report.md`)。
- 判据 1 单独用的形状 = **K3 系统化超跌反弹四臂全灭**。
- 判据 5 的排除项 = **K2「追强势」全否决 + K7-C1 诱多做局**的正面兑现(这一半是**站在案底同侧**的)。
- **本关因此只产注意力分层,⛔ 不得被读成买入期望背书**(§3.8-(b) 一字不变)。它的真伪由 K8 自己的选股时钟在**前向样本**上回答(K8 §十七),**不由本版声称**。义务已挂 §七 **P3-49**。

**五项判据(唯一定义 = `decide_landing()`;阈值住骨架包 `config.landing`,全部
`engineering_v1`,plan §五 ③-C 表逐条)**:

    1 下跌或调整已经结束   近 N_LOW=5 日最低价 > 前 N_BACK=20 日区间最低价 ×(1+LOW_TOL=0.01)
                          且 当日未创 20 日新低
    2 关键位置形成有效支撑 close ≥ max(MA20, 平台下沿) × (1 − SUP_TOL=0.01);
                          平台下沿 = 近 PLATFORM_WIN=20 日收盘的 20% 分位
    3 抛压明显衰减        近 5 日下跌日均成交额 ÷ 近 20 日均成交额 ≤ SELL_DECAY=0.90,
                          且 近 5 日最大单日跌幅 ≥ −PANIC_DROP=−0.05
    4 价格开始向上转强    close > MA5 且 close > 昨收 且 RS5 > 0(相对所属行业中位 5 日超额)
    5 当前仍处于启动早期   dist_from_high_60d ≤ −HIGH_GAP=−0.03 且 近 LIFT_WIN=3 日累计涨幅
                          ≤ LIFT_MAX=0.12 且 当日非涨停 且 未创 60 日新高

**四态映射(K8 §二 原文逐条;互斥、优先级从上往下,`state` 恒非 NULL——照
`scan/stage.py::decide_stage` / `scan/regime.py::decide_regime` 既有体例)**:

    falling            判据 1 不成立                → 排除(「下落阶段:排除」)
    landing_pending    1+2 成立,4 不成立            → T2 候选(「落地待确认」)
    liftoff_confirmed  1+2+3+4+5 全成立             → T1 候选(「支撑确认并开始起跳」)
    high_extended      1+2+3+4 成立但 5 不成立       → 低优先级:可进 T2 尾部,⛔ 不进 T1
    none               判据缺数(state_reason 逐条)  → 不拦,但 ⛔ 不给 T1

**映射补全两处(plan 四态表是偏函数,两个组合原文没写;本实现按「最不激进」补全,
登记于此,⛔ 不是静默选边,单测真值表锁死)**:
  - **判据 1 成立、判据 2 不成立** → `falling`:未获支撑 = 未落地,「下跌已结束」这个
    结论在无支撑时不可靠,归排除桶(注意力分层里最保守的一档)。
  - **判据 1+2+4 成立、判据 3 不成立** → `landing_pending`:已落地、价格转强但抛压
    未衰减 = 起跳**未确认**,按「落地待确认」处理(映射表对 landing_pending 本就
    不问判据 3,此处只是把「4 成立但 3 不成立」也归回待确认,不发明第五态)。

**缺数不猜(§3.8)**:任何一项判据的输入为 `None` = 该项 na;级联判定只要求**走到
那一步所必需**的判据可判(如判据 1 已 fail,后四项缺数不影响 `falling`),必需项
na → `none` + `state_reason` 逐条说明。判据 3 的特例:近 5 日**无下跌日**(
`down_days_5d=0`)→ 抛压衰减子门按成立读(没有抛压可言),⛔ 与「数据缺失算不出」
(na)分开——两者在原因码里分别是 `no_down_days:ok` 与 `na`。

**口径细则(登记在案,⛔ 别当 bug 修)**:
  - **价格窗口一律前复权**(公式唯一源 `data/adjust.qfq_expr`,基准因子取该票
    取数区间内最新一条 —— 与 `apply_qfq`/`basket_daily.py` 既有语义逐字一致,只是
    在惰性管线里内联):MA/新高新低/平台分位跨除权日必须可比。
    **metrics 只存比值/收益率等缩放不变量**
    (qfq 的基准因子随取数区间尾端变化,存绝对价位会让 bulk 与 day-by-day 两路
    合法地不同——比值对基准因子不变,三路等价因此成立)。
  - **收益率用原始 `close/pre_close − 1`**(`report/industry_strength.py` 既有论证:
    同行两列同标量缩放,比值精确抵消)。
  - **窗口 = 该票自己的交易行窗口**(停牌缺行不特殊处理,「近 5 日」= 该票最近
    5 个成交日;长停牌复牌初期各窗口跨停牌期,判出保守方向)。唯一例外是 **RS5**:
    行业中位 5 日和锚在**市场交易日**上(`industry_strength_daily` 逐日行),故该票
    必须在同一段 5 个市场交易日上连续有行,否则 RS5 = na(两个不同步的窗口相减
    没有意义)。
  - **阈值比较一律带 `_EPS=1e-9` 容差**(`sentinel/holding.py` 体例,恰好等于阈值
    按满足读——regime.py 同款登记取舍)。
  - **`platform_days`(近 N 日振幅 ≤ X 的连续天数)算在 `metrics_json` 里,⛔ 不另起
    一张表**(plan §五 ③-F 原文)。振幅 = 滚动 `PLATFORM_AMP_WIN` 日
    (max_high − min_low) / min_low;连续天数在 `PLATFORM_DAYS_CAP` 处右截尾
    (饱和值仍满足 Y1 首版 `platform_days ≥ 40` 的判读,docstring 登记)。
  - 行业归属用 `stock_basic.industry` 当前快照(`industry_strength` 同款既有取舍)。

**性能纪律(P0-23 / P4-50 正面靶心)**:取数一律走 `scan_table_range`(内部
`_scan_table(years=…)` 年分区裁剪,P1-26 既有修法,⛔ 不全 glob);回看窗口 =
`_lookback_bars()` 个**交易日**(由阈值推出,默认 145);增量日更只算当日一行
×全市场,batch 回填由 `landing_store.refresh_landing_states` 分块复用同一实现。
⚠ 本地实测数字不是生产结论(CLAUDE.md 铁律)——上生产前必须按 §七 P4-50
`systemd-run --scope` 隔离实测计时 + 量峰值。

**反向守门(plan §五 ③ 测试与守门原文)**:本模块零 import `neckline.sentinel.*`
与 `neckline.report.score_display`(位置态 ⛔ 不接任何持仓动作、不进任何推送、
不碰展示标度;守门单测扫源码锁死)。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.data.adjust import qfq_expr
# `_scan_table(years=…)` 年分区裁剪是 plan §五 ③-C 性能条款点名的取数路径(P1-26);
# 直接用惰性入口而不是 `scan_table_range`,是为了整条特征管线**惰性到底、单次
# collect 只物化判定日的行**——列投影/日期过滤全部下推。全市场单日 refresh 本地
# 实测(2026-07-24,5526 票 × 145 交易日回看):eager 版 ~716MB 峰值 / 1.2s →
# 惰性版 ~644MB@默认线程、~490MB@POLARS_MAX_THREADS=2 / 0.4s(峰值随 polars 线程
# 数走;`industry_strength._load_ret1d_panel` 的投影下推同款姿势,推到整条管线)。
# ⚠ 本地数不是生产结论:上生产前按 §七 P4-50 在生产机 systemd-run --scope 隔离实测。
from neckline.data.market_data import _scan_table, _years_in_range
from neckline.db import connection, init_schema
from neckline.report import industry_strength_store as strength_store
# ⚠ 对 selection.pack 只许 import 读入口(regime.py 同款权限锁姿势)。
from neckline.selection.pack import Pack, get_active_skeleton
# 复用 regime.py 的「engine_default」哨兵串(同一条纪律的同一个字面,不抄第二份)
# 与 `_ret_5d_sum`(「5 日中位收益和必须凑满窗口」的唯一实现,同包私有复用,登记)。
from neckline.scan.regime import SKELETON_VERSION_FALLBACK, _ret_5d_sum

logger = logging.getLogger(__name__)

TABLE = "landing_state_daily"

# —— 四态 + 缺数英文码(唯一源;库列值与展示映射同源,照 `stage.py` STAGE_LABELS
# 先例)。⚠ 中文标签是**注意力分层**用语,⛔ 不许出现「买入」「期望」「胜率」字眼
# (§七 P3-49-(b):位置关产出不得出现在任何声称期望/胜率/会涨的文案里)。————————
FALLING = "falling"
LANDING_PENDING = "landing_pending"
LIFTOFF_CONFIRMED = "liftoff_confirmed"
HIGH_EXTENDED = "high_extended"
NONE_STATE = "none"

STATE_ORDER: Tuple[str, ...] = (
    FALLING, LANDING_PENDING, LIFTOFF_CONFIRMED, HIGH_EXTENDED, NONE_STATE,
)

STATE_LABELS: Dict[str, str] = {
    FALLING: "下落",
    LANDING_PENDING: "落地待确认",
    LIFTOFF_CONFIRMED: "支撑确认起跳",
    HIGH_EXTENDED: "高位加速",
    NONE_STATE: "判据缺数",
}

# —— 十二个判定阈值的引擎默认(plan §五 ③-C 表原文数值)。权威住骨架包
# `config.landing`(每键 {value, provenance},全部 engineering_v1);这里只是
# **无包/缺键时的回退值**(照 `regime.REGIME_THRESHOLD_DEFAULTS` 既有分工)。
# 键名白名单与 `selection/pack.py::_LANDING_THRESHOLD_KEYS` 双向对拍(守门单测
# 锁相等,防漂)。窗口类键(n_low/n_back/platform_win/lift_win/platform_amp_win)
# 在 plan ③-C 表里就是带值的具名阈值(N_LOW=5 等),故进包,⚠ 与 regime.py
# 「窗口是引擎常量不进包」的分工刻意不同——那边窗口没在判据表里具名,这边有。——
LANDING_THRESHOLD_DEFAULTS: Dict[str, float] = {
    "n_low": 5,              # 判据1:近端最低价窗口(交易日)
    "n_back": 20,            # 判据1:前区间最低价窗口(交易日;也是「未创 20 日新低」的 20)
    "low_tol": 0.01,         # 判据1:近端低点须高出前区间低点的比例下限
    "sup_tol": 0.01,         # 判据2:支撑位下方容忍比例
    "platform_win": 20,      # 判据2:平台下沿分位窗口(交易日)
    "sell_decay": 0.90,      # 判据3:下跌日均成交额 ÷ 20 日均成交额 的上限
    "panic_drop": 0.05,      # 判据3:近 5 日单日最大跌幅的排除线(跌幅 ≥ −panic_drop 才算衰减)
    "high_gap": 0.03,        # 判据5:距 60 日高点至少回撤的比例
    "lift_win": 3,           # 判据5:近端累计涨幅窗口(交易日)
    "lift_max": 0.12,        # 判据5:近端累计涨幅上限
    "platform_amp_win": 20,  # platform_days:滚动振幅窗口(交易日)
    "platform_amp_max": 0.25,  # platform_days:振幅上限 X(Y1 平台判读同源量)
}

# —— 窗口/定义类引擎常量(事实口径,不是策略参数——照 `stage.py` LOOKBACK 常量
# 的既有分工;要改口径改这里并重算历史,⛔ 不进包)————————————————————————————
MA5_WINDOW = 5                  # 判据4 的 MA5(字面即 5 日均线)
MA20_WINDOW = 20                # 判据2 的 MA20(字面即 20 日均线)
HIGH_WINDOW_DAYS = 60           # 判据5 的 60 日高点窗口(字面即 60 日)
SELL_SHORT_WINDOW = 5           # 判据3 的「近 5 日」(字面)
SELL_LONG_WINDOW = 20           # 判据3 的「近 20 日」(字面)
RS_WINDOW_DAYS = 5              # 判据4 的 RS5 窗口(字面;regime RET_WINDOW_DAYS 同口径)
PLATFORM_QUANTILE = 0.20        # 判据2 平台下沿 =「收盘的 20% 分位」(定义的一部分)
PLATFORM_DAYS_CAP = 120         # platform_days 右截尾上限(内存上界的一部分,见模块头)
_LOOKBACK_MARGIN = 5            # 取数窗口安全余量(交易日)

# 阈值比较容差(`sentinel/holding.py` 体例)。
_EPS = 1e-9


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


# —————————————————————————————————————————————————————————————————————————————
# 阈值解析(骨架包 → 引擎默认逐键回退;照 regime.resolve_regime_thresholds 体例)
# —————————————————————————————————————————————————————————————————————————————

def resolve_landing_thresholds(
    pack: Optional[Pack],
) -> Tuple[Dict[str, float], str, Tuple[str, ...]]:
    """从骨架包 `config.landing` 解出十二个判定阈值(逐键独立回退引擎默认 +
    WARNING 不静默)。返回 `(阈值字典, skeleton_version, 附加原因码元组)`:

    - 无骨架线现役 → 全默认 + `'engine_default'` 哨兵串 + `('missing:skeleton_pack',)`;
      ⛔ 不写 NULL、不伪造版本号(regime.py 同款既有裁定)。
    - 有包但缺键/叶子形状不对(历史行才可能;新包激活时 `pack.py::_validate_landing`
      已挡)→ 该键回退默认 + WARNING,`skeleton_version` 仍是包版本。"""
    if pack is None:
        logger.warning(
            "[landing] 无现役骨架线(selection_packs 无 line_code='V' 现役行)——十二个"
            "判定阈值全部回退引擎默认常量,skeleton_version 记 %r(state_reason 记 "
            "missing:skeleton_pack)。", SKELETON_VERSION_FALLBACK,
        )
        return dict(LANDING_THRESHOLD_DEFAULTS), SKELETON_VERSION_FALLBACK, ("missing:skeleton_pack",)
    raw = pack.landing_config()
    out: Dict[str, float] = {}
    fell_back: List[str] = []
    for key, default in LANDING_THRESHOLD_DEFAULTS.items():
        leaf = raw.get(key)
        value = leaf.get("value") if isinstance(leaf, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
        else:
            out[key] = float(default)
            fell_back.append(key)
    if fell_back:
        logger.warning(
            "[landing] 骨架包 %s 的 config.landing 缺键或叶子形状不对:%s —— 逐键回退"
            "引擎默认(照 regime.resolve_regime_thresholds 体例,不静默)。",
            pack.pack_version, fell_back,
        )
    return out, pack.pack_version, ()


def _lookback_bars(thresholds: Mapping[str, float]) -> int:
    """取数回看窗口(交易日):五项判据所需的最长窗口 与 platform_days 可测跨度
    (截尾上限 + 振幅窗口)取大,加安全余量。bulk 与 day-by-day 三路等价依赖
    「截尾上限 ≤ 两种取数方式都可测的跨度」,⛔ 别把 PLATFORM_DAYS_CAP 抬到
    比这里算出的窗口还大。"""
    need = max(
        int(thresholds["n_low"]) + int(thresholds["n_back"]) + 1,   # 判据1(shift 后再看一格)
        HIGH_WINDOW_DAYS + 1,                                       # 判据5(新高看前 60 日)
        int(thresholds["platform_win"]),
        MA20_WINDOW,
        SELL_LONG_WINDOW,
        PLATFORM_DAYS_CAP + int(thresholds["platform_amp_win"]),
    )
    return need + _LOOKBACK_MARGIN


# —————————————————————————————————————————————————————————————————————————————
# 四态判据(唯一定义,纯函数)
# —————————————————————————————————————————————————————————————————————————————

def _fmt(x: float) -> str:
    return f"{x:.4f}"


def _and3(gates: Sequence[Optional[bool]]) -> Optional[bool]:
    """三值 AND(Kleene):有 False 即 False;无 False 有 None 即 None;全 True 即 True。
    「缺数不猜」的机械化:能凭已有数据判 fail 的就判 fail,判不动的才是 na。"""
    if any(g is False for g in gates):
        return False
    if any(g is None for g in gates):
        return None
    return True


def decide_landing(
    *,
    low5_over_base: Optional[float],
    new_low_20d: Optional[bool],
    close_over_support: Optional[float],
    sell_ratio: Optional[float],
    down_days_5d: Optional[int],
    max_drop_5d: Optional[float],
    close_over_ma5: Optional[float],
    ret_1d: Optional[float],
    rs5: Optional[float],
    dist_from_high_60d: Optional[float],
    lift_ret: Optional[float],
    is_limit_up: Optional[bool],
    new_high_60d: Optional[bool],
    thresholds: Optional[Mapping[str, float]] = None,
    extra_reason_tokens: Sequence[str] = (),
) -> Tuple[str, str]:
    """四态判据唯一实现(互斥、优先级从上往下,`state` 恒非 NULL)。返回
    `(state, state_reason)`。

    输入全部是**缩放不变量**(比值/收益率/布尔;理由见模块头「口径细则」):
      - `low5_over_base` = 近 n_low 日最低 ÷ 前 n_back 日区间最低(判据1);
      - `close_over_support` = close ÷ max(MA20, 平台下沿)(判据2);
      - `sell_ratio` = 近 5 日下跌日均成交额 ÷ 近 20 日均成交额;`down_days_5d`=0 时
        `sell_ratio` 合法为 None 且子门按成立读(no_down_days,模块头登记);
      - `close_over_ma5` / `ret_1d`(= close/昨收 − 1)/ `rs5`(判据4 三子门);
      - `dist_from_high_60d`(≤0,距含当日的 60 日最高的比例)/ `lift_ret`
        (近 lift_win 日累计涨幅)/ `is_limit_up` / `new_high_60d`(判据5)。

    级联(优先级与两处补全的登记见模块头「四态映射」):
        c1 fail→falling · c1 na→none · c2 fail→falling(补全①) · c2 na→none ·
        c4 fail→landing_pending · c4 na→none · c3 fail→landing_pending(补全②) ·
        c3 na→none · c5 ok→liftoff_confirmed · c5 fail→high_extended · c5 na→none

    `state_reason` = 先五项判据 rollup(`c1:ok;…;c5:na`)再逐子门明细,分号连接,
    尾挂 `extra_reason_tokens`(如 `missing:skeleton_pack`)。全部比较带 `_EPS`
    容差(恰好等于阈值按满足读,登记取舍)。"""
    th = dict(LANDING_THRESHOLD_DEFAULTS)
    if thresholds:
        th.update({k: float(v) for k, v in thresholds.items() if k in LANDING_THRESHOLD_DEFAULTS})
    detail: List[str] = []

    # —— 判据 1:下跌或调整已经结束 ————————————————————————————————————————
    if low5_over_base is None:
        g_range: Optional[bool] = None
        detail.append("c1.low5_over_base=na")
    else:
        g_range = low5_over_base > 1.0 + th["low_tol"] - _EPS
        detail.append(
            f"c1.low5_over_base={_fmt(low5_over_base)}>{_fmt(1.0 + th['low_tol'])}:"
            f"{'ok' if g_range else 'fail'}"
        )
    if new_low_20d is None:
        g_nolow: Optional[bool] = None
        detail.append("c1.new_low_20d=na")
    else:
        g_nolow = not new_low_20d
        detail.append(f"c1.new_low_20d={'yes:fail' if new_low_20d else 'no:ok'}")
    c1 = _and3((g_range, g_nolow))

    # —— 判据 2:关键位置形成有效支撑 ————————————————————————————————————————
    if close_over_support is None:
        c2: Optional[bool] = None
        detail.append("c2.close_over_support=na")
    else:
        c2 = close_over_support >= 1.0 - th["sup_tol"] - _EPS
        detail.append(
            f"c2.close_over_support={_fmt(close_over_support)}>={_fmt(1.0 - th['sup_tol'])}:"
            f"{'ok' if c2 else 'fail'}"
        )

    # —— 判据 3:抛压明显衰减 ————————————————————————————————————————————————
    if down_days_5d is not None and down_days_5d == 0:
        g_sell: Optional[bool] = True     # 无下跌日 = 无抛压可言(模块头登记的特例)
        detail.append("c3.sell_ratio=no_down_days:ok")
    elif sell_ratio is None:
        g_sell = None
        detail.append("c3.sell_ratio=na")
    else:
        g_sell = sell_ratio <= th["sell_decay"] + _EPS
        detail.append(
            f"c3.sell_ratio={_fmt(sell_ratio)}<={_fmt(th['sell_decay'])}:"
            f"{'ok' if g_sell else 'fail'}"
        )
    if max_drop_5d is None:
        g_panic: Optional[bool] = None
        detail.append("c3.max_drop_5d=na")
    else:
        g_panic = max_drop_5d >= -th["panic_drop"] - _EPS
        detail.append(
            f"c3.max_drop_5d={_fmt(max_drop_5d)}>={_fmt(-th['panic_drop'])}:"
            f"{'ok' if g_panic else 'fail'}"
        )
    c3 = _and3((g_sell, g_panic))

    # —— 判据 4:价格开始向上转强 ————————————————————————————————————————————
    if close_over_ma5 is None:
        g_ma5: Optional[bool] = None
        detail.append("c4.close_over_ma5=na")
    else:
        g_ma5 = close_over_ma5 > 1.0 - _EPS
        detail.append(f"c4.close_over_ma5={_fmt(close_over_ma5)}>1:{'ok' if g_ma5 else 'fail'}")
    if ret_1d is None:
        g_up: Optional[bool] = None
        detail.append("c4.ret_1d=na")
    else:
        g_up = ret_1d > -_EPS
        detail.append(f"c4.ret_1d={_fmt(ret_1d)}>0:{'ok' if g_up else 'fail'}")
    if rs5 is None:
        g_rs: Optional[bool] = None
        detail.append("c4.rs5=na")
    else:
        g_rs = rs5 > -_EPS
        detail.append(f"c4.rs5={_fmt(rs5)}>0:{'ok' if g_rs else 'fail'}")
    c4 = _and3((g_ma5, g_up, g_rs))

    # —— 判据 5:当前仍处于启动早期 ——————————————————————————————————————————
    if dist_from_high_60d is None:
        g_dist: Optional[bool] = None
        detail.append("c5.dist_high_60d=na")
    else:
        g_dist = dist_from_high_60d <= -th["high_gap"] + _EPS
        detail.append(
            f"c5.dist_high_60d={_fmt(dist_from_high_60d)}<={_fmt(-th['high_gap'])}:"
            f"{'ok' if g_dist else 'fail'}"
        )
    if lift_ret is None:
        g_lift: Optional[bool] = None
        detail.append("c5.lift_ret=na")
    else:
        g_lift = lift_ret <= th["lift_max"] + _EPS
        detail.append(
            f"c5.lift_ret={_fmt(lift_ret)}<={_fmt(th['lift_max'])}:{'ok' if g_lift else 'fail'}"
        )
    if is_limit_up is None:
        g_nolimit: Optional[bool] = None
        detail.append("c5.limit_up=na")
    else:
        g_nolimit = not is_limit_up
        detail.append(f"c5.limit_up={'yes:fail' if is_limit_up else 'no:ok'}")
    if new_high_60d is None:
        g_nohigh: Optional[bool] = None
        detail.append("c5.new_high_60d=na")
    else:
        g_nohigh = not new_high_60d
        detail.append(f"c5.new_high_60d={'yes:fail' if new_high_60d else 'no:ok'}")
    c5 = _and3((g_dist, g_lift, g_nolimit, g_nohigh))

    # —— 级联(互斥、优先级从上往下;两处补全见模块头登记)——————————————————————
    if c1 is False:
        state = FALLING
    elif c1 is None:
        state = NONE_STATE
    elif c2 is False:
        state = FALLING          # 补全①:落地未获支撑 → 归排除桶
    elif c2 is None:
        state = NONE_STATE
    elif c4 is False:
        state = LANDING_PENDING
    elif c4 is None:
        state = NONE_STATE
    elif c3 is False:
        state = LANDING_PENDING  # 补全②:转强但抛压未衰减 = 起跳未确认
    elif c3 is None:
        state = NONE_STATE
    elif c5 is True:
        state = LIFTOFF_CONFIRMED
    elif c5 is False:
        state = HIGH_EXTENDED
    else:
        state = NONE_STATE

    def _s(v: Optional[bool]) -> str:
        return "na" if v is None else ("ok" if v else "fail")

    rollup = f"c1:{_s(c1)};c2:{_s(c2)};c3:{_s(c3)};c4:{_s(c4)};c5:{_s(c5)}"
    reason = ";".join([rollup] + detail + list(extra_reason_tokens))
    return state, reason


# —————————————————————————————————————————————————————————————————————————————
# 全市场批量特征装配(只读;落表在 landing_store.refresh_landing_states)
# —————————————————————————————————————————————————————————————————————————————

def _recent_trading_days_before(d: date, n: int) -> List[date]:
    """`d` 严格之前最近 `n` 个交易日,升序(`stage.py`/`regime.py` 同名体例)。"""
    from neckline.calendar import prev_trading_day

    out: List[date] = []
    cur = d
    for _ in range(n):
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


def _industry_ret_sums(
    day_strs_per_day: Mapping[str, Sequence[str]], db_path: Optional[Path]
) -> Dict[Tuple[str, str], Optional[float]]:
    """每个判定日 × 每个行业的「中位收益 5 日和」(凑满窗口才算,`_ret_5d_sum`
    唯一实现)。`day_strs_per_day` = {判定日: 其 5 个市场交易日窗口(升序)}。
    返回 {(判定日, 行业): 和或 None};`industry_strength_daily` 整段缺失时返回
    空 dict(所有票 RS5 = na,缺数不猜)。"""
    all_days = sorted({d for win in day_strs_per_day.values() for d in win})
    if not all_days:
        return {}
    placeholders = ",".join("?" * len(all_days))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, industry, median_ret FROM {strength_store.TABLE} "
            f"WHERE trade_date IN ({placeholders})",
            all_days,
        ).fetchall()
    ret_by_key: Dict[Tuple[str, str], Optional[float]] = {
        (td, ind): ret for td, ind, ret in rows
    }
    industries = sorted({ind for _td, ind, _r in rows})
    out: Dict[Tuple[str, str], Optional[float]] = {}
    for day_str, window in day_strs_per_day.items():
        for ind in industries:
            out[(day_str, ind)] = _ret_5d_sum(ind, window, ret_by_key)
    return out


def _round6(v: Any) -> Any:
    return round(float(v), 6) if isinstance(v, float) else v


def compute_landing_states(
    days: Sequence[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """`days`(升序去重后)每个交易日的全市场逐票判定(全程只读)。返回行 dict
    列表(键 = `landing_state_daily` 除 `computed_at` 外全部列,`metrics` 为 dict,
    JSON 序列化由 store 落表时做)。

    - **判定域 = 当日有 `daily` 行的票**(停牌票当日无行 = 无判定行,由读侧按
      「缺行 = 不知道」披露,⛔ 不落猜出来的行)。
    - 取数一次覆盖 `[min(days) − lookback, max(days)]`(`scan_table_range` 年分区
      裁剪);逐票 rolling 全部尾窗 + `min_samples=窗口长`(缺一根 bar 即 na,
      缺数不猜)。bulk 与 day-by-day 等价:尾窗只看各票最近 N 根 bar,与取数区间
      起点无关(platform_days 的截尾保证见 `_lookback_bars` docstring)。
    - `daily` 整段缺失 / 判定日无行 → 该日零行(不猜、不凑)。"""
    uniq_days = sorted(set(days))
    if not uniq_days:
        return []
    init_schema(db_path)
    thresholds, skeleton_version, extra_tokens = resolve_landing_thresholds(
        get_active_skeleton(db_path)
    )
    n_low = int(thresholds["n_low"])
    n_back = int(thresholds["n_back"])
    platform_win = int(thresholds["platform_win"])
    lift_win = int(thresholds["lift_win"])
    amp_win = int(thresholds["platform_amp_win"])
    amp_max = float(thresholds["platform_amp_max"])

    lookback = _lookback_bars(thresholds)
    start_day = _recent_trading_days_before(uniq_days[0], lookback)[0]
    end_day = uniq_days[-1]

    years = _years_in_range(start_day, end_day)
    in_range = (pl.col("trade_date") >= start_day) & (pl.col("trade_date") <= end_day)
    daily_lf = _scan_table("daily", parquet_dir, years=years)
    if daily_lf is None:
        logger.warning("[landing] %s ~ %s 无 daily 数据,零行(缺行 = 不知道,不猜)",
                       _d(start_day), _d(end_day))
        return []
    base_lf = daily_lf.filter(in_range).select(
        ["ts_code", "trade_date", "high", "low", "close", "pre_close", "amount"]
    )

    # 市场交易日轴 = daily 数据里的日期全集(单列投影下推的小查询;RS5 连续性检查
    # 与行业 5 日窗口共用同一根轴——bulk 与 day-by-day 两种取数下,任一判定日之前
    # 的轴段完全一致,三路等价依赖这一点)。
    dates_df = base_lf.select("trade_date").unique().collect()
    if dates_df.is_empty():
        logger.warning("[landing] %s ~ %s 无 daily 数据,零行(缺行 = 不知道,不猜)",
                       _d(start_day), _d(end_day))
        return []
    market_days: List[str] = sorted(_d(x) for x in dates_df["trade_date"].to_list())

    # 前复权(公式唯一源 = `data/adjust.qfq_expr`;基准因子 = 该票在取数区间内最新
    # 一条,与 `apply_qfq`「区间内最新」语义逐字一致——sort 后组内 last() 即最新。
    # adj_factor 整表缺失 → 全 null → qfq_expr 优雅退回原始价,同 apply_qfq)。
    adj_lf = _scan_table("adj_factor", parquet_dir, years=years)
    if adj_lf is not None:
        lf = base_lf.join(
            adj_lf.filter(in_range).select(["ts_code", "trade_date", "adj_factor"]),
            on=["ts_code", "trade_date"], how="left",
        )
    else:
        lf = base_lf.with_columns(pl.lit(None, dtype=pl.Float64).alias("adj_factor"))
    lf = lf.sort(["ts_code", "trade_date"])
    lf = lf.with_columns(
        pl.col("adj_factor").last().over("ts_code").alias("_latest_adj_factor")
    ).with_columns(
        qfq_expr("high").alias("high_qfq"),
        qfq_expr("low").alias("low_qfq"),
        qfq_expr("close").alias("close_qfq"),
    )

    ret_1d = (
        pl.when(pl.col("pre_close").is_not_null() & (pl.col("pre_close") != 0))
        .then(pl.col("close") / pl.col("pre_close") - 1.0)
        .otherwise(None)
    )
    down_flag = ret_1d < 0

    lf = lf.with_columns(
        ret_1d.alias("ret_1d"),
        # 判据1
        pl.col("low_qfq").rolling_min(n_low, min_samples=n_low).over("ts_code").alias("_low5"),
        pl.col("low_qfq").rolling_min(n_back, min_samples=n_back).shift(n_low)
        .over("ts_code").alias("_backlow"),
        pl.col("low_qfq").rolling_min(n_back, min_samples=n_back).shift(1)
        .over("ts_code").alias("_prior_low"),
        # 判据2
        pl.col("close_qfq").rolling_mean(MA20_WINDOW, min_samples=MA20_WINDOW)
        .over("ts_code").alias("_ma20"),
        pl.col("close_qfq").rolling_quantile(
            quantile=PLATFORM_QUANTILE, window_size=platform_win,
            min_samples=platform_win, interpolation="linear",
        ).over("ts_code").alias("_plat_low"),
        # 判据3
        pl.when(down_flag).then(pl.col("amount")).otherwise(0.0)
        .rolling_sum(SELL_SHORT_WINDOW, min_samples=SELL_SHORT_WINDOW)
        .over("ts_code").alias("_down_amt5"),
        pl.when(down_flag).then(1).otherwise(0)
        .rolling_sum(SELL_SHORT_WINDOW, min_samples=SELL_SHORT_WINDOW)
        .over("ts_code").alias("_down_cnt5"),
        pl.col("amount").rolling_mean(SELL_LONG_WINDOW, min_samples=SELL_LONG_WINDOW)
        .over("ts_code").alias("_amt20"),
        ret_1d.rolling_min(SELL_SHORT_WINDOW, min_samples=SELL_SHORT_WINDOW)
        .over("ts_code").alias("_max_drop5"),
        # 判据4
        pl.col("close_qfq").rolling_mean(MA5_WINDOW, min_samples=MA5_WINDOW)
        .over("ts_code").alias("_ma5"),
        ret_1d.rolling_sum(RS_WINDOW_DAYS, min_samples=RS_WINDOW_DAYS)
        .over("ts_code").alias("_ret5"),
        pl.col("trade_date").shift(RS_WINDOW_DAYS - 1).over("ts_code").alias("_rs_anchor"),
        # 判据5
        pl.col("high_qfq").rolling_max(HIGH_WINDOW_DAYS, min_samples=HIGH_WINDOW_DAYS)
        .over("ts_code").alias("_high60"),
        pl.col("high_qfq").rolling_max(HIGH_WINDOW_DAYS, min_samples=HIGH_WINDOW_DAYS)
        .shift(1).over("ts_code").alias("_prior_high60"),
        ret_1d.rolling_sum(lift_win, min_samples=lift_win).over("ts_code").alias("_lift"),
        # platform_days(振幅 + 连续天数,模块头「口径细则」)
        (
            (pl.col("high_qfq").rolling_max(amp_win, min_samples=amp_win)
             - pl.col("low_qfq").rolling_min(amp_win, min_samples=amp_win))
            / pl.col("low_qfq").rolling_min(amp_win, min_samples=amp_win)
        ).over("ts_code").alias("_amp"),
    )
    lf = lf.with_columns(
        (pl.col("_amp") <= amp_max + _EPS).fill_null(False).alias("_amp_ok"),
    ).with_columns(
        (~pl.col("_amp_ok")).cast(pl.Int32).cum_sum().over("ts_code").alias("_amp_brk"),
    ).with_columns(
        pl.col("_amp_ok").cast(pl.Int32).cum_sum().over(["ts_code", "_amp_brk"]).alias("_amp_run"),
    )

    # 单次 collect 只物化判定日的行(整条管线惰性到底,峰值内存的关键一刀)。
    cols = (
        lf.filter(pl.col("trade_date").is_in(uniq_days))
        .select([
            "ts_code", "trade_date", "ret_1d", "_low5", "_backlow", "_prior_low",
            "low_qfq", "close_qfq", "high_qfq", "_ma20", "_plat_low", "_down_amt5",
            "_down_cnt5", "_amt20", "_max_drop5", "_ma5", "_ret5", "_rs_anchor",
            "_high60", "_prior_high60", "_lift", "_amp", "_amp_run",
        ])
        .sort(["trade_date", "ts_code"])
        .collect()
    )
    if cols.is_empty():
        logger.warning("[landing] 判定日 %s 在 daily 里无行,零行(缺行 = 不知道,不猜)",
                       ",".join(_d(x) for x in uniq_days))
        return []

    day_index = {s: i for i, s in enumerate(market_days)}
    rs_windows: Dict[str, List[str]] = {}
    for d0 in uniq_days:
        key = _d(d0)
        idx = day_index.get(key)
        if idx is not None and idx >= RS_WINDOW_DAYS - 1:
            rs_windows[key] = market_days[idx - RS_WINDOW_DAYS + 1: idx + 1]

    from neckline.report.industry_strength import load_industry_map

    industry_of = load_industry_map(db_path)
    ind_sums = _industry_ret_sums(rs_windows, db_path)

    # 涨停判定:只读判定日的 limit_derived 分区;某日分区缺失 = 该日全体 na(不猜)。
    limit_lf = _scan_table(
        "limit_derived", parquet_dir, years=_years_in_range(uniq_days[0], end_day)
    )
    limit_df = (
        limit_lf.filter(
            (pl.col("trade_date") >= uniq_days[0]) & (pl.col("trade_date") <= end_day)
        ).select(["ts_code", "trade_date", "is_limit_up"]).collect()
        if limit_lf is not None else pl.DataFrame()
    )
    limit_days: set = set()
    limit_up_map: Dict[Tuple[str, str], bool] = {}
    if not limit_df.is_empty():
        for ts, td, up in zip(
            limit_df["ts_code"].to_list(),
            limit_df["trade_date"].to_list(),
            limit_df["is_limit_up"].to_list(),
        ):
            key = _d(td)
            limit_days.add(key)
            if up is not None:
                limit_up_map[(key, ts)] = bool(up)

    out: List[Dict[str, Any]] = []
    for row in cols.iter_rows(named=True):
        day_str = _d(row["trade_date"])

        # 判据1 读数
        low5_over_base = (
            row["_low5"] / row["_backlow"]
            if row["_low5"] is not None and row["_backlow"] not in (None, 0.0) else None
        )
        new_low_20d = (
            bool(row["low_qfq"] < row["_prior_low"] * (1.0 - _EPS))
            if row["low_qfq"] is not None and row["_prior_low"] is not None else None
        )
        # 判据2 读数
        support = (
            max(row["_ma20"], row["_plat_low"])
            if row["_ma20"] is not None and row["_plat_low"] is not None else None
        )
        close_over_support = (
            row["close_qfq"] / support
            if row["close_qfq"] is not None and support not in (None, 0.0) else None
        )
        # 判据3 读数
        down_cnt = int(row["_down_cnt5"]) if row["_down_cnt5"] is not None else None
        sell_ratio = None
        if (
            down_cnt is not None and down_cnt > 0
            and row["_down_amt5"] is not None and row["_amt20"] not in (None, 0.0)
        ):
            sell_ratio = (row["_down_amt5"] / down_cnt) / row["_amt20"]
        # 判据4 读数(RS5:该票 5 日窗口必须与市场 5 日窗口对齐,模块头登记)
        rs_window = rs_windows.get(day_str)
        stock_ret5 = None
        if (
            rs_window is not None and row["_ret5"] is not None
            and row["_rs_anchor"] is not None and _d(row["_rs_anchor"]) == rs_window[0]
        ):
            stock_ret5 = row["_ret5"]
        industry = industry_of.get(row["ts_code"])
        industry_ret5 = ind_sums.get((day_str, industry)) if industry else None
        rs5 = (
            stock_ret5 - industry_ret5
            if stock_ret5 is not None and industry_ret5 is not None else None
        )
        close_over_ma5 = (
            row["close_qfq"] / row["_ma5"]
            if row["close_qfq"] is not None and row["_ma5"] not in (None, 0.0) else None
        )
        # 判据5 读数
        dist_high = (
            row["close_qfq"] / row["_high60"] - 1.0
            if row["close_qfq"] is not None and row["_high60"] not in (None, 0.0) else None
        )
        new_high_60d = (
            bool(row["high_qfq"] > row["_prior_high60"] * (1.0 + _EPS))
            if row["high_qfq"] is not None and row["_prior_high60"] is not None else None
        )
        # 当日 limit_derived 分区缺失 → 全体 na;分区在但该票无行/值为空 → 该票 na
        # (缺数不猜,⛔ 不把「查无此行」当「非涨停」)。
        is_limit_up = (
            limit_up_map.get((day_str, row["ts_code"])) if day_str in limit_days else None
        )
        platform_days = (
            min(int(row["_amp_run"]), PLATFORM_DAYS_CAP) if row["_amp"] is not None else None
        )

        state, reason = decide_landing(
            low5_over_base=low5_over_base,
            new_low_20d=new_low_20d,
            close_over_support=close_over_support,
            sell_ratio=sell_ratio,
            down_days_5d=down_cnt,
            max_drop_5d=row["_max_drop5"],
            close_over_ma5=close_over_ma5,
            ret_1d=row["ret_1d"],
            rs5=rs5,
            dist_from_high_60d=dist_high,
            lift_ret=row["_lift"],
            is_limit_up=is_limit_up,
            new_high_60d=new_high_60d,
            thresholds=thresholds,
            extra_reason_tokens=extra_tokens,
        )
        metrics = {
            "low5_over_base": _round6(low5_over_base),
            "new_low_20d": new_low_20d,
            "close_over_support": _round6(close_over_support),
            "close_over_ma20": _round6(
                row["close_qfq"] / row["_ma20"]
                if row["close_qfq"] is not None and row["_ma20"] not in (None, 0.0) else None
            ),
            "close_over_platform_low": _round6(
                row["close_qfq"] / row["_plat_low"]
                if row["close_qfq"] is not None and row["_plat_low"] not in (None, 0.0) else None
            ),
            "sell_ratio": _round6(sell_ratio),
            "down_days_5d": down_cnt,
            "max_drop_5d": _round6(row["_max_drop5"]),
            "close_over_ma5": _round6(close_over_ma5),
            "ret_1d": _round6(row["ret_1d"]),
            "stock_ret_5d": _round6(stock_ret5),
            "industry_ret_5d": _round6(industry_ret5),
            "rs5": _round6(rs5),
            "industry": industry,
            "dist_from_high_60d": _round6(dist_high),
            "lift_ret": _round6(row["_lift"]),
            "is_limit_up": is_limit_up,
            "new_high_60d": new_high_60d,
            "platform_amplitude": _round6(row["_amp"]),
            "platform_days": platform_days,
        }
        out.append({
            "trade_date": day_str,
            "ts_code": row["ts_code"],
            "state": state,
            "state_reason": reason,
            "metrics": metrics,
            "skeleton_version": skeleton_version,
        })
    return out


__all__ = [
    "TABLE",
    "FALLING",
    "LANDING_PENDING",
    "LIFTOFF_CONFIRMED",
    "HIGH_EXTENDED",
    "NONE_STATE",
    "STATE_ORDER",
    "STATE_LABELS",
    "LANDING_THRESHOLD_DEFAULTS",
    "PLATFORM_DAYS_CAP",
    "resolve_landing_thresholds",
    "decide_landing",
    "compute_landing_states",
]
