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

    private var lights: some View {
        HStack(spacing: 7) {
            ForEach(gates.lights) { l in
                HStack(spacing: 3) {
                    GateDot(kind: l.kind, tone: l.tone)
                    Text(l.label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

/// 7px 灯点。**机械关方角 + 外环 / 证据关圆点** —— 二分在最小尺寸下的唯一载体。
struct GateDot: View {
    let kind: NKGateKind
    let tone: NKAxisTone

    var body: some View {
        Group {
            if kind == .mechanical {
                RoundedRectangle(cornerRadius: 1, style: .continuous)
                    .fill(tone.color)
                    .frame(width: 7, height: 7)
                    .overlay(
                        RoundedRectangle(cornerRadius: 2.4, style: .continuous)
                            .stroke(tone.color.opacity(0.5), lineWidth: 1)
                            .frame(width: 11, height: 11)
                    )
                    .frame(width: 11, height: 11)
            } else {
                Circle().fill(tone.color).frame(width: 7, height: 7)
                    .frame(width: 11, height: 11)
            }
        }
    }
}

// MARK: - 宫格(详情栏:macOS 6 列 / iOS 3×2)

struct GateGrid: View {
    let gates: BasketGates

    #if os(macOS)
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 6), count: 6)
    #else
    private let columns = Array(repeating: GridItem(.flexible(), spacing: 6), count: 3)
    #endif

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("六关判定").nkLabel().foregroundStyle(NK.textTertiary)
                if let n = gates.evidenceDegrades, n > 0 {
                    Text("证据关累计降 \(n) 档").font(NKFont.caption).foregroundStyle(NK.amber)
                }
                Spacer(minLength: 0)
            }
            if gates.available {
                LazyVGrid(columns: columns, spacing: 6) {
                    ForEach(gates.lights) { GateCell(light: $0) }
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

/// 单格。机械关:实心 1.5px 边框 + 底色 + 右上「硬」角标;证据关:.5px 描边。
private struct GateCell: View {
    let light: BasketGates.Light

    private var isMech: Bool { light.kind == .mechanical }
    /// 快照里没这一关 → 灰格 + 「未记录」。⛔ 绝不留白、绝不渲染成「过」。
    private var unrecorded: Bool { light.verdict == nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 3) {
                Text(light.label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                Spacer(minLength: 0)
                if isMech {
                    Text("硬").font(.system(size: 8, weight: .bold))
                        .foregroundStyle(.white)
                        .padding(.horizontal, 3).padding(.vertical, 0.5)
                        .background(RoundedRectangle(cornerRadius: 2).fill(NK.textSecondary))
                }
            }
            Text(unrecorded ? "未记录" : light.verdictLabel)
                .font(NKFont.callout)
                .fontWeight(.semibold)
                .foregroundStyle(unrecorded ? NK.textTertiary : light.tone.color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 7).padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: NKRadius.badge)
                .fill(unrecorded ? NK.chipNeutral
                      : (isMech ? light.tone.color.opacity(0.10) : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: NKRadius.badge)
                .stroke(unrecorded ? NK.hairline
                        : light.tone.color.opacity(isMech ? 0.55 : 0.30),
                        lineWidth: isMech ? 1.5 : 0.5)
        )
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
            // ⚠ 「不得进 T1」多半是某一关**判不出**,不是"被否决" —— 分开说。
            // 🔴 **是哪一关判不出,冻结卡里查不到**(见文件头),故此处只说"某一关"。
            if gates.blocksT1 {
                Text("本篮不得进 T1(多为某一关判不出 —— 判不出 ≠ 判过了,也 ≠ 拦下来)")
                    .foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
