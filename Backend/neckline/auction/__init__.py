"""D1 次日核对(V2.5.0 S8,K9 §七 + 架构 §四 + **裁定 10**)。

    D0 出清单 + 冻结预案
      → D1 **9:26—9:29** 冻结集合竞价 → 代入预案 → 核对表(两段)→ APNs
      → D1 **10:00—10:05** 一次性结算快照 → 三分支终值 → `k9_d1_verdicts`

🔴 **两段,⛔ 结构上没有第三段**(裁定 10):9:26 那张核对表只输出
「**已触发放弃**」与「**其余待开盘后观察**」。⛔ **9:29 一律不许输出「成立」** ——
K9 §6.3 四个成立条件**全部含有「前 30 分钟」这一合取项**,9:29 时它尚未发生。
结构性保证不是靠谁记得别写:`checklist.ChecklistVerdict` 是**二值枚举**
`{rejected, pending_open}`,「成立」在**类型层面**不存在;`checklist.py` 也
**只碰** `playbook.rejection_branch`(守门 AST 断言它零命中 `confirmation_branch`)。

🔴 **三分支判定的唯一权威是 10:00 结算拍**(裁定 10):零 LLM、零推送、
不进 App 首屏。9:29 已判「放弃」的票**先到先定、⛔ 不改判**(幂等
`WHERE decided_stage IS NULL`)。成绩线只读 `decided_stage='open30'` 的终值,
或 `decided_stage='auction'` 的「放弃」终值(§5.8.2)。

🔴 **零 LLM**(架构 §四:「纯条件求值」)。本包 ⛔ 一行不许 import
`neckline.llm` / `neckline.search`(守门 G7)。K8 时代的 `auction/llm.py`(489 行)
与 `auction/mech.py`(1651 行,Z1/Y1/C1 三道夹逼闸)**已整体退役,⛔ 不许取回**
—— 它们的输入是 T1/T2 篮子,而 K9 的输入是**清单 + D0 冻结预案**。

🔴 **窗口纪律照 K8 原件逐条保住**(自 `git show eac2823:.../auction/pipeline.py`
取回再改,PROJECT_PLAN §14 S1 登记 ②):
    · 交易日门 + 窗口左闭右开;
    · **当日防重**(`neckline/dedup.py`,市场级 key);
    · **窗口外一律零落库**;
    · 🔴 **⛔ 事后不许补跑** —— 补跑会拿 9:30 之后的价格冒充 9:26 那一刻的判断,
      拿 10:30 的价格冒充 10:00 那一刻。⚠ 唯一例外是**显式注入 `now`** 的
      CLI / 回放 / 单测(同 K8 原件的既有体例)。

**模块分工**(⛔ 不许合并):
    · `quality.py`   逐条行情的七项校验 + 双源核验(**纯函数**,零 IO / 零 DB / 零 LLM)
    · `collect.py`   冻结抓取(组清单 → 拉一次价 → 冻结);⛔ 不判定、不落库
    · `checklist.py` **9:26 那一拍**:二值核对表(⛔ 结构上没有「成立」)
    · `settle.py`    **10:00 那一拍**:三分支终值(裁定 10 的唯一权威)
    · `store.py`     `k9_d1_verdicts` / `k9_checklists` 两表的两阶段读写
    · `pipeline.py`  编排 + 窗口判定 + 当日防重 + 9:29 墙钟保护
"""

from __future__ import annotations

from datetime import time
from typing import Tuple

# ══════════════════════════════════════════════════════════════════════════
# 两拍的窗口(⛔ 都不是本项目发明的数)
# ══════════════════════════════════════════════════════════════════════════

#: 竞价核对表窗口 = **9:26—9:29**(K9 §七 原文 / 架构 §四 原文)。左闭右开。
AUCTION_WINDOW_START: time = time(9, 26)
AUCTION_WINDOW_END: time = time(9, 29)

#: 🔴 结算拍窗口 = **10:00—10:05**(裁定 10 原文「D1 的 10:00–10:05 一次性结算快照」)。
SETTLE_WINDOW_START: time = time(10, 0)
SETTLE_WINDOW_END: time = time(10, 5)

#: 集合竞价撮合时刻 = **9:25**,即竞价结果的**最早可接受源时间**。
#: ⚠ K8 时代这个常量的单一源是 `sentinel/capture.AUCTION_CAPTURE_START`,
#: 而 `sentinel/` 整包已在 S1 物理删除 → 本包自己持有它。它是**交易所制度**给的
#: 时刻(集合竞价 9:15–9:25,9:25 撮合),⛔ 不是待标定参数、⛔ 不许改成别的数。
AUCTION_RESULT_TIME_START: time = time(9, 25)

# ══════════════════════════════════════════════════════════════════════════
# 逐条行情的**七项校验**结果(K8.md §二十 逐字;V2.5.0 原样保留)
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 **⛔ 不得发明「5 分钟新鲜度」之类的新阈值**:时间判据只用**交易日**与
# **9:25 / 窗口边界**这些制度给定的时刻。
#
# 🔴 **`future_timestamp` 走零容差** —— 2026-08-12 **用户裁定 #2**(⛔ 不是工程侧
# 默认值)。用户原话:「竞价时间戳先执行零容差:源时间与本机存在任何偏差即降级为
# 中性。若实盘出现误判,再由我确认容差秒数,**施工 Agent 不得自行设定**。」
# ⚠ 落点是 `src_time > captured_at`(源时间**早于**抓取时刻是正常的)。
QS_FRESH = "fresh"
QS_WRONG_TRADE_DATE = "wrong_trade_date"
QS_BEFORE_FINAL_AUCTION = "before_final_auction"
QS_FUTURE_TIMESTAMP = "future_timestamp"
QS_TIMESTAMP_UNPARSEABLE = "timestamp_unparseable"
QS_REQUIRED_FIELD_MISSING = "required_field_missing"
QS_MALFORMED = "malformed"
QUOTE_STATUSES: Tuple[str, ...] = (
    QS_FRESH, QS_WRONG_TRADE_DATE, QS_BEFORE_FINAL_AUCTION, QS_FUTURE_TIMESTAMP,
    QS_TIMESTAMP_UNPARSEABLE, QS_REQUIRED_FIELD_MISSING, QS_MALFORMED,
)

# ── 双源核验后**这一只代码**的可用状态 ————————————————————————————————————
#   · `fresh`        至少一源通过**全部**七项校验,且两源没有结论性冲突;
#   · `degraded`     读数**可以用**、但有非致命项没过(目前只有:源还没发出开盘价);
#   · `insufficient` 双源(或唯一源)都踩了**致命项** → 这一格**没有可用读数**;
#   · `conflict`     双源读数都**可用**、但出现**结论性冲突**。
# 🔴 **`degraded` 这一档⛔ 别"简化"掉**:开盘价在 9:26 那一拍本来就还没有,
# 把它算致命等于因为一个当时用不上的字段把好端端的竞价价一起扔掉。
# 🔴 **`conflict` 压过 `degraded`**(R2-02):两源结论打架比「缺开盘价」严重得多。
# 判「有没有对拍过」用的是 `usable`(无致命项)而**不是** `ok`(七项全过)——
# 用 `ok` 会让跨源核验在 9:26 那一拍结构性永不触发(源那时还没发开盘价),
# `rejection_disagree` 就成了它专为之而生的那一拍里的死代码。判别式单一源 =
# `quality._is_cross_verified`。
QF_FRESH = "fresh"
QF_DEGRADED = "degraded"
QF_INSUFFICIENT = "insufficient"
QF_CONFLICT = "conflict"

QUOTE_ROLE_PRIMARY = "primary"      # 新浪
QUOTE_ROLE_BACKUP = "backup"        # 腾讯

# ── 结论性冲突(🔴 零新百分比阈值:四类全是**结论性**的)————————————————————
#: 两源根本不是同一只票 / 同一天的读数。
CONFLICT_IDENTITY_MISMATCH = "identity_mismatch"
#: 两源对涨跌**方向**看法相反。
CONFLICT_DIRECTION_OPPOSITE = "direction_opposite"
#: 🔴 V2.5.0 新增:两源代入**同一份 D0 冻结预案**得出的「放弃」结论不一致
#: (K8 时代的 `invalidation_disagree` 换成 K9 语义:比的是**预案分支**,不是止损线)。
CONFLICT_REJECTION_DISAGREE = "rejection_disagree"
CONFLICT_CODES: Tuple[str, ...] = (
    CONFLICT_IDENTITY_MISMATCH, CONFLICT_DIRECTION_OPPOSITE, CONFLICT_REJECTION_DISAGREE,
)

# ── 样本域数据质量三态(**结构性判据,⛔ 不是百分比阈值**)————————————————
DQ_OK = "ok"
DQ_DEGRADED = "degraded"
DQ_INSUFFICIENT = "insufficient"

# ── 跳过原因码(**分开记**,混成一个码就分不出「排程错了」还是「慢了」)————
SKIP_NOT_WINDOW = "not_window"                    # 名义时刻就不在窗口
SKIP_WINDOW_CLOSED = "window_closed_before_fetch"  # 名义时刻在窗口、真到拉价那一刻已越窗
SKIP_ALREADY_RAN = "already_ran"                  # 当日防重
SKIP_NO_LISTING = "no_listing"                    # D0 没有清单(可信的空,⛔ 不是故障)
SKIP_NO_PLAYBOOK = "no_playbook"                  # D0 有清单但一份预案都没冻结

__all__ = [
    "AUCTION_WINDOW_START", "AUCTION_WINDOW_END",
    "SETTLE_WINDOW_START", "SETTLE_WINDOW_END",
    "AUCTION_RESULT_TIME_START",
    "QS_FRESH", "QS_WRONG_TRADE_DATE", "QS_BEFORE_FINAL_AUCTION", "QS_FUTURE_TIMESTAMP",
    "QS_TIMESTAMP_UNPARSEABLE", "QS_REQUIRED_FIELD_MISSING", "QS_MALFORMED",
    "QUOTE_STATUSES",
    "QF_FRESH", "QF_DEGRADED", "QF_INSUFFICIENT", "QF_CONFLICT",
    "QUOTE_ROLE_PRIMARY", "QUOTE_ROLE_BACKUP",
    "CONFLICT_IDENTITY_MISMATCH", "CONFLICT_DIRECTION_OPPOSITE",
    "CONFLICT_REJECTION_DISAGREE", "CONFLICT_CODES",
    "DQ_OK", "DQ_DEGRADED", "DQ_INSUFFICIENT",
    "SKIP_NOT_WINDOW", "SKIP_WINDOW_CLOSED", "SKIP_ALREADY_RAN",
    "SKIP_NO_LISTING", "SKIP_NO_PLAYBOOK",
]
