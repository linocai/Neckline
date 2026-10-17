import SwiftUI
#if os(macOS)
import AppKit
#endif

/// QA 与测试宿主不读写钥匙串。
private struct EphemeralTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}

@main struct NecklineApp: App {
    @StateObject private var config: AppConfig
    @State private var model: MorningModel
    #if os(iOS)
    @UIApplicationDelegateAdaptor(MorningPush.self) private var push
    #endif

    init() {
        let defaults = Self.sessionDefaults()
        let config = Self.makeConfig(defaults: defaults)
        _config = StateObject(wrappedValue: config)
        _model = State(initialValue: MorningModel(config: config, defaults: defaults))
    }

    var body: some Scene {
        WindowGroup {
            MorningRootView(model: model, config: config)
                .qaAppearance()
                .task { await launch() }
        }
        .defaultSize(width: 1180, height: 760)

        #if os(macOS)
        Settings {
            MorningSettingsView(model: model, config: config)
                .tint(NKMorning.accent)
                .qaAppearance()
                .frame(minWidth: 440, minHeight: 420)
        }
        #endif
    }

    private func launch() async {
        #if os(iOS)
        await push.attach(model)
        #endif
        #if DEBUG && os(macOS)
        await renderQASnapshotIfRequested()
        #endif
    }

    private static func sessionDefaults() -> UserDefaults {
        #if DEBUG
        if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil {
            return isolatedDefaults(suite: "top.linotsai.neckline.xctest")
        }
        if ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil {
            return isolatedDefaults(suite: "top.linotsai.neckline.qa.local-api")
        }
        #endif
        return .standard
    }

    private static func makeConfig(defaults: UserDefaults) -> AppConfig {
        #if DEBUG
        if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil {
            let config = AppConfig(defaults: defaults, tokenStore: EphemeralTokenStore(), loadPersistentCredentials: false)
            config.apiToken = ""
            config.baseURLOverride = ""
            return config
        }
        if let raw = ProcessInfo.processInfo.environment["NK_QA_API_URL"] {
            precondition(ProcessInfo.processInfo.environment["NK_DISABLE_PERSISTENT_CREDENTIALS"] == "1")
            guard let url = URL(string: raw),
                  let host = url.host?.lowercased(),
                  ["127.0.0.1", "localhost", "::1"].contains(host) else {
                preconditionFailure("Local QA requires a loopback API URL")
            }
            let config = AppConfig(defaults: defaults, tokenStore: EphemeralTokenStore(), loadPersistentCredentials: false)
            config.baseURLOverride = url.absoluteString
            return config
        }
        #endif
        return AppConfig(defaults: defaults)
    }

    private static func isolatedDefaults(suite: String) -> UserDefaults {
        let defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        return defaults
    }

    #if DEBUG && os(macOS)
    /// 只把晨报根视图画进调用方给定的 PNG。路径必须落在既有 QA 目录里。
    private func renderQASnapshotIfRequested() async {
        guard let path = ProcessInfo.processInfo.environment["NK_QA_RENDER_PATH"],
              path.hasPrefix("/tmp/neckline-v3-qa/"),
              path.hasSuffix(".png") else { return }
        await model.reload(.latest)
        await model.reloadHistory()
        let root = MorningRootView(model: model, config: config)
            .qaAppearance()
            .frame(width: 1180, height: 760)
        let view = NSHostingView(rootView: root)
        view.appearance = Self.qaNSAppearance()
        view.setFrameSize(NSSize(width: 1180, height: 760))
        view.layoutSubtreeIfNeeded()
        guard let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        guard let png = bitmap.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: URL(fileURLWithPath: path), options: .atomic)
    }

    private static func qaNSAppearance() -> NSAppearance? {
        switch ProcessInfo.processInfo.environment["NK_QA_APPEARANCE"] {
        case "dark": NSAppearance(named: .darkAqua)
        case "light": NSAppearance(named: .aqua)
        default: NSApp.effectiveAppearance
        }
    }
    #endif
}

private extension View {
    @ViewBuilder func qaAppearance() -> some View {
        #if DEBUG
        if ProcessInfo.processInfo.environment["NK_QA_APPEARANCE"] == "dark" {
            preferredColorScheme(.dark)
        } else if ProcessInfo.processInfo.environment["NK_QA_APPEARANCE"] == "light" {
            preferredColorScheme(.light)
        } else {
            self
        }
        #else
        self
        #endif
    }
}
