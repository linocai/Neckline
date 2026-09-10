import Foundation
import SwiftUI

struct ScanCoverageSummary: View {
    @Bindable var model: AppModel
    let scans: [K10Scan]

    var body: some View {
        Group {
            if !scans.isEmpty {
                VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                    V3SectionTitle(title: "来源范围与完整性", icon: "doc.text.magnifyingglass")
                    ForEach(scans) { scan in
                        V3Card {
                            VStack(alignment: .leading, spacing: 9) {
                                HStack(alignment: .top) {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(scan.window == "evening" ? "晚间扫描" : "晨间扫描").font(NKFont.headline)
                                        Text("截止 \(k10DisplayTime(scan.cutoffAt)) · \(k10StatusText(scan.coverageStatus))")
                                            .font(NKFont.caption)
                                            .foregroundStyle(NK.textSecondary)
                                    }
                                    Spacer()
                                    V3Pill(text: scan.executionProgress?.state ?? scan.status)
                                }
                                ForEach(scan.sourceCoverage) { source in
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(k10SourceText(source.sourceKey)).font(NKFont.callout.weight(.semibold))
                                        if let scope = source.scope { Text(scope).font(NKFont.caption).foregroundStyle(NK.textSecondary) }
                                        if let watermark = source.successWatermark { Text("成功水位 \(k10DisplayTime(watermark))").font(NKFont.caption).foregroundStyle(NK.textSecondary) }
                                        Text(source.timeCoverage.map { "发布时间核验：\(k10StatusText($0))" } ?? "发布时间核验：未记录")
                                            .font(NKFont.caption)
                                            .foregroundStyle(source.timeCoverage == "complete" ? NK.textSecondary : NK.amber)
                                        if let count = source.unknownPublicationTimeCount {
                                            Text("发布时间待核 \(count) 篇")
                                                .font(NKFont.caption.monospacedDigit())
                                                .foregroundStyle(count > 0 ? NK.amber : NK.textSecondary)
                                        }
                                        if let refs = source.uncertainTimeDocumentRefs, !refs.isEmpty {
                                            DisclosureGroup("查看时间待核资料") {
                                                ForEach(refs) { SourceReferenceLine(source: $0, model: model) }
                                            }
                                            .font(NKFont.caption)
                                        }
                                        if !source.displayGaps.isEmpty {
                                            Text(source.displayGaps.joined(separator: "、")).font(NKFont.caption).foregroundStyle(NK.amber)
                                        }
                                    }
                                    .padding(9)
                                    .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                                }
                                if let replay = scan.sourceReplay {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text("晚到资料回补").font(NKFont.callout.weight(.semibold))
                                        if let start = replay.replayStartAt, let end = replay.cutoffAt {
                                            Text("回补范围：\(k10DisplayTime(start)) — \(k10DisplayTime(end))")
                                        }
                                        if let start = replay.effectiveStartAt, let end = replay.cutoffAt {
                                            Text("实际查询：\(k10DisplayTime(start)) — \(k10DisplayTime(end))")
                                        }
                                        if let start = replay.nominalStartAt {
                                            Text("本轮增量起点：\(k10DisplayTime(start))")
                                        }
                                        if let state = replay.requestState { Text("采集状态：\(k10StatusText(state))") }
                                    }
                                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                } else {
                                    Text("晚到资料回补范围未记录")
                                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                }
                                if let progress = scan.executionProgress {
                                    ExecutionProgressCard(progress: progress)
                                }
                                if let research = scan.researchSummary ?? scan.executionProgress?.researchSummary {
                                    ResearchSummaryCard(summary: research, assessments: model.researchAssessments[scan.scanId] ?? [], model: model)
                                }
                                if !scan.coverageGaps.isEmpty {
                                    Label(scan.coverageGaps.joined(separator: "、"), systemImage: "exclamationmark.triangle.fill")
                                        .font(NKFont.caption)
                                        .foregroundStyle(NK.amber)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

struct SettingsView: View {
    @Bindable var model: AppModel
    @ObservedObject var config: AppConfig
    @State private var tavilyKey = ""
    @State private var showConnectionEditor = false
    @State private var showModelEditor = false
    @State private var showSourceEditor = false

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "未知"
    }

    private var appBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "未知"
    }

    var body: some View {
        if qaCoverageRoute {
            SettingsCoverageScreen(model: model, scans: model.scanSummaries)
                .task { await model.refreshAdminSettings() }
        } else {
            settingsBody
        }
    }

    // A process-only visual-QA route. It cannot affect a normal launch and
    // lets the isolated QA app show the populated title/deep-read receipt
    // without relying on coordinate-driven navigation.
    private var qaCoverageRoute: Bool {
        #if DEBUG
        ProcessInfo.processInfo.environment["NK_QA_COVERAGE_SCREEN"] == "1"
        #else
        false
        #endif
    }

    private var settingsBody: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(title: "设置", subtitle: "查看连接、资料覆盖、任务状态与个人提醒。密钥不会在页面回显。")

                V3SectionTitle(title: "连接与版本", icon: "link")
                V3Card {
                    VStack(spacing: 0) {
                        SettingsEntry(icon: "server.rack", title: "后端连接", detail: connectionDetail, badge: connectionBadge) {
                            showConnectionEditor = true
                        }
                        SettingsDivider()
                        SettingsRowContent(icon: "number.square", title: "Neckline \(appVersion)", detail: "Build \(appBuild) · K10-v2", badge: nil, showsChevron: false)
                    }
                }

                V3SectionTitle(title: "资讯与模型", icon: "sparkles")
                V3Card {
                    VStack(spacing: 0) {
                        SettingsEntry(icon: "newspaper", title: "资讯来源", detail: sourceDetail, badge: nil) {
                            showSourceEditor = true
                        }
                        SettingsDivider()
                        SettingsEntry(icon: "cpu", title: "模型配置", detail: providerDetail, badge: nil) {
                            showModelEditor = true
                        }
                        SettingsDivider()
                        NavigationLink {
                            SettingsCoverageScreen(model: model, scans: model.scanSummaries)
                        } label: {
                            SettingsRowContent(icon: "checklist", title: "来源覆盖与缺口", detail: coverageDetail, badge: nil)
                        }
                        .buttonStyle(.plain)
                    }
                }

                V3SectionTitle(title: "运行状态", icon: "clock")
                V3Card {
                    VStack(spacing: 0) {
                        SettingsStatusRow(title: "晚间扫描", scan: model.scanSummaries.first { $0.window == "evening" })
                        SettingsDivider()
                        SettingsStatusRow(title: "晨间扫描", scan: model.scanSummaries.first { $0.window == "morning" })
                        SettingsDivider()
                        DiscoveryControlRow(control: model.operationsReadiness?.runControl, isPausing: model.discoveryPauseInFlight) {
                            Task { await model.pauseDiscovery() }
                        }
                        SettingsDivider()
                        NotificationReadinessRow(readiness: model.operationsReadiness?.notificationReadiness)
                        SettingsDivider()
                        SettingsConfigurationRows(configuration: model.configuration)
                    }
                }

                #if os(iOS)
                V3SectionTitle(title: "通知", icon: "bell")
                V3Card {
                    Button {
                        Task { await model.enableNotifications() }
                    } label: {
                        SettingsRowContent(icon: "bell.badge", title: "允许并注册 K10 通知", detail: "通知只跳转到机会、公司窗口或发布批次。", badge: nil)
                    }
                    .buttonStyle(.plain)
                }
                #endif

                V3SectionTitle(title: "阅读与提醒", icon: "bookmark")
                V3Card {
                    VStack(spacing: 0) {
                        NavigationLink { DisciplineView() } label: {
                            SettingsRowContent(icon: "list.bullet.rectangle", title: "个人纪律十条", detail: "仅供个人阅读，不会产生交易操作。", badge: nil)
                        }
                        .buttonStyle(.plain)
                        SettingsDivider()
                        SettingsUsageRow(usage: model.usage)
                    }
                }
            }
            .padding(.horizontal, NKSpace.pagePad)
            .padding(.top, NKSpace.pagePad)
            .padding(.bottom, NKSpace.pagePadBottom)
        }
        .background(NK.pageBg)
        .navigationTitle("设置")
        .task { await model.refreshAdminSettings() }
        .sheet(isPresented: $showConnectionEditor) { ConnectionEditor(config: config, model: model) }
        .sheet(isPresented: $showModelEditor) { ModelEditor(model: model) }
        .sheet(isPresented: $showSourceEditor) { SourceEditor(model: model, tavilyKey: $tavilyKey) }
    }

    private var connectionName: String { config.baseURLOverride.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? config.environment.shortLabel : "自定义服务" }

    private var connectionDetail: String {
        if let error = config.connectionConfigurationError { return error }
        switch model.state {
        case .ready:
            return "\(connectionName) · 当前服务响应正常"
        case .loading:
            return "\(connectionName) · 正在检查服务"
        case .offline(let message), .unavailable(let message), .failed(let message):
            return message
        case .idle:
            return config.hasToken ? "\(connectionName) · API Token 已配置，尚未验证连接" : "尚未配置 API Token"
        }
    }

    private var connectionBadge: String {
        switch model.state {
        case .ready: return "已连接"
        case .loading: return "检查中"
        case .offline: return "离线"
        case .unavailable: return "不可用"
        case .failed: return "失败"
        case .idle: return config.hasToken ? "已配置" : "待配置"
        }
    }
    private var sourceDetail: String { model.tavilyKeySet ? "Tavily 定向核验已配置；覆盖和缺口按扫描显示。" : "Tavily 密钥未配置；不会把来源不足当作空结果。" }
    private var providerDetail: String {
        let active = model.providers.filter(\.enabled)
        guard let provider = active.first else { return "添加端点、模型名称和 API Key" }
        return "\(provider.name) · \(provider.model) · \(provider.keySet ? "Key 已配置" : "缺少 Key")"
    }
    private var coverageDetail: String { model.scanSummaries.isEmpty ? "尚无扫描回执" : "查看来源范围、成功水位与缺口" }
}

struct SettingsCoverageScreen: View {
    @Bindable var model: AppModel
    let scans: [K10Scan]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(title: "来源覆盖", subtitle: "覆盖、权限与失败状态如实显示。")
                if scans.isEmpty {
                    V3EmptyState(icon: "doc.text.magnifyingglass", title: "尚无扫描回执", message: "扫描执行后会在这里显示来源范围与缺口。")
                } else {
                    ScanCoverageSummary(model: model, scans: scans)
                }
            }
            .padding(NKSpace.pagePad)
        }
        .background(NK.pageBg)
        .navigationTitle("来源覆盖")
        #if os(iOS)
        .toolbar(.visible, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}

private struct SettingsEntry: View {
    let icon: String
    let title: String
    let detail: String
    let badge: String?
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            SettingsRowContent(icon: icon, title: title, detail: detail, badge: badge)
        }
        .buttonStyle(.plain)
    }
}

private struct SettingsRowContent: View {
    let icon: String
    let title: String
    let detail: String
    let badge: String?
    var showsChevron = true

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.headline)
                .foregroundStyle(NK.accent)
                .frame(width: 30, height: 30)
                .background(NK.accent.opacity(0.09), in: RoundedRectangle(cornerRadius: NKRadius.inner))
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                Text(detail).font(NKFont.caption).foregroundStyle(NK.textSecondary).multilineTextAlignment(.leading)
            }
            Spacer(minLength: 8)
            if let badge { V3Pill(text: badge) }
            if showsChevron {
                Image(systemName: "chevron.right").font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.textTertiary)
            }
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }
}

private struct SettingsDivider: View {
    var body: some View { Divider().overlay(NK.hairline).padding(.leading, 42) }
}

private struct SettingsStatusRow: View {
    let title: String
    let scan: K10Scan?

    var body: some View {
        HStack {
            Image(systemName: title == "晚间扫描" ? "moon.stars" : "sun.max")
                .foregroundStyle(NK.accent).frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(NKFont.callout.weight(.semibold))
                Text(scan.map { "截止 \(k10DisplayTime($0.cutoffAt))" } ?? "尚无回执")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
            Spacer()
            if let scan { V3Pill(text: scan.executionProgress?.state ?? scan.status) }
        }
        .padding(.vertical, 10)
    }
}

private struct NotificationReadinessRow: View {
    let readiness: K10NotificationReadiness?

    var body: some View {
        HStack {
            Image(systemName: "bell.badge").foregroundStyle(NK.accent).frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text("推送状态").font(NKFont.callout.weight(.semibold))
                Text(detail).font(NKFont.caption).foregroundStyle(tone)
            }
            Spacer()
            if let readiness { V3Pill(text: readiness.state) }
        }
        .padding(.vertical, 10)
    }

    private var detail: String {
        guard let readiness else { return "状态尚未读取" }
        switch readiness.state {
        case "ready": return "推送服务已就绪"
        case "blocked", "notConfigured":
            let code = readiness.reasonCode.map { "（\(k10ExecutionFailureText($0))）" } ?? ""
            return "推送待配置\(code)"
        default: return "推送状态待核"
        }
    }
    private var tone: Color { readiness?.state == "ready" ? NK.textSecondary : NK.amber }
}

private struct DiscoveryControlRow: View {
    let control: K10ExecutionRunControl?
    let isPausing: Bool
    let pause: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: control?.state == "paused" ? "pause.circle.fill" : "play.circle")
                .foregroundStyle(control?.state == "paused" ? NK.amber : NK.accent)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text("资讯处理").font(NKFont.callout.weight(.semibold))
                Text(detail).font(NKFont.caption).foregroundStyle(control?.state == "paused" ? NK.amber : NK.textSecondary)
            }
            Spacer()
            if control?.state == "paused" {
                V3Pill(text: "已暂停")
            } else {
                Button(isPausing ? "暂停中" : "暂停") { pause() }
                    .font(NKFont.caption.weight(.semibold))
                    .buttonStyle(.bordered)
                    .tint(NK.accent)
                    .disabled(isPausing)
                    .accessibilityLabel("暂停后续资讯处理")
            }
        }
        .padding(.vertical, 10)
    }

    private var detail: String {
        guard let control else { return "运行控制状态尚未读取" }
        if control.state == "paused" {
            return "后续自动处理已暂停；不会自动恢复"
        }
        return "开关已打开；仍须通过配置检查"
    }
}

private struct ExecutionProgressCard: View {
    let progress: K10ExecutionProgress

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("处理进度").font(NKFont.callout.weight(.semibold))
                Spacer()
                V3Pill(text: progress.state)
            }
            Text(stageText).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if let titles = progress.titleCounts {
                Text("标题 \(titles.received) · 精确重复 \(titles.exactDeduplicated) · 已理解 \(titles.triaged)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                Text("同事项合并 \(titles.merged) · 未入选 \(titles.notSelected) · 受保护 \(titles.protected)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                if titles.partial > 0 {
                    Text("尚有 \(titles.partial) 条资讯未确定全局去向；名单冻结前不会开始正文深读。")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                }
            } else {
                Text("已获取 \(progress.documentCounts.received) · 已理解 \(progress.documentCounts.understood) · 已核验 \(progress.eventCounts.verified) / 已比较 \(progress.eventCounts.compared)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            }
            Text(progress.eventCounts.publishable.map { "统一排序后可发布 \($0) 家" } ?? "尚未完成统一排序，不显示可发布名额")
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if let articles = progress.articleCounts {
                Text("按事件深读 · 入选 \(articles.selected) · 已读取 \(articles.admitted)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
                Text("深读完成 \(articles.completed) · 缺正文 \(articles.missingBody) · 搜索核验：摘录 \(articles.tavilyExcerpt) / 新全文 \(articles.tavilyFullArticle)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(articles.missingBody > 0 ? NK.amber : NK.textSecondary)
                Text("只深读已冻结入选文章，不代表全部资讯正文都已读取。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
            if let attempts = progress.attemptCounts,
               attempts.started + attempts.succeeded + attempts.failed + attempts.unknown > 0 {
                Text("请求回执：完成 \(attempts.succeeded) · 失败 \(attempts.failed) · 结果待确认 \(attempts.unknown)")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(attempts.failed + attempts.unknown > 0 ? NK.amber : NK.textSecondary)
            }
            if let cacheHits = progress.factCacheHits {
                Text("已复用 \(cacheHits) 份已核事实，不重复调用模型")
                    .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            }
            if progress.runControl?.state == "paused" {
                Text("处理已暂停；不会自动恢复。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            if progress.documentCounts.failedPending > 0 {
                Text("\(progress.documentCounts.failedPending) 篇资料待恢复；本轮不是完整覆盖。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            if let nextRetryAt = progress.nextRetryAt {
                Text("下次恢复：\(k10DisplayTime(nextRetryAt))").font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
            if !progress.safeFailures.isEmpty {
                DisclosureGroup("查看待处理原因") {
                    ForEach(progress.safeFailures) { failure in
                        Text("\(k10ExecutionStageText(failure.stage))：\(k10ExecutionFailureText(failure.code))")
                            .font(NKFont.caption).foregroundStyle(NK.amber)
                    }
                }
                .font(NKFont.caption)
            }
        }
        .padding(9)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }

    private var stageText: String {
        if progress.state == "paused" { return "已暂停 · 后续自动处理不会启动" }
        if progress.state == "retired" { return "旧任务已停用，不能恢复" }
        if progress.state == "notConfigured" { return "参数未配置 · 本轮没有开始处理" }
        let coverage = progress.coverageStatus == "partial" ? "存在待处理缺口" : (progress.titleCounts == nil ? "资料处理完成" : "标题处理完成")
        return "\(k10ExecutionStageText(progress.stage ?? "pending")) · \(coverage)"
    }
}

private struct ResearchSummaryCard: View {
    let summary: K10ResearchSummary
    let assessments: [K10ResearchAssessment]
    @Bindable var model: AppModel
    @State private var showsAssessments = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("命题调查").font(NKFont.callout.weight(.semibold))
                Spacer()
                Text(stateText).font(NKFont.caption.weight(.medium)).foregroundStyle(tint)
            }
            Text("事件 \(summary.eventCount) · 问题已答 \(summary.questionCounts.answered) · 待答 \(summary.questionCounts.open + summary.questionCounts.blocked)")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            Text("可比较 \(summary.companyCounts.comparable) · 待核 \(summary.companyCounts.pending) · 排除 \(summary.companyCounts.excluded)")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textSecondary)
            if summary.executionFailed {
                Text("执行失败，比较未完成。已保留可复用资料与安全错误定位。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            } else if !summary.comparisonComplete {
                Text("公司比较仍未完成；不会把当前覆盖或空候选解释为无机会。")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            if !summary.safeFailureCounts.isEmpty {
                Text("安全错误：\(summary.safeFailureCounts.keys.sorted().joined(separator: "、"))")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            if !assessments.isEmpty {
                Button { showsAssessments.toggle() } label: {
                    Label(showsAssessments ? "收起全部公司比较" : "查看全部公司比较（\(assessments.count)）", systemImage: showsAssessments ? "chevron.up" : "chevron.down")
                        .font(NKFont.caption.weight(.medium)).foregroundStyle(NK.accent)
                }
                .buttonStyle(.plain)
                if showsAssessments {
                    ForEach(assessments) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(item.companyCode).font(NKFont.callout.weight(.semibold))
                                Text(k10CategoryText(item.role)).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                Spacer()
                                if let rank = item.rank { Text("事件内第 \(rank) 位").font(NKFont.caption).foregroundStyle(NK.textSecondary) }
                            }
                            Text(item.summary).font(NKFont.caption)
                            if let safeErrorCode = item.safeErrorCode {
                                Text("执行定位：\(safeErrorCode)").font(NKFont.caption).foregroundStyle(NK.amber)
                            }
                            EvidenceDisclosureBlock(disclosure: item.evidenceDisclosure, model: model)
                        }
                        .padding(8)
                        .background(NK.pageBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
                    }
                }
            }
        }
        .padding(9)
        .background(NK.fieldBg, in: RoundedRectangle(cornerRadius: NKRadius.inner))
    }

    private var stateText: String {
        if summary.executionFailed { return "执行失败" }
        return summary.comparisonComplete ? "比较完成" : "比较未完成"
    }

    private var tint: Color { summary.executionFailed || !summary.comparisonComplete ? NK.amber : NK.accent }
}

private struct SettingsConfigurationRows: View {
    let configuration: K10Configuration?

    var body: some View {
        Group {
            if let configuration {
                if let snapshot = configuration.universeSnapshotId {
                    VStack(alignment: .leading, spacing: 4) {
                        Label("固定公司资料", systemImage: "building.2").font(NKFont.headline)
                        Text(snapshot).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        if let profile = configuration.profileSnapshotId { Text(profile).font(NKFont.caption).foregroundStyle(NK.textSecondary) }
                        if configuration.profileReviewStatus == "local_draft_awaiting_user" {
                            Text("资料为本地初稿，来源与待核项按原样保留").font(NKFont.caption).foregroundStyle(NK.amber)
                        }
                    }.padding(.vertical, 8)
                }
                ForEach(configuration.scopes) { scope in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "slider.horizontal.3").foregroundStyle(NK.accent).frame(width: 24)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(scopeTitle(scope.scope)).font(NKFont.callout.weight(.semibold))
                            if !scope.missing.isEmpty { Text("缺少：\(scope.missing.joined(separator: "、"))").font(NKFont.caption).foregroundStyle(NK.amber) }
                            if !scope.errors.isEmpty { Text(scope.errors.joined(separator: "、")).font(NKFont.caption).foregroundStyle(NK.amber) }
                        }
                        Spacer()
                        V3Pill(text: scope.state)
                    }
                    .padding(.vertical, 8)
                }
            } else {
                HStack {
                    Image(systemName: "slider.horizontal.3").foregroundStyle(NK.accent).frame(width: 24)
                    Text("尚未读取 K10 配置状态").font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    Spacer()
                }
                .padding(.vertical, 10)
            }
        }
    }

    private func scopeTitle(_ scope: String) -> String {
        ["candidate": "候选发布", "discovery": "资讯标题筛选", "analysis": "正反分析", "evaluation": "两日评价"][scope] ?? scope
    }
}

private struct SettingsUsageRow: View {
    let usage: K10UsageSummary?

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "gauge.with.dots.needle.50percent").foregroundStyle(NK.accent).frame(width: 30, height: 30)
                .background(NK.accent.opacity(0.09), in: RoundedRectangle(cornerRadius: NKRadius.inner))
            VStack(alignment: .leading, spacing: 3) {
                Text("实际用量").font(NKFont.headline)
                if let totals = usage?.totals {
                    Text("调用 \(totals.calls) 次 · 失败 \(totals.failed) 次 · Tokens \(totals.totalTokens.map(String.init) ?? "未知")")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                } else {
                    Text("暂未取得用量；费用不在客户端估算。")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
            }
            Spacer()
        }
        .padding(.vertical, 10)
    }
}

private struct ConnectionEditor: View {
    @ObservedObject var config: AppConfig
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(title: "后端连接", subtitle: "修改后需主动检查并刷新 K10 数据。")
                    V3Card {
                        VStack(alignment: .leading, spacing: 12) {
                            Text("环境").font(NKFont.headline)
                            Picker("环境", selection: $config.environment) {
                                ForEach(NKEnvironment.allCases) { Text($0.shortLabel).tag($0) }
                            }
                            .pickerStyle(.segmented)
                            TextField("自定义服务地址", text: $config.baseURLOverride)
                                .textFieldStyle(.roundedBorder)
                            SecureField("API Token", text: $config.apiToken)
                                .textFieldStyle(.roundedBorder)
                            if let error = config.connectionConfigurationError {
                                Text(error).font(NKFont.caption).foregroundStyle(NK.down)
                            }
                        }
                    }
                    Button("检查并刷新 K10") {
                        Task {
                            model.bind(config: config)
                            await model.refresh()
                            await model.refreshAdminSettings()
                            dismiss()
                        }
                    }
                    .buttonStyle(V3PrimaryButtonStyle())
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBg)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        }
    }
}

struct ModelEditor: View {
    @Bindable var model: AppModel
    @State private var selectedName: String?
    @State private var providerName = ""
    @State private var endpoint = ""
    @State private var modelName = ""
    @State private var apiKey = ""
    @State private var enabled = true
    @State private var clearKey = false
    @State private var confirmDelete = false
    @State private var saveNotice: String?
    @State private var initialized = false
    @Environment(\.dismiss) private var dismiss

    private var selected: K10Provider? { model.providers.first { $0.name == selectedName } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(title: "我的模型", subtitle: "填写自己的 API 地址、模型和 Key。支持 OpenAI 兼容的 Chat Completions 接口。")
                    connectionsCard
                    editorCard
                }
                .padding(NKSpace.pagePad)
                .frame(maxWidth: 720)
                .frame(maxWidth: .infinity)
            }
            .background(NK.pageBg)
            .navigationTitle("模型连接 · BYOK")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() }.disabled(model.providerSettingsSaving) } }
        }
        #if os(macOS)
        .frame(minWidth: 620, minHeight: 680)
        #endif
        .interactiveDismissDisabled(model.providerSettingsSaving)
        .onAppear {
            guard !initialized else { return }
            initialized = true
            load(model.providers.first(where: \.enabled) ?? model.providers.first)
        }
        .confirmationDialog("删除这组连接和服务器上的 Key？引用它的未完成任务将无法继续。", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除连接", role: .destructive) {
                guard let name = selectedName else { return }
                Task { if await model.deleteModelConnection(name: name) { load(model.providers.first(where: \.enabled) ?? model.providers.first); saveNotice = "连接及其 Key 已删除" } }
            }
        }
    }

    private var connectionsCard: some View {
        V3Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    Text("已保存的连接").font(NKFont.headline)
                    Spacer()
                    Button { load(nil) } label: { Label("新增", systemImage: "plus") }
                        .disabled(model.providerSettingsSaving)
                }
                if model.providers.isEmpty {
                    Text("还没有模型连接。填写下方信息后保存。")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
                ForEach(model.providers) { provider in
                    Button { load(provider) } label: {
                        HStack(spacing: 12) {
                            Image(systemName: selectedName == provider.name ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(NK.accent)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(provider.name).font(NKFont.callout.weight(.semibold)).foregroundStyle(NK.textPrimary)
                                Text("\(provider.model) · \(URL(string: provider.baseUrl)?.host ?? provider.baseUrl)")
                                    .font(NKFont.caption).foregroundStyle(NK.textSecondary).lineLimit(2)
                                Text(provider.keySet ? "Key 已配置" : "未配置 Key")
                                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            }
                            Spacer(minLength: 4)
                            if provider.enabled { Text("当前使用").font(NKFont.caption.weight(.semibold)).foregroundStyle(NK.accent) }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).disabled(model.providerSettingsSaving)
                }
            }
        }
    }

    private var editorCard: some View {
        V3Card {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text(selectedName == nil ? "新增连接" : "编辑连接").font(NKFont.headline)
                    Spacer()
                    if selectedName == nil {
                        Button("填入 DeepSeek 示例") { endpoint = "https://api.deepseek.com/v1"; modelName = "deepseek-v4-pro" }
                            .font(NKFont.caption)
                    }
                }
                field("连接名称", hint: "例如：日常模型") { TextField("给这组连接起个名字", text: $providerName).disabled(selectedName != nil) }
                field("API 地址", hint: "填写基础地址（如 https://example.com/v1）或完整 /chat/completions 地址。") {
                    TextField("https://…/v1", text: $endpoint)
                        #if os(iOS)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        #endif
                }
                field("模型名称", hint: "填写服务商提供的准确模型 ID。") {
                    TextField("模型 ID", text: $modelName)
                        #if os(iOS)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        #endif
                }
                field("API Key", hint: selected?.keySet == true ? "服务器已保存 Key；留空保留。输入新 Key 即可替换。" : "Key 只写入服务器，不会回显。") {
                    SecureField(clearKey ? "保存时将清除旧 Key" : "输入 API Key", text: $apiKey).disabled(clearKey)
                        #if os(iOS)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        #endif
                }
                if selected?.keySet == true {
                    Toggle("清除已保存的 Key", isOn: $clearKey).font(NKFont.caption)
                        .onChange(of: clearKey) { _, value in if value { apiKey = "" } }
                }
                Toggle("设为当前使用的连接", isOn: $enabled).font(NKFont.callout)
                Text("切换连接只影响新任务。要保留未完成任务的原配置，请新增连接；直接修改端点或模型会阻止旧任务继续。保存不调用模型，不恢复暂停任务。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                if let saveNotice, model.providerSettingsError == nil {
                    Label(saveNotice, systemImage: "checkmark.circle.fill").font(NKFont.callout).foregroundStyle(NK.up)
                }
                if let error = model.providerSettingsError {
                    Text(error).font(NKFont.callout).foregroundStyle(NK.down).textSelection(.enabled)
                }
                HStack(spacing: 12) {
                    Button(model.providerSettingsSaving ? "保存中…" : "保存连接") {
                        Task {
                            if await model.saveModelConnection(name: providerName, baseURL: endpoint, modelName: modelName, apiKey: apiKey,
                                                               enabled: enabled, creating: selectedName == nil, clearKey: clearKey) {
                                load(model.providers.first { $0.name == providerName.trimmingCharacters(in: .whitespacesAndNewlines) })
                                saveNotice = "已保存，未发起模型调用"
                            }
                        }
                    }.buttonStyle(V3PrimaryButtonStyle())
                    if selectedName != nil {
                        Button("删除连接", role: .destructive) { confirmDelete = true }.buttonStyle(.plain)
                    }
                }
            }
            .disabled(model.providerSettingsSaving)
        }
    }

    private func field<Content: View>(_ title: String, hint: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(NKFont.callout.weight(.medium))
            content().textFieldStyle(.roundedBorder)
            Text(hint).font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
    }

    private func load(_ provider: K10Provider?) {
        selectedName = provider?.name; providerName = provider?.name ?? ""
        endpoint = provider?.baseUrl ?? ""; modelName = provider?.model ?? ""
        enabled = provider?.enabled ?? !model.providers.contains(where: \.enabled)
        apiKey = ""; clearKey = false; saveNotice = nil; model.providerSettingsError = nil
    }
}

private struct SourceEditor: View {
    @Bindable var model: AppModel
    @Binding var tavilyKey: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(title: "资讯来源", subtitle: "来源权限、覆盖与失败都会如实显示，不会被当作空结果。")
                    V3Card {
                        VStack(alignment: .leading, spacing: 12) {
                            HStack {
                                Text("Tavily 定向核验").font(NKFont.headline)
                                Spacer()
                                V3Pill(text: model.tavilyKeySet ? "available" : "not_configured")
                            }
                            Text(model.tavilyKeySet ? "密钥已配置，页面不会回显。" : "尚未配置密钥。")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            SecureField("新的 Tavily Key", text: $tavilyKey).textFieldStyle(.roundedBorder)
                            HStack(spacing: 10) {
                                Button("写入密钥") {
                                    Task {
                                        await model.setTavilyKey(tavilyKey)
                                        tavilyKey = ""
                                        dismiss()
                                    }
                                }
                                .buttonStyle(V3PrimaryButtonStyle())
                                Button("清除密钥", role: .destructive) {
                                    Task { await model.clearTavilyKey() }
                                }
                                .buttonStyle(V3SecondaryButtonStyle())
                            }
                        }
                    }
                }
                .padding(NKSpace.pagePad)
            }
            .background(NK.pageBg)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("完成") { dismiss() } } }
        }
    }
}

struct DisciplineView: View {
    private let rules = [
        "因何买入，就因何持有；理由失效，不找借口。",
        "消息再好，也要接受市场反馈；走势不符，重新审视。",
        "买前功课不足，不靠买后补仓、做 T 和硬扛来挽救。",
        "曾经的龙头没有永久资格，过去的成功不能机械照搬。",
        "亏后不急着翻本，赢后不放宽标准。",
        "卖出旧票与买入新票，分别判断；空出的仓位不必急着填满。",
        "没有合适机会就等，不能因为怕踏空而降低标准。",
        "先定价格边界，再决定下单；不能为了追进去而临时抬高上限。",
        "仓位服从可承受风险；买了几只同题材，不等于分散了风险。",
        "没有时间完成判断、没有条件执行预案，就不勉强交易。"
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                V3PageHeader(title: "个人纪律十条", subtitle: "仅供个人阅读，不会写入 K10 选股、选择或两日评价。")
                ForEach(Array(rules.enumerated()), id: \.offset) { index, rule in
                    V3Card {
                        HStack(alignment: .top, spacing: 12) {
                            Text("\(index + 1)").font(NKFont.metric).foregroundStyle(NK.accent).frame(width: 30, alignment: .leading)
                            Text(rule).font(NKFont.body).foregroundStyle(NK.textPrimary)
                        }
                    }
                }
            }
            .padding(NKSpace.pagePad)
        }
        .background(NK.pageBg)
        .navigationTitle("个人纪律十条")
        #if os(iOS)
        .toolbar(.visible, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}
