// 连接、通知和关于。iOS 在标签里，macOS 在系统设置窗口。

import SwiftUI

struct MorningSettingsView: View {
    var model: MorningModel
    @ObservedObject var config: AppConfig

    var body: some View {
        Form {
            Section("连接") {
                Picker("环境", selection: $config.environment) {
                    Text(NKEnvironment.prod.label).tag(NKEnvironment.prod)
                    Text(NKEnvironment.dev.label).tag(NKEnvironment.dev)
                }
                .pickerStyle(.segmented)
                TextField("手填地址", text: $config.baseURLOverride, prompt: Text("留空则用所选环境"))
                    #if os(iOS)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                    #endif
                    .autocorrectionDisabled()
                LabeledContent("生效主机") {
                    Text(effectiveHost)
                        .foregroundStyle(config.connectionConfigurationError == nil ? NKMorning.textSecondary : NKMorning.problem)
                        .monospacedDigit()
                        .multilineTextAlignment(.trailing)
                }
                SecureField("令牌", text: $config.apiToken)
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
                        .font(NKMorningFont.caption)
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
            }
        }
        .scrollContentBackground(.hidden)
        .background(NKMorning.page.ignoresSafeArea())
        #if os(iOS)
        .task { await MorningPush.readAuthorization(into: model) }
        #endif
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
