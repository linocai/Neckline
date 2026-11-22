"""Qihuo V2 schema 3 严格校验、看板投影与只读 MCP 视图（完全同源，0.0 与 null 绝不混淆）。"""
from __future__ import annotations

from datetime import date, datetime
import html
import math
import os
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from neckline.morning.schedule import CST
from neckline.qihuo import settings as qihuo_settings
from neckline.qihuo.source import QihuoError, redact_upstream

# 严格线性引用式定义：标签最多跨一行且单行≤500字符，避免缺 `]` 时跨多行平方级回溯
_MD_REF_DEF = re.compile(
    r"(?m)^([ \t]{0,3})\[([^\]\n]{1,500}(?:\n[^\]\n]{1,500})?)\]:[ \t]*(?:\n[ \t]*)?(<[^<>\n]*>|\S+)"
    r"([ \t]*(?:(?:\n[ \t]*)?(?:\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|\((?:\\.|[^)\\\n])*\))?)?[ \t]*)$"
)
# 按完整非空白 token 捕获裸 URL，绝不把 `’` 或第二个 `http(s)://` 拆成片段再拼回
_BARE_HTTP_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_CJK_URL_SEP = re.compile(r"([，。；、！？（）【】《》「」『』〈〉〔〕]+)")
_CM_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*)>")
_CM_EMAIL_AUTOLINK = re.compile(
    r"<([A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)>"
)
_TOKENIZED_READ_PATH = re.compile(r"/(?:(?:qihuo|kx)/)?read/[A-Za-z0-9_-]+(?:/[^\s)\"'>]*)?", re.IGNORECASE)

SCHEMA_VERSION = 3
_VALID_COLLECT_STATUS = frozenset(("ok", "partial", "failed", "skipped", "running"))
_VALID_COL_STATES = frozenset(("ok", "failed", "empty", "skipped", "none", "pending", "stale", "partial", "unknown"))
_MAX_SAFE_INT = (1 << 63) - 1

_OPEN_RESEARCH_NOTE = (
    "本数据是 Neckline 对 Qihuo 晚间客观记录的快速基线（便于第一时间查阅仓单、会员持仓、近 15 日筛选消息与品种档案）；"
    "请结合你自身的实时搜索与分析能力，自由补充最新现货/基差、盘中突发消息、行业开工利润与一手资讯，不要局限于看板内已有内容。"
)


def _str(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _opt_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _opt_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if math.isfinite(num) else None
    return None


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -_MAX_SAFE_INT <= value <= _MAX_SAFE_INT else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        num = int(value)
        return num if -_MAX_SAFE_INT <= num <= _MAX_SAFE_INT else None
    return None


def _opt_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _figure_value_str(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value).strip()


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if len(cleaned) != 10 or cleaned[4] != "-" or cleaned[7] != "-":
        return False
    try:
        date.fromisoformat(cleaned)
    except ValueError:
        return False
    return True


_MD_ESCAPED_PUNCT = re.compile(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~])")
_CM_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")


def _decode_markdown_dest(raw: str) -> str:
    """按 CommonMark / cmark-gfm 实际链接目标解析顺序解码：先解反斜杠标点转义，再解带分号 HTML 字符引用。
    确保 `javascript\\&colon;alert(1)` 与 `javascript&colon;alert(1)` 均还原为真实协议后受白名单拦截。"""
    unescaped = _MD_ESCAPED_PUNCT.sub(r"\1", raw)
    return _CM_ENTITY.sub(lambda m: html.unescape(m.group(0)), unescaped)


def _escape_markdown_entities(url: str) -> str:
    """写回 Markdown 链接目标时将可被 CommonMark 解码的 `&...;` 编码为 `&amp;...;`，
    确保 cmark-gfm / Swift AttributedString 渲染点击目标与校验值逐字一致。"""
    return _CM_ENTITY.sub(
        lambda m: "&amp;" + m.group(0)[1:] if html.unescape(m.group(0)) != m.group(0) else m.group(0),
        url,
    )


def _escape_link_label_brackets(text: str) -> str:
    """在保留的外层链接显示文字中转义未转义的 `[` / `]`，防止 CommonMark 产生嵌套链接歧义。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch in ("[", "]"):
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _contains_nested_http_url(text: str, *, max_rounds: int = 5) -> bool:
    """逐层百分号解码检查组件中是否藏有嵌套 http(s):// 地址；超过 max_rounds 仍变化按不安全拒绝（fail-closed）。"""
    if not text:
        return False
    cur = text
    for _ in range(max_rounds + 1):
        low = cur.lower()
        if "http://" in low or "https://" in low:
            return True
        nxt = unquote(cur)
        if nxt == cur:
            return False
        cur = nxt
    return True


def safe_external_url(value: Any) -> str | None:
    """只放行带有效主机的纯净 HTTP(S) 外部链接：
    - 拒绝脚本协议、用户信息、空白/控制字符、`<` `>`、字面反斜杠 `\\`；
    - 拒绝含 `/read/` 访问码路径或已知密钥（含逐层百分号解码形态）；
    - 拒绝 path/query/fragment 中夹带任何嵌套 `http(s)://` 转引地址（零逃逸、fail-closed）。"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or any(ch.isspace() or ord(ch) < 32 or ch in "<>\\" for ch in cleaned):
        return None
    try:
        parts = urlsplit(cleaned)
        _ = parts.port
    except ValueError:
        return None
    if (
        parts.scheme.lower() not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    # 逐层百分号解码检查路径与已知密钥，过度嵌套编码一律 fail-closed
    cur_all = cleaned
    for _ in range(6):
        for secret in _known_secrets():
            if secret in cur_all:
                return None
        nxt_all = unquote(cur_all)
        if nxt_all == cur_all:
            break
        cur_all = nxt_all
    else:
        return None
    cur_path = parts.path or ""
    for _ in range(6):
        lower_path = cur_path.lower()
        if "/read/" in lower_path or lower_path.endswith("/read"):
            return None
        nxt_path = unquote(cur_path)
        if nxt_path == cur_path:
            break
        cur_path = nxt_path
    else:
        return None
    for seg in (parts.path or "", parts.query or "", parts.fragment or ""):
        if _contains_nested_http_url(seg):
            return None
    return cleaned


def _known_secrets() -> tuple[str, ...]:
    vals = (
        qihuo_settings.read_key(),
        (os.environ.get("NECKLINE_READ_KEY") or "").strip(),
        (os.environ.get("KX_READ_KEY") or "").strip(),
    )
    return tuple(v for v in vals if v)


def _scrub_plain_markdown(text: str, *, allow_links: bool = True) -> str:
    """对任意非已校验链接目标的纯文本片段（含正文、链接显示文字、引用标题）清理 CommonMark URI/邮箱自动链接、裸 URL、/read/ 路径与已知密钥。"""
    def _replace_autolink(match: re.Match[str]) -> str:
        if not allow_links:
            return ""
        target = match.group(1)
        safe = safe_external_url(target)
        return f"<{safe}>" if safe is not None else ""

    def _scrub_single_bare(candidate: str) -> str:
        low = candidate.lower()
        if low.count("http://") + low.count("https://") > 1:
            return ""
        if safe_external_url(candidate) is not None:
            return candidate
        if "@" in candidate:
            return ""
        trimmed = candidate.rstrip(".,;:!?\"')]}>")
        suffix = candidate[len(trimmed):]
        if trimmed and suffix and safe_external_url(trimmed) is not None:
            return trimmed + suffix
        return ""

    def _replace_bare_url(match: re.Match[str]) -> str:
        token = match.group(0)
        # 先对整个非空白 token 执行逐层百分号解码预检（密钥、/read/ 路径、编码隐藏协议，超过 5 轮 fail-closed）
        base_scheme_count = token.lower().count("http://") + token.lower().count("https://")
        cur_tok = token
        for round_idx in range(6):
            low_tok = cur_tok.lower()
            for secret in _known_secrets():
                if secret in cur_tok:
                    return ""
            if "/read/" in low_tok or low_tok.endswith("/read"):
                return ""
            if round_idx > 0 and (low_tok.count("http://") + low_tok.count("https://")) > base_scheme_count:
                return ""
            nxt_tok = unquote(cur_tok)
            if nxt_tok == cur_tok:
                break
            cur_tok = nxt_tok
        else:
            return ""

        parts = _CJK_URL_SEP.split(token)
        if len(parts) > 1:
            seen_non_url = False
            seen_query_url = False
            url_count = 0
            for idx, seg in enumerate(parts):
                if idx % 2 == 1:
                    continue
                low_seg = seg.lower()
                is_url_seg = low_seg.startswith("http://") or low_seg.startswith("https://")
                if is_url_seg:
                    if seen_non_url:
                        return ""
                    url_count += 1
                    if "?" in seg or "#" in seg or seg.endswith(("=", "&", "%", ":")):
                        seen_query_url = True
                else:
                    if "@" in seg or "://" in seg or "%" in seg:
                        return ""
                    seen_non_url = True
            if url_count > 1 and seen_query_url:
                return ""
            rebuilt: list[str] = []
            for idx, seg in enumerate(parts):
                if idx % 2 == 1:
                    rebuilt.append(seg)
                    continue
                low_seg = seg.lower()
                if low_seg.startswith("http://") or low_seg.startswith("https://"):
                    rebuilt.append(_scrub_single_bare(seg))
                else:
                    rebuilt.append(seg)
            rebuilt_str = "".join(rebuilt)
            # 同一非空白 token 绝不拼回多个 URL
            if rebuilt_str.lower().count("http://") + rebuilt_str.lower().count("https://") > 1:
                return ""
            return rebuilt_str
        return _scrub_single_bare(token)

    cleaned = _CM_EMAIL_AUTOLINK.sub("", text)
    cleaned = _CM_AUTOLINK.sub(_replace_autolink, cleaned)
    cleaned = _BARE_HTTP_URL.sub(_replace_bare_url, cleaned)
    cleaned = _TOKENIZED_READ_PATH.sub("", cleaned)
    for secret in _known_secrets():
        if secret in cleaned:
            cleaned = cleaned.replace(secret, "…")
    return cleaned


def _format_markdown_dest(url: str) -> str:
    """按 Markdown 链接目标语法安全写回已校验 URL：
    - 转义可能被 CommonMark 二次解码的字符引用；
    - 含不成对括号时用 `<...>` 包裹，确保渲染点击目标与校验值逐字一致。"""
    encoded = _escape_markdown_entities(url)
    depth = 0
    balanced = True
    for ch in encoded:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                balanced = False
                break
    if depth != 0:
        balanced = False
    return encoded if balanced else f"<{encoded}>"


def _scrub_optional_title(raw_title: str) -> str:
    """解析可选链接标题的定界符（\"...\" / '...' / (...)），先按 CommonMark 解码再清理内容并重新转义；内容清空时省略整个可选标题。"""
    stripped = raw_title.strip()
    if len(stripped) < 2:
        return ""
    opener = stripped[0]
    closer = ")" if opener == "(" else opener
    if opener not in ('"', "'", "(") or stripped[-1] != closer:
        return ""
    decoded = _decode_markdown_dest(stripped[1:-1]).replace("\n", " ")
    inner = _scrub_plain_markdown(decoded, allow_links=False).strip()
    if not inner:
        return ""
    escaped = inner.replace("\\", "\\\\").replace(closer, f"\\{closer}")
    if opener == "(":
        escaped = escaped.replace("(", "\\(")
    return f" {opener}{escaped}{closer}"


def _build_delimiter_pairs(
    text: str,
) -> tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """单遍 O(n) 预计算 Markdown 链接定界符配对表（识别反斜杠转义与 CommonMark 合法跨行链接）：
    - bracket_pairs / paren_pairs / next_dquote / next_squote: 允许同段落内跨行，遇空行清栈；
    - tight_paren_pairs: 遇任意空白（含换行）清栈，供非尖括号 destination 内成对括号 O(1) 跳跃；
    - next_gt: 遇换行清栈（CommonMark 尖括号目标不可跨行）。"""
    bracket_pairs: dict[int, int] = {}
    paren_pairs: dict[int, int] = {}
    tight_paren_pairs: dict[int, int] = {}
    next_gt: dict[int, int] = {}
    next_dquote: dict[int, int] = {}
    next_squote: dict[int, int] = {}
    b_stack: list[int] = []
    p_stack: list[int] = []
    tp_stack: list[int] = []
    last_lt: int | None = None
    last_dq: int | None = None
    last_sq: int | None = None
    saw_non_ws_on_line = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            saw_non_ws_on_line = True
            i += 2
            continue
        if ch == "\n":
            tp_stack.clear()
            last_lt = None
            if not saw_non_ws_on_line:
                b_stack.clear()
                p_stack.clear()
                last_dq = None
                last_sq = None
            saw_non_ws_on_line = False
            i += 1
            continue
        if ch.isspace():
            tp_stack.clear()
            i += 1
            continue
        saw_non_ws_on_line = True
        if ch == "[":
            b_stack.append(i)
        elif ch == "]" and b_stack:
            bracket_pairs[b_stack.pop()] = i
        elif ch == "(":
            p_stack.append(i)
            tp_stack.append(i)
        elif ch == ")":
            if p_stack:
                paren_pairs[p_stack.pop()] = i
            if tp_stack:
                tight_paren_pairs[tp_stack.pop()] = i
        elif ch == "<":
            last_lt = i
        elif ch == ">":
            if last_lt is not None:
                next_gt[last_lt] = i
                last_lt = None
        elif ch == '"':
            if last_dq is not None:
                next_dquote[last_dq] = i
            last_dq = i
        elif ch == "'":
            if last_sq is not None:
                next_squote[last_sq] = i
            last_sq = i
        i += 1
    return bracket_pairs, paren_pairs, tight_paren_pairs, next_gt, next_dquote, next_squote


def _skip_opt_ws_one_nl(text: str, j: int, limit: int) -> int:
    """CommonMark §6.3 可选空白：空格/制表符及至多一个换行符。"""
    while j < limit and text[j] in (" ", "\t"):
        j += 1
    if j < limit and text[j] == "\n":
        j += 1
        while j < limit and text[j] in (" ", "\t"):
            j += 1
    return j


def _parse_inline_link_at(
    text: str,
    start: int,
    limit: int,
    tables: tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int], dict[int, int], dict[int, int]],
) -> tuple[int, int, int, str, str] | None:
    """按 CommonMark 规则均摊 O(1) 解析 Markdown 行内链接 `[label](target optional_title)`（含合法跨行链接）：
    - 确认整条链接有效前不切片 `label`，杜绝嵌套长标签失败时平方级复制；
    - 正确处理 `(` 后换行、`<...>` 目标及引号标题内未配对 `(` / `)`；
    - 校验前按 CommonMark / cmark-gfm 顺序解码反斜杠标点转义与带分号 HTML 字符引用。"""
    bracket_pairs, paren_pairs, tight_paren_pairs, next_gt, next_dquote, next_squote = tables
    close_bracket = bracket_pairs.get(start)
    if close_bracket is None or close_bracket >= limit or close_bracket + 1 >= limit or text[close_bracket + 1] != "(":
        return None
    label_start = start + 1
    label_end = close_bracket
    j = _skip_opt_ws_one_nl(text, close_bracket + 2, limit)
    if j >= limit or text[j] == "\n":
        return None

    if text[j] == "<":
        gt = next_gt.get(j)
        if gt is None or gt >= limit:
            return None
        raw_target = _decode_markdown_dest(text[j + 1:gt])
        j = gt + 1
    else:
        dest_start = j
        while j < limit:
            ch = text[j]
            if ch == "\\":
                j += 2
                continue
            if ch in (" ", "\t", "\n", "\r") or ch == ")":
                break
            if ch == "(":
                close_inner = tight_paren_pairs.get(j)
                if close_inner is None or close_inner >= limit:
                    return None
                j = close_inner + 1
                continue
            j += 1
        if j == dest_start or j > limit:
            return None
        raw_target = _decode_markdown_dest(text[dest_start:j])

    ws_start = j
    j = _skip_opt_ws_one_nl(text, j, limit)
    if j < limit and text[j] == ")":
        return j + 1, label_start, label_end, raw_target, ""
    if j == ws_start or j >= limit:
        return None

    opener = text[j]
    if opener == '"':
        title_end = next_dquote.get(j)
    elif opener == "'":
        title_end = next_squote.get(j)
    elif opener == "(":
        title_end = paren_pairs.get(j)
    else:
        return None
    if title_end is None or title_end >= limit:
        return None
    raw_title = text[j:title_end + 1]
    j = _skip_opt_ws_one_nl(text, title_end + 1, limit)
    if j < limit and text[j] == ")":
        return j + 1, label_start, label_end, raw_target, raw_title
    return None


def _sanitize_inline_segment(text: str) -> str:
    """严格 O(n) 单缓冲非递归扫描行内 Markdown 链接与普通文本：
    - 复用同一套预计算配对表并向单一 `out` 缓冲追加片段，消除任何层间重复 join/复制；
    - 保留外层合法链接时，内层嵌套链接与自动链接均降级为纯文本并转义残留 `[` / `]`。"""
    tables = _build_delimiter_pairs(text)
    out: list[str] = []

    def _emit_plain(segment: str, *, allow_links: bool, escape_brackets: bool) -> None:
        cleaned = _scrub_plain_markdown(segment, allow_links=allow_links)
        if escape_brackets:
            cleaned = _escape_link_label_brackets(cleaned)
        if cleaned:
            out.append(cleaned)

    # 栈帧：[pos, plain_start, limit, allow_links, escape_brackets, wrap_suffix_or_None]
    stack: list[list[Any]] = [[0, 0, len(text), True, False, None]]
    while stack:
        frame = stack[-1]
        pos, plain_start, limit, allow_links, escape_brackets, wrap_suffix = frame
        pushed_child = False
        while pos < limit:
            if text[pos] == "\\":
                pos += 2
                continue
            if text[pos] == "[":
                parsed = _parse_inline_link_at(text, pos, limit, tables)
                if parsed is not None:
                    end, label_start, label_end, raw_target, raw_title = parsed
                    if pos > plain_start:
                        _emit_plain(
                            text[plain_start:pos],
                            allow_links=allow_links,
                            escape_brackets=escape_brackets,
                        )
                    frame[0] = end
                    frame[1] = end
                    safe = safe_external_url(raw_target) if allow_links else None
                    if safe is not None:
                        clean_title = _scrub_optional_title(raw_title)
                        out.append("[")
                        child_wrap = f"]({_format_markdown_dest(safe)}{clean_title})"
                        stack.append([label_start, label_start, label_end, False, True, child_wrap])
                    else:
                        stack.append([label_start, label_start, label_end, allow_links, escape_brackets, None])
                    pushed_child = True
                    break
            pos += 1
        if pushed_child:
            continue
        if plain_start < limit:
            _emit_plain(
                text[plain_start:limit],
                allow_links=allow_links,
                escape_brackets=escape_brackets,
            )
        stack.pop()
        if wrap_suffix is not None:
            out.append(wrap_suffix)
    return "".join(out)


def sanitize_markdown(text: str) -> str:
    """清理品种档案 Markdown 中的不安全链接（含行内、转义/实体编码目标、嵌套链接、引用式定义、自动链接与裸 URL）、
    显示文字与标题中的裸 URL、嵌套带用户信息 URL 及已知密钥。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    cursor = 0
    for match in _MD_REF_DEF.finditer(normalized):
        if match.start() > cursor:
            out.append(_sanitize_inline_segment(normalized[cursor:match.start()]))
        indent, label, raw_dest_token, raw_title = (
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
        )
        if raw_dest_token.startswith("<") and raw_dest_token.endswith(">"):
            raw_dest_token = raw_dest_token[1:-1]
        raw_target = _decode_markdown_dest(raw_dest_token)
        clean_label = _scrub_plain_markdown(label)
        safe = safe_external_url(raw_target)
        if safe is not None:
            clean_title = _scrub_optional_title(raw_title)
            out.append(f"{indent}[{_escape_link_label_brackets(clean_label)}]: {_format_markdown_dest(safe)}{clean_title}")
        else:
            out.append(f"{indent}{clean_label}")
        cursor = match.end()
    if cursor < len(normalized):
        out.append(_sanitize_inline_segment(normalized[cursor:]))
    return "".join(out)


def _valid_iso_timestamp(value: Any) -> bool:
    """严格 ISO-8601 时间戳（必须包含日期与时间部分，不接受仅含日期的字符串）。"""
    if not isinstance(value, str):
        return False
    cleaned = value.strip()
    if len(cleaned) < 16 or ("T" not in cleaned and " " not in cleaned):
        return False
    try:
        datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _reject_non_finite(obj: Any) -> None:
    """递归校验 JSON 对象不含 NaN / Infinity，避免投影通过后响应序列化失败。"""
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise QihuoError("qihuo_bad_board", "Qihuo 数据包含非有限数值（NaN/Infinity），已拒绝。")
        return
    if isinstance(obj, dict):
        for val in obj.values():
            _reject_non_finite(val)
    elif isinstance(obj, list):
        for item in obj:
            _reject_non_finite(item)


def require_bundle(raw: object) -> dict[str, Any]:
    """严格校验 Qihuo V2 只读 JSON（仅接受 schema_version == 3，不硬解未知版本）。"""
    if not isinstance(raw, dict):
        raise QihuoError("qihuo_unsupported_schema", "Qihuo 返回的 JSON 顶层缺少协议版本对象，已停止解析。")
    schema = raw.get("schema_version")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise QihuoError("qihuo_unsupported_schema", "Qihuo 数据缺少有效的协议版本，已停止解析。")
    if schema != SCHEMA_VERSION:
        raise QihuoError(
            "qihuo_unsupported_schema",
            f"Qihuo 数据协议版本暂不支持（收到 schema {schema}，当前仅支持 schema {SCHEMA_VERSION}）。",
        )
    version = _opt_str(raw.get("version"))
    generated_at = _opt_str(raw.get("generated_at"))
    as_of_date = raw.get("as_of_date")
    pool = raw.get("pool")
    prompt = raw.get("agent_prompt")
    products = raw.get("products")
    if not version or not generated_at or not _valid_iso_timestamp(generated_at):
        raise QihuoError("qihuo_bad_board", "Qihuo 数据缺少有效版本或生成时间。")
    if as_of_date is not None and not _valid_iso_date(as_of_date):
        raise QihuoError("qihuo_bad_board", "Qihuo 数据日期（as_of_date）格式无效。")
    collect = raw.get("latest_collect")
    if isinstance(collect, Mapping):
        if collect.get("trade_date") is not None and not _valid_iso_date(collect.get("trade_date")):
            raise QihuoError("qihuo_bad_board", "Qihuo 采集交易日格式无效。")
        if collect.get("started_at") is not None and not _valid_iso_timestamp(collect.get("started_at")):
            raise QihuoError("qihuo_bad_board", "Qihuo 采集开始时间格式无效。")
        if collect.get("finished_at") is not None and not _valid_iso_timestamp(collect.get("finished_at")):
            raise QihuoError("qihuo_bad_board", "Qihuo 采集完成时间格式无效。")
    if not isinstance(pool, dict) or not isinstance(products, list):
        raise QihuoError("qihuo_bad_board", "Qihuo 数据缺少品种池或品种列表。")
    if not isinstance(prompt, dict) or not _opt_str(prompt.get("text")):
        raise QihuoError("qihuo_bad_board", "Qihuo 数据缺少 Q1 提示词原文。")
    _reject_non_finite(raw)
    return raw


def _project_pool_ref(item: Any) -> dict[str, str] | None:
    if not isinstance(item, Mapping):
        return None
    code = _opt_str(item.get("code"))
    slug = _opt_str(item.get("slug"))
    name = _opt_str(item.get("name"))
    if not code or not name:
        return None
    return {"code": code, "slug": slug or code.lower(), "name": name}


def _project_pool(pool: Mapping[str, Any]) -> dict[str, Any]:
    active = [x for x in (_project_pool_ref(i) for i in (pool.get("active") or [])) if x is not None]
    paused = [x for x in (_project_pool_ref(i) for i in (pool.get("paused") or [])) if x is not None]
    active_count = _opt_int(pool.get("active_count"))
    paused_count = _opt_int(pool.get("paused_count"))
    total_count = _opt_int(pool.get("total_stored_count"))
    return {
        "activeCount": active_count if active_count is not None else len(active),
        "pausedCount": paused_count if paused_count is not None else len(paused),
        "totalStoredCount": total_count if total_count is not None else (len(active) + len(paused)),
        "active": active,
        "paused": paused,
    }


def _project_collect(collect: Any) -> dict[str, Any] | None:
    if not isinstance(collect, Mapping):
        return None
    status = _str(collect.get("status"), "unknown")
    if status not in _VALID_COLLECT_STATUS:
        status = "unknown"
    err = _opt_str(collect.get("error"))
    return {
        "runId": _opt_int(collect.get("run_id")),
        "slot": _opt_str(collect.get("slot")),
        "tradeDate": _opt_str(collect.get("trade_date")),
        "startedAt": _opt_str(collect.get("started_at")),
        "finishedAt": _opt_str(collect.get("finished_at")),
        "status": status,
        "error": redact_upstream(err) if err else None,
    }


def _project_schedule(schedule: Any) -> dict[str, Any]:
    raw = schedule if isinstance(schedule, Mapping) else {}
    return {
        "time": _str(raw.get("time"), "20:05"),
        "timezone": _str(raw.get("timezone"), "Asia/Shanghai"),
        "frequency": _str(raw.get("frequency"), "trading_days_daily"),
        "silent": True,  # Q 期货绝对静默，零推送
    }


def _project_col_status(item: Any) -> dict[str, Any]:
    raw = item if isinstance(item, Mapping) else {}
    state = _str(raw.get("state"), "unknown")
    if state not in _VALID_COL_STATES:
        state = "unknown"
    msg = _opt_str(raw.get("message"))
    txt = _opt_str(raw.get("text"))
    return {
        "state": state,
        "updatedAt": _opt_str(raw.get("updated_at")),
        "message": redact_upstream(msg) if msg else None,
        "text": redact_upstream(txt) if txt else None,
    }


def _project_wsr(wsr: Any) -> dict[str, Any] | None:
    if not isinstance(wsr, Mapping):
        return None
    return {
        "date": _opt_str(wsr.get("date")),
        "value": _opt_float(wsr.get("value")),  # 0.0 必须保留为 0.0，绝不当 None
        "unit": _str(wsr.get("unit"), "吨"),
        "change": _opt_float(wsr.get("change")),
        "changeText": _opt_str(wsr.get("change_text")),
    }


def _project_holding(holding: Any) -> dict[str, Any] | None:
    if not isinstance(holding, Mapping):
        return None
    return {
        "date": _opt_str(holding.get("date")),
        "contract": _opt_str(holding.get("contract")),
        "longTotal": _opt_int(holding.get("long_total")),
        "shortTotal": _opt_int(holding.get("short_total")),
        "longChg": _opt_int(holding.get("long_chg")),
        "shortChg": _opt_int(holding.get("short_chg")),
        "longRatio": _opt_float(holding.get("long_ratio")),
        "longRatioPct": _opt_float(holding.get("long_ratio_pct")),
        "net": _opt_int(holding.get("net")),
    }


def _project_spot(spot: Any) -> dict[str, Any] | None:
    if not isinstance(spot, Mapping):
        return None
    return {
        "date": _opt_str(spot.get("date")),
        "value": _opt_float(spot.get("value")),
        "displayValue": _opt_str(spot.get("display_value")),
        "unit": _str(spot.get("unit"), "元/吨"),
        "series": _opt_str(spot.get("series")),
        "source": _opt_str(spot.get("source")),
        "sourceName": _opt_str(spot.get("source_name")),
        "scope": _opt_str(spot.get("scope")),
    }


def _project_basis(basis: Any) -> dict[str, Any] | None:
    if not isinstance(basis, Mapping):
        return None
    return {
        "date": _opt_str(basis.get("date")),
        "value": _opt_float(basis.get("value")),
        "displayValue": _opt_str(basis.get("display_value")),
        "unit": _str(basis.get("unit"), "元/吨"),
        "spotDate": _opt_str(basis.get("spot_date")),
        "futuresDate": _opt_str(basis.get("futures_date")),
        "futuresContract": _opt_str(basis.get("futures_contract")),
        "text": _opt_str(basis.get("text")),
    }


def _project_snapshot(snap: Any) -> dict[str, Any]:
    raw = snap if isinstance(snap, Mapping) else {}
    ns = raw.get("news_summary") if isinstance(raw.get("news_summary"), Mapping) else {}
    return {
        "dataDate": _opt_str(raw.get("data_date")),
        "mainContract": _opt_str(raw.get("main_contract")),
        "mainContractTs": _opt_str(raw.get("main_contract_ts")),
        "priceUnit": _str(raw.get("price_unit"), "元/吨"),
        "close": _opt_float(raw.get("close")),
        "settle": _opt_float(raw.get("settle")),
        "preSettle": _opt_float(raw.get("pre_settle")),
        "pctChg": _opt_float(raw.get("pct_chg")),
        "wsr": _project_wsr(raw.get("wsr")),
        "holding": _project_holding(raw.get("holding")),
        "spot": _project_spot(raw.get("spot")),
        "basis": _project_basis(raw.get("basis")),
        "newsSummary": {
            "total": _opt_int(ns.get("total")),
            "bullCount": _opt_int(ns.get("bull_count")),
            "bearCount": _opt_int(ns.get("bear_count")),
            "latestPublishedAt": _opt_str(ns.get("latest_published_at")),
        },
    }


def _project_figure(fig: Any) -> dict[str, Any] | None:
    if not isinstance(fig, Mapping):
        return None
    metric = _opt_str(fig.get("metric"))
    if not metric:
        return None
    return {
        "metric": metric,
        "value": _figure_value_str(fig.get("value")),
        "unit": _str(fig.get("unit")),
        "change": _opt_str(fig.get("change")),
        "source": _str(fig.get("source")),
        "scope": _str(fig.get("scope")),
        "text": _opt_str(fig.get("text")),
    }


def _project_news_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    title = _opt_str(item.get("title"))
    if not title:
        return None
    figs = [f for f in (_project_figure(x) for x in (item.get("figures") or [])) if f is not None]
    published_at = _str(item.get("published_at"))
    published_display = _opt_str(item.get("published_display")) or (published_at[:16].replace("T", " ") if published_at else "")
    return {
        "publishedAt": published_at,
        "publishedDisplay": published_display,
        "collectedAt": _opt_str(item.get("collected_at")),
        "source": _str(item.get("source")),
        "url": safe_external_url(item.get("url")),
        "title": title,
        "category": _str(item.get("category")),
        "tier": _str(item.get("tier")),
        "direction": _str(item.get("direction")),
        "summary": _str(item.get("summary")),
        "figures": figs,
        "origin": _str(item.get("origin"), "auto"),
    }


def _project_data_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    dt = _opt_str(row.get("date"))
    if not dt:
        return None
    return {
        "date": dt,
        "contract": _opt_str(row.get("contract")),
        "close": _opt_float(row.get("close")),
        "settle": _opt_float(row.get("settle")),
        "preSettle": _opt_float(row.get("pre_settle")),
        "pctChg": _opt_float(row.get("pct_chg")),
        "wsr": _opt_float(row.get("wsr")),
        "wsrChg": _opt_float(row.get("wsr_chg")),
        "wsrChgText": _opt_str(row.get("wsr_chg_text")),
    }


def _project_hold_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    dt = _opt_str(row.get("date"))
    if not dt:
        return None
    return {
        "date": dt,
        "contract": _opt_str(row.get("contract")),
        "longTotal": _opt_int(row.get("long_total")),
        "shortTotal": _opt_int(row.get("short_total")),
        "longChg": _opt_int(row.get("long_chg")),
        "shortChg": _opt_int(row.get("short_chg")),
        "longRatio": _opt_float(row.get("long_ratio")),
        "longRatioPct": _opt_float(row.get("long_ratio_pct")),
        "net": _opt_int(row.get("net")),
        "collectedAt": _opt_str(row.get("collected_at")),
    }


def _project_basic(basic: Any) -> dict[str, Any]:
    raw = basic if isinstance(basic, Mapping) else {}
    ct = raw.get("contract") if isinstance(raw.get("contract"), Mapping) else {}
    chain = raw.get("chain") if isinstance(raw.get("chain"), Mapping) else {}
    spot = raw.get("standard_spot") if isinstance(raw.get("standard_spot"), Mapping) else {}

    def _nodes(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for x in items or []:
            if isinstance(x, Mapping) and _opt_str(x.get("name")):
                out.append({"name": _str(x.get("name")), "note": _opt_str(x.get("note"))})
        return out

    related: list[dict[str, Any]] = []
    for r in raw.get("related") or []:
        if isinstance(r, Mapping) and _opt_str(r.get("name")):
            related.append({
                "name": _str(r.get("name")),
                "code": _opt_str(r.get("code")),
                "enabled": bool(r.get("enabled", False)),
                "note": _opt_str(r.get("note")),
            })

    sections: list[dict[str, str]] = []
    for s in raw.get("sections") or []:
        if isinstance(s, Mapping) and _opt_str(s.get("title")):
            md = sanitize_markdown(_str(s.get("markdown")))
            if md:
                sections.append({"title": _str(s.get("title")), "markdown": md})

    return {
        "updated": _opt_str(raw.get("updated")),
        "contract": {
            "tradingUnit": _opt_str(ct.get("trading_unit")),
            "pointValue": _opt_str(ct.get("point_value")),
            "tradingHours": _opt_str(ct.get("trading_hours")),
            "nightSession": _opt_bool(ct.get("night_session")),
            "deliveryMonths": _opt_str(ct.get("delivery_months")),
        },
        "chain": {
            "upstream": _nodes(chain.get("upstream")),
            "downstream": _nodes(chain.get("downstream")),
            "plain": _opt_str(chain.get("plain")),
        },
        "related": related,
        "standardSpot": {
            "source": _opt_str(spot.get("source")),
            "sourceName": _opt_str(spot.get("source_name")),
            "series": _opt_str(spot.get("series")),
            "spec": _opt_str(spot.get("spec")),
            "unit": _opt_str(spot.get("unit")),
        },
        "sections": sections,
    }


def project_product(prod: Mapping[str, Any]) -> dict[str, Any]:
    st = prod.get("status") if isinstance(prod.get("status"), Mapping) else {}
    return {
        "code": _str(prod.get("code")),
        "slug": _str(prod.get("slug")) or _str(prod.get("code")).lower(),
        "name": _str(prod.get("name")),
        "board": _str(prod.get("board"), "化工"),
        "exchange": _str(prod.get("exchange")),
        "exchangeName": _str(prod.get("exchange_name")),
        "nightSession": _opt_bool(prod.get("night_session")),
        "chainTag": _opt_str(prod.get("chain_tag")),
        "hasWarehouseReceipt": bool(prod.get("has_warehouse_receipt", True)),
        "spotPending": bool(prod.get("spot_pending", False)),
        "snapshot": _project_snapshot(prod.get("snapshot")),
        "status": {
            "data": _project_col_status(st.get("data")),
            "news": _project_col_status(st.get("news")),
            "holding": _project_col_status(st.get("holding")),
        },
        "news": [n for n in (_project_news_item(x) for x in (prod.get("news") or [])) if n is not None],
        "dataRows": [r for r in (_project_data_row(x) for x in (prod.get("data_rows") or [])) if r is not None],
        "spotHistory": [s for s in (_project_spot(x) for x in (prod.get("spot_history") or [])) if s is not None],
        "basisHistory": [b for b in (_project_basis(x) for x in (prod.get("basis_history") or [])) if b is not None],
        "holdRows": [h for h in (_project_hold_row(x) for x in (prod.get("hold_rows") or [])) if h is not None],
        "basic": _project_basic(prod.get("basic")),
    }


def project_board(
    raw: Mapping[str, Any],
    *,
    now: datetime,
    last_read_at: datetime,
    stale: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    """将 schema 3 原始包投影为 Neckline App「Q 期货」看板 JSON。"""
    del now
    products = [
        project_product(p)
        for p in (raw.get("products") or [])
        if isinstance(p, Mapping) and _opt_str(p.get("code"))
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "version": _str(raw.get("version")),
        "generatedAt": _str(raw.get("generated_at")),
        "asOfDate": _opt_str(raw.get("as_of_date")),
        "retentionDays": _opt_int(raw.get("retention_days")) or 15,
        "schedule": _project_schedule(raw.get("schedule")),
        "latestCollect": _project_collect(raw.get("latest_collect")),
        "pool": _project_pool(raw.get("pool") or {}),
        "products": products,
        "stale": bool(stale),
        "staleReason": redact_upstream(reason) if (stale and reason) else None,
        "lastReadAt": last_read_at.astimezone(CST).isoformat(timespec="seconds"),
    }


def project_mcp_rules(raw: Mapping[str, Any], *, stale: bool = False, stale_reason: str | None = None) -> dict[str, Any]:
    """只读 MCP：原样返回 Q1 第 7 章规则与提示词，附带开放搜索说明。"""
    prompt = raw.get("agent_prompt") if isinstance(raw.get("agent_prompt"), Mapping) else {}
    return {
        "status": "ok",
        "source": _str(prompt.get("source"), "Q1.md#7"),
        "sha256": _opt_str(prompt.get("sha256")),
        "asOfDate": _opt_str(raw.get("as_of_date")),
        "version": _str(raw.get("version")),
        "rules": _str(prompt.get("text")),
        "researchNote": _OPEN_RESEARCH_NOTE,
        "stale": bool(stale),
        "staleReason": redact_upstream(stale_reason) if (stale and stale_reason) else None,
    }


def project_mcp_overview(raw: Mapping[str, Any], *, stale: bool = False, stale_reason: str | None = None) -> dict[str, Any]:
    """只读 MCP：轻量返回启用池（5/16）总览、各品种最新快照与采集状态。"""
    items = []
    for p in raw.get("products") or []:
        if not isinstance(p, Mapping) or not _opt_str(p.get("code")):
            continue
        projected = project_product(p)
        items.append({
            "code": projected["code"],
            "slug": projected["slug"],
            "name": projected["name"],
            "board": projected["board"],
            "exchange": projected["exchange"],
            "exchangeName": projected["exchangeName"],
            "nightSession": projected["nightSession"],
            "chainTag": projected["chainTag"],
            "spotPending": projected["spotPending"],
            "snapshot": projected["snapshot"],
            "status": projected["status"],
        })
    return {
        "status": "ok",
        "schemaVersion": SCHEMA_VERSION,
        "version": _str(raw.get("version")),
        "asOfDate": _opt_str(raw.get("as_of_date")),
        "generatedAt": _str(raw.get("generated_at")),
        "retentionDays": _opt_int(raw.get("retention_days")) or 15,
        "latestCollect": _project_collect(raw.get("latest_collect")),
        "pool": _project_pool(raw.get("pool") or {}),
        "products": items,
        "researchNote": _OPEN_RESEARCH_NOTE,
        "stale": bool(stale),
        "staleReason": redact_upstream(stale_reason) if (stale and stale_reason) else None,
    }


def find_raw_product(raw: Mapping[str, Any], query: str) -> Mapping[str, Any] | None:
    needle = query.strip().lower()
    if not needle:
        return None
    for p in raw.get("products") or []:
        if not isinstance(p, Mapping):
            continue
        candidates = (
            _str(p.get("code")).lower(),
            _str(p.get("slug")).lower(),
            _str(p.get("name")).lower(),
        )
        if needle in candidates:
            return p
    return None


def project_mcp_product(
    raw: Mapping[str, Any],
    query: str,
    *,
    stale: bool = False,
    stale_reason: str | None = None,
) -> dict[str, Any]:
    """只读 MCP：按代码/slug/中文名读取单个启用品种完整档案与近 15 日三栏客观记录。"""
    pool = _project_pool(raw.get("pool") or {})
    matched = find_raw_product(raw, query)
    if matched is None:
        needle = query.strip().lower()
        paused_hit = next(
            (p for p in pool["paused"] if needle in (p["code"].lower(), p["slug"].lower(), p["name"].lower())),
            None,
        )
        active_desc = "、".join(f"{p['name']}({p['code']})" for p in pool["active"])
        if paused_hit is not None:
            msg = f"{paused_hit['name']}（{paused_hit['code']}）目前已停用（历史数据保留在后台，不参与日常交易）。当前启用池仅含：{active_desc}。"
        else:
            msg = f"「{query.strip()}」不在当前 Q1 启用的 5 个品种池内。当前启用池仅含：{active_desc}。"
        return {
            "status": "not_active",
            "query": query.strip(),
            "inPausedPool": paused_hit is not None,
            "activeProducts": pool["active"],
            "message": msg,
            "researchNote": _OPEN_RESEARCH_NOTE,
        }

    return {
        "status": "ok",
        "schemaVersion": SCHEMA_VERSION,
        "version": _str(raw.get("version")),
        "asOfDate": _opt_str(raw.get("as_of_date")),
        "generatedAt": _str(raw.get("generated_at")),
        "retentionDays": _opt_int(raw.get("retention_days")) or 15,
        "latestCollect": _project_collect(raw.get("latest_collect")),
        "product": project_product(matched),
        "researchNote": _OPEN_RESEARCH_NOTE,
        "stale": bool(stale),
        "staleReason": redact_upstream(stale_reason) if (stale and stale_reason) else None,
    }


__all__ = [
    "SCHEMA_VERSION",
    "find_raw_product",
    "project_board",
    "project_mcp_overview",
    "project_mcp_product",
    "project_mcp_rules",
    "project_product",
    "require_bundle",
]
