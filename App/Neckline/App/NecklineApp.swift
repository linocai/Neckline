import SwiftUI
import Foundation
#if os(macOS)
import AppKit
#endif

private struct K10SyntheticTokenStore: APIAccessTokenStore {
    func load() -> String? { nil }
    func save(_ token: String) -> Bool { true }
}

@main struct NecklineApp: App {
    @StateObject private var config: AppConfig
    @State private var model: AppModel
    #if os(iOS)
    @UIApplicationDelegateAdaptor(K10AppDelegate.self) private var appDelegate
    #endif

    private static var usesSyntheticUI: Bool {
        #if DEBUG
        // AppKit consumes dash-prefixed launch arguments while constructing NSApplication
        // and exposes them as user-defaults. Keep the direct argument check for simulator
        // launches, and the defaults check for a macOS executable launched with the same flag.
        ProcessInfo.processInfo.arguments.contains("-K10SyntheticUI")
            || UserDefaults.standard.bool(forKey: "K10SyntheticUI")
            || ProcessInfo.processInfo.environment["K10_SYNTHETIC_UI"] == "1"
            || Bundle.main.object(forInfoDictionaryKey: "K10SyntheticUI") as? String == "YES"
            // XCTest launches the app host without the explicit UI flag. It must never
            // read the user's Keychain token or contact a configured service.
            || ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
        #else
        false
        #endif
    }

    init() {
        #if DEBUG
        if let raw = ProcessInfo.processInfo.environment["NK_QA_API_URL"] {
            precondition(ProcessInfo.processInfo.environment["NK_DISABLE_PERSISTENT_CREDENTIALS"] == "1")
            guard let url = URL(string: raw), ["127.0.0.1", "localhost", "::1"].contains(url.host ?? "") else {
                preconditionFailure("Local QA requires a loopback API URL")
            }
            let suite = "top.linotsai.neckline.qa.local-api"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            let localConfig = AppConfig(defaults: defaults, tokenStore: K10SyntheticTokenStore(), loadPersistentCredentials: false)
            localConfig.baseURLOverride = url.absoluteString
            _config = StateObject(wrappedValue: localConfig)
            _model = State(initialValue: AppModel())
            return
        }
        if Self.usesSyntheticUI {
            let suite = "top.linotsai.neckline.synthetic-ui"
            let defaults = UserDefaults(suiteName: suite)!
            defaults.removePersistentDomain(forName: suite)
            _config = StateObject(wrappedValue: AppConfig(defaults: defaults, tokenStore: K10SyntheticTokenStore(), loadPersistentCredentials: false))
            let service = K10SyntheticUIService(presentsB39State: true)
            _model = State(initialValue: AppModel(serviceFactory: { service }))
            return
        }
        #endif
        _config = StateObject(wrappedValue: AppConfig())
        _model = State(initialValue: AppModel())
    }

    var body: some Scene {
        WindowGroup {
            RootView(model: model, config: config).task {
                if Self.usesSyntheticUI {
                    await model.refresh()
                    #if DEBUG
                    if ProcessInfo.processInfo.environment["NK_QA_KEEP_FIRST"] == "1",
                       let window = model.companyWindows.first(where: { $0.companyWindowId == "synthetic-evening-window" }) {
                        await model.act("keep", window: window)
                    }
                    await applyQARoute()
                    #endif
                    return
                }
                model.bind(config: config)
                #if os(iOS)
                let initialRoute = appDelegate.attach(config: config, model: model)
                model.notificationRegistrar = { await appDelegate.requestAuthorizationAndRegister() }
                if let initialRoute { await model.openNotification(initialRoute) }
                else { await model.refresh() }
                #else
                await model.refresh()
                #endif
                #if DEBUG
                await applyQARoute()
                #endif
            }
        }.defaultSize(width: 1180, height: 760)
    }

    #if DEBUG
    /// A process-only starting route for the single reusable QA client.
    /// Real data remains read-only; synthetic selection seeding is confined above.
    private func applyQARoute() async {
        guard ProcessInfo.processInfo.environment["NK_DISABLE_PERSISTENT_CREDENTIALS"] == "1" else { return }
        if let name = ProcessInfo.processInfo.environment["NK_QA_TAB"], let tab = AppTab(rawValue: name) { model.tab = tab }
        if let window = ProcessInfo.processInfo.environment["NK_QA_DAILY_WINDOW"], ["evening", "morning"].contains(window) { model.dailyWindow = window }
        if ProcessInfo.processInfo.environment["NK_QA_MATERIALS"] == "1",
           let report = (model.dailyWindow == "morning" ? model.dailyMorning : model.dailyEvening)?.report {
            await model.openMaterials(for: report)
        }
        if ProcessInfo.processInfo.environment["NK_QA_READING"] == "1",
           let window = model.companyWindows.first(where: { model.selection(for: $0)?.state == "kept" }) {
            model.tab = .focus
            model.selectedWindow = window
        }
        #if os(macOS)
        if let path = ProcessInfo.processInfo.environment["NK_QA_RENDER_PATH"],
           path.hasPrefix("/tmp/neckline-v3-qa/"), path.hasSuffix(".png") {
            // Render only our own SwiftUI view tree. This never reads the desktop,
            // other applications or the macOS window compositor. Sheets do not present from
            // a detached hosting view, so the material QA route renders that exact view tree.
            let root: AnyView
            if ProcessInfo.processInfo.environment["NK_QA_MATERIALS"] == "1",
               let report = selectedMaterialsReportForQA {
                root = AnyView(ReportMaterialsSheet(report: report, model: model).frame(width: 1180, height: 760))
            } else {
                root = AnyView(RootView(model: model, config: config).frame(width: 1180, height: 760))
            }
            let view = NSHostingView(rootView: root)
            view.appearance = NSAppearance(named: .aqua)
            view.setFrameSize(NSSize(width: 1180, height: 760))
            view.layoutSubtreeIfNeeded()
            if let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) {
                view.cacheDisplay(in: view.bounds, to: bitmap)
                if let png = bitmap.representation(using: .png, properties: [:]) {
                    try? png.write(to: URL(fileURLWithPath: path), options: .atomic)
                }
            }
        }
        #endif
    }

    private var selectedMaterialsReportForQA: K10DailyReport? {
        (model.dailyWindow == "morning" ? model.dailyMorning : model.dailyEvening)?.report
    }
    #endif
}
