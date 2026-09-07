import SwiftUI

struct RootView: View {
    @Bindable var model: AppModel
    @ObservedObject var config: AppConfig
    private let mainTabs: [AppTab] = [.opportunities, .focus, .performance]

    var body: some View {
        shell
            .tint(NK.accent)
            .foregroundStyle(NK.textPrimary)
            .background(NK.pageBg)
            .preferredColorScheme(.light)
            .onChange(of: config.resolvedBaseURL.absoluteString) { _, _ in
                model.resetForConnectionChange(); model.bind(config: config)
            }
            .onChange(of: config.apiToken) { _, _ in
                model.resetForConnectionChange(); model.bind(config: config)
            }
            .sheet(item: primaryOpportunity) { OpportunitySheet(detail: $0, model: model) }
            #if os(iOS)
            .sheet(item: $model.selectedWindow) { window in
                FocusReadingSheet(window: window, model: model)
            }
            #endif
            .alert("Neckline", isPresented: Binding(get: { model.toast != nil }, set: { if !$0 { model.toast = nil } })) {
                Button("好", role: .cancel) {}
            } message: { Text(model.toast ?? "") }
    }

    private var primaryOpportunity: Binding<K10OpportunityDetail?> {
        Binding(get: {
            #if os(iOS)
            return model.selectedWindow == nil ? model.selectedOpportunity : nil
            #else
            return model.selectedOpportunity
            #endif
        }, set: { model.selectedOpportunity = $0 })
    }

    @ViewBuilder private var shell: some View {
        #if os(macOS)
        VStack(spacing: 0) {
            desktopHeader
            if model.tab == .settings {
                NavigationStack { SettingsView(model: model, config: config) }
            } else { page }
        }.frame(minWidth: 920, minHeight: 640)
        #else
        VStack(spacing: 0) {
            phoneHeader
            NavigationStack {
                page
                    .toolbar(.hidden, for: .navigationBar)
            }.id(model.tab)
            phoneNavigation
        }
        #endif
    }

    @ViewBuilder private var page: some View {
        switch model.tab {
        case .opportunities: OpportunitiesView(model: model)
        case .focus: FocusView(model: model)
        case .performance: PerformanceView(model: model)
        case .settings: SettingsView(model: model, config: config)
        }
    }

    private var wordmark: some View {
        HStack(spacing: 9) {
            Image(systemName: "square.stack.3d.up")
                .font(.system(size: 20, weight: .medium))
                .foregroundStyle(NK.accent)
            Text("Neckline").font(.system(size: 19, weight: .semibold))
            #if DEBUG
            if ProcessInfo.processInfo.environment["K10_SYNTHETIC_UI"] == "1" {
                Text("合成数据").font(.system(size: 11)).foregroundStyle(NK.amber)
            }
            #endif
        }.accessibilityElement(children: .combine)
    }

    private var refreshButton: some View {
        Button { Task { await model.refresh() } } label: {
            Image(systemName: "arrow.clockwise")
                .font(.system(size: 17, weight: .regular))
                .frame(width: 40, height: 40)
        }.buttonStyle(.plain).foregroundStyle(NK.textSecondary)
            .accessibilityLabel("刷新机会与资料")
    }

    private var settingsButton: some View {
        Button { model.tab = model.tab == .settings ? .opportunities : .settings } label: {
            Image(systemName: model.tab == .settings ? "xmark" : "gearshape")
                .font(.system(size: 19, weight: .regular))
                .frame(width: 40, height: 40)
        }.buttonStyle(.plain)
            .foregroundStyle(model.tab == .settings ? NK.accent : NK.textSecondary)
            .accessibilityLabel(model.tab == .settings ? "返回机会" : "设置")
    }

    #if os(macOS)
    private var desktopHeader: some View {
        HStack(spacing: 18) {
            wordmark.frame(width: 230, alignment: .leading)
            Spacer(minLength: 0)
            HStack(spacing: 36) {
                ForEach(mainTabs) { tab in
                    Button { model.tab = tab } label: {
                        HStack(spacing: 7) {
                            Image(systemName: tab.icon).font(.system(size: 16))
                            Text(tab.title).font(NKFont.headline)
                        }
                        .foregroundStyle(model.tab == tab ? NK.accent : NK.textSecondary)
                        .frame(height: 62)
                        .overlay(alignment: .bottom) {
                            Rectangle().fill(model.tab == tab ? NK.accent : .clear).frame(height: 3)
                        }
                    }.buttonStyle(.plain)
                }
            }
            Spacer(minLength: 0)
            HStack(spacing: 5) {
                Text("K10-v1.4").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                refreshButton
                settingsButton
            }.frame(width: 230, alignment: .trailing)
        }
        .padding(.horizontal, 28)
        .background(Color.white)
        .overlay(alignment: .bottom) { Rectangle().fill(NK.hairline).frame(height: 1) }
    }
    #else
    private var phoneHeader: some View {
        HStack {
            wordmark
            Spacer()
            refreshButton
            settingsButton
        }
        .padding(.leading, NKSpace.pagePad)
        .padding(.trailing, NKSpace.pagePad - 8)
        .frame(height: 52)
        .background(Color.white)
    }

    private var phoneNavigation: some View {
        HStack(spacing: 0) {
            ForEach(mainTabs) { tab in
                Button { model.tab = tab } label: {
                    VStack(spacing: 5) {
                        Image(systemName: tab.icon)
                            .font(.system(size: 21, weight: .regular))
                            .symbolVariant(.none)
                        Text(tab.title).font(.system(size: 11, weight: model.tab == tab ? .semibold : .regular))
                    }
                    .foregroundStyle(model.tab == tab ? NK.accent : NK.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 59)
                    .contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .accessibilityAddTraits(model.tab == tab ? [.isSelected] : [])
            }
        }
        .padding(.horizontal, 20)
        .background(Color.white)
        .overlay(alignment: .top) { Rectangle().fill(NK.hairline).frame(height: 1) }
    }
    #endif
}

#if os(iOS)
private struct FocusReadingSheet: View {
    let window: K10CompanyWindow
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                if let detail = model.selection(for: window) {
                    FocusReadingPane(window: window, detail: detail, model: model)
                        .padding(NKSpace.pagePad)
                } else {
                    V3EmptyState(icon: "doc.text", title: "尚无分析资料", message: "刷新后可查看已留下公司的分析进度。")
                }
            }
            .background(NK.pageBg)
            .navigationTitle("正反观点")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .sheet(item: $model.selectedOpportunity) { OpportunitySheet(detail: $0, model: model) }
        }
    }
}
#endif
