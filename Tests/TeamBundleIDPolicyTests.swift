import Foundation

@main
enum TeamBundleIDPolicyTests {
    static func main() throws {
        let vectors = [("com.example.App", "ABCDEFGHIJ"),
                       ("com.example.App.ABCDEFGHIJ", "ABCDEFGHIJ"),
                       ("com.example.App", "0123456789")]
        var results: [[String: String]] = []
        for (original, team) in vectors {
            guard let value = TeamBundleIDPolicy.make(original: original, team: team) else { fatalError("Valid ID rejected") }
            results.append(["original": original, "team": team, "bundleID": value])
        }
        precondition(TeamBundleIDPolicy.make(original: "invalid", team: "ABCDEFGHIJ") == nil)
        precondition(TeamBundleIDPolicy.make(original: "com.example.App", team: "invalid") == nil)
        precondition(TeamBundleIDPolicy.make(original: "a." + String(repeating: "z", count: 250), team: "ABCDEFGHIJ") == nil)
        for pattern in ["*", "com.example.*", "com.vendor.*", "com.vendor.Fixed"] {
            results.append(["original": "com.example.App", "pattern": pattern,
                            "bundleID": ImportedBundleIDPolicy.make(original: "com.example.App", pattern: pattern)])
        }
        print(String(data: try JSONSerialization.data(withJSONObject: results), encoding: .utf8)!)
    }
}
