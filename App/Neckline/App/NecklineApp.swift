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
    @State private var kx: KXBoardModel
    #if os(iOS)
    @UIApplicationDelegateAdaptor(MorningPush.self) private var push
    #endif

    init() {
        let defaults = Self.sessionDefaults()
        let config = Self.makeConfig(defaults: defaults)
        let model = MorningModel(config: config, defaults: defaults)
        let kx = KXBoardModel(config: config)
        #if DEBUG
        Self.applyQALaunchState(to: model)
        Self.applyQALaunchState(to: kx)
        #endif
        _config = StateObject(wrappedValue: config)
        _model = State(initialValue: model)
        _kx = State(initialValue: kx)
    }

    var body: some Scene {
        WindowGroup {
            MorningRootView(model: model, kx: kx, config: config)
                .qaAppearance()
                .task { await launch() }
        }
        .defaultSize(width: Self.initialWindowSize.width, height: Self.initialWindowSize.height)

        #if os(macOS)
        Settings {
            MorningSettingsView(model: model, config: config)
                .tint(NKMorning.accent)
                .qaAppearance()
                .frame(width: 520)
                .fixedSize(horizontal: false, vertical: true)
        }
        .windowResizability(.contentSize)
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

    #if DEBUG
    /// 截图进程在第一帧之前选定期次和标签。日期设 macOS 焦点；iOS 名单在根页打开该期。
    private static func applyQALaunchState(to model: MorningModel) {
        guard ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil else { return }
        if let date = ProcessInfo.processInfo.environment["NK_QA_TRADE_DATE"]?.trimmingCharacters(in: .whitespacesAndNewlines),
           !date.isEmpty {
            model.focus = .date(date)
        }
        switch ProcessInfo.processInfo.environment["NK_QA_ROUTE"] {
        case "history": model.k10Section = .history
        case "kx": model.tab = .kx
        case "settings": model.tab = .settings
        default: break
        }
    }

    /// 截图进程直接落在指定的分组上，省得点分段控件。
    private static func applyQALaunchState(to kx: KXBoardModel) {
        guard ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil else { return }
        switch ProcessInfo.processInfo.environment["NK_QA_KX_GROUP"] {
        case "today": kx.group = .today
        case "volume": kx.group = .volume
        case "trend": kx.group = .trend
        default: break
        }
    }

    private static var initialWindowSize: CGSize {
        ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil
            ? CGSize(width: 1480, height: 1280)
            : CGSize(width: 1180, height: 760)
    }
    #else
    private static var initialWindowSize: CGSize { CGSize(width: 1180, height: 760) }
    #endif

    #if DEBUG && os(macOS)
    /// 只把晨报根视图画进调用方给定的 PNG。路径必须落在既有 QA 目录里。
    private func renderQASnapshotIfRequested() async {
        guard let path = ProcessInfo.processInfo.environment["NK_QA_RENDER_PATH"],
              path.hasPrefix("/tmp/neckline-v3-qa/"),
              path.hasSuffix(".png") else { return }
        await model.reload(.latest)
        await model.reloadHistory()
        let root = MorningRootView(model: model, kx: kx, config: config)
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
