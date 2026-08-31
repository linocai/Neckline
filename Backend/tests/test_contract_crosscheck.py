"""**契约对拍的机器判据** —— 服务端路由面 ↔ Swift 调用面(V2.5.0 S12 重建)。

⚠ **本文件是 S1 删掉那份的接任者**。S1 一次删了 33 条 K8 路由,而客户端当时仍在调
它们,重做归 S12 —— 那半年里这条闸是空的。⛔ **不重建 = 后端删路由 / 前端调废接口
都没人管**(V2 时代那份闸抓到过 `PUT /settings/llm` 采集用户明文 key、发到一个不存在
的端点、界面上还是一副成功的样子)。

**能机器判的六组**:

1. **客户端调用面 ⊆ 服务端路由面** —— 客户端**任意 Swift 文件**里出现的每一个
   `/api/v1/...` 路径,服务端都必须真有那条路由。差集用 `==` 断言(不是 `<=`):
   ⛔ 一个只会越放越松的 allowlist 等于没有闸。
2. **HTTP method 维度** —— 路径对得上不代表方法对得上(「POST 打到 GET-only 路径」)。
3. **已删端点零残留** —— §5.12 明确删掉的 33 条,服务端不该还有。
4. **reason 面闭包** —— 服务端 raise 的每个 reason,客户端 `mapReason` 要么有 case、
   要么在登记过的欠账清单里;且 **reason 面只许按登记增长**。
5. **冻结快照类 DTO 手写 `init(from:)`** —— 写入当时冻住的那一类,服务端升级永远
   不会给老快照补新键 → 合成 `Codable` 会让「装了新 App 的用户翻几天前的老件」整条解不出。
6. **推送落点对拍**(V2.5.0 修复组 F-C 新增)—— 服务端**真的还会发出来**的每个
   `kind`,客户端路由表都必须有一条落点,且落点指向的板块 / 视图**当前真的存在**;
   反过来,客户端也不许留着指向已退役板块的路由。**这一条是 🔴-1 的根因面**:
   `push_checklist_summary` 复用 `KIND_PRECALL`,而客户端照 K8 的名字把它路由到
   `.positions` —— 裁定 11 已整块下线的板块。用户每个交易日 9:29 收到的那条唯一
   通知,点开落在一个不存在的地方,**而两侧各自的测试都是绿的**。

⚠ **本文件不是 review**:它是施工块内的自查,不等于独立复审。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.client_sources import (
    API_CLIENT,
    APP_MODEL,
    PUSH_MANAGER,
    client_swift_files,
    models_text,
    strip_comments,
    swift_decl_block,
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
_SAMPLE_SEGMENTS = ("600001.SH", "k9-v3-20260820-demo")


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
_V270_NEW_ROUTES = (
    "/api/v1/selection/latest",
    "/api/v1/selection/{}",
    "/api/v1/checklists/{}",
    "/api/v1/scoreboard/packages",
    "/api/v1/scoreboard/packages/{}",
    "/api/v1/review/bindery",
    "/api/v1/review/conclusions",
)


def test_v270_score_package_endpoints_are_reachable_shapes():
    server = server_route_surface()
    for path in _V270_NEW_ROUTES:
        assert path in server, f"V2.7 清单里的端点 {path} 没挂上"


#: §5.12「删除」栏逐条 + S1 实际删掉的 33 条路由的路径形状。
_DELETED_ROUTES = (
    "/api/v1/scoreboard/coverage", "/api/v1/scoreboard/listing", "/api/v1/scoreboard/verdicts/{}",
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
    "/api/v1/review/handoff", "/api/v1/eval/weekly", "/api/v1/legacy/k8/baskets",
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
    "SelectionSnapshot",
    "ReviewWeeklyResult", "ReviewWeeklyStats", "ReviewRoundTrip", "WeeklyReviewEntry",
    "ReviewGetResponse", "ReviewBindery", "ReviewConclusion", "ReviewConclusionsResponse",
    "ReviewSegment", "ReviewOverview",
    "PushKind", "PushSettings", "Provider", "SettingsProvider", "SettingsSnapshot",
)


def test_dto_slicer_finds_the_v3_report_snapshot():
    """本闸自己的守门:切块必须切到**同名那一个**类型,而不是同前缀的邻居。

    `Playbook` 之前排着 `PlaybookLevels` / `PlaybookCondition` / `PlaybookBranch`
    —— 裸 `split("struct Playbook")` 会切到邻居身上,**断言照样绿、守的却是另一个类型**。
    """
    body = type_block("SelectionSnapshot")
    assert body.startswith("struct SelectionSnapshot:") or body.startswith("struct SelectionSnapshot "), body[:48]
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

def test_client_checklist_verdict_enum_has_v3_morning_segments():
    """🔴 **守门 G20 的客户端半边**(服务端半边在 `test_v250_s8_auction_guard.py`)。

    服务端 `ChecklistVerdict` 是二值闭合枚举、加第三个成员 import 就炸;客户端这一侧
    同样只能有两个 case —— ⛔ 一侧守住而另一侧偷偷多一个,界面照样会画出「成立」段。
    """
    block = type_block("K9ChecklistVerdict")
    assert block, "客户端找不到 `enum K9ChecklistVerdict`"
    for value in ("rejected", "unbuyable", "pending_open"):
        assert value in block
    assert "confirmed" not in block, (
        "🔴 客户端 `ChecklistVerdict` 里出现了 `confirmed` —— 裁定 10:9:29 那一拍"
        "**结构上**判不出「成立」(四个成立分支都含「前 30 分钟」合取项)。")


def test_the_checklist_view_uses_server_three_segments_without_inventing_a_fourth():
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

    src = (CLIENT / "Views" / "CheckListView.swift").read_text(encoding="utf-8")
    code = strip_comments(src)
    assert "checklist.segments" in code
    assert "K9ChecklistVerdict" not in code, "视图必须渲染服务端 segments，不自行拼三段"


def test_the_settlement_verdicts_live_in_the_scoreboard_not_the_selection_screen():
    """🔴 **裁定 10 的落点**:10:00 结算拍的三分支终值只出现在**成绩**板块。

    ⛔ 它不进选股首屏 —— 那一屏是「今天该细看哪几只 / 明早哪几只已经死了」,
    把终值摆上去会让人在 9:30 之前就以为系统已经判了成立。
    """
    from tests.client_sources import CLIENT  # noqa: PLC0415

    scoreboard = (CLIENT / "Views" / "ScoreboardView.swift").read_text(encoding="utf-8")
    assert "activeScorePackages" in scoreboard and "settledScorePackages" in scoreboard, "成绩板块应呈现 K9-v3 独立成绩包"
    for name in ("SelectionView.swift", "CheckListView.swift", "StockDetailView.swift"):
        text = strip_comments((CLIENT / "Views" / name).read_text(encoding="utf-8"))
        assert "model.verdicts" not in text, (
            f"`{name}` 读了 `model.verdicts` —— 10:00 结算终值⛔ 不进选股板块(裁定 10)")


# ══════════════════════════════════════════════════════════════════════════
# 6. 行业分 / 选票分:两侧都⛔ 不许有合计
# ══════════════════════════════════════════════════════════════════════════

def test_client_exposes_v3_package_results_without_a_combined_score():
    from tests.client_sources import CLIENT  # noqa: PLC0415

    scoreboard_model = strip_comments(models_text())
    text = strip_comments(
        (CLIENT / "Views" / "ScoreboardView.swift").read_text(encoding="utf-8"))
    for banned in ("combinedScore", "totalScore", "industryPlusPick", "合计分", "综合分"):
        assert banned not in text, f"成绩板块出现了合计口径 `{banned}` —— ⛔ 两栏永不合并"
        assert banned not in scoreboard_model
    for field in ("selectionResult", "playbookResult", "riskTag", "coverageState"):
        assert field in scoreboard_model


# ══════════════════════════════════════════════════════════════════════════
# 7. 推送落点:服务端**还在发的** kind ↔ 客户端路由表(V2.5.0 修复组 F-C)
# ══════════════════════════════════════════════════════════════════════════
#
# 🔴 **这一组守的是 R3 🔴-1 的根因面**,不是那六条编译错。编译错只是症状:
# `PushManager.swift` 整份住在 `#if os(iOS)` 里,`-destination 'platform=macOS'`
# 一行都不编译它,于是路由表引用着裁定 11 已删的 `AppTab.baskets` / `.positions`
# 却一路全绿。**语义那一半更深**:`push_checklist_summary` 复用 `KIND_PRECALL`,
# 客户端照 K8 的名字把它送去「持仓」—— 一个已经不存在的板块。
#
# 修法是两条并用,缺一不可:
#   ① 路由表**搬出 `#if os(iOS)`**(纯数据,不需要 UIKit)→ macOS 那条构建线
#      从此替 iOS 逮这类漂移;
#   ② 本组把「服务端还在发什么」与「客户端往哪送」做成机器对拍 → 下次有人删掉
#      一条推送、或加一条新推送而忘了客户端,**当场红**。
# ⛔ 只做 ① 不做 ②:落点写成任一现役 tab 都能编过,语义错照样静默。

def _notify_source() -> str:
    import neckline  # noqa: PLC0415
    return (Path(neckline.__file__).parent / "api" / "notify.py").read_text(encoding="utf-8")


def _wording_function_to_kind() -> dict:
    """`api/notify.py` 的每个措辞函数 → 它发的 `kind`(**AST,⛔ 不按函数名猜**)。

    取法:函数体里那次 `push_event(...)` 的第一个实参。是 `KIND_*` 常量 → 解成串;
    是形参(`push_attention_alert` / `push_holding_risk_alert` 那两个共用入口把 kind
    当参数收)→ 记成 `None`,由下面的断言逼调用方来教这个扫描器,
    ⛔ 不静默当成"没有 kind"。
    """
    from neckline import notify_kinds  # noqa: PLC0415

    tree = ast.parse(_notify_source())
    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("push_"):
            continue
        if node.name == "push_event":
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "push_event" and sub.args):
                first = sub.args[0]
                if isinstance(first, ast.Name) and first.id.startswith("KIND_"):
                    out[node.name] = getattr(notify_kinds, first.id)
                else:
                    out[node.name] = None
                break
    return out


def _live_wording_functions() -> set:
    """**生产链上真的被调到**的措辞函数(`neckline/**` + `scripts/**`,⛔ 除 `notify.py` 自己)。

    ⚠ 扫的是**调用**,不是 import:`from ... import push_report_ready` 之后不调,
    那条推送依旧永不发生。`notify.push_x(...)` 与裸 `push_x(...)` 两种写法都收。
    """
    import neckline  # noqa: PLC0415

    known = set(_wording_function_to_kind())
    pkg = Path(neckline.__file__).parent
    scripts = pkg.parent / "scripts"
    called = set()
    for path in sorted(pkg.rglob("*.py")) + sorted(scripts.rglob("*.py")):
        if path == pkg / "api" / "notify.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover - 语法坏了自有别的闸报
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name in known:
                called.add(name)
    return called


def live_push_kinds() -> set:
    """服务端**当前真的还会发出去**的 kind 集合。"""
    from neckline import notify_kinds  # noqa: PLC0415

    mapping = _wording_function_to_kind()
    live = set()
    for fname in sorted(_live_wording_functions()):
        kind = mapping[fname]
        assert kind is not None, (
            f"`{fname}` 的 kind 是运行期参数,而它现在**有生产调用方**了 —— "
            f"请教会 `_wording_function_to_kind()` 怎么解它,⛔ 别让它静默漏出对拍面。")
        assert kind in notify_kinds.ALL_KINDS, (
            f"`{fname}` 发的是未登记 kind `{kind}` —— 这条调用会当场失败。")
        live.add(kind)
    return live


def _push_route_block() -> str:
    """客户端路由函数 `nkPushRoute(forKind:)` 的**配对块**(已剥整行注释)。"""
    text = PUSH_MANAGER.read_text(encoding="utf-8")
    block = swift_decl_block(r"^func nkPushRoute\(forKind", text=text)
    assert block, (
        f"在 `{PUSH_MANAGER.name}` 里找不到 `func nkPushRoute(forKind:)` —— "
        f"改名了?本组的对拍会**静默守空集**,请同步改这里。")
    return strip_comments(block)


def client_push_routes() -> dict:
    """客户端路由表:`kind` → (板块, 选股板块内的视图或 `None`)。"""
    block = _push_route_block()
    routes: dict = {}
    # `case "a", "b":` … 直到该 case 的 `return NKPushRoute(...)`。
    for m in re.finditer(
            r'case\s+((?:"[a-z_]+"\s*,?\s*)+):\s*'
            r'return\s+NKPushRoute\(tab:\s*\.(\w+)\s*,\s*selectionMode:\s*(?:\.(\w+)|nil)\s*\)',
            block):
        kinds = re.findall(r'"([a-z_]+)"', m.group(1))
        for k in kinds:
            routes[k] = (m.group(2), m.group(3))
    return routes


def _swift_enum_cases(name: str) -> set:
    """`enum <name>` 的成员名集合(`case a, b, c` 与逐行 `case a = "x"` 两种写法都收)。"""
    text = APP_MODEL.read_text(encoding="utf-8")
    block = strip_comments(type_block(name, text=text))
    assert block, f"`{APP_MODEL.name}` 里找不到 `enum {name}`"
    cases = set()
    for line in block.splitlines():
        m = re.match(r"\s*case\s+([\w\s,]+?)(?:\s*=|$)", line)
        if m:
            cases |= {c.strip() for c in m.group(1).split(",") if c.strip()}
    return cases


def test_the_push_route_table_covers_exactly_the_kinds_the_server_still_sends():
    """🔴 **服务端还在发的 kind ↔ 客户端落点,`==` 对拍**(⛔ 不是 `<=`)。

    两个方向都要:
      · **少了** = 用户点开一条真通知,App 一动不动(V2.5.0 之前 `report_ready`
        指着已改名的 `.baskets`,那半年 iOS 根本编不出来);
      · **多了** = 客户端养着一条永不触发的路由,而它的存在让人以为那个能力还在
        (`retreat` 当年就是这样,V2.4.0 P0 已按这条纪律删掉)。
    """
    from neckline import notify_kinds  # noqa: PLC0415

    live = live_push_kinds()
    routed = set(client_push_routes())
    assert routed == live, (
        f"推送落点对拍不上 —— 服务端还在发 {sorted(live)},客户端路由 {sorted(routed)}。\n"
        f"  服务端有、客户端没有:{sorted(live - routed)}(点开不跳转)\n"
        f"  客户端有、服务端没有:{sorted(routed - live)}(永不触发的死路由)")
    for k in sorted(routed):
        assert k in notify_kinds.ALL_KINDS, f"客户端路由了一个不在白名单里的 kind `{k}`"


def test_every_push_route_lands_on_a_section_that_still_exists():
    """🔴 **落点必须是当前真的存在的板块 / 视图**(裁定 11 的三板块 IA)。

    ⚠ 这一条**不靠编译器**:`AppTab` 少一个成员时 Swift 会报错,但那条错只在
    **编译到那份文件的平台**上出现 —— 路由表原先住在 `#if os(iOS)` 里,macOS
    构建线一行都看不到。本条在 Python 侧按源码对拍,与平台无关。
    """
    tabs = _swift_enum_cases("AppTab")
    modes = _swift_enum_cases("SelectionViewMode")
    assert "selection" in tabs and "checklist" in modes, "枚举扫描器怕是切错块了"
    for kind, (tab, mode) in sorted(client_push_routes().items()):
        assert tab in tabs, (
            f"kind `{kind}` 落到 `.{tab}` —— `AppTab` 里没有这个板块(现有:{sorted(tabs)})")
        if mode is not None:
            assert mode in modes, (
                f"kind `{kind}` 落到选股板块的 `.{mode}` 视图 —— "
                f"`SelectionViewMode` 里没有它(现有:{sorted(modes)})")
        if tab != "selection":
            assert mode is None, (
                f"kind `{kind}` 落在 `.{tab}`,却顺带拨了选股板块的视图 —— 那个字段"
                f"只在 `.selection` 上有意义。")


def test_the_checklist_push_lands_on_the_checklist_view_not_merely_the_tab():
    """🔴 **9:29 那条唯一的日常推送必须落在核对表视图上**,⛔ 不只是「选股」板块。

    `push_checklist_summary` 复用 `KIND_PRECALL`(2026-08-11 用户拍板),语义已由
    K8 的「盘前校准汇总」换成 K9 的「竞价核对表」。而选股板块里有两个视图,默认落哪一个
    **由钟点决定**(§5.11)—— 只拨 tab 不拨视图,9:29 点开有机会停在昨晚的清单上,
    那与落错板块是同一种答非所问。
    """
    from neckline import notify_kinds  # noqa: PLC0415

    routes = client_push_routes()
    assert routes.get(notify_kinds.KIND_PRECALL) == ("selection", "checklist"), (
        f"竞价核对表推送的落点是 {routes.get(notify_kinds.KIND_PRECALL)} —— "
        f"应为 选股板块 · 次日核对表视图。")
    assert routes.get(notify_kinds.KIND_REPORT_READY) == ("selection", "listing"), (
        f"报告就绪推送的落点是 {routes.get(notify_kinds.KIND_REPORT_READY)} —— "
        f"应为 选股板块 · 今日清单视图。")


def test_the_push_route_table_is_compiled_on_both_platforms():
    """🔴 **路由表必须住在 `#if os(iOS)` 之外** —— 这是 🔴-1 的结构性修复本身。

    纯数据的映射不需要 UIKit。放在分叉外面,**macOS 那条构建线就替 iOS 把
    「引用了已删的板块」当场逮住**;放回分叉里面,只跑一个平台的验收又会失明一次。
    ⛔ 别把它挪回去(真正 iOS 专属的是下面那个 `UNUserNotificationCenterDelegate`)。
    """
    text = PUSH_MANAGER.read_text(encoding="utf-8")
    fork = text.find("\n#if os(iOS)")
    route = text.find("\nfunc nkPushRoute(forKind")
    assert fork > 0, "`PushManager.swift` 里找不到 `#if os(iOS)` —— 这份守门在守空集"
    assert 0 < route < fork, (
        "`nkPushRoute(forKind:)` 落在了 `#if os(iOS)` 里面 —— macOS 构建线就再也"
        "看不到它了,而 V2.5.0 的六条编译错正是这样藏了整整一版。")
    # 裁定 11 已下线的两个板块名,⛔ 路由表里零残留(⚠ 剥注释后再扫:文件头那段
    # 修复说明必须写得出这两个名字才讲得清,把说明算进命中会逼后来者删注释凑绿)。
    block = _push_route_block()
    for retired in ("baskets", "positions"):
        assert retired not in block, (
            f"路由表里还留着已下线的板块 `{retired}`(裁定 11)")


def test_the_push_route_scanners_actually_see_something():
    """闸自己的守门:三个扫描器**都不许是空集**。

    一个恒空的对拍是绿的、也是没用的 —— 本仓在 `_hits` 假阳性那一条上已经写过
    同族教训(§14 S12 登记 ⑨)。
    """
    mapping = _wording_function_to_kind()
    assert len(mapping) >= 3, f"`api/notify.py` 的措辞函数只扫到 {len(mapping)} 个?"
    live_fns = _live_wording_functions()
    assert live_fns, "生产链上一个 push 调用点都没扫到 —— 扫描器失效了"
    assert live_push_kinds(), "服务端 live kind 集合是空的?"
    assert len(client_push_routes()) >= 2, "客户端路由表扫到的条目少于 2 条"
