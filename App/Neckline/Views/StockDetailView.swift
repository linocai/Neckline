import SwiftUI

/// K9-v3 preserves every revision; before 9:26 the user may append one.
struct FrozenCandidateDetailView: View {
    let candidate: K9PackageCandidate
    @Bindable var model: AppModel
    @State private var editing = false

    var body: some View {
        NKCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Text(candidate.displayName).font(NKFont.title3).foregroundStyle(NK.textPrimary)
                    Text(candidate.tsCode).font(NKFont.monoKey).foregroundStyle(NK.textTertiary)
                    Spacer()
                }
                if let industry = candidate.swL2Name, !industry.isEmpty {
                    Text(industry).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                }
                NKWrapRow(spacing: 5, lineSpacing: 5) {
                    ForEach(candidate.channels, id: \.self) { channel in
                        NKChip(text: "\(nkChannelLabel(channel)) · 第 \(candidate.channelRanks[channel] ?? 0)", tone: .info)
                    }
                    NKChip(text: candidate.playbookLabel, tone: .neutral)
                }
                stageSection
                playbookSection
            }
        }
    }

    @ViewBuilder private var stageSection: some View {
        VStack(alignment: .leading, spacing: 5) {
            NKSectionHeader(title: "D1 / D2 阶段")
            if let d1 = candidate.d1 {
                Text("9:29：\(d1.checklistVerdict.label)").font(NKFont.callout).foregroundStyle(NK.textPrimary)
                if let open = d1.openVerdict { Text("10:00：\(open.label)").font(NKFont.callout).foregroundStyle(NK.textPrimary) }
                if let close = d1.closeState { Text("D1 收盘：\(close.label)").font(NKFont.callout).foregroundStyle(NK.textPrimary) }
                if let reference = d1.referencePrice { Text("D1 10:00 参考价：\(NKFmt.price(reference))").font(NKFont.caption).foregroundStyle(NK.textSecondary) }
            } else {
                Text("D1 尚未追加。") .font(NKFont.callout).foregroundStyle(NK.textTertiary)
            }
            if let d2 = candidate.d2 {
                Text("选股结果：\(d2.selectionResult.label)").font(NKFont.callout).foregroundStyle(NK.textPrimary)
                if let playbook = d2.playbookResult, !playbook.isEmpty { Text("预案结果：\(playbook)").font(NKFont.callout).foregroundStyle(NK.textPrimary) }
                if let risk = d2.riskTag, !risk.isEmpty { NKChip(text: "风险：\(risk)", tone: .warn) }
                rawMetrics(d2.raw)
            }
        }
    }

    @ViewBuilder private var playbookSection: some View {
        VStack(alignment: .leading, spacing: 5) {
            NKSectionHeader(title: "冻结预案")
            Text("K9-v3 · \(candidate.playbookLabel)").font(NKFont.caption).foregroundStyle(NK.textSecondary)
            if candidate.d1 == nil, let batchId = model.selectedScorePackageID {
                Button("修改预案") { editing = true }.buttonStyle(.bordered)
                    .sheet(isPresented: $editing) { PlaybookRevisionEditor(candidate: candidate, batchId: batchId, model: model) }
            }
            if let history = candidate.playbookHistory, history.count > 1 {
                Text("历史版本：" + history.map { "预案第 \($0.revision) 版（\($0.sourceLabel)）" }.joined(separator: " · "))
                    .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            }
            K9PlaybookSummary(playbook: candidate.playbook)
            DisclosureGroup("查看原始冻结合同") {
                NKJSONTable(value: candidate.playbook).padding(.top, 6)
            }
            .font(NKFont.caption).foregroundStyle(NK.textSecondary)
        }
    }

    @ViewBuilder private func rawMetrics(_ raw: NKJSON) -> some View {
        let keys = ["d1ReferencePrice", "d1TenToCloseReturn", "d2MaxReturn", "d2CloseReturn", "maxDrawdown", "relativeBenchmark", "tradable"]
        let values = raw.objectValue ?? [:]
        if keys.contains(where: { values[$0] != nil }) {
            VStack(alignment: .leading, spacing: 2) {
                Text("原始结算值").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                ForEach(keys.filter { values[$0] != nil }, id: \.self) { key in
                    Text("\(key)：\(values[key]?.stringValue ?? String(describing: values[key]!))")
                        .font(NKFont.monoKey).foregroundStyle(NK.textSecondary)
                }
            }
        }
    }
}

private struct PlaybookRevisionEditor: View {
    let candidate: K9PackageCandidate
    let batchId: String
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var draft = ""
    @State private var error = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("K9-v3 · 修改预案").font(NKFont.title3)
            Text("提交会追加预案第 N 版；9:26 冻结后不可再修改。机械通道、候选和排名不能改。")
                .font(NKFont.caption).foregroundStyle(NK.textSecondary)
            TextEditor(text: $draft).font(.system(.body, design: .monospaced)).frame(minHeight: 300)
            if !error.isEmpty { Text(error).foregroundStyle(.red).font(NKFont.caption) }
            HStack { Spacer(); Button("取消") { dismiss() }; Button("追加版本") { save() }.disabled(saving) }
        }.padding().onAppear { draft = candidate.playbook.prettyText }
    }

    private func save() {
        guard let data = draft.data(using: .utf8), let plan = try? JSONDecoder().decode(NKJSON.self, from: data) else {
            error = "请输入合法 JSON 预案。"; return
        }
        saving = true
        Task { @MainActor in
            error = await model.appendPlaybookRevision(batchId: batchId, tsCode: candidate.tsCode, playbook: plan) ?? ""
            saving = false
            if error.isEmpty { dismiss() }
        }
    }
}

private struct K9PlaybookSummary: View {
    let playbook: NKJSON

    private var object: [String: NKJSON] { playbook.objectValue ?? [:] }
    private var open: [String: NKJSON] { object["openVerdict"]?.objectValue ?? [:] }
    private var confirm: [String: NKJSON] { open["confirmRange"]?.objectValue ?? [:] }
    private var conditions: [String: NKJSON] { object["conditions"]?.objectValue ?? [:] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 112), alignment: .topLeading)],
                      alignment: .leading, spacing: 12) {
                priceCell("失效价", object["invalidation"])
                priceCell("第一压力位", object["firstResistance"])
                priceCell("第二压力位", object["secondResistance"])
                priceCell("开盘放弃线", open["rejectBelow"])
                priceCell("成立区间", rangeText)
                priceCell("高开透支线", open["overextendedAtOrAbove"])
                priceCell("不可买价", open["unbuyableAtOrAbove"])
            }
            if let rationale = object["rationale"]?.stringValue, !rationale.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    Text("预案解释").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                    Text(rationale).font(NKFont.callout).foregroundStyle(NK.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ForEach(conditions.keys.sorted(), id: \.self) { channel in
                let fields = K9Presentation.readableFields(conditions[channel] ?? .null)
                if !fields.isEmpty {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("\(nkChannelLabel(channel))条件")
                            .font(NKFont.headline).foregroundStyle(NK.textPrimary)
                        ForEach(fields) { field in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(field.label).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                                Spacer(minLength: 8)
                                Text(field.value).font(NKFont.callout.monospacedDigit())
                                    .foregroundStyle(NK.textPrimary).multilineTextAlignment(.trailing)
                            }
                        }
                    }
                    .padding(10)
                    .background(RoundedRectangle(cornerRadius: 8).fill(NK.textTertiary.opacity(0.07)))
                }
            }
        }
    }

    private var rangeText: String? {
        guard let minimum = number(confirm["minimum"]), let maximum = number(confirm["maximum"]) else { return nil }
        return "\(minimum)–\(maximum)"
    }

    private func priceCell(_ title: String, _ value: NKJSON?) -> some View {
        priceCell(title, number(value))
    }

    private func priceCell(_ title: String, _ value: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(NKFont.caption).foregroundStyle(NK.textSecondary)
            Text(value ?? "—").font(NKFont.metric).foregroundStyle(NK.textPrimary)
        }
    }

    private func number(_ value: NKJSON?) -> String? {
        guard let number = value?.doubleValue else { return nil }
        return String(format: "%.2f", number)
    }
}
