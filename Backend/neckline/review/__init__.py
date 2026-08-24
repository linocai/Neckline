"""交割单分析台(架构 §六,PROJECT_PLAN §5.9,V2.5.0 S11 收口)。

**每周末一次。🔴 这一层无 LLM 调用**(架构 §六 逐字)。系统只承担三件事,
第四件(对话与总结)明确在系统之外:

| # | 系统承担 | 模块 |
|---|---|---|
| 1 | **交割单解析** —— 时点 / 价格 / 数量 / 标的 | `parse.py`(两家券商 schema,已逐字段核实,S11 原样留用) |
| 2 | **行情材料装订** —— 前后 K 线 + 买卖点 + 同期大盘 + 同期申万二级 + 当时的报告与预案快照 | `bindery.py`(S11 新建) |
| 3 | **结论存档** —— 保存本周结论,下周可检索 | `conclusions.py`(S11 新建,append-only 版本化) |
| — | ⛔ **对话与总结不在系统内** | 用户带着材料去聊天框,总结用第 3 件回存 |

配套件:`reconcile.py`(FIFO 回合闭合 + `WeeklyStats`)、`cashflow.py`(资金流水
**四分类**,🔴 刻意**没有**「账户净变动」合计字段)、`material.py`(确定性对账叙述)、
`store.py`(`reviews` 表)。不支持的判据不保留恒空壳，避免被误读为“本周很干净”。

🔴 **三条成绩线互不进入对方的分子分母**(架构 §五)。本包是「我的成绩」那条线,
隔离是**单向且结构性**的:
  · 本包**读** `k9_reports` / `k9_playbooks` / `k9_listing_entries` 当**材料**
    (架构 §六 明文要求装订「当时那几天的报告与预案快照」),但 ⛔ 一个字都不往
    `k9_*` 写;
  · `scorecard/**` ⛔ **零 import** `neckline.review` —— 交割单里的成交永远不进
    清单成绩或覆盖率的分子分母。
两条守门单测各锁一个方向(`tests/test_v250_s11_s13_guard.py`)。
"""

from neckline.review.parse import ParseResult, RawTrade, parse_workbook
from neckline.review.reconcile import WeeklyReview, run_weekly_review, weekly_review_dict
from neckline.review.material import build_material_text
from neckline.review.store import load_weekly_review, save_weekly_review
from neckline.review.bindery import WeekBinding, bind_week, render_binding_markdown
from neckline.review.conclusions import Conclusion, ConclusionInvalid

__all__ = [
    "ParseResult",
    "RawTrade",
    "parse_workbook",
    "WeeklyReview",
    "run_weekly_review",
    "weekly_review_dict",
    "build_material_text",
    "load_weekly_review",
    "save_weekly_review",
    "WeekBinding",
    "bind_week",
    "render_binding_markdown",
    "Conclusion",
    "ConclusionInvalid",
]
