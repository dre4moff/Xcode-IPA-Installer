import Foundation
import CryptoKit

enum BundleIDPolicy {
    static func make(original: String, team: String, namespace: String) -> String? {
        guard !original.isEmpty, original.count <= 255,
              original.range(of: "^[A-Za-z0-9-]+(?:\\.[A-Za-z0-9-]+)+$", options: .regularExpression) != nil,
              team.range(of: "^[A-Z0-9]{10}$", options: .regularExpression) != nil,
              namespace.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else { return nil }
        let normalized = original.lowercased()
        let component = normalized.components(separatedBy: ".").last ?? "app"
        let trimmed = String(component.prefix(40)).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let name = trimmed.isEmpty ? "app" : trimmed
        let digest = SHA256.hash(data: Data(normalized.utf8)).map { String(format: "%02x", $0) }.joined().prefix(12)
        return "local.xcodeipa.t\(team.lowercased()).i\(namespace).\(name)-\(digest)"
    }
}
