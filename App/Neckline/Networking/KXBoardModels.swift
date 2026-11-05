// KX 看板接口 v2（GET /api/v4/kx/board）。时间保持字符串，展示时再按北京时间格式化。
// 可空字段缺了或类型不对，只把那一项留空，不让整份解码失败。金额是元。

import Foundation

enum KXDeepseekStatus: Equatable {
    case ok
    case unknown(String)

    var isOK: Bool {
        if case .ok = self { return true }
        return false
    }

    static func parse(_ raw: String?) -> KXDeepseekStatus? {
        guard let raw else { return nil }
        return raw == "ok" ? .ok : .unknown(raw)
    }
}

enum KXQuoteStatus: Equatable {
    case ok
    case stale
    case missing
    case unknown(String)

    static func parse(_ raw: String?) -> KXQuoteStatus? {
        guard let raw else { return nil }
        switch raw {
        case "ok": return .ok
        case "stale": return .stale
        case "missing": return .missing
        default: return .unknown(raw)
        }
    }
}

enum KXSessionPhase: Equatable {
    case callAuction
    case preOpen
    case unknown(String)

    static func parse(_ raw: String?) -> KXSessionPhase? {
        guard let raw else { return nil }
        switch raw {
        case "call_auction": return .callAuction
        case "pre_open": return .preOpen
        default: return .unknown(raw)
        }
    }
}

enum KXRole: Equatable {
    case leader
    case follower
    case unknown(String)

    static func parse(_ raw: String?) -> KXRole? {
        guard let raw else { return nil }
        switch raw {
        case "龙头": return .leader
        case "小弟": return .follower
        default: return .unknown(raw)
        }
    }
}

/// 标签的 kind 是开放词，原文放在 unknown 里，不因为不认识的词让解码失败。
enum KXFlagKind: Equatable {
    case unknown(String)

    var raw: String {
        switch self {
        case .unknown(let raw): return raw
        }
    }
}

enum KXRoom: String, Equatable, Hashable, Identifiable {
    case leader
    case waiting
    case eliminated

    var id: String { rawValue }

    var title: String {
        switch self {
        case .leader: return "龙头室"
        case .waiting: return "等候室"
        case .eliminated: return "淘汰室"
        }
    }
}

struct KXSource: Decodable, Equatable {
    var checkedAt: String?
    var stale: Bool
    var reason: String?
    var lastReadAt: String?

    init(checkedAt: String? = nil, stale: Bool = false, reason: String? = nil, lastReadAt: String? = nil) {
        self.checkedAt = checkedAt
        self.stale = stale
        self.reason = reason
        self.lastReadAt = lastReadAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        checkedAt = container.string("checkedAt")
        stale = container.bool("stale") ?? false
        reason = container.string("reason")
        lastReadAt = container.string("lastReadAt")
    }
}

struct KXRefreshInfo: Decodable, Equatable {
    var date: String?
    var result: String?
    var finishedAt: String?
    var message: String?
    var notice: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        date = container.string("date")
        result = container.string("result")
        finishedAt = container.string("finishedAt")
        message = container.string("message")
        notice = container.string("notice")
    }
}

struct KXStructure: Decodable, Equatable {
    var version: String?
    var asOf: String?
    var generatedAt: String?
    var generatedBy: String?
    var deepseekStatus: KXDeepseekStatus?
    var deepseekNotice: String?
    var marketNote: String?
    var membershipLocked: Bool?
    var refresh: KXRefreshInfo?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        version = container.string("version")
        asOf = container.string("asOf")
        generatedAt = container.string("generatedAt")
        generatedBy = container.string("generatedBy")
        deepseekStatus = KXDeepseekStatus.parse(container.string("deepseekStatus"))
        deepseekNotice = container.string("deepseekNotice")
        marketNote = container.string("marketNote")
        membershipLocked = container.bool("membershipLocked")
        refresh = container.object("refresh")
    }
}

struct KXSession: Decodable, Equatable {
    var inSession: Bool
    var refreshSeconds: Double?

    init(inSession: Bool = false, refreshSeconds: Double? = nil) {
        self.inSession = inSession
        self.refreshSeconds = refreshSeconds
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        inSession = container.bool("inSession") ?? false
        refreshSeconds = container.double("refreshSeconds")
    }
}

struct KXFetchedAt: Decodable, Equatable {
    var limitUp: String?
    var broken: String?
    var limitDown: String?
    var yesterdayLimitUp: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        limitUp = container.string("limitUp")
        broken = container.string("broken")
        limitDown = container.string("limitDown")
        yesterdayLimitUp = container.string("yesterdayLimitUp")
    }
}

struct KXHighestBoard: Decodable, Equatable {
    var boards: Double?
    var names: [String]
    var codes: [String]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        boards = container.double("boards")
        names = container.strings("names")
        codes = container.strings("codes")
    }
}

struct KXSentiment: Decodable, Equatable {
    var tradingDay: String?
    var basis: String?
    var boardPhrases: String?
    var limitUpCount: Double?
    var brokenCount: Double?
    var brokenRate: Double?
    var limitDownCount: Double?
    var highestBoard: KXHighestBoard?
    var yesterdayConsecutive: Double?
    var yesterdayContinued: Double?
    var yesterdayTradingDay: String?
    var fetchedAt: KXFetchedAt?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        tradingDay = container.string("tradingDay")
        basis = container.string("basis")
        boardPhrases = container.string("boardPhrases")
        limitUpCount = container.double("limitUpCount")
        brokenCount = container.double("brokenCount")
        brokenRate = container.double("brokenRate")
        limitDownCount = container.double("limitDownCount")
        highestBoard = container.object("highestBoard")
        yesterdayConsecutive = container.double("yesterdayConsecutive")
        yesterdayContinued = container.double("yesterdayContinued")
        yesterdayTradingDay = container.string("yesterdayTradingDay")
        fetchedAt = container.object("fetchedAt")
    }
}

struct KXFactWarning: Decodable, Equatable {
    var groupId: String?
    var text: String

    init(from decoder: Decoder) throws {
        if let text = try? decoder.singleValueContainer().decode(String.self) {
            let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty else {
                throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "空的事实不符"))
            }
            groupId = nil
            self.text = trimmed
            return
        }
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        let text = container.string("text") ?? ""
        guard !text.isEmpty else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "事实不符没有原文"))
        }
        groupId = container.string("groupId")
        self.text = text
    }
}

struct KXQuote: Decodable, Equatable {
    var price: Double?
    var changePercent: Double?
    var previousClose: Double?
    var open: Double?
    var high: Double?
    var low: Double?
    var turnoverAmount: Double?
    var amplitudePercent: Double?
    var turnoverPercent: Double?
    var sourceTime: String?
    var quoteStatus: KXQuoteStatus?
    var sessionPhase: KXSessionPhase?
    var warnings: [String]

    static let empty = KXQuote()

    init(price: Double? = nil, changePercent: Double? = nil, previousClose: Double? = nil, open: Double? = nil,
         high: Double? = nil, low: Double? = nil, turnoverAmount: Double? = nil, amplitudePercent: Double? = nil,
         turnoverPercent: Double? = nil, sourceTime: String? = nil, quoteStatus: KXQuoteStatus? = nil,
         sessionPhase: KXSessionPhase? = nil, warnings: [String] = []) {
        self.price = price
        self.changePercent = changePercent
        self.previousClose = previousClose
        self.open = open
        self.high = high
        self.low = low
        self.turnoverAmount = turnoverAmount
        self.amplitudePercent = amplitudePercent
        self.turnoverPercent = turnoverPercent
        self.sourceTime = sourceTime
        self.quoteStatus = quoteStatus
        self.sessionPhase = sessionPhase
        self.warnings = warnings
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        self.init(
            price: container.double("price"),
            changePercent: container.double("changePercent"),
            previousClose: container.double("previousClose"),
            open: container.double("open"),
            high: container.double("high"),
            low: container.double("low"),
            turnoverAmount: container.double("turnoverAmount"),
            amplitudePercent: container.double("amplitudePercent"),
            turnoverPercent: container.double("turnoverPercent"),
            sourceTime: container.string("sourceTime"),
            quoteStatus: KXQuoteStatus.parse(container.string("quoteStatus")),
            sessionPhase: KXSessionPhase.parse(container.string("sessionPhase")),
            warnings: container.strings("warnings")
        )
    }
}

struct KXLimitUp: Decodable, Equatable {
    var inLimitUp: Bool?
    var inBroken: Bool?
    var inLimitDown: Bool?
    var consecutiveBoards: Double?
    var firstSealTime: String?
    var lastSealTime: String?
    var breakCount: Double?
    var sealAmount: Double?
    var boardTypeText: String?
    var poolFetchedAt: String?
    var warnings: [String]

    static let empty = KXLimitUp()

    init(inLimitUp: Bool? = nil, inBroken: Bool? = nil, inLimitDown: Bool? = nil, consecutiveBoards: Double? = nil,
         firstSealTime: String? = nil, lastSealTime: String? = nil, breakCount: Double? = nil, sealAmount: Double? = nil,
         boardTypeText: String? = nil, poolFetchedAt: String? = nil, warnings: [String] = []) {
        self.inLimitUp = inLimitUp
        self.inBroken = inBroken
        self.inLimitDown = inLimitDown
        self.consecutiveBoards = consecutiveBoards
        self.firstSealTime = firstSealTime
        self.lastSealTime = lastSealTime
        self.breakCount = breakCount
        self.sealAmount = sealAmount
        self.boardTypeText = boardTypeText
        self.poolFetchedAt = poolFetchedAt
        self.warnings = warnings
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        self.init(
            inLimitUp: container.bool("inLimitUp"),
            inBroken: container.bool("inBroken"),
            inLimitDown: container.bool("inLimitDown"),
            consecutiveBoards: container.double("consecutiveBoards"),
            firstSealTime: container.string("firstSealTime"),
            lastSealTime: container.string("lastSealTime"),
            breakCount: container.double("breakCount"),
            sealAmount: container.double("sealAmount"),
            boardTypeText: container.string("boardTypeText"),
            poolFetchedAt: container.string("poolFetchedAt"),
            warnings: container.strings("warnings")
        )
    }
}

struct KXSealChange: Decodable, Equatable {
    var amount: Double?
    var baselineAmount: Double?
    var baselineAgeSeconds: Double?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        amount = container.double("amount")
        baselineAmount = container.double("baselineAmount")
        baselineAgeSeconds = container.double("baselineAgeSeconds")
    }
}

struct KXOrderBook: Decodable, Equatable {
    var status: String?
    var bid1Price: Double?
    var bid1Volume: Double?
    var bid1Amount: Double?
    var asksClear: Bool?
    var sealed: Bool?
    var sealAmount: Double?
    var sealChange5m: KXSealChange?
    var sourceTime: String?
    var warnings: [String]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        status = container.string("status")
        bid1Price = container.double("bid1Price")
        bid1Volume = container.double("bid1Volume")
        bid1Amount = container.double("bid1Amount")
        asksClear = container.bool("asksClear")
        sealed = container.bool("sealed")
        sealAmount = container.double("sealAmount")
        sealChange5m = container.object("sealChange5m")
        sourceTime = container.string("sourceTime")
        warnings = container.strings("warnings")
    }
}

struct KXStock: Decodable, Equatable {
    var code: String
    var name: String
    var role: KXRole?
    var reason: String?
    var divergent: Bool?
    var quote: KXQuote
    var limitUp: KXLimitUp
    var orderBook: KXOrderBook?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        code = container.string("code") ?? ""
        name = container.string("name") ?? ""
        role = KXRole.parse(container.string("role"))
        reason = container.string("reason")
        divergent = container.bool("divergent")
        quote = container.object("quote") ?? .empty
        limitUp = container.object("limitUp") ?? .empty
        orderBook = container.object("orderBook")
    }
}

struct KXFlag: Decodable, Equatable {
    var kind: KXFlagKind
    var code: String?
    var text: String

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        let kindText = container.string("kind")
        let text = container.string("text") ?? kindText ?? ""
        guard !text.isEmpty else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "空标签"))
        }
        kind = .unknown(kindText ?? text)
        code = container.string("code")
        self.text = text
    }
}

struct KXJudgment: Decodable, Equatable {
    var verdict: String?
    var reason: String?
    var height: String?
    var drive: String?
    var resilience: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        verdict = container.string("verdict")
        reason = container.string("reason")
        height = container.string("height")
        drive = container.string("drive")
        resilience = container.string("resilience")
    }
}

struct KXGroup: Decodable, Equatable {
    var id: String?
    var theme: String?
    var roomSince: String?
    var brokeOn: String?
    var observedTradingDays: Double?
    var observedText: String?
    var flags: [KXFlag]
    var judgment: KXJudgment?
    var note: String?
    var leaders: [KXStock]
    var followers: [KXStock]

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        id = container.string("id")
        theme = container.string("theme")
        roomSince = container.string("roomSince")
        brokeOn = container.string("brokeOn")
        observedTradingDays = container.double("observedTradingDays")
        observedText = container.string("observedText")
        flags = container.array("flags")
        judgment = container.object("judgment")
        note = container.string("note")
        leaders = container.array("leaders")
        followers = container.array("followers")
    }
}

struct KXRooms: Decodable, Equatable {
    var leader: [KXGroup]
    var waiting: [KXGroup]
    var eliminated: [KXGroup]

    static let empty = KXRooms(leader: [], waiting: [], eliminated: [])

    init(leader: [KXGroup], waiting: [KXGroup], eliminated: [KXGroup]) {
        self.leader = leader
        self.waiting = waiting
        self.eliminated = eliminated
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        leader = container.array("leader")
        waiting = container.array("waiting")
        eliminated = container.array("eliminated")
    }

    /// 龙头室、等候室、淘汰室，固定这个顺序，不跟 JSON 键的书写顺序走。
    var ordered: [KXRoomGroups] {
        [
            KXRoomGroups(room: .leader, groups: leader),
            KXRoomGroups(room: .waiting, groups: waiting),
            KXRoomGroups(room: .eliminated, groups: eliminated),
        ]
    }
}

struct KXRoomGroups: Equatable, Identifiable {
    var room: KXRoom
    var groups: [KXGroup]
    var id: KXRoom { room }
}

struct KXBoard: Decodable, Equatable {
    var generatedAt: String?
    var source: KXSource?
    var structure: KXStructure?
    var status: String?
    var errors: [String]
    var warnings: [String]
    var gaps: [String]
    var factWarnings: [KXFactWarning]
    var session: KXSession?
    var sentiment: KXSentiment?
    var rooms: KXRooms

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: KXLooseKey.self)
        let markers = ["rooms", "generatedAt", "source", "structure", "sentiment", "session"]
        guard markers.contains(where: { container.contains(KXLooseKey(stringValue: $0)!) }) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "不是看板"))
        }
        generatedAt = container.string("generatedAt")
        source = container.object("source")
        structure = container.object("structure")
        status = container.string("status")
        errors = container.strings("errors")
        warnings = container.strings("warnings")
        gaps = container.strings("gaps")
        factWarnings = container.array("factWarnings")
        session = container.object("session")
        sentiment = container.object("sentiment")
        rooms = container.object("rooms") ?? .empty
    }
}

struct KXLooseKey: CodingKey {
    var stringValue: String
    var intValue: Int?

    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

extension KeyedDecodingContainer where Key == KXLooseKey {
    func string(_ name: String) -> String? {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return nil }
        guard let value = try? decode(String.self, forKey: key) else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    func double(_ name: String) -> Double? {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return nil }
        guard let value = try? decode(Double.self, forKey: key), value.isFinite else { return nil }
        return value
    }

    func bool(_ name: String) -> Bool? {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return nil }
        return try? decode(Bool.self, forKey: key)
    }

    func strings(_ name: String) -> [String] {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return [] }
        if let values = try? decode([String].self, forKey: key) {
            return values.compactMap { item in
                let trimmed = item.trimmingCharacters(in: .whitespacesAndNewlines)
                return trimmed.isEmpty ? nil : trimmed
            }
        }
        return walk(key) { try $0.decode(String.self) }.compactMap { item in
            let trimmed = item.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    func array<T: Decodable>(_ name: String) -> [T] {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return [] }
        if let values = try? decode([T].self, forKey: key) { return values }
        return walk(key) { try $0.decode(T.self) }
    }

    func object<T: Decodable>(_ name: String) -> T? {
        let key = KXLooseKey(stringValue: name)!
        guard contains(key), (try? decodeNil(forKey: key)) != true else { return nil }
        return try? decode(T.self, forKey: key)
    }

    private func walk<T>(_ key: KXLooseKey, _ decode: (inout UnkeyedDecodingContainer) throws -> T) -> [T] {
        guard var nested = try? nestedUnkeyedContainer(forKey: key) else { return [] }
        var out: [T] = []
        while !nested.isAtEnd {
            if let item = try? decode(&nested) {
                out.append(item)
            } else {
                _ = try? nested.decode(KXAnyJSON.self)
            }
        }
        return out
    }
}

/// 用来跳过一个不合格的数组元素，避免它拖垮整份看板。
struct KXAnyJSON: Decodable {
    init(from decoder: Decoder) throws {
        if var list = try? decoder.unkeyedContainer() {
            while !list.isAtEnd {
                _ = try list.decode(KXAnyJSON.self)
            }
            return
        }
        if let object = try? decoder.container(keyedBy: KXLooseKey.self) {
            for key in object.allKeys {
                _ = try object.decode(KXAnyJSON.self, forKey: key)
            }
            return
        }
        let single = try decoder.singleValueContainer()
        if single.decodeNil() { return }
        if (try? single.decode(Bool.self)) != nil { return }
        if (try? single.decode(Double.self)) != nil { return }
        if (try? single.decode(String.self)) != nil { return }
        throw DecodingError.dataCorruptedError(in: single, debugDescription: "无法跳过")
    }
}
