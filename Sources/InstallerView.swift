import SwiftUI
import AppKit
import UniformTypeIdentifiers

private struct WindowSizing: NSViewRepresentable {
    final class SizingView: NSView {
        private var configured = false
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard !configured, let window else { return }
            configured = true
            DispatchQueue.main.async {
                let name = NSWindow.FrameAutosaveName("XcodeIPAInstaller.MainWindow")
                if !window.setFrameUsingName(name) {
                    let available = window.screen?.visibleFrame.size ?? NSSize(width: 1024, height: 900)
                    window.setContentSize(NSSize(width: max(480, min(820, available.width - 60)),
                                                 height: max(480, min(780, available.height - 80))))
                    window.center()
                }
                window.setFrameAutosaveName(name)
            }
        }
    }
    func makeNSView(context: Context) -> NSView { SizingView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

private struct ActionStyle: ViewModifier {
    var prominent = false
    @ViewBuilder func body(content: Content) -> some View {
        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            if prominent { content.buttonStyle(.glassProminent) }
            else { content.buttonStyle(.glass) }
        } else {
            if prominent { content.buttonStyle(.borderedProminent) }
            else { content.buttonStyle(.bordered) }
        }
        #else
        if prominent { content.buttonStyle(.borderedProminent) }
        else { content.buttonStyle(.bordered) }
        #endif
    }
}

private struct Surface<Content: View>: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    private let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        content.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            .background {
                if reduceTransparency {
                    RoundedRectangle(cornerRadius: 20, style: .continuous).fill(Color(nsColor: .controlBackgroundColor))
                } else {
                    RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.regularMaterial)
                }
            }
            .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(.primary.opacity(0.06)))
    }
}

struct InstallerView: View {
    @StateObject private var model = InstallerModel()
    @State private var dropTarget = false
    @State private var advanced = false
    @State private var details = false
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                ScrollView(.vertical, showsIndicators: true) {
                    VStack(alignment: .leading, spacing: 20) {
                        header
                        ipaCard
                        accountsCard(wide: geometry.size.width >= 760)
                        memoryCard
                        optionsCard
                        statusView
                        if let result = model.resultPath { resultCard(result) }
                        if let error = model.errorMessage { errorCard(error) }
                        activityCard
                    }
                    .padding(geometry.size.width < 600 ? 20 : 30)
                    .frame(maxWidth: 940)
                    .frame(maxWidth: .infinity)
                }
                Divider().opacity(0.4)
                footer(compact: geometry.size.width < 690)
            }
            .background {
                ZStack {
                    Color(nsColor: .windowBackgroundColor)
                    if !reduceTransparency {
                        LinearGradient(colors: [.accentColor.opacity(0.08), .clear], startPoint: .topLeading, endPoint: .bottomTrailing)
                    }
                }.ignoresSafeArea()
            }
        }
        .frame(minWidth: 480, idealWidth: 820, minHeight: 480, idealHeight: 780)
        .background(WindowSizing().frame(width: 0, height: 0))
        .toolbar {
            ToolbarItemGroup {
                Button(action: model.openXcode) { Image(systemName: "hammer") }
                    .help(L10n.text("Open Xcode"))
                    .accessibilityLabel(L10n.text("Open Xcode"))
                Button(action: model.refresh) { Image(systemName: "arrow.clockwise") }
                    .help(L10n.text("Refresh accounts and devices"))
                    .accessibilityLabel(L10n.text("Refresh accounts and devices"))
                    .disabled(model.isBusy)
            }
        }
        .onAppear(perform: model.refresh)
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in model.cancel() }
        .onChange(of: model.accountID) { _ in model.syncTeam() }
        .onChange(of: model.teamID) { _ in model.updateBundleID() }
        .sheet(isPresented: $model.showTeamSetup) { teamSheet }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: "iphone.and.arrow.forward")
                    .font(.system(size: 21, weight: .medium)).foregroundStyle(Color.accentColor)
                Text("Xcode IPA Installer").font(.system(size: 14, weight: .medium)).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Text("v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")")
                    .font(.caption).foregroundStyle(.tertiary)
            }
            Text(L10n.text("Sign & install")).font(.system(size: 30, weight: .semibold, design: .default))
            Text(L10n.text("Your IPA, signed with your Xcode account."))
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }.padding(.bottom, 2)
    }

    private var ipaCard: some View {
        Surface {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 14) {
                    Image(systemName: model.ipa == nil ? "square.and.arrow.down" : "doc.zipper")
                        .font(.system(size: 25, weight: .light)).foregroundStyle(Color.accentColor)
                        .frame(width: 50, height: 50)
                        .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                    VStack(alignment: .leading, spacing: 5) {
                        Text(model.ipa?.name ?? L10n.text("Choose your IPA"))
                            .font(.title3.weight(.semibold)).fixedSize(horizontal: false, vertical: true)
                        if let ipa = model.ipa {
                            Text("\(ipa.version) · \(L10n.text("Extensions: %d", ipa.extensions))")
                                .font(.caption).foregroundStyle(.secondary)
                        } else {
                            Text(L10n.text("Drop a file here or choose one from your Mac."))
                                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                if let ipa = model.ipa {
                    Text(ipa.bundleID).font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    Text(ipa.path).font(.caption).foregroundStyle(.tertiary)
                        .lineLimit(1).truncationMode(.middle).help(ipa.path)
                }
                Button(L10n.text(model.ipa == nil ? "Choose IPA…" : "Change IPA…"), action: model.chooseIPA)
                    .modifier(ActionStyle()).disabled(model.isBusy)
            }
        }
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(dropTarget ? Color.accentColor : .clear, lineWidth: 2))
        .onDrop(of: [UTType.fileURL.identifier], isTargeted: $dropTarget) { items in
            guard !model.isBusy, let item = items.first else { return false }
            _ = item.loadDataRepresentation(forTypeIdentifier: UTType.fileURL.identifier) { data, _ in
                if let data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                    DispatchQueue.main.async { model.inspect(url) }
                }
            }
            return true
        }
    }

    private func accountsCard(wide: Bool) -> some View {
        Surface {
            VStack(alignment: .leading, spacing: 18) {
                sectionTitle("Signing", symbol: "person.crop.circle")
                selectionField("Xcode account", selection: $model.accountID,
                               options: model.accounts.map { ($0.id, $0.name) }, empty: "No Xcode accounts")
                if wide {
                    HStack(alignment: .top, spacing: 18) { teamField; phoneField }
                } else {
                    teamField
                    phoneField
                }
                if model.accounts.isEmpty {
                    Text(L10n.text("Add your account in Xcode → Settings → Apple Accounts, then refresh."))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
            }
        }.disabled(model.isBusy)
    }

    private var teamField: some View {
        VStack(alignment: .leading, spacing: 7) {
            if model.selectedAccount?.teams.isEmpty == false {
                selectionField("Signing team", selection: $model.teamID,
                               options: (model.selectedAccount?.teams ?? []).map { ($0.id, $0.name + " · " + $0.id) }, empty: "Choose a team")
            } else {
                Text(L10n.text("Signing team")).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Button(L10n.text("Set up team…"), action: model.configureTeam)
                    .disabled(model.selectedAccount == nil).frame(maxWidth: .infinity, alignment: .leading)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var phoneField: some View {
        selectionField("iPhone", selection: $model.deviceID,
                       options: model.phones.map { ($0.id, $0.name + ($0.version.isEmpty ? "" : " · iOS " + $0.version)) },
                       empty: "Connect an iPhone")
    }

    private func selectionField(_ title: String, selection: Binding<String>, options: [(String, String)], empty: String) -> some View {
        let selected = options.first { $0.0 == selection.wrappedValue }?.1 ?? L10n.text(empty)
        return VStack(alignment: .leading, spacing: 7) {
            Text(L10n.text(title)).font(.caption.weight(.medium)).foregroundStyle(.secondary)
            Menu {
                ForEach(options, id: \.0) { item in
                    Button {
                        selection.wrappedValue = item.0
                    } label: {
                        if item.0 == selection.wrappedValue { Label(item.1, systemImage: "checkmark") }
                        else { Text(item.1) }
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    Text(selected).lineLimit(1).truncationMode(.middle).frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .semibold)).foregroundStyle(.secondary)
                }.padding(.horizontal, 12).padding(.vertical, 10)
                    .background(.primary.opacity(0.045), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .contentShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            .menuStyle(.borderlessButton).menuIndicator(.hidden)
            .accessibilityLabel(L10n.text(title)).accessibilityValue(selected)
            .disabled(options.isEmpty)
        }.frame(maxWidth: .infinity, alignment: .leading)
    }

    private var memoryCard: some View {
        Surface {
            VStack(alignment: .leading, spacing: 14) {
                Toggle(isOn: $model.increasedMemory) {
                    Label(L10n.text("Increased Memory Limit"), systemImage: "memorychip")
                        .font(.system(size: 14, weight: .semibold))
                }.toggleStyle(.switch).disabled(model.isBusy)
                Text(L10n.text("Applies to the main app, using the signing team. Every component is still signed and verified."))
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Divider().opacity(0.5)
                Label(L10n.text(model.selectedTeam?.personal == true
                      ? "EVA is unavailable for Personal Teams. Memory Limit stays independent."
                      : "EVA is kept when requested by the IPA and supported by the team."), systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var optionsCard: some View {
        Surface {
            DisclosureGroup(isExpanded: $advanced) {
                VStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L10n.text("Signing identifier")).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                        Text(model.bundleID.isEmpty ? L10n.text("Choose an IPA and a team") : model.bundleID)
                            .font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(L10n.text("The selected Team ID is appended once. Extension identifiers follow the main app."))
                            .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    }
                    Divider().opacity(0.5)
                    Toggle(L10n.text("Keep original capabilities"), isOn: $model.preserveCapabilities).toggleStyle(.switch)
                    Text(L10n.text("Requests the IPA’s capabilities from your team. Turning this off may affect App Groups, iCloud, or push notifications."))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Button(L10n.text("Set up team…"), action: model.configureTeam).disabled(model.selectedAccount == nil)
                }.padding(.top, 16)
            } label: {
                Label(L10n.text("Options"), systemImage: "slider.horizontal.3").font(.system(size: 14, weight: .medium))
            }
        }.disabled(model.isBusy)
    }

    private var statusView: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.isBusy {
                if model.isInstalling { ProgressView(value: model.progress) }
                else { ProgressView().controlSize(.small) }
            }
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: model.errorMessage != nil ? "exclamationmark.circle" : "circle.fill")
                    .font(.system(size: model.errorMessage != nil ? 12 : 6))
                    .foregroundStyle(model.errorMessage != nil ? Color.orange : Color.accentColor)
                    .frame(width: 12, height: 16)
                Text(model.status).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }.padding(.horizontal, 4)
    }

    private func resultCard(_ path: String) -> some View {
        Surface {
            VStack(alignment: .leading, spacing: 14) {
                Label(L10n.text("Ready"), systemImage: "checkmark.circle.fill").font(.headline).foregroundStyle(.green)
                Text(model.resultMessage).font(.callout).fixedSize(horizontal: false, vertical: true)
                HStack {
                    Button(L10n.text("Show IPA")) { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)]) }
                    if let report = model.verificationPath {
                        Button(L10n.text("Verification report")) { NSWorkspace.shared.open(URL(fileURLWithPath: report)) }
                    }
                }.controlSize(.small)
            }
        }
    }

    private func errorCard(_ message: String) -> some View {
        Surface {
            VStack(alignment: .leading, spacing: 10) {
                Label(L10n.text("Couldn’t complete the operation"), systemImage: "exclamationmark.triangle")
                    .font(.headline).foregroundStyle(.orange)
                Text(message).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var activityCard: some View {
        Surface {
            DisclosureGroup(isExpanded: $details) {
                VStack(alignment: .leading, spacing: 12) {
                    ScrollView([.vertical, .horizontal]) {
                        Text(model.logs.isEmpty ? L10n.text("No activity yet.") : model.logs)
                            .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(height: 160)
                    if let path = model.logPath {
                        Button(L10n.text("Open log")) { NSWorkspace.shared.open(URL(fileURLWithPath: path)) }
                    }
                }.padding(.top, 14)
            } label: {
                Label(L10n.text("Activity"), systemImage: "text.alignleft").font(.system(size: 14, weight: .medium))
            }
        }
    }

    private func footer(compact: Bool) -> some View {
        HStack(spacing: 12) {
            if !compact {
                VStack(alignment: .leading, spacing: 3) {
                    Text(L10n.text("Saved to Downloads")).font(.caption.weight(.medium))
                    if !model.xcodeVersion.isEmpty { Text(model.xcodeVersion).font(.caption2).foregroundStyle(.secondary) }
                }
                Spacer(minLength: 8)
            }
            if model.isInstalling {
                Spacer(minLength: 0)
                Button(L10n.text("Cancel"), action: model.cancel).modifier(ActionStyle())
            } else {
                if compact { Spacer(minLength: 0) }
                Button(L10n.text("Sign only")) { model.start(install: false) }
                    .modifier(ActionStyle()).disabled(!model.canRun)
                Button { model.start(install: true) } label: {
                    Label(L10n.text("Sign & install"), systemImage: "arrow.down.to.line")
                }.modifier(ActionStyle(prominent: true)).disabled(!model.canRun)
            }
        }
        .controlSize(.large)
        .padding(.horizontal, compact ? 20 : 30).padding(.vertical, 18)
        .frame(maxWidth: 940).frame(maxWidth: .infinity)
        .background {
            if reduceTransparency { Color(nsColor: .windowBackgroundColor) }
            else { Rectangle().fill(.bar) }
        }
    }

    private func sectionTitle(_ key: String, symbol: String) -> some View {
        Label(L10n.text(key), systemImage: symbol).font(.system(size: 14, weight: .semibold))
    }

    private var teamSheet: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text(L10n.text("Set up your team")).font(.title2.weight(.semibold))
                    Text(model.selectedAccount?.name ?? "").font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L10n.text("Account name")).font(.caption).foregroundStyle(.secondary)
                        TextField(L10n.text("Account name"), text: $model.teamSetupName).textFieldStyle(.roundedBorder)
                    }
                    VStack(alignment: .leading, spacing: 6) {
                        Text(L10n.text("Team ID (10 characters)")).font(.caption).foregroundStyle(.secondary)
                        TextField(L10n.text("Team ID (10 characters)"), text: $model.teamSetupID).textFieldStyle(.roundedBorder)
                    }
                    Toggle(L10n.text("Personal Team (free account)"), isOn: $model.teamSetupPersonal)
                    Text(L10n.text("Only needed when Xcode hasn’t cached the team. No passwords or session tokens are requested."))
                        .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }.padding(28)
            }
            Divider()
            HStack {
                Spacer()
                Button(L10n.text("Cancel")) { model.showTeamSetup = false }.keyboardShortcut(.cancelAction)
                Button(L10n.text("Save"), action: model.saveTeam).keyboardShortcut(.defaultAction)
                    .modifier(ActionStyle(prominent: true))
                    .disabled(model.teamSetupID.trimmingCharacters(in: .whitespacesAndNewlines).uppercased().range(of: "^[A-Z0-9]{10}$", options: .regularExpression) == nil)
            }.padding(20)
        }.frame(minWidth: 380, idealWidth: 460, minHeight: 420, idealHeight: 460)
    }
}
