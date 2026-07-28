"""问询台服务(plan §2.5,**v1.3.3 改版:审判员 → 自由分析师**)。

用户丢一票进来 → **确定性材料(同一评分管线跑分 + 纪律/硬线核对 + 板块年龄 + K4 安检)
→ LLM 自由叙述回答用户实际问的问题**。**不再有裁决、不再有拦截。**

**v1.3.3 变了什么(用户 2026-07-27 拍板,方向已定)**

1. **硬栏杆全拆**。旧实现在「确定性纪律未过」时**直接终止**、不劳 LLM、返回一句
   「按纪律不予放行」——用户实测拿创业板票(300759 康龙化成)问,只能得到一句拒绝,
   问不出任何东西。现在:**任何票都会走完整流程、拿到实质回答**;纪律命中项降级为
   **回答里的警告标注**,一律不拦。
2. **二值裁决枚举退役**。`VERDICT_REJECT`/`VERDICT_PASS`(「不符合」/「初审通过进海选池」)
   删除;`verdict` 字段**保留**(客户端契约不破,见下)但取值改成**纯描述性标注**
   `已分析` / `已分析·有风险提示` —— 它不是判决,不授权也不禁止任何操作。
3. **「初审通过进海选池」退役**。问询台**不再自动写 `inquiry_pool`**;想让一只票进当晚
   报告,由用户在客户端**一键加自选**(自选池本就进当晚自选体检 + 哨兵关注池)。
   `inquiry_pool` 表与报告侧消费逻辑(`load_pending_inquiry_codes`/
   `mark_inquiry_pool_consumed`)**保留不动**——向后兼容,空池 noop,历史待消费行仍会被
   正常消费掉;只是不再有自动写入方。
4. **软护栏(用户拍板「保留但改成软形式」)**:**不下「买/卖」指令**。可以充分分析走势、
   逻辑、风险、赔率,但不产出买卖指令。**软 = 只在 prompt 层约束**:刻意**不做**枚举
   强校验、**不做**输出后处理拦截(旧实现那三重保险连同二值裁决一起拆了)。
   理由(写进 prompt 也写在这里,别哪天有人"顺手加回强校验"):**LLM 的买卖判断没有
   回测支撑,不该塞进用户的决策链;分析归系统,扣扳机归用户。** 强校验换来的是把
   模型逼进模板腔(违背 §2.7 自由对话体),而真正的护栏本就该是"系统永不下单"这条铁律
   (§3.8),不是在文本里 grep「买」字。

**客户端契约(刻意不破)**:macOS 客户端已装 v1.3、iOS 未装。`InquiryOut` 字段集合
**一个不增不减**(`ok/code/reply/verdict/evidence/degraded`),只把 `verdict` 的 pydantic
类型由 `Literal["不符合","初审通过进海选池"]` **放宽成 `str`**。客户端 Swift
`InquiryVerdict` 对未识别值走 `.unknown(raw)` 分支(原样显示 + 中性色调),且
`enablesBuyAction` 恒 false 穷举写死、不看 verdict 分支——故已装的 App **不会解码失败、
不会误显示成某个已知态、更不会因此冒出买入按钮**。

**同码不重写铁律**:确定性材料复用 `strategy.brain`(现役规则)+ `research.panel`(选股域)
+ `strategy.signals`(禁买谓词)+ `report.candidates`(评分)+
`report.watchlist_check.discipline_checks`(纪律判定项,与自选体检**同一个函数**)+
`report.holding_k4_check`(K4 安检判据镜像,与持仓牌/候选情报管线同一份),
**不在本模块另写一份领域规则**。

**拆墙后「纪律命中项」实际还剩什么**:现役 v1.3.3 把 `forbid_high_elasticity` 关掉后,
`discipline_checks` 只剩**真硬线**——选股域一条组合原因(ST/退市风险 / 北交所 / 股价<2 元 /
20 日均额<2000 万 / 无 MA20 即次新未成形),外加现役 config **若启用**才出现的 P4/P5/P6
(K1 血缘下三者皆 None,不产生任何命中)。停牌/未上市/代码有误 → 查无当日行情,单列一条。

**降级(缺 key)**:LLM 未激活 → 确定性材料照跑照给,LLM 段返「未激活」占位文案,
`degraded=True`,全链路不崩,**且仍然给出实质的确定性回答**(不是一句"未激活"了事)。

**工具调用的落地范围(诚实标注,承 4A.5)**:实时取数(`sentinel.quotes`)与重算
(评分 + 板块年龄 + K4)在调用 LLM **之前**跑好、作为结构化上下文注入,LLM 段本身开启
**原生联网搜索**(`provider.chat(enable_search=True)`)。未实现"LLM 主动多轮
function-calling 回调后端函数"这一形态(无法活体验证 + 预注入已覆盖三种能力),记入欠账。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from neckline.api.schemas import VERDICT_ANALYZED, VERDICT_ANALYZED_WARN
from neckline.data.market_data import resolve_stock_names
from neckline.llm.base import ChatMessage, LLMProvider, search_coverage_line
from neckline.report.candidates import _base_score_expr  # 同码:展示排序分与报告一致
from neckline.report.industry_strength import (
    IndustryStrength,
    compute_industry_strength,
    industry_strength_lookup,
    load_industry_map,
    stock_persist_days,
)
from neckline.report.sectors import (
    SectorScore,
    compute_sector_strength,
    load_index_names,
    load_member_map,
    sector_hot_lookup,
)
from neckline.report.watchlist_check import discipline_checks  # 同码:纪律判定项,与自选体检共用
from neckline.review.parse import normalize_ts_code
from neckline.strategy import brain
from neckline.strategy import signals as S
from neckline.strategy.features import build_research_panel
from neckline.strategy.momentum import MomentumConfig, build_entry_mask

logger = logging.getLogger(__name__)

_BOARD_LABEL = {"MAIN": "主板", "GEM": "创业板", "STAR": "科创板", "BSE": "北交所"}


INQUIRY_SYSTEM_PROMPT = """你是一位资深 A 股分析师,和用户一对一聊一只具体的票。用户从外部消息源
看到它,想听听你怎么看。

**你的定位**:分析师,不是审判员。用户问什么你答什么——问走势就谈走势(位置、结构、量能、
关键价位),问逻辑就谈逻辑(基本面、题材、产业链位置),问风险就谈风险,问催化就说催化在哪、
什么时候可能兑现、有没有变数。该看多就说看多的理由,该看空就说看空的理由,别和稀泥。

**唯一的硬约束:不下买卖指令。** 你可以把走势、逻辑、风险、赔率分析得很透,可以说
"这个位置的赔率不算好"、"催化还没兑现"、"这里的风险在哪",但**不要产出"现在买入/建议买入/
可以卖了/立刻清仓"这类指令**。理由不是怕你说错,而是:你的买卖判断没有回测支撑,不该塞进
用户的决策链;**分析归你,扣扳机归用户。** 用户要的是把牌摊开,不是替他做决定。

**材料**:系统会先给你一份结构化材料——该票的价量结构、当日评分、所属板块与板块年龄、
纪律核对命中的风险提示(如有)、K4 派发域安检命中(如有)。你还配有联网搜索,可查该股近期
新闻、公告、题材催化。

**信息边界(铁律)**:只依据给定的结构化数据与联网搜索实际返回的内容;搜不到就明说"未搜到
相关消息",**绝不编造新闻、传闻、业绩、题材**。不确定的地方直说不确定。

**风险提示的用法**:材料里若带了风险提示(ST / 退市风险 / 流动性太差 / 次新 / 停牌 /
K4 派发域命中等),**在回答里如实提到并说明它意味着什么**——但那是提示,不是禁令,
不要因此拒绝分析这只票,更不要把回答变成一句"不符合纪律"。

**输出风格(硬约束)**:自由叙述,写成连贯的分析文字,像分析师当面跟你聊。**禁止**分点列表、
多维打分表、"技术面/资金面/消息面"固定分栏模板、以及任何形式的结论标签行。直接说人话。"""


@dataclass
class DeterministicResult:
    """喂给 LLM(和降级文案)的确定性材料。**没有任何字段表示"准不准买"**——拆墙后
    本模块不产出通过/不通过的判定,只产出事实与提示。"""
    code: str
    basis_date: date
    has_data: bool
    name: str = ""
    board: str = ""                                           # 中文板块标签
    close: Optional[float] = None
    # 纪律/硬线命中项(**警告标注,不拦人**)。来源 = `watchlist_check.discipline_checks`
    # 同一个函数;拆墙后现役 config 下只剩真硬线,见模块头。
    risk_flags: List[str] = field(default_factory=list)
    # K4 安检命中(如「年线下涨停(派发域)」),同样只提示不拦。
    k4_flags: List[str] = field(default_factory=list)
    passes_buypoint_today: bool = False
    score: Optional[float] = None
    sectors: List[str] = field(default_factory=list)          # 所属概念板块名
    hot_sectors: List[str] = field(default_factory=list)      # 命中今日热门(含板块年龄)
    evidence: List[str] = field(default_factory=list)


def _cfg_from_active(db_path: Optional[Path]) -> Optional[MomentumConfig]:
    active = brain.get_active(db_path=db_path)
    if active is None:
        return None
    try:
        return MomentumConfig(**active.rule["config"])
    except (KeyError, TypeError):
        return None


def _k4_flags(
    code: str, basis_date: date, *, db_path: Optional[Path], parquet_dir: Optional[Path],
    industry_of: Dict[str, str], industry_hot: Dict[str, IndustryStrength],
) -> List[str]:
    """该票当日的 K4 安检命中文案(**只提示不拦**,用户 2026-07-27 拍板)。

    **同码**:判据镜像直接复用 `report.holding_k4_check` 的 `_build_holding_feature_panel`
    +`_evaluate_hits`+`load_k4_sections`(与持仓牌 ② / 候选情报管线 ③ 同一份,阈值单一源;
    跨模块引下划线函数的先例见 `report/intel_candidates.py`)。**题材类(A2/B3)持续天数
    v1.4-② 起读 `industry_strength.stock_persist_days`**(`industry_of`/`industry_hot` 由
    `run_deterministic_checks` 单独算好传入,与 `sectors`/`hot` 那份**板块展示**用的
    `member_map`/`hot` 是两套不同数据,不要混用)。任何异常 → 空列表 + 警告日志,绝不影响
    主流程(K4 是加分项,不是问询台的必需件)。"""
    try:
        from neckline.report.holding_k4_check import (
            _build_holding_feature_panel,
            _evaluate_hits,
            _load_k4_evidence,
            load_k4_sections,
        )

        panel = _build_holding_feature_panel([code], basis_date, parquet_dir)
        row = None if panel.is_empty() else panel.to_dicts()[0]
        hits = _evaluate_hits(row, stock_persist_days(code, industry_of, industry_hot), _load_k4_evidence(db_path))
        if not hits:
            return []
        sections = load_k4_sections(db_path)
        out = []
        for h in hits:
            sec = sections.get(h.code)
            tag = "K4 硬拦区" if sec == "hard_cut" else ("K4 标记区" if sec == "avoid_flag" else "K4")
            strength = "价量强证据" if h.evidence_strength == "price_volume" else "成分弱证据·仅参考"
            out.append(f"{h.label}({tag},{strength})")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台 K4 安检核算异常(%s,不影响主流程)", e)
        return []


def run_deterministic_checks(
    code: str,
    basis_date: date,
    *,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    panel_fn: Optional[Callable[..., Any]] = None,
) -> DeterministicResult:
    """确定性材料装配(§2.5 第一步):同码评分 + 纪律/硬线提示 + 板块年龄 + K4 安检。

    任何异常 → `has_data=False` 的结果 + 一条说明性 evidence,**绝不抛崩**。注意
    `has_data=False` 现在**不再意味着"不放行"**——它只是"这只票当日没有 EOD 行情可核",
    LLM 段照跑(用户可能就是想问一只停牌票的后续)。`panel_fn`/`sector_scores`/
    `industry_scores` 可注入单测,免联网。`sector_scores` 只服务板块展示文案;
    `industry_scores`(v1.4-② 起)服务题材持续天数判据(K4 安检 A2/B3 的输入),两者是
    独立的两套数据,互不代理。"""
    code = normalize_ts_code(code)      # 裸 6 位 → `300759.SZ`(面板是 TuShare 口径)
    det = DeterministicResult(code=code, basis_date=basis_date, has_data=False)
    # 中文名(v1.3.4 修):`name` 字段自建库起就声明了、`build_llm_context` 也一直在读,
    # **但从来没有任何一处赋过值** —— 喂给 LLM 的材料首行恒为「名称:未知」。后果不止是
    # 展示难看:中文名是中文财经检索最值钱的词,没有它,联网搜索基本搜不到这只票的新闻。
    # 放在所有 early return 之前,停牌/查无行情的票也要有名字(那种票更需要靠搜索说话)。
    det.name = resolve_stock_names([code], db_path).get(code, "")
    cfg = _cfg_from_active(db_path)
    if cfg is None:
        det.evidence.append("策略大脑无现役版本,无法核对纪律(配置缺陷);以下只能做定性分析。")
        return det

    build_panel = panel_fn or build_research_panel
    try:
        panel = build_panel(basis_date, basis_date, with_forward=False, parquet_dir=parquet_dir)
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台确定性检查建面板失败(%s)", e)
        det.evidence.append("无法加载当日行情面板,本次只能做定性分析(无价量数据)。")
        return det

    if panel is None or panel.is_empty():
        det.evidence.append(f"{basis_date} 全市场面板为空(可能该日无数据),无价量数据可核。")
        return det

    panel = S.add_ret_rank_column(panel)
    sub = panel.filter(panel["ts_code"] == code)
    if sub.is_empty():
        det.evidence.append(
            f"查无 {code} 在 {basis_date} 的行情——可能停牌 / 次新未上市 / 代码有误"
            f"(这是一条风险提示,不是拒绝分析的理由)。"
        )
        det.risk_flags.append("当日无行情数据(停牌 / 未上市 / 代码有误)")
        return det

    # —— 纪律/硬线核对(同码:与 `report.watchlist_check.score_watchlist` 共用同一份
    # `discipline_checks(cfg)`;拆墙后只剩真硬线,见模块头)。**命中即警告,不拦。** ——
    checks = discipline_checks(cfg)
    annotated = sub.with_columns([expr.alias(col) for col, _label, expr in checks])
    row = annotated.row(0, named=True)
    det.has_data = True
    det.close = row.get("close")
    board_raw = row.get("board", "MAIN")
    det.board = _BOARD_LABEL.get(board_raw, board_raw)
    det.risk_flags = [label for col, label, _expr in checks if row.get(col)]

    # —— 同码买点/评分(材料,不作任何门槛)——
    try:
        mask_val = sub.select(build_entry_mask(cfg).alias("_m")).row(0)[0]
        det.passes_buypoint_today = bool(mask_val)
        det.score = round(float(sub.select(_base_score_expr(cfg).alias("_s")).row(0)[0]), 1)
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台买点/评分核算异常(%s,不影响其余材料)", e)

    # —— 板块名 + 板块年龄(§2.5「板块年龄」,纯展示)——
    member_map: Dict[str, List[str]] = {}
    hot: Dict[str, SectorScore] = {}
    try:
        member_map = load_member_map(parquet_dir=parquet_dir)
        index_names = load_index_names(parquet_dir=parquet_dir)
        boards = member_map.get(code, [])
        det.sectors = [index_names.get(b, b) for b in boards]
        if boards:
            if sector_scores is None:
                sector_scores = compute_sector_strength(basis_date, parquet_dir=parquet_dir)
            hot = sector_hot_lookup(sector_scores or [])
            det.hot_sectors = [
                f"{hot[b].name}(板块年龄{hot[b].board_age}天,20日{hot[b].ret_20d:+.1%})"
                for b in boards if b in hot
            ]
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台板块年龄核算异常(%s,不影响其余材料)", e)

    # —— 题材持续天数(v1.4-② 起唯一源)+ K4 安检 ——:与上面板块展示是两套独立数据
    # (概念板块=多对多展示,`stock_basic.industry`=一对一判据输入),`industry_of`/
    # `industry_hot` 与该票是否属于任何概念板块无关(每只有 industry 的票都算)。
    industry_of: Dict[str, str] = {}
    industry_hot: Dict[str, IndustryStrength] = {}
    try:
        industry_of = load_industry_map(db_path)
        if industry_scores is None:
            industry_scores = compute_industry_strength(basis_date, parquet_dir=parquet_dir, db_path=db_path)
        industry_hot = industry_strength_lookup(industry_scores or [])
    except Exception as e:  # noqa: BLE001
        logger.warning("问询台行业强度核算异常(%s,不影响其余材料)", e)

    det.k4_flags = _k4_flags(
        code, basis_date, db_path=db_path, parquet_dir=parquet_dir,
        industry_of=industry_of, industry_hot=industry_hot,
    )
    _build_evidence(det)
    return det


def _build_evidence(det: DeterministicResult) -> None:
    """`evidence` = 展示给用户的确定性事实条目(客户端在回答旁列出)。**措辞一律中性**——
    不再有「被排除」「不予放行」这类判决腔;板块只陈述,不标允许/排除(拆墙后创业板/
    科创板本就允许,§2.3 候选生成域也早已含它们)。"""
    ev = det.evidence
    ev.append(f"板块:{det.board}")
    if det.risk_flags:
        ev.append("风险提示(仅提示,不构成禁令):" + ";".join(det.risk_flags))
    else:
        ev.append("未命中系统硬线(非 ST、满足选股域流动性/价格/形态门槛)。")
    if det.k4_flags:
        ev.append("K4 安检命中:" + "、".join(det.k4_flags))
    if det.passes_buypoint_today:
        ev.append(f"今日已同时满足母战法买点(pullback/breakout),展示排序分约 {det.score}。")
    elif det.score is not None:
        ev.append(f"今日未走出母战法买点形态(展示排序分约 {det.score};买点是形态口径,不是买卖建议)。")
    if det.hot_sectors:
        ev.append("命中今日热门板块:" + "、".join(det.hot_sectors))
    elif det.sectors:
        ev.append("所属概念板块:" + "、".join(det.sectors) + "(今日非热门)")


def build_llm_context(det: DeterministicResult, quote: Optional[Any] = None) -> str:
    """把确定性材料 + 实时行情组装成喂 LLM 的结构化上下文(纯文本块,不是 JSON)。
    **结尾不再要求任何裁决标签**——只交代材料边界,回答什么由用户的问题决定。"""
    lines = [
        f"股票代码:{det.code};名称:{det.name or '未知'};交易所板块:{det.board}",
    ]
    if not det.has_data:
        lines.append("⚠ 系统没有取到该票当日 EOD 行情(可能停牌 / 未上市 / 代码有误)——"
                     "以下无价量材料,请据搜索与常识作答,并提醒用户核对代码。")
    if det.close is not None:
        lines.append(f"最近收盘:{det.close:.2f} 元")
    if det.risk_flags:
        lines.append("系统风险提示(**提示,非禁令**;请在回答里如实提到并解释含义,不要因此拒答):"
                     + ";".join(det.risk_flags))
    else:
        lines.append("系统硬线核对:未命中任何硬线。")
    if det.k4_flags:
        lines.append("K4 派发域安检命中(研究结论,价量强证据可信度高于成分弱证据):"
                     + "、".join(det.k4_flags))
    if det.has_data:
        lines.append(f"母战法买点形态:{'今日已满足' if det.passes_buypoint_today else '今日未满足'}" +
                     (f";展示排序分约 {det.score}" if det.score is not None else "") +
                     "(形态口径,不是买卖建议)")
    if det.hot_sectors:
        lines.append("命中今日热门板块(含板块年龄):" + "、".join(det.hot_sectors))
    elif det.sectors:
        lines.append("所属概念板块:" + "、".join(det.sectors))
    if quote is not None:
        try:
            chg = (quote.price / quote.pre_close - 1) if quote.pre_close else None
            chg_txt = f"{chg:+.1%}" if chg is not None else "未知"
            lines.append(f"盘中实时(若在交易时段):现价 {quote.price:.2f},涨跌 {chg_txt}")
        except Exception:  # noqa: BLE001
            pass
    lines.append("请结合以上材料与联网搜索,回答用户的问题。用户没问的不必硬答,"
                 "问了的要答透。记住:分析可以很直接,但不下买卖指令。")
    return "\n".join(lines)


def _build_search_query(det: DeterministicResult, messages: List[Dict[str, str]]) -> str:
    """拼联网搜索检索词 = `「<中文名>(<代码>) <用户最后一句>」`(v1.3.4)。

    **为什么非拼不可**(2026-07-27 生产实测,同一条问询台链路只换最后一句):
      · 用户代词提问「这只票最近的业绩和公告怎么样?」→ 供应商自行推导的检索词里
        没有任何股票身份,搜回来的是「周六,5家创业板公司发布业绩预告」这类泛泛新闻,
        模型只能答「没有拿到 300759.SZ 的具体数据」;
      · 同一票同一材料,只把「康龙化成(300759.SZ)」放进检索词 → 命中全变成
        「康龙化成半年营收76亿」「华西医药康龙化成 2026Q1 点评」,回答直接给出
        7 月 13 日那份 2026 半年度业绩预告的真实区间。
    身份信息本来就在更早那条材料消息里,**但救不回来**——供应商的检索词紧跟最后一条
    user 消息。所以必须显式传。

    用户那句原样带上(不做意图提取):它承载了"想问什么"(业绩/走势/风险),让检索词
    比光有股票名更贴题。长度由 provider 侧截断,这里不预截。"""
    last_user = ""
    for m in reversed(messages or []):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            last_user = (m["content"] or "").strip()
            break
    subject = f"{det.name}({det.code})" if det.name else det.code
    return f"{subject} {last_user}".strip()


def run_inquiry(
    code: str,
    messages: List[Dict[str, str]],
    *,
    basis_date: date,
    db_path: Optional[Path] = None,
    parquet_dir: Optional[Path] = None,
    provider: Optional[LLMProvider] = None,
    quotes_fn: Optional[Callable[[List[str]], Dict[str, Any]]] = None,
    transport: Optional[Any] = None,
    sector_scores: Optional[List[SectorScore]] = None,
    industry_scores: Optional[List[IndustryStrength]] = None,
    panel_fn: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """跑一次问询。返回 `{reply, verdict, evidence, degraded}`。

    **v1.3.3:任何票都走完整流程**——不再有"纪律不过直接终止"的分支,LLM 段对所有票都跑
    (有 provider 时)。`verdict` 只是描述性标注(有无风险提示),**不是判决**,不参与任何
    分支决策。**不再写 `inquiry_pool`**(海选池自动写入退役,改由用户一键加自选)。

    旧签名的 `pool_date` 形参已随海选池自动写入一并删除,调用方 `api/app.py` 同步改。"""
    det = run_deterministic_checks(
        code, basis_date, db_path=db_path, parquet_dir=parquet_dir,
        sector_scores=sector_scores, industry_scores=industry_scores, panel_fn=panel_fn,
    )

    degraded = False
    if provider is None:
        degraded = True
        reply = _degraded_reply(det)
    else:
        quote = None
        if quotes_fn is not None:
            try:
                quote = (quotes_fn([det.code]) or {}).get(det.code)
            except Exception as e:  # noqa: BLE001
                logger.warning("问询台实时取数失败(%s,LLM 段不注入盘中行情)", e)
        chat_messages = [ChatMessage(role="system", content=INQUIRY_SYSTEM_PROMPT),
                         ChatMessage(role="user", content=build_llm_context(det, quote))]
        for m in messages:
            role = m.get("role")
            if role in ("user", "assistant"):
                chat_messages.append(ChatMessage(role=role, content=m.get("content", "")))
        try:
            result = provider.chat(
                chat_messages, enable_search=True,
                search_query=_build_search_query(det, messages), transport=transport,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("问询台 LLM 调用异常(%s,降级为确定性材料)", e)
            result = None
        if result is None or not result.ok:
            degraded = True
            reply = _degraded_reply(det)
        else:
            # **刻意不做任何后处理**:不抽标签、不 grep「买」、不改写模型原文(软护栏 =
            # prompt 层,见模块头 4)。模型说什么原样透给用户。
            reply = result.content.strip()
            # 搜索取证覆盖进 `evidence`(不进 `reply` —— reply 是模型原文,不掺系统文案)。
            det.evidence.append(search_coverage_line(len(result.search_hits or [])))

    verdict = VERDICT_ANALYZED_WARN if (det.risk_flags or det.k4_flags) else VERDICT_ANALYZED
    return {"reply": reply, "verdict": verdict, "evidence": det.evidence, "degraded": degraded}


def _degraded_reply(det: DeterministicResult) -> str:
    """缺 key / LLM 异常时的降级回答。**仍然是一段实质回答**(把确定性材料讲清楚),
    不是一句"未激活"了事;结尾诚实标注消息面缺席。"""
    parts = [det.code]
    if det.board:
        parts.append(f"({det.board})")
    if det.close is not None:
        parts.append(f" 最近收盘 {det.close:.2f} 元。")
    else:
        parts.append(" 当日无 EOD 行情(可能停牌 / 未上市 / 代码有误,建议先核对代码)。")
    if det.risk_flags:
        parts.append("风险提示:" + ";".join(det.risk_flags) + "——这是提示,不是禁令,是否参与由你判断。")
    elif det.has_data:
        parts.append("系统硬线核对未命中任何一条(非 ST、满足选股域流动性/价格/形态门槛)。")
    if det.k4_flags:
        parts.append("K4 安检命中:" + "、".join(det.k4_flags) + "。")
    if det.passes_buypoint_today:
        parts.append(f"形态上今日已走出母战法买点,展示排序分约 {det.score}。")
    elif det.score is not None:
        parts.append(f"形态上今日未走出母战法买点,展示排序分约 {det.score}。")
    if det.hot_sectors:
        parts.append("命中今日热门板块:" + "、".join(det.hot_sectors) + "。")
    parts.append("LLM 消息面分析未激活(未配置 LLM key),本次只有上面这些确定性材料,"
                 "没有新闻/公告/题材面的判断。")
    return "".join(parts)


__all__ = [
    "DeterministicResult",
    "INQUIRY_SYSTEM_PROMPT",
    "run_deterministic_checks",
    "build_llm_context",
    "run_inquiry",
]
