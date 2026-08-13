"""持仓语义文案**单一源**(V2.4.0 P3.1,K8.md §十三 / §十九)。

`v2.3-k8` 把 −5% 从「强制条件单」改判「亏损警戒 + 由用户复核原判断」,回落止盈与
时间退出两条机械纪律均已取消(`take_profit_retrace=None` / `max_hold_days=None`)。
旧文案把 −5% 那条线叫「止损线」、把回落止盈说成"仍然是纪律",在这套章程下是**对用户
撒谎** —— 本模块把「这条线该叫什么 / 触发后说什么 / 有没有回落止盈」这几句判定收拢到一处,
`sentinel/holding.py`、`api/app.py`、`selection/basket_card.py` 都读它,不各自拍一份文案。

🔴 **数值口径一字不动**:本模块只产文案,不改任何阈值 / 判定逻辑(`stop_pct` 仍是
`strategy_versions` 现役 config 的唯一源,本模块不读它、只接收调用方已经取到的值)。

🔴 **历史章程仍说历史真话**(K8.md §十三 末句):入参永远是**那一行**(生成时 / 那笔
持仓当时)的章程配置,不是"现役"—— 调用方传哪版就出哪版的文案,`v1.3.3`/`v2.2-k8`
等老章程一字不受影响(仍是「止损线」/「回落止盈 8%」/「时间退出 D5」)。
"""

from __future__ import annotations

from typing import Optional

# —— 亏损警戒(review)口径下必须说出口的动作短语 ——————————————————————————
# K8.md §十三 逐字:「触发后由你复核原判断」。⛔ **不是**「离场决策在你」这类未点名
# "复核"动作的旧措辞——P3.1 版本裁定用这句话取代它(旧文案未点名"由你做什么",
# 容易被读成"系统已经判完、只是不动手";新文案点名"原判断需要你重新过一遍")。
ADVISORY_ACTION_PHRASE = "触发后由你复核原判断"

# 强制条件单口径(老章程,advisory=False)下的动作短语,逐字不变——这是给
# `v2.2-k8` 激活前(K1/v1.3.3)那批章程用的,§2.1 前置提示「激活前本节其余全文
# 一字有效」的落点,⛔ 不随本次改动变化。
CONDITIONAL_ORDER_ACTION_PHRASE = "若条件单未成交请立即人工确认"

# —— 本版无回落止盈 / 无时间退出的固定披露(P3.1 表逐字)——————————————————
RETRACE_DISABLED_COPY = "本版无机械回落止盈"
TIME_EXIT_DISABLED_COPY = "本版无机械时间退出 —— D 计数只作记录"

# P3.2:离场参考的对外披露 = §2.8-C-3 前提③ **改写后**的那半句(旧措辞点名的是
# 回落止盈那条机械纪律,而 `v2.3-k8` 已经没有它,继续那样写就是对用户撒谎;版本裁定
# 全文见 `PROJECT_PLAN.md` §五 V2.4.0 P3.2 与 §2.8-C-3 正文标注)。
# ⚠ **它的消费方在客户端**(`PositionExtras.swift` 那句常显披露):Swift 读不到 Python,
# 两边各写一份字面量、由 `tests/test_v240_p3_frontend.py` 的跨语言守门把它们钉在一起
# ——同 `stop_line_short_label` 那条「两处故意各自实现一遍」的既有体例。
EXIT_REFERENCE_DISCLOSURE = "离场参考是计划参考,不是止盈信号"


def stop_line_label(advisory: bool) -> str:
    """这条线的全称:「亏损警戒线」(review 口径)或「止损线」(强制条件单口径)。

    ⚠ **数值口径一字未变**(仍是 `buy×(1−stop_pct)`)——这里只回答「叫什么」。
    """
    return "亏损警戒线" if advisory else "止损线"


def stop_line_short_label(advisory: bool) -> str:
    """紧凑位用的短称(与客户端 `Position.stopLineShortLabel` 同一套判据、同一个词,
    两处故意各自实现一遍——服务端侧给文案生成用,客户端侧给列表行等宽位用,互不调用)。
    """
    return "警戒线" if advisory else "止损线"


def stop_action_phrase(advisory: bool) -> str:
    """到线 / 逼近线之后那半句「怎么办」。"""
    return ADVISORY_ACTION_PHRASE if advisory else CONDITIONAL_ORDER_ACTION_PHRASE


def exit_reference_reached_copy(price: float, exit_low: float, exit_high: float) -> str:
    """触达离场参考区间的**事件文案单一源**(V2.4.0 P3.2)。

    施工图 P3.2 给的是**逐字骨架**「现价已触达你计划中的离场参考区间;这不是止盈信号,
    是否离场由你判断。」—— 本函数逐字照抄这句话,只在两个自然位置插进**具体数字**
    (现价 / 区间上下沿)。⚠ **插数字不是改文案**:骨架的每一个词、词序都没动,而
    「哪只票、什么价、什么区间」是 ⑪-B 三句式「讲清发生了什么」的必需件 ——
    ⛔ 拿掉它们会让这条立即级推送变成一句无法核对的空话。

    🔴 **这段话是 §2.8-C-3 记名豁免的前提③**(纯告知型、禁指令词、点明「在你计划里 +
    不是止盈信号」)。前提①②④ 一字不变、仍然缺一即豁免失效 —— **改这段话之前先回去
    读那一节**;把「这不是止盈信号」或「你计划中的」任一半句删掉,这条 kind 就不再
    被允许推送了(守门单测 `tests/test_exit_reference_gates.py` 逐条锁死)。
    """
    return (f"现价{price:.2f}已触达你计划中的离场参考区间"
            f"[{exit_low:.2f}, {exit_high:.2f}];这不是止盈信号,是否离场由你判断")


def retrace_disabled_copy(take_profit_retrace: Optional[float]) -> Optional[str]:
    """`take_profit_retrace` 未配置时的固定披露;**已配置则返回 `None`**(该说什么由
    调用方按既有的"回落止盈 N%"格式自己拼,本函数只管"没有"这一态,不重复"有"的格式化)。
    """
    if take_profit_retrace is not None and take_profit_retrace > 0:
        return None
    return RETRACE_DISABLED_COPY


__all__ = [
    "ADVISORY_ACTION_PHRASE",
    "CONDITIONAL_ORDER_ACTION_PHRASE",
    "RETRACE_DISABLED_COPY",
    "TIME_EXIT_DISABLED_COPY",
    "EXIT_REFERENCE_DISCLOSURE",
    "stop_line_label",
    "stop_line_short_label",
    "stop_action_phrase",
    "exit_reference_reached_copy",
    "retrace_disabled_copy",
]
