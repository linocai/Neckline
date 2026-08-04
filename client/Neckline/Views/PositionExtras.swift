//
//  PositionExtras.swift
//  Neckline — 持仓卡的从属组件(V2-⑮ 换血):
//    · **计划继承卡**(⑩-B):从 D0 篮子卡继承的五项 + 版本号 + 偏离提示;
//    · **per-position「不提醒」开关**(⑪-D-D):只关这一票的触达提醒,⛔ 不连坐其它持仓;
//    · 熔断锁定横幅 + 复盘解锁弹层(§2.1 第 7 条,纯提醒层)。
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

// MARK: - 熔断锁定横幅(持仓板块置顶,比退潮刹车更靠前)

struct CircuitLockBanner: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.octagon.fill").font(.system(size: 16, weight: .bold))
                VStack(alignment: .leading, spacing: 3) {
                    Text("熔断中 · \(model.circuit.episode?.triggerReasonLabel ?? "")")
                        .font(.system(size: 13.5, weight: .bold))
                    Text("今日停止开新仓、次日只减不加").font(.system(size: 12)).opacity(0.9)
                    if let note = model.circuit.episode?.note, !note.isEmpty {
                        Text(note).font(.system(size: 11.5)).opacity(0.85)
                    }
                }
                Spacer()
            }
            Button {
                model.modal = .circuitReview
            } label: {
                Text("查看复盘材料并解锁 →")
                    .font(.system(size: 12.5, weight: .bold))
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Capsule().fill(Color.white.opacity(0.22)))
            }
            .buttonStyle(.plain)
        }
        .foregroundStyle(.white)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.alertGrad))
    }
}

// MARK: - 「熔断复盘」按钮先展示材料再调 `POST /circuit/unlock`

struct CircuitReviewSheet: View {
    @Bindable var model: AppModel
    @State private var submitting = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.gap) {
                    if let ep = model.circuit.episode {
                        NKCard {
                            VStack(alignment: .leading, spacing: 10) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("触发原因").font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                                    Text(ep.triggerReasonLabel).font(.system(size: 15, weight: .semibold))
                                }
                                Divider().overlay(NK.hairline)
                                row("触发交易日", ep.triggerRefDate)
                                row("触发时间", ep.triggeredAt)
                                row("判据笔数", "\(ep.basisTradesCount) 笔")
                                row("判据时窗", ep.basisWindow)
                                Divider().overlay(NK.hairline)
                                Text(ep.note).font(.system(size: 12.5)).foregroundStyle(NK.textSecondary)
                            }
                        }
                    } else {
                        NKCard { NKEmptyState(title: "当前无锁定态", systemImage: "checkmark.seal") }
                    }
                    Text("请先阅读以上触发材料,确认已完成复盘后再解锁。系统无法验证复盘是否真实完成,但会记录本次确认时间。")
                        .font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                }
                .padding(NKSpace.pagePad)
            }
            .navigationTitle("熔断复盘")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.dismissModal() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("确认已复盘 · 解锁") {
                        Task { submitting = true; await model.confirmCircuitReview(); submitting = false }
                    }
                    .disabled(!model.circuit.locked || submitting)
                }
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label).font(.system(size: 12)).foregroundStyle(NK.textTertiary)
            Spacer()
            Text(value).font(.system(size: 12.5, weight: .medium)).foregroundStyle(NK.textPrimary)
        }
    }
}
