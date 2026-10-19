// 分段控件。KX 的分组和 iOS 的 K10「最新 / 历史」共用，取值与样图一致。

import SwiftUI

struct NKSegmentedControl<Value: Hashable>: View {
    let options: [(value: Value, title: String)]
    @Binding var selection: Value

    var body: some View {
        HStack(spacing: 0) {
            ForEach(options.indices, id: \.self) { index in
                let option = options[index]
                let on = option.value == selection
                Button {
                    selection = option.value
                } label: {
                    Text(option.title)
                        .font(on ? NKKXFont.segmentOn : NKKXFont.segment)
                        .foregroundStyle(NKMorning.textPrimary)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 6)
                        .background {
                            if on {
                                RoundedRectangle(cornerRadius: 7, style: .continuous)
                                    .fill(NKMorning.card)
                                    .shadow(color: NKKX.segmentShadow, radius: 1.5, x: 0, y: 1)
                            }
                        }
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .padding(2)
        .background(NKMorning.tagFill, in: RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}
