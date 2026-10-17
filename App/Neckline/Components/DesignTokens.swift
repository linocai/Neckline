import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

extension Color {
    /// 浅色 / 暗色各一套，随系统外观切换。
    init(light lightHex: UInt, dark darkHex: UInt, alpha: Double = 1) {
        #if os(macOS)
        self.init(nsColor: NSColor(name: nil, dynamicProvider: { appearance in
            let hex = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? darkHex : lightHex
            return NSColor(srgbRed: Self.channel(hex, shift: 16),
                            green: Self.channel(hex, shift: 8),
                            blue: Self.channel(hex, shift: 0),
                            alpha: alpha)
        }))
        #else
        self.init(uiColor: UIColor { traits in
            let hex = traits.userInterfaceStyle == .dark ? darkHex : lightHex
            return UIColor(red: Self.channel(hex, shift: 16),
                            green: Self.channel(hex, shift: 8),
                            blue: Self.channel(hex, shift: 0),
                            alpha: alpha)
        })
        #endif
    }

    private static func channel(_ hex: UInt, shift: UInt) -> CGFloat {
        CGFloat((hex >> shift) & 255) / 255
    }
}

/// 晨报界面。浅色与暗色都完整。
enum NKMorning {
    static let page = Color(light: 0xF5F6F8, dark: 0x1C1C1E)
    static let card = Color(light: 0xFFFFFF, dark: 0x2C2C2E)
    static let textPrimary = Color(light: 0x1C1C1E, dark: 0xF5F5F7)
    static let textSecondary = Color(light: 0x6E6E73, dark: 0x98989D)
    static let textTertiary = Color(light: 0x8E8E93, dark: 0x636366)
    static let hairline = Color(light: 0xE5E5EA, dark: 0x38383A)
    static let accent = Color(light: 0x0754E9, dark: 0x4C8DFF)
    static let problem = Color(light: 0xD70015, dark: 0xFF453A)
    static let selection = Color(light: 0x0754E9, dark: 0x4C8DFF, alpha: 0.12)
    static let tagFill = Color(light: 0xF2F2F7, dark: 0x3A3A3C)
}

enum NKMorningFont {
    static let pageTitle = Font.system(size: 28, weight: .semibold)
    static let caption = Font.system(size: 13)
    static let company = Font.system(size: 16, weight: .semibold)
    static let titleLine = Font.system(size: 15)
    /// 条目详情里的事件标题，比名单标题行大一号。
    static let detailTitle = Font.system(size: 17, weight: .semibold)
    static let tag = Font.system(size: 11, weight: .medium)
    static let reason = Font.system(size: 15)
    static let document = Font.system(size: 16)
}

enum NKMorningSpace {
    static let page: CGFloat = 20
    static let detail: CGFloat = 28
    static let rowV: CGFloat = 10
    static let rowH: CGFloat = 16
    static let expand: TimeInterval = 0.2
}

enum NKMorningRadius {
    static let card: CGFloat = 12
    static let tag: CGFloat = 4
}
