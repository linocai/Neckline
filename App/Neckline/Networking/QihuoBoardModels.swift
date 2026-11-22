// Q 期货看板（schema_version: 3）跨端数据模型，与后端 `/api/v4/qihuo/board` 一一对应。
// 严格区分 0.0 与 nil（例如烧碱/甲醇仓单 0.0 必须显示 0 吨；乙二醇现货与基差诚实为 nil）。

import Foundation

struct QihuoBoard: Decodable, Equatable, Sendable {
    let schemaVersion: Int
    let version: String
    let generatedAt: String
    let asOfDate: String?
    let retentionDays: Int
    let schedule: QihuoSchedule
    let latestCollect: QihuoCollectRun?
    let pool: QihuoPool
    let products: [QihuoProduct]
    let stale: Bool
    let staleReason: String?
    let lastReadAt: String
}

struct QihuoSchedule: Decodable, Equatable, Sendable {
    let time: String
    let timezone: String
    let frequency: String
    let silent: Bool
}

struct QihuoCollectRun: Decodable, Equatable, Sendable {
    let runId: Int?
    let slot: String?
    let tradeDate: String?
    let startedAt: String?
    let finishedAt: String?
    let status: String
    let error: String?
}

struct QihuoPool: Decodable, Equatable, Sendable {
    let activeCount: Int
    let pausedCount: Int
    let totalStoredCount: Int
    let active: [QihuoPoolRef]
    let paused: [QihuoPoolRef]
}

struct QihuoPoolRef: Decodable, Equatable, Identifiable, Sendable {
    let code: String
    let slug: String
    let name: String
    var id: String { code }
}

struct QihuoProduct: Decodable, Equatable, Identifiable, Sendable {
    let code: String
    let slug: String
    let name: String
    let board: String
    let exchange: String
    let exchangeName: String
    let nightSession: Bool?
    let chainTag: String?
    let hasWarehouseReceipt: Bool
    let spotPending: Bool
    let snapshot: QihuoSnapshot
    let status: QihuoColumnStatuses
    let news: [QihuoNewsItem]
    let dataRows: [QihuoDataRow]
    let spotHistory: [QihuoSpot]
    let basisHistory: [QihuoBasis]
    let holdRows: [QihuoHoldRow]
    let basic: QihuoBasic

    var id: String { slug }
}

struct QihuoSnapshot: Decodable, Equatable, Sendable {
    let dataDate: String?
    let mainContract: String?
    let mainContractTs: String?
    let priceUnit: String
    let close: Double?
    let settle: Double?
    let preSettle: Double?
    let pctChg: Double?
    let wsr: QihuoWsr?
    let holding: QihuoHolding?
    let spot: QihuoSpot?
    let basis: QihuoBasis?
    let newsSummary: QihuoNewsSummary
}

struct QihuoWsr: Decodable, Equatable, Sendable {
    let date: String?
    let value: Double?
    let unit: String
    let change: Double?
    let changeText: String?
}

struct QihuoHolding: Decodable, Equatable, Sendable {
    let date: String?
    let contract: String?
    let longTotal: Int?
    let shortTotal: Int?
    let longChg: Int?
    let shortChg: Int?
    let longRatio: Double?
    let longRatioPct: Double?
    let net: Int?
}

struct QihuoSpot: Decodable, Equatable, Sendable {
    let date: String?
    let value: Double?
    let displayValue: String?
    let unit: String
    let series: String?
    let source: String?
    let sourceName: String?
    let scope: String?
}

struct QihuoBasis: Decodable, Equatable, Sendable {
    let date: String?
    let value: Double?
    let displayValue: String?
    let unit: String
    let spotDate: String?
    let futuresDate: String?
    let futuresContract: String?
    let text: String?
}

struct QihuoNewsSummary: Decodable, Equatable, Sendable {
    let total: Int?
    let bullCount: Int?
    let bearCount: Int?
    let latestPublishedAt: String?
}

struct QihuoColumnStatuses: Decodable, Equatable, Sendable {
    let data: QihuoColumnStatus
    let news: QihuoColumnStatus
    let holding: QihuoColumnStatus
}

struct QihuoColumnStatus: Decodable, Equatable, Sendable {
    let state: String
    let updatedAt: String?
    let message: String?
    let text: String?
}

struct QihuoNewsItem: Decodable, Equatable, Identifiable, Sendable {
    let publishedAt: String
    let publishedDisplay: String
    let collectedAt: String?
    let source: String
    let url: String?
    let title: String
    let category: String
    let tier: String
    let direction: String
    let summary: String
    let figures: [QihuoFigure]
    let origin: String

    var id: String { "\(publishedAt)|\(title)" }
}

struct QihuoFigure: Decodable, Equatable, Identifiable, Sendable {
    let metric: String
    let value: String
    let unit: String
    let change: String?
    let source: String
    let scope: String
    let text: String?

    var id: String { "\(metric)|\(value)|\(source)|\(scope)" }
}

struct QihuoDataRow: Decodable, Equatable, Identifiable, Sendable {
    let date: String
    let contract: String?
    let close: Double?
    let settle: Double?
    let preSettle: Double?
    let pctChg: Double?
    let wsr: Double?
    let wsrChg: Double?
    let wsrChgText: String?

    var id: String { date }
}

struct QihuoHoldRow: Decodable, Equatable, Identifiable, Sendable {
    let date: String
    let contract: String?
    let longTotal: Int?
    let shortTotal: Int?
    let longChg: Int?
    let shortChg: Int?
    let longRatio: Double?
    let longRatioPct: Double?
    let net: Int?
    let collectedAt: String?

    var id: String { "\(date)|\(contract ?? "")" }
}

struct QihuoBasic: Decodable, Equatable, Sendable {
    let updated: String?
    let contract: QihuoContract
    let chain: QihuoChain
    let related: [QihuoRelated]
    let standardSpot: QihuoStandardSpot
    let sections: [QihuoBasicSection]
}

struct QihuoContract: Decodable, Equatable, Sendable {
    let tradingUnit: String?
    let pointValue: String?
    let tradingHours: String?
    let nightSession: Bool?
    let deliveryMonths: String?
}

struct QihuoChain: Decodable, Equatable, Sendable {
    let upstream: [QihuoChainNode]
    let downstream: [QihuoChainNode]
    let plain: String?
}

struct QihuoChainNode: Decodable, Equatable, Identifiable, Sendable {
    let name: String
    let note: String?
    var id: String { name }
}

struct QihuoRelated: Decodable, Equatable, Identifiable, Sendable {
    let name: String
    let code: String?
    let enabled: Bool
    let note: String?
    var id: String { "\(name)|\(code ?? "")" }
}

struct QihuoStandardSpot: Decodable, Equatable, Sendable {
    let source: String?
    let sourceName: String?
    let series: String?
    let spec: String?
    let unit: String?
}

struct QihuoBasicSection: Decodable, Equatable, Identifiable, Sendable {
    let title: String
    let markdown: String
    var id: String { title }
}
