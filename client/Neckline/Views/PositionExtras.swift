//
//  PositionExtras.swift
//  Neckline — 持仓卡的从属组件(V2-⑮ 换血):
//    · **计划继承卡**(⑩-B):从 D0 篮子卡继承的五项 + 版本号 + 偏离提示;
//    · **per-position「不提醒」开关**(⑪-D-D):只关这一票的触达提醒,⛔ 不连坐其它持仓;
//    · **交易时钟主观说明**(V2.2-④-B / K8 §十五):`POST /clocks/trade/{id}/note`。
//
//  🔴 **V2.2-⑤-B 删掉的**:熔断锁定横幅 `CircuitLockBanner` + 复盘解锁弹层
//  `CircuitReviewSheet` —— 熔断三件机制整体退役(用户裁定 #8),⛔ 不许接回来。
//
//  ⚠ **V2-⑮ 删掉的**:决策日志回显区 / 情景兑现勾选 / 「修订决策日志」入口 ——
//  `decision_log` 表 v2.0.0 起**停写留档**,`link`/`cancel`/`revise`/`scenario-outcome`
//  四个写端点服务端已删(⑩-C),留着这些 UI 就是**假成功面**(点了没有任何写入通道)。
//  用户的可选补充改走 `NoteSheet`(七枚标签 + 一句说明 → `user_actions`)。
//

import SwiftUI

// MARK: - ⑩-B 计划继承卡 + ⑪-D-D per-position 触达提醒开关

struct PositionPlanSection: View {
    @Bindable var model: AppModel
    let position: Position
    @State private var expanded = false

    private var plans: [PositionPlan] { model.positionPlans[position.id] ?? [] }
    private var latest: PositionPlan? { model.latestPlan(positionId: position.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            if expanded, let plan = latest {
                detail(plan)
            }
        }
        .task(id: position.id) { await model.loadPositionPlans(positionId: position.id) }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "doc.text.below.ecg").font(.system(size: 11))
                    Text(expanded ? "收起持仓计划" : headerTitle)
                        .font(.system(size: 12, weight: .medium))
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                }
            }
            .buttonStyle(.plain).foregroundStyle(NK.accent)
            Spacer()
            if plans.count > 1 { NKChip(text: "v\(latest?.version ?? plans.count)") }
        }
    }

    private var headerTitle: String {
        guard let plan = latest else { return "持仓计划(暂不可用)" }
        if !plan.available { return "持仓计划 · \(plan.unavailableText ?? "无可继承内容")" }
        if let name = plan.sourceBasketName, !name.isEmpty { return "持仓计划 · 来自「\(name)」" }
        return "持仓计划(继承自 D0 篮子卡)"
    }

    @ViewBuilder
    private func detail(_ plan: PositionPlan) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if !plan.available {
                // **合法结果**(独立买入 / 卡未就绪),行照落 —— ⛔ 不省略整条记录。
                Text(plan.unavailableText ?? "这笔仓没有可继承的计划内容")
                    .font(.system(size: 11.5)).foregroundStyle(NK.amber)
            } else {
                if let d = plan.driver, !d.isEmpty {
                    piece("共同驱动", d)
                }
                refLine("建仓观察区间", plan.entryZone?.rangeText, plan.entryZoneClamp)
                refLine("最高追价", plan.maxChase.map { "¥\(NKFmt.price($0))" }, plan.maxChaseClamp)
                // ⛔ **不许写成「止盈线」**(§2.8-C 语义红线)。
                refLine("离场参考区间(不是止盈线)", plan.exitReference?.rangeText,
                        plan.exitReferenceClamp)
                if !plan.risks.isEmpty {
                    VStack(alignment: .leading, spacing: 1) {
                        Text("主要风险").font(.system(size: 10.5, weight: .bold))
                            .foregroundStyle(NK.textTertiary)
                        ForEach(plan.risks, id: \.self) { r in
                            Text("· \(r)").font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                NKReferenceNote()
            }
            Divider().overlay(NK.hairline)
            exitReferenceToggle(plan)
        }
    }

    /// ⑪-D-D:**per-position 触达提醒开关**。
    ///
    /// 两个位刻意分开显示:
    ///  · `exitReferenceMuted` = **用户意图**(这个开关翻的就是它);
    ///  · `exitReferenceArmed` = **派生态**(服务端拿真实成交价过完机械闸算出来的)。
    /// 未武装时如实说原因(文案来自服务端 `exit_reference_armed_note` **单一源**,
    /// ⛔ 客户端不另拍一份)。
    @ViewBuilder
    private func exitReferenceToggle(_ plan: PositionPlan) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Toggle(isOn: Binding(
                get: { !plan.exitReferenceMuted },
                set: { on in
                    Task { await model.setExitReferenceMuted(positionId: position.id, muted: !on) }
                }
            )) {
                VStack(alignment: .leading, spacing: 1) {
                    Text("触达离场参考时通知我").font(.system(size: 12))
                        .foregroundStyle(NK.textPrimary)
                    Text("只影响这一票 · 关掉不会连坐其它持仓")
                        .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                }
            }
            .toggleStyle(.switch)
            if !plan.exitReferenceArmed {
                Text(plan.exitReferenceArmedNote ?? "本票的触达提醒未启用")
                    .font(.system(size: 10.5)).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text("离场参考是你计划里的参考位,**不是止盈线** —— 纪律仍是回落止盈,是否离场由你判断。")
                .font(.system(size: 10)).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func refLine(_ title: String, _ value: String?, _ clamp: String) -> some View {
        HStack(spacing: 6) {
            Text(title).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
            if let v = value {
                Text(v).font(.system(size: 12, weight: .medium).monospacedDigit())
                    .foregroundStyle(NK.textPrimary)
            } else {
                // ⛔ 不许把 nil 显示成 0 或空白。
                Text(clamp.isEmpty ? "本次不可用" : "本次不可用(\(clamp))")
                    .font(.system(size: 11.5)).foregroundStyle(NK.amber)
            }
            Spacer()
        }
    }

    private func piece(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text(text).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}


// MARK: - V2.2-④-B 交易时钟(**只读跟踪** + 唯一写入口:一条主观说明)
//
// 🔴 **交易时钟只在「实际买入」后存在**(K8 §十四 原文):这笔仓没有时钟 = 服务端
// 404 `not_found` —— 那是**合法空态**(建于 V2.2 之前的老仓 / 时钟还没建),
// ⛔ 不许渲染成错误、更不许显示成「未找到该记录」那句通用文案。
//
// 🔴 **本节零动作**:「上涨效率变化」等八项验证**只进复盘与展示**,
// ⛔ 不触发任何持仓动作、不进推送(K8 明写「保留主观判断,不设机械规则」)。

struct TradeClockSection: View {
    @Bindable var model: AppModel
    let position: Position
    /// ⚠ 初值走 QA 钩子(缺环境变量恒 `false`,正常路径逐字节不变)—— 见 `NKQA`。
    @State private var expanded = NKQA.expandDisclosures

    private var clock: TradeClock? { model.tradeClocks[position.id] }
    private var absent: Bool { model.tradeClockAbsent.contains(position.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            if expanded { detail }
        }
        .task(id: position.id) { await model.loadTradeClock(positionId: position.id) }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Button { withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "stopwatch").font(.system(size: 11))
                    Text(expanded ? "收起交易时钟" : headerTitle)
                        .font(.system(size: 12, weight: .medium))
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                }
            }
            .buttonStyle(.plain).foregroundStyle(NK.accent)
            Spacer()
            if let c = clock {
                NKChip(text: c.statusLabel, tone: c.isRunning ? .warn : .neutral)
            }
        }
    }

    private var headerTitle: String {
        if let c = clock {
            let n = c.userNotes.count
            return "交易时钟 · \(c.statusLabel)" + (n > 0 ? " · \(n) 条说明" : " · 还没写说明")
        }
        // ⚠ 「没有时钟」与「还没拉到」讲不同的话(§3.8)。
        if absent { return "交易时钟 · 这笔仓没有(建于 V2.2 之前 / 未建时钟)" }
        return "交易时钟 · 读取中"
    }

    @ViewBuilder
    private var detail: some View {
        if let c = clock {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 6) {
                    NKChip(text: "开仓 \(c.openedOn)")
                    if let closed = c.closedOn { NKChip(text: "结案 \(closed)") }
                    if let bid = c.basketId {
                        NKChip(text: "来源篮子 #\(bid)")
                    } else {
                        // **合法**:非篮子来源的手动开仓。⛔ 不写成"数据缺失"。
                        NKChip(text: "非篮子来源(手动开仓)", tone: .neutral)
                    }
                    Spacer()
                }
                if c.final == nil {
                    // 「还没结案」与「结案了但八项算不出」必须分得开(服务端 docstring)。
                    Text("还在跟踪中 · 八项结案验证要等全部离场后才有")
                        .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                } else if let f = c.final {
                    DisclosureGroup("展开结案八项验证(K8 §十四,只读)") {
                        NKJSONTable(value: f)
                    }
                    .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                }
                if c.userNotes.isEmpty {
                    Text("你还没为这笔仓写过主观说明 —— 系统**不会**替你猜(§七 P3-28)")
                        .font(.system(size: 11)).foregroundStyle(NK.amber)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(c.userNotes) { e in
                            HStack(alignment: .top, spacing: 6) {
                                Text(e.eventDate).font(.system(size: 10).monospaced())
                                    .foregroundStyle(NK.textTertiary)
                                Text(e.userNote ?? "").font(.system(size: 11.5))
                                    .foregroundStyle(NK.textSecondary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                Button { model.beginTradeClockNote(positionId: position.id) } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "square.and.pencil").font(.system(size: 11))
                        Text("补一条主观说明").font(.system(size: 12.5, weight: .semibold))
                    }
                }
                .buttonStyle(.plain).foregroundStyle(NK.accent)
            }
        } else if absent {
            Text("这笔仓没有交易时钟(**不是**读取失败):时钟只在 V2.2-④ 之后的实际买入上建立。")
                .font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("读取交易时钟…").font(.system(size: 11)).foregroundStyle(NK.textTertiary)
            }
        }
    }
}

// MARK: - 「补一条主观说明」弹层(**本版唯一新增写端点**)
//
// K8 §十五 原文:「用户只补充系统无法识别的主观原因,**每次一条简短说明**」。
// ⛔ **系统不生成、不改写、不合并**(§七 P3-28 纪律)—— 这里没有任何"帮你润色"。
// ⚠ 长度上界的**权威在服务端**(`review/trade_clock.USER_NOTE_MAX_CHARS`,超长返 422);
// `nkTradeNoteMaxChars` 只是它的镜像,用于画字数计数器,两者由 Python 守门单测钉相等。

struct TradeClockNoteSheet: View {
    @Bindable var model: AppModel
    let positionId: Int
    @State private var text = ""
    @State private var submitting = false

    private var trimmed: String { text.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var over: Bool { text.count > nkTradeNoteMaxChars }
    private var canSubmit: Bool { !trimmed.isEmpty && !over && !submitting }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.gap) {
                    NKCard {
                        VStack(alignment: .leading, spacing: 8) {
                            Text("补充系统看不到的那部分原因")
                                .font(.system(size: 13.5, weight: .semibold))
                                .foregroundStyle(NK.textPrimary)
                            Text("机器能记的(价格 / 时间 / 触发的条件)它自己会记。这里写的是**只有你知道的那半份**:为什么在这个位置动手、当时在担心什么。⛔ 系统不会替你猜、也不会改写你写的话。")
                                .font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            TextEditor(text: $text)
                                .font(.system(size: 13))
                                .frame(minHeight: 140)
                                .overlay(RoundedRectangle(cornerRadius: NKRadius.field)
                                    .stroke(over ? NK.down : NK.hairline, lineWidth: 1))
                            HStack {
                                Text("纯追加 · 不改任何既有记录")
                                    .font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                                Spacer()
                                Text("\(text.count)/\(nkTradeNoteMaxChars)")
                                    .font(.system(size: 10.5).monospacedDigit())
                                    .foregroundStyle(over ? NK.down : NK.textTertiary)
                            }
                            if over {
                                Text("超过上限了 —— 服务端会拒收(422)。⛔ 系统不会替你截断:截一半还装作收下了,那是把你写的话改掉。")
                                    .font(.system(size: 11)).foregroundStyle(NK.down)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                .padding(NKSpace.pagePad)
            }
            .navigationTitle("补一条说明")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { model.dismissModal() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("记下来") {
                        Task {
                            submitting = true
                            await model.submitTradeClockNote(positionId: positionId, note: trimmed)
                            submitting = false
                        }
                    }
                    .disabled(!canSubmit)
                }
            }
        }
    }
}
