"""候选参考件三件套(plan §五 v1.5-①,需求 9 / `archive/交接_系统线升级需求_
20260725.md` 需求 9)。**唯一实现**——候选卡的"四件套"(买点/止损/目标/证伪条件,
K1 时代模板文案)在候选语义已改成"过完安检、值得花注意力的票"之后名不副实;本模块
把候选卡输出层换成**三件 LLM 参考件**:① 买入参考区间 ② 离场参考区间 ③ 明早证伪
剧本,外加一条硬夹逼(唯一底线,防幻觉)与一张成绩单表(`reference_plan_store.py`)。

**第〇原则是本模块的宪法(PROJECT_PLAN §2.0,凌驾本模块一切细节)**:
    1. **参考件不触发任何机器动作**——不进哨兵判据、不进推送、不进排序键、不改候选
       去留、不写决策日志、不改任何纪律阈值。它们只出现在"给人看"的位置(§2.0 第
       一条落地成本模块 ①-G 的三条守门单测:sentinel 目录 grep 零命中 / 排序键白
       名单不含参考件字段 / 推送白名单仍六类)。
    2. **纪律只住章程**——止损价 = `close × (1 − stop_pct)` 系统算,`stop_pct` 读
       现役 `strategy_versions` config,**LLM 不产出止损数字**。
    3. **机器不禁、人可复核**——LLM 判风险大(否决)只收回参考件,候选与信息卡
       照留(见 `status=vetoed`)。
    4. **参考件必须标注"参考、非指令"**——见 `REFERENCE_DISCLAIMER` 单一源。
    5. **参考件必须落库**——`reference_plan_store.py`,将来与实际走势/成交对拍
       (P3-11 挂账,本版只落数据不出报表)。

**一次 LLM 调用出「自由评语 + 既有结论标签 + 三件套 json」,定死不许拆成两次调用**
(①-B)。**复用 `llm/judge.py::judge_candidate`**(duck-typed,新增可选
`context_block` 参数喂本模块的富上下文,不重写调用/解析/降级链——项目 CLAUDE.md
铁律「喂类候选对象给 LLM 审判一律复用 judge_candidate」)。本模块只负责:①组装喂给
LLM 的上下文文本(信息卡产出 + 哨兵阈值块,`build_reference_context_block`);②从
`JudgeResult.narrative` 尾部剥出三件套 json 围栏块并还原一份干净叙述
(`split_narrative_and_reference_json`,§2.7"卡面展示的是数字+why的自然语言,不是
把 JSON 摊给用户"的落地);③夹逼 + 状态判定 + 组装落库记录(`build_reference_plan`)。
`judge_and_build_reference_plan` 是给 `pipeline.py` 用的一站式编排入口(内部含自身的
降级链,详见该函数 docstring)。

**输入集 = 信息卡产出,考卷同构,零新增配额**(①-A):喂给 LLM 的富上下文只取
`info_card.py::build_info_card` 已产出的六路——60 日 K 线 / 快照七项 / 红黄牌 /
温和带 / 消息面 / 龙虎榜摘要 / 市场语境(RS 线与行业分歧线不在①-A 列出的清单内,
本模块不喂,§五 v1.5-①-A 原文逐项对照)。

**夹逼唯一底线(①-C,防幻觉,一行判定)**:只夹逼买入区间(必须落在
`[跌停价,涨停价]` 闭区间内,且 `0<low<=high`),离场区间只做格式合法性校验、不夹
涨跌停(压力位可能几天后才到)。`board`/`is_st` 唯一源 = `sentinel.universe.
load_stock_meta`,不自判 ST 前缀/不自分板块;`limit_derived.compute_intraday_
limit_prices` 唯一算涨跌停入口。
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neckline.calendar import next_trading_day
from neckline.data.limit_derived import compute_intraday_limit_prices
from neckline.llm.base import LLMProvider
from neckline.llm.judge import JudgeResult, VERDICT_VETO, judge_candidate
from neckline.llm.prompt_context import TIMELINESS_RULES, date_anchor_line
from neckline.report.candidates import Candidate, invalidation_text
from neckline.report.industry_strength import IndustryStrength
from neckline.report.info_card import build_info_card
from neckline.sentinel.precall import (
    PRECALL_AUCTION_VOL_HIGH_FRAC,
    PRECALL_AUCTION_VOL_LOW_FRAC,
    PRECALL_GAP_UP_INVALIDATE,
)
from neckline.sentinel.universe import load_stock_meta
from neckline.strategy import brain

logger = logging.getLogger(__name__)

# —— 三态(①-D,不许合并;"没看"绝不能显示成"没有")——————————————————————————
STATUS_OK = "ok"
STATUS_VETOED = "vetoed"
STATUS_UNAVAILABLE = "unavailable"

# —— 买入夹逼判定(①-C,"没给"absent 与"给了被拦"rejected_* 分开)——————————————
BUY_CLAMP_OK = "ok"
BUY_CLAMP_ABSENT = "absent"
BUY_CLAMP_REJECTED_OUT_OF_LIMIT = "rejected_out_of_limit"
BUY_CLAMP_REJECTED_MALFORMED = "rejected_malformed"
BUY_CLAMP_REJECTED_NO_LIMIT = "rejected_no_limit"

# —— 离场格式校验(不夹涨跌停,只校验 0<low<=high)——————————————————————————————
EXIT_CLAMP_OK = "ok"
EXIT_CLAMP_ABSENT = "absent"
EXIT_CLAMP_REJECTED_MALFORMED = "rejected_malformed"

# 人读理由文案(①-F `buyUnavailableReason`/`exitUnavailableReason` 单一源,与 clamp
# 码一一对应,不由调用方各自拍文案)。
_BUY_CLAMP_REASON_TEXT: Dict[str, str] = {
    BUY_CLAMP_ABSENT: "本次未生成买入参考区间",
    BUY_CLAMP_REJECTED_OUT_OF_LIMIT: "生成的买入参考区间超出明日涨跌停范围,已拦截",
    BUY_CLAMP_REJECTED_MALFORMED: "生成的买入参考区间格式不合法,已拦截",
    BUY_CLAMP_REJECTED_NO_LIMIT: "无法算出明日涨跌停价,买入参考区间不显示",
}
_EXIT_CLAMP_REASON_TEXT: Dict[str, str] = {
    EXIT_CLAMP_ABSENT: "本次未生成离场参考区间",
    EXIT_CLAMP_REJECTED_MALFORMED: "生成的离场参考区间格式不合法,已拦截",
}

# 固定文案单一源(①-F「客户端原样透传不改写」)。
REFERENCE_DISCLAIMER = "参考,非指令 —— 买卖与终选在你,系统不代下单;纪律以章程为准。"

# —— system prompt(承 JUDGE_SYSTEM_PROMPT 的信息边界三条 + 输出风格硬约束,新增
#    三件套 json 收尾格式;结尾"结论:通过|否决"标签格式与既有 `_parse_verdict`
#    逐字一致,不新起解析)——————————————————————————————————————————————————
REFERENCE_PLAN_SYSTEM_PROMPT = """你是「颈线」系统的盘后候选参谋。系统本身只做审计、不代客下单,你的判断只影响
"这只候选是否留在今晚的候选池里"以及"给用户什么参考信息",不构成买入建议,读者是一位短线交易者。

你会拿到一只候选股票的完整资料:近60个交易日K线(前复权)、当日量价快照、系统给出的红黄牌
风险标注、消息面摘要、龙虎榜信息、大盘语境,以及**盘中会自动触发警报的机械阈值**(证伪条件、
盘前校准阈值、现役止损比例、明日涨跌停参考价)。你还配有联网搜索工具,可以查该股票近期的
新闻、公告、题材催化。

信息边界(铁律,不可违反,与候选审判一致):
1. 你只能依据下面提供的结构化数据、以及联网搜索工具实际返回的内容做判断。
2. 如果搜索没有找到相关消息,或搜到的内容与该股票无关,必须在分析里明确说"未搜到相关消息",
绝不允许凭猜测编造新闻、公告、传闻或题材。
3. 系统的选股规则本身是一套减损纪律系统而非高胜率信号,你的角色是排查"催化是否站得住、是否
有明显利空正在发生",不是给出收益预测,不要暗示"这只票会涨"。

""" + TIMELINESS_RULES + """

输出风格(硬约束):自由叙述,写成一段连贯的分析文字,像分析师口头点评。禁止使用分点列表、
多维打分表、"技术面/资金面/消息面"这类固定分栏模板——可以自然地把这些角度揉进叙述里,但不要
用标题或项目符号分隔。

**你产出的一切都只是参考,不是指令**——买卖时机、价格与是否下单的终选权始终在用户,系统永远
不会代替用户下单。**不得使用"止盈线""目标价""建议买入""推荐买点"这类措辞**——买入参考区间
不是买入信号;离场参考区间是本轮上涨的压力位参考,不是止盈线(回落止盈是系统纪律,独立生效、
不受你的判断影响,不要暗示离场参考区间取代了它)。

结尾格式(两部分,均为机器可读,顺序不可颠倒,中间各空一行):

第一部分:写完叙述后,另起一行,只写"结论:通过"或"结论:否决"这两者之一(不要多余的标点或
解释,正文里不要提前出现"结论:"这个词组以免解析冲突)。"否决"意味着你认为催化站不住、有
明显利空、或消息面有硬伤,风险大到不适合给出参考区间(候选本身仍会保留在候选池供用户复核,
只是不再附带买入/离场参考区间与证伪剧本);"通过"意味着没有发现应当否决的理由。

第二部分:再空一行,给出一个```json 围栏代码块,内容严格是下面这个 JSON 对象的形状(不要
多余字段,不要在围栏外重复这段内容):

```json
{"buy": {"low": 数字, "high": 数字, "why": "一两句话说明这个买入参考区间怎么来的"},
 "exit": {"low": 数字, "high": 数字, "why": "一两句话说明这个离场参考区间(本轮上涨压力位)怎么来的"},
 "script": "面向今晚读者、明早开盘时用的行动指引,带分支写法,例如:若集合竞价大幅低开或跌破
证伪线则放弃;若温和低开且量能正常则观望;若符合预期则按买入参考区间执行——请结合资料里给出的
机械阈值(证伪条件/盘前校准阈值)写,不要给出与这些阈值矛盾的指引",
 "veto_reason": null}
```

若结论是"否决",把 buy/exit/script 三个键的值都写成 null,只在 veto_reason 里给一句不买理由;
若结论是"通过",veto_reason 写 null。**买入参考区间的两个数字必须落在资料里给出的"明日涨跌停
参考价"区间内**——超出这个区间的数字会被系统丢弃、不会展示给用户,请务必落在区间内。离场参考
区间不受涨跌停约束(压力位可能在未来几个交易日才触及)。若某一件确实无法给出合理数字,宁可对
应字段留 null,也不要编造。

**JSON 里的 script / 两个 why / veto_reason 这几处自由文本中,禁止出现"结论:通过"或"结论:
否决"这个词组**——那是上面第一部分专用的机器可读标签,写进 JSON 会与之冲突。需要表达同类
意思时改写成"放弃入场""不参与""继续观望"等说法。
"""


# ======================================================================
#  上下文装配(①-A:输入集 = 信息卡产出,零新增配额)
# ======================================================================

def _fmt_pct(x: Optional[float], digits: int = 1) -> str:
    return f"{x:+.{digits}%}" if x is not None else "未知"


def _fmt_snapshot(s: Any) -> str:
    parts: List[str] = []
    parts.append(f"量比(5日)={s.vol_ratio5:.2f}" if s.vol_ratio5 is not None else "量比未知")
    parts.append(f"换手率={s.turnover_rate:.1f}%" if s.turnover_rate is not None else "换手率未知")
    parts.append(
        f"行业当日排名={s.industry_rank}" if s.industry_rank is not None
        else "行业排名未参与(无行业分类或行业当日成员不足)"
    )
    parts.append(
        f"行业强度持续天数={s.industry_persist_days}" if s.industry_persist_days is not None
        else "行业强度持续天数未知(数据未就绪)"
    )
    if s.above_ma250 is None:
        parts.append("年线数据未就绪")
    else:
        parts.append(("高于年线" if s.above_ma250 else "低于年线") + _fmt_pct(s.dist_from_ma250_pct))
    parts.append(f"距20日高点={_fmt_pct(s.dist_from_high20d_pct)}")
    parts.append(f"连板数={s.consec_limit_up_days}")
    return "、".join(parts)


def _fmt_k4_flags(flags: List[Any]) -> str:
    if not flags:
        return "无红黄牌命中"
    lines = []
    for f in flags:
        badge = "红牌" if f.section == "hard_cut" else "黄牌"
        lines.append(f"- [{badge}] {f.label}:{f.evidence}")
    return "\n".join(lines)


def _fmt_news(news: Any) -> str:
    if not news.scanned:
        return f"消息面:{news.unavailable_reason or '不在本次消息面扫描域'}"
    if not news.items:
        return "消息面:已扫描,未发现命中条目(不代表该股无消息,只代表本次扫描未命中)"
    lines = [f"- [{it.category}] {it.summary}(来源:{it.source})" for it in news.items]
    return "消息面(已扫描,命中):\n" + "\n".join(lines)


def _fmt_top_list(tl: Any) -> str:
    base = f"近{tl.lookback_days_covered}个交易日本地数据覆盖,其中{tl.lookback_hit_days}天命中龙虎榜"
    if not tl.on_list_today:
        return f"龙虎榜:今日未上榜。{base}。"
    net_amount = f"{tl.net_amount:.1f}万元" if tl.net_amount is not None else "未知"
    return f"龙虎榜:今日上榜(净买入{net_amount},上榜原因:{tl.reason or '未知'})。{base}。"


def _fmt_market(market: Any) -> str:
    ma20 = "未知" if market.above_ma20 is None else ("高于" if market.above_ma20 else "低于")
    return f"大盘语境:{market.index_code} 今日涨停{market.limit_up_count}家/跌停{market.limit_down_count}家;大盘{ma20}MA20。"


def _fmt_kline(bars: List[Any]) -> str:
    if not bars:
        return "K线不可用。"
    lines = [f"近{len(bars)}个交易日K线(前复权,格式:日期 收盘价 较前一日涨跌幅 成交量):"]
    prev_close: Optional[float] = None
    for bar in bars:
        chg = f"{(bar.close / prev_close - 1) * 100:+.1f}%" if prev_close else "  - "
        lines.append(f"{bar.trade_date} {bar.close:.2f} {chg} {bar.vol:.0f}")
        prev_close = bar.close
    return "\n".join(lines)


def _threshold_block(candidate: Candidate, stop_pct: Optional[float], limit_up: Optional[float], limit_down: Optional[float]) -> str:
    """哨兵阈值块(需求 9 第 3 点「喂给 LLM,使剧本与盘中自动警报同频」)——全部读单一
    源,不抄字面量:`candidate.invalidation_spec`(①-C 已算好,不重复调
    `invalidation_spec()`)/ `sentinel.precall` 三个盘前常量 / 现役 `stop_pct` /
    明日涨跌停价。"""
    lines = ["——盘中会自动触发警报的机械阈值(你的剧本分支须与其同频,不要给出矛盾的行动指引)——"]
    lines.append("证伪条件(命中任一条,盘中会被判定为剔除勿进):" + invalidation_text(candidate.invalidation_spec))
    lines.append(
        f"盘前校准:开盘价高于候选买点参考位超过 {PRECALL_GAP_UP_INVALIDATE:.0%} → 判定「买点已变形今日失效」;"
        f"集合竞价量占前5日日均量比值 ≥{PRECALL_AUCTION_VOL_HIGH_FRAC:.0%} 标「竞价放量踊跃」、"
        f"≤{PRECALL_AUCTION_VOL_LOW_FRAC:.1%} 标「竞价地量无人问津」。"
    )
    stop_txt = f"{stop_pct:.1%}" if stop_pct is not None else "未知(现役章程未配置)"
    lines.append(f"现役止损比例:−{stop_txt}(以实际成交价为准,系统自动挂条件单)。")
    if limit_up is not None and limit_down is not None:
        lines.append(f"明日涨跌停参考价:涨停 {limit_up:.2f} / 跌停 {limit_down:.2f}(买入参考区间必须落在此区间内)。")
    else:
        lines.append("明日涨跌停参考价:算不出(缺股票元数据或收盘价异常)——本次买入参考区间将不会展示给用户。")
    return "\n".join(lines)


def build_reference_context_block(
    candidate: Candidate,
    trade_date: date,
    *,
    top_list_row: Optional[Dict[str, Any]] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    industry_map: Optional[Dict[str, str]] = None,
    top_list_t0: Optional[Dict[str, dict]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> str:
    """组装喂给 LLM 的结构化上下文(纯文本块,不是 JSON,与 `judge.build_context_block`
    同一理由:降低模型把它误当输出模板抄一份回来的概率)。**输入集 = `build_info_card`
    产出**(①-A 同构落地,零新增取数路径/TuShare 配额)——只取六路:60日K线/快照七项/
    红黄牌/温和带/消息面/龙虎榜摘要/市场语境(RS线与行业分歧线不在①-A 清单内,不喂)。

    `industry_scores`/`industry_map`/`top_list_t0` 为可选的"调用方已算好,别再重算
    一遍"注入点(`pipeline.py` 已有同一份,同 `build_info_card` 既有姿势);`db_path`/
    `parquet_dir` 决定读哪份数据(测试隔离用)。`top_list_row` 未被本函数使用
    (信息卡摘要走独立的近5日回看窗口,不需要单独一行 row)——保留参数位仅为与
    `judge_candidate(top_list_row=...)` 调用点签名对齐,便于 `pipeline.py` 统一传参。
    """
    card = build_info_card(
        trade_date, candidate.ts_code, k4_flags=candidate.k4_flags, name=candidate.name,
        industry_scores=industry_scores, industry_map=industry_map, top_list_t0=top_list_t0,
        parquet_dir=parquet_dir, db_path=db_path,
    )
    stop_pct = _resolve_stop_pct(db_path)
    limit_up, limit_down, _ = _resolve_next_day_limit_prices(candidate, trade_date, db_path)

    lines = [
        # v1.5.2:日期锚放第一行,且**点名「明早」= 下一交易日**(`name_tomorrow=True`)——
        # ①-C 的证伪剧本写的就是"明早开盘怎么做",不点名的话周五生成的报告里模型可能把
        # "明早"理解成自然日的周六。`ref_date=trade_date` 使补跑历史日时锚不撒谎(如实说
        # 今天几号 + 基准日几号),下一交易日与涨跌停锚 `_resolve_next_day_limit_prices`
        # 同源(都是 `next_trading_day(trade_date)`)。
        date_anchor_line(trade_date, name_tomorrow=True),
        f"股票:{card.name}({card.code});交易所板块:{candidate.board}",
        f"现价(T日收盘):{candidate.close:.2f} 元",
        "",
        _fmt_kline(card.kline),
        "",
        f"当日快照:{_fmt_snapshot(card.snapshot)}",
        f"温和带(当日涨幅落在2%~3%区间):{'是' if card.mild_band else '否'}",
        "",
        "红黄牌风险标注:",
        _fmt_k4_flags(card.k4_flags),
        "",
        _fmt_news(card.news),
        _fmt_top_list(card.top_list),
        _fmt_market(card.market),
        "",
        _threshold_block(candidate, stop_pct, limit_up, limit_down),
        "",
        "请结合以上信息与联网搜索,输出你的分析、结论标签,以及三件套参考(买入参考区间/"
        "离场参考区间/明早证伪剧本)。",
    ]
    return "\n".join(lines)


# ======================================================================
#  narrative 尾部三件套 json 解析(①-B)
# ======================================================================

_JSON_FENCE_RE = re.compile(r"```json\s*\n?(.*?)```", re.DOTALL | re.IGNORECASE)
# 残留围栏清理(v1.5.1 判定线 review 🟢-4 的两个化妆缺口):① 围栏**未闭合**(输出被
# 截断)时 `_JSON_FENCE_RE` 匹配不到、裸 JSON 也解析不动,老实现把「```json {"buy": …」
# 半截原样摊给用户;② 多围栏时只删了最后一个,前面的残留在叙述里。两者都不影响解析
# 结果(仍取最后一个**闭合**围栏),只违 §2.7「不把 JSON 摊给用户」的观感。
_JSON_FENCE_UNCLOSED_RE = re.compile(r"```json\b.*\Z", re.DOTALL | re.IGNORECASE)
_JSON_FENCE_MARK_RE = re.compile(r"```json", re.IGNORECASE)


def _strip_residual_json_fences(text: str) -> str:
    """把叙述里**所有** ```json 围栏(闭合的全删 + 末尾未闭合的那一截删到结尾)剥净。
    一个围栏标记都没有时**原样返回**(不做 strip)——degraded 占位文案/无围栏输出必须
    逐字节透传,这条由 `test_no_json_anywhere_returns_none_and_original_text_untouched`
    锁死。"""
    if not _JSON_FENCE_MARK_RE.search(text):
        return text
    return _JSON_FENCE_UNCLOSED_RE.sub("", _JSON_FENCE_RE.sub("", text)).strip()


def _extract_last_json_fence(text: str) -> Optional[Tuple[str, int, int]]:
    matches = list(_JSON_FENCE_RE.finditer(text))
    if not matches:
        return None
    m = matches[-1]
    return m.group(1), m.start(), m.end()


def _extract_bare_trailing_json(text: str) -> Optional[Tuple[Dict[str, Any], int]]:
    """无围栏时容忍"末尾裸 JSON 对象"(①-B)。用 `json.JSONDecoder.raw_decode` 逐个
    候选起点(文本内每一个 `{`)去试解析,取**第一个**能让解析恰好吃到(去除尾部空白
    后的)字符串末尾的起点——这自然就是"跨越到文本结尾的那个最外层对象",不必手写
    括号计数器去猜嵌套边界。"""
    stripped = text.rstrip()
    decoder = json.JSONDecoder()
    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            obj, end = decoder.raw_decode(stripped, idx)
        except json.JSONDecodeError:
            continue
        if end == len(stripped) and isinstance(obj, dict):
            return obj, idx
    return None


def split_narrative_and_reference_json(narrative: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """把模型输出(格式定死:自由叙述 → "结论:"标签 → 空行 → ```json 三件套围栏块)
    拆成 `(干净叙述, 解析出的 dict 或 None)`。取**最后一个**围栏块;无围栏时容忍
    "末尾裸 JSON 对象"。解析失败(围栏在、内容非法 JSON;或两种形式都没找到)→
    `(去除围栏后的叙述或原文, None)`——**绝不能让三件套解析失败拖累叙述本身**
    (项目 CLAUDE.md「解析失败→参考件为 null+理由字段,绝不让解析失败拖垮审判结论」)。
    degraded 占位文案(LLM未激活/调用失败)天然无围栏也无裸 JSON,原样返回、零影响。

    **v1.5.1 起本函数在 `_parse_verdict` 之前跑**(作为
    `judge_candidate(narrative_splitter=...)` 注入进去,判定线 review 🟡-1):入参因此是
    **含"结论:"标签的原始输出**,返回的"干净叙述"里标签仍在、随后由 `_parse_verdict`
    去掉。顺序不可再颠倒——先解析 verdict 会让 JSON 里的自由中文("若跌破证伪线则
    结论:否决"这类)劫持 last-match 锚点、静默翻转结论。本函数只认围栏/裸 JSON 边界,
    多一个标签不影响任何分支。"""
    fence = _extract_last_json_fence(narrative)
    if fence is not None:
        raw, _start, _end = fence
        # 🟢-4:清理时把**所有**围栏剥净(不只解析用的那一个),含未闭合的半截。
        cleaned = _strip_residual_json_fences(narrative)
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return cleaned, None
        return cleaned, (parsed if isinstance(parsed, dict) else None)

    bare = _extract_bare_trailing_json(narrative)
    if bare is not None:
        obj, idx = bare
        return _strip_residual_json_fences(narrative[:idx].rstrip()), obj

    return _strip_residual_json_fences(narrative), None


# ======================================================================
#  夹逼(①-C)+ 状态判定(①-D)+ 落库记录(①-E)
# ======================================================================

def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def _as_why(obj: Dict[str, Any]) -> Optional[str]:
    why = obj.get("why")
    return why.strip() if isinstance(why, str) and why.strip() else None


def _clamp_buy(
    raw_buy: Any, limit_up: Optional[float], limit_down: Optional[float]
) -> Tuple[Optional[float], Optional[float], str, Optional[str]]:
    """买入区间夹逼(①-C 唯一底线)。返回 `(low, high, clamp_status, why)`。判定
    优先级(刻意的顺序,不是随意):① LLM 压根没给(`buy` 缺失/null/两数都缺)→
    `absent`,**不管涨跌停算不算得出**——"没给"与"给了被拦"是两件事,不因为算不出
    涨跌停就把"没给"错记成"算不出";② 给了但数字本身不合法(非数/NaN/缺一个/
    low>high)→ `rejected_malformed`;③ 数字合法但算不出涨跌停 → `rejected_no_limit`；
    ④ 数字合法、涨跌停也算得出,但越界 → `rejected_out_of_limit`;⑤ 全部通过 → `ok`。
    """
    if not isinstance(raw_buy, dict):
        return None, None, BUY_CLAMP_ABSENT, None
    low, high = raw_buy.get("low"), raw_buy.get("high")
    if low is None and high is None:
        return None, None, BUY_CLAMP_ABSENT, None
    if not (_is_finite_number(low) and _is_finite_number(high)) or not (0 < float(low) <= float(high)):
        return None, None, BUY_CLAMP_REJECTED_MALFORMED, None
    low, high = float(low), float(high)
    if limit_up is None or limit_down is None:
        return None, None, BUY_CLAMP_REJECTED_NO_LIMIT, None
    if not (limit_down <= low <= limit_up and limit_down <= high <= limit_up):
        return None, None, BUY_CLAMP_REJECTED_OUT_OF_LIMIT, None
    return round(low, 2), round(high, 2), BUY_CLAMP_OK, _as_why(raw_buy)


def _clamp_exit(raw_exit: Any) -> Tuple[Optional[float], Optional[float], str, Optional[str]]:
    """离场区间格式校验(①-C:**不夹涨跌停**,压力位可能几天后才到,只校验
    `0<low<=high`)。返回 `(low, high, clamp_status, why)`,判定优先级同 `_clamp_buy`
    去掉涨跌停两档:absent → malformed → ok。"""
    if not isinstance(raw_exit, dict):
        return None, None, EXIT_CLAMP_ABSENT, None
    low, high = raw_exit.get("low"), raw_exit.get("high")
    if low is None and high is None:
        return None, None, EXIT_CLAMP_ABSENT, None
    if not (_is_finite_number(low) and _is_finite_number(high)) or not (0 < float(low) <= float(high)):
        return None, None, EXIT_CLAMP_REJECTED_MALFORMED, None
    return round(float(low), 2), round(float(high), 2), EXIT_CLAMP_OK, _as_why(raw_exit)


def _as_ratio(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _resolve_charter_pcts(db_path: Optional[Path]) -> Tuple[Optional[float], Optional[float]]:
    """现役章程的两个展示口径指纹 `(stop_pct, take_profit_retrace)`(§2.1「唯一源 =
    现役 `strategy_versions` config」,不硬编 0.05 / 0.08)。一次 `active_config` 读两个,
    别为了第二个数再开一次连接。任一未配置 → 该位 `None`,展示层退化成不带数字的
    「章程止损」/「章程回落止盈」文案(v1.5.1 两线 review 共同项),**不拿字面量补位**。"""
    cfg = brain.active_config(db_path=db_path)
    return _as_ratio(cfg.get("stop_pct")), _as_ratio(cfg.get("take_profit_retrace"))


def _resolve_stop_pct(db_path: Optional[Path]) -> Optional[float]:
    """现役止损比例(§2.1「−5.0 是全系统单一常量」,唯一源 = 现役
    `strategy_versions` config,不硬编 0.05)。"""
    return _resolve_charter_pcts(db_path)[0]


def _resolve_next_day_limit_prices(
    candidate: Candidate, trade_date: date, db_path: Optional[Path]
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """算"明日"涨跌停参考价(①-C 唯一底线的锚)。`candidate.close` 在报告日 T 上
    就是 `qfq(T)==原始收盘`(`data/adjust.py::apply_qfq` 的前复权基准=传入区间内
    最新一条 adj_factor,面板区间恰好截到 T),可直接当明日 `pre_close` 用,不必反查
    原始表(同 `info_card.py` 已核实的结论)。`board`/`is_st` 唯一源 =
    `sentinel.universe.load_stock_meta`(退潮哨兵同款,不自判 ST 前缀/不自分板块)。
    `trade_date` 传的是**明日**——ST 幅度有制度分界日,传错日期会取错幅度。

    返回 `(limit_up, limit_down, 算不出时的理由)`——查无 meta / `close<=0` /
    `next_trading_day` 异常均归为"算不出",理由文本各自不同供审计。
    """
    close = candidate.close
    if close is None or close <= 0:
        return None, None, "候选收盘价缺失或非正,无法算涨跌停"
    meta_map = load_stock_meta([candidate.ts_code], db_path=db_path)
    meta = meta_map.get(candidate.ts_code)
    if meta is None:
        return None, None, "查无股票元数据(stock_basic 缺该代码),无法判定板块/是否ST"
    try:
        next_day = next_trading_day(trade_date)
    except RuntimeError:
        return None, None, "无法确定下一交易日(日历数据异常)"
    up, down = compute_intraday_limit_prices(close, meta.board, meta.is_st, next_day)
    if up is None or down is None:
        return None, None, "涨跌停价计算返回空"
    return up, down, None


@dataclass
class ReferencePlan:
    """一只候选一天的参考件落库记录(①-E,与 `reference_plans` 表逐列对应)。"""

    ts_code: str
    status: str                       # ok | vetoed | unavailable
    verdict: str                      # 既有审判标签:通过|否决|未激活
    close: float
    limit_up: Optional[float]
    limit_down: Optional[float]
    buy_low: Optional[float]
    buy_high: Optional[float]
    buy_clamp: str
    buy_why: Optional[str]
    stop_price: Optional[float]
    stop_pct: Optional[float]
    exit_low: Optional[float]
    exit_high: Optional[float]
    exit_clamp: str
    exit_why: Optional[str]
    script_text: Optional[str]
    veto_reason: Optional[str]
    provider: str
    model: str
    degraded: bool                    # 本参考件自身是否失败(见模块头③,与 judge_result.degraded 不同一件事)
    degrade_reason: str = ""
    # v1.5.1(两线 review 共同项):与 `stop_pct` 成对的第二个章程口径指纹,供展示层
    # 动态生成「回落止盈 X%」标签。字段位置在末尾只因 dataclass 默认值规则,语义上与
    # `stop_pct` 同类;落库列见 `reference_plan_store._COLUMNS`(挨着 stop_pct)。
    take_profit_retrace: Optional[float] = None

    def to_public_dict(self) -> Dict[str, Any]:
        """①-F 客户端契约(camelCase)。`buy`/`exit` 整体对象只在各自 clamp=ok 时非
        null(`stopPrice` 嵌在 `buy` 内,买入区间被拦时一并不显示,同参考区间被拦不
        单独展示止损数字的设计)。

        **v1.5.1 增量两键**(两线 review 共同项:标签硬编 vs 数字跟章程走):`buy.stopPct`
        与 `exit.takeProfitRetrace` —— 产出本行时的现役章程比例(小数,如 0.05/0.08),
        供 markdown 与客户端**动态生成**「章程 −5%」「回落止盈 8%」这两句标签,章程一改
        标签跟着走。`None`(老快照/章程未配置)时展示层退化成不带数字的说法,不硬编。"""
        buy = None
        buy_unavailable_reason = None
        if self.buy_clamp == BUY_CLAMP_OK:
            buy = {
                "low": self.buy_low, "high": self.buy_high, "stopPrice": self.stop_price,
                "stopPct": self.stop_pct, "why": self.buy_why or "",
            }
        else:
            buy_unavailable_reason = _BUY_CLAMP_REASON_TEXT.get(self.buy_clamp, self.buy_clamp)

        exit_ = None
        exit_unavailable_reason = None
        if self.exit_clamp == EXIT_CLAMP_OK:
            exit_ = {
                "low": self.exit_low, "high": self.exit_high,
                "takeProfitRetrace": self.take_profit_retrace, "why": self.exit_why or "",
            }
        else:
            exit_unavailable_reason = _EXIT_CLAMP_REASON_TEXT.get(self.exit_clamp, self.exit_clamp)

        return {
            "status": self.status,
            "buy": buy,
            "buyUnavailableReason": buy_unavailable_reason,
            "exit": exit_,
            "exitUnavailableReason": exit_unavailable_reason,
            "script": self.script_text,
            "vetoReason": self.veto_reason,
            "unavailableReason": (self.degrade_reason or None) if self.status == STATUS_UNAVAILABLE else None,
            "disclaimer": REFERENCE_DISCLAIMER,
            "degraded": self.degraded,
        }


def _base_fields(
    candidate: Candidate, judge_result: JudgeResult, stop_price: Optional[float], stop_pct: Optional[float],
    limit_up: Optional[float], limit_down: Optional[float], take_profit_retrace: Optional[float],
) -> Dict[str, Any]:
    """五个状态分支共用的字段(收盘价/涨跌停/止损/章程口径指纹/审判身份),减少重复。"""
    return dict(
        ts_code=candidate.ts_code, verdict=judge_result.verdict, close=candidate.close,
        limit_up=limit_up, limit_down=limit_down, stop_price=stop_price, stop_pct=stop_pct,
        take_profit_retrace=take_profit_retrace,
        provider=judge_result.provider, model=judge_result.model,
    )


def build_reference_plan(
    candidate: Candidate,
    trade_date: date,
    *,
    judge_result: JudgeResult,
    parsed_json: Optional[Dict[str, Any]],
    db_path: Optional[Path] = None,
) -> ReferencePlan:
    """由**已经拿到的** `JudgeResult`(narrative 已剥出三件套 json,见
    `split_narrative_and_reference_json`)+ 解析出的 `parsed_json` 组装一条
    `ReferencePlan`(①-D 状态判定 + ①-C 夹逼)。**不发起任何 LLM 调用**——一次调用
    的产出由调用方(`judge_and_build_reference_plan`)负责取得,本函数是纯装配。

    状态判定(①-D,`judge_result.degraded` 与 `verdict==VERDICT_INACTIVE` 恒同时
    成立,查 `degraded` 即够):
        · `degraded=True` → `unavailable`(LLM未激活/调用失败)。
        · `verdict==否决` → `vetoed`(不管 `parsed_json` 给了什么,buy/exit/script
          一律丢弃只留 `veto_reason`——"机器不禁、人可复核",票与信息卡另行处理,
          本函数不touch候选去留)。
        · `verdict==通过` 且 `parsed_json is None`(围栏缺失/解析失败)→
          `unavailable`("没看"不是"没有")。
        · `verdict==通过` 且 `parsed_json` 是 dict → `ok`(即便三件套某些子项被
          夹逼拦下,整体仍 `ok`——见 `_clamp_buy`/`_clamp_exit` 各自的 absent/
          rejected_* 细分)。
    """
    stop_pct, take_profit_retrace = _resolve_charter_pcts(db_path)
    close = candidate.close
    stop_price = round(close * (1 - stop_pct), 2) if (stop_pct is not None and close and close > 0) else None
    limit_up, limit_down, _ = _resolve_next_day_limit_prices(candidate, trade_date, db_path)
    base = _base_fields(
        candidate, judge_result, stop_price, stop_pct, limit_up, limit_down, take_profit_retrace,
    )

    if judge_result.degraded:
        return ReferencePlan(
            **base, status=STATUS_UNAVAILABLE,
            buy_low=None, buy_high=None, buy_clamp=BUY_CLAMP_ABSENT, buy_why=None,
            exit_low=None, exit_high=None, exit_clamp=EXIT_CLAMP_ABSENT, exit_why=None,
            script_text=None, veto_reason=None,
            degraded=True, degrade_reason=judge_result.degrade_reason or "LLM 未激活或调用失败",
        )

    if judge_result.verdict == VERDICT_VETO:
        veto_reason = None
        if isinstance(parsed_json, dict):
            vr = parsed_json.get("veto_reason")
            veto_reason = vr.strip() if isinstance(vr, str) and vr.strip() else None
        return ReferencePlan(
            **base, status=STATUS_VETOED,
            buy_low=None, buy_high=None, buy_clamp=BUY_CLAMP_ABSENT, buy_why=None,
            exit_low=None, exit_high=None, exit_clamp=EXIT_CLAMP_ABSENT, exit_why=None,
            script_text=None, veto_reason=veto_reason,
            degraded=False, degrade_reason="",
        )

    # verdict == 通过
    if not isinstance(parsed_json, dict):
        return ReferencePlan(
            **base, status=STATUS_UNAVAILABLE,
            buy_low=None, buy_high=None, buy_clamp=BUY_CLAMP_ABSENT, buy_why=None,
            exit_low=None, exit_high=None, exit_clamp=EXIT_CLAMP_ABSENT, exit_why=None,
            script_text=None, veto_reason=None,
            degraded=True, degrade_reason="三件套 JSON 解析失败(围栏缺失或格式不合法)",
        )

    buy_low, buy_high, buy_clamp, buy_why = _clamp_buy(parsed_json.get("buy"), limit_up, limit_down)
    exit_low, exit_high, exit_clamp, exit_why = _clamp_exit(parsed_json.get("exit"))
    script_raw = parsed_json.get("script")
    script_text = script_raw.strip() if isinstance(script_raw, str) and script_raw.strip() else None

    return ReferencePlan(
        **base, status=STATUS_OK,
        buy_low=buy_low, buy_high=buy_high, buy_clamp=buy_clamp, buy_why=buy_why,
        exit_low=exit_low, exit_high=exit_high, exit_clamp=exit_clamp, exit_why=exit_why,
        script_text=script_text, veto_reason=None,
        degraded=False, degrade_reason="",
    )


# ======================================================================
#  一站式编排入口(供 pipeline.py 调用;自身含降级链,详见 docstring)
# ======================================================================

def judge_and_build_reference_plan(
    candidate: Candidate,
    trade_date: date,
    *,
    provider: Optional[LLMProvider],
    top_list_row: Optional[Dict[str, Any]] = None,
    transport: Optional[Any] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    industry_map: Optional[Dict[str, str]] = None,
    top_list_t0: Optional[Dict[str, dict]] = None,
    parquet_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
) -> Tuple[JudgeResult, Optional[ReferencePlan]]:
    """一次 LLM 调用产出「审判结论 + 参考三件套」(①-B 定死,不许拆成两次调用)的
    完整编排:① 装配参考件上下文(信息卡+哨兵阈值块,失败退回 `judge_candidate`
    默认上下文,**不阻断审判本身**);② 调 `judge_candidate`(唯一一次 LLM 调用,
    `system_prompt` 换成 `REFERENCE_PLAN_SYSTEM_PROMPT`,并把
    `split_narrative_and_reference_json` 作为 `narrative_splitter` 注入——**剥 json 发生在
    解析 verdict 之前**,v1.5.1 判定线 review 🟡-1 的修复点,顺序不可颠倒);③ 三件套 json
    与干净叙述由上一步一并带回(`JudgeResult.parsed_attachment` / `.narrative`,用户看到的
    评语不带原始 JSON,§2.7);④ 夹逼 + 状态判定组装 `ReferencePlan`。

    **两个独立产出物,一个失败不牵连另一个**(核心管线对可选情报输入必须包保险丝,
    项目 CLAUDE.md 铁律):
        · 上下文装配异常 → 退回 `context_block=None`(`judge_candidate` 内部改用
          `build_context_block` 兜底),LLM 调用照常发起——**只发起一次**,不会因
          为上下文装配失败又退回去发起第二次朴素审判调用(避免重复耗费预算/时间)。
        · json 剥离异常 → 由 `judge_candidate` 内部兜住(退回原文解析结论标签,见
          `judge._split_off_machine_block`),审判结论照出、本次无三件套。
        · 夹逼/状态装配异常(不应发生,但按"没有保险丝的必崩"铁律兜底)→
          `ReferencePlan` 部分为 `None`,`JudgeResult` 仍是刚才那次调用的结果,
          不二次调用 LLM。

    返回 `(JudgeResult, ReferencePlan | None)`——`JudgeResult.narrative` 已清掉三件套
    json 围栏(除非剥离步骤本身异常,那种情况下原样返回,极端边缘场景可接受)。
    `ReferencePlan is None` 表示本次没有可用的参考件(pipeline.py 层据此保持
    `Candidate.reference_plan` 默认 `None`)。**不落库**——落库由调用方在拿到非
    `None` 的 `ReferencePlan` 后自行决定要不要写(同 `save=True/False` 惯例)。
    """
    context_text: Optional[str] = None
    if provider is not None:
        try:
            context_text = build_reference_context_block(
                candidate, trade_date, top_list_row=top_list_row,
                industry_scores=industry_scores, industry_map=industry_map,
                top_list_t0=top_list_t0, parquet_dir=parquet_dir, db_path=db_path,
            )
        except Exception:  # noqa: BLE001 —— 上下文装配异常不得阻断审判本身,退回默认上下文
            logger.warning(
                "参考件上下文装配异常(%s),本票退回默认上下文继续审判", candidate.ts_code, exc_info=True
            )
            context_text = None

    result = judge_candidate(
        candidate, provider=provider, top_list_row=top_list_row, transport=transport,
        system_prompt=REFERENCE_PLAN_SYSTEM_PROMPT, context_block=context_text,
        narrative_splitter=split_narrative_and_reference_json,
    )

    plan: Optional[ReferencePlan] = None
    try:
        plan = build_reference_plan(
            candidate, trade_date, judge_result=result,
            parsed_json=result.parsed_attachment, db_path=db_path,
        )
    except Exception:  # noqa: BLE001 —— 参考件装配异常不得影响已产出的审判结论
        logger.warning(
            "参考件三件套装配异常(%s),审判结论照留、本次无参考件", candidate.ts_code, exc_info=True
        )
        plan = None

    return result, plan


__all__ = [
    "STATUS_OK",
    "STATUS_VETOED",
    "STATUS_UNAVAILABLE",
    "BUY_CLAMP_OK",
    "BUY_CLAMP_ABSENT",
    "BUY_CLAMP_REJECTED_OUT_OF_LIMIT",
    "BUY_CLAMP_REJECTED_MALFORMED",
    "BUY_CLAMP_REJECTED_NO_LIMIT",
    "EXIT_CLAMP_OK",
    "EXIT_CLAMP_ABSENT",
    "EXIT_CLAMP_REJECTED_MALFORMED",
    "REFERENCE_DISCLAIMER",
    "REFERENCE_PLAN_SYSTEM_PROMPT",
    "ReferencePlan",
    "build_reference_context_block",
    "split_narrative_and_reference_json",
    "build_reference_plan",
    "judge_and_build_reference_plan",
]
