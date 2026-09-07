import SwiftUI
import Foundation

struct FocusView: View {
    @Bindable var model: AppModel
    @State private var scope: FocusScope = .current
    @State private var selectedWindowID: String?

    private var currentWindows: [K10CompanyWindow] {
        orderedWindows.filter { model.selection(for: $0)?.state == "kept" }
    }

    private var historicalWindows: [K10CompanyWindow] {
        orderedWindows.filter { window in
            guard let detail = model.selection(for: window) else { return false }
            return detail.state != "kept" && (detail.observationId != nil || !detail.analyses.isEmpty)
        }
    }

    private var orderedWindows: [K10CompanyWindow] { model.companyWindows }

    private var displayedWindows: [K10CompanyWindow] {
        scope == .current ? currentWindows : historicalWindows
    }

    private var selectedWindow: K10CompanyWindow? {
        if let selectedWindowID, let window = displayedWindows.first(where: { $0.id == selectedWindowID }) {
            return window
        }
        return displayedWindows.first
    }

    var body: some View {
        Group {
            #if os(macOS)
            desktopLayout
            #else
            phoneLayout
            #endif
        }
        .background(NK.pageBg)
        .navigationTitle("关注")
        .onAppear(perform: alignSelection)
        .onChange(of: model.selectedWindow?.id) { _, _ in alignSelection() }
        .onChange(of: scope) { _, _ in selectFirstVisibleWindow() }
    }

    #if os(macOS)
    private var desktopLayout: some View {
        HStack(spacing: 0) {
            desktopSidebar.frame(width: 300)
            Divider().overlay(NK.hairline)
            desktopDetail.frame(maxWidth: .infinity, maxHeight: .infinity)
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var desktopSidebar: some View {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                V3PageHeader(title: "关注", subtitle: "留下的公司在这里完成正反阅读；取消关注后的资料保留在历史。")
                FocusScopePicker(scope: $scope, currentCount: currentWindows.count, historyCount: historicalWindows.count)
                ScrollView {
                    LazyVStack(spacing: NKSpace.rowGap) {
                        if displayedWindows.isEmpty {
                            V3EmptyState(icon: "bookmark", title: emptyTitle, message: emptyMessage)
                        } else {
                            ForEach(displayedWindows) { window in
                                FocusSidebarRow(
                                    window: window,
                                    detail: model.selection(for: window),
                                    selected: selectedWindowID == window.id,
                                    action: {
                                        selectedWindowID = window.id
                                        model.selectedWindow = window
                                    }
                                )
                            }
                        }
                    }
                    .padding(.bottom, NKSpace.pagePadBottom)
                }
            }
            .padding(.horizontal, NKSpace.listPadH + NKSpace.listHeaderExtraH)
            .padding(.top, NKSpace.listPadTop)
            .background(NK.listBg)
    }

    @ViewBuilder private var desktopDetail: some View {
            if let window = selectedWindow, let detail = model.selection(for: window) {
                ScrollView {
                    FocusReadingPane(window: window, detail: detail, model: model)
                        .padding(.horizontal, NKSpace.pagePad)
                        .padding(.top, NKSpace.pagePad)
                        .padding(.bottom, NKSpace.pagePadBottom)
                }
            } else {
                V3EmptyState(icon: "doc.text.magnifyingglass", title: emptyTitle, message: emptyMessage)
            }
    }
    #endif

    #if !os(macOS)
    private var phoneLayout: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(title: "关注", subtitle: "正方与反方各一轮，资料截止和版本始终可追溯。")
                FocusScopePicker(scope: $scope, currentCount: currentWindows.count, historyCount: historicalWindows.count)

                if displayedWindows.isEmpty {
                    V3EmptyState(icon: "bookmark", title: emptyTitle, message: emptyMessage)
                } else {
                    LazyVStack(spacing: NKSpace.blockGap) {
                        ForEach(displayedWindows) { window in
                            FocusMobileCard(window: window, detail: model.selection(for: window), model: model)
                        }
                    }
                }
            }
            .padding(.horizontal, NKSpace.pagePad)
            .padding(.top, NKSpace.pagePad)
            .padding(.bottom, NKSpace.pagePadBottom)
        }
    }
    #endif

    private var emptyTitle: String {
        scope == .current ? "当前没有留下的公司" : "没有可回看的关注资料"
    }

    private var emptyMessage: String {
        scope == .current
            ? "在机会页留下公司后，系统会用固定资料截止时间完成正方、反方分析。"
            : "只有曾留下且已有观察或分析资料的公司会出现在这里；未处理和明确略过不会混入历史。"
    }

    private func alignSelection() {
        if let requested = model.selectedWindow,
           currentWindows.contains(where: { $0.id == requested.id }) {
            scope = .current
            selectedWindowID = requested.id
        } else if let requested = model.selectedWindow,
                  historicalWindows.contains(where: { $0.id == requested.id }) {
            scope = .history
            selectedWindowID = requested.id
        } else {
            selectFirstVisibleWindow()
        }
    }

    private func selectFirstVisibleWindow() {
        selectedWindowID = displayedWindows.first?.id
    }
}

private enum FocusScope: String, CaseIterable, Identifiable {
    case current
    case history

    var id: String { rawValue }
}

private struct FocusScopePicker: View {
    @Binding var scope: FocusScope
    let currentCount: Int
    let historyCount: Int

    var body: some View {
        HStack(spacing: 3) {
            ForEach(FocusScope.allCases) { item in
                Button { scope = item } label: {
                    Text(item == .current ? "当前关注 \(currentCount)" : "历史资料 \(historyCount)")
                        .font(NKFont.callout.weight(.medium))
                        .foregroundStyle(scope == item ? NK.accent : NK.textSecondary)
                        .frame(maxWidth: .infinity, minHeight: 34)
                        .background(scope == item ? Color.white : .clear, in: RoundedRectangle(cornerRadius: 7))
                        .overlay(RoundedRectangle(cornerRadius: 7).stroke(scope == item ? NK.hairline : .clear, lineWidth: 1))
                }.buttonStyle(.plain)
            }
        }.padding(3).background(NK.fieldBg, in: RoundedRectangle(cornerRadius: 10))
            .accessibilityLabel("关注范围")
    }
}

private struct FocusSidebarRow: View {
    let window: K10CompanyWindow
    let detail: K10SelectionDetail?
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 10) {
                V3CompanyMark(code: window.companyCode, size: 34)
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(window.companyName ?? window.companyCode).font(NKFont.headline)
                        Spacer(minLength: 4)
                        V3Pill(text: detail?.state ?? "unhandled")
                    }
                    Text(analysisProgressLabel(detail))
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                    Text("D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textTertiary)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? NK.accent.opacity(0.09) : NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(selected ? NK.accent.opacity(0.55) : NK.hairline, lineWidth: 0.5))
        }
        .buttonStyle(.plain)
    }
}

private struct FocusMobileCard: View {
    let window: K10CompanyWindow
    let detail: K10SelectionDetail?
    @Bindable var model: AppModel

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                HStack(alignment: .top, spacing: 12) {
                    V3CompanyMark(code: window.companyCode)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(window.companyName ?? window.companyCode).font(NKFont.title3)
                        Text(window.companyCode)
                            .font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                    }
                    Spacer()
                    V3Pill(text: detail?.state ?? "unhandled")
                }
                Text("D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                AnalysisProgress(detail: detail)
                Button("阅读正反观点") { model.selectedWindow = window }
                    .buttonStyle(V3SecondaryButtonStyle())
            }
        }
    }
}

struct FocusReadingPane: View {
    let window: K10CompanyWindow
    let detail: K10SelectionDetail
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            V3PageHeader(
                title: window.companyName ?? window.companyCode,
                subtitle: "\(window.companyCode) · 固定窗口 D1 \(k10DisplayTime(window.d1TradeDate)) · D2 \(k10DisplayTime(window.d2TradeDate))"
            )
            V3Card {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        V3Pill(text: detail.state)
                        Spacer()
                        Text("K10-v1.4 · 资料截止随分析记录固定")
                            .font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                    }
                    AnalysisProgress(detail: detail)
                    if let job = detail.latestJob {
                        HStack(spacing: 8) {
                            Text("任务 \(k10StatusText(job.status)) · 第 \(job.attemptCount) 次")
                            Text("截止 \(k10DisplayTime(job.inputCutoffAt))")
                        }
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                        if ["failed", "not_configured"].contains(job.status) {
                            Button("重试分析") { Task { await model.retryAnalysis(for: detail) } }
                                .buttonStyle(V3SecondaryButtonStyle())
                        }
                    }
                }
            }

            V3SectionTitle(title: "正反阅读", icon: "arrow.left.arrow.right")
            if let chain = model.analysisChains[window.companyWindowId] {
                AnalysisChainReading(chain: chain, window: window, model: model)
            } else {
                AnalysisPair(analyses: detail.analyses, model: model)
            }

            SupplementaryAnalysisForm(window: window, model: model)

            if !window.opportunities.isEmpty {
                V3SectionTitle(title: "更新与资料", icon: "doc.text.magnifyingglass")
                V3Card {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("晨间风险、资料更新和撤回均在机会详情中保留，不会改写本次正反的资料截止。")
                            .font(NKFont.callout)
                            .foregroundStyle(NK.textSecondary)
                        ForEach(window.opportunities) { opportunity in
                            HStack(alignment: .top, spacing: 8) {
                                V3Pill(text: opportunity.lifecycle)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(opportunity.catalystStage).font(NKFont.callout)
                                    Text("\(k10PublicationMarkerText(opportunity.sourceMarker))首发 \(k10DisplayTime(opportunity.availableAt))")
                                        .font(NKFont.caption)
                                        .foregroundStyle(NK.textSecondary)
                                }
                                Spacer()
                                Button("查看") { Task { await model.open(opportunity) } }
                                    .buttonStyle(V3SecondaryButtonStyle())
                                    .frame(width: 70)
                            }
                        }
                    }
                }
            }

            if detail.state == "kept" {
                Button("取消关注") { Task { await model.act("withdraw", window: window) } }
                    .buttonStyle(V3SecondaryButtonStyle())
            }
        }
        .task(id: window.companyWindowId) {
            await model.loadAnalysisChain(for: window)
            await model.loadSupplementarySources(for: window)
        }
    }
}

private struct AnalysisChainReading: View {
    let chain: K10AnalysisChain
    let window: K10CompanyWindow
    @Bindable var model: AppModel

    var body: some View {
        if chain.items.isEmpty {
            AnalysisPair(analyses: [], model: model)
        } else {
            ForEach(chain.items.sorted { $0.revision < $1.revision }) { item in
                VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                    HStack {
                        Text("分析第 \(item.revision) 版").font(NKFont.headline)
                        Spacer()
                        V3Pill(text: k10AnalysisKindText(item.kind))
                    }
                    Text("资料截止 \(k10DisplayTime(item.inputCutoffAt)) · \(triggerText(item))")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    if !item.sourceRefs.isEmpty {
                        Text("本版冻结资料").font(NKFont.caption.weight(.medium)).foregroundStyle(NK.textSecondary)
                        ForEach(item.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                    }
                    if let job = item.job {
                        Text("任务 \(k10StatusText(job.status)) · 第 \(job.attemptCount) 次\(job.error.map { " · \($0.message)" } ?? "")")
                            .font(NKFont.caption).foregroundStyle(job.error == nil ? NK.textSecondary : NK.down)
                        if ["failed", "not_configured"].contains(job.status) {
                            Button("重试本版任务") { Task { await model.retryAnalysis(job: job, companyWindowID: window.companyWindowId) } }
                                .buttonStyle(V3SecondaryButtonStyle())
                        }
                    }
                    AnalysisPair(analyses: item.analyses, model: model)
                }
                .padding(NKSpace.cardPad)
                .background(NK.cardBg, in: RoundedRectangle(cornerRadius: NKRadius.card))
                .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
            }
        }
    }

    private func triggerText(_ item: K10AnalysisChainItem) -> String {
        switch item.kind {
        case "initial": return "初始分析"
        case "user_question": return item.question.map { "追问：\($0)" } ?? "用户追问"
        case "evidence_update": return item.question.map { "补充资料：\($0)" } ?? "补充资料"
        default: return k10AnalysisKindText(item.kind)
        }
    }
}

private struct SupplementaryAnalysisForm: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel
    @State private var kind = "user_question"
    @State private var question = ""
    @State private var selectedSourceKeys: Set<String> = []

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: 10) {
                Text("补充分析 / 追问").font(NKFont.headline)
                Text("新请求只追加分析版本，不改变原机会的固定 D1/D2、选择或观察窗口。离线快照不能写入。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                Picker("请求类型", selection: $kind) {
                    Text("追问").tag("user_question")
                    Text("补充资料").tag("evidence_update")
                }.pickerStyle(.segmented)
                TextEditor(text: $question).frame(minHeight: 72)
                    .padding(6).background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                    .accessibilityLabel(kind == "user_question" ? "追问内容" : "补充说明")
                if kind == "evidence_update" {
                    if availableSources.isEmpty {
                        Text("没有可追加的已保存资料。可以改用“追问”提出问题；服务端不会接受未关联资料。")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                    } else {
                        Text("选择本窗口已关联的资料版本；可点开资料查看原文。")
                            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        ForEach(availableSources) { source in
                            HStack(alignment: .top, spacing: 8) {
                                Button { toggle(source) } label: {
                                    Image(systemName: selectedSourceKeys.contains(key(source)) ? "checkmark.circle.fill" : "circle")
                                        .foregroundStyle(selectedSourceKeys.contains(key(source)) ? NK.accent : NK.textTertiary)
                                }.buttonStyle(.plain)
                                SourceReferenceLine(source: source, model: model)
                            }
                            .padding(7)
                            .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                        }
                    }
                }
                Button(model.analysisRequestInFlightWindowIDs.contains(window.companyWindowId) ? "正在提交" : "提交补充分析") {
                    Task { await model.requestAnalysis(kind: kind, question: question, sourceRefs: sourceRefs, for: window) }
                }
                .buttonStyle(V3PrimaryButtonStyle())
                .disabled(model.analysisRequestInFlightWindowIDs.contains(window.companyWindowId) || model.offline || (kind == "user_question" && question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) || (kind == "evidence_update" && sourceRefs.isEmpty))
                if model.offline { Text("离线只读：恢复连接后才能提交。") .font(NKFont.caption).foregroundStyle(NK.amber) }
            }
        }
    }

    private var sourceRefs: [K10AnalysisDocumentReference] { availableSources.filter { selectedSourceKeys.contains(key($0)) }.compactMap { guard let documentId = $0.documentId, let revision = $0.revision else { return nil }; return K10AnalysisDocumentReference(documentId: documentId, revision: revision) } }
    private var availableSources: [K10SourceReference] {
        let sampleSources = window.samples.flatMap { $0.evidence.map(\.sourceRef) }
        let morningSources = model.morningReport?.items.filter { $0.companyWindowId == window.companyWindowId }.flatMap { $0.sourceRefs + $0.independentVerificationRefs } ?? []
        let chainSources = model.analysisChains[window.companyWindowId]?.items.flatMap { $0.sourceRefs + $0.analyses.flatMap(\.sourceRefs) } ?? []
        let lifecycleSources = window.opportunities.flatMap { model.opportunityDetails[$0.opportunityId]?.lifecycleEvents.flatMap(\.sourceRefs) ?? [] }
        var seen = Set<String>()
        return (sampleSources + morningSources + chainSources + lifecycleSources).filter { source in
            guard source.documentId != nil, source.revision != nil else { return false }
            return seen.insert(key(source)).inserted
        }
    }
    private func key(_ source: K10SourceReference) -> String { "\(source.documentId ?? "")#\(source.revision.map(String.init) ?? "")" }
    private func toggle(_ source: K10SourceReference) { let sourceKey = key(source); if selectedSourceKeys.contains(sourceKey) { selectedSourceKeys.remove(sourceKey) } else { selectedSourceKeys.insert(sourceKey) } }

}

private struct AnalysisPair: View {
    let analyses: [K10Analysis]
    @Bindable var model: AppModel

    private var pro: K10Analysis? { latest(role: "pro") }
    private var con: K10Analysis? { latest(role: "con") }

    var body: some View {
        #if os(macOS)
        HStack(alignment: .top, spacing: NKSpace.blockGap) {
            analysisColumn(role: "pro", analysis: pro).frame(maxWidth: .infinity, alignment: .topLeading)
            analysisColumn(role: "con", analysis: con).frame(maxWidth: .infinity, alignment: .topLeading)
        }
        #else
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            analysisColumn(role: "pro", analysis: pro)
            analysisColumn(role: "con", analysis: con)
        }
        #endif
    }

    @ViewBuilder private func analysisColumn(role: String, analysis: K10Analysis?) -> some View {
        if let analysis {
            AnalysisBlock(analysis: analysis, model: model)
        } else {
            AnalysisWaitingCard(role: role)
        }
    }

    private func latest(role: String) -> K10Analysis? {
        analyses.filter { $0.role == role }.sorted { $0.revision > $1.revision }.first
    }
}

struct AnalysisBlock: View {
    let analysis: K10Analysis
    @Bindable var model: AppModel
    @State private var expanded = false
    @State private var document: K10DocumentPage?

    private var isPro: Bool { analysis.role == "pro" }
    private var tone: Color { isPro ? NK.up : NK.down }
    private var title: String { isPro ? "正方观点" : "反方质疑" }
    private var preview: String { firstMeaningfulSection(analysis.fullText ?? analysis.error ?? "尚未返回全文。") }

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: isPro ? "checkmark.seal" : "exclamationmark.shield")
                    .foregroundStyle(tone)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(NKFont.title3).foregroundStyle(tone)
                    Text("分析第 \(analysis.revision) 版")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                }
                Spacer()
                V3Pill(text: analysis.status)
            }

            Text("资料截至 \(k10DisplayTime(analysis.inputCutoffAt)) · \(analysis.model ?? "模型未记录")")
                .font(NKFont.caption)
                .foregroundStyle(NK.textSecondary)

            if analysis.fullText != nil {
                Text(expanded ? "完整观点" : "正文节选").font(NKFont.headline)
                K10MarkdownText(markdown: expanded ? (analysis.fullText ?? "") : preview, sourceRefs: analysis.sourceRefs) { source in
                    Task { document = await model.openDocument(source) }
                }
                Button(expanded ? "收起全文" : "阅读全文") { expanded.toggle() }
                    .buttonStyle(V3SecondaryButtonStyle())
            } else {
                Text(analysis.error ?? "模型尚未返回全文。")
                    .font(NKFont.body)
                    .foregroundStyle(analysis.status == "failed" ? NK.down : NK.textSecondary)
            }

            if expanded, !analysis.sourceRefs.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    Text("冻结资料（\(analysis.sourceRefs.count)）").font(NKFont.headline)
                    ForEach(analysis.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                }
            }
        }
        .padding(NKSpace.cardPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone.opacity(0.055), in: RoundedRectangle(cornerRadius: NKRadius.card))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(tone.opacity(0.23), lineWidth: 0.7))
        .sheet(item: $document) { SourceDocumentSheet(document: $0, model: model) }
    }

    private func firstMeaningfulSection(_ text: String) -> String {
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
        let chosen = lines.prefix(4).joined(separator: "\n")
        let content = chosen.isEmpty ? text : chosen
        guard content.count > 210 else { return content }
        let prefix = String(content.prefix(210))
        if let end = prefix.lastIndex(where: { "。；".contains($0) }), prefix.distance(from: prefix.startIndex, to: end) > 60 {
            return String(prefix[...end]) + "…"
        }
        if let partialReference = prefix.range(of: "doc_", options: .backwards),
           !prefix[partialReference.lowerBound...].contains(where: { $0.isWhitespace }) {
            return String(prefix[..<partialReference.lowerBound]) + "…"
        }
        return prefix + "…"
    }
}

private struct AnalysisWaitingCard: View {
    let role: String

    var body: some View {
        let isPro = role == "pro"
        let tone = isPro ? NK.up : NK.down
        VStack(alignment: .leading, spacing: 8) {
            Text(isPro ? "正方观点" : "反方质疑").font(NKFont.title3).foregroundStyle(tone)
            Text(isPro ? "正方尚未开始；系统不会伪造结论。" : "反方会读取固定正方全文后才开始。")
                .font(NKFont.callout)
                .foregroundStyle(NK.textSecondary)
        }
        .padding(NKSpace.cardPad)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tone.opacity(0.05), in: RoundedRectangle(cornerRadius: NKRadius.card))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(tone.opacity(0.22), lineWidth: 0.7))
    }
}

private struct AnalysisProgress: View {
    let detail: K10SelectionDetail?

    var body: some View {
        HStack(spacing: 8) {
            progressItem(title: "正方", status: latestStatus(role: "pro"), tone: NK.up)
            Image(systemName: "arrow.right").font(NKFont.caption).foregroundStyle(NK.accent)
            progressItem(title: "反方", status: latestStatus(role: "con"), tone: NK.down)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
    }

    private func progressItem(title: String, status: String?, tone: Color) -> some View {
        HStack(spacing: 4) {
            Image(systemName: status == "completed" ? "checkmark.circle.fill" : "circle")
                .foregroundStyle(status == "completed" ? tone : NK.textTertiary)
            Text("\(title)\(status.map { " · \(k10StatusText($0))" } ?? " · 等待")")
                .font(NKFont.caption)
                .foregroundStyle(status == "failed" ? NK.down : NK.textSecondary)
        }
    }

    private func latestStatus(role: String) -> String? {
        detail?.analyses.filter { $0.role == role }.sorted { $0.revision > $1.revision }.first?.status
    }
}

private func analysisProgressLabel(_ detail: K10SelectionDetail?) -> String {
    let analyses = detail?.analyses ?? []
    let pro = analyses.filter { $0.role == "pro" }.sorted { $0.revision > $1.revision }.first?.status
    let con = analyses.filter { $0.role == "con" }.sorted { $0.revision > $1.revision }.first?.status
    return "正方\(pro.map { " \(k10StatusText($0))" } ?? " 等待") → 反方\(con.map { " \(k10StatusText($0))" } ?? " 等待")"
}
