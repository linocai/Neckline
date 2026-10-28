// 根视图。iOS 底部三个标签：K10、KX、设置，K10 里再分「最新」和「历史」；
// macOS 侧栏分 K10 和 KX，K10 保持两栏，设置放在系统设置窗口。

import SwiftUI
#if DEBUG && os(macOS)
import AppKit
#endif

struct MorningRootView: View {
    @Bindable var model: MorningModel
    @Bindable var kx: KXBoardModel
    @ObservedObject var config: AppConfig
    @Environment(\.scenePhase) private var scenePhase
    #if DEBUG
    @State private var qaPath = NavigationPath()
    @State private var qaSettingsOpened = false
    #endif

    var body: some View {
        shell
            .tint(NKMorning.accent)
            .onChange(of: config.apiToken) { _, _ in
                model.connectionInputsChanged()
                kx.connectionInputsChanged()
            }
            .onChange(of: config.baseURLOverride) { _, _ in
                model.connectionInputsChanged()
                kx.connectionInputsChanged()
            }
            .onChange(of: config.environment) { _, _ in
                Task {
                    await model.handleConnectionChange()
                    await kx.handleConnectionChange()
                }
            }
            // KX 只在页面在前台、App 处于活跃时自动续取；切到别的标签、进入后台或锁屏就停。
            .onChange(of: model.tab) { _, tab in kx.setVisible(tab == .kx) }
            .onChange(of: scenePhase) { _, phase in
                kx.setActive(phase == .active)
                #if os(iOS)
                guard phase == .active else { return }
                Task { await model.retryFailedDeviceRegistration() }
                #endif
            }
            .onAppear {
                kx.setActive(scenePhase == .active)
                kx.setVisible(model.tab == .kx)
            }
            #if DEBUG
            .environment(\.nkQAScrollBottom, qaRoute == "bottom")
            .task { await applyQARoute() }
            #if os(iOS)
            .onChange(of: model.reportStackReset) { _, _ in
                qaPath = NavigationPath()
            }
            #endif
            #endif
    }

    @ViewBuilder private var shell: some View {
        #if os(macOS)
        NavigationSplitView {
            List(selection: sidebarSelection) {
                Label("K10", systemImage: "sun.max").tag(MorningTab.k10)
                Label("KX", systemImage: "tablecells").tag(MorningTab.kx)
            }
            .navigationSplitViewColumnWidth(min: 140, ideal: 160, max: 220)
        } detail: {
            if model.tab == .kx {
                KXBoardTable(kx: kx, openSettings: { MorningChrome.openSettings(model) })
            } else {
                k10Panes
            }
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
                        if model.tab == .kx {
                            await kx.refresh()
                        } else {
                            await model.reload(model.focus)
                            await model.reloadHistory()
                        }
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
            k10Tab
            kxTab
            settingsTab
        }
        #endif
    }

    #if os(iOS)
    private var k10Tab: some View {
        k10Stack
            .tabItem { Label("K10", systemImage: "sun.max") }
            .tag(MorningTab.k10)
    }

    private var kxTab: some View {
        KXBoardView(kx: kx, openSettings: { MorningChrome.openSettings(model) })
            .tabItem { Label("KX", systemImage: "tablecells") }
            .tag(MorningTab.kx)
    }

    private var settingsTab: some View {
        NavigationStack {
            MorningSettingsView(model: model, config: config)
                .navigationTitle("设置")
        }
        .tabItem { Label("设置", systemImage: "gearshape") }
        .tag(MorningTab.settings)
    }

    /// K10 一个导航栈：根页在「最新」和「历史」之间切换，点进去的详情和历史里的某一期都压在这个栈上。
    @ViewBuilder private var k10Stack: some View {
        #if DEBUG
        NavigationStack(path: $qaPath) {
            k10Root
                .navigationDestination(for: QANav.self) { qaDestination($0) }
                .navigationDestination(for: QADate.self) { date in
                    ReportView(slot: .date(date.tradeDate), model: model)
                }
        }
        .id(model.reportStackReset)
        #else
        NavigationStack {
            k10Root
        }
        .id(model.reportStackReset)
        #endif
    }

    private var k10Root: some View {
        Group {
            switch model.k10Section {
            case .latest:
                ReportView(slot: latestSlot, model: model)
            case .history:
                HistoryView(model: model)
            }
        }
        .safeAreaBar(edge: .top) {
            NKSegmentedControl(
                options: [(value: K10Section.latest, title: "最新"), (value: K10Section.history, title: "历史")],
                selection: $model.k10Section
            )
            .padding(.horizontal, NKMorningSpace.page)
            .padding(.bottom, 8)
        }
    }

    /// 截图时名单根页直接打开指定日期，不再从历史栈推进去。
    private var latestSlot: MorningSlot {
        #if DEBUG
        if qaRoute == "list", let qaTradeDate {
            return .date(qaTradeDate)
        }
        #endif
        return .latest
    }
    #endif

    #if os(macOS)
    private var sidebarSelection: Binding<MorningTab?> {
        Binding(
            get: { model.tab == .kx ? .kx : .k10 },
            set: { if let tab = $0 { model.tab = tab } }
        )
    }

    /// K10 保持两栏：左边是名单，右边是详情。
    private var k10Panes: some View {
        HSplitView {
            ReportView(slot: model.focus, model: model, showsDateMenu: true)
                .frame(minWidth: 360, idealWidth: 440, maxWidth: 640)
            k10Detail
                .frame(minWidth: 360, maxWidth: .infinity)
        }
    }

    @ViewBuilder private var k10Detail: some View {
        #if DEBUG
        NavigationStack(path: $qaPath) {
            detail
                .navigationDestination(for: QANav.self) { qaDestination($0) }
        }
        #else
        NavigationStack { detail }
        #endif
    }
    #endif

    #if os(macOS)
    @ViewBuilder private var detail: some View {
        let state = model.state(for: model.focus)
        switch state {
        case .ready(let report):
            detailColumn(report, promptIfNone: true)
        case .empty(let report):
            detailColumn(report, promptIfNone: false)
        case .error(.noReport):
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

    @ViewBuilder private func detailColumn(_ report: MorningReport, promptIfNone: Bool) -> some View {
        if let entry = model.detailEntry(in: report) {
            EntryDetailView(entry: entry, model: model)
        } else if promptIfNone {
            Color.clear
                .morningScreen()
                .overlay {
                    Text("选择左侧一条查看详情")
                        .font(NKMorningFont.caption)
                        .foregroundStyle(NKMorning.textSecondary)
                }
        } else {
            Color.clear.morningScreen()
        }
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
        case "notify":
            #if os(iOS)
            // 不在这里清路径：截图要证明是点推送本身把栈退回了名单。
            pushFirst(document: false, slot: slot)
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            await model.handleNotificationTap()
            #endif
        case "history":
            model.tab = .k10
            model.k10Section = .history
            await model.reloadHistory()
        case "kx":
            model.tab = .kx
            await kx.refresh()
        case "settings":
            model.tab = .settings
            await runConnectionCheck()
        case "list":
            break
        default:
            break
        }
        let slow = qaRoute == "document" || qaRoute == "settings" || qaRoute == "bottom" || qaRoute == "notify" || qaRoute == "detail"
        let pause: UInt64 = slow ? 1_200_000_000 : 600_000_000
        try? await Task.sleep(nanoseconds: pause)
        #if os(macOS)
        // 窗口位置会在首帧之后才从偏好恢复，恢复完再定一次尺寸，截图才够高。
        await resizeQAMainWindow()
        #endif
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
        let size = NSSize(width: 1480, height: 1280)
        let window = NSApp.mainWindow ?? NSApp.windows.first
        guard let window else { return }
        window.isRestorable = false
        window.setContentSize(size)
        var frame = window.frame
        frame.size = NSSize(width: size.width, height: size.height + window.frame.height - window.contentLayoutRect.height)
        window.setFrame(frame, display: true)
        window.center()
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
