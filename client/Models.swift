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

// MARK: - 4A.2 报告:候选四件套 + LLM 审判

struct LLMJudgment: Codable, Equatable {
    var verdict: String       // "通过" | "否决" | "未激活"
    var narrative: String
    var degraded: Bool
}

struct Candidate: Codable, Equatable, Identifiable {
    var rank: Int
    var code: String
    var name: String
    var score: Double
    var board: String                 // 主板/创业板/科创板/北交所(股票板块分类,非本页"看板")
    // 四件套(§2.2/§2.3):买点 / 止损(-5%) / 目标 / 证伪条件 —— 全部自由文本,不在客户端重排模板卡
    var buyPoint: String
    var stop: String
    var target: String
    var invalidation: String
    var formTags: [String]
    var hotSectors: [String]
    var sectorNames: [String]
    var llmJudgment: LLMJudgment?      // 仅前 10 只有(nil = 未过 LLM 审判,非降级)

    var id: String { code }

    /// `board` 服务端字面实测是英文枚举码("MAIN"/"GEM"/"STAR"/"BSE",唯一源
    /// `neckline/data/board.py` 的 `Board` 枚举,§3.2.7/CLAUDE.md「板块分类唯一源」),
    /// 不是中文名。这里只做**展示层换算四个已知常量**,不改判定、不猜测新分类
    /// (未识别值原样透传,不静默瞎翻译——万一后端枚举新增值,界面照样不崩、只是显英文)。
    var boardLabel: String {
        switch board {
        case "MAIN": return "主板"
        case "GEM": return "创业板"
        case "STAR": return "科创板"
        case "BSE": return "北交所"
        default: return board
        }
    }
}

// MARK: - 4A.2 报告:整份报告

struct ReportSnapshot: Codable, Equatable {
    var tradeDate: String
    var generatedAt: String
    var strategyVersion: String
    var sentiment: SentimentSnapshot?
    var sectors: [SectorSnapshot]
    var candidates: [Candidate]
    var degraded: Bool
    var reason: String

    /// 空态占位(无报告 / 拉取失败),UI 据 `degraded`+`reason` 诚实展示,不假装有数据。
    static func empty(reason: String) -> ReportSnapshot {
        ReportSnapshot(tradeDate: "", generatedAt: "", strategyVersion: "",
                       sentiment: nil, sectors: [], candidates: [], degraded: true, reason: reason)
    }
}

// MARK: - 4A.3 盘中看板

struct RetreatBrake: Codable, Equatable {
    var active: Bool
    var reason: String
}

/// 哨兵事件三类中文标签,后端 `_SENTINEL_LABEL` 唯一源(客户端不重译)。
enum SentinelKind: String, Codable {
    case entry = "买点"
    case invalidation = "证伪"
    case holding = "持仓"
}

struct BoardEvent: Codable, Equatable, Identifiable {
    var sentinel: String     // 买点 | 证伪 | 持仓(见 SentinelKind;未识别值原样展示,不崩)
    var code: String
    var name: String
    var eventKey: String
    var verdict: String      // 判决文案(哨兵已落库的 reason 文本,自然语言,不是模板卡)
    var ts: String

    var id: String { eventKey }
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
    var distToStopPct: Double? {
        guard hasLivePrice, price > 0 else { return nil }
        return (price - stopLine) / price * 100
    }
    /// 已破 -5% 止损线(展示红色警示;真实止损执行在券商条件单,系统只审计)。
    var hasBrokenStop: Bool {
        guard hasLivePrice else { return false }
        return price <= stopLine
    }
}

// MARK: - 4A.5 问询台

enum ChatRole: String, Codable {
    case user, assistant
}

struct ChatMessage: Identifiable, Equatable {
    let id = UUID()
    var role: ChatRole
    var text: String
}

/// 裁决二值(硬约束,§2.5「永不现在就买」)——枚举穷举只两值,任何第三个字符串
/// 归 `.unknown`(绝不静默当成某个已知态展示,便于第一时间发现契约漂移)。
enum InquiryVerdict: Equatable {
    static let rejectRaw = "不符合"
    static let passRaw = "初审通过进海选池"

    case reject
    case pass
    case unknown(String)

    init(_ raw: String) {
        switch raw {
        case Self.rejectRaw: self = .reject
        case Self.passRaw: self = .pass
        default: self = .unknown(raw)
        }
    }

    var label: String {
        switch self {
        case .reject: return Self.rejectRaw
        case .pass: return Self.passRaw
        case .unknown(let s): return s
        }
    }

    var tone: NKAxisTone {
        switch self {
        case .reject: return .bad
        case .pass: return .good
        case .unknown: return .neutral
        }
    }

    /// 硬约束不变量(§2.5「永不现在就买」):问询台裁决**任何一种取值**都不启用
    /// 「买」类操作——UI 层只展示 `label` 徽标,从不为任何 verdict 渲染下单/买入按钮。
    /// 恒 false,穷举写死,不看 verdict 分支(见 NecklineTests 的对抗性字符串单测)。
    var enablesBuyAction: Bool { false }
}

struct InquiryResult: Equatable {
    var code: String
    var reply: String
    var verdict: InquiryVerdict
    var evidence: [String]
    var degraded: Bool
}

// MARK: - 4A.5 设置

enum LLMProviderKind: String, CaseIterable, Identifiable, Codable {
    case glm, kimi
    var id: String { rawValue }
    var label: String {
        switch self {
        case .glm: return "GLM"
        case .kimi: return "Kimi"
        }
    }
}

struct PushSettings: Codable, Equatable {
    var report: Bool
    var retreatBrake: Bool
}

struct SettingsSnapshot: Codable, Equatable {
    var llmProvider: String?     // "glm" | "kimi" | nil(未设)
    var llmKeySet: Bool          // 只回布尔,绝不回明文(§3.4 高危区)
    var push: PushSettings

    static let empty = SettingsSnapshot(llmProvider: nil, llmKeySet: false,
                                        push: PushSettings(report: true, retreatBrake: true))
}

// MARK: - 展示用轴向着色(沿用 LinoN `AxisTone` 概念,四值穷举)
//
//  刻意只留纯枚举(不 import SwiftUI),保持 Models.swift 是纯 Foundation 数据层、
//  可脱离 UI 单测。真正的颜色映射在 `Components/SharedUI.swift`(那里把
//  `NKAxisTone` 映射到 `NK.up/.down/.amber/.textSecondary`)。

enum NKAxisTone {
    case good, warn, bad, neutral
}
