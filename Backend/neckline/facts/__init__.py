"""架构第一层 · 事实层(V2.5.0,PROJECT_PLAN §5.3)。只回答「今天市场发生了什么」,
**只装一天的事实,不装任何窗口量** —— 窗口长度全是策略参数,装进事实层就把可调项
埋进了不该调的地方(架构 §二 判据)。

S3 现状(本包已完工的五件):

| 模块 | 管什么 |
|---|---|
| `pack.py` | `FactPack` 列定义 + `build()`(返回 `CompletePack` \\| `IncompletePack`) |
| `store.py` | **唯一写入口** `freeze_pack()` + 只读 `load_pack` / `load_pack_range` + 保留策略 |
| `industry.py` | 申万二级成员涨跌幅中位数(裁定 2,**无参数、无门槛**) |
| `limitmap.py` | 涨停分布 + 涨停簇(裁定 3:锚在申万二级;⛔ 无概念板块) |
| `completeness.py` | 冻结前的缺口判定 ——「今天没跑成」的第一个来源 |
| `universe.py` | 当日**在市**的全市场票池(`stock_basic` 口径)—— 事实包只装当日 `daily` 有行的票,而 §6 S6 的全市场 disposition 要覆盖「一只票都没交易过」的那些(2026-08-21 复审 R3-🔴-5) |

⛔ **本包不许 import `neckline.k9` / `explain` / `playbook` / `scorecard`**
(架构 §二 边界①:事实层不知道下游有哪些策略)。守门单测 G1 逐文件 AST 扫描。

⚠ `direction_llm.py`(事实层的 LLM 方向解读,§5.3.6)**不在 S3 范围内** ——
架构 §十 把它列为「独立于主线,可随时接入」,§6 S3 的产出清单里也没有它。
"""

from __future__ import annotations

__all__: list = []
