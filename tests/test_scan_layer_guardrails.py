"""市场扫描层结构性防复发守门(plan §五 V2-④ 验收:「grep 守门:在线模块
[api/ / report/pipeline.py / api/inquiry.py] 零现算入口」),体例照
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
    "neckline/api/inquiry.py",
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
]


@pytest.mark.parametrize("rel", _ONLINE_FILES)
def test_online_paths_never_reference_scan_layer_write_entrypoints(rel: str):
    path = _PROJECT_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} 不存在(文件已改名/移动,更新本测试的文件清单)")
    text = path.read_text(encoding="utf-8")
    for name in _BANNED:
        assert name not in text, f"{rel} 出现了被禁的扫描层写入口 {name}(P0-23:在线路径只许读表)"
