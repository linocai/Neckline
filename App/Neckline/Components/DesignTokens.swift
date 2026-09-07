import SwiftUI

extension Color {
    init(hex: UInt, alpha: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 255) / 255,
                  green: Double((hex >> 8) & 255) / 255,
                  blue: Double(hex & 255) / 255, opacity: alpha)
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
