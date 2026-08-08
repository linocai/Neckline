"""篮子日报的视图模型(plan §五 V2-⑭-A)。把**已经冻结在库里的**篮子四表读成一份
报告快照结构:③ 今日篮子(V2.1 起 T1/T2,每篮一张卡)+ ③b 今日未定档篮子 + ④ 昨日
篮子复盘。

⚠ **本模块是冻结快照的读侧,一律按"数据里实际有什么"构造,⛔ 不按"引擎现在支持
什么"写死**(V2.1-②):`reports.basket_daily_json` 里躺着 V2 时代含 tier=3 的老报告,
读侧写死两档会让它们静默消失 —— 见 `BasketDaily.by_tier()`。

**本模块只读、不判、不算策略**:篮子、Tier、卡、验证、复盘全部由 ⑤⑥⑦⑧⑨ 在各自的段
落里算完落库,这里只负责「读回来 + 转成契约形状 + 每一段各自包保险丝」。⛔ 不许在这里
重算任何一个判据 —— 报告是**展示层**,重算等于开第二个事实源。

**三处纪律,写在最前面**

1. **③b 的两个原因码不许合并**(⑥-b-C / ⑭-A):`capacity_overflow`(分数够、位置满 =
   今天机会多到装不下)与 `below_quality_line`(**V2.1-② 起 = 连 `tier2_min` 都没过**
   = 今天没什么好货;⚠ **码一字不改**,变的只是"最低档"指哪一档)
   指向**相反的市场结论**,合成一句「未入选」就把两件事讲成了一件。零溢出时这一节**仍在**
   (节在 = 算过了),写「今日无未定档篮子」。
2. **「没有」与「没看」必须分开**:每一段都带 `available` + `unavailableReason`。
   `baskets=[]` 且 `available=True` = 今天真没有篮子(合法输出,⑥-b-B);
   `available=False` = 这一段本次没取到(读历史快照 / 该段降级),⛔ 不许拿空数组冒充。
   **§七 P0-39(2026-08-05,生产实打后加固)**:③ 的 `available` 曾经挂在
   「`load_today_baskets()` 读表成功」上 —— 那只证明**表读得出来**,不证明**引擎跑过**,
   于是 `no_provider` 全缺席时报告照样输出「今天没有共同驱动清晰、成员结构够格的篮子」
   这句**实质性市场判断**。现在改成:**有篮子** = 引擎跑过的活证据 → `True`;**零篮子**
   → 查 ⑤ 的段状态(`selection/basket_stage_handoff.py`,判读唯一实现在那儿)三态定夺,
   见 `_zero_basket_verdict`。⛔ 别再把"读得出表"当"跑过引擎"。
3. **`droppedBaskets` 只能由「本次跑 ⑥」的那次运行传进来,⛔ 本函数不许现算**:
   ⑥ 的 `TierResult.dropped` **不进 `baskets` 表**(`baskets.tier` NOT NULL,溢出篮
   无档可填),报告快照(`reports.basket_daily_json`)是它的落点。**V2-⑯-D 补记**:
   三段拆进程后「本次跑 ⑥」不必与「本次出报告」同一个进程——
   `report/evening.py::run_evening_chain` 在报告段独立跑(SEG_BASKET 不在本次
   `segments` 里)时,会去跨进程交接表(`selection/basket_dropped_handoff.py`,
   按 `trade_date` 存最近一次 ⑤⑥ 的结果)找"今晚早些时候〔另一个进程里〕⑥ 是否
   跑过"——**读的是同一晚同一次 ⑥ 运行留下的事实,不是重算**。本函数
   (`build_basket_daily`)的契约不变:`dropped=None` 就是"本次未取得",不关心
   调用方是从内存还是从交接表拿到的答案。历史回放(`scripts/report.py` 直调
   `pipeline.build_report`,不经过 `evening.py`)仍然拿不到 → 如实标
   `available=False`,⛔ 不许现算一遍"当时会溢出哪些"(那是拿今天的包/今天的
   数据编造昨天的结论)。

**camelCase 转换点单一**:`card_to_public_dict()` 是 `basket_cards.card_json`
(snake_case 冻结件)→ 契约 camelCase 的**唯一**实现,报告快照与 `GET /baskets/{id}/card`
两条路都走它,不各写一份(v1.3-⑥「领域层算了但 API 层没读」那类漂移的预防)。
⚠ **只转字段名,不转"数据键"**:`tier_breakdown` 的键是五维**维度名**(`driver_freshness`
等,与 K7-pack 的权重键逐字对应)、`verification_spec`/`invalidation_spec` 是喂给 ⑧ 哨兵
的结构化 spec —— 这些嵌套字典**原样透传**,把它们也 camel 化会把语义标识符改名。

**exec_hint 接线(⑬-4 的落点)**:⑬-4 原文「四条计算并入篮子剧本的输入」——⑦ 的卡生成
不该反向 import `report/`,故接线点在这里:按篮子成员算 `exec_hints_for()`,**挂在卡的
旁边**(`BasketView.exec_hints`),⛔ 不注进 `card` 字典里 —— 那份 JSON 是 D0 冻结件,
报告展示层不许往里塞东西。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# exec_hint 用的轻量特征面板回看窗(自然日)。C1/C2/C4 只要 `ret_1d`/`is_limit_up`/
# `close`/`ma20`,**ma20 = 20 个交易日**,60 自然日绰绰有余 —— 刻意**不**复用
# `holding_k4_check._LOOKBACK_CALENDAR_DAYS`(420 天,那是 ma250 的需求):篮子成员
# 数量级是持仓的 20~30 倍,照搬 420 天 × 4 张表的取数会把 16:35 报告拖成分钟级
# (§七 P1-26 的同一课:取数范围要按真实需要裁,不按"抄一个现成的")。
_EXEC_HINT_LOOKBACK_CALENDAR_DAYS = 60

# ③b 两个原因码(唯一源在 `selection/tier.py`,这里只做**展示文案**映射 —— 码本身
# 从传进来的 `DroppedBasket.reason` 原样透出,不在本模块重定义)。
# ⚠ V2.1-②:`below_quality_line` 的**码一字不改**(⑨ 按原因码归因,改码 = 历史归因
# 断线),只改展示文案里的档位名(T3 下限 → T2 下限)。
DROPPED_REASON_LABEL: Dict[str, str] = {
    "capacity_overflow": "档位已满(分数够、今天位置装不下)",
    "below_quality_line": "未过质量线(连 T2 下限都没到)",
}


# ══════════════════════════════════════════════════════════════════════════
# card_json(snake) → 契约(camel):唯一转换点
# ══════════════════════════════════════════════════════════════════════════

# 顶层字段名映射。**显式清单而非通用递归**:通用递归会把 `tier_breakdown` 的维度名、
# `verification_spec` 的条件键一起改名(那些是语义标识符,不是字段名)。
_CARD_TOP_KEYS: Tuple[Tuple[str, str], ...] = (
    ("spec_version", "specVersion"),
    ("version", "version"),
    ("basket_key", "basketKey"),
    ("trade_date", "tradeDate"),
    ("next_trade_date", "nextTradeDate"),
    ("name", "name"),
    ("driver", "driver"),
    ("driver_kind", "driverKind"),
    ("evidence", "evidence"),
    ("evidence_status", "evidenceStatus"),
    ("why_now", "whyNow"),
    ("role_conflicts", "roleConflicts"),
    ("tier", "tier"),
    ("rank_in_tier", "rankInTier"),
    ("rank_mech", "rankMech"),
    ("mech_score", "mechScore"),
    ("tier_breakdown", "tierBreakdown"),
    ("tier_reason", "tierReason"),
    ("tier_note", "tierNote"),
    ("scripts", "scripts"),
    ("scripts_unavailable_reason", "scriptsUnavailableReason"),
    ("verification_spec", "verificationSpec"),
    ("verification_text", "verificationText"),
    ("invalidation_spec", "invalidationSpec"),
    ("invalidation_text", "invalidationText"),
    ("risks", "risks"),
    ("disclaimer", "disclaimer"),
    ("discipline_labels", "disciplineLabels"),
    ("narrative", "narrative"),
    ("llm_stage", "llmStage"),
    ("degraded", "degraded"),
    ("notes", "notes"),
)

_CARD_MEMBER_KEYS: Tuple[Tuple[str, str], ...] = (
    ("ts_code", "tsCode"),
    ("name", "name"),
    ("role_llm", "roleLlm"),
    ("role_mech", "roleMech"),
    ("reason", "reason"),
    ("industry", "industry"),
    ("industry_lift", "industryLift"),
    ("lift_reason", "liftReason"),
    ("primary_reason", "primaryReason"),
    ("rs_rank", "rsRank"),
    ("k4_tag", "k4Tag"),
    ("mech", "mech"),
    ("entry_zone", "entryZone"),
    ("entry_zone_clamp", "entryZoneClamp"),
    ("entry_zone_unavailable_reason", "entryZoneUnavailableReason"),
    ("max_chase", "maxChase"),
    ("max_chase_clamp", "maxChaseClamp"),
    ("max_chase_unavailable_reason", "maxChaseUnavailableReason"),
    ("exit_reference", "exitReference"),
    ("exit_reference_clamp", "exitReferenceClamp"),
    ("exit_reference_unavailable_reason", "exitReferenceUnavailableReason"),
    ("tags", "tags"),
    ("tags_absent", "tagsAbsent"),
)

# `fingerprint` 里的键**是字段名**(不是语义标识符),照转。
_CARD_FINGERPRINT_KEYS: Tuple[Tuple[str, str], ...] = (
    ("stop_pct", "stopPct"),
    ("take_profit_retrace", "takeProfitRetrace"),
    ("charter_version", "charterVersion"),
    ("pack_version", "packVersion"),
    ("engine_api_version", "engineApiVersion"),
    ("verification_ruleset_version", "verificationRulesetVersion"),
)


def _pick(src: Mapping[str, Any], keys: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
    """按映射表取键。**缺键 → 该键不出现**(不是补 None):`card_json` 是冻结快照,
    老卡本来就可能没有新键,补一个 `null` 会把「这一版卡没有这个概念」讲成「有这个
    概念但值是空」。客户端那侧的对策是 `decodeIfPresent`(CLAUDE.md 冻结快照两类论)。"""
    out: Dict[str, Any] = {}
    for snake, camel in keys:
        if snake in src:
            out[camel] = src[snake]
    return out


def card_member_to_public_dict(m: Mapping[str, Any]) -> Dict[str, Any]:
    """卡上一名成员(`card_json.members[i]`)→ 契约 camelCase。

    `role_conflict`/`is_primary` 在冻结件里是 **0/1 整数**(SQLite 与 JSON 都不区分
    bool),契约面转成真 bool —— Swift 那侧 `Bool` 解 `0`/`1` 会直接失败,这是**转换点
    必须做的一件实事**,不是美化。"""
    out = _pick(m, _CARD_MEMBER_KEYS)
    if "role_conflict" in m:
        out["roleConflict"] = bool(m.get("role_conflict"))
    if "is_primary" in m:
        out["isPrimary"] = bool(m.get("is_primary"))
    return out


def card_to_public_dict(card: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """`basket_cards.card_json`(snake 冻结件)→ 契约 camelCase。**唯一转换点**。

    `None` 进 → `None` 出(「这个篮子的卡还没生成」= `card_not_ready`,由调用方表达,
    本函数不替它编一张空卡)。"""
    if card is None:
        return None
    if not isinstance(card, Mapping):
        logger.warning("[basket_daily] card_json 不是字典(%s),按无卡处理", type(card).__name__)
        return None
    out = _pick(card, _CARD_TOP_KEYS)
    out["members"] = [card_member_to_public_dict(m) for m in (card.get("members") or [])
                      if isinstance(m, Mapping)]
    fp = card.get("fingerprint")
    if isinstance(fp, Mapping):
        out["fingerprint"] = _pick(fp, _CARD_FINGERPRINT_KEYS)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 视图对象
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class DroppedBasketView:
    """③b 一行。契约字段就三个(⑭-A 原文 `{name, mechScore, reason}`)——**没有
    `basketId`**:它没进 `baskets` 表,给一个 id 会让客户端以为点得进去。"""

    name: str
    mech_score: Optional[float]
    reason: str

    @property
    def reason_label(self) -> str:
        return DROPPED_REASON_LABEL.get(self.reason, self.reason)

    def to_public_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "mechScore": self.mech_score, "reason": self.reason}


@dataclass
class BasketView:
    """③ 一篮。`card=None` 且 `card_unavailable_reason='card_not_ready'` = 篮子在、
    卡没生成(⑦ 的事务 2 独立于事务 1,这是**合法中间态**,不是 bug)。"""

    basket_id: int
    basket_key: str
    name: str
    tier: Optional[int]
    member_codes: Tuple[str, ...]
    card: Optional[Dict[str, Any]] = None
    card_version: Optional[int] = None
    card_unavailable_reason: Optional[str] = None
    exec_hints: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # V2.1-④ 百分制打分卡:`report/score_display.score_view()` 的**完整返回**
    # (`scorePercent` / `contributions` / `neutralFilledPercent` / `note`)。
    # **整份留着、只往契约里放两键**,理由两条:① 一个来源 —— markdown 渲染要的
    # `note`/`neutralFilledPercent` 与契约要的两键出自同一次换算,不各算一遍;
    # ② 契约面**只新增 4 个只读键**(本 DTO 两个 + `TierOut` 两个)是 ⑧ 对拍表数
    # 死的,⛔ 别顺手把 note 也发出去。
    # 🔴 **契约两键是 B 类:随报告冻住** —— 老报告读回来根本没有它们(不是 `null`,
    # 是**没有这个键**),客户端一律 `decodeIfPresent` 兜底、如实说「该报告版本无
    # 打分」;`None` = 本篮没有定档留痕 / 留痕里没有 breakdown。**两者都不是 0 分。**
    score: Optional[Dict[str, Any]] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "basketId": self.basket_id,
            "basketKey": self.basket_key,
            "name": self.name,
            "tier": self.tier,
            "memberCodes": list(self.member_codes),
            "card": self.card,
            "cardVersion": self.card_version,
            "cardUnavailableReason": self.card_unavailable_reason,
            "execHints": {k: list(v) for k, v in self.exec_hints.items()},
            # 新报告**永远带这两个键**(取不到分时 `null` + `[]`);老报告的这份 JSON
            # 里它们**根本不存在**。⚠ **如实登记一处不可区分**:两者经
            # `api/schemas.py::BasketOut` 收口后都变成 `scorePercent=null` + `[]`
            # (pydantic 序列化必然带上全部字段),客户端两种情况说的是同一句
            # 「本篮无打分」。这是可接受的 —— 两种成因给用户的动作完全相同(等下一份
            # 报告),⛔ 但别据此以为快照层也不需要区分:`scoreContributions` 恒为
            # 数组(⛔ 不是 `null`)正是为了让 `BasketOut` 那个非 Optional 的
            # `List[ScoreContribOut]` 收得下老快照与无分快照两种输入。
            "scorePercent": (self.score or {}).get("scorePercent"),
            "scoreContributions": [
                dict(c) for c in ((self.score or {}).get("contributions") or [])
            ],
        }


@dataclass
class BasketReviewView:
    """④ 一篮的昨日复盘。`mech` 是 ⑨ 落库的九项机械判原样(自由结构,**原样透传**
    ——在 API 层再镜像一套嵌套模型只会多一处会漂的定义,同 `WeeklyReviewOut.result`
    的既定惯例);`verification` 是 ⑧ 对同一篮同一天的「当前状态」三路读法结果。"""

    basket_id: int
    basket_key: str
    name: str
    tier: Optional[int]
    d0: str
    review_date: str
    depth: str
    mech: Dict[str, Any] = field(default_factory=dict)
    llm_text: Optional[str] = None
    llm_skip_reason: Optional[str] = None
    degraded: bool = False
    verification: Optional[Dict[str, Any]] = None

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "basketId": self.basket_id,
            "basketKey": self.basket_key,
            "name": self.name,
            "tier": self.tier,
            "d0": self.d0,
            "reviewDate": self.review_date,
            "depth": self.depth,
            "mech": self.mech,
            "llmText": self.llm_text,
            "llmSkipReason": self.llm_skip_reason,
            "degraded": self.degraded,
            "verification": self.verification,
        }


@dataclass
class BasketDaily:
    """一份篮子日报的结构化快照(落 `reports.basket_daily_json`,camelCase)。"""

    trade_date: date
    baskets: List[BasketView] = field(default_factory=list)
    baskets_available: bool = False
    baskets_unavailable_reason: Optional[str] = None
    dropped: List[DroppedBasketView] = field(default_factory=list)
    dropped_available: bool = False
    dropped_unavailable_reason: Optional[str] = None
    reviews: List[BasketReviewView] = field(default_factory=list)
    reviews_available: bool = False
    reviews_unavailable_reason: Optional[str] = None
    review_d0: Optional[str] = None
    pack_version: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def by_tier(self) -> Dict[int, List[BasketView]]:
        """按**实际出现的档位**分组(V2.1-②)。

        🔴 **⛔ 不许写死档位元组**:`basket_daily_json` 是**冻结快照**,读回一份 V2
        时代的老报告时里面就有 tier=3 的篮子 —— 写死 `{1:[],2:[]}` 会让它们**静默
        消失**(那是"删了历史",不是"退役新档")。写死 `{1:[],2:[],3:[]}` 同样不行:
        新报告会凭空多出一个恒空的幽灵档。**读侧宽容、写侧收紧**是这条的完整表述,
        写侧收紧在 `selection/tier.py::TIERS`。

        `tier is None`(极旧快照 / 数据缺口)的篮子**不进任何档** —— 它在 ③ 节不显示,
        但仍在 `self.baskets` 里,⛔ 别拿一个假档位把它塞进去。
        """
        out: Dict[int, List[BasketView]] = {}
        for b in self.baskets:
            if b.tier is None:
                continue
            out.setdefault(int(b.tier), []).append(b)
        return dict(sorted(out.items()))

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date.strftime("%Y%m%d"),
            "baskets": [b.to_public_dict() for b in self.baskets],
            "basketsAvailable": self.baskets_available,
            "basketsUnavailableReason": self.baskets_unavailable_reason,
            "droppedBaskets": [d.to_public_dict() for d in self.dropped],
            "droppedBasketsAvailable": self.dropped_available,
            "droppedBasketsUnavailableReason": self.dropped_unavailable_reason,
            "reviews": [r.to_public_dict() for r in self.reviews],
            "reviewsAvailable": self.reviews_available,
            "reviewsUnavailableReason": self.reviews_unavailable_reason,
            "reviewD0": self.review_d0,
            "packVersion": self.pack_version,
            "notes": list(self.notes),
        }


def empty_basket_daily(trade_date: date, reason: str) -> BasketDaily:
    """整段计算异常时的诚实占位:三段全部 `available=False` + 同一个原因。
    ⛔ 不是「今天没有篮子」——那是 `available=True` + 空列表。"""
    return BasketDaily(
        trade_date=trade_date,
        baskets_available=False, baskets_unavailable_reason=reason,
        dropped_available=False, dropped_unavailable_reason=reason,
        reviews_available=False, reviews_unavailable_reason=reason,
        notes=[reason],
    )


# ══════════════════════════════════════════════════════════════════════════
# 装配
# ══════════════════════════════════════════════════════════════════════════

def _light_feature_rows(
    codes: Sequence[str], trade_date: date, parquet_dir: Optional[Path],
) -> Dict[str, Dict[str, Any]]:
    """篮子成员当日的**轻量**特征行(`{ts_code: row}`),只为喂 `exec_hints_for()`。

    只取 `daily`(+ `adj_factor` 前复权)+ `limit_derived`,回看 60 自然日 —— C1/C2/C4
    要的四个量(`ret_1d`/`is_limit_up`/`close`/`ma20`)全在里面。**复用生产
    `add_features`/`merge_limit_features`**,不在本模块重算任何一个特征(同码不重写)。

    **为什么不复用 `holding_k4_check._build_holding_feature_panel`**:那份面板为 ma250
    取 420 自然日 × 4 张表、且默认逐票 `get_stock_history` —— 持仓 ≤3 只时无所谓,
    篮子成员几十只时会退化成上万次 parquet footer 打开(§七 P1-26 的病根)。这里改成
    「整表按区间读一次 + 按代码过滤」,3 次扫描搞定。

    任何一步异常 → 返回空 dict(exec_hint 是**附加提示**,取不到就不给,绝不掀翻报告)。
    """
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    try:
        import polars as pl

        from neckline.data.adjust import apply_qfq
        from neckline.data.market_data import scan_table_range
        # 前复权价列集合与持仓面板**共用同一份声明**(同码不重写):它是「`daily` 表里
        # 哪几列是价格」这个数据事实,两处各写一份迟早漂。
        from neckline.report.holding_k4_check import _QFQ_PRICE_COLS as price_cols
        from neckline.strategy.features import add_features, merge_limit_features
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] 特征面板依赖导入失败,exec_hint 本次留空", exc_info=True)
        return {}

    start = trade_date - timedelta(days=_EXEC_HINT_LOOKBACK_CALENDAR_DAYS)
    try:
        daily = scan_table_range("daily", start, trade_date, parquet_dir=parquet_dir)
        if daily.is_empty():
            return {}
        daily = daily.filter(pl.col("ts_code").is_in(codes))
        if daily.is_empty():
            return {}
        adj = scan_table_range("adj_factor", start, trade_date, parquet_dir=parquet_dir)
        if not adj.is_empty():
            adj = adj.filter(pl.col("ts_code").is_in(codes))
        if not adj.is_empty():
            merged = daily.join(
                adj.select(["ts_code", "trade_date", "adj_factor"]),
                on=["ts_code", "trade_date"], how="left",
            )
            adjusted = apply_qfq(merged, price_cols=price_cols)
            qfq_cols = [f"{c}_qfq" for c in price_cols]
            daily = adjusted.drop(list(price_cols)).rename(dict(zip(qfq_cols, price_cols)))
        panel = add_features(daily)
        limit_df = scan_table_range("limit_derived", start, trade_date, parquet_dir=parquet_dir)
        if not limit_df.is_empty():
            limit_df = limit_df.filter(pl.col("ts_code").is_in(codes))
        panel = merge_limit_features(panel, limit_df)
        today = panel.filter(pl.col("trade_date") == trade_date)
        return {r["ts_code"]: r for r in today.to_dicts()}
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] 篮子成员特征面板计算异常,exec_hint 本次留空", exc_info=True)
        return {}


def _attach_exec_hints(
    views: Sequence[BasketView], trade_date: date, *,
    db_path: Optional[Path], parquet_dir: Optional[Path],
) -> None:
    """⑬-4 的接线点:给每篮每名成员算 0~4 条执行提示。

    **语义红线(`exec_hint.py` 模块头)**:回答的是「如果你决定动手,怎么执行更不吃亏」,
    **不是「该不该买」**;不进排序、不进哨兵、不改去留。整段包保险丝。"""
    codes = [c for v in views for c in v.member_codes]
    if not codes:
        return
    rows = _light_feature_rows(codes, trade_date, parquet_dir)
    if not rows:
        return
    try:
        from neckline.report.exec_hint import _load_k4_exec_hint_texts, exec_hints_for

        # DB 文字读一次批量复用(`exec_hints_for` 的 `db_texts` 钩子就是为这个留的)。
        texts = _load_k4_exec_hint_texts(db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] exec_hint 文案读取异常,本次不出执行提示", exc_info=True)
        return
    for v in views:
        for code in v.member_codes:
            row = rows.get(code)
            if row is None:
                continue
            try:
                hints = exec_hints_for(row, code, trade_date, db_path=db_path, db_texts=texts)
            except Exception:  # noqa: BLE001
                logger.warning("[basket_daily] %s 的执行提示计算异常(已跳过该票)", code, exc_info=True)
                continue
            if hints:
                v.exec_hints[code] = hints


def load_today_baskets(
    trade_date: date, *, db_path: Optional[Path] = None,
) -> List[BasketView]:
    """③ 今日篮子:`baskets` + `basket_members`(⑥ 落)+ `basket_cards`(⑦ 落)。

    **有篮子无卡是合法中间态**(事务 1 与事务 2 分开):该篮 `card=None` +
    `cardUnavailableReason='card_not_ready'`,⛔ 不许因此把整篮从报告里抹掉。

    **V2.1-④ 百分制**:按 `basket_id` 读**已冻结**的 `tier_history.mech_breakdown`
    (≤7 篮 × 1 次单行查询,成本可忽略),交 `report/score_display.score_view()` 换算。
    ⛔ 这里一个数都不重算;读留痕失败**只让这一篮没有打分**,不连坐(打分是附加展示,
    绝不掀翻报告)。"""
    from neckline.report.score_display import score_view
    from neckline.selection.basket_store import (
        load_basket_card, load_baskets_for_date, load_tier_history,
    )

    refs = load_baskets_for_date(trade_date, db_path=db_path)
    views: List[BasketView] = []
    for ref in refs:
        card_row = None
        try:
            card_row = load_basket_card(ref.basket_id, db_path=db_path)
        except Exception:  # noqa: BLE001  单篮读卡失败不连坐其余篮
            logger.warning("[basket_daily] basket_id=%s 读卡异常,按卡未就绪处理",
                           ref.basket_id, exc_info=True)
        score = None
        try:
            th = load_tier_history(ref.basket_id, db_path=db_path)
            if th is not None:
                score = score_view(th.get("mech_score"), th.get("mech_breakdown"))
        except Exception:  # noqa: BLE001  单篮读留痕失败不连坐其余篮、更不阻断报告
            logger.warning("[basket_daily] basket_id=%s 读定档留痕异常,本篮不出百分制打分",
                           ref.basket_id, exc_info=True)
        card = card_to_public_dict((card_row or {}).get("card")) if card_row else None
        # 「有行但读不出」如实标 `card_corrupt`,⛔ 不降格成「卡未生成」(B1 裁定:
        # 报告侧同样要说真话 —— 那张卡不会自己好,写成"还没生成"等于叫人白等)。
        corrupt = bool(card_row and card_row.get("card_corrupt"))
        views.append(BasketView(
            basket_id=ref.basket_id, basket_key=ref.basket_key, name=ref.name,
            tier=ref.tier, member_codes=tuple(ref.member_codes),
            card=card,
            card_version=(card_row or {}).get("version") if card_row else None,
            card_unavailable_reason=(None if card else ("card_corrupt" if corrupt else "card_not_ready")),
            score=score,
        ))
    return views


def load_yesterday_reviews(
    trade_date: date, *, db_path: Optional[Path] = None,
) -> Tuple[List[BasketReviewView], Optional[str]]:
    """④ 昨日篮子复盘:⑨ 在**今天**(D+1)对**昨天冻的篮子**(D0)的复盘行。

    返回 `(views, d0)`。`d0` 从 `mech.meta.d0` 取(⑨ 落库时就冻在里面,不在这里重推
    交易日历 —— 那会在"⑨ 那天认定的 D0"与"报告这天推出来的 D0"之间开第二个事实源)。
    附带 ⑧ 对同一篮同一天的「当前状态」(三路读法唯一实现,不重判)。"""
    from neckline.review.basket_review_store import list_reviews
    from neckline.sentinel.basket_verify_store import states_for_date

    day = trade_date.strftime("%Y%m%d")
    rows = list_reviews(date_from=day, date_to=day, db_path=db_path)
    if not rows:
        return [], None
    try:
        states = states_for_date(trade_date, db_path=db_path)
    except Exception:  # noqa: BLE001  验证状态是附加披露,取不到不阻断复盘节
        logger.warning("[basket_daily] 验证状态读取异常,本次复盘节不带验证角标", exc_info=True)
        states = {}
    views: List[BasketReviewView] = []
    d0: Optional[str] = None
    for r in rows:
        meta = (r.mech.get("meta") or {}) if isinstance(r.mech, dict) else {}
        d0 = d0 or (meta.get("d0") or None)
        st = states.get(r.basket_id)
        views.append(BasketReviewView(
            basket_id=r.basket_id,
            basket_key=str(meta.get("basket_key") or ""),
            name=str(meta.get("name") or ""),
            tier=meta.get("tier"),
            d0=str(meta.get("d0") or ""),
            review_date=r.review_date,
            depth=r.depth,
            mech=r.mech if isinstance(r.mech, dict) else {},
            llm_text=r.llm_text,
            llm_skip_reason=r.llm_skip_reason,
            degraded=bool(r.degraded),
            verification=(
                {
                    "state": st.state, "source": st.source, "observedAt": st.observed_at,
                    "provisional": st.provisional, "notEvaluated": st.not_evaluated,
                    "label": st.label,
                } if st is not None else None
            ),
        ))
    views.sort(key=lambda v: ((v.tier if v.tier is not None else 99), v.basket_key))
    return views, d0


def _zero_basket_verdict(
    trade_date: date, *, db_path: Optional[Path],
) -> Tuple[bool, Optional[str]]:
    """③ 在**零篮子**时的三态判读(§七 P0-39)。返回 `(available, unavailable_reason)`。

    零篮子的三种成因必须讲成三句不同的话:
    - ⑤ 跑了、结论是"今天没有够格的篮子" → `(True, None)`,③ 照旧写那句**合法输出**;
    - ⑤ 没跑成(`no_provider` / 预算尽 / 调用失败 / ⑤⑥⑦ 整段异常)→ `(False, 原因)`,
      ③ 走「本段未取得」体例并**如实带原因码**;
    - 段状态表**无行**(读历史报告 / 只出报告 / 该日在本表上线之前)→ 同样
      `(False, 原因)` —— 我们**不知道**引擎跑没跑,⛔ 不许拿"不知道"冒充"知道没有"
      (这与 ③b 在历史回放时如实标 `available=False` 是同一条纪律)。

    判读逻辑本身在 `selection/basket_stage_handoff.py::stage_verdict`(唯一实现),
    本函数只负责把它翻译成契约字段 + 人话。整段包保险丝:查表异常 → 按"无行"处理
    (读侧永远不比"没有这张表"更糟)。
    """
    verdict = None
    try:
        from neckline.selection.basket_stage_handoff import load_stage_verdict

        verdict = load_stage_verdict(trade_date, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] ⑤ 段状态查表异常,今日篮子段按未取得处理", exc_info=True)
    if verdict is None:
        return False, (
            "本次未运行驱动聚合/定档引擎(读历史报告 / 只出报告),今日篮子信息本报告未取得。"
        )
    if verdict.engine_ran:
        return True, None
    return False, (
        f"聚合/定档引擎本次未运行(原因:{verdict.reason_code}),今日篮子信息本报告未取得。"
    )


def build_basket_daily(
    trade_date: date, *,
    dropped: Optional[Sequence[Any]] = None,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    with_exec_hints: bool = True,
) -> BasketDaily:
    """装配一份篮子日报快照。**三段各自包保险丝**:任一段炸了只让那一段标 `available=
    False` + 原因,其余两段照出(§铁律「任何一段异常都不许让当日无报告」)。

    `dropped`:⑥ 本次跑出来的 `TierResult.dropped`(每项要有 `.basket_key`/`.name`?
    —— ⑥ 的 `DroppedBasket` 只有 `basket_key`/`reason`/`mech_score`,篮子名由本函数从
    `basket_key` 兜底填,见下)。**`None` = 本次没跑 ⑥**(读历史 / 只出报告)→ ③b 如实
    标 `available=False`,⛔ 不现算。**空序列 = 跑了、今天零溢出** → `available=True`
    + 空列表,节仍在。
    """
    out = BasketDaily(trade_date=trade_date)

    # ③ 今日篮子
    try:
        out.baskets = load_today_baskets(trade_date, db_path=db_path)
        # §七 P0-39:`available` **不许挂在"读表成功"上** —— 那只证明表读得出来,
        # 不证明引擎跑过。有篮子 = ⑤⑥ 跑过的活证据(篮子就是它们产出的);零篮子
        # 才需要查 ⑤ 的段状态,把「跑了、真没够格的」与「引擎没跑」分开。
        if out.baskets:
            out.baskets_available = True
        else:
            out.baskets_available, out.baskets_unavailable_reason = _zero_basket_verdict(
                trade_date, db_path=db_path,
            )
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] 今日篮子读取异常,该段降级留空", exc_info=True)
        out.baskets = []
        out.baskets_available = False
        out.baskets_unavailable_reason = "今日篮子读取异常(详见服务端日志),本段未取得。"
        out.notes.append(out.baskets_unavailable_reason)

    if with_exec_hints and out.baskets:
        try:
            _attach_exec_hints(out.baskets, trade_date, db_path=db_path, parquet_dir=parquet_dir)
        except Exception:  # noqa: BLE001
            logger.warning("[basket_daily] 执行提示装配异常(已吞,不阻断报告)", exc_info=True)

    # 口径指纹:选股包版本从**冻结卡**里取(不查 `get_active_pack()` —— 那是"现在的
    # 现役包",不是"这份报告当时用的包";历史回放两者可能不同)。
    for b in out.baskets:
        fp = (b.card or {}).get("fingerprint") or {}
        if fp.get("packVersion"):
            out.pack_version = fp["packVersion"]
            break

    # ③b 今日未定档篮子
    if dropped is None:
        out.dropped_available = False
        out.dropped_unavailable_reason = (
            "本次未运行 Tier 分层引擎(读历史报告 / 只出报告),未定档篮子信息本报告未取得。"
        )
    else:
        try:
            name_by_key = {b.basket_key: b.name for b in out.baskets}
            out.dropped = [
                DroppedBasketView(
                    name=(getattr(d, "name", None)
                          or name_by_key.get(getattr(d, "basket_key", ""), "")
                          or getattr(d, "basket_key", "")),
                    mech_score=getattr(d, "mech_score", None),
                    reason=str(getattr(d, "reason", "") or ""),
                )
                for d in dropped
            ]
            out.dropped_available = True
        except Exception:  # noqa: BLE001
            logger.warning("[basket_daily] 未定档篮子装配异常,该段降级", exc_info=True)
            out.dropped = []
            out.dropped_available = False
            out.dropped_unavailable_reason = "未定档篮子装配异常(详见服务端日志),本段未取得。"
            out.notes.append(out.dropped_unavailable_reason)

    # ④ 昨日篮子复盘
    try:
        out.reviews, out.review_d0 = load_yesterday_reviews(trade_date, db_path=db_path)
        out.reviews_available = True
    except Exception:  # noqa: BLE001
        logger.warning("[basket_daily] 昨日复盘读取异常,该段降级留空", exc_info=True)
        out.reviews = []
        out.reviews_available = False
        out.reviews_unavailable_reason = "昨日篮子复盘读取异常(详见服务端日志),本段未取得。"
        out.notes.append(out.reviews_unavailable_reason)

    return out


def basket_daily_from_snapshot(payload: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """读历史报告快照时的容错读回:`{}`(老报告,建于本列之前)→ 一份**如实标
    「该版本还没有篮子日报」**的结构,⛔ 不冒充「那天没有篮子」。"""
    if payload:
        return dict(payload)
    return {
        "tradeDate": "",
        "baskets": [], "basketsAvailable": False,
        "basketsUnavailableReason": "该报告生成于篮子日报上线之前,无篮子快照。",
        "droppedBaskets": [], "droppedBasketsAvailable": False,
        "droppedBasketsUnavailableReason": "该报告生成于篮子日报上线之前,无未定档篮子快照。",
        "reviews": [], "reviewsAvailable": False,
        "reviewsUnavailableReason": "该报告生成于篮子日报上线之前,无复盘快照。",
        "reviewD0": None, "packVersion": None, "notes": [],
    }


__all__ = [
    "DROPPED_REASON_LABEL",
    "BasketDaily", "BasketReviewView", "BasketView", "DroppedBasketView",
    "basket_daily_from_snapshot", "build_basket_daily", "card_member_to_public_dict",
    "card_to_public_dict", "empty_basket_daily", "load_today_baskets", "load_yesterday_reviews",
]
