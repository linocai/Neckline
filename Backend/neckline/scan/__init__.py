"""市场扫描层(plan §五 V2-④)。三张 EOD 预计算表(`corr_matrix_daily` /
`limit_cluster_daily` / `leader_structure_daily`)+ 驱动种子生成,全部走
「EOD 预计算落表、在线只读」纪律(P0-23)。

模块划分(各文件"计算 + 落表读写"一体,同 `report/industry_strength_store.py`
先例,不额外拆一层 store):
    · `cluster.py` → `limit_cluster_daily`(涨停共振簇,事实,无包依赖)
    · `corr.py`    → `corr_matrix_daily`(簇内/概念内滚动相关,事实,无包依赖;
                     依赖 `cluster.py` 的簇成员做候选对)
    · `leader.py`  → `leader_structure_daily`(簇内龙头结构,事实,无包依赖;
                     依赖 `cluster.py` 的簇成员 + `corr.py` 的收益率窗口)
    · `seeds.py`   → 四类驱动种子(热点行业/暴起概念/涨停簇/异动簇,**不落表**——
                     读三张事实表 + 已有预计算表,阈值全部来自现役策略包,
                     计算本身足够便宜,不需要额外物化)
    · `freshness.py` → 扫描层新鲜度(`dataFreshness.scanLayer*` 三键的计算逻辑,
                     供未来 `report/pipeline.py` 接线消费,本块只提供函数)
    · `regime.py` + `regime_store.py` → `market_regime_daily`(plan §五 V2.2-②,
                     K8 §一 行情状态层;D0 盘后三态判定。⚠ 本对文件是**判定**不是
                     事实表:五个阈值读骨架包 `config.regime`〔无现役骨架线时回退
                     引擎默认 + `skeleton_version='engine_default'`〕,`regime.py`
                     只算不写、`regime_store.py` 只管落表读表;对 `selection.pack`
                     仅 import 读入口〔权限锁,AST 守门〕)
    · `landing.py` + `landing_store.py` → `landing_metrics_daily`(plan §五
                     V2.2-③-C,K8 §二「落地起跳」;🔴 2026-08-09 用户裁定 #11
                     整节重写——机械层从「四态判定 + 十二个阈值」收窄为**只算
                     原始读数、零判定**:十四项读数(比值/收益率/布尔事实)+
                     `metrics_missing` 逐项缺因,⛔ 无阈值、无骨架包依赖。判定
                     交给 LLM(六关⑤位置关,`neckline/selection/gates.py`,产出
                     `position_verdict ∈ {ok,weak,unfit}`,复用 `basket_reason`
                     那一次调用)。`landing.py` 只算不写、`landing_store.py` 只管
                     落表读表。🔴 只产注意力分层,⛔ 不得读成买入期望背书;雷区
                     对照与 §七 P3-49 前向证伪义务见 `landing.py` 模块头。零
                     import `neckline.sentinel.*`、`report.score_display`
                     与 `neckline.selection.pack`〔守门单测〕)
    · `stage.py`   → `industry_stage_daily`(plan §五 V2-④b,K7 需求 1b;行业题材
                     阶段六态状态机——启动/发酵/过热/分歧回调/退潮/无题材,取代
                     `driver_freshness` 原先借用的 `stock_persist_days` 单调函数。
                     "计算+落表读写+新鲜度+自检"一体自包含,同
                     `report/industry_strength_store.py` 先例,不拆进
                     `freshness.py`/`verify.py`——那两个文件的既定范围明确是三张
                     ④ 表,不含本表)。依赖 `industry_strength_daily`(单一源,只读
                     不改)与 `data/limit_derived.py`,**可与 cluster/corr/leader/
                     seeds 并行**,不依赖它们。

**"事实表"与"种子"的分野(勿混淆)**:三张表(+ `industry_stage_daily`)是**客观
市场结构事实**,不读任何策略包配置(同 `industry_strength_daily` 的既有分工:
引擎常量,不是策略参数);种子生成是**主观判断在哪算是"热"**,阈值一律读
`neckline.selection.pack.get_active_pack().config["seeds"]`,新增四个原语见
`neckline/selection/primitives.py`。
"""

from __future__ import annotations

__all__: list = []
