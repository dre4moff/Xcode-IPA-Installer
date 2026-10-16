import Foundation

enum TeamBundleIDPolicy {
    static func make(original: String, team: String) -> String? {
        guard original.count <= 255,
              original.range(of: "^[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)+$", options: .regularExpression) != nil,
              team.range(of: "^[A-Z0-9]{10}$", options: .regularExpression) != nil else { return nil }
        let result = original.hasSuffix("." + team) ? original : original + "." + team
        return result.count <= 255 ? result : nil
    }
}
