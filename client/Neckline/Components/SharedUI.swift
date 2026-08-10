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
    /// macOS 原型 264 行起 —— 两个数刻意不等,V2.3.0 统一成一个 16 是这次要收的差)。
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
        // 🔴 **空文案 → 整枚不画**(V2.3 截图核对时逮到):`depthLabel` 这类展示层换算在
        // 服务端没给该字段时会返回空串,原来会渲染成**一枚没有字的灰色胶囊** —— 它既不是
        // 「没有」也不是「没看」,只是一团噪声,而且看起来像界面坏了。
        // ⚠ 这**不是**在藏信息:真要说「这一项没取到」得用一句话说出口(本项目一贯做法),
        // ⛔ 不能靠一枚空徽标暗示。
        if text.isEmpty {
            EmptyView()
        } else {
            // 🔴 **V2.3.1:去胶囊**(§② 钉子 2,本版覆盖面最广的一处系统性偏差)。
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

/// 仓位额度三态徽标(满额/半额/休息)。
struct QuotaBadge: View {
    let quota: PositionQuota
    var body: some View {
        NKChip(text: quota.label, tone: quota.tone, filled: true)
    }
}

// ⚠ **`VerdictBadge` 已随问询台整链退役删除**(V2.1-①):它是问询台描述性标注
// (`InquiryVerdict`)专用的徽标,唯一消费方 `InquiryView.swift` 已物理删除,依赖的
// `InquiryVerdict` 类型也已从 `Models.swift` 删除,徽标随之陪葬(不是遗漏)。

// ⚠ **`LLMJudgmentBadge` 已随候选族 DTO 整族退役**(V2-⑮):`ReportOut.candidates` 键
// 已删,LLM 的产出改由**篮子卡**承载(叙述 / 剧本 / 三个参考件),每处带下面这条标注。

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

// MARK: - 退潮红色刹车横幅(§2.4「今日计划作废、禁开新仓」,最高优先级视觉)

struct RetreatBrakeBanner: View {
    let reason: String
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(NKFont.headline)
            VStack(alignment: .leading, spacing: 3) {
                Text("退潮红色刹车 · 今日计划作废、禁开新仓")
                    .font(NKFont.body).fontWeight(.bold)
                if !reason.isEmpty {
                    Text(reason).font(NKFont.callout).opacity(0.9)
                }
            }
            Spacer()
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

/// **通栏刹车条**(V2.3:压在工具栏 / 大标题下方,⛔ **不进卡片流**)。
///
/// 🔴 它管的是「**今天整份计划**」,不是某一篮 —— 所以既不是卡片、也不属于任何一个
/// 板块,由 `RootView` 的壳统一挂。⚠ **刹车激活时篮子仍然全部列出、点得开**:
/// 作废的是计划,不是数据;**补录开仓按钮同样不灰化**(硬拦 = 帮用户瞒报)。
struct RetreatBrakeBar: View {
    let reason: String
    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(NKFont.body).fontWeight(.bold)
            VStack(alignment: .leading, spacing: 2) {
                Text("退潮红色刹车 · 今日计划作废、禁开新仓")
                    .font(NKFont.body).fontWeight(.bold)
                if !reason.isEmpty {
                    Text(reason).font(NKFont.caption).opacity(0.92)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(.horizontal, NKSpace.pagePad)
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(NK.alertGrad)
    }
}

// MARK: - 漏录兜底提示条(§五 v1.1-B.4/E.3:一句提示,非弹窗打扰,补录后自动消失)

struct MissedEntryHintBanner: View {
    let text: String
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle.fill").font(.system(size: 14, weight: .semibold))
            Text(text).font(NKFont.callout).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .foregroundStyle(NK.amber)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.amber.opacity(0.12)))
    }
}

// MARK: - v1.4-①-C 板块数据过期告警(§七 P0-3:报告顶部醒目告警,不静默把过期数据
// 当正常结果展示)。

/// 数据新鲜度告警(板块 + 行业强度 + **V2-⑭-A 市场扫描层**)。
/// **三件独立故障各占一行,⛔ 不合并成一句** —— 合并读者就分不清哪个坏了
/// (服务端契约同样是三组独立键)。
struct DataFreshnessBanner: View {
    let freshness: DataFreshness
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 14, weight: .semibold))
            VStack(alignment: .leading, spacing: 6) {
                if freshness.stale {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("板块数据已过期").font(NKFont.body).fontWeight(.bold)
                        Text(sectorText).font(NKFont.callout).opacity(0.9)
                        Text("「当日暴起板块」与「题材持续天数」本日不可信").font(NKFont.caption).opacity(0.85)
                    }
                }
                if freshness.industryStrengthStale == true {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("行业强度数据未就绪").font(NKFont.body).fontWeight(.bold)
                        Text(industryText).font(NKFont.callout).opacity(0.9)
                        Text("排序缺行业维度、题材持续天数不可用").font(NKFont.caption).opacity(0.85)
                    }
                }
                if freshness.scanLayerStale == true {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("市场扫描层未就绪").font(NKFont.body).fontWeight(.bold)
                        Text(scanText).font(NKFont.callout).opacity(0.9)
                        // 扫描层没跑 → 今日无种子 → 今日无篮子;而「今天没有篮子」与
                        // 「今天没看」必须能分开,这一行就是把它们分开的那句话。
                        Text("今日篮子若为空,可能是**没看**而不是**今天真没有**")
                            .font(NKFont.caption).opacity(0.85)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .foregroundStyle(.white)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }

    private var sectorText: String {
        let dateText = freshness.sectorDataDate.map { "最新至 \($0)" } ?? "完全缺失"
        return "板块数据\(dateText),落后 \(freshness.sectorLagDays) 个交易日"
    }

    /// `-1` 是哨兵值(完全无数据),**不是"落后 -1 天"**,故单独成句。
    private var industryText: String {
        guard let date = freshness.industryStrengthDate,
              let lag = freshness.industryStrengthLagDays, lag >= 0 else {
            return "行业强度数据完全缺失(预计算表无任何数据)"
        }
        return "行业强度数据最新至 \(date),落后 \(lag) 个交易日"
    }

    private var scanText: String {
        guard let date = freshness.scanLayerDate,
              let lag = freshness.scanLayerLagDays, lag >= 0 else {
            return "扫描层三张预计算表完全缺失"
        }
        return "扫描层最新至 \(date),落后 \(lag) 个交易日"
    }
}

/// ⑤ 数据新鲜度明细(三组各自一行,**该组三键整体缺席 = 本次连新鲜度都没查到**,
/// ⛔ 不是"新鲜")。
struct DataFreshnessDetail: View {
    let freshness: DataFreshness

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            row(title: "概念板块日更",
                date: freshness.sectorDataDate, lag: freshness.sectorLagDays,
                stale: freshness.stale, present: true)
            row(title: "行业强度日更",
                date: freshness.industryStrengthDate, lag: freshness.industryStrengthLagDays,
                stale: freshness.industryStrengthStale,
                present: freshness.industryStrengthLagDays != nil || freshness.industryStrengthDate != nil
                    || freshness.industryStrengthStale != nil)
            row(title: "市场扫描层批算",
                date: freshness.scanLayerDate, lag: freshness.scanLayerLagDays,
                stale: freshness.scanLayerStale,
                present: freshness.scanLayerLagDays != nil || freshness.scanLayerDate != nil
                    || freshness.scanLayerStale != nil)
        }
    }

    @ViewBuilder
    private func row(title: String, date: String?, lag: Int?, stale: Bool?, present: Bool) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(title).font(NKFont.caption).fontWeight(.bold).foregroundStyle(NK.textTertiary)
            Spacer(minLength: 8)
            Text(text(date: date, lag: lag, stale: stale, present: present))
                .font(NKFont.caption).multilineTextAlignment(.trailing)
                .foregroundStyle(tone(stale: stale, present: present).color)
        }
    }

    private func text(date: String?, lag: Int?, stale: Bool?, present: Bool) -> String {
        guard present else { return "本次没查到(⛔ 不等于新鲜)" }
        guard let l = lag else { return date.map { "最新至 \($0)" } ?? "无数据" }
        if l < 0 { return "完全缺失(哨兵值 -1)" }
        let base = date.map { "最新至 \($0)" } ?? "无日期"
        return "\(base) · 落后 \(l) 个交易日" + (stale == true ? " · 已过期" : "")
    }

    private func tone(stale: Bool?, present: Bool) -> NKAxisTone {
        guard present else { return .warn }
        return stale == true ? .bad : .good
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
// ⚠ **V2.3 起底色 = `NK.brand`(已改红橙)+ 折线换成图标那一条**(规范 §07)。
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
    /// 🔴 **千分位分组器**(V2.3.1 补批 1 漏网的全局钉子)。
    ///
    /// 原型全篇金额都是分组的(`¥48,600` / `+¥1,444` / `¥120,000`,macOS 原型 800/778/804 行),
    /// 而 V2.3.0/V2.3.1 落地一路是 `String(format:"%.2f")` → `¥77080.00`。**四位数以上不分组,
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
    /// ⚠ 与 `signedAmount` 的区别**只在小数位**,别混用:持仓侧一律 `signedAmount`。
    static func signedMoney(_ v: Double) -> String {
        // 同 `signedAmount`:符号在 `¥` 外面(⛔ 直接喂负数会得到 `¥-142,300,000.00`)。
        let sign = v > 0 ? "+" : (v < 0 ? "-" : "")
        return sign + "¥" + grouped(abs(v), decimals: 2)
    }
    /// 无符号、一位小数(v1.3-⑥「情报」板块的亿/万元量级数字,如大盘量能/板块资金流,
    /// 不需要 `price` 的两位小数精度)。
    static func money(_ v: Double) -> String { String(format: "%.1f", v) }
}

// MARK: - V2.2-② 行情状态条(报告顶部 / 持仓页顶部;**纯展示、⛔ 零动作**)
//
// 🔴 **这不是买卖信号**:三态回答的是「今天市场结构是什么样」(趋势延续 / 高位分歧 /
// 切换确认),⛔ 不构成任何仓位建议,更不是「今天别开仓」的自动状态位(§五 〇b-7)。
//
// 🔴 **`available == false` 必须如实说出口**:服务端已经把原因写好(批算没跑 / 非交易日
// / 参数非法),⛔ 客户端不合并、不改写、**更不许什么都不显示** —— 不显示等于让读者
// 默认"今天没什么特别的",那正是把「没看」讲成「没有」。

struct MarketRegimeStrip: View {
    let regime: MarketRegime
    /// 紧凑档(持仓页顶部一行);报告顶部用完整档(带增强 / 减弱方向)。
    var compact: Bool = false

    var body: some View {
        // 🔴 **compact 是列表栏里的一小块,不是数据卡**(V2.3.1 批 3):持仓列表栏 376pt
        // 里挂一张白卡会把它抢成"这一栏最重要的东西",而它只是背景语境。形状对齐
        // 同栏的合并敞口块(`radius 9 / padding 10×12 / 淡底 + .5px 同色描边`,
        // macOS 原型 897 行)。⛔ 详情栏那一档(非 compact)仍是白卡,别一起改。
        Group {
            if compact {
                inner
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                        .fill(NK.textTertiary.opacity(0.055)))
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                        .stroke(NK.hairline, lineWidth: 0.5))
            } else {
                NKCard { inner }
            }
        }
    }

    @ViewBuilder
    private var inner: some View {
        if let d = regime.day, regime.available {
            available(d)
        } else {
            unavailable
        }
    }

    @ViewBuilder
    private func available(_ d: MarketRegimeDay) -> some View {
        VStack(alignment: .leading, spacing: compact ? 4 : 10) {
            // 原型 713–717 行:左「行情状态」弱标 + 实心状态徽标,右端**日期 · 骨架版本**
            // 合成一句 `10.5 .40 tabular`(⛔ 不是日期在左、版本徽标在右两处分开)。
            HStack(spacing: 8) {
                Text("行情状态").nkLabel().foregroundStyle(NK.textTertiary)
                NKChip(text: d.displayLabel, tone: d.tone, filled: true)
                Spacer(minLength: 6)
                if !metaLine(d).isEmpty {
                    Text(metaLine(d)).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary)
                }
            }
            if !d.regimeReason.isEmpty {
                Text(d.regimeReason).font(compact ? NKFont.caption : NKFont.body)
                    .lineSpacing(compact ? 0 : 4)
                    .foregroundStyle(compact ? NK.textSecondary : NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !compact {
                // 原型 719 行:增强 / 减弱**并排两组**(`gap:24`),⛔ 不是上下两行通栏。
                HStack(alignment: .top, spacing: 24) {
                    directionRow("增强方向", d.strengthening, .good)
                    directionRow("减弱方向", d.weakening, .warn)
                    Spacer(minLength: 0)
                }
            }
            // 🔴 五维里没算出来的那几维要说出口:缺维**不是**「这一维没问题」。
            // 原型 723–733 行把它收进披露区(收起态那一行已经点名"几维没取得")。
            if compact {
                if !d.missingDims.isEmpty {
                    Text("本次未取得的判定输入:\(d.missingDimLabels.joined(separator: "、")) —— 缺数 = 不知道,不猜")
                        .font(NKFont.caption).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text("行情状态是市场结构的描述 · 不是买卖建议、不改变任何持仓判定")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            } else {
                NKDisclosure(summary: d.missingDims.isEmpty
                             ? "行情状态不是买卖建议"
                             : "\(d.missingDims.count) 维判定输入未取得",
                             tone: d.missingDims.isEmpty ? .neutral : .warn) {
                    if !d.missingDims.isEmpty {
                        Text("本次未取得的判定输入:\(d.missingDimLabels.joined(separator: "、")) —— 缺数 = 不知道,不猜")
                            .foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Text("行情状态是市场结构的描述 · 不是买卖建议、不改变任何持仓判定")
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 「20260810 · K8-V0.5」。**缺哪个不写哪个**,⛔ 不拿占位符冒充。
    private func metaLine(_ d: MarketRegimeDay) -> String {
        [d.tradeDate, d.skeletonVersion].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    @ViewBuilder
    private func directionRow(_ title: String, _ items: [NKJSON], _ tone: NKAxisTone) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 5) {
                Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                NKWrapRow(spacing: 5, lineSpacing: 5) {
                    ForEach(Array(items.enumerated()), id: \.offset) { _, it in
                        NKChip(text: it["industry"]?.stringValue ?? it.displayText, tone: tone)
                    }
                }
            }
        }
    }

    private var unavailable: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "questionmark.circle").font(.system(size: 13))
                .foregroundStyle(NK.textTertiary)
            VStack(alignment: .leading, spacing: 2) {
                Text("行情状态本次未取得").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textSecondary)
                // 服务端已把「批算没跑 / 非交易日 / 参数非法」分开写好,原样展示。
                Text(regime.unavailableReason ?? "服务端未给原因")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("⛔ 「没取到」不等于「今天没什么特别的」")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            }
            Spacer()
        }
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
    /// `NECKLINE_INITIAL_RECEIPT=1` → 选股板块列表栏**初始选中「昨日回执」**那一行。
    /// ⚠ 与 `NECKLINE_INITIAL_BASKET_ID` 互斥:选了篮子就以篮子为准(篮子选中态住在
    /// `AppModel`,回执只是列表栏的本地选中,判据见 `BasketDailyView.detailColumn`)。
    /// ⛔ 同 `expandDisclosures`:**只给 `@State` 当初值**,不夺走用户的点击。
    static let initialReceipt: Bool =
        ProcessInfo.processInfo.environment["NECKLINE_INITIAL_RECEIPT"] == "1"
    /// `NECKLINE_INITIAL_POSITIONS_PANE=board|alerts` → 持仓板块列表栏**初始选中**
    /// 「盘中动态」/「临时提醒」那一行(选具体某笔仓走 `NECKLINE_INITIAL_POSITION_ID`,
    /// 它要等数据到位、住在 `AppModel.applyQAHooksAfterRefresh`)。
    /// ⛔ 同上:**只给 `@State` 当初值**,不夺走用户的点击。
    static let initialPositionsPane: String? =
        ProcessInfo.processInfo.environment["NECKLINE_INITIAL_POSITIONS_PANE"]
}
