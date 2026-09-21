import SwiftUI
import Foundation

struct OpportunitiesView: View {
    @Bindable var model: AppModel
    @State private var selectedCardID: String?
    private var segment: String { model.dailyWindow }
    @State private var showsHistory = Self.qaStartsHistoryExpanded

    private static var qaStartsHistoryExpanded: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["NK_QA_EXPAND_HISTORY"] == "1"
        #else
        false
        #endif
    }

    private var cards: [K10DailyCard] {
        segment == "evening" ? model.currentEveningCards : model.currentMorningCards
    }
    private var selectedCard: K10DailyCard? { cards.first { $0.cardId == selectedCardID } ?? cards.first }
    private var report: K10DailyReport? { segment == "evening" ? model.dailyEvening?.report : model.dailyMorning?.report }
    private var response: K10DailyReportResponse? { segment == "evening" ? model.dailyEvening : model.dailyMorning }
    private var delivery: K10ReportDelivery? { report?.delivery }
    /// Once a formal report is published, the card list owns the first screen.  Execution
    /// diagnostics and source-only material remain available after the result instead of
    /// competing with the user's opportunity-reading flow.
    private var hasPublishedFormalDelivery: Bool {
        guard let report, report.availableAt != nil,
              let delivery, delivery.isReadableByCurrentApp else { return false }
        return ["complete", "partial"].contains(delivery.outcome)
    }

    private var shouldShowResultDiagnostic: Bool {
        guard report?.resultAvailableAt != nil else { return false }
        guard let delivery, delivery.isReadableByCurrentApp else { return true }
        return report?.availableAt == nil || !["complete", "partial"].contains(delivery.outcome)
    }

    var body: some View {
        Group {
            if case .ready = model.state { content }
            else if case .offline = model.state { content }
            else { K10Loading(state: model.state) }
        }
        .navigationTitle("机会")
        .task {
            if case .idle = model.state { await model.refresh() }
            await model.loadOpportunityContext()
        }
        .onChange(of: segment) { _, _ in
            if !cards.contains(where: { $0.id == selectedCardID }) { selectedCardID = nil }
        }
        .onChange(of: cards.map(\.cardId)) { _, ids in
            if let selectedCardID, !ids.contains(selectedCardID) { self.selectedCardID = nil }
        }
    }

    private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                header
                reportNotices
                #if os(macOS)
                if !cards.isEmpty {
                    HStack(alignment: .top, spacing: NKSpace.pagePad) {
                        cardList.frame(width: 260)
                        if let selectedCard {
                            cardDeck(selectedCard).frame(maxWidth: 760)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                } else {
                    if k10ShowsOpportunityEmptyState(segment: segment, currentMorningUpdateCount: model.currentMorningUpdates.count) {
                        emptyState
                    }
                }
                #else
                if let selectedCard {
                    cardDeck(selectedCard)
                } else {
                    if k10ShowsOpportunityEmptyState(segment: segment, currentMorningUpdateCount: model.currentMorningUpdates.count) {
                        emptyState
                    }
                }
                #endif
                if report?.nextCursor != nil {
                Button(model.loadingMoreDailyCards ? "正在读取…" : "继续读取\(segment == "morning" ? "晨间新增" : "晚间报告")") { Task { await model.loadMoreDailyCards() } }
                    .buttonStyle(V3SecondaryButtonStyle()).disabled(model.offline || model.loadingMoreDailyCards)
                }
                postCardNotices
                DisclosureGroup("历史记录与已结束机会", isExpanded: $showsHistory) {
                    LazyVStack(spacing: NKSpace.blockGap) {
                        ForEach(model.historicalLifecycleUpdates) { update in
                            DailyLifecycleUpdateRow(update: update, model: model)
                        }
                        if !model.endedDailyCards.isEmpty {
                            DisclosureGroup("已结束推荐（\(model.endedDailyCards.count)）") {
                                ForEach(model.endedDailyCards) { card in
                                    DailyCompanyCard(card: card, model: model)
                                }
                            }
                        }
                        ForEach(model.companyWindows) { HistoryWindowRow(window: $0, model: model) }
                    }.padding(.top, NKSpace.blockGap)
                }.font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
            .padding(.horizontal, NKSpace.pagePad)
            .padding(.top, NKSpace.pagePad)
            .padding(.bottom, NKSpace.pagePadBottom)
        }
        .background(NK.pageBg)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let selectedCard {
                DailyCardActions(card: selectedCard, model: model)
                    .frame(maxWidth: 760)
                    .padding(.horizontal, NKSpace.pagePad).padding(.vertical, 10)
                    .frame(maxWidth: .infinity)
                    .background(NK.pageBg)
                    .overlay(alignment: .top) { Rectangle().fill(NK.hairline).frame(height: 0.5) }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top) {
                V3PageHeader(title: segment == "evening" ? "今晚的机会" : "晨间新增", subtitle: "K10-v2 · 每日选择 · 固定两日观察")
            }
            Picker("推荐来源", selection: $model.dailyWindow) {
                Text("晚间推荐 · \(model.currentEveningCards.count)").tag("evening")
                Text("晨间新增 · \(model.currentMorningCards.count)").tag("morning")
            }.pickerStyle(.segmented)
            if let report = selectedCard?.section == "updated" ? model.dailyMorning?.report : report {
                Text("消息截至 \(k10DisplayTime(report.cutoffAt)) · \(availabilityText(for: report))")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                if let verification = report.verificationCutoffAt {
                    Text("查证截至 \(k10DisplayTime(verification))").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
            }
        }
    }

    private func availabilityText(for report: K10DailyReport) -> String {
        if report.delivery?.outcome == "failed" { return "执行失败 · 详情可查看" }
        return "可查看 \(report.availableAt.map(k10DisplayTime) ?? "尚未发布")"
    }

    @ViewBuilder private var reportNotices: some View {
        if model.offline { NoticeLine(icon: "wifi.slash", text: "离线快照 · 仅供查看，恢复连接后再提交选择", tone: NK.amber) }
        ForEach(["evening", "morning"], id: \.self) { window in
            if let error = model.dailyReportErrors[window] {
                NoticeLine(icon: "arrow.clockwise", text: "\(window == "evening" ? "晚报" : "晨报")读取失败：\(error)。已取得的内容仍保留。", tone: NK.amber)
                Button("重新读取") { Task { await model.refresh() } }.font(NKFont.caption).foregroundStyle(NK.accent)
            }
        }
        if let reason = response?.reason, delivery == nil { NoticeLine(icon: "exclamationmark.circle", text: reason.message, tone: NK.amber) }
        if let delivery, delivery.isReadableByCurrentApp, let report {
            if !hasPublishedFormalDelivery {
                ReportDeliveryCard(
                    delivery: delivery,
                    reportStatus: report.status,
                    incompleteReviewCount: report.incompleteReviews?.count ?? 0,
                    model: model
                )
            }
        } else if report?.delivery != nil {
            NoticeLine(icon: "questionmark.circle", text: "这份报告的交付协议尚未受当前版本支持，不能推断完整度。", tone: NK.amber)
        } else if report != nil {
            NoticeLine(icon: "questionmark.circle", text: "旧报告未记录完整度，请结合当时状态和资料缺口阅读。", tone: NK.textSecondary)
        }
        if !hasPublishedFormalDelivery, let report, let materials = report.materials {
            switch materials.state {
            case "available":
                if materials.count > 0 {
                    VStack(alignment: .leading, spacing: 6) {
                        NoticeLine(icon: "doc.text", text: "尚未形成正式推荐；保留 \(materials.count) 条来源陈述与不确定性材料供核对。", tone: NK.amber)
                        Button("查看核对材料") { Task { await model.openMaterials(for: report) } }
                            .font(NKFont.caption).foregroundStyle(NK.accent)
                    }
                }
            case "empty":
                NoticeLine(icon: "doc.text", text: "本轮没有可单独展示的完成材料。", tone: NK.textSecondary)
            case "unavailable":
                NoticeLine(icon: "exclamationmark.circle", text: materials.reason?.message ?? "完成材料暂时无法读取。", tone: NK.amber)
            default:
                NoticeLine(icon: "questionmark.circle", text: "完成材料状态待核，请结合报告状态阅读。", tone: NK.amber)
            }
        }
        if shouldShowResultDiagnostic, let resultAvailableAt = report?.resultAvailableAt {
            NoticeLine(icon: "clock", text: "诊断或材料可读时间：\(k10DisplayTime(resultAvailableAt))；它不启动任何观察窗口。", tone: NK.textSecondary)
        }
        if !hasPublishedFormalDelivery, let deadline = report?.deliveryDeadlineAt {
            NoticeLine(icon: "clock.badge.exclamationmark", text: "晨报最晚交付：\(k10DisplayTime(deadline))。到点后未能形成排序时只保留诊断或材料，不会补发正式推荐。", tone: NK.textSecondary)
        }
        if let report, report.delivery == nil, !["completed", "published", "available"].contains(report.status) {
            NoticeLine(icon: "clock", text: "本轮状态：\(k10StatusText(report.status))，已完成内容可查看", tone: NK.amber)
        }
    }

    @ViewBuilder private var postCardNotices: some View {
        if hasPublishedFormalDelivery, let report, let materials = report.materials {
            switch materials.state {
            case "available" where materials.count > 0:
                Button("查看 \(materials.count) 条核对材料") { Task { await model.openMaterials(for: report) } }
                    .font(NKFont.caption).foregroundStyle(NK.accent)
            case "unavailable":
                NoticeLine(icon: "exclamationmark.circle", text: materials.reason?.message ?? "核对材料暂时无法读取。", tone: NK.amber)
            default:
                EmptyView()
            }
        }
        if hasPublishedFormalDelivery, let delivery, delivery.outcome == "partial" {
            if let gap = delivery.gaps.first {
                NoticeLine(
                    icon: "exclamationmark.circle",
                    text: "正式结果已发布，但有 \(delivery.gaps.count) 项处理未完成：\(k10DeliveryGapMessageText(gap.message)) 原因：\(k10DeliveryGapReasonText(gap.reasonCode))。",
                    tone: NK.amber
                )
            } else {
                NoticeLine(
                    icon: "exclamationmark.circle",
                    text: response?.reason?.message ?? "正式结果已发布，但仍有部分处理未完成。",
                    tone: NK.amber
                )
            }
        }
        if let incomplete = report?.incompleteReviews, !incomplete.isEmpty {
            let groups = k10IncompleteReviewGroups(incomplete)
            VStack(alignment: .leading, spacing: 12) {
                Text("晨间复核未完成（\(groups.count) 家）").font(NKFont.headline)
                ForEach(groups) { group in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(model.companyWindows.first { $0.companyWindowId == group.companyWindowId }?.companyName ?? group.companyCode)
                            .font(NKFont.callout.weight(.medium))
                        Text("\(group.reviews.count) 项晨间复核未完成")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                }
                DisclosureGroup("查看未完成复核详情（\(incomplete.count) 项）") {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(groups) { group in
                            ForEach(group.reviews) { item in
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("关联机会 \(item.opportunityId)").font(NKFont.caption.weight(.medium))
                                    Text(item.reason).font(NKFont.caption).foregroundStyle(NK.amber)
                                }
                            }
                        }
                    }.padding(.top, 4)
                }
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }.padding(NKSpace.cardPad).frame(maxWidth: .infinity, alignment: .leading)
                .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
        }
        if let gaps = report?.coverageGaps, !gaps.isEmpty {
            let visibleGaps = k10VisibleCoverageGapTexts(gaps, responseReason: response?.reason?.message)
            ForEach(visibleGaps, id: \.self) { gap in
                NoticeLine(icon: "exclamationmark.circle", text: gap, tone: NK.amber)
            }
        }
        if !model.currentLifecycleUpdates.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                Text("机会变化").font(NKFont.headline)
                ForEach(model.currentLifecycleUpdates) { update in
                    DailyLifecycleUpdateRow(update: update, model: model)
                }
            }.padding(NKSpace.cardPad).frame(maxWidth: .infinity, alignment: .leading)
                .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
        }
    }

    private var cardList: some View {
        LazyVStack(spacing: 8) {
            ForEach(cards) { card in
                Button { selectedCardID = card.cardId } label: {
                    HStack(alignment: .top, spacing: 9) {
                        V3CompanyMark(code: card.companyCode, size: 34)
                        VStack(alignment: .leading, spacing: 5) {
                            Text(card.companyName).font(NKFont.headline)
                            Text(card.summary).font(NKFont.caption).foregroundStyle(NK.textSecondary).lineLimit(2)
                            if !card.allowsSelection {
                                Text("已撤回或到期").font(NKFont.caption).foregroundStyle(NK.amber)
                            }
                            Text(k10StatusText(card.currentSelectionState)).font(NKFont.caption).foregroundStyle(NK.accent)
                        }
                        Spacer(minLength: 0)
                        Text("\(card.rank)").font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                    }
                    .padding(12)
                    .background(selectedCard?.id == card.id ? NK.accent.opacity(0.07) : NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(selectedCard?.id == card.id ? NK.accent.opacity(0.5) : NK.hairline, lineWidth: 0.5))
                }.buttonStyle(.plain)
            }
        }
    }

    private var emptyState: some View {
        V3EmptyState(icon: "rectangle.stack", title: emptyPresentation.title, message: emptyPresentation.message)
    }

    private var emptyPresentation: K10OpportunityEmptyPresentation {
        k10OpportunityEmptyPresentation(
            responseState: response?.state,
            hasReportLoadError: model.dailyReportErrors[segment] != nil,
            reportStatus: report?.status,
            deliveryOutcome: report?.delivery?.isReadableByCurrentApp == true ? report?.delivery?.outcome : nil,
            segment: segment,
            currentMorningUpdateCount: model.currentMorningUpdates.count,
            hasEndedRecommendations: hasEndedRecommendations,
            responseReason: response?.reason?.message
        )
    }

    private var hasEndedRecommendations: Bool {
        let published = segment == "evening"
            ? model.eveningCards
            : (model.dailyMorning?.report?.addedCards ?? []) + (model.dailyMorning?.report?.updatedCards ?? [])
        let noCurrentMorningUpdate = segment != "morning" || model.currentMorningUpdates.isEmpty
        return cards.isEmpty && noCurrentMorningUpdate && !published.isEmpty
    }

    private func cardDeck(_ card: K10DailyCard) -> some View {
        VStack(spacing: 14) {
            DailyCompanyCard(card: card, model: model)
            HStack {
                Button { move(-1) } label: { Label("上一张", systemImage: "arrow.left") }
                    .disabled(cards.first?.id == card.id)
                Spacer()
                Text("\((cards.firstIndex { $0.id == card.id } ?? 0) + 1) / \(cards.count)").font(NKFont.caption.monospacedDigit())
                Spacer()
                Button { move(1) } label: { Label("下一张", systemImage: "arrow.right") }
                    .disabled(cards.last?.id == card.id)
            }.font(NKFont.callout).foregroundStyle(NK.accent).buttonStyle(.plain)
            Text("翻页不作选择 · 昨日入选不自动留下").font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
        .simultaneousGesture(DragGesture(minimumDistance: 40).onEnded { value in
            guard abs(value.translation.width) > abs(value.translation.height) else { return }
            move(value.translation.width < 0 ? 1 : -1)
        })
    }

    private func move(_ direction: Int) {
        guard let card = selectedCard, let index = cards.firstIndex(where: { $0.id == card.id }) else { return }
        selectedCardID = cards[min(max(index + direction, 0), cards.count - 1)].id
    }
}

/// A report-scoped, read-only fallback for completed investigation material. It deliberately
/// has no card layout, rank, selection button, D1/D2 label or link into an opportunity window.
struct ReportMaterialsSheet: View {
    let report: K10DailyReport
    @Bindable var model: AppModel

    var body: some View {
        NavigationStack {
            Group {
                if let error = model.reportMaterialsError {
                    VStack(spacing: 14) {
                        V3EmptyState(icon: "exclamationmark.triangle", title: "暂时无法读取完成材料", message: error)
                        Button("重新读取") { Task { await model.openMaterials(for: report) } }
                            .buttonStyle(V3SecondaryButtonStyle())
                    }
                } else if let page = model.reportMaterials, page.reportId == report.reportId {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: NKSpace.cardGap) {
                            NoticeLine(icon: "doc.text", text: "这些是来源陈述与不确定性材料，不表示排序、推荐、选择或两日观察。", tone: NK.amber)
                            ForEach(page.items) { item in
                                V3Card {
                                    VStack(alignment: .leading, spacing: 10) {
                                        Text(item.eventTitle).font(NKFont.headline)
                                        if let asOf = item.asOf {
                                            Text("资料截至 \(k10DisplayTime(asOf))").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                        }
                                        if !item.facts.isEmpty {
                                            Text("来源陈述").font(NKFont.callout.weight(.medium))
                                            ForEach(item.facts) { fact in
                                                Text(fact.text).font(NKFont.callout)
                                                ForEach(fact.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                                            }
                                        }
                                        if !item.companyRelations.isEmpty {
                                            Text("公司关联").font(NKFont.callout.weight(.medium))
                                            ForEach(item.companyRelations) { relation in
                                                VStack(alignment: .leading, spacing: 3) {
                                                    Text("\(relation.companyName ?? relation.companyCode) · \(relation.relation)").font(NKFont.callout)
                                                    ForEach(relation.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                                                }
                                            }
                                        }
                                        if !item.uncertainties.isEmpty {
                                            Text("仍有不确定性").font(NKFont.callout.weight(.medium))
                                            ForEach(item.uncertainties, id: \.self) { uncertainty in
                                                Text(uncertainty).font(NKFont.caption).foregroundStyle(NK.amber)
                                            }
                                        }
                                        if !item.sourceRefs.isEmpty {
                                            DisclosureGroup("本条资料来源") {
                                                ForEach(item.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                                            }
                                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                        }
                                    }
                                }
                            }
                            if page.page.nextCursor != nil {
                                Button(model.loadingMoreReportMaterials ? "正在读取…" : "继续读取材料") {
                                    Task { await model.loadMoreReportMaterials() }
                                }
                                .buttonStyle(V3SecondaryButtonStyle())
                                .disabled(model.loadingMoreReportMaterials || model.offline)
                            }
                        }
                        .padding(NKSpace.pagePad)
                    }
                } else {
                    ProgressView("正在读取完成材料")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(NK.pageBg)
            .navigationTitle("完成材料")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { model.selectedMaterialsReport = nil }
                }
            }
        }
        .task {
            if model.reportMaterials?.reportId != report.reportId, model.reportMaterialsError == nil {
                await model.openMaterials(for: report)
            }
        }
    }
}

private struct ReportDeliveryCard: View {
    let delivery: K10ReportDelivery
    let reportStatus: String
    let incompleteReviewCount: Int
    @Bindable var model: AppModel

    private var presentation: K10DeliveryPresentation {
        k10DeliveryPresentation(
            outcome: delivery.outcome,
            reportStatus: reportStatus,
            incompleteReviewCount: incompleteReviewCount
        )
    }

    private var tone: Color {
        switch presentation.tone {
        case .positive: return NK.up
        case .caution: return NK.amber
        case .negative: return NK.down
        }
    }

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Label(presentation.title, systemImage: presentation.tone == .positive ? "checkmark.seal" : "exclamationmark.triangle")
                        .font(NKFont.headline).foregroundStyle(tone)
                    Spacer()
                }
                Text(presentation.message).font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("标题 \(delivery.counts.titleProcessed)/\(delivery.counts.titleInput) · 事件 \(delivery.counts.eventProcessed)/\(delivery.counts.eventInput) · 已发布 \(delivery.counts.publishedCompanies) 家")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                if delivery.counts.titleFailed + delivery.counts.titleUnprocessed + delivery.counts.eventFailed + delivery.counts.eventUnprocessed > 0 {
                    Text("未完成：标题 \(delivery.counts.titleFailed + delivery.counts.titleUnprocessed) · 事件 \(delivery.counts.eventFailed + delivery.counts.eventUnprocessed)")
                        .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.amber)
                }
                if !delivery.gaps.isEmpty {
                    DisclosureGroup("执行缺口（\(delivery.gaps.count)）") {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(delivery.gaps) { gap in
                                DeliveryGapRow(gap: gap, model: model)
                            }
                        }.padding(.top, 6)
                    }
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                }
            }
        }
    }
}

private struct DeliveryGapRow: View {
    let gap: K10ReportDeliveryGap
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(k10ExecutionStageText(gap.stage))
                .font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.textPrimary)
            Text(k10DeliveryGapMessageText(gap.message)).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("原因：\(k10DeliveryGapReasonText(gap.reasonCode))")
                .font(NKFont.caption).foregroundStyle(NK.amber)
            if gap.companyScopeKnown {
                Text(gap.companyCodes.isEmpty ? "影响公司范围已确认" : "影响公司：\(gap.companyCodes.joined(separator: "、"))")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            } else {
                Text("影响公司范围未确定，不能把这部分当作已筛除。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            if !gap.sourceRefs.isEmpty {
                DisclosureGroup("关联资料（\(gap.sourceRefs.count)）") {
                    ForEach(gap.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                }.font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        }
        .padding(9)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }
}

private struct DailyLifecycleUpdateRow: View {
    let update: K10DailyLifecycleUpdate
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(update.companyName).font(NKFont.headline)
                Spacer()
                Text(update.label).font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.amber)
            }
            Text(update.reason).font(NKFont.callout).fixedSize(horizontal: false, vertical: true)
            Text("\(update.companyCode) · \(k10DisplayTime(update.createdAt))").font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if let window = model.companyWindows.first(where: { $0.companyWindowId == update.companyWindowId }) {
                Text("原窗口 D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
            Button("查看变化与原始依据") { Task { await model.openOpportunity(id: update.opportunityId) } }
                .font(NKFont.caption).foregroundStyle(NK.accent).disabled(model.offline)
            if !update.sourceRefs.isEmpty {
                DisclosureGroup("来源（\(update.sourceRefs.count)）") {
                    ForEach(update.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                }.font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

private struct DailyCompanyCard: View {
    let card: K10DailyCard
    @Bindable var model: AppModel

    var body: some View {
        OpportunityCardStack {
            V3Card {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        V3CompanyMark(code: card.companyCode)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(card.companyName).font(NKFont.title3)
                            Text("\(card.companyCode) · \(card.strategyVersion)").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        }
                        Spacer(minLength: 4)
                        V3Pill(text: card.currentSelectionState)
                    }
                    HStack(spacing: 7) {
                        if card.isUnverified { V3Pill(text: "未核实") }
                        if card.sampleClass == "overlap" { V3Pill(text: "重叠机会") }
                        if card.section == "updated" { V3Pill(text: "晨间更新") }
                        if card.section == "added" { V3Pill(text: "晨间新增") }
                        if isLate { V3Pill(text: "迟到发布") }
                    }
                    if Set(card.catalysts.map(\.companyWindowId)).count > 1 {
                        NoticeLine(icon: "arrow.triangle.branch", text: "本次选择对应新机会；原机会的选择与两日成绩继续保留。", tone: NK.accent)
                    }
                    ForEach(card.catalysts.filter { ["risk", "withdrawn", "expired"].contains($0.lifecycleState ?? "") }) { catalyst in
                        NoticeLine(icon: "exclamationmark.circle", text: "\(catalyst.headline)：\(lifecycleLabel(catalyst.lifecycleState))", tone: NK.amber)
                    }
                    Divider().overlay(NK.hairline)
                    Text(card.catalysts.first(where: { $0.companyWindowId == card.companyWindowId })?.headline ?? card.summary).font(NKFont.headline).fixedSize(horizontal: false, vertical: true)
                    dailyLine("building.2", card.allowsSelection ? "公司关联与比较" : "原推荐理由", card.summary)
                    dailyLine("sparkle.magnifyingglass", card.allowsSelection ? "为什么关注两日" : "原两日观察依据", card.twoDayReason)
                    dailyLine("chart.line.uptrend.xyaxis", "已有价格反应", card.priceReaction ?? "此报告未保存价格反应资料。")
                    if let price = card.priceContext {
                        DisclosureGroup("行情依据 · 截至 \(k10DisplayTime(price.asOf))") {
                            ForEach(price.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                        }.font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                    ForEach(card.uncertainty, id: \.self) { value in
                        NoticeLine(icon: "questionmark.circle", text: value, tone: NK.amber)
                    }
                    Label("D1 \(k10DisplayTime(card.d1TradeDate)) · D2 \(k10DisplayTime(card.d2TradeDate))", systemImage: "calendar")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    DisclosureGroup("催化、比较与原始依据（\(card.catalysts.count)）") {
                        VStack(alignment: .leading, spacing: 14) {
                            ForEach(card.catalysts) { catalyst in
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(catalyst.headline).font(NKFont.headline)
                                    Text(catalyst.summary).font(NKFont.callout)
                                    if let lifecycle = catalyst.lifecycleState {
                                        Text(lifecycleLabel(lifecycle)).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                    }
                                    Text("\(classificationText(catalyst.classification)) · \(catalyst.verificationStatus == "unverified" ? "未核实" : k10StatusText(catalyst.verificationStatus))")
                                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                    if let id = catalyst.opportunityId {
                                        Button("查看完整比较与机会记录") { Task { await model.openOpportunity(id: id) } }.font(NKFont.caption).foregroundStyle(NK.accent)
                                    }
                                }
                            }
                            ForEach(card.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                        }.padding(.top, 10)
                    }.font(NKFont.callout).foregroundStyle(NK.accent)
                }
            }
        }
    }
    private var isLate: Bool { card.latePublication == true || model.companyWindows.first { $0.id == card.companyWindowId }?.opportunities.contains { $0.latePublication == true } == true }
    private func lifecycleLabel(_ value: String?) -> String {
        ["active": "观察中", "risk": "存在重要风险", "withdrawn": "理由已撤回", "expired": "观察已到期"][value ?? ""] ?? "状态未知"
    }
    private func dailyLine(_ icon: String, _ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon).foregroundStyle(NK.accent).frame(width: 18)
            VStack(alignment: .leading, spacing: 4) {
                Text(label).font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.accent)
                Text(value).font(NKFont.callout).foregroundStyle(NK.textPrimary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
    private func classificationText(_ value: String) -> String {
        ["initial": "首次机会", "material_stage": "新的实质阶段", "independent": "独立新催化", "continuation": "沿用原观察窗口", "needs_review": "资料待核", "invalidated": "理由已撤回"][value] ?? "分类未识别"
    }
}

private struct DailyCardActions: View {
    let card: K10DailyCard
    @Bindable var model: AppModel
    var body: some View {
        VStack(spacing: 7) {
            HStack(spacing: 7) {
                Text(card.allowsSelection ? "当前选择" : "历史记录").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                Text(card.companyName).font(NKFont.headline)
                Spacer(minLength: 4)
                if Set(card.catalysts.map(\.companyWindowId)).count > 1 {
                    Text("对应新机会").font(NKFont.caption).foregroundStyle(NK.accent)
                }
            }
            HStack(spacing: 10) {
                if !card.allowsSelection {
                    if card.currentSelectionState == "kept" {
                        Button("取消关注") { Task { await model.act("withdraw", card: card) } }.buttonStyle(V3SecondaryButtonStyle())
                    }
                    if let opportunity = card.catalysts.first(where: { $0.companyWindowId == card.companyWindowId })?.opportunityId {
                        Button("查看机会记录") { Task { await model.openOpportunity(id: opportunity) } }.buttonStyle(V3PrimaryButtonStyle())
                    }
                } else if card.currentSelectionState == "kept" {
                    Button("取消关注") { Task { await model.act("withdraw", card: card) } }.buttonStyle(V3SecondaryButtonStyle())
                    Button("查看正反分析") { model.tab = .focus; model.selectedWindow = model.companyWindows.first { $0.id == card.companyWindowId } }.buttonStyle(V3PrimaryButtonStyle())
                } else if card.currentSelectionState == "skipped" {
                    Text("已明确略过").font(NKFont.callout).foregroundStyle(NK.textSecondary).frame(maxWidth: .infinity)
                    Button("找回") { Task { await model.act("restore", card: card) } }.buttonStyle(V3PrimaryButtonStyle())
                } else {
                    Button { Task { await model.act("skip", card: card) } } label: { Label("略过", systemImage: "xmark.circle") }.buttonStyle(V3SecondaryButtonStyle())
                    Button { Task { await model.act("keep", card: card) } } label: { Label("留下观察", systemImage: "bookmark") }.buttonStyle(V3PrimaryButtonStyle())
                }
            }.disabled(model.offline)
            Text(card.allowsSelection ? "留下后开始正反分析 · 未操作记为未处理" : "该机会已撤回或到期 · 原选择和两日成绩保留").font(NKFont.caption).foregroundStyle(NK.textSecondary)
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
                Text(window.strategyVersion ?? "策略版本未记录")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                Text("D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
            }
            VStack(spacing: 12) {
                Button("查看") {
                    guard let opportunity = window.opportunities.first else { return }
                    Task { await model.open(opportunity) }
                }
                if window.currentSelectionState == "skipped", window.allowsSelection {
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
