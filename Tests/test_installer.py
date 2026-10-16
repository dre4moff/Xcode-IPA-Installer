import copy
import ast
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

SPEC = importlib.util.spec_from_file_location("installer", Path(__file__).parents[1] / "Backend/installer.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
TEAM = "ABCDEFGHIJ"
OTHER = "0123456789"
UDID = "test-device-udid"
ACCOUNT = dict(id="12345678-1234-1234-1234-123456789012", namespace="IDE.Identifiers.Prod", name="Fixture", teams=[dict(id=TEAM)])


def profile(bundle_id="local.fixture.app", memory=True):
    return dict(TeamIdentifier=[TEAM], ApplicationIdentifierPrefix=[TEAM],
                ExpirationDate=dt.datetime.utcnow() + dt.timedelta(days=2), ProvisionedDevices=[UDID],
                DeveloperCertificates=[b"fixture-certificate"], Entitlements={
                    "application-identifier": TEAM + "." + bundle_id,
                    "com.apple.developer.team-identifier": TEAM, m.MEMORY: memory})


class CoreTests(unittest.TestCase):
    def test_backend_localization_is_complete_and_defaults_to_english(self):
        english=m.load_translations("en")
        italian=m.load_translations("it")
        self.assertEqual(set(english),set(italian))
        tree=ast.parse(Path(m.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=="tr":
                if node.args and isinstance(node.args[0],ast.Constant):
                    self.assertIn(node.args[0].value,english)
                    self.assertTrue(italian[node.args[0].value])
        with patch.object(m,"TRANSLATIONS",english):self.assertEqual(m.tr("Preparing the IPA"),"Preparing the IPA")
        with patch.object(m,"TRANSLATIONS",italian):
            self.assertEqual(m.tr("Preparing the IPA"),"Preparazione dell’IPA")
            self.assertEqual(m.tr("untranslated system diagnostic"),"untranslated system diagnostic")

    def test_legacy_discovery_filters_unavailable_and_non_ios_devices(self):
        phone=dict(identifier=UDID,name="Fixture iPhone",platform="com.apple.platform.iphoneos",available=True,
                   simulator=False,operatingSystemVersion="16.2 (build)",interface="usb")
        values=m.devices_from_xcdevice([phone,dict(phone,simulator=True),dict(phone,available=False),
                                       dict(phone,platform="com.apple.platform.macosx")])
        self.assertEqual(len(values),1)
        self.assertEqual(values[0]["udid"],UDID)
        self.assertEqual(values[0]["version"],"16.2")
        self.assertEqual(values[0]["backend"],"ios-deploy")
        with patch.object(m,"run",return_value=json.dumps([phone]).encode()) as command:
            self.assertEqual(m.discover_devices({"DEVELOPER_DIR":"/nonexistent/legacy/Xcode"}),values)
            self.assertIn("xcdevice",command.call_args[0][0])

    def test_legacy_installer_uses_selected_device_without_launch_or_uninstall(self):
        device=dict(udid=UDID,version="16.2",backend="ios-deploy")
        with patch.object(m,"legacy_installer",return_value="/fixture/ios-deploy"),patch.object(m,"run") as command:
            m.install_app(Path("Fixture.app"),device,{},None)
            args=command.call_args[0][0]
            self.assertEqual(args[args.index("--id")+1],UDID)
            self.assertNotIn("--debug",args)
            self.assertNotIn("--uninstall",args)
        with self.assertRaises(m.InstallerError):m.legacy_installer(dict(version="17.0"))
        with patch.object(m.shutil,"which",return_value=None),patch.object(m.os.path,"isfile",return_value=False),self.assertRaises(m.InstallerError):
            m.legacy_installer(device)

    def test_eva_policy_is_independent_from_increased_memory(self):
        original = {m.EVA: True, m.MEMORY: True, "com.apple.security.application-groups": ["group.fixture"]}
        for allow_eva in [False, True]:
            requested = m.requested_entitlements(original, {}, TEAM, "local.fixture.app", True, allow_eva=allow_eva)
            self.assertIs(requested[m.MEMORY], True)
            self.assertEqual(requested.get(m.EVA, False), allow_eva)
            self.assertEqual(requested["com.apple.security.application-groups"], ["group.fixture"])
        self.assertNotIn(m.EVA, m.requested_entitlements({}, {}, TEAM, "local.fixture.app", True))
        self.assertTrue(m.eva_unavailable("Personal development teams do not support the Extended Virtual Addressing capability."))
        for error in ["Authentication failed", "No profiles were found", "App Groups are not supported", "Extended Virtual Addressing: connection timed out"]:
            self.assertFalse(m.eva_unavailable(error))
        with self.assertRaises(m.InstallerError): m.assert_eva_policy({m.EVA: True}, {})

    def test_memory_is_main_only_while_other_capabilities_are_preserved(self):
        original={key:True for key in m.MEMORY_ENTITLEMENTS}
        original["com.apple.security.application-groups"]=["group.fixture"]
        for name in ["ExampleJITHelper.appex","Widget.appex","Companion.app"]:
            with self.subTest(component=name):
                requested=m.requested_entitlements(original,{},TEAM,"local.fixture.aux",True,main_process=False)
                self.assertFalse(m.MEMORY_ENTITLEMENTS.intersection(requested))
                self.assertEqual(requested["com.apple.security.application-groups"],["group.fixture"])
                m.assert_memory_scope(requested,False)
        main=m.requested_entitlements(original,{},TEAM,"local.fixture.main",True,main_process=True)
        self.assertIs(main[m.MEMORY],True)
        for key in m.MEMORY_ENTITLEMENTS:
            with self.subTest(key=key),self.assertRaises(m.InstallerError):m.assert_memory_scope({key:True},False)

    def test_original_identifiers_are_preserved_exactly(self):
        identifiers=["com.Example.App", "com.Example.App.Share", "other.vendor.Companion"]
        self.assertEqual(m.original_bundle_ids(identifiers),{value:value for value in identifiers})

    def test_team_suffix_matches_selected_team_without_duplication(self):
        original="com.example.App"
        expected=original+"."+TEAM
        self.assertEqual(m.team_bundle_id(original,TEAM),expected)
        self.assertEqual(m.team_bundle_id(expected,TEAM),expected)
        self.assertEqual(m.team_bundle_id(original,OTHER),original+"."+OTHER)
        mapping=m.team_bundle_mapping([original,original+".Widget","other.vendor.Companion"],original,TEAM)
        self.assertEqual(mapping[original],expected)
        self.assertEqual(mapping[original+".Widget"],expected+".Widget")
        self.assertEqual(mapping["other.vendor.Companion"],expected+".other.vendor.Companion")
        with self.assertRaises(m.InstallerError):m.team_bundle_id(original,"invalid")
        with self.assertRaises(m.InstallerError):m.team_bundle_id("a."+"z"*250,TEAM)

    def test_install_error_keeps_free_profile_limit_and_occupied_identifiers(self):
        output=b'ERROR: Failed to install the app on the device.\nNSLocalizedRecoverySuggestion = This device has reached the maximum number of installed apps using a free developer profile: {( "ABCDEFGHIJ.com.example.ExistingApp" )}\nFunctionName = -[MIFreeProfileValidatedAppTracker _onQueue_addReferenceForApplicationIdentifier:bundle:error:]'
        process=Mock(returncode=1)
        process.communicate.return_value=(output,None)
        with patch.object(m.subprocess,"Popen",return_value=process),self.assertRaises(m.InstallerError) as caught:
            m.run(["offline-install-error"])
        self.assertIn("limit of apps",str(caught.exception))
        self.assertIn("com.example.ExistingApp",str(caught.exception))

    def test_widget_metadata_and_shared_keychain_relationship_are_preserved(self):
        mapping=m.original_bundle_ids(["original.app","original.app.widget"])
        group={"application-identifier":OTHER+".original.app","keychain-access-groups":[OTHER+".original.app.shared"],
               "com.apple.security.application-groups":["group.original.shared"]}
        main=m.requested_entitlements(group,mapping,TEAM,"original.app",True)
        widget=m.requested_entitlements(group,mapping,TEAM,"original.app.widget",True)
        self.assertEqual(main["keychain-access-groups"],widget["keychain-access-groups"])
        self.assertEqual(main["com.apple.security.application-groups"],["group.original.shared"])
        info={"CFBundleIdentifier":"original.app.widget","NSSupportsLiveActivities":True,
              "NSExtension":{"NSExtensionPointIdentifier":"com.apple.widgetkit-extension","ContainingApp":"original.app"}}
        updated=m.rewrite(info,mapping)
        self.assertIs(updated["NSSupportsLiveActivities"],True)
        self.assertEqual(updated["NSExtension"]["NSExtensionPointIdentifier"],"com.apple.widgetkit-extension")
        self.assertEqual(updated["NSExtension"]["ContainingApp"],"original.app")

    def test_missing_or_dropped_capability_blocks_signing(self):
        requested={m.MEMORY:True,"com.apple.security.application-groups":["group.fixture"]}
        prof=profile();prof["Entitlements"].update(requested)
        m.assert_capabilities(prof["Entitlements"],prof,requested,"local.fixture.app")
        for dropped in [True,False]:
            modified=copy.deepcopy(prof)
            del modified["Entitlements"]["com.apple.security.application-groups"]
            with self.subTest(dropped=dropped),self.assertRaises(m.InstallerError):
                m.assert_capabilities(modified["Entitlements"] if dropped else prof["Entitlements"],modified,requested,"local.fixture.app")

    def test_account_and_team_must_match_and_have_unique_owner(self):
        self.assertEqual(m.selected_account_team([ACCOUNT],ACCOUNT["id"],TEAM),ACCOUNT)
        for accounts, account_id, team in [([ACCOUNT],"missing-account",TEAM),([ACCOUNT],ACCOUNT["id"],OTHER),
                    ([ACCOUNT,dict(ACCOUNT,id="87654321-1234-1234-1234-123456789012")],ACCOUNT["id"],TEAM)]:
            with self.subTest(accounts=accounts),self.assertRaises(m.InstallerError):
                m.selected_account_team(accounts,account_id,team)

    def test_discovery_excludes_stale_account_labels(self):
        prefs = {"DVTDeveloperAccountManagerAppleIDLists": {ACCOUNT["namespace"]: [{"identifier": ACCOUNT["id"]}]}}
        labels = {ACCOUNT["id"]: dict(name="Fixture", teams=[dict(teamID=TEAM, teamName="Personal", isFreeProvisioningTeam=True)]),
                  "absent-account": dict(name="Stale", teams=[])}
        accounts = m.discover_accounts(prefs, labels)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["teams"][0]["id"], TEAM)

    def test_both_devicectl_schemas(self):
        common = dict(hardware=dict(platform="iOS", udid=UDID), state=dict(name="Fixture"),
                      connection=dict(pairingState="paired", transportType="usb"))
        new = dict(identifier="new", properties=common)
        old = dict(identifier="old", hardwareProperties=common["hardware"], deviceProperties=common["state"], connectionProperties=common["connection"])
        not_paired = dict(identifier="unpaired", properties=dict(common, connection=dict(pairingState="unpaired")))
        values = m.devices_from_json(dict(result=dict(devices=[new, old, not_paired])))
        self.assertEqual([d["id"] for d in values], ["new", "old"])
        self.assertTrue(all(d["udid"] == UDID for d in values))

    def test_memory_toggle_and_original_capabilities_optional(self):
        original = {m.MEMORY: True, "aps-environment": "production", "com.apple.developer.team-identifier": OTHER}
        basic = m.requested_entitlements(original, {}, TEAM, "local.fixture.app", False,preserve=False)
        self.assertNotIn(m.MEMORY, basic)
        self.assertNotIn("aps-environment", basic)
        enabled = m.requested_entitlements(original, {}, TEAM, "local.fixture.app", True,preserve=False)
        self.assertIs(enabled[m.MEMORY], True)
        preserved = m.requested_entitlements(original, {}, TEAM, "local.fixture.app", False, preserve=True)
        self.assertEqual(preserved["aps-environment"], "production")

    def test_invalid_or_duplicate_original_ids_fail_without_renaming(self):
        for ids in [["old.app","old.app"],["old.App","old.app"],["invalid"],["a."+"z"*254]]:
            with self.subTest(ids=ids),self.assertRaises(m.InstallerError):
                m.original_bundle_ids(ids)

    def test_profile_guards_team_device_memory_expiry_identifier(self):
        good = profile()
        m.assert_profile(good, TEAM, "local.fixture.app", UDID, True)
        variants = [dict(good, TeamIdentifier=[OTHER]), dict(good, ProvisionedDevices=["other"]),
                    dict(good, ExpirationDate=dt.datetime.utcnow()-dt.timedelta(days=1))]
        for key, value in [(m.MEMORY, False), ("com.apple.developer.team-identifier", OTHER), ("application-identifier", TEAM+".wrong.app")]:
            candidate = copy.deepcopy(good); candidate["Entitlements"][key] = value; variants.append(candidate)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises(m.InstallerError):
                m.assert_profile(candidate, TEAM, "local.fixture.app", UDID, True)

    def test_final_signature_guards_same_team_identifier_toggle(self):
        ent = profile()["Entitlements"]
        m.assert_signature(ent, TEAM, TEAM+".local.fixture.app", True)
        for key, value in [(m.MEMORY, False), ("com.apple.developer.team-identifier", OTHER), ("application-identifier", "wrong")]:
            with self.subTest(key=key), self.assertRaises(m.InstallerError):
                m.assert_signature(dict(ent, **{key: value}), TEAM, TEAM+".local.fixture.app", True)

    def test_signing_identity_must_be_in_profile(self):
        good = profile(); digest = hashlib.sha1(b"fixture-certificate").hexdigest().upper()
        self.assertEqual(m.identity_for_profile(good, digest), digest)
        with self.assertRaises(m.InstallerError):
            m.identity_for_profile(good, "1"*40)

    def test_archive_validation_traversal_duplicate_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            for name, symlink in [("../escape", False), ("Payload/A.app/escape", True), ("/absolute", False)]:
                path = Path(temp)/"bad.zip"
                with zipfile.ZipFile(path, "w") as z:
                    item = zipfile.ZipInfo(name)
                    if symlink:
                        item.external_attr = (stat.S_IFLNK|0o777)<<16
                    z.writestr(item, "../../escape" if symlink else "data")
                with zipfile.ZipFile(path) as z, self.assertRaises(m.InstallerError):
                    m.checked_members(z)

    def test_encrypted_macho_is_rejected(self):
        header = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, 2, 1, 24, 0, 0)
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp)/"Binary"
            for cryptid in [0, 1]:
                binary.write_bytes(header+struct.pack("<6I", 0x2c, 24, 0, 0, cryptid, 0))
                if cryptid:
                    with self.assertRaises(m.InstallerError): m.check_encryption(binary)
                else: m.check_encryption(binary)

    def test_executable_cannot_escape_app_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp)/"Fixture.app";app.mkdir()
            for name in ["../outside", "/outside", "..", ""]:
                with self.subTest(name=name),self.assertRaises(m.InstallerError):
                    m.bundle_executable(app,dict(CFBundleExecutable=name))

    def test_project_contains_same_team_and_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            units = [dict(path=Path("Fixture.app"), info={}, new_id="local.fixture.app", requested={m.MEMORY:True})]
            project = m.write_project(Path(temp)/"Project", units, TEAM)
            value = plistlib.loads((project/"project.pbxproj").read_bytes())
            targets = [o for o in value["objects"].values() if o.get("isa")=="XCBuildConfiguration" and "DEVELOPMENT_TEAM" in o["buildSettings"]]
            self.assertEqual(targets[0]["buildSettings"]["DEVELOPMENT_TEAM"], TEAM)
            ent = plistlib.loads((project.parent/"Provision0.entitlements").read_bytes())
            self.assertIs(ent[m.MEMORY], True)
            self.assertTrue((project/"xcshareddata/xcschemes/Provision0.xcscheme").is_file())


@unittest.skipUnless(os.environ.get("XCODE_IPA_FIXTURES"), "Set XCODE_IPA_FIXTURES for offline codesign integration")
class OfflinePipelineTests(unittest.TestCase):
    """Real local ad-hoc signing/ZIP verification; Apple provisioning/install always intercepted."""
    def scenario(self, memory, wrong_team=False, main_memory_missing=False, original_eva=False,
                 personal_team=False, eva_rejected=False, unrelated_rejection=False, rename_attempt=False,
                 already_suffixed=False, external=False, debug=False, wildcard=False, missing_extension=False, unsupported_capability=False, strict_imported=False):
        fixture = Path(os.environ["XCODE_IPA_FIXTURES"])
        events, commands = [], []
        real_run = m.run
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp); payload = temp/"Input/Payload"; payload.mkdir(parents=True)
            app = payload/"Fixture.app"; shutil.copytree(fixture/"Provision0.app", app)
            extension = app/"PlugIns/Share.appex"; extension.parent.mkdir()
            shutil.copytree(fixture/"Provision1.appex", extension)
            companion=app/"Companions/Companion.app";companion.parent.mkdir()
            shutil.copytree(fixture/"Provision0.app",companion)
            original_id="local.fixture.app"+("."+TEAM if already_suffixed else "")
            target_id=original_id if external else "local.fixture.app."+TEAM
            fingerprint=hashlib.sha1(b"fixture-certificate").hexdigest().upper()
            external_records=[]
            if external:
                for n,suffix in enumerate(["", ".companion", ".share"]):
                    if missing_extension and n:continue
                    if wildcard and n:continue
                    prof=profile("local.fixture.*" if wildcard else target_id+suffix,memory)
                    prof["Entitlements"].update({"keychain-access-groups":[TEAM+".*"],"get-task-allow":debug,
                        "com.apple.security.application-groups":["group.fixture"]})
                    if original_eva:prof["Entitlements"][m.EVA]=True
                    if main_memory_missing:prof["Entitlements"].pop(m.MEMORY,None)
                    if wrong_team:prof["TeamIdentifier"]=[OTHER]
                    path=temp/(str(n)+".mobileprovision");path.write_bytes(plistlib.dumps(prof))
                    external_records.append((path,prof))
            for p, bundle_id in [(app,original_id),(extension,original_id+".share"),(companion,original_id+".companion")]:
                info = plistlib.loads((p/"Info.plist").read_bytes());info["CFBundleIdentifier"] = bundle_id
                if p==app:info["NSSupportsLiveActivities"]=True
                elif p==extension:info["NSExtension"]={"NSExtensionPointIdentifier":"com.apple.widgetkit-extension","ContainingApp":original_id}
                (p/"Info.plist").write_bytes(plistlib.dumps(info))
            library=fixture/"libFixture.dylib"
            for container in [app/"Frameworks/Runtime.framework",extension/"Frameworks/Inner.framework"]:
                container.mkdir(parents=True)
                shutil.copyfile(library,container/"BinaryCore")
                (container/"Info.plist").write_bytes(plistlib.dumps(dict(CFBundleExecutable="BinaryCore",CFBundleIdentifier="local.fixture."+container.stem,CFBundlePackageType="FMWK",CFBundleVersion="1")))
            loose=app/"Assets/LooseLibrary";loose.parent.mkdir();shutil.copyfile(library,loose)
            if original_eva or unsupported_capability:
                entpath = temp/"original.entitlements"
                original_ent={m.MEMORY: True, "com.apple.security.application-groups": ["group.fixture"]}
                if original_eva:original_ent[m.EVA]=True
                if unsupported_capability:original_ent["com.apple.developer.carplay-audio"]=True
                entpath.write_bytes(plistlib.dumps(original_ent))
                real_run(["/usr/bin/codesign", "--force", "--sign", "-", "--entitlements", entpath, "--timestamp=none", app], quiet=True)
            source = temp/"input.ipa"; m.pack_ipa(app, source)
            original_infos={str(p.relative_to(app)):p.read_bytes() for p in app.rglob("Info.plist")}
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            def intercepted(args, **kwargs):
                args = [str(x) for x in args];commands.append(args)
                if "xcodebuild" in args:
                    self.assertIn("DEVELOPMENT_TEAM="+TEAM,args)
                    self.assertFalse(any(x.startswith("-DVT") for x in args))
                    project = Path(args[args.index("-project")+1])
                    target = args[args.index("-scheme")+1]
                    i = int(target.replace("Provision", ""))
                    requested=plistlib.loads((project.parent/(target+".entitlements")).read_bytes())
                    if personal_team: self.assertNotIn(m.EVA, requested)
                    if i==0 and unrelated_rejection: raise m.InstallerError("App Groups are not supported for this team.")
                    if i==0 and eva_rejected and m.EVA in requested:
                        self.assertEqual(requested.get(m.MEMORY), memory)
                        raise m.InstallerError("Personal development teams do not support the Extended Virtual Addressing capability.")
                    generated_info=plistlib.loads((project.parent/(target+".plist")).read_bytes())
                    is_extension="NSExtension" in generated_info
                    suffix = ".appex" if is_extension else ".app"
                    products = Path(next(x.split("=",1)[1] for x in args if x.startswith("SYMROOT=")))
                    product = products/"Debug-iphoneos"/(target+suffix);product.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copytree(fixture/("Provision1.appex" if is_extension else "Provision0.app"), product)
                    pbx=plistlib.loads((project/"project.pbxproj").read_bytes())
                    settings=next(o["buildSettings"] for o in pbx["objects"].values() if o.get("isa")=="XCBuildConfiguration" and o["buildSettings"].get("PRODUCT_NAME")==target)
                    new_id=settings["PRODUCT_BUNDLE_IDENTIFIER"]
                    prof = profile(new_id, memory)
                    requested=plistlib.loads((project.parent/(target+".entitlements")).read_bytes())
                    if i and m.MEMORY_ENTITLEMENTS.intersection(requested):
                        raise m.InstallerError("Entitlement com.apple.developer.kernel.increased-memory-limit not found and could not be included in profile.")
                    for key,value in requested.items():
                        prof["Entitlements"][key]=[v.replace("$(AppIdentifierPrefix)",TEAM+".") for v in value] if isinstance(value,list) else value
                    if is_extension:prof["Entitlements"].pop(m.MEMORY,None)
                    if main_memory_missing and i==0:prof["Entitlements"].pop(m.MEMORY,None)
                    if wrong_team: prof["TeamIdentifier"] = [OTHER]
                    (product/"embedded.mobileprovision").write_bytes(plistlib.dumps(prof))
                    ent = dict(prof["Entitlements"])
                    entpath = project.parent/(target+".effective");entpath.write_bytes(plistlib.dumps(ent))
                    real_run(["/usr/bin/codesign","--force","--sign","-","--entitlements",entpath,"--timestamp=none",product],quiet=True)
                    return b"fixture: offline Xcode result"
                if args[0]=="/usr/bin/security" and "find-identity" in args: return fingerprint.encode()
                if "devicectl" in args: return b"fixture: intercepted install"
                if external and args[0]=="/usr/bin/codesign" and "--sign" in args:
                    args=list(args);args[args.index("--sign")+1]="-"
                return real_run(args, **kwargs)
            account = dict(ACCOUNT, teams=[dict(id=TEAM, personal=personal_team)])
            with patch.object(m,"discover_accounts",return_value=[account]), \
                 patch.object(m,"xcode_environment",return_value=os.environ.copy()), \
                 patch.object(m,"discover_devices",return_value=[dict(id="fixture",udid=UDID,name="Offline fixture")]), \
                 patch.object(m,"SUPPORT",temp/"Support"), \
                 patch.object(m,"decode_profile",side_effect=lambda p:plistlib.loads(p.read_bytes())), \
                 patch.object(m,"identity_for_profile",return_value="-"), \
                 patch.object(m,"run",side_effect=intercepted), \
                 patch.object(m,"emit",side_effect=lambda kind,**values:events.append(dict(type=kind,**values))):
                config = dict(account=ACCOUNT["id"],team=TEAM,device="fixture",ipa=str(source),memory=memory,
                              outputDirectory=str(temp/"Output"),install=True)
                if external:
                    config.update(signingMode="imported",certificate=fingerprint,profile="fixture",strictImportedCapabilities=strict_imported)
                    context=(dict(id=fingerprint,name="Imported fixture",team=TEAM),external_records,external_records[0])
                    context_patch=patch.object(m,"external_context",return_value=context)
                    context_patch.start();self.addCleanup(context_patch.stop)
                if rename_attempt:config["bundleID"]="local.fixture.app."+OTHER
                if wrong_team or main_memory_missing or unrelated_rejection or rename_attempt or missing_extension or (unsupported_capability and strict_imported):
                    with self.assertRaises(m.InstallerError): m.sign_and_install(config)
                    self.assertFalse(any("devicectl" in c for c in commands))
                    if unrelated_rejection: self.assertEqual(sum("xcodebuild" in c for c in commands), 1)
                    if rename_attempt:self.assertFalse(any("xcodebuild" in c for c in commands))
                    return
                m.sign_and_install(config)
                complete = next(e for e in events if e["type"]=="complete")
                output = Path(complete["path"])
                dest=temp/"Verify";dest.mkdir();m.unpack_ipa(output,dest)
                signed = dest/"Payload/Fixture.app"
                for relative,original_info in original_infos.items():
                    if relative in ["Info.plist","PlugIns/Share.appex/Info.plist","Companions/Companion.app/Info.plist"]:
                        expected=plistlib.loads(original_info)
                        expected["CFBundleIdentifier"]=target_id+expected["CFBundleIdentifier"][len(original_id):]
                        if relative=="PlugIns/Share.appex/Info.plist":expected["NSExtension"]["ContainingApp"]=target_id
                        self.assertEqual(plistlib.loads((signed/relative).read_bytes()),expected,relative)
                    else:self.assertEqual((signed/relative).read_bytes(),original_info,relative)
                real_run(["/usr/bin/codesign","--verify","--deep","--strict",signed],quiet=True)
                self.assertEqual(m.entitlements_of(signed).get(m.MEMORY,False),memory)
                expected_eva = original_eva and not personal_team and not eva_rejected
                self.assertEqual(m.entitlements_of(signed).get(m.EVA,False),expected_eva)
                if original_eva:
                    self.assertEqual(m.entitlements_of(signed)["com.apple.security.application-groups"], ["group.fixture"])
                self.assertEqual(sum("xcodebuild" in c for c in commands), 0 if external else 4 if eva_rejected else 3)
                if external:
                    for process in [signed,signed/"PlugIns/Share.appex",signed/"Companions/Companion.app"]:
                        self.assertEqual(m.entitlements_of(process).get("get-task-allow"),debug)
                for auxiliary in [signed/"PlugIns/Share.appex",signed/"Companions/Companion.app"]:
                    self.assertFalse(m.MEMORY_ENTITLEMENTS.intersection(m.entitlements_of(auxiliary)))
                    real_run(["/usr/bin/codesign","--verify","--strict",auxiliary],quiet=True)
                main_info=plistlib.loads((signed/"Info.plist").read_bytes())
                self.assertIs(main_info["NSSupportsLiveActivities"],True)
                widget_info=plistlib.loads((signed/"PlugIns/Share.appex/Info.plist").read_bytes())
                self.assertEqual(widget_info["NSExtension"]["NSExtensionPointIdentifier"],"com.apple.widgetkit-extension")
                verification=json.loads(Path(complete["report"]).read_text())
                self.assertEqual(len(verification["codeComponents"]),6)
                if unsupported_capability:
                    self.assertNotIn("com.apple.developer.carplay-audio",m.entitlements_of(signed))
                    self.assertIn("com.apple.developer.carplay-audio",verification["processes"][0]["excludedUnsupportedCapabilities"])
                self.assertEqual(verification["memoryScope"],"mainExecutableOnly")
                self.assertEqual(verification["bundleIDPolicy"],"originalIdentifiers" if external else "selectedTeamSuffix")
                self.assertTrue(all(p["bundleID"]==target_id+p["originalBundleID"][len(original_id):] and p["bundleIDVerified"] for p in verification["processes"]))
                self.assertEqual(verification["processes"][0]["extendedVirtualAddressing"],expected_eva)
                self.assertEqual(sum(p["memoryRequested"] for p in verification["processes"]),int(memory))
                self.assertTrue(all(p["memoryVerified"]==(memory and p["mainExecutable"]) for p in verification["processes"]))
                sign_paths=[c[-1] for c in commands if c[0]=="/usr/bin/codesign" and "--force" in c]
                self.assertEqual(len(sign_paths),6)
                for i,p in enumerate(sign_paths):
                    self.assertFalse(any(Path(p) in Path(q).parents for q in sign_paths[i+1:] if q!=p))
                for p in [signed/"Frameworks/Runtime.framework",signed/"PlugIns/Share.appex/Frameworks/Inner.framework",signed/"Assets/LooseLibrary"]:
                    real_run(["/usr/bin/codesign","--verify","--strict",p],quiet=True)
                self.assertEqual(complete["team"],TEAM)
                self.assertEqual(original_hash,hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertTrue(any("devicectl" in c for c in commands))

    def test_memory_enabled_offline_pipeline(self): self.scenario(True)
    def test_memory_disabled_offline_pipeline(self): self.scenario(False)
    def test_wrong_team_cannot_reach_install(self): self.scenario(True,wrong_team=True)
    def test_main_without_memory_cannot_reach_install(self):self.scenario(True,main_memory_missing=True)
    def test_personal_team_excludes_original_eva_but_requires_memory(self):self.scenario(True,original_eva=True,personal_team=True)
    def test_supporting_team_preserves_original_eva_and_memory(self):self.scenario(True,original_eva=True)
    def test_eva_refusal_retries_without_eva_and_retains_memory(self):self.scenario(True,original_eva=True,eva_rejected=True)
    def test_eva_fallback_cannot_drop_required_memory(self):self.scenario(True,original_eva=True,eva_rejected=True,main_memory_missing=True)
    def test_other_capability_failure_is_not_silently_retried(self):self.scenario(True,original_eva=True,unrelated_rejection=True)
    def test_attempted_bundle_id_override_cannot_provision_or_install(self):self.scenario(True,rename_attempt=True)
    def test_existing_correct_team_suffix_is_not_duplicated(self):self.scenario(True,already_suffixed=True)

    def test_imported_distribution_signs_every_component_with_exact_profiles(self):self.scenario(True,external=True,original_eva=True)
    def test_imported_development_keeps_debugging_for_all_processes(self):self.scenario(True,external=True,debug=True)
    def test_imported_wildcard_profile_covers_all_processes(self):self.scenario(False,external=True,wildcard=True)
    def test_imported_missing_extension_profile_cannot_install(self):self.scenario(True,external=True,missing_extension=True)
    def test_imported_memory_permission_is_required(self):self.scenario(True,external=True,main_memory_missing=True)
    def test_imported_wrong_team_cannot_install(self):self.scenario(True,external=True,wrong_team=True)

    def test_imported_unsupported_carplay_is_excluded_without_losing_memory_or_other_components(self):self.scenario(True,external=True,unsupported_capability=True)
    def test_imported_strict_capabilities_stop_before_install(self):self.scenario(True,external=True,unsupported_capability=True,strict_imported=True)

if __name__ == "__main__":
    unittest.main(verbosity=2)
