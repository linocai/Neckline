//
//  NKMemberCard.swift
//  Neckline — V2.3 视觉升级:成员卡(规范 §05 / §01 决定 05)
//
//  🔴 **个股是一等对象**:这张卡答的唯一问题是「**这只票为什么能进篮子**」——
//  进篮理由 + 位置关 / 核心关判定与读数 + 三个参考件 + K7 标注。
//
//  🔴 **只有标题行是开关**(规范 §05 明写):点开后正文 / 读数 / 按钮都**不**收起卡片。
//  ⛔ 别图省事把整张卡包成一个 `Button` —— 那样正文选不了字、底部两个按钮也点不动。
//
//  🔴 **判定为 nil 时整块不显示**,⛔ 不写「未判定」这种看起来像结论的占位
//  (老卡缺这六键是常态,纯新增字段)。
//
//  🔴 **角色两说并存**:`roleConflict` 时**两个都摆出来**,⛔ 不挑一个当正确答案 ——
//  分歧本身就是信息(机械按 RS 排名判、LLM 按证据判,判据不同)。
//

import SwiftUI

struct NKMemberCard: View {
    @Bindable var model: AppModel
    let member: BasketMember
    let basketName: String
    let tradeDate: String

    /// ⚠ 初值来自 QA 钩子(缺环境变量时恒 `false`),**只改初值**,用户照常可收放。
    @State private var expanded: Bool = NKQA.expandDisclosures

    var body: some View {
        #if os(macOS)
        // 🔴 **macOS:成员卡是"④ 那张白卡里的一块"**(原型 `memCard()`,1770 行):
        // `radius 10`、**无边框、无底色**;选中(= 展开)才换 `#FAFAFC` 底 +
        // `inset 0 0 0 1px rgba(11,107,203,.28)` 内描边。
        // ⛔ 别再给它自己套一张 `NKCard` —— 卡里套卡会出现两层描边、两层圆角。
        VStack(alignment: .leading, spacing: 0) {
            header
                .padding(.horizontal, 12).padding(.vertical, 11)   // 原型 366 行
            if expanded {
                expandedBody
                    .padding(.horizontal, 14).padding(.bottom, 14) // 原型 375 行
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // 原型选中底 `#FAFAFC`;既有令牌 `disclosureBg` 就是 `#FAFAFB`(差一档蓝,肉眼同色)。
        .background(RoundedRectangle(cornerRadius: NKRadius.memberCard)
            .fill(expanded ? NK.disclosureBg : Color.clear))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.memberCard)
            .strokeBorder(expanded ? NK.accent.opacity(0.28) : Color.clear, lineWidth: 1))
        #else
        VStack(alignment: .leading, spacing: 0) {
            header
            if expanded { expandedBody.padding(.top, NKSpace.blockGap) }
        }
        .padding(NKSpace.cardPad)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.card).stroke(NK.hairline, lineWidth: 0.5))
        #endif
    }

    private var expandedBody: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            if !member.reason.isEmpty {
                Text(member.reason).font(NKFont.callout).lineSpacing(4)
                    .foregroundStyle(NK.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            roleConflictBlock
            chipsRow
            gateCard(title: "位置关 · 落地起跳",
                     label: member.positionVerdictLabel, tone: member.positionVerdictTone,
                     reason: member.positionReason, metrics: member.positionMetrics)
            // V2.4.0 P1.2:核心关自此是**角色感知**的(leader/core/elastic 三把尺),
            // ⛔ 标题不再写「行业龙头」—— 那正是被 §2.10-A 取代的"一把尺"。
            gateCard(title: "核心关 · 核心资格",
                     label: member.coreVerdictLabel, tone: member.coreVerdictTone,
                     reason: member.coreReason, metrics: member.coreMetrics,
                     domain: member.comparisonDomainLabel)
            // 🔴 两关**一关都没判出来**时要说出口(原型 577–580 行的整段):
            // ⛔ 不是"判过了没问题",是这次根本没判出来。
            unjudgedNote
            referenceBlock
            tagsBlock
            actionRow.padding(.top, 2)
        }
    }

    /// 位置关 / 核心关**都缺**时的那一块(原型 576–580)。⚠ 只在两关全缺时出现 ——
    /// 缺一关由那一关自己"整块不显示"表达。
    @ViewBuilder
    private var unjudgedNote: some View {
        if member.positionVerdictLabel == nil && member.coreVerdictLabel == nil {
            // 🔴 **⛔ 别用 `+` 拼**(同 `BasketCardView` 那处,V2.3.3 批 ⑦ 一并修):
            // `"a" + "b"` → `String` → `Text(String)` **不解析 Markdown**,四个星号会
            // 原样印在屏幕上。要拼就拼成**一整条字面量**。
            Text("这只票**没有位置关与核心关的判定** —— 不是判过了没问题,是这次根本没判出来。缺数 = 不知道,不猜。")
                .font(NKFont.callout).lineSpacing(4).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 13).padding(.vertical, 12)
                .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                    .fill(NK.chipNeutral.opacity(0.7)))
                .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                    .stroke(NK.hairline, lineWidth: 0.5))
        }
    }

    // MARK: - 收起行(44px):名称 · 代码 · 角色徽标 · 两枚判定徽标 · RS 名次

    /// ⚠ **两枚判定徽标在 iPhone 宽度下必须换行**(V2.3 截图核对逮到):402pt 里塞
    /// 「名称 + 代码 + 角色 + 位置X + 核心X + RS#N + chevron」会把名称挤成两行、
    /// 把徽标压成**竖排单字**(「位 置 合 适」)—— 那已经不是紧凑,是读不出来。
    /// macOS 详情栏 ≥700pt,一行放得下,故只在 iOS 分两行。
    private var header: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 9) {                 // 原型 366 行 gap:9
                    // 原型 367 行 `14px/600`;字阶就近取 `headline`(15/600)。
                    Text(member.name).font(NKFont.headline).foregroundStyle(NK.textPrimary)
                        .lineLimit(1).fixedSize(horizontal: true, vertical: false)
                    Text(member.tsCode).font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textTertiary).lineLimit(1)
                    roleBadge
                    // 🔴 裁定 ⑤:主归属是**三态** —— 已确认(绿)/ 归属待确认(黄)/
                    // 老卡未记录(中性)。⛔ 别把「技术兜底选出来的那一篮」画成策略结论。
                    if member.isPrimary {
                        NKChip(text: NKPrimaryStatus.chipText(member.primaryStatus),
                               tone: NKPrimaryStatus.isPending(member.primaryStatus) ? .warn
                                   : (NKPrimaryStatus.isRecorded(member.primaryStatus) ? .good : .info))
                    }
                    #if os(macOS)
                    Spacer(minLength: 6)
                    verdictBadges
                    #else
                    Spacer(minLength: 4)
                    #endif
                    if let rs = member.rsRank {
                        Text("RS #\(rs)").font(NKFont.caption.monospacedDigit())
                            .foregroundStyle(NK.textSecondary).lineLimit(1)
                            .frame(width: 44, alignment: .trailing)   // 原型 373 行 width:44
                    }
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(NK.textTertiary)
                }
                #if os(iOS)
                if member.positionVerdictLabel != nil || member.coreVerdictLabel != nil {
                    HStack(spacing: 4) { verdictBadges; Spacer(minLength: 0) }
                }
                #endif
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// 角色。**两说并存时收起行只给一枚琥珀「角色两说」** —— 两个角色名并排摆在
    /// 展开区(收起行放不下,而截断成一个就等于替用户挑了答案)。
    @ViewBuilder
    private var roleBadge: some View {
        if member.roleConflict {
            NKChip(text: "角色两说", tone: .warn)
        } else if !member.roleDisplay.isEmpty {
            NKChip(text: member.roleDisplay)
        }
    }

    /// 两枚判定徽标(位置 / 核心)。**各自 nil 时各自不显示**,⛔ 不占位。
    private var verdictBadges: some View {
        HStack(spacing: 4) {
            if let l = member.positionVerdictLabel {
                NKChip(text: "位置 \(l)", tone: member.positionVerdictTone, filled: true)
            }
            if let l = member.coreVerdictLabel {
                NKChip(text: "核心 \(l)", tone: member.coreVerdictTone, filled: true)
            }
        }
    }

    // MARK: - 展开区

    /// 琥珀底块并列摆出机械判与 LLM 判。⛔ 不挑一个当正确答案。
    @ViewBuilder
    private var roleConflictBlock: some View {
        if member.roleConflict {
            VStack(alignment: .leading, spacing: 5) {
                Text("角色两说并存").nkLabel().foregroundStyle(NK.amber)
                // ⚠ **这两格也要走 `nkRoleLabel`**(V2.3.1 §〇c 硬伤 2):它们直接印
                // 服务端原值,不换算就是在展开区印 `leader` / `core` —— 与收起行同一个病,
                // 只是藏得深一点。`unknown`(算不出)换算成空串,这里补回 `—`。
                HStack(spacing: 16) {
                    labeledValue("机械判", nkRoleLabelOrDash(member.roleMech))
                    labeledValue("LLM 判", nkRoleLabelOrDash(member.roleLlm))
                    Spacer(minLength: 0)
                }
                Text("两个都显示,不挑一个当正确答案 —— 判据不同(机械按 RS 排名,LLM 按证据),分歧本身就是信息。")
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.amber.opacity(0.10)))
        }
    }

    private func labeledValue(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(NK.textPrimary)
        }
    }

    @ViewBuilder
    private var chipsRow: some View {
        let hasAny = (member.industry?.isEmpty == false) || member.industryLift != nil
            || (member.k4Tag?.isEmpty == false) || (member.primaryReason?.isEmpty == false)
        if hasAny {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    if let i = member.industry, !i.isEmpty { NKChip(text: i) }
                    if let lift = member.industryLift {
                        NKChip(text: String(format: "行业 lift %.2f", lift))
                    }
                    if let k4 = member.k4Tag, !k4.isEmpty { NKChip(text: k4, tone: .warn) }
                    if let pr = member.primaryReason, !pr.isEmpty { NKChip(text: pr) }
                }
                .padding(.vertical, 1)
            }
        }
    }

    /// 一张关卡片 = 关名 + 三态实心徽标 + LLM 理由 + 分隔线 + 原始读数。
    /// **判定 nil → 整块不显示**(⛔ 不写「未判定」)。
    @ViewBuilder
    private func gateCard(title: String, label: String?, tone: NKAxisTone,
                          reason: String?, metrics: NKJSON?,
                          domain: String? = nil) -> some View {
        if let l = label {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {                       // 原型 390 行 gap:8
                    Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                    NKChip(text: l, tone: tone, filled: true)
                    Spacer(minLength: 0)
                }
                if let r = reason, !r.isEmpty {
                    Text(r).font(NKFont.callout).lineSpacing(3)
                        .foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // V2.4.0 P1.3:比较域一行(**跟谁比**)。⚠ 与下面的读数宫格分开:
                // 读数六项恒按**行业**算,比较域可能是驱动域 —— 混在一起会被读成
                // 「这些名次是驱动域内的名次」,那是假的。
                if let d = domain, !d.isEmpty {
                    Text("比较域 · \(d)").font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let m = metrics, let obj = m.objectValue, !obj.isEmpty {
                    Divider().overlay(NK.hairline).padding(.top, 3)
                    NKMetricsGrid(value: m).padding(.top, 3)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            // 原型 388 行 `radius 9 / background:#fff / border:.5px rgba(60,60,67,.10) /
            // padding:12px 13px`。⚠ **macOS 才是白底 + 描边**:成员卡展开后自己是
            // `#FAFAFB`,白块才浮得起来;iOS 的成员卡本身就是白卡,再套白块会消失,
            // 故 iOS 保留 `fieldBg` 灰底(批 7 另行核对)。
            .padding(.horizontal, 13).padding(.vertical, 12)
            #if os(macOS)
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.cardBg))
            .overlay(RoundedRectangle(cornerRadius: NKRadius.inner)
                .stroke(NK.hairline, lineWidth: 0.5))
            #else
            .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.fieldBg))
            #endif
        }
    }

    /// 三个参考件。**夹逼拒收时值是 nil 且原因非空 —— ⛔ 不许显示成 0 或空白**。
    private var referenceBlock: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("三个参考件").nkLabel().foregroundStyle(NK.textTertiary)
                .padding(.bottom, 9)                       // 原型 420 行 margin-bottom:9
            refLine(title: "建仓观察区间", value: member.entryZone?.rangeText,
                    clamp: member.entryZoneClamp, reason: member.entryZoneUnavailableReason,
                    extra: member.entryZone?.why, divider: false)
            refLine(title: "最高追价", value: member.maxChase.map { "¥\(NKFmt.price($0))" },
                    clamp: member.maxChaseClamp, reason: member.maxChaseUnavailableReason,
                    extra: nil, divider: true)
            // ⛔ **不许写成「止盈线」**(§2.8-C 语义红线):离场参考是计划参考,
            // 不是止盈信号,是否离场由用户判断(V2.4.0 P3.2 版本裁定,取代原来那句
            // 把回落止盈说成"才是纪律"的旧措辞 —— `v2.3-k8` 起没有那条机械纪律,
            // 继续那样写就是撒谎;⛔ 别抄回来,守门单测按字面量扫全客户端含注释)。
            refLine(title: "离场参考区间", value: member.exitReference?.rangeText,
                    clamp: member.exitReferenceClamp,
                    reason: member.exitReferenceUnavailableReason, extra: nil, divider: true)
            // ⚠ **一整条字面量,⛔ 不用 `+` 拼接**(即便当前无 Markdown 字符,拼接的产物
            // 是 `String` 而非字面量,`Text(String)` 不解析 Markdown——同一条坑的前置预防)。
            Text("离场参考是计划参考,不是止盈信号 —— 是否离场由你判断。参考、非指令:不进排序、不进哨兵、不改去留、不加分。")
                .font(NKFont.caption).lineSpacing(2).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 9)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 13).padding(.vertical, 12)
        #if os(macOS)
        .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.cardBg))
        .overlay(RoundedRectangle(cornerRadius: NKRadius.inner).stroke(NK.hairline, lineWidth: 0.5))
        #else
        .background(RoundedRectangle(cornerRadius: NKRadius.inner).fill(NK.fieldBg))
        #endif
    }

    /// 一行参考件(原型 421–430):`label 宽 106` + 值 `14/600 tabular` + 补充说明;
    /// 第 2、3 行上方有一条 `.5px` 细线。
    @ViewBuilder
    private func refLine(title: String, value: String?, clamp: String,
                         reason: String?, extra: String?, divider: Bool) -> some View {
        if divider { Divider().overlay(NK.hairline).padding(.vertical, 8) }
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    .frame(width: 106, alignment: .leading)
                if let v = value {
                    Text(v).font(NKFont.headline.monospacedDigit())
                        .foregroundStyle(NK.textPrimary)
                } else {
                    // 「这一项这次没有」——如实说原因,**不是 0、不是空白**。
                    Text(reason ?? (clamp.isEmpty ? "本次不可用" : "本次不可用(\(clamp))"))
                        .font(NKFont.callout).fontWeight(.semibold).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let e = extra, !e.isEmpty {
                    Text(e).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
        }
    }

    /// ⑦-K7 标注件。`text` **已含「参考、非指令」后缀,不改写、不截断**;
    /// `tagsAbsent`(判不了的码)与「判过没命中」是两回事,⛔ 不合并成"没有标注"。
    @ViewBuilder
    private var tagsBlock: some View {
        if !member.tags.isEmpty || !member.tagsAbsent.isEmpty {
            VStack(alignment: .leading, spacing: 4) {
                Text("成员观察").nkLabel().foregroundStyle(NK.textTertiary)
                ForEach(member.tags) { t in
                    HStack(alignment: .top, spacing: 5) {
                        NKChip(text: t.label, tone: t.axisTone)
                        Text(t.text).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !member.tagsAbsent.isEmpty {
                    let labels = member.tagAbsences.map(\.label).filter { !$0.isEmpty }
                    Text(labels.isEmpty
                         ? "部分标注暂无法判断（数据缺失，不代表未命中）"
                         : "暂无法判断：\(labels.joined(separator: "、"))（数据缺失，不代表未命中）")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 原型 439–442 行:**主操作在左**(蓝实底「买入补录」)、次操作在右(描边「信息卡」),
    /// 两枚都是 `padding:7px 14px; radius:8; 12px/600`。⛔ 不是一左一右分居两端的纯文字链接。
    private var actionRow: some View {
        HStack(spacing: 9) {
            // 🔴 V2.5.0 S1:「买入补录」按钮**已删除** —— 持仓台账整块下线(裁定 11),
            // 补录的落点(`POST /positions`)已不存在。⛔ 不留一个点了没反应的按钮。
            Button {
                model.openInfoCard(tradeDate: tradeDate, code: member.tsCode, name: member.name)
            } label: {
                Text("信息卡").font(NKFont.callout).fontWeight(.semibold)
                    .foregroundStyle(NK.textSecondary)
                    .padding(.horizontal, 14).padding(.vertical, 7)
                    .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                        .stroke(NK.textSecondary.opacity(0.36), lineWidth: 0.5))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Spacer(minLength: 0)
        }
    }
}

/// 原始读数网格(**等宽键名 + 等宽数值**)。键是服务端语义标识符,
/// ⛔ 客户端不改名、不重算、不猜含义;顺序按字典序**确定性**排列。
struct NKMetricsGrid: View {
    let value: NKJSON

    /// 🔴 **V2.4.0 P1.3:比较域五字段⛔ 不进读数宫格** —— 它们不是"读数"而是"这次拿它
    /// 跟谁比"的元数据(由 `NKMemberCard` 单独一行渲染)。尤其 `peer_codes` 是个数组,
    /// 塞进 `lineLimit(1)` 的格子里只会被截成一串看不懂的碎码。
    private static let excludedKeys: Set<String> = [
        "comparison_domain", "comparison_domain_key", "peer_codes", "peer_count",
        "domain_fallback_reason",
    ]

    private var keys: [String] { value.sortedKeys.filter { !Self.excludedKeys.contains($0) } }

    #if os(macOS)
    private let columns = Array(repeating: GridItem(.flexible(), alignment: .leading), count: 4)
    #else
    private let columns = Array(repeating: GridItem(.flexible(), alignment: .leading), count: 2)
    #endif

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: NKSpace.denseGap) {
            ForEach(keys, id: \.self) { k in
                VStack(alignment: .leading, spacing: 0) {
                    Text(k).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        .lineLimit(1).minimumScaleFactor(0.8)
                    Text(value.objectValue?[k]?.displayText ?? "—")
                        .font(NKFont.monoValue).foregroundStyle(NK.textPrimary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
