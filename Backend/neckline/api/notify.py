"""APNs 推送编排(plan §五 **V2-⑪-B**「通知三级」重构;前身 = v1.1/v1.2-A2/v1.3-②
的「白名单六类」,🔴 高危区)。

**V2 结构 = 三级 category × N 个 kind**(D5 拍板):

    · **三个 APNs category** —— `NKIMMEDIATE`(立即)/ `NKIMPORTANT`(重要不紧急)/
      `NKDIGEST`(盘后汇总)。category 决定**怎么响**(锁屏呈现 / 打扰级别)。
    · **每条事件带 `kind`** —— 决定**响不响**:`app_settings.push_kinds` 里按 kind
      配的开关。**⛔ 不按 category 配开关**:那会连坐(关掉「重要不紧急」= 同时
      关掉板块、大盘、时间退出三件完全不同的事;V1 拆 `HOLDINGALERT` 就是被这个
      坑逼出来的)。
    · **kind → level → category 的唯一源 = `neckline/notify_kinds.py`**,本模块不
      自己判归属、不散抄字面量。

**唯一扇出入口 = `push_event()`**。下面那些 `push_*` 具名函数**只负责措辞**(把
「发生了什么」拼成一句人话),扇出、查开关、查 APNs 配置、遍历设备全部交给
`push_event` —— 白名单的保证从「本模块只暴露六个函数」升级成「**本模块只有一条
扇出路径,而它只接受 `ALL_KINDS` 里的 kind**」(未登记的 kind 直接抛 `ValueError`,
不给第 12 类事件留一条静默的推送路径)。守门单测按 `__all__` + kind 集合结构性锁死。

**文案纪律(⑪-B 原文)**:只回答三件事 —— **发生了什么、触碰了哪条计划、要不要
打开 APP**;详细解释点开再给。⛔ 不在推送正文里塞分析、不给收益预测、不写「建议
买入」类表述(§2.8-B 语义红线)。

**系统永不代交易动作**(§3.8):本模块只发文字通知,绝不下单 / 撤单 / 改止损。

每类推送:先查该 kind 开关(关 → 直接跳过)→ 查 APNs 配置 → 遍历 `devices` 表所有
token → 逐个 `apns.send_push`(transport 可注入,单测免真连 Apple)。任何设备失败
只记日志,不拖累其它设备 / 主流程(推送是尽力而为,失败绝不能反过来打断报告落库
或哨兵主循环)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from neckline import notify_kinds
from neckline.api.stores import list_device_tokens
from neckline.notify_kinds import (
    KIND_BASKET_PEERS_WEAK,
    KIND_CIRCUIT,
    KIND_CUSTOM_ALERT,
    KIND_D5EXIT,
    KIND_HOLDING_ALERT,
    KIND_HOLDING_DECOUPLED,
    KIND_MARKET_SHOCK,
    KIND_PRECALL,
    KIND_REPORT_READY,
    KIND_RETREAT,
    KIND_SECTOR_BID_FADE,
    KIND_SECTOR_DIVE,
    KIND_STOP_APPROACH,
    KIND_TAKE_PROFIT,
)
from neckline.push import apns
from neckline.settings_store import push_kind_enabled

logger = logging.getLogger(__name__)

# 「要不要打开 APP」那一句的固定收尾(三件事里的第三件;各处措辞统一,便于用户
# 一眼分辨「这条要动手」还是「这条看看就行」)。
_OPEN_APP_NOW = "点开 APP 核对。"
_OPEN_APP_LATER = "有空点开 APP 看详情。"


@dataclass
class NotifyOutcome:
    sent: int = 0
    failed: int = 0
    skipped_reason: str = ""     # 非空 = 未推送的原因(开关关 / 无设备 / 无 APNs 配置)
    kind: str = ""               # 本次事件的 kind(便于调用方落账 / 单测断言)
    level: str = ""              # 三级之一(kind 决定,不由调用方指定)


def _fanout(
    title: str, body: str, *, category: str, custom: Optional[dict],
    db_path: Optional[Path], transport: Optional[Any],
) -> NotifyOutcome:
    tokens = list_device_tokens(db_path=db_path)
    if not tokens:
        return NotifyOutcome(skipped_reason="no_devices")
    out = NotifyOutcome()
    for tok in tokens:
        try:
            res = apns.send_push(tok, title, body, category=category, custom=custom, transport=transport)
        except Exception as e:  # noqa: BLE001  send_push 内已兜底,这里双保险
            logger.warning("APNs 推送异常(token 尾4=%s):%s", tok[-4:] if tok else "", e)
            out.failed += 1
            continue
        if res.ok:
            out.sent += 1
        else:
            out.failed += 1
            logger.warning("APNs 推送失败(status=%s reason=%s)", res.status, res.reason)
    return out


def push_event(
    kind: str, title: str, body: str, *,
    custom_extra: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """**本模块唯一的扇出路径**(⑪-B)。`kind` 决定「响不响」(按 kind 开关)与
    「怎么响」(kind → level → category);调用方**不能**自己指定 category —— 那等于
    绕开三级归属表自定义打扰级别。

    未登记的 kind → `ValueError`(`notify_kinds.level_of` 抛):白名单不开后门,拼错
    的串宁可炸在调用点,也不要静默变成一条真推送。

    `custom` 载荷恒带 `kind` / `level` 两键(客户端按 `kind` 路由到具体页面 —— 三个
    category 已不足以区分去哪儿,这是 V2 与 V1「category 即路由」的关键差异)。"""
    level = notify_kinds.level_of(kind)           # 未登记 → 抛,见 docstring
    category = notify_kinds.CATEGORY_OF_LEVEL[level]
    # ⛔ V2.4.0 P0:退役 kind 一律拒发(`notify_kinds.RETIRED_KINDS` 单一源)。闸门放在
    # **唯一扇出路径**上,而不是逐个措辞函数里 —— 措辞函数漏掉一个就是一条真推送。
    # ⚠ 排在开关闸**之前**:退役与用户把开关打开与否无关,开着也发不出去。
    if kind in notify_kinds.RETIRED_KINDS:
        return NotifyOutcome(skipped_reason=f"kind_retired:{kind}", kind=kind, level=level)
    if not push_kind_enabled(kind, db_path=db_path):
        return NotifyOutcome(skipped_reason=f"kind_off:{kind}", kind=kind, level=level)
    if not apns.settings.has_apns_config:
        return NotifyOutcome(skipped_reason="no_apns_config", kind=kind, level=level)
    custom: Dict[str, Any] = {"kind": kind, "level": level}
    if custom_extra:
        custom.update(custom_extra)
    out = _fanout(title, body, category=category, custom=custom,
                  db_path=db_path, transport=transport)
    out.kind = kind
    out.level = level
    return out


# ══════════════════════════════════════════════════════════════════════════
# 措辞层(V1 六类迁移):每个函数只拼文案 + 挑 kind,扇出一律交给 push_event
# ══════════════════════════════════════════════════════════════════════════

def push_report_ready(
    trade_date_disp: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """16:35 盘后报告就绪(kind=`report_ready`,**盘后汇总**级)。"""
    return push_event(
        KIND_REPORT_READY,
        "今日盘后报告已生成",
        # ⚠ V2.1-⑦:「今日篮子」→「今日选股」跟着板块改名(客户端 `AppTab.baskets.title`)。
        # **只改文案** —— kind / 开关 / extra 一律不动(新增推送 kind 须用户拍板,
        # 改一句话不触发那条纪律)。
        f"{trade_date_disp} 盘后报告与今日选股已就绪。{_OPEN_APP_LATER}",
        custom_extra={"tradeDate": trade_date_disp},
        db_path=db_path, transport=transport,
    )


def push_retreat_brake(
    reason: str, *, db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """⛔ **DEPRECATED(V2.4.0 P0 退役)—— 生产链零调用,恒被 `push_event` 拒发。**

    退潮红色刹车(kind=`retreat`,**立即**级)。`reason` = 退潮哨兵的刹车文案。

    **为什么留着这个函数**:① `KIND_RETREAT ∈ RETIRED_KINDS` 的拒发行为要有一条
    能真调到的路径去验(`tests/test_notify.py`);② 回滚绳的一部分(§3.14-A)。
    **唯一调用点 `api/app.py::_sentinel_loop` 已整段删除** —— P0.7 判据 #3 扫的就是
    「本函数在 `neckline/**` 里除自身定义与 `__all__` 外零引用」。
    """
    # ⛔ V2.4.0 P0:原文案(作废当日计划 + 停止开新仓)已按 P0.7 判据 #2 从全仓清除。
    # 本函数恒被 `push_event` 的退役闸拒发,这句话到不了任何人手机上。
    body = "退潮红色刹车(该能力已于 v2.4.0 退役)。" + (f" 依据:{reason}" if reason else "")
    return push_event(
        KIND_RETREAT, "退潮红色刹车", body + _OPEN_APP_NOW,
        db_path=db_path, transport=transport,
    )


# 熔断锁定期盘前提醒的固定措辞(= `sentinel/precall.py::CIRCUIT_LOCKED_PRECALL_NOTE`;
# 此处按字面量引用以免 notify → sentinel 重耦合,与 `_KIND_TIME_EXIT` 同惯例,
# 一致性由 `tests/test_notify.py` 结构性断言守护)。
# ⚠ **V2.2-⑤-B:`_CIRCUIT_LOCKED_NOTE`(「熔断中:今日只减不加」)已随熔断整体退役删除**。
# 连带后果**如实登记**(§八 第 19 项原文):那句「次日只减不加」此前是靠 9:26 汇总推送的
# **必发豁免**送到用户手机上的,**豁免一并取消** —— 以后平静的清晨就是真的没推送。


def push_precall_summary(
    counts: dict, *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """9:26 盘前校准汇总(kind=`precall`,**重要不紧急**级,plan v1.1-A.4)。`counts` =
    盘前校准四类判定的计数 dict(`gap_up` 买点变形 / `low_open` 开盘证伪 /
    `position_low_open` 持仓预警 / `auction` 竞价量能异常附注 / `member_ex_rights`
    疑似除权除息导致**今日核对不了**的成员数)。**盘前不产新票、不推荐买入**
    (§2.4 铁原则),只汇总「前晚计划被集合竞价作废/预警」的条数。

    `member_ex_rights`(判定线审计 🟡-2,2026-08-03)照 `auction` 的体例做附注:
    它既不是判定也不触发推送(缺键 → 0,老调用方零感知),但**「今天没核对」必须与
    「核对过、没异常」分得开**,否则用户会把沉默读成平安。

    ⚠ **V2.2-⑤-B:原来的「熔断锁定态」参数已删**(熔断整体退役,裁定 #8)。它原本让锁定期即便
    零判定也必发一条 —— **这条必发豁免随之取消**,汇总推送回归正常门槛
    (`PrecallResult.should_push_summary` = 有需要动作的判定才推)。**这是裁定 #8 的字面
    结果、不是遗漏**:平静的清晨从此真的没推送(§八 第 19 项已当面告知用户)。
    **纯提醒层**(§3.8):本函数只发文字,绝不代下单/撤单。"""
    n = int(counts.get("gap_up", 0))
    m = int(counts.get("low_open", 0))
    k = int(counts.get("position_low_open", 0))
    a = int(counts.get("auction", 0))
    x = int(counts.get("member_ex_rights", 0))
    body = f"集合竞价校准:{n} 只买点变形、{m} 只开盘证伪、{k} 只持仓止损预警"
    if a:
        body += f"(另 {a} 只竞价量能异常)"
    if x:
        body += f"(另 {x} 只疑似除权除息、冻结锚失效,今日未核对)"
    body += "。前晚计划按校准结果执行," + _OPEN_APP_NOW
    return push_event(
        KIND_PRECALL, "盘前校准提醒", body,
        db_path=db_path, transport=transport,
    )


def push_auction_summary(
    counts: dict, *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """9:26—9:29 **D1 集合竞价确认**汇总(V2.3.3-④,K8.md §二十)。

    🔴 **kind 复用 `KIND_PRECALL`,⛔ 不新开 kind**(2026-08-11 用户拍板):新 kind 要动
    `app_settings` 加列 + 迁移 + 设置屏,且 `ALL_KINDS` 是冻结元组、加一个要用户单独拍板。
    ⚠ **已知代价、如实登记**(§五 ⑨-B-4):9:26 盘前校准汇总与竞价确认汇总**共用一个
    开关** —— 用户关掉 `precall` 会**同时关掉两条**。这是那条拍板的**字面结果、不是遗漏**;
    要拆开需用户单独拍一个新 kind。

    `counts` 键:`confirm` / `neutral` / `veto` / `pending_explanation` /
    `hit_invalidation` / `llm_stage`(**唯一源 = `auction/pipeline.py::AuctionRunResult.
    counts`**)。⚠ `llm_stage` 是**字符串**混在 counts 里,措辞按它决定加不加那句
    「本次 LLM 未给出解释」—— 同 `push_precall_summary` 的 counts 位置。

    **推送门槛不在这里**(单一源是 `AuctionRunResult.should_push`):`_sentinel_loop`
    判完才调本函数。⛔ 不许"平静的早晨也发一条"。

    **纯提醒层**(§3.8):只发文字,绝不代下单。文案里 ⛔ 不得出现「建议买入 / 可以买」
    这类措辞 —— 系统只审计不代下单。
    """
    c = int(counts.get("confirm", 0))
    n = int(counts.get("neutral", 0))
    v = int(counts.get("veto", 0))
    h = int(counts.get("hit_invalidation", 0))
    stage = str(counts.get("llm_stage", "") or "")
    body = f"集合竞价确认:{c} 篮确认、{n} 篮中性、{v} 篮否决;{h} 只命中 D0 失效位。"
    if stage != "ok":
        body += "(本次 LLM 未给出解释,已按『待解释』记录)"
    # K8 §二十 逐字,**恒带**:竞价结论不是买入指令。
    body += "竞价结论只说明竞价反映出的信息,不等于买入指令。"
    return push_event(
        KIND_PRECALL, "集合竞价确认", body,
        db_path=db_path, transport=transport,
    )


# v1.3 两档时间退出状态码(= `PositionOut.timeExitState` 契约 / `sentinel/precall.py` 常量,
# 唯一源在那两处;此处按字面量引用以免 notify → precall 重耦合,与 CLOSE_REASON Literal 惯例同)。
_KIND_TIME_EXIT = "time_exit_next_day"
_KIND_HARD_CAP = "hard_cap_exit"


def push_d5_exit(
    name: str, code: str, d_count: int, *,
    kind: str = _KIND_TIME_EXIT, max_hold_effective: Optional[int] = None, two_tier: bool = False,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """时间退出(通知 kind=`d5exit`,**重要不紧急**级;plan v1.1-B.2 / v1.3-①-D 两档)。
    ⚠ 参数名 `kind` 是**时间退出状态码**(`time_exit_next_day`/`hard_cap_exit`),
    与通知 kind 是两回事,签名沿用 v1.1 不改(调用点已在 `_sentinel_loop`)。

    `d_count` = 现役 `max_hold_days`(不硬编 5;改 config 则随之)。**两档文案**:
      · `kind='hard_cap_exit'`:「D{n} 已达浮盈硬上限 D{k},按计划离场」(浮盈豁免单到 D15 硬退)。
      · `kind='time_exit_next_day'`:非浮盈单——`two_tier=True`(v1.3 章程激活)标「净浮盈 ≤0」;
        `two_tier=False`(K1 单档无条件时间退出)不标净浮盈(单档退出与浮亏浮盈无关,兜底 v1.1 文案)。
    浮盈豁免单(`profit_exempt`)**不推**本函数(它没到退出,只在客户端 D 徽标转 D{n}/D{15} 档)。"""
    disp = name or code
    if kind == _KIND_HARD_CAP:
        title = "浮盈硬上限时间退出"
        body = f"{disp} 今日 D{d_count} 已达浮盈硬上限 D{max_hold_effective},按计划离场。"
    elif two_tier:
        title = "时间退出"
        body = f"{disp} 今日 D{d_count} 时间退出日(净浮盈 ≤0),按计划离场。"
    else:
        title = "D5 时间退出"
        body = f"{disp} 今日 D{d_count} 时间退出日,按计划离场(时间退出是规则 v1 采纳纪律)。"
    return push_event(
        KIND_D5EXIT, title, body + _OPEN_APP_NOW,
        custom_extra={"code": code, "timeExitState": kind},
        db_path=db_path, transport=transport,
    )


def push_consecutive_stops_notice(
    count: int, *, ts_code: str = "", name: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """连续止损**纯提醒**(V2.2-⑤-B;前身 `push_circuit_breaker`,熔断已整体退役)。

    🔴 **裁定 #8 原话**:「**我不需要你替我做决定;这个程序永远是提醒 —— 连续三笔止损
    真的发生了,那也是提醒**」。故本函数的文案是**纯告知**:
      · ⛔ **禁指令词** —— 不许出现任何命令用户停手 / 限制仓位 / 解锁之类的祈使句
        (**逐词的黑名单唯一源 = 守门单测里的 `_BANNED` 元组**,见本段末行;
        ⛔ 别在这里抄第二份 —— 抄了就是两份口径,还会撞上 P0.7 判据 #2 的全仓文案扫描);
      · ⛔ 不暗示任何自动状态(没有锁、没有灰化、没有强制复盘);
      · 只陈述事实 + 诚实边界(「基于台账 N 笔已补录成交」,漏录则失灵)。
    守门单测 `tests/test_circuit.py` 按禁用词逐条扫这段文案。

    **kind 刻意仍用 `circuit`**(⑤-B 第 6 项):新增 kind 要用户拍板,而复用现有 kind 改
    文案不触发那条纪律;开关 `push_circuit` 原样保留(用户可关)。**纯提醒层**(§3.8):
    只发文字,绝不代下单/撤单/改止损。"""
    disp = (name or ts_code or "").strip()
    tail = f"(最近一笔:{disp})" if disp else ""
    body = (
        f"你最近连续 {count} 笔以止损离场{tail}。这是一条提醒,系统不改变任何设置、"
        f"也不替你做决定;基于台账 {count} 笔已补录成交,漏录则本提醒失灵。"
    )
    return push_event(
        KIND_CIRCUIT, "连续止损提醒", body + _OPEN_APP_NOW,
        custom_extra={"consecutiveStops": int(count), "code": ts_code},
        db_path=db_path, transport=transport,
    )


def push_holding_alert(
    name: str, code: str, hit_labels: List[str], *,
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """K4 持仓派发警报(kind=`holding_alert`,**重要不紧急**级 —— 三条强证据〔年线下
    涨停 / 放量大阳派发 / 换手>10%〕全属蓝图 5.5「放量异动」一族,不是止损那种秒级
    事件;plan §五 v1.3-②-C)。每只触发**强价量证据**命中的持仓推一条(≤3 仓,逐仓
    推比汇总更可执行)。**只推强价量证据命中** —— 题材持续天数是概念板块成分弱证据
    (K2 成分洞),只进盘中看板不推(§2.4「证伪只用价量结构」)。

    **系统永不代交易动作**(§3.8):本函数只发提醒(建议减仓/勿追),绝不代下单/撤单/改止损。"""
    disp = name or code
    reason = ";".join(hit_labels) if hit_labels else "触发年线下派发信号"
    return push_event(
        KIND_HOLDING_ALERT, "持仓派发警报",
        f"{disp} {reason}。疑似诱多做局,建议减仓/勿追(系统不代下单)。{_OPEN_APP_NOW}",
        custom_extra={"code": code},
        db_path=db_path, transport=transport,
    )


# ══════════════════════════════════════════════════════════════════════════
# V2-⑪ 新增措辞层:四监测(⑪-A)+ NL 临时提醒命中(⑪-C)
# ══════════════════════════════════════════════════════════════════════════
#
# 四监测的文案统一遵守 ⑪-B 的三句式:**发生了什么 / 触碰了哪条计划 / 要不要打开
# APP**。判定与阈值一律不在这里 —— 那是 `sentinel/attention.py` 的事,本层只措辞。

def push_attention_alert(
    kind: str, title: str, what_happened: str, *,
    plan_touched: str = "", code: str = "", position_id: Optional[int] = None,
    merged_exposure_note: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """⑪-A 四监测的共用措辞入口(kind ∈ 四个监测 kind,**重要不紧急**级)。

    三句式装配:`what_happened`(发生了什么,由监测器给出的事实句)+ `plan_touched`
    (触碰了哪条计划;监测器给不出就省略这一句 —— **⛔ 不编一句**)+ 固定的「要不要
    打开 APP」。`merged_exposure_note` 是同篮合并敞口提示(蓝图 6.2),有才带。

    **参考、非指令**:四监测全是旁路观察,不构成任何交易指令,也不改任何纪律判定。"""
    if kind not in (KIND_BASKET_PEERS_WEAK, KIND_SECTOR_BID_FADE,
                    KIND_HOLDING_DECOUPLED, KIND_MARKET_SHOCK):
        raise ValueError(f"push_attention_alert 只接受四监测 kind,收到 {kind!r}")
    parts = [what_happened.rstrip("。") + "。"]
    if plan_touched:
        parts.append(plan_touched.rstrip("。") + "。")
    if merged_exposure_note:
        parts.append(merged_exposure_note.rstrip("。") + "。")
    parts.append("参考、非指令(系统不代下单)。" + _OPEN_APP_LATER)
    extra: Dict[str, Any] = {}
    if code:
        extra["code"] = code
    if position_id is not None:
        extra["positionId"] = position_id
    return push_event(
        kind, title, "".join(parts),
        custom_extra=extra or None, db_path=db_path, transport=transport,
    )


def push_custom_alert(
    alert_id: int, subject: str, condition_text: str, *,
    code: str = "", quote_delay_note: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """NL 临时提醒命中(kind=`custom_alert`,**立即**级 —— 蓝图 5.5 逐字点名的第一条
    「用户自定义价格条件」;plan ⑪-C)。

    `condition_text` = 命中的那条规则的人读描述(由 `custom_alerts.describe_rule`
    生成,**不是** LLM 自由文本);`quote_delay_note` = 行情延迟披露(确认卡上答应过
    用户的那句,命中时再说一遍 —— 免得用户拿一条延迟行情当成真的瞬时成交价)。

    **只通知不自动交易**(⑪-C 安全要求 + §3.8):这条固定尾巴不可删。"""
    body = f"{subject} {condition_text}。"
    if quote_delay_note:
        body += quote_delay_note.rstrip("。") + "。"
    body += "只通知不自动交易。" + _OPEN_APP_NOW
    extra: Dict[str, Any] = {"alertId": int(alert_id)}
    if code:
        extra["code"] = code
    return push_event(
        KIND_CUSTOM_ALERT, "临时提醒命中", body,
        custom_extra=extra, db_path=db_path, transport=transport,
    )


# ══════════════════════════════════════════════════════════════════════════
# 2026-08-03 用户拍板新增措辞层:持仓哨兵既有三事件升级立即级
# ══════════════════════════════════════════════════════════════════════════

def push_holding_risk_alert(
    kind: str, title: str, reason: str, *, code: str = "",
    db_path: Optional[Path] = None, transport: Optional[Any] = None,
) -> NotifyOutcome:
    """持仓哨兵三事件的 APNs 旁路(kind ∈ `stop_approach`/`take_profit`/
    `sector_dive`,**立即**级;2026-08-03 用户定向拍板 —— ⑪-B 完工记录曾登记
    「未开」,今日由用户拍板开闸,plan §五 V2-⑪-B 定向任务书)。

    `title`/`reason` 由调用方(`sentinel/engine.py::run_tick`)原样传入,按 kind
    分两条路:
      · `stop_approach` / `sector_dive` —— 与看板/Bark 通道(`sentinel/channels.py
        ::push_all`)喂的是**同一份**文案(`_maybe_push` 内部转手调用,复用同一次
        `already_pushed`/`record_pushed` 去重),不二次措辞、不二次去重。
      · `take_profit` —— **不是** `sentinel/holding.py::check_take_profit`
        (回落止盈,现役章程机械纪律,继续独立驱动 console/Bark、一字不动),而是
        `check_exit_reference_reached`(触达 `position_plans` 继承的离场参考区间)
        ——旁路专属、独立去重。两者刻意不同源,见该函数 docstring。

    本函数只补⑪-B 三句式缺的第三句「要不要打开APP」,不改事实本身(`reason` 已经
    讲清「发生了什么 / 触碰了哪条计划」)。**系统永不代交易动作**(§3.8):即便止损
    已跌破、即便已触达离场参考,本函数不追加任何"该卖了 / 建议减仓 / 建议止盈"式
    表述——离场参考是参考、系统不代下单,语义红线不因走了 APNs 通道而松动。
    ⚠ V2.4.0 P3.2:原文这里还有半句把回落止盈说成"才是纪律",已随版本裁定删除
    (`v2.3-k8` 起没有那条机械纪律,继续那样写就是撒谎;全文见 `charter_copy.py`
    模块头 + `PROJECT_PLAN.md` §五 V2.4.0 P3.2)。⛔ **别把那半句抄回来** ——
    守门单测按字面量扫全仓(含注释),抄回来当场红。"""
    if kind not in (KIND_STOP_APPROACH, KIND_TAKE_PROFIT, KIND_SECTOR_DIVE):
        raise ValueError(f"push_holding_risk_alert 只接受持仓三事件 kind,收到 {kind!r}")
    body = reason.rstrip("。") + "。" + _OPEN_APP_NOW
    return push_event(
        kind, title, body, custom_extra={"code": code} if code else None,
        db_path=db_path, transport=transport,
    )


__all__ = [
    "NotifyOutcome",
    # 唯一扇出路径(白名单的真正落点)
    "push_event",
    # 措辞层(V1 六类迁移)
    "push_report_ready",
    "push_retreat_brake",
    "push_precall_summary",
    # 措辞层(V2.3.3-④ 新增;kind 复用 KIND_PRECALL,**零新 kind**)
    "push_auction_summary",
    "push_d5_exit",
    "push_consecutive_stops_notice",
    "push_holding_alert",
    # 措辞层(V2-⑪ 新增)
    "push_attention_alert",
    "push_custom_alert",
    # 措辞层(2026-08-03 用户拍板新增)
    "push_holding_risk_alert",
]
