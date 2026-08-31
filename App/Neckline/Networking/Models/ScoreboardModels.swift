//
//  ScoreboardModels.swift
//  K9-v3 immutable score-package API DTOs.
//
//  These types deliberately mirror only `GET /scoreboard/packages` and
//  `GET /scoreboard/packages/{batchId}`. This surface has no rolling queue
//  or cross-package composite metric.
//

import Foundation

enum K9PackageState: String, Codable, CaseIterable { case d0, d1, settled }

enum K9CoverageState: String, Codable, CaseIterable {
    case pending, complete, partial, unavailable
    var label: String {
        switch self {
        case .pending: return "待结算"
        case .complete: return "覆盖完整"
        case .partial: return "部分可评价"
        case .unavailable: return "暂不可评价"
        }
    }
}

enum K9ChecklistVerdict: String, Codable, CaseIterable {
    case rejected, unbuyable, pendingOpen = "pending_open"
    var label: String {
        switch self {
        case .rejected: return "已触发放弃"
        case .unbuyable: return "确认不可买"
        case .pendingOpen: return "待开盘后观察"
        }
    }
}

enum K9OpenVerdict: String, Codable, CaseIterable {
    case confirmed, rejected, observed, unbuyable, unavailable
    var label: String {
        switch self {
        case .confirmed: return "成立"
        case .rejected: return "放弃"
        case .observed: return "观察"
        case .unbuyable: return "不可买"
        case .unavailable: return "不可评价"
        }
    }
}

enum K9D1CloseState: String, Codable, CaseIterable {
    case enhanced, held, weakened, unavailable
    var label: String {
        switch self {
        case .enhanced: return "增强"
        case .held: return "保持"
        case .weakened: return "转弱"
        case .unavailable: return "不可评价"
        }
    }
}

enum K9D2SelectionResult: String, Codable, CaseIterable {
    case successRealized = "success_realized"
    case opportunityNotContinued = "opportunity_not_continued"
    case confirmedFailed = "confirmed_failed"
    case correctReject = "correct_reject"
    case falseReject = "false_reject"
    case observedRealized = "observed_realized"
    case observedNotRealized = "observed_not_realized"
    case unavailable
    var label: String {
        switch self {
        case .successRealized: return "成立并兑现"
        case .opportunityNotContinued: return "有机会但未延续"
        case .confirmedFailed: return "成立后失败"
        case .correctReject: return "正确放弃"
        case .falseReject: return "错误放弃"
        case .observedRealized: return "观察后兑现"
        case .observedNotRealized: return "观察后未兑现"
        case .unavailable: return "不可评价"
        }
    }
}

struct ScoreboardPackage: Codable, Equatable, Identifiable {
    let batchId: String
    let selectionDate: String
    let signalTradeDate: String
    let d1TradeDate: String
    let d2TradeDate: String
    let revision: Int
    let state: K9PackageState
    let coverageState: K9CoverageState
    let strategyVersion: String
    let paramsPackageVersion: String
    let packVersion: String
    let labelContractVersion: String
    let candidateCount: Int
    let createdAt: String
    var id: String { batchId }
}

struct ScoreboardPackagesResponse: Codable, Equatable {
    let strategyVersion: String
    let state: String
    let packages: [ScoreboardPackage]
}

struct ScoreboardPackageDetail: Codable, Equatable, Identifiable {
    let batchId: String
    let selectionDate: String
    let signalTradeDate: String
    let d1TradeDate: String
    let d2TradeDate: String
    let revision: Int
    let state: K9PackageState
    let coverageState: K9CoverageState
    let strategyVersion: String
    let paramsPackageVersion: String
    let packVersion: String
    let labelContractVersion: String
    let candidateCount: Int
    let createdAt: String
    let frozenContract: NKJSON
    let candidates: [K9PackageCandidate]
    var id: String { batchId }
}

struct K9PackageCandidate: Codable, Equatable, Identifiable {
    let tsCode: String
    let name: String?
    let swL2Code: String?
    let swL2Name: String?
    let channels: [String]
    let channelRanks: [String: Int]
    let playbook: NKJSON
    let baseline: NKJSON
    let thresholds: NKJSON
    let playbookHistory: [K9PlaybookRevision]?
    let d1: K9CandidateD1?
    let d2: K9CandidateD2?
    var id: String { tsCode }
    var displayName: String { name?.isEmpty == false ? name! : tsCode }
    var playbookRevision: Int { playbook.objectValue?["revision"]?.intValue ?? 1 }
    var playbookLabel: String { "预案第 \(playbookRevision) 版" }
}

struct K9PlaybookRevision: Codable, Equatable, Identifiable {
    let revision: Int
    let source: String
    let playbook: NKJSON
    let createdAt: String
    let frozenAt: String?
    var id: Int { revision }
    var sourceLabel: String { source == "user" ? "用户修改" : "LLM 生成" }
}

struct K9CandidateD1: Codable, Equatable {
    let checklistVerdict: K9ChecklistVerdict
    let openVerdict: K9OpenVerdict?
    let referencePrice: Double?
    let raw: NKJSON
    let closeState: K9D1CloseState?
    let closeRaw: NKJSON?
}

struct K9CandidateD2: Codable, Equatable {
    let selectionResult: K9D2SelectionResult
    let playbookResult: String?
    let riskTag: String?
    let raw: NKJSON
}

func nkChannelLabel(_ channel: String) -> String {
    switch channel {
    case "p2": return "P2 超跌修复"
    case "p3": return "P3 热门强博弈"
    case "p4": return "P4 行业超跌修复"
    default: return channel
    }
}
