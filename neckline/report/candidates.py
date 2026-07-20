"""候选评分管线(plan 2.3,§2.3/§2.6 铁律)。

**铁律(2026-07-20 用户拍板 + 阶段1判决,凌驾于历史假设文字)**:候选评分**必须
从策略大脑读取现行规则**(`neckline.strategy.brain.get_active()`),三跑道同码——
直接复用 `neckline.strategy.momentum.build_entry_mask`,**禁止在报告管线里另写
一份信号逻辑**。本模块不直接读大脑(那是 `pipeline.py` 的职责),`build_candidates`
接收调用方传入的 `rule` 字典,便于单测注入任意规则、也便于历史回放传入"当时的"
版本(阶段2.6)。

**评分口径(诚实标注,不得省略)**:规则 v1 是一套经回测验证的**减损纪律系统,不是
正 alpha**(见 `research/stage1_report.md` 收口结论)。本模块的"评分"只是**展示
排序分**——基础项呼应回测里 `MomentumStrategy` 用来分配有限建仓名额的
`rank_by`(浅回调优先),外加 §2.2 板块热度的小额软加分(只加分,不改变入池资格)。
**不代表任何收益预测**,分数越高不等于"更可能赚钱",报告渲染层不得暗示这一点。

四件套(买点/止损/目标/证伪条件)照 §2.2/§2.3 设计意图输出;证伪条件用价量结构
写死(结构化 + 自然语言两份),供阶段3 哨兵消费。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import polars as pl

from neckline.data.market_data import load_stock_basic
from neckline.report.sectors import SectorScore, sector_hot_lookup
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig, build_entry_mask

# —— 证伪条件阈值(结构化,写死供阶段3哨兵消费;只用价量结构,不看资金面,§2.4)——
LOW_OPEN_PCT = -0.02
VOL_RATIO_LOW = 0.8
VOL_RATIO_HIGH = 3.0

_BOARD_LABEL = {"GEM": "创业板", "STAR": "科创板", "BSE": "北交所"}


@dataclass
class Candidate:
    ts_code: str
    name: str
    close: float
    score: float
    rank: int
    board: str
    pattern_tags: List[str]
    hot_sectors: List[str]           # 命中的【今日热门】概念板块名(§2.2 加分来源)
    sector_names: List[str]          # 所属全部概念板块名(不限热门,供 LLM 审判上下文)
    entry_plan: str
    stop_loss: str
    target: str
    invalidation_text: str
    invalidation_spec: Dict[str, Any]
    raw: Dict[str, Any] = field(default_factory=dict)  # 原始特征行,供 judge.py 组装上下文

    def public_dict(self) -> Dict[str, Any]:
        """报告落库(JSON)/展示用的精简视图,**不含**内部特征行 `raw`(那是几十列
        的技术特征,只在生成阶段临时用来组装四件套/喂 LLM 上下文,不适合作为报告
        存档字段——报告存档只需要"报告展示了什么",不需要内部计算细节)。"""
        d = asdict(self)
        d.pop("raw", None)
        return d


def build_candidates(
    trade_date: date,
    rule: Dict[str, Any],
    *,
    sector_scores: Optional[List[SectorScore]] = None,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    top_n: int = 20,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> List[Candidate]:
    """报告候选管线入口。`rule` 是大脑 `StrategyVersion.rule`(含 `rule["config"]`,
    喂 `MomentumConfig(**...)`)——调用方(`pipeline.py`)负责从
    `neckline.strategy.brain.get_active()` 取。`build_research_panel(trade_date,
    trade_date, ...)` 正是 `features.py` docstring 里"喂今日"的报告跑道:内部按需
    加载前置窗口算特征,裁剪后只留 `trade_date` 这一天的全市场横截面。
    """
    cfg = MomentumConfig(**rule["config"])
    panel = build_research_panel(trade_date, trade_date, with_forward=False, parquet_dir=parquet_dir)
    if panel.is_empty():
        return []
    panel = S.add_ret_rank_column(panel)
    return score_candidates(
        panel,
        cfg,
        sector_scores=sector_scores,
        member_map=member_map,
        index_names=index_names,
        top_n=top_n,
        db_path=db_path,
    )


def score_candidates(
    panel: pl.DataFrame,
    cfg: MomentumConfig,
    *,
    sector_scores: Optional[List[SectorScore]] = None,
    member_map: Optional[Dict[str, List[str]]] = None,
    index_names: Optional[Dict[str, str]] = None,
    top_n: int = 20,
    db_path: Optional[Path] = None,
) -> List[Candidate]:
    """给一个已算好特征的单日面板打分排序、生成四件套。与 `build_candidates` 拆开
    是为了单测能绕过整条 I/O 数据管线,直接对手工构造的 DataFrame 断言评分/四件套
    逻辑;entry mask 仍是同一份 `build_entry_mask(cfg)`(§2.6 同码,不是另一份拷贝)。
    """
    mask = build_entry_mask(cfg)
    cands = panel.filter(mask)
    if cands.is_empty():
        return []

    member_map = member_map or {}
    index_names = index_names or {}
    hot = sector_hot_lookup(sector_scores or [])

    bonus_lookup = {
        code: max((hot[b].bonus for b in boards if b in hot), default=0.0)
        for code, boards in member_map.items()
    }
    if bonus_lookup:
        bonus_df = pl.DataFrame({"ts_code": list(bonus_lookup.keys()), "_bonus": list(bonus_lookup.values())})
        cands = cands.join(bonus_df, on="ts_code", how="left").with_columns(pl.col("_bonus").fill_null(0.0))
    else:
        cands = cands.with_columns(pl.lit(0.0).alias("_bonus"))

    cands = cands.with_columns(_base_score_expr(cfg).alias("_base_score"))
    cands = cands.with_columns((pl.col("_base_score") + pl.col("_bonus")).round(1).alias("_score"))
    cands = cands.sort("_score", descending=True, nulls_last=True)

    top_rows = cands.head(top_n).to_dicts()
    names = _load_stock_names([r["ts_code"] for r in top_rows], db_path)

    out: List[Candidate] = []
    for i, row in enumerate(top_rows, start=1):
        boards = member_map.get(row["ts_code"], [])
        hot_names = [hot[b].name for b in boards if b in hot]
        sector_names = [index_names.get(b, b) for b in boards]
        out.append(
            _build_candidate(
                row,
                score=row["_score"],
                rank=i,
                cfg=cfg,
                hot_sectors=hot_names,
                sector_names=sector_names,
                name=names.get(row["ts_code"], row["ts_code"]),
            )
        )
    return out


def _base_score_expr(cfg: MomentumConfig) -> pl.Expr:
    """展示排序分的基础项(§2.3:仅用于本报告展示排序,不代表 alpha 强度)。rule v1
    的 `rank_by="dist_from_high_20d"`(越贴近20日高点=越浅回调,分越高,与回测里
    `MomentumStrategy` 分配有限建仓名额用的同一列)值域已知([-1,0] 附近),线性映射
    到 0~100。若未来大脑版本换了别的 `rank_by` 列(值域未知),退化为当日横截面
    百分位排名 × 100——保证任何列都给出一个可比的 0~100 展示分,不崩、不出离谱数值。
    """
    if cfg.rank_by == "dist_from_high_20d":
        base = (1.0 + pl.col("dist_from_high_20d").fill_null(-1.0)) * 100.0
        return base if cfg.rank_desc else (100.0 - base)
    col = pl.col(cfg.rank_by)
    pct = col.rank(method="average") / col.count()
    return (pct * 100.0) if cfg.rank_desc else ((1.0 - pct) * 100.0)


def _load_stock_names(codes: List[str], db_path: Optional[Path]) -> Dict[str, str]:
    if not codes:
        return {}
    sb = load_stock_basic(db_path)
    if sb.is_empty():
        return {}
    sb = sb.filter(pl.col("ts_code").is_in(codes)).select(["ts_code", "name"])
    return dict(zip(sb["ts_code"].to_list(), sb["name"].to_list()))


def _build_candidate(
    row: Dict[str, Any],
    *,
    score: float,
    rank: int,
    cfg: MomentumConfig,
    hot_sectors: List[str],
    sector_names: List[str],
    name: str,
) -> Candidate:
    close = row["close"]
    stop_price = round(close * (1 - cfg.stop_pct), 2) if cfg.stop_pct else None
    spec = invalidation_spec()
    return Candidate(
        ts_code=row["ts_code"],
        name=name,
        close=close,
        score=score,
        rank=rank,
        board=row.get("board", "MAIN"),
        pattern_tags=pattern_tags(row),
        hot_sectors=hot_sectors,
        sector_names=sector_names,
        entry_plan=entry_plan_text(row, cfg),
        stop_loss=stop_loss_text(stop_price, cfg),
        target=target_text(cfg),
        invalidation_text=invalidation_text(spec),
        invalidation_spec=spec,
        raw=row,
    )


# —— 四件套文案(自然语言,§2.7 全系统 LLM 输出硬约束不适用于此处的确定性规则文案,
#    但同样不写枚举卡样式,写成一句连贯的话)——————————————————————————————

def entry_plan_text(row: Dict[str, Any], cfg: MomentumConfig) -> str:
    close = row["close"]
    if cfg.buypoint == "breakout":
        platform_high = row.get("prev_close_max_20d")
        vr = row.get("vol_ratio_5")
        ph_txt = f"{platform_high:.2f}" if platform_high is not None else "未知"
        vr_txt = f"{vr:.2f}" if vr is not None else "未知"
        return f"平台放量突破:现价 {close:.2f} 已突破前 20 日收盘高点({ph_txt}),量比 {vr_txt};跟随突破节奏,不追高于突破当日过多的位置。"
    ma10 = row.get("ma10")
    dist = row.get("dist_from_high_20d")
    ma10_txt = f"{ma10:.2f}" if ma10 is not None else "未知"
    dist_txt = f"{dist:+.1%}" if dist is not None else "未知"
    return (
        f"回调低吸:现价 {close:.2f},站稳 10 日线(MA10≈{ma10_txt})不破位,"
        f"距 20 日高点 {dist_txt}(越浅回调优先);次日维持强势结构可低吸,不追高开缺口。"
    )


def stop_loss_text(stop_price: Optional[float], cfg: MomentumConfig) -> str:
    if stop_price is None or cfg.stop_pct is None:
        return "本版规则未设固定止损(异常状态——rule v1 恒为 -5%,若看到这行文案请检查大脑配置)。"
    return (
        f"参考止损价约 {stop_price:.2f} 元(现价基准 ×(1-{cfg.stop_pct:.0%});以实际成交价为准)。"
        f"-5% 是全系统单一止损常量(§2.1),须挂券商条件单,只设、不许撤、不许下调。"
    )


def target_text(cfg: MomentumConfig) -> str:
    parts = ["不设固定止盈线(§2.1 纪律:止盈不设固定线)"]
    if cfg.take_profit_retrace:
        parts.append(
            f"自建仓收盘峰值回落 ≥{cfg.take_profit_retrace:.0%} 次日开盘离场"
            f"(回落止盈,阶段1证据偏弱,待walk-forward复核)"
        )
    parts.append(f"或持有满 {cfg.max_hold_days} 个交易日无条件离场(时间退出,母战法 2-5 日区间)")
    return ";".join(parts) + "。"


def invalidation_spec() -> Dict[str, Any]:
    """证伪条件(结构化,§2.3「用价量结构写死供阶段3哨兵消费」)。只用价量,不看
    资金面(§2.4 铁律:盘中主力资金流免费源不可靠)。"""
    return {
        "low_open_pct": LOW_OPEN_PCT,
        "require_stay_below_prev_close": True,   # 全天未能翻红(收盘仍 < 昨收)
        "vwap_break": True,                        # 全天收盘价 < 当日 VWAP
        "vol_ratio_low": VOL_RATIO_LOW,
        "vol_ratio_high": VOL_RATIO_HIGH,
    }


def invalidation_text(spec: Dict[str, Any]) -> str:
    return (
        f"次日低开 ≤{spec['low_open_pct']:.0%} 且全天未翻红;或全天收盘价跌破当日 VWAP;"
        f"或量比萎缩至 <{spec['vol_ratio_low']:.1f} 倍(地量无接力)或暴增至 >{spec['vol_ratio_high']:.1f} 倍"
        f"(异常放量疑似出货)——命中任一条,盘中剔除勿进(阶段3证伪哨兵消费本条,只用价量结构)。"
    )


def pattern_tags(row: Dict[str, Any]) -> List[str]:
    """价量结构形态标签(确定性,不耗 LLM;后10只候选只展示这个 + 评分)。"""
    tags: List[str] = []
    consec = row.get("consec_limit_up_days") or 0
    if consec >= 2:
        tags.append(f"连板{int(consec)}日")
    elif row.get("is_limit_up"):
        tags.append("今日涨停")
    if (row.get("limitup_count_20d") or 0) >= 1:
        tags.append("近20日有涨停")
    dist = row.get("dist_from_high_20d")
    if dist is not None:
        if dist >= -0.05:
            tags.append("浅回调贴前高")
        elif dist <= -0.15:
            tags.append("深回调")
    vr = row.get("vol_ratio_5")
    if vr is not None:
        if vr < 0.8:
            tags.append("缩量")
        elif vr >= 1.5:
            tags.append("放量")
    if row.get("above_ma20_bullish"):
        tags.append("均线多头")
    board = row.get("board")
    if board in _BOARD_LABEL:
        tags.append(_BOARD_LABEL[board])
    return tags


__all__ = [
    "Candidate",
    "build_candidates",
    "score_candidates",
    "entry_plan_text",
    "stop_loss_text",
    "target_text",
    "invalidation_spec",
    "invalidation_text",
    "pattern_tags",
    "LOW_OPEN_PCT",
    "VOL_RATIO_LOW",
    "VOL_RATIO_HIGH",
]
