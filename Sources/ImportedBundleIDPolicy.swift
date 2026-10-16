import Foundation

enum ImportedBundleIDPolicy {
    static func make(original: String, pattern: String) -> String {
        if pattern == "*" { return original }
        if pattern.hasSuffix(".*") {
            let prefix = String(pattern.dropLast())
            return original.hasPrefix(prefix) ? original : prefix + original
        }
        return pattern
    }
}
