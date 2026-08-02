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

**"事实表"与"种子"的分野(勿混淆)**:三张表是**客观市场结构事实**,不读任何
策略包配置(同 `industry_strength_daily` 的既有分工:引擎常量,不是策略参数);
种子生成是**主观判断在哪算是"热"**,阈值一律读 `neckline.selection.pack.
get_active_pack().config["seeds"]`,新增四个原语见 `neckline/selection/
primitives.py`。
"""

from __future__ import annotations

__all__: list = []
