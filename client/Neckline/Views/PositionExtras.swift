//
//  PositionExtras.swift
//  Neckline — 持仓卡的 v1.2 扩展:决策日志回显 + 情景兑现勾选(v1.2-E.1)、呼吸
//  台账入口露出(v1.2-E.4)、熔断锁定横幅 + 复盘解锁弹层(v1.2-E.3)。
//
//  拆成独立文件是因为 TodayPlanView.swift 已经很长——这些组件都是「挂在持仓卡
//  上的次级信息」,不是独立板块(§五 v1.2-E「不新增第六个 tab」硬边界),故仍属于
//  「今日计划」这一个 View 的从属组件,只是物理上拆文件保持可读性。
//

import SwiftUI

// MARK: - v1.2-E.1/E.4:持仓卡「决策日志」回显区 + 呼吸台账入口

/// 入口露出规则(§五 v1.2-E.4,产品拍板,照做):优先按该持仓已关联的决策日志
/// `playbookTag == BREATHING_TRIAL` 决定是否在持仓卡**主展示区**露出台账入口;
/// 拿不到关联信息的持仓(老持仓 / 无日志)**不在主区域主动露出**,但通过卡片的
/// 「更多」次级菜单始终保留入口——避免「没日志就永远进不去台账」。
struct PositionDecisionSection: View {
    @Bindable var model: AppModel
    let position: Position
    @State private var expanded = false

    private var linked: DecisionLog? { model.linkedDecision(forPositionId: position.id) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let log = linked {
                header(for: log)
                if expanded { detail(for: log) }
            } else {
                HStack {
                    Text("未关联决策日志").font(.system(size: 11.5)).foregroundStyle(NK.textTertiary)
                    Spacer()
                    moreMenu
                }
            }
        }
    }

    @ViewBuilder
    private func header(for log: DecisionLog) -> some View {
        HStack(spacing: 6) {
            Button {
                withAnimation(.easeInOut(duration: 0.16)) { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "doc.text.magnifyingglass").font(.system(size: 11))
                    Text(expanded ? "收起决策日志" : "决策日志 · \(log.playbookTagLabel)")
                        .font(.system(size: 12, weight: .medium))
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").font(.system(size: 10))
                }
            }
            .buttonStyle(.plain)
            .foregroundStyle(NK.accent)
            Spacer()
            // v1.3-②-D/⑥-D:情景树每日对照提醒入口(该持仓有关联决策的非空情景树待
            // 每日对照——`scenarioReviewPending` 服务端算好,只做「挑出来」)。点击直接
            // 展开决策日志详情,情景文本本身仍只读、勾选仍走既有 `setScenarioOutcome`。
            if position.scenarioReviewPending {
                Button {
                    withAnimation(.easeInOut(duration: 0.16)) { expanded = true }
                } label: {
                    NKChip(text: "情景待对照", tone: .warn)
                }
                .buttonStyle(.plain)
            }
            // 呼吸底仓试验主动露出台账入口(主展示区);其余打法仍可从「更多」次级菜单进入。
            if log.isBreathingTrial {
                Button {
                    model.openBreathingSheet(positionId: position.id)
                } label: {
                    NKChip(text: "呼吸台账", tone: .good)
                }
                .buttonStyle(.plain)
            }
            moreMenu
        }
    }

    private var moreMenu: some View {
        Menu {
            Button("呼吸 T 台账") { model.openBreathingSheet(positionId: position.id) }
            if let log = linked {
                Button("修订决策日志") { model.beginReviseDecision(log) }
            }
        } label: {
            Image(systemName: "ellipsis.circle").font(.system(size: 13)).foregroundStyle(NK.textTertiary)
        }
    }

    @ViewBuilder
    private func detail(for log: DecisionLog) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            piece("为什么买", log.whyBuy)
            piece("为什么这个入场价", log.whyEntryPrice)
            HStack(spacing: 12) {
                if let t = log.targetPrice { piece("目标价", "¥\(NKFmt.price(t))") }
                if let lo = log.exitLow, let hi = log.exitHigh {
                    piece("离场区间", "¥\(NKFmt.price(lo)) ~ ¥\(NKFmt.price(hi))")
                }
            }
            if !log.thesisTagLabels.isEmpty {
                HStack(spacing: 4) {
                    ForEach(log.thesisTagLabels, id: \.self) { NKChip(text: $0) }
                }
            }
            piece("证伪条件", log.invalidation)
            if !log.contingencyScenarios.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    Text("应对方案 · 点击勾选情景兑现").font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                    ForEach(Array(log.contingencyScenarios.enumerated()), id: \.offset) { idx, s in
                        ScenarioOutcomeRow(model: model, decisionId: log.id, index: idx, scenario: s)
                    }
                }
            }
        }
    }

    private func piece(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label).font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
            Text(text).font(.system(size: 12)).foregroundStyle(NK.textPrimary)
        }
    }
}

/// 情景兑现勾选行(次日复盘用)。**情景文本(scenario/trigger/action)UI 上只读、
/// 不可改**——本行只暴露 `matched` 一个可交互点,点击整行即翻转,调
/// `POST /decisions/{id}/scenario-outcome`(只翻 matched,不动情景文本,§五 v1.2-E.1
/// 硬边界)。
private struct ScenarioOutcomeRow: View {
    @Bindable var model: AppModel
    let decisionId: Int
    let index: Int
    let scenario: ContingencyScenario

    var body: some View {
        Button {
            Task { await model.toggleScenarioOutcome(decisionId: decisionId, index: index, matched: !scenario.matched) }
        } label: {
            HStack(alignment: .top, spacing: 6) {
                Image(systemName: scenario.matched ? "checkmark.square.fill" : "square")
                    .foregroundStyle(scenario.matched ? NK.up : NK.textTertiary)
                VStack(alignment: .leading, spacing: 1) {
                    Text("\(scenario.scenario) → \(scenario.actionLabel)")
                        .font(.system(size: 11.5)).foregroundStyle(NK.textPrimary)
                    Text("触发:\(scenario.trigger)").font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                }
                Spacer()
            }
        }
        .buttonStyle(.plain)
    }
}

// MARK: - v1.2-E.3:熔断锁定横幅(今日计划面置顶,比退潮刹车更靠前)

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

// MARK: - v1.2-E.3:「熔断复盘」按钮先展示材料再调 `POST /circuit/unlock`

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
