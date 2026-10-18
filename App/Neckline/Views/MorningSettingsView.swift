// 连接、通知和关于。iOS 在标签里，macOS 在系统设置窗口。

import SwiftUI

struct MorningSettingsView: View {
    var model: MorningModel
    @ObservedObject var config: AppConfig

    var body: some View {
        ScrollViewReader { proxy in
            form
                #if DEBUG
                .onAppear { scrollAboutIntoView(proxy) }
                #endif
        }
    }

    private var form: some View {
        Form {
            Section("连接") {
                Picker(selection: $config.environment) {
                    Text(NKEnvironment.prod.label).tag(NKEnvironment.prod)
                    Text(NKEnvironment.dev.label).tag(NKEnvironment.dev)
                } label: {
                    fieldName("环境")
                }
                .pickerStyle(.segmented)
                addressField
                LabeledContent {
                    Text(effectiveHost)
                        .foregroundStyle(config.connectionConfigurationError == nil ? NKMorning.textSecondary : NKMorning.problem)
                        .monospacedDigit()
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                } label: {
                    fieldName("生效主机")
                }
                tokenField
                Button("检查连接") { Task { await model.checkConnection() } }
                    .disabled(model.isCheckingConnection)
                if model.isCheckingConnection {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(MorningCopy.reading)
                            .font(NKMorningFont.caption)
                            .foregroundStyle(NKMorning.textSecondary)
                    }
                }
                ForEach(model.connectionLines) { line in
                    Text(line.text)
                        .nkScaledFont(.caption)
                        .foregroundStyle(line.problem ? NKMorning.problem : NKMorning.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            #if os(iOS)
            Section("通知") {
                LabeledContent("权限", value: model.notificationPermission.title)
                if let registration = model.notificationRegistration {
                    LabeledContent("登记", value: registration.title)
                }
            }
            #endif
            Section("关于") {
                Text(about)
                    .foregroundStyle(NKMorning.textPrimary)
                    .id("nk-qa-about")
            }
        }
        .formStyle(.grouped)
        #if os(iOS)
        .scrollContentBackground(.hidden)
        .containerBackground(NKMorning.page, for: .navigation)
        .scrollEdgeEffectStyle(.soft, for: .top)
        .navigationTitle("设置")
        .navigationBarTitleDisplayMode(.large)
        .toolbarTitleDisplayMode(.large)
        .task { await MorningPush.readAuthorization(into: model) }
        #endif
    }

    #if DEBUG
    private func scrollAboutIntoView(_ proxy: ScrollViewProxy) {
        guard ProcessInfo.processInfo.environment["NK_QA_ROUTE"] == "settings" else { return }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 450_000_000)
            var transaction = Transaction()
            transaction.disablesAnimations = true
            withTransaction(transaction) {
                proxy.scrollTo("nk-qa-about", anchor: .bottom)
            }
        }
    }
    #endif

    private func fieldName(_ title: String) -> some View {
        Text(title)
            .lineLimit(1)
            .fixedSize(horizontal: true, vertical: false)
            .layoutPriority(1)
    }

    @ViewBuilder private var addressField: some View {
        LabeledContent {
            #if os(iOS)
            TextField("留空则用所选环境", text: $config.baseURLOverride)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
                .multilineTextAlignment(.trailing)
                .autocorrectionDisabled()
            #else
            TextField("", text: $config.baseURLOverride, prompt: Text("留空则用所选环境"))
                .autocorrectionDisabled()
            #endif
        } label: {
            fieldName("地址")
        }
    }

    @ViewBuilder private var tokenField: some View {
        LabeledContent {
            #if os(iOS)
            SecureField("令牌", text: $config.apiToken)
                .multilineTextAlignment(.trailing)
            #else
            SecureField("", text: $config.apiToken)
            #endif
        } label: {
            fieldName("令牌")
        }
    }

    private var effectiveHost: String {
        if let error = config.connectionConfigurationError { return error }
        let url = config.resolvedBaseURL
        guard let host = url.host else { return url.absoluteString }
        if let port = url.port { return "\(host):\(port)" }
        return host
    }

    private var about: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return "Neckline \(version) (\(build))"
    }
}
