//
//  NKStopScale.swift
//  Neckline — V2.3 视觉升级:止损刻度尺(规范 §05;V2.3.1 批 3 按原型 1012–1029 行改齐)
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
//  ⚠ **V2.3.1 批 3:刻度标签改成跟着刻度浮动**(原型 `transform:translateX(-50%)`)——
//  V2.3.0 是底下一排固定顺序的读数(理由:价格挨得近时浮动标签会互相压住)。原型的
//  判据优先(§五 〇a「对不上时以原型为准」),但**两端夹紧**:标签中心被 clamp 在
//  `[labelHalf, w - labelHalf]` 内,否则最左的「止损」会被切掉半个字。
//

import SwiftUI

struct NKStopScale: View {
    let stop: Double
    let cost: Double
    /// 现价。`0` = 本次拉不到行情(见文件头),此时不画现价刻度。
    let price: Double
    /// 峰值。`nil` = 本次没有回落止盈态,**不画**这一刻度。
    var peak: Double? = nil
    /// 🔴 **V2.4.0 P3.1:这根刻度叫什么由调用方按**那一笔的**章程给**
    /// (`Position.stopLineShortLabel` → 「警戒线」/「止损线」)。**缺省 `"止损"` =
    /// 老口径逐字不变**,⛔ 别在组件里读章程 —— 组件拿不到、也不该拿到那条纪律。
    /// ⚠ 同一屏上这条线的称呼此前已经统一走 `stopLineShortLabel`(徽标 / 四格 / 那句
    /// 解释),**只有这把尺上的标签漏了** —— 一屏两个名字正是 V2.3.2-⑤ 实拍逮到的病。
    var stopLabel: String = "止损"

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

    // —— 原型 1013–1021 行的绝对坐标(容器高 44)——
    private let blockHeight: CGFloat = 44
    private let trackTop: CGFloat = 20
    private let trackHeight: CGFloat = 5
    /// 上排标签(止损 / 成本 / 峰值)的基线带:`top:0`,字高约 13。
    private let topLabelCenterY: CGFloat = 6.5
    /// 现价标签在**下方**(原型 `top:36`)。
    private let priceLabelCenterY: CGFloat = 41
    /// 标签半宽(clamp 与「推开多远」共用;「峰值 46.90」这类**两字 + 五字符数字**在
    /// 11px 下约 56pt 宽 → 半宽 30 带一点余量)。
    /// ⚠ **V2.4.0 P3.1**:`stopLabel` 可能变成三个字(「警戒线」),每多一个汉字就多
    /// 半个字宽(11px CJK 字宽 ≈ 字号)——**不加这一项,三字标签会与「成本」压在一起**
    /// (V2.3.1 批 7 那次「戏卒 36.94」是同一个病)。**纯版式量,⛔ 不是任何判据。**
    private var labelHalf: CGFloat { 30 + CGFloat(max(stopLabel.count - 2, 0)) * 5.5 }

    /// 🔴 **上排标签防重叠**(V2.3.1 批 7 实拍逮到:`peak == cost` 时两个标签**压在一起**,
    /// 渲染成「戏卒 36.94」这种谁也读不出的字形 —— 而 `peak == cost` 正是**买入后一路没涨过**
    /// 这一常见情形的必然结果,不是极端数据)。
    ///
    /// **做法 = 沿横轴推开,⛔ 不是丢掉一个标签**(丢了就会出现一根没有名字的刻度线),
    /// 也 ⛔ 不往上叠一层(那要改整块的高度,而高度在 `GeometryReader` 外面定死,
    /// 拿不到宽度就算不出该加多少)。按优先级 **止损 > 成本 > 峰值** 依次放,与已放的
    /// 有横向重叠就往右挪(挪不下再往左),最后仍 clamp 在轨道两端内。
    /// ⚠ 返回值顺序固定 `[止损, 成本, 峰值]`,调用点按下标取 —— 改顺序两处一起改。
    private func topLabelCenters(w: CGFloat) -> [CGFloat] {
        var wanted: [CGFloat] = [x(stop, width: w), x(cost, width: w)]
        if let p = peak { wanted.append(x(p, width: w)) }
        let span = labelHalf * 2 - 2
        var placed: [CGFloat] = []
        var out: [CGFloat] = []
        for target in wanted {
            var c = clampX(target, width: w)
            var guardCount = 0
            while let hit = placed.first(where: { abs($0 - c) < span }), guardCount < 8 {
                c = hit + span                       // 先往右让
                if c > w - labelHalf { c = hit - span }  // 右边到头了就往左
                c = clampX(c, width: w)
                guardCount += 1
            }
            placed.append(c)
            out.append(c)
        }
        return out
    }

    private func clampX(_ cx: CGFloat, width: CGFloat) -> CGFloat {
        min(max(cx, labelHalf), max(width - labelHalf, labelHalf))
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let lc = topLabelCenters(w: w)
            ZStack(alignment: .topLeading) {
                // 轨道底(原型 1014:`top:20; height:5; radius:999; rgba(60,60,67,.07)`)。
                Capsule().fill(NK.hairline.opacity(0.7))
                    .frame(width: w, height: trackHeight)
                    .offset(y: trackTop)

                if broken {
                    // 破线:轨道**浅红底**从左端铺到现价(原型 1139)—— 此刻没有安全区。
                    Capsule().fill(NK.down.opacity(0.30))
                        .frame(width: max(x(price, width: w), 0), height: trackHeight)
                        .offset(y: trackTop)
                } else {
                    // **安全区** = 止损线 → 右端,由浅到深的涨色渐变
                    // (原型 1015:`rgba(15,169,104,.25) → rgba(15,169,104,.55)`)。
                    let sx = x(stop, width: w)
                    LinearGradient(colors: [NK.up.opacity(0.25), NK.up.opacity(0.55)],
                                   startPoint: .leading, endPoint: .trailing)
                        .frame(width: max(w - sx, 0), height: trackHeight)
                        .clipShape(Capsule())
                        .offset(x: sx, y: trackTop)
                }

                // 刻度(顺序 = 画的先后,后画的压在上面;止损与现价最高)。
                // ⚠ 标签的层号来自 `topLabelLevels`,顺序**必须**与那里一致:止损 / 成本 / 峰值。
                if let p = peak {
                    tick(x(p, width: w), NK.textTertiary.opacity(0.75), w: 2, h: 15, top: 15)
                    label(lc[2], "峰值 \(NKFmt.price(p))", NK.textTertiary,
                          bold: false, y: topLabelCenterY)
                }
                tick(x(cost, width: w), NK.textTertiary, w: 2, h: 15, top: 15)
                label(lc[1], "成本 \(NKFmt.price(cost))", NK.textSecondary,
                      bold: false, y: topLabelCenterY)

                tick(x(stop, width: w), NK.down, w: 3, h: 17, top: 14)
                label(lc[0], "\(stopLabel) \(NKFmt.price(stop))", NK.down,
                      bold: true, y: topLabelCenterY)

                if hasPrice {
                    tick(x(price, width: w), priceColor, w: 3, h: 23, top: 11)
                    // 原型 1020:现价标签在**下方** `top:36`、`11/700`,与上排分开 —— 它是
                    // 这条尺上唯一"会动"的那一个(下排只有它,不参与上排的推挤)。
                    label(clampX(x(price, width: w), width: w), "现价 \(NKFmt.price(price))",
                          priceColor, bold: true, y: priceLabelCenterY)
                }
            }
            .frame(width: w, height: blockHeight, alignment: .topLeading)
        }
        .frame(height: blockHeight)
        // 原型 1012 行 `margin:0 4px` —— 两端各留 4,免得极值刻度贴着卡边。
        .padding(.horizontal, 4)
    }

    private var priceColor: Color {
        if broken { return NK.down }
        return price >= cost ? NK.up : NK.amber
    }

    private func tick(_ cx: CGFloat, _ color: Color, w: CGFloat, h: CGFloat,
                      top: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: 2)
            .fill(color)
            .frame(width: w, height: h)
            // `cx` 是刻度中心,减半个线宽才对齐。
            .offset(x: cx - w / 2, y: top)
    }

    /// 浮动刻度标签(原型 `transform:translateX(-50%)`)。
    /// ⚠ `cx` 已由 `topLabelCenters` 推挤 + clamp 过,这里不再二次夹紧。
    private func label(_ cx: CGFloat, _ text: String, _ color: Color,
                       bold: Bool, y: CGFloat) -> some View {
        Text(text)
            .font(NKFont.caption.monospacedDigit())
            .fontWeight(bold ? .bold : .regular)
            .foregroundStyle(color)
            .fixedSize()
            .frame(width: labelHalf * 2)
            .offset(x: cx - labelHalf, y: y - 7)
    }
}

/// 刻度尺 + 「纪律位置」标题的成卡形态。**破线时整卡 1px `NK.down` 描边** ——
/// 这是持仓页上最高优先级的视觉,⛔ 别弱化成一句文字。
struct NKStopScaleCard<Footer: View>: View {
    let stop: Double
    let cost: Double
    let price: Double
    var peak: Double? = nil
    /// V2.4.0 P3.1:这根红刻度的名字(缺省 `"止损"` = 老口径逐字不变),原样透传给尺。
    var stopLabel: String = "止损"
    /// 卡底部的补充读数(距止损线 / 自峰值回落),由调用方给。
    /// 🔴 原型第四格是「占总仓 35.3%」—— **本版不画**(§五 〇-4:分母
    /// `Settings.total_capital` 从未下发,客户端写死 12 万 = 造第二份事实源)。
    @ViewBuilder var footer: Footer

    private var broken: Bool { price > 0 && price < stop }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // 原型 1010 行:`11/700 ls .5 .40` + `margin-bottom:14`。
            Text("纪律位置").nkLabel().foregroundStyle(NK.textTertiary)
                .padding(.bottom, 14)
            NKStopScale(stop: stop, cost: cost, price: price, peak: peak, stopLabel: stopLabel)
            footer
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, NKSpace.cardPad).padding(.horizontal, NKSpace.cardPadH)
        .background(RoundedRectangle(cornerRadius: NKRadius.card).fill(NK.cardBg))
        .overlay(
            RoundedRectangle(cornerRadius: NKRadius.card)
                .stroke(broken ? NK.down : NK.hairline, lineWidth: broken ? 1 : 0.5)
        )
    }
}

/// 列表行里的**迷你刻度条**(原型 923–935 行)。轨道 4px、三个标记:
/// 止损左侧的红色危险段 + 成本细线 + 现价粗线。⛔ 它不是上面那把尺的缩小版 ——
/// 峰值不画、标签走行内一行(左「止损 x」/ 右「现价 x · 距止损 y」)。
struct NKStopMiniBar: View {
    let stop: Double
    let cost: Double
    let price: Double

    private var hasPrice: Bool { price > 0 }
    private var broken: Bool { hasPrice && price <= stop }

    private var domain: (lo: Double, hi: Double) {
        var vs = [stop, cost]
        if hasPrice { vs.append(price) }
        let lo = vs.min() ?? 0
        let hi = vs.max() ?? 1
        let span = max(hi - lo, 0.0001)
        let pad = span * 0.22
        return (lo - pad, hi + pad)
    }

    private func x(_ v: Double, width: CGFloat) -> CGFloat {
        let d = domain
        let t = (v - d.lo) / max(d.hi - d.lo, 0.0001)
        return width * CGFloat(min(max(t, 0), 1))
    }

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .topLeading) {
                Capsule().fill(NK.textTertiary.opacity(0.15))
                    .frame(width: w, height: 4).offset(y: 4)
                // 止损线**左侧**那一段 = 危险区(原型 925:`width:11%; #E5443B`)。
                Capsule().fill(NK.down)
                    .frame(width: max(x(stop, width: w), 0), height: 4).offset(y: 4)
                // 止损标记(原型 926:1.5px,上下各出头 3)。
                mark(x(stop, width: w), NK.textSecondary, w: 1.5, h: 10, top: 1)
                mark(x(cost, width: w), NK.textTertiary.opacity(0.75), w: 1.5, h: 10, top: 1)
                if hasPrice {
                    // 现价(原型 928:2.5px,上下各出头 4)。
                    mark(x(price, width: w), broken ? NK.down : NK.up, w: 2.5, h: 12, top: 0)
                }
            }
            .frame(width: w, height: 12, alignment: .topLeading)
        }
        .frame(height: 12)
    }

    private func mark(_ cx: CGFloat, _ color: Color, w: CGFloat, h: CGFloat,
                      top: CGFloat) -> some View {
        RoundedRectangle(cornerRadius: w / 2).fill(color)
            .frame(width: w, height: h)
            .offset(x: cx - w / 2, y: top)
    }
}
