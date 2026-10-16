#!/usr/bin/env python3
"""Local IPA signing using the selected, existing Xcode account. Python 3.9+."""
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
        raise InstallerError("Operazione annullata.")
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
            raise InstallerError("Tempo esaurito: " + Path(str(args[0])).name)
        text = output.decode("utf-8", errors="replace")
        if log:
            with log.open("a") as f:
                f.write("\n$ " + " ".join(str(x) for x in args) + "\n" + redact(text))
        if CANCELLED:
            raise InstallerError("Operazione annullata.")
        if process.returncode:
            lines = [x.strip() for x in text.splitlines() if "error:" in x.lower() or "Error Domain=" in x]
            if "This device has reached the maximum number of installed apps using a free developer profile" in text or "maximum number of apps for free development profiles" in text:
                detail = "L’iPhone ha raggiunto il limite di app installate con profili gratuiti. Un aggiornamento richiede lo stesso Bundle ID della copia esistente.\n\n" + text[-9000:]
            else:
                detail = "\n".join(lines[-8:]) if lines else text[-2200:]
            if "Failed Registering Bundle Identifier" in text:
                detail += "\n\nApple non accetta l’identificativo con il suffisso del team selezionato."
            raise InstallerError(redact(detail.strip()) or "Comando non riuscito: " + str(args[0]))
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
        raise InstallerError("Installa Xcode completo e aprilo almeno una volta.")
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
                name = teams[0].get("teamName") if teams else "Account Xcode " + identifier[:8]
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
        raise InstallerError("L’account selezionato non è più disponibile in Xcode.")
    if team not in [t["id"] for t in account["teams"]]:
        raise InstallerError("Seleziona un team associato a questo account.")
    # Xcode's documented signing selector is DEVELOPMENT_TEAM. A shared team does not
    # distinguish two member accounts: fail closed rather than promise the chosen login.
    owners = {a["id"] for a in accounts if any(t["id"] == team for t in a["teams"])}
    if owners != {account_id}:
        raise InstallerError("Questo team è associato a più account Xcode. La selezione del singolo account non è univoca: usa il Personal Team dell’account desiderato.")
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
    with tempfile.TemporaryDirectory(prefix="xcode-ipa-discovery-") as folder:
        path = Path(folder) / "devices.json"
        run(["/usr/bin/xcrun", "devicectl", "list", "devices", "--json-output", path], env=env, quiet=True)
        return devices_from_json(json.loads(path.read_text()))


def save_account(config):
    accounts = discover_accounts()
    account = next((x for x in accounts if x["id"] == config["account"]), None)
    if not account:
        raise InstallerError("Questo account non è più presente in Xcode.")
    tid = config["team"].strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", tid):
        raise InstallerError("Il Team ID deve contenere 10 lettere o numeri.")
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
            raise InstallerError("Percorso non sicuro nell’IPA.")
        if name in names:
            raise InstallerError("L’IPA contiene nomi duplicati: " + name)
        names.add(name)
        mode = item.external_attr >> 16
        if stat.S_ISLNK(mode):
            target = archive.read(item).decode("utf-8")
            if target.startswith("/") or ".." in PurePosixPath(target).parts:
                raise InstallerError("L’IPA contiene un collegamento fuori dal pacchetto.")
        total += item.file_size
    return total


def inspect_ipa(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise InstallerError("Seleziona un file IPA esistente.")
    with zipfile.ZipFile(path) as archive:
        size = checked_members(archive)
        mains = [x for x in archive.namelist() if len(PurePosixPath(x).parts) == 3
                 and x.startswith("Payload/") and x.endswith(".app/Info.plist")]
        if len(mains) != 1:
            raise InstallerError("L’IPA deve contenere una sola app in Payload.")
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
            raise InstallerError("Spazio insufficiente per estrarre e firmare questa IPA.")
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
        raise InstallerError("Nome dell’eseguibile non valido: " + bundle.name)
    binary = bundle / name
    if not binary.resolve().is_relative_to(bundle.resolve()):
        raise InstallerError("L’eseguibile è fuori dal pacchetto dell’app.")
    if not is_macho(binary):
        raise InstallerError("Eseguibile iOS mancante: " + bundle.name)
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
            if len(initial) != 8: raise InstallerError("Header Mach-O troncato.")
            count = struct.unpack(endian + "I", initial[4:])[0]
            if not 0 < count <= 64: raise InstallerError("Numero architetture Mach-O non valido.")
            offsets = []
            for _ in range(count):
                entry = stream.read(entry_size)
                if len(entry) != entry_size: raise InstallerError("Header Mach-O troncato.")
                offset, size = struct.unpack_from(endian + fmt, entry, 8)
                if offset < 8 + count * entry_size or offset + size > length:
                    raise InstallerError("Architettura Mach-O fuori dal file.")
                offsets.append((offset, size))
        result = []
        for offset, size in offsets:
            stream.seek(offset); header = stream.read(min(size, 32))
            if header[:4] not in thin: raise InstallerError("Architettura Mach-O non valida.")
            endian, header_size = thin[header[:4]]
            if len(header) < header_size: raise InstallerError("Header Mach-O troncato.")
            filetype, commands, command_bytes = struct.unpack_from(endian + "III", header, 12)
            if commands > command_bytes // 8 or command_bytes > min(size-header_size, 16*1024*1024):
                raise InstallerError("Load command Mach-O non validi.")
            result.append(dict(offset=offset, endian=endian, headerSize=header_size,
                               filetype=filetype, commands=commands, commandBytes=command_bytes))
        return result


def check_encryption(path):
    for entry in macho_slices(path):
        with path.open("rb") as stream:
            stream.seek(entry["offset"] + entry["headerSize"]); data = stream.read(entry["commandBytes"])
        pos = 0
        for _ in range(entry["commands"]):
            if pos + 8 > len(data): raise InstallerError("Load command Mach-O troncati.")
            cmd, size = struct.unpack_from(entry["endian"] + "II", data, pos)
            if size < 8 or pos + size > len(data): raise InstallerError("Header Mach-O non valido.")
            if cmd in (0x21, 0x2c):
                if size < 20: raise InstallerError("Comando cifratura Mach-O non valido.")
                if struct.unpack_from(entry["endian"] + "I", data, pos + 16)[0] != 0:
                    raise InstallerError("Questa IPA contiene codice App Store cifrato e non può essere rifirmata: " + path.name)
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
            raise InstallerError("Questo componente richiede un proprio bundle app e profilo: " + str(bundle.relative_to(root)))
        plan[bundle] = None; covered.add(binary.resolve())
    for binary in root.rglob("*"):
        if binary.is_symlink() or not binary.is_file() or not is_macho(binary): continue
        kinds = {s["filetype"] for s in macho_slices(binary)}
        if not kinds.intersection({2, 6, 8, 11}): continue  # Object/debug files are resources, not executable code.
        check_encryption(binary)
        if binary.resolve() in covered: continue
        if 2 in kinds:
            raise InstallerError("Eseguibile senza un bundle e profilo dedicati: " + str(binary.relative_to(root)))
        plan[binary] = None; covered.add(binary.resolve())
    return sorted(plan.items(), key=lambda item: (-len(item[0].parts), str(item[0])))


def entitlements_of(path):
    result = subprocess.run(["/usr/bin/codesign", "-d", "--entitlements", ":-", str(path)], capture_output=True)
    if not result.stdout.strip():
        return {}
    try:
        return plistlib.loads(result.stdout)
    except plistlib.InvalidFileException:
        raise InstallerError("Impossibile leggere gli entitlement originali: " + path.name)


def valid_bundle_id(value):
    return isinstance(value, str) and len(value) <= 255 and bool(re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", value))


def original_bundle_ids(bundles):
    """Validate every process identifier and preserve its exact original spelling."""
    if any(not valid_bundle_id(value) for value in bundles):
        raise InstallerError("L’IPA contiene un identificativo app/estensione non valido. Gli identificativi originali non vengono modificati.")
    if len({value.lower() for value in bundles}) != len(bundles):
        raise InstallerError("L’IPA contiene identificativi app/estensione duplicati.")
    return {value: value for value in bundles}


def team_bundle_id(original, team):
    if not valid_bundle_id(original) or not re.fullmatch(r"[A-Z0-9]{10}", team):
        raise InstallerError("Identificativo originale o Team ID non valido.")
    result = original if original.endswith("." + team) else original + "." + team
    if not valid_bundle_id(result):
        raise InstallerError("L’identificativo con il suffisso del team supera 255 caratteri.")
    return result


def team_bundle_mapping(bundles, root_id, team):
    original_bundle_ids(bundles)
    root = team_bundle_id(root_id, team)
    result = {}
    for old in bundles:
        target = root + old[len(root_id):] if old == root_id or old.startswith(root_id + ".") else root + "." + old
        if not valid_bundle_id(target) or target.lower() in {value.lower() for value in result.values()}:
            raise InstallerError("Gli identificativi dei componenti con il team selezionato non sono validi o univoci.")
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
        raise InstallerError("La firma finale non rispecchia la disponibilità di Extended Virtual Addressing per il team selezionato.")


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
    project_settings = dict(SDKROOT="iphoneos", SUPPORTED_PLATFORMS="iphoneos", IPHONEOS_DEPLOYMENT_TARGET="16.0", ARCHS="arm64", ENABLE_BITCODE="NO")
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
                   compatibilityVersion="Xcode 14.0", developmentRegion="en", knownRegions=["en"], mainGroup=main_group,
                   productRefGroup=products, projectDirPath="", projectRoot="", targets=targets))
    project = folder / "Provision.xcodeproj"; project.mkdir()
    schemes = project / "xcshareddata/xcschemes"; schemes.mkdir(parents=True)
    for i, unit in enumerate(units):
        scheme = ET.Element("Scheme", LastUpgradeVersion="2700", version="1.3")
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
    (project / "project.pbxproj").write_bytes(plistlib.dumps(project_strings(dict(archiveVersion=1, classes={}, objectVersion=56, objects=objects, rootObject=root))))
    return project


def decode_profile(path):
    return plistlib.loads(run(["/usr/bin/security", "cms", "-D", "-i", path], quiet=True))


def assert_profile(profile, team, bundle_id, udid, memory):
    if team not in profile.get("TeamIdentifier", []):
        raise InstallerError("Il profilo appartiene a un team diverso da quello selezionato.")
    ent = profile.get("Entitlements", {})
    if ent.get("com.apple.developer.team-identifier") != team:
        raise InstallerError("Team del profilo ed entitlement non coincidono.")
    prefix = profile.get("ApplicationIdentifierPrefix", [team])[0]
    if not fnmatch.fnmatchcase(prefix + "." + bundle_id, ent.get("application-identifier", "")):
        raise InstallerError("Il profilo non autorizza l’identificativo di questa app.")
    if not profile.get("ProvisionsAllDevices") and udid not in profile.get("ProvisionedDevices", []):
        raise InstallerError("Il profilo non include l’iPhone selezionato.")
    expiry = profile.get("ExpirationDate")
    if not isinstance(expiry, dt.datetime) or expiry.replace(tzinfo=dt.timezone.utc) <= dt.datetime.now(dt.timezone.utc):
        raise InstallerError("Il profilo di provisioning è scaduto.")
    if memory and ent.get(MEMORY) is not True:
        raise InstallerError("Xcode non ha autorizzato Increased Memory Limit per il team selezionato. Installazione interrotta.")


def assert_signature(actual, team, app_identifier, memory):
    if actual.get("com.apple.developer.team-identifier") != team:
        raise InstallerError("La firma finale non corrisponde al team selezionato.")
    if actual.get("application-identifier") != app_identifier:
        raise InstallerError("L’identificativo nella firma non coincide con quello autorizzato dal profilo.")
    if (actual.get(MEMORY) is True) != memory:
        raise InstallerError("La firma finale non rispecchia l’opzione Increased Memory Limit.")


def assert_memory_scope(actual, main_process):
    if not main_process and MEMORY_ENTITLEMENTS.intersection(actual):
        raise InstallerError("Una capability memoria è stata applicata a un componente diverso dall’app principale.")


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
            raise InstallerError("Xcode ha omesso o modificato la capability richiesta “" + key + "” per " + bundle_id)
    for key, value in effective.items():
        if key not in authorized or not entitlement_allowed(authorized[key], value):
            raise InstallerError("Il profilo non autorizza la capability “" + key + "” per " + bundle_id)


def identity_for_profile(profile, identities_output):
    available = set(re.findall(r"\b[0-9A-F]{40}\b", identities_output.upper()))
    for certificate in profile.get("DeveloperCertificates", []):
        digest = hashlib.sha1(certificate).hexdigest().upper()
        if digest in available:
            return digest
    raise InstallerError("Il certificato autorizzato dal profilo non ha una chiave di firma disponibile. Controlla gli account in Xcode.")


def pack_ipa(app, output):
    temporary = output.with_name(output.name + ".partial")
    if output.exists() or temporary.exists():
        raise InstallerError("Il file di destinazione esiste già.")
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
                raise InstallerError("Verifica dell’IPA firmata non riuscita.")
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def sign_and_install(config):
    team = str(config["team"]).upper()
    account = selected_account_team(discover_accounts(), config["account"], team)
    personal_team = next(t for t in account["teams"] if t["id"] == team).get("personal", False)
    env = xcode_environment()
    device = next((d for d in discover_devices(env) if d["id"] == config["device"]), None)
    if not device or not device["udid"]:
        raise InstallerError("L’iPhone selezionato non è disponibile o non è associato al Mac.")
    metadata = inspect_ipa(config["ipa"])
    memory = bool(config.get("memory", False))
    new_id = team_bundle_id(metadata["bundleID"], team)
    if config.get("bundleID", new_id) != new_id:
        raise InstallerError("L’identificativo deve coincidere con quello dell’IPA più il suffisso del team di firma selezionato.")
    SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs = SUPPORT / "Logs"; logs.mkdir(exist_ok=True, mode=0o700)
    log = logs / (dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"); log.touch(mode=0o600)
    emit("logfile", path=str(log))
    emit("log", message="Team " + team + " · Bundle ID " + new_id + " (suffisso del team di firma)")
    with tempfile.TemporaryDirectory(prefix="xcode-ipa-sign-") as temp:
        work = Path(temp)
        emit("phase", message="Preparazione dell’IPA", progress=0.05)
        unpack_ipa(metadata["path"], work)
        root = work / metadata["root"]
        bundles = [root] + sorted([p for p in root.rglob("*") if p.suffix in (".app", ".appex") and (p / "Info.plist").is_file()])
        units = []
        for bundle in bundles:
            info = plistlib.loads((bundle / "Info.plist").read_bytes())
            if set(info.get("CFBundleSupportedPlatforms", ["iPhoneOS"])) - {"iPhoneOS"}:
                raise InstallerError("Questo pacchetto contiene un’app Watch o una piattaforma diversa da iOS: " + bundle.name)
            binary = bundle_executable(bundle, info)
            check_encryption(binary)
            units.append(dict(path=bundle, info=info, old_id=info["CFBundleIdentifier"], original=entitlements_of(bundle)))
        mapping = team_bundle_mapping([u["old_id"] for u in units], metadata["bundleID"], team)
        for unit in units:
            unit["is_main"] = unit["path"] == root
            unit["memory"] = memory and unit["is_main"]
            unit["new_id"] = mapping[unit["old_id"]]
            unit["requested"] = requested_entitlements(unit["original"], mapping, team, unit["new_id"], unit["memory"],
                                                        preserve=bool(config.get("preserveCapabilities", True)), main_process=unit["is_main"],
                                                        allow_eva=not personal_team)
            unit["eva_policy"] = "personalTeam" if personal_team else "automatic"
        plan = signing_plan(root, units)
        emit("log", message=str(len(units)) + " app/estensioni · " + str(len(plan)) + " componenti di codice da firmare")
        emit("log", message="Increased Memory Limit: " + ("solo " + root.name if memory else "disattivato") + "; tutti i componenti restano inclusi nella firma.")
        if personal_team:
            emit("log", message="Extended Virtual Addressing: esclusa, non disponibile per i Personal Team. Increased Memory Limit resta indipendente.")
        project = write_project(work / "Provisioning", units, team)
        for i, unit in enumerate(units):
            emit("phase", message="Xcode: profilo " + unit["path"].name, progress=0.12 + 0.50 * i / len(units))
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
                emit("log", message="Extended Virtual Addressing non autorizzata da Xcode per questo team: riprovo senza EVA, mantenendo Increased Memory Limit e le altre capability richieste.")
                run(args, env=env, log=log, timeout=600)
            product = work / "Products/Debug-iphoneos" / unit["product"]
            profile_path = product / "embedded.mobileprovision"
            if not profile_path.exists():
                raise InstallerError("Xcode non ha generato il profilo per " + unit["new_id"])
            profile = decode_profile(profile_path)
            assert_profile(profile, team, unit["new_id"], device["udid"], unit["memory"])
            identities = run(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"], quiet=True).decode()
            unit["identity"] = identity_for_profile(profile, identities)
            unit["profile"] = profile_path
            unit["profile_data"] = profile
            unit["app_identifier"] = profile.get("ApplicationIdentifierPrefix", [team])[0] + "." + unit["new_id"]
            unit["effective"] = entitlements_of(product)
            if not unit["effective"]:
                raise InstallerError("Xcode non ha generato gli entitlement di firma.")
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
        emit("phase", message="Firma locale con il team " + team, progress=0.68)
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
                    raise InstallerError("L’identificativo di un componente non coincide con quello previsto per il team selezionato.")
                actual = entitlements_of(path)
                assert_signature(actual, team, unit["app_identifier"], unit["memory"])
                assert_memory_scope(actual, unit["is_main"])
                assert_eva_policy(actual, unit["requested"])
                assert_capabilities(actual, unit["profile_data"], unit["requested"], unit["new_id"])
        run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", root], log=log)
        emit("phase", message="Verifica e creazione IPA firmata", progress=0.82)
        output_dir = Path(config.get("outputDirectory") or str(Path.home() / "Downloads"))
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", metadata["name"]).strip("-.") or "App"
        output = output_dir / (safe_name + "-signed-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".ipa")
        pack_ipa(root, output)
        report_path = output.with_suffix(".verification.json")
        report_path.write_text(json.dumps(dict(team=team, account=account["name"], personalTeam=personal_team,
            memoryRequested=memory, memoryScope="mainExecutableOnly", extendedVirtualAddressingPolicy="automaticForTeam", bundleIDPolicy="selectedTeamSuffix",
            codeComponents=[dict(path=str(p.relative_to(root.parent)), kind="app/extension" if unit else "library/bundle", signatureVerified=True) for p,unit in plan],
            processes=[dict(bundleID=u["new_id"], originalBundleID=u["old_id"], bundleIDVerified=True,
                            team=team, mainExecutable=u["is_main"], memoryRequested=u["memory"],
                            memoryVerified=u["memory"], memoryPolicyVerified=True, expires=u["expiry"],
                            extendedVirtualAddressing=u["effective"].get(EVA, False), evaDecision=u["eva_policy"],
                            excludedOriginalMemoryCapabilities=sorted(k for k in MEMORY_ENTITLEMENTS if k in u["original"] and k not in u["requested"]),
                            requestedCapabilities=sorted(u["requested"]), supportsLiveActivities=u["info"].get("NSSupportsLiveActivities"),
                            extensionPoint=u["info"].get("NSExtension",{}).get("NSExtensionPointIdentifier")) for u in units]), indent=2, ensure_ascii=False)+"\n")
        report_path.chmod(0o600)
        if config.get("install", True):
            emit("phase", message="Installazione su " + device["name"], progress=0.93)
            run(["/usr/bin/xcrun", "devicectl", "device", "install", "app", "--device", device["id"], root], env=env, log=log, timeout=600)
        emit("complete", path=str(output), message="Installazione completata." if config.get("install", True) else "IPA firmata salvata.",
             team=team, account=account["name"], memory=memory, expires=units[0]["expiry"],
             processCount=len(units), componentCount=len(plan), memoryProcessCount=int(memory), report=str(report_path))


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["discover", "inspect", "save-account", "run"])
    args = parser.parse_args()
    config = json.load(sys.stdin) if args.command != "discover" else {}
    try:
        if args.command == "discover":
            env = xcode_environment()
            version = run(["/usr/bin/xcrun", "xcodebuild", "-version"], env=env, quiet=True).decode().strip()
            emit("discovery", accounts=discover_accounts(), devices=discover_devices(env), xcode=version)
        elif args.command == "inspect":
            emit("ipa", **inspect_ipa(config["path"]))
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
