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
    @Published var isBusy = false
    @Published var isInstalling = false
    @Published var progress = 0.0
    @Published var status = "Scegli un’IPA per iniziare."
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
    private var process: Process?

    var selectedAccount: XcodeAccount? { accounts.first { $0.id == accountID } }
    var selectedTeam: SigningTeam? { selectedAccount?.teams.first { $0.id == teamID } }
    var canRun: Bool {
        !isBusy && ipa != nil && selectedTeam != nil && phones.contains { $0.id == deviceID } && !bundleID.isEmpty
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
        panel.message = "Scegli l’IPA da firmare con un account già presente in Xcode."
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
        bundleID = ipa.flatMap { TeamBundleIDPolicy.make(original: $0.bundleID, team: teamID) } ?? ""
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
            "ipa": ipa.path, "memory": increasedMemory, "install": install,
            "preserveCapabilities": preserveCapabilities]
        call("run", data: data)
    }

    func cancel() { process?.terminate(); status = "Annullamento in corso…" }

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
            status = accounts.isEmpty ? "Aggiungi un account in Xcode → Impostazioni → Apple Accounts." : "Account Xcode e dispositivi aggiornati."
        case "ipa":
            if let value = try? JSONDecoder().decode(IPADetails.self, from: data) {
                ipa = value; updateBundleID(); status = "IPA pronta per la firma con il suffisso del team selezionato."
            }
        case "phase":
            status = event["message"] as? String ?? status
            progress = event["progress"] as? Double ?? progress
            appendLog(status)
        case "log": appendLog(event["message"] as? String ?? "")
        case "logfile": logPath = event["path"] as? String
        case "error":
            errorMessage = event["message"] as? String ?? "Operazione non riuscita."
            status = "Operazione non completata."; appendLog(errorMessage ?? "")
        case "complete":
            resultPath = event["path"] as? String
            verificationPath = event["report"] as? String
            let base = event["message"] as? String ?? "Operazione completata."
            let memory = (event["memory"] as? Bool ?? false) ? "Increased Memory Limit verificato solo sull’app principale." : "Increased Memory Limit disattivato."
            resultMessage = base + "\n" + memory
            status = base; progress = 1
        case "saved":
            DispatchQueue.main.async { self.refresh() }
        default: break
        }
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
                if child.terminationStatus != 0 && self.errorMessage == nil { self.errorMessage = "Il processo è terminato. Controlla i dettagli dell’operazione." }
                if command == "save-account" && child.terminationStatus == 0 { self.refresh() }
            }
        }
    }
}

struct InstallerView: View {
    @StateObject private var model = InstallerModel()
    @State private var dropTarget = false
    @State private var advanced = false
    @State private var details = false

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                Image(systemName: "iphone.and.arrow.forward").font(.system(size: 34)).foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Xcode IPA Installer").font(.title2.bold())
                    Text("Account nativi di Xcode · firma sul tuo Mac").foregroundStyle(.secondary)
                }
                Spacer()
                Text("v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "") · \(model.xcodeVersion)")
                    .font(.caption).foregroundStyle(.secondary)
            }
            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Label(model.ipa?.name ?? "Scegli un’IPA", systemImage: "doc.zipper")
                            .font(.headline)
                        Spacer()
                        Button("Scegli IPA…", action: model.chooseIPA).disabled(model.isBusy)
                    }
                    if let ipa = model.ipa {
                        Text("\(ipa.bundleID) · \(ipa.version)\(ipa.extensions > 0 ? " · \(ipa.extensions) estensioni" : "")")
                            .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        Text(ipa.path).font(.caption).lineLimit(1).truncationMode(.middle).foregroundStyle(.secondary)
                    } else {
                        Text("Trascina qui il file oppure selezionalo dal Mac.").foregroundStyle(.secondary)
                    }
                }.padding(8)
            }
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(dropTarget ? Color.accentColor : .clear, lineWidth: 2))
            .onDrop(of: [UTType.fileURL.identifier], isTargeted: $dropTarget) { items in
                guard !model.isBusy, let item = items.first else { return false }
                _ = item.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                    if let data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                        DispatchQueue.main.async { model.inspect(url) }
                    }
                }
                return true
            }
            GroupBox {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 12) {
                    GridRow {
                        Text("Account Xcode")
                        Picker("Account Xcode", selection: $model.accountID) {
                            ForEach(model.accounts) { Text($0.name).tag($0.id) }
                        }.labelsHidden()
                        Button(action: model.refresh) { Image(systemName: "arrow.clockwise") }.help("Aggiorna account e dispositivi")
                    }
                    GridRow {
                        Text("Team di firma")
                        if model.selectedAccount?.teams.isEmpty == false {
                            Picker("Team di firma", selection: $model.teamID) {
                                ForEach(model.selectedAccount?.teams ?? []) { Text("\($0.name) · \($0.id)").tag($0.id) }
                            }.labelsHidden()
                        } else {
                            Button("Configura il Team ID…", action: model.configureTeam).disabled(model.selectedAccount == nil)
                        }
                    }
                    GridRow {
                        Text("iPhone")
                        Picker("iPhone", selection: $model.deviceID) {
                            ForEach(model.phones) { Text("\($0.name) · iOS \($0.version)").tag($0.id) }
                        }.labelsHidden()
                    }
                }.padding(8)
            }.disabled(model.isBusy)
            Toggle(isOn: $model.increasedMemory) {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Increased Memory Limit").font(.headline)
                    Text("Solo sull’app principale, con lo stesso team della firma. Tutti gli altri componenti vengono comunque firmati e verificati.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.disabled(model.isBusy)
            Text(model.selectedTeam?.personal == true
                 ? "EVA: esclusa per questo Personal Team. Increased Memory Limit resta attivo se selezionato."
                 : "EVA: mantenuta se presente nell’IPA e autorizzata dal team; esclusa automaticamente se Xcode non la supporta.")
                .font(.caption).foregroundStyle(.secondary)
            DisclosureGroup("Opzioni", isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Identificativo di firma: \(model.bundleID.isEmpty ? "seleziona IPA e team" : model.bundleID)")
                        .textSelection(.enabled)
                    Text("Il principale usa l’ID originale più il Team ID dell’account selezionato. Il suffisso non viene duplicato. Gli ID dei componenti seguono il principale e tutti i profili devono appartenere allo stesso team.")
                        .font(.caption).foregroundStyle(.secondary)
                    Toggle("Mantieni le capability originali dell’IPA", isOn: $model.preserveCapabilities)
                    Text("Le capability originali vengono richieste al tuo team; EVA segue la disponibilità del team e le capability memoria restano solo sul principale. Disattivandole puoi perdere App Groups, iCloud o aggiornamenti push.")
                        .font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Button("Apri Xcode", action: model.openXcode)
                        Button("Configura team…", action: model.configureTeam).disabled(model.selectedAccount == nil)
                    }
                }.padding(.top, 6)
            }.disabled(model.isBusy)
            if model.isBusy {
                ProgressView(value: model.isInstalling ? model.progress : nil)
            }
            Text(model.status).font(.callout).foregroundStyle(.secondary).lineLimit(2)
            if let result = model.resultPath {
                HStack {
                    Label(model.resultMessage, systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    Spacer()
                    Button("Mostra IPA") { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: result)]) }
                    if let path = model.verificationPath {
                        Button("Verifica componenti") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                    }
                }.font(.callout)
            }
            if let error = model.errorMessage {
                ScrollView { Text(error).font(.callout).foregroundStyle(.red).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    .frame(maxHeight: 90)
            }
            DisclosureGroup("Dettagli dell’operazione", isExpanded: $details) {
                ScrollView { Text(model.logs.isEmpty ? "Nessuna operazione in corso." : model.logs).font(.system(.caption, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                    .frame(height: 100)
                if let path = model.logPath { Button("Apri log") { NSWorkspace.shared.open(URL(fileURLWithPath: path)) } }
            }
            Spacer(minLength: 0)
            HStack {
                Text("IPA firmata salvata anche in Download.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if model.isInstalling { Button("Annulla", action: model.cancel) }
                else {
                    Button("Solo firma") { model.start(install: false) }.disabled(!model.canRun)
                    Button("Firma e installa") { model.start(install: true) }.buttonStyle(.borderedProminent).disabled(!model.canRun)
                }
            }
        }
        .padding(24).frame(minWidth: 740, idealWidth: 780, minHeight: 650)
        .onAppear(perform: model.refresh)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in model.cancel() }
        .onChange(of: model.accountID) { _ in model.syncTeam() }
        .onChange(of: model.teamID) { _ in model.updateBundleID() }
        .sheet(isPresented: $model.showTeamSetup) {
            VStack(alignment: .leading, spacing: 14) {
                Text("Team dell’account Xcode").font(.title3.bold())
                Text("\(model.selectedAccount?.name ?? "")").foregroundStyle(.secondary)
                TextField("Nome account", text: $model.teamSetupName)
                TextField("Team ID (10 caratteri)", text: $model.teamSetupID)
                Toggle("Personal Team gratuito", isOn: $model.teamSetupPersonal)
                Text("Serve solo quando Xcode non ha ancora salvato il team nella propria cache. Non vengono richieste password o token.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack { Spacer(); Button("Annulla") { model.showTeamSetup = false }; Button("Salva", action: model.saveTeam).keyboardShortcut(.defaultAction) }
            }.padding(24).frame(width: 420)
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
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}
