import Foundation

/// Push payloads carry K10 identifiers only. Unknown payloads remain visible to the system and
/// do not redirect the user to an unrelated page.
struct K10PushRoute: Equatable {
    let tab: AppTab
    let companyWindowID: String?
    let opportunityID: String?
    let batchID: String?
    let scanID: String?

    init?(userInfo: [AnyHashable: Any]) {
        let companyWindowID = userInfo["companyWindowId"] as? String
        let opportunityID = userInfo["opportunityId"] as? String
        let batchID = userInfo["batchId"] as? String
        let scanID = userInfo["scanId"] as? String
        if let target = userInfo["target"] as? String, let tab = AppTab(rawValue: target) {
            self.tab = tab
        } else if companyWindowID != nil {
            self.tab = .focus
        } else if opportunityID != nil || batchID != nil || scanID != nil {
            self.tab = .opportunities
        } else {
            return nil
        }
        self.companyWindowID = companyWindowID; self.opportunityID = opportunityID; self.batchID = batchID; self.scanID = scanID
    }
}

#if os(iOS)
import UIKit
import UserNotifications

@MainActor
final class K10AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private weak var config: AppConfig?
    private weak var model: AppModel?

    func attach(config: AppConfig, model: AppModel) {
        self.config = config; self.model = model
        UNUserNotificationCenter.current().delegate = self
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        guard let config, config.hasToken else { return }
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task {
            do { try await K10AdminClient(baseURL: config.resolvedBaseURL, token: config.apiToken).registerDevice(token: token) }
            catch { self.model?.toast = error.localizedDescription }
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        model?.toast = "K10 推送注册失败：\(error.localizedDescription)"
    }

    func requestAuthorizationAndRegister() async {
        let center = UNUserNotificationCenter.current()
        do {
            let settings = await center.notificationSettings()
            if settings.authorizationStatus == .notDetermined {
                guard try await center.requestAuthorization(options: [.alert, .badge, .sound]) else { return }
            }
            UIApplication.shared.registerForRemoteNotifications()
        } catch { model?.toast = error.localizedDescription }
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions { [.banner, .sound, .badge, .list] }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        guard let route = K10PushRoute(userInfo: response.notification.request.content.userInfo), let model else { return }
        model.tab = route.tab
        await model.refresh()
    }
}
#endif
