"""行情状态层:D0 盘后三态判定唯一实现(plan §五 V2.2-②,K8 §一「行情状态层」)。

每个交易日盘后判定 **趋势延续 / 高位分歧 / 切换确认** 三态之一,选股(③ 市场关,
批 2 接线)、持仓(纯展示)、复盘(④ 选股时钟)三处共读同一张 `market_regime_daily`
表。本模块只做「算」:纯判定 `decide_regime()` + 五维输入的只读采集
`compute_market_regime_for_day()`;落表/读表在 `regime_store.py`(P0-23:EOD 预计算
落表、在线只读)。**本模块零写库**(全部 SQL 只读;守门单测 AST 锁)。

**三态判据(唯一定义 = `decide_regime()`,互斥、优先级从上往下,`regime` 恒非
NULL —— 照 `scan/stage.py::decide_stage` 既有体例)**:

    rotation_confirmed   旧主线 5 日中位收益 < 0 且 新方向 5 日中位收益 > 旧主线 + ROT_GAP
                         且 新方向近 3 日至少 2 天为强度日 且 资金迁移排名上升 ≥ ROT_RANK
    high_divergence      核心强度较 5 日均值下降 ≥ DIV_CORE_DROP 或
                         (板块广度分位 ≤ DIV_BREADTH 且 涨停家数环比降 ≥ DIV_LIMIT_DROP)
    trend_continuation   其余(默认态)

- **顺序即优先级**:先判切换、再判分歧、剩下算延续。**默认态是「延续」不是
  「不知道」**(plan §五 ② 720 行写死了理由:三态里只有延续不改变任何门槛,把
  不确定归到最不激进的那一态)。「不知道」的形态是**当日缺行**,不在本函数域内。
- 🔴 **high_divergence 的优先级读法(如实登记,plan §五 ② 施工指令要求写明)**:
  施工图原文「A 或 B 且 C」优先级含糊,本实现按 **「A 或 (B 且 C)」** 读 —— 与 K8
  「高位分歧 = 核心走弱 **或** 广度+涨停同步萎缩」的语义一致(A = 核心强度下降,
  B = 广度分位低,C = 涨停环比降)。⛔ 不是笔者静默选边,是登记过的裁定。
- **缺数不猜(§3.8)**:任何一门的输入为 `None` = 该门不成立(na),⛔ 不当 0 参与
  比较;某维整体缺席时判定照跑,`regime_reason` 记 `missing:<dim>`。
- **阈值比较一律带 `_EPS=1e-9` 容差**(`sentinel/holding.py` 体例)——代价是
  「恰好等于阈值」按满足读(如 gap 恰为 0.03 判过):二进制浮点下严格边界本就
  不可靠,这是登记过的既有取舍,不是 bug。

**五个判定阈值住骨架包 `config.regime`(K8-V0.5),⛔ 不是本模块常量的抄写处**:
`resolve_regime_thresholds()` 逐键回退引擎默认(照 `tier.resolve_quality_lines()`
体例:缺键/形状不对 → 回退 + WARNING 不静默)。**无骨架线现役**(批 1 期间四包均
未激活的真实状态)→ 全部回退引擎默认常量,`skeleton_version` 落哨兵串
`'engine_default'`(⛔ 不写 NULL、不伪造版本号)+ `regime_reason` 记
`missing:skeleton_pack`。窗口常量(5 日/10 日/60 日等)是**事实表引擎常量**,
不是策略参数,住本模块(照 `stage.py` DIVERGENCE_LOOKBACK_DAYS 的既有分工)。

**第 5 维「近期 T1/T2 正确率」的两道锁(plan §五 ② 用户裁定 #7,断开 K8 文本自带
的反馈环)**:
  1. **前视锁**:只吃「已结案且结案日 ≤ D0−1」的样本 —— 采样窗口结构性止于
     `_recent_trading_days_before(D0, N)`(严格早于 D0),D0 当天的验证行读不进来。
  2. **权限锁**:第 5 维只影响 `regime` 三态,⛔ 不改任何引擎阈值/包/篮子去留。
     本模块对 `neckline.selection.pack` **只 import 读入口**(`Pack` /
     `get_active_skeleton`),⛔ 零 import `activate_pack` 等写入口(AST 守门)。
  ⚠ 冷启动现实(§七 P3-51):④ 选股时钟未上线前退化读 `basket_verification` 的
  EOD 定论行(`source='basket_verification_fallback'` 如实标注);零样本 →
  `available=false` + `unavailable_reason='clock_samples_insufficient'`,⛔ 不当
  「正确率 0」。且**首版判据公式本就不含第 5 维**(它是登记在案的判断依据、只
  留痕披露)——把正确率接进任何阈值/规则都须重新拿用户拍板(§2.9-C-5)。

**五维数据源(全部读已有数据,⛔ 零新增 TuShare 调用、零全历史 scan_parquet)**:
  1 核心强度   前一交易日 T1/T2 篮子核心角色成员(`basket_members`,role_mech 优先、
               缺席退 role_llm ∈ {leader, core},照 `positions_entry.py` 既有先例)
               当日 `daily.pct_chg` 全市场分位的**中位数**(0~1;分位算法用
               bisect,与行序无关、天然确定)+ 连板梯队最高高度(`report/
               sentiment.py`,留痕不进判据)。
  2 板块广度   强度行业数 ÷ 总行业数(`industry_strength_daily`;分母 = 当日已评级
               行业数)+ 其 60 日窗口分位;涨停家数及环比(`limit_derived` 当日/
               前日两个单日分区)。
  3 相对强弱   `industry_strength_daily.median_ret` 序列。**旧主线/新方向的机械
               识别(工程首版定义,如实登记)**:旧主线 = 近 10 个交易日(止于
               D0−1)强度日数最多且 ≥ 3 的行业(tie-break:天数降序 → 行业名升序);
               新方向候选 = 近 3 日(含 D0)≥ 2 个强度日、且 ≠ 旧主线的行业里
               5 日中位收益和最大者(tie-break 同上姿势)。「5 日中位收益」= 逐日
               行业中位收益的 **5 日求和**,且**窗口必须凑满 5 天**(照 `leader.py`
               RS20 严格口径先例,少一天都不算、不近似)。
  4 资金迁移   `moneyflow_dc` 按 **`stock_basic.industry` 行业**聚合主力净额的排名
               变化(D0 vs 5 个交易日前;先 (净额降序, 行业名升序) 确定性 tie-break
               再 ordinal —— CLAUDE.md「rank 必须先排 tie-break」铁律)。
               ⚠ **与 `report/sector_moneyflow.py` 的口径差(如实登记)**:那份是
               概念板块(ths_member)聚合,行业与概念板块是两个不同的量(CLAUDE.md
               明文禁混);本维判据键是**行业**(施工图原文「行业维度主力净额排名
               变化」),故按行业映射自聚合,只复用其 `MONEYFLOW_COVERAGE_START`
               的诚实缺席文案先例。整维包保险丝(07-27 裸调教训)。
  5 T1/T2 正确率  见上「两道锁」。正确率口径(工程首版,只留痕):verified=1、
               partial=0.5、falsified=0,unclear 不计入分母(说不清不猜)。

**position_quota(满额/半额/休息)是 inputs_json 的一项输入,两者并存不合并**
(plan §五 ② 724 行):本模块读 `report/sentiment.py::compute_sentiment`,
⛔ 不反向改它(零写入,AST 守门)。
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.data.market_data import get_market_slice
from neckline.db import connection, init_schema
from neckline.report import industry_strength_store as strength_store
# ⚠ 权限锁:对 selection.pack 只许 import **读入口**(Pack / get_active_skeleton),
# ⛔ 零 import activate_pack 等写入口 —— 守门单测 AST 锁,别在这里加名字。
from neckline.selection.pack import Pack, get_active_skeleton

logger = logging.getLogger(__name__)

# —— 三态英文码(唯一源;库列值与展示映射同源,照 `stage.py` STAGE_LABELS 先例)——
TREND_CONTINUATION = "trend_continuation"
HIGH_DIVERGENCE = "high_divergence"
ROTATION_CONFIRMED = "rotation_confirmed"

# 优先级从上往下(仅供文档/展示;`decide_regime()` 的判据本身已互斥分支)。
REGIME_ORDER: Tuple[str, ...] = (ROTATION_CONFIRMED, HIGH_DIVERGENCE, TREND_CONTINUATION)

REGIME_LABELS: Dict[str, str] = {
    TREND_CONTINUATION: "趋势延续",
    HIGH_DIVERGENCE: "高位分歧",
    ROTATION_CONFIRMED: "切换确认",
}

# —— 五维英文码(inputs_json 键 + `missing:<dim>` 原因码,唯一源)————————————
DIM_CORE_STRENGTH = "core_strength"
DIM_BREADTH = "breadth"
DIM_RELATIVE_STRENGTH = "relative_strength"
DIM_MONEYFLOW = "moneyflow_migration"
DIM_ACCURACY = "t1t2_accuracy"
DIM_ORDER: Tuple[str, ...] = (
    DIM_CORE_STRENGTH, DIM_BREADTH, DIM_RELATIVE_STRENGTH, DIM_MONEYFLOW, DIM_ACCURACY,
)

# —— 五个判定阈值的引擎默认(plan §五 ② 714–718 原文数值)。权威住骨架包
# `config.regime`(每键 {value, provenance});这里只是**无包/缺键时的回退值**
# (照 `tier.TIER1_MIN_SCORE` 降级为缺省回退值的既有分工)。键名白名单与
# `selection/pack.py::_REGIME_THRESHOLD_KEYS` 双向对拍(守门单测锁相等,防漂)。——
REGIME_THRESHOLD_DEFAULTS: Dict[str, float] = {
    "rot_gap": 0.03,          # 切换确认:新方向 5 日中位收益须超旧主线的差值下限
    "rot_rank": 10.0,         # 切换确认:资金迁移排名上升名次下限
    "div_core_drop": 0.30,    # 高位分歧 A:核心强度较 5 日均值下降的分位下限
    "div_breadth": 0.35,      # 高位分歧 B:板块广度分位上限
    "div_limit_drop": 0.30,   # 高位分歧 C:涨停家数环比降幅下限
}

# 无骨架线现役时 `skeleton_version` 列的哨兵串(⛔ 不写 NULL、不伪造版本号)。
SKELETON_VERSION_FALLBACK = "engine_default"

# 阈值比较容差(`sentinel/holding.py` 体例)。
_EPS = 1e-9

# —— 窗口/样本引擎常量(事实口径,不是策略参数 —— 照 `stage.py` 两个 LOOKBACK
# 常量的既有分工;要改口径改这里并重算历史,⛔ 不进包)————————————————————
RET_WINDOW_DAYS = 5               # 「5 日中位收益」窗口(严格凑满,RS20 先例)
CORE_AVG_LOOKBACK_DAYS = 5        # 核心强度「5 日均值」= D0 之前 5 个交易日
CORE_AVG_MIN_SAMPLES = 3          # 5 日里至少 3 天算得出核心强度才敢给均值
MAINLINE_LOOKBACK_DAYS = 10       # 旧主线识别窗口(止于 D0−1)
MAINLINE_MIN_STRENGTH_DAYS = 3    # 旧主线资格:窗口内强度日数下限
NEW_DIRECTION_WINDOW_DAYS = 3     # 「近 3 日」窗口(含 D0)
NEW_DIRECTION_MIN_STRENGTH_DAYS = 2   # 判据原文「至少 2 天为强度日」(定死在判据文本)
BREADTH_PCTILE_WINDOW_DAYS = 60   # 板块广度分位窗口(含 D0)
BREADTH_PCTILE_MIN_WINDOW_DAYS = 20   # 窗口内不足 20 天有数 → 分位不可得(不猜)
MONEYFLOW_COMPARE_LAG_DAYS = 5    # 资金迁移排名对比日 = D0 往前第 5 个交易日
ACCURACY_WINDOW_DAYS = 10         # 第 5 维采样窗口(止于 D0−1,前视锁)
DIRECTIONS_TOP_N = 5              # strengthening/weakening 各留几条

# 「核心角色」的角色码集合(role_mech 优先、缺席退 role_llm,照 `positions_entry`
# 先例;leader 是主线核心的头名,一并计入 —— 工程首版口径,docstring 已登记)。
_CORE_ROLES = frozenset({"leader", "core"})


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _recent_trading_days_before(d: date, n: int) -> List[date]:
    """`d` **严格之前**最近 `n` 个交易日,升序(照 `stage.py` 同名私有函数体例;
    第 5 维的前视锁在结构上依赖「严格之前」这三个字)。"""
    from neckline.calendar import prev_trading_day

    out: List[date] = []
    cur = d
    for _ in range(n):
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


# —————————————————————————————————————————————————————————————————————————————
# 阈值解析(骨架包 → 引擎默认逐键回退)
# —————————————————————————————————————————————————————————————————————————————

def resolve_regime_thresholds(
    pack: Optional[Pack],
) -> Tuple[Dict[str, float], str, Tuple[str, ...]]:
    """从骨架包 `config.regime` 解出五个判定阈值(照 `tier.resolve_quality_lines()`
    体例:逐键独立回退引擎默认 + WARNING 不静默)。

    返回 `(阈值字典, skeleton_version, 附加原因码元组)`:
      - 无骨架线现役(批 1 期间的真实状态)→ 全默认 + `'engine_default'` 哨兵串 +
        `('missing:skeleton_pack',)`;⛔ 不写 NULL、不伪造版本号。
      - 有包但缺键/叶子形状不对(历史行才可能;新包激活时 `pack.py::_validate_regime`
        已挡)→ 该键回退默认 + WARNING,`skeleton_version` 仍是包版本(阈值主体
        口径仍以包为准,个别回退键在 WARNING 里点名)。"""
    if pack is None:
        logger.warning(
            "[regime] 无现役骨架线(selection_packs 无 line_code='V' 现役行)——五个判定"
            "阈值全部回退引擎默认常量,skeleton_version 记 %r(regime_reason 记 "
            "missing:skeleton_pack)。", SKELETON_VERSION_FALLBACK,
        )
        return dict(REGIME_THRESHOLD_DEFAULTS), SKELETON_VERSION_FALLBACK, ("missing:skeleton_pack",)
    raw = pack.regime_config()
    out: Dict[str, float] = {}
    fell_back: List[str] = []
    for key, default in REGIME_THRESHOLD_DEFAULTS.items():
        leaf = raw.get(key)
        value = leaf.get("value") if isinstance(leaf, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
        else:
            out[key] = float(default)
            fell_back.append(key)
    if fell_back:
        logger.warning(
            "[regime] 骨架包 %s 的 config.regime 缺键或叶子形状不对:%s —— 逐键回退"
            "引擎默认(照 tier.resolve_quality_lines 体例,不静默)。",
            pack.pack_version, fell_back,
        )
    return out, pack.pack_version, ()


# —————————————————————————————————————————————————————————————————————————————
# 三态判据(唯一定义,纯函数)
# —————————————————————————————————————————————————————————————————————————————

def _fmt(x: float) -> str:
    return f"{x:.4f}"


def decide_regime(
    *,
    old_mainline_ret_5d: Optional[float],
    new_direction_ret_5d: Optional[float],
    new_direction_strength_days_3d: Optional[int],
    new_direction_rank_gain: Optional[float],
    core_drop_from_avg5: Optional[float],
    breadth_pctile: Optional[float],
    limit_up_drop_ratio: Optional[float],
    thresholds: Optional[Mapping[str, float]] = None,
    missing_dims: Sequence[str] = (),
    extra_reason_tokens: Sequence[str] = (),
) -> Tuple[str, str]:
    """三态判据唯一实现(互斥、优先级从上往下)。返回 `(regime, regime_reason)`,
    `regime` 恒非 NULL。

    - **切换确认**四门:旧主线 5 日收益 < 0 · 新旧差 > `rot_gap` · 新方向近 3 日
      强度日 ≥ 2 · 资金迁移排名上升 ≥ `rot_rank`,**全 ok 才成立**。
    - **高位分歧**读法 = **「A 或 (B 且 C)」**(A=核心强度较 5 日均值下降 ≥
      `div_core_drop`;B=板块广度分位 ≤ `div_breadth`;C=涨停家数环比降 ≥
      `div_limit_drop`)—— 施工图原文「A 或 B 且 C」优先级含糊,按 K8「核心走弱
      **或** 广度+涨停同步萎缩」的语义取此读法,登记于此,⛔ 不是静默选边。
    - **任何输入为 `None` = 该门 na = 不成立,⛔ 不当 0 参与比较**;缺席的维度经
      `missing_dims` 追加 `missing:<dim>` 原因码,判定照跑(默认落到最不激进的
      「延续」态)。
    - 全部比较带 `_EPS` 容差(恰好等于阈值按满足读,docstring 已登记取舍)。

    `regime_reason` = 分号连接的机器可读原因码串(逐门留痕:`ok`/`fail`/`na`,
    `hit`/`no`),尾部挂 `missing:<dim>` 与 `extra_reason_tokens`(如
    `missing:skeleton_pack`)。"""
    th = dict(REGIME_THRESHOLD_DEFAULTS)
    if thresholds:
        th.update({k: float(v) for k, v in thresholds.items() if k in REGIME_THRESHOLD_DEFAULTS})
    tokens: List[str] = []

    # —— 切换确认(四门,顺序 = 施工图判据行文顺序)————————————————————————
    rot_gates: List[bool] = []
    if old_mainline_ret_5d is None:
        tokens.append("rot.old_5d=na")
        rot_gates.append(False)
    else:
        ok = old_mainline_ret_5d < -_EPS
        tokens.append(f"rot.old_5d={_fmt(old_mainline_ret_5d)}<0:{'ok' if ok else 'fail'}")
        rot_gates.append(ok)
    if old_mainline_ret_5d is None or new_direction_ret_5d is None:
        tokens.append("rot.gap=na")
        rot_gates.append(False)
    else:
        gap = new_direction_ret_5d - old_mainline_ret_5d
        ok = gap > th["rot_gap"] - _EPS
        tokens.append(f"rot.gap={_fmt(gap)}>{_fmt(th['rot_gap'])}:{'ok' if ok else 'fail'}")
        rot_gates.append(ok)
    if new_direction_strength_days_3d is None:
        tokens.append("rot.strength_3d=na")
        rot_gates.append(False)
    else:
        sd = int(new_direction_strength_days_3d)
        ok = sd >= NEW_DIRECTION_MIN_STRENGTH_DAYS
        tokens.append(
            f"rot.strength_3d={sd}>={NEW_DIRECTION_MIN_STRENGTH_DAYS}:{'ok' if ok else 'fail'}"
        )
        rot_gates.append(ok)
    if new_direction_rank_gain is None:
        tokens.append("rot.rank_gain=na")
        rot_gates.append(False)
    else:
        rg = float(new_direction_rank_gain)
        ok = rg >= th["rot_rank"] - _EPS
        tokens.append(f"rot.rank_gain={_fmt(rg)}>={_fmt(th['rot_rank'])}:{'ok' if ok else 'fail'}")
        rot_gates.append(ok)
    rotation = all(rot_gates)

    # —— 高位分歧:A 或 (B 且 C)(读法登记见 docstring)——————————————————————
    if core_drop_from_avg5 is None:
        a_ok = False
        tokens.append("div.core_drop=na")
    else:
        a_ok = core_drop_from_avg5 >= th["div_core_drop"] - _EPS
        tokens.append(
            f"div.core_drop={_fmt(core_drop_from_avg5)}>={_fmt(th['div_core_drop'])}:"
            f"{'hit' if a_ok else 'no'}"
        )
    if breadth_pctile is None:
        b_ok = False
        tokens.append("div.breadth_pctile=na")
    else:
        b_ok = breadth_pctile <= th["div_breadth"] + _EPS
        tokens.append(
            f"div.breadth_pctile={_fmt(breadth_pctile)}<={_fmt(th['div_breadth'])}:"
            f"{'hit' if b_ok else 'no'}"
        )
    if limit_up_drop_ratio is None:
        c_ok = False
        tokens.append("div.limit_drop=na")
    else:
        c_ok = limit_up_drop_ratio >= th["div_limit_drop"] - _EPS
        tokens.append(
            f"div.limit_drop={_fmt(limit_up_drop_ratio)}>={_fmt(th['div_limit_drop'])}:"
            f"{'hit' if c_ok else 'no'}"
        )
    divergence = a_ok or (b_ok and c_ok)

    if rotation:
        regime = ROTATION_CONFIRMED
    elif divergence:
        regime = HIGH_DIVERGENCE
    else:
        regime = TREND_CONTINUATION

    tail = [f"missing:{dim}" for dim in missing_dims] + list(extra_reason_tokens)
    return regime, ";".join(tokens + tail)


# —————————————————————————————————————————————————————————————————————————————
# 五维输入采集(全部只读;各维在 compute_market_regime_for_day 里各自包保险丝)
# —————————————————————————————————————————————————————————————————————————————

def _rs_percentiles(
    day: date, codes: Sequence[str], parquet_dir: Optional[Path]
) -> Tuple[Optional[List[float]], str]:
    """`codes` 在 `day` 的全市场 `pct_chg` 分位(0~1)。分位 = (严格小于数 + 相等数
    的一半) / 全市场样本数 —— bisect 实现,与行序无关、并列票天然对称,不需要
    ordinal tie-break(CLAUDE.md rank 铁律的另一种满足方式:根本不产 ordinal)。
    `(None, 原因)` = 当日算不出(daily 分区缺失等),不猜。"""
    daily = get_market_slice(day, table="daily", parquet_dir=parquet_dir)
    if daily.is_empty():
        return None, f"daily {_d(day)} 分区缺失"
    by_code = {
        c: p for c, p in zip(daily["ts_code"].to_list(), daily["pct_chg"].to_list())
        if p is not None
    }
    if not by_code:
        return None, f"daily {_d(day)} 的 pct_chg 全空"
    market = sorted(by_code.values())
    n = len(market)
    vals: List[float] = []
    for c in codes:
        p = by_code.get(c)
        if p is None:
            continue
        vals.append((bisect_left(market, p) + bisect_right(market, p)) / (2.0 * n))
    if not vals:
        return None, "核心成员当日无行情行"
    return vals, ""


def _core_member_codes(day: date, db_path: Optional[Path]) -> List[str]:
    """`day` 的核心成员 = **前一交易日** T1/T2 篮子里角色为 leader/core 的成员
    (role_mech 优先、缺席退 role_llm,照 `positions_entry.py`/`schemas.py` 既有
    先例)。排序去重保证确定性。"""
    from neckline.calendar import prev_trading_day

    basket_day = prev_trading_day(day)
    with connection(db_path) as conn:
        rows = conn.execute(
            "SELECT bm.ts_code, bm.role_llm, bm.role_mech FROM basket_members bm "
            "JOIN baskets b ON b.id = bm.basket_id "
            "WHERE b.trade_date=? AND b.tier IN (1,2)",
            (_d(basket_day),),
        ).fetchall()
    return sorted({ts for ts, role_llm, role_mech in rows if (role_mech or role_llm) in _CORE_ROLES})


def _core_strength_for_day(
    day: date, db_path: Optional[Path], parquet_dir: Optional[Path]
) -> Tuple[Optional[float], int, str]:
    """某日核心强度 = 核心成员当日 RS 分位的**中位数**(样本排序后取中,确定)。
    `(None, 0, 原因)` = 当日算不出(无核心成员 / daily 缺失),不猜。"""
    codes = _core_member_codes(day, db_path)
    if not codes:
        return None, 0, "前一交易日无 T1/T2 篮子核心角色成员"
    vals, why = _rs_percentiles(day, codes, parquet_dir)
    if vals is None:
        return None, 0, why
    vals.sort()
    n = len(vals)
    med = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
    return med, n, ""


def _compute_core_strength_dim(
    day: date, db_path: Optional[Path], parquet_dir: Optional[Path],
    max_consec_limit_up: Optional[int],
) -> Dict[str, Any]:
    """维 1:核心强度。`available` 跟「今日值算不算得出」走;5 日均值另有最低样本数
    要求(`CORE_AVG_MIN_SAMPLES`,某些日子合法地没有主线篮子),不足 → `avg_5d=None`
    → 判据 A 门 na(缺数不猜)。`max_consec_limit_up` 只留痕不进判据。"""
    today, member_n, why = _core_strength_for_day(day, db_path, parquet_dir)
    prior: List[float] = []
    for prev_day in _recent_trading_days_before(day, CORE_AVG_LOOKBACK_DAYS):
        v, _n, _w = _core_strength_for_day(prev_day, db_path, parquet_dir)
        if v is not None:
            prior.append(v)
    avg = sum(prior) / len(prior) if len(prior) >= CORE_AVG_MIN_SAMPLES else None
    drop = (avg - today) if (avg is not None and today is not None) else None
    return {
        "available": today is not None,
        "unavailable_reason": "" if today is not None else why,
        "today": today,
        "avg_5d": avg,
        "drop_from_avg5": drop,
        "member_count": member_n,
        "avg_sample_days": len(prior),
        "max_consec_limit_up": max_consec_limit_up,
    }


def _compute_breadth_dim(
    day: date, db_path: Optional[Path], parquet_dir: Optional[Path]
) -> Dict[str, Any]:
    """维 2:板块广度 + 涨停家数环比。广度分母 = 当日已评级行业数
    (`is_strength_day IS NOT NULL`);分位 = 今日广度在 60 日窗口(有数的日子)里的
    `<=` 占比,窗口有数天数 < 20 → 分位不可得(`pctile=None`,不猜)。
    直接只读 `industry_strength_daily` 若干日的聚合(照 `stage.py::_strength_today_rows`
    先例:store 在线入口按契约过滤未达标行,这里要的是全体已评级行业)。"""
    window_days = _recent_trading_days_before(day, BREADTH_PCTILE_WINDOW_DAYS - 1) + [day]
    day_strs = [_d(x) for x in window_days]
    placeholders = ",".join("?" * len(day_strs))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, SUM(CASE WHEN is_strength_day=1 THEN 1 ELSE 0 END), "
            f"COUNT(is_strength_day) FROM {strength_store.TABLE} "
            f"WHERE trade_date IN ({placeholders}) GROUP BY trade_date",
            day_strs,
        ).fetchall()
    ratio_by_day = {td: (s or 0) / c for td, s, c in rows if c}
    today_key = _d(day)
    today_pair = next(((s or 0, c) for td, s, c in rows if td == today_key and c), None)

    # 涨停家数(当日 + 前一交易日各一个单日分区;分区缺失 = None,不猜 0)
    from neckline.calendar import prev_trading_day

    lt_today = get_market_slice(day, table="limit_derived", parquet_dir=parquet_dir)
    limit_up_today = None if lt_today.is_empty() else int(lt_today.filter(pl.col("is_limit_up")).height)
    prev_day = prev_trading_day(day)
    lt_prev = get_market_slice(prev_day, table="limit_derived", parquet_dir=parquet_dir)
    limit_up_prev = None if lt_prev.is_empty() else int(lt_prev.filter(pl.col("is_limit_up")).height)
    drop_ratio = None
    if limit_up_today is not None and limit_up_prev is not None and limit_up_prev > 0:
        drop_ratio = (limit_up_prev - limit_up_today) / limit_up_prev

    if today_pair is None:
        return {
            "available": False,
            "unavailable_reason": "industry_strength_daily 当日无行(16:05 日更未跑或失败)",
            "strength_industries": None, "total_industries": None, "ratio": None,
            "pctile": None, "pctile_window_days": len(ratio_by_day),
            "limit_up_today": limit_up_today, "limit_up_prev": limit_up_prev,
            "limit_up_drop_ratio": drop_ratio,
        }
    strength_n, total_n = today_pair
    ratio = strength_n / total_n
    pctile = None
    if len(ratio_by_day) >= BREADTH_PCTILE_MIN_WINDOW_DAYS:
        pctile = sum(1 for r in ratio_by_day.values() if r <= ratio + _EPS) / len(ratio_by_day)
    return {
        "available": True,
        "unavailable_reason": "",
        "strength_industries": int(strength_n),
        "total_industries": int(total_n),
        "ratio": ratio,
        "pctile": pctile,
        "pctile_window_days": len(ratio_by_day),
        "limit_up_today": limit_up_today,
        "limit_up_prev": limit_up_prev,
        "limit_up_drop_ratio": drop_ratio,
    }


def _strength_rows_for_days(
    days: Sequence[date], db_path: Optional[Path]
) -> List[Tuple[str, str, Optional[float], Optional[int]]]:
    """`industry_strength_daily` 若干日的 `(trade_date, industry, median_ret,
    is_strength_day)`(直接只读,先例与理由同 `_compute_breadth_dim` docstring)。"""
    day_strs = [_d(x) for x in days]
    placeholders = ",".join("?" * len(day_strs))
    with connection(db_path) as conn:
        return conn.execute(
            f"SELECT trade_date, industry, median_ret, is_strength_day "
            f"FROM {strength_store.TABLE} WHERE trade_date IN ({placeholders})",
            day_strs,
        ).fetchall()


def _ret_5d_sum(
    industry: str, ret_day_strs: Sequence[str],
    ret_by_key: Mapping[Tuple[str, str], Optional[float]],
) -> Optional[float]:
    """「5 日中位收益」= 逐日行业中位收益的 5 日求和,**窗口必须凑满**(严格口径,
    照 `leader.py` RS20 先例:这是一个有明确窗口长度的量,少一天都不算、不近似)。"""
    vals = []
    for td in ret_day_strs:
        v = ret_by_key.get((td, industry))
        if v is None:
            return None
        vals.append(float(v))
    return sum(vals)


def _compute_relative_strength_dim(day: date, db_path: Optional[Path]) -> Dict[str, Any]:
    """维 3:旧主线 vs 新方向的 5 日中位收益(机械识别定义见模块 docstring 五维表,
    工程首版、如实登记)。`available` 跟「旧主线识别得出且其 5 日序列完整」走;
    「识别得出旧主线、但当前没有合格新方向候选」是**评估过的合法结论**(available
    仍 True,`new_direction=None`),不是「没看」。"""
    ret_days = _recent_trading_days_before(day, RET_WINDOW_DAYS - 1) + [day]
    ret_day_strs = [_d(x) for x in ret_days]
    mainline_days = _recent_trading_days_before(day, MAINLINE_LOOKBACK_DAYS)
    mainline_day_strs = {_d(x) for x in mainline_days}
    nd_day_strs = {_d(x) for x in ret_days[-NEW_DIRECTION_WINDOW_DAYS:]}

    all_days = sorted(set(ret_days) | set(mainline_days))
    rows = _strength_rows_for_days(all_days, db_path)

    ret_by_key: Dict[Tuple[str, str], Optional[float]] = {}
    mainline_cnt: Dict[str, int] = {}
    nd_cnt: Dict[str, int] = {}
    for td, industry, median_ret, is_str in rows:
        ret_by_key[(td, industry)] = median_ret
        if is_str:
            if td in mainline_day_strs:
                mainline_cnt[industry] = mainline_cnt.get(industry, 0) + 1
            if td in nd_day_strs:
                nd_cnt[industry] = nd_cnt.get(industry, 0) + 1

    qualified = [
        (cnt, ind) for ind, cnt in mainline_cnt.items() if cnt >= MAINLINE_MIN_STRENGTH_DAYS
    ]
    if not qualified:
        return {
            "available": False,
            "unavailable_reason": (
                f"近 {MAINLINE_LOOKBACK_DAYS} 个交易日无行业达到 "
                f"{MAINLINE_MIN_STRENGTH_DAYS} 个强度日,判不出旧主线"
            ),
            "old_mainline": None, "old_mainline_strength_days": None,
            "old_ret_5d_sum": None, "new_direction": None, "new_ret_5d_sum": None,
            "new_strength_days_3d": None, "candidate_pool_size": 0,
        }
    # tie-break:强度日数降序 → 行业名升序(确定性,rank 铁律姿势)
    qualified.sort(key=lambda t: (-t[0], t[1]))
    mainline_strength_days, mainline = qualified[0]
    old_ret = _ret_5d_sum(mainline, ret_day_strs, ret_by_key)
    if old_ret is None:
        return {
            "available": False,
            "unavailable_reason": (
                f"旧主线 {mainline} 的 5 日中位收益序列不完整"
                "(窗口内缺行,严格口径不近似)"
            ),
            "old_mainline": mainline, "old_mainline_strength_days": mainline_strength_days,
            "old_ret_5d_sum": None, "new_direction": None, "new_ret_5d_sum": None,
            "new_strength_days_3d": None, "candidate_pool_size": 0,
        }

    pool: List[Tuple[float, str]] = []
    for ind, cnt in nd_cnt.items():
        if ind == mainline or cnt < NEW_DIRECTION_MIN_STRENGTH_DAYS:
            continue
        r = _ret_5d_sum(ind, ret_day_strs, ret_by_key)
        if r is not None:
            pool.append((r, ind))
    pool.sort(key=lambda t: (-t[0], t[1]))
    new_direction = pool[0][1] if pool else None
    return {
        "available": True,
        "unavailable_reason": "",
        "old_mainline": mainline,
        "old_mainline_strength_days": mainline_strength_days,
        "old_ret_5d_sum": old_ret,
        "new_direction": new_direction,
        "new_ret_5d_sum": pool[0][0] if pool else None,
        "new_strength_days_3d": nd_cnt.get(new_direction) if new_direction else None,
        "candidate_pool_size": len(pool),
    }


def _industry_net_ranks(mf, industry_of: Mapping[str, str]) -> Dict[str, int]:
    """`moneyflow_dc` 一日切片 → 行业主力净额排名(1 = 净流入最大)。
    **先 (净额降序, 行业名升序) 确定性 tie-break 再编 ordinal 名次**(CLAUDE.md
    rank 铁律,`industry_strength._day_local_table` 同款姿势)。"""
    net_by_ind: Dict[str, float] = {}
    for code, net in zip(mf["ts_code"].to_list(), mf["net_amount"].to_list()):
        if net is None:
            continue
        ind = industry_of.get(code)
        if ind:
            net_by_ind[ind] = net_by_ind.get(ind, 0.0) + float(net)
    ordered = sorted(net_by_ind.items(), key=lambda kv: (-kv[1], kv[0]))
    return {ind: rank for rank, (ind, _net) in enumerate(ordered, start=1)}


def _compute_moneyflow_dim(
    day: date, new_direction: Optional[str],
    db_path: Optional[Path], parquet_dir: Optional[Path],
) -> Dict[str, Any]:
    """维 4:行业主力净额排名变化(D0 vs 5 个交易日前)。行业口径与
    `sector_moneyflow.py` 概念口径的差异已在模块 docstring 登记;这里只复用其
    覆盖起始日常量做诚实缺席文案。整维由调用方包保险丝(07-27 教训)。"""
    from neckline.report.sector_moneyflow import MONEYFLOW_COVERAGE_START

    base: Dict[str, Any] = {
        "available": False, "unavailable_reason": "",
        "compare_lag_days": MONEYFLOW_COMPARE_LAG_DAYS,
        "new_direction": new_direction,
        "rank_today": None, "rank_prev": None, "rank_gain": None,
        "industries_ranked": 0,
    }
    mf_today = get_market_slice(day, table="moneyflow_dc", parquet_dir=parquet_dir)
    if mf_today.is_empty():
        base["unavailable_reason"] = (
            f"moneyflow_dc 覆盖仅自 {MONEYFLOW_COVERAGE_START.isoformat()} 起,该日早于覆盖范围,不臆造。"
            if day < MONEYFLOW_COVERAGE_START
            else "moneyflow_dc 当日无数据(数据管线缺口或非交易日),已留空。"
        )
        return base
    compare_days = _recent_trading_days_before(day, MONEYFLOW_COMPARE_LAG_DAYS)
    compare_day = compare_days[0] if compare_days else None
    mf_prev = (
        get_market_slice(compare_day, table="moneyflow_dc", parquet_dir=parquet_dir)
        if compare_day is not None else None
    )
    if mf_prev is None or mf_prev.is_empty():
        base["unavailable_reason"] = (
            f"对比日 {_d(compare_day) if compare_day else '?'} 无 moneyflow_dc 数据,排名变化算不出。"
        )
        return base
    from neckline.report.industry_strength import load_industry_map

    industry_of = load_industry_map(db_path)
    if not industry_of:
        base["unavailable_reason"] = "stock_basic.industry 为空(无行业映射)"
        return base
    rank_today = _industry_net_ranks(mf_today, industry_of)
    rank_prev = _industry_net_ranks(mf_prev, industry_of)
    if not rank_today or not rank_prev:
        base["unavailable_reason"] = "moneyflow_dc 与行业映射零命中,排名算不出。"
        return base
    base["available"] = True
    base["industries_ranked"] = len(rank_today)
    if new_direction is not None:
        rt = rank_today.get(new_direction)
        rp = rank_prev.get(new_direction)
        base["rank_today"] = rt
        base["rank_prev"] = rp
        if rt is not None and rp is not None:
            base["rank_gain"] = rp - rt   # 正 = 名次上升(净流入榜往前挪)
    return base


def _compute_accuracy_dim(day: date, db_path: Optional[Path]) -> Dict[str, Any]:
    """维 5:近期 T1/T2 正确率(冷启动退化源 = `basket_verification` 的 EOD 定论行,
    §七 P3-51;`source` 键如实标 `basket_verification_fallback`)。

    **前视锁在结构上生效**:采样窗口 = `_recent_trading_days_before(D0, 10)`,
    **严格早于 D0**(该表 `trade_date` 天然是 D+1 结案日)—— D0 当天的验证行读不进
    窗口。同一 `(basket_id, trade_date)` 多行(append-only 流水)取 id 最大的一行
    (读侧「最新状态」既有约定)。正确率口径(工程首版,只留痕不进判据):
    verified=1、partial=0.5、falsified=0,unclear 不计入分母(说不清不猜)。
    零样本 → `available=false` + `'clock_samples_insufficient'`,⛔ 不当正确率 0。

    ⚠ **V2.2-④ 起换算搬走了**:`verified=1 / partial=0.5 / falsified=0、unclear 不计`
    这套折算改由 `selection/verification_rules.accuracy_from_counts()` 提供(**取值
    逐位不变**,只是从这里提成单一源)—— 周度「T1/T2 入场信号正确率」要吃同一份定义,
    两处各写一份迟早漂。
    """
    from neckline.selection.verification_rules import accuracy_from_counts
    window = _recent_trading_days_before(day, ACCURACY_WINDOW_DAYS)
    day_strs = [_d(x) for x in window]
    placeholders = ",".join("?" * len(day_strs))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT v.id, v.basket_id, v.trade_date, v.state "
            f"FROM basket_verification v JOIN baskets b ON b.id = v.basket_id "
            f"WHERE v.source='eod' AND b.tier IN (1,2) "
            f"AND v.trade_date IN ({placeholders}) ORDER BY v.id ASC",
            day_strs,
        ).fetchall()
    latest: Dict[Tuple[int, str], str] = {}
    for _row_id, basket_id, td, state in rows:
        latest[(basket_id, td)] = state          # id 升序遍历,后写覆盖 = 取最新
    counts: Dict[str, int] = {}
    for state in latest.values():
        counts[state] = counts.get(state, 0) + 1
    accuracy, denom = accuracy_from_counts(counts)
    return {
        "available": denom > 0,
        "unavailable_reason": "" if denom > 0 else "clock_samples_insufficient",
        "source": "basket_verification_fallback",
        "window_days": ACCURACY_WINDOW_DAYS,
        "samples": denom,
        "verified": counts.get("verified", 0),
        "partial": counts.get("partial", 0),
        "falsified": counts.get("falsified", 0),
        "unclear": counts.get("unclear", 0),
        "accuracy": accuracy,
    }


def _compute_position_quota_entry(
    day: date, parquet_dir: Optional[Path]
) -> Tuple[Dict[str, Any], Optional[int]]:
    """inputs_json 的 `position_quota` 项(plan §五 ② 724 行:仓位额度三态自此成为
    行情状态的一项输入;两者并存、职责不同,⛔ 不合并)。本函数**只读** `compute_
    sentiment` 的产出,零回写。`limit_derived` 当日缺分区时 `compute_sentiment` 会按
    零家数降级计算(它自己的既有取舍)—— 这里如实标 `available=false`,不拿降级值
    冒充「看过了」。返回 `(quota 项, 连板梯队最高高度)`(后者供维 1 留痕)。"""
    from neckline.report.sentiment import compute_sentiment

    lt = get_market_slice(day, table="limit_derived", parquet_dir=parquet_dir)
    dash = compute_sentiment(day, parquet_dir)
    if lt.is_empty():
        return {
            "available": False,
            "unavailable_reason": "limit_derived 当日分区缺失(情绪三态的家数输入不可得,降级值不作数)",
            "quota": dash.position_quota,
            "reason": dash.quota_reason,
        }, None
    return {
        "available": True,
        "unavailable_reason": "",
        "quota": dash.position_quota,
        "reason": dash.quota_reason,
    }, int(dash.max_consec_limit_up)


def _compute_directions(
    day: date, db_path: Optional[Path]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """增强/减弱方向(落 `strengthening_json` / `weakening_json`,**留痕披露件,
    不进三态判据**)。行业口径(`industry_strength_daily`);概念板块刻意不进 ——
    `board_age` 只做板块展示、不做任何判据数据源(CLAUDE.md 明文),留痕件也不用它。

      增强 = 今日强度日的行业,按 (persist_days 升序, industry_rank 升序, 行业名升序)
             取前 N(越新启动越靠前);
      减弱 = 近 10 日(止于 D0−1)≥3 个强度日、今日非强度日、且 5 日中位收益和 < 0
             的行业,按 (收益和升序, 行业名升序) 取前 N。"""
    today_str = _d(day)
    with connection(db_path) as conn:
        today_rows = conn.execute(
            f"SELECT industry, is_strength_day, persist_days, industry_rank, median_ret "
            f"FROM {strength_store.TABLE} WHERE trade_date=?",
            (today_str,),
        ).fetchall()
    strengthening = sorted(
        (
            (persist if persist is not None else 10 ** 6,
             rank if rank is not None else 10 ** 6, industry)
            for industry, is_str, persist, rank, _ret in today_rows if is_str
        ),
    )[:DIRECTIONS_TOP_N]
    strengthening_out = [
        {
            "industry": ind,
            "basis": f"今日强度日(persist_days={p if p < 10 ** 6 else '?'},"
                     f"industry_rank={r if r < 10 ** 6 else '?'})",
        }
        for p, r, ind in strengthening
    ]

    ret_days = _recent_trading_days_before(day, RET_WINDOW_DAYS - 1) + [day]
    ret_day_strs = [_d(x) for x in ret_days]
    mainline_days = _recent_trading_days_before(day, MAINLINE_LOOKBACK_DAYS)
    rows = _strength_rows_for_days(sorted(set(ret_days) | set(mainline_days)), db_path)
    mainline_day_strs = {_d(x) for x in mainline_days}
    ret_by_key: Dict[Tuple[str, str], Optional[float]] = {}
    cnt: Dict[str, int] = {}
    for td, industry, median_ret, is_str in rows:
        ret_by_key[(td, industry)] = median_ret
        if is_str and td in mainline_day_strs:
            cnt[industry] = cnt.get(industry, 0) + 1
    today_strength = {ind for ind, is_str, _p, _r, _ret in today_rows if is_str}
    weakening: List[Tuple[float, str, int]] = []
    for ind, c in cnt.items():
        if c < MAINLINE_MIN_STRENGTH_DAYS or ind in today_strength:
            continue
        r5 = _ret_5d_sum(ind, ret_day_strs, ret_by_key)
        if r5 is not None and r5 < -_EPS:
            weakening.append((r5, ind, c))
    weakening.sort(key=lambda t: (t[0], t[1]))
    weakening_out = [
        {
            "industry": ind,
            "basis": f"近{MAINLINE_LOOKBACK_DAYS}日{c}个强度日,今日非强度日,"
                     f"5日中位收益和{r5:+.4f}",
        }
        for r5, ind, c in weakening[:DIRECTIONS_TOP_N]
    ]
    return strengthening_out, weakening_out


# —————————————————————————————————————————————————————————————————————————————
# 当日全量组装(只读;落表在 regime_store.refresh_market_regime)
# —————————————————————————————————————————————————————————————————————————————

@dataclass
class RegimeDayResult:
    """一日判定的完整产出(纯数据,可逐位比较 —— 前视锁守门单测靠 `==` 断言逐位
    不变,故本类**刻意不含 computed_at**,时间戳由落表层补)。"""

    trade_date: date
    regime: str
    regime_reason: str
    inputs: Dict[str, Any]
    strengthening: List[Dict[str, Any]] = field(default_factory=list)
    weakening: List[Dict[str, Any]] = field(default_factory=list)
    skeleton_version: str = SKELETON_VERSION_FALLBACK


def compute_market_regime_for_day(
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> RegimeDayResult:
    """当日五维采集 + 三态判定(全程只读)。**每一维各自包保险丝**(07-27
    `compute_sector_moneyflow` 裸调教训的推广):单维炸了 → 该维 `available=false` +
    `unavailable_reason='exception:<类名>'`,判定照跑;**少一维是「证据薄」不是
    「判不了」,⛔ 不许因此不产结果**(§七 P3-51 原文)。"""
    init_schema(db_path)
    thresholds, skeleton_version, extra_tokens = resolve_regime_thresholds(
        get_active_skeleton(db_path)
    )

    def _fused(dim_key: str, fn, *args) -> Dict[str, Any]:
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001  保险丝:单维故障不升级为当日无判定
            logger.warning(
                "[regime] %s 维计算异常(保险丝:该维记未取得,判定照跑)",
                dim_key, exc_info=True,
            )
            return {"available": False, "unavailable_reason": f"exception:{type(exc).__name__}"}

    try:
        quota_entry, max_consec = _compute_position_quota_entry(trade_date, parquet_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[regime] position_quota 输入计算异常(保险丝)", exc_info=True)
        quota_entry = {"available": False, "unavailable_reason": f"exception:{type(exc).__name__}"}
        max_consec = None

    core = _fused(DIM_CORE_STRENGTH, _compute_core_strength_dim,
                  trade_date, db_path, parquet_dir, max_consec)
    breadth = _fused(DIM_BREADTH, _compute_breadth_dim, trade_date, db_path, parquet_dir)
    rel = _fused(DIM_RELATIVE_STRENGTH, _compute_relative_strength_dim, trade_date, db_path)
    moneyflow = _fused(DIM_MONEYFLOW, _compute_moneyflow_dim,
                       trade_date, rel.get("new_direction"), db_path, parquet_dir)
    accuracy = _fused(DIM_ACCURACY, _compute_accuracy_dim, trade_date, db_path)

    inputs: Dict[str, Any] = {
        DIM_CORE_STRENGTH: core,
        DIM_BREADTH: breadth,
        DIM_RELATIVE_STRENGTH: rel,
        DIM_MONEYFLOW: moneyflow,
        DIM_ACCURACY: accuracy,
        "position_quota": quota_entry,
    }
    missing = [k for k in DIM_ORDER if not inputs[k].get("available")]

    regime, reason = decide_regime(
        old_mainline_ret_5d=rel.get("old_ret_5d_sum"),
        new_direction_ret_5d=rel.get("new_ret_5d_sum"),
        new_direction_strength_days_3d=rel.get("new_strength_days_3d"),
        new_direction_rank_gain=moneyflow.get("rank_gain"),
        core_drop_from_avg5=core.get("drop_from_avg5"),
        breadth_pctile=breadth.get("pctile"),
        limit_up_drop_ratio=breadth.get("limit_up_drop_ratio"),
        thresholds=thresholds,
        missing_dims=missing,
        extra_reason_tokens=extra_tokens,
    )
    try:
        strengthening, weakening = _compute_directions(trade_date, db_path)
    except Exception:  # noqa: BLE001  留痕件炸了不连累判定
        logger.warning("[regime] 增强/减弱方向计算异常(保险丝:两列留空)", exc_info=True)
        strengthening, weakening = [], []
    return RegimeDayResult(
        trade_date=trade_date,
        regime=regime,
        regime_reason=reason,
        inputs=inputs,
        strengthening=strengthening,
        weakening=weakening,
        skeleton_version=skeleton_version,
    )


__all__ = [
    "TREND_CONTINUATION",
    "HIGH_DIVERGENCE",
    "ROTATION_CONFIRMED",
    "REGIME_ORDER",
    "REGIME_LABELS",
    "DIM_CORE_STRENGTH",
    "DIM_BREADTH",
    "DIM_RELATIVE_STRENGTH",
    "DIM_MONEYFLOW",
    "DIM_ACCURACY",
    "DIM_ORDER",
    "REGIME_THRESHOLD_DEFAULTS",
    "SKELETON_VERSION_FALLBACK",
    "RegimeDayResult",
    "resolve_regime_thresholds",
    "decide_regime",
    "compute_market_regime_for_day",
]
