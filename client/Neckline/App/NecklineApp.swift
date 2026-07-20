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

    #if os(iOS)
    @Environment(\.scenePhase) private var scenePhase
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    #endif

    init() {
        // model 的 clientProvider 在 RootView.task 里注入(依赖 config,坑吸收④:
        // bind(config:) 必须先于 refresh(),放 .task 而非 .onAppear)。
        _model = State(initialValue: AppModel())
    }

    var body: some Scene {
        #if os(macOS)
        WindowGroup {
            RootView(model: model, config: config)
                .environmentObject(config)
                .frame(minWidth: 1080, minHeight: 640)
        }
        .windowResizability(.contentMinSize)
        .defaultSize(width: 1240, height: 780)
        #else
        WindowGroup {
            RootView(model: model, config: config)
                .environmentObject(config)
                .onAppear { wire() }
                .onChange(of: scenePhase) { _, phase in
                    if phase == .active { appDelegate.clearBadge() }
                }
        }
        #endif
    }

    private func wire() {
        #if os(iOS)
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
