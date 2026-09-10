import Foundation
@main struct RecoveryDecode {
    static func main() throws {
        for kind in ["morning", "evening"] {
            let path = "/tmp/neckline-v320-dto/b57_early_\(kind)_recovered.json"
            let response = try JSONDecoder().decode(K10DailyReportResponse.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            precondition(response.state == "available" && response.report?.status == "completed")
            precondition(response.report?.availableAt != nil && response.reason == nil)
            print("\(kind): actual recovered API response decoded, completed, failure cleared")
        }
    }
}
