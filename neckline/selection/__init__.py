"""选股策略包机制(plan §五 V2-③,§十二 全文落地)。

**策略包 = 声明式配置包,不是代码插件**(§12.1 定案,勿重开):包里只装参数与规则
声明,执行引擎(本包全部代码)永远住系统线仓库,不随包传输。子模块分工:

    · `engine_api`   —— 引擎 API 兼容版本单一源(`ENGINE_API_VERSION`)。
    · `primitives`   —— 原语注册表(特征白名单 + 声明式 filter/feature/sort_key)。
    · `pack`         —— manifest/config schema 校验、包文件装载、`selection_packs`
                        读写(`get_active_pack()` 为读现役包唯一入口,照
                        `neckline.strategy.brain.get_active()` 体例)。

激活走 `scripts/activate_pack.py`(复刻 `scripts/activate_charter.py` 四道闸),
**不做 API 端点**(同章程激活铁律,§3.8 系统内核永不被客户端改)。

**插槽边界(定死,勿扩,见 plan §五 V2-③)**:包只管 ①扫描层的驱动种子生成规则
(用哪些过滤器、什么阈值)与 ③Tier 的机械分维度选择与权重。②驱动聚合的两道机械闸、
④篮子卡冻结体例、⑤复盘与评价引擎 = 引擎本体,不进包;**纪律章程不进包**(两条
版本线、两张表、两套激活流程,防双权威漂移——本包全程不 import
`neckline.strategy.brain`,不碰 `strategy_versions`)。
"""

from __future__ import annotations
