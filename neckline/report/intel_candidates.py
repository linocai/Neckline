"""候选池情报筛选管线(plan §五 v1.3-③-C3,需求 5,K1 选股逻辑退役)。

**产品语义变更(用户 2026-07-25 拍板,§2.3)**:候选列表不再是「系统认为会涨的票」,
改为「**过完安检、值得用户花注意力的票**」,终选权在用户。生成源从 **K1 entry mask
退役**——本模块**不调用 `strategy.momentum.build_entry_mask`**(单测 `test_intel_candidates`
直接断言),改走四步情报筛选管线:

    ① 板块层  = 拥挤度 top:**五板块常驻**(`settings_store.get_intel_watch_boards`,按
                `ths_index.name` **精确匹配**取 ts_code——禁关键词模糊:"芯片"会误命中
                汽车芯片/存储芯片、"机器人"会误命中人形机器人,实测见
                `settings_store.DEFAULT_INTEL_WATCH_BOARDS`)+ **当日暴起板块**(`compute_
                sector_strength` 拥挤度 top-N,**先过 `board_pool` 卫生线**剔资格/宽基标签)。
    ② 个股层  = 上述板块成员 ∩ **全板块 MAIN/GEM/STAR**(排 BSE,`base_universe_expr` 已含)
                ∩ 只过卫生线(`research.panel.base_universe_expr` 子集:非 ST + amount_ma20
                ≥2000万 + close≥2 + ma20 非空)∩ **非次新**(`~signals.forbid_new_stock(120)`,
                = days_since_listing≥120,与 K4 A4/base 口径同源)∩ **趋势向上**(`close>ma20`
                **粗代理**,标注)。**不套 K1 主板 only、不套 pullback/breakout 回调买点**
                (与 K1 entry mask 解耦,§3.8-(b))。
    ③ K4 安检 = 读 DB `K4.k4_advisory` 分区(`holding_k4_check.load_k4_sections`):
                `hard_cut` 命中 → **拦截出池**;`avoid_flag` 命中 → **打标保留**(机器不禁、
                情报展示给人判)。**复用 ②-A 的 polars 镜像评估器(`holding_k4_check` 同一份,
                阈值单一源,不写两遍)**——只是把持仓 I/O(逐票循环)换成全板块 bulk 面板
                I/O(见「性能坑」)。合成派发码 `A3b_belowyear_bigvol`(不在 DB,证据源=雷区
                地图 3-⑤)按 `_DEFAULT_SECTION` 归 avoid_flag(打标不拦,机器不禁;是否升级
                为 hard_cut 留用户拍板)。
    ④ 情报排序 = 板块资金流强度(C2 `sector_moneyflow`,取候选所属常驻/暴起板块的最大净
                流入)+ 题材持续天数**反用**(`_theme_freshness_score`:1 天新鲜 > 2-3 天警惕 >
                ≥4 天已在 ③ hard_cut〔A2〕剔)+ 高弹标注 → 出 **20 只**交用户终选。

**§3.8 铁律「同码」重述的落地核对**:候选生成(本模块)与回测信号**解耦**——不声称
回测过的 alpha、输出「值得关注」非「会涨」。**纪律红绿灯(问询台 `api/inquiry.py` /
自选体检 `report/watchlist_check.py`)仍与报告同码**(`base_universe_expr` + config 禁买
过滤),本模块不碰它们。**`report/candidates.py` 的评分表达式 `_base_score_expr`、四件套
文案、`pattern_tags` 均复用**(展示排序分/四件套/形态标签同一份,不重写);候选 `rank` 由
情报排序决定、`score` = `_base_score_expr` 展示分(技术贴前高度,**非**排序键,见 §④)。

**性能坑(plan §五 v1.3-③-C3「③C1/C2 施工者点名交接」)**:`holding_k4_check` 的 K4 镜像
原按「≤3 持仓、逐票 `get_stock_history` 循环」写(内存友好但全板块数千只会很慢)。本模块
选 **(a):复用其判据表达式 + 换全市场 bulk 面板 I/O**——给 `holding_k4_check._build_holding_
feature_panel` 注入 `_bulk_load_codes_table`(一次 `scan_parquet` 谓词下推,按 code 集合过滤,
免逐票 N 次开文件),**特征/判据装配与阈值与 ② 完全同一份**(单一源)。两条 I/O 路径的
一致性由 `tests/test_intel_candidates.py::test_bulk_and_percode_loaders_agree` 直接对拍。

**生成域刻意含高弹**(GEM/STAR;用户知情拍板,与 K1「剔高弹」哲学相反,止损频率代价已在
策略线审计定价)——**不偷偷加回 K1 的高弹剔除**,只 `intelRank.highElasticity` 标注给人判。
"""

from __future__ import annotations

import glob
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import polars as pl

from neckline.data.board import classify_by_code
from neckline.data.market_data import table_dir
from neckline.report.board_pool import apply_hygiene, count_members, invert_member_map
from neckline.report.candidates import (
    Candidate,
    _base_score_expr,
    _load_stock_names,
    entry_plan_text,
    entry_spec,
    invalidation_spec,
    invalidation_text,
    pattern_tags,
    stop_loss_text,
    target_text,
)
from neckline.report.holding_k4_check import (
    _A2_PERSIST_MIN,
    _LOOKBACK_CALENDAR_DAYS,
    _build_holding_feature_panel,
    _evaluate_hits,
    _load_k4_evidence,
    _theme_persist_days,
    load_k4_sections,
)
from neckline.report.sectors import (
    SectorScore,
    compute_sector_strength,
    load_index_names,
    load_member_map,
    sector_hot_lookup,
)
from neckline.report.sector_moneyflow import compute_sector_moneyflow
from neckline.research.panel import base_universe_expr
from neckline.settings_store import get_intel_watch_boards
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig

logger = logging.getLogger(__name__)

# 全板块 MAIN/GEM/STAR(排 BSE,`board.py::Board` 枚举码;base_universe_expr 已含 !=BSE,
# 此处显式再挡一道,贴 plan「全板块 MAIN/GEM/STAR」措辞)。
_ALLOWED_BOARDS = ("MAIN", "GEM", "STAR")
NON_NEW_MIN_DAYS = 120        # 非次新门槛(days_since_listing≥120,复用 signals.forbid_new_stock,同 K4 A4/base 口径)
BREAKOUT_TOP_N = 10           # 当日暴起板块 top-N(过卫生线后按拥挤度取)
TOP_N_CANDIDATES = 20         # 出榜候选数(交用户终选)
_ALL_BOARDS_TOP_N = 1000      # 远超真实概念板块总数(394,2026-07-24 快照),拿全量排序结果(同 intel.py)
_MONEYFLOW_ALL_TOP_N = 10 ** 9  # 拿 C2 全部板块资金流(非只 top-15),供逐候选查其板块净流入
# DB `k4_advisory` 无归属的合成码(A3b_belowyear_bigvol,证据源=雷区地图 3-⑤)默认归属:
# avoid_flag = 打标保留(机器不禁、给人判)。**不默认 hard_cut**——严守「hard_cut 单一源 = DB」,
# 不在 DB 之外自造硬剔判据(是否把年线下派发放量大阳升级为 hard_cut,留用户拍板,见 report ⑦)。
_DEFAULT_SECTION = "avoid_flag"

# 题材持续天数**反用**评分(plan §④:1 天新鲜 > 2-3 天警惕;≥4 天已在 ③ A2 hard_cut 剔,
# 0 = 板块未站上 MA20/未启动=最弱)。阈值语义与 `holding_k4_check` A2/B3 同源(≥4=A2、
# 2-3=B3),此处只做「越新鲜分越高」的展示排序映射,不新增判据阈值。
_THEME_FRESHNESS = {1: 3, 2: 2, 3: 1}


def _theme_freshness_score(persist_days: int) -> int:
    return _THEME_FRESHNESS.get(persist_days, 0)


def _bulk_load_codes_table(
    codes: List[str], start: date, end: date, table: str, parquet_dir: Optional[Path]
) -> pl.DataFrame:
    """全市场 bulk 区间加载器(注入 `holding_k4_check._build_holding_feature_panel`,替换
    ② 的逐票 `get_stock_history` 循环——数千只 universe 逐票会很慢,见模块「性能坑」)。
    **一次 `scan_parquet` + 谓词下推**:同时按 [start,end] 与 `ts_code ∈ codes` 过滤,
    parquet predicate pushdown 只物化 universe 相关行(免逐票 N 次开文件 + 免全市场物化)。
    表目录缺失/无文件 → 空 DataFrame(同 `market_data._scan_table` 优雅降级)。"""
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


def _resolve_watch_board_codes(
    index_names: Dict[str, str], db_path: Optional[Path]
) -> Tuple[Set[str], List[str]]:
    """五板块常驻名单(板块中文名)→ index_code 集合,**按 `ths_index.name` 精确匹配**
    (禁关键词模糊,见模块 docstring)。返回 (解析到的 index_code 集合, 未解析到的名字列表)。
    精确名极少数情况可能对应多个 index_code → 全取(仍是精确名,不模糊)。"""
    names = get_intel_watch_boards(db_path)
    name_to_codes: Dict[str, List[str]] = {}
    for code, nm in index_names.items():
        name_to_codes.setdefault(nm, []).append(code)
    codes: Set[str] = set()
    unresolved: List[str] = []
    for nm in names:
        hit = name_to_codes.get(nm)
        if hit:
            codes.update(hit)
        else:
            unresolved.append(nm)
    return codes, unresolved


def build_intel_candidates(
    trade_date: date,
    rule: Dict[str, Any],
    *,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    top_n: int = TOP_N_CANDIDATES,
    breakout_top_n: int = BREAKOUT_TOP_N,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    forced_codes: Optional[List[str]] = None,
) -> List[Candidate]:
    """候选情报筛选管线入口(角色对应旧 `candidates.build_candidates`,pipeline 侧替换点)。
    `rule` = 大脑现役 `brain.get_active().rule`(cfg 读 stop_pct/max_hold_days 等,单一源;
    **仅用于四件套文案/展示分,不用于 entry mask**)。`member_map`/`index_names` 由 pipeline
    传入(报告已加载,不重复读 parquet);`sector_scores`(大列表,拥挤度 + board_age)缺省
    则内部 `compute_sector_strength(top_n=1000)` 自算。`forced_codes` = 问询台海选池「初审
    通过」的票(§2.5)强制并入(用户点名,豁免 ② 卫生线与 ③ hard_cut,仅 K4 打标展示)。"""
    cfg = MomentumConfig(**rule["config"])
    member_map = member_map if member_map is not None else load_member_map(parquet_dir=parquet_dir)
    index_names = index_names if index_names is not None else load_index_names(parquet_dir=parquet_dir)
    forced_set = {c for c in (forced_codes or []) if c}

    # —— ① 板块层:五常驻 + 当日暴起 top-N(先过卫生线)————————————————————————
    all_scores = (
        sector_scores
        if sector_scores is not None
        else compute_sector_strength(trade_date, parquet_dir=parquet_dir, top_n=_ALL_BOARDS_TOP_N)
    )
    hygiene = apply_hygiene(index_names, count_members(member_map))
    kept_boards = hygiene.kept
    # 当日暴起 = 过卫生线的板块按拥挤度(compute_sector_strength 已按 board_ret_20d 降序)top-N。
    breakout_codes = [s.index_code for s in all_scores if s.index_code in kept_boards][:breakout_top_n]
    permanent_codes, unresolved = _resolve_watch_board_codes(index_names, db_path)
    if unresolved:
        logger.warning("候选情报管线:五常驻板块名未在 ths_index 精确匹配到:%s(跳过,不模糊回退)", unresolved)
    step1_boards: Set[str] = set(permanent_codes) | set(breakout_codes)
    # 题材持续天数/展示只看候选**所属的 step① 板块**的 board_age(不看非热板块),故 hot 限定 step① 内。
    all_hot = sector_hot_lookup(all_scores)
    step1_hot = {b: all_hot[b] for b in step1_boards if b in all_hot}

    # —— ② 个股层:step① 板块成员 ∩ MAIN/GEM/STAR ∩ 卫生线 ∩ 非次新 ∩ 趋势向上 ————————
    inv = invert_member_map(member_map)
    member_codes: Set[str] = set()
    for b in step1_boards:
        member_codes.update(inv.get(b, []))
    today = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    # 板块归属(MAIN/GEM/STAR/BSE)取自 today 面板(merge_meta 已算,单一源 board.classify);
    # forced 票若不在 today(极端)退 classify_by_code 前缀兜底。K4 面板(holding_k4_check
    # 的 builder)不含 board 列,故 board 只从这里取,不从 K4 面板 row 取。
    board_by_code: Dict[str, str] = (
        dict(zip(today["ts_code"].to_list(), today["board"].to_list())) if not today.is_empty() else {}
    )
    survivor_codes: Set[str] = set()
    if not today.is_empty() and member_codes:
        step2 = today.filter(
            pl.col("ts_code").is_in(list(member_codes))
            & pl.col("board").is_in(list(_ALLOWED_BOARDS))
            & base_universe_expr()
            & ~S.forbid_new_stock(NON_NEW_MIN_DAYS)
            & (pl.col("close") > pl.col("ma20"))   # 趋势向上(粗代理,§② 标注)
        )
        survivor_codes = set(step2["ts_code"].to_list())

    universe_codes = survivor_codes | forced_set
    if not universe_codes:
        return []

    # —— ③ K4 安检:全板块 bulk 面板(复用 ② 判据镜像,阈值单一源)→ hard_cut 拦 / avoid_flag 标 ——
    k4_panel = _build_holding_feature_panel(
        list(universe_codes), trade_date, parquet_dir, load_fn=_bulk_load_codes_table
    )
    if not k4_panel.is_empty():
        k4_panel = k4_panel.with_columns(_base_score_expr(cfg).alias("_base_score"))
    rows_by_code: Dict[str, Dict[str, Any]] = (
        {r["ts_code"]: r for r in k4_panel.to_dicts()} if not k4_panel.is_empty() else {}
    )
    sections = load_k4_sections(db_path)
    evidence = _load_k4_evidence(db_path)

    # —— ④ 情报排序输入:板块资金流(C2 全板块)————————————————————————————————
    mf = compute_sector_moneyflow(
        trade_date, member_map=member_map, index_names=index_names,
        parquet_dir=parquet_dir, top_n=_MONEYFLOW_ALL_TOP_N,
    )
    flow_by_board = {i.index_code: i.net_inflow_wan for i in mf.top_inflow} if mf.available else {}

    kept: List[Dict[str, Any]] = []   # {code, row, k4_flags, sector_flow, persist, freshness, base_score, is_forced}
    for code in universe_codes:
        row = rows_by_code.get(code)
        persist = _theme_persist_days(code, member_map, step1_hot)
        hits = _evaluate_hits(row, persist, evidence)
        hard = [h for h in hits if sections.get(h.code, _DEFAULT_SECTION) == "hard_cut"]
        is_forced = code in forced_set
        if hard and not is_forced:
            continue   # ③ hard_cut 命中 → 拦截出池(forced 问询票用户点名,豁免硬剔、仅打标)
        if row is None:
            continue   # 无当日 EOD 数据(停牌/未上市)——无法出四件套候选卡,跳过
        # 保留候选的 K4 标注码:普通候选 = avoid_flag 命中;forced 票即使命中 hard_cut 也全数标注(诚实透出危险)。
        k4_flags = [h.code for h in hits]
        its_step1_boards = [b for b in member_map.get(code, []) if b in step1_boards]
        flows = [flow_by_board[b] for b in its_step1_boards if b in flow_by_board]
        sector_flow = max(flows) if flows else None
        kept.append({
            "code": code, "row": row, "k4_flags": k4_flags,
            "board": board_by_code.get(code) or classify_by_code(code),
            "sector_flow": sector_flow, "persist": persist,
            "freshness": _theme_freshness_score(persist),
            "base_score": float(row.get("_base_score") or 0.0),
            "is_forced": is_forced, "its_step1_boards": its_step1_boards,
        })

    if not kept:
        return []

    # 情报排序键(降序):板块资金流强度 → 题材新鲜度(反用) → 展示分 → 代码(确定性兜底)。
    # sector_flow=None(C2 无数据 / 该票板块无资金流)排最后(-inf)。
    def _sort_key(e: Dict[str, Any]) -> tuple:
        sf = e["sector_flow"] if e["sector_flow"] is not None else float("-inf")
        return (-sf, -e["freshness"], -e["base_score"], e["code"])

    kept.sort(key=_sort_key)
    top = kept[:top_n]
    # forced 票即使排序在 top_n 之外也保证出现(§2.5「强制纳入」)。
    if forced_set:
        present = {e["code"] for e in top}
        for e in kept:
            if e["is_forced"] and e["code"] not in present:
                top.append(e)
                present.add(e["code"])
        top.sort(key=_sort_key)

    names = _load_stock_names([e["code"] for e in top], db_path)
    out: List[Candidate] = []
    for i, e in enumerate(top, start=1):
        out.append(_build_intel_candidate(e, rank=i, cfg=cfg, step1_hot=step1_hot,
                                           member_map=member_map, index_names=index_names, names=names))
    return out


def _build_intel_candidate(
    e: Dict[str, Any],
    *,
    rank: int,
    cfg: MomentumConfig,
    step1_hot: Dict[str, SectorScore],
    member_map: Dict[str, List[str]],
    index_names: Dict[str, str],
    names: Dict[str, str],
) -> Candidate:
    """把情报管线的一个保留候选装配成 `Candidate`(复用 candidates.py 四件套文案/形态标签/
    展示分,同码不重写)。新增 `k4_flags`(K4 命中标注码)+ `intel_rank`(情报排序理由)。"""
    row = e["row"]
    code = e["code"]
    close = row["close"]
    board = e["board"]   # 取自 today 面板(merge_meta),K4 面板 row 无 board 列
    boards = member_map.get(code, [])
    hot_names = [
        f"{step1_hot[b].name}(板块年龄{step1_hot[b].board_age}天,20日{step1_hot[b].ret_20d:+.1%})"
        for b in boards if b in step1_hot
    ]
    sector_names = [index_names.get(b, b) for b in boards]
    stop_price = round(close * (1 - cfg.stop_pct), 2) if cfg.stop_pct else None
    spec = invalidation_spec()
    intel_rank = {
        "sectorFlow": round(e["sector_flow"], 1) if e["sector_flow"] is not None else None,
        "themePersistDays": e["persist"],
        "highElasticity": board in S.HIGH_ELASTICITY_BOARDS,
    }
    return Candidate(
        ts_code=code,
        name=names.get(code, code),
        close=close,
        score=round(e["base_score"], 1),   # 展示排序分(技术贴前高度),**非**排序键(rank 由情报排序定)
        rank=rank,
        board=board,
        pattern_tags=pattern_tags(row),
        hot_sectors=hot_names,
        sector_names=sector_names,
        entry_plan=entry_plan_text(row, cfg),
        stop_loss=stop_loss_text(stop_price, cfg),
        target=target_text(cfg),
        invalidation_text=invalidation_text(spec),
        invalidation_spec=spec,
        entry_spec=entry_spec(row, cfg),
        k4_flags=e["k4_flags"],
        intel_rank=intel_rank,
        raw=row,
    )


__all__ = [
    "build_intel_candidates",
    "NON_NEW_MIN_DAYS",
    "BREAKOUT_TOP_N",
    "TOP_N_CANDIDATES",
]
