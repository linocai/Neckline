"""⑦-K7 成员标注件:「龙回头位」+ 双尾警示族(plan §五 V2-⑦-K7,K7 需求 5)。

**口径单一源**(plan 原文):判据 + 标签码 + 中文名 + 文案模板**全在本模块**;
篮子卡(⑦,`selection/basket_card.py`)与信息卡(⑬-N,`report/info_card.py`)
**读同一份**,禁两处各写一遍(v1.5 自选 / 持仓两侧 K4 镜像的交叉断言体例平移 ——
⑬-N 落地时要加「同一票同一天,两侧标签集合逐位相同」的交叉断言)。

**⛔ 四不(硬约束,守门单测 `tests/test_selection_member_tags.py`)**:
    1. **不进 `_TIER_SCORE_INPUTS`** —— 标签不参与机械分,一分不加;
    2. **不进排序键 dims 白名单** —— 不参与任何排序;
    3. **不进哨兵判据** —— `neckline/sentinel/` 全目录零命中;
    4. **不改篮子 / 成员去留** —— 打标前后 Tier 序与成员集逐位不变。
它们是**纯展示位**,落在篮子卡的「成员、角色与比较结果」节里
(`basket_cards.card_json.members[].tags`,与卡同生共死,**不新建表、不新建列**)。

**判据全部机械算、零 LLM**(它们是价量结构判定,不是叙述),口径逐字取自
`research/k7_pre2_report.md` §1(H12 预注册定义)与 `research/k7p_h12_pullback.py`
的表达式,**不重新发明**:

    · 强势资格 = `limitup_count_20d ≥ 2` 且 `ret_20d ≥ +25%`
    · 回调态   = `dist_from_high_20d ∈ [−25%, −8%]` 且 `close > ma20`
    · 企稳日   = `vol < vol_ma5`(缩量)且 `ret_1d ∈ [−3%, +2%]`
    · 追入带   = 强势资格 且 `dist_from_high_20d > −3%`(对照 B,用户最常下手的位置)
    · 连板头名 = 簇内 `leader_structure_daily.limit_height` 头名

**阈值为什么是「函数关键字默认值」而不是模块级常量**:`neckline/selection/` 下的
模块级数值字面量受 `tests/test_selection_primitives.py::
test_no_unwhitelisted_module_level_numeric_thresholds` 扫描约束,而该测试自己给出
的两种合规形态之一就是「纯函数参数默认值」。这些数是 H12 预注册定义的一部分
(改它等于改标注件的含义,要走研究线而不是换包),放在签名上既满足守门、也让调用
方**能够**在单测里显式传另一档做敏感性验证。

**缺数据 → 不打标(不猜)**:任一输入算不出 → 该标签落 `absent`,**不写 `False`
冒充「已判定为否」**(「没有」与「没看」必须分得开,同 P0-23 与 ⑤ 的
`evidence_status` 三态精神)。

**不重复建标(交接稿明文)**:**过热态由 A2 红牌承接**、**年线下放量族由 A3 / B1
现有牌承接**,本模块不再造第二套。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import polars as pl

logger = logging.getLogger(__name__)

# 纪律阈值比较容差(项目 CLAUDE.md「盘中哨兵」条铁律:`0.08-0.02` 二进制浮点下
# ≠ 0.06,裸 `>=`/`<=` 漏判边界。同 `sentinel/holding.py::_EPS` /
# `primitives.py::_LIFT_EPS`,是工程不变量、不是策略参数)。
_EPS = 1e-9

# —— 标签码(DDL 之外的展示层枚举;`card_json.members[].tags[].code` 用它)——————
TAG_PULLBACK_LEADER = "pullback_leader"
TAG_WARN_STREAK_TOP = "warn_streak_top"
TAG_WARN_CHASE_ZONE = "warn_chase_zone"

ALL_TAG_CODES: Tuple[str, ...] = (
    TAG_PULLBACK_LEADER, TAG_WARN_STREAK_TOP, TAG_WARN_CHASE_ZONE,
)

# 展示色调(客户端 chip 用;`warn` 走警示色,`neutral` 走中性色 —— ⑬-N 原文
# 「展示为**警示 / 中性色调**的标注 chip」)。
TONE_NEUTRAL = "neutral"
TONE_WARN = "warn"

# 「参考、非指令」后缀**单一源**。由 `tag_text()` 统一追加,调用方拿到的 `text`
# 天然带着它 —— 想在某一处忘掉都做不到(plan 验收第 ③ 条「文案每处带『参考、
# 非指令』」靠"没有那条路径"担保,不靠自觉)。
REFERENCE_ONLY_SUFFIX = "—— 参考、非指令。"

# 证据出处(plan「数字必须带来源与『参考』标注」的「来源」那一半;`text` 本身
# 逐字照 plan 文案,来源单列一字段供渲染层展示,不去改 plan 给的文案)。
TAG_EVIDENCE_REF = "K7 前置 · 战役二结构池审计(research/k7_pre2_report.md H12/H13)"

_LABELS: Dict[str, str] = {
    TAG_PULLBACK_LEADER: "龙回头位",
    TAG_WARN_STREAK_TOP: "双尾警示 · 连板头名",
    TAG_WARN_CHASE_ZONE: "双尾警示 · 强势追入位",
}

_TONES: Dict[str, str] = {
    TAG_PULLBACK_LEADER: TONE_NEUTRAL,   # 机会密度高但左尾也厚,双尾 → 中性,不是好评
    TAG_WARN_STREAK_TOP: TONE_WARN,
    TAG_WARN_CHASE_ZONE: TONE_WARN,
}

# 文案主体(**逐字照 plan §五 ⑦-K7「展示文案要点」**,禁写成收益承诺)。后缀由
# `tag_text()` 追加,故这里不带「参考、非指令」。
_TEXT_BODY: Dict[str, str] = {
    TAG_PULLBACK_LEADER: (
        "机会密度约为市场的 1.8×,但 3 日跌停率也是市场的 4–6×(双尾);"
        "同为强势票,回调位的跌停率约为追入位的 1/3。"
    ),
    TAG_WARN_STREAK_TOP: "簇内连板高度第一:涨停触达约 1.5×,次日跌停约 3× 于后排。",
    TAG_WARN_CHASE_ZONE: "强势且贴着 20 日高:3 日跌停率 15–19%,三段样本一致。",
}

# `evaluate_member_tags` 认识的输入键(**白名单**,同 ⑥ `_TIER_SCORE_INPUTS` 的
# 精神:让"这个判据到底读了什么"可被机器核对。⚠ 与 ⑥ 那个白名单是**两件事** ——
# 这里列的是标注件的输入,`_TIER_SCORE_INPUTS` 列的是机械分的输入,四不第 1 条
# 要求两者交集为空,由守门单测断言)。
TAG_INPUTS: Tuple[str, ...] = (
    "limitup_count_20d", "ret_20d", "dist_from_high_20d", "close", "ma20",
    "vol", "vol_ma5", "ret_1d", "streak_top",
)


def tag_label(code: str) -> str:
    return _LABELS[code]


def tag_tone(code: str) -> str:
    return _TONES[code]


def tag_text(code: str) -> str:
    """标签展示文案(**恒带**「参考、非指令」后缀,单一源)。"""
    return _TEXT_BODY[code] + REFERENCE_ONLY_SUFFIX


@dataclass(frozen=True)
class MemberTag:
    """一条命中的成员标注。`text` 已含「参考、非指令」;`source` 是数字出处。"""

    code: str
    label: str
    tone: str
    text: str
    source: str = TAG_EVIDENCE_REF

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "label": self.label, "tone": self.tone,
                "text": self.text, "source": self.source}


@dataclass(frozen=True)
class MemberTagResult:
    """一只成员一天的标注结论。三种取值语义**互不合并**:

        · `tags`   —— 命中(按 `ALL_TAG_CODES` 固定序,可复现);
        · `missed` —— 数据齐全、判定为**否**(真的不是龙回头位);
        · `absent` —— 数据算不出、**本次没判**(不写 False 冒充"判定为否")。
    """

    ts_code: str
    tags: Tuple[MemberTag, ...] = ()
    missed: Tuple[str, ...] = ()
    absent: Tuple[str, ...] = ()

    def codes(self) -> Tuple[str, ...]:
        return tuple(t.code for t in self.tags)

    def to_dict_list(self) -> list:
        return [t.to_dict() for t in self.tags]


def _num(v: Any) -> Optional[float]:
    """取有限实数,否则 `None`(bool 不算数 —— `True` 在 Python 里是 1,拿它当
    `ret_20d` 会静默算出荒谬结论)。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def evaluate_member_tags(
    row: Mapping[str, Any],
    *,
    strong_min_limitups_20d: int = 2,
    strong_min_ret_20d: float = 0.25,
    pullback_dist_floor: float = -0.25,
    pullback_dist_ceiling: float = -0.08,
    calm_ret_1d_floor: float = -0.03,
    calm_ret_1d_ceiling: float = 0.02,
    chase_zone_dist_above: float = -0.03,
) -> MemberTagResult:
    """**纯函数**:一只票一天的三个标签判定。`row` 只读 `TAG_INPUTS` 里的键。

    `row["streak_top"]`:`True`/`False` = 簇内连板高度是不是头名(由
    `streak_top_flags()` 机械算好);`None` / 缺键 = 该票今天不在任何涨停簇里
    (或龙头结构表当日无数据)→ 连板高度**算不出** → `warn_streak_top` 落
    `absent`,**不是** `False`。

    三个标签**各自独立降级**:强势资格算不出只影响 `pullback_leader` 与
    `warn_chase_zone`,不影响 `warn_streak_top`(它读的是另一路数据)。
    """
    ts_code = str(row.get("ts_code") or "")
    tags: list = []
    missed: list = []
    absent: list = []

    lu = row.get("limitup_count_20d")
    lu = None if isinstance(lu, bool) else lu
    lu_v = None if lu is None else _num(lu)
    ret20 = _num(row.get("ret_20d"))
    dist = _num(row.get("dist_from_high_20d"))
    close = _num(row.get("close"))
    ma20 = _num(row.get("ma20"))
    vol = _num(row.get("vol"))
    vol_ma5 = _num(row.get("vol_ma5"))
    ret1 = _num(row.get("ret_1d"))

    # —— 强势资格(两个标签共用的前置)——————————————————————————————————
    strong: Optional[bool] = None
    if lu_v is not None and ret20 is not None:
        strong = (lu_v >= strong_min_limitups_20d - _EPS) and (ret20 >= strong_min_ret_20d - _EPS)

    # —— ① 龙回头位 = 强势资格 × 回调态 × 企稳日 ——————————————————————————
    pullback_inputs_ok = (
        strong is not None and dist is not None and close is not None and ma20 is not None
        and vol is not None and vol_ma5 is not None and ret1 is not None
    )
    if not pullback_inputs_ok:
        absent.append(TAG_PULLBACK_LEADER)
    else:
        in_pullback = (
            (dist >= pullback_dist_floor - _EPS)
            and (dist <= pullback_dist_ceiling + _EPS)
            and (close > ma20 + _EPS)
        )
        calm_day = (
            (vol < vol_ma5 - _EPS)
            and (ret1 >= calm_ret_1d_floor - _EPS)
            and (ret1 <= calm_ret_1d_ceiling + _EPS)
        )
        if strong and in_pullback and calm_day:
            tags.append(_make_tag(TAG_PULLBACK_LEADER))
        else:
            missed.append(TAG_PULLBACK_LEADER)

    # —— ② 双尾警示 · 连板头名 ——————————————————————————————————————————
    streak_top = row.get("streak_top")
    if streak_top is None:
        absent.append(TAG_WARN_STREAK_TOP)
    elif bool(streak_top):
        tags.append(_make_tag(TAG_WARN_STREAK_TOP))
    else:
        missed.append(TAG_WARN_STREAK_TOP)

    # —— ③ 双尾警示 · 强势追入位 ————————————————————————————————————————
    if strong is None or dist is None:
        absent.append(TAG_WARN_CHASE_ZONE)
    elif strong and dist > chase_zone_dist_above + _EPS:
        tags.append(_make_tag(TAG_WARN_CHASE_ZONE))
    else:
        missed.append(TAG_WARN_CHASE_ZONE)

    order = {c: i for i, c in enumerate(ALL_TAG_CODES)}
    return MemberTagResult(
        ts_code=ts_code,
        tags=tuple(sorted(tags, key=lambda t: order[t.code])),
        missed=tuple(sorted(missed, key=lambda c: order[c])),
        absent=tuple(sorted(absent, key=lambda c: order[c])),
    )


def _make_tag(code: str) -> MemberTag:
    return MemberTag(code=code, label=tag_label(code), tone=tag_tone(code), text=tag_text(code))


# ══════════════════════════════════════════════════════════════════════════
# 机械数据装配(两路只读,各自包保险丝 —— 少一路只让相关标签落 absent)
# ══════════════════════════════════════════════════════════════════════════

def load_tag_panel_rows(
    codes: Sequence[str], trade_date: date, *, parquet_dir: Optional[Path] = None
) -> Dict[str, Dict[str, Any]]:
    """`ts_code -> {TAG_INPUTS 的价量部分}`(`build_research_panel` 单日切片,同
    `member_hygiene._load_liquidity_rows` 既有调用姿势:45 自然日缓冲、**非全历史
    扫描**,符合 P0-23「别在在线路径上扫全历史」)。整体失败 / 当日无数据 → 空
    dict(调用方按"算不出"处理 → 相关标签 `absent`,不猜)。"""
    if not codes:
        return {}
    from neckline.data.panel import build_research_panel

    try:
        panel = build_research_panel(trade_date, trade_date, with_forward=False,
                                     parquet_dir=parquet_dir)
    except Exception:  # noqa: BLE001
        logger.warning("[member_tags] 装配价量面板失败,本次相关标签全体落 absent", exc_info=True)
        return {}
    if panel.is_empty():
        return {}
    wanted = list(dict.fromkeys(c for c in codes if c))
    cols = [c for c in ("ts_code", "limitup_count_20d", "ret_20d", "dist_from_high_20d",
                        "close", "ma20", "vol", "vol_ma5", "ret_1d") if c in panel.columns]
    sub = panel.filter(pl.col("ts_code").is_in(wanted)).select(cols)
    if sub.is_empty():
        return {}
    return {r["ts_code"]: dict(r) for r in sub.iter_rows(named=True)}


def streak_top_flags(
    codes: Sequence[str], trade_date: date, *, db_path: Optional[Path] = None
) -> Dict[str, bool]:
    """`ts_code -> 是否簇内连板高度头名`(只含**当日在龙头结构表里有行**的码;
    不在表里的码不出现在返回值中 → 调用方据此落 `absent`,不写 `False`)。

    数据源 = ④ 的 `leader_structure_daily.limit_height`(**只读表**,P0-23
    「判据类全市场扫描一律预计算落表,在线路径只读」;缺表 / 缺行不现算自愈)。

    **判据落在「共振邻域」而不是「单个簇」上(plan 未点名,builder 判断,附真实
    数据证据,如实登记)**:H10/H13 审计用的粗簇是「(日, 行业) 涨停家数 ≥3」——
    **一只票只属于一个簇**,「簇内第一」没有歧义。而 ④ 的 `limit_cluster_daily`
    是概念/行业多路聚类,**一只票同日常属于十几个簇**(2026-07-24 真实数据:42 只
    涨停票摊在 144 个簇、490 行)。若照字面取「任一簇头名即头名」,当日 **36/42
    (86%)** 都会被打上「连板头名」——一个 86% 命中率的警示等于没有警示。故本模块
    把「簇内」读作**「与它共振的全部票」**(它所在的每一个簇的成员并集):

        · **在共振邻域里连板高度并列或独占最高** —— 并列最高**都打标**(警示从严:
          两只票风险相同,任选其一打标等于对另一只隐瞒;⑥ 的 `rs_rank` 要做确定性
          tie-break 是因为它进排序键、名次必须唯一,标注件不进排序,没有这个约束)。
        · **且邻域里至少有一只票连板更低**(确有「后排」)—— 文案说的是「次日跌停
          约 3× **于后排**」,全员齐平时根本没有后排,这句话无从成立。
        · `limit_height < 1`(没有连板)一律不打标:「连板高度第一」为 0 没有意义。
        · 邻域为空(该票所在簇只有它自己 —— ④ 的 `MIN_CLUSTER_SIZE=2` 下不会发生,
          但不靠上游担保)→ 不打标。

    这套读法在 2026-07-22/23/24 三天真实数据上的命中数是 6 / 6 / 5(分母 48 / 129
    / 42),量级与「双尾警示」的定位相称。
    """
    if not codes:
        return {}
    from neckline.scan import leader as leader_mod

    try:
        df = leader_mod.load_leader_structure(trade_date, db_path=db_path)
    except Exception:  # noqa: BLE001
        logger.warning("[member_tags] 读龙头结构表失败,连板头名标签本次全体落 absent", exc_info=True)
        return {}
    if df.is_empty():
        return {}
    df = df.filter(pl.col("limit_height").is_not_null())
    if df.is_empty():
        return {}

    # 邻域装配:当日表规模是百~千行(单日单表,非全历史扫描),纯 Python 聚合足够,
    # 也比自连接更容易读懂。
    members_of: Dict[str, set] = {}
    height_of: Dict[str, int] = {}
    for r in df.select(["cluster_key", "ts_code", "limit_height"]).iter_rows(named=True):
        members_of.setdefault(r["cluster_key"], set()).add(r["ts_code"])
        height_of[r["ts_code"]] = int(r["limit_height"])

    neighbours: Dict[str, set] = {}
    for group in members_of.values():
        for code in group:
            neighbours.setdefault(code, set()).update(group - {code})

    wanted = set(c for c in codes if c)
    out: Dict[str, bool] = {}
    for code, mates in neighbours.items():
        if code not in wanted:
            continue
        h = height_of[code]
        mate_h = [height_of[m] for m in mates if m in height_of]
        out[code] = bool(mate_h) and h >= 1 and h >= max(mate_h) and min(mate_h) < h
    return out


@dataclass(frozen=True)
class MemberTagBatch:
    """一批成员一天的标注结果 + 两路数据源的可用性(如实披露,不藏)。"""

    trade_date: str
    results: Dict[str, MemberTagResult] = field(default_factory=dict)
    panel_available: bool = False
    leader_available: bool = False

    def get(self, ts_code: str) -> MemberTagResult:
        return self.results.get(ts_code, MemberTagResult(ts_code=ts_code, absent=ALL_TAG_CODES))


def tags_for_members(
    codes: Sequence[str],
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    panel_rows: Optional[Mapping[str, Mapping[str, Any]]] = None,
    streak_top: Optional[Mapping[str, bool]] = None,
) -> MemberTagBatch:
    """**篮子卡(⑦)与信息卡(⑬-N)共用的唯一入口**:一批码 → 一批标注结论。

    `panel_rows` / `streak_top`:调用方已经算好时可注入(避免同一份 I/O 做两遍;
    ⑦ 的卡生成本来就要读价量面板)。不注入就自己读 —— 两条路**产出必须一致**,
    这正是 ⑬-N 交叉断言要锁的东西。
    """
    wanted = list(dict.fromkeys(c for c in codes if c))
    if not wanted:
        return MemberTagBatch(trade_date=trade_date.strftime("%Y%m%d"))

    rows = dict(panel_rows) if panel_rows is not None else load_tag_panel_rows(
        wanted, trade_date, parquet_dir=parquet_dir)
    flags = dict(streak_top) if streak_top is not None else streak_top_flags(
        wanted, trade_date, db_path=db_path)

    results: Dict[str, MemberTagResult] = {}
    for code in wanted:
        row: Dict[str, Any] = dict(rows.get(code) or {})
        row["ts_code"] = code
        row["streak_top"] = flags.get(code)
        results[code] = evaluate_member_tags(row)
    return MemberTagBatch(
        trade_date=trade_date.strftime("%Y%m%d"),
        results=results,
        panel_available=bool(rows),
        leader_available=bool(flags),
    )


__all__ = [
    "TAG_PULLBACK_LEADER",
    "TAG_WARN_STREAK_TOP",
    "TAG_WARN_CHASE_ZONE",
    "ALL_TAG_CODES",
    "TONE_NEUTRAL",
    "TONE_WARN",
    "REFERENCE_ONLY_SUFFIX",
    "TAG_EVIDENCE_REF",
    "TAG_INPUTS",
    "MemberTag",
    "MemberTagResult",
    "MemberTagBatch",
    "tag_label",
    "tag_tone",
    "tag_text",
    "evaluate_member_tags",
    "load_tag_panel_rows",
    "streak_top_flags",
    "tags_for_members",
]
