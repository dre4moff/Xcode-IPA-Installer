import Foundation
import Security
import CryptoKit

@main
enum CertificateImportTests {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let data = try Data(contentsOf: root.appendingPathComponent("fixture.p12"))
        let der = try Data(contentsOf: root.appendingPathComponent("fixture.der"))
        let fingerprint = Insecure.SHA1.hash(data: der).map { String(format: "%02X", $0) }.joined()
        var previous: CFArray?
        try checkSecurity(SecKeychainCopySearchList(&previous), "test")
        let metadata = try validatePKCS12(data, password: "offline-test-password", expected: [fingerprint])
        precondition(metadata["id"] as? String == fingerprint)
        precondition(metadata["name"] as? String == "Offline Certificate Fixture")
        for (password, expected, code) in [("wrong-password", [fingerprint], "p12Import"),
                                            ("offline-test-password", [String(repeating: "0", count: 40)], "profileCertificateMismatch")] {
            do {
                _ = try validatePKCS12(data, password: password, expected: expected)
                fatalError("Invalid import succeeded")
            } catch let error as CertificateImportError { precondition(error.code == code) }
        }
        var after: CFArray?
        try checkSecurity(SecKeychainCopySearchList(&after), "test")
        precondition(CFEqual(previous, after), "Validation changed the user's Keychain search list")
        // Validation uses isolated, deleted keychains; no fixture key enters the login keychain.
        let query: [String: Any] = [kSecClass as String: kSecClassCertificate,
                                    kSecAttrLabel as String: "Offline Certificate Fixture",
                                    kSecMatchLimit as String: kSecMatchLimitAll]
        precondition(SecItemCopyMatching(query as CFDictionary, nil) == errSecItemNotFound)
        print("Native P12 validation: password, private key, fingerprint, dates, and Keychain isolation passed")
    }
}
