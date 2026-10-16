import Foundation
import Security
import CryptoKit

struct CertificateImportError: Error {
    let code: String
    let status: OSStatus
    init(_ code: String, _ status: OSStatus = errSecParam) { self.code = code; self.status = status }
}

func checkSecurity(_ status: OSStatus, _ code: String) throws {
    if status != errSecSuccess { throw CertificateImportError(code, status) }
}

// Supplying a keychain is essential during validation: on macOS, a nil target imports
// into the user's default keychain. Passwords stay in memory and never enter argv.
func importPKCS12(_ data: Data, password: String, keychain: SecKeychain?, access: SecAccess?) throws -> SecIdentity {
    var options: [String: Any] = [kSecImportExportPassphrase as String: password]
    if let keychain { options[kSecImportExportKeychain as String] = keychain }
    if let access { options[kSecImportExportAccess as String] = access }
    var items: CFArray?
    try checkSecurity(SecPKCS12Import(data as CFData, options as CFDictionary, &items), "p12Import")
    guard let entries = items as? [[String: Any]], entries.count == 1,
          let raw = entries[0][kSecImportItemIdentity as String], CFGetTypeID(raw as CFTypeRef) == SecIdentityGetTypeID() else {
        throw CertificateImportError("oneIdentityRequired")
    }
    let identity = raw as! SecIdentity
    var key: SecKey?
    try checkSecurity(SecIdentityCopyPrivateKey(identity, &key), "privateKeyMissing")
    return identity
}

func certificateMetadata(_ identity: SecIdentity) throws -> [String: Any] {
    var cert: SecCertificate?
    try checkSecurity(SecIdentityCopyCertificate(identity, &cert), "certificateMissing")
    guard let cert else { throw CertificateImportError("certificateMissing") }
    let der = SecCertificateCopyData(cert) as Data
    let fingerprint = Insecure.SHA1.hash(data: der).map { String(format: "%02X", $0) }.joined()
    let keys = [kSecOIDX509V1ValidityNotAfter, kSecOIDX509V1ValidityNotBefore, kSecOIDOrganizationalUnitName]
    let values = SecCertificateCopyValues(cert, keys as CFArray, nil) as? [String: [String: Any]] ?? [:]
    func value(_ key: CFString) -> Any? { values[key as String]?[kSecPropertyKeyValue as String] }
    guard let expiry = value(kSecOIDX509V1ValidityNotAfter) as? Double,
          let start = value(kSecOIDX509V1ValidityNotBefore) as? Double,
          start <= Date.timeIntervalSinceReferenceDate, expiry > Date.timeIntervalSinceReferenceDate else {
        throw CertificateImportError("certificateExpired")
    }
    var name: CFString?
    try checkSecurity(SecCertificateCopyCommonName(cert, &name), "certificateMissing")
    return ["id": fingerprint, "name": name as String? ?? fingerprint,
            "expires": ISO8601DateFormatter().string(from: Date(timeIntervalSinceReferenceDate: expiry)),
            "team": value(kSecOIDOrganizationalUnitName) as? String ?? ""]
}

func validatePKCS12(_ data: Data, password: String, expected: [String]) throws -> [String: Any] {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("xcode-ipa-certificate-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                          attributes: [.posixPermissions: 0o700])
    defer { try? FileManager.default.removeItem(at: directory) }
    var previous: CFArray?
    try checkSecurity(SecKeychainCopySearchList(&previous), "keychain")
    defer { if let previous { SecKeychainSetSearchList(previous) } }
    var keychain: SecKeychain?
    let secret = UUID().uuidString + UUID().uuidString
    let status = secret.withCString { pass in
        SecKeychainCreate(directory.appendingPathComponent("validation.keychain-db").path,
                          UInt32(secret.utf8.count), pass, false, nil, &keychain)
    }
    try checkSecurity(status, "keychain")
    guard let keychain else { throw CertificateImportError("keychain") }
    defer { SecKeychainDelete(keychain) }
    let identity = try importPKCS12(data, password: password, keychain: keychain, access: nil)
    let metadata = try certificateMetadata(identity)
    guard let fingerprint = metadata["id"] as? String, expected.contains(fingerprint) else {
        throw CertificateImportError("profileCertificateMismatch")
    }
    return metadata
}

func importCertificate(request: [String: Any]) throws -> [String: Any] {
    guard let path = request["path"] as? String, let password = request["password"] as? String,
          let expected = request["expected"] as? [String], !expected.isEmpty else {
        throw CertificateImportError("invalidRequest")
    }
    let data = try Data(contentsOf: URL(fileURLWithPath: path))
    guard data.count <= 16 * 1024 * 1024 else { throw CertificateImportError("invalidRequest") }
    let metadata = try validatePKCS12(data, password: password, expected: expected)
    var trusted: SecTrustedApplication?
    try checkSecurity(SecTrustedApplicationCreateFromPath("/usr/bin/codesign", &trusted), "keychain")
    var access: SecAccess?
    try checkSecurity(SecAccessCreate("Xcode IPA Installer signing key" as CFString,
                                     [trusted!] as CFArray, &access), "keychain")
    _ = try importPKCS12(data, password: password, keychain: nil, access: access)
    return metadata
}
