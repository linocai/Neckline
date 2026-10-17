// iOS 推送登记。macOS 不编译。结果只写进设置里的通知状态，不弹窗。

#if os(iOS)
import UIKit
import UserNotifications

@MainActor
final class MorningPush: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private weak var model: MorningModel?
    private var pendingTap = false

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // 点按通知冷启动时，系统会在 delegate 装上之后回调 didReceive。
        // 启动参数里的远程通知也先记下，attach 时同样走 handleNotificationTap。
        UNUserNotificationCenter.current().delegate = self
        if launchOptions?[.remoteNotification] != nil {
            pendingTap = true
        }
        return true
    }

    func attach(_ model: MorningModel) async {
        self.model = model
        UNUserNotificationCenter.current().delegate = self
        if pendingTap {
            pendingTap = false
            await model.handleNotificationTap()
        }
        await requestAuthorizationAndRegister()
    }

    static func readAuthorization(into model: MorningModel) async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        model.noteNotificationPermission(Self.permission(settings.authorizationStatus))
    }

    func requestAuthorizationAndRegister() async {
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
        guard let model else {
            pendingTap = true
            return
        }
        await model.handleNotificationTap()
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
