#!/bin/zsh
set -euo pipefail
project_dir="${0:A:h}"
build_temp="$(mktemp -d -t xcode-ipa-build)"
trap 'rm -rf -- "$build_temp"' EXIT
output_dir="${1:-$HOME/Downloads}"
mkdir -p "$output_dir"
app="$build_temp/Xcode IPA Installer.app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
export COPYFILE_DISABLE=1
cp "$project_dir/Resources/Info.plist" "$app/Contents/Info.plist"
cp "$project_dir/Backend/installer.py" "$app/Contents/Resources/installer.py"
ditto --norsrc --noextattr "$project_dir/Resources/en.lproj" "$app/Contents/Resources/en.lproj"
ditto --norsrc --noextattr "$project_dir/Resources/it.lproj" "$app/Contents/Resources/it.lproj"
for build_arch in arm64 x86_64; do
    xcrun swiftc -parse-as-library -swift-version 5 -target "$build_arch-apple-macosx12.0" -O \
        -module-cache-path "$build_temp/Modules-$build_arch" "$project_dir/Sources/App.swift" "$project_dir/Sources/InstallerView.swift" \
        "$project_dir/Sources/Localization.swift" "$project_dir/Sources/TeamBundleIDPolicy.swift" \
        -o "$build_temp/XcodeIPAInstaller-$build_arch"
done
xcrun lipo -create "$build_temp/XcodeIPAInstaller-arm64" "$build_temp/XcodeIPAInstaller-x86_64" \
    -output "$app/Contents/MacOS/XcodeIPAInstaller"
xcrun swiftc -swift-version 5 -module-cache-path "$build_temp/Modules" \
    "$project_dir/Sources/Icon.swift" -o "$build_temp/icon-builder"
"$build_temp/icon-builder" "$build_temp/AppIcon.iconset"
iconutil -c icns "$build_temp/AppIcon.iconset" -o "$app/Contents/Resources/AppIcon.icns"
xattr -cr "$app"
codesign --force --sign - --timestamp=none "$app"
codesign --verify --deep --strict "$app"
destination="$output_dir/Xcode IPA Installer.app"
if [[ -e "$destination" ]]; then
    print -u2 "The destination already exists: $destination"; exit 1
fi
ditto --norsrc --noextattr "$app" "$destination"
codesign --verify --deep --strict "$destination"
print "Created: $destination"
