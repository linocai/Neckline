//
//  NoteSheet.swift
//  Neckline — **用户可选补充**(⑩-C,V2-⑮):七枚标签 + 一句可选说明。
//
//  ⚠ **这不是决策日志**:`decision_log` 表 v2.0.0 起**停写留档**,九项强制表单整套退役
//  (「买卖录入控制在数秒内、不再要求长表单」)。本表单落 `user_actions`
//  (`kind='label'` / `'voice_note'`),**全部可空、空提交合法**(服务端 200,不是 400),
//  ⛔ 不做任何硬阻断 —— 硬阻断会逼出假日志。
//
//  七枚标签码是**服务端唯一源**(`schemas.NoteLabelLiteral`),中文走客户端展示层换算
//  (`nkNoteLabelText`),⛔ 不另造一套中文键。
//

import SwiftUI

struct NoteSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("股票代码", text: $model.noteForm.code)
                        #if os(iOS)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        #endif
                } header: {
                    Text("标的")
                } footer: {
                    Text("留空也能提交(该次没有可落的内容时服务端记零条,不报错)。")
                }

                Section {
                    ForEach(NoteLabel.allCases) { label in
                        Button {
                            if model.noteForm.labels.contains(label) {
                                model.noteForm.labels.remove(label)
                            } else {
                                model.noteForm.labels.insert(label)
                            }
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: model.noteForm.labels.contains(label)
                                      ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(model.noteForm.labels.contains(label)
                                                     ? NK.accent : NK.textTertiary)
                                Text(label.label).foregroundStyle(NK.textPrimary)
                                Spacer()
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text("这次是哪一类(可多选,可全不选)")
                } footer: {
                    Text("系统已经自动记下了价格 / 数量 / 时间 / 来源篮子 / 角色 / 市场快照;这里只补机器不知道的那部分。")
                }

                Section {
                    TextField("一句话说明(可选)", text: $model.noteForm.voiceNote, axis: .vertical)
                        .lineLimit(2...5)
                } header: {
                    Text("补充说明")
                }
            }
            .formStyle(.grouped)
            .navigationTitle("补充说明")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("跳过") { model.dismissModal() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("记下") { Task { await model.submitNote() } }
                }
            }
        }
    }
}

// MARK: - ⑪-C NL 提醒:输入框 → **七项确认卡** → 落库

/// ⚠ **确认卡七项必须齐**(含**行情延迟披露**这一必选项与「只通知不自动交易」固定尾巴),
/// ⛔ 不许为了界面清爽省掉任何一项 —— 用户是在这张卡上同意这两件事的。
struct AlertComposerSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("标的代码(留空 = 大盘级)", text: $model.alertForm.tsCode)
                        #if os(iOS)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        #endif
                    TextField("用一句话说清条件,例如「跌破 12 块提醒我」", text: $model.alertForm.text,
                              axis: .vertical)
                        .lineLimit(2...5)
                    Button {
                        Task { await model.parseAlertText() }
                    } label: {
                        HStack {
                            if model.alertForm.parsing {
                                ProgressView().controlSize(.small)
                                Text("解析中…")
                            } else {
                                Image(systemName: "wand.and.stars")
                                Text("解析成规则")
                            }
                        }
                    }
                    .disabled(model.alertForm.parsing
                              || model.alertForm.text.trimmingCharacters(in: .whitespaces).isEmpty)
                } header: {
                    Text("自然语言提醒")
                } footer: {
                    Text("LLM 只做解析;执行归确定性哨兵。**系统永不自动交易**。")
                }

                if let parsed = model.alertForm.parsed {
                    parsedSection(parsed)
                }
            }
            .formStyle(.grouped)
            .navigationTitle("新建提醒")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { model.showAlertComposer = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("确认创建") { Task { await model.confirmAlertDraft() } }
                        .disabled(model.alertForm.parsed?.draft == nil || model.alertForm.submitting)
                }
            }
        }
    }

    @ViewBuilder
    private func parsedSection(_ parsed: AlertParseResult) -> some View {
        if parsed.degraded {
            Section {
                Text("LLM 当前不可用,已给出可手填的结构化表单 —— **不是静默失败**。")
                    .font(.system(size: 12)).foregroundStyle(NK.amber)
                if let form = parsed.manualForm {
                    NKJSONTable(value: form)
                }
            } header: {
                Text("降级:手填结构化表单")
            }
        }
        if !parsed.narrative.isEmpty {
            Section {
                Text(parsed.narrative).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                NKReferenceNote()
            } header: {
                Text("模型的复述(只展示,不进判据)")
            }
        }
        if let card = parsed.confirmationCard {
            Section {
                // 七项**逐项**展示 —— ⛔ 一项都不许省。
                ForEach(Array(card.rows.enumerated()), id: \.offset) { _, row in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(row.title).font(.system(size: 10.5, weight: .bold))
                            .foregroundStyle(NK.textTertiary)
                        Text(row.text.isEmpty ? "—" : row.text)
                            .font(.system(size: 12.5)).foregroundStyle(NK.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            } header: {
                Text("确认卡(七项)")
            } footer: {
                Text("确认即创建。命中时只通知你,**系统不会下任何单**。")
            }
        } else if !parsed.ok {
            Section {
                Text("没能解析成可执行的规则:\(parsed.reason)")
                    .font(.system(size: 12)).foregroundStyle(NK.down)
            }
        }
        if !parsed.matches.isEmpty {
            Section {
                ForEach(parsed.matches) { m in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.subjectLabel).font(.system(size: 12.5, weight: .semibold))
                        Text(m.condition).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                    }
                }
            } header: {
                Text("命中的既有提醒")
            }
        }
    }
}
