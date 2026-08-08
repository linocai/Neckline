"""百分制打分卡:机械分 ×100 + 五维贡献拆解(plan §五 V2.1-④「甲案 · 纯展示层」)。

🔴 **本模块住 `report/`(展示层)而不是 `selection/`(判定层),这是设计的一半**。
「百分制分数不进任何判定路径」在这里不是一句口号,而是一条**方向性**规则,守门只需
一句 AST:`selection/**`、`sentinel/**`、`strategy/**`、`eval/**`、`review/**`
**全仓零 import `neckline.report.score_display`**(同项目既有的 `research → neckline`
方向恒定体例)。⛔ 谁要把这个分数拿去排序 / 喂哨兵 / 决定去留,第一步必然是 import
本模块,而那一步会当场被守门单测拦下。

**四条边界(逐条都有守门,⛔ 别以为是注释)**

1. **纯函数、零 I/O、零 DB、零重算**。数据**全部**来自已经冻结在
   `tier_history.mech_breakdown_json` 里的那份 dict(`dims` / `weights` / `contrib` /
   `flags` / `neutral_filled_weight` 五个键在 ⑥ 定档当时就一起落库了)。本模块**一个
   数都不重新算** —— 它做的事只有「×100、贴中文标签、排个确定的序」。
2. **零 import `neckline.selection`**(反向也守):把判定层拉进展示层,方向规则就废了。
3. **`selection/tier.py::_TIER_SCORE_INPUTS` 逐位不变**:百分制是**换算**,不是新维度,
   ⛔ 不许因为"要展示"就往机械分白名单里塞字段。
4. **`mech_score` 缺席 → 返 `None`,⛔ 不返 0**(§3.8「没有」与「没看」必须分得开;
   0 分是一个**极差的实质性判断**,拿它冒充"没这个数"是本项目反复禁止的那类谎)。

**为什么它是"甲案"**:V2.1 用户裁定 #5 —— 百分制**只换算不改语义**,零 K7 包改动、
零策略语义变更。若用起来觉得"分不对",那些不满是**策略台的需求单**(K7 整改 = 乙案),
⛔ 不是在这里悄悄调系数就能解决的事。

**`_DIM_NEUTRAL_FILL_FLAGS` 是一份"受监督的重复"(如实登记,不是疏忽)**
每一维「是否被中性填充」的唯一事实源是 `selection/tier.py::_DIM_MISSING_FLAGS`,而
边界 2 又禁止本模块 import 它 —— 两条约束把"直接复用"这条路堵死了。处置:本模块保留
一份**字面等价**的副本,并由 `tests/test_score_display.py::
test_neutral_fill_flag_map_matches_the_engine_exactly` **逐位断言两者相等**。漂移因此
是一条**当场报红的机器判据**,不是"但愿有人记得同步"。⛔ 别把这份副本当成可以随手改的
展示层配置 —— 它一改就红,而红的意思是「引擎那边变了,来这里对齐」,不是「把断言删掉」。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# 五维中文标签(**展示层唯一映射,只住本模块**)。服务端不在别处另建一份中文映射,
# 客户端也不重推一套 —— 同 `CandidateOut.board` → `boardLabel` 那条教训的反面:
# 那次是"英文码给客户端、中文只在客户端算",这次是"中文只在展示层算、判定层永远只
# 认英文维度名"。两者共同的原则是**一个语义只有一个中文来源**。
# 未登记的 `dim` 用**原名兜底**(⛔ 不抛 —— 一个新维度不该让整份报告出不来;
# ⛔ 也不吞 —— 原名照样显示出来,让人一眼看见"有个没登记的维度")。
DIM_LABELS: Dict[str, str] = {
    "sector_strength": "板块强度",
    "leader_clarity": "龙头清晰度",
    "tradability": "可交易性",
    "driver_freshness": "驱动新鲜度",
    "card_density": "卡密度",
}

# 每一维「取了中性分 0.5」对应哪些 flag —— **`selection/tier.py::_DIM_MISSING_FLAGS`
# 的受监督副本**(理由与守门见模块头最后一节)。
# ⚠ **只认 flag,不认数值**:`leader_clarity` 的 `1/rank` 在 `rank=2` 时恰好也等于
# 中性分 0.5(真实第二名与"没数据"数值撞车),拿数值反推会判错。
# ⚠ `stage_unmapped` **刻意不在这里** —— 它单独出现时该维仍可能是别的行业算出来的
# 真实值,不代表被中性填充(与引擎侧同一条判断)。
_DIM_NEUTRAL_FILL_FLAGS: Dict[str, frozenset] = {
    "sector_strength": frozenset({"sector_strength_missing"}),
    "driver_freshness": frozenset({"stage_missing", "stage_scores_absent"}),
    "leader_clarity": frozenset({"leader_clarity_missing"}),
    "tradability": frozenset({"tradability_missing"}),
    "card_density": frozenset({"card_density_missing"}),
}

# 分项贡献保留的小数位。**刻意比 `scorePercent` 的 1 位更精**,理由是自洽判据:
# 五个分项各自四舍五入到 1 位后,合计与总分最坏能差 5×0.05+0.05 = 0.30,
# 「五维合计 ≈ 总分」这条本模块最要紧的自洽性会被舍入噪声吃掉(plan §五④ 写的
# 0.15 容差在 1 位小数下并非安全上界,已在完工记录登记)。保留 4 位后误差只剩
# 总分自己那 0.05,自洽判据才立得住。**展示层各自 `:.1f` 格式化**(报告 markdown
# 与客户端卡面都只显示 1 位)—— 精度住契约,位数住展示,两件事。
_CONTRIB_ND = 4

_NOTE_BASE = (
    "百分制 = 机械分 ×100 的等价换算,**纯展示**:不进排序、不进哨兵、不改去留。"
    "五维贡献 = 归一化权重 × 该维得分 ×100,合计即总分(各项独立舍入,末位可能差零点几)。"
)
_NOTE_NEUTRAL_FMT = (
    "⚠ 其中 {pct}% 的权重来自**中性填充** —— 那几维今天没算出来、按中性分 0.5 计入,"
    "**不是「这几维表现好」**。"
)


def _f(v: Any) -> Optional[float]:
    """安全转 float。转不动 → `None`(⛔ 不是 0)。"""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def score_percent(mech_score: Any) -> Optional[float]:
    """机械分 → 百分分数 = `round(mech_score * 100, 1)`。

    ⚠ **plan 原文签名写的是 `-> float`,实现是 `Optional[float]`**:`None` 进 → `None`
    出。这不是放宽,而是同一块 plan 自己的测试条款(「缺 `mech_breakdown` → 返 `None`
    而不是 0」)所要求的同一条纪律 —— 分数缺席就说缺席,⛔ 不用 0 冒充。
    """
    v = _f(mech_score)
    if v is None:
        return None
    return round(v * 100, 1)


def score_view(
    mech_score: Any, mech_breakdown: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    """一篮的百分制打分卡视图(camelCase,直接进契约与快照)。

    返回形状::

        {
          "scorePercent": 62.5,
          "contributions": [
            {"dim": "sector_strength", "label": "板块强度", "dimScore": 0.72,
             "weight": 0.25, "contribPercent": 18.0, "neutralFilled": false}, ...
          ],
          "neutralFilledPercent": 0.0,
          "note": "……",
        }

    **返回 `None` 的两种情形**(都属"这份数据里没有分可展示",⛔ 不是 0 分):
      · `mech_score` 缺席 / 不是数(极旧留痕行、手工造数的库);
      · `mech_breakdown` 不是字典或为空(⑥ 落库之前的行、或 JSON 解不出被
        `basket_store.load_tier_history` 兜成 `{}` 的行 —— 那是**读不出**,不是零分)。

    调用方拿到 `None` 时的正确说法是「该报告版本无打分」/「本篮无定档留痕」,
    ⛔ 不许渲染成 0.0。

    **排序确定性**:`(contribPercent 降序, dim 升序)`;`contribPercent` 算不出的项
    排在最后(它们没有可比的大小,⛔ 不许当 0 混进中间)。
    """
    pct = score_percent(mech_score)
    if pct is None:
        return None
    if not isinstance(mech_breakdown, Mapping) or not mech_breakdown:
        return None

    contrib = mech_breakdown.get("contrib")
    dims = mech_breakdown.get("dims")
    weights = mech_breakdown.get("weights")
    raw_flags = mech_breakdown.get("flags")
    contrib = contrib if isinstance(contrib, Mapping) else {}
    dims = dims if isinstance(dims, Mapping) else {}
    weights = weights if isinstance(weights, Mapping) else {}
    flag_set = {str(f) for f in raw_flags} if isinstance(raw_flags, (list, tuple, set, frozenset)) else set()

    items: List[Dict[str, Any]] = []
    for dim in contrib:
        name = str(dim)
        cv = _f(contrib.get(dim))
        items.append({
            "dim": name,
            "label": DIM_LABELS.get(name, name),
            "dimScore": _f(dims.get(dim)),
            "weight": _f(weights.get(dim)),
            "contribPercent": (None if cv is None else round(cv * 100, _CONTRIB_ND)),
            # 未登记的维度 → `False`,语义精确读作「该维**没有已登记的**中性填充 flag
            # 出现」。新维度真要有自己的缺数据 flag,引擎侧一加,上面那条逐位相等的
            # 守门就红 —— 闭环在守门上,不靠这里猜。
            "neutralFilled": bool(_DIM_NEUTRAL_FILL_FLAGS.get(name, frozenset()) & flag_set),
        })
    items.sort(key=lambda it: (
        -(it["contribPercent"] if it["contribPercent"] is not None else float("-inf")),
        it["dim"],
    ))

    nfw = _f(mech_breakdown.get("neutral_filled_weight"))
    nfp = None if nfw is None else round(nfw * 100, 1)
    note = _NOTE_BASE
    if nfp:                                     # 0.0 与 None 都不追加(没有就别啰嗦)
        note += _NOTE_NEUTRAL_FMT.format(pct=f"{nfp:g}")
    return {
        "scorePercent": pct,
        "contributions": items,
        "neutralFilledPercent": nfp,
        "note": note,
    }


def contribution_line(view: Optional[Mapping[str, Any]]) -> Optional[str]:
    """打分卡 → markdown 报告 ③ 节那一行(**唯一文案实现**,渲染层不另拼一份)。

    形如::

        机械分 62.5 / 100(板块强度 18.0 · 龙头清晰度 15.0 · 可交易性 12.0 · …)

    `view` 为 `None` → 返 `None`(调用方据此**整行不出**,⛔ 不出一行「机械分 —/100」
    的空壳:那看起来像"算过了是空的")。中性填充的维度**带一个 `*` 角标**并在行尾
    统一说明它的含义 —— 一个数字后面跟着"这个数是猜的"是必须当场说清的事,不能只放在
    结构化字段里等客户端记得展示。
    """
    if not isinstance(view, Mapping):
        return None
    pct = view.get("scorePercent")
    if pct is None:
        return None
    parts: List[str] = []
    for it in view.get("contributions") or []:
        if not isinstance(it, Mapping):
            continue
        cp = it.get("contribPercent")
        val = "—" if cp is None else f"{float(cp):.1f}"
        mark = "*" if it.get("neutralFilled") else ""
        parts.append(f"{it.get('label') or it.get('dim') or '?'} {val}{mark}")
    body = f"({' · '.join(parts)})" if parts else ""
    line = f"机械分 {float(pct):.1f} / 100{body}"
    if any(isinstance(it, Mapping) and it.get("neutralFilled")
           for it in (view.get("contributions") or [])):
        nfp = view.get("neutralFilledPercent")
        share = "" if nfp in (None, 0) else f",占权重 {float(nfp):g}%"
        line += f";`*` = 该维今天没算出来、按中性分 0.5 计入(**不是表现好**{share})"
    return line


__all__ = ["DIM_LABELS", "contribution_line", "score_percent", "score_view"]
