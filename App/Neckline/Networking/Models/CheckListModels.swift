//
//  CheckListModels.swift
//  Neckline — 次日核对表 DTO。
//
//  对齐后端 `GET /api/v1/checklist/{tradeDate}`
//  (`neckline/auction/checklist.py::Checklist.to_dict`,逐字段对齐,⛔ 别猜)。
//
//  🔴 **核对表恰好两段,⛔ 结构上没有「成立」这个取值**(裁定 10 / 守门 G20)。
//  服务端 `ChecklistVerdict` 是**二值闭合枚举** `{rejected, pending_open}`,加第三个成员
//  会让服务端模块 import 就炸;客户端这一侧同样只有两个 case。
//
//  **为什么 9:29 判不出「成立」——这是结构性的,不是口味问题**(K9 §七 / §5.7.2):
//  K9 §6.3 四个形态的「成立」分支**全部含有「前 30 分钟」这一合取项**
//  (p1 的「前 30 分钟最低价 ≥ [B]」、p2 的「前 30 分钟不创昨日新低」、
//   p3/p4 的「前 30 分钟不破 [A]」),而 9:29 时前 30 分钟**还没发生**;
//  四个「放弃」分支则全是单条破位判定,竞价价就能触发。
//  **三分支的终值由 D1 10:00 的一次性结算读数产出**,它挂在**成绩**板块下
//  (`/scoreboard/verdicts/{date}`)—— ⛔ 不进选股首屏。
//
//  ⚠ 404 = **那天没跑过那一拍**(⛔ 不是「跑了、表是空的」——后者会返回一张两段皆空的表)。
//

import Foundation

// MARK: - 二值裁定(⛔ 没有第三个取值)

/// 核对表的两段。🔴 **`case confirmed` 永远不存在** —— 这不是「本版先不做」,
/// 是 9:29 那一拍结构上判不出成立(见文件头)。⛔ 别加第三个 case。
enum ChecklistVerdict: String, Codable, Equatable, CaseIterable {
    case rejected = "rejected"          // 已触发放弃
    case pendingOpen = "pending_open"   // 其余待开盘后观察

    /// 段名。**全映射**(⛔ 无 fallback:枚举只有两个成员,不存在第三种情况)。
    var label: String {
        switch self {
        case .rejected: return "已触发放弃"
        case .pendingOpen: return "待开盘后观察"
        }
    }

    var tone: NKAxisTone {
        switch self {
        // 「已触发放弃」是一条**结论**,画红是在说「这只今天可以划掉了」。
        case .rejected: return .bad
        // 「待观察」既不是好也不是坏 —— 它就是还没到能判的时候,⛔ 别画成绿色。
        case .pendingOpen: return .neutral
        }
    }
}

// MARK: - 核对表上的一行

struct ChecklistRow: Codable, Equatable, Identifiable {
    var tsCode: String = ""
    var name: String? = nil
    var pattern: String = ""
    /// 未识别值 → `nil`:服务端只可能发两个取值,解不出说明契约漂了,
    /// ⛔ 不静默归到某一段里(那会把一只没判过的票摆进「已触发放弃」)。
    var verdict: ChecklistVerdict? = nil
    var segment: String = ""
    var playbookVersion: Int = 0
    /// 9:26 那一拍能读到的量(`MetricRef → 值`)。⚠ 服务端**刻意不提供**
    /// `open_price` / `gap_pct` / `first30_high` —— 9:29 时开盘还没发生,
    /// 给它们一个值就是编数。缺席即「读不到」,⛔ 界面不许补 0。
    var readings: [String: Double?] = [:]
    /// 「放弃」分支的逐条留痕(哪一条触发了、读数是多少)。
    var rejectionBranch: NKJSON = .object([:])
    /// 报价新鲜度(`QuoteQuality.freshness`)。空串 = **这一只没抓到价**。
    var quoteState: String = ""

    var id: String { tsCode }
    var displayName: String { (name?.isEmpty == false) ? name! : tsCode }

    enum CodingKeys: String, CodingKey {
        case tsCode, name, pattern, verdict, segment, playbookVersion
        case readings, rejectionBranch, quoteState
    }

    init(tsCode: String = "", name: String? = nil, pattern: String = "",
         verdict: ChecklistVerdict? = nil, segment: String = "", playbookVersion: Int = 0,
         readings: [String: Double?] = [:], rejectionBranch: NKJSON = .object([:]),
         quoteState: String = "") {
        self.tsCode = tsCode; self.name = name; self.pattern = pattern
        self.verdict = verdict; self.segment = segment; self.playbookVersion = playbookVersion
        self.readings = readings; self.rejectionBranch = rejectionBranch
        self.quoteState = quoteState
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tsCode = try c.decodeIfPresent(String.self, forKey: .tsCode) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name)
        pattern = try c.decodeIfPresent(String.self, forKey: .pattern) ?? ""
        verdict = try? c.decodeIfPresent(ChecklistVerdict.self, forKey: .verdict)
        segment = try c.decodeIfPresent(String.self, forKey: .segment) ?? ""
        playbookVersion = try c.decodeIfPresent(Int.self, forKey: .playbookVersion) ?? 0
        readings = try c.decodeIfPresent([String: Double?].self, forKey: .readings) ?? [:]
        rejectionBranch = try c.decodeIfPresent(NKJSON.self, forKey: .rejectionBranch) ?? .object([:])
        quoteState = try c.decodeIfPresent(String.self, forKey: .quoteState) ?? ""
    }

    /// 有读数的量,按 `MetricRef` 名排序(**确定性** —— 刷新一次顺序就跳是最烦人的)。
    /// ⚠ 值为 `nil` 的量**不进这张表**:那是「这一拍读不到它」,⛔ 不补 0。
    var readingRows: [(label: String, value: Double)] {
        readings.compactMap { key, value -> (String, Double)? in
            guard let v = value else { return nil }
            return (nkMetricRefLabel(key), v)
        }
        .sorted { $0.0 < $1.0 }
        .map { (label: $0.0, value: $0.1) }
    }
}

// MARK: - 一张 9:29 核对表

/// **两段,⛔ 没有第三段。** `footnote` 恒带那一行把「谁在什么时候定成立」说清楚
/// (服务端 `CHECKLIST_FOOTNOTE` 是唯一源,⛔ 客户端不另抄一句)。
struct Checklist: Codable, Equatable {
    var tradeDate: String = ""
    /// 这张表核的是**哪一天的清单**(D0)。⚠ 与 `tradeDate`(D1)刻意分开。
    var d0Date: String = ""
    var capturedAt: String = ""
    var dataQuality: String = ""
    var segments: [ChecklistSegment] = []
    /// D0 在清单上、但这次**没抓到价**的票(它们仍落在「待观察」段,如实标注)。
    var noQuoteCodes: [String] = []
    /// D0 在清单上、但**没有冻结预案**的票 —— ⛔ 不拿一份现编的条件顶替,
    /// 界面必须逐只说出来:**明早核对不了这一只**。
    var noPlaybookCodes: [String] = []
    /// 🔴 服务端给的那一行:「成立由 10:00 结算,9:30–10:00 由我自己判定。」
    /// ⛔ 客户端不许改写、不许省略 —— 它是这张表**没有「成立」段**的解释。
    var footnote: String = ""
    var notes: [String] = []
    var createdAt: String = ""

    enum CodingKeys: String, CodingKey {
        case tradeDate, d0Date, capturedAt, dataQuality, segments
        case noQuoteCodes, noPlaybookCodes, footnote, notes, createdAt
    }

    init(tradeDate: String = "", d0Date: String = "", capturedAt: String = "",
         dataQuality: String = "", segments: [ChecklistSegment] = [],
         noQuoteCodes: [String] = [], noPlaybookCodes: [String] = [],
         footnote: String = "", notes: [String] = [], createdAt: String = "") {
        self.tradeDate = tradeDate; self.d0Date = d0Date; self.capturedAt = capturedAt
        self.dataQuality = dataQuality; self.segments = segments
        self.noQuoteCodes = noQuoteCodes; self.noPlaybookCodes = noPlaybookCodes
        self.footnote = footnote; self.notes = notes; self.createdAt = createdAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tradeDate = try c.decodeIfPresent(String.self, forKey: .tradeDate) ?? ""
        d0Date = try c.decodeIfPresent(String.self, forKey: .d0Date) ?? ""
        capturedAt = try c.decodeIfPresent(String.self, forKey: .capturedAt) ?? ""
        dataQuality = try c.decodeIfPresent(String.self, forKey: .dataQuality) ?? ""
        segments = try c.decodeIfPresent([ChecklistSegment].self, forKey: .segments) ?? []
        noQuoteCodes = try c.decodeIfPresent([String].self, forKey: .noQuoteCodes) ?? []
        noPlaybookCodes = try c.decodeIfPresent([String].self, forKey: .noPlaybookCodes) ?? []
        footnote = try c.decodeIfPresent(String.self, forKey: .footnote) ?? ""
        notes = try c.decodeIfPresent([String].self, forKey: .notes) ?? []
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
    }

    func segment(_ v: ChecklistVerdict) -> ChecklistSegment? {
        segments.first { $0.verdict == v }
    }

    var rejectedCount: Int { segment(.rejected)?.rows.count ?? 0 }
    var pendingCount: Int { segment(.pendingOpen)?.rows.count ?? 0 }
}

struct ChecklistSegment: Codable, Equatable, Identifiable {
    var verdict: ChecklistVerdict? = nil
    var label: String = ""
    var rows: [ChecklistRow] = []

    var id: String { verdict?.rawValue ?? label }

    enum CodingKeys: String, CodingKey { case verdict, label, rows }

    init(verdict: ChecklistVerdict? = nil, label: String = "", rows: [ChecklistRow] = []) {
        self.verdict = verdict; self.label = label; self.rows = rows
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        verdict = try? c.decodeIfPresent(ChecklistVerdict.self, forKey: .verdict)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        rows = try c.decodeIfPresent([ChecklistRow].self, forKey: .rows) ?? []
    }

    /// 段名:服务端给的 `label` 优先(⛔ 别在客户端另抄一份中文映射)。
    var displayLabel: String { label.isEmpty ? (verdict?.label ?? "") : label }
}
