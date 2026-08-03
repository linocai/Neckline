//
//  SettingsView.swift
//  Neckline — 设置(§五 阶段4C.4,🔴 APNs 相关建议 @builder-pro 复审):
//  后端地址 + API token · LLM 供应商切换 + key 填写(安全态,不回显存量 key)·
//  推送开关(v1.1-G.1 扩至四类:报告 / 退潮刹车 / 盘前校准 / D5 时间退出)·
//  连接自检 · iOS 推送重注册。
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

    var body: some View {
        #if os(iOS)
        NavigationStack { form.navigationTitle("设置").navigationBarTitleDisplayMode(.inline) }
        #else
        form
        #endif
    }

    private var form: some View {
        Form {
            envSection
            tokenSection
            overrideSection
            selfCheckSection
            llmSection
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
            Text("Dev 连本机 uvicorn(:8002);Prod 连 hz ECS(ln.linotsai.top,HTTPS)。切换即时生效。")
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

    // MARK: - LLM(🔴 key 服务端存取)

    private var llmSection: some View {
        Section {
            Picker("供应商", selection: $model.llmProviderDraft) {
                ForEach(LLMProviderKind.allCases) { p in Text(p.label).tag(p) }
            }
            SecureField("填入新 key(不回显存量)", text: $model.llmKeyDraft)
                .font(.system(size: 14).monospaced())
            HStack {
                Text("当前状态")
                Spacer()
                Label(model.settings.llmKeySet ? "已配置(\(model.settings.llmProvider ?? "—"))" : "未配置",
                      systemImage: model.settings.llmKeySet ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(model.settings.llmKeySet ? NK.up : NK.amber)
            }
            Button("保存 LLM 设置") { Task { await model.saveLLMSettings() } }
                .disabled(model.llmKeyDraft.trimmingCharacters(in: .whitespaces).isEmpty)
        } header: {
            Text("LLM(GLM / Kimi)")
        } footer: {
            Text("激活问询台深判 + 报告 LLM 审判 + 复盘材料;缺 key 全链路优雅降级不崩。key 只服务端存取,App 从不回显存量值。")
        }
    }

    // MARK: - 推送开关

    private var pushSection: some View {
        Section {
            Toggle("16:35 报告就绪", isOn: $model.pushReportDraft)
            Toggle("退潮红色刹车", isOn: $model.pushRetreatDraft)
            Toggle("9:26 盘前校准汇总", isOn: $model.pushPrecallDraft)
            Toggle("D5 时间退出", isOn: $model.pushD5exitDraft)
            // v1.2-A2:第五类(熔断提醒,默认开,§2.1 第 7 条)。
            Toggle("熔断提醒", isOn: $model.pushCircuitDraft)
            // v1.3-②/⑥:第六类(K4 持仓派发警报,默认开)——独立开关,不复用 D5 时间退出通道
            // (年线下涨停/放量大阳等强价量证据疑似派发,与"持有到期了"是两回事)。
            Toggle("K4 持仓派发警报", isOn: $model.pushHoldingAlertDraft)
            Button("保存推送设置") { Task { await model.savePushSettings() } }
        } header: {
            Text("APNs 推送")
        } footer: {
            Text("买点触发 / 证伪剔除 / 持仓预警(普通级)不推送,只进「盘中看板」/持仓卡(§2.4 拍板)。")
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
            Text("锁屏推送")
        } footer: {
            Text("切到 prod 后点此,把 device token 重新注册到 prod 库。模拟器拿不到真 token,真推留阶段 4E。")
        }
    }
    #endif

    // v1.5-⑤-E:A2 版本号治理——设置屏诚实展示「App 版本 + 服务端版本」双版本(此前
    // `health()` 拿到的 `version` 被丢弃,现改为展示);不一致时只提示、不拦任何功能。
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

    /// 服务端 vs App 版本号不一致时的提示文案(§五 v1.5-⑤-E「只提示,不拦任何功能」)。
    /// 两者都源自同一个 `MARKETING_VERSION`(A2 版本号治理单一源 + 守门单测锁三处
    /// 恒等),故去掉服务端 "v" 前缀后与 `CFBundleShortVersionString` 直接字符串比较
    /// 即可,不需要语义化版本号解析。服务端版本未知(尚未连通)时不提示——沉默,不是
    /// "已确认一致"。
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
        // v1.5-⑤-E:自检本就打了一发 /health,顺手把拿到的版本回填模型(比等 .task
        // 里那次更新鲜——用户可能刚切了环境/改了 baseURL 才点的自检)。
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
