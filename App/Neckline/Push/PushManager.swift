//
//  PushManager.swift
//  Neckline — 锁屏推送。**收发是 iOS 专属**(macOS 无锁屏推送,平台分叉);
//  **落点表 `nkPushRoute(forKind:)` 双端都编译**(纯数据;理由见本文件头最后一段)。
//
//  现行两个 APNs category：`NKIMPORTANT`(竞价核对表)与 `NKDIGEST`(盘后报告)。
//  `neckline/notify_kinds.py` 的 `CATEGORY_*` **逐字一致**(改串 = 改契约)。
//
//  ⚠ **业务分支一律读 payload 里的 `kind`,⛔ 不按 category 分支** ——
//  **category 只决定「怎么响」(系统层呈现分组),`kind` 决定「响不响」与「点开去哪」。**
//  同一 category 可以包含不同业务通知，因此不能拿它代替业务分支。
//
//  ⚠ **未知 `kind` 必须优雅降级**:服务端日后加 kind 时,旧 App 收到不认识的 `kind`
//  应当**照常显示标题正文**(系统本就会显示;这里只是不做路由、不静默吞),
//  ⛔ **不许静默丢弃**。
//
//  纯数据路由映射不依赖 UIKit，放在条件编译外以保证双端构建共同覆盖。
//

import Foundation

/// 一条推送点开之后**落在哪** —— 板块 + (选股板块内的)视图。
///
/// 🔴 **两级,不是一级**:选股板块里有两个视图(今日清单 / 次日核对表),
/// 而 9:26 那条竞价核对表推送的落点**就是核对表那一视图** —— 只把 tab 拨到「选股」
/// 会让用户落在昨晚的清单上,和落错板块一样答非所问。
struct NKPushRoute: Equatable {
    let tab: AppTab
    /// 仅在 `tab == .selection` 时有意义。`nil` = 不动用户当前所在的视图。
    let selectionMode: SelectionViewMode?
}

/// 纯路由函数（零 UIKit 依赖，双端都编译）。
///
/// **按 `kind` 分发,⛔ 不按 category** —— 三级 category 只决定系统层怎么响。
/// kind 串的唯一源是服务端 `neckline/notify_kinds.py`;这里**只做「去哪个视图」的
/// 展示层映射**,⛔ 不硬编一份"有哪些 kind"的清单去过滤(未登记的 kind 走
/// `default` → `nil` = 不跳转,通知照常展示)。
///
/// 🔴 **只登记服务端当前会发出的两类通知**：晚间报告与次日核对表。
/// 其余 kind 一律走 `default` → 不跳转，避免把未知通知错误带到某个页面。
/// ⚠ 服务端接线与这张表的对拍守门在
/// `Backend/tests/test_contract_crosscheck.py`(第七组)。
func nkPushRoute(forKind kind: String) -> NKPushRoute? {
    switch kind {
    // 19:00 晚间链完成后报告就绪 → 选股 · **今日清单**(那条推送讲的就是这一份清单)。
    case "report_ready":
        return NKPushRoute(tab: .selection, selectionMode: .listing)
    // 9:26—9:29 竞价核对表汇总 → 选股 · **次日核对表**。
    // ⚠ kind 串由服务端 `KIND_PRECALL` 定义，语义是次日核对表。
    case "precall":
        return NKPushRoute(tab: .selection, selectionMode: .checklist)
    // 未知 kind 不跳转，但通知照常显示（优雅降级，⛔ 不静默丢弃）。
    default:
        return nil
    }
}

#if os(iOS)
import UIKit
import UserNotifications

/// APNs category 标识(**必须与服务端 `notify_kinds.py` 字面一致**)。
/// 与服务端现行两个通知级别一致。
enum NKNotificationCategory {
    static let important = "NKIMPORTANT"
    static let digest = "NKDIGEST"

    static let all = [important, digest]
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

    /// 启动时挂载:设 delegate + 注册两个 category。
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

    /// 点开通知 → 按 **payload 的 `kind`** 路由到对应板块 + 视图。
    /// 未知 kind → **不路由**(停在当前页),但通知本身照常显示过 —— ⛔ 不吞、不崩。
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        let info = response.notification.request.content.userInfo
        let kind = (info[kNKKindKey] as? String) ?? ""
        if let route = nkPushRoute(forKind: kind) {
            model?.view = route.tab
            // ⚠ 先拨视图再拉数:拉的那几秒里用户就该看着正确的那一页,
            // ⛔ 别让他先盯着昨晚的清单几秒再跳。
            if let mode = route.selectionMode { model?.selectionMode = mode }
            await model?.refresh(for: route.tab)
        }
        clearBadge()
    }
}
#endif
