//
//  InquiryView.swift
//  Neckline — 问询台(§五 阶段4C.3):自由对话体聊天(§2.7 禁模板卡),`POST /inquiry`。
//
//  标注徽标是描述性的(§2.5「已分析」/「已分析·有风险提示」,v1.3.3 起自由分析师,
//  **不是裁决**),**本视图任何路径都不出现「买」按钮**——只展示 `VerdictBadge`
//  (纯文本徽标)+ 依据列表 + 自由对话回复,没有任何可点击的下单/买入控件(见
//  NecklineTests 对该不变量的断言)。
//

import SwiftUI

struct InquiryView: View {
    @Bindable var model: AppModel
    @State private var codeDraft = ""
    @FocusState private var composerFocused: Bool

    var body: some View {
        #if os(iOS)
        NavigationStack {
            body_.navigationTitle("问询台")
                .toolbar {
                    ToolbarItem(placement: .primaryAction) { historyButton }
                }
        }
        .sheet(isPresented: $model.showInquiryHistory) { InquiryHistoryView(model: model) }
        #else
        body_
            .toolbar {
                ToolbarItem { historyButton }
            }
            .sheet(isPresented: $model.showInquiryHistory) { InquiryHistoryView(model: model) }
        #endif
    }

    /// v1.4-⑦-B(§七 P3-13):问询历史列表入口。
    private var historyButton: some View {
        Button {
            model.showInquiryHistory = true
        } label: {
            Label("历史", systemImage: "clock.arrow.circlepath")
        }
    }

    private var body_: some View {
        VStack(spacing: 0) {
            codeBar
            Divider().overlay(NK.hairline)
            if model.inquiryCode.isEmpty {
                NKEmptyState(title: "先填股票代码开始问询",
                            subtitle: "丢一只外部消息源的票进来,系统先跑确定性检查,再由 LLM 自由对话给结论。",
                            systemImage: "bubble.left.and.bubble.right")
                    .frame(maxHeight: .infinity)
            } else {
                threadScroll
                if let v = model.inquiryVerdict {
                    verdictBar(v)
                }
                composer
            }
        }
        .background(platformBg)
    }

    private var platformBg: Color {
        #if os(iOS)
        NK.pageBgIOS
        #else
        NK.pageBg
        #endif
    }

    private var codeBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(NK.textTertiary)
            TextField("股票代码,如 600519.SH", text: $codeDraft)
                #if os(iOS)
                .textInputAutocapitalization(.characters)
                .autocorrectionDisabled()
                #endif
                .onSubmit { beginOrSwitch() }
            if !model.inquiryCode.isEmpty {
                Text(model.inquiryCode).font(.system(size: 12, weight: .semibold).monospaced())
                    .foregroundStyle(NK.accent)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .background(Capsule().fill(NK.accent.opacity(0.10)))
            }
            Button("开始") { beginOrSwitch() }
                .buttonStyle(.borderedProminent)
                .disabled(codeDraft.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        .padding(NKSpace.pagePad)
    }

    private func beginOrSwitch() {
        let code = codeDraft.trimmingCharacters(in: .whitespaces)
        guard !code.isEmpty else { return }
        model.startInquiry(code: code)
        codeDraft = ""
    }

    private var threadScroll: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if model.inquiryDegraded {
                        NKChip(text: "LLM 未激活 · 仅确定性检查结果", tone: .warn)
                    }
                    ForEach(model.inquiryThread) { msg in
                        ChatBubble(message: msg).id(msg.id)
                    }
                    if model.inquiryLoading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("正在核对纪律 + 生成判断…").font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                        }
                        .padding(.leading, 4)
                    }
                }
                .padding(NKSpace.pagePad)
            }
            .onChange(of: model.inquiryThread.count) { _, _ in
                if let last = model.inquiryThread.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private func verdictBar(_ v: InquiryVerdict) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                VerdictBadge(verdict: v)
                Spacer()
            }
            if !model.inquiryEvidence.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(model.inquiryEvidence, id: \.self) { e in
                        HStack(alignment: .top, spacing: 4) {
                            Text("·").foregroundStyle(NK.textTertiary)
                            Text(e).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary)
                        }
                    }
                }
            }
        }
        .padding(.horizontal, NKSpace.pagePad).padding(.vertical, 8)
        .background(NK.cardBg)
    }

    private var composer: some View {
        HStack(spacing: 8) {
            TextField("继续问…", text: $model.inquiryComposer, axis: .vertical)
                .lineLimit(1...4)
                .focused($composerFocused)
                .padding(10)
                .background(RoundedRectangle(cornerRadius: NKRadius.field).fill(NK.fieldBg))
            Button {
                Task { await model.sendInquiryComposer() }
            } label: {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 28))
            }
            .buttonStyle(.plain)
            .foregroundStyle(NK.accent)
            .disabled(model.inquiryComposer.trimmingCharacters(in: .whitespaces).isEmpty || model.inquiryLoading)
        }
        .padding(NKSpace.pagePad)
        .background(.ultraThinMaterial)
    }
}

private struct ChatBubble: View {
    let message: ChatMessage
    var body: some View {
        // user 靠右:先放 Spacer 把气泡推到右边;assistant 靠左:Spacer 放气泡之后。
        HStack {
            if message.role == .user { Spacer(minLength: 40) }
            Text(message.text)
                .font(.system(size: 13.5))
                .foregroundStyle(message.role == .user ? Color.white : NK.textPrimary)
                .padding(.horizontal, 13).padding(.vertical, 9)
                .background(RoundedRectangle(cornerRadius: 16)
                    .fill(message.role == .user ? NK.accent : NK.chipNeutral))
            if message.role == .assistant { Spacer(minLength: 40) }
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - v1.4-⑦-B 问询历史(§七 P3-13)。**与已退役的 `inquiry_pool` 无耦合**——本节
// 展示的是问答本身的档案记录(`inquiry_log`),不是那张已退役的历史队列表。

struct InquiryHistoryView: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationStack {
            Group {
                if model.inquiryHistoryLoading && model.inquiryHistory.isEmpty {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if model.inquiryHistory.isEmpty {
                    NKEmptyState(title: "暂无问询记录", subtitle: "在问询台问过的票会记一行在这里。",
                                systemImage: "clock.arrow.circlepath")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List(model.inquiryHistory) { entry in
                        NavigationLink {
                            InquiryHistoryDetailView(entry: entry)
                        } label: {
                            row(entry)
                        }
                    }
                    .listStyle(.plain)
                }
            }
            .navigationTitle("问询历史")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { model.showInquiryHistory = false }
                }
            }
            .task { await model.loadInquiryHistory() }
        }
        #if os(macOS)
        .frame(width: 420, height: 560)
        #endif
    }

    private func row(_ entry: InquiryLogEntry) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(entry.name.isEmpty ? entry.code : entry.name).font(.system(size: 13, weight: .semibold))
                Text(entry.code).font(.system(size: 10.5)).foregroundStyle(NK.textTertiary)
                Spacer()
                VerdictBadge(verdict: entry.verdictBadge)
            }
            if !entry.question.isEmpty {
                Text(entry.question).font(.system(size: 11.5)).foregroundStyle(NK.textSecondary).lineLimit(1)
            }
            Text(entry.createdAt).font(.system(size: 10)).foregroundStyle(NK.textTertiary)
        }
        .padding(.vertical, 2)
    }
}

private struct InquiryHistoryDetailView: View {
    let entry: InquiryLogEntry

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: NKSpace.gap) {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.name.isEmpty ? entry.code : entry.name).font(NKFont.stockName)
                        Text(entry.code).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                    }
                    Spacer()
                    VerdictBadge(verdict: entry.verdictBadge)
                }
                Text(entry.createdAt).font(.system(size: 11)).foregroundStyle(NK.textTertiary)
                if !entry.question.isEmpty {
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("问").font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                            Text(entry.question).font(.system(size: 13)).foregroundStyle(NK.textPrimary)
                        }
                    }
                }
                NKCard {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("答").font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                        Text(entry.answer).font(.system(size: 13)).foregroundStyle(NK.textPrimary)
                    }
                }
                if !entry.evidence.isEmpty {
                    NKCard {
                        VStack(alignment: .leading, spacing: 4) {
                            Text("依据").font(.system(size: 10.5, weight: .bold)).foregroundStyle(NK.textTertiary)
                            ForEach(entry.evidence, id: \.self) { e in
                                HStack(alignment: .top, spacing: 4) {
                                    Text("·").foregroundStyle(NK.textTertiary)
                                    Text(e).font(.system(size: 12)).foregroundStyle(NK.textSecondary)
                                }
                            }
                        }
                    }
                }
            }
            .padding(NKSpace.pagePad)
        }
        .navigationTitle("问询详情")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
    }
}
