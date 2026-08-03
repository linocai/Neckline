"""通知三级 × N `kind` 注册表(plan §五 V2-⑪-B,**D5 已拍板**)—— 全项目**唯一
一处**定义「有哪些通知 kind、每个 kind 属哪一级、APNs category 叫什么」的地方。

**为什么单起一个叶子模块**(零项目内 import,谁都能安全引):

    · `api/notify.py`(APNs 扇出)、`sentinel/`(新监测与 NL 提醒的产出侧)、
      `settings_store.py`(按 kind 存开关)、`api/schemas.py`(契约)四处都要吃同
      一份定义。散抄字面量 = 四份口径,和 `verification_rules.py` 单起小模块是同
      一条理由。
    · 它**不能**住进 `api/notify.py`:那样 `sentinel/` 引一个 kind 常量就得 import
      `neckline.api.*`(→ `api/stores.py` → …),平白把哨兵拖进 API 层的依赖网。

**三级(D5 定案,category 字面量不可擅改 —— 客户端 `PushManager` 按字面量注册)**:

    立即 `NKIMMEDIATE`      —— 需要现在就看一眼(蓝图 5.5「立即通知」)
    重要不紧急 `NKIMPORTANT` —— 今天要处理,但不必打断手头的事
    盘后汇总 `NKDIGEST`      —— 收盘后一起看

**开关按 `kind` 配,不按 category 配(D5 的核心)**:按 category 配会**连坐** ——
关掉「重要不紧急」等于同时关掉板块、大盘、时间退出三类完全不同的事(V1 拆
`HOLDINGALERT` 就是被这个坑逼出来的:D5「持有到期」与 K4「可能被派发」合用一个
开关,用户关掉一个必然连带关掉另一个)。**category 只决定「怎么响」,kind 决定
「响不响」。**

**⚠ 新增 kind 仍须用户拍板**(plan §五 V2-⑪-B 原文纪律,V1「推送白名单六类」那条
纪律的 V2 形态):`ALL_KINDS` 是冻结元组,`tests/test_notify_kinds.py` 按**精确集合**
锁死;要加第 12 个 kind,先拿到用户拍板,再改这里 + 改守门单测。**⛔ 不许"顺手"
加一个**——推送是唯一会主动打断用户的通道,白名单的意义全在"加不进来"。

**本版 11 个 kind 的来源逐条可追溯**(⛔ 没有一个是本模块自己发明的):

    · 6 个来自 V1 六类白名单迁移(plan ⑪-B 逐字点名);
    · 1 个 `custom_alert` 来自 ⑪-C(NL 临时提醒);
    · 4 个来自 ⑪-A「新增四监测」。

**两处刻意的缺席**(不是遗漏,完工记录已登记、等用户/planner 裁定):

    · `basket_falsified`:⑪-B 的 kind 例举里有它,但 **⑦-b / ⑧-C2 的语义红线**
      写死「篮子 `falsified` ⛔ 不触发任何交易动作、**不进推送**、不改任何持仓
      判定」,且 `tests/test_sentinel_basket_verify.py::test_verification_never_
      touches_positions_or_push_channels` 已把这条红线锁成守门单测。两份权威正面
      冲突,**本块取保守方向(不推)**:少推一条通知是可逆的,越过一条红线不是。
    · `stop_approach` / `take_profit` / `sector_dive`:蓝图 5.5「立即通知」确实
      点名了「逼近或触发止损」,但这三条是**持仓哨兵既有的看板事件**(现走
      `sentinel/channels.py` 的 Bark/日志通道,从来没进过 APNs),⑪-B 的 kind
      例举里也没有它们。给它们开 APNs = 给用户凭空多一路推送,按本模块自己那条
      「新增 kind 须用户拍板」纪律,**本块不擅自加**。
"""

from __future__ import annotations

from typing import Dict, Tuple

# ══════════════════════════════════════════════════════════════════════════
# 三级(级别码 + APNs category 字面量)
# ══════════════════════════════════════════════════════════════════════════

LEVEL_IMMEDIATE = "immediate"
LEVEL_IMPORTANT = "important"
LEVEL_DIGEST = "digest"

LEVELS: Tuple[str, ...] = (LEVEL_IMMEDIATE, LEVEL_IMPORTANT, LEVEL_DIGEST)

# D5 拍板的三个 APNs category 字面量。**改这三个串 = 改客户端契约**(客户端按字面量
# 注册 category 与动作按钮),不可擅改。
CATEGORY_IMMEDIATE = "NKIMMEDIATE"
CATEGORY_IMPORTANT = "NKIMPORTANT"
CATEGORY_DIGEST = "NKDIGEST"

CATEGORY_OF_LEVEL: Dict[str, str] = {
    LEVEL_IMMEDIATE: CATEGORY_IMMEDIATE,
    LEVEL_IMPORTANT: CATEGORY_IMPORTANT,
    LEVEL_DIGEST: CATEGORY_DIGEST,
}

LEVEL_LABEL: Dict[str, str] = {
    LEVEL_IMMEDIATE: "立即",
    LEVEL_IMPORTANT: "重要不紧急",
    LEVEL_DIGEST: "盘后汇总",
}

# ══════════════════════════════════════════════════════════════════════════
# kind 常量(**唯一源**;消费方一律 import,禁散抄字面量)
# ══════════════════════════════════════════════════════════════════════════

# —— V1 六类白名单迁移(kind 串按 plan ⑪-B 原文逐字)————————————————————
KIND_REPORT_READY = "report_ready"      # 16:35 盘后报告就绪
KIND_RETREAT = "retreat"                # 退潮红色刹车
KIND_PRECALL = "precall"                # 9:26 盘前校准汇总
KIND_D5EXIT = "d5exit"                  # 时间退出(D5 / 浮盈硬上限)
KIND_CIRCUIT = "circuit"                # 熔断提醒
KIND_HOLDING_ALERT = "holding_alert"    # K4 持仓派发警报

# —— ⑪-C:自然语言临时提醒命中 ————————————————————————————————————————
KIND_CUSTOM_ALERT = "custom_alert"

# —— ⑪-A:新增四监测(**旁路,不进任何既有纪律判据**)————————————————————
KIND_BASKET_PEERS_WEAK = "basket_peers_weak"    # ① 同篮子成员集体转弱
KIND_SECTOR_BID_FADE = "sector_bid_fade"        # ② 板块(基准指数)承接消失
KIND_HOLDING_DECOUPLED = "holding_decoupled"    # ③ 持仓从跟随板块转为独立弱势
KIND_MARKET_SHOCK = "market_shock"              # ④ 大盘突变

ALL_KINDS: Tuple[str, ...] = (
    KIND_REPORT_READY,
    KIND_RETREAT,
    KIND_PRECALL,
    KIND_D5EXIT,
    KIND_CIRCUIT,
    KIND_HOLDING_ALERT,
    KIND_CUSTOM_ALERT,
    KIND_BASKET_PEERS_WEAK,
    KIND_SECTOR_BID_FADE,
    KIND_HOLDING_DECOUPLED,
    KIND_MARKET_SHOCK,
)

# —— 分级归属(蓝图 5.5 逐条对照;每一条都在下面标了依据,⛔ 不许凭手感改)————
#
#   立即       蓝图原文:自定义价格条件 / 逼近或触发止损 / 快速跳水 / 涨跌停打开 /
#              重大公告或交易风险
#   重要不紧急 蓝图原文:放量异动 / 持仓明显弱于篮子 / 板块核心转弱 / 大盘快速变化
#   盘后汇总   蓝图原文:普通波动 / 轻微技术变化 / 一般板块分歧
#
LEVEL_OF_KIND: Dict[str, str] = {
    # 「重大交易风险」——红色刹车 = 今日计划整体作废、全天禁开新仓。
    KIND_RETREAT: LEVEL_IMMEDIATE,
    # 「重大交易风险」——熔断 = 今日停开新仓、次日只减不加。
    KIND_CIRCUIT: LEVEL_IMMEDIATE,
    # 蓝图逐字点名的第一条「用户自定义价格条件」。
    KIND_CUSTOM_ALERT: LEVEL_IMMEDIATE,
    # 盘前校准是**汇总件**(9:26 一条),要在开盘前核对但不是逐笔紧急。
    KIND_PRECALL: LEVEL_IMPORTANT,
    # 时间退出是「今天按计划离场」,当日内处理即可,不是秒级。
    KIND_D5EXIT: LEVEL_IMPORTANT,
    # K4 三条强证据(年线下涨停 / 放量大阳派发 / 换手>10%)全部属蓝图「放量异动」
    # 一族,文案本身也是「建议减仓/勿追」的 advisory 口吻,不是止损那种秒级事件。
    KIND_HOLDING_ALERT: LEVEL_IMPORTANT,
    # 「持仓明显弱于篮子」的另一面:篮子整体在转弱。
    KIND_BASKET_PEERS_WEAK: LEVEL_IMPORTANT,
    # 「板块核心转弱」。
    KIND_SECTOR_BID_FADE: LEVEL_IMPORTANT,
    # 「持仓明显弱于篮子」。
    KIND_HOLDING_DECOUPLED: LEVEL_IMPORTANT,
    # 「大盘快速变化」。
    KIND_MARKET_SHOCK: LEVEL_IMPORTANT,
    # 报告就绪本就是**收盘之后**那一条,盘后汇总级天生就是它的位置。
    KIND_REPORT_READY: LEVEL_DIGEST,
}

# 设置屏用的人读名(客户端展示层若要中文可直接用它,不必各端再抄一份映射)。
KIND_LABEL: Dict[str, str] = {
    KIND_REPORT_READY: "盘后报告就绪",
    KIND_RETREAT: "退潮红色刹车",
    KIND_PRECALL: "盘前校准汇总",
    KIND_D5EXIT: "时间退出",
    KIND_CIRCUIT: "熔断提醒",
    KIND_HOLDING_ALERT: "持仓派发警报",
    KIND_CUSTOM_ALERT: "自定义临时提醒",
    KIND_BASKET_PEERS_WEAK: "同篮成员集体转弱",
    KIND_SECTOR_BID_FADE: "板块指数承接消失",
    KIND_HOLDING_DECOUPLED: "持仓转独立弱势",
    KIND_MARKET_SHOCK: "大盘突变",
}

# 全部 kind 默认**开**(承 V1 六类开关「默认开可关」的既定口径)。
DEFAULT_ENABLED: bool = True

# V1 六类开关列 → V2 kind 的迁移映射(`db.py::_seed_push_kinds` 一次性播种时用;
# 老库里用户已经关掉的开关不能因为改版就被悄悄打开)。
LEGACY_COLUMN_OF_KIND: Dict[str, str] = {
    KIND_REPORT_READY: "push_report",
    KIND_RETREAT: "push_retreat",
    KIND_PRECALL: "push_precall",
    KIND_D5EXIT: "push_d5exit",
    KIND_CIRCUIT: "push_circuit",
    KIND_HOLDING_ALERT: "push_holding_alert",
}


def level_of(kind: str) -> str:
    """`kind` → 三级之一。未登记的 kind 直接抛 —— **不给"未知 kind 默认某一级"
    的兜底**:那等于给白名单开后门,任何拼错的串都会静默变成一条真推送。"""
    try:
        return LEVEL_OF_KIND[kind]
    except KeyError:
        raise ValueError(
            f"未登记的通知 kind={kind!r};合法取值见 neckline.notify_kinds.ALL_KINDS。"
            "新增 kind 须用户拍板(plan §五 V2-⑪-B)。"
        ) from None


def category_of(kind: str) -> str:
    """`kind` → APNs category 字面量(三选一)。未登记的 kind 同样抛。"""
    return CATEGORY_OF_LEVEL[level_of(kind)]


def kinds_of_level(level: str) -> Tuple[str, ...]:
    """某一级下的全部 kind(按 `ALL_KINDS` 顺序,**确定性**)。"""
    return tuple(k for k in ALL_KINDS if LEVEL_OF_KIND[k] == level)


__all__ = [
    "LEVEL_IMMEDIATE", "LEVEL_IMPORTANT", "LEVEL_DIGEST", "LEVELS",
    "CATEGORY_IMMEDIATE", "CATEGORY_IMPORTANT", "CATEGORY_DIGEST",
    "CATEGORY_OF_LEVEL", "LEVEL_LABEL",
    "KIND_REPORT_READY", "KIND_RETREAT", "KIND_PRECALL", "KIND_D5EXIT",
    "KIND_CIRCUIT", "KIND_HOLDING_ALERT", "KIND_CUSTOM_ALERT",
    "KIND_BASKET_PEERS_WEAK", "KIND_SECTOR_BID_FADE", "KIND_HOLDING_DECOUPLED",
    "KIND_MARKET_SHOCK",
    "ALL_KINDS", "LEVEL_OF_KIND", "KIND_LABEL", "DEFAULT_ENABLED",
    "LEGACY_COLUMN_OF_KIND",
    "level_of", "category_of", "kinds_of_level",
]
