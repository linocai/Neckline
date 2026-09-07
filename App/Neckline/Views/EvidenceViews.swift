import SwiftUI
import Foundation

struct OpportunitySheet: View {
    let detail: K10OpportunityDetail
    @Bindable var model: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(
                        title: detail.companyName ?? detail.companyCode,
                        subtitle: detail.eventHeadline ?? "共同事件与公司比较"
                    )
                    OpportunitySummary(detail: detail)

                    V3SectionTitle(title: "共同事实", icon: "checklist")
                    CommonFactsCard(facts: detail.commonFacts)

                    V3SectionTitle(title: "相关公司比较", icon: "arrow.triangle.branch")
                    V3Card {
                        VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                            ForEach(detail.samples) { sample in
                                VStack(alignment: .leading, spacing: 7) {
                                    HStack(spacing: 7) {
                                        V3Pill(text: k10CategoryText(sample.category))
                                        Text(sample.companyName ?? sample.companyCode).font(NKFont.headline)
                                        Spacer()
                                        if let rank = sample.rank {
                                            Text("第 \(rank) 位")
                                                .font(NKFont.caption)
                                                .foregroundStyle(NK.textSecondary)
                                        }
                                    }
                                    if let summary = sample.comparison.summary {
                                        Text(summary).font(NKFont.body)
                                    }
                                    ComparisonDetails(comparison: sample.comparison)
                                    EvidenceBlock(evidence: sample.evidence, model: model)
                                }
                                if sample.id != detail.samples.last?.id { Divider().overlay(NK.hairline) }
                            }
                        }
                    }

                    LifecycleBlock(events: detail.lifecycleEvents, model: model)
                }
                .padding(.horizontal, NKSpace.pagePad)
                .padding(.top, NKSpace.pagePad)
                .padding(.bottom, NKSpace.pagePadBottom)
            }
            .background(NK.pageBg)
            .navigationTitle("机会依据")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }
}

private struct OpportunitySummary: View {
    let detail: K10OpportunityDetail

    var body: some View {
        V3Card {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    V3Pill(text: detail.lifecycle)
                    V3Pill(text: detail.sampleClass)
                    Spacer()
                    Text("事件修订 \(detail.eventRevision)")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                }
                Text(detail.catalystStage).font(NKFont.title3)
                Text("\(k10PublicationMarkerText(detail.sourceMarker))首发 \(k10DisplayTime(detail.availableAt)) · 固定窗口 D1 \(k10DisplayTime(detail.d1TradeDate))、D2 \(k10DisplayTime(detail.d2TradeDate))")
                    .font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
                if detail.latePublication == true {
                    Label("迟到发布：从下一交易日起观察", systemImage: "clock.badge.exclamationmark")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.amber)
                }
                if detail.sampleClass == "overlap" {
                    Label("重叠机会保留完整观察，不进入主样本统计。", systemImage: "rectangle.3.group.bubble")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.amber)
                }
            }
        }
    }
}

private struct CommonFactsCard: View {
    let facts: [K10CommonFact]

    var body: some View {
        V3Card {
            if facts.isEmpty {
                Text("当前没有可展示的共同事实。")
                    .font(NKFont.callout)
                    .foregroundStyle(NK.textSecondary)
            } else {
                VStack(alignment: .leading, spacing: 9) {
                    ForEach(facts) { fact in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "checkmark.circle")
                                .font(NKFont.callout)
                                .foregroundStyle(NK.accent)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(fact.key).font(NKFont.headline)
                                Text(fact.text).font(NKFont.callout)
                            }
                        }
                    }
                }
            }
        }
    }
}

struct LifecycleBlock: View {
    let events: [K10LifecycleEvent]
    @Bindable var model: AppModel

    var body: some View {
        if !events.isEmpty {
            V3SectionTitle(title: "更新、反证与撤回记录", icon: "arrow.clockwise")
            V3Card {
                VStack(alignment: .leading, spacing: NKSpace.blockGap) {
                    ForEach(events) { item in
                        VStack(alignment: .leading, spacing: 7) {
                            HStack {
                                V3Pill(text: item.kind)
                                Text(k10DisplayTime(item.occurredAt))
                                    .font(NKFont.caption)
                                    .foregroundStyle(NK.textSecondary)
                                Spacer()
                            }
                            if let reason = item.reason, !reason.isEmpty {
                                Text(reason).font(NKFont.callout)
                            }
                            if !item.sourceRefs.isEmpty {
                                ForEach(item.sourceRefs) { SourceReferenceLine(source: $0, model: model) }
                            }
                        }
                        if item.id != events.last?.id { Divider().overlay(NK.hairline) }
                    }
                }
            }
        }
    }
}

struct EvidenceBlock: View {
    let evidence: [K10Evidence]
    @Bindable var model: AppModel

    var body: some View {
        if evidence.isEmpty {
            Text("当前没有可展示的原始资料引用。")
                .font(NKFont.caption)
                .foregroundStyle(NK.textSecondary)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                ForEach(evidence) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        if !item.claim.isEmpty { Text(item.claim).font(NKFont.callout) }
                        if let uncertainty = item.uncertainty, !uncertainty.isEmpty {
                            Label("待核：\(uncertainty)", systemImage: "questionmark.circle")
                                .font(NKFont.caption)
                                .foregroundStyle(NK.amber)
                        }
                        SourceReferenceLine(source: item.sourceRef, model: model)
                    }
                }
            }
        }
    }
}

struct SourceReferenceLine: View {
    let source: K10SourceReference
    @Bindable var model: AppModel
    @State private var document: K10DocumentPage?
    @State private var loading = false

    private var isMarketSnapshot: Bool { source.sourceKey == "market_snapshot" }
    private var isTavily: Bool { (source.sourceKey ?? "").lowercased().contains("tavily") }
    private var canonicalURL: URL? {
        guard let raw = source.url,
              let components = URLComponents(string: raw),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              components.host != nil else { return nil }
        return components.url
    }

    var body: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: isMarketSnapshot ? "chart.line.uptrend.xyaxis" : "doc.text")
                .font(NKFont.callout)
                .foregroundStyle(isMarketSnapshot ? NK.accent : NK.textSecondary)
                .frame(width: 16, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                Text(primaryLine)
                    .font(NKFont.callout.weight(.medium))
                    .foregroundStyle(NK.textPrimary)
                Text(secondaryLine)
                    .font(NKFont.caption)
                    .foregroundStyle(NK.textSecondary)
                if isTavily, source.excerpt != nil {
                    Text("该来源保存的是搜索摘录。")
                        .font(NKFont.caption)
                        .foregroundStyle(NK.textSecondary)
                }
            }
            Spacer(minLength: 4)
            VStack(alignment: .trailing, spacing: 5) {
                if source.documentId != nil && !isMarketSnapshot {
                    Button(loading ? "读取中" : "查看资料") {
                        Task {
                            loading = true
                            document = await model.openDocument(source)
                            loading = false
                        }
                    }
                    .buttonStyle(V3SecondaryButtonStyle())
                    .frame(width: 82)
                    .disabled(loading)
                }
                if let canonicalURL {
                    Link(destination: canonicalURL) {
                        Label("查看网页", systemImage: "safari")
                            .font(NKFont.caption)
                    }
                    .foregroundStyle(NK.accent)
                }
            }
        }
        .sheet(item: $document) { SourceDocumentSheet(document: $0, model: model) }
    }

    private var primaryLine: String {
        if isMarketSnapshot {
            return "行情快照 · \(source.companyCode ?? "公司待核") · \(source.tradeDate.map(k10DisplayTime) ?? "交易日待核")"
        }
        return source.title ?? k10SourceText(source.sourceKey ?? "来源待标识")
    }

    private var secondaryLine: String {
        if isMarketSnapshot {
            let collected = source.collectedAt.map { "本次整理 \(k10DisplayTime($0))" } ?? "本次整理时间未记录"
            let fetched = source.fetchedAt.map { "原始采集 \(k10DisplayTime($0))" } ?? "原始采集时间未记录"
            return "\(collected) · \(fetched)"
        }
        let revision = source.revision.map { "资料修订 \($0)" } ?? "资料修订待核"
        let published = source.publishedAt.map { "发布 \(k10DisplayTime($0))" } ?? "发布时间待核"
        let fetched = source.fetchedAt.map { "取得 \(k10DisplayTime($0))" } ?? "取得时间待核"
        return "\(revision) · \(published) · \(fetched) · \(k10PublishedPrecisionText(source.publishedPrecision))"
    }
}

struct SourceDocumentSheet: View {
    @State var document: K10DocumentPage
    @Bindable var model: AppModel
    @State private var loading = false
    @Environment(\.dismiss) private var dismiss

    private var isExcerptOnly: Bool { document.body == nil && document.excerpt != nil }
    private var canonicalURL: URL? {
        guard let raw = document.canonicalUrl,
              let components = URLComponents(string: raw),
              let scheme = components.scheme?.lowercased(),
              ["http", "https"].contains(scheme),
              components.host != nil else { return nil }
        return components.url
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: NKSpace.cardGap) {
                    V3PageHeader(title: document.title ?? document.sourceKey, subtitle: isExcerptOnly ? "搜索摘录" : "原始全文")
                    V3Card {
                        VStack(alignment: .leading, spacing: 7) {
                            Text("资料修订 \(document.revision) · \(k10PublishedPrecisionText(document.publishedPrecision))")
                                .font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                            Text("发布 \(document.publishedAt.map(k10DisplayTime) ?? "时间待核") · 取得 \(k10DisplayTime(document.fetchedAt))")
                                .font(NKFont.caption)
                                .foregroundStyle(NK.textSecondary)
                            if isExcerptOnly {
                                Label("该来源没有保存完整正文，以下内容是搜索摘录。", systemImage: "text.quote")
                                    .font(NKFont.callout)
                                    .foregroundStyle(NK.amber)
                            }
                            if let canonicalURL {
                                Link(destination: canonicalURL) {
                                    Label("查看来源网页", systemImage: "safari")
                                        .font(NKFont.callout.weight(.semibold))
                                }
                                .foregroundStyle(NK.accent)
                            }
                        }
                    }
                    V3Card {
                        K10MarkdownText(markdown: document.body ?? document.excerpt ?? "该版本未保存原始正文。", sourceRefs: []) { _ in }
                    }
                    if document.page.nextCursor != nil {
                        Button(loading ? "加载中" : "加载后续正文") {
                            Task {
                                loading = true
                                if let next = await model.loadMoreDocument(document) { document = next }
                                loading = false
                            }
                        }
                        .buttonStyle(V3SecondaryButtonStyle())
                        .disabled(loading)
                    }
                }
                .padding(.horizontal, NKSpace.pagePad)
                .padding(.top, NKSpace.pagePad)
                .padding(.bottom, NKSpace.pagePadBottom)
            }
            .background(NK.pageBg)
            .navigationTitle(isExcerptOnly ? "来源摘录" : "原始全文")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
            }
        }
    }
}

struct K10MarkdownText: View {
    let markdown: String
    let sourceRefs: [K10SourceReference]
    let onSourceTap: (K10SourceReference) -> Void

    private var lines: [String] {
        markdown.components(separatedBy: .newlines).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                MarkdownLine(line: line, sourceRefs: sourceRefs, onSourceTap: onSourceTap)
            }
        }
        .textSelection(.enabled)
    }
}

private struct MarkdownLine: View {
    let line: String
    let sourceRefs: [K10SourceReference]
    let onSourceTap: (K10SourceReference) -> Void

    private var trimmed: String { line.trimmingCharacters(in: .whitespaces) }
    private var headingLevel: Int {
        trimmed.prefix { $0 == "#" }.count
    }
    private var displayText: String {
        var value = trimmed
        if headingLevel > 0 { value = String(value.dropFirst(headingLevel)).trimmingCharacters(in: .whitespaces) }
        if value.hasPrefix("- ") || value.hasPrefix("* ") { value = String(value.dropFirst(2)) }
        return value
    }
    private var isBullet: Bool { trimmed.hasPrefix("- ") || trimmed.hasPrefix("* ") }
    private var references: [MarkdownSourceReference] { referencedSources(in: line) }
    private var webLinks: [MarkdownWebLink] { markdownLinks(in: line) }

    var body: some View {
        HStack(alignment: .top, spacing: 7) {
            if isBullet {
                Image(systemName: "circle.fill")
                    .font(.system(size: 5))
                    .foregroundStyle(NK.accent)
                    .padding(.top, 5)
            }
            VStack(alignment: .leading, spacing: 5) {
                inlineText(projectedText(displayText))
                    .font(headingFont)
                    .foregroundStyle(NK.textPrimary)
                if !references.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(references) { reference in
                            Button("[资料 \(reference.index)]") { onSourceTap(reference.source) }
                                .buttonStyle(MarkdownReferenceButtonStyle())
                        }
                    }
                }
                if !webLinks.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(webLinks) { link in
                            Link(link.label, destination: link.url)
                                .buttonStyle(MarkdownReferenceButtonStyle())
                        }
                    }
                }
            }
        }
    }

    private var headingFont: Font {
        switch headingLevel {
        case 1: return NKFont.title2
        case 2: return NKFont.title3
        case 3...: return NKFont.headline
        default: return NKFont.body
        }
    }

    private func inlineText(_ raw: String) -> Text {
        let pieces = raw.components(separatedBy: "**")
        return pieces.enumerated().reduce(Text("")) { result, item in
            let fragment = Text(verbatim: item.element)
            return result + (item.offset.isMultiple(of: 2) ? fragment : fragment.bold())
        }
    }

    private func projectedText(_ text: String) -> String {
        let sourceMatches = referenceMatches(in: text)
        let links = markdownLinks(in: text).filter { link in
            !sourceMatches.contains { NSIntersectionRange($0.range, link.range).length > 0 }
        }
        var replacements = sourceMatches.map { ($0.range, "[资料 \($0.reference.index)]") }
        replacements += links.map { ($0.range, $0.label) }
        let ordered = replacements.sorted { $0.0.location < $1.0.location }
        var result = ""
        var location = 0
        for replacement in ordered where replacement.0.location >= location {
            guard let prefixRange = Range(NSRange(location: location, length: replacement.0.location - location), in: text),
                  Range(replacement.0, in: text) != nil else { continue }
            result += String(text[prefixRange])
            result += replacement.1
            location = replacement.0.location + replacement.0.length
        }
        guard let suffixRange = Range(NSRange(location: location, length: (text as NSString).length - location), in: text) else {
            return text
        }
        return result + String(text[suffixRange])
    }

    private func referencedSources(in text: String) -> [MarkdownSourceReference] {
        var seen = Set<String>()
        return referenceMatches(in: text).compactMap { match in
            seen.insert(match.reference.id).inserted ? match.reference : nil
        }
    }

    private func referenceMatches(in text: String) -> [MarkdownReferenceMatch] {
        let range = NSRange(text.startIndex..., in: text)
        var matches: [MarkdownReferenceMatch] = []

        func append(_ expression: NSRegularExpression?, resolve: (NSTextCheckingResult) -> MarkdownSourceReference?) {
            guard let expression else { return }
            for match in expression.matches(in: text, range: range) {
                guard !matches.contains(where: { NSIntersectionRange($0.range, match.range).length > 0 }),
                      let reference = resolve(match) else { continue }
                matches.append(MarkdownReferenceMatch(range: match.range, reference: reference))
            }
        }

        append(try? NSRegularExpression(pattern: "\\[(doc_[A-Za-z0-9_-]+)@(\\d+)\\]")) { match in
            guard let idRange = Range(match.range(at: 1), in: text),
                  let revisionRange = Range(match.range(at: 2), in: text),
                  let revision = Int(text[revisionRange]) else { return nil }
            return reference(documentID: String(text[idRange]), revision: revision)
        }

        append(try? NSRegularExpression(pattern: "`(doc_[A-Za-z0-9_-]+)`")) { match in
            guard let idRange = Range(match.range(at: 1), in: text) else { return nil }
            return uniqueReference(documentID: String(text[idRange]))
        }

        append(try? NSRegularExpression(pattern: "(?<![A-Za-z0-9_-])(doc_[A-Za-z0-9_-]+)(?![A-Za-z0-9_-])")) { match in
            guard let idRange = Range(match.range(at: 1), in: text) else { return nil }
            return uniqueReference(documentID: String(text[idRange]))
        }

        append(try? NSRegularExpression(pattern: "\\[S([1-9][0-9]*)\\]")) { match in
            guard let numberRange = Range(match.range(at: 1), in: text),
                  let number = Int(text[numberRange]),
                  sourceRefs.indices.contains(number - 1),
                  sourceRefs[number - 1].documentId != nil else { return nil }
            return MarkdownSourceReference(source: sourceRefs[number - 1], index: number)
        }

        return matches.sorted { $0.range.location < $1.range.location }
    }

    private func reference(documentID: String, revision: Int) -> MarkdownSourceReference? {
        guard let index = sourceRefs.firstIndex(where: { $0.documentId == documentID && $0.revision == revision }) else { return nil }
        return MarkdownSourceReference(source: sourceRefs[index], index: index + 1)
    }

    private func uniqueReference(documentID: String) -> MarkdownSourceReference? {
        let candidates = sourceRefs.enumerated().filter { $0.element.documentId == documentID }
        guard candidates.count == 1, let candidate = candidates.first else { return nil }
        return MarkdownSourceReference(source: candidate.element, index: candidate.offset + 1)
    }

    private func markdownLinks(in text: String) -> [MarkdownWebLink] {
        guard let expression = try? NSRegularExpression(pattern: "\\[([^\\]\\n]+)\\]\\((https?://[^\\s)]+)\\)") else { return [] }
        let range = NSRange(text.startIndex..., in: text)
        var seen = Set<String>()
        return expression.matches(in: text, range: range).compactMap { match in
            guard let labelRange = Range(match.range(at: 1), in: text),
                  let urlRange = Range(match.range(at: 2), in: text),
                  let url = safeWebURL(String(text[urlRange])) else { return nil }
            let label = String(text[labelRange])
            let key = url.absoluteString
            guard seen.insert(key).inserted else { return nil }
            return MarkdownWebLink(range: match.range, label: label, url: url)
        }
    }
}

private struct MarkdownSourceReference: Identifiable {
    let source: K10SourceReference
    let index: Int
    var id: String { "\(source.id)#\(index)" }
}

private struct MarkdownReferenceMatch {
    let range: NSRange
    let reference: MarkdownSourceReference
}

private struct MarkdownWebLink: Identifiable {
    let range: NSRange
    let label: String
    let url: URL
    var id: String { url.absoluteString }
}

private struct MarkdownReferenceButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(NKFont.caption.weight(.semibold))
            .foregroundStyle(NK.accent)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(NK.accent.opacity(configuration.isPressed ? 0.12 : 0.06), in: RoundedRectangle(cornerRadius: NKRadius.badge))
    }
}

private func safeWebURL(_ raw: String) -> URL? {
    guard let components = URLComponents(string: raw),
          let scheme = components.scheme?.lowercased(),
          ["http", "https"].contains(scheme),
          components.host != nil else { return nil }
    return components.url
}
