#!/bin/zsh
# Offline only: no provisioning updates, account authentication, or device installation.
set -euo pipefail
project_dir="${0:A:h}"
test_temp="$(mktemp -d -t xcode-ipa-tests)"
trap 'rm -rf -- "$test_temp"' EXIT
export PYTHONDONTWRITEBYTECODE=1
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
xcrun --sdk iphoneos clang -target arm64-apple-ios16.0 -isysroot "$(xcrun --sdk iphoneos --show-sdk-path)" \
    -dynamiclib -x c "$test_temp/Project/main.c" -o "$XCODE_IPA_FIXTURES/libFixture.dylib"
xcrun python3 -m unittest discover -s "$project_dir/Tests" -v
