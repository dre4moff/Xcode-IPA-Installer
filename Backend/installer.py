#!/usr/bin/env python3
"""Local IPA signing using the selected, existing Xcode account. Python 3.8+."""
import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET

MEMORY = "com.apple.developer.kernel.increased-memory-limit"
EVA = "com.apple.developer.kernel.extended-virtual-addressing"
MEMORY_ENTITLEMENTS = {MEMORY, EVA,
                       "com.apple.developer.kernel.increased-debugging-memory-limit"}
_STANDARD_SUPPORT = Path.home() / "Library/Application Support/Xcode IPA Installer"
SUPPORT = (_STANDARD_SUPPORT if os.access(_STANDARD_SUPPORT if _STANDARD_SUPPORT.exists() else _STANDARD_SUPPORT.parent, os.W_OK)
           else Path.home() / ".local/share/XcodeIPAInstaller")
ACCOUNT_FILE = SUPPORT / "accounts.json"
ACTIVE_PROCESS = None
CANCELLED = False
LANGUAGE = "it" if os.environ.get("XCODE_IPA_LANGUAGE", "en").split("-", 1)[0] == "it" else "en"


def load_translations(language):
    candidates = [Path(__file__).parent / (language + ".lproj/Backend.json"),
                  Path(__file__).parent.parent / ("Resources/" + language + ".lproj/Backend.json")]
    for path in candidates:
        if path.is_file():
            return json.loads(path.read_text())
    return {}


TRANSLATIONS = load_translations(LANGUAGE)


def tr(message):
    return TRANSLATIONS.get(message, message)


class InstallerError(Exception):
    pass


def emit(kind, **values):
    print(json.dumps(dict(type=kind, **values), ensure_ascii=False), flush=True)


def redact(text):
    return re.sub(r'(?i)((?:password|access[_-]?token|auth[_-]?token|identity[_-]?token)\s*[:=]\s*)[^\s,;]+',
                  r'\1<omesso>', text)


def run(args, env=None, log=None, timeout=180, quiet=False):
    global ACTIVE_PROCESS
    if CANCELLED:
        raise InstallerError(tr('Operation cancelled.'))
    process = subprocess.Popen([str(x) for x in args], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, env=env, start_new_session=True)
    ACTIVE_PROCESS = process
    try:
        try:
            output = process.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            raise InstallerError(tr('Timed out: ') + Path(str(args[0])).name)
        text = output.decode("utf-8", errors="replace")
        if log:
            with log.open("a") as f:
                f.write("\n$ " + " ".join(str(x) for x in args) + "\n" + redact(text))
        if CANCELLED:
            raise InstallerError(tr('Operation cancelled.'))
        if process.returncode:
            lines = [x.strip() for x in text.splitlines() if "error:" in x.lower() or "Error Domain=" in x]
            if "This device has reached the maximum number of installed apps using a free developer profile" in text or "maximum number of apps for free development profiles" in text:
                detail = tr('This iPhone has reached the limit of apps installed with free profiles. An update must use the same Bundle ID as the existing app.\n\n') + text[-9000:]
            else:
                detail = "\n".join(lines[-8:]) if lines else text[-2200:]
            if "Failed Registering Bundle Identifier" in text:
                detail += tr('\n\nApple did not accept the identifier with the selected team suffix.')
            raise InstallerError(redact(detail.strip()) or tr('Command failed: ') + str(args[0]))
        if not quiet and log:
            for line in text.splitlines():
                if any(s in line for s in ["Registering", "Creating provisioning", "Signing", "Provisioning Profile:"]):
                    emit("log", message=redact(line.strip()))
        return output
    finally:
        ACTIVE_PROCESS = None


def cancel(signum, frame):
    global CANCELLED
    CANCELLED = True
    if ACTIVE_PROCESS and ACTIVE_PROCESS.poll() is None:
        try:
            os.killpg(ACTIVE_PROCESS.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def xcode_environment():
    selected = run(["/usr/bin/xcode-select", "-p"], quiet=True).decode().strip()
    path = Path(selected)
    if not (path / "usr/bin/xcodebuild").exists():
        path = Path("/Applications/Xcode.app/Contents/Developer")
    if not (path / "usr/bin/xcodebuild").exists():
        raise InstallerError(tr('Install the full Xcode app and open it at least once.'))
    env = os.environ.copy()
    env["DEVELOPER_DIR"] = str(path)
    return env


def xcode_preferences():
    return plistlib.loads(run(["/usr/bin/defaults", "export", "com.apple.dt.Xcode", "-"], quiet=True))


def load_account_labels():
    try:
        return json.loads(ACCOUNT_FILE.read_text())
    except (OSError, ValueError):
        return {}


def discover_accounts(preferences=None, labels=None):
    preferences = xcode_preferences() if preferences is None else preferences
    labels = load_account_labels() if labels is None else labels
    groups = preferences.get("DVTDeveloperAccountManagerAppleIDLists", {})
    cached = preferences.get("IDEProvisioningTeamByIdentifier", {})
    accounts = []
    for namespace, entries in groups.items():
        for entry in entries:
            identifier = entry.get("identifier", "")
            if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", identifier):
                continue
            known = labels.get(identifier, {})
            teams = cached.get(identifier, []) or known.get("teams", [])
            name = known.get("name") or entry.get("username") or entry.get("name")
            if not name:
                name = teams[0].get("teamName") if teams else tr('Xcode account ') + identifier[:8]
            clean = []
            for team in teams:
                tid = str(team.get("teamID", ""))
                if re.fullmatch(r"[A-Z0-9]{10}", tid):
                    clean.append(dict(id=tid, name=team.get("teamName", tid),
                                      personal=bool(team.get("isFreeProvisioningTeam", False))))
            accounts.append(dict(id=identifier, namespace=namespace, name=name, teams=clean))
    return accounts


def selected_account_team(accounts, account_id, team):
    account = next((a for a in accounts if a["id"] == account_id), None)
    if account is None:
        raise InstallerError(tr('The selected account is no longer available in Xcode.'))
    if team not in [t["id"] for t in account["teams"]]:
        raise InstallerError(tr('Choose a team associated with this account.'))
    # Xcode's documented signing selector is DEVELOPMENT_TEAM. A shared team does not
    # distinguish two member accounts: fail closed rather than promise the chosen login.
    owners = {a["id"] for a in accounts if any(t["id"] == team for t in a["teams"])}
    if owners != {account_id}:
        raise InstallerError(tr('This team belongs to multiple Xcode accounts. The individual account cannot be selected unambiguously. Use the Personal Team of the account you want.'))
    return account


def devices_from_json(document):
    result = []
    for device in document.get("result", {}).get("devices", []):
        props = device.get("properties", {})
        hw = props.get("hardware", {}) or device.get("hardwareProperties", {})
        state = props.get("state", {}) or device.get("deviceProperties", {})
        connection = props.get("connection", {}) or device.get("connectionProperties", {})
        if hw.get("platform") != "iOS" or connection.get("pairingState") != "paired":
            continue
        software = props.get("software", {})
        version = software.get("osVersionNumber", {})
        version = version.get("stringValue", "") if isinstance(version, dict) else str(version)
        result.append(dict(id=device["identifier"], udid=hw.get("udid", ""),
                           name=state.get("name", device["identifier"]),
                           version=version or state.get("osVersionNumber", ""),
                           connection=connection.get("transportType", "")))
    return result


def discover_devices(env):
    if not (Path(env["DEVELOPER_DIR"]) / "usr/bin/devicectl").is_file():
        output = run(["/usr/bin/xcrun", "xcdevice", "list", "--timeout", "5"], env=env, quiet=True)
        return devices_from_xcdevice(json.loads(output))
    with tempfile.TemporaryDirectory(prefix="xcode-ipa-discovery-") as folder:
        path = Path(folder) / "devices.json"
        run(["/usr/bin/xcrun", "devicectl", "list", "devices", "--json-output", path], env=env, quiet=True)
        return devices_from_json(json.loads(path.read_text()))


def devices_from_xcdevice(document):
    return [dict(id=d["identifier"], udid=d["identifier"], name=d.get("name", d["identifier"]),
                 version=d.get("operatingSystemVersion", "").split(" ", 1)[0],
                 connection=d.get("interface", ""), backend="ios-deploy")
            for d in document if d.get("platform") == "com.apple.platform.iphoneos"
            and not d.get("simulator", False) and d.get("available", False) and d.get("identifier")]


def legacy_installer(device):
    if int((device.get("version") or "0").split(".", 1)[0]) >= 17:
        raise InstallerError(tr("This iPhone requires Xcode 15 or later and a compatible macOS version. The legacy installer supports iOS 16 and earlier."))
    candidates = [shutil.which("ios-deploy"), "/opt/homebrew/bin/ios-deploy", "/usr/local/bin/ios-deploy"]
    tool = next((p for p in candidates if p and os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not tool:
        raise InstallerError(tr("This Xcode version needs ios-deploy to install apps. Install it with Homebrew (brew install ios-deploy), or choose Sign only to export the IPA. Use a USB cable with iOS 16 or earlier."))
    return tool


def install_app(root, device, env, log):
    if device.get("backend") == "ios-deploy":
        # No --debug/--uninstall: update the bundle without launching or erasing it.
        run([legacy_installer(device), "--id", device["udid"], "--bundle", root, "--timeout", "60"],
            env=env, log=log, timeout=600)
    else:
        run(["/usr/bin/xcrun", "devicectl", "device", "install", "app", "--device", device["id"], root],
            env=env, log=log, timeout=600)


def save_account(config):
    accounts = discover_accounts()
    account = next((x for x in accounts if x["id"] == config["account"]), None)
    if not account:
        raise InstallerError(tr('This account is no longer in Xcode.'))
    tid = config["team"].strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", tid):
        raise InstallerError(tr('The Team ID must contain 10 letters or numbers.'))
    labels = load_account_labels()
    labels[account["id"]] = dict(name=config.get("name", account["name"]), teams=[
        dict(teamID=tid, teamName=config.get("teamName", "Team " + tid),
             isFreeProvisioningTeam=bool(config.get("personal", True)))])
    SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = ACCOUNT_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(labels, indent=2)); temp.chmod(0o600); temp.replace(ACCOUNT_FILE)


def checked_members(archive):
    names, total = set(), 0
    for item in archive.infolist():
        name = item.filename
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or "\\" in name or "\x00" in name:
            raise InstallerError(tr('Unsafe path in the IPA.'))
        if name in names:
            raise InstallerError(tr('The IPA contains duplicate names: ') + name)
        names.add(name)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            target = archive.read(item).decode("utf-8")
            if target.startswith("/") or ".." in PurePosixPath(target).parts:
                raise InstallerError(tr('The IPA contains a link outside the bundle.'))
        total += item.file_size
    return total


def inspect_ipa(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise InstallerError(tr('Choose an existing IPA file.'))
    with zipfile.ZipFile(path) as archive:
        size = checked_members(archive)
        mains = [x for x in archive.namelist() if len(PurePosixPath(x).parts) == 3
                 and x.startswith("Payload/") and x.endswith(".app/Info.plist")]
        if len(mains) != 1:
            raise InstallerError(tr('The IPA must contain exactly one app in Payload.'))
        info = plistlib.loads(archive.read(mains[0]))
        root = str(PurePosixPath(mains[0]).parent)
        return dict(path=str(path), name=info.get("CFBundleDisplayName") or info.get("CFBundleName") or Path(root).stem,
                    bundleID=info["CFBundleIdentifier"], version=info.get("CFBundleShortVersionString", ""),
                    unpackedBytes=size, root=root,
                    extensions=sum(1 for x in archive.namelist() if x.endswith(".appex/Info.plist")))


def unpack_ipa(path, folder):
    with zipfile.ZipFile(path) as archive:
        size = checked_members(archive)
        if shutil.disk_usage(folder).free < size * 2 + 512 * 1024 ** 2:
            raise InstallerError(tr('Not enough free space to extract and sign this IPA.'))
        for item in archive.infolist():
            if not item.filename.startswith("Payload/"):
                continue
            dest = folder / item.filename
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                dest.symlink_to(archive.read(item).decode())
            else:
                with archive.open(item) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                dest.chmod((mode & 0o777) or 0o644)


def is_macho(path):
    try:
        with Path(path).open("rb") as f:
            return f.read(4) in [b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
                                 b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"]
    except OSError:
        return False


def bundle_executable(bundle, info):
    name = info.get("CFBundleExecutable", "")
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        raise InstallerError(tr('Invalid executable name: ') + bundle.name)
    binary = bundle / name
    try:
        binary.resolve().relative_to(bundle.resolve())
    except ValueError:
        raise InstallerError(tr('The executable is outside the app bundle.'))
    if not is_macho(binary):
        raise InstallerError(tr('Missing iOS executable: ') + bundle.name)
    return binary


def macho_slices(path):
    thin = {b"\xcf\xfa\xed\xfe": ("<", 32), b"\xfe\xed\xfa\xcf": (">", 32),
            b"\xce\xfa\xed\xfe": ("<", 28), b"\xfe\xed\xfa\xce": (">", 28)}
    fat = {b"\xca\xfe\xba\xbe": (">", 20, "II"), b"\xbe\xba\xfe\xca": ("<", 20, "II"),
           b"\xca\xfe\xba\xbf": (">", 32, "QQ"), b"\xbf\xba\xfe\xca": ("<", 32, "QQ")}
    with path.open("rb") as stream:
        length = path.stat().st_size
        initial = stream.read(8)
        offsets = [(0, length)]
        if initial[:4] in fat:
            endian, entry_size, fmt = fat[initial[:4]]
            if len(initial) != 8: raise InstallerError(tr('Truncated Mach-O header.'))
            count = struct.unpack(endian + "I", initial[4:])[0]
            if not 0 < count <= 64: raise InstallerError(tr('Invalid Mach-O architecture count.'))
            offsets = []
            for _ in range(count):
                entry = stream.read(entry_size)
                if len(entry) != entry_size: raise InstallerError(tr('Truncated Mach-O header.'))
                offset, size = struct.unpack_from(endian + fmt, entry, 8)
                if offset < 8 + count * entry_size or offset + size > length:
                    raise InstallerError(tr('Mach-O architecture extends beyond the file.'))
                offsets.append((offset, size))
        result = []
        for offset, size in offsets:
            stream.seek(offset); header = stream.read(min(size, 32))
            if header[:4] not in thin: raise InstallerError(tr('Invalid Mach-O architecture.'))
            endian, header_size = thin[header[:4]]
            if len(header) < header_size: raise InstallerError(tr('Truncated Mach-O header.'))
            filetype, commands, command_bytes = struct.unpack_from(endian + "III", header, 12)
            if commands > command_bytes // 8 or command_bytes > min(size-header_size, 16*1024*1024):
                raise InstallerError(tr('Invalid Mach-O load commands.'))
            result.append(dict(offset=offset, endian=endian, headerSize=header_size,
                               filetype=filetype, commands=commands, commandBytes=command_bytes))
        return result


def check_encryption(path):
    for entry in macho_slices(path):
        with path.open("rb") as stream:
            stream.seek(entry["offset"] + entry["headerSize"]); data = stream.read(entry["commandBytes"])
        pos = 0
        for _ in range(entry["commands"]):
            if pos + 8 > len(data): raise InstallerError(tr('Truncated Mach-O load commands.'))
            cmd, size = struct.unpack_from(entry["endian"] + "II", data, pos)
            if size < 8 or pos + size > len(data): raise InstallerError(tr('Invalid Mach-O header.'))
            if cmd in (0x21, 0x2c):
                if size < 20: raise InstallerError(tr('Invalid Mach-O encryption command.'))
                if struct.unpack_from(entry["endian"] + "I", data, pos + 16)[0] != 0:
                    raise InstallerError(tr('This IPA contains encrypted App Store code and cannot be signed again: ') + path.name)
            pos += size


def signing_plan(root, units):
    """Include every loadable Mach-O, even outside standard Frameworks directories."""
    plan = {u["path"]: u for u in units}
    covered = {bundle_executable(u["path"], u["info"]).resolve() for u in units}
    for bundle in root.rglob("*"):
        if bundle.is_symlink() or not bundle.is_dir() or bundle.suffix not in (".framework", ".bundle"):
            continue
        infos = [bundle/"Info.plist", bundle/"Resources/Info.plist", bundle/"Versions/Current/Resources/Info.plist"]
        info_path = next((p for p in infos if p.is_file()), None)
        if not info_path: continue
        info = plistlib.loads(info_path.read_bytes())
        name = info.get("CFBundleExecutable")
        if not name: continue  # Resource-only bundles are sealed by their enclosing signature.
        binary = bundle_executable(bundle, info)
        if 2 in {s["filetype"] for s in macho_slices(binary)}:
            raise InstallerError(tr('This component requires its own app bundle and profile: ') + str(bundle.relative_to(root)))
        plan[bundle] = None; covered.add(binary.resolve())
    for binary in root.rglob("*"):
        if binary.is_symlink() or not binary.is_file() or not is_macho(binary): continue
        kinds = {s["filetype"] for s in macho_slices(binary)}
        if not kinds.intersection({2, 6, 8, 11}): continue  # Object/debug files are resources, not executable code.
        check_encryption(binary)
        if binary.resolve() in covered: continue
        if 2 in kinds:
            raise InstallerError(tr('Executable without a dedicated bundle and profile: ') + str(binary.relative_to(root)))
        plan[binary] = None; covered.add(binary.resolve())
    return sorted(plan.items(), key=lambda item: (-len(item[0].parts), str(item[0])))


def entitlements_of(path):
    result = subprocess.run(["/usr/bin/codesign", "-d", "--entitlements", ":-", str(path)], capture_output=True)
    if not result.stdout.strip():
        return {}
    try:
        return plistlib.loads(result.stdout)
    except plistlib.InvalidFileException:
        raise InstallerError(tr('Unable to read the original entitlements: ') + path.name)


def valid_bundle_id(value):
    return isinstance(value, str) and len(value) <= 255 and bool(re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value))


def original_bundle_ids(bundles):
    """Validate every process identifier and preserve its exact original spelling."""
    if any(not valid_bundle_id(value) for value in bundles):
        raise InstallerError(tr('The IPA contains an invalid app or extension identifier.'))
    if len({value.lower() for value in bundles}) != len(bundles):
        raise InstallerError(tr('The IPA contains duplicate app or extension identifiers.'))
    return {value: value for value in bundles}


def team_bundle_id(original, team):
    if not valid_bundle_id(original) or not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise InstallerError(tr('Invalid original identifier or Team ID.'))
    result = original if original.endswith("." + team) else original + "." + team
    if not valid_bundle_id(result):
        raise InstallerError(tr('The identifier with the team suffix exceeds 255 characters.'))
    return result


def team_bundle_mapping(bundles, root_id, team):
    original_bundle_ids(bundles)
    root = team_bundle_id(root_id, team)
    result = {}
    for old in bundles:
        target = root + old[len(root_id):] if old == root_id or old.startswith(root_id + ".") else root + "." + old
        if not valid_bundle_id(target) or target.lower() in {value.lower() for value in result.values()}:
            raise InstallerError(tr('Component identifiers for the selected team are invalid or not unique.'))
        result[old] = target
    return result


def rewrite(value, mapping, old_prefix=None, new_prefix=None):
    if isinstance(value, dict):
        return {k: rewrite(v, mapping, old_prefix, new_prefix) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite(v, mapping, old_prefix, new_prefix) for v in value]
    if isinstance(value, str):
        if old_prefix and new_prefix and value.startswith(old_prefix + "."):
            return new_prefix + "." + rewrite(value[len(old_prefix)+1:], mapping)
        for old in sorted(mapping, key=len, reverse=True):
            if value == old or value.startswith(old + "."):
                value = mapping[old] + value[len(old):]
                break
        return value
    return value


def requested_entitlements(original, mapping, team, bundle_id, memory, preserve=True, main_process=True,
                           allow_eva=True):
    managed = {"application-identifier", "com.apple.developer.team-identifier", "get-task-allow", "beta-reports-active"}
    old = original.get("com.apple.developer.team-identifier")
    result = {"keychain-access-groups": ["$(AppIdentifierPrefix)" + bundle_id]}
    for key, value in original.items():
        if key in managed or key == MEMORY or (not main_process and key in MEMORY_ENTITLEMENTS):
            continue
        if key == EVA and not allow_eva:
            continue
        if not preserve:
            continue
        if key == "keychain-access-groups":
            prefix = original.get("application-identifier", "").split(".", 1)[0] or old
            result[key] = []
            for group in value:
                body = group[len(prefix)+1:] if prefix and group.startswith(prefix + ".") else group
                result[key].append("$(AppIdentifierPrefix)" + rewrite(body, mapping))
        else:
            result[key] = rewrite(value, mapping, old, team)
    if memory and main_process:
        result[MEMORY] = True
    return result


def eva_unavailable(error):
    """Retry only an explicit EVA capability refusal, never a generic auth/profile failure."""
    message = str(error).lower()
    named = "extended virtual addressing" in message or EVA in message
    refused = any(s in message for s in ("do not support", "does not support", "doesn't support",
                  "not supported", "not available", "not found and could not be included in profile"))
    return named and refused


def assert_eva_policy(actual, requested):
    if actual.get(EVA) != requested.get(EVA):
        raise InstallerError(tr('The final signature does not match Extended Virtual Addressing availability for the selected team.'))


def write_project(folder, units, team):
    folder.mkdir(parents=True, exist_ok=True)
    objects = {}
    def ident(name):
        return hashlib.sha1(name.encode()).hexdigest()[:24].upper()
    def add(name, value):
        key = ident(name); objects[key] = value; return key
    (folder / "main.c").write_text("int main(int argc, char **argv) { return 0; }\nint NSExtensionMain(int argc, char **argv) { return 0; }\n")
    file_id = add("source", dict(isa="PBXFileReference", lastKnownFileType="sourcecode.c.c", path="main.c", sourceTree="<group>"))
    products = add("products", dict(isa="PBXGroup", name="Products", children=[], sourceTree="<group>"))
    main_group = add("group", dict(isa="PBXGroup", children=[file_id, products], sourceTree="<group>"))
    def configs(name, settings):
        config = add(name + "config", dict(isa="XCBuildConfiguration", name="Debug", buildSettings=settings))
        return add(name + "configs", dict(isa="XCConfigurationList", buildConfigurations=[config], defaultConfigurationName="Debug", defaultConfigurationIsVisible=0))
    project_settings = dict(SDKROOT="iphoneos", SUPPORTED_PLATFORMS="iphoneos", IPHONEOS_DEPLOYMENT_TARGET="15.0", ARCHS="arm64", ENABLE_BITCODE="NO")
    targets, attrs = [], {}
    for i, unit in enumerate(units):
        name = "Provision" + str(i)
        extension = unit["path"].suffix == ".appex"
        product_type = "com.apple.product-type.app-extension" if extension else "com.apple.product-type.application"
        product_suffix = ".appex" if extension else ".app"
        info = dict(CFBundleIdentifier="$(PRODUCT_BUNDLE_IDENTIFIER)", CFBundleExecutable="$(EXECUTABLE_NAME)",
                    CFBundleName=name, CFBundlePackageType="XPC!" if extension else "APPL", CFBundleShortVersionString="1.0", CFBundleVersion="1")
        if extension:
            info["NSExtension"] = unit["info"].get("NSExtension", dict(NSExtensionPointIdentifier="com.apple.share-services"))
        (folder / (name + ".plist")).write_bytes(plistlib.dumps(info))
        (folder / (name + ".entitlements")).write_bytes(plistlib.dumps(unit["requested"]))
        product = add(name + "product", dict(isa="PBXFileReference", explicitFileType="wrapper.app-extension" if extension else "wrapper.application", includeInIndex=0, path=name + product_suffix, sourceTree="BUILT_PRODUCTS_DIR"))
        objects[products]["children"].append(product)
        build_file = add(name + "buildfile", dict(isa="PBXBuildFile", fileRef=file_id))
        phase = add(name + "sources", dict(isa="PBXSourcesBuildPhase", buildActionMask=2147483647, files=[build_file], runOnlyForDeploymentPostprocessing=0))
        settings = dict(PRODUCT_NAME=name, PRODUCT_BUNDLE_IDENTIFIER=unit["new_id"], DEVELOPMENT_TEAM=team,
                        CODE_SIGN_STYLE="Automatic", CODE_SIGN_IDENTITY="Apple Development", CODE_SIGN_ENTITLEMENTS=name + ".entitlements",
                        INFOPLIST_FILE=name + ".plist", GENERATE_INFOPLIST_FILE="NO", TARGETED_DEVICE_FAMILY="1,2", CLANG_ENABLE_MODULES="YES", OTHER_LDFLAGS=["-framework", "UIKit"], ASSETCATALOG_COMPILER_GENERATE_SWIFT_ASSET_SYMBOL_EXTENSIONS="NO")
        if extension:
            settings["APPLICATION_EXTENSION_API_ONLY"] = "YES"
        target = add(name + "target", dict(isa="PBXNativeTarget", name=name, productName=name,
                         productReference=product, productType=product_type, buildConfigurationList=configs(name, settings),
                         buildPhases=[phase], buildRules=[], dependencies=[]))
        targets.append(target)
        attrs[target] = dict(DevelopmentTeam=team, ProvisioningStyle="Automatic")
        if unit["requested"].get(MEMORY):
            attrs[target]["SystemCapabilities"] = {"com.apple.IncreasedMemoryLimit": {"enabled": 1}}
        unit["target"] = name
        unit["product"] = name + product_suffix
    root = add("project", dict(isa="PBXProject", attributes=dict(TargetAttributes=attrs), buildConfigurationList=configs("project", project_settings),
                   compatibilityVersion="Xcode 12.0", developmentRegion="en", knownRegions=["en"], mainGroup=main_group,
                   productRefGroup=products, projectDirPath="", projectRoot="", targets=targets))
    project = folder / "Provision.xcodeproj"; project.mkdir()
    schemes = project / "xcshareddata/xcschemes"; schemes.mkdir(parents=True)
    for i, unit in enumerate(units):
        scheme = ET.Element("Scheme", LastUpgradeVersion="1300", version="1.3")
        action = ET.SubElement(scheme, "BuildAction", parallelizeBuildables="NO", buildImplicitDependencies="YES")
        entries = ET.SubElement(action, "BuildActionEntries")
        entry = ET.SubElement(entries, "BuildActionEntry", buildForTesting="NO", buildForRunning="YES",
                              buildForProfiling="NO", buildForArchiving="YES", buildForAnalyzing="YES")
        ET.SubElement(entry, "BuildableReference", BuildableIdentifier="primary", BlueprintIdentifier=targets[i],
                      BuildableName=unit["product"], BlueprintName=unit["target"], ReferencedContainer="container:Provision.xcodeproj")
        ET.ElementTree(scheme).write(schemes / (unit["target"] + ".xcscheme"), encoding="UTF-8", xml_declaration=True)
    def project_strings(value):
        if isinstance(value, dict):
            return {k: project_strings(v) for k, v in value.items()}
        if isinstance(value, list):
            return [project_strings(v) for v in value]
        return str(value) if isinstance(value, int) else value
    (project / "project.pbxproj").write_bytes(plistlib.dumps(project_strings(dict(archiveVersion=1, classes={}, objectVersion=54, objects=objects, rootObject=root))))
    return project


def decode_profile(path):
    return plistlib.loads(run(["/usr/bin/security", "cms", "-D", "-i", path], quiet=True))


def assert_profile(profile, team, bundle_id, udid, memory):
    if team not in profile.get("TeamIdentifier", []):
        raise InstallerError(tr('The profile belongs to a different team from the one selected.'))
    ent = profile.get("Entitlements", {})
    if ent.get("com.apple.developer.team-identifier") != team:
        raise InstallerError(tr('The profile and entitlement teams do not match.'))
    prefix = profile.get("ApplicationIdentifierPrefix", [team])[0]
    if not fnmatch.fnmatchcase(prefix + "." + bundle_id, ent.get("application-identifier", "")):
        raise InstallerError(tr('The profile does not authorize this app identifier.'))
    if not profile.get("ProvisionsAllDevices") and udid not in profile.get("ProvisionedDevices", []):
        raise InstallerError(tr('The profile does not include the selected iPhone.'))
    expiry = profile.get("ExpirationDate")
    if not isinstance(expiry, dt.datetime) or expiry.replace(tzinfo=dt.timezone.utc) <= dt.datetime.now(dt.timezone.utc):
        raise InstallerError(tr('The provisioning profile has expired.'))
    if memory and ent.get(MEMORY) is not True:
        raise InstallerError(tr('The selected profile does not authorize Increased Memory Limit. Signing stopped.'))


def assert_signature(actual, team, app_identifier, memory):
    if actual.get("com.apple.developer.team-identifier") != team:
        raise InstallerError(tr('The final signature does not match the selected team.'))
    if actual.get("application-identifier") != app_identifier:
        raise InstallerError(tr('The signed identifier does not match the identifier authorized by the profile.'))
    if (actual.get(MEMORY) is True) != memory:
        raise InstallerError(tr('The final signature does not match the Increased Memory Limit option.'))


def assert_memory_scope(actual, main_process):
    if not main_process and MEMORY_ENTITLEMENTS.intersection(actual):
        raise InstallerError(tr('A memory capability was applied to a component other than the main app.'))


def entitlement_allowed(allowed, value):
    if isinstance(value, bool): return allowed is value
    if isinstance(value, str): return isinstance(allowed, str) and fnmatch.fnmatchcase(value, allowed)
    if isinstance(value, list):
        choices = allowed if isinstance(allowed, list) else [allowed]
        return all(any(entitlement_allowed(candidate, entry) for candidate in choices) for entry in value)
    if isinstance(value, dict):
        return isinstance(allowed, dict) and all(key in allowed and entitlement_allowed(allowed[key], entry) for key, entry in value.items())
    return type(value) is type(allowed) and value == allowed


def assert_capabilities(effective, profile, requested, bundle_id):
    prefix = profile.get("ApplicationIdentifierPrefix", profile["TeamIdentifier"])[0] + "."
    def expand(value):
        if isinstance(value, str):
            return value.replace("$(AppIdentifierPrefix)", prefix).replace("$(TeamIdentifierPrefix)", profile["TeamIdentifier"][0]+".").replace("$(PRODUCT_BUNDLE_IDENTIFIER)", bundle_id)
        if isinstance(value, list): return [expand(v) for v in value]
        if isinstance(value, dict): return {k:expand(v) for k,v in value.items()}
        return value
    authorized = profile.get("Entitlements", {})
    for key, value in requested.items():
        if effective.get(key) != expand(value):
            raise InstallerError(tr('Xcode omitted or changed the requested capability “') + key + tr('” for ') + bundle_id)
    for key, value in effective.items():
        if key not in authorized or not entitlement_allowed(authorized[key], value):
            raise InstallerError(tr('The profile does not authorize the capability “') + key + tr('” for ') + bundle_id)


def identity_for_profile(profile, identities_output):
    available = set(re.findall(r"\b[0-9A-F]{40}\b", identities_output.upper()))
    for certificate in profile.get("DeveloperCertificates", []):
        digest = hashlib.sha1(certificate).hexdigest().upper()
        if digest in available:
            return digest
    raise InstallerError(tr('The certificate authorized by the profile has no available signing key. Check your accounts in Xcode.'))


def assets_directory():
    return SUPPORT / "Signing Assets"


def asset_directory(identifier):
    if not re.fullmatch(r"[0-9A-F]{40}", str(identifier)):
        raise InstallerError(tr('Invalid imported certificate identifier.'))
    return assets_directory() / identifier


def profile_pattern(profile):
    prefixes = profile.get("ApplicationIdentifierPrefix", [])
    value = profile.get("Entitlements", {}).get("application-identifier", "")
    if not prefixes or not value.startswith(prefixes[0] + "."):
        raise InstallerError(tr('The imported profile has an invalid App ID prefix.'))
    pattern = value[len(prefixes[0]) + 1:]
    # Apple App ID wildcards are terminal, not general-purpose glob expressions.
    if not (valid_bundle_id(pattern) or pattern == "*" or
            (pattern.endswith(".*") and valid_bundle_id(pattern[:-2]))):
        raise InstallerError(tr('The imported profile has an invalid app identifier.'))
    return pattern


def certificate_hashes(profile):
    return {hashlib.sha1(c).hexdigest().upper() for c in profile.get("DeveloperCertificates", [])}


def profile_summary(path, profile):
    pattern = profile_pattern(profile)
    ent = profile.get("Entitlements", {})
    teams = profile.get("TeamIdentifier", [])
    if (len(teams) != 1 or not re.fullmatch(r"[A-Z0-9]{10}", str(teams[0])) or
            ent.get("com.apple.developer.team-identifier") != teams[0] or
            not isinstance(profile.get("ExpirationDate"), dt.datetime) or not certificate_hashes(profile)):
        raise InstallerError(tr('The imported profile is missing valid team, certificate, or expiry information.'))
    return dict(id=hashlib.sha256(path.read_bytes()).hexdigest(), name=str(profile.get("Name", path.stem)),
                bundlePattern=pattern, team=profile["TeamIdentifier"][0],
                expires=profile["ExpirationDate"].isoformat(), memory=ent.get(MEMORY) is True,
                eva=ent.get(EVA) is True, debug=ent.get("get-task-allow") is True,
                allDevices=bool(profile.get("ProvisionsAllDevices")), deviceCount=len(profile.get("ProvisionedDevices", [])))


def load_signing_assets():
    result = []
    for path in sorted(assets_directory().glob("*/certificate.json")):
        try:
            value = json.loads(path.read_text())
            if path.parent != asset_directory(value["id"]): continue
            profiles = []
            for p in sorted(path.parent.glob("Profiles/*.mobileprovision")):
                profiles.append(profile_summary(p, decode_profile(p)))
            result.append(dict(value, profiles=profiles))
        except (OSError, ValueError, KeyError, TypeError, InstallerError):
            continue
    return result


def certificate_tool(path, password, expected):
    global ACTIVE_PROCESS
    helper = Path(__file__).parent.parent / "MacOS/CertificateTool"
    if not helper.is_file():
        raise InstallerError(tr('The certificate import helper is missing. Rebuild or reinstall the app.'))
    # A private pipe, never command arguments, environment variables, or a log file.
    request = json.dumps(dict(path=str(path), password=password, expected=sorted(expected))).encode()
    child = subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    ACTIVE_PROCESS = child
    try:
        try:
            output = child.communicate(input=request, timeout=120)[0]
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGTERM); child.communicate()
            raise InstallerError(tr('Certificate import timed out. Check any macOS Keychain prompt and try again.'))
        if CANCELLED: raise InstallerError(tr('Operation cancelled.'))
    finally:
        ACTIVE_PROCESS = None
    value = json.loads(output)
    if child.returncode:
        code = value.get("error")
        if code == "p12Import":
            raise InstallerError(tr('Cannot open the P12. Check its password and that it contains a certificate with a private key.'))
        if code == "profileCertificateMismatch":
            raise InstallerError(tr('The certificate does not match the selected provisioning profiles.'))
        if code == "certificateExpired":
            raise InstallerError(tr('The signing certificate has expired or is not valid yet.'))
        raise InstallerError(tr('Certificate import failed. Check the file and any macOS Keychain prompt. Security status: ') + str(value.get("status")))
    return value


def private_write(path, data):
    descriptor, temporary = tempfile.mkstemp(prefix=".import-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def import_signing_assets(config):
    paths = [Path(p) for p in config.get("profiles", [])]
    if not paths:
        raise InstallerError(tr('Choose at least one mobileprovision file.'))
    profiles = [decode_profile(p) for p in paths]
    expected = None
    teams = set()
    for path, profile in zip(paths, profiles):
        profile_summary(path, profile)  # Structural validation before importing any key.
        teams.add(profile["TeamIdentifier"][0])
        certificates = certificate_hashes(profile)
        expected = certificates if expected is None else expected.intersection(certificates)
    if not expected or len(teams) != 1:
        raise InstallerError(tr('These profiles must share one signing certificate and team. Import other certificates separately.'))
    if config.get("certificate"):
        directory = asset_directory(config["certificate"])
        value = json.loads((directory / "certificate.json").read_text())
        if value["id"] not in expected or value["team"] not in teams:
            raise InstallerError(tr('The certificate does not match the selected provisioning profiles.'))
    else:
        value = certificate_tool(Path(config["p12"]), config.get("password", ""), expected)
        value["team"] = next(iter(teams))
        directory = asset_directory(value["id"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    target = directory / "Profiles"; target.mkdir(exist_ok=True, mode=0o700)
    target.chmod(0o700)
    for path in paths:
        data = path.read_bytes()
        dest = target / (hashlib.sha256(data).hexdigest() + ".mobileprovision")
        private_write(dest, data)
    metadata = directory / "certificate.json"
    private_write(metadata, (json.dumps(value, indent=2) + "\n").encode())
    emit("assets", certificates=load_signing_assets(), selected=value["id"])


def external_bundle_id(original, pattern):
    if not valid_bundle_id(original): raise InstallerError(tr('Invalid original app identifier.'))
    if not fnmatch.fnmatchcase(original, pattern):
        raise InstallerError(tr('The imported profile authorizes ') + pattern + tr(', but the original app identifier is ') + original +
            tr('. Import a matching or wildcard profile. Original Bundle IDs are never changed in imported-certificate mode.'))
    return original


def external_context(config):
    directory = asset_directory(config["certificate"])
    value = json.loads((directory / "certificate.json").read_text())
    if not re.fullmatch(r"[0-9a-f]{64}", str(config.get("profile", ""))):
        raise InstallerError(tr('Choose the main app provisioning profile.'))
    main = directory / "Profiles" / (config["profile"] + ".mobileprovision")
    records = [(p, decode_profile(p)) for p in sorted(directory.glob("Profiles/*.mobileprovision"))]
    chosen = next(((p, data) for p, data in records if p == main), None)
    if not chosen: raise InstallerError(tr('The selected provisioning profile is no longer available.'))
    return value, records, chosen


def external_effective(profile, requested, team, bundle_id):
    prefix = profile["ApplicationIdentifierPrefix"][0] + "."
    def expand(value):
        if isinstance(value, str):
            return value.replace("$(AppIdentifierPrefix)", prefix).replace("$(TeamIdentifierPrefix)", team + ".").replace("$(PRODUCT_BUNDLE_IDENTIFIER)", bundle_id)
        if isinstance(value, list): return [expand(v) for v in value]
        if isinstance(value, dict): return {k: expand(v) for k, v in value.items()}
        return value
    result = expand(requested)
    result.update({"application-identifier": prefix + bundle_id, "com.apple.developer.team-identifier": team})
    authorized = profile["Entitlements"]
    # The profile, not a certificate's marketing name or the original IPA, controls debugging.
    if "get-task-allow" in authorized:
        result["get-task-allow"] = authorized["get-task-allow"] is True
    return result


def compatible_capabilities(profile, requested, team, bundle_id, strict=False):
    expanded = external_effective(profile, requested, team, bundle_id)
    authorized = profile["Entitlements"]
    result = {}
    excluded = []
    for key in requested:
        value = expanded[key]
        if key in authorized and entitlement_allowed(authorized[key], value):
            result[key] = value
            continue
        # Increased Memory Limit is an explicit user requirement, never an optional fallback.
        if strict or key == MEMORY:
            raise InstallerError(tr('The profile does not authorize the capability “') + key + tr('” for ') + bundle_id)
        excluded.append(key)
        if isinstance(value, list):
            entries = authorized.get(key, [])
            retained = [v for v in value if entitlement_allowed(entries, [v])]
            if retained: result[key] = retained
        if key == "keychain-access-groups" and key not in result:
            fallback = profile["ApplicationIdentifierPrefix"][0] + "." + bundle_id
            if entitlement_allowed(authorized.get(key, []), [fallback]): result[key] = [fallback]
    return result, excluded


def prepare_external_units(units, mapping, records, chosen, certificate, team, device, memory, preserve, strict=False):
    for i, unit in enumerate(units):
        unit["is_main"] = i == 0
        unit["memory"] = memory and i == 0
        unit["new_id"] = mapping[unit["old_id"]]
        candidates = [chosen] if i == 0 else sorted(records, key=lambda r: ("*" in profile_pattern(r[1]), -len(profile_pattern(r[1])), str(r[0])))
        failure = None
        for path, profile in candidates:
            try:
                if certificate not in certificate_hashes(profile): continue
                assert_profile(profile, team, unit["new_id"], device["udid"], unit["memory"])
                allow_eva = profile["Entitlements"].get(EVA) is True
                requested = requested_entitlements(unit["original"], mapping, team, unit["new_id"], unit["memory"],
                    preserve=preserve, main_process=unit["is_main"], allow_eva=allow_eva)
                requested, excluded = compatible_capabilities(profile, requested, team, unit["new_id"], strict)
                effective = external_effective(profile, requested, team, unit["new_id"])
                assert_capabilities(effective, profile, requested, unit["new_id"])
                assert_signature(effective, team, effective["application-identifier"], unit["memory"])
                assert_memory_scope(effective, unit["is_main"])
                assert_eva_policy(effective, requested)
                unit.update(profile=path, profile_data=profile, identity=certificate, requested=requested, effective=effective,
                    app_identifier=effective["application-identifier"], expiry=profile["ExpirationDate"].isoformat(),
                    excluded_capabilities=excluded,
                    eva_policy="authorizedByImportedProfile" if allow_eva else "unavailableInImportedProfile")
                if excluded:
                    emit("log", message=tr('Excluded unsupported capabilities for ') + unit["path"].name + ": " + ", ".join(excluded))
                break
            except InstallerError as error:
                failure = error
        else:
            if i == 0 and failure:
                raise InstallerError(unit["path"].name + ": " + str(failure))
            raise InstallerError(tr('No compatible imported profile for ') + unit["new_id"] +
                tr('. Import a matching extension profile or a wildcard profile for the same certificate. ') + (str(failure) if failure else ""))


def pack_ipa(app, output):
    temporary = output.with_name(output.name + ".partial")
    if output.exists() or temporary.exists():
        raise InstallerError(tr('The destination file already exists.'))
    try:
        with zipfile.ZipFile(temporary, "x", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for p in sorted([app] + list(app.rglob("*"))):
                if p.name in (".DS_Store", "__MACOSX") or p.name.startswith("._"):
                    continue
                relative = "Payload/" + str(p.relative_to(app.parent))
                if p.is_symlink():
                    info = zipfile.ZipInfo(relative); info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, os.readlink(p))
                elif p.is_file():
                    archive.write(p, relative)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip():
                raise InstallerError(tr('The signed IPA could not be verified.'))
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def sign_and_install(config):
    external = config.get("signingMode") == "imported"
    records = chosen = None
    if external:
        certificate, records, chosen = external_context(config)
        team = certificate["team"]
        account = dict(name=certificate["name"])
        personal_team = False
        identities = run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"], quiet=True).decode()
        if certificate["id"] not in re.findall(r"\b[0-9A-F]{40}\b", identities.upper()):
            raise InstallerError(tr('The imported signing identity is unavailable or expired. Check its private key and trust chain in Keychain Access.'))
    else:
        team = str(config["team"]).upper()
        account = selected_account_team(discover_accounts(), config["account"], team)
        personal_team = next(t for t in account["teams"] if t["id"] == team).get("personal", False)
    env = xcode_environment()
    device = next((d for d in discover_devices(env) if d["id"] == config["device"]), None)
    if not device or not device["udid"]:
        raise InstallerError(tr('The selected iPhone is unavailable or is not paired with this Mac.'))
    if config.get("install", True) and device.get("backend") == "ios-deploy":
        legacy_installer(device)  # Check compatibility before any provisioning changes.
    metadata = inspect_ipa(config["ipa"])
    memory = bool(config.get("memory", False))
    new_id = (external_bundle_id(metadata["bundleID"], profile_pattern(chosen[1])) if external
              else team_bundle_id(metadata["bundleID"], team))
    if not external and config.get("bundleID", new_id) != new_id:
        raise InstallerError(tr('The identifier must match the IPA identifier plus the selected signing team suffix.'))
    SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs = SUPPORT / "Logs"; logs.mkdir(exist_ok=True, mode=0o700)
    log = logs / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"); log.touch(mode=0o600)
    emit("logfile", path=str(log))
    emit("log", message="Team " + team + " · Bundle ID " + new_id + (tr(' (imported profile)') if external else tr(' (signing team suffix)')))
    with tempfile.TemporaryDirectory(prefix="xcode-ipa-sign-") as temp:
        work = Path(temp)
        emit("phase", message=tr('Preparing the IPA'), progress=0.05)
        unpack_ipa(metadata["path"], work)
        root = work / metadata["root"]
        bundles = [root] + sorted([p for p in root.rglob("*") if p.suffix in (".app", ".appex") and (p / "Info.plist").is_file()])
        units = []
        for bundle in bundles:
            info = plistlib.loads((bundle / "Info.plist").read_bytes())
            if set(info.get("CFBundleSupportedPlatforms", ["iPhoneOS"])) - {"iPhoneOS"}:
                raise InstallerError(tr('This package contains a Watch app or a platform other than iOS: ') + bundle.name)
            binary = bundle_executable(bundle, info)
            check_encryption(binary)
            units.append(dict(path=bundle, info=info, old_id=info["CFBundleIdentifier"], original=entitlements_of(bundle)))
        if external:
            mapping = original_bundle_ids([u["old_id"] for u in units])
            prepare_external_units(units, mapping, records, chosen, certificate["id"], team, device, memory,
                                   bool(config.get("preserveCapabilities", True)), bool(config.get("strictImportedCapabilities", False)))
        else:
            mapping = team_bundle_mapping([u["old_id"] for u in units], metadata["bundleID"], team)
        for unit in ([] if external else units):
            unit["is_main"] = unit["path"] == root
            unit["memory"] = memory and unit["is_main"]
            unit["new_id"] = mapping[unit["old_id"]]
            unit["requested"] = requested_entitlements(unit["original"], mapping, team, unit["new_id"], unit["memory"],
                                                        preserve=bool(config.get("preserveCapabilities", True)), main_process=unit["is_main"],
                                                        allow_eva=not personal_team)
            unit["eva_policy"] = "personalTeam" if personal_team else "automatic"
        plan = signing_plan(root, units)
        emit("log", message=str(len(units)) + tr(' apps/extensions · ') + str(len(plan)) + tr(' code components to sign'))
        emit("log", message="Increased Memory Limit: " + (tr('only ') + root.name if memory else tr('off')) + tr('; every component is still included in signing.'))
        if personal_team:
            emit("log", message=tr('Extended Virtual Addressing: excluded, unavailable for Personal Teams. Increased Memory Limit stays independent.'))
        if not external:
            project = write_project(work / "Provisioning", units, team)
            for i, unit in enumerate(units):
                emit("phase", message=tr('Xcode: profile ') + unit["path"].name, progress=0.12 + 0.50 * i / len(units))
                args = ["/usr/bin/xcrun", "xcodebuild", "-project", project, "-scheme", unit["target"], "-configuration", "Debug",
                        "-derivedDataPath", work / "DerivedData",
                        "-sdk", "iphoneos", "-destination", "id=" + device["udid"], "-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration",
                        "SYMROOT=" + str(work / "Products"), "OBJROOT=" + str(work / "Objects"), "COMPILER_INDEX_STORE_ENABLE=NO",
                        "CLANG_MODULE_CACHE_PATH=" + str(work / "Modules"), "MODULE_CACHE_DIR=" + str(work / "Modules"),
                        "SDK_STAT_CACHE_DIR=" + str(work / "SDKStatCaches"), "SHARED_PRECOMPS_DIR=" + str(work / "Precompiled"),
                        "DEVELOPMENT_TEAM=" + team, "build"]
                try:
                    run(args, env=env, log=log, timeout=600)
                except InstallerError as error:
                    if EVA not in unit["requested"] or not eva_unavailable(error):
                        raise
                    # Capability availability belongs to the selected team, not the IPA name.
                    # Change only EVA; the required memory entitlement and all other capabilities stay.
                    unit["requested"].pop(EVA)
                    unit["eva_policy"] = "unavailableForTeam"
                    (project.parent / (unit["target"] + ".entitlements")).write_bytes(plistlib.dumps(unit["requested"]))
                    emit("log", message=tr('Xcode did not authorize Extended Virtual Addressing for this team. Retrying without EVA, keeping Increased Memory Limit and all other requested capabilities.'))
                    run(args, env=env, log=log, timeout=600)
                product = work / "Products/Debug-iphoneos" / unit["product"]
                profile_path = product / "embedded.mobileprovision"
                if not profile_path.exists():
                    raise InstallerError(tr('Xcode did not generate a profile for ') + unit["new_id"])
                profile = decode_profile(profile_path)
                assert_profile(profile, team, unit["new_id"], device["udid"], unit["memory"])
                identities = run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"], quiet=True).decode()
                unit["identity"] = identity_for_profile(profile, identities)
                unit["profile"] = profile_path
                unit["profile_data"] = profile
                unit["app_identifier"] = profile.get("ApplicationIdentifierPrefix", [team])[0] + "." + unit["new_id"]
                unit["effective"] = entitlements_of(product)
                if not unit["effective"]:
                    raise InstallerError(tr('Xcode did not generate signing entitlements.'))
                if not unit["is_main"]:
                    for key in MEMORY_ENTITLEMENTS: unit["effective"].pop(key, None)
                elif not unit["memory"]:
                    unit["effective"].pop(MEMORY, None)
                if EVA not in unit["requested"]:
                    unit["effective"].pop(EVA, None)
                assert_signature(unit["effective"], team, unit["app_identifier"], unit["memory"])
                assert_memory_scope(unit["effective"], unit["is_main"])
                assert_eva_policy(unit["effective"], unit["requested"])
                assert_capabilities(unit["effective"], profile, unit["requested"], unit["new_id"])
                unit["expiry"] = profile["ExpirationDate"].isoformat()
        emit("phase", message=tr('Signing locally with team ') + team, progress=0.68)
        for path in sorted(root.rglob("_CodeSignature"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        for unit in units:
            info = rewrite(unit["info"], mapping)
            info["CFBundleIdentifier"] = unit["new_id"]
            (unit["path"] / "Info.plist").write_bytes(plistlib.dumps(info))
            shutil.copyfile(unit["profile"], unit["path"] / "embedded.mobileprovision")
        for i, (path, unit) in enumerate(plan):
            identity = unit["identity"] if unit else units[0]["identity"]
            args = ["/usr/bin/codesign", "--force", "--sign", identity, "--timestamp=none"]
            if unit:
                ent = work / ("Sign" + str(i) + ".plist"); ent.write_bytes(plistlib.dumps(unit["effective"]))
                args.extend(["--entitlements", ent])
                binary = bundle_executable(path, unit["info"])
                binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            run(args + [path], log=log)
            run(["/usr/bin/codesign", "--verify", "--strict", "--verbose=2", path], log=log)
            if unit:
                if plistlib.loads((path / "Info.plist").read_bytes())["CFBundleIdentifier"] != unit["new_id"]:
                    raise InstallerError(tr('A component identifier does not match the identifier expected for the selected team.'))
                actual = entitlements_of(path)
                assert_signature(actual, team, unit["app_identifier"], unit["memory"])
                assert_memory_scope(actual, unit["is_main"])
                assert_eva_policy(actual, unit["requested"])
                assert_capabilities(actual, unit["profile_data"], unit["requested"], unit["new_id"])
                if external and actual.get("get-task-allow", False) != unit["effective"].get("get-task-allow", False):
                    raise InstallerError(tr('The final debugging entitlement does not match the imported profile.'))
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", root], log=log)
        emit("phase", message=tr('Verifying and exporting the signed IPA'), progress=0.82)
        output_dir = Path(config.get("outputDirectory") or str(Path.home() / "Downloads"))
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", metadata["name"]).strip("-.") or "App"
        output = output_dir / (safe_name + "-signed-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".ipa")
        pack_ipa(root, output)
        report_path = output.with_suffix(".verification.json")
        report_path.write_text(json.dumps(dict(team=team, account=account["name"], personalTeam=personal_team,
            memoryRequested=memory, memoryScope="mainExecutableOnly", extendedVirtualAddressingPolicy="importedProfile" if external else "automaticForTeam",
            signingMode="imported" if external else "xcode", certificateSHA1=certificate["id"] if external else None,
            importedCapabilityPolicy=("strict" if config.get("strictImportedCapabilities") else "authorizedOnly") if external else None,
            bundleIDPolicy="originalIdentifiers" if external else "selectedTeamSuffix",
            codeComponents=[dict(path=str(p.relative_to(root.parent)), kind="app/extension" if unit else "library/bundle", signatureVerified=True) for p,unit in plan],
            processes=[dict(bundleID=u["new_id"], originalBundleID=u["old_id"], bundleIDVerified=True,
                            team=team, mainExecutable=u["is_main"], memoryRequested=u["memory"],
                            memoryVerified=u["memory"], memoryPolicyVerified=True, expires=u["expiry"],
                            extendedVirtualAddressing=u["effective"].get(EVA, False), evaDecision=u["eva_policy"],
                            debuggingAllowed=u["effective"].get("get-task-allow", False),
                            profileUUID=u["profile_data"].get("UUID"),
                            excludedUnsupportedCapabilities=u.get("excluded_capabilities", []),
                            excludedOriginalMemoryCapabilities=sorted(k for k in MEMORY_ENTITLEMENTS if k in u["original"] and k not in u["requested"]),
                            requestedCapabilities=sorted(u["requested"]), supportsLiveActivities=u["info"].get("NSSupportsLiveActivities"),
                            extensionPoint=u["info"].get("NSExtension",{}).get("NSExtensionPointIdentifier")) for u in units]), indent=2, ensure_ascii=False)+"\n")
        report_path.chmod(0o600)
        if config.get("install", True):
            emit("phase", message=tr('Installing on ') + device["name"], progress=0.93)
            install_app(root, device, env, log)
        emit("complete", path=str(output), message=tr('Installation completed.') if config.get("install", True) else tr('Signed IPA saved.'),
             team=team, account=account["name"], memory=memory, expires=units[0]["expiry"],
             processCount=len(units), componentCount=len(plan), memoryProcessCount=int(memory), report=str(report_path))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["discover", "inspect", "save-account", "import-signing", "remove-signing", "run"])
    args = parser.parse_args()
    config = json.load(sys.stdin) if args.command != "discover" else {}
    try:
        if args.command == "discover":
            env = xcode_environment()
            version = run(["/usr/bin/xcrun", "xcodebuild", "-version"], env=env, quiet=True).decode().strip()
            try:
                accounts = discover_accounts()
            except InstallerError:
                accounts = []
            emit("discovery", accounts=accounts, devices=discover_devices(env), certificates=load_signing_assets(), xcode=version)
        elif args.command == "inspect":
            emit("ipa", **inspect_ipa(config["path"]))
        elif args.command == "import-signing":
            import_signing_assets(config)
        elif args.command == "remove-signing":
            # Forget only app-owned profile copies and metadata; shared Keychain identities remain.
            directory = asset_directory(config["certificate"])
            if directory.exists(): shutil.rmtree(directory)
            emit("assets", certificates=load_signing_assets())
        elif args.command == "save-account":
            save_account(config); emit("saved")
        else:
            sign_and_install(config)
    except Exception as error:
        emit("error", message=redact(str(error))); return 1
    return 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cancel); signal.signal(signal.SIGINT, cancel)
    sys.exit(main())
