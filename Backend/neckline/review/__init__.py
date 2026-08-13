"""周复盘对账引擎(plan §五 阶段4D)。

    · `parse.py`   —— 券商交割单 xlsx 解析(两家格式 + 可配列映射)。
    · `reconcile.py` —— FIFO 闭合回合 + 对账三查(计划内外/持仓台账、止损纪律、
      章程执行)+ 单周统计 + 强制复盘判定。
    · `material.py`  —— 确定性复盘材料(自由叙述段落)。
    · `store.py`     —— `reviews` 表(week PK)读写。

对账逻辑全部在后端(本包),客户端(macOS 周复盘工作台)只负责拖文件上传与展示
——不在客户端重算任何判定(§3.8「同码不重写」精神的延伸:领域判定只在一处)。
"""

from neckline.review.parse import ParseResult, RawTrade, parse_workbook
from neckline.review.reconcile import WeeklyReview, run_weekly_review, weekly_review_dict
from neckline.review.material import build_material_text
from neckline.review.store import load_weekly_review, save_weekly_review

__all__ = [
    "ParseResult",
    "RawTrade",
    "parse_workbook",
    "WeeklyReview",
    "run_weekly_review",
    "weekly_review_dict",
    "build_material_text",
    "load_weekly_review",
    "save_weekly_review",
]
