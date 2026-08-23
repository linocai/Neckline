//
//  SettingsView.swift
//  Neckline — 设置(D8 四板块之一,V2-⑮ 换血):
//    后端地址 + API token · **Provider 注册表增删改**(自填制)· **任务路由表** ·
//    **按 `kind` 的推送开关(动态渲染 + 按 level 分组)** · 连接自检 · iOS 推送重注册 ·
//    App / 服务端双版本行。
//
//  ⚠ **V2-② Provider 自填制**:`glm`/`kimi` 二值枚举整个退役 —— 任意 OpenAI 兼容端点
//  可配。**`apiKey` 只写不回显**(服务端只回 `keySet` 布尔),删除走二次确认。
//  ⚠ **V2-⑪ 推送开关按 `kind` 配**:权威在服务端 `notify_kinds.py`,客户端
//  **⛔ 不硬编 kind 清单** —— 服务端发什么就渲染什么,新增 kind 时客户端**不改代码
//  就能显示出来**;未识别的 `level` 也照常成一组显示,⛔ 不静默丢弃。
//

import SwiftUI

private enum SelfCheckState: Equatable {
    case idle, running
    case ok(String)
    case tokenError
    case networkError(String)
}

/// macOS 设置默认只放日常确认项；改连接、密钥与模型路由统一沉入高级区。
enum NKSettingsGroup: String, CaseIterable, Identifiable {
    case backend, llm, push, version, advanced
    var id: String { rawValue }
    var title: String {
        switch self {
        case .backend: return "连接与账号"
        case .llm: return "研究服务"
        case .push: return "锁屏推送"
        case .version: return "版本"
        case .advanced: return "高级与诊断"
        }
    }
    var systemImage: String {
        switch self {
        case .backend: return "network"
        case .llm: return "brain"
        case .push: return "bell.badge"
        case .version: return "info.circle"
        case .advanced: return "wrench.and.screwdriver"
        }
    }
}

struct SettingsView: View {
    @Bindable var model: AppModel
    @ObservedObject var config: AppConfig

    @State private var tokenRevealed = false
    @State private var check: SelfCheckState = .idle
    @State private var deletingProvider: String? = nil
    /// macOS 四组导航态(纯本地,不进 `AppModel` —— 它不跨板块)。
    /// ⚠ 初值吃 QA 钩子 `NECKLINE_INITIAL_SETTINGS_GROUP`(⛔ 只给 `@State` 当初值,
    /// 不夺走用户的点击 —— 同 `NECKLINE_INITIAL_RECEIPT` 先例)。
    @State private var group: NKSettingsGroup = NKQA.initialSettingsGroup ?? .backend

    var body: some View {
        #if os(iOS)
        NavigationStack { form.navigationTitle("设置").navigationBarTitleDisplayMode(.inline) }
            .sheet(isPresented: $model.showProviderForm) { ProviderFormSheet(model: model) }
        #else
        NKSplitLayout {
            groupListColumn
        } detail: {
            groupDetail
        }
        .task {
            await model.loadSettings()
            await model.loadServerVersion()
            // QA 钩子:注册表拿回来之后才谈得上"编辑第一个"(⛔ `init()` 里够不着)。
            if NKQA.initialProviderForm, let p = model.providers.first, !model.showProviderForm {
                model.beginEditProvider(p)
            }
        }
        .sheet(isPresented: $model.showProviderForm) {
            // 原型弹层宽 440(`Neckline 弹层.dc.html` 29 行 `width:440px`)。
            ProviderFormSheet(model: model).frame(width: 440, height: 730)
        }
        .alert("删除 Provider", isPresented: Binding(get: { deletingProvider != nil },
                                                     set: { if !$0 { deletingProvider = nil } })) {
            Button("取消", role: .cancel) { deletingProvider = nil }
            Button("删除", role: .destructive) {
                if let n = deletingProvider { Task { await model.deleteProvider(name: n) } }
                deletingProvider = nil
            }
        } message: {
            Text("将删除「\(deletingProvider ?? "")」及其已保存的 key。系统会同时清除指向它的默认模型和任务路由。")
        }
        #endif
    }

    // MARK: - macOS 列表栏(原型 1577–1616)
    //
    // 🔴 **原型的四行没有图标**(1582–1607 每一行只有「标题 + 右端读数」两段 + 次行说明)。
    // V2.3.0 给每行挂了一枚 SF Symbol —— 376pt 栏里那枚图标把标题往里推 24pt,
    // 与同栏其它板块(选股 / 成绩 / 复盘的列表行都无图标)也不是一套语言。

    #if os(macOS)
    private var groupListColumn: some View {
        VStack(alignment: .leading, spacing: NKSpace.rowGap) {
            // 标题区 `padding:18px 16px 12px`(1578 行);栏本身给的是行那一套(横 10),
            // 故这里再补 `listHeaderExtraH` 凑到 16(同批 2 三个板块的做法)。
            Text("设置").font(NKFont.title2).foregroundStyle(NK.textPrimary)
                .tracking(-0.3)
                .padding(.horizontal, NKSpace.listHeaderExtraH)
                .padding(.bottom, 12)
            ForEach(NKSettingsGroup.allCases) { g in
                NKListRow(selected: group == g) { group = g } content: {
                    VStack(alignment: .leading, spacing: 3) {   // 原型 margin-top:3
                        HStack(spacing: 8) {                    // 原型 gap:8
                            Text(g.title).font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            Spacer(minLength: 6)
                            groupTrailing(g)
                        }
                        Text(groupCaption(g)).font(NKFont.caption)
                            .foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }

    /// 行右端的读数(原型 1586 / 1594 / 1602 / 1610 行各一种)。
    @ViewBuilder
    private func groupTrailing(_ g: NKSettingsGroup) -> some View {
        switch g {
        case .backend:
            // 原型是一颗 6px 绿点。⚠ 它说的是**这一组配齐了没有**(地址 + token),
            // ⛔ 不是"连得上" —— 连通性要点「连接自检」才知道,次行那句话说的也正是配置。
            Circle().fill(config.hasToken ? NK.up : NK.amber).frame(width: 6, height: 6)
        case .llm:
            Text("\(model.providers.count)").font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary)
        case .push:
            Text("\(enabledPushCount) / \(model.pushKindsDraft.count) 开")
                .font(NKFont.caption.monospacedDigit()).foregroundStyle(NK.textTertiary)
        case .version:
            Text(appShortVersion).font(NKFont.caption.monospacedDigit())
                .foregroundStyle(NK.textTertiary)
        case .advanced:
            Text("桌面端").font(NKFont.caption)
                .foregroundStyle(NK.textTertiary)
        }
    }

    private var enabledPushCount: Int { model.pushKindsDraft.filter { $0.enabled }.count }

    private func groupCaption(_ g: NKSettingsGroup) -> String {
        switch g {
        case .backend:
            return "\(config.environment.label) · \(config.hasToken ? "访问码已填" : "访问码未填")"
        case .llm:
            return "模型与联网资料状态"
        case .push:
            return "按通知类型配,不按呈现分组配"
        case .version:
            return "App 与服务端双版本行"
        case .advanced:
            return "服务地址、密钥、模型与诊断"
        }
    }

    // MARK: - macOS 详情栏(原型 1618–1745;⛔ 不再用 `Form(.grouped)`)
    //
    // 🔴 `Form(.grouped)` 的圆角 / 页边距 / 分隔线 / 段标题字号**全由系统定、改不了**,
    // 逐项对不到原型的 inline style —— 故这四屏改用 `NKFormKit` 的 `NKFieldCard`。
    // ⚠ iOS 侧仍走下面的 `form`(批 7 才做 iOS 逐屏比对),⛔ 别顺手一起切。

    @ViewBuilder
    private var groupDetail: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            switch group {
            case .backend: connDetail
            case .llm: providersDetail
            case .push: pushDetail
            case .version: versionDetail
            case .advanced: advancedDetail
            }
        }
    }

    /// 详情栏大标题(原型 1621 行 `26px/700; letter-spacing:-.4px`)+ 可选副标题。
    @ViewBuilder
    private func detailTitle(_ title: String, _ subtitle: LocalizedStringKey? = nil) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(NKFont.title1).foregroundStyle(NK.textPrimary).tracking(-0.4)
            if let s = subtitle {
                Text(s).font(NKFont.callout).lineSpacing(4)
                    .foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // —— 默认设置：只显示状态与日常开关 ————————————————————————————————

    @ViewBuilder
    private var connDetail: some View {
        detailTitle("连接与账号", "这里仅确认状态；更换服务地址、访问码或排查连接，请到“高级与诊断”。")
        NKFieldCard {
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "当前环境")
                Spacer(minLength: 8)
                Text(config.environment.label).font(NKFont.callout).foregroundStyle(NK.textPrimary)
            }
            NKFieldSeparator()
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "访问配置")
                Spacer(minLength: 8)
                NKChip(text: config.hasToken ? "已配置" : "未配置",
                       tone: config.hasToken ? .good : .warn)
            }
            NKFieldSeparator()
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "服务状态")
                Spacer(minLength: 8)
                Text(model.serverVersion == nil ? "尚未确认" : "可读取版本信息")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
        }
    }

    @ViewBuilder
    private var providersDetail: some View {
        detailTitle("研究服务", "确认模型与联网资料的可用状态；具体配置只在“高级与诊断”。")
        NKFieldCard {
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "联网资料")
                Spacer(minLength: 8)
                NKChip(text: model.settings.tavily.keySet ? "已配置" : "未配置",
                       tone: model.settings.tavily.keySet ? .good : .warn)
            }
            NKFieldSeparator()
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "可用模型")
                Spacer(minLength: 8)
                Text("\(eligibleProviders.count) 个").font(NKFont.callout.monospacedDigit())
                    .foregroundStyle(NK.textPrimary)
            }
        }
        usageDetail
    }

    @ViewBuilder
    private var advancedDetail: some View {
        detailTitle("高级与诊断", "仅在更换服务、访问凭据、模型路由或排查问题时使用。")
        advancedConnectionDetail
        advancedProvidersDetail
    }

    // —— 高级：后端连接与鉴权 ————————————————————————————————————————

    @ViewBuilder
    private var advancedConnectionDetail: some View {
        NKGroupLabel(text: "连接与访问凭据")

        NKFieldCard {
            NKFieldRow(v: 14, h: 18, alignment: .top) {
                VStack(alignment: .leading, spacing: 9) {   // 原型 margin-bottom:9
                    NKGroupLabel(text: "环境")
                    NKSegmented(options: NKEnvironment.allCases.map { ($0, $0.shortLabel) },
                                selection: $config.environment)
                    NKInlineNote(text: "可选择本机或云端服务；切换后即时生效。",
                                 tone: .neutral)
                }
            }
            NKFieldSeparator()
            NKFieldRow(v: 14, h: 18) {
                NKFieldLabel(text: "当前服务地址")
                Text(config.resolvedBaseURL.absoluteString)
                    .font(NKFont.callout.monospaced()).foregroundStyle(NK.textPrimary)
                    .lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 0)
            }
            NKFieldSeparator()
            NKFieldRow(v: 14, h: 18, alignment: .top) {
                VStack(alignment: .leading, spacing: 7) {
                    Text("临时服务地址（可选）").font(NKFont.body)
                        .foregroundStyle(NK.textPrimary.opacity(0.75))
                    NKTextFieldBox(placeholder: "留空则用环境默认",
                                   text: $config.baseURLOverride, mono: true)
                    // 🔴 原型 1637 行这句是琥珀的 —— 它是**排障口诀**(CLAUDE.md 登记:
                    // 「换包后连不上/一片空白」先来这里看有没有手填过老基址)。
                    NKInlineNote(text: "⚠ 这里会优先于环境选择；无法连接时可先检查是否留有旧地址。",
                                 tone: .warn)
                }
            }
        }

        NKFieldCard {
            NKFieldRow(v: 16, h: 18, alignment: .top) {
                VStack(alignment: .leading, spacing: 10) {
                    NKGroupLabel(text: "访问码")
                    HStack(spacing: 10) {
                        // 🔴 **只写不回显的是 Provider 的 key,不是这个 token** ——
                        // token 是用户自己填进本机 UserDefaults 的,给个眼睛按钮让他核对
                        // 是对的(原型 1645 行画的就是这枚眼睛)。
                        Group {
                            if tokenRevealed {
                                NKTextFieldBox(placeholder: "粘贴 API Token",
                                               text: $config.apiToken, mono: true)
                            } else {
                                NKTextFieldBox(placeholder: "粘贴 API Token",
                                               text: $config.apiToken, mono: true, secure: true)
                            }
                        }
                        Button { tokenRevealed.toggle() } label: {
                            Image(systemName: tokenRevealed ? "eye.slash" : "eye")
                                .font(.system(size: 13))
                                .foregroundStyle(NK.textSecondary)
                                .frame(width: 30, height: 30)
                                .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    HStack(spacing: 7) {
                        Image(systemName: config.hasToken
                              ? "checkmark.circle" : "exclamationmark.triangle")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(config.hasToken ? NK.up : NK.amber)
                        Text(config.hasToken ? "已填入" : "未填入")
                            .font(NKFont.callout).fontWeight(.semibold)
                            .foregroundStyle(config.hasToken ? NK.up : NK.amber)
                        Spacer(minLength: 8)
                        Text("仅保存在当前设备，不会上传到报告或同步到其他设备。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    }
                }
            }
        }

        NKFieldCard {
            NKFieldRow(v: 16, h: 18, alignment: .top) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        NKOutlineButton(title: "连接自检", systemImage: "wifi",
                                        busy: check == .running) {
                            Task { await runSelfCheck() }
                        }
                        selfCheckResult
                        Spacer(minLength: 0)
                    }
                    // ⚠ 第二探针在 V2.5.0 S1 已从 `/positions`(随持仓板块退役删除)换成
                    // `/settings` —— 这四句文案当时忘了跟着改,对用户描述了一条会 404 的路由。
                    NKInlineNote(text: "会检查服务是否可连接，以及访问码是否有效。")
                }
            }
        }
    }

    @ViewBuilder
    private var selfCheckResult: some View {
        switch check {
        case .idle, .running:
            EmptyView()
        case .ok(let desc):
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle").font(.system(size: 13, weight: .semibold))
                Text(desc).font(NKFont.callout)
            }
            .foregroundStyle(NK.up)
        case .tokenError:
            HStack(spacing: 6) {
                Image(systemName: "xmark.circle").font(.system(size: 13, weight: .semibold))
                Text("访问码无效或已失效，请重新确认。").font(NKFont.callout)
            }
            .foregroundStyle(NK.down)
        case .networkError(let m):
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle").font(.system(size: 13, weight: .semibold))
                Text(m).font(NKFont.callout)
            }
            .foregroundStyle(NK.amber)
        }
    }

    // —— 高级：LLM Provider 与任务路由 —————————————————————————————

    @ViewBuilder
    private var advancedProvidersDetail: some View {
        NKGroupLabel(text: "模型、联网资料与任务路由")

        NKFieldCard {
            NKFieldRow(v: 14, h: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Tavily 联网搜索").font(NKFont.headline).foregroundStyle(NK.textPrimary)
                    Text("免费账号先用 Basic 搜索；检索次数与 LLM Token 分开记账。")
                        .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
                Spacer(minLength: 8)
                NKChip(text: model.settings.tavily.keySet ? "key 已配" : "key 未配",
                       tone: model.settings.tavily.keySet ? .good : .warn)
            }
            NKFieldSeparator()
            NKFieldRow(v: 12, h: 18) {
                NKTextFieldBox(placeholder: model.settings.tavily.keySet
                               ? "填入新 key（留空 = 不改）" : "Tavily API key",
                               text: $model.tavilyKeyDraft, mono: true,
                               secure: true, bordered: false)
                Button("保存") { Task { await model.saveTavilyKey() } }
                    .buttonStyle(.plain).foregroundStyle(NK.accent)
                    .font(NKFont.callout).fontWeight(.semibold)
                    .disabled(model.tavilyKeyDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                if model.settings.tavily.keySet {
                    Button("清除") { Task { await model.clearTavilyKey() } }
                        .buttonStyle(.plain).foregroundStyle(NK.down)
                        .font(NKFont.callout).fontWeight(.semibold)
                }
            }
            NKFieldSeparator()
            NKFieldRow(v: 10, h: 18) {
                NKInlineNote(text: "密钥保存后不会再次显示。")
            }
        }

        if model.providers.isEmpty {
            NKFieldCard {
                NKFieldRow(v: 16, h: 18) {
                    Text("还没有配置任何 Provider —— LLM 相关能力(解释层资料 / 日K 评价 / 预案填值)会走优雅降级,不崩。")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        ForEach(model.providers) { p in
            NKFieldCard {
                NKFieldRow(v: 16, h: 18, alignment: .top) {
                    VStack(alignment: .leading, spacing: 0) {
                        HStack(spacing: 8) {
                            Text(p.name).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                            if !p.enabled { NKChip(text: "已停用") }
                            Spacer(minLength: 6)
                            // 🔴 **只回布尔,绝不回明文**(V2-② 硬纪律)。
                            NKChip(text: p.keySet ? "key 已配" : "key 未配",
                                   tone: p.keySet ? .good : .warn)
                        }
                        Text("\(p.model) · \(p.baseUrl)")
                            .font(NKFont.caption.monospaced()).foregroundStyle(NK.textTertiary)
                            .lineLimit(1).truncationMode(.middle)
                            .padding(.top, 6)
                        if let n = p.notes, !n.isEmpty {
                            Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .padding(.top, 4)
                        }
                        // 原型 1683 行:`margin-top:12; padding-top:12; border-top:.5px`
                        Rectangle().fill(NK.hairline).frame(height: 0.5).padding(.top, 12)
                        HStack(spacing: 14) {
                            Button("编辑") { model.beginEditProvider(p) }
                                .buttonStyle(.plain).foregroundStyle(NK.accent)
                                .font(NKFont.callout).fontWeight(.semibold)
                            Spacer(minLength: 8)
                            Button("删除") { deletingProvider = p.name }
                                .buttonStyle(.plain).foregroundStyle(NK.down)
                                .font(NKFont.callout).fontWeight(.semibold)
                        }
                        .padding(.top, 12)
                    }
                }
            }
        }
        NKDashedButton(title: "新增 Provider", systemImage: "plus") { model.beginCreateProvider() }

        NKFieldCard {
            NKFieldRow(v: 14, h: 18) { NKGroupLabel(text: "任务路由") }
            ForEach(model.llmRoutes.routes.keys.sorted(), id: \.self) { task in
                NKFieldSeparator()
                NKFieldRow(v: 10, h: 18) {
                    // 任务名是**服务端登记的机器标识符**(`basket_card` / `daily_review`),
                    // 等宽展示 = 说明"这是机器名",⛔ 不在客户端造一套中文任务名。
                    Text(task).font(NKFont.callout.monospaced()).foregroundStyle(NK.textPrimary)
                    Spacer(minLength: 8)
                    NKInlineMenu(options: [("", "(不指定)")] + eligibleProviders.map { ($0.name, $0.name) },
                                 selection: routeBinding(task))
                }
            }
            NKFieldSeparator()
            NKFieldRow(v: 10, h: 18) {
                Text("默认 Provider").font(NKFont.body)
                    .foregroundStyle(NK.textPrimary.opacity(0.75))
                Spacer(minLength: 8)
                NKInlineMenu(options: [("", "未设置")] + eligibleProviders.map { ($0.name, $0.name) },
                             selection: defaultProviderBinding)
            }
            NKFieldSeparator()
            NKFieldRow(v: 12, h: 18) {
                NKInlineNote(text: "只有“已启用 + key 已配”的 Provider 可选；路由未命中时回退默认模型。")
                Spacer(minLength: 8)
                Button("保存") {
                    Task {
                        await model.saveRoutes(model.llmRoutes.routes,
                                               defaultProvider: model.llmRoutes.defaultProvider)
                    }
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
                .font(NKFont.callout).fontWeight(.semibold)
            }
        }

    }

    @ViewBuilder
    private var usageDetail: some View {
        NKFieldCard {
            NKFieldRow(v: 14, h: 18) { NKGroupLabel(text: "最近 5 日用量") }
            if model.usageSummary.days.isEmpty {
                Text("暂时还没有可用的用量记录。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
            } else {
                ForEach(model.usageSummary.days) { day in
                    NKFieldSeparator()
                    VStack(alignment: .leading, spacing: 5) {
                        Text(NKFmt.reportDate(day.date)).font(NKFont.callout).foregroundStyle(NK.textPrimary)
                        ForEach(day.tasks) { task in
                            Text("\(usageTaskLabel(task.task))：\(task.calls) 次 · Token \(task.totalTokens.map(String.init) ?? "未回传") · 搜索额度 \(task.tavilyCredits.map(String.init) ?? "—")")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
        }
    }

    // —— ③ 锁屏推送(原型 1707–1732)——————————————————————————————————

    @ViewBuilder
    private var pushDetail: some View {
        detailTitle("锁屏推送",
                    "开关按通知类型分别设置；关闭一种通知不会影响其他通知。")

        if model.pushKindsDraft.isEmpty {
            NKFieldCard {
                NKFieldRow(v: 16, h: 18) {
                    Text("尚未取到通知类型清单(服务端 notify_kinds 是唯一源;客户端不硬编)。")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        } else {
            NKFieldCard {
                // ⛔ **不硬编 kind 清单**:服务端发什么就渲染什么;未识别的 level 也自成一组。
                ForEach(Array(orderedPushGroups.enumerated()),
                        id: \.element.level) { idx, grp in
                    if idx > 0 { NKFieldSeparator() }
                    // 分组头:`padding:12px 18px 8px` + 按级着色的淡底(原型 1712 / 1717 / 1727)。
                    NKFieldRow(v: 10, h: 18, background: pushLevelTint(grp.level)) {
                        Text(nkPushLevelLabel(grp.level)).nkLabel()
                            .foregroundStyle(pushLevelColor(grp.level))
                    }
                    ForEach(grp.kinds) { k in
                        NKFieldSeparator()
                        NKFieldRow(v: 11, h: 18) {
                            Text(k.label).font(NKFont.body).foregroundStyle(
                                pushEnabled(k.kind) ? NK.textPrimary : NK.textSecondary)
                            Spacer(minLength: 8)
                            NKSwitch(isOn: Binding(
                                get: { pushEnabled(k.kind) },
                                set: { model.setPushKind(k.kind, enabled: $0) }
                            ))
                        }
                    }
                }
            }
            HStack(spacing: 12) {
                NKInlineNote(text: "清单由服务端 notify_kinds 下发 —— 新增类型时这里会自动出现,客户端不硬编。")
                Spacer(minLength: 8)
                NKOutlineButton(title: "保存推送设置") { Task { await model.savePushSettings() } }
            }
        }
    }

    private func pushEnabled(_ kind: String) -> Bool {
        model.pushKindsDraft.first(where: { $0.kind == kind })?.enabled ?? true
    }

    /// 三组的**呈现顺序 = 由重到轻**(原型 1712 紧急 → 1717 提示 → 1727 日常)。
    ///
    /// ⚠ 这是**展示层排序**,`PushSettings.groupedByLevel`(服务端出现顺序)原样不动 ——
    /// 那个顺序是双端共用的模型层语义,⛔ 不为一屏的版式去改它。
    /// **未识别的 level 排在三档之后、组间保持服务端相对顺序**(⛔ 不丢弃、也不假装它是某一级)。
    private var orderedPushGroups: [(level: String, kinds: [PushKind])] {
        let rank: [String: Int] = ["immediate": 0, "important": 1, "digest": 2]
        return PushSettings(kinds: model.pushKindsDraft).groupedByLevel
            .enumerated()
            .sorted { a, b in
                let ra = rank[a.element.level] ?? 3, rb = rank[b.element.level] ?? 3
                return ra == rb ? a.offset < b.offset : ra < rb
            }
            .map { $0.element }
    }

    /// 三级各自的着色(原型 1712 红 / 1717 琥珀 / 1727 灰)。
    /// ⚠ **未识别的 level 走灰档**(服务端将来加第四级时不会变成一片白),⛔ 不丢弃该组。
    private func pushLevelColor(_ level: String) -> Color {
        switch level {
        case "immediate": return NK.down
        case "important": return NK.amber
        default: return NK.textSecondary
        }
    }

    private func pushLevelTint(_ level: String) -> Color {
        switch level {
        case "immediate": return NK.down.opacity(0.05)
        case "important": return NK.amber.opacity(0.05)
        default: return NK.textTertiary.opacity(0.06)
        }
    }

    // —— ④ 版本(原型 1733–1745)——————————————————————————————————————

    @ViewBuilder
    private var versionDetail: some View {
        detailTitle("版本")
        NKFieldCard {
            versionRow("App 版本", appVersion)
            NKFieldSeparator()
            versionRow("服务端版本", model.serverVersion ?? "未知(未连通)")
            NKFieldSeparator()
            // 🔴 **V2.5.0 S12 换成 K9 的两个版本号**(K8 的「纪律章程 / 选股包」已随
            // 那条链退役)。⛔ 不在客户端硬编,也⛔ 不在没取到时留白 ——
            // 空白读作"没有",而事实分两种:「本次没有报告」与「参数未配置」。后者
            // 只有报告自己把它列为失败原因时才成立,不能拿 `selectionLoaded` 猜。
            versionRow("参数包版本", paramsPackageVersionText)
            NKFieldSeparator()
            versionRow("事实包版本", model.selection.packVersion?.isEmpty == false
                       ? (model.selection.packVersion ?? "") : "未取得(本次没有报告)")
        }
        if let note = versionMismatchNote {
            NKInlineNote(text: LocalizedStringKey(note), tone: .warn)
        }
        NKNoteBlock(text: "服务端版本未知时这里沉默 —— 沉默不是「已确认一致」。两者不一致只提示、不拦功能。")
    }

    private func versionRow(_ title: String, _ value: String) -> some View {
        NKFieldRow(v: 13, h: 18) {
            Text(title).font(NKFont.body).foregroundStyle(NK.textPrimary.opacity(0.75))
            Spacer(minLength: 8)
            Text(value).font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
                .foregroundStyle(NK.textPrimary)
        }
    }

    private var paramsPackageVersionText: String {
        if let value = model.selection.paramsPackageVersion, !value.isEmpty { return value }
        if model.selection.parameterPackWasMissing { return "参数未配置（本次报告未跑成）" }
        return "未取得（本次没有报告）"
    }
    #endif

    private var appShortVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
    }

    private func usageTaskLabel(_ task: String) -> String {
        switch task {
        case "market_direction": return "市场方向"
        case "news_scan": return "消息核实"
        case "explain": return "个股资料"
        case "playbook": return "次日预案"
        default: return "其他任务"
        }
    }

    /// 双端共用(macOS 路由行 / iOS `routesSection` 都靠它)。
    private func routeBinding(_ task: String) -> Binding<String> {
        Binding(
            get: {
                let current = model.llmRoutes.routes[task] ?? ""
                return eligibleProviderNames.contains(current) ? current : ""
            },
            set: {
                if $0.isEmpty { model.llmRoutes.routes.removeValue(forKey: task) }
                else { model.llmRoutes.routes[task] = $0 }
            }
        )
    }

    private var eligibleProviders: [Provider] {
        model.providers.filter { $0.enabled && $0.keySet }
    }

    private var eligibleProviderNames: Set<String> {
        Set(eligibleProviders.map(\.name))
    }

    private var defaultProviderBinding: Binding<String> {
        Binding(
            get: {
                guard let current = model.llmRoutes.defaultProvider,
                      eligibleProviderNames.contains(current) else { return "" }
                return current
            },
            set: { model.llmRoutes.defaultProvider = $0.isEmpty ? nil : $0 }
        )
    }

    // MARK: - iOS：只保留安全状态、日常通知与桌面端指引

    #if os(iOS)
    private var form: some View {
        Form {
            iosStatusSection
            desktopOnlyNote
            pushSection
            footerSection
        }
        .formStyle(.grouped)
        .task {
            await model.loadSettings()
            await model.loadServerVersion()
        }
    }

    // MARK: - 桌面场景说明(iOS)

    #if os(iOS)
    /// 🔴 **说出口,不留白**:手机上看不到 Provider 注册表 / 任务路由 / 交割单上传,
    /// 是**刻意的**(配置动作留桌面)—— 不说,用户只会以为这版少了功能或者坏了。
    private var desktopOnlyNote: some View {
        Section {
            Label("研究服务与高级配置请在 Mac 上管理", systemImage: "desktopcomputer")
                .font(NKFont.body).foregroundStyle(NK.textSecondary)
        } footer: {
            Text("模型、联网资料和服务地址等高级配置请在 Mac 上完成；手机保留日常阅读与通知设置。")
        }
    }

    private var iosStatusSection: some View {
        Section {
            LabeledContent("访问配置") {
                Label(config.hasToken ? "已配置" : "未配置",
                      systemImage: config.hasToken ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(NKFont.body).fontWeight(.semibold)
                    .foregroundStyle(config.hasToken ? NK.up : NK.amber)
            }
            LabeledContent("联网资料") {
                Text(model.settings.tavily.keySet ? "已配置" : "未配置")
                    .font(NKFont.body).foregroundStyle(NK.textSecondary)
            }
            LabeledContent("可用模型", value: "\(eligibleProviders.count) 个")
        } header: {
            Text("安全状态")
        } footer: {
            Text("手机不会显示服务地址、访问码、模型密钥或诊断入口。")
        }
    }
    #endif

    // MARK: - 后端连接

    private var envSection: some View {
        Section {
            Picker("环境", selection: $config.environment) {
                ForEach(NKEnvironment.allCases) { env in Text(env.label).tag(env) }
            }
            LabeledContent("当前服务") {
                Text(config.resolvedBaseURL.absoluteString)
                    .font(NKFont.body.monospaced())
                    .foregroundStyle(NK.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
            }
        } header: {
            Text("连接")
        } footer: {
            Text("选择本机或云端服务；切换后即时生效。服务地址可在 Mac 的高级设置中管理。")
        }
    }

    private var tokenSection: some View {
        Section {
            HStack(spacing: 8) {
                Group {
                    if tokenRevealed {
                        TextField("粘贴 API Token", text: $config.apiToken)
                    } else {
                        SecureField("粘贴 API Token", text: $config.apiToken)
                    }
                }
                .font(NKFont.body.monospaced())
                #if os(iOS)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                #endif
                Button { tokenRevealed.toggle() } label: {
                    Image(systemName: tokenRevealed ? "eye.slash" : "eye").foregroundStyle(NK.textSecondary)
                }
                .buttonStyle(.plain)
            }
            LabeledContent("当前状态") {
                Label(config.hasToken ? "已填入" : "未填入",
                      systemImage: config.hasToken ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(NKFont.body).fontWeight(.semibold)
                    .foregroundStyle(config.hasToken ? NK.up : NK.amber)
            }
        } header: {
            Text("访问码")
        } footer: {
            Text("访问码仅保存在当前设备。")
        }
    }

    private var overrideSection: some View {
        Section {
            TextField("留空则用环境默认", text: $config.baseURLOverride)
                .font(NKFont.body.monospaced())
                #if os(iOS)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                #endif
        } header: {
            Text("baseURL 覆盖(可选)")
        } footer: {
            Text("临时连别的地址时填,例如 http://192.168.x.x:8002。空则按上方环境。")
        }
    }

    private var selfCheckSection: some View {
        Section {
            Button {
                Task { await runSelfCheck() }
            } label: {
                HStack {
                    if check == .running {
                        ProgressView().controlSize(.small)
                        Text("自检中…")
                    } else {
                        Image(systemName: "wifi")
                        Text("连接自检")
                    }
                }
            }
            .disabled(check == .running)

            switch check {
            case .idle, .running:
                EmptyView()
            case .ok(let desc):
                Label(desc, systemImage: "checkmark.circle.fill").font(NKFont.body).foregroundStyle(NK.up)
            case .tokenError:
                Label("访问码无效或已失效，请重新确认。", systemImage: "xmark.circle.fill")
                    .font(NKFont.body).foregroundStyle(NK.down)
            case .networkError(let m):
                Label(m, systemImage: "exclamationmark.triangle.fill").font(NKFont.body).foregroundStyle(NK.amber)
            }
        } header: {
            Text("连接自检")
        } footer: {
            Text("会检查服务是否可连接，以及访问码是否有效。")
        }
    }

    // MARK: - V2-② Provider 注册表(自填制;🔴 key 只写不回显)

    private var providersSection: some View {
        Section {
            if model.providers.isEmpty {
                Text("还没有配置任何 Provider —— LLM 相关能力(解释层资料 / 日K 评价 / 预案填值)会走优雅降级,不崩。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
            ForEach(model.providers) { p in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(p.name).font(NKFont.body).fontWeight(.semibold)
                            .foregroundStyle(NK.textPrimary)
                        if !p.enabled { NKChip(text: "已停用") }
                        Spacer()
                        // **只回布尔,绝不回明文**。
                        NKChip(text: p.keySet ? "key 已配" : "key 未配",
                               tone: p.keySet ? .good : .warn)
                    }
                    Text("\(p.model) · \(p.baseUrl)")
                        .font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        .lineLimit(1).truncationMode(.middle)
                    if let n = p.notes, !n.isEmpty {
                        Text(n).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                    HStack(spacing: 14) {
                        Button("编辑") { model.beginEditProvider(p) }
                            .buttonStyle(.plain).foregroundStyle(NK.accent)
                            .font(NKFont.callout).fontWeight(.semibold)
                        Spacer()
                        Button("删除") { deletingProvider = p.name }
                            .buttonStyle(.plain).foregroundStyle(NK.down)
                            .font(NKFont.callout).fontWeight(.semibold)
                    }
                }
                .padding(.vertical, 2)
            }
            Button { model.beginCreateProvider() } label: {
                Label("新增 Provider", systemImage: "plus.circle.fill")
            }
        } header: {
            Text("LLM Provider 注册表(自填制)")
        } footer: {
            Text("任意 OpenAI 兼容端点均可配。key 只发一次、服务端从不回显明文；联网检索统一由桌面端设置里的 Tavily 提供。")
        }
    }

    // MARK: - 任务路由表

    private var routesSection: some View {
        Section {
            if model.llmRoutes.routes.isEmpty {
                Text("暂无任务路由(全部任务走默认 Provider)。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
            ForEach(model.llmRoutes.routes.keys.sorted(), id: \.self) { task in
                Picker(task, selection: routeBinding(task)) {
                    Text("(不指定)").tag("")
                    ForEach(eligibleProviders) { p in Text(p.name).tag(p.name) }
                }
            }
            Picker("默认 Provider", selection: defaultProviderBinding) {
                Text("未设置").tag("")
                ForEach(eligibleProviders) { p in Text(p.name).tag(p.name) }
            }
            Button("保存任务路由") {
                Task {
                    await model.saveRoutes(model.llmRoutes.routes,
                                           defaultProvider: model.llmRoutes.defaultProvider)
                }
            }
        } header: {
            Text("任务路由")
        } footer: {
            Text("哪个任务用哪个 Provider（全量覆盖式保存）。未登记的任务名无法保存。")
        }
    }

    // MARK: - V2-⑪ 推送开关(**按 kind 动态渲染 + 按 level 分组**)

    private var pushSection: some View {
        Section {
            if model.pushKindsDraft.isEmpty {
                Text("尚未取到通知类型清单(服务端 `notify_kinds` 是唯一源;客户端不硬编)。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
            // ⛔ **不硬编 kind 清单**:服务端发什么就渲染什么;未识别的 level 也自成一组。
            ForEach(PushSettings(kinds: model.pushKindsDraft).groupedByLevel, id: \.level) { group in
                VStack(alignment: .leading, spacing: 4) {
                    Text(nkPushLevelLabel(group.level))
                        .font(NKFont.caption).fontWeight(.bold).foregroundStyle(NK.textTertiary)
                    ForEach(group.kinds) { k in
                        Toggle(k.label, isOn: Binding(
                            get: { model.pushKindsDraft.first(where: { $0.kind == k.kind })?.enabled ?? true },
                            set: { model.setPushKind(k.kind, enabled: $0) }
                        ))
                    }
                }
                .padding(.vertical, 2)
            }
            Button("保存推送设置") { Task { await model.savePushSettings() } }
                .disabled(model.pushKindsDraft.isEmpty)
        } header: {
            Text("锁屏推送(按通知类型)")
        } footer: {
            Text("开关按通知类型分别设置；关闭一种通知不会影响其他通知。")
        }
    }

    #if os(iOS)
    @ViewBuilder
    private var devicePushSection: some View {
        Section {
            LabeledContent("Device Token") {
                Text(model.pushManager?.lastDeviceToken ?? "未注册")
                    .font(NKFont.body.monospaced())
                    .foregroundStyle(model.pushManager?.lastDeviceToken == nil ? NK.textTertiary : NK.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                    .textSelection(.enabled)
            }
            if let err = model.pushManager?.registerError {
                LabeledContent("注册错误") {
                    Text(err).font(NKFont.callout).foregroundStyle(NK.down).multilineTextAlignment(.trailing)
                }
            }
            Button {
                Task { await model.pushManager?.requestAuthorizationAndRegister() }
            } label: {
                Label("重新注册推送", systemImage: "bell.badge")
            }
        } header: {
            Text("设备注册")
        } footer: {
            Text("切换环境后点此,把 device token 重新注册到该环境的库。模拟器拿不到真 token。")
        }
    }
    #endif

    /// A2 版本号治理:诚实展示「App 版本 + 服务端版本」双版本;不一致时**只提示、不拦功能**。
    private var footerSection: some View {
        Section {
            LabeledContent("App 版本", value: appVersion)
            LabeledContent("服务端版本", value: model.serverVersion ?? "未知(未连通)")
            if let note = versionMismatchNote {
                Text(note).font(NKFont.caption).foregroundStyle(NK.amber)
            }
        }
    }
    #endif

    private var appVersion: String {
        let v = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—"
        let b = Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—"
        return "\(v) (\(b))"
    }

    /// 两者都源自同一个 `MARKETING_VERSION`(守门单测锁三处恒等),故去掉服务端 "v"
    /// 前缀后直接字符串比较即可。服务端版本未知时**不提示** —— 沉默,不是"已确认一致"。
    private var versionMismatchNote: String? {
        guard let server = model.serverVersion else { return nil }
        let serverBare = server.hasPrefix("v") ? String(server.dropFirst()) : server
        let appShort = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        guard serverBare != appShort else { return nil }
        return "服务端已是 v\(serverBare),当前 App 为 \(appShort),请换包"
    }

    // MARK: - 自检逻辑

    private func runSelfCheck() async {
        check = .running
        let client = APIClient(baseURL: config.resolvedBaseURL, token: config.apiToken)
        let health = try? await client.health()
        if let v = health?.version { model.serverVersion = v }
        guard health?.ok == true else {
            check = .networkError("服务暂不可达，请检查环境或网络。")
            return
        }
        do {
            // 🔴 V2.5.0 S1:自检的第二条从 `/positions`(已随持仓板块退役删除)换成
            // `/settings` —— 它是**鉴权后**的只读端点,能同时验通「token 对不对」与
            // 「服务端答不答」。⛔ 不许退化成只打 `/health`:那条免鉴权,验不了 token。
            let snapshot = try await client.fetchSettings()
            check = .ok("服务与访问配置正常（\(snapshot.push.kinds.count) 项推送开关）")
        } catch APIError.unauthorized, APIError.noToken {
            check = .tokenError
        } catch let e as APIError {
            check = .networkError(e.errorDescription ?? "请求失败")
        } catch {
            check = .networkError(error.localizedDescription)
        }
    }
}

// MARK: - Provider 增 / 改表单(🔴 key 只写不回显)

struct ProviderFormSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(macOS)
        macBody
        #else
        iosBody
        #endif
    }

    // MARK: - macOS(原型 `Neckline 弹层.dc.html` 188–235:第四个弹层)

    #if os(macOS)
    /// 🔴 **界面上永远只看得到「已配 / 未配」这个布尔**(V2-② 既有硬纪律):
    /// 服务端只回 `keySet`,⛔ 不回明文、⛔ 也不回掩码位数(位数本身也是信息)。
    private var keySet: Bool {
        guard let n = model.providerForm.editingName else { return false }
        return model.providers.first(where: { $0.name == n })?.keySet ?? false
    }

    private var macBody: some View {
        NKSheetShell(
            title: model.providerForm.isEditing ? "编辑 Provider" : "新增 Provider",
            primaryTitle: "保存",
            primaryDisabled: !model.providerForm.isValid,
            onCancel: {
                model.providerForm = ProviderForm()   // 安全态:key 草稿立即丢弃
                model.showProviderForm = false
            },
            onPrimary: { Task { await model.submitProviderForm() } }
        ) {
            // —— 端点 ——
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "端点")
                NKFieldCard {
                    NKFieldRow(v: 12, h: 15) {
                        NKFieldLabel(text: "名称", width: 76)
                        if model.providerForm.isEditing {
                            Text(model.providerForm.name).font(NKFont.body)
                                .foregroundStyle(NK.textSecondary)
                            Spacer(minLength: 8)
                            Text("创建后不可改").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        } else {
                            NKTextFieldBox(placeholder: "唯一名,如 glm",
                                           text: $model.providerForm.name, bordered: false)
                        }
                    }
                    NKFieldSeparator()
                    NKFieldRow(v: 12, h: 15, alignment: .top) {
                        NKFieldLabel(text: "Base URL", width: 76)
                        NKTextFieldBox(placeholder: "https://api.example.com/v1/chat/completions",
                                       text: $model.providerForm.baseUrl, mono: true, bordered: false)
                    }
                    NKFieldSeparator()
                    NKFieldRow(v: 12, h: 15) {
                        NKFieldLabel(text: "模型名", width: 76)
                        NKTextFieldBox(placeholder: "如 glm-4-plus",
                                       text: $model.providerForm.model, mono: true, bordered: false)
                    }
                }
                // 原型 205 行:这条踩过的坑从 footer 里提出来贴到字段下方并着琥珀色。
                NKInlineNote(text: "⚠ Base URL 需要填写完整端点（包含 /chat/completions）；地址不完整会导致连接失败。",
                             tone: .warn)
            }

            // —— 凭据 ——
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "凭据")
                NKFieldCard {
                    NKFieldRow(v: 12, h: 15) {
                        NKTextFieldBox(placeholder: model.providerForm.isEditing
                                       ? "填入新 key(留空 = 不改)" : "API key",
                                       text: $model.providerForm.apiKey,
                                       mono: true, secure: true, bordered: false)
                        if model.providerForm.isEditing {
                            NKChip(text: keySet ? "key 已配" : "key 未配",
                                   tone: keySet ? .good : .warn)
                        }
                    }
                }
                NKInlineNote(text: "key 只发一次，服务端不会回显明文。界面上只显示「已配 / 未配」。")
            }

            // —— 能力 ——
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "能力")
                NKFieldCard {
                    NKFieldRow(v: 12, h: 15) {
                        Text("启用").font(NKFont.body).foregroundStyle(NK.textPrimary)
                        Spacer(minLength: 8)
                        NKSwitch(isOn: $model.providerForm.enabled, width: 42, height: 25)
                    }
                    NKFieldSeparator()
                    NKFieldRow(v: 12, h: 15, alignment: .top) {
                        NKFieldLabel(text: "备注", width: 76)
                        NKTextFieldBox(placeholder: "可选", text: $model.providerForm.notes,
                                       bordered: false)
                    }
                }
            }

            NKTintedNote(text: "Provider 只负责推理。联网检索统一由 Tavily 完成，不再向模型端点发送厂商私有搜索工具协议。",
                         tone: .info)
        }
    }
    #endif

    #if os(iOS)
    private var iosBody: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("名称(唯一,创建后不可改)", text: $model.providerForm.name)
                        .disabled(model.providerForm.isEditing)
                        #if os(iOS)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        #endif
                    TextField("Base URL,如 https://api.example.com/v1",
                              text: $model.providerForm.baseUrl)
                        .font(NKFont.body.monospaced())
                        #if os(iOS)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        #endif
                    TextField("模型名", text: $model.providerForm.model)
                        #if os(iOS)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        #endif
                } header: {
                    Text("端点")
                }

                Section {
                    SecureField(model.providerForm.isEditing ? "填入新 key(留空 = 不改)" : "API key",
                                text: $model.providerForm.apiKey)
                        .font(NKFont.body.monospaced())
                } header: {
                    Text("凭据")
                } footer: {
                    Text("key 只发一次，之后不会回显。编辑时留空表示保持原值不变。")
                }

                Section {
                    Toggle("启用", isOn: $model.providerForm.enabled)
                    TextField("备注(可选)", text: $model.providerForm.notes)
                } header: {
                    Text("能力")
                } footer: {
                    Text("Provider 只负责推理；联网检索统一由 macOS 设置里的 Tavily 提供。")
                }
            }
            .formStyle(.grouped)
            .navigationTitle(model.providerForm.isEditing ? "编辑 Provider" : "新增 Provider")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") {
                        model.providerForm = ProviderForm()   // 安全态:key 草稿立即丢弃
                        model.showProviderForm = false
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") { Task { await model.submitProviderForm() } }
                        .disabled(!model.providerForm.isValid)
                }
            }
        }
    }
    #endif
}
