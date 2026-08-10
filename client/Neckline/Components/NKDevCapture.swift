//
//  NKDevCapture.swift
//  Neckline — **QA / 视觉核对专用**的窗口尺寸钩子 + 自截图钩子(macOS)
//
//  🔴 **整个文件包在 `#if os(macOS) && DEBUG` 里** —— Release 构建里这些代码根本不存在,
//  用户装的包**一行都编不进去**。⛔ 别把它挪出 DEBUG,也别在正常路径上调用它:
//  两个钩子都要显式环境变量才动,缺环境变量时行为与没有这个文件**逐字节相同**。
//
//  **为什么需要它**(V2.3.1 §〇b 的兜底,2026-08-10 施工中实打出来的):
//  立项时实测通过的 `screencapture -x -o -l<windowid>` 路线**当天就失效了** ——
//  同一台机器、同一个会话,先成功截了四张,之后对**任何** App 的窗口都返回
//  `could not create image from window`(拿 Xcode 的窗口对照测过,不是本 App 的问题)。
//  判据:`CGWindowListCopyWindowInfo` 仍读得到 27/31 个窗口**标题**、
//  `CGPreflightScreenCaptureAccess()` 仍返回 `true`,但 `SCShareableContent` 报
//  **`displays=0`** —— 元数据可读、**像素不可读**,即屏幕录制授权在会话中途被系统收回
//  (macOS 的周期性重新授权提示,agent 点不动)。
//  ⚠ **教训**:「窗口标题读得到 = 有屏幕录制权限」这条判据在 macOS 26 上**不成立**,
//  ⛔ 别再拿它当截图链可用的证据;要判就判 `SCShareableContent.displays` 非空。
//
//  **本文件的路线不依赖那个授权**:App 渲染**自己的**窗口(`NSThemeFrame.cacheDisplay`),
//  这是进程内的绘制,不经过屏幕捕获。顺带还拆了两颗雷:
//  ① 截出来的图**只可能**是本 App 的窗口,⛔ 永远拍不到用户桌面;
//  ② 尺寸由 `NECKLINE_WINDOW_SIZE` 钉死,不再受"窗口帧被系统忽略"的影响
//     (`.hiddenTitleBar` 落地后,UserDefaults 里那条 `NSWindow Frame …` 实测**不再生效**,
//     窗口每次都起在 1080×774 = 最小宽 × 90%,比对基准会漂)。
//

#if os(macOS) && DEBUG

import AppKit
import SwiftUI

/// 挂在 macOS 工具栏上的隐形钩子(0×0)。**只读环境变量**,缺变量时什么都不做。
struct NKDevCaptureHook: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView { Hook() }
    func updateNSView(_ nsView: NSView, context: Context) {}

    final class Hook: NSView {
        private var done = false
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard !done, let w = window else { return }
            done = true
            NKDevCapture.applyWindowSize(to: w)
            NKDevCapture.scheduleCaptureIfRequested(of: w)
        }
    }
}

enum NKDevCapture {
    private static var env: [String: String] { ProcessInfo.processInfo.environment }

    /// `NECKLINE_WINDOW_SIZE=1200x860` → 把窗口钉成原型画布尺寸(⛔ 缺变量不动窗口)。
    static func applyWindowSize(to window: NSWindow) {
        guard let raw = env["NECKLINE_WINDOW_SIZE"] else { return }
        let parts = raw.lowercased().split(separator: "x")
        guard parts.count == 2, let w = Double(parts[0]), let h = Double(parts[1]) else { return }
        // 保持左上角不动地改尺寸(AppKit 原点在左下,所以要把 y 往下补回去)。
        let old = window.frame
        let newFrame = NSRect(x: old.origin.x, y: old.origin.y + old.height - h, width: w, height: h)
        window.setFrame(newFrame, display: true)
    }

    /// `NECKLINE_CAPTURE_PNG=<路径>` → 延迟 `NECKLINE_CAPTURE_DELAY` 秒(默认 6)后把
    /// **整个窗口**(含系统红绿灯:它们是 `NSThemeFrame` 的后代)渲染成 PNG。
    /// `NECKLINE_CAPTURE_QUIT=1` 则截完退出,便于脚本串联。
    static func scheduleCaptureIfRequested(of window: NSWindow) {
        guard let path = env["NECKLINE_CAPTURE_PNG"], !path.isEmpty else { return }
        let delay = Double(env["NECKLINE_CAPTURE_DELAY"] ?? "") ?? 6
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
            let ok = capture(window: window, to: path)
            FileHandle.standardError.write(
                "[capture] \(ok ? "ok" : "FAILED") -> \(path)\n".data(using: .utf8)!)
            if env["NECKLINE_CAPTURE_QUIT"] == "1" {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) { NSApp.terminate(nil) }
            }
        }
    }

    /// 渲染窗口自身。⚠ 取的是 `contentView.superview`(= `NSThemeFrame`,窗口的根视图),
    /// **不是** `contentView` —— 红绿灯挂在 `NSTitlebarContainerView` 上,它是 themeFrame 的
    /// 子视图、与 contentView 平级;只截 contentView 就把要验的那三颗按钮漏了。
    @discardableResult
    static func capture(window: NSWindow, to path: String) -> Bool {
        guard let root = window.contentView?.superview else { return false }
        let bounds = root.bounds
        guard let rep = root.bitmapImageRepForCachingDisplay(in: bounds) else { return false }
        root.cacheDisplay(in: bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return false }
        do {
            try png.write(to: URL(fileURLWithPath: path))
            return true
        } catch {
            return false
        }
    }
}

#endif
