"""自选池(watchlist)存取 + CRUD(plan §五 v1.1-C.1)+ 同花顺 txt 互转/对账(C.4)。

≤30 上限硬校验(`MAX_WATCHLIST_SIZE`),真正的新增超限时 `add_watchlist` 抛
`WatchlistFullError`(API 层据此转 422,任务拍板「超限 422」)。**增删只经本模块的
用户显式调用**——系统代码路径(报告管线 / 哨兵 / 问询台等)只应调用只读的
`list_watchlist`/`list_watchlist_codes`,不得调用 `add_watchlist`/`remove_watchlist`/
`set_pinned`(任务拍板「增删只经用户端点,系统代码路径绝不写入」);本模块不另设
「系统自动加自选」的入口,单测断言 API 端点是唯一的写入通道。

同花顺无自选官方 API,对账一律走「PC 端导出 txt 文件」离线比对(决策写死,拒绝
模拟登录路线——账号封禁风险,§五 v1.1-C.4)。Neckline `ts_code` 本身已是
`XXXXXX.SH/SZ/BJ` 格式(`neckline.data.tushare_client.to_ts_code` 同一套映射,
与 LinoN v1.3.0 `thsMarketSuffix` 的导出格式经验一致——同花顺自选导入/导出正是
这个「裸6位+市场后缀」格式),故导出侧几乎是恒等操作;解析侧复用
`neckline.review.parse.normalize_ts_code`(它已复用 `sentinel.quotes.to_symbol`/
`data.board.classify_by_code` 单一源判定前缀),不另造正则。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from neckline.db import connection, init_schema
from neckline.review.parse import clean_str, normalize_ts_code

MAX_WATCHLIST_SIZE = 30

# source 留痕枚举(纯审计,不影响任何判定分支)。
SOURCE_MANUAL = "manual"
SOURCE_CANDIDATE = "candidate"
SOURCE_INQUIRY = "inquiry"
SOURCE_THS_IMPORT = "ths_import"
VALID_SOURCES = (SOURCE_MANUAL, SOURCE_CANDIDATE, SOURCE_INQUIRY, SOURCE_THS_IMPORT)


class WatchlistFullError(Exception):
    """自选池已达 `MAX_WATCHLIST_SIZE` 上限,API 层据此转 422。"""


@dataclass
class WatchlistItem:
    ts_code: str
    name: str
    added_at: str
    source: str
    note: Optional[str]
    pinned: bool
    updated_at: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "ts_code": self.ts_code, "name": self.name, "added_at": self.added_at,
            "source": self.source, "note": self.note, "pinned": self.pinned,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_SELECT_COLS = "ts_code, name, added_at, source, note, pinned, updated_at"


def _row_to_item(row) -> WatchlistItem:
    return WatchlistItem(
        ts_code=row[0], name=row[1] or row[0], added_at=row[2], source=row[3],
        note=row[4], pinned=bool(row[5]), updated_at=row[6],
    )


# —— CRUD(唯一写入通道,§v1.1-C.1)——————————————————————————————————————

def list_watchlist(db_path: Optional[Path] = None) -> List[WatchlistItem]:
    """全部自选池条目,按加入时间升序。供 CRUD 列表展示 / 报告体检 / 哨兵关注池
    并入读取——系统代码路径只应调用本函数或 `list_watchlist_codes`。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(f"SELECT {_SELECT_COLS} FROM watchlist ORDER BY added_at, ts_code").fetchall()
    return [_row_to_item(r) for r in rows]


def list_watchlist_codes(db_path: Optional[Path] = None) -> List[str]:
    """只要 `ts_code` 列表(供 `sentinel/universe.py` 并入关注池,轻量,不必构造
    完整 dataclass)。"""
    return [w.ts_code for w in list_watchlist(db_path)]


def get_watchlist_item(ts_code: str, db_path: Optional[Path] = None) -> Optional[WatchlistItem]:
    ts_code = normalize_ts_code(ts_code)
    init_schema(db_path)
    with connection(db_path) as conn:
        row = conn.execute(f"SELECT {_SELECT_COLS} FROM watchlist WHERE ts_code=?", (ts_code,)).fetchone()
    return _row_to_item(row) if row else None


def add_watchlist(
    ts_code: str,
    name: Optional[str] = None,
    note: Optional[str] = None,
    source: str = SOURCE_MANUAL,
    db_path: Optional[Path] = None,
) -> WatchlistItem:
    """加一只自选(**用户显式调用专用**)。已存在该代码 → 幂等更新 name/note(不算
    「新增」,不占用额度、不报满,重复加不炸);真正的新增超过 `MAX_WATCHLIST_SIZE`
    → 抛 `WatchlistFullError`,API 层转 422。`source` 非法值 → 静默归一为
    `SOURCE_MANUAL`(不因展示性留痕字段的脏输入拒绝整个请求)。"""
    ts_code = normalize_ts_code(ts_code)
    if not ts_code:
        raise ValueError("ts_code 不能为空或无法识别")
    src = source if source in VALID_SOURCES else SOURCE_MANUAL
    init_schema(db_path)
    now = _now()
    clean_name = clean_str(name) or None
    clean_note = clean_str(note) or None
    with connection(db_path) as conn:
        existing = conn.execute("SELECT ts_code FROM watchlist WHERE ts_code=?", (ts_code,)).fetchone()
        if existing is None:
            count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
            if count >= MAX_WATCHLIST_SIZE:
                raise WatchlistFullError(f"自选池已达上限 {MAX_WATCHLIST_SIZE} 只,请先移除再添加。")
            conn.execute(
                "INSERT INTO watchlist (ts_code, name, added_at, source, note, pinned, updated_at) "
                "VALUES (?,?,?,?,?,0,?)",
                (ts_code, clean_name or ts_code, now, src, clean_note, now),
            )
        else:
            conn.execute(
                "UPDATE watchlist SET name=COALESCE(?, name), note=?, updated_at=? WHERE ts_code=?",
                (clean_name, clean_note, now, ts_code),
            )
    item = get_watchlist_item(ts_code, db_path=db_path)
    assert item is not None  # 刚写入,必然读得到
    return item


def remove_watchlist(ts_code: str, db_path: Optional[Path] = None) -> bool:
    """删一只自选(**用户显式调用专用**)。返回是否真的删到了(不存在 → False,
    API 层据此 404)。"""
    ts_code = normalize_ts_code(ts_code)
    init_schema(db_path)
    with connection(db_path) as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE ts_code=?", (ts_code,))
        return cur.rowcount > 0


def set_pinned(ts_code: str, pinned: bool, db_path: Optional[Path] = None) -> bool:
    """切换 pinned(用户点名「每日必审」,§v1.1-C.3 LLM 只审 changed∪pinned 的判据
    之一)。返回是否命中该代码(不存在 → False,API 层据此 404)。"""
    ts_code = normalize_ts_code(ts_code)
    init_schema(db_path)
    now = _now()
    with connection(db_path) as conn:
        cur = conn.execute(
            "UPDATE watchlist SET pinned=?, updated_at=? WHERE ts_code=?",
            (1 if pinned else 0, now, ts_code),
        )
        return cur.rowcount > 0


# —— 同花顺 txt 互转 / 对账(§v1.1-C.4)—————————————————————————————————————

_CODE_LINE_RE = re.compile(r"^\s*(\d{6})")


def _decode_ths_bytes(data: bytes) -> str:
    """同花顺 PC 端(Windows 软件)导出 txt 的真实编码未经活体验证(留 v1.1-H 用
    真实导出文件核对),保守按 UTF-8(含 BOM)→ GBK 顺序尝试解码,两者都解不出时
    用 UTF-8 + `errors="ignore"` 兜底——不因编码猜错抛崩,优雅降级。"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="ignore")


def parse_ths_txt(data: bytes) -> List[str]:
    """解析同花顺自选导出 txt(plan 原文「一行一代码」)。每行只取行首连续 6 位
    数字,忽略其后任何内容——同一份宽松策略天然兼容"裸6位"("600000")、"6位+
    市场后缀"("600000.SH")、"代码+其它列"("600000\\t浦发银行")等未经活体验证前
    无法确定的真实变体;取到的裸代码经 `normalize_ts_code` 转回 Neckline `ts_code`
    (**不新造前缀判定正则**,复用它已复用的 `sentinel.quotes.to_symbol`/
    `data.board.classify_by_code` 单一源)。空行 / 无法识别出 6 位代码的行直接跳过,
    不抛异常、不计入 warning(纯文本 txt,不像 4D xlsx 那样有"表头/sheet"概念可
    报告)。返回去重后的 `ts_code` 列表,保持文件内首次出现的顺序。"""
    text = _decode_ths_bytes(data)
    codes: List[str] = []
    seen = set()
    for line in text.splitlines():
        line = clean_str(line)
        if not line:
            continue
        m = _CODE_LINE_RE.match(line)
        if not m:
            continue
        code = normalize_ts_code(m.group(1))
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def export_ths_txt(codes: List[str]) -> str:
    """导出为同花顺可导入 txt(plan C.4「Neckline 自选导出为同花顺可导入格式」)。
    Neckline `ts_code` 已是同花顺认得的 `XXXXXX.SH/SZ/BJ` 格式,一行一代码原样
    输出,不重新推导后缀(单一事实源:后缀映射唯一权威在
    `neckline.data.tushare_client.to_ts_code`,本函数不重新判断)。"""
    lines = [c for c in codes if c]
    return "\n".join(lines) + ("\n" if lines else "")


def reconcile_ths(ths_codes: List[str], neckline_codes: List[str]) -> Dict[str, List[str]]:
    """两边差集(plan C.4「差异对账端点(两边差集)」)。两侧输入均已是 Neckline
    `ts_code` 格式(`ths_codes` 来自 `parse_ths_txt`,已归一;`neckline_codes` 来自
    `list_watchlist_codes`,建表时已归一)——直接比较,不再二次归一。对齐动作
    (加/删)由客户端按差异结果调 CRUD 端点,本函数只算差集、不做任何写入
    (plan「对齐动作由客户端按差异调 C.1 CRUD,后端不批量自动改」)。"""
    ths_set, nk_set = set(c for c in ths_codes if c), set(c for c in neckline_codes if c)
    return {
        "onlyInThs": sorted(ths_set - nk_set),
        "onlyInNeckline": sorted(nk_set - ths_set),
        "both": sorted(ths_set & nk_set),
    }


__all__ = [
    "MAX_WATCHLIST_SIZE",
    "SOURCE_MANUAL", "SOURCE_CANDIDATE", "SOURCE_INQUIRY", "SOURCE_THS_IMPORT",
    "WatchlistFullError", "WatchlistItem",
    "list_watchlist", "list_watchlist_codes", "get_watchlist_item",
    "add_watchlist", "remove_watchlist", "set_pinned",
    "parse_ths_txt", "export_ths_txt", "reconcile_ths",
]
