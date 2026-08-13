"""生产侧画像契约。

`preference.py` 维护在线偏好统计；`models.py` 与 `store.py` 保留能力画像的稳定
读写模型，供 API 展示已经生成的结果。能力计算、回测评价和批处理入口均由
`/Users/linotsai/Lino/whynotme` 负责，Backend 不再包含研究引擎。

画像结果不得反向改变客观 Tier。`neckline.selection` 与 `neckline.scan` 也不得
依赖本包；守门测试见 `tests/test_profile_guardrails.py`。
"""

from __future__ import annotations

__all__: list = []
