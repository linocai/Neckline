//
//  PushManager.swift
//  Neckline — 锁屏推送(iOS 专属;macOS 无锁屏推送,平台分叉)
//
//  **V2-⑪ 通知三级(D5 已拍板)**:三个 APNs category —— `NKIMMEDIATE`(立即)/
//  `NKIMPORTANT`(重要不紧急)/ `NKDIGEST`(盘后汇总)。category 字面量必须与服务端
//  `neckline/notify_kinds.py` 的 `CATEGORY_*` **逐字一致**(改串 = 改契约)。
//
//  ⚠ **业务分支一律读 payload 里的 `kind`,⛔ 不按 category 分支** ——
//  **category 只决定「怎么响」(系统层呈现分组),`kind` 决定「响不响」与「点开去哪」。**
//  按 category 分支会**连坐**:同一组里躺着好几件完全不同的事(正是 V1 拆
//  `HOLDINGALERT` 被逼出来的教训)。
//
//  ⚠ **未知 `kind` 必须优雅降级**:服务端日后加 kind 时,旧 App 收到不认识的 `kind`
//  应当**照常显示标题正文**(系统本就会显示;这里只是不做路由、不静默吞),
//  ⛔ **不许静默丢弃**(v1.5「Swift 未知 `status` 静默消失」的同类坑)。
//

import Foundation
#if os(iOS)
import UIKit
import UserNotifications

/// APNs category 标识(**必须与服务端 `notify_kinds.py` 字面一致**)。
/// V2-⑪ 起由 V1 的六个具名 category 收敛为**三级**。
enum NKNotificationCategory {
    static let immediate = "NKIMMEDIATE"
    static let important = "NKIMPORTANT"
    static let digest = "NKDIGEST"

    static let all = [immediate, important, digest]
}

/// payload 里携带业务 `kind` 的键名(服务端 `api/notify.py` 扇出时写入)。
private let kNKKindKey = "kind"

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

    /// 启动时挂载:设 delegate + 注册三个 category。
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

    /// 三级均为**纯信息通知、无动作按钮**(点通知本体即打开 App 到对应板块)。
    /// ⛔ 通知里不提供任何"下单 / 卖出"类动作 —— 系统永不代下单(§3.8)。
    private func registerCategories() {
        let categories = NKNotificationCategory.all.map {
            UNNotificationCategory(identifier: $0, actions: [], intentIdentifiers: [], options: [])
        }
        UNUserNotificationCenter.current().setNotificationCategories(Set(categories))
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

    /// 前台收到推送:**照常展示横幅**(含未知 kind —— ⛔ 不静默丢弃)。
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async
        -> UNNotificationPresentationOptions {
        return [.banner, .sound, .badge, .list]
    }

    /// 点开通知 → 按 **payload 的 `kind`** 路由到对应板块。
    /// 未知 kind → **不路由**(停在当前页),但通知本身照常显示过 —— ⛔ 不吞、不崩。
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        let kind = (info[kNKKindKey] as? String) ?? ""
        if let tab = Self.targetTab(forKind: kind) {
            model?.view = tab
            switch tab {
            case .baskets: await model?.refresh()
            case .positions: await model?.loadBoard()
            default: break
            }
        }
        clearBadge()
    }

    /// 纯路由函数(单测覆盖,不依赖 UNUserNotificationCenter 真实回调链路)。
    ///
    /// **按 `kind` 分发,⛔ 不按 category** —— 三级 category 只决定系统层怎么响。
    /// kind 串的唯一源是服务端 `neckline/notify_kinds.py`;这里**只做「去哪个板块」的
    /// 展示层映射**,⛔ 不硬编一份"有哪些 kind"的清单去过滤(未登记的 kind 走
    /// `default` → `nil` = 不跳转,通知照常展示)。
    static func targetTab(forKind kind: String) -> AppTab? {
        switch kind {
        // 选股线:报告就绪 / 退潮红色刹车(今日计划整体作废)→ 今日篮子
        case "report_ready", "retreat": return .baskets
        // 持仓线(80% 注意力都在这):熔断 / 时间退出 / K4 派发 / 止损逼近 / 触达离场参考 /
        // 板块跳水 / ⑪-A 四监测 / NL 临时提醒 —— 全部指向持仓板块。
        case "circuit", "d5exit", "holding_alert", "precall",
             "stop_approach", "take_profit", "sector_dive",
             "basket_peers_weak", "sector_bid_fade", "holding_decoupled", "market_shock",
             "custom_alert":
            return .positions
        // 未知 kind:**不跳转**,但通知已照常显示(优雅降级,⛔ 不静默丢弃)。
        default: return nil
        }
    }
}
#endif
