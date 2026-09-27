import Foundation

struct K10CacheSnapshot: Codable {
    let availableAt: String
    let savedAt: Date
    let publications: [K10Publication]
    let companyWindows: [K10CompanyWindow]
    let selections: [K10SelectionDetail]
    let results: K10Results?
    var dailyEvening: K10DailyReportResponse? = nil
    var dailyMorning: K10DailyReportResponse? = nil

    var isReadableByCurrentApp: Bool {
        (dailyEvening?.isReadableByCurrentApp ?? true) &&
        (dailyMorning?.isReadableByCurrentApp ?? true)
    }
}

enum K10Cache {
    private static let prefix = "neckline.k10.v15.cache."
    static func load(baseURL: URL, scope: String) -> K10CacheSnapshot? {
        guard let data = UserDefaults.standard.data(forKey: key(baseURL: baseURL, scope: scope)),
              let snapshot = try? JSONDecoder().decode(K10CacheSnapshot.self, from: data),
              snapshot.isReadableByCurrentApp else { return nil }
        return snapshot
    }
    static func save(_ snapshot: K10CacheSnapshot, baseURL: URL, scope: String) {
        guard snapshot.isReadableByCurrentApp else { return }
        UserDefaults.standard.set(try? JSONEncoder().encode(snapshot), forKey: key(baseURL: baseURL, scope: scope))
    }
    static func clearAllK10() { for key in UserDefaults.standard.dictionaryRepresentation().keys where key.hasPrefix("neckline.k10.") { UserDefaults.standard.removeObject(forKey: key) } }
    static func clearBeforeFreshStart(defaults: UserDefaults = .standard) {
        for key in defaults.dictionaryRepresentation().keys
        where key.hasPrefix("neckline.k10.") && !key.hasPrefix(prefix) {
            defaults.removeObject(forKey: key)
        }
    }
    private static func key(baseURL: URL, scope: String) -> String { "\(prefix)\(baseURL.absoluteString).\(scope)" }
}

enum K10CacheScope { static func value() -> String { "anonymous" } }
