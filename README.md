# Xcode IPA Installer

A native macOS app for signing IPAs and installing them on an iPhone with **accounts already signed in to Xcode** or **imported P12 certificates and provisioning profiles**. Every code component is signed locally, without a remote anisette server.

**[Download](https://github.com/dre4moff/Xcode-IPA-Installer/releases/latest)** · **[Setup guide](docs/SETUP.md)** · [Italiano](README.it.md) · [MIT license](LICENSE)

![Where to add your Apple Account in Xcode](docs/images/xcode-apple-accounts.svg)

## Getting started

1. Install **full Xcode**, open it, and finish installing its iOS tools. Command Line Tools alone are not enough.
2. Open **Xcode → Settings… → Apple Accounts**, then **+ / Add Apple Account…**. Complete sign-in and two-factor authentication inside Xcode. Older versions use **Preferences… → Accounts**.
3. Connect your iPhone over USB, trust the Mac, and enable **Settings → Privacy & Security → Developer Mode** on iOS 16 or later.
4. Open Xcode IPA Installer and refresh. Choose the Xcode account, its signing team, and the iPhone. If the team is missing, choose **Options → Set up team…**; see the [guide](docs/SETUP.md#3-set-up-the-signing-team).
5. Choose or drop an IPA. Enable **Increased Memory Limit** if needed, then choose **Sign only** or **Sign & install**.

The app supports **macOS 12 and later on Apple Silicon and Intel** with a universal binary. Use a full Xcode version that supports your macOS and iPhone’s iOS version. Modern devices use `devicectl` from Xcode 15 or later. Older Xcode versions use `xcdevice` for discovery and an independently installed [ios-deploy](https://github.com/ios-control/ios-deploy) for installation on **iOS 16 or earlier over USB**. Install that optional tool with `brew install ios-deploy`, or use **Sign only**. An older Mac/Xcode cannot deploy to a newer iPhone simply by using this fallback. [Apple’s Xcode compatibility table](https://developer.apple.com/xcode/system-requirements)

Builds are signed ad hoc and are not notarized. If macOS blocks the download, verify its source and follow [Apple’s instructions for opening a trusted app](https://support.apple.com/en-us/102445).

## Imported certificates and paid profiles

Choose **Signing → Imported certificate → Import certificate…**. Select a folder containing a `.p12` / `.pfx` and `.mobileprovision` files, or choose them individually. Enter the P12 password in the app, leaving it empty if none. Text files in the folder are never read. Certificate/profile matching is validated before the private key is imported into the macOS default Keychain. The password is passed through a private pipe and is not saved. Handle any macOS Keychain prompt personally.

Select the certificate and **Main app profile**. Extension-specific matching profiles are preferred when available; otherwise the selected imported profile can be reused with its authorized signing App ID. Use **Manage certificate → Add profiles…** to add profiles belonging to that certificate. **Forget certificate** removes the app’s profile copies and metadata; the signing identity remains in Keychain Access because it may be shared with other tools. The original files are untouched.

- **Original Bundle IDs are always preserved** for the main app and every extension. They identify distinct installed apps. The code signature’s `application-identifier` is handled separately: an explicit imported profile supplies its authorized signing App ID without renaming the bundles. Multiple different Bundle IDs may therefore use one fixed signing App ID in this manual-signing workflow.
- **Wildcard profiles:** the signature uses a concrete App ID within the authorized pattern. Compatible Bundle IDs are used directly; other IDs produce a concrete signing App ID under the profile’s allowed prefix. The actual Bundle IDs remain unchanged.
- The certificate, private key, team, expiry, device coverage, App ID, and requested capabilities must match for every app/extension. An unauthorized claimed signing App ID, invalid identity, wrong device, or missing required memory authorization stops signing before installation. Store profiles from different certificates separately.
- By default, supported original capabilities are retained and unsupported optional capabilities (such as CarPlay) are excluded and listed in the log/report. Supported entries in shared keychain groups are retained, with an authorized fallback derived from the signing App ID when needed. Enable **Options → Require all original capabilities** to stop instead of excluding them. A feature whose permission is excluded may no longer work.
- Increased Memory Limit is required on the main app when its checkbox is on. Original EVA is retained only when authorized by that process’s profile. Auxiliary processes remain signed without memory entitlements.
- `get-task-allow` follows **each actual provisioning profile**, including profiles that permit debugging. This preserves eligibility for a compatible JIT tool; it does not enable JIT by itself or turn a distribution profile into a development profile. Device, OS, app, pairing, and the chosen JIT method still matter. [Apple’s provisioning-profile documentation](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles)

This separation is based on the profile-entitlement handling in [Zsign used by Feather](https://github.com/claration/zsign/blob/package/src/openssl.cpp) and checked against an ESign-produced IPA. It is a manual-signing compatibility workflow; Xcode automatic provisioning retains its stricter Bundle ID/App ID correspondence. App-specific services, keychain sharing, widgets, Live Activities, push, and account sessions may require more than a valid signature. Device installation/runtime acceptance remains separate.

No Apple Account sign-in or remote anisette service is used in this mode, and the app does not request new profiles from Apple. Full Xcode is still needed for the bundled Python runtime and device tools. Expiry, device limits, revocation, and supported capabilities come from the imported assets. The report records the distinct `bundleID` and signed `applicationIdentifier` for every process, plus `signingMode: imported`, `bundleIDPolicy: originalIdentifiers`, the certificate fingerprint, profile UUIDs, and each process’s debugging/EVA status.

## Interface and languages

The content scrolls independently of the bottom action bar, so signing controls remain accessible in a small window. Account fields stack vertically when space is limited; long identifiers and error messages wrap. The minimum content size is 480 × 480 points.

On macOS 26 or later, action buttons use native **Liquid Glass**. macOS 12–25 use standard native materials and button styles. The interface respects light/dark appearance and Reduce Transparency.

**English is the development and fallback language. Italian is included through standard `en.lproj` / `it.lproj` resources.** macOS chooses the localization from its language preferences; there is no in-app language selector. App status and error messages follow that language too. Diagnostics returned directly by Xcode, codesign, and iOS retain their original language.

## Memory and component signing

- **Increased Memory Limit** is requested and verified **only on the main executable**, using the selected signing team. If enabled but not authorized by the main provisioning profile, signing stops instead of silently dropping it.
- **Extended Virtual Addressing (EVA)** is separate. An original EVA entitlement is retained when supported by the team. It is excluded for Personal Teams; an explicit Xcode refusal for another team removes EVA from the provisioning request. Increased Memory Limit remains independently required when selected.
- All embedded apps, extensions, JIT helpers, frameworks, and libraries are signed and verified from the inside out. Auxiliary processes do not receive memory capabilities. Libraries use the limits of their hosting process.
- Original capabilities are requested from the selected Xcode team. If Xcode explicitly refuses optional capabilities, provisioning retries after removing only those permissions. The generated profile then determines which requested capabilities can be signed; exclusions are listed per process in the log and verification report. Authentication, identity, device limits, and missing required Increased Memory Limit still stop the operation. WidgetKit/Live Activities metadata and bundle relationships are retained. Keychain groups are mapped consistently. App Groups keep their original names when available; only group identifiers explicitly refused as unavailable to the new team are removed. The report also lists these excluded values.

These entitlements do not add physical RAM. iOS and the device determine the actual memory available. Live Activities, Dynamic Island, App Groups, iCloud, and push services also depend on the app and the new team’s permissions. Signing does not transfer services owned by the original team. [Memory entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.increased-memory-limit) · [EVA entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.extended-virtual-addressing) · [ActivityKit](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities)

## Accounts, identifiers, and local data

The app reads account metadata from Xcode preferences and selects the team with `DEVELOPMENT_TEAM`. If the same team is associated with multiple signed-in accounts, signing stops because a Team ID alone cannot select a specific member account unambiguously. Distinct Personal Teams can be selected separately.

In Xcode mode, passwords, OTPs, session tokens, and private keys are not read from Xcode. Xcode handles its session, and codesign uses the macOS Keychain. Xcode still contacts Apple for App IDs, certificates, devices, and provisioning profiles when required. In imported-certificate mode, only the explicitly selected P12 is imported via native Security APIs; its password is not retained. App-owned profile copies and certificate metadata are stored under `Signing Assets` in the local support directory.

In **Xcode mode**, the main Bundle ID is **the original ID plus the selected signing Team ID**, such as `com.example.App.ABCDEFGHIJ`. An existing matching suffix is not duplicated. Extensions follow the main identifier, for example `com.example.App.ABCDEFGHIJ.Widget`. The backend calculates this independently and verifies team/profile/signature consistency for every process. The report records `bundleIDPolicy: selectedTeamSuffix`.

Updating an existing app requires the same complete Bundle ID and a compatible signature. A copy using another identifier remains a separate app; data is not migrated between IDs. Apple must authorize the resulting identifier for your team.

The source IPA remains unchanged. Signed IPAs and `.verification.json` reports are exported to **Downloads** before installation. Temporary working files and DerivedData are removed on completion or cancellation. Logs remain available under **Activity → Open log**. Account metadata and logs live in `~/Library/Application Support/Xcode IPA Installer`, falling back to `~/.local/share/XcodeIPAInstaller` when needed. Review logs/reports before sharing: they may contain account names, Team IDs, device identifiers, and local paths.

Encrypted App Store IPAs, Watch packages, and standalone executables without a dedicated app/extension bundle and profile are unsupported. Apple’s provisioning limits and expiry rules still apply.

## Build and verify

SwiftUI/AppKit, Python 3.8+ from Xcode, and native macOS/Xcode tools are used. A modern Xcode SDK is needed to build native Liquid Glass; builds made with an older SDK use the material fallback. The app deployment target is macOS 12.

```sh
git clone https://github.com/dre4moff/Xcode-IPA-Installer.git
cd Xcode-IPA-Installer
zsh test.sh
zsh build.sh "$HOME/Downloads/Xcode-IPA-Installer-build"
```

`build.sh` creates an ad-hoc signed app and refuses to overwrite an existing destination. `test.sh` builds unsigned iOS fixtures, exercises real local ad-hoc signing for Xcode and imported-profile paths, checks Xcode identifier policy parity between Swift/Python, validates a synthetic password-protected P12 in isolated temporary keychains, and verifies English, Italian, and unsupported-language fallback in a real resource bundle. Apple provisioning and device installation are intercepted: **tests do not sign in with real accounts or install anything on an iPhone**. Both scripts clean their working directories.

Build checks and offline tests do not establish physical-device acceptance or runtime compatibility with every supported macOS version.

The memory option uses the Apple capability also used by [GetMoreRam](https://github.com/hugeBlack/GetMoreRam); this project does not incorporate its source. The optional ios-deploy utility is a separate project and is not bundled.
