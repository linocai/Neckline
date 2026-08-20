//
//  StockDetailView.swift
//  Neckline — 🔴 **个股详情**(选股板块;取代已删的 `BasketCardView` 与 `InfoCardView`)。
//
//  §5.11:个股详情 = **解释层资料 + 日K 评价 + 完整预案 + 预案修改入口**。
//
//  **诚实披露纪律**:
//   1. **三段各自可能缺席,各自如实标**:
//      · 资料 `nil` = 那天解释层没跑过 / 这一只没跑成 —— ⛔ 别显示成「这只票没什么可说的」;
//      · 预案 `nil` = 那天没给这一只冻预案 → **明早核对不了它**;
//      · 消息面 `unverified` = **没查成** —— ⛔ 不许显示成「无异常」。
//   2. **上方机械空间(机械、排序用)≠ 第一压力位(LLM、预案用)**(裁定 1)——
//      本页刻意把两者**分成两块**呈现,名字各写各的,⛔ 永不互相顶替。
//   3. **预案是 append-only 的**:改一次 = 新版本,老版本一个字不动 —— 版本历史看得见。
//   4. **骨架不可改**:用户能改的是方括号里的**数**,不是「哪个量跟谁比」(K9 §6.4)。
//      要填哪几个数由**服务端下发**(`playbookSlots`),⛔ 客户端不硬编一份键表。
//

import SwiftUI

struct StockDetailView: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(iOS)
        NavigationStack {
            ScrollView { content.padding(NKSpace.pagePad) }
                .background(NK.pageBgIOS)
                .navigationTitle(titleText)
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("关闭") { model.dismissStockDetail() }
                    }
                }
        }
        #else
        content
        #endif
    }

    private var titleText: String {
        model.stockDetail?.entry.name ?? model.stockDetailCode ?? "个股详情"
    }

    @ViewBuilder
    private var content: some View {
        VStack(alignment: .leading, spacing: NKSpace.cardGap) {
            if model.stockDetailLoading && model.stockDetail == nil {
                ProgressView().frame(maxWidth: .infinity).padding(.vertical, 40)
            } else if let err = model.stockDetailError {
                NKEmptyState(title: "没取到这一只的详情", subtitle: err,
                             systemImage: "exclamationmark.triangle")
            } else if let detail = model.stockDetail {
                identityCard(detail)
                explainCard(detail)
                playbookCard(detail)
                versionsCard(detail)
            } else {
                NKDetailPlaceholderCompat(title: "选一只票来看")
            }
        }
        .sheet(isPresented: Binding(get: { model.showPlaybookEditor },
                                    set: { model.showPlaybookEditor = $0 })) {
            PlaybookEditorSheet(model: model)
        }
    }

    // MARK: - 身份 + 形态标注 + 上方机械空间

    private func identityCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(d.entry.name ?? d.tsCode).font(NKFont.title3)
                        .foregroundStyle(NK.textPrimary)
                    Text(d.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                    Spacer(minLength: 6)
                    Text("第 \(d.entry.rank) 名").font(NKFont.caption.monospacedDigit())
                        .foregroundStyle(NK.textSecondary)
                }
                NKWrapRow(spacing: 5, lineSpacing: 5) {
                    ForEach(d.entry.patterns, id: \.self) { p in
                        NKChip(text: nkPatternLabel(p),
                               tone: p == d.entry.primaryPattern ? .info : .neutral,
                               filled: p == d.entry.primaryPattern)
                    }
                    NKChip(text: nkTierLabel(d.entry.tier),
                           tone: d.entry.tier == "strict" ? .good : .warn)
                    NKChip(text: nkSeatKindLabel(d.entry.seatKind), tone: .neutral)
                    if let n = d.entry.swL2Name, !n.isEmpty { NKChip(text: n, tone: .neutral) }
                }
                // 🔴 **上方机械空间单独一块**(裁定 1:它是机械排序量,不是价位)。
                if let stock = model.selection.stocks.first(where: { $0.tsCode == d.tsCode }) {
                    HStack(spacing: 6) {
                        Text("上方机械空间").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        if let pct = stock.upsideRoomMechPct {
                            Text(NKFmt.signedRatioPct(pct)).font(NKFont.monoValue)
                                .foregroundStyle(NK.textPrimary)
                            Text("(收盘价距过去 N 日最高价;**机械算出、只用于排序**)")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        } else {
                            Text("本形态不看这一项(K9 §3.3 / §3.5 的强度性里没有它)")
                                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        }
                    }
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 解释层资料 + 日K 评价

    private func explainCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                NKSectionHeader(title: "解释层资料")
                if let e = d.explain {
                    if !e.llmOk {
                        // ⛔ 「跑了但没跑成」不许显示成空白 —— 空白读起来像「没什么可说的」。
                        Text("⚠ 这一只的资料聚合**没跑成** —— 下面的内容可能不全。")
                            .font(NKFont.callout).foregroundStyle(NK.amber)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(e.profileRows, id: \.label) { row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.label).font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            Text(row.text).font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let k = e.klineComment, !k.isEmpty {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("日K 形态评价").font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                            Text(k).font(NKFont.body).foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    // 🔴 消息面三态。`unverified` 单独占一格,⛔ 不许折成「无异常」。
                    HStack(spacing: 6) {
                        NKChip(text: nkNewsStateLabel(e.newsState),
                               tone: nkNewsStateTone(e.newsState))
                        if let c = e.newsCategory, !c.isEmpty {
                            NKChip(text: c, tone: .bad)
                        }
                    }
                    if e.newsState == "unverified" {
                        Text("⚠ 「未核实」= **这次没查成**(没有 provider / 调用失败 / 模型没按格式收尾)"
                             + " —— 它既不是「查过了、干净」,也不是「有问题」。")
                            .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let obj = e.news.objectValue, !obj.isEmpty {
                        NKDisclosure(summary: "消息面检索留痕 · \(obj.count) 项") {
                            NKJSONTable(value: e.news)
                        }
                    }
                    // 🔴 LLM 产出与硬纪律之间的那条线(§2.8 红线,每处出现都要带)。
                    NKReferenceNote(text: "解释层资料是 LLM 产出 · 参考、非指令 · 不进排序、不改去留")
                } else {
                    Text("那天解释层没跑过这一只 —— ⚠ 这**不是**「这只票没什么可说的」。")
                        .font(NKFont.body).foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - 完整预案 + 修改入口

    private func playbookCard(_ d: K9StockDetail) -> some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    NKSectionHeader(title: "次日预案")
                    Spacer()
                    if !d.playbookSlots.isEmpty {
                        Button("修改预案") { model.beginPlaybookEdit() }
                            .buttonStyle(.plain)
                            .font(NKFont.callout).foregroundStyle(NK.accent)
                    }
                }
                if let pb = d.playbook {
                    HStack(spacing: 8) {
                        NKChip(text: "v\(pb.version)", tone: .info)
                        NKChip(text: pb.isUserEdited ? "我改过" : "预案层填的",
                               tone: pb.isUserEdited ? .good : .neutral)
                        if !pb.filledAt.isEmpty {
                            Text(pb.filledAt).font(NKFont.caption.monospacedDigit())
                                .foregroundStyle(NK.textTertiary)
                        }
                    }
                    // 🔴 三个价位 —— **LLM 判断**(裁定 1:与上方机械空间永不互顶)。
                    PriceLadder(levels: pb.levels)
                    branchBlock("成立", pb.confirmBranch, .good)
                    branchBlock("放弃", pb.rejectBranch, .bad)
                    Text("其余:\(pb.defaultBranch)")
                        .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    Text("⚠ **骨架是机械的**:哪个量跟谁比由形态决定,你能改的是方括号里的数。"
                         + "求值在服务端、只在 D1 那两拍发生 —— 界面不替系统判。")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("那天没给这一只冻预案 —— 🔴 **明早核对不了它**。")
                        .font(NKFont.body).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    @ViewBuilder
    private func branchBlock(_ name: String, _ branch: PlaybookBranch?, _ tone: NKAxisTone) -> some View {
        if let b = branch, !b.all.isEmpty {
            VStack(alignment: .leading, spacing: 3) {
                Text(name).font(NKFont.callout).fontWeight(.semibold).foregroundStyle(tone.color)
                ForEach(b.all) { cond in
                    Text("· \(cond.text)").font(NKFont.callout.monospacedDigit())
                        .foregroundStyle(NK.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    /// 全部版本(升序)。**append-only 的证据**:改过几次、每次改了什么,这里看得见。
    @ViewBuilder
    private func versionsCard(_ d: K9StockDetail) -> some View {
        if d.playbookVersions.count > 1 {
            NKCard {
                VStack(alignment: .leading, spacing: 8) {
                    NKSectionHeader(title: "预案版本历史",
                                    trailing: "append-only · 老版本一个字不动")
                    ForEach(d.playbookVersions, id: \.version) { pb in
                        HStack(spacing: 8) {
                            NKChip(text: "v\(pb.version)",
                                   tone: pb.version == d.playbook?.version ? .info : .neutral)
                            Text(pb.isUserEdited ? "我改的" : "预案层填的")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                            Spacer(minLength: 6)
                            Text("压 \(NKFmt.price(pb.levels.firstResistance)) / 失 \(NKFmt.price(pb.levels.invalidation))")
                                .font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - 三个价位的刻度(⛔ 只是版式,不代表任何概率、不构成任何建议)

/// 失效位 → 第一压力位 → 第二压力位 的线性刻度。
///
/// 🔴 **这三个是 LLM 判断的价位**(K9 §6.1),⛔ 与「上方机械空间」永不互相顶替(裁定 1)。
/// ⚠ 刻度只回答「三个位相对在哪」——⛔ 不画概率、不画建议、不画"应该"。
struct PriceLadder: View {
    let levels: PlaybookLevels

    private var domain: (lo: Double, hi: Double) {
        let vs = [levels.invalidation, levels.firstResistance, levels.secondResistance]
        let lo = vs.min() ?? 0, hi = vs.max() ?? 1
        let pad = max(hi - lo, 0.0001) * 0.12
        return (lo - pad, hi + pad)
    }

    private func x(_ v: Double, width: CGFloat) -> CGFloat {
        let d = domain
        let t = (v - d.lo) / max(d.hi - d.lo, 0.0001)
        return width * CGFloat(min(max(t, 0), 1))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            GeometryReader { geo in
                let w = geo.size.width
                ZStack(alignment: .topLeading) {
                    Capsule().fill(NK.hairline.opacity(0.7)).frame(width: w, height: 5)
                        .offset(y: 8)
                    tick(x(levels.invalidation, width: w), NK.down)
                    tick(x(levels.firstResistance, width: w), NK.up)
                    tick(x(levels.secondResistance, width: w), NK.textTertiary)
                }
                .frame(width: w, height: 22, alignment: .topLeading)
            }
            .frame(height: 22)
            HStack(spacing: 14) {
                legend("失效位", levels.invalidation, NK.down)
                legend("第一压力位", levels.firstResistance, NK.up)
                legend("第二压力位", levels.secondResistance, NK.textTertiary)
            }
        }
    }

    private func tick(_ cx: CGFloat, _ color: Color) -> some View {
        RoundedRectangle(cornerRadius: 1.5).fill(color)
            .frame(width: 3, height: 17).offset(x: cx - 1.5, y: 2)
    }

    private func legend(_ title: String, _ v: Double, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textTertiary)
            Text(NKFmt.price(v)).font(NKFont.monoValue).foregroundStyle(color)
        }
    }
}

// MARK: - 预案修改弹层

/// 🔴 **要填哪几个数由服务端下发**(`playbookSlots`)—— 客户端⛔ 不硬编键表。
/// 🔴 **append-only**:提交 = 存一个新版本,原冻结版本一个字不改。
/// ⚠ **⛔ 不替用户补 0**:没填成数字的项当场拦下来说清是哪几项,
/// 补一个 0 发出去等于把一个他没填的数冻进明天的核对条件。
struct PlaybookEditorSheet: View {
    @Bindable var model: AppModel

    private var slots: [PlaybookSlot] { model.stockDetail?.playbookSlots ?? [] }

    var body: some View {
        NKSheetShell(title: "修改预案",
                     primaryTitle: "存新版",
                     primaryDisabled: slots.isEmpty || model.playbookSubmitting,
                     onCancel: { model.showPlaybookEditor = false },
                     onPrimary: { Task { await model.submitPlaybookEdit() } }) {
            if slots.isEmpty {
                Text("这只票的形态没有可改的数值位。")
                    .font(NKFont.body).foregroundStyle(NK.textSecondary)
            } else {
                Text("🔴 **骨架不可改**:哪个量跟谁比由形态决定(K9 §6.3)。"
                     + "你改的是方括号里的**数**;存下去会成为**新的一版**,原版本一个字不动。")
                    .font(NKFont.callout).foregroundStyle(NK.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                ForEach(slots) { slot in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Text(slot.label).font(NKFont.callout).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            if !slot.unit.isEmpty {
                                Text(slot.unit).font(NKFont.caption)
                                    .foregroundStyle(NK.textTertiary)
                            }
                        }
                        TextField(slot.key, text: Binding(
                            get: { model.playbookDraft[slot.key] ?? "" },
                            set: { model.playbookDraft[slot.key] = $0 }))
                            .textFieldStyle(.roundedBorder)
                            .font(NKFont.body.monospacedDigit())
                        if !slot.hint.isEmpty {
                            Text(slot.hint).font(NKFont.caption)
                                .foregroundStyle(NK.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                Text("⚠ 每一项都要填成数字 —— 服务端**不接受**缺键或多键(会 422),"
                     + "界面也**不替你补 0**。")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        #if os(macOS)
        .frame(minWidth: 460, minHeight: 420)
        #endif
    }
}

/// macOS 详情栏的空态在 `RootView` 里(`NKDetailPlaceholder`);iOS 没有那个容器,
/// 这里给一个双端都能用的薄壳,⛔ 不为一句空态在两处各写一份。
struct NKDetailPlaceholderCompat: View {
    let title: String
    var body: some View {
        NKEmptyState(title: title, systemImage: "sidebar.left")
    }
}
