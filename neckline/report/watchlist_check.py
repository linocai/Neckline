"""自选体检(plan §2.3 v1.1 拍板 / §五 v1.1-C.3)。16:35 报告新增的独立一节——对
用户自选池(`neckline.watchlist`)**每只票用同一套评分管线**跑当日评分 + 形态标签 +
纪律红绿灯 + 形态触发买点条件的给完整四件套。

**同码铁律(任务原文「复用 report/candidates 的评分函数,禁止另写」)**:
    · 评分公式 = `report.candidates._base_score_expr(cfg)` +(同一套板块热度加分),
      与候选评分**同一个函数**,不重新推一遍。
    · 买点是否「触发」直接用 `strategy.momentum.build_entry_mask(cfg)` 本尊——与
      候选评分「今天算不算入围候选」用的是同一把尺,不是另造一套"买点判定"。
    · 四件套(买点/止损/目标/证伪条件 + 结构化 `entry_spec`/`invalidation_spec`)
      直接调用 `report.candidates` 已导出的公开函数,一字不重写。
    · 形态标签 = `report.candidates.pattern_tags`,同一份。

**纪律红绿灯**(禁买规则 / 票型黑名单 / ST / 板块限制,读大脑现役 config)是
`build_entry_mask` 的"选股域 + 禁买过滤"子句单独抽出展示(见 `discipline_checks`
docstring)——`build_entry_mask` 本身是选股域∧强势∧买点∧¬禁买 的单一 AND 结果,
无法从中拆出"具体是哪条规则导致不能碰这只票";本模块用同一批
`research.panel.base_universe_expr()`/`strategy.signals.forbid_*` 谓词单独求值,
只是**换一种组合方式展示原因**,不是重新定义任何阈值。

**`discipline_checks` 是本模块与 `api/inquiry.py::run_deterministic_checks` 的
共享单一源(plan §五 v1.3-⑤)**:问询台此前手写重复了一份选股域逻辑(ST/北交所/
价格/流动性/MA20 逐条 Python 重刻,且当初未核对 P4/P5 两条禁买过滤),与本模块
各自维护一份阈值、容易漂移。host 选在本模块(而非另开共享文件)——因为「正确
姿势」本就是本模块先立的(v1.1-C.3),`api/inquiry.py` 早已从 `report.candidates`
反向导入评分/四件套函数(同一 `report → api` 依赖方向的既有先例),此处沿用
同一方向、不新增模块。

**自选体检不改候选20评分逻辑、不进候选榜**——本模块只读大脑现役规则与当日面板,
不接触 `report.candidates.build_candidates`/`score_candidates` 的任何状态,是独立
一节(同码复用评分,只扩输入范围到自选池)。

**LLM 控成本(任务拍板)**:体检只对「当日状态较上一份报告发生变化的」∪「用户
`pinned` 的」跑 LLM(见 `apply_llm_review`/`_is_changed`),其余确定性输出、不耗
LLM。复用 `llm.judge.judge_candidate`(降级链继承),只是换一套
`WATCHLIST_JUDGE_SYSTEM_PROMPT`(候选审判"是否留在候选池"的框定语并不贴合自选票
场景,故 `judge_candidate` 新增了可选 `system_prompt` 参数,默认值不变、不影响
候选审判调用点——纯粹的向后兼容扩展)。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import polars as pl

from neckline.llm.base import LLMProvider
from neckline.llm.judge import JudgeResult, WATCHLIST_JUDGE_SYSTEM_PROMPT, judge_candidate
from neckline.report.candidates import (
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
from neckline.report.sectors import SectorScore, sector_hot_lookup
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig, build_entry_mask

NO_DATA_REASON = "当日行情面板查无该票(停牌 / 未上市 / 代码有误),无法核对纪律。"


@dataclass
class WatchlistCheckItem:
    ts_code: str
    name: str
    pinned: bool
    source: str
    has_data: bool
    close: float = 0.0
    board: str = "MAIN"
    score: Optional[float] = None
    pattern_tags: List[str] = field(default_factory=list)
    hot_sectors: List[str] = field(default_factory=list)
    sector_names: List[str] = field(default_factory=list)
    # 纪律红绿灯(红=触禁买、绿=可动):green_light=False 时 disqualifiers 非空,
    # 逐条列出触发的具体规则(禁买线 / 票型黑名单 / ST / 板块限制)。
    green_light: bool = False
    disqualifiers: List[str] = field(default_factory=list)
    # 形态是否已触发母战法买点(= `build_entry_mask(cfg)` 在该行为真,与候选评分
    # "今天算不算入围"同一把尺)——为 True 时下方四件套字段才有实质内容。
    buy_point_triggered: bool = False
    entry_plan: str = ""
    stop_loss: str = ""
    target: str = ""
    invalidation_text: str = ""
    invalidation_spec: Dict[str, Any] = field(default_factory=dict)
    entry_spec: Dict[str, Any] = field(default_factory=dict)
    # 状态较上一份报告是否变化(红绿灯翻转 / 买点触发翻转 / 形态标签变化,见
    # `_is_changed`);供 API 层展示 + 是 LLM 审判判据之一。
    status_changed: bool = False
    # 仅当 (status_changed ∪ pinned) 且 has_data 时才非 None(§LLM 控成本)。
    llm_judgment: Optional[Dict[str, Any]] = None

    def public_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _no_data_item(w: Dict[str, Any]) -> WatchlistCheckItem:
    code = w["ts_code"]
    return WatchlistCheckItem(
        ts_code=code, name=w.get("name") or code,
        pinned=bool(w.get("pinned")), source=w.get("source", "manual"),
        has_data=False, green_light=False, disqualifiers=[NO_DATA_REASON],
        entry_plan="无数据,无法给出买点计划。", stop_loss="无数据。",
        target="无数据。", invalidation_text="无数据。",
    )


def discipline_checks(cfg: MomentumConfig) -> List[Tuple[str, str, pl.Expr]]:
    """纪律红绿灯用到的禁买/黑名单判定项:(列名, 中文原因, 布尔表达式=True 表示
    触发该项禁买)。「选股域」四项(ST/北交所/价格/流动性/MA20 未形成)合并成一条
    展示原因(它们是 `research.panel.base_universe_expr()` 内部已经 AND 在一起的
    单一布尔源,该函数本身不可拆分——若在此处重新逐项手写等价条件,数值阈值会
    与 `base_universe_expr()` 各自维护一份,一旦上游改动就会漂移,故宁可损失一点
    展示粒度也不重复刻字面量);现役 config **可配的四项禁买过滤**(P4/P5/P6)与
    `momentum.build_entry_mask` 是同一批 `strategy.signals` 谓词,按 cfg 是否启用
    逐项决定是否纳入判定,与 `build_entry_mask` 的 if 分支一一对应,不新拍任何
    阈值。

    **公开函数(plan §五 v1.3-⑤ 提升)**:`api/inquiry.py::run_deterministic_checks`
    与本模块 `score_watchlist` 共用同一份——两处任何时候都对同一票同一日给出相同
    的 `passes_discipline`/disqualifiers 判定,不再各自维护一份选股域字面量。"""
    from neckline.research.panel import base_universe_expr

    checks: List[Tuple[str, str, pl.Expr]] = [
        (
            "_dq_base",
            "不满足选股域(ST / 北交所 / 股价<2元 / 20日均额<2000万 / 无MA20 任一项,选股域清洗常开)",
            ~base_universe_expr(),
        ),
    ]
    if cfg.forbid_green_bigdown is not None:
        checks.append((
            "_dq_bigdown",
            f"绿盘大阴线(当日跌幅≤{cfg.forbid_green_bigdown:.0%},现役规则禁买)",
            S.forbid_green_bigdown(cfg.forbid_green_bigdown),
        ))
    if cfg.forbid_far_from_high is not None:
        checks.append((
            "_dq_farhigh",
            f"距20日高点过远(≤{cfg.forbid_far_from_high:.0%},现役规则禁买下跌途中票)",
            S.forbid_far_from_high(cfg.forbid_far_from_high),
        ))
    if cfg.forbid_new_days is not None:
        checks.append((
            "_dq_new",
            f"次新股(上市不足{cfg.forbid_new_days}自然日,现役规则剔除)",
            S.forbid_new_stock(cfg.forbid_new_days),
        ))
    if cfg.forbid_high_elasticity:
        checks.append((
            "_dq_elastic",
            "高弹题材板块(创业板/科创板,20%涨跌幅易跌停,现役规则风控剔除)",
            S.forbid_high_elasticity(),
        ))
    return checks


def build_watchlist_check(
    trade_date: date,
    rule: Dict[str, Any],
    watchlist_items: List[Dict[str, Any]],
    *,
    sector_scores: Optional[List[SectorScore]] = None,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[WatchlistCheckItem]:
    """自选体检 I/O 入口(角色对应 `candidates.build_candidates`)。`watchlist_items`
    通常是 `neckline.watchlist.list_watchlist()` 每项的 `to_dict()`(含 ts_code/
    name/pinned/source)——传 dict 而非强类型,好让本模块与 `neckline.watchlist`
    的表结构解耦。空自选池 → 直接返回空列表,不建面板(省一次 I/O)。"""
    if not watchlist_items:
        return []
    cfg = MomentumConfig(**rule["config"])
    panel = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    return score_watchlist(
        panel, cfg, watchlist_items,
        sector_scores=sector_scores, member_map=member_map, index_names=index_names, db_path=db_path,
    )


def score_watchlist(
    panel: pl.DataFrame,
    cfg: MomentumConfig,
    watchlist_items: List[Dict[str, Any]],
    *,
    sector_scores: Optional[List[SectorScore]] = None,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    db_path: Optional[Path] = None,
) -> List[WatchlistCheckItem]:
    """给一个已算好特征的单日面板核对自选池每只票(角色对应
    `candidates.score_candidates`:纯函数可测,不碰 I/O)。评分/红绿灯/买点触发
    三类派生列在**全市场面板**上一次性算好(`with_columns`,与 rule v1 当前
    `rank_by="dist_from_high_20d"` 是逐行公式、不依赖横截面上下文,故与
    `candidates.py` 只在"mask 通过的候选子集"上算分在数值上恒等;仅当未来大脑
    版本换成需要横截面排名的 `rank_by` 时,两处percentile 的分母集合才会不同——
    这是已知的、目前不可能触发的简化,写在此处供未来排查),再按 `ts_code` 抽取
    自选池那几行,不额外对全市场每行都构造 Python 对象。"""
    if not watchlist_items:
        return []
    if panel.is_empty():
        return [_no_data_item(w) for w in watchlist_items]

    checks = discipline_checks(cfg)
    annotated = panel.with_columns(
        [expr.alias(col) for col, _label, expr in checks]
        + [build_entry_mask(cfg).alias("_entry_mask"), _base_score_expr(cfg).alias("_base_score")]
    )
    codes = [w["ts_code"] for w in watchlist_items]
    sub = annotated.filter(pl.col("ts_code").is_in(codes))
    rows_by_code: Dict[str, Dict[str, Any]] = (
        {r["ts_code"]: r for r in sub.to_dicts()} if not sub.is_empty() else {}
    )

    member_map = member_map or {}
    index_names = index_names or {}
    hot = sector_hot_lookup(sector_scores or [])
    names = _load_stock_names(codes, db_path)

    out: List[WatchlistCheckItem] = []
    for w in watchlist_items:
        row = rows_by_code.get(w["ts_code"])
        if row is None:
            out.append(_no_data_item(w))
            continue
        out.append(_score_row(row, w, cfg, checks, member_map, index_names, hot, names))
    return out


def _score_row(
    row: Dict[str, Any],
    w: Dict[str, Any],
    cfg: MomentumConfig,
    checks: List[Tuple[str, str, pl.Expr]],
    member_map: Dict[str, List[str]],
    index_names: Dict[str, str],
    hot: Dict[str, SectorScore],
    names: Dict[str, str],
) -> WatchlistCheckItem:
    code = w["ts_code"]
    disqualifiers = [label for col, label, _expr in checks if row.get(col)]
    green_light = not disqualifiers
    buy_point_triggered = bool(row.get("_entry_mask"))

    boards = member_map.get(code, [])
    hot_names = [
        f"{hot[b].name}(板块年龄{hot[b].board_age}天,20日{hot[b].ret_20d:+.1%})"
        for b in boards if b in hot
    ]
    sector_names = [index_names.get(b, b) for b in boards]
    bonus = max((hot[b].bonus for b in boards if b in hot), default=0.0)
    score = round(float(row.get("_base_score") or 0.0) + bonus, 1)

    item = WatchlistCheckItem(
        ts_code=code, name=names.get(code, w.get("name") or code),
        pinned=bool(w.get("pinned")), source=w.get("source", "manual"),
        has_data=True, close=float(row.get("close") or 0.0), board=row.get("board", "MAIN"),
        score=score, pattern_tags=pattern_tags(row),
        hot_sectors=hot_names, sector_names=sector_names,
        green_light=green_light, disqualifiers=disqualifiers,
        buy_point_triggered=buy_point_triggered,
    )
    if buy_point_triggered:
        stop_price = round(item.close * (1 - cfg.stop_pct), 2) if cfg.stop_pct else None
        spec = invalidation_spec()
        item.entry_plan = entry_plan_text(row, cfg)
        item.stop_loss = stop_loss_text(stop_price, cfg)
        item.target = target_text(cfg)
        item.invalidation_text = invalidation_text(spec)
        item.invalidation_spec = spec
        item.entry_spec = entry_spec(row, cfg)
    else:
        item.entry_plan = "今日未触发母战法买点(仅供关注,非现在买入建议)。"
        item.stop_loss = "未触发买点,暂无参考止损价。"
        item.target = "未触发买点,暂无止盈计划。"
        item.invalidation_text = "未触发买点,暂无证伪条件。"
    return item


# —— 状态变化 diff + LLM 控成本(plan v1.1-C.3)——————————————————————————————

def _is_changed(prev: Optional[Dict[str, Any]], cur: WatchlistCheckItem) -> bool:
    """状态变化定义(任务原文钉死,单测锁死此定义):
        · 无上一份报告快照(该票首次出现在自选体检里)→ 视为变化(给新入选自选票
          至少一次 LLM 审视机会)。
        · 否则,以下任一为真 → 变化:
            ① 纪律红绿灯翻转(`green_light` 不等,任一方向);
            ② 买点触发状态翻转(`buy_point_triggered` 不等,任一方向——任务原文
               点名的"首次触发买点"是"False→True"这一个方向,这里对称处理
               "True→False"〔买点条件不再成立〕也算变化,更贴合"值得再看一眼"
               的直觉,不缩小任务原文覆盖的范围);
            ③ 形态标签集合变化(`pattern_tags` 按集合比较,忽略顺序与重复)。
    与上一份报告的 `watchlistCheck` 快照(`WatchlistCheckItem.public_dict()` 的
    JSON 往返)逐字段 diff,不比较 score(评分本就每日随行情连续变动,不是「状态」)。
    """
    if prev is None:
        return True
    if bool(prev.get("green_light")) != cur.green_light:
        return True
    if bool(prev.get("buy_point_triggered")) != cur.buy_point_triggered:
        return True
    if set(prev.get("pattern_tags") or []) != set(cur.pattern_tags):
        return True
    return False


def apply_llm_review(
    items: List[WatchlistCheckItem],
    previous_snapshot: Dict[str, Dict[str, Any]],
    *,
    provider: Optional[LLMProvider],
    top_list: Optional[Dict[str, Dict[str, Any]]] = None,
    transport: Optional[Any] = None,
) -> None:
    """原地(in-place)标记每项的 `status_changed`,并对「`status_changed` ∪
    `pinned`」且 `has_data=True` 的项跑 LLM(复用 `llm.judge.judge_candidate`,
    §v1.1-C.3「LLM 控成本」)。`previous_snapshot`:`ts_code -> `上一份报告的
    `WatchlistCheckItem.public_dict()``(无上一份报告 → 传空 dict,此时全部视为
    首次出现从而 `status_changed=True`,见 `_is_changed`)。`provider=None`(缺
    key)→ `judge_candidate` 走「未激活」占位降级,全链路不崩(继承 §2.4/§3.4
    降级链)。"""
    top_list = top_list or {}
    for item in items:
        item.status_changed = _is_changed(previous_snapshot.get(item.ts_code), item)
        if not item.has_data:
            continue
        if not (item.status_changed or item.pinned):
            continue
        result: JudgeResult = judge_candidate(
            item, provider=provider, top_list_row=top_list.get(item.ts_code),
            transport=transport, system_prompt=WATCHLIST_JUDGE_SYSTEM_PROMPT,
        )
        item.llm_judgment = {
            "verdict": result.verdict, "narrative": result.narrative, "degraded": result.degraded,
        }


__all__ = [
    "WatchlistCheckItem",
    "NO_DATA_REASON",
    "discipline_checks",
    "build_watchlist_check",
    "score_watchlist",
    "apply_llm_review",
]
