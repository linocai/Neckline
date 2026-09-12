import SwiftUI
import Foundation

struct OpportunitiesView: View {
    @Bindable var model: AppModel
    @State private var selectedCardID: String?
    private var segment: String { model.dailyWindow }
    @State private var showsHistory = false

    private var cards: [K10DailyCard] {
        segment == "evening" ? model.currentEveningCards : model.currentMorningCards
    }
    private var selectedCard: K10DailyCard? { cards.first { $0.cardId == selectedCardID } ?? cards.first }
    private var report: K10DailyReport? { segment == "evening" ? model.dailyEvening?.report : model.dailyMorning?.report }
    private var response: K10DailyReportResponse? { segment == "evening" ? model.dailyEvening : model.dailyMorning }

    var body: some View {
        Group {
            if case .ready = model.state { content }
            else if case .offline = model.state { content }
            else { K10Loading(state: model.state) }
        }
        .navigationTitle("机会")
        .task { if case .idle = model.state { await model.refresh() } }
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
                } else { emptyState }
                #else
                if let selectedCard { cardDeck(selectedCard) }
                else { emptyState }
                #endif
                if segment == "morning", model.dailyMorning?.report?.nextCursor != nil {
                    Button(model.loadingMoreDailyCards ? "正在读取…" : "继续读取晨间新增") { Task { await model.loadMoreDailyCards() } }
                        .buttonStyle(V3SecondaryButtonStyle()).disabled(model.offline || model.loadingMoreDailyCards)
                }
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
                Text("消息截至 \(k10DisplayTime(report.cutoffAt)) · 可查看 \(report.availableAt.map(k10DisplayTime) ?? "尚未发布")")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                if let verification = report.verificationCutoffAt {
                    Text("查证截至 \(k10DisplayTime(verification))").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
            }
        }
    }

    @ViewBuilder private var reportNotices: some View {
        if model.offline { NoticeLine(icon: "wifi.slash", text: "离线快照 · 仅供查看，恢复连接后再提交选择", tone: NK.amber) }
        ForEach(["evening", "morning"], id: \.self) { window in
            if let error = model.dailyReportErrors[window] {
                NoticeLine(icon: "arrow.clockwise", text: "\(window == "evening" ? "晚报" : "晨报")读取失败：\(error)。已取得的内容仍保留。", tone: NK.amber)
                Button("重新读取") { Task { await model.refresh() } }.font(NKFont.caption).foregroundStyle(NK.accent)
            }
        }
        if !cards.isEmpty, let reason = response?.reason { NoticeLine(icon: "exclamationmark.circle", text: reason.message, tone: NK.amber) }
        if let report, !["completed", "published", "available"].contains(report.status) {
            NoticeLine(icon: "clock", text: "本轮状态：\(k10StatusText(report.status))，已完成内容可查看", tone: NK.amber)
        }
        if let incomplete = report?.incompleteReviews, !incomplete.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Text("晨间复核未完成").font(NKFont.headline)
                ForEach(incomplete) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(model.companyWindows.first { $0.companyWindowId == item.companyWindowId }?.companyName ?? item.companyCode)
                            .font(NKFont.callout.weight(.medium))
                        Text(item.reason).font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                }
                Text("这些机会尚未完成晨间复核，请结合原报告阅读。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }.padding(NKSpace.cardPad).frame(maxWidth: .infinity, alignment: .leading)
                .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
        } else if let gaps = report?.coverageGaps {
            ForEach(Array(gaps.enumerated()), id: \.offset) { _, gap in
                NoticeLine(icon: "exclamationmark.circle", text: k10CoverageGapText(gap), tone: NK.amber)
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
        if !model.currentMorningUpdates.isEmpty {
            DisclosureGroup("晨间更新 · \(model.currentMorningUpdates.count) 家") {
                VStack(alignment: .leading, spacing: 12) {
                    ForEach(model.currentMorningUpdates) { card in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(card.companyName).font(NKFont.headline)
                            Text(card.summary).font(NKFont.callout)
                            ForEach(card.uncertainty, id: \.self) { Text($0).font(NKFont.caption).foregroundStyle(NK.amber) }
                            if model.currentEveningCards.contains(where: { $0.companyCode == card.companyCode }) {
                                Button("查看更新后的公司卡") {
                                    model.dailyWindow = "evening"
                                    selectedCardID = model.currentEveningCards.first { $0.companyCode == card.companyCode }?.cardId
                                }.font(NKFont.caption).foregroundStyle(NK.accent)
                            } else if let opportunity = card.catalysts.first?.opportunityId {
                                Button("查看关联机会") { Task { await model.openOpportunity(id: opportunity) } }.font(NKFont.caption)
                            }
                        }
                    }
                }.padding(.top, 10)
            }.padding(NKSpace.cardPad).background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
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
        V3EmptyState(icon: "rectangle.stack", title: emptyTitle, message: emptyMessage)
    }

    private var emptyTitle: String {
        if response?.state == "not_configured" { return "今天没跑成 · 参数未配置" }
        if model.dailyReportErrors[segment] != nil { return "暂时无法读取报告" }
        if hasEndedRecommendations { return "暂无进行中的机会" }
        guard let report else { return "等待首次报告" }
        if ["completed", "published", "available"].contains(report.status) {
            return segment == "morning" ? "本晨没有新增公司" : "本轮未推荐公司"
        }
        return "报告尚未完成"
    }

    private var emptyMessage: String {
        if response?.state == "not_configured" { return "请到设置查看缺少的参数或公司资料，配置齐全后再运行。" }
        if model.dailyReportErrors[segment] != nil { return "可以重新读取；读取失败不代表本轮没有机会。" }
        if hasEndedRecommendations { return "本轮推荐已撤回或结束观察，可在下方历史记录中查看原报告与两日窗口。" }
        if let reason = response?.reason { return reason.message }
        if let report, ["completed", "published", "available"].contains(report.status) {
            return segment == "morning" ? "已有公司的变化列在晨间更新中，原有选择继续保留。" : "本轮比较已完成，没有形成正式推荐。"
        }
        return "正式报告完成后在这里逐张查看，未操作的公司记为未处理。"
    }

    private var hasEndedRecommendations: Bool {
        let published = segment == "evening" ? model.eveningCards : (model.dailyMorning?.report?.addedCards ?? [])
        return cards.isEmpty && !published.isEmpty
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
