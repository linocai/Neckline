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
版本线、两张表、两套激活流程,防双权威漂移)。

⚠ **「不 import `neckline.strategy.brain`」这条约束的准确范围是「包机制本身」**
(`pack.py` / `primitives.py` / `engine_api.py`),不是本包每一个文件 —— 原文写成
「本包全程」是 ③ 施工时的措辞,V2-⑤ 起已不再成立,此处如实修正:

    · `aggregate.py`(⑤)读 `brain.get_active().version` 落 `baskets.charter_version`
      口径指纹;
    · `basket_card.py`(⑦)读 `brain.active_config()` 的 `stop_pct` /
      `take_profit_retrace` 算止损价与口径指纹(plan §五 V2-⑦ 明文要求:
      「止损价系统算、不由 LLM 给,`stop_pct` 读现役 config,**禁硬编 0.05**」)。

两处都是**只读**,且读的是**章程这一条版本线**的唯一源;真正要防的「双权威漂移」
是**把纪律参数写进策略包**,那条一步没退 —— 包 schema 里没有、也不许有任何纪律
阈值键。
"""

from __future__ import annotations
