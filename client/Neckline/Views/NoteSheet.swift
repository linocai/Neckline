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
//  ⚠ **V2.3.1 批 5**:macOS 侧改自绘弹层(`NKSheetShell` + `NKFormKit`),对齐
//  `Neckline 弹层.dc.html` 74–135(补充说明)/ 136–186(新建提醒)。
//  **iOS 侧原样保留 `Form`** —— iOS 逐屏比对归批 7。
//

import SwiftUI

struct NoteSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(macOS)
        macBody
        #else
        iosBody
        #endif
    }

    // MARK: - macOS(原型 74–135)

    #if os(macOS)
    /// 七枚标签排**两列**(原型 90 行 `grid-template-columns:1fr 1fr; gap:7px`);
    /// 数量为奇数时**最后一枚通栏**(原型 122 行 `grid-column:span 2`)。
    private var labelRows: [[NoteLabel]] {
        var out: [[NoteLabel]] = []
        var pair: [NoteLabel] = []
        for l in NoteLabel.allCases {
            pair.append(l)
            if pair.count == 2 { out.append(pair); pair = [] }
        }
        if !pair.isEmpty { out.append(pair) }
        return out
    }

    private var macBody: some View {
        NKSheetShell(title: "补充说明", cancelTitle: "跳过", primaryTitle: "记下",
                     onCancel: { model.dismissModal() },
                     onPrimary: { Task { await model.submitNote() } }) {
            // 原型 79 行:蓝底说明块 —— ⑩ 的产品意图(系统自动记录,你只补机器不知道的)。
            NKTintedNote(text: "系统已经记下了价格 / 数量 / 时间 / 来源篮子 / 角色 / 市场快照。**这里只补机器不知道的那部分** —— 全部可空,空提交也合法。",
                         tone: .info)

            // ⚠ **原型没有这一格**:它画的是「从某笔成交的落地页进来」那条路,标的由系统
            // 自动关联。本 App 的补充说明是**从工具栏独立打开**的(不挂在某笔成交上),
            // 没有可自动关联的标的 —— 去掉这一格就说不清在补哪只票。⛔ 不是随手加的输入。
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "标的 · 可留空")
                NKFieldCard {
                    NKFieldRow(v: 12, h: 15) {
                        NKFieldLabel(text: "股票代码", width: 88)
                        NKTextFieldBox(placeholder: "如 002812.SZ", text: $model.noteForm.code,
                                       mono: true, bordered: false, emphasized: true)
                    }
                }
                NKInlineNote(text: "留空也能提交 —— 该次没有可落的内容时服务端记零条,不报错。")
            }

            VStack(alignment: .leading, spacing: 9) {   // 原型 88 行 margin-bottom:9
                NKGroupLabel(text: "这次是哪一类 · 可多选,可全不选")
                VStack(spacing: 7) {                    // 原型 gap:7
                    ForEach(Array(labelRows.enumerated()), id: \.offset) { _, row in
                        HStack(spacing: 7) {
                            ForEach(row) { l in
                                NKCheckSquare(text: l.label,
                                              selected: model.noteForm.labels.contains(l)) {
                                    if model.noteForm.labels.contains(l) {
                                        model.noteForm.labels.remove(l)
                                    } else {
                                        model.noteForm.labels.insert(l)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            VStack(alignment: .leading, spacing: 9) {
                NKGroupLabel(text: "一句话说明 · 可选")
                // 原型 128 行:`min-height:62px; radius 11; 白底 + .5px 描边`。
                NKTextFieldBox(placeholder: "例如:早盘那根缩量长下影没跌破昨天的低点,我是看到这个才加的。",
                               text: $model.noteForm.voiceNote,
                               multiline: true, minHeight: 62, filled: false)
            }

            NKInlineNote(text: "⛔ 不做任何硬阻断 —— 硬阻断会逼出假日志。七枚标签的英文码是服务端唯一源,中文只在展示层换算。")
        }
    }
    #endif

    // MARK: - iOS(⚠ 批 5 不动;iOS 逐屏比对归批 7)

    #if os(iOS)
    private var iosBody: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("股票代码", text: $model.noteForm.code)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
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
            .navigationBarTitleDisplayMode(.inline)
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
    #endif
}

// MARK: - ⑪-C NL 提醒:输入框 → **七项确认卡** → 落库

/// ⚠ **确认卡七项必须齐**(含**行情延迟披露**这一必选项与「只通知不自动交易」固定尾巴),
/// ⛔ 不许为了界面清爽省掉任何一项 —— 用户是在这张卡上同意这两件事的。
struct AlertComposerSheet: View {
    @Bindable var model: AppModel

    var body: some View {
        #if os(macOS)
        macBody
        #else
        iosBody
        #endif
    }

    // MARK: - macOS(原型 136–186)

    #if os(macOS)
    private var macBody: some View {
        NKSheetShell(title: "新建提醒", primaryTitle: "确认创建",
                     primaryDisabled: model.alertForm.parsed?.draft == nil
                                      || model.alertForm.submitting,
                     onCancel: { model.showAlertComposer = false },
                     onPrimary: { Task { await model.confirmAlertDraft() } }) {
            // 原型 142 行:一张卡,上半是「标的」键值行,下半是原话正文。
            NKFieldCard {
                NKFieldRow(v: 13, h: 15) {
                    NKFieldLabel(text: "标的", width: 88)
                    NKTextFieldBox(placeholder: "如 002812.SZ", text: $model.alertForm.tsCode,
                                   mono: true, filled: false)
                    Text("留空 = 大盘级").font(NKFont.caption).foregroundStyle(NK.textTertiary)
                }
                NKFieldSeparator()
                NKFieldRow(v: 13, h: 15, alignment: .top) {
                    NKTextFieldBox(placeholder: "用一句话说清条件,例如「跌破 12 块提醒我」",
                                   text: $model.alertForm.text,
                                   multiline: true, minHeight: 44, filled: false)
                }
            }

            // 原型 148 行:淡蓝底小胶囊(`padding:9px 14px; radius 9; bg rgba(11,107,203,.08)`)。
            Button { Task { await model.parseAlertText() } } label: {
                HStack(spacing: 8) {
                    if model.alertForm.parsing {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "wand.and.stars").font(.system(size: 12, weight: .semibold))
                    }
                    Text(model.alertForm.parsing ? "解析中…" : "解析成规则")
                        .font(NKFont.body).fontWeight(.semibold)
                }
                .foregroundStyle(NK.accent)
                .padding(.horizontal, 14).padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: NKRadius.inner)
                    .fill(NK.accent.opacity(0.08)))
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(model.alertForm.parsing
                      || model.alertForm.text.trimmingCharacters(in: .whitespaces).isEmpty)

            if let parsed = model.alertForm.parsed { macParsed(parsed) }

            NKInlineNote(text: "LLM 只做解析,执行归确定性哨兵。LLM 不可用时给可手填的结构化表单并明说「**不是静默失败**」。")
        }
    }

    @ViewBuilder
    private func macParsed(_ parsed: AlertParseResult) -> some View {
        if parsed.degraded {
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "降级 · 手填结构化表单")
                NKTintedNote(text: "LLM 当前不可用,已给出可手填的结构化表单 —— **不是静默失败**。",
                             tone: .warn)
                if let form = parsed.manualForm {
                    NKFieldCard { NKFieldRow(v: 12, h: 15) { NKJSONTable(value: form) } }
                }
            }
        }
        if !parsed.narrative.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "模型的复述 · 只展示,不进判据")
                NKFieldCard {
                    NKFieldRow(v: 13, h: 15, alignment: .top) {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(parsed.narrative).font(NKFont.body).lineSpacing(4)
                                .foregroundStyle(NK.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                            NKReferenceNote()
                        }
                    }
                }
            }
        }
        if let card = parsed.confirmationCard {
            VStack(alignment: .leading, spacing: 9) {
                HStack(spacing: 8) {
                    NKGroupLabel(text: "确认卡 · 七项")
                    // 原型 154 行:红实底小徽标,与七项**同排**。
                    NKChip(text: "一项都不许省", tone: .bad, filled: true)
                    Spacer(minLength: 0)
                }
                NKFieldCard {
                    // 七项**逐项**展示 —— ⛔ 一项都不许省。
                    ForEach(Array(card.rows.enumerated()), id: \.offset) { idx, row in
                        if idx > 0 { NKFieldSeparator() }
                        NKFieldRow(v: 9, h: 15, background: confirmRowTint(idx), alignment: .top) {
                            Text(row.title).font(NKFont.caption).fontWeight(.bold)
                                .foregroundStyle(idx == 5 ? NK.amber : NK.textTertiary)
                                .frame(width: 108, alignment: .leading)
                            // 🔴 服务端在这几句里写了 `**加粗**`(⑥ 行的「**有延迟**」),
                            // `Text(String)` 不解析 Markdown → 星号会原样上屏(实拍逮到)。
                            Text(row.text.isEmpty ? AttributedString("—") : nkMarkdown(row.text))
                                .font(NKFont.callout).lineSpacing(3)
                                .fontWeight(idx == 6 ? .semibold : .regular)
                                .foregroundStyle(NK.textPrimary)
                                .fixedSize(horizontal: false, vertical: true)
                            Spacer(minLength: 0)
                        }
                    }
                }
            }
        } else if !parsed.ok {
            NKFieldCard {
                NKFieldRow(v: 13, h: 15) {
                    Text("没能解析成可执行的规则:\(parsed.reason)")
                        .font(NKFont.callout).foregroundStyle(NK.down)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        if !parsed.matches.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                NKGroupLabel(text: "命中的既有提醒")
                NKFieldCard {
                    ForEach(Array(parsed.matches.enumerated()), id: \.offset) { idx, m in
                        if idx > 0 { NKFieldSeparator() }
                        NKFieldRow(v: 11, h: 15, alignment: .top) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(m.subjectLabel).font(NKFont.body).fontWeight(.semibold)
                                    .foregroundStyle(NK.textPrimary)
                                Text(m.condition).font(NKFont.caption)
                                    .foregroundStyle(NK.textSecondary)
                            }
                        }
                    }
                }
            }
        }
    }

    /// 🔴 **⑥ 行情延迟琥珀底 / ⑦ 动作灰底**(原型 181 / 182 行):用户是在这张卡上同意
    /// 「数据会延迟」与「只通知不交易」这两件事的,⛔ 不能让它们混在其它行里被扫过去。
    private func confirmRowTint(_ idx: Int) -> Color? {
        switch idx {
        case 5: return NK.amber.opacity(0.05)
        case 6: return NK.textTertiary.opacity(0.06)
        default: return nil
        }
    }
    #endif

    // MARK: - iOS(⚠ 批 5 不动;iOS 逐屏比对归批 7)

    #if os(iOS)
    private var iosBody: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("标的代码(留空 = 大盘级)", text: $model.alertForm.tsCode)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
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
            .navigationBarTitleDisplayMode(.inline)
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
                    .font(NKFont.callout).foregroundStyle(NK.amber)
                if let form = parsed.manualForm {
                    NKJSONTable(value: form)
                }
            } header: {
                Text("降级:手填结构化表单")
            }
        }
        if !parsed.narrative.isEmpty {
            Section {
                Text(parsed.narrative).font(NKFont.callout).foregroundStyle(NK.textSecondary)
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
                        Text(row.title).nkLabel()
                            .foregroundStyle(NK.textTertiary)
                        // 同上(iOS 侧同一个 bug,一并修;iOS 实拍核对归批 7)。
                        Text(row.text.isEmpty ? AttributedString("—") : nkMarkdown(row.text))
                            .font(NKFont.callout).foregroundStyle(NK.textPrimary)
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
                    .font(NKFont.callout).foregroundStyle(NK.down)
            }
        }
        if !parsed.matches.isEmpty {
            Section {
                ForEach(parsed.matches) { m in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.subjectLabel).font(NKFont.callout).fontWeight(.semibold)
                        Text(m.condition).font(NKFont.caption).foregroundStyle(NK.textSecondary)
                    }
                }
            } header: {
                Text("命中的既有提醒")
            }
        }
    }
    #endif
}
