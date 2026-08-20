"""架构第三层 · 解释层(V2.5.0 S9,架构 §3.3 + PROJECT_PLAN §5.5)。

**职责**:把清单上的票,讲成用户读得懂的东西 —— 它是什么公司、当前消息面、
在行业里的处境、位置与结构状态、近期表现,以及**日K 形态的评价**。

🔴 **双盲(架构 §3.3 逐字:「解释层收到的输入不含通道身份与排序位次」)**
—— 这是**结构性保证**,不是约定:

    ① `ExplainInput` 是**独立 DTO**,里面**根本没有** `patterns` / `channel` /
       `rank` / `score` / `tier` / `seat_kind` 这些字段(不是「有但不填」);
       字段集冻结成 `EXPLAIN_INPUT_FIELDS`,守门单测逐字断言(G5)——
       加字段必须先改那个列表 = 一次自觉行为。
    ② AST:`neckline/explain/**` **零 import** `neckline.k9`。
    ③ 🔴 **排序位次也会从列表顺序泄漏** —— 交给解释层的序列一律按 `ts_code`
       **升序**,`input.build_inputs()` 里排序是唯一入口,单测断言它确实排了序。

🔴 **消息面排除在这一层**(K9 §二 末段 / 架构 §3.3):爆雷 / 减持 / 立案 / 监管
四类在此查出并剔除,剔除后由排序中的**后备票补位**。
⚠ **补位决定由编排器做**(它才知道排名,`report/evening.py`)—— 解释层自己
不知道谁是第几名,**双盲不破**。

🔴 **检索走 Tavily**(`search/tavily.py`,经 `llm/factory.get_provider(TASK_NEWS_SCAN)`
包成 `TavilyGroundedProvider`),⛔ 不用 Provider 自带联网(V2.4.2 已收口的教训:
不被上游认识的组合会 `ok=True` **静默返 0 条**,而模型照样写得出像样的分析)。

🔴 **三态,⛔ 不许折平**(`news_exclusion.NewsState`):
    `clean`      查过了、干净;
    `excluded`   命中四类之一 → 剔除;
    `unverified` **没查成**(没有 provider / 调用失败 / 模型没按格式给结论)。
把 `unverified` 当 `clean` 是「没看」冒充「看过了没事」;当 `excluded` 则会因为
一次检索失败悄悄砍掉一只好票。两种折平都错,所以它必须是第三态。
"""

from __future__ import annotations

__all__: list = []
