// 晨报根视图。iOS 三个标签；macOS 两栏，设置放在系统设置窗口。

import SwiftUI
#if DEBUG && os(macOS)
import AppKit
#endif

struct MorningRootView: View {
    @Bindable var model: MorningModel
    @ObservedObject var config: AppConfig
    #if DEBUG
    @State private var qaPath = NavigationPath()
    @State private var qaHistoryPath = NavigationPath()
    @State private var qaSettingsOpened = false
    #endif

    var body: some View {
        shell
            .tint(NKMorning.accent)
            .onChange(of: config.apiToken) { _, _ in model.connectionInputsChanged() }
            .onChange(of: config.baseURLOverride) { _, _ in model.connectionInputsChanged() }
            .onChange(of: config.environment) { _, _ in Task { await model.handleConnectionChange() } }
            #if DEBUG
            .environment(\.nkQAReviewOpen, qaRoute == "review")
            .environment(\.nkQAScrollBottom, qaRoute == "bottom")
            .task { await applyQARoute() }
            #endif
    }

    @ViewBuilder private var shell: some View {
        #if os(macOS)
        NavigationSplitView {
            ReportView(slot: model.focus, model: model, showsDateMenu: true)
                .navigationSplitViewColumnWidth(min: 360, ideal: 440, max: 640)
        } detail: {
            #if DEBUG
            NavigationStack(path: $qaPath) {
                detail
                    .navigationDestination(for: QANav.self) { qaDestination($0) }
            }
            #else
            NavigationStack { detail }
            #endif
        }
        .navigationSplitViewStyle(.balanced)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                SettingsLink {
                    Image(systemName: "gearshape")
                }
                .accessibilityLabel("设置")
                .help("设置")
                Button {
                    Task {
                        await model.reload(model.focus)
                        await model.reloadHistory()
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .accessibilityLabel("刷新")
                .help("刷新")
            }
        }
        #else
        TabView(selection: $model.tab) {
            reportTab
            historyTab
            settingsTab
        }
        #endif
    }

    #if os(iOS)
    private var reportTab: some View {
        reportStack
            .tabItem { Label("晨报", systemImage: "sun.horizon") }
            .tag(MorningTab.report)
    }

    private var historyTab: some View {
        historyStack
            .tabItem { Label("历史", systemImage: "calendar") }
            .tag(MorningTab.history)
    }

    private var settingsTab: some View {
        NavigationStack {
            MorningSettingsView(model: model, config: config)
                .navigationTitle("设置")
        }
        .tabItem { Label("设置", systemImage: "gearshape") }
        .tag(MorningTab.settings)
    }

    @ViewBuilder private var reportStack: some View {
        #if DEBUG
        NavigationStack(path: $qaPath) {
            ReportView(slot: .latest, model: model)
                .navigationDestination(for: QANav.self) { qaDestination($0) }
        }
        #else
        NavigationStack {
            ReportView(slot: .latest, model: model)
        }
        #endif
    }

    @ViewBuilder private var historyStack: some View {
        #if DEBUG
        NavigationStack(path: $qaHistoryPath) {
            HistoryView(model: model)
                .navigationDestination(for: QADate.self) { date in
                    ReportView(slot: .date(date.tradeDate), model: model)
                }
        }
        #else
        NavigationStack {
            HistoryView(model: model)
        }
        #endif
    }
    #endif

    #if os(macOS)
    @ViewBuilder private var detail: some View {
        let state = model.state(for: model.focus)
        switch state {
        case .ready(let report):
            if let entry = selectedDetailEntry(in: report) {
                EntryDetailView(entry: entry, model: model)
            } else {
                Color.clear
                    .morningScreen()
                    .overlay {
                        Text("选择左侧一条查看详情")
                            .font(NKMorningFont.caption)
                            .foregroundStyle(NKMorning.textSecondary)
                    }
            }
        case .empty, .error(.noReport):
            Color.clear.morningScreen()
        case .loading:
            MorningLoadingView().morningScreen()
        default:
            if let message = model.message(for: state) {
                MorningStatusView(message: message, retry: { Task { await model.reload(model.focus) } }, openSettings: { MorningChrome.openSettings(model) })
                    .morningScreen()
            } else {
                MorningLoadingView().morningScreen()
            }
        }
    }

    private func selectedDetailEntry(in report: MorningReport) -> MorningEntry? {
        guard let selectedEntryKey = model.selectedEntryKey else { return nil }
        return report.content?.entries.first { $0.listID == selectedEntryKey }
    }
    #endif

    #if DEBUG
    private struct QADate: Hashable {
        let tradeDate: String
    }

    private enum QANav: Hashable {
        case detail(String)
        case document(String)
    }

    private var qaRoute: String {
        let raw = ProcessInfo.processInfo.environment["NK_QA_ROUTE"]?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let raw, !raw.isEmpty { return raw }
        return "list"
    }

    private var qaTradeDate: String? {
        let raw = ProcessInfo.processInfo.environment["NK_QA_TRADE_DATE"]?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let raw, !raw.isEmpty else { return nil }
        return raw
    }

    @ViewBuilder private func qaDestination(_ step: QANav) -> some View {
        switch step {
        case .detail(let listID):
            if let entry = entry(listID: listID) {
                EntryDetailView(entry: entry, model: model)
            }
        case .document(let documentID):
            DocumentView(documentID: documentID, model: model)
        }
    }

    private func applyQARoute() async {
        guard ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil else { return }
        #if os(macOS)
        await resizeQAMainWindow()
        #endif
        let slot = model.focus
        await settle(slot)
        switch qaRoute {
        case "detail":
            pushFirst(document: false, slot: slot)
        case "document":
            pushFirst(document: true, slot: slot)
        case "history":
            model.tab = .history
            await model.reloadHistory()
        case "settings":
            model.tab = .settings
            await runConnectionCheck()
        case "list":
            #if os(iOS)
            if let qaTradeDate {
                await openHistoryDate(qaTradeDate)
            }
            #endif
        default:
            break
        }
        let pause: UInt64 = (qaRoute == "document" || qaRoute == "review" || qaRoute == "settings" || qaRoute == "bottom") ? 1_200_000_000 : 600_000_000
        try? await Task.sleep(nanoseconds: pause)
        markQAReady()
    }

    private func markQAReady() {
        #if os(macOS)
        let wins = NSApp.windows.map { "\($0.title)|\($0.identifier?.rawValue ?? "-")|\(Int($0.frame.width))x\(Int($0.frame.height))" }.joined(separator: ";")
        let note = "\(qaRoute) \(slotName(model.focus)) \(stateName(model.state(for: model.focus))) lines=\(model.connectionLines.count) settings=\(qaSettingsOpened) wins=\(wins)"
        #else
        let note = "\(qaRoute) \(slotName(model.focus)) \(stateName(model.state(for: model.focus))) lines=\(model.connectionLines.count)"
        #endif
        let data = Data(note.utf8)
        try? data.write(to: URL(fileURLWithPath: "/tmp/neckline-v3-qa/app-ready"), options: .atomic)
        let local = FileManager.default.temporaryDirectory.appendingPathComponent("neckline-qa-ready")
        try? data.write(to: local, options: .atomic)
    }

    private func slotName(_ slot: MorningSlot) -> String {
        switch slot {
        case .latest: "latest"
        case .date(let date): date
        }
    }

    private func stateName(_ state: MorningLoadState) -> String {
        switch state {
        case .loading: "loading"
        case .ready: "ready"
        case .empty: "empty"
        case .failed: "failed"
        case .running: "running"
        case .error: "error"
        }
    }

    private func settle(_ slot: MorningSlot) async {
        if case .loading = model.state(for: slot) {
            await model.reload(slot)
        }
        for _ in 0..<40 where !isSettled(slot) {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    private func isSettled(_ slot: MorningSlot) -> Bool {
        switch model.state(for: slot) {
        case .loading: false
        default: true
        }
    }

    private func pushFirst(document: Bool, slot: MorningSlot) {
        guard case .ready(let report) = model.state(for: slot),
              let entry = report.content?.entries.first else { return }
        model.select(entry)
        guard qaPath.isEmpty else { return }
        if document, let documentID = entry.sources.first?.documentId {
            qaPath.append(QANav.document(documentID))
        } else if !document {
            #if os(iOS)
            qaPath.append(QANav.detail(entry.listID))
            #endif
        }
    }

    private func entry(listID: String) -> MorningEntry? {
        reportEntries(for: model.focus).first { $0.listID == listID }
    }

    private func reportEntries(for slot: MorningSlot) -> [MorningEntry] {
        model.report(for: slot)?.content?.entries ?? []
    }

    #if os(iOS)
    private func openHistoryDate(_ tradeDate: String) async {
        await model.reload(.date(tradeDate))
        model.tab = .history
        try? await Task.sleep(nanoseconds: 350_000_000)
        if qaHistoryPath.isEmpty {
            qaHistoryPath.append(QADate(tradeDate: tradeDate))
        }
        for _ in 0..<30 where !isSettled(.date(tradeDate)) {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }
    #endif

    private func runConnectionCheck() async {
        #if os(macOS)
        let opened = await presentMacSettings()
        qaSettingsOpened = opened
        if opened, await pressCheckConnection() {
            for _ in 0..<30 {
                if !model.isCheckingConnection && !model.connectionLines.isEmpty { return }
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        #endif
        await model.checkConnection()
    }

    #if os(macOS)
    private func presentMacSettings() async -> Bool {
        for _ in 0..<12 {
            NSApp.activate(ignoringOtherApps: true)
            if openSettingsFromMenu() || NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil) {
                for _ in 0..<8 {
                    if let window = settingsWindow() {
                        window.makeKeyAndOrderFront(nil)
                        return true
                    }
                    try? await Task.sleep(nanoseconds: 100_000_000)
                }
            }
            try? await Task.sleep(nanoseconds: 150_000_000)
        }
        return settingsWindow() != nil
    }

    private func openSettingsFromMenu() -> Bool {
        guard let items = NSApp.mainMenu?.items else { return false }
        for top in items {
            guard let submenu = top.submenu else { continue }
            for item in submenu.items {
                let title = item.title
                guard title.contains("设置") || title.contains("Settings") || title.contains("偏好") else { continue }
                guard let action = item.action else { continue }
                return NSApp.sendAction(action, to: item.target, from: item)
            }
        }
        return false
    }

    private func settingsWindow() -> NSWindow? {
        if let named = NSApp.windows.first(where: { window in
            let title = window.title
            if title.contains("设置") || title.contains("Settings") || title.contains("偏好") { return true }
            let identifier = window.identifier?.rawValue ?? ""
            return identifier.localizedCaseInsensitiveContains("settings")
        }) {
            return named
        }
        return NSApp.windows.first { window in
            window.isVisible && abs(window.frame.width - 1280) > 80 && window.frame.width > 200
        }
    }

    private func resizeQAMainWindow() async {
        for _ in 0..<25 where NSApp.mainWindow == nil && NSApp.windows.isEmpty {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        let size = NSSize(width: 1280, height: 800)
        let window = NSApp.mainWindow ?? NSApp.windows.first
        window?.setContentSize(size)
        window?.center()
    }

    private func pressCheckConnection() async -> Bool {
        let app = AXUIElementCreateApplication(getpid())
        for _ in 0..<25 {
            if let button = findButton(label: "检查连接", element: app, depth: 0) {
                return AXUIElementPerformAction(button, kAXPressAction as CFString) == .success
            }
            try? await Task.sleep(nanoseconds: 120_000_000)
        }
        return false
    }

    private func findButton(label: String, element: AXUIElement, depth: Int) -> AXUIElement? {
        if depth > 10 { return nil }
        var role: CFTypeRef?
        AXUIElementCopyAttributeValue(element, kAXRoleAttribute as CFString, &role)
        if (role as? String) == (kAXButtonRole as String) {
            var title: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXTitleAttribute as CFString, &title)
            if (title as? String) == label { return element }
            var description: CFTypeRef?
            AXUIElementCopyAttributeValue(element, kAXDescriptionAttribute as CFString, &description)
            if (description as? String) == label { return element }
        }
        var children: CFTypeRef?
        AXUIElementCopyAttributeValue(element, kAXChildrenAttribute as CFString, &children)
        for child in (children as? [AXUIElement]) ?? [] {
            if let found = findButton(label: label, element: child, depth: depth + 1) { return found }
        }
        return nil
    }
    #endif
    #endif
}
