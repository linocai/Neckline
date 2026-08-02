"""评价引擎(plan §五 V2-⑨-C / ⑨-C2)。选股能力的审计者:把「Tier 有没有用、
篮子共振不共振、龙头带不带得动、换了包有没有变好」这些长期命题,变成**可复现的
数字**,喂给策略线下一轮迭代。

模块划分::

    · `exit_sim.py`     → **判分引擎唯一源**(⑨-D)。`_sim_one` / `SLIP` / `BROKER`
                          从 `research/h9_exit_reform.py` **下沉**至此,research 三处
                          改为 import 它;另含考官线(§九)竞价成交层 `fill_and_score`。
                          ⛔ `neckline/eval/` 内**不许出现第二份判分实现**(⑨-C2
                          验收第 ② 条,`tests/test_eval_exit_sim.py` 有 grep 守门)。
    · `metrics.py`      → 评价引擎指标(Tier 单调性 / 共振率 / 验证率 / 龙头 vs 成员 /
                          可交易收益 / 已选 vs 未选),全部按
                          `pack_version` × `verification_ruleset_version` 分层。
    · `placebo.py`      → ⑨-C2 两条对照臂(随机同规模篮子 + 满仓持有基准)。
                          `zlib.crc32` 派生种子,同一交易日跑两次逐位相同。
    · `calibration.py`  → 周度校准报告(装配 + markdown 渲染)。**不接报告管线**
                          (那是 ⑭),本块只提供函数 + `scripts/weekly_calibration.py`。

**边界**:本包只做**回看审计**,产出**只进周报与策略线迭代输入**,⛔ 不进任何在线
判据(不改 Tier、不改排序、不进哨兵、不接持仓动作)。每日复盘只记录,不因单日失败
改策略(蓝图 4.9);改权重一律走换包。
"""

from __future__ import annotations

__all__: list = []
