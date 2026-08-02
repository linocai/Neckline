"""④篮子卡的**验证 / 失效条件集与聚合规则**(plan §五 V2-⑦-b,2026-08-02 planner
裁定)—— 全项目**唯一一处**定义「什么算被验证、什么算被证伪、几只成员算数、四态
怎么映射」的地方。

**为什么单独一个小模块**(⑦-b 落地要求原文:「集中到一处命名常量……或独立小模块」):

    · **生产者与消费者要吃同一份定义**。⑦(`selection/basket_card.py`)把阈值**冻**
      进 `card_json.verification_spec` / `invalidation_spec`,⑧(`sentinel/
      basket_verify.py`)盘中 / EOD 拿现价代进去判 —— 两边各写一份比较语义,迟早漂。
    · **不能塞进 `basket_card.py`**:那样 ⑧ 就得 `import selection.basket_card`,而
      后者 `import neckline.sentinel.universe`(查 `board`/`is_st`)→ 一条 sentinel ⇄
      selection 的模块级环。本模块**零项目内 import**,谁都可以安全地引它。

**归属裁定(⑦-b-A,写死防以后类推)**:本节这套条件集是 **引擎默认,⛔ 本版不进包**。
三条理由:①§12.2 插槽边界是**用户拍板**的,明文把「④篮子卡冻结体例」列在「引擎本体,
不进包」一侧,要包化必须走「扩插槽边界(用户拍板)→ 扩 schema → 发包」三步;②**零
审计背书**,进包会被"体面化"成「策略线校准过的参数」;③它有下游机械消费方(哨兵)。
⚠ 包化的风险**不来自**「换包会改盘中判定」—— spec 在 D0 就冻进卡,⑧ 读冻结件。

⚠ **本模块的每一个数字都是「临时默认、零审计背书」** —— 没有任何回测或事件研究支持
它们,它们是为了让四态状态机能跑起来而拟的占位值(§七 P3-34 已挂账)。**升级路径写死**:
⑨ 评价引擎攒够样本、若证明需要按 regime 校准 → 才谈包化,且必须走上面那三步。
**在那之前 ⛔ 不许自行改数、也不许自行加包键。**

**语义红线(文案与接线都必须守)**:

    · `verified` 证明的是「**没走坏 + 共振存在**」,**不是「驱动兑现」、更不是「可以
      追」**。卡面 / 报告 / 客户端一律不得把它写成买入信号,每处带「参考、非指令」。
    · 失效侧的**章程止损线在这里只用来判「驱动是否被证伪」**,**⛔ 不触发任何交易
      动作、不进推送、不改任何持仓判定**。它与哨兵对真实持仓执行的 −5% 止损纪律
      是两回事(同一个数字、同一个单一源,但一个问"这个假设还成不成立"、一个问
      "这笔仓该不该走")。**⛔ 不许把篮子 `falsified` 接成任何持仓动作。**
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# —— 条件集版本(⑦-b 新增,**与 `spec_version` 分开**)————————————————————
# `spec_version` 跟的是**形状**(键怎么摆),本串跟的是**条件集与阈值**(判据是什么)。
# **条件或阈值一改就 bump**。为什么要它:⑨ 评价引擎按它分层,才谈得上「这套条件集的
# 验证率是多少」;没有它,日后回看会把两套条件集的成绩混成一锅。
VERIFICATION_RULESET_VERSION = "verify_ruleset_v1"

# —— 结构化条件码(**⑧ 的唯一判据源**;码即契约,改名等于改契约 → 连带 bump 上面
#    的 ruleset 版本与对应 spec 的 `spec_version`)————————————————————————
COND_CLOSE_AT_OR_ABOVE_REF = "close_at_or_above_ref"
COND_HOLDS_MA20 = "holds_ma20"
COND_CLOSE_BELOW_STOP_LINE = "close_below_stop_line"
COND_LIMIT_DOWN_TOUCH = "limit_down_touch"
# ⑦-b-B 修订(2026-08-02):原「收盘 < MA20」单条与验证侧的「≥MA20」**互为反面**,
# 擦边跌破立刻等于「驱动已证伪」,`unclear` 几乎没有生存空间、`falsified` 打得又早
# 又滥。改成「< D0 收盘 **且** < D0 MA20」两条 AND,中间地带(高于起点但丢了均线 /
# 低于起点但守住均线)才落回 `partial`/`unclear`,四个格子各有各的地盘。
# **本修订零新常量** —— 只是把两个已有条件用 AND 连起来,不发明任何数。
COND_BELOW_REF_AND_MA20 = "close_below_ref_and_ma20"

# 复合条件 `COND_BELOW_REF_AND_MA20` 在 `members[]` 行里的两个子阈值键名。
LEVEL_REF_CLOSE = "ref_close"
LEVEL_MA20 = "ma20"

COND_DESC: Dict[str, str] = {
    COND_CLOSE_AT_OR_ABOVE_REF: "次日收盘 ≥ 基准日收盘(驱动被跟随)",
    COND_HOLDS_MA20: "次日收盘 ≥ 基准日 MA20(结构未破)",
    COND_CLOSE_BELOW_STOP_LINE: "次日收盘 ≤ 章程止损线(现役 stop_pct 算,系统算不由模型给)",
    COND_LIMIT_DOWN_TOUCH: "次日最低价 ≤ 跌停价(触及跌停)",
    COND_BELOW_REF_AND_MA20: "次日收盘 < 基准日收盘 **且** < 基准日 MA20(两条同时成立才算破位)",
}

# —— 比较语义(**存进卡里**,⑧ 按卡里的这个串选比较器;卡是冻结件,老卡写的是哪种
#    比较就按哪种判,不因引擎升级改口)————————————————————————————————————
CMP_CLOSE_GE = "close>=level"        # 现价 / 收盘 ≥ 阈值
CMP_CLOSE_LE = "close<=level"        # 现价 / 收盘 ≤ 阈值
CMP_LOW_LE = "low<=level"            # 当日最低 ≤ 阈值(**触及即算**,不要求收在那)
CMP_CLOSE_LT_ALL = "close<all_levels"  # 现价 / 收盘 **同时** 严格低于全部子阈值

# —— 成员级 · 验证条件(两条 **AND**)——————————————————————————————————————
# 单看很弱(「没跌」而已),但**篮子级聚合才是它的意义所在**:「过半成员都没跌且都
# 站上 MA20」是**共振**的直接证据,远强于任何单只票的表现。⛔ 不要为了让单条看起来
# "够格"而加阈值。
VERIFY_REQUIRE_ALL: Tuple[str, ...] = (COND_CLOSE_AT_OR_ABOVE_REF, COND_HOLDS_MA20)

# —— 成员级 · 失效条件(三条 **OR**,第 ③ 条即上面那条修订过的复合条件)——————
INVALIDATE_ANY_OF: Tuple[str, ...] = (
    COND_CLOSE_BELOW_STOP_LINE, COND_LIMIT_DOWN_TOUCH, COND_BELOW_REF_AND_MA20,
)

_COMPARE_OF: Dict[str, str] = {
    COND_CLOSE_AT_OR_ABOVE_REF: CMP_CLOSE_GE,
    COND_HOLDS_MA20: CMP_CLOSE_GE,
    COND_CLOSE_BELOW_STOP_LINE: CMP_CLOSE_LE,
    COND_LIMIT_DOWN_TOUCH: CMP_LOW_LE,
    COND_BELOW_REF_AND_MA20: CMP_CLOSE_LT_ALL,
}

# —— 篮子级 · 聚合门槛 = `ceil(n / 2)`(n=1→1,n=2→1,n=3→2)———————————————
# 取 `ceil` 而不是「严格过半」:2 只篮要求两只同时命中会让「已验证」几乎不可能发生,
# 而四态里本就有 `partial` 承接「只对了一半」。**验证侧与失效侧用同一个数**(对称、
# 可解释、不发明第二个数;「按角色加权」是合理的日后改良方向但没有证据,本版不做,
# 已登记 §七 P3-34)。
MIN_MEMBERS_HIT_DIVISOR = 2

# 浮点比较容差(工程不变量,同 `sentinel/holding.py::_EPS` / `primitives._LIFT_EPS`
# 先例:裸 >=/<= 比较浮点价位是本项目通用坑),非策略参数。
EPS = 1e-9

# —— 四态(⑧ 的状态机取值域,同时是 `basket_verification.state` 的取值域)————
STATE_VERIFIED = "verified"
STATE_PARTIAL = "partial"
STATE_UNCLEAR = "unclear"
STATE_FALSIFIED = "falsified"
STATES: Tuple[str, ...] = (STATE_VERIFIED, STATE_PARTIAL, STATE_UNCLEAR, STATE_FALSIFIED)


def min_members_hit(n: int) -> int:
    """篮子级聚合门槛 = **过半(向上取整)成员命中**;两侧同一个数(见上方常量注释)。"""
    n_i = max(0, int(n))
    return max(1, (n_i + MIN_MEMBERS_HIT_DIVISOR - 1) // MIN_MEMBERS_HIT_DIVISOR)


def decide_state(verify_hits: int, invalidate_hits: int, min_hit: int) -> str:
    """四态映射(⑦-b 定死,⑧ 照此实现,**⛔ 不许在别处再写一份**)::

        falsified | 失效命中数 ≥ min_hit                       ← **优先级压过一切**
        verified  | 验证命中数 ≥ min_hit 且未达失效门槛
        partial   | 有验证命中但 < min_hit,且未达失效门槛
        unclear   | 零验证命中且未达失效门槛(含数据缺失)

    **`falsified` 压过一切**(同一拍两侧都达门槛时判 `falsified`):证伪是风险信息,
    **宁可先说坏消息**,与「参考在上、纪律兜底」的保守方向一致。
    """
    m = max(1, int(min_hit))
    if int(invalidate_hits) >= m:
        return STATE_FALSIFIED
    if int(verify_hits) >= m:
        return STATE_VERIFIED
    if int(verify_hits) > 0:
        return STATE_PARTIAL
    return STATE_UNCLEAR


def compare_of(code: str) -> Optional[str]:
    """某条件码的比较语义串(生成 spec 时写进 `conditions[].compare`)。未知码 → None。"""
    return _COMPARE_OF.get(code)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def evaluate_condition(
    compare: str, levels: Any, *, price: Optional[float], low: Optional[float]
) -> Optional[bool]:
    """按 spec 里写的 `compare` 串判一条条件。

    返回 `True`/`False` = 判了;**`None` = 这一条判不了**(阈值是 `null` / 观测缺失 /
    比较语义不认识)。⚠ **`None` 绝不许被当成 `False`**:「没有」与「没看」必须分得开
    (⑦-b:数据缺失 ≠ 证伪)。

    `levels`:标量阈值,或复合条件的 `{子键: 阈值}` 映射(见 `CMP_CLOSE_LT_ALL`)。
    """
    p = _num(price)
    if compare == CMP_CLOSE_GE:
        lv = _num(levels)
        return None if (p is None or lv is None) else p >= lv - EPS
    if compare == CMP_CLOSE_LE:
        lv = _num(levels)
        return None if (p is None or lv is None) else p <= lv + EPS
    if compare == CMP_LOW_LE:
        lv, lo = _num(levels), _num(low)
        return None if (lo is None or lv is None) else lo <= lv + EPS
    if compare == CMP_CLOSE_LT_ALL:
        if not isinstance(levels, Mapping) or p is None:
            return None
        vals = [_num(levels.get(k)) for k in (LEVEL_REF_CLOSE, LEVEL_MA20)]
        if any(v is None for v in vals):
            return None            # 任一子阈值算不出 → 整条复合条件不判(不猜半条)
        return all(p < v - EPS for v in vals if v is not None)
    return None                    # 不认识的比较语义:如实"判不了",不瞎猜


def conditions_block(codes: Sequence[str]) -> List[Dict[str, Any]]:
    """spec 里 `conditions[]` 那一段(每条带人读描述 + 机器可用的比较语义)。"""
    out: List[Dict[str, Any]] = []
    for c in codes:
        row: Dict[str, Any] = {
            "code": c, "scope": "member", "compare": compare_of(c), "desc": COND_DESC[c],
        }
        if c == COND_BELOW_REF_AND_MA20:
            row["levels"] = [LEVEL_REF_CLOSE, LEVEL_MA20]
        out.append(row)
    return out


__all__ = [
    "VERIFICATION_RULESET_VERSION",
    "COND_CLOSE_AT_OR_ABOVE_REF", "COND_HOLDS_MA20", "COND_CLOSE_BELOW_STOP_LINE",
    "COND_LIMIT_DOWN_TOUCH", "COND_BELOW_REF_AND_MA20",
    "LEVEL_REF_CLOSE", "LEVEL_MA20", "COND_DESC",
    "CMP_CLOSE_GE", "CMP_CLOSE_LE", "CMP_LOW_LE", "CMP_CLOSE_LT_ALL",
    "VERIFY_REQUIRE_ALL", "INVALIDATE_ANY_OF",
    "MIN_MEMBERS_HIT_DIVISOR", "EPS",
    "STATE_VERIFIED", "STATE_PARTIAL", "STATE_UNCLEAR", "STATE_FALSIFIED", "STATES",
    "min_members_hit", "decide_state", "compare_of", "evaluate_condition", "conditions_block",
]
