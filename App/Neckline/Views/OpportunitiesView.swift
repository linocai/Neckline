import SwiftUI
import Foundation

struct OpportunitiesView: View {
    @Bindable var model: AppModel
    @State private var phoneIndex = 0
    @State private var macSelectedWindowID: String?
    @State private var showsHistory = false

    var body: some View {
        Group {
            if case .ready = model.state {
                content
            } else if case .offline = model.state {
                content
            } else {
                K10Loading(state: model.state)
            }
        }
        .navigationTitle("机会")
        .task {
            if case .idle = model.state { await model.refresh() }
        }
        .onChange(of: openWindows.map(\.companyWindowId)) { _, ids in
            phoneIndex = min(phoneIndex, max(ids.count - 1, 0))
            if let selected = macSelectedWindowID, !ids.contains(selected) {
                macSelectedWindowID = ids.first
            } else if macSelectedWindowID == nil {
                macSelectedWindowID = ids.first
            }
        }
    }

    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                OpportunityHeader(
                    openCount: openWindows.count,
                    totalCount: model.companyWindows.count,
                    availableAt: model.lastAvailableAt,
                    offline: model.offline,
                    openSettings: { model.tab = .settings }
                )

                if let error = model.morningReportLoadError {
                    MorningReportRefreshNotice(report: model.morningReport, error: error, model: model)
                }
                if let morningReport = model.morningReport {
                    MorningReportCard(report: morningReport, model: model)
                }

                #if os(macOS)
                desktopContent
                #else
                phoneContent
                #endif

                historySection
            }
            .padding(.horizontal, NKSpace.pagePad)
            .padding(.top, NKSpace.pagePad)
            .padding(.bottom, NKSpace.pagePadBottom)
        }
        .background(NK.pageBg)
        #if os(iOS)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let window = openWindows[safe: boundedPhoneIndex] {
                VStack(spacing: 10) {
                    SelectionButtons(
                        window: window,
                        state: window.currentSelectionState ?? "unhandled",
                        model: model
                    )
                }
                .padding(.horizontal, NKSpace.pagePad)
                .padding(.vertical, 12)
                .background(NK.pageBg)
                .overlay(alignment: .top) { Rectangle().fill(NK.hairline).frame(height: 0.5) }
            }
        }
        #endif
    }

    #if os(macOS)
    @ViewBuilder private var desktopContent: some View {
        if openWindows.isEmpty {
            OpportunityCompletionState(hasHistory: !historyWindows.isEmpty, model: model, revealHistory: { showsHistory = true })
        } else {
            HStack(alignment: .top, spacing: 0) {
                OpportunityWindowList(
                    windows: openWindows,
                    selectedID: $macSelectedWindowID,
                    model: model
                )
                .frame(width: 286)

                Divider().overlay(NK.hairline)

                if let selected = selectedDesktopWindow {
                    CompanyWindowCard(
                        window: selected,
                        model: model,
                        ordinal: openWindows.firstIndex(where: { $0.id == selected.id }).map { $0 + 1 },
                        total: openWindows.count,
                        onPrevious: moveDesktopBackward,
                        onNext: moveDesktopForward
                    )
                    .frame(maxWidth: 760)
                    .padding(.leading, NKSpace.pagePad)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(NK.listBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
        }
    }
    #else
    @ViewBuilder private var phoneContent: some View {
        if openWindows.isEmpty {
            OpportunityCompletionState(hasHistory: !historyWindows.isEmpty, model: model, revealHistory: { showsHistory = true })
        } else if let window = openWindows[safe: boundedPhoneIndex] {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                CompanyWindowCard(
                    window: window,
                    model: model,
                    ordinal: boundedPhoneIndex + 1,
                    total: openWindows.count,
                    onPrevious: movePhoneBackward,
                    onNext: movePhoneForward,
                    showsSelection: false
                )
                .simultaneousGesture(
                    DragGesture(minimumDistance: 32)
                        .onEnded { value in
                            guard abs(value.translation.width) > abs(value.translation.height) else { return }
                            if value.translation.width < 0 { movePhoneForward() }
                            else { movePhoneBackward() }
                        }
                )
            }
        }
    }
    #endif

    @ViewBuilder private var historySection: some View {
        if !historyWindows.isEmpty {
            DisclosureGroup(isExpanded: $showsHistory) {
                LazyVStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    ForEach(historyWindows) { window in
                        HistoryWindowRow(window: window, model: model)
                    }
                }
                .padding(.top, NKSpace.blockGap)
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "clock.arrow.circlepath")
                        .foregroundStyle(NK.textSecondary)
                    Text("已处理与历史")
                        .font(NKFont.headline)
                    Text("\(historyWindows.count)")
                        .font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textSecondary)
                }
            }
            .padding(NKSpace.cardPad)
            .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
        }
    }

    private func isBrowsable(_ window: K10CompanyWindow) -> Bool {
        window.opportunities.contains { ["published", "evidence_update", "risk"].contains($0.lifecycle) }
    }

    private func ordered(_ windows: [K10CompanyWindow]) -> [K10CompanyWindow] { windows }

    private var openWindows: [K10CompanyWindow] {
        ordered(model.companyWindows.filter { ($0.currentSelectionState ?? "unhandled") == "unhandled" && isBrowsable($0) })
    }

    private var historyWindows: [K10CompanyWindow] {
        ordered(model.companyWindows.filter { !((($0.currentSelectionState ?? "unhandled") == "unhandled") && isBrowsable($0)) })
    }

    private var boundedPhoneIndex: Int { min(phoneIndex, max(openWindows.count - 1, 0)) }

    private func movePhoneBackward() { guard !openWindows.isEmpty else { return }; phoneIndex = max(phoneIndex - 1, 0) }
    private func movePhoneForward() { guard !openWindows.isEmpty else { return }; phoneIndex = min(phoneIndex + 1, openWindows.count - 1) }

    #if os(macOS)
    private var selectedDesktopWindow: K10CompanyWindow? {
        guard !openWindows.isEmpty else { return nil }
        return openWindows.first(where: { $0.companyWindowId == macSelectedWindowID }) ?? openWindows.first
    }
    private func moveDesktopBackward() { moveDesktop(by: -1) }
    private func moveDesktopForward() { moveDesktop(by: 1) }
    private func moveDesktop(by delta: Int) {
        guard let selected = selectedDesktopWindow, let index = openWindows.firstIndex(where: { $0.id == selected.id }) else { return }
        let next = min(max(index + delta, 0), openWindows.count - 1)
        macSelectedWindowID = openWindows[next].companyWindowId
    }
    #endif
}

private struct MorningReportRefreshNotice: View {
    let report: K10MorningReport?
    let error: String
    @Bindable var model: AppModel

    var body: some View {
        V3Card {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .foregroundStyle(NK.amber)
                VStack(alignment: .leading, spacing: 4) {
                    Text("晨报暂未刷新").font(NKFont.headline)
                    Text(detail).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    Text(error).font(NKFont.caption).foregroundStyle(NK.amber)
                }
                Spacer(minLength: 8)
                Button("重试") { Task { await model.refresh() } }
                    .buttonStyle(V3SecondaryButtonStyle())
            }
        }
        .accessibilityLabel("晨报暂未刷新，可重试")
    }

    private var detail: String {
        report.map { "仍显示截止 \(k10DisplayTime($0.cutoffAt)) 的上一份晨报。" }
            ?? "暂未取得可显示的晨报，稍后可重试。"
    }
}

private struct MorningReportCard: View {
    let report: K10MorningReport
    @Bindable var model: AppModel
    @State private var expanded = true

    private let sections: [(String, String, String)] = [
        ("major_contrary", "重大反证与撤回", "exclamationmark.triangle.fill"),
        ("thesis_changed", "论点改变", "arrow.triangle.2.circlepath"),
        ("continuing_or_expiring", "继续观察或到期", "clock.arrow.circlepath"),
        ("new", "晨间新增", "sparkles"),
        ("needs_review", "待核资料", "questionmark.circle")
    ]

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                Button { expanded.toggle() } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "sun.max.fill").foregroundStyle(NK.accent)
                        VStack(alignment: .leading, spacing: 3) {
                            Text("晨报").font(NKFont.title3)
                            Text("截止 \(k10DisplayTime(report.cutoffAt)) · 完成 \(k10DisplayTime(report.createdAt)) · 覆盖 \(coverageText)")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        }
                        Spacer()
                        V3Pill(text: report.status)
                        Image(systemName: expanded ? "chevron.up" : "chevron.down").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                }.buttonStyle(.plain)
                if expanded {
                    if !report.coverageGaps.isEmpty {
                        Label("待核：\(report.coverageGaps.map(k10CoverageGapText).joined(separator: "、"))", systemImage: "questionmark.circle")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                    ForEach(sections, id: \.0) { section in
                        let items = report.items.filter { $0.section == section.0 }
                        if !items.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Label(section.1, systemImage: section.2).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                                ForEach(items.sorted { ($0.displayRank ?? .max, $0.itemId) < ($1.displayRank ?? .max, $1.itemId) }) { item in
                                    MorningReportRow(item: item, model: model)
                                }
                            }
                        }
                    }
                    if report.items.isEmpty {
                        Text("本晨没有处于固定 D1/D2 窗口内的正式候选。")
                            .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    }
                }
            }
        }
        .accessibilityLabel("晨报，截止 \(k10DisplayTime(report.cutoffAt))")
    }

    private var coverageText: String { report.coverageStatus == "complete" ? "完整" : "待核" }
}

private struct MorningReportRow: View {
    let item: K10MorningReportItem
    @Bindable var model: AppModel
    @State private var showsSources = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text(item.companyName ?? item.companyCode ?? "正式候选").font(NKFont.callout.weight(.semibold))
                if let rank = item.displayRank { Text("#\(rank)").font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary) }
                if let selection = item.selectionState { V3Pill(text: selection) }
                Spacer(minLength: 0)
                Text(item.coverageStatus == "complete" ? "已核" : "待核").font(NKFont.caption).foregroundStyle(item.coverageStatus == "complete" ? NK.accent : NK.amber)
            }
            Text(item.summary).font(NKFont.callout)
            Text("完成 \(k10DisplayTime(item.createdAt)) · \(item.coverageStatus == "complete" ? "可核" : "等待核验") · \(deadlineText)")
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if !item.coverageGaps.isEmpty { Text("缺口：\(item.coverageGaps.map(k10CoverageGapText).joined(separator: "、"))").font(NKFont.caption).foregroundStyle(NK.amber) }
            if !sources.isEmpty {
                HStack(spacing: 8) {
                    if !item.independentVerificationRefs.isEmpty {
                        Label("含 \(item.independentVerificationRefs.count) 条独立核验", systemImage: "checkmark.seal")
                            .font(NKFont.caption).foregroundStyle(NK.accent)
                    }
                    Button { showsSources.toggle() } label: {
                        Label(showsSources ? "收起依据" : "查看依据（\(sources.count) 条）", systemImage: showsSources ? "chevron.up" : "doc.text")
                    }
                    .font(NKFont.caption.weight(.medium))
                    .foregroundStyle(NK.accent)
                    .buttonStyle(.plain)
                }
                if showsSources {
                    ForEach(sources) { SourceReferenceLine(source: $0, model: model) }
                }
            }
        }
        .padding(10)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }

    private var deadlineText: String { item.deadlineAt.map { "窗口截止 \(k10DisplayTime($0))" } ?? "窗口截止待核" }
    private var sources: [K10SourceReference] {
        var seen = Set<String>()
        return (item.independentVerificationRefs + item.sourceRefs).filter { seen.insert($0.id).inserted }
    }
}

private struct OpportunityHeader: View {
    let openCount: Int
    let totalCount: Int
    let availableAt: String?
    let offline: Bool
    let openSettings: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                V3PageHeader(
                    title: openCount == 0 ? "本轮机会" : "发现机会",
                    subtitle: subtitle
                )
                Spacer(minLength: 12)
                Button("来源与覆盖", action: openSettings)
                    .font(NKFont.callout)
                    .foregroundStyle(NK.accent)
            }

            HStack(spacing: 8) {
                Text(openCount == 0 ? "已保存 \(totalCount) 张公司卡" : "\(openCount) 张待选择 · \(totalCount) 张推荐记录")
                    .font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
                if offline { V3Pill(text: "离线只读") }
            }

            GeometryReader { proxy in
                Capsule()
                    .fill(NK.hairline)
                    .overlay(alignment: .leading) {
                        Capsule()
                            .fill(NK.accent)
                            .frame(width: progressWidth(in: proxy.size.width))
                    }
            }
            .frame(height: 3)
            .accessibilityLabel("待处理机会 \(openCount) 张，共 \(totalCount) 张")
        }
    }

    private var subtitle: String? {
        guard let availableAt else { return "K10-v1.4 · 等待可查看的发布批次" }
        return "更新于 \(k10DisplayTime(availableAt))"
    }

    private func progressWidth(in width: CGFloat) -> CGFloat {
        guard totalCount > 0 else { return 0 }
        return width * CGFloat(max(totalCount - openCount, 0)) / CGFloat(totalCount)
    }
}

private struct OpportunityWindowList: View {
    let windows: [K10CompanyWindow]
    @Binding var selectedID: String?
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.denseGap) {
            V3SectionTitle(title: "公司机会", icon: "building.2")
                .padding(.horizontal, NKSpace.cardPad)
                .padding(.top, NKSpace.cardPad)

            ForEach(windows) { window in
                let selected = selectedID == window.companyWindowId
                Button {
                    selectedID = window.companyWindowId
                } label: {
                    HStack(spacing: 10) {
                        V3CompanyMark(code: window.companyCode, size: 36)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(window.companyName ?? window.companyCode)
                                .font(NKFont.headline)
                                .foregroundStyle(NK.textPrimary)
                            Text(window.headline)
                                .font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                                .lineLimit(1)
                        }
                        Spacer(minLength: 0)
                        if window.hasRisk {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(NKFont.caption)
                                .foregroundStyle(NK.down)
                        } else {
                            Text(window.recommendationLabel)
                                .font(NKFont.caption)
                                .foregroundStyle(NK.accent)
                        }
                    }
                    .padding(.horizontal, NKSpace.cardPad)
                    .padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(selected ? NK.accent.opacity(0.08) : Color.clear, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                    .overlay(alignment: .leading) {
                        if selected { Capsule().fill(NK.accent).frame(width: 3) }
                    }
                }
                .buttonStyle(.plain)
                .padding(.horizontal, NKSpace.listPadH)
            }
            Spacer(minLength: 10)
        }
        .padding(.bottom, NKSpace.cardPad)
    }
}

struct CompanyWindowCard: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel
    let ordinal: Int?
    let total: Int?
    let onPrevious: (() -> Void)?
    let onNext: (() -> Void)?
    let showsSelection: Bool

    init(
        window: K10CompanyWindow,
        model: AppModel,
        ordinal: Int? = nil,
        total: Int? = nil,
        onPrevious: (() -> Void)? = nil,
        onNext: (() -> Void)? = nil,
        showsSelection: Bool = true
    ) {
        self.window = window
        self.model = model
        self.ordinal = ordinal
        self.total = total
        self.onPrevious = onPrevious
        self.onNext = onNext
        self.showsSelection = showsSelection
    }

    private var lead: K10PublicationSample? {
        window.samples.sorted { ($0.rank ?? .max) < ($1.rank ?? .max) }.first
    }

    private var actionOpportunity: K10Opportunity? {
        window.opportunities.first(where: { !["withdrawal", "expired"].contains($0.lifecycle) }) ?? window.opportunities.first
    }

    var body: some View {
        VStack(spacing: 14) {
            OpportunityCardStack {
                V3Card {
                    VStack(alignment: .leading, spacing: 14) {
                        cardHeader
                        Divider().overlay(NK.hairline)
                        primaryStory
                        signalSummary
                        HStack(spacing: 6) {
                            Image(systemName: "calendar")
                            Text("D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                            Spacer(minLength: 0)
                            if window.opportunities.contains(where: { $0.latePublication == true }) { V3Pill(text: "迟到") }
                        }.font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        if window.hasRisk {
                            NoticeLine(icon: "exclamationmark.triangle", text: "有重要反证，先查看最新依据", tone: NK.down)
                        }
                        CardFooter(window: window, model: model, onOpen: openDetail)
                    }
                }
            }
            if showsSelection { selectionArea }
            pagingControls
        }
    }

    private var cardHeader: some View {
        HStack(alignment: .top, spacing: 12) {
            V3CompanyMark(code: window.companyCode)
            VStack(alignment: .leading, spacing: 4) {
                Text(window.companyName ?? window.companyCode).font(NKFont.title3)
                Text("\(window.companyCode) · 创业板").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                HStack(spacing: 6) {
                    V3Pill(text: window.recommendationLabel)
                    if window.hasRisk { V3Pill(text: "风险更新") }
                    if window.sampleClass == "overlap" { V3Pill(text: "重叠样本") }
                }
            }
            Spacer(minLength: 4)
            if let ordinal, let total {
                Text("\(ordinal) / \(total)")
                    .font(NKFont.monoValue)
                    .foregroundStyle(NK.textSecondary)
            }
        }
    }

    private var primaryStory: some View {
        Text(window.sourceTitle ?? window.headline)
            .font(NKFont.headline)
            .foregroundStyle(NK.textPrimary)
            .lineLimit(2)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var signalSummary: some View {
        VStack(alignment: .leading, spacing: 12) {
            SignalLine(icon: "doc.text", title: "新增事实", text: window.headline)
            if let relation = window.relationSummary {
                SignalLine(icon: "building.2", title: "公司关联", text: relation)
            }
            if let reason = lead?.comparison.twoDayReason ?? lead?.comparison.summary ?? lead?.comparison.rationale {
                SignalLine(icon: "sparkle.magnifyingglass", title: "为什么关注", text: reason)
            }
            if let condition = lead?.comparison.rankChangeConditions {
                SignalLine(icon: "arrow.up.arrow.down", title: "改变判断的条件", text: condition)
            }
        }
    }

    @ViewBuilder private var stateNotice: some View {
        if window.hasRisk {
            NoticeLine(icon: "exclamationmark.triangle.fill", text: "出现重要反证或风险更新；先查看依据，再决定是否留下。", tone: NK.down)
        } else if window.sampleClass == "overlap" {
            NoticeLine(icon: "square.on.square", text: "与既有窗口重叠，完整观察但不计入主样本命中率。", tone: NK.amber)
        } else if window.opportunities.contains(where: { $0.latePublication == true }) {
            NoticeLine(icon: "clock.badge.exclamationmark", text: "迟到发布：固定观察从下一交易日开始。", tone: NK.amber)
        } else {
            NoticeLine(icon: "calendar", text: "固定观察：D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))", tone: NK.textSecondary)
        }
    }

    @ViewBuilder private var selectionArea: some View {
        if actionOpportunity != nil {
            SelectionButtons(
                window: window,
                state: window.currentSelectionState ?? model.selection(for: window)?.state ?? "unhandled",
                model: model
            )
        }
    }

    @ViewBuilder private var pagingControls: some View {
        if let onPrevious, let onNext, let ordinal, let total, total > 1 {
            HStack {
                Button(action: onPrevious) {
                    Label("上一张", systemImage: "arrow.left")
                }
                .buttonStyle(.borderless)
                .disabled(ordinal == 1)

                Spacer()
                Text("翻页不作选择")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                Spacer()

                Button(action: onNext) {
                    Label("下一张", systemImage: "arrow.right")
                }
                .buttonStyle(.borderless)
                .disabled(ordinal == total)
            }
        }
    }

    private func openDetail() {
        guard let opportunity = actionOpportunity else { return }
        Task { await model.open(opportunity) }
    }
}

struct CatalystList: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel
    var onOpen: ((K10Opportunity) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            V3SectionTitle(title: "同卡催化", icon: "rectangle.3.group")
            ForEach(window.opportunities) { opportunity in
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: opportunity.lifecycle == "risk" ? "exclamationmark.triangle" : "bolt")
                        .font(NKFont.caption)
                        .foregroundStyle(opportunity.lifecycle == "risk" ? NK.down : NK.accent)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(k10PublicationMarkerText(opportunity.sourceMarker) + "首发 · " + k10DisplayTime(opportunity.availableAt))
                            .font(NKFont.callout)
                        Text("资料修订 \(opportunity.eventRevision) · \(k10StatusText(opportunity.lifecycle))")
                            .font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    Button("查看") {
                        if let onOpen { onOpen(opportunity) } else { Task { await model.open(opportunity) } }
                    }.font(NKFont.caption)
                        .foregroundStyle(NK.accent)
                }
            }
        }
    }
}

struct ComparisonBlock: View {
    let sample: K10PublicationSample
    let all: [K10PublicationSample]
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 7) {
                V3Pill(text: k10CategoryText(sample.category))
                Text("公司比较")
                    .font(NKFont.headline)
            }
            if let summary = sample.comparison.summary {
                Text(summary).font(NKFont.callout)
            }
            ComparisonDetails(comparison: sample.comparison, model: model)
            if all.count > 1 {
                Text("同一公司有 \(all.count) 条已发布催化，选择和两日成绩合并记录。")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
            }
        }
    }
}

struct ComparisonDetails: View {
    let comparison: K10Comparison
    @Bindable var model: AppModel
    @State private var showsHistory = false

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.denseGap) {
            if let priorityReason = comparison.priorityReason ?? comparison.rationale {
                ComparisonDetailLine(icon: "checkmark.circle", label: "优先理由", value: priorityReason)
            }
            if let gap = comparison.gap {
                ComparisonDetailLine(icon: "arrow.left.arrow.right", label: "比较差距", value: gap)
            }
            if let conditions = comparison.rankChangeConditions {
                ComparisonDetailLine(icon: "arrow.up.arrow.down", label: "排序改变条件", value: conditions)
            }
            if let twoDayReason = comparison.twoDayReason {
                ComparisonDetailLine(icon: "calendar", label: "两日观察依据", value: twoDayReason)
            }
            Divider().overlay(NK.hairline)
            if let classification = comparison.classification {
                Text("本次为何新建机会").font(NKFont.callout.weight(.medium))
                ComparisonDetailLine(icon: "doc.text", label: "新增事实", value: classification.newFacts)
                ComparisonDetailLine(icon: "arrow.triangle.branch", label: "判断依据", value: classification.reason)
                if let changed = classification.changedJudgment, !changed.isEmpty {
                    ComparisonDetailLine(icon: "arrow.up.arrow.down", label: "改变判断", value: changed)
                }
                ComparisonDetailLine(icon: "calendar.badge.clock", label: "新两日理由", value: classification.twoDayReason)
                if let relatedID = classification.relatedOpportunityId, !relatedID.isEmpty {
                    Button {
                        Task { await model.openOpportunity(id: relatedID) }
                    } label: {
                        Label("查看关联原机会", systemImage: "arrowshape.turn.up.left")
                            .font(NKFont.caption.weight(.medium))
                            .foregroundStyle(NK.accent)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("查看关联原机会 \(relatedID)")
                }
            } else {
                ComparisonDetailLine(icon: "questionmark.circle", label: "分类说明", value: "未记录／待核")
            }
            if let coverage = comparison.historicalCoverage {
                Divider().overlay(NK.hairline)
                Button { showsHistory.toggle() } label: {
                    HStack {
                        Text("历史同类资料：\(k10StatusText(coverage.state))").font(NKFont.callout.weight(.medium))
                        Spacer()
                        Image(systemName: showsHistory ? "chevron.up" : "chevron.down").font(NKFont.caption)
                    }
                }.buttonStyle(.plain)
                Text(k10ReasonText(coverage.reason)).font(NKFont.caption).foregroundStyle(coverage.state == "complete" ? NK.textSecondary : NK.amber)
                if !coverage.missingOutcomes.isEmpty { Text("缺少：\(k10HistoricalOutcomeListText(coverage.missingOutcomes))").font(NKFont.caption).foregroundStyle(NK.amber) }
                if !coverage.sourceRefs.isEmpty {
                    Text("覆盖资料").font(NKFont.caption.weight(.medium)).foregroundStyle(NK.textSecondary)
                    ForEach(coverage.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                }
                if showsHistory {
                    if let cases = comparison.historicalCases, !cases.isEmpty {
                        ForEach(cases) { item in
                            VStack(alignment: .leading, spacing: 5) {
                                Text("\(k10HistoricalOutcomeText(item.outcome)) · \(item.summary)").font(NKFont.caption)
                                if let observedAt = item.observedAt { Text(k10DisplayTime(observedAt)).font(NKFont.caption).foregroundStyle(NK.textSecondary) }
                                if !item.sourceRefs.isEmpty {
                                    Text("案例资料").font(NKFont.caption.weight(.medium)).foregroundStyle(NK.textSecondary)
                                    ForEach(item.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                                }
                                if !item.marketFacts.isEmpty {
                                    Text("行情资料").font(NKFont.caption.weight(.medium)).foregroundStyle(NK.textSecondary)
                                    ForEach(item.marketFacts) { SourceReferenceLine(source: $0, model: model) }
                                }
                            }
                        }
                    } else { Text("未记录可展示的历史案例。") .font(NKFont.caption).foregroundStyle(NK.textTertiary) }
                }
            }
        }
    }
}

struct SelectionButtons: View {
    let window: K10CompanyWindow
    let state: String
    @Bindable var model: AppModel

    var body: some View {
        switch state {
        case "kept":
            HStack {
                Button("查看关注") { model.tab = .focus }
                    .buttonStyle(V3SecondaryButtonStyle())
                Button("取消关注", role: .destructive) {
                    Task { await model.act("withdraw", window: window) }
                }
                .buttonStyle(V3SecondaryButtonStyle())
            }
        case "skipped":
            HStack {
                Button("已明确略过") {}
                    .disabled(true)
                    .buttonStyle(V3SecondaryButtonStyle())
                Button("找回") { Task { await model.act("restore", window: window) } }
                    .buttonStyle(V3PrimaryButtonStyle())
            }
        default:
            HStack(spacing: 10) {
                Button { Task { await model.act("skip", window: window) } } label: { Label("略过", systemImage: "xmark.circle") }
                    .buttonStyle(V3SecondaryButtonStyle())
                Button { Task { await model.act("keep", window: window) } } label: { Label("留下", systemImage: "bookmark") }
                    .buttonStyle(V3PrimaryButtonStyle())
            }
            Text("留下后开始正反分析 · 未操作记为未处理")
            .font(NKFont.caption)
            .foregroundStyle(NK.textSecondary)
            .frame(maxWidth: .infinity)
        }
    }
}

private struct CardFooter: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel
    let onOpen: () -> Void
    @State private var showCatalysts = false

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onOpen) {
                Text("原始依据")
            }
            .font(NKFont.callout)
            .foregroundStyle(NK.accent)

            Spacer(minLength: 0)
            Button { showCatalysts = true } label: {
                Label("公司比较", systemImage: "arrow.right")
            }
            .font(NKFont.callout)
            .foregroundStyle(NK.accent)

        }
        .buttonStyle(.plain)
        .sheet(isPresented: $showCatalysts) { NavigationStack { CatalystDetailView(window: window, model: model) } }
    }
}

private struct CatalystDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selectedDetail: K10OpportunityDetail?
    let window: K10CompanyWindow
    @Bindable var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(title: window.companyName ?? window.companyCode, subtitle: "同公司催化、来源与比较")
                V3Card { CatalystList(window: window, model: model, onOpen: { opportunity in
                    Task { selectedDetail = await model.loadOpportunity(opportunity) }
                }) }
                if let lead = window.leadingSample {
                    V3Card { ComparisonBlock(sample: lead, all: window.samples, model: model) }
                }
            }
            .padding(NKSpace.pagePad)
        }
        .navigationTitle("机会详情")
        .toolbar { ToolbarItem(placement: .confirmationAction) { Button("关闭") { dismiss() } } }
        .frame(idealWidth: 680, idealHeight: 720)
        .sheet(item: $selectedDetail) { OpportunitySheet(detail: $0, model: model) }
    }
}

private struct HistoryWindowRow: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            V3CompanyMark(code: window.companyCode, size: 34)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(window.companyName ?? window.companyCode)
                        .font(NKFont.headline)
                    V3Pill(text: window.currentSelectionState ?? "unhandled")
                    Spacer()
                    Text(window.lifecycleLabel)
                        .font(NKFont.caption)
                        .foregroundStyle(window.hasRisk ? NK.down : NK.textSecondary)
                }
                Text(window.headline)
                    .font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
                    .lineLimit(2)
                Text("D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
            }
            VStack(spacing: 12) {
                Button("查看") {
                    guard let opportunity = window.opportunities.first else { return }
                    Task { await model.open(opportunity) }
                }
                if window.currentSelectionState == "skipped" {
                    Button("找回") { Task { await model.act("restore", window: window) } }
                }
            }
            .font(NKFont.caption)
            .foregroundStyle(NK.accent)
        }
        .padding(NKSpace.cardPad)
        .background(NK.disclosureBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }
}

private struct OpportunityCompletionState: View {
    let hasHistory: Bool
    @Bindable var model: AppModel
    let revealHistory: () -> Void

    var body: some View {
        V3EmptyState(
            icon: "checkmark.circle",
            title: hasHistory ? "暂时没有待选择的机会" : "等待新的机会",
            message: hasHistory ? "留下的公司在关注页；过往推荐和固定两日记录都可以回看。" : "还没有可查看的正式推荐。请到设置查看来源覆盖与任务状态。"
        )
        VStack(spacing: 10) {
            if hasHistory {
                Button("查看已处理与历史", action: revealHistory)
                    .buttonStyle(V3SecondaryButtonStyle())
            }
            Button("查看关注") { model.tab = .focus }
                .buttonStyle(V3PrimaryButtonStyle())
        }
        .frame(maxWidth: .infinity)
    }
}

private struct OpportunityCardStack<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        ZStack(alignment: .top) {
            RoundedRectangle(cornerRadius: NKRadius.card)
                .fill(NK.cardBg.opacity(0.72))
                .offset(y: 9)
            RoundedRectangle(cornerRadius: NKRadius.card)
                .fill(NK.cardBg.opacity(0.9))
                .offset(y: 4)
            content
                .shadow(color: Color.black.opacity(0.07), radius: 14, y: 7)
        }
        .padding(.bottom, 9)
    }
}

private struct SignalLine: View {
    let icon: String
    let title: String
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: icon)
                .font(NKFont.callout.weight(.semibold))
                .foregroundStyle(NK.accent)
                .frame(width: 16, alignment: .center)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.accent)
                Text(text).font(NKFont.callout).foregroundStyle(NK.textPrimary).lineLimit(2)
            }
        }
    }
}

private struct NoticeLine: View {
    let icon: String
    let text: String
    let tone: Color

    var body: some View {
        Label {
            Text(text).font(NKFont.caption)
        } icon: {
            Image(systemName: icon)
        }
        .foregroundStyle(tone)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(tone.opacity(0.08), in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }
}

private struct ComparisonDetailLine: View {
    let icon: String
    let label: String
    let value: String

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: icon).foregroundStyle(NK.accent).font(NKFont.caption)
            Text(label).font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.textSecondary)
            Text(value).font(NKFont.caption).foregroundStyle(NK.textPrimary)
        }
    }
}

private extension K10CompanyWindow {
    var leadingSample: K10PublicationSample? {
        samples.sorted { ($0.rank ?? .max) < ($1.rank ?? .max) }.first
    }

    var headline: String {
        let values = samples
            .flatMap(\.evidence)
            .map(\.claim)
            .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        if let claim = values.first { return claim }
        if let summary = leadingSample?.comparison.summary, !summary.isEmpty { return summary }
        return "已发布公司机会，查看依据了解新增事实与比较结论。"
    }

    var sourceTitle: String? {
        samples
            .flatMap(\.evidence)
            .compactMap { $0.sourceRef.title }
            .first { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    var relationSummary: String? {
        let technical = Set(["supports", "refutes", "neutral", "related", "direct", "indirect", "unknown"])
        if let relation = samples.flatMap(\.evidence).compactMap(\.relation).first(where: {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !technical.contains($0.lowercased())
        }) { return relation }
        return leadingSample?.comparison.priorityReason ?? leadingSample?.comparison.rationale
    }

    var recommendationLabel: String {
        guard let category = leadingSample?.category else { return "待看比较" }
        return k10CategoryText(category)
    }

    var hasRisk: Bool { opportunities.contains { $0.lifecycle == "risk" || $0.lifecycle == "withdrawal" } }

    var lifecycleLabel: String {
        if hasRisk { return "风险或撤回更新" }
        if opportunities.contains(where: { $0.lifecycle == "expired" }) { return "观察已到期" }
        return "已处理"
    }
}

private extension Collection {
    subscript(safe index: Index) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}
