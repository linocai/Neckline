//
//  NKGateViews.swift
//  Neckline — V2.3 视觉升级:六关灯条 + 六关宫格(规范 §05)
//
//  🔴 **二分是产品语义,不是配色偏好**(裁定 #6 / #11 / #12):
//    · **机械关**(市场 / 板块)= 读客观预计算量 → **硬否决**,不过就没了;
//    · **证据关**(驱动 / 核心 / 位置 / 证据)= LLM 组织证据 → **只降级**,
//      最坏也只是「退出正式候选、仍在 ③b 列名」。
//  两者混成一种灯 = 把「否决」与「扣分」讲成同一回事。故:
//    · **灯条**(7px 点):机械关画**方角 + 外环**,证据关画**圆点** —— 最小尺寸下也分得开;
//    · **宫格**:机械关**实心 1.5px 边框 + 底色 + 「硬」角标**,证据关 **.5px 描边**。
//
//  🔴 **`available == false` 绝不许渲染成「六关都过了」** —— 那是把「没看」讲成
//  「没有问题」(§3.8)。
//
//  ⚠⚠ **「判不出」是篮级的、不是格级的**(V2.3 施工期核实,⛔ 别照 mock 画):
//  原型在**某一格**上画了「判不出」徽标,但冻结卡 `mech_breakdown_json.gates` 里
//  **没有逐关的 `available`** —— 服务端 `gates.py` 对判不出的关发的是
//  `VERDICT_PASS + available=False + blocks_t1=True`,而 `tier.py::_gate_breakdown`
//  只把 `verdicts`(pass/degrade/reject)与**篮级** `blocks_t1` 写进快照。
//  于是「是哪一关判不出」这件事**契约里查不到**。故本组件:
//    · 格级只画 pass / degrade / reject,快照里没这一关的键 → 「未记录」灰格;
//    · 「某一关判不出」由**篮级**那句 `blocksT1` 文案承载(⛔ 不猜是哪一格)。
//  ⛔ 谁要把「判不出」画回某一格,先去服务端把逐关 `available` 落进冻结卡。
//

import SwiftUI

// MARK: - 灯条(列表行 / iOS 卡:一行 7px 点)

struct GateLightBar: View {
    let gates: BasketGates
    /// 展开态多给一行「卡在哪一关 / 降了几档」。
    var showDetail: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            if gates.available {
                lights
                if showDetail { GateFootnotes(gates: gates) }
            } else {
                Text("六关判定:本份快照没有关口记录(⛔ 不等于六关都过了)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 🔴 **V2.3.1 批 2:六个点 + 一句话,⛔ 不是六个点各挂一个关名**(macOS 原型
    /// 128–135 行):列表行里六个关名一字排开会把行撑满、把右边的验证徽标与成员数挤掉,
    /// 而关名在**详情栏的宫格里**本来就有。原型给的是 `gap:3` 的六个 7px 点 +
    /// 一句 `10.5px rgba(60,60,67,.40)` 的摘要(「六关 · 核心关降级」)。
    private var lights: some View {
        HStack(spacing: 8) {                        // 原型 127 行 gap:8(点组与摘要之间)
            HStack(spacing: 3) {                    // 原型 128 行 gap:3(点与点之间)
                ForEach(gates.lights) { l in
                    GateDot(kind: l.kind, tone: l.tone)
                }
            }
            Text(summary).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .lineLimit(1)
        }
    }

    /// 摘要一句。🔴 **「不得进 T1」时⛔ 不点名是哪一关判不出** —— 冻结卡里查不到
    /// (见文件头);原型那句「六关 · 位置关判不出」是 mock 才有的信息。
    private var summary: String {
        if let g = gates.blockedGate {
            let verdict = gates.verdicts[g].map(nkGateVerdictLabel) ?? ""
            return "六关 · \(nkGateLabel(g))\(verdict)"
        }
        if gates.blocksT1 { return "六关 · 不得进 T1(某一关判不出)" }
        return "六关 · 全过"
    }
}

/// 7px 灯点。**机械关方角 + 外环 / 证据关圆点** —— 二分在最小尺寸下的唯一载体。
///
/// ⚠ **外环用 `overlay` 而不是把 `frame` 撑大**(V2.3.1 批 2 对齐原型):HTML 那边是
/// `box-shadow:0 0 0 1.5px`,**不占布局**,所以六个点仍然是 7px + gap 3;V2.3.0 把
/// 每颗撑到 11×11 参与布局,点组整体宽了近一半。
struct GateDot: View {
    let kind: NKGateKind
    let tone: NKAxisTone

    /// 未记录(`.neutral`)那格原型是 `rgba(60,60,67,.30)`,就近对齐 `textTertiary`(.40)。
    private var color: Color { tone == .neutral ? NK.textTertiary : tone.color }

    var body: some View {
        Group {
            if kind == .mechanical {
                RoundedRectangle(cornerRadius: 2, style: .continuous)
                    .fill(color)
                    .frame(width: 7, height: 7)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2.75, style: .continuous)
                            .stroke(color.opacity(0.30), lineWidth: 1.5)
                            .frame(width: 8.5, height: 8.5)
                    )
            } else {
                Circle().fill(color).frame(width: 7, height: 7)
            }
        }
        .frame(width: 7, height: 7)
    }
}

// MARK: - 宫格(详情栏:macOS 6 列 / iOS 3×2)

struct GateGrid: View {
    let gates: BasketGates

    // 原型 294 行 `grid-template-columns:repeat(6,1fr); gap:8`(iOS 窄栏放不下 6 列 → 3 列)。
    #if os(macOS)
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 6)
    #else
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 3)
    #endif

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {   // 原型 291 行卡头 margin-bottom:12
            HStack(spacing: 8) {
                Text("六关判定").nkLabel().foregroundStyle(NK.textTertiary)
                Spacer(minLength: 0)
                // 原型 293 行:降档提示在**右端**,不是紧跟标题(左标题 / 右状态)。
                if let n = gates.evidenceDegrades, n > 0 {
                    Text("证据关累计降 \(n) 档").font(NKFont.caption).fontWeight(.semibold)
                        .foregroundStyle(NK.amber)
                }
            }
            if gates.available {
                LazyVGrid(columns: columns, spacing: 8) {
                    ForEach(gates.lights) { GateCell(light: $0) }
                }
                // 🔴 **「不得进 T1」是篮级结论,原型把它画成宫格下面一块明面上的琥珀条**
                // (macOS 原型 692 行),⛔ 不收进折叠区 —— 它解释的正是这张宫格为什么
                // 「看起来都过了却仍然进不了 T1」,藏起来就等于没说。
                // ⚠ 是**哪一关**判不出,冻结卡里查不到(见文件头),故只说"某一关"。
                if gates.blocksT1 {
                    Text("本篮不得进 T1(多为某一关判不出 —— 判不出 ≠ 判过了,也 ≠ 拦下来)")
                        .font(NKFont.callout).lineSpacing(3).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 12).padding(.vertical, 10)
                        .background(RoundedRectangle(cornerRadius: NKRadius.control)
                            .fill(NK.amber.opacity(0.06)))
                        .overlay(RoundedRectangle(cornerRadius: NKRadius.control)
                            .stroke(NK.amber.opacity(0.20), lineWidth: 0.5))
                }
                GateFootnotes(gates: gates)
            } else {
                Text("本份快照没有关口记录(⛔ 不等于六关都过了)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// 单格(macOS 原型 295–301 行)。
///
/// **居中三行**:关名 `11.5/600` → 结论 `14/600` → 类别角标。
/// · 机械关:`1.5px` 实边框 + 语义底色 + 白字「硬」角标(`9/700`,底 `rgba(60,60,67,.50)`,
///   **radius 3** —— §〇d 结转第 4 条点名的那处,⛔ 不是 `NKRadius.badge`);
/// · 证据关:`.5px` 描边 + 无底色 + 灰「证据」角标(`9px`,纯文字不带底)。
///
/// ⚠ V2.3.0 是**左对齐两行**、格圆角吃 `badge`(4)、机械关的「硬」挤在关名右边 ——
/// 与原型的居中三行完全不同形(§③ 必查钉子:格 `radius 9 / padding 9px 8px / 居中`)。
private struct GateCell: View {
    let light: BasketGates.Light

    private var isMech: Bool { light.kind == .mechanical }
    /// 快照里没这一关 → 灰格 + 「未记录」。⛔ 绝不留白、绝不渲染成「过」。
    private var unrecorded: Bool { light.verdict == nil }

    var body: some View {
        VStack(spacing: 0) {
            Text(light.label).font(NKFont.callout).fontWeight(.semibold)
                .foregroundStyle(NK.textPrimary)
                .lineLimit(1).minimumScaleFactor(0.85)
            Text(unrecorded ? "未记录" : light.verdictLabel)
                .font(NKFont.headline)                    // 原型 14px/600
                .foregroundStyle(unrecorded ? NK.textSecondary : light.tone.color)
                .lineLimit(1).minimumScaleFactor(0.8)
                .padding(.top, 3)
            kindTag.padding(.top, isMech ? 5 : 6)         // 原型 296 / 300 行两档 margin-top
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 8).padding(.vertical, 9)    // 原型 `padding:9px 8px`
        .background(
            RoundedRectangle(cornerRadius: NKRadius.inner)
                .fill(unrecorded ? NK.chipNeutral
                      : (isMech ? light.tone.color.opacity(0.08) : Color.clear))
        )
        .overlay(
            // 证据关描边原型是 `rgba(60,60,67,.15)`,就近对齐 `NK.hairline`(.10)——
            // §② 钉子 5 定的规矩:除列表栏底色那一枚外,其余色差一律向既有令牌收。
            RoundedRectangle(cornerRadius: NKRadius.inner)
                .stroke(unrecorded ? NK.hairline
                        : (isMech ? light.tone.color.opacity(0.55) : NK.hairline),
                        lineWidth: isMech ? 1.5 : 0.5)
        )
    }

    /// 「硬」/「证据」角标 —— 二分的**文字**载体(边框粗细是它的图形载体)。
    @ViewBuilder
    private var kindTag: some View {
        if isMech {
            Text("硬").font(.system(size: 9, weight: .bold)).foregroundStyle(.white)
                .padding(.horizontal, 4).padding(.vertical, 1)
                // 原型底色 `rgba(60,60,67,.50)` = `NK.textSecondary`(同一灰的 .55)。
                .background(RoundedRectangle(cornerRadius: NKRadius.hardTag)
                    .fill(NK.textSecondary))
        } else {
            Text("证据").font(.system(size: 9)).foregroundStyle(NK.textTertiary)
        }
    }
}

// MARK: - 灯条 / 宫格共用的脚注(**诚实披露文案一字未改**,只是收进折叠区)

struct GateFootnotes: View {
    let gates: BasketGates

    var body: some View {
        NKDisclosure(summary: footnoteSummary, tone: footnoteTone) {
            Text("实心边框 + 「硬」= 机械关(市场 / 板块),不过 = 硬否决;"
                 + "描边 = 证据关(驱动 / 核心 / 位置 / 证据),最坏只降级、仍在 ③b 列名")
                .fixedSize(horizontal: false, vertical: true)
            if let g = gates.blockedGate {
                Text("卡在:\(nkGateLabel(g))(\(nkGateKind(g).label))")
                    .fontWeight(.semibold).foregroundStyle(NK.amber)
            }
            if let n = gates.evidenceDegrades, n > 0, !gates.degradedGates.isEmpty {
                Text("证据关累计降 \(n) 档(\(gates.degradedGates.map(nkGateLabel).joined(separator: "、")))")
            }
            // ⚠ 「不得进 T1」那一句**已经画在宫格下面的琥珀条上**(`GateGrid`),
            // ⛔ 这里不再重复 —— 同一句话在一屏里出现两遍,读者会以为是两件事。
            // ⚠ 灯条(列表行)那条路上没有琥珀条,靠的是灯条自己的摘要「不得进 T1」。
            if gates.positionUnfit {
                Text("位置关有成员被判「不合适」(裁定 #11:退出正式候选,⛔ 非硬否决)")
            }
            if gates.coreUnfit {
                Text("核心关有成员被判「不是龙头」(裁定 #12:退出正式候选,⛔ 非硬否决)")
            }
        }
    }

    /// 收起态那一行要说清「里面是什么性质」——有降级 / 卡关 / 不得进 T1 时点名,
    /// 否则只说这是硬否决与只降级的区别说明。
    private var footnoteSummary: String {
        var parts: [String] = []
        if let g = gates.blockedGate { parts.append("卡在\(nkGateLabel(g))") }
        if gates.blocksT1 { parts.append("不得进 T1") }
        if let n = gates.evidenceDegrades, n > 0 { parts.append("证据关降 \(n) 档") }
        if parts.isEmpty { return "硬否决与只降级的区别" }
        return parts.joined(separator: " · ")
    }

    private var footnoteTone: NKAxisTone {
        if gates.blockedGate != nil || gates.blocksT1 { return .warn }
        if let n = gates.evidenceDegrades, n > 0 { return .warn }
        return .neutral
    }
}
