# Xcode IPA Installer

A native macOS app for signing IPAs and installing them on an iPhone with **accounts already signed in to Xcode**. Choose your account, team, and device. Signing happens on your Mac, without a remote anisette server.

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

## Interface and languages

The content scrolls independently of the bottom action bar, so signing controls remain accessible in a small window. Account fields stack vertically when space is limited; long identifiers and error messages wrap. The minimum content size is 480 × 480 points.

On macOS 26 or later, action buttons use native **Liquid Glass**. macOS 12–25 use standard native materials and button styles. The interface respects light/dark appearance and Reduce Transparency.

**English is the development and fallback language. Italian is included through standard `en.lproj` / `it.lproj` resources.** macOS chooses the localization from its language preferences; there is no in-app language selector. App status and error messages follow that language too. Diagnostics returned directly by Xcode, codesign, and iOS retain their original language.

## Memory and component signing

- **Increased Memory Limit** is requested and verified **only on the main executable**, using the selected signing team. If enabled but not authorized by the main provisioning profile, signing stops instead of silently dropping it.
- **Extended Virtual Addressing (EVA)** is separate. An original EVA entitlement is retained when supported by the team. It is excluded for Personal Teams; an explicit Xcode refusal for another team triggers one retry without EVA. Increased Memory Limit and the other requested capabilities remain independent.
- All embedded apps, extensions, JIT helpers, frameworks, and libraries are signed and verified from the inside out. Auxiliary processes do not receive memory capabilities. Libraries use the limits of their hosting process.
- Original capabilities are preserved by default and requested from the selected team. WidgetKit/Live Activities metadata and bundle relationships are retained. Keychain groups are mapped consistently; App Groups keep their original names.

These entitlements do not add physical RAM. iOS and the device determine the actual memory available. Live Activities, Dynamic Island, App Groups, iCloud, and push services also depend on the app and the new team’s permissions. Signing does not transfer services owned by the original team. [Memory entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.increased-memory-limit) · [EVA entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.extended-virtual-addressing) · [ActivityKit](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities)

## Accounts, identifiers, and local data

The app reads account metadata from Xcode preferences and selects the team with `DEVELOPMENT_TEAM`. If the same team is associated with multiple signed-in accounts, signing stops because a Team ID alone cannot select a specific member account unambiguously. Distinct Personal Teams can be selected separately.

Passwords, OTPs, session tokens, and private keys are not imported or exported. Xcode handles its session, and codesign uses the macOS Keychain. Xcode still contacts Apple for App IDs, certificates, devices, and provisioning profiles when required. External P12/provisioning-profile import is not implemented.

The main Bundle ID is **the original ID plus the selected signing Team ID**, such as `com.example.App.ABCDEFGHIJ`. An existing matching suffix is not duplicated. Extensions follow the main identifier, for example `com.example.App.ABCDEFGHIJ.Widget`. The backend calculates this independently and verifies team/profile/signature consistency for every process. The report records `bundleIDPolicy: selectedTeamSuffix`.

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

`build.sh` creates an ad-hoc signed app and refuses to overwrite an existing destination. `test.sh` builds unsigned iOS fixtures, exercises real local ad-hoc signing, checks team-suffix parity between Swift/Python, and verifies English, Italian, and unsupported-language fallback in a real resource bundle. Apple provisioning and device installation are intercepted: **tests do not sign in with real accounts or install anything on an iPhone**. Both scripts clean their working directories.

Build checks and offline tests do not establish physical-device acceptance or runtime compatibility with every supported macOS version.

The memory option uses the Apple capability also used by [GetMoreRam](https://github.com/hugeBlack/GetMoreRam); this project does not incorporate its source. The optional ios-deploy utility is a separate project and is not bundled.
