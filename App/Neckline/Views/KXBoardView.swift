// KX 看板。iPhone 单列，macOS 在够宽时左右分栏；结构、用词和颜色按样图。

import SwiftUI

enum KXBoardStyle {
    case phone
    case mac

    var titleFont: Font { self == .phone ? NKKXFont.titlePhone : NKKXFont.titleMac }
    var pageInset: CGFloat { self == .phone ? 16 : 18 }
    var cardInset: CGFloat { self == .phone ? 12 : 18 }
    var titleTop: CGFloat { self == .phone ? 4 : 14 }
}

extension KXStatusPiece.Tone {
    var color: Color {
        switch self {
        case .up: return NKKX.up
        case .broken: return NKKX.amber
        case .down: return NKKX.down
        case .off: return NKMorning.textSecondary
        case .missing: return NKMorning.textTertiary
        }
    }
}

extension KXTrend {
    var color: Color {
        switch self {
        case .up: return NKKX.up
        case .down: return NKKX.down
        case .flat: return NKKX.flat
        }
    }
}

extension KXDelta.Direction {
    var color: Color {
        switch self {
        case .up: return NKKX.up
        case .down: return NKKX.down
        case .flat: return NKMorning.textSecondary
        }
    }
}

struct KXBoardView: View {
    @Bindable var kx: KXBoardModel
    var openSettings: () -> Void = {}

    private var style: KXBoardStyle {
        #if os(iOS)
        return .phone
        #else
        return .mac
        #endif
    }

    var body: some View {
        scrolling {
            switch kx.state {
            case .loading:
                VStack(spacing: 0) {
                    KXTitleBar(style: style, meta: nil, sub: nil)
                    MorningLoadingView()
                }
            case .error(let error):
                VStack(spacing: 0) {
                    KXTitleBar(style: style, meta: nil, sub: nil)
                    MorningStatusView(
                        message: KXCopy.message(for: error),
                        retry: { Task { await kx.refresh() } },
                        openSettings: openSettings
                    )
                }
            case .ready(let board):
                KXBoardPage(board: board, refreshFailed: kx.refreshFailed, style: style)
            }
        }
        .frame(maxWidth: contentMaxWidth)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .background(NKMorning.page.ignoresSafeArea())
    }

    /// 截图验收可以用 NK_QA_KX_WIDTH 把看板收窄。
    private var contentMaxWidth: CGFloat {
        #if DEBUG
        if let raw = ProcessInfo.processInfo.environment["NK_QA_KX_WIDTH"], let width = Double(raw), width > 0 {
            return CGFloat(width)
        }
        #endif
        return .infinity
    }

    @ViewBuilder
    private func scrolling<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        #if os(iOS)
        ScrollView { content() }
            .refreshable { await kx.refresh() }
        #else
        ScrollView { content() }
        #endif
    }
}

struct KXBoardPage: View {
    var board: KXBoard
    var refreshFailed: Bool
    var style: KXBoardStyle

    var body: some View {
        let sentiment = KXCopy.sentiment(board.sentiment)
        let footer = KXCopy.footer(board)
        VStack(alignment: .leading, spacing: 0) {
            KXTitleBar(
                style: style,
                meta: KXCopy.headerMeta(board: board, refreshFailed: refreshFailed),
                sub: KXCopy.headerSub(board: board)
            )
            ForEach(Array(KXCopy.amberLines(board: board).enumerated()), id: \.offset) { _, line in
                KXAmberBar(text: line)
                    .padding(.horizontal, style.cardInset)
                    .padding(.top, 4)
                    .padding(.bottom, 2)
            }
            if let gray = KXCopy.grayLine(board: board) {
                Text(verbatim: gray)
                    .font(NKKXFont.gray)
                    .foregroundStyle(NKMorning.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, style.pageInset)
                    .padding(.vertical, 1)
            }
            KXSentimentBar(text: sentiment, style: style)
                .padding(.horizontal, style.cardInset)
                .padding(.top, 6)
            if let note = KXCopy.clean(board.structure?.marketNote) {
                KXMarketNote(note: note)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(NKKX.deepseekFill, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .padding(.horizontal, style.cardInset)
                    .padding(.top, 8)
            }
            ForEach(board.rooms.ordered) { room in
                KXRoomSection(room: room, board: board, style: style)
            }
            VStack(alignment: .leading, spacing: 0) {
                Text(verbatim: footer.line)
                    .font(NKKXFont.foot)
                if let version = footer.version {
                    Text(verbatim: version)
                        .font(NKKXFont.version)
                }
            }
            .foregroundStyle(NKMorning.textTertiary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, style.pageInset)
            .padding(.top, 6)
            .padding(.bottom, style == .phone ? 10 : 16)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct KXTitleBar: View {
    var style: KXBoardStyle
    var meta: String?
    var sub: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("KX")
                    .font(style.titleFont)
                    .tracking(0.2)
                    .foregroundStyle(NKMorning.textPrimary)
                Spacer(minLength: 8)
                if let meta {
                    Text(verbatim: meta)
                        .font(NKKXFont.meta)
                        .foregroundStyle(NKMorning.textSecondary)
                        .monospacedDigit()
                }
            }
            if let sub {
                Text(verbatim: sub)
                    .font(NKKXFont.meta)
                    .foregroundStyle(NKMorning.textSecondary)
            }
        }
        .padding(.horizontal, style.pageInset)
        .padding(.top, style.titleTop)
        .padding(.bottom, style == .phone ? 0 : 4)
    }
}

struct KXAmberBar: View {
    var text: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle")
                .font(.system(size: 14))
                .padding(.top, 1)
            Text(verbatim: text)
                .font(NKKXFont.alert)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(NKKX.amberInk)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NKKX.amberBackground, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

struct KXSentimentBar: View {
    var text: KXSentimentText
    var style: KXBoardStyle
    @State private var namesOpen = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if style == .phone {
                HStack(alignment: .top, spacing: 8) {
                    count("主板涨停", text.limitUp)
                    broken
                    count("跌停", text.limitDown)
                    highest
                }
                if let title = text.nameTitle, !text.names.isEmpty {
                    KXFoldButton(title: title, open: $namesOpen)
                    if namesOpen {
                        Text(verbatim: text.names.joined(separator: "、"))
                            .font(NKKXFont.names)
                            .foregroundStyle(NKMorning.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Text(verbatim: text.yesterday)
                    .font(NKKXFont.yesterdayPhone)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 6)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .overlay(alignment: .top) {
                        Rectangle().fill(NKMorning.hairline).frame(height: 0.5)
                    }
            } else {
                HStack(alignment: .top, spacing: 8) {
                    count("主板涨停", text.limitUp)
                    broken
                    count("跌停", text.limitDown)
                    highest
                    Text(verbatim: text.yesterday)
                        .font(NKKXFont.yesterdayMac)
                        .foregroundStyle(NKMorning.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            if text.intraday {
                Text("盘中统计")
                    .font(NKKXFont.intraday)
                    .foregroundStyle(NKMorning.textTertiary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NKMorning.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var broken: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(verbatim: text.broken)
                    .font(NKKXFont.sentiValue)
                    .monospacedDigit()
                Text(verbatim: text.brokenRate)
                    .font(NKKXFont.sentiRate)
                    .foregroundStyle(NKMorning.textSecondary)
                    .monospacedDigit()
            }
            Text("炸板")
                .font(NKKXFont.label)
                .foregroundStyle(NKMorning.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var highest: some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(alignment: .firstTextBaseline, spacing: 1) {
                Text(verbatim: text.boards)
                    .font(NKKXFont.sentiValue)
                    .monospacedDigit()
                if text.showsBoardUnit {
                    Text("板")
                        .font(NKKXFont.sentiUnit)
                }
            }
            Text(verbatim: text.highestLabel)
                .font(NKKXFont.label)
                .foregroundStyle(NKMorning.textSecondary)
                .lineLimit(1)
            if style == .mac, !text.names.isEmpty {
                Text(verbatim: text.names.joined(separator: "、"))
                    .font(NKKXFont.names)
                    .foregroundStyle(NKMorning.textSecondary)
                    .lineLimit(2)
                    .padding(.top, 4)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func count(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(verbatim: value)
                .font(NKKXFont.sentiValue)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(NKKXFont.label)
                .foregroundStyle(NKMorning.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct KXFoldButton: View {
    var title: String
    @Binding var open: Bool

    var body: some View {
        Button {
            open.toggle()
        } label: {
            HStack(spacing: 0) {
                Text(open ? "▾ " : "▸ ")
                    .foregroundStyle(NKMorning.textTertiary)
                Text(title)
                    .foregroundStyle(NKMorning.accent)
                Spacer(minLength: 0)
            }
            .font(NKKXFont.link)
        }
        .buttonStyle(.plain)
    }
}

struct KXMarketNote: View {
    var note: String
    var subdued: Bool = false
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                open.toggle()
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("DeepSeek")
                        .font(NKKXFont.mark)
                        .foregroundStyle(NKMorning.accent)
                        .tracking(0.4)
                    if !open {
                        Text(verbatim: note)
                            .font(subdued ? NKKXFont.names : NKKXFont.market)
                            .foregroundStyle(subdued ? NKMorning.textSecondary : NKMorning.textPrimary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                    Text(open ? "收起" : "展开")
                        .font(NKKXFont.disclosure)
                        .foregroundStyle(NKMorning.accent)
                }
            }
            .buttonStyle(.plain)
            if open {
                Text(verbatim: note)
                    .font(subdued ? NKKXFont.names : NKKXFont.market)
                    .foregroundStyle(subdued ? NKMorning.textSecondary : NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

struct KXRoomSection: View {
    var room: KXRoomGroups
    var board: KXBoard
    var style: KXBoardStyle

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(room.room.title)
                    .font(NKKXFont.section)
                    .foregroundStyle(NKMorning.textSecondary)
                Spacer(minLength: 8)
                Text("\(room.groups.count) 组")
                    .font(NKKXFont.sectionCount)
                    .foregroundStyle(NKMorning.textTertiary)
                    .monospacedDigit()
            }
            .padding(.horizontal, style.pageInset)
            .padding(.top, 12)
            .padding(.bottom, 6)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isHeader)

            if room.groups.isEmpty {
                Text(KXCopy.emptyRoom)
                    .font(NKKXFont.empty)
                    .foregroundStyle(NKMorning.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 14)
                    .background(NKMorning.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .padding(.horizontal, style.cardInset)
                    .padding(.bottom, 10)
            } else {
                ForEach(Array(room.groups.enumerated()), id: \.offset) { _, group in
                    KXGroupCard(group: group, room: room.room, board: board, style: style)
                        .padding(.horizontal, style.cardInset)
                        .padding(.bottom, 10)
                }
            }
        }
    }
}

struct KXGroupCard: View {
    var group: KXGroup
    var room: KXRoom
    var board: KXBoard
    var style: KXBoardStyle
    @State private var width: CGFloat = 1200

    private var mismatches: [String] { KXCopy.mismatches(group: group, board: board) }
    private var deepseek: KXDeepseekBody? { KXCopy.deepseekBody(group) }
    private var showsDeepseek: Bool { deepseek != nil || !mismatches.isEmpty }
    private var sideBySide: Bool {
        guard style == .mac else { return false }
        let floor: CGFloat = group.followers.isEmpty ? 520 : 560
        return width >= floor
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if style == .phone {
                leaders.padding(.top, 6)
                if showsDeepseek {
                    deepseekBox.padding(.top, 8)
                }
                if !group.followers.isEmpty {
                    followers(divided: false).padding(.top, 8)
                }
            } else if !sideBySide {
                leaders.padding(.top, 6)
                if !group.followers.isEmpty {
                    followers(divided: false).padding(.top, 8)
                }
                if showsDeepseek {
                    deepseekBox.padding(.top, 8)
                }
            } else if group.followers.isEmpty {
                HStack(alignment: .top, spacing: 18) {
                    leaders.frame(maxWidth: .infinity, alignment: .topLeading)
                    if showsDeepseek {
                        deepseekBox.frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                }
                .padding(.top, 6)
            } else {
                HStack(alignment: .top, spacing: 18) {
                    leaders.frame(maxWidth: .infinity, alignment: .topLeading)
                    followers(divided: true)
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
                .padding(.top, 6)
                if showsDeepseek {
                    deepseekBox.padding(.top, 8)
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NKMorning.card, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onGeometryChange(for: CGFloat.self) { proxy in
            proxy.size.width
        } action: { width = $0 }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(verbatim: group.theme ?? KXCopy.dash)
                .font(NKKXFont.theme)
                .foregroundStyle(NKMorning.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
            KXWrapLayout(spacing: 6, lineSpacing: 4) {
                Text(verbatim: KXCopy.roomSince(group))
                    .font(NKKXFont.meta)
                    .foregroundStyle(NKMorning.textSecondary)
                ForEach(Array(KXCopy.flagLabels(group).enumerated()), id: \.offset) { _, label in
                    KXTag(text: label)
                }
            }
            .padding(.top, 4)
            if room == .eliminated {
                Text(verbatim: KXCopy.eliminatedLine(group))
                    .font(NKKXFont.meta)
                    .foregroundStyle(NKMorning.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
            }
        }
    }

    private var leaders: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(group.leaders.enumerated()), id: \.offset) { index, stock in
                if index > 0 {
                    Rectangle()
                        .fill(NKMorning.hairline)
                        .frame(height: 0.5)
                        .padding(.vertical, 8)
                }
                KXLeaderRow(stock: stock)
            }
        }
    }

    private var deepseekBox: some View {
        KXDeepseekBox(summary: deepseek, mismatches: mismatches)
    }

    private func followers(divided: Bool) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("小弟 \(group.followers.count)")
                .font(NKKXFont.followerLabel)
                .foregroundStyle(NKMorning.textTertiary)
                .padding(.bottom, 4)
            ForEach(Array(group.followers.enumerated()), id: \.offset) { _, stock in
                KXFollowerRow(stock: stock)
            }
        }
        .padding(.leading, divided ? 16 : 0)
        .overlay(alignment: .leading) {
            if divided {
                Rectangle().fill(NKMorning.hairline).frame(width: 0.5)
            }
        }
    }
}

struct KXDeepseekBox: View {
    var summary: KXDeepseekBody?
    var mismatches: [String]
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(mismatches.enumerated()), id: \.offset) { _, text in
                VStack(alignment: .leading, spacing: 2) {
                    Text(KXCopy.mismatchTitle)
                        .font(NKKXFont.extra.weight(.semibold))
                        .foregroundStyle(NKMorning.problem)
                    Text(verbatim: text)
                        .font(NKKXFont.extra)
                        .foregroundStyle(NKMorning.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.bottom, 4)
            }
            switch summary {
            case .judgment(let verdict, let reason, let title, let lines):
                HStack(spacing: 6) {
                    Text("DeepSeek")
                        .font(NKKXFont.mark)
                        .foregroundStyle(NKMorning.accent)
                        .tracking(0.4)
                    Text(verbatim: verdict)
                        .font(NKKXFont.verdict)
                        .foregroundStyle(Color.white)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 1)
                        .background(NKMorning.accent, in: RoundedRectangle(cornerRadius: 5, style: .continuous))
                }
                Text(verbatim: reason)
                    .font(NKKXFont.reason)
                    .foregroundStyle(NKMorning.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 4)
                KXFoldButton(title: title, open: $open)
                    .padding(.top, 6)
                if open {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(lines, id: \.label) { line in
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                Text(line.label)
                                    .font(NKKXFont.extra.weight(.semibold))
                                    .foregroundStyle(NKMorning.textSecondary)
                                    .frame(width: 36, alignment: .leading)
                                Text(verbatim: line.value)
                                    .font(NKKXFont.extra)
                                    .foregroundStyle(NKMorning.textPrimary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                    .padding(.top, 4)
                }
            case .note(let note):
                KXMarketNote(note: note, subdued: true)
            case nil:
                EmptyView()
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NKKX.deepseekFill, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

struct KXLeaderRow: View {
    var stock: KXStock

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            KXQuoteLine(stock: stock, showsInlineStatus: false)
            KXRichText.leaderFacts(stock)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 3)
            if let order = KXCopy.orderLine(stock) {
                KXOrderLineView(line: order)
                    .padding(.top, 3)
            }
            if let extra = KXCopy.extraWarnings(stock) {
                Text(verbatim: extra)
                    .font(NKKXFont.why)
                    .foregroundStyle(NKMorning.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 2)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct KXFollowerRow: View {
    var stock: KXStock

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            KXQuoteLine(stock: stock, showsInlineStatus: true)
            if let bits = KXCopy.poolFacts(stock.limitUp) {
                KXRichText.factLine(bits)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 1)
                    .padding(.bottom, 4)
            }
            if let reason = KXCopy.clean(stock.reason) {
                Text(verbatim: reason)
                    .font(NKKXFont.followerReason)
                    .foregroundStyle(NKMorning.textTertiary)
                    .lineLimit(1)
                    .padding(.top, 1)
                    .padding(.bottom, 7)
            }
        }
    }
}

struct KXQuoteLine: View {
    var stock: KXStock
    var showsInlineStatus: Bool

    private var trend: KXTrend { KXCopy.trend(stock.quote.changePercent) }

    var body: some View {
        HStack(alignment: .center, spacing: 4) {
            if showsInlineStatus, stock.divergent == true {
                KXTag(text: "背离")
            }
            Text(stock.name.isEmpty ? KXCopy.dash : stock.name)
                .font(NKKXFont.name)
                .foregroundStyle(NKMorning.textPrimary)
                .lineLimit(1)
                .frame(maxWidth: 90, alignment: .leading)
            Text(verbatim: stock.code)
                .font(NKKXFont.code)
                .foregroundStyle(NKMorning.textTertiary)
                .monospacedDigit()
            Spacer(minLength: 8)
            Text(verbatim: KXCopy.price(stock.quote.price))
                .font(NKKXFont.price)
                .monospacedDigit()
                .foregroundStyle(trend.color)
                .layoutPriority(2)
            Text(verbatim: KXCopy.percent(stock.quote.changePercent))
                .font(NKKXFont.price)
                .monospacedDigit()
                .foregroundStyle(trend.color)
                .layoutPriority(2)
            if KXCopy.isAuction(stock) {
                Text("竞价中")
                    .font(NKKXFont.auction)
                    .foregroundStyle(NKMorning.accent)
                    .padding(.horizontal, 4)
                    .overlay(
                        RoundedRectangle(cornerRadius: 4, style: .continuous)
                            .stroke(NKMorning.accent, lineWidth: 1)
                    )
                    .layoutPriority(2)
            }
            if showsInlineStatus {
                KXRichText.statusLine(KXCopy.todayStatus(stock.limitUp))
                    .lineLimit(1)
                    .frame(maxWidth: 132, alignment: .trailing)
                    .layoutPriority(1)
            }
        }
    }
}

struct KXOrderLineView: View {
    var line: KXOrderLine

    var body: some View {
        KXWrapLayout(spacing: 6, lineSpacing: 2) {
            Text(line.kind == .seal ? "封单" : "买一挂单")
                .font(NKKXFont.facts)
                .foregroundStyle(NKMorning.textSecondary)
            Text(verbatim: line.amount)
                .font(NKKXFont.facts.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(NKMorning.textPrimary)
            if let delta = line.delta {
                Text(verbatim: delta.text)
                    .font(NKKXFont.facts)
                    .monospacedDigit()
                    .foregroundStyle(delta.direction.color)
            }
            if let warnings = line.warnings {
                Text(verbatim: warnings)
                    .font(NKKXFont.why)
                    .foregroundStyle(NKMorning.textTertiary)
            }
        }
    }
}

struct KXTag: View {
    var text: String

    var body: some View {
        Text(verbatim: text)
            .font(NKKXFont.tag)
            .foregroundStyle(NKMorning.textSecondary)
            .lineLimit(1)
            .padding(.horizontal, 6)
            .padding(.vertical, 1)
            .background(NKMorning.tagFill, in: RoundedRectangle(cornerRadius: 5, style: .continuous))
    }
}

enum KXRichText {
    static func leaderFacts(_ stock: KXStock) -> Text {
        let status = statusLine(KXCopy.todayStatus(stock.limitUp))
        guard let pool = KXCopy.poolFacts(stock.limitUp) else { return status }
        return status + separator() + factLine(pool)
    }

    static func statusLine(_ pieces: [KXStatusPiece]) -> Text {
        guard let first = pieces.first else { return Text(verbatim: "") }
        return pieces.dropFirst().reduce(piece(first)) { partial, item in
            partial + Text(verbatim: " ") + piece(item)
        }
    }

    static func factLine(_ bits: [KXFactBit]) -> Text {
        guard let first = bits.first else { return Text(verbatim: "") }
        return bits.dropFirst().reduce(bit(first)) { partial, item in
            partial + separator() + bit(item)
        }
    }

    private static func piece(_ item: KXStatusPiece) -> Text {
        let word = Text(verbatim: item.text).foregroundStyle(item.tone.color)
        return item.tone == .missing ? word.font(NKKXFont.facts) : word.font(NKKXFont.status)
    }

    private static func bit(_ item: KXFactBit) -> Text {
        Text(verbatim: item.text)
            .font(NKKXFont.facts)
            .foregroundStyle(item.missing ? NKMorning.textTertiary : NKMorning.textPrimary)
    }

    private static func separator() -> Text {
        Text(verbatim: " · ")
            .font(NKKXFont.facts)
            .foregroundStyle(NKMorning.textTertiary)
    }
}

struct KXWrapLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let limit = proposal.width ?? .greatestFiniteMagnitude
        var x: CGFloat = 0
        var y: CGFloat = 0
        var row: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > limit {
                x = 0
                y += row + lineSpacing
                row = 0
            }
            row = max(row, size.height)
            x += size.width + (x > 0 || size.width > 0 ? spacing : 0)
        }
        return CGSize(width: proposal.width ?? max(0, x - spacing), height: y + row)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var row: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX + 0.5 {
                x = bounds.minX
                y += row + lineSpacing
                row = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: size.width, height: size.height))
            row = max(row, size.height)
            x += size.width + spacing
        }
    }
}
