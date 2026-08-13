//
//  SharedModels.swift
//  Neckline — 客户端展示层数据模型 · 共享小件:通用 JSON 载体 / 展示层枚举换算 /
//  情绪与板块快照 / 信息卡摘要 / 设置(Provider 与推送开关)/ 自然语言临时提醒 /
//  展示用轴向着色。**下面这段文件头是原 `Models.swift` 的,整份契约共用,原样保留。**
//
//  对齐后端 `neckline/api/schemas.py`(4A 契约,§五 阶段4C「逐字段对齐,别猜」)。
//  出参 camelCase 直接 Codable 解码(后端 pydantic 模型字段本身就是 camelCase,
//  默认 keyDecodingStrategy 不做任何转换);sentiment/sectors 两个内层快照是后端
//  `Dict[str,Any]` 透传(领域 dataclass `asdict()` 结果,字段 snake_case),用显式
//  CodingKeys 映射,不依赖 `.convertFromSnakeCase` 的隐式规则(数字开头分段行为
//  不透明,显式映射更稳)。
//
//  领域四条铁律(§2.1/§2.5,客户端只展示、不重算、不越权):
//   · 止损线 -5% 由服务端算好随 Position.stopLine 下发,客户端不再自派生。
//   · 问询台裁决只有两个字面值,枚举穷举、UI 不存在第三态或"买"按钮。
//   · 持仓开/清仓是审计台账动作(用户已在券商真实操作后来补录),客户端从不代下单、
//     不因退潮刹车而硬拦这个记录动作(阻拦=帮用户瞒报真实操作),只做醒目警示。
//   · 板块分类 / 涨跌停等领域计算全在服务端,客户端不重复实现。
//
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

// MARK: - 4A.2 报告:情绪仪表盘快照(SentimentDashboard,snake_case 透传)

struct SentimentSnapshot: Codable, Equatable {
    var tradeDate: String
    var limitUpCount: Int
    var limitDownCount: Int
    var zabanCount: Int
    var zabanRate: Double
    var maxConsecLimitUp: Int
    /// nil = 昨日无涨停股或数据缺失,非"溢价为0"(后端 docstring 原话,展示须区分)。
    var prevLimitUpPremiumAvg: Double?
    var prevLimitUpSample: Int
    var positionQuota: String       // "满额" | "半额" | "休息"
    var quotaReason: String

    enum CodingKeys: String, CodingKey {
        case tradeDate = "trade_date"
        case limitUpCount = "limit_up_count"
        case limitDownCount = "limit_down_count"
        case zabanCount = "zaban_count"
        case zabanRate = "zaban_rate"
        case maxConsecLimitUp = "max_consec_limit_up"
        case prevLimitUpPremiumAvg = "prev_limit_up_premium_avg"
        case prevLimitUpSample = "prev_limit_up_sample"
        case positionQuota = "position_quota"
        case quotaReason = "quota_reason"
    }
}

/// 仓位额度三态(唯一事实源 = 后端 `report/sentiment.py` 字面量,客户端只做穷举匹配作展示,
/// 不重新推导阈值)。未识别的字符串归 `.unknown`(前向兼容,不崩)。
enum PositionQuota: Equatable {
    case full, half, rest, unknown(String)

    init(_ raw: String) {
        switch raw {
        case "满额": self = .full
        case "半额": self = .half
        case "休息": self = .rest
        default: self = .unknown(raw)
        }
    }

    var label: String {
        switch self {
        case .full: return "满额"
        case .half: return "半额"
        case .rest: return "休息"
        case .unknown(let s): return s
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .full: return .good
        case .half: return .warn
        case .rest: return .bad
        case .unknown: return .neutral
        }
    }
}

// MARK: - 4A.2 报告:强势板块快照(SectorScore,snake_case 透传)

struct SectorSnapshot: Codable, Equatable, Identifiable {
    var indexCode: String
    var name: String
    var boardAge: Int
    var ret20d: Double
    var bonus: Double
    var rank: Int

    var id: String { indexCode }

    enum CodingKeys: String, CodingKey {
        case indexCode = "index_code"
        case name
        case boardAge = "board_age"
        case ret20d = "ret_20d"
        case bonus
        case rank
    }
}

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
/// 🔴 **V2.3.1 §〇c 硬伤 2**:V2.3.0 之前三处 `roleDisplay` 把服务端原值**原样返回**,
/// 而生产实际发的是 `leader` / `core` / `elastic`(源 `neckline/selection/aggregate.py`)
/// —— 界面上直接印英文。⚠ 既有单测**是绿的**,因为 fixture 直接喂了中文「龙头」/「跟随」:
/// **绿的测试没有拦住线上印英文**,所以本函数的用例必须喂英文码。
///
/// 换算表(V2.3.1 §⑪,用户 2026-08-10 拍板,⛔ 不得重开):
/// - `leader` → 龙头 · `core` → 跟随(macOS 原型 369 行 / A-workbench 376 行有据)
/// - `elastic` → **弹性**(用户原话「elastic 就叫弹性」;`neckline/scan/leader.py` 口径 =
///   簇内排除头名后的后一半,「弹性」是三者里最不带褒贬的一个)
/// - `unknown` → **空串 → 整枚徽标不画**(⛔ 绝不画「未知」):服务端注释写死
///   「`unknown` = 算不出,**不是一种角色**」,画出来就是把"没算出来"讲成了一种判断。
///   空串由 `NKChip` 的「空文案整枚不画」规则天然吞掉。
/// - 其余未识别值 → **原样透传**(⛔ 不瞎翻译成中文)
func nkRoleLabel(_ raw: String) -> String {
    let v = raw.trimmingCharacters(in: .whitespaces)
    switch v {
    case "leader": return "龙头"
    case "core": return "跟随"
    case "elastic": return "弹性"
    case "unknown": return ""
    default: return v
    }
}

/// 单个角色码 → 中文;换算不出(`unknown` / 缺键)时给 `—`。
/// 用于「两说并存」那种**必须并排摆出两个值**的场合(整枚不画会让两说变一说)。
func nkRoleLabelOrDash(_ raw: String?) -> String {
    let s = nkRoleLabel(raw ?? "")
    return s.isEmpty ? "—" : s
}

/// 角色两说的展示串(**三处 `roleDisplay` 共用同一份实现**,⛔ 别再抄第四份)。
///
/// **冲突时两个都出现**(⛔ 不挑一个当正确答案),不冲突时只出一个。
/// ⚠ 「换算成空」与「原值就为空」是**两回事**,判定刻意分开:
/// - 原值是 `unknown`(算不出)→ 返回空串 → 整枚徽标不画;
/// - 两个键**原值都没有**(老卡缺这两个键是常态)→ 沿用既有的「角色未判定」。
func nkRoleDisplay(roleLlm: String?, roleMech: String?, roleConflict: Bool) -> String {
    let rawLlm = (roleLlm ?? "").trimmingCharacters(in: .whitespaces)
    let rawMech = (roleMech ?? "").trimmingCharacters(in: .whitespaces)
    let llm = nkRoleLabel(rawLlm)
    let mech = nkRoleLabel(rawMech)
    if roleConflict {
        return "LLM:\(llm.isEmpty ? "—" : llm) / 机械:\(mech.isEmpty ? "—" : mech)"
    }
    if !mech.isEmpty { return mech }
    if !llm.isEmpty { return llm }
    if !rawMech.isEmpty || !rawLlm.isEmpty { return "" }
    return "角色未判定"
}

/*
 ⚠ **V2-⑮:候选族 Swift 类型整族退役**(⑭-C 对拍表 §六 A1 / 6.4-D1)。
   · `EntrySpec` —— 服务端 `report/candidates.py` 已随 ⑬-1 单票候选管线整链删除;
   · `IntelRank` / `LLMJudgment` / `InfoCardSummary` / `Candidate` —— `ReportOut.candidates`
     键已由 ⑭-B **删除**,换 `basketDaily`(篮子日报三段)。
   `Candidate.buyPoint`/`stop`/`target`/`invalidation` 曾是 `try c.decode` **硬解码**,
   服务端不发就整份报告解不出 —— 这次双端同批换血(D2=A 路:老 App 打老机、新 App 打
   新机,两者不交叉),故可直接删。⚠ 「先客户端可选解码、下版服务端才删键」这条两步
   淘汰纪律**本身仍然有效**(CLAUDE.md 铁律),V2 只是靠换机窗口绕开了它一次。
*/

/// K4 红黄牌分区展示层换算(v1.4-④,`InfoCardK4Flag.section`)——
/// hard_cut=红牌(会拦出候选池)、avoid_flag=黄牌(打标保留,只提醒)。
/// 沿 `nkBoardLabel` 先例,未识别值原样透传,不静默瞎翻译。
func nkK4SectionLabel(_ raw: String) -> String {
    switch raw {
    case "hard_cut": return "红牌"
    case "avoid_flag": return "黄牌"
    default: return raw
    }
}

func nkK4SectionTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "hard_cut": return .bad
    case "avoid_flag": return .warn
    default: return .neutral
    }
}

/// V2.2-③-C / ③-C2 位置关 / 核心关判定三态展示层换算(裁定 #11/#12:机械层只出
/// 读数,判定交 LLM)。`ok` = 按它那个角色的判据站得住(**V2.4.0 P1.2 起核心关是
/// 角色感知的,⛔ 不再是"所有角色都得是龙头"**);`weak` = 勉强、降一档;
/// `unfit` = 有明确反证 → 🔴 **V2.4.0 P1.4:只把这一只成员移出篮子** + 单独列进
/// ③b 的 OUT 清单,**篮子仍在**(⛔ 不是硬否决、⛔ 也不再连坐整篮;
/// 理由见 `positionReason`/`coreReason`)。
/// 位置关与核心关**同构**(裁定 #12 与 #11 同款处理),共用同一套三态换算。
/// 沿 `nkBoardLabel` 先例:服务端只发英文码,未识别值原样透传、不静默瞎翻译。
func nkVerdictLabel(_ raw: String) -> String {
    switch raw {
    case "ok": return "合适"
    case "weak": return "勉强"
    case "unfit": return "不合适"
    default: return raw
    }
}

func nkVerdictTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "ok": return .good
    case "weak": return .warn
    case "unfit": return .bad
    default: return .neutral
    }
}

/// 一组三值里**最差**的那个(`unfit` > `weak` > `ok`)。空 → nil。
/// ⚠ 未识别码**不参与比较**(⛔ 不猜它有多严重),全是未识别码时返回第一个原样。
func nkWorstVerdict(_ raws: [String]?) -> String? {
    guard let raws, !raws.isEmpty else { return nil }
    let rank = ["ok": 0, "weak": 1, "unfit": 2]
    let known = raws.filter { rank[$0] != nil }
    guard !known.isEmpty else { return raws.first }
    return known.max { (rank[$0] ?? 0) < (rank[$1] ?? 0) }
}

// MARK: - v1.4-④ 信息卡摘要(挂 `Candidate.infoCard`,不含 60 日序列,§五 v1.4-④-B)
//
// 服务端重构后恒是完整对象(pydantic 默认值兜底 + 全量序列化,§五-④-C「数据不可得
// 如实缺省」由服务端保证);这里用普通 `Codable`(不写容错 init),同 `K4Advisory`/
// `RetraceState` 等 v1.2+ 新增类型的先例——旧报告快照缺整个 `infoCard` 键时,由
// `Candidate.init(from:)` 的 `decodeIfPresent` 兜成 `nil`,不深入本类型内部兜底。

struct InfoCardSnapshot: Codable, Equatable {
    var volRatio5: Double? = nil
    var turnoverRate: Double? = nil
    var industryRank: Int? = nil            // ② 行业强度当日排名(1=最强);nil=未参与排名,不当 0
    /// ② 行业强度持续天数。**`nil` ≠ 0**(v1.4-⑩-E):`nil` = 行业强度表当日无数据
    /// (「没看」);`0` = 评了、不是强度日(「看了,没有」)。UI 据此显示「不可用」而非「0 天」。
    var industryPersistDays: Int? = nil
    var aboveMa250: Bool? = nil             // ma250 未就绪(<250 交易日历史)→ nil,不当"年线下"
    var distFromMa250Pct: Double? = nil     // 小数(非百分数),如 0.05 = 高于年线 5%
    var distFromHigh20dPct: Double? = nil
    var consecLimitUpDays: Int = 0
}

struct InfoCardNewsItem: Codable, Equatable, Identifiable {
    var category: String    // REDUCTION | INVESTIGATION | BLOWUP | REGULATORY(同 NewsAlert.category)
    var summary: String
    var source: String

    var id: String { "\(category)|\(summary)" }
    var categoryLabel: String { nkNewsCategoryLabel(category) }
}

/// 消息面摘要。「没扫到」(不在扫描域)与「扫了没有」必须能区分(同 `NewsAlertScanStatus`
/// 一贯原则)——`scanned=false` 时 `unavailableReason` 必有值,`items` 恒空数组不代表
/// "确认无消息"。
struct InfoCardNews: Codable, Equatable {
    var scanned: Bool
    var items: [InfoCardNewsItem] = []
    var unavailableReason: String? = nil
}

/// 龙虎榜摘要。`lookbackDaysCovered`(近 5 个交易日里本地已落盘、真能判定的天数,≤5)
/// 诚实反映"查了几天"——**不为凑齐而回补历史**,`lookbackDaysCovered<5` 不代表"其余
/// 天数确认未上榜",只代表"没查到那几天"。
struct InfoCardTopList: Codable, Equatable {
    var onListToday: Bool = false
    var reason: String? = nil
    var netAmount: Double? = nil
    var netRate: Double? = nil
    var lookbackDaysCovered: Int = 0
    var lookbackHitDays: Int = 0
}

// ⚠ V2.1-① 起「问询台」一族类型(`ChatRole`/`ChatMessage`/`InquiryVerdict`/
// `InquiryResult`/`InquiryLogEntry`,原 "MARK: - 4A.5 问询台" +
// "MARK: - v1.4-⑦-B 问询记录档案" 两节)已随问询台整链退役删除——见
// `tests/test_v21_retirement_guard.py::test_inquiry_desk_is_gone`。

// MARK: - 4A.5 设置(V2-②/⑪ 换血:Provider 自填制 + 按 kind 的推送开关)
//
// ⚠ **`LLMProviderKind`(`glm`/`kimi` 二值枚举)已退役**:V2-② 起 Provider 是**自填制**
// (任意 OpenAI 兼容端点可配),枚举写死两家本身就是那个要被替换掉的东西。
// ⚠ **`PushSettings` 六个具名 bool 已退役**:V2-⑪ 起 = **按 kind 的开关清单**。

/// 一个通知 kind 的开关行。`level` 是三级之一(`immediate`/`important`/`digest`),
/// `label` 是**服务端给的人读名** —— 避免双端各抄一份中文映射(`board` 的反面教训)。
///
/// ⛔ **客户端不许硬编 kind 清单**:权威在服务端 `notify_kinds.py`,硬编一份必然漂移;
/// 新增 kind 时客户端应当**不改代码就能显示出来**。
struct PushKind: Codable, Equatable, Identifiable {
    var kind: String
    var level: String
    var label: String
    var enabled: Bool
    /// **V2.4.0 P0:退役位,唯一源在服务端** `notify_kinds.RETIRED_KINDS`(随 `/settings` 下发)。
    /// `true` = 该 kind 已退役、服务端永不再发这类推送 → **设置屏隐藏这一行开关**。
    /// 🔴 ⛔ **客户端不许硬编码一份退役 kind 黑名单** —— 那是第二份事实源(§3.14-B)。
    /// ⚠ **隐藏只发生在渲染层**:这一行仍留在 `pushKindsDraft` 里,`PUT /settings/push`
    /// 照旧把它一起发回去(服务端要求给全 `ALL_KINDS`,少一个键就 422)。
    /// ⚠ 老服务端不发这个键 → `decodeIfPresent` 兜底 `false` = 「没退役」,与旧行为一致。
    var retired: Bool

    var id: String { kind }
    /// 未识别 `level` 原样透传(**照常显示**,⛔ 不静默丢弃这一行)。
    var levelLabel: String { nkPushLevelLabel(level) }

    enum CodingKeys: String, CodingKey { case kind, level, label, enabled, retired }

    init(kind: String, level: String, label: String, enabled: Bool, retired: Bool = false) {
        self.kind = kind; self.level = level; self.label = label
        self.enabled = enabled; self.retired = retired
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        level = try c.decodeIfPresent(String.self, forKey: .level) ?? ""
        // `label` 缺席时退回 kind 串本身:**照常显示**好过什么都不显示(E6 同一条纪律)。
        let rawLabel = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        label = rawLabel.isEmpty ? kind : rawLabel
        enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        retired = try c.decodeIfPresent(Bool.self, forKey: .retired) ?? false
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
    /// 按 level 分组的**可见清单**。
    ///
    /// 🔴 **V2.4.0 P0:`retired == true` 的行在这里被过滤掉** —— 服务端已退役该 kind、
    /// 永不再发这类推送,留一个开关只会让用户以为"关了就不会收到、开着就会收到"。
    /// ⚠ **只在渲染层过滤**:`kinds`(= `pushKindsDraft`)里那一行**照旧留着**,
    /// `enabledMap` 仍把它一起发回服务端(`PUT /settings/push` 要求给全 `ALL_KINDS`,
    /// 少一个键就 422)。⛔ 别顺手把它从 `kinds` 里删掉。
    /// 🔴 判据来自服务端下发的 `retired` 位,⛔ **不是客户端硬编码的 kind 黑名单**。
    var groupedByLevel: [(level: String, kinds: [PushKind])] {
        var order: [String] = []
        var bucket: [String: [PushKind]] = [:]
        for k in kinds where !k.retired {
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

struct SettingsSnapshot: Codable, Equatable {
    var providers: [SettingsProvider] = []
    var routes: [String: String] = [:]
    var push: PushSettings = PushSettings()
    var reviewColMap: [String: String] = [:]     // 4D 周复盘交割单列映射

    static let empty = SettingsSnapshot()

    enum CodingKeys: String, CodingKey { case providers, routes, push, reviewColMap }

    init(providers: [SettingsProvider] = [], routes: [String: String] = [:],
         push: PushSettings = PushSettings(), reviewColMap: [String: String] = [:]) {
        self.providers = providers; self.routes = routes
        self.push = push; self.reviewColMap = reviewColMap
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        providers = try c.decodeIfPresent([SettingsProvider].self, forKey: .providers) ?? []
        routes = try c.decodeIfPresent([String: String].self, forKey: .routes) ?? [:]
        push = try c.decodeIfPresent(PushSettings.self, forKey: .push) ?? PushSettings()
        reviewColMap = try c.decodeIfPresent([String: String].self, forKey: .reviewColMap) ?? [:]
    }
}

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2-⑪-C 自然语言临时提醒(`custom_alerts`)—— **只通知,永不交易**
// ══════════════════════════════════════════════════════════════════════════

/// 一条提醒规则的单个条件(白名单在服务端领域层卡死,客户端只搬运)。
struct AlertCondition: Codable, Equatable, Identifiable {
    var metric: String = ""
    var op: String = ""
    var value: Double = 0
    var ref: String? = nil            // 仅 index_chg_pct 需要
    var refBasketId: Int? = nil       // 仅 basket_weak_ratio 可选

    var id: String { "\(metric)|\(op)|\(value)|\(ref ?? "")|\(refBasketId.map(String.init) ?? "")" }

    enum CodingKeys: String, CodingKey { case metric, op, value, ref, refBasketId }

    init(metric: String = "", op: String = "", value: Double = 0,
         ref: String? = nil, refBasketId: Int? = nil) {
        self.metric = metric; self.op = op; self.value = value
        self.ref = ref; self.refBasketId = refBasketId
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        metric = try c.decodeIfPresent(String.self, forKey: .metric) ?? ""
        op = try c.decodeIfPresent(String.self, forKey: .op) ?? ""
        value = try c.decodeIfPresent(Double.self, forKey: .value) ?? 0
        ref = try c.decodeIfPresent(String.self, forKey: .ref)
        refBasketId = try c.decodeIfPresent(Int.self, forKey: .refBasketId)
    }
}

/// `POST /alerts` 的请求体(= LLM 解析结果里的 `draft`,用户点「确认」时**原样回传**)。
/// 手填表单走的也是这个入口 —— LLM 解析只是把这些字段先替用户填好,落库路径只有一条。
struct AlertDraft: Codable, Equatable {
    var tsCode: String? = nil          // nil = 大盘级
    var nlText: String = ""
    var conditions: [AlertCondition] = []
    var logic: String = "all"
    var activeFrom: String? = nil
    var activeTo: String? = nil
    var expiresAt: String? = nil
    var persist: Bool = false
    var cooldownSeconds: Int = 0
    var maxFires: Int = 1

    enum CodingKeys: String, CodingKey {
        case tsCode, nlText, conditions, logic, activeFrom, activeTo, expiresAt
        case persist, cooldownSeconds, maxFires
    }

    init(tsCode: String? = nil, nlText: String = "", conditions: [AlertCondition] = [],
         logic: String = "all", activeFrom: String? = nil, activeTo: String? = nil,
         expiresAt: String? = nil, persist: Bool = false, cooldownSeconds: Int = 0,
         maxFires: Int = 1) {
        self.tsCode = tsCode; self.nlText = nlText; self.conditions = conditions
        self.logic = logic; self.activeFrom = activeFrom; self.activeTo = activeTo
        self.expiresAt = expiresAt; self.persist = persist
        self.cooldownSeconds = cooldownSeconds; self.maxFires = maxFires
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode)
        nlText = try c.decodeIfPresent(String.self, forKey: .nlText) ?? ""
        conditions = try c.decodeIfPresent([AlertCondition].self, forKey: .conditions) ?? []
        logic = try c.decodeIfPresent(String.self, forKey: .logic) ?? "all"
        activeFrom = try c.decodeIfPresent(String.self, forKey: .activeFrom)
        activeTo = try c.decodeIfPresent(String.self, forKey: .activeTo)
        expiresAt = try c.decodeIfPresent(String.self, forKey: .expiresAt)
        persist = try c.decodeIfPresent(Bool.self, forKey: .persist) ?? false
        cooldownSeconds = try c.decodeIfPresent(Int.self, forKey: .cooldownSeconds) ?? 0
        maxFires = try c.decodeIfPresent(Int.self, forKey: .maxFires) ?? 1
    }
}

/// ⑪-C 的**七项确认卡**。后两项(`quoteDelayDisclosure` / `noAutoTrade`)是固定文案、
/// **恒出现**(蓝图 5.6 安全要求)—— ⛔ 客户端不得隐藏任何一项:用户是在这张卡上同意
/// 「行情有延迟」「只通知不交易」的。
struct ConfirmationCard: Codable, Equatable {
    var subject: String = ""                 // ① 标的
    var condition: String = ""               // ② 触发条件与方向
    var activeWindow: String = ""            // ③ 生效时间
    var notifyLimit: String = ""             // ④ 通知次数 / 冷却
    var expiry: String = ""                  // ⑤ 到期时间
    var quoteDelayDisclosure: String = ""    // ⑥ 行情延迟 / 数据中断披露(必选)
    var noAutoTrade: String = ""             // ⑦ 只通知不自动交易
    var rule: NKJSON = .object([:])

    enum CodingKeys: String, CodingKey {
        case subject, condition, activeWindow, notifyLimit, expiry
        case quoteDelayDisclosure, noAutoTrade, rule
    }

    init(subject: String = "", condition: String = "", activeWindow: String = "",
         notifyLimit: String = "", expiry: String = "", quoteDelayDisclosure: String = "",
         noAutoTrade: String = "", rule: NKJSON = .object([:])) {
        self.subject = subject; self.condition = condition; self.activeWindow = activeWindow
        self.notifyLimit = notifyLimit; self.expiry = expiry
        self.quoteDelayDisclosure = quoteDelayDisclosure; self.noAutoTrade = noAutoTrade
        self.rule = rule
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        subject = try c.decodeIfPresent(String.self, forKey: .subject) ?? ""
        condition = try c.decodeIfPresent(String.self, forKey: .condition) ?? ""
        activeWindow = try c.decodeIfPresent(String.self, forKey: .activeWindow) ?? ""
        notifyLimit = try c.decodeIfPresent(String.self, forKey: .notifyLimit) ?? ""
        expiry = try c.decodeIfPresent(String.self, forKey: .expiry) ?? ""
        quoteDelayDisclosure = try c.decodeIfPresent(String.self,
                                                     forKey: .quoteDelayDisclosure) ?? ""
        noAutoTrade = try c.decodeIfPresent(String.self, forKey: .noAutoTrade) ?? ""
        rule = try c.decodeIfPresent(NKJSON.self, forKey: .rule) ?? .object([:])
    }

    /// 七项**逐项**(标题 + 正文),供 UI 逐行渲染 —— ⛔ 不许为界面清爽省掉任何一项。
    var rows: [(title: String, text: String)] {
        [("① 标的", subject), ("② 触发条件与方向", condition), ("③ 生效时间", activeWindow),
         ("④ 通知次数 / 冷却", notifyLimit), ("⑤ 到期时间", expiry),
         ("⑥ 行情延迟 / 数据中断", quoteDelayDisclosure), ("⑦ 只通知不自动交易", noAutoTrade)]
    }
}

/// 一条已落库的临时提醒。`status` 是**库里那一列**;`expiredNow` 是「按此刻算实际上还
/// 生不生效」—— 读路径不写库,两者可能短暂不一致,**分开给、不合并**。
struct CustomAlert: Codable, Equatable, Identifiable {
    var id: Int = 0
    var tsCode: String? = nil        // nil = 大盘级
    var nlText: String = ""          // 用户原话(留痕;哨兵不看)
    var rule: NKJSON = .object([:])
    var condition: String = ""       // 由结构化规则生成的人读描述
    var activeFrom: String? = nil
    var activeTo: String? = nil
    var expiresAt: String? = nil
    var persist: Bool = false
    var cooldownSeconds: Int = 0
    var maxFires: Int = 1
    var firedCount: Int = 0
    var status: String = "active"    // active | expired | cancelled
    var expiredNow: Bool = false
    var createdAt: String = ""
    var updatedAt: String = ""

    enum CodingKeys: String, CodingKey {
        case id, tsCode, nlText, rule, condition, activeFrom, activeTo, expiresAt
        case persist, cooldownSeconds, maxFires, firedCount, status, expiredNow
        case createdAt, updatedAt
    }

    init(id: Int = 0, tsCode: String? = nil, nlText: String = "", rule: NKJSON = .object([:]),
         condition: String = "", activeFrom: String? = nil, activeTo: String? = nil,
         expiresAt: String? = nil, persist: Bool = false, cooldownSeconds: Int = 0,
         maxFires: Int = 1, firedCount: Int = 0, status: String = "active",
         expiredNow: Bool = false, createdAt: String = "", updatedAt: String = "") {
        self.id = id; self.tsCode = tsCode; self.nlText = nlText; self.rule = rule
        self.condition = condition; self.activeFrom = activeFrom; self.activeTo = activeTo
        self.expiresAt = expiresAt; self.persist = persist
        self.cooldownSeconds = cooldownSeconds; self.maxFires = maxFires
        self.firedCount = firedCount; self.status = status; self.expiredNow = expiredNow
        self.createdAt = createdAt; self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode)
        nlText = try c.decodeIfPresent(String.self, forKey: .nlText) ?? ""
        rule = try c.decodeIfPresent(NKJSON.self, forKey: .rule) ?? .object([:])
        condition = try c.decodeIfPresent(String.self, forKey: .condition) ?? ""
        activeFrom = try c.decodeIfPresent(String.self, forKey: .activeFrom)
        activeTo = try c.decodeIfPresent(String.self, forKey: .activeTo)
        expiresAt = try c.decodeIfPresent(String.self, forKey: .expiresAt)
        persist = try c.decodeIfPresent(Bool.self, forKey: .persist) ?? false
        cooldownSeconds = try c.decodeIfPresent(Int.self, forKey: .cooldownSeconds) ?? 0
        maxFires = try c.decodeIfPresent(Int.self, forKey: .maxFires) ?? 1
        firedCount = try c.decodeIfPresent(Int.self, forKey: .firedCount) ?? 0
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "active"
        expiredNow = try c.decodeIfPresent(Bool.self, forKey: .expiredNow) ?? false
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        updatedAt = try c.decodeIfPresent(String.self, forKey: .updatedAt) ?? ""
    }

    var statusLabel: String {
        switch status {
        case "active": return expiredNow ? "生效中(已过期,待哨兵下一拍收口)" : "生效中"
        case "expired": return "已到期"
        case "cancelled": return "已停用"
        default: return status
        }
    }

    var statusTone: NKAxisTone {
        if status == "active" && !expiredNow { return .good }
        return .neutral
    }

    var subjectLabel: String { tsCode ?? "大盘" }
}

/// NL 解析结果。**永远 200**(交互式接口):失败也把可读原因和降级表单给出去 ——
/// 「LLM 不可用 → 降级为手填结构化表单,**不静默失败**」的契约落点。
struct AlertParseResult: Codable, Equatable {
    var ok: Bool = false
    var action: String = "create"   // create | query | cancel | modify
    var reason: String = "ok"
    var narrative: String = ""      // 模型那句复述(只展示,不进判据)
    var degraded: Bool = false      // true = LLM 不可用,已给手填表单
    var manualForm: NKJSON? = nil
    var confirmationCard: ConfirmationCard? = nil
    var draft: AlertDraft? = nil
    var targetAlertId: Int? = nil
    var matches: [CustomAlert] = []

    enum CodingKeys: String, CodingKey {
        case ok, action, reason, narrative, degraded, manualForm
        case confirmationCard, draft, targetAlertId, matches
    }

    init(ok: Bool = false, action: String = "create", reason: String = "ok",
         narrative: String = "", degraded: Bool = false, manualForm: NKJSON? = nil,
         confirmationCard: ConfirmationCard? = nil, draft: AlertDraft? = nil,
         targetAlertId: Int? = nil, matches: [CustomAlert] = []) {
        self.ok = ok; self.action = action; self.reason = reason; self.narrative = narrative
        self.degraded = degraded; self.manualForm = manualForm
        self.confirmationCard = confirmationCard; self.draft = draft
        self.targetAlertId = targetAlertId; self.matches = matches
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        action = try c.decodeIfPresent(String.self, forKey: .action) ?? "create"
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? "ok"
        narrative = try c.decodeIfPresent(String.self, forKey: .narrative) ?? ""
        degraded = try c.decodeIfPresent(Bool.self, forKey: .degraded) ?? false
        manualForm = try c.decodeIfPresent(NKJSON.self, forKey: .manualForm)
        confirmationCard = try c.decodeIfPresent(ConfirmationCard.self, forKey: .confirmationCard)
        draft = try c.decodeIfPresent(AlertDraft.self, forKey: .draft)
        targetAlertId = try c.decodeIfPresent(Int.self, forKey: .targetAlertId)
        matches = try c.decodeIfPresent([CustomAlert].self, forKey: .matches) ?? []
    }
}

// MARK: - 展示用轴向着色(沿用 LinoN `AxisTone` 概念,四值穷举)
//
//  刻意只留纯枚举(不 import SwiftUI),保持 Models.swift 是纯 Foundation 数据层、
//  可脱离 UI 单测。真正的颜色映射在 `Components/SharedUI.swift`(那里把
//  `NKAxisTone` 映射到 `NK.up/.down/.amber/.textSecondary`)。

enum NKAxisTone: Equatable {
    case good, warn, bad, neutral
    /// 交互蓝(`NK.accent`)。**不是一种判定**,只给"计数 / 版本"这类中性徽标用
    /// (原型工具栏 39 行的篮子计数 `color:#0B6BCB; background:rgba(11,107,203,.12)`)。
    /// ⛔ 别拿它标结论 —— 判定只有 good / warn / bad 三档。
    case info
}
