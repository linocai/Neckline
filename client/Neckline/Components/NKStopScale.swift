//
//  NKStopScale.swift
//  Neckline — V2.3 视觉升级:止损刻度尺(规范 §05)
//
//  🔴 **刻度尺只是版式**:位置按价格**线性映射**,⛔ 不代表任何概率、不构成任何建议。
//  它回答的唯一问题是「离止损线还有多远」—— 不看数字也知道。
//
//  🔴 **`price == 0` 是「拉不到行情」不是「跌到 0 元」**(`Position.price` 契约):
//  这种情况**不画现价刻度**、并如实说一句,⛔ 绝不把 0 映射到轨道最左端 ——
//  那会画出一条"已经跌穿一切"的假图。
//
//  ⚠ 峰值刻度**只在有回落止盈态时画**(`retraceState != nil`);没有 ≠ 峰值等于现价。
//

import SwiftUI

struct NKStopScale: View {
    let stop: Double
    let cost: Double
    /// 现价。`0` = 本次拉不到行情(见文件头),此时不画现价刻度。
    let price: Double
    /// 峰值。`nil` = 本次没有回落止盈态,**不画**这一刻度。
    var peak: Double? = nil

    private var hasPrice: Bool { price > 0 }
    /// 破线 = 现价已低于止损线(有现价才谈得上破线)。
    private var broken: Bool { hasPrice && price < stop }

    /// 线性映射的定义域。**含所有要画的刻度 + 两端留白**,保证每个刻度都落在轨道内。
    private var domain: (lo: Double, hi: Double) {
        var vs = [stop, cost]
        if hasPrice { vs.append(price) }
        if let p = peak { vs.append(p) }
        let lo = vs.min() ?? 0
        let hi = vs.max() ?? 1
        let span = max(hi - lo, 0.0001)
        let pad = span * 0.14
        return (lo - pad, hi + pad)
    }

    private func x(_ v: Double, width: CGFloat) -> CGFloat {
        let d = domain
        let t = (v - d.lo) / max(d.hi - d.lo, 0.0001)
        return width * CGFloat(min(max(t, 0), 1))
    }

    private let trackHeight: CGFloat = 6
    private let tallTick: CGFloat = 18
    private let shortTick: CGFloat = 12

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                let w = geo.size.width
                ZStack(alignment: .leading) {
                    // 轨道底:破线时整条改红色浅底,否则中性底。
                    Capsule()
                        .fill(broken ? NK.down.opacity(0.14) : NK.hairline.opacity(0.7))
                        .frame(height: trackHeight)

                    // **安全区**:止损线 → 右端的涨色浅渐变。破线时不画(此刻没有安全区)。
                    if !broken {
                        let sx = x(stop, width: w)
                        LinearGradient(
                            colors: [NK.up.opacity(0.28), NK.up.opacity(0.06)],
                            startPoint: .leading, endPoint: .trailing
                        )
                        .frame(width: max(w - sx, 0), height: trackHeight)
                        .clipShape(Capsule())
                        .offset(x: sx)
                    }

                    // 刻度(顺序 = 画的先后,后画的压在上面;止损与现价最高)。
                    if let p = peak {
                        tick(at: x(p, width: w), color: NK.textTertiary, width: 2, height: shortTick)
                    }
                    tick(at: x(cost, width: w), color: NK.textTertiary, width: 2, height: shortTick)
                    tick(at: x(stop, width: w), color: NK.down, width: 3, height: tallTick)
                    if hasPrice {
                        tick(at: x(price, width: w), color: priceColor, width: 3, height: tallTick)
                    }
                }
                .frame(height: tallTick, alignment: .center)
            }
            .frame(height: tallTick)

            legend
        }
        .padding(.vertical, 2)
    }

    private var priceColor: Color {
        if broken { return NK.down }
        return price >= cost ? NK.up : NK.amber
    }

    private func tick(at cx: CGFloat, color: Color, width: CGFloat, height: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: width / 2)
            .fill(color)
            .frame(width: width, height: height)
            // `cx` 是刻度中心,减半个线宽才对齐。
            .offset(x: cx - width / 2)
    }

    /// 四项读数一行(**固定语义顺序**:止损 / 成本 / 现价 / 峰值),各自着刻度色。
    /// ⚠ 刻度标签**不跟着 x 位置浮动** —— 价格挨得近时浮动标签会互相压住,
    /// 而"看不清哪个数是哪个"比"标签没对齐刻度"严重得多。
    private var legend: some View {
        HStack(alignment: .top, spacing: 14) {
            legendItem("止损", NKFmt.price(stop), NK.down)
            legendItem("成本", NKFmt.price(cost), NK.textSecondary)
            if hasPrice {
                legendItem("现价", NKFmt.price(price), priceColor)
            } else {
                legendItem("现价", "本次拉不到", NK.amber)
            }
            if let p = peak {
                legendItem("峰值", NKFmt.price(p), NK.textSecondary)
            }
            Spacer(minLength: 0)
        }
    }

    private func legendItem(_ title: String, _ value: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
            Text(value).font(NKFont.callout.monospacedDigit()).foregroundStyle(color)
        }
    }
}

/// 刻度尺 + 「纪律位置」标题的成卡形态。**破线时整卡 1px `NK.down` 描边** ——
/// 这是持仓页上最高优先级的视觉,⛔ 别弱化成一句文字。
struct NKStopScaleCard<Footer: View>: View {
    let stop: Double
    let cost: Double
    let price: Double
    var peak: Double? = nil
    /// 卡底部的补充读数(距止损线 / 自峰值回落 / 回落止盈线 / 占总仓),由调用方给。
    @ViewBuilder var footer: Footer

    private var broken: Bool { price > 0 && price < stop }

    var body: some View {
        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
            Text("纪律位置").nkLabel().foregroundStyle(NK.textTertiary)
            NKStopScale(stop: stop, cost: cost, price: price, peak: peak)
            footer
        }
        .padding(NKSpace.cardPad)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(
            RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(broken ? NK.down : NK.hairline, lineWidth: broken ? 1 : 0.5)
        )
    }
}
