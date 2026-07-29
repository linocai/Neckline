"""exec_hint 产品化(plan §五 v1.4-⑤-A,需求 8 末段:「执行提示(exec_hint)产品化随
本需求一并做:强票市价/小δ提示、挂低单预期下调提示、温和带标注——全部读 DB
`K4.k4_advisory`,不抄常量」)。

**语义红线(定死,任何文案违反即验收不过)**:exec_hint 回答的是"如果你决定动手,
怎么执行更不吃亏",**不是"该不该买"**——展示标题统一「执行提示」,文案不得出现
"建议买入/推荐/看好/值得买"。**exec_hint 不进排序键**(它未经方向审计,只进候选卡/
信息卡展示,见 `report/intel_candidates.py::_SORT_KEY_INPUTS` 白名单 + 本模块单测
`tests/test_intel_candidates.py` 的锁)。

**文字一律读 DB,不抄常量**:`strategy_versions` K4 行 `rule_json["k4_advisory"]["exec_hint"]`
是 `{code: text}` 扁平映射(2026-07-29 真库探活确认——与 `hard_cut`/`avoid_flag` 的
`{code:{expr,src,evidence}}` 嵌套形状不同,倒是与 `circuit_breaker` 节同形)。**命中条件
的可执行镜像**住本模块命名常量(下表),**改阈值须同改两处**(DB 文字 + 本模块常量),
镜像口径对照表(同 `holding_k4_check.py` 体例):

    advisory 码                        | advisory text(规格档,DB k4_advisory.exec_hint)      | 本模块可执行镜像(命中条件)
    ------------------------------------|-------------------------------------------------------|--------------------------------------------------------
    C1_strong_market_order             | 强票用市价/小δ立即介入不回踩(H3:挂低单系统性漏起飞强票) | `ret_1d ≥ _C1_STRONG_RET(=0.05)` 或 `is_limit_up`
    C2_mild_red_low_variance           | 温和带(2-3%)低方差首选(H5,但≈0期望非正alpha)          | `is_mild_band(ret_1d)`(复用 `info_card.MILD_BAND_RANGE`=[0.02,0.03],不重开一份阈值)
    C3_low_limit_self_aware            | 坚持挂低单=样本限定回踩偏弱侧,应下调预期收紧退出(H3)   | 该票在 `trade_date` 当天或之前**最近一条** `decision_log`(任意状态,见 `_hit_c3` docstring):`max_chase_pct≤0` 或 `planned_price<pre_close`
    C4_no_pullback_bigred_mechanical   | 回调大红机械层不做(战役三:信号级+1.69%→组合级-46%)     | `close>ma20` 且 `ret_1d ≥ _C4_BIGRED_UP(=0.05)`

    ⚠ **C4 与 `holding_k4_check.B4_chase_strong_red` 数值巧合相同(均 5%),两者概念上
    独立**(exec_hint vs avoid_flag 是 DB 里两个不同 section 的不同条目,各自的
    「改阈值须同改两处」契约分别对各自的 DB 文字负责)——本模块**不复用** `holding_k4_check`
    内部私有的 `_hit_B4` 布尔列,而是独立从 `close`/`ma20`/`ret_1d` 现算,避免把两个概念
    耦合成同一份、未来任一方单独调参时误伤另一方。

**DB 缺该节(隔离测试库 / K4 行缺失 / 结构异常)时的兜底**:`_FALLBACK_HINT_TEXT` 逐码
兜底,**允许四码部分命中 DB、部分回退**(不要求整节要么全有要么全无)——每条独立标注
`source: "db" | "fallback"`,供客户端/审计诚实展示这条文字的来源。

**两条消费路径**(同 `info_card.py`/`intel_candidates.py` 既有姿势,本模块只有一条,
因为 exec_hint 不需要"单只按需现算"的完整版——候选生成时算一次即够,不像信息卡 K
线需要 60 日序列才现算):`attach_exec_hints(candidates, trade_date, ...)` 在
`pipeline.py` 报告生成期批量调用,**零额外 parquet 读取**——C1/C2/C4 直接读
`Candidate.raw`(候选生成时已装配好的 K4 特征面板行,含 `ret_1d`/`is_limit_up`/
`close`/`ma20`/`pre_close`);C3 每只候选查一次该 `ts_code` **在 `trade_date` 当天或
之前**最近一条 `decision_log`(SQLite 按 `ts_code`+日期索引点查,轻量,不读
parquet;`trade_date` 截断见 `_latest_decision` docstring——无前视偏差铁律的落地,
不是摆设参数)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline.report.candidates import Candidate
from neckline.report.info_card import is_mild_band

logger = logging.getLogger(__name__)

# —— advisory 码(DB `k4_advisory.exec_hint` 四条 key,原样照抄——这是"码"当查找键,
#    不是抄阈值/文案,§3.8 单一事实源不适用于 key 名本身)——————————————————————————
C1_STRONG_MARKET_ORDER = "C1_strong_market_order"
C2_MILD_RED_LOW_VARIANCE = "C2_mild_red_low_variance"
C3_LOW_LIMIT_SELF_AWARE = "C3_low_limit_self_aware"
C4_NO_PULLBACK_BIGRED_MECHANICAL = "C4_no_pullback_bigred_mechanical"

# 固定展示/求值顺序(与 DB advisory 编号顺序一致,C1→C4)。
_ALL_CODES: tuple = (
    C1_STRONG_MARKET_ORDER, C2_MILD_RED_LOW_VARIANCE,
    C3_LOW_LIMIT_SELF_AWARE, C4_NO_PULLBACK_BIGRED_MECHANICAL,
)

# —— 触发镜像阈值(可执行镜像单一源;镜像 DB advisory 文字口径,改阈值须同改两处)——
_C1_STRONG_RET = 0.05    # C1:当日 ret_1d ≥5% 视为"强票"(或涨停)
_C4_BIGRED_UP = 0.05     # C4:回调大红,ret_1d ≥5%(与 holding_k4_check._B4_UP 数值巧合
                         # 相同、概念独立声明,见模块头「⚠」)

# DB 读不到该节(隔离测试库 / K4 行缺失)时的兜底文案(镜像 research 判决摘要;生产恒读 DB)。
_FALLBACK_HINT_TEXT: Dict[str, str] = {
    C1_STRONG_MARKET_ORDER: "强票挂低单会系统性漏掉起飞的(H3);要动手就市价/小 δ",
    C2_MILD_RED_LOW_VARIANCE: "低方差首选带(H5:核心带尾部最紧);但≈0期望、非正alpha,不构成买入理由",
    C3_LOW_LIMIT_SELF_AWARE: "坚持挂低单=把样本限定在会回踩的偏弱侧,应下调预期、收紧退出(H3)",
    C4_NO_PULLBACK_BIGRED_MECHANICAL: "回调大红机械层不做(战役三:信号级+1.69%→组合级-46%、2026通杀)",
}


@dataclass
class ExecHint:
    code: str
    text: str
    source: str   # db | fallback

    def public_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "text": self.text, "source": self.source}


# —— DB 读取(单一事实源,不抄常量;缺读兜底不崩,同 `holding_k4_check._load_k4_evidence`)——

def _load_k4_exec_hint_texts(db_path: Optional[Path]) -> Dict[str, str]:
    """读 DB `strategy_versions` K4 行 `k4_advisory.exec_hint` 四条原文——**扁平
    `{code: text}` 映射**(同 `circuit_breaker` 节形状,与 `hard_cut`/`avoid_flag` 的
    `{code:{expr,src,evidence}}` 嵌套形状不同,见模块头 2026-07-29 真库探活记录)。K4
    行缺失(隔离测试库)/ `k4_advisory` 结构异常 / `exec_hint` 节缺失或类型不对 → 空
    dict,调用方按码逐一 fallback 到 `_FALLBACK_HINT_TEXT`(允许部分命中部分兜底)。"""
    from neckline.strategy import brain

    try:
        v = brain.get_version("K4", db_path=db_path)
    except Exception:  # noqa: BLE001  隔离库读失败不崩
        return {}
    if v is None:
        return {}
    adv = (v.rule or {}).get("k4_advisory") or {}
    section = adv.get("exec_hint")
    if not isinstance(section, dict):
        return {}
    return {str(code): str(text) for code, text in section.items() if isinstance(text, str)}


def _hint_text_and_source(code: str, db_texts: Dict[str, str]) -> tuple:
    if code in db_texts:
        return db_texts[code], "db"
    return _FALLBACK_HINT_TEXT.get(code, ""), "fallback"


# —— 触发镜像(逐条纯函数,各自独立可单测,对齐模块头对照表)——————————————————————

def _hit_c1(row: Optional[Dict[str, Any]]) -> bool:
    """C1 强票市价/小δ:当日 `ret_1d≥5%` 或涨停。`row` 为空(当日无 EOD 行)→ 不触发
    (没有数据就不判,不臆造"这是强票")。"""
    if not row:
        return False
    ret_1d = row.get("ret_1d")
    return bool(row.get("is_limit_up")) or (ret_1d is not None and ret_1d >= _C1_STRONG_RET)


def _hit_c2(row: Optional[Dict[str, Any]]) -> bool:
    """C2 温和带:直接复用 ④ 已定的 `info_card.is_mild_band`/`MILD_BAND_RANGE`
    (=[2%,3%]),**不重开一份阈值**(模块头「advisory 文字为规格档,改阈值同改两处」的
    单一源就是 `info_card.py`,本模块只是消费方)。"""
    if not row:
        return False
    return is_mild_band(row.get("ret_1d"))


def _hit_c3(row: Optional[Dict[str, Any]], recent_decision: Optional[Any]) -> bool:
    """C3 挂低单自觉,与 ⑤-B 联动:该票**关联决策日志**存在时,`max_chase_pct≤0`
    (显式选择只在低开时买)或 `planned_price<pre_close`(计划挂单价已低于昨收)→ 提示
    应下调预期、收紧退出。

    **"关联决策日志"的判断口径(plan 未逐字规定,本模块显式登记的判断;由调用方
    `_latest_decision` 负责挑选,本函数是纯判定、不关心怎么选出来的)**:取该 `ts_code`
    **最近一条**(`created_at` 最新,且不晚于报告的 `trade_date`——见 `_latest_decision`
    docstring 的无前视偏差截断)`decision_log` 行,**不限 `status`**——即便已
    `filled`/`cancelled`/`expired`,它仍代表用户对这只票"当时怎么打算买"的最新记录,
    C3 提醒的是"你自己曾经的预注册计划已经暴露了低吸倾向,别忘了这个自觉",与该计划
    最终有没有成交/是否已取消无关。调用方(`attach_exec_hints`)只查一次「最近一条」,
    不遍历该票全部历史决策。

    `recent_decision=None`(该票从无关联决策日志)→ 不触发(不无中生有"这票该挂低
    单")。`max_chase_pct`/`planned_price` 两者都是 `None`(用户预注册时两个都没填,
    理论不该发生——⑤-B 要求 `maxChasePct` 必填传,但 `plannedPrice` 本就一直可选)→
    不触发。"""
    if recent_decision is None:
        return False
    chase = getattr(recent_decision, "max_chase_pct", None)
    if chase is not None and chase <= 0:
        return True
    planned = getattr(recent_decision, "planned_price", None)
    pre_close = (row or {}).get("pre_close")
    return planned is not None and pre_close is not None and planned < pre_close


def _hit_c4(row: Optional[Dict[str, Any]]) -> bool:
    """C4 回调大红机械层不做:`close>ma20`(回调态)且 `ret_1d≥5%`。三个量任一缺失
    (数据不全)→ 不触发(不臆造)。"""
    if not row:
        return False
    close, ma20, ret_1d = row.get("close"), row.get("ma20"), row.get("ret_1d")
    if close is None or ma20 is None or ret_1d is None:
        return False
    return close > ma20 and ret_1d >= _C4_BIGRED_UP


def _evaluate_codes(row: Optional[Dict[str, Any]], recent_decision: Optional[Any]) -> List[str]:
    """一只候选当日触发的 exec_hint 码列表,**固定 C1→C4 顺序**(可 0~4 条——多条并存
    是允许的,例如 C1 与 C4 可能同时成立:C1 说"强票要动手就市价",C4 说"回调大红机械
    层不做",两者是"执行风格提示"与"另一机械层不入选"的不同维度,不互斥)。"""
    hits = {
        C1_STRONG_MARKET_ORDER: _hit_c1(row),
        C2_MILD_RED_LOW_VARIANCE: _hit_c2(row),
        C3_LOW_LIMIT_SELF_AWARE: _hit_c3(row, recent_decision),
        C4_NO_PULLBACK_BIGRED_MECHANICAL: _hit_c4(row),
    }
    return [code for code in _ALL_CODES if hits[code]]


def _latest_decision(ts_code: str, trade_date: date, db_path: Optional[Path]) -> Optional[Any]:
    """该 `ts_code` **在 `trade_date` 当天或之前**创建的最近一条 `decision_log` 行
    (任意状态,见 `_hit_c3` docstring),无则 `None`。

    **`trade_date` 截断是无前视偏差铁律的落地(§3.8「回测第 T 日任何计算都不得读到
    > T 的数据」)**:`build_report(trade_date, ...)` 支持历史回放(§2.6「喂历史=
    回测、喂今日=报告,同一套代码」)——若不按 `trade_date` 截断,重新生成某个历史
    交易日的报告时,`_latest_decision` 会捞到**该历史日之后**才创建的决策日志,让
    exec_hint 用到当时根本不存在的未来信息,是货真价实的前视偏差,不是"无伤大雅的
    展示层细节"。复用既有 `list_decisions(date_to=...)`(同 `GET /decisions` 端点的
    日期区间过滤),不重开一份日期过滤逻辑。

    **截断口径 = 北京日(v1.4 review 契约线 🟡-2 修)**:`created_at` 落库是 UTC,从前
    这条截断拿 UTC 日期比 —— 北京 **T+1 00:00–07:59**(盘前预注册的现实时段)创建的决策
    UTC 日期还是 T,于是 T 日回放看得见它,铁律漏了 8 小时。换算住
    `decision_log.created_at_cn_date`(唯一实现,与 ⑥-A 共用 `CN_TZ`)。

    `neckline.decision_log.list_decisions` 按 `created_at` 升序返回,取最后一项即
    "trade_date 当天或之前"里最近的一条。"""
    from neckline import decision_log

    rows = decision_log.list_decisions(
        ts_code=ts_code, date_to=trade_date.strftime("%Y%m%d"), db_path=db_path,
    )
    return rows[-1] if rows else None


# —— 批量装配(pipeline.py 报告生成期调用,`Candidate` 原地补 `exec_hints`)——————————

def attach_exec_hints(
    candidates: List[Candidate],
    trade_date: date,
    *,
    db_path: Optional[Path] = None,
) -> None:
    """给一批候选**原地**补 `Candidate.exec_hints`(plan §五 v1.4-⑤-A)。`pipeline.py::
    build_report` 在候选算好后调用一次。**零额外 parquet 读取**——C1/C2/C4 直接读
    `Candidate.raw`(候选生成时已装配好的 K4 特征面板行);C3 每只候选查一次该
    `ts_code` 最近一条 `decision_log`(SQLite 索引点查)。DB `k4_advisory.exec_hint`
    文字只读一次(四码共用同一份 `db_texts`),逐码各自判定 db/fallback 来源。

    `trade_date` **用于 C3 的无前视偏差截断**(见 `_latest_decision` docstring)——
    只看该票在 `trade_date` 当天或之前创建的决策日志,不让历史回放读到"未来"才存在
    的决策记录(§3.8「无前视偏差」铁律)。C1/C2/C4 不需要 `trade_date`(它们只读
    `Candidate.raw`,该行本身已经是 `trade_date` 当天的横截面,不存在跨日期问题)。
    """
    db_texts = _load_k4_exec_hint_texts(db_path)
    for c in candidates:
        row = c.raw or {}
        recent_decision = _latest_decision(c.ts_code, trade_date, db_path)
        codes = _evaluate_codes(row, recent_decision)
        hints: List[Dict[str, Any]] = []
        for code in codes:
            text, source = _hint_text_and_source(code, db_texts)
            hints.append(ExecHint(code=code, text=text, source=source).public_dict())
        c.exec_hints = hints


__all__ = [
    "C1_STRONG_MARKET_ORDER",
    "C2_MILD_RED_LOW_VARIANCE",
    "C3_LOW_LIMIT_SELF_AWARE",
    "C4_NO_PULLBACK_BIGRED_MECHANICAL",
    "ExecHint",
    "attach_exec_hints",
]
