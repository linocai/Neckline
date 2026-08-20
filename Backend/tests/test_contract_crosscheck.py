"""**契约对拍的机器判据** —— 服务端路由面 ↔ Swift 调用面(V2.5.0 S12 重建)。

⚠ **本文件是 S1 删掉那份的接任者**。S1 一次删了 33 条 K8 路由,而客户端当时仍在调
它们,重做归 S12 —— 那半年里这条闸是空的。⛔ **不重建 = 后端删路由 / 前端调废接口
都没人管**(V2 时代那份闸抓到过 `PUT /settings/llm` 采集用户明文 key、发到一个不存在
的端点、界面上还是一副成功的样子)。

**能机器判的五组**:

1. **客户端调用面 ⊆ 服务端路由面** —— 客户端**任意 Swift 文件**里出现的每一个
   `/api/v1/...` 路径,服务端都必须真有那条路由。差集用 `==` 断言(不是 `<=`):
   ⛔ 一个只会越放越松的 allowlist 等于没有闸。
2. **HTTP method 维度** —— 路径对得上不代表方法对得上(「POST 打到 GET-only 路径」)。
3. **已删端点零残留** —— §5.12 明确删掉的 33 条,服务端不该还有。
4. **reason 面闭包** —— 服务端 raise 的每个 reason,客户端 `mapReason` 要么有 case、
   要么在登记过的欠账清单里;且 **reason 面只许按登记增长**。
5. **冻结快照类 DTO 手写 `init(from:)`** —— 写入当时冻住的那一类,服务端升级永远
   不会给老快照补新键 → 合成 `Codable` 会让「装了新 App 的用户翻几天前的老件」整条解不出。

⚠ **本文件不是 review**:它是施工块内的自查,不等于独立复审。
"""

from __future__ import annotations

import re

import pytest

from tests.client_sources import (
    API_CLIENT,
    client_swift_files,
    models_text,
    strip_comments,
    type_block,
)

# Swift 里的路径字面量:`"/api/v1/selection/\(date)/stock/\(code)"` → `/api/v1/selection/{}/stock/{}`
_PATH_LITERAL = re.compile(r'"(/api/v1[^"]*)"')
_INTERPOLATION = re.compile(r"\\\([^)]*\)")
# 只认"字面量直接传给这四个私有请求方法"的调用点(见下方**已知不完整**声明)。
_METHOD_CALL = re.compile(r'\b(get|post|put|delete)\(\s*"(/api/v1[^"]*)"')


def _normalize(path: str) -> str:
    """插值段归一成 `{}`、去掉查询串。

    FastAPI 的 `{trade_date}` 同样归一成 `{}`;客户端测试里的**具体示例值**
    (如 `"/api/v1/selection/20260820/stock/600001.SH"`)里的纯数字段也折叠 ——
    对拍的是**路径形状**,不是参数名或取值。
    ⚠ **只折叠纯数字段**:`600001.SH` 这种带后缀的股票代码不是纯数字,
    折不掉,故 `stock/{}` 那一段靠 `_SAMPLE_SEGMENTS` 显式登记(见下)。
    """
    path = path.split("?", 1)[0]
    path = _INTERPOLATION.sub("{}", path)
    path = re.sub(r"\{[^}]*\}", "{}", path)
    path = re.sub(r"/\d+(?=/|$)", "/{}", path)
    for sample in _SAMPLE_SEGMENTS:
        path = path.replace(f"/{sample}", "/{}")
    return path.rstrip("/") or "/"


#: 客户端**测试**里为了断言 URL 拼接而写死的示例路径段(它们在服务端眼里都是路径参数)。
#: ⚠ 已核实服务端真实路由没有任何固定的字面段与它们同名,折叠不会掩盖真实回归。
_SAMPLE_SEGMENTS = ("600001.SH",)


def client_call_surface() -> set:
    """客户端**实际会打出去**的路径集合(含已删端点的活调用 —— 那正是本闸要抓的)。

    **扫 `App/` 下全部 `.swift`**(含 `NecklineTests/`),⛔ 不只锚 `APIClient.swift`
    —— 日后任何 View / Model 直接拼 URL 立刻被机器抓到。
    """
    texts = (p.read_text(encoding="utf-8") for p in client_swift_files())
    return {_normalize(m) for text in texts for m in _PATH_LITERAL.findall(text)}


def server_route_surface() -> set:
    from neckline.api.app import app

    return {_normalize(r.path) for r in app.routes
            if getattr(r, "path", "").startswith("/api/v1")}


def client_call_method_pairs() -> set:
    """客户端**能确定 HTTP method** 的 `(METHOD, 归一化路径)` 对。

    ⚠ **已知不完整**:只覆盖 `APIClient.swift` 里路径字面量**直接**传给
    `get/post/put/delete(...)` 的调用点;先 `let path = "..."` 再传变量的调用点
    正则不解析变量流、**不纳入**这条校验 —— 宁可少覆盖一部分,不假装能可靠解析
    变量流而产出假阳性。路径层面的缺失由第 1 条闸抓。
    """
    text = API_CLIENT.read_text(encoding="utf-8")
    return {(method.upper(), _normalize(path)) for method, path in _METHOD_CALL.findall(text)}


def server_route_method_pairs() -> dict:
    """`{归一化路径: {服务端支持的 METHOD 集合}}`,只收 `/api/v1` 路由。"""
    from neckline.api.app import app

    out: dict = {}
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith("/api/v1"):
            continue
        methods = {m for m in (getattr(r, "methods", None) or set())
                   if m not in ("HEAD", "OPTIONS")}
        out.setdefault(_normalize(path), set()).update(methods)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 1. 调用面 ⊆ 路由面
# ══════════════════════════════════════════════════════════════════════════

#: **已登记的欠账**:客户端还在调、服务端已经没有的路径。
#:
#: ✅ **V2.5.0 S12 重建时为空** —— ⚠ 空集合**不是"闸松了",恰恰相反**:下面那条断言
#: 用 `==`,清空之后**任何**新增的「打不到的调用」都会立刻红,这是本闸最严的状态。
#: ⛔ 别再往里加条目当豁免用;真要新增一笔债,得先在 PROJECT_PLAN 里登记它。
PENDING_CLIENT_CALLS: set = set()


def test_client_call_surface_is_subset_of_server_routes_modulo_registered_debt():
    """**客户端调用面 ⊆ 服务端路由面**。

    差集必须**恰好等于**已登记的欠账集合:
      · 多出来 = 新增了打不到的调用(回归);
      · 少了   = 债还完了但清单没更新(该收债了)。
    """
    missing = client_call_surface() - server_route_surface()
    assert missing == PENDING_CLIENT_CALLS, (
        f"客户端调用面与服务端路由面不闭合。\n"
        f"  多出的(新回归,必须修):{sorted(missing - PENDING_CLIENT_CALLS)}\n"
        f"  清单里有但代码已无(已还债,请同步删清单):"
        f"{sorted(PENDING_CLIENT_CALLS - missing)}"
    )


def test_registered_debt_entries_are_really_still_in_the_client():
    """反向守门:清单里的每一条**必须真的还在客户端代码里**。
    防的是"债还完了、清单没删",那会让上面那条断言变成一句空话。"""
    stale = PENDING_CLIENT_CALLS - client_call_surface()
    assert not stale, f"这些欠账在客户端已不存在,请从清单里删掉:{sorted(stale)}"


def test_client_call_methods_match_server_route_methods_where_determinable():
    """HTTP method 维度对拍 —— 路径对得上不代表方法对得上。"""
    server_methods = server_route_method_pairs()
    bad = []
    for method, path in sorted(client_call_method_pairs()):
        allowed = server_methods.get(path)
        if allowed is None:
            continue   # 路径本身缺失不归本条测试管
        if method not in allowed:
            bad.append((method, path, sorted(allowed)))
    assert not bad, (
        f"客户端用了服务端该路径不支持的 HTTP method"
        f"(method, 客户端路径, 服务端允许的 method):{bad}"
    )


def test_the_method_pair_scanner_actually_sees_something():
    """闸自己的守门:正则真的解析到了调用点。

    ⛔ 一个恒空的集合会让上面那条 method 校验变成一句空话(它对空集恒绿)。
    """
    pairs = client_call_method_pairs()
    assert len(pairs) >= 15, f"只解析到 {len(pairs)} 个调用点 —— 正则怕是失效了"
    assert ("GET", "/api/v1/selection/latest") in pairs
    assert ("POST", "/api/v1/devices") in pairs


# ══════════════════════════════════════════════════════════════════════════
# 2. V2.5.0 的新路由必须真的挂上了 / 已删的必须零残留
# ══════════════════════════════════════════════════════════════════════════

#: §5.12 定稿的新端点(**路由面自检**;逐字段契约由客户端 DTO 与冒烟覆盖)。
#: ⚠ `GET /api/v1/scoreboard/listing`(清单成绩五指标)**不在这张表里** ——
#: 它归 **S17**(批 B,等参数标定),现在还没有。⛔ 别为了"补齐"提前挂一个空壳路由。
_V250_NEW_ROUTES = (
    "/api/v1/selection/latest",
    "/api/v1/selection/{}",
    "/api/v1/selection/{}/stock/{}",
    "/api/v1/selection/{}/stock/{}/playbook",
    "/api/v1/checklist/{}",
    "/api/v1/scoreboard/coverage",
    "/api/v1/scoreboard/verdicts/{}",
    "/api/v1/review/bindery",
    "/api/v1/review/conclusions",
    "/api/v1/legacy/k8/baskets",
)


def test_v250_new_endpoints_are_reachable_shapes():
    server = server_route_surface()
    for path in _V250_NEW_ROUTES:
        assert path in server, f"§5.12 清单里的端点 {path} 没挂上"


def test_the_listing_scorecard_route_is_honestly_absent_until_s17():
    """🔴 **`/scoreboard/listing` 现在就该不存在**(S17,批 B)。

    ⛔ 这条不是"少做一片"的遮羞布,是**反向**守门:提前挂一个恒空的路由,会让
    「五指标还没开始结算」看起来像「结算了、结果是空的」——那是把没做讲成做了。
    ⚠ S17 落地时**同时**做三件事:挂路由 / 加进 `_V250_NEW_ROUTES` / 删掉本条。
    """
    assert "/api/v1/scoreboard/listing" not in server_route_surface(), (
        "`/scoreboard/listing` 挂上了 —— 若这是 S17 落地,请把它加进 `_V250_NEW_ROUTES` "
        "并删掉本条测试;若是提前挂的空壳,⛔ 摘掉。")


#: §5.12「删除」栏逐条 + S1 实际删掉的 33 条路由的路径形状。
_DELETED_ROUTES = (
    # 报告与篮子(→ /selection 与 /legacy)
    "/api/v1/report/latest", "/api/v1/report", "/api/v1/report/{}/info-card/{}",
    "/api/v1/baskets", "/api/v1/baskets/{}", "/api/v1/baskets/{}/card",
    "/api/v1/baskets/{}/verification", "/api/v1/baskets/{}/review",
    # 盘中看板 / 行情状态 / 竞价(→ /checklist)
    "/api/v1/board", "/api/v1/market-regime", "/api/v1/auction",
    # 持仓六 + 一(裁定 11 整块下线)
    "/api/v1/positions", "/api/v1/positions/{}/close",
    "/api/v1/positions/entry-suggestion", "/api/v1/positions/{}/plans",
    "/api/v1/positions/{}/entry-snapshot",
    # 决策台账 / 双时钟 / 提醒 / 画像 / 策略包 / 移交件
    "/api/v1/decisions", "/api/v1/decisions/{}/track",
    "/api/v1/clocks/selection", "/api/v1/clocks/trade/{}", "/api/v1/clocks/trade/{}/note",
    "/api/v1/alerts", "/api/v1/alerts/{}", "/api/v1/alerts/parse",
    "/api/v1/profile/preference", "/api/v1/profile/capability",
    "/api/v1/packs", "/api/v1/packs/{}",
    "/api/v1/review/handoff",
    # 更早退役的(问询台 / 熔断 / 关注池 —— 留在清单里防止有人"复活"）
    "/api/v1/inquiry", "/api/v1/inquiries", "/api/v1/circuit", "/api/v1/circuit/unlock",
    "/api/v1/watchlist", "/api/v1/settings/llm", "/api/v1/settings/intel-boards",
)


def test_deleted_endpoints_have_no_server_route():
    """S1 删掉的 33 条 + 更早退役的那几条,服务端零残留。"""
    server = server_route_surface()
    for gone in _DELETED_ROUTES:
        assert gone not in server, f"{gone} 应已删除,服务端不该还有这条路由"


def test_deleted_endpoints_have_no_client_call_either():
    """客户端同样零残留 —— 上面那条只管服务端。

    ⚠ 与第 1 条闸的区别:那条抓的是「客户端调了一条服务端没有的路径」;这条抓的是
    「那条路径**恰好**是我们明令删掉的」——后者的信息量在于**指名道姓**,红的时候
    读者立刻知道是谁被复活了。
    """
    surface = client_call_surface()
    revived = [p for p in _DELETED_ROUTES if p in surface]
    assert not revived, f"客户端把已退役的端点接回来了:{revived}"


# ══════════════════════════════════════════════════════════════════════════
# 3. reason 面闭包
# ══════════════════════════════════════════════════════════════════════════

#: 服务端 raise 的全部 reason 字符串。
#:
#: 🔴 **V2.5.0 只剩六条,全部出自设置屏**。K9 的四条新端点(`/selection/*` /
#: `/checklist/*`)返的是**纯字符串 detail**(「20260430 没有报告」这类),
#: ⛔ 不进 reason 面 —— 它们不需要客户端换算,原文直接给用户看比一个英文码更清楚。
#: ⚠ **新增会返 4xx 的端点必须同步更新本集合与客户端 `mapReason`** —— 这条测试
#: 本身就是那份检查清单。
SERVER_REASONS = {
    "not_found",
    "already_exists",
    "invalid_provider",
    "invalid_task",
    "invalid_tavily_key",
    "invalid_push_kinds",
}

#: 待加 `mapReason` case 的字符串。**空 = 本闸最严的状态**(下面那条断言用 `==`)。
PENDING_MAP_REASON_CASES: set = set()


def _server_reason_literals() -> set:
    from tests.client_sources import _ROOT  # noqa: PLC0415  只在这一处用

    text = (_ROOT / "neckline" / "api" / "app.py").read_text(encoding="utf-8")
    return set(re.findall(r'"reason":\s*"([a-z_]+)"', text)) | set(
        re.findall(r'^REASON_[A-Z_]+\s*=\s*"([a-z_]+)"', text, re.M))


def test_server_reason_inventory_is_complete():
    """服务端真的 raise 的 reason 不许悄悄多出一个没登记的 —— 那正是「新增会返 404
    的端点忘了检查 `mapReason`」这个坑的入口。"""
    found = _server_reason_literals()
    unregistered = found - SERVER_REASONS
    assert not unregistered, (
        f"这些 reason 在 app.py 里出现但没登记进 SERVER_REASONS:{sorted(unregistered)}。"
        f"新增会返 4xx 的端点,必须同时检查客户端 `mapReason` 要不要加新 case"
        f"(⛔ 复用已有字符串不算'没加',全新字符串才需要新 case)。"
    )


def test_no_dead_reason_constants_linger_after_their_endpoints_were_deleted():
    """🔴 **反向**:登记过的每一条都必须**真的还能被 raise**。

    死掉的 reason 常量不是无害的:它会**要求客户端一直养着一个死 case**,
    而那个 case 的存在又让人以为对应端点还在。S12 就是因为这条清掉了七个
    K8 时代的常量(`basket_not_found` / `card_not_ready` / `card_corrupt` /
    `not_trading_day` / `future_buy_date` / `auction_not_ready` / `auction_corrupt`)。
    """
    found = _server_reason_literals()
    dead = SERVER_REASONS - found
    assert not dead, (
        f"这些 reason 登记了但 `app.py` 里已经 raise 不出来了:{sorted(dead)} —— "
        f"端点删了就把 reason 一起删,⛔ 别留死码。"
    )


def test_map_reason_covers_every_server_reason_modulo_registered_debt():
    """`mapReason` 的 case 集合 ∪ 已登记欠账 ⊇ 服务端 reason 集合。"""
    text = API_CLIENT.read_text(encoding="utf-8")
    body = text.split("private func mapReason(", 1)[1].split("\n    }", 1)[0]
    cases = set(re.findall(r'case\s+"([a-z_]+)"', body))
    uncovered = SERVER_REASONS - cases
    assert uncovered == PENDING_MAP_REASON_CASES, (
        f"`mapReason` 与服务端 reason 面不闭合。\n"
        f"  没 case 也没登记(必须修):{sorted(uncovered - PENDING_MAP_REASON_CASES)}\n"
        f"  登记了但其实已有 case(请从清单删):"
        f"{sorted(PENDING_MAP_REASON_CASES - uncovered)}"
    )
    # 反向:客户端不许养着一个服务端 raise 不出来的 case(同上条的理由)。
    stale = cases - SERVER_REASONS
    assert not stale, f"客户端 `mapReason` 里有服务端已经不发的 case:{sorted(stale)}"


def test_the_404_fallback_is_not_some_unrelated_business_error():
    """🔴 **404 的 fallback 必须是通用「未找到」+ 服务端原文**。

    上一版那个 fallback 是 `.notHolding`「该持仓已清或不存在」—— 持仓整块下线之后,
    K9 的每一条 404(「20260430 没有报告」/「600001.SH 不在清单里」/
    「没有竞价核对表」)都会显示成那句驴唇不对马嘴的话。
    ⚠ v1.4 的 `watchlist` 与 V2 的 `card_not_ready` 已经踩过两次同一个坑。
    """
    text = API_CLIENT.read_text(encoding="utf-8")
    m = re.search(r"case 404:\s*\n\s*throw mapReason\(data, fallback: \.(\w+)", text)
    assert m, "找不到 404 分支的 fallback —— 这条守门锚错了地方"
    assert m.group(1) == "notFound", (
        f"404 的 fallback 是 `.{m.group(1)}` —— 必须是 `.notFound(服务端原文)`。"
        f"拿一个具体业务错误当 404 的兜底 = 用户看到一句与他做的事毫无关系的话。"
    )
    # 且它必须真的把服务端那句 detail 带上(⛔ 不是一个写死的中文)。
    assert "fallback: .notFound(reasonString(data) ?? \"\")" in text


# ══════════════════════════════════════════════════════════════════════════
# 4. 冻结快照类 DTO 的解码姿势
# ══════════════════════════════════════════════════════════════════════════

#: **写入当时冻住**的那一类:服务端升级永远不会给老快照补新键 → 客户端必须手写
#: `init(from:)` + 全字段 `decodeIfPresent` 兜底。
#: ⛔ 合成 `Codable` 的后果是「装了新 App 的用户翻几天前的老件 → 整条解不出」。
#:
#: ⚠ V2.5.0 的真 B 类:报告快照(`k9_reports` 一行随发布冻住)、预案(`k9_playbooks`
#: append-only 版本化)、核对表(`k9_checklists` 的 json 列)、周报
#: (`reviews.result_json`)、结论(`review_conclusions` 行)。
#: 其余几件是每次响应重拼的 A 类,手写 `init(from:)` 是**白拿的保险**,一并收进本闸。
FROZEN_SNAPSHOT_DTOS = (
    "SelectionSnapshot", "K9Stock", "Playbook", "PlaybookLevels", "PlaybookBranch",
    "PlaybookCondition", "K9ExplainNote", "K9StockDetail", "K9StockEntry",
    "Checklist", "ChecklistSegment", "ChecklistRow",
    "CoverageSnapshot", "CoverageDay", "CoverageMiss",
    "K9VerdictsSnapshot", "K9VerdictRow",
    "ReviewWeeklyResult", "ReviewWeeklyStats", "ReviewRoundTrip", "WeeklyReviewEntry",
    "ReviewGetResponse", "ReviewBindery", "ReviewConclusion", "ReviewConclusionsResponse",
    "ReviewSegment", "ReviewOverview",
    "PushKind", "PushSettings", "Provider", "SettingsProvider", "SettingsSnapshot",
)


def test_dto_slicer_is_not_fooled_by_same_prefix_types():
    """本闸自己的守门:切块必须切到**同名那一个**类型,而不是同前缀的邻居。

    `Playbook` 之前排着 `PlaybookLevels` / `PlaybookCondition` / `PlaybookBranch`
    —— 裸 `split("struct Playbook")` 会切到邻居身上,**断言照样绿、守的却是另一个类型**。
    """
    body = type_block("Playbook")
    assert body.startswith("struct Playbook:") or body.startswith("struct Playbook "), body[:48]
    assert "struct PlaybookLevels" not in body
    assert "struct PlaybookBranch" not in body
    # 同前缀族第二例:`Checklist` 之外还有 `ChecklistSegment` / `ChecklistRow`。
    cl = type_block("Checklist")
    assert cl.startswith("struct Checklist:") or cl.startswith("struct Checklist ")
    assert "struct ChecklistSegment" not in cl and "struct ChecklistRow" not in cl
    assert type_block("NoSuchDTOAnywhere") == ""


@pytest.mark.parametrize("name", FROZEN_SNAPSHOT_DTOS)
def test_frozen_snapshot_dtos_hand_write_init_from_decoder(name: str):
    body = type_block(name)
    assert body, f"客户端找不到 `struct/enum {name}` —— 改名了?请同步改本清单"
    assert "init(from decoder: Decoder)" in body, (
        f"`{name}` 是**写入当时冻住**的历史快照类 DTO,必须手写 `init(from:)` + 全字段 "
        f"`decodeIfPresent` 兜底;合成 Codable 会让老快照解不出。"
    )
    assert "decodeIfPresent" in body, f"`{name}` 的 `init(from:)` 里没有 `decodeIfPresent` 兜底"


# ══════════════════════════════════════════════════════════════════════════
# 5. 裁定 10 的客户端半边:核对表里**结构上没有「成立」**
# ══════════════════════════════════════════════════════════════════════════

def test_client_checklist_verdict_enum_has_exactly_two_members_and_no_confirmed():
    """🔴 **守门 G20 的客户端半边**(服务端半边在 `test_v250_s8_auction_guard.py`)。

    服务端 `ChecklistVerdict` 是二值闭合枚举、加第三个成员 import 就炸;客户端这一侧
    同样只能有两个 case —— ⛔ 一侧守住而另一侧偷偷多一个,界面照样会画出「成立」段。
    """
    block = type_block("ChecklistVerdict")
    assert block, "客户端找不到 `enum ChecklistVerdict`"
    cases = re.findall(r"case\s+(\w+)\s*=\s*\"([a-z_]+)\"", block)
    assert [raw for _, raw in cases] == ["rejected", "pending_open"], (
        f"客户端 `ChecklistVerdict` 应恰好两个成员 —— 实得 {cases}")
    assert "confirmed" not in block, (
        "🔴 客户端 `ChecklistVerdict` 里出现了 `confirmed` —— 裁定 10:9:29 那一拍"
        "**结构上**判不出「成立」(四个成立分支都含「前 30 分钟」合取项)。")


def test_the_checklist_view_never_renders_a_confirmed_segment():
    """🔴 **裁定 10 的文案扫描**:核对表视图里「成立」⛔ 不许出现在任何**段名 / 徽标 /
    枚举取值**上;只允许出现在**解释它为什么不存在**的整句说明里。

    **判据 = 字符串字面量的长度**,不是关键词黑名单:
    段名 / 徽标是**短**串(「已触发成立」五个字),说明是**长句**。这条判据没有
    "只要写了某个词就放行"的口子 —— 想把「成立」做成一枚徽标,怎么写都过不去。

    ⚠ **先剥掉注释与 doc comment 再扫**:一条纪律总要写出它禁止的那个词才解释得清,
    把说明算进命中会逼着后来者删注释去凑绿(同 `guard_scan.py` 的体例)。
    ⚠ 服务端下发的 `footnote`(「成立由 10:00 结算…」)不是客户端字面量,不在扫描域内 ——
    它**必须**照原样显示,那正是这张表没有「成立」段的解释。
    """
    from tests.client_sources import CLIENT  # noqa: PLC0415

    #: 段名 / 徽标的长度上界。⚠ 超过它的一律视为整句说明。
    label_max = 20
    src = (CLIENT / "Views" / "CheckListView.swift").read_text(encoding="utf-8")
    code = strip_comments(src)
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', code)
    short_hits = [s for s in literals if "成立" in s and len(s) <= label_max]
    assert not short_hits, (
        f"核对表视图里出现了带「成立」的**短串**(段名 / 徽标的形状):{short_hits} —— "
        f"裁定 10:9:29 那一拍**结构上**判不出「成立」,这张表只有两段。"
    )
    # 闸自己的守门:剥注释这一步不许把真代码剥掉,且扫描器真的解析到了字面量。
    # ⚠ 锚点取**真的出现在代码行里**的标识符(⛔ 别锚只出现在文件头注释里的类型名 ——
    # 那样锚点会随剥注释一起消失,自检永远绿)。
    assert "list.segments" in code and "list.footnote" in code
    assert len(literals) >= 10, "字面量扫描器怕是失效了(一个恒空的闸等于没有闸)"
    # 正向:那句解释**必须在**(⛔ 不许只是"没有成立"而不说为什么)。
    assert any("成立" in s and len(s) > label_max for s in literals), (
        "核对表视图必须有一句话解释「为什么这里没有成立段」——⛔ 不许只是沉默地少一段")


def test_the_settlement_verdicts_live_in_the_scoreboard_not_the_selection_screen():
    """🔴 **裁定 10 的落点**:10:00 结算拍的三分支终值只出现在**成绩**板块。

    ⛔ 它不进选股首屏 —— 那一屏是「今天该细看哪几只 / 明早哪几只已经死了」,
    把终值摆上去会让人在 9:30 之前就以为系统已经判了成立。
    """
    from tests.client_sources import CLIENT  # noqa: PLC0415

    scoreboard = (CLIENT / "Views" / "ScoreboardView.swift").read_text(encoding="utf-8")
    assert "model.verdicts" in scoreboard, "成绩板块应当呈现三分支终值"
    for name in ("SelectionView.swift", "CheckListView.swift", "StockDetailView.swift"):
        text = strip_comments((CLIENT / "Views" / name).read_text(encoding="utf-8"))
        assert "model.verdicts" not in text, (
            f"`{name}` 读了 `model.verdicts` —— 10:00 结算终值⛔ 不进选股板块(裁定 10)")


# ══════════════════════════════════════════════════════════════════════════
# 6. 行业分 / 选票分:两侧都⛔ 不许有合计
# ══════════════════════════════════════════════════════════════════════════

def test_neither_side_offers_a_combined_industry_plus_pick_score():
    """🔴 **守门 G13 的客户端半边**。

    K9 §八 口径原文:行业分低是**方向层**的问题,行业分高而选票分低是**选票参数**的
    问题 —— **两者吃的药完全不同**。服务端 `scorecard` 存储层刻意没有合计字段;
    客户端同理,⛔ 不许自己相加。
    """
    from tests.client_sources import CLIENT  # noqa: PLC0415

    scoreboard_model = strip_comments(type_block("NKListingScorecard") or "")
    text = strip_comments(
        (CLIENT / "Views" / "ScoreboardView.swift").read_text(encoding="utf-8"))
    for banned in ("combinedScore", "totalScore", "industryPlusPick", "合计分", "综合分"):
        assert banned not in text, f"成绩板块出现了合计口径 `{banned}` —— ⛔ 两栏永不合并"
        assert banned not in scoreboard_model
    # 正向:两栏确实都在。
    models = models_text()
    assert '("行业分"' in models and '("选票分"' in models


def test_client_never_hardcodes_the_playbook_slot_keys():
    """🔴 **要填哪几个数由服务端下发**(`playbookSlots`)—— ⛔ 客户端不许硬编一份键表。

    硬编的后果是静默的:服务端 `playbook/skeleton.py` 改了槽位,用户改完点提交拿一个
    英文 422,而界面上的表单一路是绿的(同 `PushKind.label` 由服务端下发的先例)。
    """
    from neckline.playbook import skeleton as skeleton_mod  # noqa: PLC0415
    from tests.client_sources import CLIENT  # noqa: PLC0415

    keys = set()
    for pattern in skeleton_mod.SKELETONS:
        keys |= set(skeleton_mod.required_keys(pattern))
    assert keys, "服务端槽位表是空的?那本条守门就是空的"
    swift = strip_comments("\n".join(
        p.read_text(encoding="utf-8") for p in sorted((CLIENT).rglob("*.swift"))))
    # 三个价位的键客户端**要读**(它们是 `PlaybookLevels` 的字段),故只查形态槽位那一半。
    level_keys = {s.key for s in skeleton_mod.LEVEL_SLOTS}
    for k in sorted(keys - level_keys):
        assert f'"{k}"' not in swift, (
            f"客户端硬编了形态槽位键 `{k}` —— 它该由服务端的 `playbookSlots` 下发。")
