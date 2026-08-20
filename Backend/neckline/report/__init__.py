"""报告层(V2.5.0,PROJECT_PLAN §5.10)。

**S3 现状**:本包只剩两件 ——
    · `store.py` —— 报告落库,**双日期契约**(`report_date` 管标题 / 推送 / 可见身份,
      `trade_date` 管 EOD 读数 / 清单 / 预案 / 审计键)。⛔ 不许退化(LRN-20260816-001)。
    · `state.py` —— `ReportState` 三值枚举 + **全映射**首行渲染(S5,§5.10)。

`pipeline.py` / `render.py` / `evening.py` 归 S7 重做;K8 的报告件(sentiment /
candidates / intel / news_alerts / 行业强度 / 板块 …)已随 S1、S3 整块退役。

🔴 **S3 退役的三件(PROJECT_PLAN §14 S1 登记里预告过的那三个「纯计算件」)**:
`industry_strength.py` / `board_pool.py` / `sectors.py`。
    · `industry_strength.py` 里的 `_MIN_MEMBERS = 5` 是**硬编码的待标定参数**
      (§8.2 第 16 项「行业中位数的最小成员数」),⛔ 绝不允许活进 K9 路径。
      它的职责由 `facts/industry.py`(申万二级中位数,**无门槛**)+ 将来的
      `k9/industry_heat.py`(读 `params.industry.minMembers`)接手。
    · `board_pool.py` / `sectors.py` 是**概念板块**的板块池卫生线与强度计算,
      唯一消费方是 S1 时期的 `facts/limitmap.py`。K9 §3.0 / 架构 §3.1 明令
      「概念板块不进入任何机械计算」,limitmap 切到申万二级后它们无人消费。
"""
