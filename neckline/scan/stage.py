"""行业题材阶段六态状态机预计算表 `industry_stage_daily`(plan §五 V2-④b,K7 需求
1b,§七 **P0-23**:EOD 预计算落表、在线只读)。

**为什么有这张表**:`report/holding_k4_check.py` 的 A2/B3、`intel_candidates.py`
候选安检、问询台三处判据早已把「题材持续天数」的唯一源钉死在
`report/industry_strength.py::stock_persist_days`(单调的连续强度日计数)。K7 交接
稿需求 1b 要求把未来 ⑥ 的 `driver_freshness` 维度换成**更细的六态状态机**(启动/
发酵/过热/分歧回调/退潮/无题材)—— 状态机需要「近 N 日是否有强度日」+「当日涨停
家数」两个额外输入,与 `industry_strength_daily` 的口径指纹(quantile/min_members)
不同源,故 planner 定为**新表**而不是给 `industry_strength_daily` 加列(三条理由见
plan §五 ④b-B docstring 引用,不重复贴)。

**六态判据(唯一定义 = `decide_stage()`,互斥、优先级从上往下,`stage` 恒非
NULL)**:

    ignition      强度日 且 persist_days == 1
    fermentation  强度日 且 persist_days ∈ [2, 3]
    overheat      强度日 且 persist_days >= 4
    divergence    非强度日 且 近 2 个交易日内有强度日 且 当日涨停家数 >= 1
    ebb           非强度日 且 近 5 个交易日内有强度日 且 当日涨停家数 == 0
    none          其余(含全部"缺数不猜"的落点,`stage_reason` 逐条说明具体原因)

**单一源纪律(本模块不重算,只读)**:
    · 「强度日」判据与 `persist_days` 直接读 `industry_strength_daily`(`report/
      industry_strength.py` 唯一源的既有物化)—— 本模块**不重新实现强度日判定,
      也绝不写 / 改动那张表**,同族口径不重复第二份。
    · 「当日该行业涨停家数」读 `data/limit_derived.py` 的 `is_limit_up`(唯一源,
      禁自己乘系数);行业归属读 `stock_basic.industry`
      (`report/industry_strength.py::load_industry_map`,与 `industry_strength_daily`
      同一份行业映射,不混用概念板块——项目 CLAUDE.md 明文:行业一对一 vs 概念
      板块多对多是两个不同的量)。
    · 「近 N 个交易日内是否有强度日」**读本表自己已落好的 `is_strength_day`
      历史**(不是重新查 `industry_strength_daily` 的多日窗口)—— 同
      `industry_strength_store.py::_prev_persist` 的既有设计精神(跨日的量靠
      "读自己表的过去"而不是重新扫源头);日更增量因此每天只需要:
      ①`industry_strength_daily` 当日一行(indexed 查询,非 parquet 扫描);
      ②`limit_derived` 当日一个分区;③本表自己过去 `EBB_LOOKBACK_DAYS` 个交易日
      的既有行(indexed 查询)。全程不扫任何全历史。

**"没有"与"没看"分三层,刻意不合并(§3.8)**:
    ①**当日整批缺行**——`industry_strength_daily` 当日一行都没有(如该表当日
      更新失败)→ 本表当日**不落任何行**(对任何行业都没有)。这是真正的"缺行",
      留给 `industry_stage_status()` 的新鲜度三键如实披露,不许猜一个 'none' 出来
      冒充"算过了"。
    ②**该行业当日有源数据但"判据缺数"**——`industry_strength_daily` 该行业当日
      `is_strength_day IS NULL`(成员数 <5 未评级),或 `limit_derived` 当日分区
      整体缺失(算不出涨停家数)→ 本表**照样落一行**,`stage='none'`,但
      `stage_reason` 如实点名是哪一种缺数,不是"真的无题材"的同义反复。
    ③**评估完毕、确实不构成任何题材阶段**——强度日=False 且分歧回调/退潮两个
      条件都不满足 → `stage='none'`,`stage_reason` 说明具体不满足哪个条件。
    ②③的 `stage` 存储值相同(都是字面量 `'none'`,区别信息在 `stage_reason`
    里),但①与②③的区别是**行存在与否**——`load_industry_stage()` 对①返回的
    结果里根本不含该交易日,对②③则有行。

**`is_strength_day` 列 NOT NULL 0/1 是有损但故意的简化**:落表时把源头的
`None`(未评级)与 `False`(评了、不是)**都**存成 `0`(DDL 注释「留痕供复现」)。
这一列落表的用途是给**未来天**的"近 N 日有强度日"窗口查询当布尔证据用——
"未评级"与"评了否"在这个用途下效果相同(两者都不贡献"有强度证据"),合并存储
不算丢关键信息;真正需要区分"未评级 vs 评了否"的地方只有**当天自己**要不要
进入 divergence/ebb 判定,这个区别只活在写入那一刻的 `decide_stage()` 调用里
(`is_strength_day` 形参是三态 `Optional[bool]`),不需要、也无法从存储读回。
`verify_industry_stage()` 的自洽检查因此天然只覆盖"给定当前存储字段能否推出
当前存储 stage",不覆盖"最初写入时 None/False 判断对不对"——后者属于单测对
`decide_stage()`/`refresh_industry_stage()` 的直接覆盖,不是 verify() 的职责。

**中英映射唯一源** = 本模块 `STAGE_LABELS`;报告 / 客户端只读它,禁在别处抄第二份
中文表(同 `CandidateOut.board` 先例,项目 CLAUDE.md 明文)。

**"两遍法"的如实departure(同 V2-④ 自己「如实登记」#5 的既有先例)**:plan 原文
建议 `scripts/industry_stage.py bootstrap` 照 `scripts/industry_strength.py` 体例
做"两遍法"。本表**没有** `persist_days` 那种跨日无界递推状态——它唯一的跨日依赖
是一个固定 `EBB_LOOKBACK_DAYS`(=5)日回看窗口,且读的是**本表自己**的历史。
按升序逐日处理天然满足"批 == 逐日"(与 V2-④ 的 `cluster.py`/`corr.py`/
`leader.py` 同一分工:批量与逐日是同一份"逐日循环"实现,由三路等价单测证明
逐位相同),故 `refresh_industry_stage()` **不做两遍法**,CLI 的 `bootstrap` 子
命令与 `refresh` 共用同一实现(同 `scripts/scan_layer.py` 的既有先例)。

**局限如实登记(未采纳自动向前级联,与 `persist_days` 的自动补洞机制不同)**:
若事后回填 / 纠正某个历史日的行,本模块**不会**像
`industry_strength_store._resolve_targets` 那样自动把处理区间向后延——受影响的
只是该日之后最多 `EBB_LOOKBACK_DAYS` 天的 divergence/ebb 判断(blast radius 有界,
不像 `persist_days` 那样无界传播,量级不同)。`verify_industry_stage()` 的②自洽
检查会用**当前**存储值重新核对,能发现这种滞后不一致;运维据此对受影响的后续
几天手动重跑 `refresh` 即可,本块未自动化这一步,留作未来如认为有必要再补。
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import polars as pl

from neckline.data.market_data import day_file_path, get_market_slice
from neckline.db import connection, init_schema
from neckline.report import industry_strength_store as strength_store
from neckline.report.industry_strength import (
    _MIN_MEMBERS,
    _STRENGTH_QUANTILE,
    load_industry_map,
)

logger = logging.getLogger(__name__)

TABLE = "industry_stage_daily"

# —— 六态英文码(唯一源;库列值与 config 键同源,理由照 `CandidateOut.board` 先例)——
IGNITION = "ignition"
FERMENTATION = "fermentation"
OVERHEAT = "overheat"
DIVERGENCE = "divergence"
EBB = "ebb"
NONE_STAGE = "none"

# 优先级从上往下(仅供文档 / 展示用;`decide_stage()` 的判据本身已经互斥,不依赖
# 遍历这个元组)。
STAGE_ORDER: Tuple[str, ...] = (IGNITION, FERMENTATION, OVERHEAT, DIVERGENCE, EBB, NONE_STAGE)

# 中英映射唯一源(见模块 docstring)。
STAGE_LABELS: Dict[str, str] = {
    IGNITION: "启动",
    FERMENTATION: "发酵",
    OVERHEAT: "过热",
    DIVERGENCE: "分歧回调",
    EBB: "退潮",
    NONE_STAGE: "无题材",
}

# 「近 N 个交易日内有强度日」的两个窗口常量(④b-A 定死,不是策略参数——事实表
# 引擎常量,同 `industry_strength.py::_MIN_MEMBERS` 的既有分工)。
DIVERGENCE_LOOKBACK_DAYS = 2
EBB_LOOKBACK_DAYS = 5

# 口径指纹:强度日判据(复用 industry_strength 单一源的 q/成员数下限)+ 本表自己
# 的两个窗口常量,序列化成一个字符串列(`spec_fingerprint`,DDL 定死单列非多列)。
SPEC_FINGERPRINT = (
    f"q={_STRENGTH_QUANTILE}|min_members={_MIN_MEMBERS}"
    f"|divergence_window={DIVERGENCE_LOOKBACK_DAYS}|ebb_window={EBB_LOOKBACK_DAYS}"
)

_COLUMNS = (
    "trade_date, industry, stage, is_strength_day, persist_days, limit_up_count, "
    "member_count, stage_reason, spec_fingerprint, computed_at"
)
_UPSERT_SQL = f"INSERT OR REPLACE INTO {TABLE} ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?)"

_ROW_SCHEMA: Dict[str, Any] = {
    "trade_date": pl.String,
    "industry": pl.String,
    "stage": pl.String,
    "is_strength_day": pl.Int64,
    "persist_days": pl.Int64,
    "limit_up_count": pl.Int64,
    "member_count": pl.Int64,
    "stage_reason": pl.String,
    "spec_fingerprint": pl.String,
    "computed_at": pl.String,
}


def _d(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_d(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def refresh_command_hint(start: Optional[date] = None, end: Optional[date] = None) -> str:
    """补算命令**原文**(单一源,同 `industry_strength_store.refresh_command_hint`
    先例)。"""
    if start is None and end is None:
        return "python scripts/industry_stage.py refresh"
    a = _d(start) if start else _d(end)      # type: ignore[arg-type]
    b = _d(end) if end else _d(start)        # type: ignore[arg-type]
    return f"python scripts/industry_stage.py refresh --from {a} --to {b}"


# —————————————————————————————————————————————————————————————————————————————
# 六态判据(唯一定义,纯函数)
# —————————————————————————————————————————————————————————————————————————————

def decide_stage(
    *,
    is_strength_day: Optional[bool],
    persist_days: Optional[int],
    limit_up_count: Optional[int],
    recent_divergence_has_strength: bool,
    recent_ebb_has_strength: bool,
    member_count: Optional[int] = None,
) -> Tuple[str, str]:
    """六态判据唯一实现(④b-A,互斥、优先级从上往下)。`(stage, stage_reason)`。

    所有决策输入都可能是"缺数"(`None`)——本函数从不猜测,任何一环缺数都安全落到
    `none` 且 `stage_reason` 如实写明原因(不是"无题材"的同义反复)。`member_count`
    仅用于丰富 `stage_reason` 文案(审计可读性),不参与判据本身(判据已经通过
    `is_strength_day is None` 完整表达"未评级")。
    """
    if is_strength_day is True:
        if persist_days == 1:
            return IGNITION, "强度日且 persist_days=1"
        if persist_days is not None and 2 <= persist_days <= 3:
            return FERMENTATION, f"强度日且 persist_days={persist_days}"
        if persist_days is not None and persist_days >= 4:
            return OVERHEAT, f"强度日且 persist_days={persist_days}"
        return NONE_STAGE, (
            f"强度日但 persist_days={persist_days!r} 与 industry_strength_daily 不自洽"
            "(反常,按缺数处理,不猜属于哪一态)"
        )
    if is_strength_day is False:
        if limit_up_count is None:
            return NONE_STAGE, (
                "非强度日,但当日涨停家数算不出(limit_derived 当日分区缺失),"
                "不猜分歧回调/退潮"
            )
        if recent_divergence_has_strength and limit_up_count >= 1:
            return DIVERGENCE, (
                f"非强度日,近 {DIVERGENCE_LOOKBACK_DAYS} 个交易日内有强度日,"
                f"当日该行业涨停 {limit_up_count} 家"
            )
        if recent_ebb_has_strength and limit_up_count == 0:
            return EBB, f"非强度日,近 {EBB_LOOKBACK_DAYS} 个交易日内有强度日,当日零涨停"
        return NONE_STAGE, (
            f"非强度日,不满足分歧回调/退潮条件(近{DIVERGENCE_LOOKBACK_DAYS}日有强度="
            f"{recent_divergence_has_strength},近{EBB_LOOKBACK_DAYS}日有强度="
            f"{recent_ebb_has_strength},当日涨停{limit_up_count}家)"
        )
    mc_note = f",member_count={member_count}" if member_count is not None else ""
    return NONE_STAGE, (
        f"强度日判据缺数(industry_strength_daily 当日无该行业评级或无该行{mc_note})"
    )


# —————————————————————————————————————————————————————————————————————————————
# 近 N 日回看(读本表自己的历史,不重查 industry_strength_daily)
# —————————————————————————————————————————————————————————————————————————————

def _recent_trading_days_before(d: date, n: int) -> List[date]:
    """`d` 严格之前最近 `n` 个交易日,升序。"""
    from neckline.calendar import prev_trading_day

    out: List[date] = []
    cur = d
    for _ in range(n):
        cur = prev_trading_day(cur)
        out.append(cur)
    return list(reversed(out))


def _load_recent_strength_flags(
    industries: Iterable[str], d: date, *, db_path: Optional[Path] = None
) -> Dict[str, Tuple[bool, bool]]:
    """`industry -> (近 DIVERGENCE_LOOKBACK_DAYS 日有强度日, 近 EBB_LOOKBACK_DAYS 日有
    强度日)`。读**本表自己**已算好的 `is_strength_day` 历史(见模块 docstring「单一源
    纪律」第三条),`d` 之前最近 `EBB_LOOKBACK_DAYS` 个交易日;某天该行业缺行按
    "不贡献"处理(存在性证据,不是全知——缺的那天既不证明强也不证明弱)。"""
    industries = list(industries)
    recent_days = _recent_trading_days_before(d, EBB_LOOKBACK_DAYS)
    if not recent_days:
        return {ind: (False, False) for ind in industries}
    divergence_days = {_d(x) for x in recent_days[-DIVERGENCE_LOOKBACK_DAYS:]}
    day_strs = [_d(x) for x in recent_days]
    placeholders = ",".join("?" * len(day_strs))
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, industry, is_strength_day FROM {TABLE} "
            f"WHERE trade_date IN ({placeholders})",
            day_strs,
        ).fetchall()
    has_ebb: Dict[str, bool] = {}
    has_div: Dict[str, bool] = {}
    for td, industry, is_str in rows:
        if not is_str:
            continue
        has_ebb[industry] = True
        if td in divergence_days:
            has_div[industry] = True
    return {ind: (has_div.get(ind, False), has_ebb.get(ind, False)) for ind in industries}


# —————————————————————————————————————————————————————————————————————————————
# 当日原始输入的读取(industry_strength_daily 当日全部行 + limit_derived 当日涨停家数)
# —————————————————————————————————————————————————————————————————————————————

def _strength_today_rows(
    trade_date: date, db_path: Optional[Path]
) -> List[Tuple[str, Optional[bool], Optional[int], Optional[int]]]:
    """`industry_strength_daily` 当日**全部**行(含未达标 / member<5 的行,
    `is_strength_day` 为 NULL)—— 这是"全部行业"宇宙的唯一来源。**不走**
    `industry_strength_store.load_industry_strength()`(那是在线判据的唯一入口,
    但按契约过滤掉 `industry_rank IS NULL` 的行,会漏掉未达标行业;④b 需要落
    全部行业含 `stage='none'`,同 `industry_strength_daily` 自己的既有取舍)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT industry, is_strength_day, persist_days, member_count "
            f"FROM {strength_store.TABLE} WHERE trade_date=?",
            (_d(trade_date),),
        ).fetchall()
    return [
        (industry, None if is_str is None else bool(is_str), persist, member_count)
        for industry, is_str, persist, member_count in rows
    ]


def _limit_up_counts_for_day(
    trade_date: date, industry_of: Dict[str, str], parquet_dir: Optional[Path]
) -> Optional[Dict[str, int]]:
    """当日各行业涨停家数(唯一源 `data/limit_derived.py` 的 `is_limit_up` 列)。

    分区文件**不存在** / `industry_of` 为空(无行业映射)→ `None`(整天算不出,
    禁猜 0——见模块 docstring);分区存在但某行业零命中 → 该行业不出现在返回
    字典里,调用方 `.get(industry, 0)` 取 0,这是合法的"零涨停",不是缺数。"""
    if not industry_of:
        return None
    if not day_file_path("limit_derived", trade_date, parquet_dir).exists():
        return None
    limit_today = get_market_slice(trade_date, table="limit_derived", parquet_dir=parquet_dir)
    if limit_today.is_empty():
        return {}
    up = limit_today.filter(pl.col("is_limit_up"))
    if up.is_empty():
        return {}
    counts: Dict[str, int] = {}
    for code in up["ts_code"].to_list():
        ind = industry_of.get(code)
        if ind:
            counts[ind] = counts.get(ind, 0) + 1
    return counts


# —————————————————————————————————————————————————————————————————————————————
# 纯函数:当日原始输入 → 落表行(无 I/O,单测直接喂标量输入用这层)
# —————————————————————————————————————————————————————————————————————————————

def compute_industry_stage_for_day(
    trade_date: date,
    strength_rows: List[Tuple[str, Optional[bool], Optional[int], Optional[int]]],
    limit_up_by_industry: Optional[Dict[str, int]],
    recent_flags_by_industry: Dict[str, Tuple[bool, bool]],
) -> List[Tuple]:
    """纯函数(无 I/O):当日原始输入 → 落表行元组列表(顺序 = `_COLUMNS`)。
    `refresh_industry_stage()` 与单测共用本函数——单测可以完全绕开 SQLite /
    parquet,直接喂原始输入精确覆盖 `decide_stage()` 的每条边界。"""
    day_s = _d(trade_date)
    now = _now()
    out: List[Tuple] = []
    for industry, is_str, persist, member_count in strength_rows:
        lu = None if limit_up_by_industry is None else limit_up_by_industry.get(industry, 0)
        rec_div, rec_ebb = recent_flags_by_industry.get(industry, (False, False))
        stage, reason = decide_stage(
            is_strength_day=is_str,
            persist_days=persist,
            limit_up_count=lu,
            recent_divergence_has_strength=rec_div,
            recent_ebb_has_strength=rec_ebb,
            member_count=member_count,
        )
        out.append((
            day_s, industry, stage,
            1 if is_str else 0,
            None if persist is None else int(persist),
            lu,
            None if member_count is None else int(member_count),
            reason, SPEC_FINGERPRINT, now,
        ))
    return out


# —————————————————————————————————————————————————————————————————————————————
# 写侧:日更增量 == bootstrap(同一实现,见模块 docstring「两遍法的如实 departure」)
# —————————————————————————————————————————————————————————————————————————————

def refresh_industry_stage(
    days: Iterable[date],
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """批算 + upsert 落表(日更增量与历史 bootstrap 共用同一入口,见模块 docstring)。

    **升序逐日处理**(下一天的近 N 日回看依赖本表刚写完的上一天):每天①查
    `industry_strength_daily` 当日全部行(indexed SQL);②读 `limit_derived` 当日
    一个分区算涨停家数;③查本表自己过去 `EBB_LOOKBACK_DAYS` 天的既有行算回看
    旗标;④`decide_stage()` 逐行业判定;⑤`INSERT OR REPLACE`(每天一个事务,
    不拿大事务锁库,同 `industry_strength_store.refresh_industry_strength` 体例)。

    `industry_strength_daily` 当日**一行都没有** → 本表当日**不落任何行**(真
    「缺行」,不猜;计入 `missing_source`)。返回
    `{"days": 处理天数, "rows": 落行数, "missing_source": 当日源表无行的天数}`。"""
    init_schema(db_path)
    stats = {"days": 0, "rows": 0, "missing_source": 0}
    for d in sorted(set(days)):
        stats["days"] += 1
        strength_rows = _strength_today_rows(d, db_path)
        if not strength_rows:
            stats["missing_source"] += 1
            continue
        industry_of = load_industry_map(db_path)
        limit_up = _limit_up_counts_for_day(d, industry_of, parquet_dir)
        industries = [r[0] for r in strength_rows]
        recent_flags = _load_recent_strength_flags(industries, d, db_path=db_path)
        payload = compute_industry_stage_for_day(d, strength_rows, limit_up, recent_flags)
        with connection(db_path) as conn:
            conn.executemany(_UPSERT_SQL, payload)
        stats["rows"] += len(payload)
    return stats


# —————————————————————————————————————————————————————————————————————————————
# 读侧(在线唯一入口,不现算自愈)
# —————————————————————————————————————————————————————————————————————————————

def _fingerprint_ok(fp: Optional[str]) -> bool:
    return fp == SPEC_FINGERPRINT


def load_industry_stage(trade_date: date, *, db_path: Optional[Path] = None) -> pl.DataFrame:
    """在线唯一读入口:给定交易日全部行业的阶段行(空 = 当日缺行,合法结果,走
    保险丝——不现算自愈)。口径指纹不匹配的行视同缺行 + WARNING(同
    `industry_strength_store.load_industry_strength` 先例)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM {TABLE} WHERE trade_date=?", (_d(trade_date),)).fetchall()
    if not rows:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    kept = [r for r in rows if _fingerprint_ok(r[8])]
    stale = len(rows) - len(kept)
    if stale:
        logger.warning(
            "行业题材阶段口径已变更:%s 有 %d 行的 spec_fingerprint 与现行常量(%s)不符,"
            "已视同缺行。请重跑 bootstrap 整表重算:%s",
            _d(trade_date), stale, SPEC_FINGERPRINT,
            "python scripts/industry_stage.py bootstrap",
        )
    if not kept:
        return pl.DataFrame(schema=_ROW_SCHEMA)
    return pl.DataFrame(kept, schema=_ROW_SCHEMA, orient="row")


def stage_lookup(rows: pl.DataFrame) -> Dict[str, str]:
    """`industry -> stage`,给未来 ⑥ 消费用的便捷查表(同
    `industry_strength_lookup`/`sector_hot_lookup` 先例)。"""
    if rows.is_empty():
        return {}
    return dict(zip(rows["industry"].to_list(), rows["stage"].to_list()))


# —————————————————————————————————————————————————————————————————————————————
# 新鲜度(→ 未来 `ReportOut.dataFreshness` 的三个新键;本块只提供函数,不接线
# `report/pipeline.py`——同 V2-④ `freshness.py` 的既有分工,接线留给未来实际消费
# 本表的块)
# —————————————————————————————————————————————————————————————————————————————

# `industryStageLagDays` 的哨兵值:表内**完全没有**任何行(同
# `industry_strength_store.INDUSTRY_STRENGTH_LAG_UNKNOWN`/
# `scan.freshness.SCAN_LAYER_LAG_UNKNOWN` 既定惯例)。
INDUSTRY_STAGE_LAG_UNKNOWN = -1


@dataclass
class IndustryStageFreshness:
    """行业题材阶段新鲜度(→ 未来 `dataFreshness` 三键,与既有板块三键 / 行业强度
    三键 / ④ 的 scanLayer 三键**一律不合并**,④b-C 明文)。"""

    latest_date: str    # 'YYYYMMDD';完全无数据 → ""
    lag_days: int
    stale: bool

    @property
    def unavailable(self) -> bool:
        return self.lag_days == INDUSTRY_STAGE_LAG_UNKNOWN

    def to_public_dict(self) -> Dict[str, object]:
        return {
            "industryStageDate": self.latest_date or None,
            "industryStageLagDays": self.lag_days,
            "industryStageStale": self.stale,
        }

    def note(self) -> str:
        if self.unavailable:
            return (
                "行业题材阶段数据未就绪(表内无任何数据)——driver_freshness 六态"
                "本日不可得,按中性分处理且不拦。"
            )
        if self.lag_days > 0:
            return (
                f"行业题材阶段数据未就绪(最新至 {self.latest_date},落后 {self.lag_days} "
                "个交易日)——driver_freshness 六态本日不可得,按中性分处理且不拦。"
            )
        return ""

    def latest_label(self) -> str:
        return self.latest_date or "无数据"


def industry_stage_status(report_date: date, *, db_path: Optional[Path] = None) -> IndustryStageFreshness:
    """表内最新阶段日相对报告日落后几个交易日(零容忍,同
    `industry_strength_status` 既定口径:16:05 批算当天就该有,不给缓冲)。"""
    from neckline.calendar import trading_days_between

    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT MAX(trade_date) FROM {TABLE}").fetchone()
    newest_s = row[0] if row else None
    if not newest_s:
        return IndustryStageFreshness("", INDUSTRY_STAGE_LAG_UNKNOWN, True)
    newest = _parse_d(newest_s)
    if newest >= report_date:
        return IndustryStageFreshness(newest_s, 0, False)
    lag = max(len(trading_days_between(newest, report_date)) - 1, 0)
    return IndustryStageFreshness(newest_s, lag, lag > 0)


# —————————————————————————————————————————————————————————————————————————————
# 自检(CLI `verify` 与单测共用同一实现,④b-C 验证判据:①交易日无洞 ②五态判据
# 自洽 ③口径指纹一致)
# —————————————————————————————————————————————————————————————————————————————

def _self_consistency_errors(db_path: Optional[Path], lo: date, hi: date) -> List[str]:
    """②五态判据自洽:用**本表自己**存的字段(`is_strength_day`/`persist_days`/
    `limit_up_count`)+ 由本表历史重建的近 N 日回看旗标,重新跑一遍 `decide_stage()`,
    与库内存的 `stage` 比对。**不重新查 `industry_strength_daily`/`limit_derived`**
    ——那是"对源表重新计算"的三路等价范畴,不是这里的"自洽"范畴(模块 docstring
    已说明两者边界)。

    与 `industry_strength_store.verify_industry_strength` 的②同款处理"窗口首日
    依赖窗口之前历史"的问题:读**全部** `trade_date<=hi` 的行来重建每个行业的
    `is_strength_day` 历史,只对 `trade_date>=lo` 的行下断言。"""
    lo_s, hi_s = _d(lo), _d(hi)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, industry, stage, is_strength_day, persist_days, limit_up_count "
            f"FROM {TABLE} WHERE trade_date<=? ORDER BY industry ASC, trade_date ASC",
            (hi_s,),
        ).fetchall()
    is_str_by_key: Dict[Tuple[str, str], bool] = {
        (td, industry): bool(is_str) for td, industry, _, is_str, _, _ in rows
    }
    errors: List[str] = []
    for td, industry, stage, is_str, persist, lu in rows:
        if td < lo_s:
            continue
        d = _parse_d(td)
        recent_days = [_d(x) for x in _recent_trading_days_before(d, EBB_LOOKBACK_DAYS)]
        rec_div = any(is_str_by_key.get((rd, industry), False) for rd in recent_days[-DIVERGENCE_LOOKBACK_DAYS:])
        rec_ebb = any(is_str_by_key.get((rd, industry), False) for rd in recent_days)
        expected_stage, _ = decide_stage(
            is_strength_day=bool(is_str),
            persist_days=persist,
            limit_up_count=lu,
            recent_divergence_has_strength=rec_div,
            recent_ebb_has_strength=rec_ebb,
        )
        if expected_stage != stage:
            errors.append(
                f"{TABLE} {td}/{industry}: 存储 stage={stage!r},按存量字段重算应为 {expected_stage!r}"
            )
    return errors


def verify_industry_stage(
    start: Optional[date] = None, end: Optional[date] = None, *, db_path: Optional[Path] = None
) -> Dict[str, Any]:
    """三项自检(④b-C):①交易日无洞 ②五态判据自洽 ③口径指纹一致。CLI 与单测
    共用同一实现(同 `verify_industry_strength`/`verify_scan_layer` 体例)。"""
    from neckline.calendar import trading_days_between

    init_schema(db_path)
    with connection(db_path) as conn:
        bounds = conn.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {TABLE}").fetchone()
        if not bounds or bounds[0] is None:
            return {
                "ok": False, "rows": 0, "reason": "表为空(未 bootstrap / 未日更)",
                "missing_days": [], "extra_days": [], "self_consistency_errors": [],
                "bad_fingerprints": [],
            }
        lo = start or _parse_d(bounds[0])
        hi = end or _parse_d(bounds[1])
        lo_s, hi_s = _d(lo), _d(hi)
        have_days = {
            r[0] for r in conn.execute(
                f"SELECT DISTINCT trade_date FROM {TABLE} WHERE trade_date>=? AND trade_date<=?",
                (lo_s, hi_s),
            )
        }
        n_rows = conn.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE trade_date>=? AND trade_date<=?", (lo_s, hi_s)
        ).fetchone()[0]
        bad_fp_rows = conn.execute(
            f"SELECT DISTINCT spec_fingerprint FROM {TABLE} WHERE trade_date>=? AND trade_date<=? "
            f"AND spec_fingerprint!=?",
            (lo_s, hi_s, SPEC_FINGERPRINT),
        ).fetchall()

    cal_days = {_d(x) for x in trading_days_between(lo, hi)}
    missing_days = sorted(cal_days - have_days)
    extra_days = sorted(have_days - cal_days)
    self_consistency = _self_consistency_errors(db_path, lo, hi)
    bad_fingerprints = [r[0] for r in bad_fp_rows]

    ok = not missing_days and not extra_days and not self_consistency and not bad_fingerprints
    return {
        "ok": ok,
        "range": [lo_s, hi_s],
        "rows": int(n_rows),
        "days": len(have_days),
        "missing_days": missing_days,
        "extra_days": extra_days,
        "self_consistency_errors": self_consistency,
        "bad_fingerprints": bad_fingerprints,
    }


__all__ = [
    "TABLE",
    "IGNITION",
    "FERMENTATION",
    "OVERHEAT",
    "DIVERGENCE",
    "EBB",
    "NONE_STAGE",
    "STAGE_ORDER",
    "STAGE_LABELS",
    "DIVERGENCE_LOOKBACK_DAYS",
    "EBB_LOOKBACK_DAYS",
    "SPEC_FINGERPRINT",
    "decide_stage",
    "compute_industry_stage_for_day",
    "refresh_industry_stage",
    "refresh_command_hint",
    "load_industry_stage",
    "stage_lookup",
    "INDUSTRY_STAGE_LAG_UNKNOWN",
    "IndustryStageFreshness",
    "industry_stage_status",
    "verify_industry_stage",
]
