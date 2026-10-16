#!/bin/zsh
# Offline only: no provisioning updates, account authentication, or device installation.
set -euo pipefail
project_dir="${0:A:h}"
test_temp="$(mktemp -d -t xcode-ipa-tests)"
trap 'rm -rf -- "$test_temp"' EXIT
export PYTHONDONTWRITEBYTECODE=1
export XCODE_IPA_LANGUAGE=en
export XCODE_IPA_PROJECT="$project_dir" XCODE_IPA_TEMP="$test_temp"
xcrun python3 - <<'PY'
import importlib.util, os
from pathlib import Path
root=Path(os.environ['XCODE_IPA_PROJECT'])
spec=importlib.util.spec_from_file_location('installer',root/'Backend/installer.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
units=[dict(path=Path('Fixture.app'),info={},new_id='local.fixture.app',requested={m.MEMORY:True}),
       dict(path=Path('Share.appex'),info={},new_id='local.fixture.app.share',requested={})]
m.write_project(Path(os.environ['XCODE_IPA_TEMP'])/'Project',units,'AAAAAAAAAA')
PY
for scheme in Provision0 Provision1; do
    xcrun xcodebuild -project "$test_temp/Project/Provision.xcodeproj" -scheme "$scheme" \
        -derivedDataPath "$test_temp/DerivedData" \
        -configuration Debug -sdk iphoneos -destination generic/platform=iOS \
        SYMROOT="$test_temp/Products" OBJROOT="$test_temp/Objects" \
        CLANG_MODULE_CACHE_PATH="$test_temp/Modules" MODULE_CACHE_DIR="$test_temp/Modules" \
        SDK_STAT_CACHE_DIR="$test_temp/SDKCaches" SHARED_PRECOMPS_DIR="$test_temp/Precompiled" \
        COMPILER_INDEX_STORE_ENABLE=NO DEVELOPMENT_TEAM=AAAAAAAAAA CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
        build > "$test_temp/$scheme.log" 2>&1 || { cat "$test_temp/$scheme.log"; exit 1; }
done
export XCODE_IPA_FIXTURES="$test_temp/Products/Debug-iphoneos"
xcrun --sdk iphoneos clang -target arm64-apple-ios15.0 -isysroot "$(xcrun --sdk iphoneos --show-sdk-path)" \
    -dynamiclib -x c "$test_temp/Project/main.c" -o "$XCODE_IPA_FIXTURES/libFixture.dylib"
xcrun python3 -m unittest discover -s "$project_dir/Tests" -v
# Synthetic PKCS#12 only. The real user's certificate folder is never imported by tests.
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$test_temp/fixture.key" -out "$test_temp/fixture.pem" \
    -days 2 -subj '/CN=Offline Certificate Fixture/OU=ABCDEFGHIJ' > "$test_temp/openssl.log" 2>&1
openssl x509 -in "$test_temp/fixture.pem" -outform DER -out "$test_temp/fixture.der"
openssl pkcs12 -export -inkey "$test_temp/fixture.key" -in "$test_temp/fixture.pem" \
    -out "$test_temp/fixture.p12" -passout stdin <<< 'offline-test-password'
xcrun swiftc -parse-as-library -swift-version 5 -target arm64-apple-macosx12.0 \
    -module-cache-path "$test_temp/SwiftModules" "$project_dir/Sources/CertificateImport.swift" \
    "$project_dir/Tests/CertificateImportTests.swift" -o "$test_temp/certificate-tests" \
    > "$test_temp/certificate-build.log" 2>&1 || { cat "$test_temp/certificate-build.log"; exit 1; }
"$test_temp/certificate-tests" "$test_temp"
xcrun swiftc -parse-as-library -swift-version 5 -module-cache-path "$test_temp/SwiftModules" \
    "$project_dir/Sources/TeamBundleIDPolicy.swift" "$project_dir/Sources/ImportedBundleIDPolicy.swift" \
    "$project_dir/Tests/TeamBundleIDPolicyTests.swift" -o "$test_temp/policy-tests"
"$test_temp/policy-tests" > "$test_temp/swift-policy.json"
xcrun python3 - <<'PY'
import importlib.util,json,os
from pathlib import Path
root=Path(os.environ['XCODE_IPA_PROJECT'])
spec=importlib.util.spec_from_file_location('installer',root/'Backend/installer.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
values=json.loads((Path(os.environ['XCODE_IPA_TEMP'])/'swift-policy.json').read_text())
for v in values:
    actual=(m.external_bundle_id(v['original'],v['pattern']) if 'pattern' in v else m.team_bundle_id(v['original'],v['team']))
    assert actual==v['bundleID']
print('Swift/Python identifier parity: 3 team-suffix and 4 imported-profile vectors passed')
PY
localization_app="$test_temp/LocalizationTests.app"
mkdir -p "$localization_app/Contents/MacOS" "$localization_app/Contents/Resources"
cp "$project_dir/Resources/Info.plist" "$localization_app/Contents/Info.plist"
ditto --norsrc --noextattr "$project_dir/Resources/en.lproj" "$localization_app/Contents/Resources/en.lproj"
ditto --norsrc --noextattr "$project_dir/Resources/it.lproj" "$localization_app/Contents/Resources/it.lproj"
xcrun swiftc -parse-as-library -swift-version 5 -target arm64-apple-macosx12.0 \
    -module-cache-path "$test_temp/SwiftModules" \
    "$project_dir/Sources/Localization.swift" "$project_dir/Tests/LocalizationTests.swift" \
    -o "$localization_app/Contents/MacOS/XcodeIPAInstaller"
"$localization_app/Contents/MacOS/XcodeIPAInstaller" en -AppleLanguages '(en)'
"$localization_app/Contents/MacOS/XcodeIPAInstaller" it -AppleLanguages '(it)'
"$localization_app/Contents/MacOS/XcodeIPAInstaller" en -AppleLanguages '(ja)'
