"""市场扫描层结构性防复发守门(plan §五 V2-④ 验收:「grep 守门:在线模块
[api/ / report/pipeline.py] 零现算入口」),体例照
`tests/test_industry_strength_store.py::test_online_paths_never_reference_full_scan_entrypoints`
(同一条 P0-23 纪律的结构性防线)。

**为什么现在(④还没有任何模块 import `neckline.scan`)也要写这条测试**:这是
给**未来**⑤/⑥/⑭ 等消费方钉的护栏——它们会真的开始从在线路径读扫描层数据,
到那时"只准读表,不准现算"的边界必须已经是机器可查的,不能等出问题才补。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_ONLINE_FILES = [
    "neckline/report/pipeline.py",
    # V2-⑭-A 新增:篮子日报视图模型是 `build_report` 的**只读**装配层,同属在线路径。
    # (⛔ **`neckline/report/evening.py` 刻意不在本清单里** —— 它就是那条 16:35 批算链,
    # 调批算写入口是它的职责;把它加进来会让这条守门与 ⑭-A 的编排直接冲突。)
    "neckline/report/basket_daily.py",
    "neckline/api/app.py",
    "neckline/api/deps.py",
    # ⚠ V2.1-① 起 `neckline/api/inquiry.py` 已随问询台整链退役物理删除,从本清单
    # 摘除(不是"文件改名/移动"那种要靠 skip 兜底的场景,而是这个在线消费方
    # 本身不存在了——清单摘除比留一条永远 skip 的参数化用例更诚实)。
    "neckline/api/notify.py",
    "neckline/api/schemas.py",
    "neckline/api/stores.py",
]

# 三张事实表各自的"重算 + 写库"入口(P0-23 管的是这些——它们要么做 I/O 密集
# 的窗口扫描,要么直接写库;在线路径只准调 `load_*`/`generate_seeds`/
# `scan_layer_status` 这类读函数)。
_BANNED = [
    "refresh_limit_clusters",
    "refresh_corr_matrix",
    "refresh_leader_structure",
    "compute_limit_clusters_for_day",
    "compute_corr_for_day",
    "compute_leader_structure_for_day",
    # V2.2-③-C:落地起跳的批算/写入口(全市场逐票 × 145 交易日回看,P4-50 点名的
    # 第二条 P0-23 靶心路径)——在线路径只许读 `landing_store.load_*`。
    "refresh_landing_metrics",
    "compute_landing_metrics",
]


@pytest.mark.parametrize("rel", _ONLINE_FILES)
def test_online_paths_never_reference_scan_layer_write_entrypoints(rel: str):
    path = _PROJECT_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} 不存在(文件已改名/移动,更新本测试的文件清单)")
    text = path.read_text(encoding="utf-8")
    for name in _BANNED:
        assert name not in text, f"{rel} 出现了被禁的扫描层写入口 {name}(P0-23:在线路径只许读表)"
