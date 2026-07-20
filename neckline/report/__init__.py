"""盘后报告管线(plan 阶段 2 / §2.3):情绪仪表盘(sentiment)、强势板块(sectors)、
候选评分四件套(candidates)、报告落库(store)、markdown 渲染(render)、整体编排
(pipeline)。LLM 逻辑审判在独立的 `neckline.llm`/`neckline.llm.judge`。

铁律(§2.6/§3.8 同码三跑道):候选评分**必须**复用 `neckline.strategy.momentum`
的信号函数(`build_entry_mask` + 策略大脑 `strategy_versions` 现行版本),不得在
本包里另写一份选股逻辑。
"""
