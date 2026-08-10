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
        let m = AppModel()
        // 纯 QA/截图辅助:`simctl launch` 可用 `SIMCTL_CHILD_NECKLINE_INITIAL_TAB=<tab>`
        // 免交互地把 App 启动到指定板块(数值取 AppTab.rawValue —— V2.1-⑦ 起合法值 =
        // **baskets | positions | review | settings**,`inquiry` 已随问询台整链退役删除),
        // 用于视觉核对;不影响正常用户启动路径(缺此环境变量则按默认 .baskets 打开)。
        if let raw = ProcessInfo.processInfo.environment["NECKLINE_INITIAL_TAB"],
           let tab = AppTab(rawValue: raw) {
            m.view = tab
        }
        // 同款 QA 钩子扩到弹层——`NECKLINE_INITIAL_MODAL=open|note|tradeNote:<positionId>`
        // (用于本环境 computer-use 点击权限受限时的视觉核对,见 CLAUDE.md
        // 「模拟器截图走 xcrun simctl io screenshot」坑吸收)。不影响正常用户路径。
        // ⚠ `circuitReview` 已随熔断整体退役删除(V2.2-⑤-B):传它 → 落 default、
        // 不弹任何层(⛔ 不要为了"兼容老脚本"把那个弹层留着)。
        if let modalRaw = ProcessInfo.processInfo.environment["NECKLINE_INITIAL_MODAL"] {
            switch modalRaw {
            case "open": m.modal = .open
            case "note": m.modal = .note
            case let s where s.hasPrefix("tradeNote:"):
                if let pid = Int(s.dropFirst("tradeNote:".count)) {
                    m.modal = .tradeNote(positionId: pid)
                }
            default: break
            }
        }
        // ⚠ **数据到位之后才能触发的钩子**(`NECKLINE_INITIAL_BASKET_ID` /
        // `NECKLINE_INITIAL_INFOCARD_CODE` / V2.1-⑦ 新增的
        // `NECKLINE_INITIAL_REVIEW_PAGE=daily|selectionClock|tradeClock|cumulative|reconcile` 与
        // `NECKLINE_INITIAL_REVIEW_WEEK=YYYYMMDD`)**不能塞进这里** —— 那些内容是
        // `AppModel.refresh()` 异步拉回来的,`init()` 里够不着。它们落在
        // `AppModel.applyQAHooksAfterRefresh()`(v1.4-⑧ 立下的先例)。
        _model = State(initialValue: m)
    }

    var body: some Scene {
        #if os(macOS)
        WindowGroup {
            RootView(model: model, config: config)
                .environmentObject(config)
                .frame(minWidth: 1080, minHeight: 640)
        }
        // 🔴 **V2.3.1 §〇c 硬伤 1:窗口壳只许有一条栏**。
        // V2.3.0 漏了这一句 → 系统原生标题栏还在,自建的 50px `NKToolbar` 挂在它**下面**,
        // 成了**两条栏**;`NKToolbar` 里那句红绿灯占位因此变成一段**纯空白**,而真正的
        // 红绿灯在上面另一条栏里。原型是**一条**:红绿灯与 Logo / 板块胶囊同排
        // (macOS 原型 23–27 行)。⚠ 隐藏标题栏之后有两件必须一起办,漏一件就是新的坏:
        // ① **窗口拖动** —— 见 `NKToolbar` 的 `WindowDragGesture`(⛔ 不许用
        //    `NSWindow.isMovableByWindowBackground`,那会让整个内容区都能拖窗,
        //    列表点选会变成拖窗口);
        // ② **红绿灯垂直居中** —— 见 `NKTrafficLightAligner`(系统把三颗按钮钉在标准
        //    标题栏 28pt 的垂直中线,不是 50px 工具栏的中线)。
        .windowStyle(.hiddenTitleBar)
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
