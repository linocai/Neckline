// KX V4 看板模型（schema_version 5）。严格分离程序事实（facts）与 DeepSeek 判断（judgment），
// 缺失数值保留 nil，界面统一展示为“—”，绝不当成 0。

import Foundation

enum KXZone: String, Codable, CaseIterable, Sendable, Hashable {
    case leader
    case hot
    case elim
}

enum KXRhythmKind: String, Codable, Sendable, Hashable {
    case surge
    case pullback
    case divergence
    case washout
    case dead
}

enum KXChangeBadge: String, Codable, Sendable, Hashable {
    case up
    case new
    case `return`
    case elimNew = "elim_new"
    case elimOut = "elim_out"
    case stay
}

enum KXSeatBadgeKind: String, Codable, Sendable, Hashable {
    case hot
    case quant
    case lasa
    case inst
    case north
}

struct KXBoard: Decodable, Equatable, Sendable {
    let schemaVersion: Int
    let status: String
    let version: String
    let asOf: String
    let generatedAt: String
    let generatedBy: String
    let checkedAt: String?
    let lastReadAt: String
    let stale: Bool
    let staleReason: String?
    let deepseekStatus: String
    let deepseekNotice: String?
    let factWarnings: [String]
    let pushSummary: String
    let refresh: KXRefreshStatus?
    let capacity: KXCapacity
    let sentiment: KXSentiment
    let factions: [KXFactionSummary]
    let changes: KXChanges
    let items: [KXBoardItem]

    var latestSentimentDay: KXSentimentDay? {
        sentiment.days.last
    }

    func items(in zone: KXZone) -> [KXBoardItem] {
        items.filter { $0.zone == zone }
    }

    /// 合并所有仍有效的告警（缓存过期、上游整理未完成、DeepSeek 降级、事实核验提醒），去重后逐条展示，互不遮盖。
    var alertMessages: [String] {
        var result: [String] = []
        func appendUnique(_ raw: String?) {
            guard let text = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return }
            if !result.contains(text) {
                result.append(text)
            }
        }
        if stale {
            if let reason = staleReason?.trimmingCharacters(in: .whitespacesAndNewlines), !reason.isEmpty {
                appendUnique("当前展示缓存快照：\(reason)")
            } else {
                appendUnique("当前展示最近一次盘后缓存快照，最新拉取未成功。")
            }
        }
        appendUnique(refresh?.notice)
        appendUnique(deepseekNotice)
        if deepseekStatus != "ok", deepseekNotice?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ?? true {
            appendUnique("DeepSeek 盘后判断未完成，当前已刷新程序事实并沿用旧结构。")
        }
        if (status == "partial" || refresh?.result == "failed"), result.isEmpty {
            appendUnique(refresh?.message ?? "今日盘后整理未完整完成，当前展示已有快照。")
        }
        for warning in factWarnings {
            appendUnique("事实核验提醒：\(warning)")
        }
        return result
    }

    var alertBannerText: String? {
        alertMessages.first
    }
}

struct KXRefreshStatus: Decodable, Equatable, Sendable {
    let date: String?
    let result: String?
    let poolVersion: String?
    let asOf: String?
    let deepseekStatus: String?
    let finishedAt: String?
    let message: String?
    let notice: String?
    let elapsedSeconds: Double?
}

struct KXCapacity: Decodable, Equatable, Sendable {
    let used: Int
    let max: Int
    let leaderCount: Int
    let hotCount: Int
    let elimCount: Int
    let nextFree: Int
}

struct KXSentiment: Decodable, Equatable, Sendable {
    let days: [KXSentimentDay]
    let judgment: KXSentimentJudgment?
}

struct KXStockRef: Decodable, Equatable, Sendable, Hashable {
    let code: String
    let name: String
    let boardType: String
}

struct KXSentimentDay: Decodable, Equatable, Sendable, Identifiable {
    let date: String
    let zt: Int?
    let zb: Int?
    let zbRate: Double?
    let dt: Int?
    let maxLbc: Int?
    let highest: [KXStockRef]
    let ladder: [String: [KXStockRef]]
    let yZtN: Int?
    let yCont: Int?
    let yOpenMean: Double?
    let yCloseMean: Double?
    let yCloseMed: Double?

    var id: String { date }
}

struct KXSentimentJudgment: Decodable, Equatable, Sendable {
    let stage: String
    let marketNote: String
}

struct KXFactionSummary: Decodable, Equatable, Sendable, Identifiable {
    let name: String
    let count: Int

    var id: String { name }
}

struct KXChanges: Decodable, Equatable, Sendable {
    let added: [String]
    let promoted: [String]
    let toElim: [String]
    let returned: [String]
    let removed: [KXRemovedItem]
}

struct KXRemovedItem: Decodable, Equatable, Sendable, Identifiable {
    let code: String
    let name: String
    let reason: String

    var id: String { code }
}

struct KXBoardItem: Decodable, Equatable, Sendable, Identifiable, Hashable {
    let code: String
    let name: String
    let industry: String
    let zone: KXZone
    let firstSeen: String
    let zoneSince: String
    let daysInZone: Int
    let elimSince: String?
    let elimDays: Int
    let nextRemove: Bool
    let changeBadge: KXChangeBadge
    let cycleTag: String
    let facts: KXItemFacts
    let judgment: KXItemJudgment

    var id: String { code }

    func matches(faction: String, query: String) -> Bool {
        if faction != KXCopy.allFaction {
            let hitFaction = judgment.factions.contains { $0.name == faction }
            if !hitFaction { return false }
        }
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty { return true }
        let lower = q.lowercased()
        if name.lowercased().contains(lower) { return true }
        if code.lowercased().contains(lower) { return true }
        if industry.lowercased().contains(lower) { return true }
        if judgment.factions.contains(where: { $0.name.lowercased().contains(lower) }) { return true }
        return false
    }
}

struct KXItemFacts: Decodable, Equatable, Sendable, Hashable {
    let close: Double?
    let pct: Double?
    let tr: Double?
    let circMvYi: Double?
    let boardType: String
    let boardStatus: String
    let lbc: Int?
    let breakDays: Int?
    let firstSealTime: String?
    let lastSealTime: String?
    let breakCount: Int?
    let ma5: Double?
    let ma5Diff: Double?
    let ma10: Double?
    let ma10Diff: Double?
    let ddPeak: Double?
    let volRatioPeak: Double?
    let zt5dN: Int?
    let dt5dN: Int?
    let lhb5dN: Int?
    let suspended: Bool
    let traj: [KXTrajDay]
    let lhbIsToday: Bool
    let lhb: KXLHB?
}

struct KXTrajDay: Decodable, Equatable, Sendable, Hashable, Identifiable {
    let date: String
    let pct: Double?
    let tr: Double?
    let zt: Bool
    let dt: Bool
    let zb: Bool
    let lhb: Bool

    var id: String { date }
}

struct KXLHB: Decodable, Equatable, Sendable, Hashable {
    let date: String
    let reasons: [String]
    let netW: Double?
    let buyTotalW: Double?
    let sellTotalW: Double?
    let buys: [KXLHBSeat]
    let sells: [KXLHBSeat]
}

struct KXLHBSeat: Decodable, Equatable, Sendable, Hashable {
    let org: String
    let fullOrg: String
    let buyW: Double?
    let sellW: Double?
    let netW: Double?
    let badges: [KXSeatBadge]
}

struct KXSeatBadge: Decodable, Equatable, Sendable, Hashable {
    let text: String
    let kind: KXSeatBadgeKind
}

struct KXItemJudgment: Decodable, Equatable, Sendable, Hashable {
    let rhythm: KXRhythm
    let factions: [KXFactionTag]
    let why: String
    let lhbComment: String
    let watchNote: String
}

struct KXRhythm: Decodable, Equatable, Sendable, Hashable {
    let kind: KXRhythmKind
    let text: String
}

struct KXFactionTag: Decodable, Equatable, Sendable, Hashable {
    let name: String
    let primary: Bool
}
