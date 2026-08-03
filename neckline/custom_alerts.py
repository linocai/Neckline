"""自然语言临时提醒的**结构化规则 + 台账**(plan §五 V2-⑪-C;蓝图 5.6)。

**分工(⑪-C 定死,三段互不越界)**:

    LLM  只负责**理解**  →  `neckline/llm/nl_alert.py`(自然语言 → 一份候选规则)
    本模块 负责**校验、留痕、台账**(LLM 给的东西先过白名单才准落库)
    哨兵 负责**执行**    →  `neckline/sentinel/custom.py`(只读 `rule_json`,逐拍判)

**⛔ LLM 产出的自由文本永不进哨兵判据**(§2.8-C 第 2 条):`nl_text` 只留痕用户
原话,哨兵一个字都不看;它只读 `rule_json`,而 `rule_json` 的每一个键、每一个取值
都必须先过本模块的 `normalize_rule()` 白名单。模型多写一个字段 / 编一个不存在的
指标 / 给一个荒唐的阈值,统统在这一关被拒,**不会静默流进执行层**。

**⛔ 永不自动交易**(§3.8 + ⑪-C 安全要求):本模块与 `sentinel/custom.py` 都不含
任何下单 / 撤单 / 改止损路径(守门单测 grep 全模块零命中)。确认卡上那句「只通知、
不自动交易」是承诺,也是这两个模块的物理事实。

**安全四条(蓝图 5.6 原文,逐条落在代码里)**:

    1. 相同提醒去重 —— `find_duplicate()`:同一标的 + 规范化后逐字节相同的规则,
       已有 active 行就不许再建(调用方映射 409)。
    2. 默认首次命中后不重复轰炸 —— `max_fires` 默认 1。
    3. 临时规则收盘自动失效,除非显式长期有效 —— `persist=0` 且 `expires_at` 为空
       时,**有效期止于创建当日 15:00(北京时间)**;见 `effective_expiry()`。
    4. 行情延迟 / 数据中断必须明确提示 —— `QUOTE_DELAY_DISCLOSURE` 是**确认卡的
       必选项**(`build_confirmation_card` 恒带,没有"省略"这个选项),命中推送时
       再说一遍。

**时区与收盘时刻唯一源 = `neckline.calendar`**(`CN_TZ` / `MARKET_CLOSE_TIME`),
本模块不自己写 `timezone(timedelta(hours=8))` 或 `time(15, 0)`(CLAUDE.md 定案)。
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from neckline.calendar import CN_TZ, MARKET_CLOSE_TIME
from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════
# 规则 schema(**哨兵的唯一判据形状**;改这里 = 改契约,要 bump 版本串)
# ══════════════════════════════════════════════════════════════════════════

RULE_SCHEMA_VERSION = "nl_alert_rule_v1"

# —— 首批条件集(蓝图 5.6 逐条:价格 / 涨跌幅 / 相对成本 / 相对日内高点 / 量能 /
#    大盘或篮子条件的组合)。⛔ 加指标 = 加执行器分支 + 加白名单 + 加单测,不许只改一头。
METRIC_PRICE = "price"                    # 现价(元)
METRIC_CHG_PCT = "chg_pct"                # 相对昨收涨跌幅(小数,+0.05 = 涨 5%)
METRIC_VS_COST = "vs_cost"                # 相对**持仓成本**的盈亏比例(小数)
METRIC_FROM_DAY_HIGH = "from_day_high"    # 相对日内最高价的偏离,**恒 ≤ 0**
                                          #   (-0.03 = 已从日内高点回落 3%)
METRIC_VOLUME_RATIO = "volume_ratio"      # 量比(折算全天量 / 前5日均量)
METRIC_INDEX_CHG_PCT = "index_chg_pct"    # 大盘 / 指数涨跌幅(小数),`ref` 指定指数码
METRIC_BASKET_WEAK_RATIO = "basket_weak_ratio"  # 来源篮子里转弱成员占比(0~1)

# 需要一个具体标的才成立的指标(大盘级提醒里出现 → 拒收)。
_STOCK_SCOPED_METRICS = (
    METRIC_PRICE, METRIC_CHG_PCT, METRIC_VS_COST, METRIC_FROM_DAY_HIGH,
    METRIC_VOLUME_RATIO, METRIC_BASKET_WEAK_RATIO,
)

ALL_METRICS: Tuple[str, ...] = (
    METRIC_PRICE, METRIC_CHG_PCT, METRIC_VS_COST, METRIC_FROM_DAY_HIGH,
    METRIC_VOLUME_RATIO, METRIC_INDEX_CHG_PCT, METRIC_BASKET_WEAK_RATIO,
)

METRIC_LABEL: Dict[str, str] = {
    METRIC_PRICE: "现价",
    METRIC_CHG_PCT: "当日涨跌幅",
    METRIC_VS_COST: "相对持仓成本盈亏",
    METRIC_FROM_DAY_HIGH: "相对日内高点",
    METRIC_VOLUME_RATIO: "量比",
    METRIC_INDEX_CHG_PCT: "指数涨跌幅",
    METRIC_BASKET_WEAK_RATIO: "同篮成员转弱占比",
}

# 百分比口径的指标(展示成 % / 校验区间用)。
_PCT_METRICS = (METRIC_CHG_PCT, METRIC_VS_COST, METRIC_FROM_DAY_HIGH,
                METRIC_INDEX_CHG_PCT, METRIC_BASKET_WEAK_RATIO)

OP_GE = ">="
OP_LE = "<="
OP_GT = ">"
OP_LT = "<"
ALL_OPS: Tuple[str, ...] = (OP_GE, OP_LE, OP_GT, OP_LT)
OP_LABEL: Dict[str, str] = {OP_GE: "≥", OP_LE: "≤", OP_GT: ">", OP_LT: "<"}

LOGIC_ALL = "all"
LOGIC_ANY = "any"
ALL_LOGICS: Tuple[str, ...] = (LOGIC_ALL, LOGIC_ANY)

# 一条提醒最多几个条件:**组合提醒**要支持(蓝图 5.6 明写),但不允许写成一份小程序
# ——超过这个数就不是"临时提醒"而是"策略",该走策略包那条线。
MAX_CONDITIONS = 5

DEFAULT_MAX_FIRES = 1        # 蓝图安全要求 2:默认首次命中后不重复轰炸
DEFAULT_COOLDOWN_SECONDS = 0

STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"
ALL_STATUSES: Tuple[str, ...] = (STATUS_ACTIVE, STATUS_EXPIRED, STATUS_CANCELLED)

# 行情延迟 / 数据中断披露(**确认卡必选项**,蓝图 5.6 安全要求第 4 条)。措辞里必须
# 同时说清三件事:数据是延迟的、判定是逐拍的(不是逐笔)、源中断时会漏判且不补判。
QUOTE_DELAY_DISCLOSURE = (
    "行情来自免费实时源(新浪 / 腾讯),**有延迟**且非逐笔;哨兵每分钟判一拍,"
    "两拍之间的瞬时价格不会被捕捉。数据源中断时该拍不判、事后也不补判 —— "
    "本提醒是尽力而为的辅助,不能当成成交保证。"
)
NO_AUTO_TRADE_DISCLOSURE = "只通知,不自动交易:系统永不代下单 / 撤单 / 改止损。"

_EPS = 1e-9


class RuleValidationError(ValueError):
    """规则不合白名单。消息面向用户可读(会经 HTTP 422 原样回给客户端)。"""


# ══════════════════════════════════════════════════════════════════════════
# 规则规范化 / 校验
# ══════════════════════════════════════════════════════════════════════════

def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RuleValidationError(f"{what} 必须是数字,收到 {v!r}")
    f = float(v)
    if math.isnan(f) or math.isinf(f):
        raise RuleValidationError(f"{what} 必须是有限数字,收到 {v!r}")
    return f


def _check_range(metric: str, value: float) -> None:
    if metric == METRIC_PRICE and value <= 0:
        raise RuleValidationError("价格阈值必须为正数")
    if metric == METRIC_VOLUME_RATIO and not (0 < value <= 100):
        raise RuleValidationError("量比阈值须落在 (0, 100]")
    if metric == METRIC_BASKET_WEAK_RATIO and not (0 <= value <= 1):
        raise RuleValidationError("同篮转弱占比须落在 [0, 1]")
    if metric in (METRIC_CHG_PCT, METRIC_VS_COST, METRIC_INDEX_CHG_PCT) and not (-1 <= value <= 1):
        raise RuleValidationError(f"{METRIC_LABEL[metric]} 阈值须是小数比例(如 -0.05 表示 −5%),落在 [-1, 1]")
    if metric == METRIC_FROM_DAY_HIGH and not (-1 <= value <= 0):
        raise RuleValidationError("相对日内高点是**非正**的小数(-0.03 = 从高点回落 3%),须落在 [-1, 0]")


def normalize_rule(rule: Mapping[str, Any], *, ts_code: Optional[str]) -> Dict[str, Any]:
    """把一份候选规则(LLM 产出 / 手填表单产出)**校验 + 规范化**成哨兵能吃的形状。

    规范化 = 只保留白名单里的键、按固定顺序排、数值转 float —— 使「相同的提醒」在
    去重时能逐字节比较(`canonical_rule_text`),不受键序 / 多余键 / 整数浮点写法影响。

    不合规一律 `RuleValidationError`,**⛔ 不做"贴心修正"**:模型把 `-5` 当成 −5%
    写进来,我们不能替它猜成 `-0.05`(猜错了就是一条完全不同的提醒)。"""
    if not isinstance(rule, Mapping):
        raise RuleValidationError("规则必须是一个对象")
    logic = str(rule.get("logic") or LOGIC_ALL)
    if logic not in ALL_LOGICS:
        raise RuleValidationError(f"logic 只能是 {ALL_LOGICS},收到 {logic!r}")
    raw_conds = rule.get("conditions")
    if not isinstance(raw_conds, Sequence) or isinstance(raw_conds, (str, bytes)) or not raw_conds:
        raise RuleValidationError("conditions 必须是非空数组")
    if len(raw_conds) > MAX_CONDITIONS:
        raise RuleValidationError(f"一条提醒最多 {MAX_CONDITIONS} 个条件,收到 {len(raw_conds)} 个")
    conds: List[Dict[str, Any]] = []
    for i, c in enumerate(raw_conds):
        if not isinstance(c, Mapping):
            raise RuleValidationError(f"第 {i + 1} 个条件必须是对象")
        metric = str(c.get("metric") or "")
        if metric not in ALL_METRICS:
            raise RuleValidationError(f"第 {i + 1} 个条件的 metric 不在白名单:{metric!r};合法取值 {ALL_METRICS}")
        op = str(c.get("op") or "")
        if op not in ALL_OPS:
            raise RuleValidationError(f"第 {i + 1} 个条件的 op 不在白名单:{op!r};合法取值 {ALL_OPS}")
        value = _num(c.get("value"), f"第 {i + 1} 个条件的 value")
        _check_range(metric, value)
        if metric in _STOCK_SCOPED_METRICS and not ts_code:
            raise RuleValidationError(
                f"{METRIC_LABEL[metric]} 需要一个具体标的,但这条提醒是大盘级(未指定 ts_code)"
            )
        out: Dict[str, Any] = {"metric": metric, "op": op, "value": value}
        if metric == METRIC_INDEX_CHG_PCT:
            ref = str(c.get("ref") or "").strip()
            if not ref:
                raise RuleValidationError("指数涨跌幅条件必须给 ref(指数代码,如 000001.SH)")
            out["ref"] = ref.upper()
        if metric == METRIC_BASKET_WEAK_RATIO:
            ref_bid = c.get("ref_basket_id")
            if ref_bid is not None:
                out["ref_basket_id"] = int(ref_bid)
        conds.append(out)
    return {"schema_version": RULE_SCHEMA_VERSION, "logic": logic, "conditions": conds}


def canonical_rule_text(rule: Mapping[str, Any]) -> str:
    """规范化规则的**确定性文本**(去重比较用;`sort_keys` + 紧凑分隔符)。"""
    return json.dumps(rule, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fmt_value(metric: str, value: float) -> str:
    if metric in _PCT_METRICS:
        return f"{value:+.2%}" if metric != METRIC_BASKET_WEAK_RATIO else f"{value:.0%}"
    if metric == METRIC_PRICE:
        return f"{value:.2f} 元"
    return f"{value:g}"


def describe_condition(cond: Mapping[str, Any]) -> str:
    metric = str(cond["metric"])
    label = METRIC_LABEL[metric]
    if metric == METRIC_INDEX_CHG_PCT:
        label = f"{cond.get('ref', '')} 涨跌幅"
    return f"{label} {OP_LABEL[str(cond['op'])]} {_fmt_value(metric, float(cond['value']))}"


def describe_rule(rule: Mapping[str, Any]) -> str:
    """人读描述(确认卡 / 命中推送用)。**由结构化规则生成,不是 LLM 自由文本**——
    用户看到的那句话与哨兵真正执行的判据是同一份东西,不存在"说的和做的不一样"。"""
    conds = [describe_condition(c) for c in rule.get("conditions", [])]
    joiner = " 且 " if str(rule.get("logic", LOGIC_ALL)) == LOGIC_ALL else " 或 "
    return joiner.join(conds)


# ══════════════════════════════════════════════════════════════════════════
# 行 / 确认卡
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CustomAlert:
    id: int
    ts_code: Optional[str]           # None = 大盘级
    nl_text: str                     # 用户原话(留痕;**哨兵不看**)
    rule: Dict[str, Any]
    active_from: Optional[str]       # 'HH:MM'
    active_to: Optional[str]
    expires_at: Optional[str]        # ISO8601(北京时间);None 见 effective_expiry
    persist: bool
    cooldown_seconds: int
    max_fires: int                   # 0 = 不限次(仍受 cooldown 约束)
    fired_count: int
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ConfirmationCard:
    """⑪-C 的**七项确认卡**(缺一不可;⛔ 后两项是固定文案,不接受调用方覆盖)。"""

    subject: str                    # ① 标的
    condition: str                  # ② 触发条件与方向
    active_window: str              # ③ 生效时间
    notify_limit: str               # ④ 通知次数 / 冷却
    expiry: str                     # ⑤ 到期时间
    quote_delay_disclosure: str     # ⑥ 行情延迟或数据中断披露(**必选**)
    no_auto_trade: str              # ⑦ 只通知不自动交易
    rule: Dict[str, Any] = field(default_factory=dict)   # 待确认的结构化规则原文

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _fmt_window(active_from: Optional[str], active_to: Optional[str]) -> str:
    if not active_from and not active_to:
        return "全交易时段(09:30–15:00)"
    return f"{active_from or '开盘'} 起至 {active_to or '收盘'}"


def _fmt_notify_limit(max_fires: int, cooldown_seconds: int) -> str:
    times = "命中后只通知一次" if max_fires == 1 else (
        "不限次数" if max_fires <= 0 else f"最多通知 {max_fires} 次")
    if cooldown_seconds > 0:
        return f"{times},两次通知至少间隔 {cooldown_seconds} 秒"
    return times


def _fmt_expiry(persist: bool, expires_at: Optional[str]) -> str:
    if expires_at:
        return f"{expires_at} 到期" + ("(长期有效,到该时刻止)" if persist else "")
    if persist:
        return "长期有效,直到你手动取消"
    return "**今日收盘(15:00)自动失效**(未设为长期有效)"


def build_confirmation_card(
    *, rule: Mapping[str, Any], ts_code: Optional[str], name: Optional[str] = None,
    active_from: Optional[str] = None, active_to: Optional[str] = None,
    expires_at: Optional[str] = None, persist: bool = False,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS, max_fires: int = DEFAULT_MAX_FIRES,
) -> ConfirmationCard:
    """拼确认卡(⑪-C:**用户确认后才落库**;本函数只拼,不写库)。

    ⑥⑦ 两项是**固定文案、恒出现** —— 蓝图 5.6 把「行情延迟或数据中断必须明确提示」
    与「只通知不自动交易」列为安全要求,不是可选装饰。"""
    if ts_code:
        subject = f"{name}({ts_code})" if name else ts_code
    else:
        subject = "大盘 / 指数(未绑定个股)"
    return ConfirmationCard(
        subject=subject,
        condition=describe_rule(rule),
        active_window=_fmt_window(active_from, active_to),
        notify_limit=_fmt_notify_limit(max_fires, cooldown_seconds),
        expiry=_fmt_expiry(persist, expires_at),
        quote_delay_disclosure=QUOTE_DELAY_DISCLOSURE,
        no_auto_trade=NO_AUTO_TRADE_DISCLOSURE,
        rule=dict(rule),
    )


# ══════════════════════════════════════════════════════════════════════════
# 有效期 / 生效窗
# ══════════════════════════════════════════════════════════════════════════

def _parse_iso(text: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def created_trade_day(alert: CustomAlert) -> Optional[date]:
    """这条提醒是**哪天(北京时间)**建的。`created_at` 由本模块以 UTC ISO 落库
    (同项目各 store 的 `_now()` 惯例),这里归一到北京时间再取日期 —— 收盘失效讲的
    是**市场的那一天**,不是 UTC 的那一天(跨零点会差一天)。"""
    dt = _parse_iso(alert.created_at)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CN_TZ).date()


def effective_expiry(alert: CustomAlert) -> Optional[datetime]:
    """这条提醒的**实际失效时刻**(北京时间 aware datetime);`None` = 不自动失效。

    · 显式 `expires_at` → 以它为准(解析不出 → 当作没设,记 warning,**不猜**)。
    · `persist=1` 且无 `expires_at` → `None`(长期有效,只能由用户取消)。
    · `persist=0` 且无 `expires_at` → **创建当日 15:00(北京时间)**,即蓝图
      「临时规则收盘自动失效」的落地(DDL 注释写的就是这个语义)。"""
    if alert.expires_at:
        dt = _parse_iso(alert.expires_at)
        if dt is None:
            logger.warning("[custom_alerts] id=%s 的 expires_at 解析失败(%r),按未设置处理",
                           alert.id, alert.expires_at)
        else:
            return dt if dt.tzinfo else dt.replace(tzinfo=CN_TZ)
    if alert.persist:
        return None
    day = created_trade_day(alert)
    if day is None:
        return None
    return datetime.combine(day, MARKET_CLOSE_TIME, tzinfo=CN_TZ)


def is_expired_at(alert: CustomAlert, now_cn: datetime) -> bool:
    exp = effective_expiry(alert)
    return exp is not None and now_cn >= exp


def _parse_hhmm(text: Optional[str]) -> Optional[time]:
    if not text:
        return None
    try:
        hh, mm = str(text).split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError):
        logger.warning("[custom_alerts] 生效窗 %r 解析失败,按未设置处理", text)
        return None


def in_active_window(alert: CustomAlert, now_cn: datetime) -> bool:
    """当前时刻是否落在生效窗内(`active_from` ≤ now < `active_to`;两端可缺省)。
    解析不出的端点按**未设置**处理(不因为一个写坏的 'HH:MM' 就让提醒永不生效)。"""
    t = now_cn.time()
    start = _parse_hhmm(alert.active_from)
    end = _parse_hhmm(alert.active_to)
    if start is not None and t < start:
        return False
    if end is not None and t >= end:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
# 台账 CRUD(`custom_alerts` 表;三律 = 可改,用户自己的规则)
# ══════════════════════════════════════════════════════════════════════════

_COLS = ("id, ts_code, nl_text, rule_json, active_from, active_to, expires_at, persist, "
         "cooldown_seconds, max_fires, fired_count, status, created_at, updated_at")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_alert(r: sqlite3.Row) -> CustomAlert:
    try:
        rule = json.loads(r[3]) if r[3] else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("[custom_alerts] id=%s 的 rule_json 解析失败,视为空规则(哨兵将跳过)", r[0])
        rule = {}
    return CustomAlert(
        id=int(r[0]), ts_code=r[1], nl_text=str(r[2]), rule=rule,
        active_from=r[4], active_to=r[5], expires_at=r[6], persist=bool(r[7]),
        cooldown_seconds=int(r[8]), max_fires=int(r[9]), fired_count=int(r[10]),
        status=str(r[11]), created_at=str(r[12]), updated_at=str(r[13]),
    )


def create_alert(
    *, rule: Mapping[str, Any], nl_text: str, ts_code: Optional[str] = None,
    active_from: Optional[str] = None, active_to: Optional[str] = None,
    expires_at: Optional[str] = None, persist: bool = False,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS, max_fires: int = DEFAULT_MAX_FIRES,
    db_path: Optional[Path] = None,
) -> CustomAlert:
    """落一条提醒(**用户已在确认卡上确认之后**才该调到这里)。

    `rule` 会再过一遍 `normalize_rule` —— 即便调用方号称已经校验过:落库那一刻是
    最后一道闸,过了这道闸的东西哨兵会当judge使,不能靠"上游应该验过了"。"""
    code = (ts_code or "").strip().upper() or None
    norm = normalize_rule(rule, ts_code=code)
    if int(max_fires) < 0:
        raise RuleValidationError("max_fires 不能为负(0 = 不限次)")
    if int(cooldown_seconds) < 0:
        raise RuleValidationError("cooldown_seconds 不能为负")
    init_schema(db_path)
    now = _now_utc()
    with connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO custom_alerts (ts_code, nl_text, rule_json, active_from, active_to, "
            "expires_at, persist, cooldown_seconds, max_fires, fired_count, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (code, nl_text, canonical_rule_text(norm), active_from, active_to, expires_at,
             1 if persist else 0, int(cooldown_seconds), int(max_fires), STATUS_ACTIVE, now, now),
        )
        new_id = int(cur.lastrowid)
        row = conn.execute(f"SELECT {_COLS} FROM custom_alerts WHERE id=?", (new_id,)).fetchone()
    return _row_to_alert(row)


def get_alert(alert_id: int, db_path: Optional[Path] = None) -> Optional[CustomAlert]:
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT {_COLS} FROM custom_alerts WHERE id=?", (int(alert_id),)).fetchone()
    return _row_to_alert(row) if row else None


def list_alerts(
    *, status: Optional[str] = None, ts_code: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[CustomAlert]:
    """列提醒(按 id 升序,**确定性**)。

    ⚠ **只读,不改任何行的 status** —— 到期行在这里仍可能显示 `active`。判断"实际
    上还生不生效"用 `is_expired_at()`(展示层据此标「已过期」);真正把 status 翻成
    `expired` 的是哨兵那一拍(`expire_due`)。读路径不写库是本项目一贯姿势。"""
    init_schema(db_path)
    sql = f"SELECT {_COLS} FROM custom_alerts"
    where: List[str] = []
    args: List[Any] = []
    if status:
        where.append("status=?")
        args.append(status)
    if ts_code:
        where.append("ts_code=?")
        args.append(ts_code.strip().upper())
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    with connection(db_path) as conn:
        rows = conn.execute(sql, tuple(args)).fetchall()
    return [_row_to_alert(r) for r in rows]


def find_duplicate(
    rule: Mapping[str, Any], ts_code: Optional[str], *, db_path: Optional[Path] = None
) -> Optional[CustomAlert]:
    """已有一条**规则逐字节相同、标的相同**的 active 提醒?(蓝图安全要求 1)

    比的是 `canonical_rule_text` 而不是用户原话 —— 「跌到 15 通知我」和「15 块提醒
    一下」是同一条提醒,不该建两遍;反过来,措辞相同但阈值不同就是两条不同的提醒。"""
    code = (ts_code or "").strip().upper() or None
    target = canonical_rule_text(normalize_rule(rule, ts_code=code))
    for a in list_alerts(status=STATUS_ACTIVE, db_path=db_path):
        if a.ts_code == code and canonical_rule_text(a.rule) == target:
            return a
    return None


def update_alert(
    alert_id: int, *,
    rule: Optional[Mapping[str, Any]] = None,
    nl_text: Optional[str] = None,
    active_from: Optional[str] = None, active_to: Optional[str] = None,
    expires_at: Optional[str] = None, persist: Optional[bool] = None,
    cooldown_seconds: Optional[int] = None, max_fires: Optional[int] = None,
    reset_fired: bool = False,
    db_path: Optional[Path] = None,
) -> Optional[CustomAlert]:
    """局部更新(用户「改一下」那条路径)。未传的字段不动;不存在 → `None`。

    `reset_fired=True` 时把 `fired_count` 归零 —— 改过条件的提醒理应重新有一次
    命中机会,但**要不要重置由调用方显式决定**,不在这里替用户拍板。"""
    cur = get_alert(alert_id, db_path=db_path)
    if cur is None:
        return None
    sets: List[str] = []
    args: List[Any] = []
    if rule is not None:
        norm = normalize_rule(rule, ts_code=cur.ts_code)
        sets.append("rule_json=?")
        args.append(canonical_rule_text(norm))
    if nl_text is not None:
        sets.append("nl_text=?")
        args.append(nl_text)
    for col, val in (("active_from", active_from), ("active_to", active_to),
                     ("expires_at", expires_at)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    if persist is not None:
        sets.append("persist=?")
        args.append(1 if persist else 0)
    if cooldown_seconds is not None:
        if int(cooldown_seconds) < 0:
            raise RuleValidationError("cooldown_seconds 不能为负")
        sets.append("cooldown_seconds=?")
        args.append(int(cooldown_seconds))
    if max_fires is not None:
        if int(max_fires) < 0:
            raise RuleValidationError("max_fires 不能为负(0 = 不限次)")
        sets.append("max_fires=?")
        args.append(int(max_fires))
    if reset_fired:
        sets.append("fired_count=0")
    if not sets:
        return cur
    sets.append("updated_at=?")
    args.append(_now_utc())
    args.append(int(alert_id))
    with connection(db_path) as conn:
        conn.execute(f"UPDATE custom_alerts SET {', '.join(sets)} WHERE id=?", tuple(args))
    return get_alert(alert_id, db_path=db_path)


def set_status(alert_id: int, status: str, db_path: Optional[Path] = None) -> bool:
    if status not in ALL_STATUSES:
        raise RuleValidationError(f"status 只能是 {ALL_STATUSES},收到 {status!r}")
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE custom_alerts SET status=?, updated_at=? WHERE id=?",
            (status, _now_utc(), int(alert_id)),
        )
        return cur.rowcount > 0


def cancel_alert(alert_id: int, db_path: Optional[Path] = None) -> bool:
    """用户取消(蓝图:随时可查询、修改、取消)。**不删行** —— 台账留痕,
    `cancelled` 与 `expired` 两种下场分得开。"""
    return set_status(alert_id, STATUS_CANCELLED, db_path=db_path)


def mark_fired(alert_id: int, db_path: Optional[Path] = None) -> int:
    """命中一次:`fired_count += 1`,返回累计次数。**只有真推送出去才该调**。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        conn.execute(
            "UPDATE custom_alerts SET fired_count=fired_count+1, updated_at=? WHERE id=?",
            (_now_utc(), int(alert_id)),
        )
        row = conn.execute("SELECT fired_count FROM custom_alerts WHERE id=?", (int(alert_id),)).fetchone()
    return int(row[0]) if row else 0


def expire_due(now_cn: datetime, db_path: Optional[Path] = None) -> List[int]:
    """把已过实际失效时刻的 active 行翻成 `expired`,返回被翻的 id 列表(幂等)。

    由哨兵那一拍调用(读路径不写库)。**收盘自动失效**这条安全要求就落在这里:
    `persist=0` 且没显式设到期的提醒,过了创建当日 15:00 就不再有效。"""
    flipped: List[int] = []
    for a in list_alerts(status=STATUS_ACTIVE, db_path=db_path):
        if is_expired_at(a, now_cn):
            if set_status(a.id, STATUS_EXPIRED, db_path=db_path):
                flipped.append(a.id)
    return flipped


__all__ = [
    "RULE_SCHEMA_VERSION", "ALL_METRICS", "METRIC_LABEL", "ALL_OPS", "OP_LABEL",
    "ALL_LOGICS", "LOGIC_ALL", "LOGIC_ANY", "MAX_CONDITIONS",
    "METRIC_PRICE", "METRIC_CHG_PCT", "METRIC_VS_COST", "METRIC_FROM_DAY_HIGH",
    "METRIC_VOLUME_RATIO", "METRIC_INDEX_CHG_PCT", "METRIC_BASKET_WEAK_RATIO",
    "OP_GE", "OP_LE", "OP_GT", "OP_LT",
    "DEFAULT_MAX_FIRES", "DEFAULT_COOLDOWN_SECONDS",
    "STATUS_ACTIVE", "STATUS_EXPIRED", "STATUS_CANCELLED", "ALL_STATUSES",
    "QUOTE_DELAY_DISCLOSURE", "NO_AUTO_TRADE_DISCLOSURE",
    "RuleValidationError", "CustomAlert", "ConfirmationCard",
    "normalize_rule", "canonical_rule_text", "describe_rule", "describe_condition",
    "build_confirmation_card", "effective_expiry", "created_trade_day",
    "is_expired_at", "in_active_window",
    "create_alert", "get_alert", "list_alerts", "find_duplicate", "update_alert",
    "set_status", "cancel_alert", "mark_fired", "expire_due",
]
