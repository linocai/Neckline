"""偏好画像 / 能力画像引擎(plan §五 V2-⑫-B,蓝图 6.3)。两张账分开、两个引擎:

    · **偏好画像**(`preference.py`)答「喜欢什么」——常买题材 / 角色 / 两个口径的
      入场方式(几何口径 + `user_actions.kind='label'` 用户自述口径)/ 常选 Tier
      (纯统计口味,不判断好坏)。落 `profile_preference` 表。
    · **能力画像**(`capability.py`)答「什么真有效」——各类选择的胜率 / 盈亏比 /
      MFE·MAE(读 `positions` 真实成交)、是否跑赢同篮未选股票(判分复用
      `neckline.eval.metrics`/`exit_sim` 唯一源,即 ⑨ 评价引擎产出的同一条链路)、
      哪些机会经常被错误忽略。落 `profile_capability` 表。
    · `common.py` → 两个引擎共用的面板装配(`load_buy_contexts`,只读
      `positions`/`entry_snapshots`/`position_plans`)+ 样本量/置信度工程常量。
    · `store.py` → 两张表的读写(每期一版,`INSERT OR REPLACE`)。

数据源覆盖 plan 原文「⑨ 的评价引擎产出 + `user_actions` + `positions` + 对账
结果」四项中的前三项;第四项(周复盘对账结果,`review/reconcile.py` 的
`WeeklyReview`)**未接入**——那是交割单解析产生的 `RoundTrip` 对象,与
`positions` 表之间没有确定性外键可连(只能靠金额/日期近似匹配),勉强关联
反而可能引入误判,已如实登记在 ⑫ 完工记录,留给用户/⑭ 判断是否值得专门打通。

**画像计算是批算落表(EOD/周度),在线只读**——两个 `compute_*` 函数不接任何
在线请求路径,`scripts/profile.py` 是唯一的批算入口(⑭ 落地前手动/挂 timer 驱动,
同 ⑨ `scripts/basket_review.py` 的既定分工)。

**⛔ 初期不得反向影响客观 Tier(蓝图 4.4 禁令,守门单测见
`tests/test_profile_guardrails.py`)**:本包只读,不 import 也不写
`neckline.selection` / `neckline.scan` 的任何东西;反过来,`neckline/selection/`
与 `neckline/scan/` 全目录禁止出现 `profile_` / `neckline.profile` 引用——两个
方向都不许通。每项画像结论都带样本量 / 时间范围 / 置信度,样本不足如实标
「样本不足 / 置信度 low」,不给结论(不是不出行,行还在,只是不下判断)。
"""

from __future__ import annotations

__all__: list = []
