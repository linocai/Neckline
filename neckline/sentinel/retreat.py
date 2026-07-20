"""退潮哨兵(plan §2.4 第2条)。「盘中情绪恶化(炸板率飙升/跌停家数急扩/主线板块
跳水)→ 推『今日计划作废、禁开新仓』红色刹车」。触发后应联动买点哨兵(engine.py
的编排职责:退潮生效当日不再推新的买点确认,§2.4 铁律"永不盘中推荐新票"的延伸
——已经触发的买点确认也算"新开仓许可",同样要被刹车抑制)。

**设计决策说明(工程判断,非偏离 plan——plan 未指定退潮哨兵具体轮询多大范围)**:
本哨兵的市场宽度统计**不轮询全市场约5900只股票**,而是统计「关注池」
(`universe.load_watch_universe`:候选 + 持仓 + 昨日涨停股,已在同一拍批量拉价,
零额外网络开销)。理由:
    1. 免费实时源(新浪/腾讯)未公开单请求代码数上限、也未公开限流阈值;
       每分钟对全市场发起数千代码的批量请求,持续6.5小时,是明显偏离"个人
       量化助手正常使用量级"的重负载,长时间高频这样打很可能触发限流甚至
       封禁本机IP——对一个需要长期稳定运行的免费源,这是不可接受的操作风险。
    2. 关注池本身就是「候选(强势票)+ 持仓 + 昨日涨停股(严格意义上的当前市场
       主线/情绪最前沿)」,恰好是判断"主线是否退潮"最相关的样本,不是随手
       选一批无关票打折扣替代全市场。
本设计的代价:样本量远小于全市场(通常几十到大约 `universe.DEFAULT_BREADTH_CAP`
只),`zaban_rate`/跌停家数的绝对值**不能直接与盘后报告(`report.sentiment`,全
市场真实统计)的同名字段比较量级**——本模块的判定阈值因此单独设置,并且明确
标注为**未回测的启发式**,不是既有情绪仪表盘阈值的简单复制。若实盘归因显示
此代理样本不够灵敏,Backlog 已记录"改为定期(如5分钟一次)全市场轮询"作为
后续候选,不在本阶段实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from neckline.data.limit_derived import compute_intraday_limit_prices
from neckline.report.sentiment import SentimentDashboard
from neckline.sentinel.quotes import Quote
from neckline.sentinel.universe import StockMeta, is_new_stock_exempt

# —— 阈值(全部**未回测,启发式**,与本项目一贯的诚实标注风格一致;命名常量
#    便于实盘归因后定点调整,不散落各处魔法数字)——————————————————————————

# 炸板率绝对触发线(呼应 `report.sentiment.MIN_ZABAN_RATE_FOR_REST=0.50`,但这是
# 关注池样本而非全市场,数值同为0.5是刻意沿用"过半即弱"的直觉,不是同一份统计)。
ZABAN_RATE_ABS_TRIGGER = 0.50
# 炸板率相对昨晚报告的飙升幅度(绝对百分点差值)。
ZABAN_RATE_SPIKE_DELTA = 0.20
# 炸板率判定所需最小分母(涨停+炸板样本数太小时百分比噪声大,不判断)。
ZABAN_MIN_SAMPLE = 5

# 跌停家数触发(绝对数与占关注池比例,任一达到即触发)。
LIMIT_DOWN_COUNT_TRIGGER = 5
LIMIT_DOWN_RATE_TRIGGER = 0.15

# 主线板块跳水:关注池内热门板块标签个股的平均盘中跌幅阈值。
SECTOR_DIVE_RET_TRIGGER = -0.03


@dataclass
class MarketBreadthSnapshot:
    trade_date: date
    sample_size: int
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    zaban_rate: float


@dataclass
class RetreatAlert:
    reasons: List[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.reasons)

    @property
    def reason_text(self) -> str:
        return ";".join(self.reasons)


def compute_breadth_snapshot(
    trade_date: date,
    quotes: Dict[str, Quote],
    meta: Dict[str, StockMeta],
) -> MarketBreadthSnapshot:
    """关注池当前的涨停/跌停/炸板统计。涨跌停价用 `compute_intraday_limit_prices`
    逐票现算(复用 `limit_derived` 幅度规则,§2.4「盘中涨跌停判定用现价对涨跌停价」)
    ——不是从 EOD `limit_derived` 表读昨天的价,是拿当前 `quote.pre_close` 现算
    今天的涨跌停价。缺 meta(未知板块)或处于新股豁免窗口的票跳过,不计入分母
    (它们结构上不可能"涨停/跌停",纳入分母只会把比率稀释失真)。
    """
    limit_up = limit_down = zaban = 0
    sample = 0
    for code, quote in quotes.items():
        m = meta.get(code)
        if m is None:
            continue
        if is_new_stock_exempt(m, trade_date):
            continue
        up, down = compute_intraday_limit_prices(quote.pre_close, m.board, m.is_st, trade_date)
        if up is None or down is None:
            continue
        sample += 1
        if quote.price >= up:
            limit_up += 1
        elif quote.price <= down:
            limit_down += 1
        elif quote.high >= up:
            zaban += 1

    denom = zaban + limit_up
    zaban_rate = (zaban / denom) if denom > 0 else 0.0
    return MarketBreadthSnapshot(
        trade_date=trade_date, sample_size=sample, limit_up_count=limit_up,
        limit_down_count=limit_down, zaban_count=zaban, zaban_rate=zaban_rate,
    )


def check_retreat(
    snapshot: MarketBreadthSnapshot,
    prev_eod_sentiment: Optional[SentimentDashboard] = None,
    hot_sector_peer_rets: Optional[List[float]] = None,
) -> Optional[RetreatAlert]:
    """盘中情绪恶化 → 红色刹车。命中任一条即触发(不要求同时满足)。"""
    reasons: List[str] = []

    if snapshot.zaban_count + snapshot.limit_up_count >= ZABAN_MIN_SAMPLE:
        if snapshot.zaban_rate >= ZABAN_RATE_ABS_TRIGGER:
            reasons.append(
                f"关注池炸板率{snapshot.zaban_rate:.0%}(样本{snapshot.zaban_count + snapshot.limit_up_count}只)过高"
            )
        elif prev_eod_sentiment is not None:
            delta = snapshot.zaban_rate - prev_eod_sentiment.zaban_rate
            if delta >= ZABAN_RATE_SPIKE_DELTA:
                reasons.append(
                    f"炸板率较昨晚报告飙升{delta:+.0%}(昨{prev_eod_sentiment.zaban_rate:.0%}→现{snapshot.zaban_rate:.0%})"
                )

    if snapshot.sample_size > 0:
        limit_down_rate = snapshot.limit_down_count / snapshot.sample_size
        if snapshot.limit_down_count >= LIMIT_DOWN_COUNT_TRIGGER or limit_down_rate >= LIMIT_DOWN_RATE_TRIGGER:
            reasons.append(
                f"关注池跌停{snapshot.limit_down_count}只(占比{limit_down_rate:.0%},样本{snapshot.sample_size}只)"
            )

    if hot_sector_peer_rets:
        avg_ret = sum(hot_sector_peer_rets) / len(hot_sector_peer_rets)
        if avg_ret <= SECTOR_DIVE_RET_TRIGGER:
            reasons.append(f"热门板块可比个股平均跌幅{avg_ret:.1%}(样本{len(hot_sector_peer_rets)}只),疑似主线跳水")

    if not reasons:
        return None
    return RetreatAlert(reasons=reasons)


__all__ = [
    "MarketBreadthSnapshot",
    "RetreatAlert",
    "compute_breadth_snapshot",
    "check_retreat",
    "ZABAN_RATE_ABS_TRIGGER",
    "ZABAN_RATE_SPIKE_DELTA",
    "ZABAN_MIN_SAMPLE",
    "LIMIT_DOWN_COUNT_TRIGGER",
    "LIMIT_DOWN_RATE_TRIGGER",
    "SECTOR_DIVE_RET_TRIGGER",
]
