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

    private var plans: [PositionPlan] { model.positionPlans[position.id] ?? [] }
    private var latest: PositionPlan? { model.latestPlan(positionId: position.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if let plan = latest {
                detail(plan)
            } else if model.positionPlans[position.id] != nil {
                // 🔴 **拉回来了、就是零行** ≠ 还没拉到:前者是"这笔仓没有计划记录"
                //(独立买入 / 建于 ⑩ 之前),后者是"不知道"。⛔ 两句话不许合并。
                Text("这笔仓没有计划记录 —— 独立买入,或建于 ⑩「计划继承」之前(不是读取失败)")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("持仓计划读取中 —— 还没拉到这笔仓的计划版本")
                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
        .task(id: position.id) { await model.loadPositionPlans(positionId: position.id) }
    }

    /// 原型 1048–1050 行:弱标题 + `v2` 版本徽标(`10/600`),**常开**(⛔ V2.3.0 那种
    /// 「点一下才展开」不是原型的样子 —— 建仓区间 / 最高追价这三格是这一屏的正文)。
    private var header: some View {
        HStack(spacing: 8) {
            Text(headerTitle).nkLabel().foregroundStyle(NK.textTertiary)
            if let v = latest?.version, plans.count > 1 || v > 1 { NKChip(text: "v\(v)") }
            Spacer(minLength: 6)
        }
    }

    private var headerTitle: String {
        guard let plan = latest else { return "持仓计划" }
        if !plan.available { return "持仓计划 · \(plan.unavailableText ?? "无可继承内容")" }
        return "持仓计划 · 继承自 D0 篮子卡"
    }

    @ViewBuilder
    private func detail(_ plan: PositionPlan) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            if !plan.available {
                // **合法结果**(独立买入 / 卡未就绪),行照落 —— ⛔ 不省略整条记录。
                Text(plan.unavailableText ?? "这笔仓没有可继承的计划内容")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
            } else {
                // 原型 1051–1054 行:**三列等宽网格**(`gap:14`),
                // 名 `10.5/700 .40` 在上、值 `14/600` 在下(`margin-top:3`)。
                HStack(alignment: .top, spacing: 14) {
                    refCell("建仓观察区间", plan.entryZone?.rangeText, plan.entryZoneClamp)
                    refCell("最高追价", plan.maxChase.map { "¥\(NKFmt.price($0))" },
                            plan.maxChaseClamp)
                    // ⛔ **不许写成「止盈线」**(§2.8-C 语义红线)—— 但标题本身**不带括号注**
                    // (macOS 原型 1054 行,同 `NKMemberCard` 篮子卡侧的先例):「不是止盈线」
                    // 这句整话就在下面那个披露区里(原型 1069 行同位),标题里再说一遍
                    // 会把三列表头挤成两行。
                    refCell("离场参考区间", plan.exitReference?.rangeText,
                            plan.exitReferenceClamp)
                }
                if let d = plan.driver, !d.isEmpty {
                    piece("共同驱动", d)
                }
                if !plan.risks.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("主要风险").nkLabel().foregroundStyle(NK.textTertiary)
                        ForEach(plan.risks, id: \.self) { r in
                            Text("· \(r)").font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
            // 原型 1055 行:一条 `.5px` 上边线,下面是那个开关。
            VStack(alignment: .leading, spacing: 0) {
                Rectangle().fill(NK.hairline.opacity(0.8)).frame(height: 0.5)
                    .padding(.bottom, 12)
                exitReferenceToggle(plan)
            }
            // 原型 1062–1073 行:「披露 · 参考、非指令」折叠,展开是三句 `11 .55`。
            NKDisclosure(summary: "参考、非指令") {
                Text("参考、非指令 · 不进排序、不进哨兵、不改去留、不加分")
                    .fixedSize(horizontal: false, vertical: true)
                Text("离场参考是你计划里的参考位,**不是止盈线** —— 纪律仍是回落止盈,是否离场由你判断。")
                    .fixedSize(horizontal: false, vertical: true)
                Text("计划与实际偏离只是提示,**不质问、不阻断、不进任何判定**。")
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// 三列网格里的一格。⛔ 值为 nil 时**不许显示成 0 或空白** —— 说清是被哪一关夹掉的。
    ///
    /// 🔴 **`clamp` 是服务端的英文机器码,⛔ 不许当中文正文印出来**(V2.3.1 批 3 复核逮到,
    /// 硬伤 2「界面上直接出现服务端英文常量」的**第八处**):`plan_json.*_clamp` 的取值域是
    /// `absent` / `rejected_out_of_limit` / `rejected_malformed` / `rejected_no_limit` /
    /// `rejected_not_above_close` / `rejected_no_close`(唯一源
    /// `neckline/selection/basket_card.py:115-128`)—— 原写法 `本次不可用(\(clamp))` 在**生产
    /// 数据**上会印成「本次不可用(rejected_not_above_close)」。⚠ 演示库把这一格喂成了中文
    /// 「夹逼拒收」,**实拍因此看不出这个 bug**。
    ///
    /// **为什么不在客户端补一张中文换算表**(与 `nkBoardLabel` 那一族的先例刻意不同):
    /// 这几个码的人读文案**服务端已经有唯一源**(`basket_card.clamp_reason_text`,并且已经
    /// 随篮子卡下发成 `*UnavailableReason` —— `NKMemberCard` 用的就是它);缺的只是
    /// `positions_entry._member_plan_fields()` **没把 `*_unavailable_reason` 一起搬进
    /// `plan_json`**。`exit_reference_arm_note` 的 docstring 已经把规矩写死:
    /// 「**单一源** …… ⛔ 不由客户端/渲染层各自拍文案」。故本处的正确解法是**服务端补发**
    /// (已登记,本批边界内不碰 `neckline/**`);客户端这一版只做到「**不把机器码当人话讲**」——
    /// 正文说「本次不可用」,机器码降到等宽灰字(`monoKey` 那一档答的就是"这是机器标识符")。
    /// ⛔ 不许直接把它藏掉:藏掉就查不出是被哪一关夹的。
    @ViewBuilder
    private func refCell(_ title: String, _ value: String?, _ clamp: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).nkLabel().foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            if let v = value {
                Text(v).font(NKFont.headline.monospacedDigit()).foregroundStyle(NK.textPrimary)
            } else {
                // 原型 1054 行第三格:值档与另外两格**同一档**(`14/600`,琥珀色)。
                Text("本次不可用").font(NKFont.headline).foregroundStyle(NK.amber)
                if !clamp.isEmpty {
                    Text(clamp).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// ⑪-D-D:**per-position 触达提醒开关**。
    ///
    /// 两个位刻意分开显示:
    ///  · `exitReferenceMuted` = **用户意图**(这个开关翻的就是它);
    ///  · `exitReferenceArmed` = **派生态**(服务端拿真实成交价过完机械闸算出来的)。
    /// 未武装时如实说原因(文案来自服务端 `exit_reference_armed_note` **单一源**,
    /// ⛔ 客户端不另拍一份)。
    /// 原型 1056–1061 行:开关在**左**、两行文案在右(`12.5` + `10.5 .40`),`gap:11`。
    @ViewBuilder
    private func exitReferenceToggle(_ plan: PositionPlan) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            // ⚠ 开关必须在**左**、文案在右(原型 1056–1060 行)。`Toggle` 自带的 label
            // 在 macOS 上会把开关甩到文案右边、且位置随文案宽度浮动 —— 故 `labelsHidden()`
            // 之后自己排。
            HStack(alignment: .center, spacing: 11) {
                Toggle("", isOn: Binding(
                    get: { !plan.exitReferenceMuted },
                    set: { on in
                        Task { await model.setExitReferenceMuted(positionId: position.id,
                                                                 muted: !on) }
                    }
                ))
                .toggleStyle(.switch)
                .labelsHidden()
                VStack(alignment: .leading, spacing: 1) {
                    Text("触达离场参考时通知我").font(NKFont.callout)
                        .foregroundStyle(NK.textPrimary)
                    Text("只影响这一票 · 关掉不会连坐其它持仓")
                        .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
                Spacer(minLength: 0)
            }
            if !plan.exitReferenceArmed {
                Text(plan.exitReferenceArmedNote ?? "本票的触达提醒未启用")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func piece(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).nkLabel().foregroundStyle(NK.textTertiary)
            Text(text).font(NKFont.callout).lineSpacing(2).foregroundStyle(NK.textSecondary)
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

    private var clock: TradeClock? { model.tradeClocks[position.id] }
    private var absent: Bool { model.tradeClockAbsent.contains(position.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            // 原型 1080–1094 行:一条横向 D 轴(圆点 9px + 2px 连线,末点 11px 带光晕)。
            // 🔴 **口径是本项目的 D 计数**(买入日 = D1,唯一源服务端 `dCount`)——
            // ⛔ 不照抄原型那个从 D0 起算的轴,那是另一套数法。
            dTimeline
            statusLine
            noteBlock
            // 原型 1095–1098 行:虚线描边的一枚按钮,宽度只到内容为止。
            noteButton
            if let c = clock, c.final != nil {
                NKAuditSection(contains: "结案八项验证(K8 §十四,只读)") {
                    NKJSONTable(value: c.final ?? .object([:]))
                }
            }
        }
        .task(id: position.id) { await model.loadTradeClock(positionId: position.id) }
    }

    /// 原型 1077–1079 行:弱标题在左、`10.5 .40` 的一句在右。
    private var header: some View {
        HStack(spacing: 8) {
            Text("交易时钟 · 只读跟踪").nkLabel().foregroundStyle(NK.textTertiary)
            if let c = clock {
                NKChip(text: c.statusLabel, tone: c.isRunning ? .warn : .neutral)
            }
            Spacer(minLength: 6)
            Text("全部离场后结案").font(NKFont.caption).foregroundStyle(NK.textTertiary)
        }
    }

    /// D 轴。⚠ 天数多时**不逐点画** —— 376 以上的详情栏也塞不下二十个点,
    /// 超过 8 天只画首尾 + 中间省略号,⛔ 不缩点距把它挤成一条虚线。
    private var dTimeline: some View {
        let n = max(position.dCount, 1)
        let days: [Int] = n <= 8 ? Array(1...n) : [1, -1, n - 1, n]
        return HStack(spacing: 0) {
            ForEach(Array(days.enumerated()), id: \.offset) { idx, d in
                if idx > 0 {
                    Rectangle().fill(connectorColor(days[idx - 1], d))
                        .frame(height: 2).padding(.bottom, 16)
                }
                if d < 0 {
                    Text("…").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                        .padding(.bottom, 16)
                } else {
                    dDot(d, isLast: d == n)
                }
            }
        }
    }

    private func connectorColor(_ from: Int, _ to: Int) -> Color {
        (to == position.dCount && position.isExitDay) ? NK.amber.opacity(0.7) : NK.up
    }

    private func dDot(_ d: Int, isLast: Bool) -> some View {
        let color: Color = isLast && position.isExitDay ? NK.down : NK.up
        return VStack(spacing: 5) {
            Circle().fill(color)
                .frame(width: isLast ? 11 : 9, height: isLast ? 11 : 9)
                .overlay {
                    if isLast {
                        Circle().stroke(color.opacity(0.18), lineWidth: 3)
                            .frame(width: 17, height: 17)
                    }
                }
            Text(label(d, isLast: isLast))
                .font(NKFont.caption)
                .fontWeight(isLast ? .bold : .regular)
                .foregroundStyle(isLast && position.isExitDay ? NK.down : NK.textSecondary)
                .fixedSize()
        }
    }

    private func label(_ d: Int, isLast: Bool) -> String {
        if d == 1 { return isLast ? "D1 今天" : "D1 买入" }
        return isLast ? "D\(d) 今天" : "D\(d)"
    }

    @ViewBuilder
    private var statusLine: some View {
        if let c = clock {
            HStack(spacing: 6) {
                NKChip(text: "开仓 \(c.openedOn)")
                if let closed = c.closedOn { NKChip(text: "结案 \(closed)") }
                if let bid = c.basketId {
                    NKChip(text: "来源篮子 #\(bid)")
                } else {
                    // **合法**:非篮子来源的手动开仓。⛔ 不写成"数据缺失"。
                    NKChip(text: "非篮子来源(手动开仓)", tone: .neutral)
                }
                Spacer(minLength: 0)
            }
        } else if absent {
            Text("这笔仓没有交易时钟(**不是**读取失败):时钟只在 V2.2-④ 之后的实际买入上建立。")
                .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("读取交易时钟…").font(NKFont.caption).foregroundStyle(NK.textTertiary)
            }
        }
    }

    @ViewBuilder
    private var noteBlock: some View {
        if let c = clock {
            if c.userNotes.isEmpty {
                Text("你还没为这笔仓写过主观说明 —— 系统**不会**替你猜(§七 P3-28)")
                    .font(NKFont.caption).foregroundStyle(NK.amber)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(c.userNotes) { e in
                        HStack(alignment: .top, spacing: 6) {
                            Text(e.eventDate).font(NKFont.monoKey)
                                .foregroundStyle(NK.textTertiary)
                            Text(e.userNote ?? "").font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
            }
        }
    }

    private var noteButton: some View {
        Button { model.beginTradeClockNote(positionId: position.id) } label: {
            HStack(spacing: 6) {
                Image(systemName: "square.and.pencil").font(.system(size: 12, weight: .medium))
                Text("补一条主观说明 —— 系统不会替你写那句话")
                    .font(NKFont.callout).fontWeight(.semibold)
            }
            .foregroundStyle(NK.textSecondary)
            .padding(.horizontal, 12).padding(.vertical, 9)
            .overlay(
                RoundedRectangle(cornerRadius: NKRadius.control)
                    .strokeBorder(NK.textTertiary.opacity(0.55),
                                  style: StrokeStyle(lineWidth: 0.5, dash: [4, 3]))
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
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
                                .font(NKFont.body).fontWeight(.semibold)
                                .foregroundStyle(NK.textPrimary)
                            Text("机器能记的(价格 / 时间 / 触发的条件)它自己会记。这里写的是**只有你知道的那半份**:为什么在这个位置动手、当时在担心什么。⛔ 系统不会替你猜、也不会改写你写的话。")
                                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            TextEditor(text: $text)
                                .font(NKFont.body)
                                .frame(minHeight: 140)
                                .overlay(RoundedRectangle(cornerRadius: NKRadius.field)
                                    .stroke(over ? NK.down : NK.hairline, lineWidth: 1))
                            HStack {
                                Text("纯追加 · 不改任何既有记录")
                                    .font(NKFont.caption).foregroundStyle(NK.textTertiary)
                                Spacer()
                                Text("\(text.count)/\(nkTradeNoteMaxChars)")
                                    .font(NKFont.caption.monospacedDigit())
                                    .foregroundStyle(over ? NK.down : NK.textTertiary)
                            }
                            if over {
                                Text("超过上限了 —— 服务端会拒收(422)。⛔ 系统不会替你截断:截一半还装作收下了,那是把你写的话改掉。")
                                    .font(NKFont.caption).foregroundStyle(NK.down)
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
