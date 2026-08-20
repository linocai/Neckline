//
//  RootView.swift
//  Neckline — 导航壳(平台分叉:iOS 底部 TabView / **macOS 50px 统一工具栏**)。
//
//  🔴 **信息架构 = 三板块 选股 / 成绩 / 复盘 + 设置沉底**(**裁定 11**,⛔ 施工时不得重开)。
//  设置**在产品语义上不算板块** —— 它只是个入口(iOS 排 TabBar 最后一项、
//  macOS 沉成工具栏右端的齿轮),所以既不进「交易」组也不进「复盘」组。
//
//  ⚠ **⛔ 别把 macOS 那条 240px 玻璃侧栏加回来**(V2.3 已删):它与工具栏胶囊是**同一组
//  导航的两种形态**,并存 = 两套导航。板块内部各自是「列表栏 376 + 详情栏自适应」。
//
//  ⚠ **前身**:V2.1 三板块是 选股 / **持仓** / 复盘;V2.5.0 裁定 11 把持仓整块下线、
//  成绩线升为板块。⛔ 不许以任何名字把持仓加回来。
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

    // MARK: - iOS:底部 TabView(**三板块 + 设置沉底**,顺序 = 选股 / 成绩 / 复盘 / 设置)
    //
    // ⚠ 这行注释在 V2.5.0 S12 之后还写着「持仓」半年 —— 裁定 11 那块板已经整块下线,
    // 而下面的代码里根本没有它。**顺序的唯一源是 `AppTab` 的枚举声明顺序**,
    // ⛔ 别再让注释与它漂开(注释在陈述一件不成立的事实时,比没有注释更坏)。

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
        // ⛔ **V2.4.0 P0**:原先挂在这里的**通栏退潮刹车条**已整体删除(P0.1 表
        // 「全 App 顶部退潮红条 = 删」)。⛔ 不许换个名字再挂一条壳层横幅回来。
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 90) }
        // 只加载**当前 Tab**(默认 `.selection`,QA 钩子可覆盖 ——
        // `NecklineApp.init()` 里 `m.view = tab` 早于本 `.task` 执行)。
        .task { model.bind(config: config); await model.ensureLoaded(model.view) }
        // 切 Tab 首次到达时才拉那个 Tab 的数据;已加载过的 Tab 再切回来不重打请求。
        .onChange(of: model.view) { _, tab in Task { await model.ensureLoaded(tab) } }
    }
    #endif

    // MARK: - macOS:50px 统一工具栏 + 内容区(板块内部再分列表栏 / 详情栏)

    #if os(macOS)
    private var macShell: some View {
        VStack(spacing: 0) {
            NKToolbar(model: model)
            // ⛔ V2.4.0 P0:通栏退潮刹车条已删(见 iOS 分支同款注释)。
            content
                // 🔴 **两处 `maxHeight: .infinity` 缺一不可**(规范 §06 踩过的坑的 SwiftUI
                // 等价物):内容区不吃满高度时,内层 `ScrollView` 拿不到可滚区间 ——
                // 表现是**两栏都不滚**,而不是报错。
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 1080, maxWidth: .infinity, minHeight: 640, maxHeight: .infinity)
        // 🔴 **V2.3.1:内容必须顶到窗口最上沿**(§〇c 硬伤 1 的收尾一步)。
        // `.windowStyle(.hiddenTitleBar)` 只是把系统标题栏**画成透明**,SwiftUI 仍然按
        // 32pt 的标题栏**安全区**把内容整体往下推 —— 实测(自截图逐像素量):红绿灯落在
        // 距顶 25(已由 `NKTrafficLightAligner` 对到 50pt 栏的中线),而工具栏自己却从 32
        // 起、中线在 57,**两者差 32pt**,顶上白白空出一条 —— 看起来仍然像两条栏。
        // ⛔ 别去改红绿灯的目标值来"对齐"这 32pt:那是把安全区的坑焊死进按钮坐标,
        // 窗口一变(全屏 / 分屏)就再次错位。要顶的是内容。
        .ignoresSafeArea(.container, edges: .top)
        .background(NK.pageBg)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 24) }
        // 🔴 V2.4.0 P3.6:同 iOS 分支 —— 只加载当前 Tab,切 Tab 首次到达才补拉。
        .task { model.bind(config: config); await model.ensureLoaded(model.view) }
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
                    // 🔴 **V2.3.1 批 2:列表栏是「两套页边距」,不是一套**(§③ 必查钉子 1)。
                    // 这里给的是**行**那一套(原型 86 行 `padding:0 10px`);标题区自己再补
                    // `listHeaderExtraH`(6)凑到原型 82 行的 `18px 16px 10px`。
                    // ⛔ 别为了"统一"把这里改成 16 —— 那会让每一行都往里缩 6。
                    .padding(.horizontal, NKSpace.listPadH)
                    .padding(.top, NKSpace.listPadTop)
                    .padding(.bottom, NKSpace.listPadBottom)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(width: Self.listWidth)
            // 🔴 **V2.3.1:两栏底色分开**(§② 钉子 3)。列表栏 `#FCFCFD`(macOS 原型 81 行)
            // 比详情栏 `#F6F6F8`(247 行)亮一档 —— 两栏同色时「选什么」与「看什么」糊成
            // 一片,而且列表栏的**白色选中行**几乎浮不出来。⛔ 别再调回同一个值。
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
