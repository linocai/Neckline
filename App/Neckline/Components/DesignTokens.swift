import SwiftUI
#if os(macOS)
import AppKit
#else
import UIKit
#endif

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 255) / 255,
                  green: Double((hex >> 8) & 255) / 255,
                  blue: Double(hex & 255) / 255, opacity: alpha)
    }

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

/// Shared native visual language, based on the approved V3 references.
/// Green/red keep the user's established rise/fall convention; blue denotes interaction.
enum NK {
    static let accent = Color(hex: 0x0754E9)
    static let up = Color(hex: 0x23804D)
    static let down = Color(hex: 0xD84B46)
    static let amber = Color(hex: 0xB96C12)
    static let textPrimary = Color(hex: 0x18202E)
    static let textSecondary = Color(hex: 0x667080)
    static let textTertiary = Color(hex: 0x9098A4)
    static let hairline = Color(hex: 0xE4E8EF)
    static let cardBg = Color.white
    static let pageBg = Color(hex: 0xFAFBFD)
    static let pageBgIOS = Color(hex: 0xFBFCFE)
    static let listBg = Color(hex: 0xF7F9FC)
    static let fieldBg = Color(hex: 0xF4F6FA)
    static let chipNeutral = Color(hex: 0xF0F3F7)
    static let disclosureBg = Color(hex: 0xF8FAFD)
    static let brand = LinearGradient(colors: [Color(hex: 0x2F75FF), accent], startPoint: .topLeading, endPoint: .bottomTrailing)
    static let alertGrad = LinearGradient(colors: [down, amber], startPoint: .topLeading, endPoint: .bottomTrailing)
}

enum NKRadius {
    static let card: CGFloat = 16
    static let inner: CGFloat = 10
    static let control: CGFloat = 10
    static let memberCard: CGFloat = 12
    static let badge: CGFloat = 5
    static let field: CGFloat = 10
    static let hero: CGFloat = 20
    static let sheet: CGFloat = 24
}

enum NKSpace {
    #if os(macOS)
    static let pagePad: CGFloat = 28
    static let cardPad: CGFloat = 22
    #else
    static let pagePad: CGFloat = 20
    static let cardPad: CGFloat = 20
    #endif
    static let pagePadWide: CGFloat = 28
    static let pagePadBottom: CGFloat = 36
    static let cardPadH: CGFloat = 20
    static let cardGap: CGFloat = 18
    static let blockGap: CGFloat = 14
    static let gap: CGFloat = 12
    static let denseGap: CGFloat = 6
    static let rowGap: CGFloat = 6
    static let listPadH: CGFloat = 16
    static let listPadV: CGFloat = 20
    static let listHeaderExtraH: CGFloat = 4
    static let listPadTop: CGFloat = 24
}

enum NKFont {
    static let heroNumber = Font.system(size: 38, weight: .semibold).monospacedDigit()
    static let metric = Font.system(size: 24, weight: .semibold).monospacedDigit()
    static let title1 = Font.system(size: 28, weight: .bold)
    static let title2 = Font.system(size: 24, weight: .bold)
    static let title3 = Font.system(size: 19, weight: .semibold)
    #if os(macOS)
    static let headline = Font.system(size: 16, weight: .semibold)
    static let body = Font.system(size: 14)
    static let callout = Font.system(size: 13)
    static let caption = Font.system(size: 12)
    #else
    static let headline = Font.headline
    static let body = Font.system(.body)
    static let callout = Font.subheadline
    static let caption = Font.footnote
    #endif
    static let label = Font.system(size: 11, weight: .semibold)
    static let badge = Font.system(size: 11, weight: .medium)
    static let labelTracking: CGFloat = 0.5
    static let monoKey = Font.system(size: 11).monospaced()
    static let monoValue = Font.system(size: 13, weight: .medium).monospacedDigit()
}

extension View {
    func nkLabel() -> some View { font(NKFont.label).tracking(NKFont.labelTracking) }
}

/// 晨报界面。浅色与暗色都完整；v3 仍在用的 `NK` / `NKFont` / `NKSpace` / `NKRadius` 先留着。
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
