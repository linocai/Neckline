// iOS 推送登记与通知路由。macOS 不编译。结果只写进设置里的通知状态，不弹窗。

#if os(iOS)
import UIKit
import UserNotifications

enum NecklinePushTarget: Equatable {
    case morning
    case kx

    static func parse(userInfo: [AnyHashable: Any]?) -> NecklinePushTarget {
        if let raw = userInfo?["target"] as? String, raw.lowercased() == "kx" {
            return .kx
        }
        return .morning
    }
}

@MainActor
final class MorningPush: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private weak var model: MorningModel?
    private weak var kx: KXBoardModel?
    private var pendingTarget: NecklinePushTarget?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // 点按通知冷启动时，系统会在 delegate 装上之后回调 didReceive。
        // 启动参数里的远程通知也先记下，attach 时按 target 路由到 K10 或 KX。
        UNUserNotificationCenter.current().delegate = self
        if let payload = launchOptions?[.remoteNotification] as? [AnyHashable: Any] {
            pendingTarget = NecklinePushTarget.parse(userInfo: payload)
        }
        return true
    }

    func attach(_ model: MorningModel, kx: KXBoardModel) async {
        self.model = model
        self.kx = kx
        UNUserNotificationCenter.current().delegate = self
        if let target = pendingTarget {
            pendingTarget = nil
            await route(target)
        }
        await requestAuthorizationAndRegister()
    }

    static func readAuthorization(into model: MorningModel) async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        model.noteNotificationPermission(Self.permission(settings.authorizationStatus))
    }

    func requestAuthorizationAndRegister() async {
        #if DEBUG
        // 截图验收不弹系统通知框；权限状态仍按当前结果读，未询问就保持未询问。
        if ProcessInfo.processInfo.environment["NK_QA_API_URL"] != nil {
            if let model { await Self.readAuthorization(into: model) }
            return
        }
        #endif
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined:
            do {
                let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
                model?.noteNotificationPermission(granted ? .allowed : .denied)
                if granted { UIApplication.shared.registerForRemoteNotifications() }
            } catch {
                model?.noteNotificationPermission(.notAsked)
            }
        case .authorized, .provisional, .ephemeral:
            model?.noteNotificationPermission(.allowed)
            UIApplication.shared.registerForRemoteNotifications()
        case .denied:
            model?.noteNotificationPermission(.denied)
        @unknown default:
            model?.noteNotificationPermission(.notAsked)
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        guard let model else { return }
        Task { await model.registerDevice(hexToken: hex) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        model?.noteNotificationRegistration(.failed)
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge, .list]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard response.actionIdentifier == UNNotificationDefaultActionIdentifier else { return }
        let target = NecklinePushTarget.parse(userInfo: response.notification.request.content.userInfo)
        guard model != nil else {
            pendingTarget = target
            return
        }
        await route(target)
    }

    private func route(_ target: NecklinePushTarget) async {
        guard let model else { return }
        switch target {
        case .morning:
            await model.handleNotificationTap()
        case .kx:
            model.tab = .kx
            if let kx {
                await kx.handlePushTap()
            }
        }
    }

    private static func permission(_ status: UNAuthorizationStatus) -> MorningNoticePermission {
        switch status {
        case .notDetermined: .notAsked
        case .denied: .denied
        case .authorized, .provisional, .ephemeral: .allowed
        @unknown default: .notAsked
        }
    }
}
#endif
