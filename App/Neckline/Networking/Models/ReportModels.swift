//
//  ReportModels.swift
//  Neckline — 客户端展示层数据模型 · 整份报告 + 计划继承 / 建仓快照 / 画像 / 策略包 / 评价(A 类)+ 完整信息卡
//  + 复盘情报件 / 板块资金流 / 消息面
//
//  ⚠ **V2.4.0 P3.7 纯机械拆分**:本文件与同目录另外五份 `*Models.swift` 是原
//  `Networking/Models.swift`(5633 行)**逐字**切出来的,⛔ 一个声明没改、没加、没删
//  (切点全在顶层 `// MARK:` 之前的空行上;拆分脚本对拼回来的全文做过逐字节比对)。
//  🔴 **守门单测不再按 `Models.swift` 这个文件名读客户端 DTO** —— 一律走
//  `tests/client_sources.py::networking_swift_text()`(把本目录全部 `.swift` 拼起来)。
//  ⛔ 新增 DTO 文件必须放在本目录下,否则那些「某字段已退役」的**缺席断言**会静默变成
//  真(读不到的文件里当然搜不到),看起来还全绿 —— 这是拆分引入的唯一新风险面,
//  `tests/client_sources.py` 里的哨兵断言就是为它立的。
//
//  ⚠ 加 / 移动 `.swift` 与新增一样,**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2 计划继承 / 建仓快照 / 画像 / 策略包 / 评价(A 类)
// ══════════════════════════════════════════════════════════════════════════

/// 一条持仓计划版本(⑩-B)。`version == 1` 恒从 D0 篮子卡继承;用户可创建
/// `version = 2,3…`,**新版本不修改原始篮子卡**。
///
/// `plan` **原样透传领域 `plan_json`(snake_case)** —— 它是哨兵旁路 E 的判据源,
/// 四个武装态键(`exit_reference_armed` / `..._reason` / `..._note` / `..._muted`)
/// **恒存在**,缺键即不武装(fail-closed)。
struct PositionPlan: Codable, Equatable, Identifiable {
    var id: Int = 0
    var positionId: Int = 0
    var version: Int = 1
    var sourceBasketId: Int? = nil
    var sourceCardVersion: Int? = nil
    var plan: NKJSON = .object([:])
    var note: String? = nil
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey {
        case id, positionId, version, sourceBasketId, sourceCardVersion, plan, note, createdAt
    }

    init(id: Int = 0, positionId: Int = 0, version: Int = 1, sourceBasketId: Int? = nil,
         sourceCardVersion: Int? = nil, plan: NKJSON = .object([:]), note: String? = nil,
         createdAt: String = "") {
        self.id = id; self.positionId = positionId; self.version = version
        self.sourceBasketId = sourceBasketId; self.sourceCardVersion = sourceCardVersion
        self.plan = plan; self.note = note; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        positionId = try c.decodeIfPresent(Int.self, forKey: .positionId) ?? 0
        version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 1
        sourceBasketId = try c.decodeIfPresent(Int.self, forKey: .sourceBasketId)
        sourceCardVersion = try c.decodeIfPresent(Int.self, forKey: .sourceCardVersion)
        plan = try c.decodeIfPresent(NKJSON.self, forKey: .plan) ?? .object([:])
        note = try c.decodeIfPresent(String.self, forKey: .note)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    // —— `plan_json`(snake_case)便利读取。⛔ 只读不算,任何判定都在服务端 ——

    /// `false` = 无来源篮子 或 篮子有但卡未就绪(**合法**,行照落,不是错误)。
    var available: Bool { plan["available"]?.boolValue ?? false }
    /// `no_source_basket` | `card_not_ready` | `card_corrupt`;`available == true` 时为 nil。
    var unavailableReason: String? { plan["reason"]?.stringValue }
    var unavailableText: String? {
        switch unavailableReason {
        case "no_source_basket": return "独立买入 · 没有来源篮子可继承"
        case "card_not_ready": return "有来源篮子,但当时卡还没生成"
        // `card_corrupt`(2026-08-04,B1 同类裁定):**不是**"还没生成"——那张卡是
        // 冻结件、有行但读不出,坏了就是永久坏的。⛔ 不与上一条合并展示,⛔ 不进任何
        // 静默重试路径(同 `APIError.cardCorrupt` 的既定文案方向)。
        case "card_corrupt": return "来源卡数据损坏,已记录待排查"
        case .some(let r): return r
        case .none: return nil
        }
    }
    var sourceBasketKey: String? { plan["source_basket_key"]?.stringValue }
    var sourceBasketName: String? { plan["source_basket_name"]?.stringValue }
    var driver: String? { plan["driver"]?.stringValue }
    var entryZone: BasketPriceBand? { Self.band(plan["entry_zone"]) }
    var entryZoneClamp: String { plan["entry_zone_clamp"]?.stringValue ?? "" }
    var maxChase: Double? { plan["max_chase"]?.doubleValue }
    var maxChaseClamp: String { plan["max_chase_clamp"]?.stringValue ?? "" }
    var exitReference: BasketPriceBand? { Self.band(plan["exit_reference"]) }
    var exitReferenceClamp: String { plan["exit_reference_clamp"]?.stringValue ?? "" }
    var risks: [String] { (plan["risks"]?.arrayValue ?? []).compactMap { $0.stringValue } }

    /// ⑪-D 武装态(**派生态**,由服务端重算,客户端说了不算)。
    var exitReferenceArmed: Bool { plan["exit_reference_armed"]?.boolValue ?? false }
    var exitReferenceArmedReason: String? { plan["exit_reference_armed_reason"]?.stringValue }
    /// 未武装理由的人读文案(**服务端单一源**,客户端不另拍文案)。
    var exitReferenceArmedNote: String? { plan["exit_reference_armed_note"]?.stringValue }
    /// ⑪-D-D 的**用户意图位**(per-position「不提醒」开关的真身)。与 `exitReferenceArmed`
    /// 分开存:一个是用户说的,一个是机械闸算的。
    var exitReferenceMuted: Bool { plan["exit_reference_muted"]?.boolValue ?? false }

    private static func band(_ v: NKJSON?) -> BasketPriceBand? {
        guard let obj = v?.objectValue else { return nil }
        return BasketPriceBand(low: obj["low"]?.doubleValue, high: obj["high"]?.doubleValue,
                               why: obj["why"]?.stringValue)
    }

    /// 翻转静音位后的新计划正文(⛔ **只翻这一个键,计划正文一项不动**)。
    /// 落法 = `POST /positions/{id}/plans` 追加新版本(版本化只增表,不就地改历史行)。
    func planBodyTogglingMute(_ muted: Bool) -> NKJSON {
        var obj = plan.objectValue ?? [:]
        obj["exit_reference_muted"] = .bool(muted)
        return .object(obj)
    }
}

/// 建仓瞬间的冻结快照(⑩-A,`entry_snapshots` 一行)。
/// `snapshot.not_captured` **如实列出本次没采到的项** —— ⛔ 别把"没采"读成"没有"。
struct EntrySnapshot: Codable, Equatable {
    var positionId: Int = 0
    var tsCode: String = ""
    var tradeDate: String = ""
    var basketId: Int? = nil
    var cardVersion: Int? = nil
    var tier: Int? = nil
    var role: String? = nil
    var snapshot: NKJSON = .object([:])
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey {
        case positionId, tsCode, tradeDate, basketId, cardVersion, tier, role, snapshot, createdAt
    }

    init(positionId: Int = 0, tsCode: String = "", tradeDate: String = "", basketId: Int? = nil,
         cardVersion: Int? = nil, tier: Int? = nil, role: String? = nil,
         snapshot: NKJSON = .object([:]), createdAt: String = "") {
        self.positionId = positionId; self.tsCode = tsCode; self.tradeDate = tradeDate
        self.basketId = basketId; self.cardVersion = cardVersion; self.tier = tier
        self.role = role; self.snapshot = snapshot; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        positionId = try c.decodeIfPresent(Int.self, forKey: .positionId) ?? 0
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId)
        cardVersion = try c.decodeIfPresent(Int.self, forKey: .cardVersion)
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        role = try c.decodeIfPresent(String.self, forKey: .role)
        snapshot = try c.decodeIfPresent(NKJSON.self, forKey: .snapshot) ?? .object([:])
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    /// 本次**没采到**的项(⑩ 范围内:资金流 / 竞价表现 / 换手率 / 量比四项未采集)。
    var notCaptured: [String] {
        (snapshot["not_captured"]?.arrayValue ?? []).compactMap { $0.stringValue }
    }
}

/// 偏好画像 / 能力画像(⑫-B,每期一版)。**两张账刻意分开**:偏好答「喜欢什么」、
/// 能力答「什么真有效」—— ⛔ 不合并成一张"用户画像"。
/// `asOf` 为空 = **该期从未算过**(不是"算出来是空的")。
struct Profile: Codable, Equatable {
    var asOf: String = ""
    var available: Bool = false
    var unavailableReason: String? = nil
    var items: [NKJSON] = []

    enum CodingKeys: String, CodingKey { case asOf, available, unavailableReason, items }

    init(asOf: String = "", available: Bool = false, unavailableReason: String? = nil,
         items: [NKJSON] = []) {
        self.asOf = asOf; self.available = available
        self.unavailableReason = unavailableReason; self.items = items
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        asOf = try c.decodeIfPresent(String.self, forKey: .asOf) ?? ""
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        items = try c.decodeIfPresent([NKJSON].self, forKey: .items) ?? []
    }
}

/// 画像一行的读取视图。每行必带**样本量 / 时间范围 / 置信度**;
/// `confidence == "low"` 时**必须**显式写「样本不足,不给结论」,⛔ 不许把低置信度的
/// 数字当结论展示(⑫ 验收条款)。
struct ProfileRow: Identifiable, Equatable {
    let raw: NKJSON
    var id: String { "\(dimension)|\(bucket)" }

    var dimension: String { raw["dimension"]?.stringValue ?? "" }
    /// ⚠ **服务端这一格叫 `value`,不叫 `bucket`**(`profile/store.py::list_*` 的行形状:
    /// `dimension` / `value` / `share` / `sampleN` / …)。V2.1-⑦ 修:原实现只读 `bucket`,
    /// 于是**每一行的分组名恒为空**(界面上显示成「role · 」),而且**看不出是 bug** ——
    /// 一行画像看起来只是"没写清楚"。`bucket` 保留在第一顺位只为向前兼容,⛔ 别删回去。
    var bucket: String { raw["bucket"]?.stringValue ?? raw["value"]?.stringValue ?? "" }
    var sampleN: Int { raw["sample_n"]?.intValue ?? raw["sampleN"]?.intValue ?? 0 }
    var windowStart: String { raw["window_start"]?.stringValue ?? raw["windowStart"]?.stringValue ?? "" }
    var windowEnd: String { raw["window_end"]?.stringValue ?? raw["windowEnd"]?.stringValue ?? "" }
    var confidence: String { raw["confidence"]?.stringValue ?? "" }
    var isLowConfidence: Bool { confidence == "low" }
    /// 除去上述元信息之外的度量键(按字典序,确定性)。
    /// ⚠ `value` 也算元信息(它就是 `bucket` 那一格,见上)——不排除会在度量区再列一遍。
    var metricKeys: [String] {
        let meta: Set<String> = ["dimension", "bucket", "value", "sample_n", "sampleN",
                                 "window_start", "windowStart", "window_end", "windowEnd",
                                 "confidence"]
        return raw.sortedKeys.filter { !meta.contains($0) }
    }
}

/// 一个选股策略包(`selection_packs` 一行)。
/// ⚠ **策略包与纪律章程是两条版本线、两张表、两套激活流程,永不混用** ——
/// 本类型**不含任何纪律参数**(`stop_pct` 等住 `strategy_versions`)。
struct Pack: Codable, Equatable, Identifiable {
    var packVersion: String = ""
    var isActive: Bool = false
    var createdAt: String = ""
    var activatedAt: String? = nil
    var manifest: NKJSON = .object([:])
    var config: NKJSON = .object([:])

    var id: String { packVersion }

    enum CodingKeys: String, CodingKey {
        case packVersion, isActive, createdAt, activatedAt, manifest, config
    }

    init(packVersion: String = "", isActive: Bool = false, createdAt: String = "",
         activatedAt: String? = nil, manifest: NKJSON = .object([:]),
         config: NKJSON = .object([:])) {
        self.packVersion = packVersion; self.isActive = isActive; self.createdAt = createdAt
        self.activatedAt = activatedAt; self.manifest = manifest; self.config = config
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion) ?? ""
        isActive = try c.decodeIfPresent(Bool.self, forKey: .isActive) ?? false
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        activatedAt = try c.decodeIfPresent(String.self, forKey: .activatedAt)
        manifest = try c.decodeIfPresent(NKJSON.self, forKey: .manifest) ?? .object([:])
        config = try c.decodeIfPresent(NKJSON.self, forKey: .config) ?? .object([:])
    }
}

/// 周度评价校准报告(⑨-C,含安慰剂对照臂)。
/// ⚠ **评价是长期统计,不是单日打分**:`available == false` 时 `unavailableReason` 必有值,
/// ⛔ 不许拿半截样本给结论。
struct EvalWeekly: Codable, Equatable {
    var weekStart: String = ""
    var weekEnd: String = ""
    var available: Bool = false
    var unavailableReason: String? = nil
    var result: NKJSON = .object([:])
    var markdown: String = ""

    enum CodingKeys: String, CodingKey {
        case weekStart, weekEnd, available, unavailableReason, result, markdown
    }

    init(weekStart: String = "", weekEnd: String = "", available: Bool = false,
         unavailableReason: String? = nil, result: NKJSON = .object([:]), markdown: String = "") {
        self.weekStart = weekStart; self.weekEnd = weekEnd; self.available = available
        self.unavailableReason = unavailableReason; self.result = result; self.markdown = markdown
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart) ?? ""
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd) ?? ""
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        result = try c.decodeIfPresent(NKJSON.self, forKey: .result) ?? .object([:])
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
    }
}

// MARK: - v1.4-④ 信息卡(完整,`GET /report/{date}/info-card/{code}` 专用,§五 v1.4-④)
//
// 摘要位共用的 `InfoCardSnapshot`/`InfoCardNews`/`InfoCardTopList` 已在上面声明;
// 这里只补 60 日序列 + 红黄牌明细专属类型。**第〇原则(考卷
// 同构)**:数据不可得如实缺省,禁止硬凑——每一路数据源独立 `*Available`/
// `*UnavailableReason`,任何一路缺失都不得连带其余各路"看起来也不可用"。
// ⚠ V2-⑬-4:信息卡页原先复用 `Candidate.execHints` 展示执行提示卡,该键已删 →
// 执行提示卡随之下线(⑬-N 改造后信息卡的三块新内容归 ⑮ 出 UI)。

struct InfoCardKlineBar: Codable, Equatable, Identifiable {
    var tradeDate: String
    var open: Double
    var high: Double
    var low: Double
    var close: Double
    var vol: Double
    var ma20: Double? = nil      // 早期行(历史不足窗口)→ nil,不是"均线为 0"
    var ma250: Double? = nil

    var id: String { tradeDate }
    var isUp: Bool { close >= open }
}

/// RS 线 / 行业分歧线 / 大盘指数化线共用的一个点(起点归一 100)。
struct InfoCardIndexPoint: Codable, Equatable, Identifiable {
    var tradeDate: String
    var value: Double

    var id: String { tradeDate }
}

/// 红黄牌明细。`section`:hard_cut(红牌)| avoid_flag(黄牌)——展示层换算见
/// `nkK4SectionLabel`/`nkK4SectionTone`,同 `board`/`NewsCategory` 惯例服务端不存中文。
struct InfoCardK4Flag: Codable, Equatable, Identifiable {
    var code: String
    var label: String
    var level: String              // strong | normal
    var section: String            // hard_cut | avoid_flag
    var evidenceStrength: String   // price_volume | constituent
    var evidence: String

    var id: String { code }
    var sectionLabel: String { nkK4SectionLabel(section) }
    var sectionTone: NKAxisTone { nkK4SectionTone(section) }
}

/// 市场语境(报告级构件,考卷 §三.8 同构位——大盘 60 日指数化形态 + 当日涨跌停家数 +
/// 大盘 MA20 上下)。
struct InfoCardMarket: Codable, Equatable {
    var indexCode: String = "000001.SH"
    var indexLine: [InfoCardIndexPoint] = []
    var limitUpCount: Int = 0
    var limitDownCount: Int = 0
    var aboveMa20: Bool? = nil
}

/// ⑬-N 三块之一:①所属篮子与共同驱动 ②本票角色(含对拍分歧)③与同篮其他成员的对比。
/// `available == false` 时 `unavailableReason` **必有值且两态分得开**:
/// 「不在任何篮子里」vs「在篮子里但卡没生成」—— ⛔ 不许显示成同一句话。
struct InfoCardBasketPeer: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var roleLlm: String? = nil
    var roleMech: String? = nil
    var roleConflict: Bool = false
    var rsRank: Int? = nil
    var close: Double? = nil
    var industry: String? = nil

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, roleLlm, roleMech, roleConflict, rsRank, close, industry
    }

    init(tsCode: String = "", name: String = "", roleLlm: String? = nil, roleMech: String? = nil,
         roleConflict: Bool = false, rsRank: Int? = nil, close: Double? = nil,
         industry: String? = nil) {
        self.tsCode = tsCode; self.name = name; self.roleLlm = roleLlm; self.roleMech = roleMech
        self.roleConflict = roleConflict; self.rsRank = rsRank; self.close = close
        self.industry = industry
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        roleLlm = try c.decodeIfPresent(String.self, forKey: .roleLlm)
        roleMech = try c.decodeIfPresent(String.self, forKey: .roleMech)
        roleConflict = try c.decodeIfPresent(Bool.self, forKey: .roleConflict) ?? false
        rsRank = try c.decodeIfPresent(Int.self, forKey: .rsRank)
        close = try c.decodeIfPresent(Double.self, forKey: .close)
        industry = try c.decodeIfPresent(String.self, forKey: .industry)
    }

    /// 角色两说并存(同 `BasketMember.roleDisplay`,⛔ 冲突时不挑一个当正确答案)。
    var roleDisplay: String {
        nkRoleDisplay(roleLlm: roleLlm, roleMech: roleMech, roleConflict: roleConflict)
    }
}

struct InfoCardBasket: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var basketId: Int? = nil
    var basketKey: String = ""
    var name: String = ""
    var tier: Int? = nil
    var driver: String = ""
    var driverKind: String = ""
    var whyNow: String = ""
    var roleLlm: String? = nil
    var roleMech: String? = nil
    var roleConflict: Bool = false
    var roleReason: String = ""
    var isPrimary: Bool = false
    /// 🔴 裁定 ⑤:主归属的确认状态。`nil` / 空串 = **这张卡没记**(老 `basket_card_v4`
    /// 及更早),⛔ 不许当成 `confirmed`。
    var primaryStatus: String? = nil
    var primaryPendingReason: String? = nil
    var industry: String? = nil
    var industryLift: Double? = nil
    var peers: [InfoCardBasketPeer] = []

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, basketId, basketKey, name, tier
        case driver, driverKind, whyNow, roleLlm, roleMech, roleConflict, roleReason
        case isPrimary, industry, industryLift, peers
        case primaryStatus, primaryPendingReason
    }

    init(available: Bool = false, unavailableReason: String? = nil, basketId: Int? = nil,
         basketKey: String = "", name: String = "", tier: Int? = nil, driver: String = "",
         driverKind: String = "", whyNow: String = "", roleLlm: String? = nil,
         roleMech: String? = nil, roleConflict: Bool = false, roleReason: String = "",
         isPrimary: Bool = false, primaryStatus: String? = nil,
         primaryPendingReason: String? = nil,
         industry: String? = nil, industryLift: Double? = nil,
         peers: [InfoCardBasketPeer] = []) {
        self.available = available; self.unavailableReason = unavailableReason
        self.basketId = basketId; self.basketKey = basketKey; self.name = name; self.tier = tier
        self.driver = driver; self.driverKind = driverKind; self.whyNow = whyNow
        self.roleLlm = roleLlm; self.roleMech = roleMech; self.roleConflict = roleConflict
        self.roleReason = roleReason; self.isPrimary = isPrimary; self.industry = industry
        self.primaryStatus = primaryStatus; self.primaryPendingReason = primaryPendingReason
        self.industryLift = industryLift; self.peers = peers
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId)
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        driver = try c.decodeIfPresent(String.self, forKey: .driver) ?? ""
        driverKind = try c.decodeIfPresent(String.self, forKey: .driverKind) ?? ""
        whyNow = try c.decodeIfPresent(String.self, forKey: .whyNow) ?? ""
        roleLlm = try c.decodeIfPresent(String.self, forKey: .roleLlm)
        roleMech = try c.decodeIfPresent(String.self, forKey: .roleMech)
        roleConflict = try c.decodeIfPresent(Bool.self, forKey: .roleConflict) ?? false
        roleReason = try c.decodeIfPresent(String.self, forKey: .roleReason) ?? ""
        isPrimary = try c.decodeIfPresent(Bool.self, forKey: .isPrimary) ?? false
        // 裁定 ⑤:缺键 = 老卡没记(⛔ 不补一个 confirmed)。
        primaryStatus = try c.decodeIfPresent(String.self, forKey: .primaryStatus)
        primaryPendingReason = try c.decodeIfPresent(String.self, forKey: .primaryPendingReason)
        industry = try c.decodeIfPresent(String.self, forKey: .industry)
        industryLift = try c.decodeIfPresent(Double.self, forKey: .industryLift)
        peers = try c.decodeIfPresent([InfoCardBasketPeer].self, forKey: .peers) ?? []
    }

    /// 两态**分得开**的诚实文案(⛔ 不许合并)。
    var unavailableText: String? {
        guard !available else { return nil }
        switch unavailableReason {
        case "not_in_any_basket": return "这只票不在当日任何篮子里"
        case "card_not_ready": return "在篮子里,但那张卡还没生成"
        case .some(let r): return r
        case .none: return "篮子信息暂不可用"
        }
    }

    /// 角色两说并存(同 `BasketMember.roleDisplay`,⛔ 冲突时不挑一个当正确答案)。
    var roleDisplay: String {
        nkRoleDisplay(roleLlm: roleLlm, roleMech: roleMech, roleConflict: roleConflict)
    }
}

/// 完整信息卡。⚠ **本类型全部属性手写 `init(from:)`**(⑭-C 对拍表 §七-2 登记项):
/// 原先用合成 `Codable` 直接解 wire,**每个属性都是必需的** —— 服务端日后停发任一键
/// 就是整张卡解不出。⑮ 动了 info-card 契约(⑬-N 三块 + ⑬-N-K7 标注件),照 CLAUDE.md
/// 「服务端删/停发任何键之前先查已装客户端是不是硬解码」这条,顺手把它改成可选解码。
struct InfoCard: Codable, Equatable {
    var code: String = ""
    var name: String = ""
    var tradeDate: String = ""
    var klineAvailable: Bool = false
    var kline: [InfoCardKlineBar] = []
    var klineUnavailableReason: String? = nil
    var rsAvailable: Bool = false
    var rsLine: [InfoCardIndexPoint] = []
    var rsBenchmark: String = "000001.SH"
    var rsUnavailableReason: String? = nil
    var industryDivergenceAvailable: Bool = false
    var industryDivergenceLine: [InfoCardIndexPoint] = []
    var industry: String = ""
    var industryDivergenceNote: String = "行业线=行业成员中位数合成,非申万官方指数"
    var industryDivergenceUnavailableReason: String? = nil
    var snapshot: InfoCardSnapshot = InfoCardSnapshot()
    var k4Flags: [InfoCardK4Flag] = []
    var mildBand: Bool = false
    var news: InfoCardNews = InfoCardNews(scanned: false)
    var topList: InfoCardTopList = InfoCardTopList()
    var market: InfoCardMarket = InfoCardMarket()
    // —— V2-⑬-N:篮子成员详情页 + ⑬-N-K7 标注件展示区 ————————————————————
    var basket: InfoCardBasket = InfoCardBasket()
    var tags: [BasketMemberTag] = []
    /// **判不了的标注码**(数据缺失)—— 与「判过没命中」是两回事,⛔ 不许合并成"没有标注"。
    var tagsAbsent: [String] = []
    var tagAbsences: [BasketTagAbsence] = []

    enum CodingKeys: String, CodingKey {
        case code, name, tradeDate, klineAvailable, kline, klineUnavailableReason
        case rsAvailable, rsLine, rsBenchmark, rsUnavailableReason
        case industryDivergenceAvailable, industryDivergenceLine, industry
        case industryDivergenceNote, industryDivergenceUnavailableReason
        case snapshot, k4Flags, mildBand, news, topList, market
        case basket, tags, tagsAbsent, tagAbsences
    }

    init(code: String = "", name: String = "", tradeDate: String = "",
         klineAvailable: Bool = false, kline: [InfoCardKlineBar] = [],
         klineUnavailableReason: String? = nil, rsAvailable: Bool = false,
         rsLine: [InfoCardIndexPoint] = [], rsBenchmark: String = "000001.SH",
         rsUnavailableReason: String? = nil, industryDivergenceAvailable: Bool = false,
         industryDivergenceLine: [InfoCardIndexPoint] = [], industry: String = "",
         industryDivergenceNote: String = "行业线=行业成员中位数合成,非申万官方指数",
         industryDivergenceUnavailableReason: String? = nil,
         snapshot: InfoCardSnapshot = InfoCardSnapshot(), k4Flags: [InfoCardK4Flag] = [],
         mildBand: Bool = false, news: InfoCardNews = InfoCardNews(scanned: false),
         topList: InfoCardTopList = InfoCardTopList(), market: InfoCardMarket = InfoCardMarket(),
         basket: InfoCardBasket = InfoCardBasket(), tags: [BasketMemberTag] = [],
         tagsAbsent: [String] = [], tagAbsences: [BasketTagAbsence] = []) {
        self.code = code; self.name = name; self.tradeDate = tradeDate
        self.klineAvailable = klineAvailable; self.kline = kline
        self.klineUnavailableReason = klineUnavailableReason
        self.rsAvailable = rsAvailable; self.rsLine = rsLine; self.rsBenchmark = rsBenchmark
        self.rsUnavailableReason = rsUnavailableReason
        self.industryDivergenceAvailable = industryDivergenceAvailable
        self.industryDivergenceLine = industryDivergenceLine; self.industry = industry
        self.industryDivergenceNote = industryDivergenceNote
        self.industryDivergenceUnavailableReason = industryDivergenceUnavailableReason
        self.snapshot = snapshot; self.k4Flags = k4Flags; self.mildBand = mildBand
        self.news = news; self.topList = topList; self.market = market
        self.basket = basket; self.tags = tags; self.tagsAbsent = tagsAbsent
        self.tagAbsences = tagAbsences
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decodeIfPresent(String.self, forKey: .code) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        klineAvailable = try c.decodeIfPresent(Bool.self, forKey: .klineAvailable) ?? false
        kline = try c.decodeIfPresent([InfoCardKlineBar].self, forKey: .kline) ?? []
        klineUnavailableReason = try c.decodeIfPresent(String.self, forKey: .klineUnavailableReason)
        rsAvailable = try c.decodeIfPresent(Bool.self, forKey: .rsAvailable) ?? false
        rsLine = try c.decodeIfPresent([InfoCardIndexPoint].self, forKey: .rsLine) ?? []
        rsBenchmark = try c.decodeIfPresent(String.self, forKey: .rsBenchmark) ?? "000001.SH"
        rsUnavailableReason = try c.decodeIfPresent(String.self, forKey: .rsUnavailableReason)
        industryDivergenceAvailable = try c.decodeIfPresent(
            Bool.self, forKey: .industryDivergenceAvailable) ?? false
        industryDivergenceLine = try c.decodeIfPresent(
            [InfoCardIndexPoint].self, forKey: .industryDivergenceLine) ?? []
        industry = try c.decodeIfPresent(String.self, forKey: .industry) ?? ""
        industryDivergenceNote = try c.decodeIfPresent(
            String.self, forKey: .industryDivergenceNote) ?? "行业线=行业成员中位数合成,非申万官方指数"
        industryDivergenceUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .industryDivergenceUnavailableReason)
        snapshot = try c.decodeIfPresent(InfoCardSnapshot.self,
                                          forKey: .snapshot) ?? InfoCardSnapshot()
        k4Flags = try c.decodeIfPresent([InfoCardK4Flag].self, forKey: .k4Flags) ?? []
        mildBand = try c.decodeIfPresent(Bool.self, forKey: .mildBand) ?? false
        news = try c.decodeIfPresent(InfoCardNews.self,
                                      forKey: .news) ?? InfoCardNews(scanned: false)
        topList = try c.decodeIfPresent(InfoCardTopList.self, forKey: .topList) ?? InfoCardTopList()
        market = try c.decodeIfPresent(InfoCardMarket.self, forKey: .market) ?? InfoCardMarket()
        basket = try c.decodeIfPresent(InfoCardBasket.self, forKey: .basket) ?? InfoCardBasket()
        tags = try c.decodeIfPresent([BasketMemberTag].self, forKey: .tags) ?? []
        tagsAbsent = try c.decodeIfPresent([String].self, forKey: .tagsAbsent) ?? []
        tagAbsences = try c.decodeIfPresent([BasketTagAbsence].self, forKey: .tagAbsences) ?? []
    }
}

// MARK: - 4A.2 报告:整份报告

// MARK: - v1.3-③-C1 复盘情报件(ReportOut.intel;服务端 `Dict[str,Any]` 透传,§2.3)
//
// 后端 `report/intel.py::IntelReport.to_public_dict()` 是这份数据的唯一形状源(同
// `sentiment`/`sectors` 的透传惯例);客户端仍按已知稳定形状声明强类型 Codable
// (同 `ReviewWeeklyResult` 先例)。**证据强度标注**:题材/成分类字段
// (`evidenceStrength`)依赖概念板块成分(K2「成分洞」)标「参考」;涨跌幅/涨停梯队/
// 跌停榜/大盘量能/市值偏好/涨跌停制度偏好均为 EOD 硬数据(强证据)。

struct IntelMover: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var pctChg: Double
    var close: Double
    var id: String { code }
}

struct IntelLimitLadderRung: Codable, Equatable, Identifiable {
    var consecDays: Int
    var count: Int
    var id: Int { consecDays }
}

struct IntelMarketVolume: Codable, Equatable {
    var shAmountYi: Double
    var szAmountYi: Double
    var totalAmountYi: Double
    var ma5AmountYi: Double
    var sampleDays: Int   // <5 时样本不足,§硬要求诚实标注(UI 据此加注"样本仅 N 日")
}

struct IntelThemeLeader: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var pctChg: Double
    var isLimitUp: Bool
    var id: String { code }
}

struct IntelThemeItem: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var boardAge: Int
    var ret20d: Double
    var persistenceLabel: String     // 服务端已是中文文案(未站上MA20/新起1日/持续2-3日/已延续≥4日)
    var evidenceStrength: String     // 恒 constituent(成分依赖,弱证据),同 K4Advisory 词表
    var leaders: [IntelThemeLeader]
    var id: String { code }
}

struct IntelBucketCount: Codable, Equatable, Identifiable {
    var label: String
    var count: Int
    var pctOfTotal: Double
    var id: String { label }
}

struct IntelSection: Codable, Equatable {
    var tradeDate: String
    var evidenceNote: String
    var gainers: [IntelMover]
    var losers: [IntelMover]
    var limitUpLadder: [IntelLimitLadderRung]
    var limitDown: [IntelMover]
    var limitDownTotalCount: Int      // 跌停榜展示有截断上限,这里是真实总数(截断不撒谎)
    var marketVolume: IntelMarketVolume?
    var topThemes: [IntelThemeItem]
    var themePersistenceDistribution: [String: Int]
    var mvPreference: [IntelBucketCount]
    var limitRegimePreference: [IntelBucketCount]
    var excludedBoardsNote: String
    var warnings: [String]

    /// 全空 = 这份报告快照压根没有情报节(旧报告 / 尚未生成),UI 据此展示"暂无"而非
    /// 空白卡片(§硬要求「没有 vs 没看」分开)。
    var hasContent: Bool {
        !(gainers.isEmpty && losers.isEmpty && limitUpLadder.isEmpty && limitDown.isEmpty
            && marketVolume == nil && topThemes.isEmpty)
    }
}

// MARK: - v1.3-③-C2 板块资金流(ReportOut.sectorMoneyflow)
//
// **定位写死(硬要求,不可当选股信号)**:拥挤情报件,STRATEGY_LAB K2 判决板块层有效
// 但无次日领先性——展示文案不得暗示"买入依据"。

struct SectorMoneyflowItem: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var netInflowWan: Double     // 万元,东财 moneyflow_dc 口径
    var memberCount: Int
    var rank: Int
    var evidenceStrength: String   // 恒 constituent(板块归属依赖成分快照,弱证据)
    var id: String { code }
}

struct SectorMoneyflowSection: Codable, Equatable {
    var tradeDate: String
    var available: Bool
    var unavailableReason: String   // available=false 时必读(2023-09 前无数据 / 当日缺失等)
    var topInflow: [SectorMoneyflowItem]
    var topOutflow: [SectorMoneyflowItem]
    var excludedBoardsNote: String
    var evidenceNote: String
}

// MARK: - v1.3-③-C4 消息面(ReportOut.newsAlerts + newsAlertsScan,§硬要求「没扫到 vs
// 扫了没有必须能区分」)

/// 消息面命中告警。`category` 服务端码(REDUCTION/INVESTIGATION/BLOWUP/REGULATORY),
/// 展示层中文换算见 `nkNewsCategoryLabel`(沿 `boardLabel` 先例,未识别原样透传)。
struct NewsAlert: Codable, Equatable, Identifiable {
    var code: String
    var name: String
    var category: String
    var summary: String
    var source: String   // tushare_holdertrade | llm_<provider>

    var id: String { "\(code)|\(category)|\(summary)" }
    var categoryLabel: String { nkNewsCategoryLabel(category) }
}

func nkNewsCategoryLabel(_ raw: String) -> String {
    switch raw {
    case "REDUCTION": return "减持"
    case "INVESTIGATION": return "立案"
    case "BLOWUP": return "暴雷"
    case "REGULATORY": return "监管"
    default: return raw
    }
}

/// 消息面扫描状态——**必须先读这个再展示 `newsAlerts`**,不能只看后者是否为空就下结论
/// (空数组本身无法表达"这次到底扫没扫、扫没扫完")。`codesSkipped`(墙钟预算耗尽、根本
/// 没发起调用就跳过)/ `codesFailed`(调用了但失败)/ `codesNoSearch`(调用成功但联网
/// 搜索命中 0 条,结论未经搜索证实)/ `codesRotationDeferred`(v1.4-⑥-B 自选隔日轮扫、
/// 本日轮空)**四者语义各不相同,必须分开展示,不许合并成一个"没扫到"数字**。
struct NewsAlertScanStatus: Codable, Equatable, Identifiable {
    var source: String       // tushare_holdertrade | llm
    var scanned: Bool
    var reason: String = ""
    var codesTotal: Int = 0
    var codesFailed: Int = 0
    var codesSkipped: Int = 0
    var codesNoSearch: Int = 0            // v1.3.4:调用成功但联网搜索命中 0 条的标的数
    // v1.4-⑥-B:自选隔日轮扫披露。`rotationGroup` = 本次扫的自选组("A"/"B",持仓每日
    // 必扫、不参与轮扫);`codesRotationDeferred` = 本日**轮空**(压根没进本次名单)的
    // 自选数。老报告快照没有这两个键 → 缺省 ""/0,前向兼容不崩。
    var rotationGroup: String = ""
    var codesRotationDeferred: Int = 0

    /// 显式 CodingKeys + 容错 `init(from:)`(本类型历经 v1.3-③-C4→v1.3.4→v1.4-⑥-B 三次
    /// 加字段,旧报告快照 / 手工 fixture 缺新键是常态——同 `IntelRank` 的处理姿势,不必
    /// 逐个改旧测试 fixture)。
    enum CodingKeys: String, CodingKey {
        case source, scanned, reason, codesTotal, codesFailed, codesSkipped, codesNoSearch
        case rotationGroup, codesRotationDeferred
    }

    init(source: String, scanned: Bool, reason: String = "", codesTotal: Int = 0, codesFailed: Int = 0,
         codesSkipped: Int = 0, codesNoSearch: Int = 0, rotationGroup: String = "",
         codesRotationDeferred: Int = 0) {
        self.source = source
        self.scanned = scanned
        self.reason = reason
        self.codesTotal = codesTotal
        self.codesFailed = codesFailed
        self.codesSkipped = codesSkipped
        self.codesNoSearch = codesNoSearch
        self.rotationGroup = rotationGroup
        self.codesRotationDeferred = codesRotationDeferred
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        scanned = try c.decodeIfPresent(Bool.self, forKey: .scanned) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        codesTotal = try c.decodeIfPresent(Int.self, forKey: .codesTotal) ?? 0
        codesFailed = try c.decodeIfPresent(Int.self, forKey: .codesFailed) ?? 0
        codesSkipped = try c.decodeIfPresent(Int.self, forKey: .codesSkipped) ?? 0
        codesNoSearch = try c.decodeIfPresent(Int.self, forKey: .codesNoSearch) ?? 0
        rotationGroup = try c.decodeIfPresent(String.self, forKey: .rotationGroup) ?? ""
        codesRotationDeferred = try c.decodeIfPresent(Int.self, forKey: .codesRotationDeferred) ?? 0
    }

    var id: String { source }
    var sourceLabel: String {
        switch source {
        case "tushare_holdertrade": return "减持(股东增减持,结构化数据)"
        case "llm": return "立案 / 暴雷 / 监管(LLM 联网核实)"
        default: return source
        }
    }
}

/// 数据新鲜度(v1.4-①-C 板块三键 + v1.4-⑩-F 行业强度三键;§七 P0-3 / P0-23)。
/// `sectorLagDays=-1` = 板块数据完全缺失(哨兵值,服务端
/// `report/sectors.py::SECTOR_LAG_UNKNOWN`,刻意不用 0——0 是"新鲜")。
/// `stale=true` 时「当日暴起板块」与「题材持续天数」本日不可信,须显式标注。
///
/// **⚠ `stale` 只表板块数据,一个字没改**;行业强度未就绪是**另一件独立故障**,走下面
/// 三个键。两者**不许合并成一个 bool** —— 合并就分不清哪个坏了(服务端同样并列存放)。
///
/// 三个行业强度键是 `Optional`:老报告快照(建于本字段前)没有这三键 → `nil` 兜底不崩。
///
/// **2026-08-05 契约类型核对订正一处不准确的旧注释**:此前这里写「`dataFreshness` 属于
/// `_shape_report` 每次响应重新构造那一类,不是 `reviews.result_json` 那种冻结快照」——
/// **这个定性是错的**:`app.py::_shape_report` 里 `dataFreshness=rep.get("data_freshness",
/// {})` 读的是 `reports.data_freshness_json` 列,与 `reviews.result_json` 同样是**写入
/// 当时冻住、读回原样不补全**的历史快照(`pipeline.py::build_report` 只在生成报告那一刻
/// 写一次)。本类型能安全用合成 `Codable`(非 Optional 只有 `sectorLagDays`/`stale` 两个)
/// 靠的是**另一条**、且更脆弱的理由:这两个必填键是 `dataFreshness` 概念最早的那版形状
/// (v1.4-①-C),写侧 `{**sector_freshness.to_public_dict(), ...}` 恒把它们俩**一起**摊开,
/// 从未有过"这两个键其中一个单独缺席"的写法——所以字典要么整个是 `{}`(建于 v1.4-①-C 之前
/// 的老报告 / `_empty_report()` 降级态),要么这两个键必然同现。**真正兜底的是调用点的
/// `try?`**(`APIClient.swift::ReportResponse.init(from:)`):`{}` 触发 `keyNotFound` 时靠它
/// 把"形状不对"归一成 `nil`,不是本类型自己健壮。⚠ 这正是 2026-08-05 同批发现的
/// `sentiment` 那个坑的反面教材——`sentiment` 满足一模一样的"写侧要么整体空、要么必填键
/// 同现"前提,却唯独调用点漏了 `try?`,详见 `APIClient.swift` 与
/// `DTODecodeTests.swift::testDecodeEmptyReportRealShapeSentimentIsEmptyObjectNotNull`。
/// 新增本类型的非 Optional 字段前,先确认写侧真的会让它与 `sectorLagDays`/`stale`
/// **恒同现**,否则请连同调用点一起手写容错(同 CLAUDE.md「V2-⑮ 起客户端 DTO 一律手写
/// `init(from:)`」这条纪律,本类型是当前唯一的、有条件成立的例外)。
struct DataFreshness: Codable, Equatable {
    var sectorDataDate: String?
    var sectorLagDays: Int
    var stale: Bool
    /// 行业强度预计算表(`industry_strength_daily`)库内最新日;`nil` = 完全无数据 / 老快照缺键。
    var industryStrengthDate: String? = nil
    /// 落后几个交易日;`-1` = 完全无数据(哨兵值,同 `sectorLagDays` 惯例)。
    var industryStrengthLagDays: Int? = nil
    /// `lag > 0` 即 true(**无容忍度** —— 行业强度用当日 EOD 算,16:05 当天就该有)。
    var industryStrengthStale: Bool? = nil
    // —— V2-⑭-A 市场扫描层三键(**第三组独立故障,⛔ 不与上面两组合并**)——————————
    // 扫描层没跑 → 今日无种子 → 今日无篮子;而「今天没有篮子」与「今天没看」必须能分开。
    // **该组三键整体缺席 = 本次连新鲜度都没查到**,⛔ 不是"新鲜"。
    /// 三张预计算表(`corr_matrix_daily`/`limit_cluster_daily`/`leader_structure_daily`)
    /// 批算到的最新交易日;`nil` = 完全无数据 / 老快照缺键。
    var scanLayerDate: String? = nil
    /// 落后几个交易日;`-1` = 完全无数据(哨兵值,同 `sectorLagDays` 惯例)。
    var scanLayerLagDays: Int? = nil
    var scanLayerStale: Bool? = nil

    /// 顶部横幅是否该出现:板块过期 **或** 行业强度未就绪 **或** 扫描层未就绪,
    /// 任一成立即展示(**三条各自成行**,合并就分不清哪个坏了)。
    var needsBanner: Bool { stale || industryStrengthStale == true || scanLayerStale == true }
}

/// 报告展示模型。
///
/// ⚠ **刻意不是 `Codable`**(⑭-C 对拍表 §七-1 登记项):`/report` 走 `APIClient` 里的
/// 私有 wire DTO 解码后**手工映射**到本类型,合成 `Decodable` 从来没被 JSON 解过 —— 是
/// 死代码,留着只会让「wire 字段清单」与「展示字段清单」两份各自漂。⑮ 顺手收掉。
struct ReportSnapshot: Equatable {
    var tradeDate: String
    var generatedAt: String
    var strategyVersion: String
    var sentiment: SentimentSnapshot?
    var sectors: [SectorSnapshot]
    /// V2-⑭-B:篮子日报三段(③ 今日篮子 / ③b 未定档 / ④ 昨日复盘),取代已退役的
    /// `candidates`。**随报告冻住**:读三天前的报告该看到当时的篮子,不是今天的。
    var basketDaily: BasketDaily = BasketDaily()
    var degraded: Bool
    var reason: String
    /// §五 v1.1-B.4 漏录兜底:当日买点哨兵触发过但台账无补录时的一句提示,否则空串
    /// (服务端实时算,用户补录后自动消失;E.3 据此在今日计划顶部展示提示条)。
    var missedEntryHint: String = ""
    // —— v1.3-③-C1/C2/C4「情报」板块(§五 v1.3-⑥-F)——————————————————————————————
    /// nil = 该报告快照没有情报节(旧报告 / 降级态,后端 `intel` 落空字典 `{}`,解码
    /// 阶段 `try?` 兜成 nil,见 `APIClient.ReportResponse`)。
    var intel: IntelSection? = nil
    var sectorMoneyflow: SectorMoneyflowSection? = nil
    var newsAlerts: [NewsAlert] = []
    var newsAlertsScan: [NewsAlertScanStatus] = []
    /// v1.4-①-C:板块数据新鲜度(§七 P0-3)。`nil` = 老报告(建于本字段前)或空对象
    /// (解码阶段同 `intel`/`sectorMoneyflow` 用 `try?` 归一),客户端按"该版本还没有
    /// 新鲜度概念"处理,不当"新鲜"展示。
    var dataFreshness: DataFreshness? = nil

    /// 空态占位(无报告 / 拉取失败),UI 据 `degraded`+`reason` 诚实展示,不假装有数据。
    static func empty(reason: String) -> ReportSnapshot {
        ReportSnapshot(tradeDate: "", generatedAt: "", strategyVersion: "",
                       sentiment: nil, sectors: [], basketDaily: BasketDaily(),
                       degraded: true, reason: reason)
    }
}
