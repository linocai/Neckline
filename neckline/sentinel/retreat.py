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

**v1.1-H2 双级制重构(2026-07-22 生产过敏后拍板;方向不得改)**:上线首日实盘
暴露过敏——早盘拿"进行时炸板率 38%"对比"昨晚收盘定稿值 8%",基数不同(样本
在演化 + 早盘小样本噪声)导致结构性假信号。四条修法(全部收命名常量,标注
"启发式待实盘校准",落点见各条注释):
    1. **飙升条件改同时段对比**:炸板率飙升不再对比昨晚 EOD 全市场值,改对比
       「昨日同一时刻(±5min 窗)本关注池」的炸板率(同样本、同时段,苹果对
       苹果)。基线由 `retreat_store` 从 `retreat_metrics` 表读;昨日同时段无
       数据(如部署首日)→ 该条件静默失效,其余绝对条件照常。
    2. **持续性要求**:任何条件族须**连续 2 拍成立**才升红(上一拍触发集从
       `retreat_metrics` 读)。进程重启保守:首拍不触发红色(`allow_red=False`)。
    3. **早盘加严**:10:00 前绝对阈值整体上调(小样本 + 竞价噪声,要求更强
       证据),见 `_thresholds`。
    4. **双级制**:**黄色预警**(单条件首次成立)= 只进看板、不推送、不抑制
       买点;**红色刹车**(条件连续 2 拍成立 **或** ≥2 个不同条件族同拍成立)=
       推送 + 抑制买点 + 全天闩锁(现有语义)。判级在 `evaluate_retreat`,
       落库/编排在 `engine.run_tick`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Dict, Iterable, List, NamedTuple, Optional

from neckline.data.limit_derived import compute_intraday_limit_prices
from neckline.sentinel.mainline import MIN_MAINLINE_SAMPLE
from neckline.sentinel.quotes import Quote
from neckline.sentinel.universe import StockMeta, is_new_stock_exempt

# —— 浮点容差(同 `sentinel/holding.py::_EPS`;阈值比较不写裸 >=/<=,防两配置值
#    做减法落在二进制浮点边界上漏判,见项目 CLAUDE.md)——————————————————————
_EPS = 1e-9

# —— 早盘加严分界(修法3):10:00 前用更严阈值——————————————————————————————
# 早盘(集合竞价刚过)关注池样本小、量能前置、噪声大,一律要求更强证据才触发。
EARLY_SESSION_CUTOFF = time(10, 0)

# —— 常规时段阈值(全部**未回测,启发式待实盘校准**;命名常量便于实盘归因后
#    定点调整,不散落魔法数字)——————————————————————————————————————————————
# 炸板率绝对触发线(样本 = 关注池,非全市场,数值沿用"过半即弱"直觉,不是同一份统计)。
ZABAN_RATE_ABS_TRIGGER = 0.50
# 炸板率相对**昨日同一时刻**(修法1,非昨晚 EOD)的飙升幅度(绝对百分点差值)。
ZABAN_RATE_SPIKE_DELTA = 0.20
# 炸板率判定所需最小分母(涨停+炸板样本太小时百分比噪声大,不判断)。
ZABAN_MIN_SAMPLE = 5
# 跌停家数触发(绝对数与占关注池比例,任一达到即触发)。
LIMIT_DOWN_COUNT_TRIGGER = 5
LIMIT_DOWN_RATE_TRIGGER = 0.15
# 主线板块跳水:主线样本个股的平均盘中跌幅阈值。⚠ **样本来源自 V2-⑧-F 起 =
# ④ 机械种子成分 ∩ 关注池机械成分**(派生见 `sentinel/mainline.py`),不再是 V1 的
# 「热门板块标签个股」、也不是 V2-⑬-1 那版的「T1/T2 篮子成员」;**阈值本身一字未动**。
SECTOR_DIVE_RET_TRIGGER = -0.03

# —— 早盘(< 10:00)加严阈值(修法3,**同样未回测,启发式待实盘校准**)——————
# 任务点名的两档:炸板率 50%→65%、跌停家数 5→8;其余绝对量级同步上调一档,
# 形成"早盘要求更强证据"的一致梯度(占比/飙升/跳水均加严),避免只堵两个口子
# 留下别的早盘假信号缝。飙升条件本身已被"同时段对比"结构性修复,早盘再抬 delta
# 是叠加的小样本保守,不是重复。
ZABAN_RATE_ABS_TRIGGER_EARLY = 0.65
ZABAN_RATE_SPIKE_DELTA_EARLY = 0.30
LIMIT_DOWN_COUNT_TRIGGER_EARLY = 8
LIMIT_DOWN_RATE_TRIGGER_EARLY = 0.20
SECTOR_DIVE_RET_TRIGGER_EARLY = -0.04

# 昨日同一时刻基线的匹配窗口(±分钟)。
SAME_TIME_WINDOW_MIN = 5

# —— 条件族键(用于"≥2 个不同条件同拍"与"连续 2 拍持续性"判定的最小粒度)。
#    炸板率的「绝对过高」与「同时段飙升」是同一族(都在说"炸板坏"),算一个条件,
#    不因两个子判据都真就当成两个不同条件去凑"≥2 条同拍红"。——————————————
COND_ZABAN = "zaban"
COND_LIMIT_DOWN = "limit_down"
COND_SECTOR_DIVE = "sector_dive"


class _Thresholds(NamedTuple):
    zaban_abs: float
    zaban_spike_delta: float
    limit_down_count: int
    limit_down_rate: float
    sector_dive: float


def _thresholds(now_time: time) -> _Thresholds:
    """按时段取阈值:10:00 前(早盘)用加严档,之后用常规档(修法3)。"""
    if now_time < EARLY_SESSION_CUTOFF:
        return _Thresholds(
            ZABAN_RATE_ABS_TRIGGER_EARLY, ZABAN_RATE_SPIKE_DELTA_EARLY,
            LIMIT_DOWN_COUNT_TRIGGER_EARLY, LIMIT_DOWN_RATE_TRIGGER_EARLY,
            SECTOR_DIVE_RET_TRIGGER_EARLY,
        )
    return _Thresholds(
        ZABAN_RATE_ABS_TRIGGER, ZABAN_RATE_SPIKE_DELTA,
        LIMIT_DOWN_COUNT_TRIGGER, LIMIT_DOWN_RATE_TRIGGER, SECTOR_DIVE_RET_TRIGGER,
    )


@dataclass
class MarketBreadthSnapshot:
    trade_date: date
    sample_size: int
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    zaban_rate: float


@dataclass
class RetreatMetrics:
    """单拍关注池宽度指标(落 `retreat_metrics` 表,供同时段对比 + 成绩单)。
    `hot_sector_avg_chg=None` 表示本 tick 无热门板块可比样本(诚实"无数据")。

    `hot_sector_sample_detail`(V2-⑧-F 留痕):主线跳水样本的**构成**(codes + 逐码
    来源标签 + 样本量 + 种子计数 + 不可用原因),由 `sentinel/mainline.py` 产出。
    **触发与否都落**——「样本没被 LLM 塑形」这件事要事后可审计,不能靠读代码相信。
    默认空 dict(老调用点 / 测试替身不传时不炸,如实表达"本次没记样本构成")。"""
    trade_date: date
    hhmm: str
    sample_size: int
    limit_up_count: int
    limit_down_count: int
    zaban_count: int
    zaban_rate: float
    hot_sector_avg_chg: Optional[float]
    hot_sector_sample_detail: Dict[str, object] = field(default_factory=dict)

    def metric_payload(self) -> Dict[str, object]:
        """落进 sentinel_events / 看板事件 payload 的全量指标快照(修法纪律:
        每次触发黄/红都带全量指标值,供未来算刹车命中率成绩单)。"""
        return {
            "hhmm": self.hhmm,
            "sample_size": self.sample_size,
            "limit_up_count": self.limit_up_count,
            "limit_down_count": self.limit_down_count,
            "zaban_count": self.zaban_count,
            "zaban_rate": round(self.zaban_rate, 4),
            "hot_sector_avg_chg": (round(self.hot_sector_avg_chg, 4)
                                   if self.hot_sector_avg_chg is not None else None),
            "hot_sector_sample": dict(self.hot_sector_sample_detail),
        }


@dataclass
class RetreatAlert:
    """红色刹车告警(仅红色时构造;`engine`/`_sentinel_loop` 据此推送)。"""
    reasons: List[str] = field(default_factory=list)

    @property
    def triggered(self) -> bool:
        return bool(self.reasons)

    @property
    def reason_text(self) -> str:
        return ";".join(self.reasons)


@dataclass
class RetreatDecision:
    """一拍的退潮判级结果。`tier` ∈ {none, yellow, red};`triggered` 为本拍成立的
    条件族键(有序,供落库做下一拍持续性判据);`red_via` 记红色触发路径(审计)。"""
    tier: str
    triggered: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    red_via: List[str] = field(default_factory=list)

    @property
    def is_red(self) -> bool:
        return self.tier == "red"

    @property
    def is_yellow(self) -> bool:
        return self.tier == "yellow"

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


def evaluate_retreat(
    snapshot: MarketBreadthSnapshot,
    *,
    now_time: time,
    same_time_zaban_baseline: Optional[float],
    hot_sector_avg_chg: Optional[float],
    hot_sector_sample: int,
    prev_tick_triggered: Iterable[str],
    allow_red: bool,
) -> RetreatDecision:
    """盘中情绪恶化判级(纯函数,不碰 DB/网络)。返回 `RetreatDecision`。

    参数:
        now_time — 用于早盘加严(修法3),`< EARLY_SESSION_CUTOFF` 取加严档。
        same_time_zaban_baseline — 昨日同一时刻(±窗)关注池炸板率(修法1);
            `None`=无基线(部署首日/昨日该时段无数据)→ 飙升子判据静默失效。
        hot_sector_avg_chg / hot_sector_sample — 主线板块跳水的读数(V2-⑧-G 起 =
            **per-seed 均值**,见 `sentinel/mainline.py::estimate`)与有报价的样本
            只数;`None` 或样本 < `MIN_MAINLINE_SAMPLE` → 该条件**不判**(诚实
            "无数据",不是判"板块健康")。
        prev_tick_triggered — 上一拍成立的条件族键集合(持续性判据,修法2)。
        allow_red — False 时任何红色降级为黄色(进程重启后首拍保守,修法2)。

    判级(修法4):
        · 无条件成立 → none。
        · 有条件成立,但不满足红色两条路径之一 → yellow(单条件首次成立)。
        · 红色两路径任一:①≥2 个不同条件族同拍成立;②任一条件族"连续 2 拍成立"
          (本拍 ∩ 上一拍)→ red;但 `allow_red=False` 时降级为 yellow。
    """
    th = _thresholds(now_time)
    reasons_by_cond: Dict[str, str] = {}

    # —— 炸板率族(绝对过高 OR 较昨日同时段飙升;两者同族,只出一条理由)——————
    zaban_denom = snapshot.zaban_count + snapshot.limit_up_count
    if zaban_denom >= ZABAN_MIN_SAMPLE:
        if snapshot.zaban_rate >= th.zaban_abs - _EPS:
            reasons_by_cond[COND_ZABAN] = (
                f"关注池炸板率{snapshot.zaban_rate:.0%}(样本{zaban_denom}只)过高"
            )
        elif same_time_zaban_baseline is not None:
            delta = snapshot.zaban_rate - same_time_zaban_baseline
            if delta >= th.zaban_spike_delta - _EPS:
                reasons_by_cond[COND_ZABAN] = (
                    f"炸板率较昨日同时段飙升{delta:+.0%}"
                    f"(昨同时段{same_time_zaban_baseline:.0%}→现{snapshot.zaban_rate:.0%})"
                )

    # —— 跌停家数族(绝对数 OR 占关注池比例,任一达到)——————————————————————
    if snapshot.sample_size > 0:
        limit_down_rate = snapshot.limit_down_count / snapshot.sample_size
        if (snapshot.limit_down_count >= th.limit_down_count
                or limit_down_rate >= th.limit_down_rate - _EPS):
            reasons_by_cond[COND_LIMIT_DOWN] = (
                f"关注池跌停{snapshot.limit_down_count}只"
                f"(占比{limit_down_rate:.0%},样本{snapshot.sample_size}只)"
            )

    # —— 主线板块跳水族——————————————————————————————————————————————————
    # ⚠ **V2-⑧-G-E:准入门槛由 `> 0` 抬到 `>= MIN_MAINLINE_SAMPLE`(=5,同源引用
    # `industry_strength._MIN_MEMBERS`)。阈值比较本身一字未动** —— n=3 时横截面
    # 收益率标准误约 2pp,拿它判 −3% 阈值接近抛硬币,而误触发的代价是**整天禁开
    # 新仓**;配额切片上线后正常日样本 40~100 只,n<5 意味着上游已经出事,**这种
    # 时候更不该开火**。样本不足 = 该路不判(不是判"板块健康")。
    if hot_sector_avg_chg is not None and hot_sector_sample >= MIN_MAINLINE_SAMPLE:
        if hot_sector_avg_chg <= th.sector_dive + _EPS:
            reasons_by_cond[COND_SECTOR_DIVE] = (
                f"热门板块可比个股平均跌幅{hot_sector_avg_chg:.1%}"
                f"(样本{hot_sector_sample}只),疑似主线跳水"
            )

    triggered = sorted(reasons_by_cond.keys())
    if not triggered:
        return RetreatDecision(tier="none")

    reasons = [reasons_by_cond[c] for c in triggered]
    prev = set(prev_tick_triggered)

    red_via: List[str] = []
    if len(triggered) >= 2:
        red_via.append("multi_condition")
    for c in triggered:
        if c in prev:  # 该族"连续 2 拍成立"(上一拍也在触发集)
            red_via.append(f"persist:{c}")

    if red_via and allow_red:
        return RetreatDecision(tier="red", triggered=triggered, reasons=reasons, red_via=red_via)
    # 否则黄色:单条件首次成立;或红色被"重启首拍保守"降级(red_via 仍留痕供审计)。
    return RetreatDecision(tier="yellow", triggered=triggered, reasons=reasons, red_via=red_via)


__all__ = [
    "MarketBreadthSnapshot",
    "RetreatMetrics",
    "RetreatAlert",
    "RetreatDecision",
    "compute_breadth_snapshot",
    "evaluate_retreat",
    "EARLY_SESSION_CUTOFF",
    "SAME_TIME_WINDOW_MIN",
    "ZABAN_RATE_ABS_TRIGGER",
    "ZABAN_RATE_SPIKE_DELTA",
    "ZABAN_MIN_SAMPLE",
    "LIMIT_DOWN_COUNT_TRIGGER",
    "LIMIT_DOWN_RATE_TRIGGER",
    "SECTOR_DIVE_RET_TRIGGER",
    "ZABAN_RATE_ABS_TRIGGER_EARLY",
    "ZABAN_RATE_SPIKE_DELTA_EARLY",
    "LIMIT_DOWN_COUNT_TRIGGER_EARLY",
    "LIMIT_DOWN_RATE_TRIGGER_EARLY",
    "SECTOR_DIVE_RET_TRIGGER_EARLY",
    "COND_ZABAN",
    "COND_LIMIT_DOWN",
    "COND_SECTOR_DIVE",
]
