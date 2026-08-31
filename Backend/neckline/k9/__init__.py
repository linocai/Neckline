"""架构第二层 · 策略层(K9 第一~三层),PROJECT_PLAN §5.4。

**性质**:全机械、参数化、确定性。同样的事实包 + 同样的参数包 → **逐字节相同**的清单。

🔴 **四条硬边界,靠结构保证而不是靠注释提醒**(§5.4.1 / §10 G2–G4):
1. `k9/**` ⛔ 不得 import `neckline.llm` / `neckline.search` / `httpx` / `openai` /
   `requests` / `urllib` / `socket` —— **策略层内没有 LLM 调用**(架构 §3.2);
2. `k9/**` ⛔ 不得 import `neckline.data.tushare_client` / `neckline.data.market_data`
   —— 「取数唯一来源是事实包」的真牙齿(策略契约第三条);
3. `k9/channels/pN_*.py` 之间 ⛔ 零 import(召回通道互不知道,架构 §二 边界②);
4. `K9Params` 的**每个字段都没有默认值** —— 少一个值就**构造不出对象**,
   不是靠 if 判断(裁定 5:读不到参数配置就明确报「参数未配置」并停止出清单)。

K9-v3 的参数校验、机械通道与不可变成绩包写入均在本包实现。
正式数值仍只来自用户批准的外部参数包；仓库没有可运行的生产参数或回退值。
"""

from __future__ import annotations

__all__: list = []
