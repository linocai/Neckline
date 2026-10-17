// 晨报根视图。iOS 三个标签；macOS 两栏，设置放在系统设置窗口。

import SwiftUI

struct MorningRootView: View {
    @Bindable var model: MorningModel
    @ObservedObject var config: AppConfig

    var body: some View {
        shell
            .tint(NKMorning.accent)
            .onChange(of: config.apiToken) { _, _ in model.connectionInputsChanged() }
            .onChange(of: config.baseURLOverride) { _, _ in model.connectionInputsChanged() }
            .onChange(of: config.environment) { _, _ in Task { await model.handleConnectionChange() } }
    }

    @ViewBuilder private var shell: some View {
        #if os(macOS)
        NavigationSplitView {
            ReportView(slot: model.focus, model: model, showsDateMenu: true)
                .navigationSplitViewColumnWidth(min: 360, ideal: 440, max: 640)
        } detail: {
            NavigationStack { detail }
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
            NavigationStack {
                ReportView(slot: .latest, model: model, hidesNavigationBar: true)
            }
            .tabItem { Label("晨报", systemImage: "sun.horizon") }
            .tag(MorningTab.report)

            NavigationStack {
                HistoryView(model: model)
            }
            .tabItem { Label("历史", systemImage: "calendar") }
            .tag(MorningTab.history)

            NavigationStack {
                MorningSettingsView(model: model, config: config)
                    .navigationTitle("设置")
            }
            .tabItem { Label("设置", systemImage: "gearshape") }
            .tag(MorningTab.settings)
        }
        #endif
    }

    #if os(macOS)
    @ViewBuilder private var detail: some View {
        let state = model.state(for: model.focus)
        if case .ready(let report) = state, let entry = model.selectedEntry(in: report) {
            EntryDetailView(entry: entry, model: model)
        } else if case .loading = state {
            MorningLoadingView().morningScreen()
        } else if let message = model.message(for: state) {
            MorningStatusView(message: message) {
                Task { await model.reload(model.focus) }
            }
            .morningScreen()
        } else {
            MorningLoadingView().morningScreen()
        }
    }
    #endif
}
