//
//  RootView.swift
//  Neckline — 导航壳(平台分叉:iOS 底部 TabView / **macOS 50px 统一工具栏**)。
//
//  🔴 **信息架构 = 三板块 选股 / 成绩 / 复盘 + 设置沉底**(**裁定 11**,⛔ 施工时不得重开)。
//  设置**在产品语义上不算板块** —— 它只是个入口(iOS 排 TabBar 最后一项、
//  macOS 沉成工具栏右端的齿轮),所以既不进「交易」组也不进「复盘」组。
//
//  macOS 使用统一工具栏；板块内部采用列表栏与详情栏布局。
//

import SwiftUI

struct RootView: View {
    @Bindable var model: AppModel
    @ObservedObject var config: AppConfig

    var body: some View {
        Group {
            #if os(iOS)
            iosShell
            #else
            macShell
            #endif
        }
        .preferredColorScheme(.light)
        .onChange(of: config.resolvedBaseURL.absoluteString) { _, _ in
            model.clearReportSnapshotCache()
        }
        .onChange(of: config.apiToken) { _, _ in
            model.clearReportSnapshotCache()
        }
    }

    // MARK: - iOS: 底部 TabView（选股 / 成绩 / 复盘 / 设置）

    #if os(iOS)
    private var iosShell: some View {
        TabView(selection: Binding(get: { model.view }, set: { model.view = $0 })) {
            SelectionView(model: model)
                .tabItem { Label(AppTab.selection.title, systemImage: AppTab.selection.systemImage) }
                .tag(AppTab.selection)
            ScoreboardView(model: model)
                .tabItem { Label(AppTab.scoreboard.title, systemImage: AppTab.scoreboard.systemImage) }
                .tag(AppTab.scoreboard)
            ReviewView(model: model)
                .tabItem { Label(AppTab.review.title, systemImage: AppTab.review.systemImage) }
                .tag(AppTab.review)
            // ⚠ **设置排最后**:它是入口不是板块,⛔ 别把它挪到板块中间去。
            SettingsView(model: model, config: config)
                .tabItem { Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage) }
                .tag(AppTab.settings)
        }
        .tint(NK.accent)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 90) }
        // 只加载**当前 Tab**(默认 `.selection`,QA 钩子可覆盖 ——
        // `NecklineApp.init()` 里 `m.view = tab` 早于本 `.task` 执行)。
        .task { await bootstrap() }
        // 切 Tab 首次到达时才拉那个 Tab 的数据;已加载过的 Tab 再切回来不重打请求。
        .onChange(of: model.view) { _, tab in Task { await model.ensureLoaded(tab) } }
    }
    #endif

    // MARK: - macOS:50px 统一工具栏 + 内容区(板块内部再分列表栏 / 详情栏)

    #if os(macOS)
    private var macShell: some View {
        VStack(spacing: 0) {
            NKToolbar(model: model)
            content
        // 内容区必须吃满高度，内层滚动区才能正确计算。
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 1080, maxWidth: .infinity, minHeight: 640, maxHeight: .infinity)
        // 隐藏标题栏后仍需忽略顶部安全区，使工具栏贴齐窗口顶部。
        .ignoresSafeArea(.container, edges: .top)
        .background(NK.pageBg)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 24) }
        .task { await bootstrap() }
        .onChange(of: model.view) { _, tab in Task { await model.ensureLoaded(tab) } }
    }

    @ViewBuilder
    private var content: some View {
        switch model.view {
        case .selection: SelectionView(model: model)
        case .scoreboard: ScoreboardView(model: model)
        case .review: ReviewView(model: model)
        case .settings: SettingsView(model: model, config: config)
        }
    }
    #endif

    private func bootstrap() async {
        model.bind(config: config)
        async let version: Void = model.loadServerVersion()
        await model.ensureLoaded(model.view)
        await version
    }

    @ViewBuilder
    private var toastOverlay: some View {
        if let toast = model.toast {
            ToastView(toast: toast).id(toast.id)
        }
    }
}

// MARK: - macOS 列表栏 376 固定 + 详情栏自适应
//
// 🔴 **每个板块都是同一套「列表 + 详情」骨架**(规范 §06):
//   选股 = 清单上的票 / 成绩 = 三块 / 复盘 = 四页 / 设置 = 四组。
// 统一成一个容器,是为了让四个板块的**滚动行为、页边距、分隔线**只有一处实现 ——
// 各写一遍必然漂。
//
// ⚠ 详情栏为空时**必须给一句话说清"选一个来看"**,⛔ 不许留一片白 —— 白屏在这个
// 项目里永远读作"出问题了"。

#if os(macOS)
struct NKSplitLayout<ListContent: View, DetailContent: View>: View {
    @ViewBuilder var list: ListContent
    @ViewBuilder var detail: DetailContent

    /// 规范 §06 定死:列表栏 **376 固定**,详情栏自适应。
    static var listWidth: CGFloat { 376 }

    var body: some View {
        HStack(spacing: 0) {
            ScrollView {
                list
                    // 列表行与标题区采用各自的页边距。
                    .padding(.horizontal, NKSpace.listPadH)
                    .padding(.top, NKSpace.listPadTop)
                    .padding(.bottom, NKSpace.listPadBottom)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: Self.listWidth)
            // 列表栏与详情栏使用不同底色以保持层次。
            .background(NK.listBg)

            Divider().overlay(NK.hairline)

            ScrollView {
                detail
                    // 原型四屏详情逐字相同的 `padding:22px 26px 40px`
                    // (macOS 原型 250 / 645 / 709 / 828 行)。⚠ 横 26 ≠ 纵 22。
                    .padding(.horizontal, NKSpace.pagePadWide)
                    .padding(.top, NKSpace.pagePad)
                    .padding(.bottom, NKSpace.pagePadBottom)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxWidth: .infinity)
            .background(NK.pageBg)
        }
        .frame(maxHeight: .infinity)
    }
}

/// 详情栏的空态。**说清该做什么**,⛔ 不留白。
struct NKDetailPlaceholder: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String = "sidebar.right"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage).font(.system(size: 30)).foregroundStyle(NK.textTertiary)
            Text(title).font(NKFont.headline).foregroundStyle(NK.textSecondary)
            if let s = subtitle {
                Text(s).font(NKFont.body).foregroundStyle(NK.textTertiary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 80)
    }
}
#endif
