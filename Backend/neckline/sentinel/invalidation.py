"""⛔ **DEPRECATED(V2.4.0 P0 退役)—— 生产链零调用,本模块不再产生任何新事件。**

    · **退役的是什么**:「**通用**盘中证伪」—— 所有 T1/T2 成员共用**一套全局常量**
      (低开阈 / 量比上下限 / VWAP 开关),而不是每票 D0 冻结的独立失效条件;
      命中即写 `sentinel='invalidation'` 事件并当日按固定 `event_key` 闩锁,
      后续站回 VWAP、翻红、量能修复**都不会翻转前端结论**(审计规格 P0.1)。
    · **产品决定**:用户自行观察盘中分时;系统不再据普通盘中波动出「证伪」判决。
    · **断链落点**:`sentinel/engine.py::run_tick` 原第 2 段整段删除;
      `sentinel/universe.py::WatchTarget.invalidation_spec` 字段与那次 import 一并摘除。
    · **为什么文件还在**:30+ 条既有单测把它当行为基准读,且它是「代码回 v2.3.3」
      这条回滚绳的一部分(§3.14-A)。**判据不是「文件在哪」,是「生产入口有没有它」**
      —— 守门单测 AST 扫 `engine.py`,`check_invalidation` 调用点数必须为 0。
    · ⛔ **不许半退役**:⛔ 不许「前端隐藏、后台仍在判」,⛔ 不许改名叫「风险 / 观察」
      再接回去,⛔ 不许换成分数 / 状态机 / 交通灯。

🔴 **「invalidation」在本仓有三个互不相干的含义,本模块是 ①,只有 ① 退役**:

    ① **本模块** = 通用盘中证伪(VWAP / 低开未翻红 / 折算量比)—— **已退役**;
    ② 卡上 `invalidation_spec` / `close_below_stop_line` = **D0 冻结的判断失效位置**
       (K8 §十一 交易资格四件套第 4 件)—— **明令保留,一行未动**;
    ③ `auction/mech.py::hit_invalidation` + `decision_log.invalidation` = 竞价层命中
       D0 失效位 / 决策日志字段 —— **明令保留,一行未动**。

    ⚠ 本模块的 `invalidation_spec()` 是 ①(零入参全局常量),**与 ② 同名不同物** ——
    裸 `grep invalidation` 会把三者一起捞上来,**按文件与符号看,⛔ 不按词看**。

以下为退役前的原文说明(**历史留痕,行为已从生产链断开**):

证伪哨兵(plan §2.4 第4条,**⑪-A 点名的「证伪四哨兵」= 现役纪律分支,原样保留**)。
**只用价量结构,不看资金面**(§2.4 铁律:「盘中主力资金流免费源不可靠,证伪只用
价量结构」)。读 `WatchTarget.invalidation_spec`,不重新发明任何阈值:

⚠ **V2-⑬-1 判定对象换血(判定逻辑一行未改)**:V1 的对象是「昨晚 20 只候选」,
候选榜已删 → 对象换成 **D0 冻结的 T1/T2 篮子成员**(`universe.WatchTarget`)。
之所以能一行不改地平移:本模块用到的 `invalidation_spec` 是**全局常量三件**
(低开阈 / 量比上下限 / VWAP 开关),`report/candidates.py::invalidation_spec()`
本来就是**零入参**的纯常量函数 —— 它不是"每只票各自算出来的",没有任何 per-code
依赖需要跟着候选榜一起消失。该函数与三个常量随 `report/candidates.py` 删除**搬到
本模块**(唯一消费方就是这里)。

⛔ **与 ⑦-b/⑧ 的「篮子 `falsified`」是两件事,不许混**:那个问「这个驱动假设还
成不成立」(篮子级、⛔ 不进推送);本模块问「这只票今天还能不能进」(成员级、
进推送)。同一天同一只票两者给相反结论是**合法**的。

    · 低开不回:开盘涨幅 ≤ `low_open_pct`(默认 -2%)**且**截至当前仍未翻红
      (现价 < 昨收)。EOD 口径原文是"全天未翻红",盘中检查的是"截至目前"——
      这是哨兵与报告的本质差异:报告是事后总结,哨兵是提前预警,不必等到
      15:00 才告诉用户"今天别进了"。
    · 跌破VWAP:现价 < 当日VWAP(`require...vwap_break` 为真时生效)。
    · 量能异常:折算量比 < `vol_ratio_low`(地量无接力)或 > `vol_ratio_high`
      (异常放量疑似出货)。

命中任一条 → 一条**剔除类盘中判决**(⛔ 原措辞已随 V2.4.0 P0 按 P0.7 判据 #2
从全仓清除)。与买点哨兵共用同一条「开盘头几分钟不判断」纪律
(结构性判断在集合竞价延续期不可靠)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from neckline.sentinel.intraday import elapsed_trading_minutes, intraday_vol_ratio, vwap_of
from neckline.data.realtime import Quote

# 开盘头几分钟集合竞价延续 + 极早盘噪声大,VWAP/价格结构判断均不可靠,先按兵不动
# (量能折算另有更严的 60min 阈,见 `intraday.EARLY_MINUTES_THRESHOLD`)。
# ⚠ V2-⑬-1 前住在已退役的 `sentinel/entry.py`(买点哨兵)里,随其删除搬到本模块。
MIN_STRUCTURAL_ELAPSED_MINUTES = 5

# —— 证伪条件阈值(结构化;只用价量结构,不看资金面,§2.4)——————————————————
# V2-⑬-1 前住在 `report/candidates.py`(候选四件套的一部分),随该模块删除搬来。
LOW_OPEN_PCT = -0.02
VOL_RATIO_LOW = 0.8
VOL_RATIO_HIGH = 3.0


def invalidation_spec() -> Dict[str, Any]:
    """证伪条件(结构化,**零入参 = 全局常量,不是 per-code 判据**)。只用价量,不看
    资金面(§2.4 铁律:盘中主力资金流免费源不可靠)。`universe.py` 给每个关注目标挂
    的就是这一份。"""
    return {
        "low_open_pct": LOW_OPEN_PCT,
        "require_stay_below_prev_close": True,   # 全天未能翻红(收盘仍 < 昨收)
        "vwap_break": True,                        # 全天收盘价 < 当日 VWAP
        "vol_ratio_low": VOL_RATIO_LOW,
        "vol_ratio_high": VOL_RATIO_HIGH,
    }


@dataclass
class InvalidationSignal:
    ts_code: str
    name: str
    price: float
    reasons: List[str] = field(default_factory=list)

    @property
    def reason_text(self) -> str:
        return ";".join(self.reasons)


def check_invalidation(
    target: "Any",
    quote: Optional[Quote],
    prev5_avg_vol: float,
    now: datetime,
) -> Optional[InvalidationSignal]:
    """该关注目标是否命中证伪条件。`quote is None` → None(拉不到行情时不妄下
    "剔除"判断,宁可漏判也不能拿缺失数据当证据)。

    `target` 是 **duck-typed**:只要求 `.ts_code`/`.name`/`.invalidation_spec` 三个
    属性(现役实现 = `sentinel.universe.WatchTarget`)。"""
    if quote is None:
        return None

    elapsed_min = elapsed_trading_minutes(now)
    if elapsed_min < MIN_STRUCTURAL_ELAPSED_MINUTES:
        return None

    spec = target.invalidation_spec or {}
    reasons: List[str] = []

    if quote.pre_close and quote.pre_close > 0:
        gap_pct = (quote.open - quote.pre_close) / quote.pre_close
        low_open_pct = spec.get("low_open_pct")
        still_red = quote.price < quote.pre_close
        if low_open_pct is not None and gap_pct <= low_open_pct and still_red:
            reasons.append(f"低开{gap_pct:.1%}且截至目前未翻红")

    vwap, is_above_vwap = vwap_of(quote)
    if spec.get("vwap_break") and is_above_vwap is False:
        reasons.append(f"现价{quote.price:.2f}跌破当日VWAP{vwap:.2f}")

    vol_ratio, vol_note = intraday_vol_ratio(quote.volume, prev5_avg_vol, elapsed_min)
    if vol_ratio is not None:
        vol_low = spec.get("vol_ratio_low")
        vol_high = spec.get("vol_ratio_high")
        if vol_low is not None and vol_ratio < vol_low:
            reasons.append(f"量能折算仅{vol_ratio:.1f}倍(地量无接力)")
        elif vol_high is not None and vol_ratio > vol_high:
            reasons.append(f"量能折算高达{vol_ratio:.1f}倍(异常放量疑似出货)")

    if not reasons:
        return None

    return InvalidationSignal(ts_code=target.ts_code, name=target.name, price=quote.price, reasons=reasons)


__all__ = [
    "InvalidationSignal", "check_invalidation", "invalidation_spec",
    "MIN_STRUCTURAL_ELAPSED_MINUTES", "LOW_OPEN_PCT", "VOL_RATIO_LOW", "VOL_RATIO_HIGH",
]
