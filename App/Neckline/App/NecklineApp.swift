//
//  NecklineApp.swift
//  Neckline — 多平台 App 入口(iOS + macOS 单 target)
//
//  Bundle ID top.linotsai.neckline · deploymentTarget iOS 26 / macOS 26。
//  iOS 接 AppDelegate 拿 APNs device token → PushManager 上报(§五 阶段4C 坑吸收⑥:
//  平台分叉 Scene body 内 #if 不能跨 WindowGroup 混写太多分支,故用两套独立 body 分支
//  而非在单个 Scene 里穿插 #if)。
//

import SwiftUI

@main
struct NecklineApp: App {
    @StateObject private var config = AppConfig()
    @State private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    #if os(iOS)
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    #endif

    init() {
        // model 的 clientProvider 在 RootView.task 里注入(依赖 config,坑吸收④:
        // bind(config:) 必须先于 refresh(),放 .task 而非 .onAppear)。
        let m = AppModel()
        // QA 可通过 `SIMCTL_CHILD_NECKLINE_INITIAL_TAB` 指定当前 AppTab；缺省为选股页。
        if let raw = ProcessInfo.processInfo.environment["NECKLINE_INITIAL_TAB"],
           let tab = AppTab(rawValue: raw) {
            m.view = tab
        }
        // ⚠ **数据到位之后才能触发的钩子不能塞进这里**(`NECKLINE_INITIAL_SELECTION_VIEW` /
        // `NECKLINE_INITIAL_STOCK_CODE` / `NECKLINE_INITIAL_REVIEW_PAGE` /
        // `NECKLINE_INITIAL_REVIEW_WEEK`)—— 那些内容是异步拉回来的,`init()` 里够不着。
        // 它们落在 `AppModel.applyQAHooksAfterRefresh()`。
        _model = State(initialValue: m)
    }

    var body: some Scene {
        #if os(macOS)
        WindowGroup {
            RootView(model: model, config: config)
                .environmentObject(config)
                .frame(minWidth: 1080, minHeight: 640)
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        Task { await model.refreshSettlementOnActivation() }
                    }
                }
        }
        // 隐藏系统标题栏，避免它与自建工具栏重叠；拖动与红绿灯对齐由工具栏组件处理。
        .windowStyle(.hiddenTitleBar)
        .windowResizability(.contentMinSize)
        .defaultSize(width: 1240, height: 780)
        #else
        WindowGroup {
            RootView(model: model, config: config)
                .environmentObject(config)
                .onAppear { wire() }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active {
                        appDelegate.clearBadge()
                        Task { await model.refreshSettlementOnActivation() }
                    }
                }
        }
        #endif
    }

    private func wire() {
        #if os(iOS)
        // 纯 QA/截图辅助:`NECKLINE_SKIP_PUSH_PROMPT=1` 时不挂推送(系统授权弹窗会盖住
        // 页面,让 `xcrun simctl io screenshot` 的视觉核对拍不到内容;本环境 computer-use
        // 点不动模拟器,没法手动点「允许」)。⛔ **只影响截图路径**:缺此环境变量时行为
        // 与之前逐字节相同,正常用户永远走 `attach()`。
        if ProcessInfo.processInfo.environment["NECKLINE_SKIP_PUSH_PROMPT"] == "1" { return }
        appDelegate.attach(config: config, model: model)
        #endif
    }
}

#if os(iOS)
import UIKit

/// iOS 远程通知 token 回调桥。
final class AppDelegate: NSObject, UIApplicationDelegate {
    private var pushManager: PushManager?
    private var pendingToken: Data?

    @MainActor
    func attach(config: AppConfig, model: AppModel) {
        if pushManager == nil {
            let pm = PushManager(config: config, model: model)
            pm.bootstrap()
            self.pushManager = pm
            model.pushManager = pm
            Task { await pm.requestAuthorizationAndRegister() }
            if let t = pendingToken { pm.didRegister(deviceToken: t); pendingToken = nil }
        }
    }

    @MainActor
    func clearBadge() { pushManager?.clearBadge() }

    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task { @MainActor in
            if let pm = pushManager { pm.didRegister(deviceToken: deviceToken) }
            else { pendingToken = deviceToken }
        }
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in pushManager?.didFailToRegister(error: error) }
    }
}
#endif
