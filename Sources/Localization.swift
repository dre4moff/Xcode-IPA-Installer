import Foundation

enum L10n {
    static var language: String {
        Bundle.main.preferredLocalizations.first?.hasPrefix("it") == true ? "it" : "en"
    }
    static func text(_ key: String, _ arguments: CVarArg...) -> String {
        let value = Bundle.main.localizedString(forKey: key, value: key, table: nil)
        return arguments.isEmpty ? value : String(format: value, locale: Locale(identifier: language), arguments: arguments)
    }
}
