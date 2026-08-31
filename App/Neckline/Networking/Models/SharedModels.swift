//
//  SharedModels.swift
//  Neckline — 客户端展示层数据模型 · **共享小件**:通用 JSON 载体 / 板块换算 /
//  设置(Provider 与推送开关)/ 展示用轴向着色。
//
//  对齐后端 `neckline/api/schemas.py`(逐字段对齐,⛔ 别猜)。出参 camelCase 直接
//  Codable 解码(服务端 pydantic 模型字段本身就是 camelCase,默认 keyDecodingStrategy
//  不做任何转换);服务端 `Dict[str,Any]` 原样透传的自由结构一律走 `NKJSON`。
//
//  本文件只维护当前 API 的共享 DTO；删除端点不保留客户端占位模型。
//
//  🔴 **DTO 的落点纪律**:客户端 DTO **必须**放在 `Networking/Models/` 下 ——
//  守门单测 `tests/test_contract_crosscheck.py` 走 `tests/client_sources.py` 把本目录
//  整棵子树拼起来读，避免字段缺席断言被拆散后失效。
//  ⚠ 加 / 移动 / 删 `.swift` 之后**必须 `xcodegen generate`**(pbxproj 是显式文件引用)。
//

import Foundation

// MARK: - 通用 JSON 值(服务端「自由结构原样透传」字段的载体)
//
// `mech` / `evidence` 之外的 `tierBreakdown` / `verificationSpec` / `invalidationSpec` /
// `fingerprint` / `plan` / `snapshot` / `rule` / `result` 这一族字段,服务端 `schemas.py`
// 的既定口径就是 `Dict[str, Any]` **原样透传**(该文件原话:「在 API 层再镜像一套嵌套
// 模型只会多一处会漂的定义」)。客户端同理:再镜像一份强类型只会多一处会漂的定义,
// 且 `tierBreakdown` 的键是**五维维度名**、`verificationSpec` 的键是**喂哨兵的条件名**
// ——那些是语义标识符,不是字段名,连 camel 化都刻意不做。
//
// 本类型只保证三件事:**解得出来 / 按需取值 / 诚实展示**。⛔ 客户端不用它重算任何判据。

enum NKJSON: Codable, Equatable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([NKJSON])
    case object([String: NKJSON])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        // ⚠ Bool 必须排在 Double 之前:JSON `true` 在 Foundation 里也能解成 1.0,
        // 顺序反了会把布尔悄悄变成数字(展示成 "1" 而不是 "是")。
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let d = try? c.decode(Double.self) { self = .number(d); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([NKJSON].self) { self = .array(a); return }
        if let o = try? c.decode([String: NKJSON].self) { self = .object(o); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "无法识别的 JSON 值")
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }

    var isNull: Bool { if case .null = self { return true }; return false }
    var stringValue: String? { if case .string(let v) = self { return v }; return nil }
    var doubleValue: Double? { if case .number(let v) = self { return v }; return nil }
    var intValue: Int? { if case .number(let v) = self { return Int(v) }; return nil }
    var boolValue: Bool? { if case .bool(let v) = self { return v }; return nil }
    var arrayValue: [NKJSON]? { if case .array(let v) = self { return v }; return nil }
    var objectValue: [String: NKJSON]? { if case .object(let v) = self { return v }; return nil }

    subscript(key: String) -> NKJSON? { objectValue?[key] }

    /// 该对象里的键(按字典序,**确定性** —— 界面上逐项列出时顺序不能每次刷新都跳)。
    var sortedKeys: [String] { (objectValue ?? [:]).keys.sorted() }

    /// 人读串。标量原样(布尔译「是 / 否」),数组 / 对象走紧凑 JSON。
    /// **纯展示兜底**,⛔ 不参与任何判定。
    var displayText: String {
        switch self {
        case .null: return "—"
        case .bool(let v): return v ? "是" : "否"
        case .number(let v):
            if v == v.rounded() && abs(v) < 1e15 { return String(Int(v)) }
            return String(format: "%.4g", v)
        case .string(let v): return v
        case .array, .object:
            let enc = JSONEncoder()
            enc.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
            guard let data = try? enc.encode(self),
                  let s = String(data: data, encoding: .utf8) else { return "—" }
            return s
        }
    }

    var prettyText: String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(self),
              let string = String(data: data, encoding: .utf8) else { return displayText }
        return string
    }
}

struct K9DirectionTheme: Equatable, Identifiable {
    let name: String
    let reason: String
    var id: String { "\(name)\u{1F}\(reason)" }
}

struct K9DirectionPresentation: Equatable {
    let state: String
    let summary: String
    let themes: [K9DirectionTheme]
    let failureReason: String
}

struct K9ReadableField: Equatable, Identifiable {
    let path: String
    let label: String
    let value: String
    var id: String { path }
}

enum K9Presentation {
    static func direction(_ value: NKJSON) -> K9DirectionPresentation {
        let object = value.objectValue ?? [:]
        let themes = (object["themes"]?.arrayValue ?? []).compactMap { item -> K9DirectionTheme? in
            guard let theme = item.objectValue,
                  let name = theme["name"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
                  let reason = theme["reason"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines),
                  !name.isEmpty, !reason.isEmpty else { return nil }
            return K9DirectionTheme(name: name, reason: reason)
        }
        return K9DirectionPresentation(
            state: object["state"]?.stringValue ?? "",
            summary: object["summary"]?.stringValue ?? "",
            themes: themes,
            failureReason: object["failureReason"]?.stringValue ?? ""
        )
    }

    static func readableFields(_ value: NKJSON) -> [K9ReadableField] {
        flatten(value, path: "")
    }

    private static func flatten(_ value: NKJSON, path: String) -> [K9ReadableField] {
        if let object = value.objectValue {
            return orderedKeys(object).flatMap { key -> [K9ReadableField] in
                let childPath = path.isEmpty ? key : "\(path).\(key)"
                let child = object[key]!
                if child.objectValue != nil || child.arrayValue != nil {
                    return flatten(child, path: childPath)
                }
                return [K9ReadableField(path: childPath, label: label(for: childPath),
                                        value: valueText(child, key: key))]
            }
        }
        if let array = value.arrayValue {
            return array.enumerated().flatMap { index, child in
                flatten(child, path: "\(path)[\(index + 1)]")
            }
        }
        return [K9ReadableField(path: path, label: label(for: path),
                                value: valueText(value, key: path))]
    }

    private static func label(for path: String) -> String {
        let key = path.split(separator: ".").last.map(String.init) ?? path
        let labels = [
            "holdAbove": "守住价",
            "minimumMemberCoverage": "最低成员覆盖",
            "medianReturnAtOrAbove": "行业中位涨幅至少",
            "breadthAtOrAbove": "上涨成员占比至少",
            "relativeBenchmarkReturnAtOrAbove": "相对基准涨幅至少",
            "failBelowMedianReturn": "行业中位涨幅低于即失效",
            "failBelowBreadth": "上涨成员占比低于即失效",
            "failBelowRelativeBenchmarkReturn": "相对基准涨幅低于即失效",
            "relativeIndustryReturnAtOrAbove": "个股相对行业涨幅至少",
            "required": "成立要求",
            "reject": "放弃条件",
        ]
        let base = labels[key] ?? key
        if path.hasPrefix("industry.") { return "行业 · \(base)" }
        if path.hasPrefix("stock.") { return "个股 · \(base)" }
        return base
    }

    private static func orderedKeys(_ object: [String: NKJSON]) -> [String] {
        let order = [
            "holdAbove", "industry", "stock", "minimumMemberCoverage",
            "medianReturnAtOrAbove", "breadthAtOrAbove", "relativeBenchmarkReturnAtOrAbove",
            "relativeIndustryReturnAtOrAbove", "failBelowMedianReturn", "failBelowBreadth",
            "failBelowRelativeBenchmarkReturn", "required", "reject",
        ]
        let ranks = Dictionary(uniqueKeysWithValues: order.enumerated().map { ($1, $0) })
        return object.keys.sorted {
            let lhs = ranks[$0] ?? Int.max
            let rhs = ranks[$1] ?? Int.max
            return lhs == rhs ? $0 < $1 : lhs < rhs
        }
    }

    private static func valueText(_ value: NKJSON, key: String) -> String {
        guard let number = value.doubleValue else { return value.displayText }
        let lower = key.lowercased()
        if lower.contains("return") || lower.contains("coverage") || lower.contains("breadth") {
            return String(format: "%.1f%%", number * 100)
        }
        return String(format: "%.2f", number)
    }
}

// MARK: - 展示层枚举换算(服务端只存英文码,中文一律在客户端换算)

/// 板块英文码 → 中文展示名(唯一展示层换算源;未识别值原样透传,不静默瞎翻译)。
func nkBoardLabel(_ raw: String) -> String {
    switch raw {
    case "MAIN": return "主板"
    case "GEM": return "创业板"
    case "STAR": return "科创板"
    case "BSE": return "北交所"
    default: return raw
    }
}

/// 簇内角色英文码 → 中文展示名(**唯一**展示层换算源,沿 `nkBoardLabel` 先例:
/// 服务端只发英文码、中文在客户端换算、未识别值原样透传)。
///
/// `roleDisplay` 不能把服务端原值直接暴露给用户，
/// 而生产实际发的是 `leader` / `core` / `elastic`(源 `neckline/selection/aggregate.py`)
/// —— 界面上直接印英文。⚠ 既有单测**是绿的**,因为 fixture 直接喂了中文「龙头」/「跟随」:
/// **绿的测试没有拦住线上印英文**,所以本函数的用例必须喂英文码。
///
/// 换算表：
/// - `leader` → 龙头 · `core` → 跟随(macOS 原型 369 行 / A-workbench 376 行有据)
/// - `elastic` → **弹性**(用户原话「elastic 就叫弹性」;`neckline/scan/leader.py` 口径 =
///   簇内排除头名后的后一半,「弹性」是三者里最不带褒贬的一个)
/// - `unknown` → **空串 → 整枚徽标不画**(⛔ 绝不画「未知」):服务端注释写死
///   「`unknown` = 算不出,**不是一种角色**」,画出来就是把"没算出来"讲成了一种判断。
///   空串由 `NKChip` 的「空文案整枚不画」规则天然吞掉。
/// - 其余未识别值 → **原样透传**(⛔ 不瞎翻译成中文)
// MARK: - 设置：Provider 配置与按 kind 的推送开关

/// 一个通知 kind 的开关行。`level` 是 `important` 或 `digest`,
/// `label` 是**服务端给的人读名** —— 避免双端各抄一份中文映射(`board` 的反面教训)。
///
/// ⛔ **客户端不许硬编 kind 清单**:权威在服务端 `notify_kinds.py`,硬编一份必然漂移;
/// 新增 kind 时客户端应当**不改代码就能显示出来**。
struct PushKind: Codable, Equatable, Identifiable {
    var kind: String
    var level: String
    var label: String
    var enabled: Bool
    var id: String { kind }
    /// 未识别 `level` 原样透传(**照常显示**,⛔ 不静默丢弃这一行)。
    var levelLabel: String { nkPushLevelLabel(level) }

    enum CodingKeys: String, CodingKey { case kind, level, label, enabled }

    init(kind: String, level: String, label: String, enabled: Bool) {
        self.kind = kind; self.level = level; self.label = label
        self.enabled = enabled
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        level = try c.decodeIfPresent(String.self, forKey: .level) ?? ""
        // `label` 缺席时退回 kind 串本身:**照常显示**好过什么都不显示(E6 同一条纪律)。
        let rawLabel = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        label = rawLabel.isEmpty ? kind : rawLabel
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
    }
}

/// 三级的展示层换算(服务端 `notify_kinds.LEVEL_LABEL` 同一份文案;
/// **未识别值原样透传** —— 服务端日后加第四级时,设置屏照样把它分成独立一组显示)。
func nkPushLevelLabel(_ raw: String) -> String {
    switch raw {
    case "immediate": return "立即"
    case "important": return "重要不紧急"
    case "digest": return "盘后汇总"
    default: return raw.isEmpty ? "未分级" : raw
    }
}

/// V2-⑪ 起 = **按 kind 的开关清单**(不再是 V1 的六个具名布尔字段)。
/// ⚠ `kinds` 顺序 = 服务端 `notify_kinds.ALL_KINDS` 顺序(确定性,客户端照序渲染)。
struct PushSettings: Codable, Equatable {
    var kinds: [PushKind] = []

    enum CodingKeys: String, CodingKey { case kinds }

    init(kinds: [PushKind] = []) { self.kinds = kinds }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kinds = try c.decodeIfPresent([PushKind].self, forKey: .kinds) ?? []
    }

    /// 按 `level` 分组(**服务端出现过的每一个 level 都成一组**,含未识别的;
    /// 组内保持服务端顺序)。⛔ 不按硬编的三级过滤 —— 那等于把未知 level 静默丢掉。
    var groupedByLevel: [(level: String, kinds: [PushKind])] {
        var order: [String] = []
        var bucket: [String: [PushKind]] = [:]
        for k in kinds {
            if bucket[k.level] == nil { order.append(k.level) }
            bucket[k.level, default: []].append(k)
        }
        return order.map { (level: $0, kinds: bucket[$0] ?? []) }
    }

    /// 全量覆盖式写请求体的载荷(`{kind: enabled}`)—— 服务端要求**给全**每一个已登记
    /// kind(缺键 → 422),承 V1「六字段均必填,防漏传静默重置某开关」的同一条纪律。
    var enabledMap: [String: Bool] {
        Dictionary(kinds.map { ($0.kind, $0.enabled) }, uniquingKeysWith: { _, b in b })
    }
}

/// `GET /settings` 内嵌的精简 Provider 视图(比 `GET /settings/providers` 少
/// `baseUrl`/`searchEngine`/`notes`,只给设置屏首屏摘要够用的五个字段)。
struct SettingsProvider: Codable, Equatable, Identifiable {
    var name: String = ""
    var model: String = ""
    var hasWebSearch: Bool = false
    /// **只回布尔,绝不回 key 明文**(§3.4 高危区)。
    var keySet: Bool = false
    var enabled: Bool = true

    var id: String { name }

    enum CodingKeys: String, CodingKey { case name, model, hasWebSearch, keySet, enabled }

    init(name: String = "", model: String = "", hasWebSearch: Bool = false,
         keySet: Bool = false, enabled: Bool = true) {
        self.name = name; self.model = model; self.hasWebSearch = hasWebSearch
        self.keySet = keySet; self.enabled = enabled
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? ""
        hasWebSearch = try c.decodeIfPresent(Bool.self, forKey: .hasWebSearch) ?? false
        keySet = try c.decodeIfPresent(Bool.self, forKey: .keySet) ?? false
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
    }
}

/// LLM Provider 完整安全视图(`GET /settings/providers`)。**绝不含 `apiKey`**,
/// 只回 `keySet` 布尔 —— 写入是单向的:key 只发出去,永不回显。
struct Provider: Codable, Equatable, Identifiable {
    var name: String = ""
    var baseUrl: String = ""
    var model: String = ""
    var hasWebSearch: Bool = false
    var searchEngine: String? = nil
    var notes: String? = nil
    var enabled: Bool = true
    var keySet: Bool = false

    var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, baseUrl, model, hasWebSearch, searchEngine, notes, enabled, keySet
    }

    init(name: String = "", baseUrl: String = "", model: String = "", hasWebSearch: Bool = false,
         searchEngine: String? = nil, notes: String? = nil, enabled: Bool = true,
         keySet: Bool = false) {
        self.name = name; self.baseUrl = baseUrl; self.model = model
        self.hasWebSearch = hasWebSearch; self.searchEngine = searchEngine; self.notes = notes
        self.enabled = enabled; self.keySet = keySet
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        baseUrl = try c.decodeIfPresent(String.self, forKey: .baseUrl) ?? ""
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? ""
        hasWebSearch = try c.decodeIfPresent(Bool.self, forKey: .hasWebSearch) ?? false
        searchEngine = try c.decodeIfPresent(String.self, forKey: .searchEngine)
        notes = try c.decodeIfPresent(String.self, forKey: .notes)
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        keySet = try c.decodeIfPresent(Bool.self, forKey: .keySet) ?? false
    }
}

/// 任务 → provider 路由表(`GET|PUT /settings/llm-routes`)。
/// `routes` 的键须落在服务端 `llm/router.ALL_TASKS`,否则 422 `invalid_task`。
struct LLMRoutes: Codable, Equatable {
    var routes: [String: String] = [:]
    var defaultProvider: String? = nil

    enum CodingKeys: String, CodingKey { case routes, defaultProvider }

    init(routes: [String: String] = [:], defaultProvider: String? = nil) {
        self.routes = routes; self.defaultProvider = defaultProvider
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        routes = try c.decodeIfPresent([String: String].self, forKey: .routes) ?? [:]
        defaultProvider = try c.decodeIfPresent(String.self, forKey: .defaultProvider)
    }
}

/// 独立联网检索设置。API key 只写不回显，客户端只接收是否已配置。
struct TavilySettings: Codable, Equatable {
    var keySet: Bool = false
}

struct SettingsSnapshot: Codable, Equatable {
    var providers: [SettingsProvider] = []
    var routes: [String: String] = [:]
    var tavily: TavilySettings = TavilySettings()
    var push: PushSettings = PushSettings()
    var reviewColMap: [String: String] = [:]     // 4D 周复盘交割单列映射

    static let empty = SettingsSnapshot()

    enum CodingKeys: String, CodingKey { case providers, routes, tavily, push, reviewColMap }

    init(providers: [SettingsProvider] = [], routes: [String: String] = [:],
         tavily: TavilySettings = TavilySettings(), push: PushSettings = PushSettings(),
         reviewColMap: [String: String] = [:]) {
        self.providers = providers; self.routes = routes
        self.tavily = tavily
        self.push = push; self.reviewColMap = reviewColMap
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        providers = try c.decodeIfPresent([SettingsProvider].self, forKey: .providers) ?? []
        routes = try c.decodeIfPresent([String: String].self, forKey: .routes) ?? [:]
        tavily = try c.decodeIfPresent(TavilySettings.self, forKey: .tavily) ?? TavilySettings()
        push = try c.decodeIfPresent(PushSettings.self, forKey: .push) ?? PushSettings()
        reviewColMap = try c.decodeIfPresent([String: String].self, forKey: .reviewColMap) ?? [:]
    }
}

/// 高级诊断的去敏用量账；只展示真实回传的 Token/credits，不含密钥或请求材料。
struct UsageTaskSummary: Codable, Equatable, Identifiable {
    var task: String = ""
    var calls: Int = 0
    var failed: Int = 0
    var usageUnavailable: Int = 0
    var totalTokens: Int? = nil
    var tavilyCredits: Int? = nil
    var id: String { task }
}

struct UsageDaySummary: Codable, Equatable, Identifiable {
    var date: String = ""
    var tasks: [UsageTaskSummary] = []
    var id: String { date }
}

struct UsageSummary: Codable, Equatable {
    var days: [UsageDaySummary] = []
    static let empty = UsageSummary()
}

// MARK: - 展示用轴向着色(沿用 LinoN `AxisTone` 概念,四值穷举)
//
//  刻意只留纯枚举(不 import SwiftUI),保持 DTO 层是纯 Foundation 数据层、
//  可脱离 UI 单测。真正的颜色映射在 `Components/SharedUI.swift`(那里把
//  `NKAxisTone` 映射到 `NK.up/.down/.amber/.textSecondary`)。

enum NKAxisTone: Equatable {
    case good, warn, bad, neutral
    /// 交互蓝(`NK.accent`)。**不是一种判定**,只给"计数 / 版本"这类中性徽标用。
    /// ⛔ 别拿它标结论 —— 判定只有 good / warn / bad 三档。
    case info
}
