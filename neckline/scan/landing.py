"""落地起跳位置关:全市场逐票**原始读数**唯一实现(plan §五 V2.2-③-C,K8 §二,
🔴 2026-08-09 用户裁定 #11 整节重写)。

**用户裁定 #11 原话**:「推翻,不要搞这个机械层了。我们现在在研究这个机械,研究的
要死。其实对于 LLM 来说,它能够完全决定的。所以说这个地方的判定直接给到大模型。」

裁定后本模块的职责收窄为**只算事实、不下结论**:每个交易日盘后对**当日有 `daily`
行的每只票**算出 K8 §二 五句定性话各自对应的原始读数(比值/收益率/布尔事实),
落 `landing_metrics_daily`(EOD 预计算落表、在线只读,P0-23;§七 P4-50 登记的
本项目第二条「全市场逐票 × 多年回看」批算路径)。**判定交给 LLM**:六关⑤位置关
(`neckline/selection/gates.py`)拿 K8 §二 原文 + 引擎定性准则 `gates.position.guidance`
+ 本模块的读数,产出 `position_verdict ∈ {ok, weak, unfit}`(复用 `basket_reason`
那一次调用,⛔ 不新增 LLM 调用)。本模块只做「算」:纯特征装配
`compute_landing_metrics()`;落表/读表在 `landing_store.py`。**本模块零写库**
(全部 SQL 只读)。

🔴 **本次改动删掉了什么(如实登记,防后人以为是遗漏而"补回去")**:四态判定
`decide_landing()`、四态枚举字面量(下落/落地待确认/支撑确认起跳/高位加速)、
十二个判定阈值、阈值解析函数、对骨架包 `config.landing` 段的一切依赖 ——
**整段删除,不是重构**。机械层现在不做任何「≥ X 即通过」「属于第几态」的判断
(裁定 #11-c 原话「保留读数、删阈值与四态」)。骨架包 `packs/K8-skeleton.json`
自本次起也不再携带 `config.landing` 段。

🔴 **雷区对照(plan §五 ③-C 原文一字不省;防后人当新发现,也防后人当禁令删掉;
只作"知情登记"、不作"否决依据"——§七 P3-49 用户裁定 #10)**:

- 判据 1+2+4 的形状 = **K3-B2 臂③「升势回撤 + 启动确认」**,那一臂被判「**确认信号 = 死猫跳顶点,比直接买更差**」(`research/k3_report.md`)。
- 判据 1 单独用的形状 = **K3 系统化超跌反弹四臂全灭**。
- 判据 5 的排除项 = **K2「追强势」全否决 + K7-C1 诱多做局**的正面兑现(这一半是**站在案底同侧**的)。
- **本关因此只产注意力分层,⛔ 不得被读成买入期望背书**(§3.8-(b) 一字不变)。它的真伪由 K8 自己的选股时钟在**前向样本**上回答(K8 §十七),**不由本版声称**。义务已挂 §七 **P3-49**。
- 🆕 **判定交给 LLM 之后,P3-49 的证伪义务不减反增**:现在连"当时按什么标准判的"都不再是一组可回放的数字,而是一段模型输出 —— 故 `gate_evaluations.evidence_json`(写在 `gates.py`)**必须同时存下当次读数与 LLM 理由**,否则事后无法复核它到底在拿什么下判断。

**十四个原始读数(`metrics_json` 键名,唯一定义 = `compute_landing_metrics()`;
两个 agent 共用同一份契约,⛔ 不许改名、不许增减,详见 `METRIC_KEYS`)**:

    K8 那句话                键(全部无阈值、无及格线,纯事实)
    ────────────────────────────────────────────────────────────────────
    下跌或调整已经结束        low5_over_low20_ratio  近5日最低 ÷ 前20日区间最低
                             is_new_low_20d         bool,当日是否创 20 日新低
    关键位置形成有效支撑      close_over_ma20_dev            close/MA20 − 1
                             close_over_platform_floor_dev  close÷近20日收盘20%分位 − 1
    抛压明显衰减              down_day_amount_ratio_5v20  近5日下跌日均额÷近20日均额
                             max_daily_drop_5d           近5日最大单日跌幅
    价格开始向上转强          close_over_ma5_dev      close/MA5 − 1
                             pct_chg                 当日涨跌幅(close/昨收 − 1)
                             rs5                     相对所属行业中位 5 日超额
    仍处于启动早期            dist_from_high_60d      close/60日高点(含当日) − 1
                             cum_return_3d           近 3 日累计涨幅(日收益率之和)
                             is_limit_up             bool
                             is_new_high_60d         bool
                             platform_days           近 N 日振幅 ≤ X 的连续天数

- 🔴 **机械层的唯一职责 = 把数算对、把缺的说清楚。⛔ 任何形如「≥ X 即通过」「属于
  第几态」的东西一律不许出现在本模块里**——那正是裁定 #11 推翻的东西。上表里
  仅有的三个布尔字段(`is_new_low_20d`/`is_limit_up`/`is_new_high_60d`)与
  `platform_days` 是**契约点名的事实型读数本身**(不是派生的通过/不通过判断),
  ⛔ 不属于本条禁令覆盖范围。
- **缺数照旧不猜**:某项算不出 → 记入 `metrics_missing`(`{读数键: 原因码}`,
  原因码见 `REASON_*` 常量),**⛔ 不填 0、不填默认值**(喂给 LLM 的必须是「这项
  没取到」而不是一个假数)。

**`platform_days` 的两个定义参数(`PLATFORM_AMP_WIN` / `PLATFORM_AMP_MAX`)**:
这是**算这个量的定义参数,不是判据阈值**(振幅窗口 + 振幅上限,用来数「连续多少
天振幅足够收敛」这件事本身——就像「MA20」需要"20"这个数字才能定义,但"20"不是
一条判据)。⛔ 别把它们塞回骨架包:`packs/K8-skeleton.json` 自裁定 #11 起已删掉
整个 `config.landing` 段,本模块也不再对任何包做 import。

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
    5 个成交日;长停牌复牌初期各窗口跨停牌期,读数据实反映这一情况)。唯一例外
    是 **RS5**:行业中位 5 日锚在**市场交易日**上(`industry_strength_daily` 逐日
    行),故该票必须在同一段 5 个市场交易日上连续有行,否则 `rs5` 缺失
    (`window_misaligned`,两个不同步的窗口相减没有意义)。
  - **定义性比较一律带 `_EPS=1e-9` 容差**(`sentinel/holding.py` 体例,恰好持平
    按满足读):`is_new_low_20d`/`is_new_high_60d` 的严格不等号、`platform_days`
    的振幅上限判断均属此类——⚠ 这些是**布尔事实/量的定义**本身的一部分,不是
    「通过/不通过」的判据(见上方"唯一职责"条)。
  - **`platform_days`(近 N 日振幅 ≤ X 的连续天数)算在 `metrics_json` 里,⛔ 不另起
    一张表**(plan §五 ③-F 原文)。振幅 = 滚动 `PLATFORM_AMP_WIN` 日
    (max_high − min_low) / min_low;连续天数在 `PLATFORM_DAYS_CAP` 处右截尾
    (饱和值仍满足 Y1 首版「platform_days ≥ 40」的判读,docstring 登记)。
  - 行业归属用 `stock_basic.industry` 当前快照(`industry_strength` 同款既有取舍)。
  - **`is_limit_up` 的「缺」只有一种,不是两种**(2026-08-09 用真实生产数据回放
    时发现并订正,登记于此防回归):`limit_derived` 是**稀疏表**
    (`data/limit_derived.py` 模块头「仅落涨停/跌停/炸板命中行」,源码
    `hit = df.filter(is_limit_up | is_limit_down | is_zaban)` 逐字为证)——某票
    某日不在表里是「三者皆不成立」的**确定事实**(`is_limit_up=False`),⛔ 不是
    「不知道」;真正的「不知道」只有分区**文件本身不存在**这一种
    (`limit_data_unavailable`)。真实数据实测:全市场 5526 票里单日仅 ~80 只
    在稀疏表中有行,若把「查无此行」当缺数,会把 5400+ 票的确定事实(它就是没
    涨停)错报成"没取到"。

**性能纪律(P0-23 / P4-50 正面靶心)**:取数一律走 `_scan_table(years=…)`(年分区
裁剪,P1-26 既有修法,⛔ 不全 glob);回看窗口 = `_lookback_bars()` 个**交易日**
(全部由固定窗口常量推出,145);增量日更只算当日一行 × 全市场,batch 回填由
`landing_store.refresh_landing_metrics` 分块复用同一实现。底层特征管线(join/
rolling/collect 结构)与裁定 #11 之前**逐字未变**——本次改动只动了"算出读数之后
怎么处理"这一步(不再喂进 `decide_landing()`,直接整理成 `metrics`/`metrics_missing`
两个 dict),内存/耗时量级不预期变化。⚠ 本地实测数字不是生产结论(CLAUDE.md 铁律)
——上生产前必须按 §七 P4-50 `systemd-run --unit=… --property=User=neckline` 隔离
实测计时 + 量峰值(⛔ nk 上不用 root `systemd-run --scope`)。

**反向守门(plan §五 ③ 测试与守门原文)**:本模块零 import `neckline.sentinel.*`
与 `neckline.report.score_display`(位置态 ⛔ 不接任何持仓动作、不进任何推送、
不碰展示标度;守门单测扫源码锁死)。本模块也零 import `neckline.selection.pack`
(裁定 #11 后没有阈值要从包里读,已不存在需要读包的理由)。
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
# 实测(2026-07-24,5526 票 × 145 交易日回看,裁定 #11 之前的同一条管线):eager
# 版 ~716MB 峰值 / 1.2s → 惰性版 ~644MB@默认线程、~490MB@POLARS_MAX_THREADS=2 /
# 0.4s(峰值随 polars 线程数走;`industry_strength._load_ret1d_panel` 的投影下推
# 同款姿势,推到整条管线)。裁定 #11 只删了"算完之后下判断"那一步,管线结构不变,
# 数量级预期不变——⚠ 本地数不是生产结论:上生产前按 §七 P4-50 在生产机隔离实测。
from neckline.data.market_data import _scan_table, _years_in_range, day_file_path
from neckline.db import connection, init_schema
from neckline.report import industry_strength_store as strength_store
# 复用 regime.py 的 `_ret_5d_sum`(「5 日中位收益和必须凑满窗口」的唯一实现,同包
# 私有复用,登记于此;裁定 #11 后不再需要 regime.py 的 `SKELETON_VERSION_FALLBACK`
# ——本表没有 skeleton_version 列,机械层零阈值 = 零口径指纹可言)。
from neckline.scan.regime import _ret_5d_sum

logger = logging.getLogger(__name__)

TABLE = "landing_metrics_daily"

# —— 十四个原始读数键名(唯一契约,与 `neckline/selection/gates.py` 共用;
# ⛔ 不许改名、不许增减,模块头「十四个原始读数」表逐字对应)。————————————————
METRIC_KEYS: Tuple[str, ...] = (
    "low5_over_low20_ratio",
    "is_new_low_20d",
    "close_over_ma20_dev",
    "close_over_platform_floor_dev",
    "down_day_amount_ratio_5v20",
    "max_daily_drop_5d",
    "close_over_ma5_dev",
    "pct_chg",
    "rs5",
    "dist_from_high_60d",
    "cum_return_3d",
    "is_limit_up",
    "is_new_high_60d",
    "platform_days",
)

# —— `metrics_missing` 的原因码词汇(唯一定义;喂 LLM 时原样透传,让它知道
# "没取到"具体是哪一类,不是笼统的一个 null)。——————————————————————————————
REASON_INSUFFICIENT_HISTORY = "insufficient_history"      # 滚动窗口未凑满(该票交易行不够长)
REASON_NO_DOWN_DAYS = "no_down_days"                       # 近 5 日无下跌日,均额无定义(非缺数据)
REASON_INDUSTRY_UNMAPPED = "industry_unmapped"              # stock_basic.industry 查无该票
REASON_INDUSTRY_DATA_UNAVAILABLE = "industry_data_unavailable"  # 行业 5 日中位收益凑不满
REASON_WINDOW_MISALIGNED = "window_misaligned"              # 该票 5 日窗口与市场 5 日窗口不同步
REASON_LIMIT_DATA_UNAVAILABLE = "limit_data_unavailable"    # 当日 limit_derived 分区/该票行缺失

# —— 窗口/定义类引擎常量(事实口径,不是策略参数,⛔ 不进包——照 `stage.py`
# LOOKBACK 常量的既有分工;要改口径改这里并重算历史)。—————————————————————
N_LOW_DAYS = 5                   # 判据①近端最低价窗口(交易日)
N_BACK_DAYS = 20                 # 判据①前区间最低价窗口(交易日;也是「20 日新低」的 20)
MA5_WINDOW = 5                   # 判据④ MA5
MA20_WINDOW = 20                 # 判据② MA20
PLATFORM_WIN_DAYS = 20           # 判据②平台下沿分位窗口(交易日)
PLATFORM_QUANTILE = 0.20         # 判据②平台下沿 =「收盘的 20% 分位」(定义的一部分)
SELL_SHORT_WINDOW = 5            # 判据③「近 5 日」(字面)
SELL_LONG_WINDOW = 20            # 判据③「近 20 日」(字面)
RS_WINDOW_DAYS = 5               # 判据④ RS5 窗口(regime RET_WINDOW_DAYS 同口径)
HIGH_WINDOW_DAYS = 60            # 判据⑤ 60 日高点窗口(字面即 60 日)
LIFT_WINDOW_DAYS = 3             # 判据⑤近端累计涨幅窗口(cum_return_3d 定义的一部分,字面即 3 日)
PLATFORM_DAYS_CAP = 120          # platform_days 右截尾上限(内存上界的一部分,见模块头性能纪律)

# platform_days 的两个定义参数(模块头「`platform_days` 的两个定义参数」段落已
# 详述取舍——这是量的定义,不是及格线,⛔ 别把它们塞回骨架包)。—————————————————
PLATFORM_AMP_WIN = 20             # 滚动振幅窗口(交易日)
PLATFORM_AMP_MAX = 0.25           # 振幅上限 X(取自 K8 §七 Y1「平台够长、振幅够收敛」的定义)

_LOOKBACK_MARGIN = 5              # 取数窗口安全余量(交易日)

# 定义性比较容差(`sentinel/holding.py` 体例;仅用于布尔事实/`platform_days`
# 定义本身的严格不等号,⛔ 不是判据阈值——见模块头「口径细则」)。
_EPS = 1e-9


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _round6(v: Any) -> Any:
    return round(float(v), 6) if isinstance(v, float) else v


def _lookback_bars() -> int:
    """取数回看窗口(交易日):全部读数所需的最长窗口与 `platform_days` 可测跨度
    (截尾上限 + 振幅窗口)取大,加安全余量。全部由本模块固定常量推出(裁定 #11
    后没有骨架包可读,窗口不再随包变化)。bulk 与 day-by-day 三路等价依赖
    「截尾上限 ≤ 两种取数方式都可测的跨度」,⛔ 别把 `PLATFORM_DAYS_CAP` 抬到
    比这里算出的窗口还大。"""
    need = max(
        N_LOW_DAYS + N_BACK_DAYS + 1,     # 判据①(shift 后再看一格)
        HIGH_WINDOW_DAYS + 1,             # 判据⑤(新高看前 60 日)
        PLATFORM_WIN_DAYS,
        MA20_WINDOW,
        SELL_LONG_WINDOW,
        PLATFORM_DAYS_CAP + PLATFORM_AMP_WIN,
    )
    return need + _LOOKBACK_MARGIN


# —————————————————————————————————————————————————————————————————————————————
# 全市场批量特征装配(只读;落表在 landing_store.refresh_landing_metrics)
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
    空 dict(所有票 rs5 缺失,缺数不猜)。"""
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


def _assemble_row_metrics(
    row: Mapping[str, Any],
    *,
    day_str: str,
    rs_windows: Mapping[str, List[str]],
    industry_of: Mapping[str, str],
    ind_sums: Mapping[Tuple[str, str], Optional[float]],
    limit_partition_present: bool,
    limit_up_map: Mapping[Tuple[str, str], bool],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """单票单日:十四个原始读数 + 缺项原因码(唯一装配点)。返回
    `(metrics, metrics_missing)`——`metrics` 只含算出来的键,`metrics_missing`
    只含缺失的键(值 = 原因码);两者的键并集恰为 `METRIC_KEYS`,零重叠。"""
    metrics: Dict[str, Any] = {}
    missing: Dict[str, str] = {}

    def _set(key: str, value: Any, reason: str) -> None:
        if value is None:
            missing[key] = reason
        else:
            metrics[key] = _round6(value)

    # —— 下跌或调整已经结束 ————————————————————————————————————————————————
    low5_over_base = (
        row["_low5"] / row["_backlow"]
        if row["_low5"] is not None and row["_backlow"] not in (None, 0.0) else None
    )
    _set("low5_over_low20_ratio", low5_over_base, REASON_INSUFFICIENT_HISTORY)

    is_new_low_20d = (
        bool(row["low_qfq"] < row["_prior_low"] * (1.0 - _EPS))
        if row["low_qfq"] is not None and row["_prior_low"] is not None else None
    )
    _set("is_new_low_20d", is_new_low_20d, REASON_INSUFFICIENT_HISTORY)

    # —— 关键位置形成有效支撑 ————————————————————————————————————————————————
    ma20_dev = (
        row["close_qfq"] / row["_ma20"] - 1.0
        if row["close_qfq"] is not None and row["_ma20"] not in (None, 0.0) else None
    )
    _set("close_over_ma20_dev", ma20_dev, REASON_INSUFFICIENT_HISTORY)

    plat_dev = (
        row["close_qfq"] / row["_plat_low"] - 1.0
        if row["close_qfq"] is not None and row["_plat_low"] not in (None, 0.0) else None
    )
    _set("close_over_platform_floor_dev", plat_dev, REASON_INSUFFICIENT_HISTORY)

    # —— 抛压明显衰减 ——————————————————————————————————————————————————————
    down_cnt = int(row["_down_cnt5"]) if row["_down_cnt5"] is not None else None
    if down_cnt is None:
        missing["down_day_amount_ratio_5v20"] = REASON_INSUFFICIENT_HISTORY
    elif down_cnt == 0:
        # 近 5 日无下跌日:均额无定义(除以空集),与「数据不够算」是两回事——
        # 对 LLM 而言这本身是有信息量的事实(模块头「口径细则」登记)。
        missing["down_day_amount_ratio_5v20"] = REASON_NO_DOWN_DAYS
    elif row["_down_amt5"] is None or row["_amt20"] in (None, 0.0):
        missing["down_day_amount_ratio_5v20"] = REASON_INSUFFICIENT_HISTORY
    else:
        metrics["down_day_amount_ratio_5v20"] = _round6(
            (row["_down_amt5"] / down_cnt) / row["_amt20"]
        )

    _set("max_daily_drop_5d", row["_max_drop5"], REASON_INSUFFICIENT_HISTORY)

    # —— 价格开始向上转强 ————————————————————————————————————————————————————
    ma5_dev = (
        row["close_qfq"] / row["_ma5"] - 1.0
        if row["close_qfq"] is not None and row["_ma5"] not in (None, 0.0) else None
    )
    _set("close_over_ma5_dev", ma5_dev, REASON_INSUFFICIENT_HISTORY)
    _set("pct_chg", row["ret_1d"], REASON_INSUFFICIENT_HISTORY)

    # rs5:该票 5 日窗口必须与市场 5 日窗口对齐(模块头「口径细则」登记)。
    rs_window = rs_windows.get(day_str)
    stock_ret5 = None
    if (
        rs_window is not None and row["_ret5"] is not None
        and row["_rs_anchor"] is not None and _d(row["_rs_anchor"]) == rs_window[0]
    ):
        stock_ret5 = row["_ret5"]
    industry = industry_of.get(row["ts_code"])
    industry_ret5 = ind_sums.get((day_str, industry)) if industry else None
    if stock_ret5 is None:
        reason = REASON_INSUFFICIENT_HISTORY if row["_ret5"] is None else REASON_WINDOW_MISALIGNED
        missing["rs5"] = reason
    elif industry is None:
        missing["rs5"] = REASON_INDUSTRY_UNMAPPED
    elif industry_ret5 is None:
        missing["rs5"] = REASON_INDUSTRY_DATA_UNAVAILABLE
    else:
        metrics["rs5"] = _round6(stock_ret5 - industry_ret5)

    # —— 仍处于启动早期 ——————————————————————————————————————————————————————
    dist_high = (
        row["close_qfq"] / row["_high60"] - 1.0
        if row["close_qfq"] is not None and row["_high60"] not in (None, 0.0) else None
    )
    _set("dist_from_high_60d", dist_high, REASON_INSUFFICIENT_HISTORY)
    _set("cum_return_3d", row["_lift3"], REASON_INSUFFICIENT_HISTORY)

    # `limit_derived` 是**稀疏表**(`data/limit_derived.py` 模块头:「输出:仅落
    # 涨停/跌停/炸板命中行……衍生表设计为稀疏表,只存"有信号"的行」)——某票某日
    # 不在表里 ⛔ 不是「不知道」,是「涨停/跌停/炸板三者皆不成立」的确定事实
    # (源码 `hit = df.filter(is_limit_up | is_limit_down | is_zaban)` 逐字证实:
    # 未命中的票根本不会被写进表,不是"漏采")。真正的「不知道」只有一种:那一天
    # 的分区**文件本身不存在**(批算没跑,`limit_partition_present=False`)。两者
    # 判据必须分开——2026-08-09 用真实生产数据回放时发现:某票某日在 84 行的稀疏
    # 表里查无此行是全市场 5526 票里 5400+ 票的常态(绝大多数股票任何一天都不会
    # 涨停),若把「查无此行」当「不知道」,会把全市场每天 98% 的确定事实(它就是
    # 没涨停)错报成"没取到",这既不诚实也会把 `metrics_missing` 的信号噪声比拖垮。
    if limit_partition_present:
        metrics["is_limit_up"] = bool(limit_up_map.get((day_str, row["ts_code"]), False))
    else:
        missing["is_limit_up"] = REASON_LIMIT_DATA_UNAVAILABLE

    new_high_60d = (
        bool(row["high_qfq"] > row["_prior_high60"] * (1.0 + _EPS))
        if row["high_qfq"] is not None and row["_prior_high60"] is not None else None
    )
    _set("is_new_high_60d", new_high_60d, REASON_INSUFFICIENT_HISTORY)

    platform_days = (
        min(int(row["_amp_run"]), PLATFORM_DAYS_CAP) if row["_amp"] is not None else None
    )
    _set("platform_days", platform_days, REASON_INSUFFICIENT_HISTORY)

    return metrics, missing


def compute_landing_metrics(
    days: Sequence[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """`days`(升序去重后)每个交易日的全市场逐票**原始读数**装配(全程只读,
    零判定)。返回行 dict 列表:`{"trade_date", "ts_code", "metrics",
    "metrics_missing"}`(JSON 序列化由 store 落表时做)。

    - **判定域 = 当日有 `daily` 行的票**(停牌票当日无行 = 无判定行,由读侧按
      「缺行 = 不知道」披露,⛔ 不落猜出来的行)。
    - 取数一次覆盖 `[min(days) − lookback, max(days)]`(`_scan_table(years=…)` 年
      分区裁剪);逐票 rolling 全部尾窗 + `min_samples=窗口长`(缺一根 bar 即记入
      `metrics_missing`,缺数不猜)。bulk 与 day-by-day 等价:尾窗只看各票最近
      N 根 bar,与取数区间起点无关(`platform_days` 的截尾保证见 `_lookback_bars`
      docstring)。
    - `daily` 整段缺失 / 判定日无行 → 该日零行(不猜、不凑)。
    """
    uniq_days = sorted(set(days))
    if not uniq_days:
        return []
    init_schema(db_path)

    lookback = _lookback_bars()
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
        # 下跌或调整已经结束
        pl.col("low_qfq").rolling_min(N_LOW_DAYS, min_samples=N_LOW_DAYS).over("ts_code").alias("_low5"),
        pl.col("low_qfq").rolling_min(N_BACK_DAYS, min_samples=N_BACK_DAYS).shift(N_LOW_DAYS)
        .over("ts_code").alias("_backlow"),
        pl.col("low_qfq").rolling_min(N_BACK_DAYS, min_samples=N_BACK_DAYS).shift(1)
        .over("ts_code").alias("_prior_low"),
        # 关键位置形成有效支撑
        pl.col("close_qfq").rolling_mean(MA20_WINDOW, min_samples=MA20_WINDOW)
        .over("ts_code").alias("_ma20"),
        pl.col("close_qfq").rolling_quantile(
            quantile=PLATFORM_QUANTILE, window_size=PLATFORM_WIN_DAYS,
            min_samples=PLATFORM_WIN_DAYS, interpolation="linear",
        ).over("ts_code").alias("_plat_low"),
        # 抛压明显衰减
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
        # 价格开始向上转强
        pl.col("close_qfq").rolling_mean(MA5_WINDOW, min_samples=MA5_WINDOW)
        .over("ts_code").alias("_ma5"),
        ret_1d.rolling_sum(RS_WINDOW_DAYS, min_samples=RS_WINDOW_DAYS).over("ts_code").alias("_ret5"),
        pl.col("trade_date").shift(RS_WINDOW_DAYS - 1).over("ts_code").alias("_rs_anchor"),
        # 仍处于启动早期
        pl.col("high_qfq").rolling_max(HIGH_WINDOW_DAYS, min_samples=HIGH_WINDOW_DAYS)
        .over("ts_code").alias("_high60"),
        pl.col("high_qfq").rolling_max(HIGH_WINDOW_DAYS, min_samples=HIGH_WINDOW_DAYS)
        .shift(1).over("ts_code").alias("_prior_high60"),
        ret_1d.rolling_sum(LIFT_WINDOW_DAYS, min_samples=LIFT_WINDOW_DAYS)
        .over("ts_code").alias("_lift3"),
        # platform_days(振幅 + 连续天数,模块头「定义参数」段)
        (
            (pl.col("high_qfq").rolling_max(PLATFORM_AMP_WIN, min_samples=PLATFORM_AMP_WIN)
             - pl.col("low_qfq").rolling_min(PLATFORM_AMP_WIN, min_samples=PLATFORM_AMP_WIN))
            / pl.col("low_qfq").rolling_min(PLATFORM_AMP_WIN, min_samples=PLATFORM_AMP_WIN)
        ).over("ts_code").alias("_amp"),
    )
    lf = lf.with_columns(
        (pl.col("_amp") <= PLATFORM_AMP_MAX + _EPS).fill_null(False).alias("_amp_ok"),
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
            "_high60", "_prior_high60", "_lift3", "_amp", "_amp_run",
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

    # 涨停判定:只读判定日的 limit_derived 分区(**稀疏表**,`_assemble_row_metrics`
    # 「is_limit_up」段已详述其语义)。这里只负责两件事:① 收集该稀疏表里**真的
    # 有的**行(某票某日命中涨停/跌停/炸板其一才会在这);② 逐日直接查文件是否
    # 存在(⛔ 不用"该日有没有行"当存在性代理——那会把"当天恰好零命中"的极端日子
    # 误判成"分区缺失",`day_file_path(...).exists()` 才是与旧测试
    # `os.remove(...)` 手法完全对称的存在性判据)。
    limit_lf = _scan_table(
        "limit_derived", parquet_dir, years=_years_in_range(uniq_days[0], end_day)
    )
    limit_df = (
        limit_lf.filter(
            (pl.col("trade_date") >= uniq_days[0]) & (pl.col("trade_date") <= end_day)
        ).select(["ts_code", "trade_date", "is_limit_up"]).collect()
        if limit_lf is not None else pl.DataFrame()
    )
    limit_up_map: Dict[Tuple[str, str], bool] = {}
    if not limit_df.is_empty():
        for ts, td, up in zip(
            limit_df["ts_code"].to_list(),
            limit_df["trade_date"].to_list(),
            limit_df["is_limit_up"].to_list(),
        ):
            if up is not None:
                limit_up_map[(_d(td), ts)] = bool(up)
    limit_partition_present: Dict[str, bool] = {
        _d(d0): day_file_path("limit_derived", d0, parquet_dir).exists() for d0 in uniq_days
    }

    out: List[Dict[str, Any]] = []
    for row in cols.iter_rows(named=True):
        day_str = _d(row["trade_date"])
        metrics, missing = _assemble_row_metrics(
            row, day_str=day_str, rs_windows=rs_windows, industry_of=industry_of,
            ind_sums=ind_sums, limit_partition_present=limit_partition_present.get(day_str, False),
            limit_up_map=limit_up_map,
        )
        out.append({
            "trade_date": day_str,
            "ts_code": row["ts_code"],
            "metrics": metrics,
            "metrics_missing": missing,
        })
    return out


__all__ = [
    "TABLE",
    "METRIC_KEYS",
    "REASON_INSUFFICIENT_HISTORY",
    "REASON_NO_DOWN_DAYS",
    "REASON_INDUSTRY_UNMAPPED",
    "REASON_INDUSTRY_DATA_UNAVAILABLE",
    "REASON_WINDOW_MISALIGNED",
    "REASON_LIMIT_DATA_UNAVAILABLE",
    "PLATFORM_DAYS_CAP",
    "compute_landing_metrics",
]
