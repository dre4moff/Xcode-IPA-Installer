import SwiftUI
import AppKit
import UniformTypeIdentifiers

struct SigningTeam: Decodable, Identifiable {
    let id: String
    let name: String
    let personal: Bool
}

struct XcodeAccount: Decodable, Identifiable {
    let id: String
    let name: String
    let namespace: String
    let teams: [SigningTeam]
}

struct Phone: Decodable, Identifiable {
    let id: String
    let udid: String
    let name: String
    let version: String
    let connection: String
}

struct IPADetails: Decodable {
    let path: String
    let name: String
    let bundleID: String
    let version: String
    let unpackedBytes: Int64
    let extensions: Int
}

struct ImportedProfile: Decodable, Identifiable {
    let id, name, bundlePattern, team, expires: String
    let memory, eva, debug, allDevices: Bool
    let deviceCount: Int
}

struct ImportedCertificate: Decodable, Identifiable {
    let id, name, expires, team: String
    let profiles: [ImportedProfile]
}

@MainActor
final class InstallerModel: ObservableObject {
    @Published var accounts: [XcodeAccount] = []
    @Published var phones: [Phone] = []
    @Published var accountID = ""
    @Published var teamID = ""
    @Published var deviceID = ""
    @Published var ipa: IPADetails?
    @Published var bundleID = ""
    @Published var increasedMemory = true
    @Published var preserveCapabilities = true
    @Published var strictImportedCapabilities = false
    @Published var isBusy = false
    @Published var isInstalling = false
    @Published var progress = 0.0
    @Published var status = L10n.text("Choose an IPA to get started.")
    @Published var logs = ""
    @Published var xcodeVersion = ""
    @Published var errorMessage: String?
    @Published var resultPath: String?
    @Published var resultMessage = ""
    @Published var logPath: String?
    @Published var verificationPath: String?
    @Published var showTeamSetup = false
    @Published var teamSetupName = ""
    @Published var teamSetupID = ""
    @Published var teamSetupPersonal = true
    @Published var signingMode = UserDefaults.standard.string(forKey: "SigningMode") ?? "xcode"
    @Published var certificates: [ImportedCertificate] = []
    @Published var certificateID = ""
    @Published var profileID = ""
    @Published var showCertificateImport = false
    @Published var importP12 = ""
    @Published var importProfiles: [String] = []
    @Published var importPassword = ""
    private var process: Process?

    var selectedAccount: XcodeAccount? { accounts.first { $0.id == accountID } }
    var selectedTeam: SigningTeam? { selectedAccount?.teams.first { $0.id == teamID } }
    var usesImportedCertificate: Bool { signingMode == "imported" }
    var selectedCertificate: ImportedCertificate? { certificates.first { $0.id == certificateID } }
    var selectedProfile: ImportedProfile? { selectedCertificate?.profiles.first { $0.id == profileID } }
    var canRun: Bool {
        !isBusy && ipa != nil && (usesImportedCertificate ? selectedProfile != nil : selectedTeam != nil)
            && phones.contains { $0.id == deviceID } && !bundleID.isEmpty
    }

    func refresh() {
        guard !isBusy else { return }
        call("discover")
    }

    func chooseIPA() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "ipa") ?? .zip]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = L10n.text("Choose an IPA to sign on your Mac.")
        if panel.runModal() == .OK, let url = panel.url { inspect(url) }
    }

    func inspect(_ url: URL) {
        guard !isBusy else { return }
        resultPath = nil; ipa = nil; bundleID = ""
        call("inspect", data: ["path": url.path])
    }

    func syncTeam() {
        let available = selectedAccount?.teams ?? []
        if !available.contains(where: { $0.id == teamID }) { teamID = available.first?.id ?? "" }
        updateBundleID()
        UserDefaults.standard.set(accountID, forKey: "SelectedAccount")
    }

    func updateBundleID() {
        if usesImportedCertificate, selectedProfile != nil, let ipa {
            bundleID = ipa.bundleID
        } else if !usesImportedCertificate {
            bundleID = ipa.flatMap { TeamBundleIDPolicy.make(original: $0.bundleID, team: teamID) } ?? ""
        } else { bundleID = "" }
        UserDefaults.standard.set(signingMode, forKey: "SigningMode")
    }

    func syncCertificate() {
        let profiles = selectedCertificate?.profiles ?? []
        if !profiles.contains(where: { $0.id == profileID }) { profileID = profiles.first?.id ?? "" }
        updateBundleID()
        UserDefaults.standard.set(certificateID, forKey: "SelectedCertificate")
        UserDefaults.standard.set(profileID, forKey: "SelectedProfile")
    }

    func beginCertificateImport() {
        importP12 = ""; importProfiles = []; importPassword = ""; errorMessage = nil
        showCertificateImport = true
    }

    func chooseCertificateFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true
        if panel.runModal() == .OK, let url = panel.url {
            let files = (try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: nil)) ?? []
            let p12 = files.filter { ["p12", "pfx"].contains($0.pathExtension.lowercased()) }
            importP12 = p12.count == 1 ? p12[0].path : ""
            importProfiles = files.filter { $0.pathExtension.lowercased() == "mobileprovision" }.map(\.path).sorted()
        }
    }

    func chooseP12() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "p12") ?? .data, UTType(filenameExtension: "pfx") ?? .data]
        if panel.runModal() == .OK, let url = panel.url { importP12 = url.path }
    }

    func chooseProfiles(adding: Bool = false) {
        let panel = NSOpenPanel(); panel.allowsMultipleSelection = true
        panel.allowedContentTypes = [UTType(filenameExtension: "mobileprovision") ?? .data]
        if panel.runModal() == .OK {
            let paths = panel.urls.map(\.path)
            if adding {
                call("import-signing", data: ["certificate": certificateID, "profiles": paths])
            } else { importProfiles = paths }
        }
    }

    func importCertificate() {
        let data: [String: Any] = ["p12": importP12, "profiles": importProfiles, "password": importPassword]
        importPassword = ""
        call("import-signing", data: data)
    }

    func forgetCertificate() {
        call("remove-signing", data: ["certificate": certificateID])
    }

    func configureTeam() {
        teamSetupName = selectedAccount?.name ?? ""
        teamSetupID = teamID
        teamSetupPersonal = selectedTeam?.personal ?? true
        showTeamSetup = true
    }

    func saveTeam() {
        showTeamSetup = false
        call("save-account", data: ["account": accountID, "name": teamSetupName,
             "team": teamSetupID, "personal": teamSetupPersonal, "teamName": teamSetupName])
    }

    func start(install: Bool) {
        guard canRun, let ipa else { return }
        progress = 0; logs = ""; resultPath = nil; verificationPath = nil; errorMessage = nil; isInstalling = true
        let data: [String: Any] = ["account": accountID, "team": teamID, "device": deviceID,
            "signingMode": signingMode, "certificate": certificateID, "profile": profileID,
            "strictImportedCapabilities": strictImportedCapabilities,
            "ipa": ipa.path, "memory": increasedMemory, "install": install,
            "preserveCapabilities": preserveCapabilities]
        call("run", data: data)
    }

    func cancel() { process?.terminate(); status = L10n.text("Cancelling…") }

    func openXcode() {
        NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/Xcode.app"))
    }

    private func consume(_ line: String) {
        guard let data = line.data(using: .utf8),
              let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = event["type"] as? String else {
            if !line.isEmpty { appendLog(line) }; return
        }
        switch type {
        case "discovery":
            consumeCertificates(event)
            if let raw = try? JSONSerialization.data(withJSONObject: event["accounts"] ?? []),
               let values = try? JSONDecoder().decode([XcodeAccount].self, from: raw) { accounts = values }
            if let raw = try? JSONSerialization.data(withJSONObject: event["devices"] ?? []),
               let values = try? JSONDecoder().decode([Phone].self, from: raw) { phones = values }
            xcodeVersion = (event["xcode"] as? String ?? "").components(separatedBy: "\n").first ?? ""
            if !accounts.contains(where: { $0.id == accountID }) {
                let previous = UserDefaults.standard.string(forKey: "SelectedAccount")
                accountID = accounts.first(where: { $0.id == previous })?.id ?? accounts.first?.id ?? ""
            }
            syncTeam()
            if !phones.contains(where: { $0.id == deviceID }) { deviceID = phones.first?.id ?? "" }
            status = accounts.isEmpty && certificates.isEmpty ? L10n.text("Add an account in Xcode or import a signing certificate.") : L10n.text("Accounts and devices are up to date.")
        case "assets":
            consumeCertificates(event)
            if let selected = event["selected"] as? String { certificateID = selected }
            syncCertificate(); showCertificateImport = false; importPassword = ""
            status = L10n.text("Signing certificates and profiles are up to date.")
        case "ipa":
            if let value = try? JSONDecoder().decode(IPADetails.self, from: data) {
                ipa = value; updateBundleID(); status = L10n.text("Ready to sign with the selected team.")
            }
        case "phase":
            status = event["message"] as? String ?? status
            progress = event["progress"] as? Double ?? progress
            appendLog(status)
        case "log": appendLog(event["message"] as? String ?? "")
        case "logfile": logPath = event["path"] as? String
        case "error":
            errorMessage = event["message"] as? String ?? L10n.text("The operation failed.")
            status = L10n.text("The operation could not be completed."); appendLog(errorMessage ?? "")
        case "complete":
            resultPath = event["path"] as? String
            verificationPath = event["report"] as? String
            let base = event["message"] as? String ?? L10n.text("Operation completed.")
            let memory = (event["memory"] as? Bool ?? false) ? L10n.text("Increased Memory Limit verified on the main app.") : L10n.text("Increased Memory Limit is off.")
            resultMessage = base + "\n" + memory
            status = base; progress = 1
        case "saved":
            DispatchQueue.main.async { self.refresh() }
        default: break
        }
    }

    private func consumeCertificates(_ event: [String: Any]) {
        if let raw = try? JSONSerialization.data(withJSONObject: event["certificates"] ?? []),
           let values = try? JSONDecoder().decode([ImportedCertificate].self, from: raw) { certificates = values }
        if !certificates.contains(where: { $0.id == certificateID }) {
            let previous = UserDefaults.standard.string(forKey: "SelectedCertificate")
            certificateID = certificates.first(where: { $0.id == previous })?.id ?? certificates.first?.id ?? ""
            profileID = UserDefaults.standard.string(forKey: "SelectedProfile") ?? ""
        }
        syncCertificate()
    }

    private func appendLog(_ text: String) {
        logs += text + "\n"
        if logs.count > 50000 { logs = String(logs.suffix(40000)) }
    }

    private func call(_ command: String, data: [String: Any] = [:]) {
        guard !isBusy, let script = Bundle.main.url(forResource: "installer", withExtension: "py") else { return }
        isBusy = true; errorMessage = nil
        let child = Process(); child.executableURL = URL(fileURLWithPath: "/usr/bin/xcrun")
        child.arguments = ["python3", "-u", script.path, command]
        let output = Pipe(), input = Pipe()
        child.standardOutput = output; child.standardError = output; child.standardInput = input
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["XCODE_IPA_LANGUAGE"] = L10n.language
        child.environment = environment
        process = child
        do {
            try child.run()
            if command != "discover", let encoded = try? JSONSerialization.data(withJSONObject: data) {
                input.fileHandleForWriting.write(encoded)
            }
            try? input.fileHandleForWriting.close()
        } catch {
            isBusy = false; isInstalling = false; process = nil; errorMessage = error.localizedDescription; return
        }
        DispatchQueue.global(qos: .userInitiated).async {
            var buffer = Data()
            while true {
                let chunk = output.fileHandleForReading.availableData
                if chunk.isEmpty { break }
                buffer.append(chunk)
                while let newline = buffer.firstIndex(of: 10) {
                    let line = String(data: buffer[..<newline], encoding: .utf8) ?? ""
                    buffer.removeSubrange(...newline)
                    DispatchQueue.main.async { self.consume(line) }
                }
            }
            if !buffer.isEmpty {
                let last = String(data: buffer, encoding: .utf8) ?? ""
                DispatchQueue.main.async { self.consume(last) }
            }
            child.waitUntilExit()
            DispatchQueue.main.async {
                self.isBusy = false; self.isInstalling = false; self.process = nil
                if child.terminationStatus != 0 && self.errorMessage == nil { self.errorMessage = L10n.text("The process stopped. Check the activity details.") }
                if command == "save-account" && child.terminationStatus == 0 { self.refresh() }
            }
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

@main
struct XcodeIPAInstallerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        WindowGroup { InstallerView() }.windowStyle(.titleBar)
            .windowToolbarStyle(.unifiedCompact)
            .commands { CommandGroup(replacing: .newItem) {} }
    }
}
