"""V2.3.3 竞价层的**结构性守门**(§五 〇b-2/3 + §3.13-B 的代偿闸门)。

四条,每一条都是"靠自觉就会失守"的那种:

  1. **依赖方向单向 `auction → sentinel → selection`** —— `sentinel/**` 与
     `selection/**` 零 import `neckline.auction`;`auction/**` 零反向 import
     `review/**`。
  2. **竞价层不接任何交易动作** —— 零 import 持仓 / 开仓 / 交易时钟;零写
     `baskets` / `tier_history` / `basket_cards` / `selection_clock` 四张正式结论表。
  3. 🔴 **机械列永不 UPDATE 的列白名单** —— 两张新表**刻意不进**
     `_APPEND_ONLY_TABLES`(两阶段生命周期,同 `trade_clock` 先例),**缺了这条守门,
     那一步就是个后门**(§3.13-B 逐字)。
  4. **⛔ 全仓禁止 `qualified` / `wait` / `cancelled` 作为竞价结论码**(K8 §二十 明令)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PKG = _REPO / "neckline"
_AUCTION = _PKG / "auction"
_SCRIPTS = _REPO / "scripts"

#: 🔴 「机械列永不改」这条闸门的**扫描域**:`neckline/**` **与 `scripts/**`。
#: ⚠ 只扫 `neckline/**` 是个洞(复审 🟡-3):半年后一个「修数据」脚本放进
#: `scripts/oneoff/` 就能就地改写那份 9:26 的冻结事实,而全量套件依然全绿。
_SQL_SCAN_ROOTS = (_PKG, _SCRIPTS)


def _sql_scan_files():
    for root in _SQL_SCAN_ROOTS:
        if root.exists():
            yield from sorted(root.rglob("*.py"))


def _module_body(path: Path) -> str:
    """去掉 docstring 与 `#` 注释后的代码文本(禁令本身就写在注释里,裸 grep 会把
    「写明禁止」当成「违反禁止」—— 那种守门只会逼人删注释,反而更糟)。"""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                c = body[0].value
                for i in range(c.lineno - 1, (c.end_lineno or c.lineno)):
                    lines[i] = ""
    return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))


def _imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node.module


# ══════════════════════════════════════════════════════════════════════════
# 1. 依赖方向(**正面钉死**,§五 ②-A / §3.13-A)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("pkg", ["sentinel", "selection"])
def test_sentinel_and_selection_never_import_the_auction_package(pkg):
    """🔴 反向 import = 环,而且会把 LLM 拖进「纯规则零 LLM」的 `sentinel/` 包 ——
    那个包的身份由既有守门锁着,破它等于把身份声明作废。"""
    offenders = []
    for p in sorted((_PKG / pkg).rglob("*.py")):
        for mod in _imported_modules(p):
            if mod == "neckline.auction" or mod.startswith("neckline.auction."):
                offenders.append((str(p.relative_to(_REPO)), mod))
    assert offenders == [], f"依赖方向被反向了(auction → {pkg} 是单向的):{offenders}"


def test_auction_never_imports_review():
    """`review/selection_clock.py` 读 `auction/store.py` 是允许的(review 不在
    `auction → sentinel → selection` 那条链上,不成环);但 **`auction/**` ⛔ 不许
    反向 import `review/**`**。"""
    offenders = []
    for p in sorted(_AUCTION.rglob("*.py")):
        for mod in _imported_modules(p):
            if mod == "neckline.review" or mod.startswith("neckline.review."):
                offenders.append((str(p.relative_to(_REPO)), mod))
    assert offenders == [], f"auction 反向 import 了 review:{offenders}"


# ══════════════════════════════════════════════════════════════════════════
# 2. 竞价层不接任何交易动作(§五 〇b-2,**结构性保证**)
# ══════════════════════════════════════════════════════════════════════════

_BANNED_TRADING_MODULES = (
    "neckline.sentinel.positions",
    "neckline.positions_entry",
    "neckline.review.trade_clock",
)


def test_auction_package_imports_no_trading_action_module():
    offenders = []
    for p in sorted(_AUCTION.rglob("*.py")):
        for mod in _imported_modules(p):
            for banned in _BANNED_TRADING_MODULES:
                if mod == banned or mod.startswith(banned + "."):
                    offenders.append((str(p.relative_to(_REPO)), mod))
    assert offenders == [], f"竞价层碰了交易动作模块:{offenders}"


_FORMAL_RESULT_TABLES = ("baskets", "tier_history", "basket_cards", "selection_clock")


def test_auction_package_writes_none_of_the_four_formal_result_tables():
    """⛔ 零写 `baskets` / `tier_history` / `basket_cards` / `selection_clock`。
    ⚠ **读是允许的**(卡上的冻结失效位、篮子引擎归属都得读),禁的是**写**。"""
    offenders = []
    write_verbs = ("INSERT INTO ", "INSERT OR IGNORE INTO ", "INSERT OR REPLACE INTO ",
                   "REPLACE INTO ", "UPDATE ", "DELETE FROM ")
    for p in sorted(_AUCTION.rglob("*.py")):
        body = _module_body(p)
        upper = body.upper()
        for verb in write_verbs:
            for m in re.finditer(re.escape(verb), upper):
                tail = upper[m.end():m.end() + 40].strip()
                for tbl in _FORMAL_RESULT_TABLES:
                    if tail.startswith(tbl.upper()):
                        offenders.append((str(p.relative_to(_REPO)), verb.strip(), tbl))
    assert offenders == [], f"竞价层写了正式结论表:{offenders}"


# ══════════════════════════════════════════════════════════════════════════
# 3. 🔴 机械列永不 UPDATE(两张新表不进 `_APPEND_ONLY_TABLES` 的**代偿闸门**)
# ══════════════════════════════════════════════════════════════════════════

#: ⚠ **V2.4.0 P2.4 的四个新列全部登记在这里**(`quote_quality_json` +
#: `critical_data_quality` / `context_data_quality` / `quality_detail_json`)——
#: 它们是机械冻结列,⛔ 一个都不许出现在 LLM 白名单里(§3.14-E 逐字)。
_MECHANICAL_REPORT_COLUMNS = (
    "trade_date", "d0_date", "source", "captured_at", "requested_codes", "fetched_codes",
    "missing_codes_json", "conflict_codes_json", "data_quality", "index_gaps_json",
    "market_anchors_json", "baskets_covered", "quote_quality_json", "created_at",
)
_MECHANICAL_VERDICT_COLUMNS = (
    "basket_id", "trade_date", "d0_date", "basket_key", "name", "covered_tier",
    "engine_code", "engine_version", "skeleton_version", "regime_at_d0", "data_quality",
    "members_json", "sector_sync_json", "rel_strength_json", "history_json",
    "hit_invalidation_json", "plan_consistency_json",
    "critical_data_quality", "context_data_quality", "quality_detail_json", "created_at",
)


def _updated_columns(sql: str, table: str):
    """`UPDATE <table> SET a=?, b=COALESCE(?, b) WHERE …` → `{a, b}`。

    ⚠ 手工切而不是一条正则:`SET … WHERE` 的懒惰匹配会在含 `COALESCE(?, x)` 的语句上
    把 `WHERE trade_date` 也当成一列(施工时真踩过一次)。
    """
    up = sql.upper()
    i = up.find(f"UPDATE {table.upper()}")
    if i < 0:
        return set()
    j = up.find(" SET ", i)
    if j < 0:
        return set()
    k = up.find(" WHERE ", j)
    clause = sql[j + 5: k if k > 0 else len(sql)]
    cols = set()
    depth = 0
    seg = ""
    for ch in clause:                       # 按顶层逗号切(`COALESCE(?, x)` 里的逗号不算)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.add(seg)
            seg = ""
        else:
            seg += ch
    cols.add(seg)
    return {c.split("=")[0].strip() for c in cols if "=" in c and c.split("=")[0].strip()}


def _execute_first_args(path: Path):
    """所有 `xxx.execute*(...)` 的第一个实参节点。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in ("execute", "executemany", "executescript"):
            continue
        if node.args:
            yield node


def _sql_literals(path: Path):
    """把文件里所有 `conn.execute(...)` 的 SQL 字面量(含 f-string 的静态片段)拼出来。"""
    return [s for s in (_flatten_str(n.args[0]) for n in _execute_first_args(path)) if s]


def _flatten_str(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(_flatten_str(v) if not isinstance(v, ast.FormattedValue) else " "
                       for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _flatten_str(node.left) + _flatten_str(node.right)
    return ""


@pytest.mark.parametrize("table,mechanical,whitelist_name", [
    ("auction_reports", _MECHANICAL_REPORT_COLUMNS, "LLM_UPDATABLE_REPORT_COLUMNS"),
    ("auction_verdicts", _MECHANICAL_VERDICT_COLUMNS, "LLM_UPDATABLE_VERDICT_COLUMNS"),
])
def test_second_stage_update_never_touches_a_mechanical_column(table, mechanical, whitelist_name):
    """🔴 §3.13-B:两张表**刻意不进** `_APPEND_ONLY_TABLES`(它们是有生命周期的对象,
    同 `trade_clock` 先例)。**代偿闸门就是这一条** —— 第二阶段只准 UPDATE LLM 列白名单,
    机械列一个都不许动。缺了它,上面那一步就是个后门。"""
    from neckline.auction import store as astore

    whitelist = set(getattr(astore, whitelist_name))
    assert not (whitelist & set(mechanical)), "白名单里混进了机械列"
    pattern = re.compile(rf"UPDATE\s+{table}\b", re.IGNORECASE)
    touched = set()
    for p in _sql_scan_files():
        seen_in_sql = 0
        for sql in _sql_literals(p):
            if pattern.search(sql):
                seen_in_sql += 1
                cols = _updated_columns(sql, table)
                # 🔴 解析不出列 = **这条 SQL 是动态拼的** → 本守门对它失明,等于没有。
                # ⛔ 一律要求静态字面量(「本次不改」用 `COALESCE(?, 原值)` 表达)。
                assert cols, (
                    f"{p.relative_to(_REPO)} 的 UPDATE {table} 不是静态字面量,"
                    f"本守门解析不出列集合 —— 动态拼 SET 会让这一层失明,⛔ 不许。")
                assert cols <= whitelist, (
                    f"{p.relative_to(_REPO)} 的 UPDATE {table} 动了白名单外的列:"
                    f"{sorted(cols - whitelist)}")
                touched |= cols
        # 🔴 **失明自检**(复审 🟡-3 逮到的洞):`sql = "UPDATE …" ; conn.execute(sql)`
        # 这种「先赋给变量再执行」的写法,AST 那一路**一条都看不见**(`_sql_literals`
        # 返回 `[]`)—— 守门于是安安静静地全绿。所以再按**整份文件文本**数一遍:
        # 文本里有、AST 里没有 = 我解析不到 = **失明**,⛔ 一律报红。
        in_text = len(pattern.findall(_module_body(p)))
        assert in_text <= seen_in_sql, (
            f"{p.relative_to(_REPO)}:文本里有 {in_text} 处 `UPDATE {table}`,"
            f"但只有 {seen_in_sql} 处是 `execute()` 的静态 SQL 实参 —— "
            f"本守门对其余那些**失明**(SQL 先赋给变量再执行 / 动态拼),⛔ 不许。")
    assert touched, f"全仓找不到任何 UPDATE {table} 语句 —— 两阶段写的第二阶段丢了?"


@pytest.mark.parametrize("table", ["auction_reports", "auction_verdicts"])
def test_nothing_anywhere_deletes_from_the_two_auction_tables(table):
    """🔴 复审 🟡-3 第三个洞:两张表不在 `_APPEND_ONLY_TABLES` 里,而「机械列永不
    UPDATE」那条守门只管 UPDATE —— **`DELETE FROM` 完全没人管**。

    这两张表是竞价复盘**唯一的原始证据**:9:26 那一刻看到了什么、系统当时怎么说。
    删掉 = 那天的账永久消失,而且不会有任何东西报错。
    ⛔ 要修数据就新起一天的行 / 走人工 SQL 并留档,**不在代码里删**。
    """
    pattern = re.compile(rf"DELETE\s+FROM\s+{table}\b", re.IGNORECASE)
    offenders = [str(p.relative_to(_REPO)) for p in _sql_scan_files()
                 if pattern.search(_module_body(p))]
    assert offenders == [], f"有代码在删 {table} 的行:{offenders}"


def test_the_store_module_only_ever_executes_static_sql_literals():
    """🔴 复审 🟡-3 的**最省事也最严**的那条:`auction/store.py` 里每一个
    `execute*()` 的第一个实参都必须是**静态字符串字面量**(常量 / f-string 静态片段 /
    字面量相加)。

    ⚠ 为什么单独把这个模块钉死:它是两张表的**读写唯一通道**,而「机械列永不 UPDATE」
    那条闸门是按 AST 取 SQL 字面量再解析列集合的 —— 只要有人在这里写
    `sql = "UPDATE auction_reports SET " + ", ".join(...)`,闸门当场失明。
    与其事后靠正则去追,不如在**源头**要求这个模块里一条动态 SQL 都不许有。
    """
    from neckline.auction import store as astore

    path = Path(astore.__file__)
    offenders = []
    for node in _execute_first_args(path):
        arg = node.args[0]
        if not _flatten_str(arg) or isinstance(arg, ast.Name):
            offenders.append(node.lineno)
    assert offenders == [], (
        f"neckline/auction/store.py 第 {offenders} 行把非字面量喂给了 execute() —— "
        f"「机械列永不 UPDATE」那条闸门对它失明,⛔ 不许。")


def test_finalize_is_idempotent_by_the_pending_guard():
    """幂等闸:`finalize_*` 一律带 `WHERE … llm_stage='pending'` —— 与「`llm.explain()`
    的签名里根本没有 store 句柄(工作线程只写内存 box,**够不着**库)」构成**双保险**,
    让 9:29 之后才回来的结论写不进去。
    ⚠ 施工图 ④-B 提到的 `deadline_passed` 标志位没有落地、也不该落地(复审 🔵-1)——
    现有这两条不依赖任何人记得检查一个布尔。"""
    from neckline.auction import store as astore

    body = _module_body(Path(astore.__file__))
    for stmt in ("UPDATE auction_reports", "UPDATE auction_verdicts"):
        idx = body.find(stmt)
        assert idx >= 0, stmt
        assert "llm_stage='" in body[idx:idx + 900], f"{stmt} 缺 pending 幂等闸"


# ══════════════════════════════════════════════════════════════════════════
# 4. ⛔ 全仓禁止 `qualified` / `wait` / `cancelled` 作为竞价结论码(K8 §二十 明令)
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("banned", ["qualified", "wait", "cancelled"])
def test_auction_layer_never_emits_intraday_trading_states(banned):
    """K8 §二十 逐字:「不输出 `qualified`、`wait`、`cancelled` 等盘中交易状态」。
    竞价结论只有 `confirm` / `neutral` / `veto`(+「待解释」这个"没解释"状态)。"""
    offenders = []
    for p in sorted(_AUCTION.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        docs = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                b = getattr(node, "body", None) or []
                if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant):
                    docs.add(id(b[0].value))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docs and node.value.strip() == banned):
                offenders.append((str(p.relative_to(_REPO)), node.lineno))
    assert offenders == [], f"竞价层出现了被 K8 明令禁止的盘中交易状态码 {banned!r}:{offenders}"


def test_the_three_verdict_codes_are_exactly_what_k8_declared():
    from neckline import auction

    assert auction.VERDICTS == ("confirm", "neutral", "veto")
    assert auction.VERDICT_PENDING_EXPLANATION == "pending_explanation"


def test_manual_note_text_is_a_single_source_and_matches_k8_verbatim():
    """🔴 §五 ③-D:小纸条是 K8.md §二十 的**固定文案**,服务端下发、客户端原样透传。
    ⛔ 客户端不许自己写这段字(同 `BASKET_CARD_DISCLAIMER` 既有体例)。"""
    from neckline import auction

    note = auction.AUCTION_MANUAL_NOTE
    for frag in ("9:20—9:25 虚拟开盘价是否稳定", "匹配量是否持续增加",
                 "未匹配量偏买方还是卖方", "尾段明显撤弱", "保持谨慎"):
        assert frag in note, frag
    # 全仓只此一份(⛔ 别抄第二份)
    hits = [p for p in sorted(_PKG.rglob("*.py"))
            if "请在同花顺看 9:20—9:25" in p.read_text(encoding="utf-8")]
    assert [p.name for p in hits] == ["__init__.py"], hits
