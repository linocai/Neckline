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
                                    V3Pill(text: scan.status)
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
    @State private var providerName = "k10-deepseek"
    @State private var providerKey = ""
    @State private var providerEnabled = true
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
                        SettingsRowContent(icon: "number.square", title: "Neckline \(appVersion)", detail: "Build \(appBuild) · K10-v1.4", badge: nil, showsChevron: false)
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
        .sheet(isPresented: $showModelEditor) { ModelEditor(model: model, providerName: $providerName, providerKey: $providerKey, providerEnabled: $providerEnabled) }
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
        return active.isEmpty ? "尚未读取或未启用连接" : "\(active.count) 个启用连接 · K10 固定 DeepSeek V4 Pro"
    }
    private var coverageDetail: String { model.scanSummaries.isEmpty ? "尚无扫描回执" : "查看来源范围、成功水位与缺口" }
}

private struct SettingsCoverageScreen: View {
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
            if let scan { V3Pill(text: scan.status) }
        }
        .padding(.vertical, 10)
    }
}

private struct SettingsConfigurationRows: View {
    let configuration: K10Configuration?

    var body: some View {
        Group {
            if let configuration {
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
        ["candidate": "候选发布", "analysis": "正反分析", "evaluation": "两日评价"][scope] ?? scope
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

private struct ModelEditor: View {
    @Bindable var model: AppModel
    @Binding var providerName: String
    @Binding var providerKey: String
    @Binding var providerEnabled: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(title: "模型配置", subtitle: "K10-v1.4 固定使用 DeepSeek V4 Pro；密钥只写入服务器。")
                    V3Card {
                        VStack(alignment: .leading, spacing: 10) {
                            if model.providers.isEmpty {
                                Text("尚未读取已保存的连接。保存时不会回显密钥。")
                                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            } else {
                                ForEach(model.providers) { provider in
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(provider.name).font(NKFont.callout.weight(.semibold))
                                            Text("\(provider.enabled ? "启用" : "停用") · \(provider.keySet ? "密钥已配置" : "未配置密钥")")
                                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                        }
                                        Spacer()
                                        V3Pill(text: provider.enabled ? "available" : "not_configured")
                                    }
                                }
                            }
                        }
                    }
                    V3Card {
                        VStack(alignment: .leading, spacing: 12) {
                            TextField("连接名称", text: $providerName).textFieldStyle(.roundedBorder)
                            SecureField("新的 API Key（留空不改现有）", text: $providerKey).textFieldStyle(.roundedBorder)
                            Toggle("启用该连接", isOn: $providerEnabled).font(NKFont.callout)
                            Button("保存 DeepSeek 连接") {
                                Task {
                                    await model.saveDeepSeekConnection(name: providerName, apiKey: providerKey, enabled: providerEnabled)
                                    providerKey = ""
                                    dismiss()
                                }
                            }
                            .buttonStyle(V3PrimaryButtonStyle())
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
