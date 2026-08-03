//
//  DecisionLogSheet.swift
//  Neckline — 预注册决策日志八项录入/修订(§五 v1.2-E.1)。嵌「已按计划买入」补录
//  流程之前:建计划 → 录八项 → 成交后一键关联(`AppModel.beginPositionEntryFlow`
//  系列 → 本表单提交 → `OpenPositionSheet` → `submitOpenPosition()` 自动 link)。
//
//  三条硬边界(§五 v1.2-E 验收逐条要验):
//   · **决策日志强制度 = 软约束**(§三条本版硬约束②,用户拍板)——本表单顶部提供
//     「跳过,直接补录开仓」出口,绝不硬阻断开仓流程(硬阻断会逼出假日志)。
//     ⚠ **V2-⑬-5 起更进一步:表单强制度整体退役**(服务端 ⑩-C 已下线五项必填校验,
//     `POST /decisions` 换血成「用户可选补充」入口)。客户端的必填分支与校验文案随之
//     删除,`DecisionLogForm.isValid` 只剩「有 code」这一条真硬前提。
//   · **八项内容落库后不可编辑**——本表单只有两种提交路径:创建(`POST /decisions`)
//     与修订(`POST /decisions/{id}/revise` 新增一行,原行原地不变),没有任何
//     「改旧行」的提交路径;情景树的 `matched` 事后结果标记走别处的专用端点
//     (见 `PositionExtras.swift` 的 `ScenarioOutcomeRow`),本表单里的情景文本
//     纯粹是预注册内容,提交后不会再回到这里编辑。
//   · **审计件、非下单件**(§3.8)——提交本表单绝不触发任何下单 / 开仓动作,只落
//     一条 decision_log 行;真实开仓仍走既有 `POST /positions`。
//

import SwiftUI

struct DecisionLogSheet: View {
    @Bindable var model: AppModel

    private var isRevising: Bool { model.revisingDecisionId != nil }

    var body: some View {
        NavigationStack {
            Form {
                if !isRevising { skipSection }
                symbolSection
                whySection
                targetExitSection
                thesisSection
                invalidationSection
                scenarioSection
                playbookSection
                plannedSection
                maxChaseSection
            }
            .formStyle(.grouped)
            .navigationTitle(isRevising ? "修订决策日志" : "决策日志预注册")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { model.dismissModal() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isRevising ? "提交修订" : "提交并继续") {
                        Task { await model.submitDecisionLog() }
                    }
                    .disabled(!model.decisionForm.isValid)
                }
            }
        }
    }

    private var skipSection: some View {
        Section {
            Button {
                model.skipDecisionLog()
            } label: {
                Label("跳过预注册,直接补录开仓", systemImage: "arrow.right.circle")
                    .font(.system(size: 13))
            }
            .foregroundStyle(NK.textSecondary)
        } footer: {
            Text("决策日志是软约束、不强制——跳过不会阻止你补录开仓,但周复盘会把「无决策日志开仓」计为纪律项统计出来。")
        }
    }

    private var symbolSection: some View {
        Section {
            TextField("代码,如 600519.SH", text: $model.decisionForm.code)
                .disabled(isRevising)
            TextField("名称(可选)", text: $model.decisionForm.name)
                .disabled(isRevising)
        } header: {
            Text("标的")
        } footer: {
            Text("审计件、非下单件:提交只落一条预注册计划,不触发任何下单/开仓动作。")
        }
    }

    private var whySection: some View {
        Section("① 为什么买 / ② 为什么这个入场价") {
            TextField("为什么买这只票", text: $model.decisionForm.whyBuy, axis: .vertical)
                .lineLimit(2...4)
            TextField("为什么选这个入场价", text: $model.decisionForm.whyEntryPrice, axis: .vertical)
                .lineLimit(2...4)
        }
    }

    private var targetExitSection: some View {
        Section {
            TextField("目标价(可选)", text: $model.decisionForm.targetPrice)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            HStack {
                TextField("离场区间下限", text: $model.decisionForm.exitLow)
                Text("~")
                TextField("离场区间上限", text: $model.decisionForm.exitHigh)
            }
            #if os(iOS)
            .keyboardType(.decimalPad)
            #endif
        } header: {
            Text("③ 目标价 / ④ 离场价格区间")
        } footer: {
            if let range = model.entrySuggestionRange {
                Text("参考:¥\(NKFmt.price(range.capFloor))–¥\(NKFmt.price(range.capCeil))(上限 = 违纪判定线,非推荐值);止损线约 ¥\(NKFmt.price(range.stopLine))。")
            }
        }
    }

    private var thesisSection: some View {
        Section("⑤ 论点标签(多选)") {
            ThesisTagPicker(selection: $model.decisionForm.thesisTags)
        }
    }

    private var invalidationSection: some View {
        Section("⑥ 证伪条件") {
            TextField("什么情况发生就说明这次判断错了", text: $model.decisionForm.invalidation, axis: .vertical)
                .lineLimit(2...4)
        }
    }

    private var scenarioSection: some View {
        Section {
            ForEach(Array(model.decisionForm.scenarios.enumerated()), id: \.element.id) { idx, _ in
                ScenarioDraftRow(
                    index: idx, row: $model.decisionForm.scenarios[idx],
                    onRemove: { model.decisionForm.scenarios.remove(at: idx) },
                    removable: model.decisionForm.scenarios.count > 1
                )
            }
            if model.decisionForm.scenarios.count < 4 {
                Button {
                    model.decisionForm.scenarios.append(ContingencyScenarioDraft())
                } label: {
                    Label("添加情景", systemImage: "plus.circle")
                }
            }
        } header: {
            Text("⑦ 应对方案 · 情景树")
        } footer: {
            Text("推演 2-3 种次日走势情景,每行:情景描述 + 触发条件 + 动作。留空的行提交时会被自动忽略,不当垃圾数据提交。")
        }
    }

    private var playbookSection: some View {
        Section("⑧ 打法标签(单选)") {
            Picker("打法", selection: $model.decisionForm.playbookTag) {
                ForEach(PlaybookTag.allCases) { tag in Text(tag.label).tag(tag) }
            }
            .pickerStyle(.segmented)
        }
    }

    private var plannedSection: some View {
        Section {
            TextField("计划价(可选)", text: $model.decisionForm.plannedPrice)
                #if os(iOS)
                .keyboardType(.decimalPad)
                #endif
            TextField("计划量,股(可选)", text: $model.decisionForm.plannedQty)
                #if os(iOS)
                .keyboardType(.numberPad)
                #endif
        } header: {
            Text("计划价 / 计划量")
        } footer: {
            if let range = model.entrySuggestionRange {
                Text("参考手数区间 \(range.qtyLow)–\(range.qtyHigh) 股(¥\(NKFmt.price(range.capFloor))–¥\(NKFmt.price(range.capCeil)),上限 4 万 = 违纪判定线、非推荐值)。系统不替你拍板单笔金额。")
            }
        }
    }

    // —— ⑨ 最高追价上限(v1.4-⑤-B,需求 2 补充)——————————————————————————————————
    // 语义定死(考官规格 §九 同构):相对昨收百分比,开盘价 > 上限 → 放弃该票、盘中不
    // 追补;低开照买(不设下沿)。**二选一强制**:填数字或勾选「不设上限」,两者皆无
    // ⚠ V2-⑬-5:强制表单退役 —— 本节不再驱动提交按钮的 disabled 态(留空合法)。

    private var maxChaseSection: some View {
        Section {
            TextField("最高追价上限,% (相对昨收,可填负值)", text: $model.decisionForm.maxChasePct)
                #if os(iOS)
                .keyboardType(.numbersAndPunctuation)
                #endif
                .disabled(model.decisionForm.maxChaseNoCap)
            Toggle("不设上限(无论开盘涨多高都照买,不设放弃线)", isOn: $model.decisionForm.maxChaseNoCap)
        } header: {
            Text("⑨ 最高追价上限(可选)")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                Text("开盘价超过此涨幅 → 放弃该票、盘中不追补;低开照买(不设下沿)。V2 起可留空(强制表单已退役),留空 = 本次不记这一项。")
                if let hint = maxChasePriceHint {
                    Text(hint)
                }
            }
        }
    }

    /// 实时换算价格提示(纯展示便利,非契约字段)。**诚实边界**:客户端拿不到「昨收」
    /// (前收盘价)数据,只能以用户自己填的「计划价」做近似基准——文案标明是估算,
    /// 不冒充精确换算;计划价未填时不显示任何数字(不虚构价格)。
    private var maxChasePriceHint: String? {
        guard !model.decisionForm.maxChaseNoCap,
              let pct = model.decisionForm.maxChasePctValue,
              let base = Double(model.decisionForm.plannedPrice.trimmingCharacters(in: .whitespaces)),
              base > 0 else { return nil }
        let approx = base * (1 + pct / 100)
        return "≈¥\(NKFmt.price(approx))(以计划价估算,非昨收精确换算)"
    }
}

// MARK: - ⑦ 情景树草稿行(录入,非结果标记——matched 勾选在持仓卡回显区)

private struct ScenarioDraftRow: View {
    let index: Int
    @Binding var row: ContingencyScenarioDraft
    let onRemove: () -> Void
    let removable: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("情景 \(index + 1)").font(.system(size: 10.5, weight: .semibold)).foregroundStyle(NK.textTertiary)
                Spacer()
                Picker("动作", selection: $row.action) {
                    ForEach(ScenarioAction.allCases) { a in Text(a.label).tag(a) }
                }
                .pickerStyle(.menu)
                .font(.system(size: 12))
                if removable {
                    Button(role: .destructive, action: onRemove) {
                        Image(systemName: "trash").font(.system(size: 12))
                    }
                    .buttonStyle(.plain)
                }
            }
            TextField("情景描述,如「次日高开超预期」", text: $row.scenario)
            TextField("触发条件,如「开盘涨幅>3%」", text: $row.trigger)
        }
        .padding(.vertical, 4)
    }
}

// MARK: - ⑤ 论点标签多选(chip 式,SwiftUI 无原生多选 Picker)

private struct ThesisTagPicker: View {
    @Binding var selection: Set<ThesisTag>

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(ThesisTag.allCases) { tag in
                    let isOn = selection.contains(tag)
                    Button {
                        if isOn { selection.remove(tag) } else { selection.insert(tag) }
                    } label: {
                        NKChip(text: tag.label, tone: isOn ? .good : .neutral, filled: isOn)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.vertical, 2)
        }
    }
}
