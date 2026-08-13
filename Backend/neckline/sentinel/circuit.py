"""连续止损**纯提醒**(V2.2-⑤-B;熔断纪律已整体退役,§2.1 第 7 条已加删除线留痕)。

🔴 **2026-08-09 用户裁定 #8,原话(不得摘要、不得软化)**:

    「**我不需要你替我做决定;这个程序永远是提醒 —— 连续三笔止损真的发生了,那也是提醒**」

**退役边界(写死,⛔ 施工/维护时都不许越)**:
    · **锁定态 / 次日只减不加 / 强制复盘解锁 三件机制全删** —— 本模块不再有
      `get_state` / `is_locked` / `unlock` / `evaluate_after_close` /
      `auto_unlock_for_reviews`,`circuit_breaker` 表**停写留档不 DROP**(§七 P4-31 由
      七张扩到八张),读函数一并删(无下游消费方,同 `inquiry_log` 先例;查历史行走
      `sqlite3`)。
    · **单日实现亏损 −4000 那一档一并删**(`CIRCUIT_DAILY_LOSS_YUAN` 已不存在)——
      用户只点名了「连续三笔」。
    · **「连续 3 笔止损」这个事件本身保留**,降为**一条推送 + 一条看板事件**,
      **零状态、零锁、零行为改变**。
    · ⛔ **不许「为了安全」偷偷留一个锁定标志、一个灰化按钮、或一个「建议今天别开仓」
      的自动状态位**(§五 〇b-7)。**用户明确说了不要程序替他做决定。** 守门单测
      `tests/test_circuit.py` 把这条钉成机器判据。

**本模块现在只剩一件事**:`count_tail_consecutive_stops(db_path) -> int` —— 无状态纯读,
读 `positions`,**⛔ 不写任何表**。编排(达阈值 → 记一条看板事件 + 推一条提醒)在
`neckline/positions_entry.py::notice_consecutive_stops_after_close`,API 与 CLI 两个清仓
入口共用同一段,行为不因"从哪个口子录的"而不同。

**提醒阈值 = 命名常量 `CIRCUIT_CONSECUTIVE_STOPS=3`**(住本模块,**非** `strategy_versions`
config;理由同 `review/reconcile.py::FORCED_REVIEW_LOSS_FRAC` —— 政策值非回测参数,不进
大脑、不占 K 命名空间)。连续止损判据用到的 `stop_pct` **仍读现役 config**
(`brain.active_config`,不硬编 -5%)。

**尾部连续止损的口径(与退役前逐字相同,刻意不改)**:全部已平仓持仓按 `(sell_date, id)`
升序,从最近一笔往前数**尾部连续**的止损离场笔数;遇一笔非止损离场即断链归零。
⚠ **锚点仍是「实际以 ≤ 止损线成交的卖出」**(台账 / 交割单口径),⛔ **不改成「触发止损
警戒的次数」**(§五 ⑤ 工程细节 3 写死:那会让提醒被提醒频率驱动,而不是被真实亏损驱动
—— 尤其在 −5% 从强制条件单降为警戒之后)。
⚠ **链无时间窗、也无"重置"概念了**:横跨数月的 3 笔同样计数;第 4 笔止损 → 尾部连续
数变成 4 → **再提醒一条**(既然没有锁,也就没有"解锁后才重推"这回事,⛔ 别自己发明)。

**离场原因兜底(近似口径,已标注)**:仅当 `close_reason IS NULL` 时,**近似**判定
`sell_price ≤ buy_price×(1−stop_pct)+_EPS` → 计止损;用户显式选了非 NULL 码则**信标注、
不用价格二次猜**。**兜底只用于提醒计数、绝不回写 `positions.close_reason`**(库里仍
NULL,不臆造历史)。

**诚实边界**:只能基于**用户已补录进台账**的成交计数,漏录则失灵 —— 提醒文案里如实
写「基于台账 N 笔已补录成交」。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from neckline.db import init_schema
from neckline.sentinel.positions import (
    CLOSE_REASON_STOP_LOSS,
    STATUS_CLOSED,
    load_all_positions,
)

# —— 提醒阈值命名常量(单一源,住本模块;禁止把 3 抄进任何别处)——————————————
# ⚠ 它现在的语义是**提醒阈值**,不是"熔断阈值"——达到它只会多一条通知,不改变任何行为。
CIRCUIT_CONSECUTIVE_STOPS = 3

# 阈值比较浮点容差(CLAUDE.md 记的纪律阈值比较一律加 _EPS,不写裸 >=/<=)。
_EPS = 1e-9


def _active_stop_pct(db_path: Optional[Path]) -> float:
    """现役 config 的 `stop_pct`(单一事实源,§3.8 铁律,不硬编 -5%)。无现役版本 →
    退回 `MomentumConfig` 字段默认(与 api `_active_config` 同款兜底)。

    **兜底判据是「键缺失」不是「falsy」(审计 🔵-9)**:章程若**显式**设 `stop_pct=None`
    (不设止损),不能被悄悄换回 0.05。显式 None → 返 0.0 = 价格兜底阈值退化为
    `sell_price ≤ buy_price`,即「没有止损线可判」时不把普通小亏当止损计入连续链
    (保守方向:少计,不臆造)。

    ⚠ `v2.2-k8` 起 `stop_pct` **仍是 0.05**(§五 ⑤:值与唯一源地位一字不动,改的只是它
    触发什么),故本函数在新章程下行为不变。"""
    from neckline.strategy import brain
    from neckline.strategy.momentum_config import MomentumConfig

    cfg = brain.active_config(db_path=db_path)
    if "stop_pct" not in cfg:
        return float(MomentumConfig().stop_pct)
    val = cfg["stop_pct"]
    return float(val) if val is not None else 0.0


def _is_stop_loss_close(pos, stop_pct: float) -> bool:
    """一笔已平仓是否为「止损离场」。**显式 `close_reason` 非空 → 信用户标注**(只认
    `STOP_LOSS`);**仅当 `close_reason` NULL/空 → 价格近似兜底**(`sell_price ≤
    buy_price×(1−stop_pct)+_EPS`)。"""
    if pos.close_reason:  # 非空 = 用户显式标注 → 不用价格二次猜
        return pos.close_reason == CLOSE_REASON_STOP_LOSS
    if pos.sell_price is None or pos.buy_price is None or pos.buy_price <= 0:
        return False
    threshold = pos.buy_price * (1.0 - stop_pct)
    return pos.sell_price <= threshold + _EPS


def count_tail_consecutive_stops(db_path: Optional[Path] = None) -> int:
    """**尾部连续止损笔数**(本模块唯一的公开函数,无状态纯读)。

    读 `positions` 的全部已平仓回合,按 `(sell_date, id)` 升序,从最近一笔往前数连续的
    止损离场笔数;遇一笔非止损离场即停(返回到此为止的计数)。**⛔ 不写任何表、不落
    任何状态、不发任何推送** —— 要不要提醒由调用方按 `CIRCUIT_CONSECUTIVE_STOPS` 判。

    库里零已平仓 / 最近一笔不是止损 → 返 `0`。"""
    init_schema(db_path)
    stop_pct = _active_stop_pct(db_path)
    closed: List = [
        p for p in load_all_positions(db_path=db_path)
        if p.status == STATUS_CLOSED and p.sell_date and p.sell_price is not None
    ]
    closed.sort(key=lambda p: (p.sell_date, p.id))
    n = 0
    for p in reversed(closed):
        if not _is_stop_loss_close(p, stop_pct):
            break
        n += 1
    return n


__all__ = [
    "CIRCUIT_CONSECUTIVE_STOPS",
    "count_tail_consecutive_stops",
]
