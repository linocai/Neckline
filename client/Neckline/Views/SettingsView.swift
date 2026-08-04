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

struct SettingsView: View {
    @Bindable var model: AppModel
    @ObservedObject var config: AppConfig

    @State private var tokenRevealed = false
    @State private var check: SelfCheckState = .idle
    @State private var deletingProvider: String? = nil

    var body: some View {
        #if os(iOS)
        NavigationStack { form.navigationTitle("设置").navigationBarTitleDisplayMode(.inline) }
            .sheet(isPresented: $model.showProviderForm) { ProviderFormSheet(model: model) }
        #else
        form
            .sheet(isPresented: $model.showProviderForm) {
                ProviderFormSheet(model: model).frame(width: 460, height: 560)
            }
        #endif
    }

    private var form: some View {
        Form {
            envSection
            tokenSection
            overrideSection
            selfCheckSection
            providersSection
            routesSection
            pushSection
            #if os(iOS)
            devicePushSection
            #endif
            footerSection
        }
        .formStyle(.grouped)
        .task {
            await model.loadSettings()
            await model.loadServerVersion()
        }
        .alert("删除 Provider", isPresented: Binding(get: { deletingProvider != nil },
                                                     set: { if !$0 { deletingProvider = nil } })) {
            Button("取消", role: .cancel) { deletingProvider = nil }
            Button("删除", role: .destructive) {
                if let n = deletingProvider { Task { await model.deleteProvider(name: n) } }
                deletingProvider = nil
            }
        } message: {
            Text("将删除「\(deletingProvider ?? "")」及其已保存的 key。指向它的任务路由会失去目标,记得同步改路由表。")
        }
    }

    // MARK: - 后端连接

    private var envSection: some View {
        Section {
            Picker("环境", selection: $config.environment) {
                ForEach(NKEnvironment.allCases) { env in Text(env.label).tag(env) }
            }
            LabeledContent("生效 baseURL") {
                Text(config.resolvedBaseURL.absoluteString)
                    .font(.system(size: 12.5).monospaced())
                    .foregroundStyle(NK.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
            }
        } header: {
            Text("后端连接")
        } footer: {
            Text("Dev 连本机 uvicorn(:8002);Prod 连云端 HTTPS。切换即时生效。")
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
                .font(.system(size: 14).monospaced())
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
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(config.hasToken ? NK.up : NK.amber)
            }
        } header: {
            Text("鉴权 Token")
        } footer: {
            Text("Token 仅存本机 UserDefaults,绝不提交进 git。")
        }
    }

    private var overrideSection: some View {
        Section {
            TextField("留空则用环境默认", text: $config.baseURLOverride)
                .font(.system(size: 14).monospaced())
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
                Label(desc, systemImage: "checkmark.circle.fill").font(.system(size: 13)).foregroundStyle(NK.up)
            case .tokenError:
                Label("401 · Token 错或缺(/health 通但 /positions 被拒)", systemImage: "xmark.circle.fill")
                    .font(.system(size: 13)).foregroundStyle(NK.down)
            case .networkError(let m):
                Label(m, systemImage: "exclamationmark.triangle.fill").font(.system(size: 13)).foregroundStyle(NK.amber)
            }
        } header: {
            Text("连接自检")
        } footer: {
            Text("GET /health(免鉴权)+ GET /positions(带 token)。")
        }
    }

    // MARK: - V2-② Provider 注册表(自填制;🔴 key 只写不回显)

    private var providersSection: some View {
        Section {
            if model.providers.isEmpty {
                Text("还没有配置任何 Provider —— LLM 相关能力(篮子卡叙述 / 问询台 / 提醒解析)会走优雅降级,不崩。")
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
            ForEach(model.providers) { p in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Text(p.name).font(.system(size: 13.5, weight: .semibold))
                            .foregroundStyle(NK.textPrimary)
                        if !p.enabled { NKChip(text: "已停用") }
                        if p.hasWebSearch { NKChip(text: "带联网检索", tone: .good) }
                        Spacer()
                        // **只回布尔,绝不回明文**。
                        NKChip(text: p.keySet ? "key 已配" : "key 未配",
                               tone: p.keySet ? .good : .warn)
                    }
                    Text("\(p.model) · \(p.baseUrl)")
                        .font(.system(size: 11).monospaced()).foregroundStyle(NK.textTertiary)
                        .lineLimit(1).truncationMode(.middle)
                    if let n = p.notes, !n.isEmpty {
                        Text(n).font(.system(size: 11)).foregroundStyle(NK.textSecondary)
                    }
                    HStack(spacing: 14) {
                        Button("编辑") { model.beginEditProvider(p) }
                            .buttonStyle(.plain).foregroundStyle(NK.accent)
                            .font(.system(size: 12, weight: .semibold))
                        Spacer()
                        Button("删除") { deletingProvider = p.name }
                            .buttonStyle(.plain).foregroundStyle(NK.down)
                            .font(.system(size: 12, weight: .semibold))
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
            Text("任意 OpenAI 兼容端点均可配。key 只发一次、**服务端从不回显明文**;界面上只看得到「已配 / 未配」。⚠ 勾了「带联网检索」的端点必须真支持 GLM 式 `web_search` 工具协议,否则会发错协议。")
        }
    }

    // MARK: - 任务路由表

    private var routesSection: some View {
        Section {
            if model.llmRoutes.routes.isEmpty {
                Text("暂无任务路由(全部任务走默认 Provider)。")
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
            ForEach(model.llmRoutes.routes.keys.sorted(), id: \.self) { task in
                Picker(task, selection: routeBinding(task)) {
                    Text("(不指定)").tag("")
                    ForEach(model.providers) { p in Text(p.name).tag(p.name) }
                }
            }
            LabeledContent("默认 Provider") {
                Text(model.llmRoutes.defaultProvider ?? "未设置")
                    .foregroundStyle(model.llmRoutes.defaultProvider == nil ? NK.amber : NK.textSecondary)
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
            Text("哪个任务用哪个 Provider(全量覆盖式保存)。任务名由服务端登记,填了没登记的名字会被 422 拒绝。")
        }
    }

    private func routeBinding(_ task: String) -> Binding<String> {
        Binding(get: { model.llmRoutes.routes[task] ?? "" },
                set: { model.llmRoutes.routes[task] = $0 })
    }

    // MARK: - V2-⑪ 推送开关(**按 kind 动态渲染 + 按 level 分组**)

    private var pushSection: some View {
        Section {
            if model.pushKindsDraft.isEmpty {
                Text("尚未取到通知类型清单(服务端 `notify_kinds` 是唯一源;客户端不硬编)。")
                    .font(.system(size: 12)).foregroundStyle(NK.textSecondary)
            }
            // ⛔ **不硬编 kind 清单**:服务端发什么就渲染什么;未识别的 level 也自成一组。
            ForEach(PushSettings(kinds: model.pushKindsDraft).groupedByLevel, id: \.level) { group in
                VStack(alignment: .leading, spacing: 4) {
                    Text(nkPushLevelLabel(group.level))
                        .font(.system(size: 11, weight: .bold)).foregroundStyle(NK.textTertiary)
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
            Text("开关按**通知类型**配、不按呈现分组配 —— 关掉某一类不会连坐同组里别的事。三级只决定「怎么响」,类型决定「响不响」。")
        }
    }

    #if os(iOS)
    @ViewBuilder
    private var devicePushSection: some View {
        Section {
            LabeledContent("Device Token") {
                Text(model.pushManager?.lastDeviceToken ?? "未注册")
                    .font(.system(size: 12).monospaced())
                    .foregroundStyle(model.pushManager?.lastDeviceToken == nil ? NK.textTertiary : NK.textSecondary)
                    .lineLimit(1).truncationMode(.middle)
                    .textSelection(.enabled)
            }
            if let err = model.pushManager?.registerError {
                LabeledContent("注册错误") {
                    Text(err).font(.system(size: 12.5)).foregroundStyle(NK.down).multilineTextAlignment(.trailing)
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
                Text(note).font(.system(size: 11.5)).foregroundStyle(NK.amber)
            }
        }
    }

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
            check = .networkError("/health 不可达 · 检查环境 / 网络")
            return
        }
        do {
            let positions = try await client.fetchPositions()
            check = .ok("health ok · positions ok(\(positions.count) 持仓)")
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
                        .font(.system(size: 13).monospaced())
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
                        .font(.system(size: 13).monospaced())
                } header: {
                    Text("凭据")
                } footer: {
                    Text("key 只发一次、**从不回显**。编辑时留空表示保持原值不变。")
                }

                Section {
                    Toggle("带联网检索", isOn: $model.providerForm.hasWebSearch)
                    if model.providerForm.hasWebSearch {
                        TextField("检索引擎标识(可选)", text: $model.providerForm.searchEngine)
                            #if os(iOS)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            #endif
                    }
                    Toggle("启用", isOn: $model.providerForm.enabled)
                    TextField("备注(可选)", text: $model.providerForm.notes)
                } header: {
                    Text("能力")
                } footer: {
                    Text("⚠ 勾了「带联网检索」= 按 GLM 式 `web_search` 工具协议发请求。端点若不认这套协议,搜索会静默 0 命中(这是已登记的已知代价,不是 bug)。")
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
}
