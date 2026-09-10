import Foundation
@main struct ProductionDecode {
 static func main() throws {
  let root = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))) as! [String:Any]
  func decode<T:Decodable>(_ type:T.Type,_ key:String) throws -> T { try JSONDecoder().decode(type,from:JSONSerialization.data(withJSONObject:root[key]!)) }
  let config=try decode(K10Configuration.self,"configuration")
  precondition(config.scopes.count==4 && config.scopes.allSatisfy{$0.state=="configured"})
  precondition(config.strategyVersion=="K10-v2")
  for key in ["evening","morning"] { let r=try decode(K10DailyReportResponse.self,key);precondition(r.state=="empty") }
  let providers=try decode(K10ProviderList.self,"providers")
  precondition(providers.items.count==1 && providers.items[0].name=="deepseek" && providers.items[0].model=="deepseek-v4-pro" && providers.items[0].enabled && providers.items[0].keySet)
  let results=try decode(K10Results.self,"results")
  let opportunities=try decode(K10OpportunityList.self,"opportunities")
  precondition(opportunities.items.count==2)
  print("Production API decoded: unchanged BYOK connection; 4 configured scopes; K10-v2; evening/morning empty; \(opportunities.items.count) preserved opportunities; \(results.records.count) evaluation records")
 }
}
