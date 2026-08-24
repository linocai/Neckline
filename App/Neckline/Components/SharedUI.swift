//
//  SharedUI.swift
//  Neckline — 跨板块共用 UI 组件(卡片容器 / 徽标 / Toast / 着色映射)
//

import SwiftUI

// MARK: - 轴向着色映射(Models.swift 的 NKAxisTone → 实际颜色,保持 Models.swift 无 UI 依赖)

extension NKAxisTone {
    var color: Color {
        switch self {
        case .good: return NK.up
        case .warn: return NK.amber
        case .bad: return NK.down
        case .neutral: return NK.textSecondary
        case .info: return NK.accent
        }
    }
}

// MARK: - 卡片容器(不透明背景,Liquid Glass 只用于栏/浮层,§3.5)

struct NKCard<Content: View>: View {
    /// 给了值 = **上下左右都用它**(旧调用点 `NKCard(padding: 12)` 语义逐字节不变);
    /// 不给 = 走令牌的**上下 / 左右两档**(原型每张数据卡都是 `padding:16px 18px`,
    /// 两个方向的间距刻意不同。
    var padding: CGFloat? = nil
    @ViewBuilder var content: Content

    var body: some View {
        content
            .padding(.vertical, padding ?? NKSpace.cardPad)
            .padding(.horizontal, padding ?? NKSpace.cardPadH)
            .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
    }
}

/// 列表栏 / 详情栏共用的**分组头**(macOS 原型 113–117 / 234 行)。
///
/// 形状 = `[实心 Tier 徽标] 说明文字 ————————` :`10.5/600 + tracking .5` 的说明
/// 后面跟一条 `.5px` 细线吃掉剩余宽度。⛔ 别用 `title3`(17)那一档 —— 原型的分组头
/// 是**压在内容之下**的一条弱标,17 会把它抢成页面标题。
struct NKGroupHeader<Leading: View>: View {
    let text: String
    @ViewBuilder var leading: Leading

    var body: some View {
        HStack(spacing: 7) {                       // 原型 113 行 gap:7
            leading
            Text(text).nkLabel().foregroundStyle(NK.textTertiary)
            Rectangle().fill(NK.hairline).frame(height: 0.5)
        }
    }
}

extension NKGroupHeader where Leading == EmptyView {
    init(_ text: String) { self.init(text: text) { EmptyView() } }
}

/// **自动换行的横排**(HTML `display:flex; flex-wrap:wrap` 的等价物)。
///
/// ⚠ 用它的地方原型都是 `flex-wrap:wrap`(代码方块 / 板块标签 / 成员 chips)——
/// ⛔ 别拿横向 `ScrollView` 顶替:桌面上没有滚动提示,超出宽度的项**看不见也不知道有**。
struct NKWrapRow: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0, widest: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > 0, x + spacing + size.width > maxWidth {
                y += lineHeight + lineSpacing
                x = 0; lineHeight = 0
            }
            x += (x > 0 ? spacing : 0) + size.width
            widest = max(widest, x)
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: proposal.width ?? widest, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > 0, x + spacing + size.width > bounds.width {
                y += lineHeight + lineSpacing
                x = 0; lineHeight = 0
            }
            if x > 0 { x += spacing }
            s.place(at: CGPoint(x: bounds.minX + x, y: bounds.minY + y),
                    anchor: .topLeading, proposal: ProposedViewSize(size))
            x += size.width
            lineHeight = max(lineHeight, size.height)
        }
    }
}

/// **灰底说明块**(macOS 原型 1196 / 1224 行:`padding:13px 15px; border-radius:10;
/// background:rgba(60,60,67,.035); border:.5px solid rgba(60,60,67,.10); font-size:12px;
/// color:rgba(60,60,67,.65); line-height:1.6`)。
///
/// ⚠ 与 `NKDisclosure` 刻意不同:**这一块常开、不折叠** —— 原型把「盘中关注池是代理
/// 样本」「时间退出判向挂起是什么意思」这类**读这一屏就必须知道的前提**画成常开的灰块,
/// 而 `NKDisclosure` 收的是"想深究再点开"的那一层。⛔ 别把这块也折起来。
struct NKNoteBlock: View {
    let text: LocalizedStringKey

    var body: some View {
        Text(text)
            .font(NKFont.callout)
            .lineSpacing(4)                      // 12px × 1.6 行高 ≈ 额外 4pt
            .foregroundStyle(NK.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 15).padding(.vertical, 13)
            .background(RoundedRectangle(cornerRadius: 10).fill(NK.textTertiary.opacity(0.09)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(NK.hairline, lineWidth: 0.5))
    }
}

/// 服务端文案里的 `**加粗**` / 反引号 → 真的加粗 / 等宽。
///
/// **`Text(String)` 不解析 Markdown,只有 `Text("字面量")` 解析**(§五 〇d 第 7 条)——
/// 而服务端有大量文案本来就是按 markdown 写的(周度校准 `disclaimer`、四分类
/// `suggestion`、安慰剂 `note`、校准段 `unavailableReason` …），
/// 逮到「对照口径:两臂都\*\*不设最高追价上限\*\*」这类**星号原样上屏**。
///
/// ⚠ **只做渲染,⛔ 不改一个字**:解析失败(markdown 语法坏了)→ 原样返回纯文本,
/// 绝不吞内容。⛔ 也别拿它去"清洗"星号 —— 那是删信息,不是渲染。
/// ⚠ `inlineOnlyPreservingWhitespace` = 只认行内语法,保留原文的换行与空白
/// (默认策略会把软换行吃掉,把两段并成一段)。
func nkMarkdown(_ s: String) -> AttributedString {
    (try? AttributedString(
        markdown: s,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(s)
}

/// **一格读数**(原型 1454–1459 / 1494–1496:`11px .55` 标题 + `20/600 tabular` 数值)。
/// 与 `NKMetricsGrid`(等宽键值,答"这个数是怎么来的")**刻意不同**:这一档是给人看的
/// 概览读数,⛔ 别把它俩合并。
struct NKStatCell: View {
    let title: String
    let value: String
    var tone: NKAxisTone = .neutral
    /// 数值下面那一行更细的口径(如「2 笔 / 判定 2 笔」)。**缺就不写**,⛔ 不占位。
    var footnote: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value).font(NKFont.metric)
                .foregroundStyle(tone == .neutral ? NK.textPrimary : tone.color)
            if let f = footnote, !f.isEmpty {
                Text(f).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// **三列读数网格**(原型 1453 / 1493 行 `repeat(3,1fr); gap:18px 20px`)。
struct NKStatGrid<Content: View>: View {
    var columns: Int = 3
    @ViewBuilder var content: Content

    var body: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 20,
                                                     alignment: .topLeading),
                                 count: columns),
                  alignment: .leading, spacing: 18) { content }
    }
}

struct NKSectionHeader: View {
    let title: String
    var trailing: String? = nil

    var body: some View {
        HStack {
            Text(title).font(NKFont.headline).foregroundStyle(NK.textPrimary)
            Spacer()
            if let t = trailing {
                Text(t).font(NKFont.callout).foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - 徽标 / 标签

struct NKChip: View {
    let text: String
    var tone: NKAxisTone = .neutral
    var filled: Bool = false

    var body: some View {
        // 空文案时整枚不画；展示层换算在
        // 服务端没给该字段时会返回空串,原来会渲染成**一枚没有字的灰色胶囊** —— 它既不是
        // 「没有」也不是「没看」,只是一团噪声,而且看起来像界面坏了。
        // ⚠ 这**不是**在藏信息:真要说「这一项没取到」得用一句话说出口(本项目一贯做法),
        // ⛔ 不能靠一枚空徽标暗示。
        if text.isEmpty {
            EmptyView()
        } else {
            // 徽标不使用胶囊形状。
            // 规范 §04 写死「徽标(**方**)4–5 · 原来全是胶囊」,而六份原型里每一枚徽标
            // 的 inline style 都是 `border-radius:4px; font-size:10.5px; font-weight:600;
            // padding:2px 6px`(macOS 原型 40 / 253–258 / 367–376 行)。
            // ⚠ 这是**全 App 徽标的唯一实现** —— 改这一处,四个板块 + 弹层 + 信息卡的
            // 每一枚徽标同时变方、变小、变紧,后面每一批的实拍都要复查一遍密度。
            Text(text)
                .font(NKFont.badge)
                .foregroundStyle(filled ? Color.white : tone.color)
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(
                    RoundedRectangle(cornerRadius: NKRadius.badge)
                        .fill(filled ? tone.color : tone.color.opacity(0.12))
                )
        }
    }
}

/// **「参考、非指令」标注**(§2.8 红线:参考件每处出现都要带)。
///
/// **四不**:不进排序 / 不进哨兵 / 不改去留 / 不加分。⛔ 不许省略、不许改写成
/// 「建议」「推荐」之类的指令口吻 —— 这句话是 LLM 产出与硬纪律之间的那条线。
struct NKReferenceNote: View {
    var text: String = "参考、非指令 · 不进排序、不进哨兵、不改去留、不加分"
    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: "info.circle").font(.system(size: 9))
            Text(text).font(NKFont.caption)
        }
        .foregroundStyle(NK.textTertiary)
    }
}

/// 自由结构字段(`mech` / `tierBreakdown` / `verificationSpec` / `manualForm` …)的
/// 键值表。**只展示、不解释**:这些键是服务端的语义标识符(维度名 / 条件名),
/// ⛔ 客户端不改名、不重算、不猜含义。
struct NKJSONTable: View {
    let value: NKJSON

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            if let obj = value.objectValue, !obj.isEmpty {
                // 按字典序,**确定性** —— 顺序不能每次刷新都跳。
                ForEach(value.sortedKeys, id: \.self) { k in
                    HStack(alignment: .top, spacing: 8) {
                        Text(k).font(NKFont.monoKey)
                            .foregroundStyle(NK.textTertiary)
                        Spacer(minLength: 8)
                        Text(obj[k]?.displayText ?? "—")
                            .font(NKFont.monoKey)
                            .foregroundStyle(NK.textSecondary)
                            .multilineTextAlignment(.trailing)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } else if let arr = value.arrayValue {
                ForEach(Array(arr.enumerated()), id: \.offset) { _, item in
                    Text("· \(item.displayText)").font(NKFont.monoKey)
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text(value.displayText).font(NKFont.monoKey)
                    .foregroundStyle(NK.textSecondary)
            }
        }
    }
}

// MARK: - iOS 刷新胶囊

#if os(iOS)
/// **蓝底刷新胶囊**(iOS 原型 90–93 / 316–319 行:`padding:6px 12px; radius:999;
/// background:#0B6BCB` + 11px 图标 + `12/600` 白字的**上次刷新时刻**)。
///
/// 🔴 **按钮上直接写时刻**,与 macOS 工具栏那枚同一口径(`NKToolbar.refreshButton`):
/// 盘中最常问的是「我看的这份是几点的」——⛔ 一枚裸箭头答不了。
/// ⚠ 还没成功刷新过就写「刷新」二字,⛔ 不拿"现在"冒充。
struct NKRefreshPill: View {
    @Bindable var model: AppModel

    var body: some View {
        // 按当前页面刷新，避免无关页面重复请求。
        Button { Task { await model.refresh(for: model.view) } } label: {
            HStack(spacing: 5) {
                if model.isLoadingCurrentTab {
                    ProgressView().controlSize(.mini).tint(.white)
                } else {
                    Image(systemName: "arrow.clockwise").font(.system(size: 11, weight: .semibold))
                }
                Text(model.lastRefreshedAt.map(NKRefreshPill.hhmm) ?? "刷新")
                    .font(NKFont.callout.monospacedDigit()).fontWeight(.semibold)
            }
            .foregroundStyle(.white)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Capsule().fill(NK.accent))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(model.isLoadingCurrentTab)
    }

    private static let fmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()
    static func hhmm(_ d: Date) -> String { fmt.string(from: d) }
}
#endif

// MARK: - 列表行外壳(选中态 = 白底 + accent 描边;⛔ 靠选中态分隔,不靠留白)
//
// 共用列表栏放在共享组件中，不属于任何单一页面。

struct NKListRow<Content: View>: View {
    let selected: Bool
    let action: () -> Void
    @ViewBuilder var content: Content

    var body: some View {
        Button(action: action) {
            content
                // 原型 `pick()`(macOS 原型 1783 行):`padding:11px 12px; border-radius:9px`。
                .padding(.horizontal, 12).padding(.vertical, 11)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .fill(selected ? NK.cardBg : Color.clear)
                        // 🔴 选中态是**白底 + 1.5px 实蓝描边 + 一层极轻投影**。
                        // ⚠ 阴影**恒定不参与动画**(全局三禁之一):选中态切换只换颜色。
                        .shadow(color: selected ? Color.black.opacity(0.06) : .clear,
                                radius: 1.5, y: 1)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: NKRadius.inner)
                        .stroke(selected ? NK.accent : Color.clear, lineWidth: 1.5)
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Toast

struct ToastView: View {
    let toast: Toast
    var body: some View {
        Text(toast.message)
            .font(NKFont.body).fontWeight(.medium)
            .foregroundStyle(.white)
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(Capsule().fill(toast.isError ? NK.down : Color.black.opacity(0.82)))
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .padding(.horizontal, 24)
    }
}

// MARK: - 空态 / 降级占位

struct NKEmptyState: View {
    let title: String
    var subtitle: String? = nil
    var systemImage: String = "tray"

    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: systemImage).font(.system(size: 32)).foregroundStyle(NK.textTertiary)
            Text(title).font(NKFont.body).fontWeight(.medium).foregroundStyle(NK.textSecondary)
            if let s = subtitle {
                Text(s).font(NKFont.callout).foregroundStyle(NK.textTertiary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }
}

// MARK: - 品牌 Logo(颈线折线的极简抽象,与 App 图标同源)
//
// 底色使用 `NK.brand` 红橙渐变。
// 🔴 **图标按尺寸分三档、不是同一张图缩放**;`NKLogo` 只用得到最小那两档:
//   · `size > 20` —— 实线颈线 + 两侧颈线点(对应图标 128 档的意思);
//   · `size <= 20` —— **简化版**:只留四拐点的粗折线(对应图标 64 及以下档)。
// ⛔ 别给 `NKLogo` 加突破点圆环:那是 256 以上档的元素,20~38px 上糊成一团。

struct NKLogo: View {
    var size: CGFloat = 27

    /// 四拐点折线:左平台 → 回踩 → 站上颈线 → 突破拉升。**与图标 64 档同一条**。
    private var necklinePath: Path {
        Path { p in
            p.move(to: CGPoint(x: size * 0.16, y: size * 0.575))
            p.addLine(to: CGPoint(x: size * 0.31, y: size * 0.60))
            p.addLine(to: CGPoint(x: size * 0.41, y: size * 0.72))
            p.addLine(to: CGPoint(x: size * 0.53, y: size * 0.565))
            p.addLine(to: CGPoint(x: size * 0.84, y: size * 0.27))
        }
    }

    /// 颈线水位上的点(暗示"这条线之前一直在这儿")。小尺寸下整组不画。
    private var necklineDots: some View {
        ForEach([0.085, 0.205, 0.645, 0.755], id: \.self) { x in
            Circle()
                .fill(Color.white.opacity(0.55))
                .frame(width: size * 0.055, height: size * 0.055)
                .position(x: size * x, y: size * 0.565)
        }
    }

    var body: some View {
        ZStack {
            // 圆角 22.37% 连续曲率 —— 与图标 PNG 里烘进去的那一档对齐(⛔ 不是满幅方角)。
            RoundedRectangle(cornerRadius: size * 0.2237, style: .continuous).fill(NK.brand)
            if size > 20 { necklineDots }
            necklinePath.stroke(
                Color.white,
                style: StrokeStyle(lineWidth: max(1.6, size * (size > 20 ? 0.095 : 0.125)),
                                   lineCap: .round, lineJoin: .round)
            )
        }
        .frame(width: size, height: size)
    }
}

// MARK: - 数字格式化

enum NKFmt {
    /// API 的 YYYYMMDD / YYYY-MM-DD 统一显示为中文日期；无法识别时保留原文，绝不猜日期。
    static func reportDate(_ raw: String) -> String {
        let clean = raw.replacingOccurrences(of: "-", with: "")
        guard clean.count == 8, let year = Int(clean.prefix(4)),
              let month = Int(clean.dropFirst(4).prefix(2)), let day = Int(clean.suffix(2)) else { return raw }
        return "\(year)年\(month)月\(day)日"
    }
    /// ISO 时间统一显示为本地可读日期时间；解析失败保留原文，不猜测时区。
    static func timestamp(_ raw: String) -> String {
        guard let value = ISO8601DateFormatter().date(from: raw) else { return reportDate(raw) }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.dateFormat = "yyyy年M月d日 HH:mm"
        return formatter.string(from: value)
    }
    /// 千分位分组器。
    ///
    /// 原型全篇金额都是分组的(`¥48,600` / `+¥1,444` / `¥120,000`,macOS 原型 800/778/804 行),
    /// **四位数以上不分组，
    /// 读数时要一位一位数** —— 这是四个板块共 27 个调用点同时中招的一处系统性偏差。
    ///
    /// ⚠ **locale 钉死 `en_US_POSIX`**:分组符必须是逗号且**与用户系统区域无关** ——
    /// 跟着系统走会让不同机器上的截图对不上,也会让某些区域出现空格分组(`77 080`)。
    private static func grouped(_ v: Double, decimals: Int) -> String {
        let f = NumberFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.numberStyle = .decimal
        f.usesGroupingSeparator = true
        f.groupingSeparator = ","
        f.groupingSize = 3
        f.minimumFractionDigits = decimals
        f.maximumFractionDigits = decimals
        return f.string(from: NSNumber(value: v)) ?? String(format: "%.\(decimals)f", v)
    }

    /// **股价 / 费用**:两位小数 + 千分位(高价股 `¥1,802.00` 也读得出来)。
    /// ⚠ 与下面的 `amount` **刻意分档**:价要小数(¥42.30 与 ¥42.3 是不同的信息),
    /// 额不要(合计成本精确到分对决策毫无用处,却把数字拉长一截)。
    static func price(_ v: Double) -> String { grouped(v, decimals: 2) }

    /// **金额**(合计成本 / 同题材敞口 / 总仓分母):千分位、**不带小数**。
    /// 原型 800 行 `¥48,600`、804 行 `¥120,000` —— ⛔ 别加回 `.00`。
    static func amount(_ v: Double) -> String { grouped(v, decimals: 0) }

    /// **带符号金额**(合计浮盈 / 单笔浮盈 / 周复盘净盈亏):千分位、**不带小数**。
    /// 原型 778 行 `+¥1,444`、856 行 `+¥2,560`、958 行 `-¥1,116`。
    /// 🔴 **符号必须在 `¥` 外面**(原型 958 行 `-¥1,116`)。⛔ 别把负数直接喂给 formatter ——
    /// 那会得到 **`¥-1,116`**(负号跑进货币符号里面),而且**每一笔亏损仓都会中**,
    /// 编译与单测都发现不了。故:取绝对值格式化、符号自己加。
    /// ⚠ `-0.4` 这类 0 位小数下四舍五入到 0 的值,**符号仍保留**(`-¥0`)——
    /// 宁可看着怪,也不把一笔小亏印成持平。
    static func signedAmount(_ v: Double) -> String {
        let sign = v > 0 ? "+" : (v < 0 ? "-" : "")
        return sign + "¥" + grouped(abs(v), decimals: 0)
    }

    static func pct(_ v: Double) -> String { String(format: "%.2f%%", v) }
    /// **比例**(0.05)→ 展示百分数("5%");非整百分点保留小数("5.5%"),不四舍五入
    /// 成 "6%" 骗人。章程口径指纹(止损比例 / 回落止盈比例)专用——与 `pct(_:)`
    /// (入参已经是百分数)**不是一回事**,别混用。服务端同款实现见
    /// `report/render.py::_ratio_pct_txt`(两端各自格式化,不下发拼好的文案)。
    static func ratioPct(_ v: Double) -> String {
        var s = String(format: "%.2f", v * 100)
        if s.contains(".") {
            while s.hasSuffix("0") { s.removeLast() }
            if s.hasSuffix(".") { s.removeLast() }
        }
        return s + "%"
    }
    static func signedPct(_ v: Double) -> String {
        let sign = v > 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", v))%"
    }
    /// **带符号金额 · 保留分**(龙虎榜净额那一族:信息卡原型 185 行
    /// `净额 +¥142,300,000.00` —— 那一屏的原型自己就带两位小数)。
    /// ⚠ 与 `signedAmount` 的区别**只在小数位**；交易金额一律用 `signedAmount`。
    static func signedMoney(_ v: Double) -> String {
        // 同 `signedAmount`:符号在 `¥` 外面(⛔ 直接喂负数会得到 `¥-142,300,000.00`)。
        let sign = v > 0 ? "+" : (v < 0 ? "-" : "")
        return sign + "¥" + grouped(abs(v), decimals: 2)
    }
    /// 无符号、一位小数(亿 / 万元量级数字,不需要 `price` 的两位小数精度)。
    static func money(_ v: Double) -> String { String(format: "%.1f", v) }

    /// **可编辑数值位**(预案修改入口的输入框预填)。
    /// 🔴 **⛔ 不带千分位、⛔ 不带货币符号** —— 这个串会被原样 `Double(...)` 解回去,
    /// `1,802.00` 解不出来。`price(_:)` 是**展示**用的,两者⛔ 不许互换。
    static func slotValue(_ v: Double) -> String {
        var s = String(format: "%.4f", v)
        while s.contains("."), s.hasSuffix("0") { s.removeLast() }
        if s.hasSuffix(".") { s.removeLast() }
        return s
    }

    /// **比例 → 带符号百分数**(上方机械空间那一族:`0.1234` → `+12.34%`)。
    /// ⚠ 与 `ratioPct` 的区别只在符号,别混用。
    static func signedRatioPct(_ v: Double) -> String {
        (v > 0 ? "+" : "") + String(format: "%.2f", v * 100) + "%"
    }
}


// MARK: - QA / 截图钩子(⚠ **只影响初始展开态,⛔ 不改任何数据、不夺走交互**)
//
// 🔴 **为什么需要它**:本环境 computer-use **点不动 Simulator**(CLAUDE.md 登记的
// 已知限制,`mcp__Claude_Code_iOS_Simulator__control` 的 tap 恒报错、且与
// `xcode-select` 无关),视觉核对只能走 `xcrun simctl io screenshot` —— 于是
// 「点开才看得到」的内容(如位置关 / 核心关的读数展开区)在截图里永远拍不到。
// 同 `NECKLINE_INITIAL_TAB` / `NECKLINE_INITIAL_MODAL` 的既有先例。
//
// ⚠ **缺环境变量时恒 `false`**:正常用户路径逐字节不变。
// ⛔ 用它的地方必须是 `@State` 的**初值**,不许写成 `isExpanded: .constant(...)`
// —— 那会把用户的点击一起夺走,拿截图便利换掉真交互。

enum NKQA {
    /// `NECKLINE_EXPAND_DISCLOSURES=1` → 展开区默认展开(纯截图辅助)。
    static let expandDisclosures: Bool =
        ProcessInfo.processInfo.environment["NECKLINE_EXPAND_DISCLOSURES"] == "1"
    /// `NECKLINE_INITIAL_SETTINGS_GROUP=backend|llm|push|version` → 设置板块列表栏
    /// **初始选中**那一组；只给 `@State` 当初值。
    static let initialSettingsGroup: NKSettingsGroup? =
        ProcessInfo.processInfo.environment["NECKLINE_INITIAL_SETTINGS_GROUP"]
            .flatMap(NKSettingsGroup.init(rawValue:))
    // ⚠ **`NECKLINE_INITIAL_SELECTION_VIEW` / `NECKLINE_INITIAL_STOCK_CODE` 不在本枚举里**:
    // 它们要等报告拉回来才有东西可切 / 可开,故消费点在
    // `AppModel.applyQAHooksAfterRefresh()` —— 本枚举里的钩子都是**同步可读的初值**,
    // ⛔ 别把异步那一族搬进来。
    // 仅保留当前页面可消费的启动钩子。

    /// `NECKLINE_INITIAL_PROVIDER_FORM=1` → 设置 · Provider 屏开**编辑 Provider** 弹层
    /// (取列表首个 Provider)。⚠ 它要等 `loadSettings()` 拿回注册表才有东西可编,
    /// 故触发点在 `SettingsView.task` 里而不是 `NecklineApp.init()`。
    static let initialProviderForm: Bool =
        ProcessInfo.processInfo.environment["NECKLINE_INITIAL_PROVIDER_FORM"] == "1"
}
