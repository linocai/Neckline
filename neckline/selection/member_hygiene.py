"""成员卫生线闸(plan §五 V2-⑤-b,2026-08-02 planner 裁定追加子项)。

**背景**:⑤(`neckline/selection/aggregate.py`)完工时疏漏了成员卫生线——篮子成员
可能含 ST / 次新 / 流动性差 / 命中 K4 hard_cut 的票。planner 裁定补在本块,**判据
一律复用 ③ 的三个原语(`stock_hygiene` / `non_new_stock` / `k4_advisory_gate`)
读现役包参数,不自造第二份判据**。

**落点**:调用方(`aggregate.py::aggregate_baskets`)必须在「装配给 LLM 看的成员
清单」之前(`MAX_MEMBERS_IN_CONTEXT` 截断之前)调用 `apply_member_hygiene()`,
对**候选成员集**(本日全部种子成分的并集)一次性算好、全篮复用——本模块只提供
纯函数入口,不管调用时机,时机由调用方保证(⛔ 不许每篮各调一次)。

**两级保险丝(定死,别一刀切)**:
    · **便宜的硬风险永远拦**:ST / 停牌 / 次新 / 板块不在 `allowed_boards`。数据源是
      `sentinel.universe.load_stock_meta`(`stock_basic`)+ `suspend_d` 分区,便宜且
      必在——**算不出就是异常,该码保守拒收**(fail closed,不放行)。
    · **贵的趋势/流动性线算不出才降级**:`ma20` / `amount_ma20` / `close` 任一为
      `None`(整体读取失败、某码当日查无该行、或有行但该字段是 null)→ **该维度
      降级为不拦**(放行)+ `MemberHygieneResult.hygiene_unavailable=True` 如实
      披露(P0-23「降级=不拦 + 显式披露」定案)。**⛔ 不许静默当作"都合格"**。
      ⚠ **"有行但字段 null"与"整行缺失"刻意不区分、同等处理**(与最初设计不同,
      如实登记):`non_new_stock` 的 `min_days=120` 自然日门槛远严于 ma20 需要的
      20 个交易日——能过 `non_new_stock` 却 `ma20` 仍是 null,现实里代表的是
      **数据缺口**(该码窗口内交易日不足但已上市 120+ 自然日),不是"真的太新"
      (真太新的票已被 `non_new_stock` 拦下,不需要 `stock_hygiene` 再判一次);
      按数据缺口处理才与 P0-23「降级=不拦」的立法原意一致,也不会让"测试只喂了
      一两天历史"这种夹具局限被误判成"真的不达标"。
    · **K4 安检**同样"贵"(需要 ~420 自然日窗口的价量特征镜像,复用
      `report.holding_k4_check` 既有机械镜像),整体算不出 → 该维度降级为
      `k4_section=None`(`k4_advisory_gate` 原语对 `None` 天然放行,无需额外分支)+
      `MemberHygieneResult.k4_unavailable=True`。**plan 原文只点名 ma20/amount_ma20
      这一档"贵而降级"**,K4 未点名——本模块按同一 P0-23 降级哲学类推,如实登记为
      builder 判断(与 ma20/amount_ma20 分开计数,便于日后核实是否要单独处理)。

`hard_cut_action='exclude'` → 剔出成员清单(不进 `kept`);`avoid_flag_action='tag'`
→ 保留但打标(`k4_tag_of[code]='avoid_flag'`),标随成员传给调用方(未来 ⑦ 卡面
展示 + ⑥ `card_density` 维度消费,本块只负责把标算出来、放进返回值)。

被剔票留痕(`MemberHygieneResult.rejected`),与 `aggregate.py` 的机械闸拒收
(`RejectedProposal`)**分开计数**——两种"没进来"语义不同:一个是"这只票不干净"，
一个是"这条 LLM 建议不可信"。
"""

from __future__ import annotations

import glob
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Optional, Sequence, Tuple

import polars as pl

from neckline.data.market_data import table_dir
from neckline.selection.pack import Pack
from neckline.selection.primitives import PRIMITIVES
from neckline.sentinel.universe import load_stock_meta
from neckline.strategy.features import build_research_panel

logger = logging.getLogger(__name__)

# 拒收原语标签(**语义不合并**,每一种"为什么没进来"要能分开查;不是 DDL 枚举,只是
# 留痕文案的分类键)。
REJECT_SUSPENDED = "suspended"
REJECT_META_MISSING = "meta_missing"
REJECT_NON_NEW_STOCK = "non_new_stock"
REJECT_STOCK_HYGIENE = "stock_hygiene"
REJECT_K4_ADVISORY = "k4_advisory_gate"

# K4 分区缺 DB 登记时的保守默认(同 `report/intel_candidates.py::_DEFAULT_SECTION`
# 语义:缺 DB 行 → 当 avoid_flag、不当 hard_cut,不在 DB 之外自造硬剔判据。本模块
# 不 import 那个即将随候选榜退役的模块〔plan §五 V2-⑬-1〕,本地另拟同值同名常量,
# 值的出处见 `report/holding_k4_check.py::load_k4_sections` docstring)。
_K4_DEFAULT_SECTION = "avoid_flag"

# 贵而算不出时的占位值:**恒为非 None 且大到不可能被任何现实阈值挡住**——
# `stock_hygiene` 的 close/ma20/amount_ma20 三项检查全是 `is None` 或 `< 下限`,
# 用 `float("inf")` 占位可以保证该维度**必定放行**、不臆造一个"看起来正常"的数值。
# ⚠ `float("inf")` 是函数调用(`ast.literal_eval` 无法求值 Call 节点),不算「模块
# 级数值字面量」,不需要进 `test_selection_primitives.py::_ENGINE_CONSTANT_WHITELIST`
# (与 `MIN_MEMBERS`=1 那类真字面量常量性质不同)。
_UNAVAILABLE_SENTINEL = float("inf")


@dataclass(frozen=True)
class MemberRejection:
    """一只候选成员被卫生线剔除的留痕:剔了谁、因为哪条原语、详情是什么。"""

    ts_code: str
    primitive: str
    detail: str


@dataclass(frozen=True)
class MemberHygieneResult:
    """`apply_member_hygiene()` 的返回值。`kept` = 通过全部保险丝的候选成员集
    (装配 `presented_by_seed` 前先用它过滤 `seed.member_codes`);`k4_tag_of` 只含
    `avoid_flag` 命中的**存活**成员(hard_cut 命中已被剔,不会出现在这里)。"""

    kept: FrozenSet[str] = field(default_factory=frozenset)
    k4_tag_of: Dict[str, str] = field(default_factory=dict)
    rejected: Tuple[MemberRejection, ...] = ()
    hygiene_unavailable: bool = False
    k4_unavailable: bool = False


def _load_suspended_codes(trade_date: date, parquet_dir: Optional[Path]) -> Tuple[FrozenSet[str], bool]:
    """当日 `suspend_d` 停牌代码集合。`(集合, 读取是否失败)`——**读取失败**(异常)
    与"读到了、当日恰好没人停牌"必须分得开:前者按 tier-1 fail-closed 处理(调用方
    对所有码保守拒收),后者是合法的空集。"""
    from neckline.data.market_data import get_market_slice

    try:
        df = get_market_slice(trade_date, table="suspend_d", parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[member_hygiene] 读取 suspend_d 失败,本次全体候选按 tier-1 fail-closed 拒收",
                       exc_info=True)
        return frozenset(), True
    if df.is_empty():
        return frozenset(), False
    return frozenset(df["ts_code"].to_list()), False


def _load_liquidity_rows(
    codes: Sequence[str], trade_date: date, parquet_dir: Optional[Path]
) -> Dict[str, Dict[str, Any]]:
    """`ts_code -> {'ma20':.., 'amount_ma20':..}`(`build_research_panel` 单日切片,
    同 `report/intel_candidates.py` 既有调用姿势:`build_research_panel(d, d,
    with_forward=False)`,45 自然日缓冲、非全历史扫描)。整体读取失败 / 该日全市场
    无数据 → 空 dict(调用方按"该码面板缺失"处理,降级不拦)。"""
    try:
        panel = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[member_hygiene] 装配趋势/流动性面板失败,ma20/amount_ma20 本次全体降级为不拦",
                       exc_info=True)
        return {}
    if panel.is_empty():
        return {}
    wanted = set(codes)
    sub = panel.filter(pl.col("ts_code").is_in(list(wanted)))
    if sub.is_empty():
        return {}
    return {
        r["ts_code"]: {"ma20": r.get("ma20"), "amount_ma20": r.get("amount_ma20")}
        for r in sub.select(["ts_code", "ma20", "amount_ma20"]).iter_rows(named=True)
    }


def _bulk_scan_codes_range(
    codes: Sequence[str], start: date, end: date, table: str, parquet_dir: Optional[Path]
) -> pl.DataFrame:
    """`holding_k4_check._build_holding_feature_panel` 的 `TableLoader` 实现:一次
    `scan_parquet` + 谓词下推(日期区间 ∧ `ts_code ∈ codes`),只物化候选成员集相关
    行——同 `report/intel_candidates.py::_bulk_load_codes_table` 的 I/O 手法,本地
    另写一份等价实现(那个函数挂在按 plan §五 V2-⑬-1 将随候选榜退役的模块上,
    `aggregate.py` 已有先例〔`market_industry_shares` docstring〕不 import 计划删除
    模块的私有函数)。表目录缺失/无文件 → 空 DataFrame(优雅降级)。"""
    if not codes:
        return pl.DataFrame()
    d = table_dir(table, parquet_dir)
    if not d.exists():
        return pl.DataFrame()
    pattern = str(d / "year=*" / "*.parquet")
    if not glob.glob(pattern):
        return pl.DataFrame()
    code_set = list(dict.fromkeys(codes))
    return (
        pl.scan_parquet(pattern)
        .filter(
            (pl.col("trade_date") >= start)
            & (pl.col("trade_date") <= end)
            & pl.col("ts_code").is_in(code_set)
        )
        .collect()
    )


def _load_k4_panel_rows(
    codes: Sequence[str], trade_date: date, parquet_dir: Optional[Path]
) -> Dict[str, Dict[str, Any]]:
    """K4 判据需要的价量特征面板(逐票行),复用 `holding_k4_check` 既有机械镜像
    (`_build_holding_feature_panel` 内部 qfq → `add_features` → `merge_limit_features`
    /`merge_daily_basic` → `_add_k4_features`/`_add_hit_columns`),只把 I/O 换成
    `_bulk_scan_codes_range`(候选成员集 bulk 谓词下推,同 ② 持仓体检 vs ③ 候选管线
    的既有二选一分工)。**独立的模块级函数**——测试可直接 monkeypatch 本函数,绕开
    ~420 自然日真实历史数据的构造成本,专注测上层"命中如何映射成 hard_cut/avoid_flag"
    的编排逻辑(价量镜像本身已在 `tests/test_holding_k4_check.py` 43 例覆盖,不重复
    验证)。"""
    from neckline.report.holding_k4_check import _build_holding_feature_panel

    panel = _build_holding_feature_panel(
        list(codes), trade_date, parquet_dir, load_fn=_bulk_scan_codes_range,
    )
    if panel.is_empty():
        return {}
    return {r["ts_code"]: r for r in panel.to_dicts()}


def _resolve_k4_sections(
    codes: Sequence[str],
    trade_date: date,
    industry_of: Mapping[str, str],
    *,
    db_path: Optional[Path],
    parquet_dir: Optional[Path],
) -> Tuple[Dict[str, str], bool]:
    """`ts_code -> 'hard_cut'|'avoid_flag'`(只含有分区归属的码;未命中的码不出现在
    字典里,`k4_advisory_gate` 原语对 `.get(code)=None` 天然放行,不需要占位 None
    值)。`(映射, 整体是否降级)`——K4 评估任一环节抛异常 → 空映射 + `True`(该维度
    全体不打标、不拦,同 ma20/amount_ma20 的降级哲学,builder 类推、如实登记)。

    **两处 `.get()` 语义刻意不同**(CLAUDE.md「复用与设计体例」条,同
    `intel_candidates.py` 先例):判 hard_cut 用 `sections.get(h.code,
    _K4_DEFAULT_SECTION)`(缺 DB 登记时保守当 avoid_flag、不拦);判 avoid_flag 用
    `sections.get(h.code)` **不给默认**(未在 DB 明确登记为 avoid_flag 的码——含
    hard_cut 命中、含不在 DB 的合成码——一律不打 avoid_flag 标)。"""
    try:
        from neckline.report.holding_k4_check import _load_k4_evidence, load_k4_sections
        from neckline.report.industry_strength import industry_strength_lookup, stock_persist_days
        from neckline.report.industry_strength_store import load_industry_strength
        from neckline.report.holding_k4_check import _evaluate_hits

        sections = load_k4_sections(db_path)
        evidence = _load_k4_evidence(db_path)
        industry_hot = industry_strength_lookup(load_industry_strength(trade_date, db_path=db_path))
        rows_by_code = _load_k4_panel_rows(codes, trade_date, parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[member_hygiene] K4 安检评估失败,本次全体不打 K4 标(降级不拦)", exc_info=True)
        return {}, True

    out: Dict[str, str] = {}
    for code in codes:
        persist = stock_persist_days(code, industry_of, industry_hot)
        row = rows_by_code.get(code)
        hits = _evaluate_hits(row, persist, evidence)
        if not hits:
            continue
        if any(sections.get(h.code, _K4_DEFAULT_SECTION) == "hard_cut" for h in hits):
            out[code] = "hard_cut"
        elif any(sections.get(h.code) == "avoid_flag" for h in hits):
            out[code] = "avoid_flag"
    return out, False


def apply_member_hygiene(
    codes: Sequence[str],
    trade_date: date,
    pack: Pack,
    *,
    industry_of: Mapping[str, str],
    close_of: Mapping[str, float],
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
) -> MemberHygieneResult:
    """成员卫生线闸唯一入口。`codes` = 候选成员集(本日全部种子 `member_codes` 的
    并集);调用方须在装配 `presented_by_seed` 之前调用本函数**恰好一次**、用
    `result.kept` 过滤每颗种子的 `member_codes` 再截断(`MAX_MEMBERS_IN_CONTEXT`)。

    `industry_of` / `close_of`:调用方(`aggregate.py::build_mech_context`)已经算好
    的行业映射 / 当日收盘价,复用避免重复 I/O(`close` 与 qfq 面板锚点当日数值相同,
    见完工记录如实登记 —— 前复权锚点是面板每票最新一行,单日面板〔trade_date,
    trade_date〕的锚点就是 trade_date 本身,故当日 qfq 收盘 = 当日原始收盘)。
    """
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return MemberHygieneResult()

    hygiene_prim = PRIMITIVES["stock_hygiene"]
    non_new_prim = PRIMITIVES["non_new_stock"]
    k4_prim = PRIMITIVES["k4_advisory_gate"]
    hygiene_params = pack.seeds_config("stock_hygiene")
    non_new_params = pack.seeds_config("non_new_stock")
    k4_params = pack.seeds_config("k4_advisory_gate")

    suspended, suspend_unavailable = _load_suspended_codes(trade_date, parquet_dir)
    try:
        meta_map = load_stock_meta(wanted, db_path)
        meta_unavailable = False
    except Exception:  # noqa: BLE001 —— tier-1 数据源"必在",真炸了也不能掀翻聚合链路
        logger.warning("[member_hygiene] load_stock_meta 失败,本次候选按 tier-1 fail-closed 拒收",
                       exc_info=True)
        meta_map, meta_unavailable = {}, True

    liquidity_rows = _load_liquidity_rows(wanted, trade_date, parquet_dir)
    k4_section_of, k4_unavailable = _resolve_k4_sections(
        wanted, trade_date, industry_of, db_path=db_path, parquet_dir=parquet_dir,
    )

    kept: list = []
    k4_tag_of: Dict[str, str] = {}
    rejected: list = []
    hygiene_unavailable = False

    for code in wanted:
        if suspend_unavailable:
            rejected.append(MemberRejection(code, REJECT_SUSPENDED, "停牌名单读取失败,tier-1 保守拒收"))
            continue
        if code in suspended:
            rejected.append(MemberRejection(code, REJECT_SUSPENDED, "当日在 suspend_d 停牌名单"))
            continue

        meta = None if meta_unavailable else meta_map.get(code)
        if meta is None:
            rejected.append(MemberRejection(code, REJECT_META_MISSING, "stock_basic 查无此票(或读取失败)"))
            continue

        days_since_listing = (trade_date - meta.list_date).days if meta.list_date is not None else None
        if not non_new_prim.run({"days_since_listing": days_since_listing}, non_new_params):
            rejected.append(MemberRejection(
                code, REJECT_NON_NEW_STOCK, f"days_since_listing={days_since_listing}"
            ))
            continue

        liq = liquidity_rows.get(code)
        ma20_v: Any = liq.get("ma20") if liq is not None else None
        amount_ma20_v: Any = liq.get("amount_ma20") if liq is not None else None
        close_v: Any = close_of.get(code)
        # **贵而算不出才降级**——三值任一为 `None` 都按"这一维度本次算不出"处理,
        # 不区分"该码整行都不在面板里"与"面板里有行但字段是 null"两种子情形:
        # `non_new_stock`(`min_days=120` 自然日)已经比 ma20 需要的 20 个交易日
        # 严格得多,一只票能过 `non_new_stock` 却 `ma20` 仍是 null,现实里代表的是
        # **数据缺口**(该码在已加载窗口内交易日不足 20 天却已上市 120+ 自然日,
        # 只可能是停牌/数据源缺失连续多日)而不是"真的太新"——真太新的票早被
        # `non_new_stock` 拦在前一步了,不需要 `stock_hygiene` 再兜底判一次"是否
        # 真的没数据"。故这里统一按"面板缺失"处理:降级不拦 + 如实标
        # `hygiene_unavailable`(P0-23 定案),**不许因为凑巧只喂了一两天历史的
        # 测试夹具就被当成"真的不达标"而拦掉**。
        if ma20_v is None or amount_ma20_v is None or close_v is None:
            hygiene_unavailable = True
            if ma20_v is None:
                ma20_v = _UNAVAILABLE_SENTINEL
            if amount_ma20_v is None:
                amount_ma20_v = _UNAVAILABLE_SENTINEL
            if close_v is None:
                close_v = _UNAVAILABLE_SENTINEL

        hygiene_row = {
            "is_st": meta.is_st, "board": meta.board.value,
            "close": close_v, "ma20": ma20_v, "amount_ma20": amount_ma20_v,
        }
        if not hygiene_prim.run(hygiene_row, hygiene_params):
            rejected.append(MemberRejection(code, REJECT_STOCK_HYGIENE, repr(hygiene_row)))
            continue

        section = k4_section_of.get(code)
        if not k4_prim.run({"k4_section": section}, k4_params):
            rejected.append(MemberRejection(code, REJECT_K4_ADVISORY, f"k4_section={section}"))
            continue
        if section == "avoid_flag":
            k4_tag_of[code] = section

        kept.append(code)

    return MemberHygieneResult(
        kept=frozenset(kept),
        k4_tag_of=k4_tag_of,
        rejected=tuple(rejected),
        hygiene_unavailable=hygiene_unavailable,
        k4_unavailable=k4_unavailable,
    )


__all__ = [
    "REJECT_SUSPENDED",
    "REJECT_META_MISSING",
    "REJECT_NON_NEW_STOCK",
    "REJECT_STOCK_HYGIENE",
    "REJECT_K4_ADVISORY",
    "MemberRejection",
    "MemberHygieneResult",
    "apply_member_hygiene",
]
