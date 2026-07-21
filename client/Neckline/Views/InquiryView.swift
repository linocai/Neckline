//
//  InquiryView.swift
//  Neckline — 问询台(§五 阶段4C.3):自由对话体聊天(§2.7 禁模板卡),`POST /inquiry`。
//
//  裁决徽标只有二值(§2.5「不符合」/「初审通过进海选池」),**本视图任何路径都不出现
//  「买」按钮**——只展示 `VerdictBadge`(纯文本徽标)+ 依据列表 + 自由对话回复,
//  没有任何可点击的下单/买入控件(见 NecklineTests 对该不变量的断言)。
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
        }
        #else
        body_
        #endif
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
                // v1.1-F.3:问询台裁决卡「+自选」——不看裁决值(增删是用户自主动作,
                // 不受系统裁决门槛限制,即便「不符合」用户仍可选择继续盯着)。
                Button {
                    Task { await model.quickAddWatchlist(code: model.inquiryCode) }
                } label: {
                    Label("+自选", systemImage: "star").font(.system(size: 12, weight: .medium))
                }
                .buttonStyle(.plain).foregroundStyle(NK.textSecondary)
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
