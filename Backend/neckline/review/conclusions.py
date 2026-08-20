"""结论存档(V2.5.0 S11,架构 §六 第 3 件事,PROJECT_PLAN §5.9)。

「保存本周复盘结论,**下周可检索**。」装订好的材料(`review/bindery.py`)由用户带到
聊天框做对话与总结,总结**回存到这里**;系统不做对话(架构 §六「系统之外」)。

🔴 **append-only 版本化,⛔ 没有 UPDATE、⛔ 没有 DELETE**。同一周改一次结论 = 写
`version + 1` 的**新行**。理由不是洁癖:复盘结论是**下一周做决定的依据**,
「我上周到底是怎么想的」被静默改写之后就再也查不回来了。本模块里因此根本不存在
那两条 SQL(守门单测扫本文件),不是靠谁记得别写。

🔴 **本层零 LLM**(架构 §六)。存档就是存档:收什么存什么,不总结、不改写、不评价。

⚠ **与 `reviews.material` 刻意不是一回事**:那一列装的是**系统算出来的**对账叙述
(`review/material.py`,每次上传交割单幂等覆盖);本表装的是**用户想出来的**结论。
两者混进一列 = 下次上传交割单就把用户写的东西冲掉了。

⚠ **检索只做「找得到」,不做「排得准」**:`search()` 是 `LIKE` 直筛(标题 / 正文 /
标签),按周降序返回**每周最新版**。⛔ 不上全文索引、⛔ 不做相关度打分 ——
这是个一周一条、一年 52 条的表,给它上 FTS 是在解决一个不存在的问题。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from neckline.db import connection, init_schema

logger = logging.getLogger(__name__)

TABLE = "review_conclusions"

#: 默认作者标签。⛔ 不留空串:「谁写的」空着,一年后就分不清是我写的还是导入的。
AUTHOR_USER = "user"

#: 一条结论的正文上限(字符)。⚠ 工程容量上限,不是策略参数 —— 它挡的是把整份
#: 聊天记录粘进来(那属于材料,不属于结论)。超了**当场拒绝并说清楚**,⛔ 不静默截断
#: (截断会让用户以为存进去了,而丢掉的恰恰是结尾那句结论)。
MAX_BODY_CHARS = 20000
MAX_TITLE_CHARS = 200
MAX_TAGS = 20
MAX_TAG_CHARS = 40

_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


class ConclusionInvalid(ValueError):
    """入参不合法。⚠ 一律**在写之前**抛,⛔ 不写半行再回滚。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Conclusion:
    week: str
    version: int
    title: str
    body: str
    tags: Sequence[str] = field(default_factory=tuple)
    author: str = AUTHOR_USER
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "week": self.week, "version": self.version, "title": self.title,
            "body": self.body, "tags": list(self.tags),
            "author": self.author, "createdAt": self.created_at,
        }


def _validate(week: str, title: str, body: str, tags: Sequence[str], author: str) -> List[str]:
    """⛔ 校验一次性把**全部**问题列出来,不是抛第一个 —— 用户改一轮就该能过。"""
    bad: List[str] = []
    if not _WEEK_RE.match(week or ""):
        bad.append(f"week 必须是 ISO 周 'YYYY-Www'(如 2026-W34),收到 {week!r}")
    if not (title or "").strip():
        bad.append("title 不能为空 —— 检索列表上看到的就是它")
    elif len(title) > MAX_TITLE_CHARS:
        bad.append(f"title 超过 {MAX_TITLE_CHARS} 字(收到 {len(title)})")
    if not (body or "").strip():
        bad.append("body 不能为空 —— 空结论与「这周没写结论」不是一回事,后者别存")
    elif len(body) > MAX_BODY_CHARS:
        bad.append(
            f"body 超过 {MAX_BODY_CHARS} 字(收到 {len(body)})。整段聊天记录属于"
            "**材料**不属于结论;⛔ 本模块不静默截断,请自己收一收再存")
    if len(tags) > MAX_TAGS:
        bad.append(f"标签最多 {MAX_TAGS} 个(收到 {len(tags)})")
    for t in tags:
        if not isinstance(t, str) or not t.strip():
            bad.append(f"标签不能为空:{t!r}")
        elif len(t) > MAX_TAG_CHARS:
            bad.append(f"标签 {t!r} 超过 {MAX_TAG_CHARS} 字")
    if not (author or "").strip():
        bad.append("author 不能为空")
    return bad


def next_version(week: str, *, db_path: Optional[Path] = None) -> int:
    """下一个版本号(现有最大 + 1;没有则 1)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(f"SELECT MAX(version) FROM {TABLE} WHERE week=?", (week,)).fetchone()
    return int(r[0]) + 1 if r and r[0] is not None else 1


def save(
    week: str, title: str, body: str, *,
    tags: Sequence[str] = (), author: str = AUTHOR_USER,
    db_path: Optional[Path] = None,
) -> Conclusion:
    """存一版结论。**`INSERT`,⛔ 不是 `INSERT OR REPLACE`** —— 版本号由本函数取,
    撞键说明并发写了两版,那要抛出来给人看,不该静默盖掉一版。"""
    tags = tuple(tags or ())
    problems = _validate(week, title, body, tags, author)
    if problems:
        raise ConclusionInvalid("; ".join(problems))
    init_schema(db_path)
    version = next_version(week, db_path=db_path)
    now = _now()
    with connection(db_path) as conn:
        conn.execute(
            f"INSERT INTO {TABLE} (week, version, title, body, tags_json, author, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (week, version, title.strip(), body,
             json.dumps(list(tags), ensure_ascii=False), author.strip(), now),
        )
    return Conclusion(week=week, version=version, title=title.strip(), body=body,
                      tags=tags, author=author.strip(), created_at=now)


def _row(r: Sequence[Any]) -> Conclusion:
    try:
        tags = tuple(json.loads(r[4]))
    except (TypeError, ValueError):
        logger.warning("[conclusions] %s v%s 的 tags_json 解不出,按空标签读回", r[0], r[1])
        tags = ()
    return Conclusion(week=r[0], version=int(r[1]), title=r[2], body=r[3],
                      tags=tags, author=r[5], created_at=r[6])


_SELECT = "week, version, title, body, tags_json, author, created_at"


def load_latest(week: str, *, db_path: Optional[Path] = None) -> Optional[Conclusion]:
    """某周的**最新版**结论。`None` = 那周还没写过结论(完全正常的场景,
    ⛔ 别把它渲染成「这周没问题」)。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        r = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} WHERE week=? ORDER BY version DESC LIMIT 1",
            (week,),
        ).fetchone()
    return None if r is None else _row(r)


def load_versions(week: str, *, db_path: Optional[Path] = None) -> List[Conclusion]:
    """某周的**全部版本**(升序)。改过几次、每次改了什么,在这里看得见。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} WHERE week=? ORDER BY version", (week,)
        ).fetchall()
    return [_row(r) for r in rows]


def list_latest(
    *, limit: int = 20, db_path: Optional[Path] = None
) -> List[Conclusion]:
    """最近几周的结论(每周只出**最新版**,按周降序)。「下周可检索」的默认入口。"""
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} c WHERE c.version = "
            f"(SELECT MAX(version) FROM {TABLE} q WHERE q.week=c.week) "
            f"ORDER BY c.week DESC LIMIT ?",
            (max(int(limit), 1),),
        ).fetchall()
    return [_row(r) for r in rows]


def search(
    query: str, *, limit: int = 20, db_path: Optional[Path] = None
) -> List[Conclusion]:
    """在标题 / 正文 / 标签里直筛 `query`(大小写不敏感),每周只出最新版,按周降序。

    空 query → 退回 `list_latest()`(⛔ 不返回空列表:「搜了个空串」与
    「搜了但没搜到」不是一回事)。
    """
    q = (query or "").strip()
    if not q:
        return list_latest(limit=limit, db_path=db_path)
    like = f"%{q}%"
    init_schema(db_path)
    with connection(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM {TABLE} c WHERE c.version = "
            f"(SELECT MAX(version) FROM {TABLE} q WHERE q.week=c.week) "
            f"AND (c.title LIKE ? OR c.body LIKE ? OR c.tags_json LIKE ?) "
            f"ORDER BY c.week DESC LIMIT ?",
            (like, like, like, max(int(limit), 1)),
        ).fetchall()
    return [_row(r) for r in rows]


__all__ = [
    "TABLE", "AUTHOR_USER",
    "MAX_BODY_CHARS", "MAX_TITLE_CHARS", "MAX_TAGS", "MAX_TAG_CHARS",
    "ConclusionInvalid", "Conclusion",
    "next_version", "save", "load_latest", "load_versions", "list_latest", "search",
]
