import Foundation
@main struct ProductionDecode {
    static func main() throws {
        let root = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [String:Any]
        func decode<T:Decodable>(_ type:T.Type, _ key:String) throws -> T {
            try JSONDecoder().decode(type, from:JSONSerialization.data(withJSONObject:root[key]!))
        }
        let config = try decode(K10Configuration.self,"configuration")
        precondition(config.scopes.count == 4 && config.scopes.allSatisfy{$0.state == "configured"})
        let evening = try decode(K10DailyReportResponse.self,"evening")
        let morning = try decode(K10DailyReportResponse.self,"morning")
        let providers = try decode(K10ProviderList.self,"providers")
        precondition(providers.items.contains{$0.model == "deepseek-flash" && $0.enabled && $0.keySet})
        _ = try decode(K10Results.self,"results")
        _ = try decode(K10OpportunityList.self,"opportunities")
        precondition(evening.report?.cutoffAt == "2026-09-10T13:00:00+00:00")
        print("Actual API decoded: evening=\(evening.report?.status ?? evening.state), cards=\(evening.report?.eveningCards.count ?? 0), morning=\(morning.state), configured scopes=4")
    }
}
