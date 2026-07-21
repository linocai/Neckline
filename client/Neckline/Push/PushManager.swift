//
//  PushManager.swift
//  Neckline — 锁屏推送(iOS 专属;macOS 无锁屏推送,平台分叉,搬自 LinoN §五 阶段4C)
//
//  §2.4 拍板:APNs 只推两类且均为「信息类」通知(无需盘中动作按钮,对比 LinoN
//  的持仓硬线推送做了简化)——① 16:00 报告就绪 → 点开跳「今日计划」;
//  ② 退潮红色刹车 → 点开跳「盘中看板」。category 标识须与后端 `push/apns.py`
//  的 `CATEGORY_REPORT="REPORT"` / `CATEGORY_RETREAT="RETREAT"` 字面一致。
//
//  ⚠️ 真实推送投递留阶段 4E(需 ECS 部署 + 真机 + 真签名)。本期实现注册 + 通知
//     处理 + UI 路由,本地可走授权→token→上报闭环(真机才有真 token)。
//

import Foundation
#if os(iOS)
import UIKit
import UserNotifications

/// 推送 category 标识(必须与后端 `neckline/push/apns.py` 字面一致)。v1.1 推送白名单
/// 扩到四类:precall(9:26 盘前校准汇总)/ d5exit(D5 时间退出)。
enum NKNotificationCategory {
    static let report = "REPORT"
    static let retreat = "RETREAT"
    static let precall = "PRECALL"
    static let d5exit = "D5EXIT"
}

@MainActor
final class PushManager: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    private let config: AppConfig
    private weak var model: AppModel?

    @Published var authorizationStatus: UNAuthorizationStatus = .notDetermined
    @Published var lastDeviceToken: String? = nil
    @Published var registerError: String? = nil

    init(config: AppConfig, model: AppModel) {
        self.config = config
        self.model = model
        super.init()
    }

    /// 启动时挂载:设 delegate + 注册 category。
    func bootstrap() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        registerCategories()
        center.getNotificationSettings { settings in
            Task { @MainActor in self.authorizationStatus = settings.authorizationStatus }
        }
        clearBadge()
    }

    func clearBadge() {
        UNUserNotificationCenter.current().setBadgeCount(0)
    }

    /// 四类信息通知均无动作按钮(点通知本体即打开 App 到对应板块,§2.4 简化;v1.1
    /// 推送白名单扩到四类,新两类 precall/d5exit 同款「纯信息、无按钮」)。
    private func registerCategories() {
        let report = UNNotificationCategory(identifier: NKNotificationCategory.report,
                                            actions: [], intentIdentifiers: [], options: [])
        let retreat = UNNotificationCategory(identifier: NKNotificationCategory.retreat,
                                             actions: [], intentIdentifiers: [], options: [])
        let precall = UNNotificationCategory(identifier: NKNotificationCategory.precall,
                                             actions: [], intentIdentifiers: [], options: [])
        let d5exit = UNNotificationCategory(identifier: NKNotificationCategory.d5exit,
                                            actions: [], intentIdentifiers: [], options: [])
        UNUserNotificationCenter.current().setNotificationCategories([report, retreat, precall, d5exit])
    }

    /// 请求通知权限 → 注册远程通知(拿 device token)。已决定则不再弹系统对话框。
    func requestAuthorizationAndRegister() async {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        authorizationStatus = settings.authorizationStatus
        switch settings.authorizationStatus {
        case .notDetermined:
            do {
                let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
                authorizationStatus = granted ? .authorized : .denied
                if granted { UIApplication.shared.registerForRemoteNotifications() }
            } catch {
                registerError = error.localizedDescription
            }
        case .authorized, .provisional, .ephemeral:
            UIApplication.shared.registerForRemoteNotifications()
        default:
            break   // denied:不重复弹窗
        }
    }

    /// AppDelegate 回调:拿到 device token → 上报后端 POST /devices。
    func didRegister(deviceToken: Data) {
        let tokenHex = deviceToken.map { String(format: "%02x", $0) }.joined()
        lastDeviceToken = tokenHex
        #if DEBUG
        print("[Neckline] APNs device token (sandbox): \(tokenHex)")
        #endif
        Task {
            let client = APIClient(baseURL: config.resolvedBaseURL, token: config.apiToken)
            do {
                try await client.registerDevice(token: tokenHex)
                registerError = nil
            } catch {
                registerError = (error as? APIError)?.errorDescription ?? error.localizedDescription
            }
        }
    }

    func didFailToRegister(error: Error) {
        registerError = error.localizedDescription
    }

    // MARK: - UNUserNotificationCenterDelegate

    /// 前台收到推送:仍展示横幅(两类都值得立即看见)。
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        return [.banner, .sound, .badge, .list]
    }

    /// 点开通知 → 路由到对应板块(报告→今日计划;退潮→盘中看板)。
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        let category = response.notification.request.content.categoryIdentifier
        if let tab = Self.targetTab(forCategory: category) {
            model?.view = tab
            switch tab {
            case .today: await model?.refresh()
            case .board: await model?.loadBoard()
            default: break
            }
        }
        clearBadge()
    }

    /// 纯路由函数(单测覆盖,不依赖 UNUserNotificationCenter 真实回调链路)。v1.1-G.2:
    /// PRECALL→盘中看板(校准明细在看板)、D5EXIT→今日计划(持仓区置顶可见 D5 卡片)。
    static func targetTab(forCategory category: String) -> AppTab? {
        switch category {
        case NKNotificationCategory.report: return .today
        case NKNotificationCategory.retreat: return .board
        case NKNotificationCategory.precall: return .board
        case NKNotificationCategory.d5exit: return .today
        default: return nil
        }
    }
}
#endif
