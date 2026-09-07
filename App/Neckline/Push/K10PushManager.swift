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
        // A concrete V1.4 object is stronger than a generic tab hint. This avoids an
        // analysis notification with both fields opening a non-specific opportunity page.
        if companyWindowID != nil {
            self.tab = .focus
        } else if opportunityID != nil || batchID != nil || scanID != nil {
            self.tab = .opportunities
        } else if let target = userInfo["target"] as? String, let tab = AppTab(rawValue: target) {
            self.tab = tab
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
    private var pendingRoute: K10PushRoute?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        // Install the delegate before SwiftUI attaches the model so a cold-start response is
        // retained and resolved only after its K10 connection is available.
        UNUserNotificationCenter.current().delegate = self
        if let payload = launchOptions?[.remoteNotification] as? [AnyHashable: Any],
           let route = K10PushRoute(userInfo: payload) {
            pendingRoute = route
        }
        return true
    }

    func attach(config: AppConfig, model: AppModel) {
        self.config = config; self.model = model
        UNUserNotificationCenter.current().delegate = self
        if let route = pendingRoute {
            pendingRoute = nil
            Task { [weak model] in await model?.openNotification(route) }
        }
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
        guard let route = K10PushRoute(userInfo: response.notification.request.content.userInfo) else { return }
        guard let model else {
            pendingRoute = route
            return
        }
        await model.openNotification(route)
    }
}
#endif
