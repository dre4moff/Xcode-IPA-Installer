import Foundation

@main
enum LocalizationTests {
    static func main() throws {
        let expected = CommandLine.arguments[1]
        precondition(L10n.language == expected, "Incorrect preferred language: \(L10n.language)")
        precondition(L10n.text("Sign only") == (expected == "it" ? "Solo firma" : "Sign only"))
        precondition(L10n.text("Extensions: %d", 3) == (expected == "it" ? "Estensioni: 3" : "Extensions: 3"))
        precondition(L10n.text("Untranslated diagnostic") == "Untranslated diagnostic")
        print("Bundle localization passed: \(expected)")
    }
}
