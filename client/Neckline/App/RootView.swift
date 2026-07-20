//
//  RootView.swift
//  Neckline — 导航壳(平台分叉:iOS 底部 TabView / macOS 240px 玻璃侧栏 + 周复盘工作台)
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

    // MARK: - iOS:底部 TabView(四板块,不含周复盘工作台——阶段4D 明文桌面场景)

    #if os(iOS)
    private var iosShell: some View {
        TabView(selection: Binding(get: { model.view }, set: { model.view = $0 })) {
            TodayPlanView(model: model)
                .tabItem { Label(AppTab.today.title, systemImage: AppTab.today.systemImage) }
                .tag(AppTab.today)
            BoardView(model: model)
                .tabItem { Label(AppTab.board.title, systemImage: AppTab.board.systemImage) }
                .tag(AppTab.board)
            InquiryView(model: model)
                .tabItem { Label(AppTab.inquiry.title, systemImage: AppTab.inquiry.systemImage) }
                .tag(AppTab.inquiry)
            SettingsView(model: model, config: config)
                .tabItem { Label(AppTab.settings.title, systemImage: AppTab.settings.systemImage) }
                .tag(AppTab.settings)
        }
        .tint(NK.accent)
        .overlay(alignment: .bottom) { toastOverlay.padding(.bottom, 90) }
        .task { model.bind(config: config); await model.refresh() }
    }
    #endif

    // MARK: - macOS:240px 玻璃侧栏 + 周复盘工作台(五板块)

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

            Text("交易").font(.system(size: 10.5, weight: .semibold)).tracking(0.6)
                .foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 16).padding(.bottom, 7)

            navItem(.today)
            navItem(.board, badge: model.board.retreatBrake.active ? "!" : nil, badgeColor: NK.down)
            navItem(.inquiry)
            navItem(.settings)

            Divider().overlay(NK.hairline).padding(.vertical, 10).padding(.horizontal, 16)
            Text("复盘").font(.system(size: 10.5, weight: .semibold)).tracking(0.6)
                .foregroundStyle(NK.textTertiary)
                .padding(.horizontal, 16).padding(.bottom, 7)
            navItem(.review)

            Spacer()
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
        case .today: TodayPlanView(model: model)
        case .board: BoardView(model: model)
        case .inquiry: InquiryView(model: model)
        case .settings: SettingsView(model: model, config: config)
        case .review: ReviewWorkbenchView()
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
