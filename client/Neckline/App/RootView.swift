//
//  RootView.swift
//  Neckline — 导航壳(平台分叉:iOS 底部 TabView / macOS 240px 玻璃侧栏)
//
//  **信息架构 = V2.1 三板块(2026-08-07 用户裁定 #2,⛔ 施工时不得重开)**:
//    **选股 / 持仓 / 复盘** 三个板块,**设置沉底为入口**(iOS 排 TabBar 最后一项、
//    macOS 沉到侧栏最底部并与分组间加分隔线)—— 🔴 **设置在产品语义上不算板块**,
//    它只是个入口,所以既不进「交易」组也不进「复盘」组。
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
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 90) }
        .task { model.bind(config: config); await model.refresh() }
    }
    #endif

    // MARK: - macOS:240px 玻璃侧栏(交易组 / 复盘组 + 沉底的设置入口)

    #if os(macOS)
    private var macShell: some View {
        HStack(spacing: 0) {
            sidebar.frame(width: 240)
            Divider().overlay(NK.hairline)
            content.frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(minWidth: 1080, maxWidth: .infinity, minHeight: 640, maxHeight: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 24) }
        .task { model.bind(config: config); await model.refresh() }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 9) {
                NKLogo(size: 27)
                Text("Neckline").font(.system(size: 14.5, weight: .semibold)).foregroundStyle(NK.textPrimary)
                Spacer()
            }
            .padding(.horizontal, 16).padding(.top, 18).padding(.bottom, 16)

            // 「交易」组 = 今天要做什么(选股 + 持仓);⚠ **设置已从本组移出**(V2.1-⑦)。
            Text("交易").font(.system(size: 10.5, weight: .semibold)).tracking(0.6)
                .foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 16).padding(.bottom, 7)

            navItem(.baskets)
            navItem(.positions, badge: model.board.retreatBrake.active ? "!" : nil, badgeColor: NK.down)

            Divider().overlay(NK.hairline).padding(.vertical, 10).padding(.horizontal, 16)
            // 「复盘」组 = 事后看得怎么样(每日 / 累计 / 对账三页在板块内切)。
            Text("复盘").font(.system(size: 10.5, weight: .semibold)).tracking(0.6)
                .foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 16).padding(.bottom, 7)
            navItem(.review)

            Spacer()

            // 🔴 **设置沉底**:与上面两组之间加分隔线,视觉上就不是"第三个板块"。
            Divider().overlay(NK.hairline).padding(.vertical, 10).padding(.horizontal, 16)
            navItem(.settings)
                .padding(.bottom, 12)
        }
        .background(.ultraThinMaterial)
    }

    private func navItem(_ v: AppTab, badge: String? = nil, badgeColor: Color = NK.textSecondary) -> some View {
        let active = model.view == v
        return Button(action: { model.view = v }) {
            HStack(spacing: 9) {
                Image(systemName: v.systemImage).font(.system(size: 14, weight: .medium))
                    .foregroundStyle(active ? NK.accent : NK.textSecondary)
                    .frame(width: 18)
                Text(v.title).font(.system(size: 13, weight: active ? .semibold : .regular))
                    .foregroundStyle(active ? NK.textPrimary : NK.textSecondary)
                Spacer()
                if let b = badge {
                    Text(b).font(.system(size: 10, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Capsule().fill(badgeColor))
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 9).fill(active ? NK.accent.opacity(0.10) : .clear))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 10)
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

    @ViewBuilder
    private var toastOverlay: some View {
        if let toast = model.toast {
            ToastView(toast: toast).id(toast.id)
        }
    }
}
