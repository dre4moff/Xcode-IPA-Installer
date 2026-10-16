# Set up Xcode IPA Installer

[Italian guide](SETUP.it.md) · [Back to README](../README.md)

## 1. Sign in inside Xcode

Install full Xcode, launch it, and finish its initial setup, including the iOS tools. Use a version compatible with your macOS and the iPhone’s iOS release. Command Line Tools alone are not sufficient.

1. In Xcode, press **⌘ ,** or choose **Xcode → Settings…** from the menu bar.
2. Select **Apple Accounts** in the sidebar. Older releases call this **Accounts** and use **Preferences…** instead of Settings.
3. Click **+** or **Add Apple Account…** and complete sign-in.
4. Enter your verification code in Xcode. Confirm that the account and its team appear in the list.

![Where to sign in to Xcode](images/xcode-apple-accounts.svg)

*Illustrative diagram, not a screenshot of a real account. Controls may differ between Xcode versions.*

You can add multiple accounts without changing the Mac’s main iCloud account. The installer reuses Xcode’s session; it does not copy tokens or ask for passwords. If Xcode reports an expired session or pending agreement, complete that step in Xcode and refresh the installer. [Apple’s account guide](https://help.apple.com/xcode/mac/current/en.lproj/devaf282080a.html)

## 2. Connect your iPhone

Connect over USB, unlock the iPhone, and accept **Trust This Computer** if prompted. In Xcode, open **Window → Devices and Simulators** to check pairing and device readiness.

On iOS 16 or later, enable **Settings → Privacy & Security → Developer Mode**, then complete the restart and confirmation. If the option is missing, pair with Xcode first. Refresh the installer. Wi-Fi connections require prior pairing and support from Xcode. [Apple’s Developer Mode guide](https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device)

**Sign only** also requires a selected iPhone because the development profile is checked against that device.

### macOS 12 and older Xcode versions

The app runs on macOS 12+. Liquid Glass requires macOS 26; earlier versions use native material and standard buttons.

Device discovery uses `devicectl` when Xcode provides it. With older Xcode versions, the app uses `xcdevice`; installing to **iOS 16 and earlier** additionally requires the separate [ios-deploy](https://github.com/ios-control/ios-deploy) utility:

```sh
brew install ios-deploy
```

Use a USB cable. The installer checks for this utility before provisioning an installation request. It does not download or install it automatically. You can still export an IPA with **Sign only**. Devices on iOS 17+ require Xcode 15+ and a compatible macOS release. A fallback cannot supply device support that an older Xcode does not have. Consult [Apple’s Xcode system requirements](https://developer.apple.com/xcode/system-requirements).

## 3. Set up the signing team

Open the app from [Releases](https://github.com/dre4moff/Xcode-IPA-Installer/releases/latest). Choose your Xcode account and signing team. If Xcode has not cached team metadata, use **Set up team…**, also available under **Options**.

Enter a recognizable account name and the **10-character Team ID**. Keep **Personal Team (free account)** enabled for a free team. This only associates local metadata; it does not sign in or create a team.

To find the Team ID:

- For an Apple Developer Program membership, open the team’s membership details in [Apple Developer Account](https://developer.apple.com/account).
- For a Personal Team, create a small **iOS → App** project in Xcode. Select its target → **Signing & Capabilities**, enable **Automatically manage signing**, and select your team. In **Build Settings**, find **Development Team** / `DEVELOPMENT_TEAM`. If only the name is shown, inspect `project.pbxproj` with a text editor and look for `DEVELOPMENT_TEAM = ...;`. You do not need to build or install this project.

For an existing configured project, you can also run:

```sh
xcrun xcodebuild -project "/path/to/Example.xcodeproj" \
  -scheme "Example" -showBuildSettings |
  awk '/ DEVELOPMENT_TEAM = / { print $3; exit }'
```

Replace the path and scheme. The code in parentheses in a certificate’s display name is not necessarily the Team ID.

If the same team appears under multiple authenticated accounts, the installer reports the ambiguity. Choose a distinct Personal Team or resolve the account association in Xcode; `DEVELOPMENT_TEAM` does not choose a specific member of a shared team.

## 4. Sign or install

1. Choose or drop the IPA.
2. Check account, team, and iPhone.
3. Enable **Increased Memory Limit** when required. It applies to the main executable only; every component is still signed.
4. Choose **Sign only** to export, or **Sign & install** to export and install.

In small windows, scroll to reach all options. Signing actions stay pinned at the bottom. **Options** shows the final signing identifier. **Activity** contains operation details and an **Open log** button. Errors and reports remain selectable.

The main identifier is `<original Bundle ID>.<selected Team ID>`. A matching suffix is not added twice; extensions follow the main identifier. Every process profile and signature must match the team. Updates require the same complete identifier and compatible signing. An app previously installed with another ID remains a separate app.

The source file is unchanged. **Downloads** receives the signed IPA and a `.verification.json` report listing component signatures, memory policy, team, and expiry. These may already exist if installation fails afterward.

Handle any macOS Keychain request personally. If iOS asks to trust the developer, check **Settings → General → VPN & Device Management**.

## 5. Memory capabilities

| Setting | Behavior |
| --- | --- |
| Increased Memory Limit on | Requires `com.apple.developer.kernel.increased-memory-limit` in the main profile and final signature. Missing authorization stops signing. |
| Increased Memory Limit off | Excludes that entitlement from the main signature. |
| Original EVA, supported team | Keeps `com.apple.developer.kernel.extended-virtual-addressing` on the main app. |
| EVA unavailable | Excludes it for Personal Teams; an explicit refusal on another team causes one retry without EVA. Increased Memory Limit remains requested. |
| Auxiliary components | Embedded apps, extensions, and JIT helpers are signed without memory capabilities. Frameworks/libraries run within the host process. |

The policy applies to all IPAs. EVA is not added when the original app does not request it. With **Keep original capabilities**, Xcode is asked to enable the original permissions. An explicit refusal of optional capabilities triggers a retry without those permissions, for the main app and each extension independently. Supported permissions remain requested and the generated profile is checked before signing. App Group identifiers explicitly reported as unavailable to the new team are removed individually, preserving other requested groups. Exclusions appear in the log and report; dependent services such as iCloud, shared containers, or push may not work. Required memory, signing identity, and device/expiry checks remain mandatory. These options do not add physical RAM.

## 6. Language and appearance

macOS chooses English or Italian using its standard language preferences. Unsupported languages fall back to English; there is no in-app selector. Light/dark appearance and Reduce Transparency follow system settings. Xcode and iOS diagnostics may use their own language.

## Import a certificate instead of using an Xcode account

Choose **Signing → Imported certificate → Import certificate…**. Choose the P12/PFX and one or more mobileprovision files, or their folder, then enter the P12 password in the secure field. Leave it empty if there is no password. Text files are ignored. Select the main profile after import, connect a covered iPhone, and choose Sign only or Sign & install. No Apple Account sign-in is required in this mode.

The private key enters your macOS default Keychain; the password is not saved. Allow any Keychain request yourself. Add extension profiles for that certificate through the management menu. Original app and extension Bundle IDs are preserved. The profile-authorized signing App ID is separate: an explicit profile can supply its fixed application-identifier without changing installed app identities. Specific matching extension profiles are preferred; otherwise the selected imported profile can be reused. This manual signing behavior differs from Xcode automatic profile matching. Unsupported optional capabilities are excluded and reported by default; Options → Require all original capabilities makes them errors. Identity and requested Increased Memory Limit remain mandatory, and every component is signed. [Full profile and JIT behavior](../README.md#imported-certificates-and-paid-profiles).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Missing account / expired session | Sign in under Xcode → Settings → Apple Accounts, then refresh. |
| Missing team | Associate the correct Team ID and Personal Team status under Set up team. |
| Missing iPhone | USB, trust, unlock state, Xcode’s device support, Devices and Simulators. |
| Missing ios-deploy | On older Xcode, install the utility for iOS 16 or earlier, or use Sign only. |
| Unsupported modern iPhone on old macOS | Use a compatible Xcode/macOS combination; the legacy path cannot install on iOS 17+. |
| Bundle Identifier unavailable | Apple has not authorized that team-suffixed ID. The app stops without generating alternate IDs. |
| Free-profile app limit | The full error lists occupied identifiers. Updating requires the existing app’s complete ID and compatible signing. A different ID uses another slot. |
| Increased Memory Limit not authorized | Signing stops instead of silently dropping the requested entitlement. Check the selected team/profile. |
| Personal Team does not support capabilities | Update to version 1.2.3 or later. Explicitly refused optional capabilities are excluded automatically, without skipping extensions or required memory. Features requiring excluded permissions may not work. |
| Encrypted IPA / Watch component | These packages are unsupported. |
| App installs but a feature fails | Signing cannot guarantee app code, remote services, Live Activities, Dynamic Island, or JIT. Inspect logs and the report. |

Apple’s provisioning limits and expiry rules apply. Keep the source IPA and local account metadata. Remove personal identifiers from logs and verification reports before sharing them.
