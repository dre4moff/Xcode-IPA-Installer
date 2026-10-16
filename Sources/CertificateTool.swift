import Foundation

@main
struct CertificateTool {
    static func main() {
        do {
            let input = FileHandle.standardInput.readDataToEndOfFile()
            guard let request = try JSONSerialization.jsonObject(with: input) as? [String: Any] else {
                throw CertificateImportError("invalidRequest")
            }
            let value = try importCertificate(request: request)
            FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: value))
        } catch {
            let failure = error as? CertificateImportError
            let value: [String: Any] = ["error": failure?.code ?? "certificateRead", "status": failure?.status ?? -1]
            if let data = try? JSONSerialization.data(withJSONObject: value) { FileHandle.standardOutput.write(data) }
            exit(1)
        }
    }
}
