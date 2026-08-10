//
//  RootView.swift
//  Neckline — 导航壳(平台分叉:iOS 底部 TabView / **macOS 50px 统一工具栏**)
//
//  **信息架构 = V2.1 三板块(2026-08-07 用户裁定 #2,⛔ 施工时不得重开)**:
//    **选股 / 持仓 / 复盘** 三个板块,**设置沉底为入口**(iOS 排 TabBar 最后一项、
//    macOS 沉成工具栏右端的齿轮)—— 🔴 **设置在产品语义上不算板块**,
//    它只是个入口,所以既不进「交易」组也不进「复盘」组。
//
//  ⚠ **V2.3 视觉升级:macOS 240px 玻璃侧栏整个删掉**(规范 §01 决定 01)。三个板块
//  改成工具栏胶囊,窗口宽度全部还给内容;板块内部各自是「列表栏 376 + 详情栏自适应」。
//  ⛔ **别把侧栏加回来** —— 它与工具栏胶囊是同一组导航的两种形态,并存 = 两套导航。
//
//  ⚠ **前身 = D8 四板块**(今日篮子 / 持仓 / 问询台 / 设置 + macOS 独有的周复盘工作台):
//    问询台整链退役(V2.1-①)、「今日篮子」改名「选股」、周复盘工作台**升为复盘板块**
//    并进 iOS(V2.1-⑦)—— 上传交割单仍是桌面场景,iOS 侧只读展示。
//  V1 的「盘中看板」不再是 tab,内容并入持仓板块(见 `BoardSection`)。
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
    }

    // MARK: - iOS:底部 TabView(**三板块 + 设置沉底**,顺序 = 选股 / 持仓 / 复盘 / 设置)

    #if os(iOS)
    private var iosShell: some View {
        TabView(selection: Binding(get: { model.view }, set: { model.view = $0 })) {
            BasketDailyView(model: model)
                .tabItem { Label(AppTab.baskets.title, systemImage: AppTab.baskets.systemImage) }
                .tag(AppTab.baskets)
            PositionsView(model: model)
                .tabItem { Label(AppTab.positions.title, systemImage: AppTab.positions.systemImage) }
                .tag(AppTab.positions)
            ReviewView(model: model)
                .tabItem { Label(AppTab.review.title, systemImage: AppTab.review.systemImage) }
                .tag(AppTab.review)
            // ⚠ **设置排最后**:它是入口不是板块,⛔ 别把它挪到板块中间去。
            SettingsView(model: model, config: config)
                .tabItem { Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage) }
                .tag(AppTab.settings)
        }
        .tint(NK.accent)
        // 🔴 刹车条挂在**壳**上、盖在 TabView 顶部:它管的是今天整份计划,不属于任何
        // 一个板块 —— ⛔ 别退回到各板块自己画一条(那样切板块它就消失了)。
        .safeAreaInset(edge: .top, spacing: 0) { brakeBar }
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 90) }
        .task { model.bind(config: config); await model.refresh() }
    }
    #endif

    // MARK: - macOS:50px 统一工具栏 + 内容区(板块内部再分列表栏 / 详情栏)

    #if os(macOS)
    private var macShell: some View {
        VStack(spacing: 0) {
            NKToolbar(model: model)
            brakeBar
            content
                // 🔴 **两处 `maxHeight: .infinity` 缺一不可**(规范 §06 踩过的坑的 SwiftUI
                // 等价物):内容区不吃满高度时,内层 `ScrollView` 拿不到可滚区间 ——
                // 表现是**两栏都不滚**,而不是报错。
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 1080, maxWidth: .infinity, minHeight: 640, maxHeight: .infinity)
        .background(NK.pageBg)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 24) }
        .task { model.bind(config: config); await model.refresh() }
    }

    @ViewBuilder
    private var content: some View {
        switch model.view {
        case .baskets: BasketDailyView(model: model)
        case .positions: PositionsView(model: model)
        case .review: ReviewView(model: model)
        case .settings: SettingsView(model: model, config: config)
        }
    }
    #endif

    /// 通栏刹车条(双端共用)。⛔ 不进卡片流、不属于任何板块。
    @ViewBuilder
    private var brakeBar: some View {
        if let warning = model.retreatWarning {
            RetreatBrakeBar(reason: warning)
        }
    }

    @ViewBuilder
    private var toastOverlay: some View {
        if let toast = model.toast {
            ToastView(toast: toast).id(toast.id)
        }
    }
}

// MARK: - V2.3 macOS 三区骨架:列表栏 376 固定 + 详情栏自适应
//
// 🔴 **每个板块都是同一套「列表 + 详情」骨架**(规范 §06):
//   选股 = 篮子 / 持仓 = 仓位 / 复盘 = 五页 / 设置 = 四组。
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
                    .padding(.horizontal, NKSpace.listPadH)
                    .padding(.vertical, NKSpace.listPadV)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: Self.listWidth)
            .background(NK.pageBg)

            Divider().overlay(NK.hairline)

            ScrollView {
                detail
                    .padding(.horizontal, NKSpace.pagePad)
                    .padding(.vertical, NKSpace.pagePad)
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
