//
//  Models.swift
//  Neckline — 客户端展示层数据模型
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
/// 读数,判定交 LLM,只降级不除名)。`ok` = 判定合适 / 是那一群的龙头;
/// `weak` = 勉强、降一档;`unfit` = 不合适 / 不是龙头、退出正式候选(**⛔ 不是
/// 硬否决**——票仍在 ③b 列名,理由见 `positionReason`/`coreReason`)。
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

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2 篮子族(⑭-B 契约,⑮ 客户端落地)
// ══════════════════════════════════════════════════════════════════════════
//
// **两类 DTO,决定解码怎么写**(CLAUDE.md「落库快照两类论」/ 对拍表 §四.1):
//   · **A 类:每次响应重新拼装**(`Basket` / `Tier` / `BasketVerification` /
//     `DroppedBasket` / `PositionPlan` / `Profile` / `Pack` / `EvalWeekly`)——服务端用
//     pydantic 默认值重构,新字段旧数据也会补全。
//   · **B 类:写入当时冻住的历史快照**(`BasketCard` ← `basket_cards.card_json`;
//     `BasketReview.mech` ← `basket_review_daily.mech_json`)——服务端升级**永远不会**
//     给老快照补新键。⛔ 用合成 `Codable` 的后果:装了新 App 的用户翻几周前的老卡 →
//     **整张卡解不出**。
// 本文件对**两类一律手写 `init(from:)` + `decodeIfPresent`**:B 类是硬要求;A 类这么写
// 是白拿的保险(Swift 合成 Decodable 对非 Optional 属性**不会**因为声明了默认值就容忍
// 缺键 —— 默认值只影响 memberwise init,这条坑本文件其它类型早已踩过)。

/// 篮子卡上的一条证据(`evidence[]`,⑤ 两段式流水的检索产出)。**来源与日期必带**,
/// 缺了就如实留空,⛔ 不替 LLM 补一个看起来像样的出处。
struct BasketEvidence: Codable, Equatable, Identifiable {
    var claim: String = ""
    var source: String = ""
    var date: String = ""
    var url: String = ""

    var id: String { "\(claim)|\(source)|\(date)" }

    enum CodingKeys: String, CodingKey { case claim, source, date, url }

    init(claim: String = "", source: String = "", date: String = "", url: String = "") {
        self.claim = claim; self.source = source; self.date = date; self.url = url
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        claim = try c.decodeIfPresent(String.self, forKey: .claim) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
        date = try c.decodeIfPresent(String.self, forKey: .date) ?? ""
        url = try c.decodeIfPresent(String.self, forKey: .url) ?? ""
    }
}

/// 次日强 / 平 / 弱三剧本。**参考件**(⑦ 十一项之一)—— 展示处必带「参考、非指令」。
struct BasketScripts: Codable, Equatable {
    var strong: String? = nil
    var flat: String? = nil
    var weak: String? = nil

    enum CodingKeys: String, CodingKey { case strong, flat, weak }

    init(strong: String? = nil, flat: String? = nil, weak: String? = nil) {
        self.strong = strong; self.flat = flat; self.weak = weak
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        strong = try c.decodeIfPresent(String.self, forKey: .strong)
        flat = try c.decodeIfPresent(String.self, forKey: .flat)
        weak = try c.decodeIfPresent(String.self, forKey: .weak)
    }

    var isEmpty: Bool {
        [strong, flat, weak].allSatisfy { ($0 ?? "").trimmingCharacters(in: .whitespaces).isEmpty }
    }
}

/// 口径指纹(章程 / 包 / 引擎 / 验证条件集四个版本号 + 两个纪律比例)。
/// ⑨ 的按包归因靠它分层,**不是装饰字段**。
struct BasketFingerprint: Codable, Equatable {
    var stopPct: Double? = nil
    var takeProfitRetrace: Double? = nil
    var charterVersion: String? = nil
    var packVersion: String? = nil
    /// 服务端契约是 int(`neckline/db.py` 三处 `engine_api_version INTEGER`,`selection/
    /// pack.py`/`aggregate.py` 的 pydantic 字段同为 `int`)。2026-08-05 定向快修:此前
    /// 误写成 `String?`,生产恒发数字 `1` → `typeMismatch` 直接拖炸**整份**报告解码
    /// (Mac 实证,iPhone 同代码同炸)。
    var engineApiVersion: Int? = nil
    var verificationRulesetVersion: String? = nil

    enum CodingKeys: String, CodingKey {
        case stopPct, takeProfitRetrace, charterVersion, packVersion
        case engineApiVersion, verificationRulesetVersion
    }

    init(stopPct: Double? = nil, takeProfitRetrace: Double? = nil, charterVersion: String? = nil,
         packVersion: String? = nil, engineApiVersion: Int? = nil,
         verificationRulesetVersion: String? = nil) {
        self.stopPct = stopPct; self.takeProfitRetrace = takeProfitRetrace
        self.charterVersion = charterVersion; self.packVersion = packVersion
        self.engineApiVersion = engineApiVersion
        self.verificationRulesetVersion = verificationRulesetVersion
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        stopPct = try c.decodeIfPresent(Double.self, forKey: .stopPct)
        takeProfitRetrace = try c.decodeIfPresent(Double.self, forKey: .takeProfitRetrace)
        charterVersion = try c.decodeIfPresent(String.self, forKey: .charterVersion)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        // B 类冻结快照字段(`basket_cards.card_json` 写入当时冻住,`INSERT OR IGNORE`
        // 永不覆盖)——容错双态解码:数字优先(现役契约),字符串数字也认(防万一某张
        // 历史卡是字符串形态),都没有 / 都解不出才 nil,⛔ 不许因这一个字段整份报告炸。
        if let n = try? c.decode(Int.self, forKey: .engineApiVersion) {
            engineApiVersion = n
        } else if let s = try? c.decode(String.self, forKey: .engineApiVersion) {
            engineApiVersion = Int(s)
        } else {
            engineApiVersion = nil
        }
        verificationRulesetVersion = try c.decodeIfPresent(String.self,
                                                           forKey: .verificationRulesetVersion)
    }
}

/// 价格区间参考件(`entryZone` = `{low, high, why}`;`exitReference` = `{low, high}`)。
/// ⛔ **`exitReference` 不是止盈线**(§2.8-C 语义红线)——文案里不许这么写:回落止盈
/// 才是纪律,离场参考只是来源篮子卡上的一个参考位。
struct BasketPriceBand: Codable, Equatable {
    var low: Double? = nil
    var high: Double? = nil
    var why: String? = nil

    enum CodingKeys: String, CodingKey { case low, high, why }

    init(low: Double? = nil, high: Double? = nil, why: String? = nil) {
        self.low = low; self.high = high; self.why = why
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        low = try c.decodeIfPresent(Double.self, forKey: .low)
        high = try c.decodeIfPresent(Double.self, forKey: .high)
        why = try c.decodeIfPresent(String.self, forKey: .why)
    }

    /// `nil` = 夹逼闸拒收(值为 null 且原因非空)——⛔ 展示时不许把 nil 画成 0 或空白。
    var rangeText: String? {
        guard let lo = low, let hi = high else { return nil }
        return "¥\(String(format: "%.2f", lo)) ~ ¥\(String(format: "%.2f", hi))"
    }
}

/// ⑦-K7 成员标注件一条(与 `InfoCardOut.tags` 同源同形,`selection/member_tags.py`
/// 唯一实现)。`text` **已含「参考、非指令」后缀,客户端不许改写、不许截断**。
/// **四不硬约束**:不进排序 / 不进哨兵 / 不改去留 / 不加分。
struct BasketMemberTag: Codable, Equatable, Identifiable {
    var code: String = ""
    var label: String = ""
    var tone: String = "neutral"   // neutral | warn
    var text: String = ""
    var source: String = ""

    var id: String { code }
    var axisTone: NKAxisTone { tone == "warn" ? .warn : .neutral }

    enum CodingKeys: String, CodingKey { case code, label, tone, text, source }

    init(code: String = "", label: String = "", tone: String = "neutral",
         text: String = "", source: String = "") {
        self.code = code; self.label = label; self.tone = tone
        self.text = text; self.source = source
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        code = try c.decodeIfPresent(String.self, forKey: .code) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        tone = try c.decodeIfPresent(String.self, forKey: .tone) ?? "neutral"
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source) ?? ""
    }
}

/// 篮子卡上的一名成员(**B 类冻结快照**)。
///
/// **两条展示纪律写死在类型注释里**:
///  1. `roleLlm` / `roleMech` 是**两说并存**的对拍结果 —— `roleConflict=true` 时
///     **两个都显示**,⛔ 不许挑一个当"正确答案"。
///  2. 三个参考件各带 `*Clamp` + `*UnavailableReason`:夹逼闸拒收时值是 `nil` 且原因
///     非空,⛔ 不许把 `nil` 显示成 `0` 或空白了事。
struct BasketMember: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String = ""
    var roleLlm: String? = nil
    var roleMech: String? = nil
    var roleConflict: Bool = false
    var reason: String = ""
    var isPrimary: Bool = false
    var industry: String? = nil
    var industryLift: Double? = nil
    var liftReason: String? = nil
    var primaryReason: String? = nil
    var rsRank: Int? = nil
    var k4Tag: String? = nil
    // —— V2.2-③-C 位置关(裁定 #11:机械层只出读数,判定交 LLM,只降级不除名)——
    // `positionVerdict` ∈ ok(合适)/ weak(勉强,降一档)/ unfit(不合适,退出正式
    // 候选但**仍在 ③b 列名**,⛔ 非硬否决)。`positionMetrics` = 当次喂给模型的
    // 落地起跳读数原样(⛔ 缺项就是真的没取到,不是 0)。**老卡缺这三键是常态**——
    // 纯新增,`landingState` 那个作废的四态枚举从未上过产,零删键。
    var positionVerdict: String? = nil
    var positionReason: String? = nil
    var positionMetrics: NKJSON? = nil
    // —— V2.2-③-C2 核心关(裁定 #12:同构但独立于位置关,问的是"是不是那一群的
    // 龙头")—— `coreMetrics` 一定带分母(`industry_member_count`),⛔ 缺项不是 0。
    var coreVerdict: String? = nil
    var coreReason: String? = nil
    var coreMetrics: NKJSON? = nil
    /// 机械面板原样透传(⑦ `MemberMech.to_dict()`,自由结构)。
    var mech: NKJSON = .object([:])
    var entryZone: BasketPriceBand? = nil
    var entryZoneClamp: String = ""
    var entryZoneUnavailableReason: String? = nil
    var maxChase: Double? = nil
    var maxChaseClamp: String = ""
    var maxChaseUnavailableReason: String? = nil
    var exitReference: BasketPriceBand? = nil
    var exitReferenceClamp: String = ""
    var exitReferenceUnavailableReason: String? = nil
    var tags: [BasketMemberTag] = []
    /// **判不了的标注码** —— 与「判过没命中」是两回事,⛔ 不许合并成"没有标注"。
    var tagsAbsent: [String] = []

    var id: String { tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, roleLlm, roleMech, roleConflict, reason, isPrimary
        case industry, industryLift, liftReason, primaryReason, rsRank, k4Tag
        case positionVerdict, positionReason, positionMetrics
        case coreVerdict, coreReason, coreMetrics
        case mech
        case entryZone, entryZoneClamp, entryZoneUnavailableReason
        case maxChase, maxChaseClamp, maxChaseUnavailableReason
        case exitReference, exitReferenceClamp, exitReferenceUnavailableReason
        case tags, tagsAbsent
    }

    init(tsCode: String = "", name: String = "", roleLlm: String? = nil, roleMech: String? = nil,
         roleConflict: Bool = false, reason: String = "", isPrimary: Bool = false,
         industry: String? = nil, industryLift: Double? = nil, liftReason: String? = nil,
         primaryReason: String? = nil, rsRank: Int? = nil, k4Tag: String? = nil,
         positionVerdict: String? = nil, positionReason: String? = nil,
         positionMetrics: NKJSON? = nil, coreVerdict: String? = nil, coreReason: String? = nil,
         coreMetrics: NKJSON? = nil,
         mech: NKJSON = .object([:]), entryZone: BasketPriceBand? = nil,
         entryZoneClamp: String = "", entryZoneUnavailableReason: String? = nil,
         maxChase: Double? = nil, maxChaseClamp: String = "",
         maxChaseUnavailableReason: String? = nil, exitReference: BasketPriceBand? = nil,
         exitReferenceClamp: String = "", exitReferenceUnavailableReason: String? = nil,
         tags: [BasketMemberTag] = [], tagsAbsent: [String] = []) {
        self.tsCode = tsCode; self.name = name; self.roleLlm = roleLlm; self.roleMech = roleMech
        self.roleConflict = roleConflict; self.reason = reason; self.isPrimary = isPrimary
        self.industry = industry; self.industryLift = industryLift; self.liftReason = liftReason
        self.primaryReason = primaryReason; self.rsRank = rsRank; self.k4Tag = k4Tag
        self.positionVerdict = positionVerdict; self.positionReason = positionReason
        self.positionMetrics = positionMetrics
        self.coreVerdict = coreVerdict; self.coreReason = coreReason; self.coreMetrics = coreMetrics
        self.mech = mech; self.entryZone = entryZone; self.entryZoneClamp = entryZoneClamp
        self.entryZoneUnavailableReason = entryZoneUnavailableReason
        self.maxChase = maxChase; self.maxChaseClamp = maxChaseClamp
        self.maxChaseUnavailableReason = maxChaseUnavailableReason
        self.exitReference = exitReference; self.exitReferenceClamp = exitReferenceClamp
        self.exitReferenceUnavailableReason = exitReferenceUnavailableReason
        self.tags = tags; self.tagsAbsent = tagsAbsent
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        roleLlm = try c.decodeIfPresent(String.self, forKey: .roleLlm)
        roleMech = try c.decodeIfPresent(String.self, forKey: .roleMech)
        roleConflict = try c.decodeIfPresent(Bool.self, forKey: .roleConflict) ?? false
        reason = try c.decodeIfPresent(String.self, forKey: .reason) ?? ""
        isPrimary = try c.decodeIfPresent(Bool.self, forKey: .isPrimary) ?? false
        industry = try c.decodeIfPresent(String.self, forKey: .industry)
        industryLift = try c.decodeIfPresent(Double.self, forKey: .industryLift)
        liftReason = try c.decodeIfPresent(String.self, forKey: .liftReason)
        primaryReason = try c.decodeIfPresent(String.self, forKey: .primaryReason)
        rsRank = try c.decodeIfPresent(Int.self, forKey: .rsRank)
        k4Tag = try c.decodeIfPresent(String.self, forKey: .k4Tag)
        positionVerdict = try c.decodeIfPresent(String.self, forKey: .positionVerdict)
        positionReason = try c.decodeIfPresent(String.self, forKey: .positionReason)
        positionMetrics = try c.decodeIfPresent(NKJSON.self, forKey: .positionMetrics)
        coreVerdict = try c.decodeIfPresent(String.self, forKey: .coreVerdict)
        coreReason = try c.decodeIfPresent(String.self, forKey: .coreReason)
        coreMetrics = try c.decodeIfPresent(NKJSON.self, forKey: .coreMetrics)
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        entryZone = try c.decodeIfPresent(BasketPriceBand.self, forKey: .entryZone)
        entryZoneClamp = try c.decodeIfPresent(String.self, forKey: .entryZoneClamp) ?? ""
        entryZoneUnavailableReason = try c.decodeIfPresent(String.self,
                                                           forKey: .entryZoneUnavailableReason)
        maxChase = try c.decodeIfPresent(Double.self, forKey: .maxChase)
        maxChaseClamp = try c.decodeIfPresent(String.self, forKey: .maxChaseClamp) ?? ""
        maxChaseUnavailableReason = try c.decodeIfPresent(String.self,
                                                          forKey: .maxChaseUnavailableReason)
        exitReference = try c.decodeIfPresent(BasketPriceBand.self, forKey: .exitReference)
        exitReferenceClamp = try c.decodeIfPresent(String.self, forKey: .exitReferenceClamp) ?? ""
        exitReferenceUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .exitReferenceUnavailableReason)
        tags = try c.decodeIfPresent([BasketMemberTag].self, forKey: .tags) ?? []
        tagsAbsent = try c.decodeIfPresent([String].self, forKey: .tagsAbsent) ?? []
    }

    /// 角色两说的展示串。**冲突时两个都出现**(⛔ 不挑一个),不冲突时只出一个。
    var roleDisplay: String {
        let llm = (roleLlm ?? "").trimmingCharacters(in: .whitespaces)
        let mech = (roleMech ?? "").trimmingCharacters(in: .whitespaces)
        if roleConflict {
            return "LLM:\(llm.isEmpty ? "—" : llm) / 机械:\(mech.isEmpty ? "—" : mech)"
        }
        if !mech.isEmpty { return mech }
        if !llm.isEmpty { return llm }
        return "角色未判定"
    }

    // —— V2.2-③-C/③-C2 位置关 / 核心关判定的**纯展示层换算**(沿 `nkBoardLabel` 先例:
    // 服务端只发英文码 ok/weak/unfit,中文与着色在客户端算,⛔ 不要服务端另建映射)。
    // `verdict == nil` 时 label 也是 `nil`(老卡缺这三键是常态)——调用方据此整行不显示,
    // ⛔ 不许显示成"未判定"这种看起来像结论的占位。

    var positionVerdictLabel: String? { positionVerdict.map(nkVerdictLabel) }
    var positionVerdictTone: NKAxisTone { positionVerdict.map(nkVerdictTone) ?? .neutral }
    var coreVerdictLabel: String? { coreVerdict.map(nkVerdictLabel) }
    var coreVerdictTone: NKAxisTone { coreVerdict.map(nkVerdictTone) ?? .neutral }
}

/// 一张 D0 冻结的篮子卡(**B 类冻结快照**,蓝图 4.6 十一项)。
///
/// `narrative` 是 LLM 叙述,**原文整段呈现**、不得拆解塞回枚举卡片(§2.7);
/// `degraded=true` = **人话半份缺席、结构化半份照出**(不是"这张卡不可信")。
/// `disclaimer` 是固定文案单一源,**客户端原样透传不改写**。
struct BasketCard: Codable, Equatable {
    var specVersion: String? = nil
    var version: Int? = nil
    var basketKey: String = ""
    var tradeDate: String = ""
    var nextTradeDate: String? = nil
    var name: String = ""
    var driver: String = ""
    var driverKind: String = ""
    // V2.2-③-E 引擎归属三键(裁定 #9:单篮子单引擎,成员继承篮子引擎;老卡缺键 =
    // 「当时没有引擎归属概念」,⛔ 不是 engine 为空)。⚠ 成员上没有这两键(引擎标在
    // 篮子上),要显示成员引擎从这里(或壳 `Basket.engineCode` 等)取。
    var engineCode: String? = nil
    var engineVersion: String? = nil
    var skeletonVersion: String? = nil
    var evidence: [BasketEvidence] = []
    /// ⑤ 两段式流水的单侧故障披露:`ok` | `search_unavailable` | `partial`。
    /// ⛔ 不是 `ok` 时必须显式标注"取证不完整",不许静默当完整证据展示。
    var evidenceStatus: String = ""
    var whyNow: String = ""
    var members: [BasketMember] = []
    var roleConflicts: [String] = []
    var tier: Int? = nil
    var rankInTier: Int? = nil
    var rankMech: Int? = nil
    var mechScore: Double? = nil
    /// 五维分项 + 权重。**键是维度名**(与现役包权重键逐字对应),原样透传、⛔ 不改名。
    var tierBreakdown: NKJSON = .object([:])
    var tierReason: String? = nil
    var tierNote: String? = nil
    var scripts: BasketScripts? = nil
    var scriptsUnavailableReason: String? = nil
    /// 喂 ⑧ 哨兵的结构化 spec(机器半份),原样透传。
    var verificationSpec: NKJSON = .object([:])
    var verificationText: String? = nil
    var invalidationSpec: NKJSON = .object([:])
    var invalidationText: String? = nil
    var risks: [String] = []
    var disclaimer: String = ""
    var fingerprint: BasketFingerprint = BasketFingerprint()
    var disciplineLabels: [String] = []
    var narrative: String = ""
    var llmStage: String = ""
    var degraded: Bool = false
    var notes: [String] = []

    enum CodingKeys: String, CodingKey {
        case specVersion, version, basketKey, tradeDate, nextTradeDate, name
        case driver, driverKind, engineCode, engineVersion, skeletonVersion
        case evidence, evidenceStatus, whyNow, members, roleConflicts
        case tier, rankInTier, rankMech, mechScore, tierBreakdown, tierReason, tierNote
        case scripts, scriptsUnavailableReason
        case verificationSpec, verificationText, invalidationSpec, invalidationText
        case risks, disclaimer, fingerprint, disciplineLabels, narrative, llmStage, degraded, notes
    }

    init(specVersion: String? = nil, version: Int? = nil, basketKey: String = "",
         tradeDate: String = "", nextTradeDate: String? = nil, name: String = "",
         driver: String = "", driverKind: String = "", engineCode: String? = nil,
         engineVersion: String? = nil, skeletonVersion: String? = nil,
         evidence: [BasketEvidence] = [],
         evidenceStatus: String = "", whyNow: String = "", members: [BasketMember] = [],
         roleConflicts: [String] = [], tier: Int? = nil, rankInTier: Int? = nil,
         rankMech: Int? = nil, mechScore: Double? = nil, tierBreakdown: NKJSON = .object([:]),
         tierReason: String? = nil, tierNote: String? = nil, scripts: BasketScripts? = nil,
         scriptsUnavailableReason: String? = nil, verificationSpec: NKJSON = .object([:]),
         verificationText: String? = nil, invalidationSpec: NKJSON = .object([:]),
         invalidationText: String? = nil, risks: [String] = [], disclaimer: String = "",
         fingerprint: BasketFingerprint = BasketFingerprint(), disciplineLabels: [String] = [],
         narrative: String = "", llmStage: String = "", degraded: Bool = false,
         notes: [String] = []) {
        self.specVersion = specVersion; self.version = version; self.basketKey = basketKey
        self.tradeDate = tradeDate; self.nextTradeDate = nextTradeDate; self.name = name
        self.driver = driver; self.driverKind = driverKind
        self.engineCode = engineCode; self.engineVersion = engineVersion
        self.skeletonVersion = skeletonVersion; self.evidence = evidence
        self.evidenceStatus = evidenceStatus; self.whyNow = whyNow; self.members = members
        self.roleConflicts = roleConflicts; self.tier = tier; self.rankInTier = rankInTier
        self.rankMech = rankMech; self.mechScore = mechScore; self.tierBreakdown = tierBreakdown
        self.tierReason = tierReason; self.tierNote = tierNote; self.scripts = scripts
        self.scriptsUnavailableReason = scriptsUnavailableReason
        self.verificationSpec = verificationSpec; self.verificationText = verificationText
        self.invalidationSpec = invalidationSpec; self.invalidationText = invalidationText
        self.risks = risks; self.disclaimer = disclaimer; self.fingerprint = fingerprint
        self.disciplineLabels = disciplineLabels; self.narrative = narrative
        self.llmStage = llmStage; self.degraded = degraded; self.notes = notes
    }

    /// **B 类冻结快照 → 全字段 `decodeIfPresent`**(⛔ 一个 `try c.decode` 都不许有):
    /// `card_json` 是写入当时冻住的,服务端升级永远不会给老卡补新键;硬解码任何一个键,
    /// 装了新 App 的用户翻几周前的老卡就是**整张卡解不出**。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        specVersion = try c.decodeIfPresent(String.self, forKey: .specVersion)
        version = try c.decodeIfPresent(Int.self, forKey: .version)
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        nextTradeDate = try c.decodeIfPresent(String.self, forKey: .nextTradeDate)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        driver = try c.decodeIfPresent(String.self, forKey: .driver) ?? ""
        driverKind = try c.decodeIfPresent(String.self, forKey: .driverKind) ?? ""
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion)
        evidence = try c.decodeIfPresent([BasketEvidence].self, forKey: .evidence) ?? []
        evidenceStatus = try c.decodeIfPresent(String.self, forKey: .evidenceStatus) ?? ""
        whyNow = try c.decodeIfPresent(String.self, forKey: .whyNow) ?? ""
        members = try c.decodeIfPresent([BasketMember].self, forKey: .members) ?? []
        roleConflicts = try c.decodeIfPresent([String].self, forKey: .roleConflicts) ?? []
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        rankInTier = try c.decodeIfPresent(Int.self, forKey: .rankInTier)
        rankMech = try c.decodeIfPresent(Int.self, forKey: .rankMech)
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        tierBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .tierBreakdown) ?? .object([:])
        tierReason = try c.decodeIfPresent(String.self, forKey: .tierReason)
        tierNote = try c.decodeIfPresent(String.self, forKey: .tierNote)
        scripts = try c.decodeIfPresent(BasketScripts.self, forKey: .scripts)
        scriptsUnavailableReason = try c.decodeIfPresent(String.self,
                                                         forKey: .scriptsUnavailableReason)
        verificationSpec = try c.decodeIfPresent(NKJSON.self, forKey: .verificationSpec) ?? .object([:])
        verificationText = try c.decodeIfPresent(String.self, forKey: .verificationText)
        invalidationSpec = try c.decodeIfPresent(NKJSON.self, forKey: .invalidationSpec) ?? .object([:])
        invalidationText = try c.decodeIfPresent(String.self, forKey: .invalidationText)
        risks = try c.decodeIfPresent([String].self, forKey: .risks) ?? []
        disclaimer = try c.decodeIfPresent(String.self, forKey: .disclaimer) ?? ""
        fingerprint = try c.decodeIfPresent(BasketFingerprint.self,
                                            forKey: .fingerprint) ?? BasketFingerprint()
        disciplineLabels = try c.decodeIfPresent([String].self, forKey: .disciplineLabels) ?? []
        narrative = try c.decodeIfPresent(String.self, forKey: .narrative) ?? ""
        llmStage = try c.decodeIfPresent(String.self, forKey: .llmStage) ?? ""
        degraded = try c.decodeIfPresent(Bool.self, forKey: .degraded) ?? false
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    /// 取证完整性展示(⛔ 不是 `ok` 就必须说出来,不许静默当完整证据展示)。
    var evidenceIncompleteNote: String? {
        switch evidenceStatus {
        case "", "ok": return nil
        case "search_unavailable": return "取证不完整 · 检索侧不可用,以下证据未经联网核实"
        case "partial": return "取证不完整 · 仅部分证据经检索核实"
        default: return "取证不完整(\(evidenceStatus))"
        }
    }
}

/// 百分制打分卡里的**一维贡献**(V2.1-④,**纯展示层**)。
///
/// `contribPercent = 归一化权重 × 该维得分 × 100`,五维合计 ≈ `scorePercent`
/// (各项独立舍入,末位可能差零点几)。**唯一换算实现在服务端**
/// `neckline/report/score_display.py` —— ⛔ 客户端不重算、不另建中文标签表
/// (`label` 由服务端给);本类型只负责把已经算好的数**格式化**成一位小数。
/// ⚠ 契约里 `contribPercent` 是 **4 位小数**(精度住契约),展示层各自 `:.1f`
/// (位数住展示)—— ⛔ 别在解码时先舍入,那会把"五维合计 ≈ 总分"的自洽性吃掉。
///
/// 🔴 **`neutralFilled == true` 是一句必须说出口的话**:那一维今天**没算出来**、
/// 按中性分 0.5 计入 —— 它撑起来的那部分分数**不是「这一维表现好」**。
/// ⛔ 不许渲染成与其它维度无差别的一根条(§3.8「没有」与「没看」必须分得开)。
///
/// `dimScore` / `weight` 为 `nil` = 该维在冻结留痕里就缺这个数,**⛔ 不是 0**。
struct ScoreContribution: Codable, Equatable, Identifiable {
    var dim: String = ""
    var label: String = ""
    var dimScore: Double? = nil
    var weight: Double? = nil
    var contribPercent: Double? = nil
    var neutralFilled: Bool = false

    var id: String { dim }

    enum CodingKeys: String, CodingKey {
        case dim, label, dimScore, weight, contribPercent, neutralFilled
    }

    init(dim: String = "", label: String = "", dimScore: Double? = nil, weight: Double? = nil,
         contribPercent: Double? = nil, neutralFilled: Bool = false) {
        self.dim = dim; self.label = label; self.dimScore = dimScore; self.weight = weight
        self.contribPercent = contribPercent; self.neutralFilled = neutralFilled
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        dim = try c.decodeIfPresent(String.self, forKey: .dim) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        dimScore = try c.decodeIfPresent(Double.self, forKey: .dimScore)
        weight = try c.decodeIfPresent(Double.self, forKey: .weight)
        contribPercent = try c.decodeIfPresent(Double.self, forKey: .contribPercent)
        neutralFilled = try c.decodeIfPresent(Bool.self, forKey: .neutralFilled) ?? false
    }

    /// 展示名:服务端给了中文 `label` 就用它,没给(未登记的新维度)原样显示 `dim`
    /// —— ⛔ 不在客户端另建一份中文映射(那就是第二个会漂的语义来源)。
    var displayLabel: String { label.isEmpty ? dim : label }
}

/// 一篮的 Tier 定档留痕(`tier_history` 一行,**A 类**)。
/// **Tier = 注意力优先级,不是收益预测**(§2.8-C 红线):`rankInTier` 排第一 ≠ 最会涨。
///
/// `tier` 取值域:**新数据 ∈ {1, 2}**(V2.1-② T3 全链退役,写侧收窄);
/// 历史留痕行仍可能是 `3` —— ⛔ 客户端别把 3 当非法值,那是 V2 时代的真实数据。
struct Tier: Codable, Equatable {
    var basketId: Int = 0
    var tradeDate: String = ""
    var tier: Int? = nil
    var mechScore: Double? = nil
    var mechBreakdown: NKJSON = .object([:])
    var rankInTier: Int? = nil
    /// LLM 微调**之前**的机械序;与 `llmRankDelta`(微调位移)两个都留着,定档才谈得上可复现。
    var rankMech: Int? = nil
    var llmRankDelta: Int = 0
    var llmReason: String? = nil
    var packVersion: String? = nil
    /// **V2.1-④ 新增两个只读键**:`mechScore` 的百分制等价换算 + 五维拆解,
    /// 由服务端 `report/score_display.py` 从**同一份已冻结的 `mechBreakdown`** 算出。
    /// ⛔ 它不是第二个分数、不进任何判定路径。
    /// `scorePercent == nil` = 这一篮取不到分(没有 breakdown),**⛔ 不是 0 分**。
    var scorePercent: Double? = nil
    var scoreContributions: [ScoreContribution] = []

    enum CodingKeys: String, CodingKey {
        case basketId, tradeDate, tier, mechScore, mechBreakdown
        case rankInTier, rankMech, llmRankDelta, llmReason, packVersion
        case scorePercent, scoreContributions
    }

    init(basketId: Int = 0, tradeDate: String = "", tier: Int? = nil, mechScore: Double? = nil,
         mechBreakdown: NKJSON = .object([:]), rankInTier: Int? = nil, rankMech: Int? = nil,
         llmRankDelta: Int = 0, llmReason: String? = nil, packVersion: String? = nil,
         scorePercent: Double? = nil, scoreContributions: [ScoreContribution] = []) {
        self.basketId = basketId; self.tradeDate = tradeDate; self.tier = tier
        self.mechScore = mechScore; self.mechBreakdown = mechBreakdown
        self.rankInTier = rankInTier; self.rankMech = rankMech; self.llmRankDelta = llmRankDelta
        self.llmReason = llmReason; self.packVersion = packVersion
        self.scorePercent = scorePercent; self.scoreContributions = scoreContributions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        mechBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .mechBreakdown) ?? .object([:])
        rankInTier = try c.decodeIfPresent(Int.self, forKey: .rankInTier)
        rankMech = try c.decodeIfPresent(Int.self, forKey: .rankMech)
        llmRankDelta = try c.decodeIfPresent(Int.self, forKey: .llmRankDelta) ?? 0
        llmReason = try c.decodeIfPresent(String.self, forKey: .llmReason)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        scorePercent = try c.decodeIfPresent(Double.self, forKey: .scorePercent)
        scoreContributions = try c.decodeIfPresent([ScoreContribution].self,
                                                   forKey: .scoreContributions) ?? []
    }
}

/// 一个篮子的壳(**A 类**)。`card == nil` + `cardUnavailableReason == "card_not_ready"`
/// = **篮子在、卡没生成**(⑦ 事务 1 与事务 2 分开,合法中间态)。
/// ⛔ 不许把它显示成「篮子不存在」——那是另一回事(`basket_not_found`)。
struct Basket: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var basketKey: String = ""
    var name: String = ""
    var tradeDate: String = ""
    var tier: Int? = nil
    var memberCodes: [String] = []
    // V2.2-③-E 引擎归属三键(裁定 #9:单篮子单引擎,成员继承篮子引擎)。⚠ **与
    // `scorePercent` 不同构**:live(`GET /baskets`)与报告快照两条路**都**从
    // `baskets` 表行直填(服务端 `_shape_basket`/`load_today_baskets` 同一姿势),
    // ⛔ 不是"live 路径刻意留空"那种非对称 —— 这里不需要 `?? tierHistory` 式合并。
    // `card.engineCode` 等是同一次落库写入在冻结卡里的另一份拷贝,读法见下方
    // `engineVersionDisplay`(壳优先,壳缺席时兜底读卡)。
    var engineCode: String? = nil
    var engineVersion: String? = nil
    var skeletonVersion: String? = nil
    var card: BasketCard? = nil
    var cardVersion: Int? = nil
    var cardUnavailableReason: String? = nil
    var tierHistory: Tier? = nil
    /// 🔴 **这两键只在「报告快照」这条路上有值**(V2.1-④,**B 类:随
    /// `reports.basket_daily_json` 冻住**);**live 路径(`GET /baskets`)刻意留空**,
    /// 那条路上同一个数住 `tierHistory.scorePercent`(分数是定档留痕的属性)。
    /// ⛔ 读的时候别只读一处 —— 唯一正确读法是下面的 `scoreDisplayPercent`
    /// (= `scorePercent ?? tierHistory?.scorePercent`,服务端 `BasketOut` docstring 定死)。
    var scorePercent: Double? = nil
    var scoreContributions: [ScoreContribution] = []

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, basketKey, name, tradeDate, tier, memberCodes
        case engineCode, engineVersion, skeletonVersion
        case card, cardVersion, cardUnavailableReason, tierHistory
        case scorePercent, scoreContributions
    }

    init(basketId: Int = 0, basketKey: String = "", name: String = "", tradeDate: String = "",
         tier: Int? = nil, memberCodes: [String] = [], engineCode: String? = nil,
         engineVersion: String? = nil, skeletonVersion: String? = nil, card: BasketCard? = nil,
         cardVersion: Int? = nil, cardUnavailableReason: String? = nil, tierHistory: Tier? = nil,
         scorePercent: Double? = nil, scoreContributions: [ScoreContribution] = []) {
        self.basketId = basketId; self.basketKey = basketKey; self.name = name
        self.tradeDate = tradeDate; self.tier = tier; self.memberCodes = memberCodes
        self.engineCode = engineCode; self.engineVersion = engineVersion
        self.skeletonVersion = skeletonVersion
        self.card = card; self.cardVersion = cardVersion
        self.cardUnavailableReason = cardUnavailableReason; self.tierHistory = tierHistory
        self.scorePercent = scorePercent; self.scoreContributions = scoreContributions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        memberCodes = try c.decodeIfPresent([String].self, forKey: .memberCodes) ?? []
        engineCode = try c.decodeIfPresent(String.self, forKey: .engineCode)
        engineVersion = try c.decodeIfPresent(String.self, forKey: .engineVersion)
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion)
        card = try c.decodeIfPresent(BasketCard.self, forKey: .card)
        cardVersion = try c.decodeIfPresent(Int.self, forKey: .cardVersion)
        cardUnavailableReason = try c.decodeIfPresent(String.self, forKey: .cardUnavailableReason)
        tierHistory = try c.decodeIfPresent(Tier.self, forKey: .tierHistory)
        scorePercent = try c.decodeIfPresent(Double.self, forKey: .scorePercent)
        scoreContributions = try c.decodeIfPresent([ScoreContribution].self,
                                                   forKey: .scoreContributions) ?? []
    }

    // —— V2.1-④ 百分制打分卡的**唯一读法**(两条路各填一处,展示层收口在这里)——

    /// 本篮的百分分数。`nil` = **本篮无打分可显示**(老报告快照没有这两个键 /
    /// 这一篮没有定档留痕)—— 🔴 **⛔ 绝不是 0 分**,展示处如实写「本报告版本无打分」。
    var scoreDisplayPercent: Double? { scorePercent ?? tierHistory?.scorePercent }

    /// 与 `scoreDisplayPercent` **同源**的五维拆解:分数从哪条路来,拆解就从哪条路取
    /// (⛔ 不许一处取分、另一处取拆解 —— 那会在同一张卡上拼出两份数据的混合体)。
    var scoreDisplayContributions: [ScoreContribution] {
        scorePercent != nil ? scoreContributions : (tierHistory?.scoreContributions ?? [])
    }

    // —— V2.2-③-E 引擎徽标的**唯一读法**(壳优先,壳缺席〔极旧数据〕时兜底读卡;
    // 两条路正常情况下逐位相同,见上方字段注释,这里的 `??` 只是防御性兜底,
    // ⛔ 不是"两条路刻意不同步"那种语义)——

    var engineCodeDisplay: String? { engineCode ?? card?.engineCode }
    var engineVersionDisplay: String? { engineVersion ?? card?.engineVersion }
    var skeletonVersionDisplay: String? { skeletonVersion ?? card?.skeletonVersion }

    /// V2.2-③ 六关灯条的**唯一读法**(两条路各自带一份**同一次写入**的拷贝):
    /// 报告快照路只有 `card.tierBreakdown`(⚠ `BasketView` 不发 `tierHistory`),
    /// live 路(`GET /baskets/{id}`)两处都有 —— 故卡优先、留痕兜底。
    /// ⛔ 这不是"两份可能不同步的数据":`tier.py` 把同一个 breakdown dict 同时写进
    /// `tier_history.mech_breakdown` 与卡的 `tier_breakdown`。
    var gates: BasketGates {
        let g = BasketGates(tierBreakdown: card?.tierBreakdown)
        return g.available ? g : BasketGates(tierBreakdown: tierHistory?.mechBreakdown)
    }

    /// 「落地起跳位置态」的篮子级摘要(裁定 #11:位置关是**成员级**判定,篮子级只能
    /// 说"最差的那只是什么")。nil = 这张卡没有任何成员带位置判定(老卡常态)。
    /// ⛔ 别把 nil 显示成「位置合适」。
    var worstPositionVerdict: String? { nkWorstVerdict(card?.members.compactMap(\.positionVerdict)) }

    /// 核心关同款(裁定 #12)。
    var worstCoreVerdict: String? { nkWorstVerdict(card?.members.compactMap(\.coreVerdict)) }

    /// 卡未就绪时的诚实文案。⛔ **不是**「篮子不存在」。
    var cardUnavailableText: String? {
        guard card == nil else { return nil }
        switch cardUnavailableReason {
        case "card_not_ready", .none: return "本篮的卡还没生成"
        // B1(2026-08-04):「有卡行但读不出」是**数据事故**,不是等待中 ——
        // ⛔ 不许合进上一条,那会让用户以为再等等就有了(卡是冻结件,不会自己好)。
        case "card_corrupt": return "本篮卡数据损坏,已记录待排查"
        case .some(let r): return "本篮的卡暂不可用(\(r))"
        }
    }
}

/// ③b 未定档篮子一行(⑥-b-C;**V2.2-③ 起「名 / 分 / 卡在哪一关、差多少 / 原因码」**)。
/// 🔴 **每个 `reason` 码指向不同的市场 / 系统结论,⛔ 不许合并成一句「未入选」**——
/// 逐码含义见 `nkDroppedReasonLabel`(唯一源 = 服务端
/// `report/basket_daily.py::DROPPED_REASON_LABEL`,这里是它的中文展示层镜像)。
/// `gate`/`gateDetail` 是 **V2.2 新增可选键**:老快照缺它们 = 该版本还没有关口概念
/// (⛔ 不是「没卡在任何关」)。
/// **没有 `basketId`** —— 它没进 `baskets` 表,给一个 id 会让用户以为点得进去。
struct DroppedBasket: Codable, Equatable, Identifiable {
    var name: String = ""
    var mechScore: Double? = nil
    var reason: String = ""
    /// 卡在哪一关(`market|driver|sector|core|position|evidence`);nil = 老快照 / 与关口无关。
    var gate: String? = nil
    /// 差多少(服务端的机器原因码串,数值内嵌)。**原样展示**,⛔ 不改写、不翻译。
    var gateDetail: String? = nil

    // ⚠ id 必须把 `gate` 也算进去:同一篮同一 reason 但卡在不同关是可能的(如
    // 证据关降级出局时 `gate` 取第一道降级关),不带进 id 会让 ForEach 撞 key。
    var id: String { "\(name)|\(reason)|\(gate ?? "")" }

    enum CodingKeys: String, CodingKey { case name, mechScore, reason, gate, gateDetail }

    init(name: String = "", mechScore: Double? = nil, reason: String = "",
         gate: String? = nil, gateDetail: String? = nil) {
        self.name = name; self.mechScore = mechScore; self.reason = reason
        self.gate = gate; self.gateDetail = gateDetail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        mechScore = try c.decodeIfPresent(Double.self, forKey: .mechScore)
        reason = try c.decodeIfPresent(String.self, forKey: .reason)  ?? ""
        gate = try c.decodeIfPresent(String.self, forKey: .gate)
        gateDetail = try c.decodeIfPresent(String.self, forKey: .gateDetail)
    }

    var reasonLabel: String { nkDroppedReasonLabel(reason) }
    var reasonTone: NKAxisTone { nkDroppedReasonTone(reason) }

    /// V2.3 信息层级:把「主句 · 补充」拆成两行(⛔ **不改字面**,只是换行位置)。
    /// 主句着 `reasonTone`、补充走次级色 —— 一列九种原因挤成同色同字号的一坨,
    /// 「系统缺席」那两码就淹在里面了,而它们恰恰是最该被看见的。
    var reasonHeadline: String {
        reasonLabel.components(separatedBy: " · ").first ?? reasonLabel
    }
    /// `nil` = 这个码没有补充句(如未识别码原样透传)。
    var reasonDetail: String? {
        let parts = reasonLabel.components(separatedBy: " · ")
        guard parts.count > 1 else { return nil }
        return parts.dropFirst().joined(separator: " · ")
    }
    /// 「卡在哪一关」的人读名;nil = 老快照没有这个键(⛔ 不写「无」)。
    var gateLabel: String? { gate.map(nkGateLabel) }
    /// 该关是机械关(硬否决)还是证据关(只降级)—— nil = 不知道是哪一关。
    var gateKind: NKGateKind? { gate.map(nkGateKind) }
}

/// ③b 原因码的展示层换算(**⛔ 不许合并**,每个码指向不同的结论)。
/// 🔴 **唯一源是服务端 `report/basket_daily.py::DROPPED_REASON_LABEL`**;这里是
/// 中文短句镜像(界面要一行放得下,服务端那份带括号补充给 markdown 报告用)。
/// ⚠ **码字符串一经落库不改**(⑨ 按原因码归因,改码 = 历史归因断线)——
/// 新增码只加 case,⛔ 不改既有码的字面。未识别码原样透传,不静默瞎翻译。
func nkDroppedReasonLabel(_ raw: String) -> String {
    switch raw {
    case "capacity_overflow": return "档位已满 · 今天机会多到装不下"
    // ⚠ V2.2 门槛制**之前**的历史码:老快照回放仍会出现,⛔ 别当非法值。
    case "below_quality_line": return "未过质量线 · 今天没什么好货(V2.2 前的历史码)"
    // —— V2.2-③ 六道关口新增七码(服务端已登记;⛔ 一个都不许并成「未入选」)——
    case "evidence_degraded_out": return "证据关降级出局 · 逻辑没被证据撑住"
    case "mech_gate_rejected": return "机械关硬否决 · 市场关 / 板块关不过"
    // 🔴 裁定 #11:位置关判定交 LLM,`unfit` **不是硬否决** —— 票就在这张表里、
    // 写明是哪只成员与模型的理由。
    case "position_unfit": return "位置关判定不合适 · 落地起跳位置不对(非硬否决)"
    // 🔴 裁定 #12:核心关同款交 LLM。「不是龙头」≠「这票不行」。
    case "core_unfit": return "核心关判定不是龙头 · 同行业里不占核心地位(非硬否决)"
    case "members_all_removed": return "成员级机械关对拍后成员全部出篮"
    // 🔴 下面两码是**系统缺席**,不是市场结论 —— ⛔ 别读成「今天没好票」。
    case "no_active_engine": return "无运行中的引擎线 · 系统缺席(不是市场结论)"
    case "engine_unresolved": return "引擎归属解析失败 · 无引擎可容纳"
    default: return raw
    }
}

/// ③b 原因码的着色。**三类分开**:关口过了只是位置满(good)/ 市场或证据判它不行
/// (warn)/ **系统自己缺席**(bad —— 那不是市场结论,是我们没跑起来,该最刺眼)。
func nkDroppedReasonTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "capacity_overflow": return .good
    case "no_active_engine", "engine_unresolved": return .bad
    default: return .warn
    }
}

// MARK: - V2.2-③ 六道关口(展示层:码 → 中文 + 机械关 / 证据关二分)
//
// 🔴 **③-A 二分是产品语义,不是配色偏好**(用户裁定 #6 + #11 + #12):
//   · **机械关**(市场 / 板块)读的是已预计算的**客观量** → **硬否决**,过不去就没了;
//   · **证据关**(驱动 / 核心 / 位置 / 证据)由 LLM 组织证据 → **只降级**
//     (T1→T2→退出正式候选,**仍在报告 ③b 列名**)。
// ⛔ 界面上两者必须能分辨:后果完全不同,混成一种灯就是把「否决」与「扣分」讲成一回事。
// 唯一源 = 服务端 `selection/gates.py`(`MECH_GATES` / `EVIDENCE_GATES` / `GATE_LABELS`)。

enum NKGateKind: Equatable {
    /// 机械关:硬否决(市场 / 板块)。
    case mechanical
    /// 证据关:只降级、不除名(驱动 / 核心 / 位置 / 证据)。
    case evidence
    /// 未识别的关口码(新服务端加了关而这版客户端还不认识)——⛔ 不猜成任何一类。
    case unknown

    var label: String {
        switch self {
        case .mechanical: return "机械关 · 硬否决"
        case .evidence:   return "证据关 · 只降级"
        case .unknown:    return "未知关口"
        }
    }
}

/// 六关码的固定展示顺序(= 服务端 `GATE_ORDER`)。⛔ 别按字典序排 —— 那会把
/// 「市场 → 驱动 → 板块 → 核心 → 位置 → 证据」这条管线顺序打乱。
let nkGateOrder: [String] = ["market", "driver", "sector", "core", "position", "evidence"]

func nkGateLabel(_ raw: String) -> String {
    switch raw {
    case "market": return "市场关"
    case "driver": return "驱动关"
    case "sector": return "板块关"
    case "core": return "核心关"
    case "position": return "位置关"
    case "evidence": return "证据关"
    default: return raw
    }
}

func nkGateKind(_ raw: String) -> NKGateKind {
    switch raw {
    case "market", "sector": return .mechanical
    case "driver", "core", "position", "evidence": return .evidence
    default: return .unknown
    }
}

/// 关口判定三值的展示层换算(服务端 `VERDICT_PASS/DEGRADE/REJECT`)。
func nkGateVerdictLabel(_ raw: String) -> String {
    switch raw {
    case "pass": return "过"
    case "degrade": return "降级"
    case "reject": return "否决"
    default: return raw
    }
}

func nkGateVerdictTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "pass": return .good
    case "degrade": return .warn
    case "reject": return .bad
    default: return .neutral
    }
}

/// 一篮的六关灯条读数(**纯展示投影**,从已冻结的 `card.tierBreakdown["gates"]` 读出)。
///
/// 🔴 **数据来源刻意是冻结卡而不是现连**:这一节由 `selection/tier.py::_gate_breakdown`
/// 在定档当时写进 `mech_breakdown_json.gates`,并随篮子卡冻住 —— 回看三天前的报告
/// 该看到**当时**判的六关,不是今天重判一遍的结果。⛔ 别改成调 `gate_evaluations`。
///
/// **`available == false` 的三种成因经本类型收口后不可区分**(老卡没有这一节 /
/// 这一篮没有关口汇总 / 汇总读不出)—— 三者给用户的动作相同(等下一份报告),
/// 但 ⛔ **绝不许渲染成「六关都过了」**:那是把「没看」讲成「没有问题」。
struct BasketGates: Equatable {
    var available: Bool = false
    /// `gate 码 → verdict 码`(每关取**最严**的那一条,服务端已归并)。
    var verdicts: [String: String] = [:]
    var engineCode: String? = nil
    var engineVersion: String? = nil
    /// 证据关累计降了几档(服务端 `evidence_degrades`)。
    var evidenceDegrades: Int? = nil
    /// 哪些证据关判了降级。
    var degradedGates: [String] = []
    /// **该篮不得进 T1**(⚠ 与「被否决」不是一回事:多半是某一关**判不出**)。
    var blocksT1: Bool = false
    /// 位置关 / 核心关有成员被判 `unfit`(裁定 #11 / #12:退出正式候选,⛔ 非硬否决)。
    var positionUnfit: Bool = false
    var coreUnfit: Bool = false

    /// 从 `card.tierBreakdown` 里读那一节。⛔ 不重算任何判据,只做投影。
    init(tierBreakdown: NKJSON?) {
        guard let node = tierBreakdown?["gates"], let obj = node.objectValue else { return }
        available = obj["available"]?.boolValue ?? false
        guard available else { return }
        engineCode = obj["engine_code"]?.stringValue
        engineVersion = obj["engine_version"]?.stringValue
        evidenceDegrades = obj["evidence_degrades"]?.intValue
        degradedGates = (obj["degraded_gates"]?.arrayValue ?? []).compactMap(\.stringValue)
        blocksT1 = obj["blocks_t1"]?.boolValue ?? false
        positionUnfit = obj["position_unfit"]?.boolValue ?? false
        coreUnfit = obj["core_unfit"]?.boolValue ?? false
        for (k, v) in (obj["verdicts"]?.objectValue ?? [:]) {
            if let s = v.stringValue { verdicts[k] = s }
        }
    }

    /// 灯条一格。`verdict == nil` = **这一关这份快照里没有记录**,⛔ 不是「过了」。
    struct Light: Identifiable, Equatable {
        let gate: String
        let verdict: String?
        var id: String { gate }
        var label: String { nkGateLabel(gate) }
        var kind: NKGateKind { nkGateKind(gate) }
        var verdictLabel: String { verdict.map(nkGateVerdictLabel) ?? "未记录" }
        var tone: NKAxisTone { verdict.map(nkGateVerdictTone) ?? .neutral }
    }

    /// 六格,**恒定六格、恒定顺序**(缺记录的那格如实标「未记录」,⛔ 不隐藏 ——
    /// 隐藏会让「这一关没判」看起来像「这一关不存在」)。
    var lights: [Light] {
        nkGateOrder.map { Light(gate: $0, verdict: verdicts[$0]) }
    }

    /// 「卡在哪一关」= 按管线顺序第一道非 pass 的关;全过 → nil。
    var blockedGate: String? {
        nkGateOrder.first { (verdicts[$0] ?? "pass") != "pass" }
    }
}

/// ⑧ 的「当前状态」三路读法(**A 类**)。三个位分别回答不同问题,⛔ 不许合并:
/// `state` 四态 / `provisional`(盘中暂态、未收盘定论)/ `notEvaluated`(**今天还没判过**,
/// 不是「判了是 unclear」)。
///
/// ⚠ **篮子 `falsified` ≠ 持仓该走**(CLAUDE.md 坑条):它说的是「这个驱动假设不成立了」,
/// **不指向任何持仓动作**、不进推送。展示处不得写成卖出暗示。
struct BasketVerification: Codable, Equatable {
    var basketId: Int = 0
    var tradeDate: String = ""
    var state: String = ""      // verified | partial | unclear | falsified
    var label: String = ""
    var source: String? = nil
    var observedAt: String? = nil
    var provisional: Bool = false
    var notEvaluated: Bool = false
    var evidence: NKJSON? = nil
    var rows: [NKJSON] = []

    enum CodingKeys: String, CodingKey {
        case basketId, tradeDate, state, label, source, observedAt
        case provisional, notEvaluated, evidence, rows
    }

    init(basketId: Int = 0, tradeDate: String = "", state: String = "", label: String = "",
         source: String? = nil, observedAt: String? = nil, provisional: Bool = false,
         notEvaluated: Bool = false, evidence: NKJSON? = nil, rows: [NKJSON] = []) {
        self.basketId = basketId; self.tradeDate = tradeDate; self.state = state
        self.label = label; self.source = source; self.observedAt = observedAt
        self.provisional = provisional; self.notEvaluated = notEvaluated
        self.evidence = evidence; self.rows = rows
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? ""
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        source = try c.decodeIfPresent(String.self, forKey: .source)
        observedAt = try c.decodeIfPresent(String.self, forKey: .observedAt)
        provisional = try c.decodeIfPresent(Bool.self, forKey: .provisional) ?? false
        notEvaluated = try c.decodeIfPresent(Bool.self, forKey: .notEvaluated) ?? false
        evidence = try c.decodeIfPresent(NKJSON.self, forKey: .evidence)
        rows = try c.decodeIfPresent([NKJSON].self, forKey: .rows) ?? []
    }

    /// 角标文案。**「今天还没判过」与「判了是 unclear」讲不同的话**。
    var badgeText: String {
        if notEvaluated { return "今日尚未判定" }
        let base = label.isEmpty ? nkVerificationStateLabel(state) : label
        return provisional ? "\(base) · 盘中暂态" : base
    }

    var badgeTone: NKAxisTone {
        if notEvaluated { return .neutral }
        switch state {
        case "verified": return .good
        case "partial": return .warn
        case "falsified": return .bad
        default: return .neutral
        }
    }
}

/// 验证四态的展示层换算(服务端 `label` 优先;这里只做兜底,未识别原样透传)。
func nkVerificationStateLabel(_ raw: String) -> String {
    switch raw {
    case "verified": return "已验证"
    case "partial": return "部分验证"
    case "unclear": return "未明"
    case "falsified": return "驱动假设已证伪"
    default: return raw.isEmpty ? "未判定" : raw
    }
}

/// ⑨ 的一篮盘后复盘(`mech` 是 **B 类冻结快照**,`llmText` 是参考件)。
/// `depth`:`full`(T1/T2 详复盘)| `brief`(T3 简评)。
/// `llmText == nil` + `llmSkipReason` 非空 = **未生成**(预算耗尽 / 降级),
/// ⛔ 不拿空串冒充「生成了但没内容」。
struct BasketReview: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var basketKey: String = ""
    var name: String = ""
    var tier: Int? = nil
    var d0: String = ""
    var reviewDate: String = ""
    var depth: String = ""
    var mech: NKJSON = .object([:])
    var llmText: String? = nil
    var llmSkipReason: String? = nil
    var degraded: Bool = false
    var verification: NKJSON? = nil

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, basketKey, name, tier, d0, reviewDate, depth
        case mech, llmText, llmSkipReason, degraded, verification
    }

    init(basketId: Int = 0, basketKey: String = "", name: String = "", tier: Int? = nil,
         d0: String = "", reviewDate: String = "", depth: String = "",
         mech: NKJSON = .object([:]), llmText: String? = nil, llmSkipReason: String? = nil,
         degraded: Bool = false, verification: NKJSON? = nil) {
        self.basketId = basketId; self.basketKey = basketKey; self.name = name; self.tier = tier
        self.d0 = d0; self.reviewDate = reviewDate; self.depth = depth; self.mech = mech
        self.llmText = llmText; self.llmSkipReason = llmSkipReason; self.degraded = degraded
        self.verification = verification
    }

    /// **B 类冻结快照**(`mech` ← `basket_review_daily.mech_json`)→ 全字段 `decodeIfPresent`。
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        basketKey = try c.decodeIfPresent(String.self, forKey: .basketKey) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        tier = try c.decodeIfPresent(Int.self, forKey: .tier)
        d0 = try c.decodeIfPresent(String.self, forKey: .d0) ?? ""
        reviewDate = try c.decodeIfPresent(String.self, forKey: .reviewDate) ?? ""
        depth = try c.decodeIfPresent(String.self, forKey: .depth) ?? ""
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        llmText = try c.decodeIfPresent(String.self, forKey: .llmText)
        llmSkipReason = try c.decodeIfPresent(String.self, forKey: .llmSkipReason)
        degraded = try c.decodeIfPresent(Bool.self, forKey: .degraded) ?? false
        verification = try c.decodeIfPresent(NKJSON.self, forKey: .verification)
    }

    var depthLabel: String {
        switch depth {
        case "full": return "详复盘"
        case "brief": return "简评"
        default: return depth
        }
    }
}

/// 报告里的篮子日报三段(③ 今日篮子 / ③b 未定档 / ④ 昨日复盘)。
///
/// **每段各自带 `*Available` + `*UnavailableReason`**,两种「空」在界面上**必须讲不同的话**:
///  · 空数组 + `available == true` = **今天真没有**(合法输出);
///  · `available == false` = **本次没取到**。
struct BasketDaily: Codable, Equatable {
    var tradeDate: String = ""
    var baskets: [Basket] = []
    var basketsAvailable: Bool = false
    var basketsUnavailableReason: String? = nil
    var droppedBaskets: [DroppedBasket] = []
    var droppedBasketsAvailable: Bool = false
    var droppedBasketsUnavailableReason: String? = nil
    var reviews: [BasketReview] = []
    var reviewsAvailable: Bool = false
    var reviewsUnavailableReason: String? = nil
    var reviewD0: String? = nil
    var packVersion: String? = nil
    var notes: [String] = []

    enum CodingKeys: String, CodingKey {
        case tradeDate, baskets, basketsAvailable, basketsUnavailableReason
        case droppedBaskets, droppedBasketsAvailable, droppedBasketsUnavailableReason
        case reviews, reviewsAvailable, reviewsUnavailableReason
        case reviewD0, packVersion, notes
    }

    init(tradeDate: String = "", baskets: [Basket] = [], basketsAvailable: Bool = false,
         basketsUnavailableReason: String? = nil, droppedBaskets: [DroppedBasket] = [],
         droppedBasketsAvailable: Bool = false, droppedBasketsUnavailableReason: String? = nil,
         reviews: [BasketReview] = [], reviewsAvailable: Bool = false,
         reviewsUnavailableReason: String? = nil, reviewD0: String? = nil,
         packVersion: String? = nil, notes: [String] = []) {
        self.tradeDate = tradeDate; self.baskets = baskets
        self.basketsAvailable = basketsAvailable
        self.basketsUnavailableReason = basketsUnavailableReason
        self.droppedBaskets = droppedBaskets
        self.droppedBasketsAvailable = droppedBasketsAvailable
        self.droppedBasketsUnavailableReason = droppedBasketsUnavailableReason
        self.reviews = reviews; self.reviewsAvailable = reviewsAvailable
        self.reviewsUnavailableReason = reviewsUnavailableReason
        self.reviewD0 = reviewD0; self.packVersion = packVersion; self.notes = notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        baskets = try c.decodeIfPresent([Basket].self, forKey: .baskets) ?? []
        basketsAvailable = try c.decodeIfPresent(Bool.self, forKey: .basketsAvailable) ?? false
        basketsUnavailableReason = try c.decodeIfPresent(String.self,
                                                         forKey: .basketsUnavailableReason)
        droppedBaskets = try c.decodeIfPresent([DroppedBasket].self, forKey: .droppedBaskets) ?? []
        droppedBasketsAvailable = try c.decodeIfPresent(Bool.self,
                                                        forKey: .droppedBasketsAvailable) ?? false
        droppedBasketsUnavailableReason = try c.decodeIfPresent(
            String.self, forKey: .droppedBasketsUnavailableReason)
        reviews = try c.decodeIfPresent([BasketReview].self, forKey: .reviews) ?? []
        reviewsAvailable = try c.decodeIfPresent(Bool.self, forKey: .reviewsAvailable) ?? false
        reviewsUnavailableReason = try c.decodeIfPresent(String.self,
                                                         forKey: .reviewsUnavailableReason)
        reviewD0 = try c.decodeIfPresent(String.self, forKey: .reviewD0)
        packVersion = try c.decodeIfPresent(String.self, forKey: .packVersion)
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
    }

    /// 某一档的篮子。**空档位如实显示「今日 T1 为空」,⛔ 不隐藏**(E1)。
    /// `tier == nil`(极旧快照 / 数据缺口)的篮子**不进任何档**,⛔ 别拿假档位塞进去。
    func baskets(tier: Int) -> [Basket] { baskets.filter { $0.tier == tier } }

    /// **现役档位**(V2.1-② 起引擎两档化;写侧收紧的单一源在服务端
    /// `selection/tier.py::TIERS`,客户端这份是展示层的镜像)。
    static let liveTiers: [Int] = [1, 2]

    /// ③ 节要渲染的档位 = **现役两档 ∪ 本份快照实际出现的档位**(V2.1-② 移交 ⑦ 的硬约束,
    /// 与服务端 `render._render_today_baskets` 同构)。
    ///
    /// 🔴 **⛔ 不许写死 `[1, 2]`**:`basketDaily` 是**冻结快照**,回放一份 V2 时代的老报告
    /// 时里面就有 tier=3 的篮子 —— 写死两档会让它们在客户端**静默消失**(那是"把历史删了",
    /// 不是"退役新档"),等于把服务端刚立起来的读侧宽容在展示层拆掉。
    /// 🔴 **⛔ 也不许写死 `[1, 2, 3]`**:新报告会凭空多出一个恒空的 T3 分组并说
    /// 「今日 T3 为空(算过了,今天没有达到该档标准的篮子)」—— 而真相是 T3 已取消,
    /// **那正是把系统缺席讲成实质性市场结论**(§2 诚实披露红线)。
    /// 并集两头都对:现役档保证「今日 T2 为空」这句诚实披露不消失,实际档保证老报告照出。
    var displayTiers: [Int] {
        Array(Set(Self.liveTiers).union(baskets.compactMap { $0.tier })).sorted()
    }
}

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
        let llm = (roleLlm ?? "").trimmingCharacters(in: .whitespaces)
        let mech = (roleMech ?? "").trimmingCharacters(in: .whitespaces)
        if roleConflict { return "LLM:\(llm.isEmpty ? "—" : llm) / 机械:\(mech.isEmpty ? "—" : mech)" }
        if !mech.isEmpty { return mech }
        if !llm.isEmpty { return llm }
        return "角色未判定"
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
    var industry: String? = nil
    var industryLift: Double? = nil
    var peers: [InfoCardBasketPeer] = []

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, basketId, basketKey, name, tier
        case driver, driverKind, whyNow, roleLlm, roleMech, roleConflict, roleReason
        case isPrimary, industry, industryLift, peers
    }

    init(available: Bool = false, unavailableReason: String? = nil, basketId: Int? = nil,
         basketKey: String = "", name: String = "", tier: Int? = nil, driver: String = "",
         driverKind: String = "", whyNow: String = "", roleLlm: String? = nil,
         roleMech: String? = nil, roleConflict: Bool = false, roleReason: String = "",
         isPrimary: Bool = false, industry: String? = nil, industryLift: Double? = nil,
         peers: [InfoCardBasketPeer] = []) {
        self.available = available; self.unavailableReason = unavailableReason
        self.basketId = basketId; self.basketKey = basketKey; self.name = name; self.tier = tier
        self.driver = driver; self.driverKind = driverKind; self.whyNow = whyNow
        self.roleLlm = roleLlm; self.roleMech = roleMech; self.roleConflict = roleConflict
        self.roleReason = roleReason; self.isPrimary = isPrimary; self.industry = industry
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

    var roleDisplay: String {
        let llm = (roleLlm ?? "").trimmingCharacters(in: .whitespaces)
        let mech = (roleMech ?? "").trimmingCharacters(in: .whitespaces)
        if roleConflict { return "LLM:\(llm.isEmpty ? "—" : llm) / 机械:\(mech.isEmpty ? "—" : mech)" }
        if !mech.isEmpty { return mech }
        if !llm.isEmpty { return llm }
        return "角色未判定"
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

    enum CodingKeys: String, CodingKey {
        case code, name, tradeDate, klineAvailable, kline, klineUnavailableReason
        case rsAvailable, rsLine, rsBenchmark, rsUnavailableReason
        case industryDivergenceAvailable, industryDivergenceLine, industry
        case industryDivergenceNote, industryDivergenceUnavailableReason
        case snapshot, k4Flags, mildBand, news, topList, market
        case basket, tags, tagsAbsent
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
         tagsAbsent: [String] = []) {
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

// MARK: - 4A.3 盘中看板

struct RetreatBrake: Codable, Equatable {
    var active: Bool
    var reason: String
}

/// 哨兵事件中文标签,后端 `_SENTINEL_LABEL` 唯一源(客户端不重译)。v1.1-G.3 补
/// `precall`/`d5exit` 两枚举(盘前校准 / D5 时间退出,标签字面见 `api/app.py::_SENTINEL_LABEL`)。
enum SentinelKind: String, Codable {
    case entry = "买点"
    case invalidation = "证伪"
    case holding = "持仓"
    case precall = "盘前校准"
    case d5exit = "D5退出"
}

struct BoardEvent: Codable, Equatable, Identifiable {
    var sentinel: String     // 买点 | 证伪 | 持仓(见 SentinelKind;未识别值原样展示,不崩)
    var code: String
    var name: String
    var eventKey: String
    var verdict: String      // 判决文案(哨兵已落库的 reason 文本,自然语言,不是模板卡)
    var ts: String

    // id 必须含 code:eventKey 是判定类型名(gap_up_invalidate 等),跨股票共用,
    // 单用它做 ForEach 身份会 id 撞车 → 全列表渲染成第一只票的内容(实机踩过)。
    var id: String { "\(code)|\(eventKey)|\(ts)" }
    var kind: SentinelKind? { SentinelKind(rawValue: sentinel) }
}

struct BoardSnapshot: Codable, Equatable {
    var tradeDate: String
    var asof: String
    var retreatBrake: RetreatBrake
    var events: [BoardEvent]

    static let empty = BoardSnapshot(tradeDate: "", asof: "",
                                     retreatBrake: RetreatBrake(active: false, reason: ""), events: [])
}

// MARK: - 4A.4 持仓(审计台账,永不代下单)

/// 回落止盈状态(§五 v1.1-B.1,服务端 `_retrace_state` 算好下发:峰值 / 回落幅度 /
/// 是否触发——判定复用 `sentinel/holding.py::check_take_profit`,客户端只展示,不重算阈值)。
struct RetraceState: Codable, Equatable {
    var peak: Double
    var retracePct: Double
    var triggered: Bool
}

/// K4 持仓牌单条命中(v1.3-② / §五 v1.3-⑥-C)。服务端 16:35 EOD 面板上对持仓票重算
/// K4 advisory 命中,客户端只展示不重算。
///  · `level`:strong(强警示,置顶醒目)| normal(普通警示,进列表)。
///  · `evidenceStrength`:price_volume(价量硬数据,强证据)| constituent(概念板块成分,
///    弱证据,标「参考」——题材持续天数依赖 `ths_member` 快照,不单独触发强警示)。
///  · 只有「level=strong ∧ evidenceStrength=price_volume」才置顶醒目展示(疑似派发/换手
///    异常等);其余(含 strong 但成分类证据、或 normal)一律降级为列表/chip 展示。
struct K4Advisory: Codable, Equatable, Identifiable {
    var code: String
    var label: String
    var level: String              // strong | normal
    var evidence: String
    var evidenceStrength: String   // price_volume | constituent

    var id: String { code }
    var isStrong: Bool { level == "strong" }
    var isPriceVolumeEvidence: Bool { evidenceStrength == "price_volume" }
    /// 置顶醒目的判据(§五 v1.3-⑥-C 硬约束,不是「strong 就置顶」——弱证据即便标了
    /// strong 也只降级展示,守 §2.4 铁律「证伪只用价量结构」)。
    var isTopBillboard: Bool { isStrong && isPriceVolumeEvidence }
}

/// v1.3-① 两档时间退出态(服务端权威判定,§2.1 第 2 条;客户端只展示,不重算净浮盈)。
/// 未识别字符串兜底 `.holding`(不误报离场——宁可少提醒,不可错误地把未知态判成「该走了」)。
enum PositionTimeExitState: Equatable {
    static let timeExitNextDayRaw = "time_exit_next_day"
    static let profitExemptRaw = "profit_exempt"
    static let hardCapExitRaw = "hard_cap_exit"
    static let holdingRaw = "holding"
    static let suspendedHoldRaw = "suspended_hold"   // v1.4-①-B(§七 P0-2)

    case timeExitNextDay   // 非浮盈,次日按计划离场
    case profitExempt      // 浮盈豁免时间退出,交回落止盈+止损管到硬上限——**持有态,非离场提示**
    case hardCapExit       // 已达浮盈硬上限(D15),次日无条件离场
    case holding           // 常规持有(K1 单档下恒为此值或 timeExitNextDay)
    // v1.4-①-B:当日无 EOD 行(停牌/数据缺口)且尚未定格 → 判向挂起,不推 D5/硬上限
    // 提醒;`dCount` 照常按交易日累计并展示。复牌当日 16:35 用复牌当日 EOD 正常定格。
    case suspendedHold

    init(_ raw: String) {
        switch raw {
        case Self.timeExitNextDayRaw: self = .timeExitNextDay
        case Self.profitExemptRaw: self = .profitExempt
        case Self.hardCapExitRaw: self = .hardCapExit
        case Self.suspendedHoldRaw: self = .suspendedHold
        default: self = .holding
        }
    }
}

/// 持仓票价格陈旧度(v1.4-①-B,§七 P0-2)。当日**无 EOD 行**时才会有值(正常票不背这个
/// 字段的负担,`Position.priceStale` 为 `nil`)——`reason` 三态:`suspended`(停牌名单
/// 命中)/ `data_gap`(全市场当日有数据但唯独这只没有)/ `unknown`(停牌名单本身拿不到,
/// 如实说不知道,绝不猜成 suspended)。**绝不静默把老价当今日价**——这个类型就是那句
/// 「静默」的解药。
struct PriceStale: Codable, Equatable {
    var staleDays: Int
    var lastCloseDate: String    // 'YYYYMMDD';回看窗口内都找不到 → ""(如实留空,不臆造)
    var reason: String           // suspended | data_gap | unknown

    var reasonLabel: String {
        switch reason {
        case "suspended": return "停牌"
        case "data_gap": return "数据缺口"
        case "unknown": return "原因未知"
        default: return reason
        }
    }
}

struct Position: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var buyPrice: Double
    var qty: Int
    var entryReason: String
    var buyDate: String      // 'YYYYMMDD',服务端字面口径(见 sentinel/positions.py)
    var price: Double        // 哨兵最近一拍 / EOD 兜底;拉不到 → 0.0(不可与"跌停 0 元"混淆,UI 需判断)
    var status: String
    var stopLine: Double     // 服务端派生 = buy×0.95(§2.1 单一常量),客户端不重算
    var stopOrderChecked: Bool
    // —— §五 v1.1-B.1/E.1 持仓生命周期派生字段(服务端算好,客户端不重算日历/阈值)——
    var dCount: Int = 1              // D 计数(买入日=D1,唯一源 sentinel/positions.py::d_count)
    /// 现役 `max_hold_days`(读 config,不硬编 5)。
    /// 🔴 **V2.2-⑤ 起可为 nil = 本版章程无时间退出条款**(`v2.2-k8`,K8 §十三:时间退出
    /// 让位主观换股权)。**取值域放宽,不是删键**。⛔ 拿 5 顶上冒充"有时间退出"是本项目
    /// 反复禁止的那类谎 —— nil 时展示层走 `timeExitDisclosure`,不显示任何 D 上限。
    var maxHoldDays: Int? = 5
    var distToStopPctServer: Double? = nil   // 服务端算好的距止损线百分比(小数,非 ×100);无实时价 → nil
    var retraceState: RetraceState? = nil
    var todayAction: String = ""     // 今日动作提示文案(D5离场/距止损/回落止盈已触发等,服务端定文案)
    // —— v1.3-① 两档时间退出(服务端按 D5 净浮盈判好下发,客户端不重算)——————————————
    /// 该单有效硬上限:非浮盈=maxHoldDays;浮盈豁免=硬上限(如 15)。
    /// 🔴 **同样自 V2.2-⑤ 起可为 nil**(章程无时间退出条款 → 根本没有"有效硬上限"这回事)。
    var maxHoldDaysEffective: Int? = 5
    var timeExitState: String = "holding"
    // —— v1.3-① 费用回显(实付,供周复盘对账用真数;nil=未录)——————————————————————
    var buyFees: Double? = nil
    var sellFees: Double? = nil
    // —— v1.4-①-B 停牌 / 无行情持仓票的显式标注(§七 P0-2)————————————————————————
    /// 当日无 EOD 行时给出「陈旧几个交易日 / 最后成交日 / 为什么」三件;当日有行 → nil
    /// (正常票不背这个字段的负担)。
    var priceStale: PriceStale? = nil
    /// K4 每日体检是否因无 EOD 行被整份跳过。**三值**:true=没体检 / false=体检过了
    /// (空 `k4Advisory` 才等于「体检过没问题」)/ nil=老快照未记录(如实说不知道,不冒充 false)。
    var k4DataUnavailable: Bool? = nil
    // —— v1.4-⑥-C 定格日 ≠ D5 显式标注(§七 P1-6)——————————————————————————————————
    /// 定格发生当时的 `dCount`;nil=尚未定格(或老快照缺记录),**不拿今天冒充定格日**。
    var timeExitLockedDay: Int? = nil
    /// = `timeExitLockedDay − maxHoldDays`,下限 0;客户端 **>0 才展示**
    /// 「定格于 D{n},晚于 D{maxHoldDays} {k} 天」。⛔ 只提示,不改判定逻辑。
    var timeExitLockedLateDays: Int = 0
    // —— v1.3-② K4 持仓牌(服务端 16:35 EOD 重算命中;老快照/刚开仓未体检 → 空数组,
    // 前向兼容不特判)——————————————————————————————————————————————————————————
    var k4Advisory: [K4Advisory] = []
    // 该持仓是否有关联决策日志(via position_id)含非空情景树待每日对照(v1.3-②-D 提醒)。
    // ⚠ 🔵-5 小审 2026-08-03 措辞订正:原注释称"勾选仍走既有 `POST /decisions/{id}/
    // scenario-outcome`"——V2-⑩-C 起 `decision_log` 停写留档,该端点与客户端对应方法
    // `setScenarioOutcome` 均已物理删除(见 `APIClient.swift:555`)。本字段现在纯只读
    // 展示「挑出来」,不再有任何写回动作,别被这句话误导去把调用接回来。
    var scenarioReviewPending: Bool = false

    /// 显式 CodingKeys(`distToStopPctServer` 与服务端字面 `distToStopPct` 改了名——避免
    /// 和下面既有的、语义不同的客户端计算属性 `distToStopPct` 撞名;其余字段名与 JSON
    /// 字面一致)。**本类型自 v1.3-⑥ 起改手写 `init(from:)`**(见下)——`maxHoldDaysEffective`/
    /// `timeExitState`/`k4Advisory`/`scenarioReviewPending` 等虽非 Optional 但要容忍旧
    /// fixture/旧快照缺键(Swift 合成 Decodable 对非 Optional 属性不会自动容忍缺键,
    /// 默认值只影响 memberwise init、不影响解码,同 `Candidate` 这一版的处理姿势)。
    enum CodingKeys: String, CodingKey {
        case id, code, name, buyPrice, qty, entryReason, buyDate, price, status, stopLine, stopOrderChecked
        case dCount, maxHoldDays, retraceState, todayAction
        case distToStopPctServer = "distToStopPct"
        case maxHoldDaysEffective, timeExitState, buyFees, sellFees, k4Advisory, scenarioReviewPending
        case priceStale, k4DataUnavailable, timeExitLockedDay, timeExitLockedLateDays
    }

    init(id: Int, code: String, name: String, buyPrice: Double, qty: Int, entryReason: String,
         buyDate: String, price: Double, status: String, stopLine: Double, stopOrderChecked: Bool,
         dCount: Int = 1, maxHoldDays: Int? = 5, distToStopPctServer: Double? = nil,
         retraceState: RetraceState? = nil, todayAction: String = "",
         maxHoldDaysEffective: Int? = 5, timeExitState: String = "holding",
         buyFees: Double? = nil, sellFees: Double? = nil,
         priceStale: PriceStale? = nil, k4DataUnavailable: Bool? = nil,
         timeExitLockedDay: Int? = nil, timeExitLockedLateDays: Int = 0,
         k4Advisory: [K4Advisory] = [], scenarioReviewPending: Bool = false) {
        self.id = id; self.code = code; self.name = name; self.buyPrice = buyPrice; self.qty = qty
        self.entryReason = entryReason; self.buyDate = buyDate; self.price = price; self.status = status
        self.stopLine = stopLine; self.stopOrderChecked = stopOrderChecked
        self.dCount = dCount; self.maxHoldDays = maxHoldDays; self.distToStopPctServer = distToStopPctServer
        self.retraceState = retraceState; self.todayAction = todayAction
        self.maxHoldDaysEffective = maxHoldDaysEffective; self.timeExitState = timeExitState
        self.buyFees = buyFees; self.sellFees = sellFees
        self.priceStale = priceStale; self.k4DataUnavailable = k4DataUnavailable
        self.timeExitLockedDay = timeExitLockedDay; self.timeExitLockedLateDays = timeExitLockedLateDays
        self.k4Advisory = k4Advisory; self.scenarioReviewPending = scenarioReviewPending
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(Int.self, forKey: .id)
        code = try c.decode(String.self, forKey: .code)
        name = try c.decode(String.self, forKey: .name)
        buyPrice = try c.decode(Double.self, forKey: .buyPrice)
        qty = try c.decode(Int.self, forKey: .qty)
        entryReason = try c.decode(String.self, forKey: .entryReason)
        buyDate = try c.decode(String.self, forKey: .buyDate)
        price = try c.decode(Double.self, forKey: .price)
        status = try c.decode(String.self, forKey: .status)
        stopLine = try c.decode(Double.self, forKey: .stopLine)
        stopOrderChecked = try c.decode(Bool.self, forKey: .stopOrderChecked)
        dCount = try c.decodeIfPresent(Int.self, forKey: .dCount) ?? 1
        // 🔴 **「缺键」与「显式 null」在这里语义相反,必须分开**(V2.2-⑤):
        //   · **缺键** = 真·老服务端 / 老 fixture(v1.1 之前根本没有这个字段)→ 按当时
        //     的单档口径补 5,老断言逐位不变;
        //   · **显式 null** = **本版章程无时间退出条款**(`v2.2-k8`)→ 如实 nil,
        //     ⛔ 不许拿 5 顶上冒充"有时间退出"。
        // `decodeIfPresent` 两种情况都返回 nil、区分不了 → 用 `contains(_:)` 判键在不在
        // (它对显式 null 返回 true)。⛔ 别"简化"回 `?? 5`,那会让新章程静默显示 D5。
        maxHoldDays = c.contains(.maxHoldDays)
            ? try c.decodeIfPresent(Int.self, forKey: .maxHoldDays)
            : 5
        distToStopPctServer = try c.decodeIfPresent(Double.self, forKey: .distToStopPctServer)
        retraceState = try c.decodeIfPresent(RetraceState.self, forKey: .retraceState)
        todayAction = try c.decodeIfPresent(String.self, forKey: .todayAction) ?? ""
        maxHoldDaysEffective = c.contains(.maxHoldDaysEffective)
            ? try c.decodeIfPresent(Int.self, forKey: .maxHoldDaysEffective)
            : maxHoldDays
        // 缺键(真正的旧服务端/旧 fixture,v1.3-① 前)→ 按旧单档口径派生(dCount>=maxHoldDays
        // 才算到期),与「服务端本该发什么」逐位一致——不是拍脑袋的"holding"兜底,而是精确
        // 复现 v1.1 单档时间退出行为,故老 fixture 的 isExitDay 断言不必因这次改动而重写。
        // ⚠ **无上限(nil)时这条派生整个不成立** → 只能是 `holding`(没有"到期"这回事)。
        timeExitState = try c.decodeIfPresent(String.self, forKey: .timeExitState)
            ?? ((maxHoldDays.map { dCount >= $0 } ?? false)
                ? PositionTimeExitState.timeExitNextDayRaw : PositionTimeExitState.holdingRaw)
        buyFees = try c.decodeIfPresent(Double.self, forKey: .buyFees)
        sellFees = try c.decodeIfPresent(Double.self, forKey: .sellFees)
        priceStale = try c.decodeIfPresent(PriceStale.self, forKey: .priceStale)
        k4DataUnavailable = try c.decodeIfPresent(Bool.self, forKey: .k4DataUnavailable)
        timeExitLockedDay = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedDay)
        timeExitLockedLateDays = try c.decodeIfPresent(Int.self, forKey: .timeExitLockedLateDays) ?? 0
        k4Advisory = try c.decodeIfPresent([K4Advisory].self, forKey: .k4Advisory) ?? []
        scenarioReviewPending = try c.decodeIfPresent(Bool.self, forKey: .scenarioReviewPending) ?? false
    }

    var hasLivePrice: Bool { price > 0 }
    var pnlPct: Double {
        guard hasLivePrice, buyPrice > 0 else { return 0 }
        return (price - buyPrice) / buyPrice * 100
    }
    var pnlAmount: Double {
        guard hasLivePrice else { return 0 }
        return (price - buyPrice) * Double(qty)
    }
    /// 距止损线百分比(正 = 尚有缓冲,负 = 已破线);无实时价 → nil,UI 不误显 0%。
    /// 客户端派生(与服务端 `distToStopPctServer` 算法一致,同一口径,仅百分比展示单位不同),
    /// 保留是因为早于 B.1 已有该计算且被既有单测覆盖;新代码可直接读 `distToStopPctServer`。
    var distToStopPct: Double? {
        guard hasLivePrice, price > 0 else { return nil }
        return (price - stopLine) / price * 100
    }
    /// 已破 -5% 止损线(展示红色警示;真实止损执行在券商条件单,系统只审计)。
    var hasBrokenStop: Bool {
        guard hasLivePrice else { return false }
        return price <= stopLine
    }

    // —— §五 v1.1-E.1/v1.3-⑥-A 展示层派生(纯视觉强度选择,文案本身来自服务端
    // `todayAction`,这里只按服务端权威 `timeExitState` 两态选颜色/是否醒目横幅,
    // 不重新推导任何领域判定,同 `hasBrokenStop` 的展示层派生先例)。

    /// 服务端两档时间退出态的展示层枚举(见 `PositionTimeExitState`)。
    var timeExitKind: PositionTimeExitState { PositionTimeExitState(timeExitState) }

    // —— V2.2-⑤ 章程按 K8 持仓原则修订:**时间退出让位主观换股权** ——————————
    //
    // 🔴 `maxHoldDaysEffective == nil` = **本版章程没有时间退出条款**(不是"读不到")。
    // ⛔ 不许显示成 `D3/D5` 这类假上限,也不许显示成 `D3/D0`。

    /// 本单是否受时间退出条款约束。
    var hasTimeExitRule: Bool { maxHoldDaysEffective != nil }

    /// D 徽标文案。无时间退出条款时**只报 D 计数**(它仍是有用的持有天数记录)。
    var dBadgeText: String {
        guard let cap = maxHoldDaysEffective else { return "D\(dCount)" }
        return "D\(dCount)/D\(cap)"
    }

    /// 无时间退出条款时那句必须说出口的话;有条款时 nil(不啰嗦)。
    var timeExitDisclosure: String? {
        guard maxHoldDaysEffective == nil else { return nil }
        return "本版章程无时间退出条款(K8:换股由你主观决定),D 计数只作记录、不构成离场提示"
    }

    /// 是否该醒目展示为「离场/到期」(两档:非浮盈到期 `timeExitNextDay` 或浮盈硬上限到期
    /// `hardCapExit`)。**`profitExempt` 不算**——它是持有态(交回落止盈+止损管到硬上限),
    /// §五 v1.3-⑥-A 明文「不要当离场提示展示」,故不能再用旧口径 `dCount >= maxHoldDays`
    /// 判定(那样会把「浮盈豁免续持到 D15」的正常单错误标红成「该走了」)。
    var isExitDay: Bool { timeExitKind == .timeExitNextDay || timeExitKind == .hardCapExit }

    var todayActionTone: NKAxisTone {
        if isExitDay { return .bad }
        if timeExitKind == .profitExempt { return .good }   // 浮盈豁免:持有态,非警示,给个正向色调
        // v1.4-①-B:判向挂起(停牌/无当日行情)——警示级但非"该走了",价格本身是陈旧的,
        // 不该被下面的距止损/回落止盈信号(基于陈旧价算出)误染成更高优先级的警示。
        if timeExitKind == .suspendedHold { return .warn }
        if retraceState?.triggered == true { return .bad }
        if let d = distToStopPctServer {
            if d <= 0 { return .bad }
            if d <= 0.02 { return .warn }
        }
        return .neutral
    }
}

// MARK: - v1.2 枚举展示层换算(服务端码 + 客户端展示层换算,沿 `nkBoardLabel` 先例;
// 未识别码原样透传,不静默瞎翻译)。自由函数用于「解码任意历史码做展示」的场景
// (如 `DecisionLog.thesisTags`);下面各 `CaseIterable` 枚举用于「录入表单的有限
// 可选项 picker」场景——两者共用同一份 label 映射,不重复定义第二份中文对照表。

func nkThesisTagLabel(_ raw: String) -> String {
    switch raw {
    case "THEME": return "题材主线"
    case "SENTIMENT_CYCLE": return "情绪周期位"
    case "CAPITAL_FLOW": return "资金流向"
    case "TECH_PATTERN": return "技术形态"
    case "NEWS": return "消息"
    default: return raw
    }
}

/// ⑤ 论点标签(v1.2-B,多选)。
enum ThesisTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case theme = "THEME"
    case sentimentCycle = "SENTIMENT_CYCLE"
    case capitalFlow = "CAPITAL_FLOW"
    case techPattern = "TECH_PATTERN"
    case news = "NEWS"

    var id: String { rawValue }
    var label: String { nkThesisTagLabel(rawValue) }
}

func nkPlaybookTagLabel(_ raw: String) -> String {
    switch raw {
    case "SWING_CHASE": return "短线追击"
    case "BREATHING_TRIAL": return "呼吸底仓试验"
    default: return raw
    }
}

/// ⑧ 打法标签(v1.2-B,单选;对应三仓 = 2 短线追击 + 1 呼吸底仓试验,§2.1 第 3 条)。
enum PlaybookTag: String, CaseIterable, Identifiable, Hashable, Codable {
    case swingChase = "SWING_CHASE"
    case breathingTrial = "BREATHING_TRIAL"

    var id: String { rawValue }
    var label: String { nkPlaybookTagLabel(rawValue) }
}

func nkScenarioActionLabel(_ raw: String) -> String {
    switch raw {
    case "BUY": return "买入"
    case "HOLD": return "持有"
    case "REDUCE": return "减仓"
    case "ABANDON": return "放弃"
    default: return raw
    }
}

/// ⑦ 应对方案·情景树的动作枚举(v1.2-B)。
enum ScenarioAction: String, CaseIterable, Identifiable, Hashable, Codable {
    case buy = "BUY", hold = "HOLD", reduce = "REDUCE", abandon = "ABANDON"

    var id: String { rawValue }
    var label: String { nkScenarioActionLabel(rawValue) }
}

func nkCloseReasonLabel(_ raw: String) -> String {
    switch raw {
    case "STOP_LOSS": return "止损"
    case "TAKE_PROFIT": return "回落止盈"
    case "TIME_EXIT": return "时间退出"
    case "INVALIDATION": return "证伪离场"
    case "MANUAL": return "主动离场"
    // —— v2.0.0(⑩-A)蓝图 §5.2 卖出快捷标签新增四码 ——————————————————————
    case "SECTOR_WEAKENING": return "板块转弱"
    // ⛔ **不许写成「止盈」**:离场参考区间不是止盈线(§2.8-C 语义红线),
    // 回落止盈才是纪律。码名与文案都要守住这条。
    case "TARGET_ZONE_REACHED": return "达到参考区间"
    case "ACTIVE_SWITCH": return "主动切换"
    case "AD_HOC": return "临时决定"
    default: return raw
    }
}

/// 离场原因(`PositionCloseIn.closeReason`)。v2.0.0(⑩-A)起**九码**:既有五码原样
/// 不动、只加不改;熔断判据「是否止损离场」只看 `STOP_LOSS`,新增四码不改任何纪律判定。
/// 不选则服务端按价格兜底判止损(见 CLAUDE.md「熔断兜底判据」坑)。
enum CloseReasonCode: String, CaseIterable, Identifiable, Hashable, Codable {
    case stopLoss = "STOP_LOSS"
    case takeProfit = "TAKE_PROFIT"
    case timeExit = "TIME_EXIT"
    case invalidation = "INVALIDATION"
    case manual = "MANUAL"
    case sectorWeakening = "SECTOR_WEAKENING"
    case targetZoneReached = "TARGET_ZONE_REACHED"
    case activeSwitch = "ACTIVE_SWITCH"
    case adHoc = "AD_HOC"

    var id: String { rawValue }
    var label: String { nkCloseReasonLabel(rawValue) }
}

/// 蓝图 §2.2「用户可选补充」七枚标签(`POST /decisions` 的 `labels`,落 `user_actions`)。
/// **服务端只存英文码**(`schemas.NoteLabelLiteral` 唯一源),中文在此换算 —— 同
/// `board`/`NewsCategory`/`CloseReasonCode` 三处的既定体例。⛔ 不另造一套中文键。
func nkNoteLabelText(_ raw: String) -> String {
    switch raw {
    case "THEME_SHIFT": return "题材切换"
    case "LEADER_REACTIVATE": return "龙头重新激活"
    case "VOLUME_BREAKOUT": return "放量突破"
    case "WEAK_TO_STRONG": return "弱转强"
    case "CORE_POSITION": return "容量中军"
    case "NEWS_CATALYST": return "消息催化"
    case "PURE_TAPE_READING": return "纯盘口判断"
    default: return raw
    }
}

enum NoteLabel: String, CaseIterable, Identifiable, Hashable, Codable {
    case themeShift = "THEME_SHIFT"
    case leaderReactivate = "LEADER_REACTIVATE"
    case volumeBreakout = "VOLUME_BREAKOUT"
    case weakToStrong = "WEAK_TO_STRONG"
    case corePosition = "CORE_POSITION"
    case newsCatalyst = "NEWS_CATALYST"
    case pureTapeReading = "PURE_TAPE_READING"

    var id: String { rawValue }
    var label: String { nkNoteLabelText(rawValue) }
}

// MARK: - v1.2-B 预注册决策日志(§五 v1.2-E.1;审计件、非下单件——本文件任何类型
// 都只是展示/编解码模型,不含任何触发下单的逻辑)。

/// ⑦ 应对方案·情景树单项。`Codable` 双向复用:解码 `DecisionOut.contingencyScenarios`
/// 时用,构造 `POST /decisions`·`revise` 请求体时也用(服务端 `ContingencyScenarioIn`/
/// `ContingencyScenarioOut` 形状一致,不必两份类型)。
struct ContingencyScenario: Codable, Equatable {
    var scenario: String
    var trigger: String
    var action: String        // BUY/HOLD/REDUCE/ABANDON,服务端码
    var matched: Bool = false

    var actionLabel: String { nkScenarioActionLabel(action) }
}

/// 对齐 `DecisionOut`(逐字段,见「v1.2 客户端契约清单」)。字段名与服务端 JSON
/// 完全一致,直接 `Codable` 解码,不需要私有 wire DTO 中转(同 `Position`/
/// `BoardEvent`/`Position` 的直接解码先例)。
struct DecisionLog: Codable, Equatable, Identifiable {
    var id: Int
    var code: String
    var name: String
    var createdAt: String
    var whyBuy: String
    var whyEntryPrice: String
    var targetPrice: Double?
    var exitLow: Double?
    var exitHigh: Double?
    var thesisTags: [String]
    var invalidation: String
    var contingencyScenarios: [ContingencyScenario]
    var playbookTag: String
    var plannedPrice: Double?
    var plannedQty: Int?
    var status: String                // pending | filled | cancelled | expired
    var positionId: Int?
    var revisionOf: Int?
    /// ⑨ 最高追价上限(v1.4-⑤-B,需求 2 补充)。相对昨收百分比,如 `3.0`=+3%(**不是
    /// 小数 0.03**);允许负值(只在低开时买);`nil` = 显式选择"不设上限",**或**老行
    /// (建于本字段前)——两者在存储层无法区分,是迁移引入新必填字段时不可避免的历史
    /// 模糊,不影响新行起的强制语义。与 `plannedPrice`("我打算挂多少价")是两回事,
    /// 不要合并展示:本字段回答的是"开盘冲多高我就放弃、盘中不追补"。
    var maxChasePct: Double? = nil

    var thesisTagLabels: [String] { thesisTags.map(nkThesisTagLabel) }
    var playbookTagLabel: String { nkPlaybookTagLabel(playbookTag) }
    /// 三仓 = 2 短线追击 + 1 呼吸底仓试验(§2.1 第 3 条)——呼吸台账入口露出规则
    /// (§五 v1.2-E.4)据此判断,不新存第二份「是否呼吸仓」标记。
    var isBreathingTrial: Bool { playbookTag == PlaybookTag.breathingTrial.rawValue }
}

// MARK: - v1.4-⑦-A 挂单未成交追踪(§五 v1.4-⑦-A,§七 P3-12)。领域数据自 v1.3-④ 起已在攒
// (`report/pending_track.py`),本节把 `GET /decisions/{id}/track` 已有数据接上展示。

struct DecisionTrackRow: Codable, Equatable, Identifiable {
    var tradeDate: String
    var dOffset: Int
    var close: Double
    var retFromPlan: Double? = nil    // nil = 该决策未设 plannedPrice,不臆造

    var id: String { tradeDate }
}

/// `rows` 按 `tradeDate` 升序,**可能为空**——该决策尚未攒到任何追踪快照(刚创建、还没
/// 到下一交易日)不等于"没有这条决策"(那是 404),这是合法的 200 空态,UI 须展示
/// "暂未攒到数据"而非当作错误处理。
struct DecisionTrack: Codable, Equatable {
    var status: String
    var planPrice: Double? = nil
    var rows: [DecisionTrackRow] = []
}

// ⚠ **`CircuitEpisode` / `CircuitState` 两个 DTO 已于 v2.3.0 物理删除**(两步淘汰第二步)。
//
// 熔断三件机制在 V2.2-⑤-B 随用户裁定 #8 整体退役;当时按零删键铁律(〇b-3)让服务端
// `PositionsOut.circuit` 恒发空态过渡一版,客户端两个 DTO 也一并留着。本版服务端删键、
// 客户端删 DTO,**同一版落地**。
// 🔴 **删得掉的判据**:历代客户端 `/positions` 一律解进 `PositionsListResponse { holdings }`,
// **没有任何一版声明过 `circuit` 字段** —— 2.0.0 那台 iPhone 读的是**独立端点**
// `GET /circuit`(自 V2.2 起 404,与本键无关)。⛔ 别把这条当成「零删键铁律可以不守」的先例。
// ⛔ 更不许以任何名字把熔断状态位加回来(§五 〇b-7,用户裁定 #8:「我不需要你替我做决定」)。

// MARK: - v1.1-B.3/v1.2-E.5 一键补录预填(区间双档,替换 v1.1 的单 `qty`)
//
// `EntrySuggestionOut` 改区间:`qtyHigh`/`capCeil` = 现役 `single_cap` 违纪判定
// 上限对应手数/金额(**非推荐值**);`qtyLow`/`capFloor` = 半仓保守下沿。客户端只
// 展示两档供参考,不替用户拍单笔金额(§2.1 第 3 条三仓制「单笔金额不定死」)。

struct EntrySuggestionRange: Codable, Equatable {
    var code: String
    var price: Double
    var qtyLow: Int
    var qtyHigh: Int
    var capFloor: Double
    var capCeil: Double
    var stopLine: Double
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

    var id: String { kind }
    /// 未识别 `level` 原样透传(**照常显示**,⛔ 不静默丢弃这一行)。
    var levelLabel: String { nkPushLevelLabel(level) }

    enum CodingKeys: String, CodingKey { case kind, level, label, enabled }

    init(kind: String, level: String, label: String, enabled: Bool) {
        self.kind = kind; self.level = level; self.label = label; self.enabled = enabled
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


// MARK: - 4D 周复盘工作台(对账三查 + 单周统计,§五 阶段4D)
//
// 后端 `neckline/api/schemas.py` 的 `WeeklyReviewOut.result`/`ReviewGetOut.result`
// 在 API 层是 `Dict[str, Any]` 透传(领域形状唯一源 = `neckline/review/reconcile.py`
// 的 `weekly_review_dict()`,同 `ReportOut.sentiment/sectors` 的透传惯例)——客户端
// 仍按已知稳定形状声明强类型 Codable(同 `SentimentSnapshot`/`SectorSnapshot` 先例),
// 便于渲染表格,不必满页 `[String: Any]` 手动取值。

struct ReviewRoundTrip: Codable, Equatable, Identifiable {
    var tsCode: String
    var name: String
    var buyDate: String
    var buyPrice: Double
    var qty: Int
    var buyAmount: Double
    var fees: Double
    var sellDate: String?
    var sellPrice: Double?
    var closed: Bool
    var netPnl: Double?
    var pnlPct: Double?

    var id: String { "\(tsCode)-\(buyDate)-\(sellDate ?? "open")-\(qty)-\(buyPrice)" }
}

struct ReviewPlanCheck: Codable, Equatable, Identifiable {
    var tsCode: String
    var name: String
    var tradeDate: String
    var price: Double
    var qty: Int
    var amount: Double
    var planStatus: String
    var ledgerStatus: String

    var id: String { "\(tsCode)-\(tradeDate)-\(price)" }
    var isOffPlan: Bool { planStatus.hasPrefix("计划外") }
    var isLedgerMissing: Bool { ledgerStatus.hasPrefix("台账缺失") }
    var isLedgerMismatch: Bool { ledgerStatus.hasPrefix("台账记录价格不符") }
}

/// 止损纪律分类(后端字面常量,`neckline.review.reconcile` 的四个模块常量,
/// 唯一源不重译阈值——只做展示层四常量换算,同 `Candidate.boardLabel` 先例)。
enum StopDisciplineKind: String, Codable {
    case breached = "breached"
    case keptStop = "kept_stop"
    case notTriggered = "not_triggered"
    case notApplicable = "not_applicable"

    var label: String {
        switch self {
        case .breached: return "破止损未离场"
        case .keptStop: return "止损执行到位"
        case .notTriggered: return "未触及止损"
        case .notApplicable: return "不适用"
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .breached: return .bad
        case .keptStop: return .good
        case .notTriggered: return .neutral
        case .notApplicable: return .neutral
        }
    }
}

struct ReviewStopDisciplineEntry: Codable, Equatable, Identifiable {
    var roundTrip: ReviewRoundTrip
    var classification: String
    var note: String

    var id: String { roundTrip.id + classification }
    var kind: StopDisciplineKind? { StopDisciplineKind(rawValue: classification) }
}

struct ReviewWeeklyStats: Codable, Equatable {
    var closedCount: Int
    var openCount: Int
    var winRate: Double
    var profitFactor: Double?      // nil = 本周无亏损回合(数学上的无穷,后端已转 null)
    var profitLossRatio: Double?
    var totalFees: Double
    var grossPnl: Double
    var realizedPnl: Double
    var realizedLoss: Double       // 只累加亏损(§2.1 第4条口径),恒 <= 0
}

/// 周内一段「同一版章程治下」的区间(v1.4-⑥-A,§七 P1-4)。`start=nil` = 自周初起的
/// 那一段;时刻为北京时间 `'YYYY-MM-DD HH:MM'`。
struct ReviewCharterSegment: Codable, Equatable, Identifiable {
    var version: String
    var start: String? = nil
    var tradeCount: Int = 0

    var id: String { "\(version)|\(start ?? "week-start")|\(tradeCount)" }
}

/// 周内发生的一次章程切换(= `strategy_versions` 的一次激活落在本周窗口内)。
struct ReviewCharterSwitch: Codable, Equatable, Identifiable {
    var at: String            // 'YYYY-MM-DD HH:MM' 北京时间
    var fromVersion: String
    var toVersion: String
    var note: String = ""

    var id: String { at }
}

struct ReviewWeeklyResult: Codable, Equatable {
    var week: String
    var weekStart: String
    var weekEnd: String
    /// v1.4-⑥-A:该周**周初标签**(`brain.config_governing_for_week`,判据「激活日 <
    /// week_start」)——**不可再当"整周按这版判"展示**,该周若发生过章程切换,逐笔实际
    /// 按哪版判见 `charterSegments`/`charterSwitches`。旧结果(建于本字段前)读回空串。
    var strategyVersion: String = ""
    var charterSegments: [ReviewCharterSegment] = []
    var charterSwitches: [ReviewCharterSwitch] = []
    var roundTrips: [ReviewRoundTrip]
    var closedRoundTrips: [ReviewRoundTrip]
    var planChecks: [ReviewPlanCheck]
    var disciplineViolations: [String]
    var stopDiscipline: [ReviewStopDisciplineEntry]
    var stats: ReviewWeeklyStats?
    var forcedReview: Bool
    var forcedReviewReason: String

    /// `result` 是 `reviews.result_json` **写入当时**冻住的快照(不像 `intelRank`/
    /// `infoCard` 那样每次响应都由服务端重构、天然带全新字段默认值)——真实历史周报
    /// (建于 v1.4-⑥-A 之前)落库时压根没有 `strategyVersion`/`charterSegments`/
    /// `charterSwitches` 三键,**必须手写容错解码**,否则老周报直接读不出来。
    enum CodingKeys: String, CodingKey {
        case week, weekStart, weekEnd, strategyVersion, charterSegments, charterSwitches
        case roundTrips, closedRoundTrips, planChecks, disciplineViolations, stopDiscipline
        case stats, forcedReview, forcedReviewReason
    }

    init(week: String, weekStart: String, weekEnd: String, strategyVersion: String = "",
         charterSegments: [ReviewCharterSegment] = [], charterSwitches: [ReviewCharterSwitch] = [],
         roundTrips: [ReviewRoundTrip], closedRoundTrips: [ReviewRoundTrip],
         planChecks: [ReviewPlanCheck], disciplineViolations: [String],
         stopDiscipline: [ReviewStopDisciplineEntry], stats: ReviewWeeklyStats?,
         forcedReview: Bool, forcedReviewReason: String) {
        self.week = week; self.weekStart = weekStart; self.weekEnd = weekEnd
        self.strategyVersion = strategyVersion
        self.charterSegments = charterSegments; self.charterSwitches = charterSwitches
        self.roundTrips = roundTrips; self.closedRoundTrips = closedRoundTrips
        self.planChecks = planChecks; self.disciplineViolations = disciplineViolations
        self.stopDiscipline = stopDiscipline; self.stats = stats
        self.forcedReview = forcedReview; self.forcedReviewReason = forcedReviewReason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        week = try c.decode(String.self, forKey: .week)
        weekStart = try c.decode(String.self, forKey: .weekStart)
        weekEnd = try c.decode(String.self, forKey: .weekEnd)
        strategyVersion = try c.decodeIfPresent(String.self, forKey: .strategyVersion) ?? ""
        charterSegments = try c.decodeIfPresent([ReviewCharterSegment].self, forKey: .charterSegments) ?? []
        charterSwitches = try c.decodeIfPresent([ReviewCharterSwitch].self, forKey: .charterSwitches) ?? []
        roundTrips = try c.decode([ReviewRoundTrip].self, forKey: .roundTrips)
        closedRoundTrips = try c.decode([ReviewRoundTrip].self, forKey: .closedRoundTrips)
        planChecks = try c.decode([ReviewPlanCheck].self, forKey: .planChecks)
        disciplineViolations = try c.decode([String].self, forKey: .disciplineViolations)
        stopDiscipline = try c.decode([ReviewStopDisciplineEntry].self, forKey: .stopDiscipline)
        stats = try c.decodeIfPresent(ReviewWeeklyStats.self, forKey: .stats)
        forcedReview = try c.decode(Bool.self, forKey: .forcedReview)
        forcedReviewReason = try c.decode(String.self, forKey: .forcedReviewReason)
    }
}

struct WeeklyReviewEntry: Codable, Equatable, Identifiable {
    var week: String
    var result: ReviewWeeklyResult
    var material: String

    var id: String { week }
}

struct ReviewUploadResponse: Codable, Equatable {
    var ok: Bool
    var weeks: [WeeklyReviewEntry]
    var parseWarnings: [String]
    var dataWarnings: [String]
    var sheetFormats: [String: String]
}

struct ReviewGetResponse: Codable, Equatable {
    var ok: Bool
    var found: Bool
    var week: String
    var generatedAt: String
    var result: ReviewWeeklyResult?
    var material: String
}

// ══════════════════════════════════════════════════════════════════════════
// MARK: - V2.1-⑤/⑦ 复盘板块:累计页五段 + 校准移交件(**B 类:含冻结产物内容**)
// ══════════════════════════════════════════════════════════════════════════
//
// 🔴 **为什么整族手写 `init(from:)` + `decodeIfPresent`**:这两条端点回的不是"服务端
// 每次重拼的视图",而是**已经落盘冻住的产物原文**(周度校准报告 JSON / `reviews.result_json`
// / 画像表行)透传出来的 —— 服务端升级**不会**给老产物补新键(CLAUDE.md 落库快照两类论
// 的 B 类)。合成 `Decodable` 对非 Optional 属性「有默认值也不容忍缺键」,一旦某期老产物
// 缺一个键,**整页复盘直接解不出**。

/// 复盘板块「累计」页里的**一段**(V2.1-⑤,五段形状统一)。
///
/// 🔴 **三态读法,⛔ 不许拿一个总开关罩住五段**:
///   · **有**   → `available == true` + 有内容;
///   · **没有** → `available == true` + 空内容(该段自己的空态文案说清为什么空);
///   · **没取到** → `available == false` + `unavailableReason`(⛔ 不许拿空数组冒充)。
///
/// ⚠ **画像段与对账段的空态服务端刻意判得不一样,客户端也必须分开渲染,⛔ 别"统一"**:
/// 画像缺席 = **系统自己那一步没跑**(周度批算未运行)→ 那是**「没看」**→ `available=false`;
/// 对账缺席 = 输入(券商交割单)**只能由用户给**、系统查过表确实没有 → 那是**「没有」**
/// → `available=true` + `detail.found == false`。两者给用户的动作完全不同(等系统 vs 去上传)。
struct ReviewSegment: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var label: String = ""
    /// 该段的时点标识:画像期 / ISO 周 / 校准窗口(`20260720→20260724`)。
    var asOf: String = ""
    /// **原样透传领域形状**(同 `WeeklyReviewOut.result` / `EvalWeeklyOut.result` 惯例)。
    var items: [NKJSON] = []
    var detail: NKJSON = .object([:])

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, label, asOf, items, detail
    }

    init(available: Bool = false, unavailableReason: String? = nil, label: String = "",
         asOf: String = "", items: [NKJSON] = [], detail: NKJSON = .object([:])) {
        self.available = available; self.unavailableReason = unavailableReason
        self.label = label; self.asOf = asOf; self.items = items; self.detail = detail
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        asOf = try c.decodeIfPresent(String.self, forKey: .asOf) ?? ""
        items = try c.decodeIfPresent([NKJSON].self, forKey: .items) ?? []
        detail = try c.decodeIfPresent(NKJSON.self, forKey: .detail) ?? .object([:])
    }

    /// 对账段专用:`false` = **这周没有**(不是"没查")。⚠ 只在 `available == true` 时
    /// 才读得出意思;`available == false` 时该段根本没查成,别拿它当"没有"。
    var found: Bool? { detail["found"]?.boolValue }
    /// 该段可直接渲染的一句话(服务端给的空态说明,如「本周尚未上传交割单 —— …」)。
    var note: String? { detail["note"]?.stringValue }
}

/// 复盘板块「累计」页的聚合读(V2.1-⑤,`GET /review/overview`)。
///
/// **零现算**:五段全部读**已冻结 / 已落盘**的产物(校准报告由离线周度作业算好落盘)。
/// 🔴 **本端点一律不 404**(空态走各段 `available=false`)→ 客户端**不需要**为它加任何
/// `mapReason` case(V2.1 零新增 reason 字符串)。
struct ReviewOverview: Codable, Equatable {
    var weekStart: String = ""
    var weekEnd: String = ""
    /// ISO 周键(`YYYY-Www`),对账段按它取。
    var weekKey: String = ""
    var calibration: ReviewSegment = ReviewSegment()
    var preference: ReviewSegment = ReviewSegment()
    var capability: ReviewSegment = ReviewSegment()
    var reconcile: ReviewSegment = ReviewSegment()
    var observations: ReviewSegment = ReviewSegment()
    // —— V2.2-④ 新增三段(**同样各自 `available` + `unavailableReason`**;
    //    ⛔ 不许被上面五段的任何一段罩住 —— 它们是八件互不相干的事)——
    var selectionClock: ReviewSegment = ReviewSegment()
    var tradeClock: ReviewSegment = ReviewSegment()
    var iterationSuggestions: ReviewSegment = ReviewSegment()

    enum CodingKeys: String, CodingKey {
        case weekStart, weekEnd, weekKey, calibration, preference, capability
        case reconcile, observations
        case selectionClock, tradeClock, iterationSuggestions
    }

    init(weekStart: String = "", weekEnd: String = "", weekKey: String = "",
         calibration: ReviewSegment = ReviewSegment(), preference: ReviewSegment = ReviewSegment(),
         capability: ReviewSegment = ReviewSegment(), reconcile: ReviewSegment = ReviewSegment(),
         observations: ReviewSegment = ReviewSegment(),
         selectionClock: ReviewSegment = ReviewSegment(),
         tradeClock: ReviewSegment = ReviewSegment(),
         iterationSuggestions: ReviewSegment = ReviewSegment()) {
        self.weekStart = weekStart; self.weekEnd = weekEnd; self.weekKey = weekKey
        self.calibration = calibration; self.preference = preference
        self.capability = capability; self.reconcile = reconcile; self.observations = observations
        self.selectionClock = selectionClock; self.tradeClock = tradeClock
        self.iterationSuggestions = iterationSuggestions
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        weekStart = try c.decodeIfPresent(String.self, forKey: .weekStart) ?? ""
        weekEnd = try c.decodeIfPresent(String.self, forKey: .weekEnd) ?? ""
        weekKey = try c.decodeIfPresent(String.self, forKey: .weekKey) ?? ""
        calibration = try c.decodeIfPresent(ReviewSegment.self, forKey: .calibration) ?? ReviewSegment()
        preference = try c.decodeIfPresent(ReviewSegment.self, forKey: .preference) ?? ReviewSegment()
        capability = try c.decodeIfPresent(ReviewSegment.self, forKey: .capability) ?? ReviewSegment()
        reconcile = try c.decodeIfPresent(ReviewSegment.self, forKey: .reconcile) ?? ReviewSegment()
        observations = try c.decodeIfPresent(ReviewSegment.self, forKey: .observations) ?? ReviewSegment()
        selectionClock = try c.decodeIfPresent(ReviewSegment.self, forKey: .selectionClock) ?? ReviewSegment()
        tradeClock = try c.decodeIfPresent(ReviewSegment.self, forKey: .tradeClock) ?? ReviewSegment()
        iterationSuggestions = try c.decodeIfPresent(ReviewSegment.self,
                                                     forKey: .iterationSuggestions) ?? ReviewSegment()
    }
}

// MARK: - V2.2-④-D 修改建议四分类(K8 §十七;**只给建议,⛔ 零写回**)
//
// 🔴 **`klass == nil` + `klassStatus == "thresholds_undecided"` 是设计中的状态,不是缺陷**:
// K8 §十七 只给了定性描述(保留 / 观察 / 降权 / 淘汰),**没有给「多少样本算够」
// 「差多少算失效」这两个数**;它们必须由用户拍板、经四道闸进骨架包才生效。
// ⛔ **界面上绝不许把它显示成「暂无建议」或空白** —— 那会把「还没决定」讲成「没问题」;
// ⛔ 也不许渲染成「观察」——「还没决定」与「样本不足」是两件事(服务端 docstring 原话)。

/// 四分类码 → 中文(唯一源 = 服务端 `eval/iteration.py::KLASS_LABELS`;
/// 服务端已在 `klassLabel` 里下发,本函数只在那个键缺席时兜底)。
func nkIterationKlassLabel(_ raw: String) -> String {
    switch raw {
    case "keep": return "保留 · 持续有效"
    case "observe": return "观察 · 样本不足"
    case "downweight": return "降权 · 辅助有效"
    case "retire": return "淘汰 · 持续失效"
    default: return raw
    }
}

func nkIterationKlassTone(_ raw: String) -> NKAxisTone {
    switch raw {
    case "keep": return .good
    case "observe": return .neutral
    case "downweight": return .warn
    case "retire": return .bad
    default: return .neutral
    }
}

/// 四分类建议一行(`ReviewSegment.items` 的一条,**自由结构原样透传** → 这里只做投影)。
struct IterationSuggestion: Identifiable, Equatable {
    let raw: NKJSON
    /// 因素标识(`dimension=value`,如 `gate=position:ok`)。
    var factor: String { raw["factor"]?.stringValue ?? "—" }
    var n: Int { raw["n"]?.intValue ?? 0 }
    var klass: String? { raw["klass"]?.stringValue }
    var klassStatus: String? { raw["klassStatus"]?.stringValue }
    /// 服务端给的中文名优先(`klassLabel`),缺席才用客户端换算。
    var klassLabel: String? {
        raw["klassLabel"]?.stringValue ?? klass.map(nkIterationKlassLabel)
    }
    var klassTone: NKAxisTone { klass.map(nkIterationKlassTone) ?? .neutral }
    /// 服务端写好的整句建议(**分界线未定时,这句话里就写着缺哪两个数**)。⛔ 原样展示。
    var suggestion: String { raw["suggestion"]?.stringValue ?? "" }
    var engineCode: String? { raw["engineCode"]?.stringValue }
    var engineVersion: String? { raw["engineVersion"]?.stringValue }
    var accuracy: Double? { raw["accuracy"]?.doubleValue }
    var delta: Double? { raw["delta"]?.doubleValue }
    var placeboEdge: String? { raw["placeboEdge"]?.stringValue }

    /// 🔴 **分界线还没拍板**。⛔ 界面必须显式说出这件事。
    var thresholdsUndecided: Bool { klass == nil && klassStatus == "thresholds_undecided" }

    var id: String { "\(engineCode ?? "")|\(engineVersion ?? "")|\(factor)" }
}

/// 校准移交件(V2.1-⑤,`GET /review/handoff`)——一份能**直接交给策略台**的 markdown。
///
/// **`available == false` 的两种成因文案服务端已分开写在 `unavailableReason` 里**
/// (① 一期产物都还没有 = **会自愈**;② 指定窗口的产物读不出 = **不会自愈**),
/// ⛔ 客户端原样展示那句话,别自己再合并成一句「暂不可用」。
struct ReviewHandoff: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var windowFrom: String = ""
    var windowTo: String = ""
    var generatedAt: String = ""
    /// 那份文档 §① 的数字版(`tradingDays` / `baskets` / `strata` / `preferenceRows` / …)。
    var sampleN: [String: Int] = [:]
    var markdown: String = ""

    enum CodingKeys: String, CodingKey {
        case available, unavailableReason, windowFrom, windowTo, generatedAt, sampleN, markdown
    }

    init(available: Bool = false, unavailableReason: String? = nil, windowFrom: String = "",
         windowTo: String = "", generatedAt: String = "", sampleN: [String: Int] = [:],
         markdown: String = "") {
        self.available = available; self.unavailableReason = unavailableReason
        self.windowFrom = windowFrom; self.windowTo = windowTo; self.generatedAt = generatedAt
        self.sampleN = sampleN; self.markdown = markdown
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        windowFrom = try c.decodeIfPresent(String.self, forKey: .windowFrom) ?? ""
        windowTo = try c.decodeIfPresent(String.self, forKey: .windowTo) ?? ""
        generatedAt = try c.decodeIfPresent(String.self, forKey: .generatedAt) ?? ""
        sampleN = try c.decodeIfPresent([String: Int].self, forKey: .sampleN) ?? [:]
        markdown = try c.decodeIfPresent(String.self, forKey: .markdown) ?? ""
    }

    /// 窗口显示名;两端都空时给一句诚实的占位(⛔ 不显示 "→")。
    var windowLabel: String {
        guard !windowFrom.isEmpty || !windowTo.isEmpty else { return "窗口未知" }
        return "\(windowFrom) → \(windowTo)"
    }

    /// 导出文件名(`ShareLink` / macOS 存盘共用,**双端同一个名字**)。
    var suggestedFilename: String {
        let w = windowFrom.isEmpty && windowTo.isEmpty ? "unknown" : "\(windowFrom)_\(windowTo)"
        return "Neckline_校准移交件_\(w).md"
    }
}

/// 观察项一条(移交件与累计页共用的读取视图;服务端 `HANDOFF_OBSERVATIONS` 静态登记册)。
/// **只读透传,⛔ 客户端不改写 status、不给"建议"** —— 它是**等证据的策略问题清单**,
/// 攒够样本后由用户带去策略台,不是待办事项。
struct ReviewObservation: Identifiable, Equatable {
    let raw: NKJSON
    var id: String { obsId }

    var obsId: String { raw["id"]?.stringValue ?? "" }
    var title: String { raw["title"]?.stringValue ?? "" }
    var question: String { raw["question"]?.stringValue ?? "" }
    var evidenceNeeded: String { raw["evidence_needed"]?.stringValue ?? "" }
    var status: String { raw["status"]?.stringValue ?? "" }
}

// MARK: - V2.2-② 行情状态层(`GET /market-regime`,D0 盘后三态)
//
// 🔴 **三条硬边界**(服务端 docstring 原文):**只读、零现算、一律不 404** ——
// 缺行 / 表空 / 参数非法一律 200 + `available=false` + 自由文本原因。
// ⛔ **`available == false` 既不是错误、也不是「没风险」**:它是「我们今天没算出
// 行情状态」。展示处必须把 `unavailableReason` 那句话原样说出口(§3.8 诚实披露)。
//
// ⚠ **纯展示、⛔ 零动作**:行情状态**不改变任何持仓判定、不触发任何提醒**
// (§五 〇b-7:不许留「建议今天别开仓」这类自动状态位)。

/// `market_regime_daily` 一行。`inputs` / `strengthening` / `weakening` 是**原样透传**
/// 的自由结构(五维各自带 `available`/`unavailable_reason` 双位);`regimeLabel` 由
/// **服务端**给人读名(唯一源 `scan/regime.py::REGIME_LABELS`)——⛔ 客户端不另建
/// 一份中文映射,那会在服务端改名时静默显示旧名。
struct MarketRegimeDay: Codable, Equatable {
    var tradeDate: String = ""
    /// `trend_continuation` | `high_divergence` | `rotation_confirmed`。
    var regime: String = ""
    var regimeLabel: String = ""
    var regimeReason: String = ""
    var inputs: NKJSON = .object([:])
    var strengthening: [NKJSON] = []
    var weakening: [NKJSON] = []
    var skeletonVersion: String = ""
    var computedAt: String = ""

    enum CodingKeys: String, CodingKey {
        case tradeDate, regime, regimeLabel, regimeReason, inputs
        case strengthening, weakening, skeletonVersion, computedAt
    }

    init(tradeDate: String = "", regime: String = "", regimeLabel: String = "",
         regimeReason: String = "", inputs: NKJSON = .object([:]),
         strengthening: [NKJSON] = [], weakening: [NKJSON] = [],
         skeletonVersion: String = "", computedAt: String = "") {
        self.tradeDate = tradeDate; self.regime = regime; self.regimeLabel = regimeLabel
        self.regimeReason = regimeReason; self.inputs = inputs
        self.strengthening = strengthening; self.weakening = weakening
        self.skeletonVersion = skeletonVersion; self.computedAt = computedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        regime = try c.decodeIfPresent(String.self, forKey: .regime) ?? ""
        regimeLabel = try c.decodeIfPresent(String.self, forKey: .regimeLabel) ?? ""
        regimeReason = try c.decodeIfPresent(String.self, forKey: .regimeReason) ?? ""
        inputs = try c.decodeIfPresent(NKJSON.self, forKey: .inputs) ?? .object([:])
        strengthening = try c.decodeIfPresent([NKJSON].self, forKey: .strengthening) ?? []
        weakening = try c.decodeIfPresent([NKJSON].self, forKey: .weakening) ?? []
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion) ?? ""
        computedAt = try c.decodeIfPresent(String.self, forKey: .computedAt) ?? ""
    }

    /// 人读名兜底:服务端没给 `regimeLabel` 时原样显示英文码(⛔ 不瞎翻译)。
    var displayLabel: String { regimeLabel.isEmpty ? regime : regimeLabel }

    /// 三态着色。**「切换确认」不是坏事、「趋势延续」也不是买入背书** —— 这里的颜色
    /// 只表达「市场结构有多不稳」,⛔ 不是行情看多看空的信号(§2.8-C)。
    var tone: NKAxisTone {
        switch regime {
        case "trend_continuation": return .good
        case "high_divergence": return .bad
        case "rotation_confirmed": return .warn
        default: return .neutral
        }
    }

    /// 五维里**没算出来**的那几维(`inputs.<dim>.available == false`)。
    /// 🔴 界面要把它说出口:缺维不是「这一维没问题」。
    var missingDims: [String] {
        (inputs.objectValue ?? [:])
            .filter { $0.value["available"]?.boolValue == false }
            .keys.sorted()
    }
}

/// `GET /market-regime` 响应。`day` = 单日查询;`days` = 区间查询(本版只用 `day`)。
struct MarketRegime: Codable, Equatable {
    var available: Bool = false
    var unavailableReason: String? = nil
    var day: MarketRegimeDay? = nil
    var days: [MarketRegimeDay] = []

    static let empty = MarketRegime()

    enum CodingKeys: String, CodingKey { case available, unavailableReason, day, days }

    init(available: Bool = false, unavailableReason: String? = nil,
         day: MarketRegimeDay? = nil, days: [MarketRegimeDay] = []) {
        self.available = available; self.unavailableReason = unavailableReason
        self.day = day; self.days = days
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        available = try c.decodeIfPresent(Bool.self, forKey: .available) ?? false
        unavailableReason = try c.decodeIfPresent(String.self, forKey: .unavailableReason)
        day = try c.decodeIfPresent(MarketRegimeDay.self, forKey: .day)
        days = try c.decodeIfPresent([MarketRegimeDay].self, forKey: .days) ?? []
    }
}

// MARK: - V2.2-④ 双时钟(选股时钟 / 交易时钟)
//
// 🔴 **两个时钟问的是两件事,⛔ 不合并**(K8 §十四):
//   · **选股时钟** = 「这批票选得对不对」—— 覆盖 D0 **全部** T1/T2,
//     **与用户买没买无关**,D1 收盘统一验证一次后**结案**;
//   · **交易时钟** = 「这笔买卖做得怎么样」—— **启动唯一条件 = 实际买入**,全部离场后结案。
// ⛔ 客户端文案不许把选股时钟写成「你关注的篮子」之类 —— 那会把覆盖域讲小。
//
// ⚠ 两者都是 **B 类冻结快照**(`selection_clock.mech_json` / `trade_clock.final_json`
// 写入当时冻住、不随服务端升级补键)→ DTO **必须手写 `init(from:)`**(V2-⑮ 铁律)。

/// 一篮的选股时钟**结案件**。`tierAccuracy` 是 ⑦-b **四态原样**
/// (`verified`/`partial`/`unclear`/`falsified`)加两个"没判"码 —— **⛔ 不是 0/1 的
/// 对错**,折成正确率是周度侧的事,客户端不折。
struct SelectionClock: Codable, Equatable, Identifiable {
    var basketId: Int = 0
    var d0Date: String = ""
    var d1Date: String = ""
    var coveredTier: Int = 0
    /// D0 当天的行情状态;**nil = 当日无行**(如实,⛔ 不猜)。
    var regimeAtD0: String? = nil
    var tierAccuracy: String? = nil
    /// 未触发原因(触发了则 nil)。
    var untriggeredReason: String? = nil
    var closedAt: String = ""
    var skeletonVersion: String = ""
    var verificationRulesetVersion: String = ""
    /// 引擎归因快照(裁定 #9 单篮子单引擎 → 两键)。
    var engineBreakdown: NKJSON = .object([:])
    /// 九项验证内容(**顺序即 K8 原文顺序**;每项自带 available/source)。原样透传。
    var mech: NKJSON = .object([:])

    var id: Int { basketId }

    enum CodingKeys: String, CodingKey {
        case basketId, d0Date, d1Date, coveredTier, regimeAtD0, tierAccuracy
        case untriggeredReason, closedAt, skeletonVersion, verificationRulesetVersion
        case engineBreakdown, mech
    }

    init(basketId: Int = 0, d0Date: String = "", d1Date: String = "", coveredTier: Int = 0,
         regimeAtD0: String? = nil, tierAccuracy: String? = nil, untriggeredReason: String? = nil,
         closedAt: String = "", skeletonVersion: String = "",
         verificationRulesetVersion: String = "", engineBreakdown: NKJSON = .object([:]),
         mech: NKJSON = .object([:])) {
        self.basketId = basketId; self.d0Date = d0Date; self.d1Date = d1Date
        self.coveredTier = coveredTier; self.regimeAtD0 = regimeAtD0
        self.tierAccuracy = tierAccuracy; self.untriggeredReason = untriggeredReason
        self.closedAt = closedAt; self.skeletonVersion = skeletonVersion
        self.verificationRulesetVersion = verificationRulesetVersion
        self.engineBreakdown = engineBreakdown; self.mech = mech
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId) ?? 0
        d0Date = try c.decodeIfPresent(String.self, forKey: .d0Date) ?? ""
        d1Date = try c.decodeIfPresent(String.self, forKey: .d1Date) ?? ""
        coveredTier = try c.decodeIfPresent(Int.self, forKey: .coveredTier) ?? 0
        regimeAtD0 = try c.decodeIfPresent(String.self, forKey: .regimeAtD0)
        tierAccuracy = try c.decodeIfPresent(String.self, forKey: .tierAccuracy)
        untriggeredReason = try c.decodeIfPresent(String.self, forKey: .untriggeredReason)
        closedAt = try c.decodeIfPresent(String.self, forKey: .closedAt) ?? ""
        skeletonVersion = try c.decodeIfPresent(String.self, forKey: .skeletonVersion) ?? ""
        verificationRulesetVersion = try c.decodeIfPresent(
            String.self, forKey: .verificationRulesetVersion) ?? ""
        engineBreakdown = try c.decodeIfPresent(NKJSON.self, forKey: .engineBreakdown) ?? .object([:])
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
    }

    var engineCode: String? { engineBreakdown["engine_code"]?.stringValue }
    var engineVersion: String? { engineBreakdown["engine_version"]?.stringValue }

    /// ⚠ **`tierAccuracy == nil` 是「没判」,不是「判错了」** —— 展示处如实说。
    var tierAccuracyLabel: String {
        guard let t = tierAccuracy else { return "本篮未给分层准确性判定" }
        return nkVerificationStateLabel(t)
    }
    var tierAccuracyTone: NKAxisTone {
        switch tierAccuracy {
        case "verified": return .good
        case "partial": return .warn
        case "falsified": return .bad
        default: return .neutral   // unclear / 两个"没判"码 / nil
        }
    }
}

/// 交易时钟事件流水一行(**append-only**)。`userNote` = K8 §十五 用户主观说明
/// (⛔ 系统不生成、不改写、不合并 —— §七 P3-28 纪律)。
struct TradeClockEvent: Codable, Equatable, Identifiable {
    var id: Int = 0
    var eventDate: String = ""
    /// `d1_open | daily_check | target_zone | invalidation | manual_note | close`。
    var kind: String = ""
    var mech: NKJSON = .object([:])
    var userNote: String? = nil
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey { case id, eventDate, kind, mech, userNote, createdAt }

    init(id: Int = 0, eventDate: String = "", kind: String = "", mech: NKJSON = .object([:]),
         userNote: String? = nil, createdAt: String = "") {
        self.id = id; self.eventDate = eventDate; self.kind = kind
        self.mech = mech; self.userNote = userNote; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(Int.self, forKey: .id) ?? 0
        eventDate = try c.decodeIfPresent(String.self, forKey: .eventDate) ?? ""
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        mech = try c.decodeIfPresent(NKJSON.self, forKey: .mech) ?? .object([:])
        userNote = try c.decodeIfPresent(String.self, forKey: .userNote)
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    var kindLabel: String { nkTradeClockEventKindLabel(kind) }
}

/// 交易时钟事件类型的展示层换算(未识别码原样透传,⛔ 不瞎翻译)。
func nkTradeClockEventKindLabel(_ raw: String) -> String {
    switch raw {
    case "d1_open": return "D1 开盘"
    case "daily_check": return "每日检查"
    case "target_zone": return "进入目标区间"
    case "invalidation": return "失效信号"
    case "manual_note": return "你的说明"
    case "close": return "结案"
    default: return raw
    }
}

/// 一笔真实买入的交易时钟。`final` **只在结案后有值**(运行中恒 nil ——「还没结案」
/// 与「结案了但八项算不出」必须分得开);`basketId == nil` = 非篮子来源的手动开仓,
/// **合法**,⛔ 不是数据错误。
struct TradeClock: Codable, Equatable, Identifiable {
    var positionId: Int = 0
    var tsCode: String = ""
    var basketId: Int? = nil
    var openedOn: String = ""
    var closedOn: String? = nil
    /// `running` | `closed`。
    var status: String = ""
    /// 开仓时从 `position_plans` 冻的四件套快照(原样透传)。
    var entryPlan: NKJSON = .object([:])
    /// 结案时的八项验证(K8 §十四)。nil = **还在跑**。
    var final: NKJSON? = nil
    var events: [TradeClockEvent] = []

    var id: Int { positionId }

    enum CodingKeys: String, CodingKey {
        case positionId, tsCode, basketId, openedOn, closedOn, status, entryPlan, final, events
    }

    init(positionId: Int = 0, tsCode: String = "", basketId: Int? = nil, openedOn: String = "",
         closedOn: String? = nil, status: String = "", entryPlan: NKJSON = .object([:]),
         final: NKJSON? = nil, events: [TradeClockEvent] = []) {
        self.positionId = positionId; self.tsCode = tsCode; self.basketId = basketId
        self.openedOn = openedOn; self.closedOn = closedOn; self.status = status
        self.entryPlan = entryPlan; self.final = final; self.events = events
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        positionId = try c.decodeIfPresent(Int.self, forKey: .positionId) ?? 0
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        basketId = try c.decodeIfPresent(Int.self, forKey: .basketId)
        openedOn = try c.decodeIfPresent(String.self, forKey: .openedOn) ?? ""
        closedOn = try c.decodeIfPresent(String.self, forKey: .closedOn)
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? ""
        entryPlan = try c.decodeIfPresent(NKJSON.self, forKey: .entryPlan) ?? .object([:])
        // ⚠ `final` 运行中服务端发的是 JSON `null`(不是缺键)——`decodeIfPresent`
        // 对显式 null 返回 nil,正是我们要的「还没结案」。
        final = try c.decodeIfPresent(NKJSON.self, forKey: .final)
        events = try c.decodeIfPresent([TradeClockEvent].self, forKey: .events) ?? []
    }

    var isRunning: Bool { status == "running" }
    var statusLabel: String {
        switch status {
        case "running": return "跟踪中"
        case "closed": return "已结案"
        default: return status.isEmpty ? "状态未知" : status
        }
    }
    /// 用户已补的主观说明(按时间序)。**空 = 这笔仓一条说明都没写**(K8 §十五 覆盖率
    /// 稀疏的直接体现,§七 P3-28)—— ⛔ 系统不代猜、不代填。
    var userNotes: [TradeClockEvent] { events.filter { ($0.userNote ?? "").isEmpty == false } }
}

/// `POST /clocks/trade/{id}/note` 响应。`coverage` = 「本期 N 笔中有 M 笔带说明」
/// (§七 P3-28 候选解法①:让稀疏程度当场可见)。
struct TradeClockNoteResult: Codable, Equatable {
    var ok: Bool = true
    var eventId: Int = 0
    var eventDate: String = ""
    var coverage: NKJSON = .object([:])

    enum CodingKeys: String, CodingKey { case ok, eventId, eventDate, coverage }

    init(ok: Bool = true, eventId: Int = 0, eventDate: String = "",
         coverage: NKJSON = .object([:])) {
        self.ok = ok; self.eventId = eventId; self.eventDate = eventDate; self.coverage = coverage
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? true
        eventId = try c.decodeIfPresent(Int.self, forKey: .eventId) ?? 0
        eventDate = try c.decodeIfPresent(String.self, forKey: .eventDate) ?? ""
        coverage = try c.decodeIfPresent(NKJSON.self, forKey: .coverage) ?? .object([:])
    }

    /// 一句人读的覆盖率(键缺就不说,⛔ 不拿 0 冒充)。
    var coverageText: String? {
        guard let total = coverage["total"]?.intValue,
              let withNote = coverage["withNote"]?.intValue else { return nil }
        return "本期 \(total) 笔中 \(withNote) 笔带说明"
    }
}

/// 🔴 **用户主观说明的长度上界 —— 这是服务端 `review/trade_clock.USER_NOTE_MAX_CHARS`
/// 的镜像,⛔ 不是客户端自己定的阈值。** 权威永远在服务端:超长服务端返 **422**,
/// 客户端这个数只用来画字数计数器与提前提示。
/// ⚠ 两处不同步就会静默出现「客户端说能写、服务端说太长」——
/// 故 `tests/test_contract_crosscheck.py` 有一条机器判据把两个数钉成相等,
/// **改服务端那个常量不改这里,Python 套件当场红**。
let nkTradeNoteMaxChars: Int = 500

// MARK: - 展示用轴向着色(沿用 LinoN `AxisTone` 概念,四值穷举)
//
//  刻意只留纯枚举(不 import SwiftUI),保持 Models.swift 是纯 Foundation 数据层、
//  可脱离 UI 单测。真正的颜色映射在 `Components/SharedUI.swift`(那里把
//  `NKAxisTone` 映射到 `NK.up/.down/.amber/.textSecondary`)。

enum NKAxisTone: Equatable {
    case good, warn, bad, neutral
}
