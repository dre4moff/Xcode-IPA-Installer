import copy
import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

from test_installer import m, TEAM, UDID, profile


class ExternalAssetTests(unittest.TestCase):
    def test_profile_binding_uses_apple_prefix_not_team_prefix(self):
        prof = profile("com.example.Fixed")
        prof["ApplicationIdentifierPrefix"] = ["OLDPREFIX1"]
        prof["Entitlements"]["application-identifier"] = "OLDPREFIX1.com.example.Fixed"
        prof["Entitlements"]["keychain-access-groups"] = ["OLDPREFIX1.*"]
        prof["Entitlements"]["get-task-allow"] = True
        self.assertEqual(m.profile_pattern(prof), "com.example.Fixed")
        m.assert_profile(prof, TEAM, "com.example.Fixed", UDID, True)
        requested = m.requested_entitlements({}, {}, TEAM, "com.example.Fixed", True)
        actual = m.external_effective(prof, requested, TEAM, "com.example.Fixed")
        self.assertEqual(actual["application-identifier"], "OLDPREFIX1.com.example.Fixed")
        self.assertEqual(actual["keychain-access-groups"], ["OLDPREFIX1.com.example.Fixed"])
        self.assertIs(actual["get-task-allow"], True)
        m.assert_capabilities(actual, prof, requested, "com.example.Fixed")

    def test_exact_and_wildcard_id_rules(self):
        vectors = [("com.original.App", "*", "com.original.App"),
                   ("com.original.App", "com.original.*", "com.original.App"),
                   ("com.original.App", "com.original.App", "com.original.App"),
                   ("com.original.App", "com.*", "com.original.App")]
        for original, pattern, expected in vectors:
            self.assertEqual(m.external_bundle_id(original, pattern), expected)
        for pattern in ["com.vendor.Fixed","com.vendor.*"]:
            with self.assertRaises(m.InstallerError):m.external_bundle_id("com.original.App",pattern)
        for pattern in ["com.*.bad", "com.example.?", "com.example.[ab]", "", "com.example.*.*"]:
            prof = profile(pattern)
            with self.subTest(pattern=pattern), self.assertRaises(m.InstallerError): m.profile_pattern(prof)

    def test_import_copies_only_profiles_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); path = root / "input.mobileprovision"
            path.write_bytes(plistlib.dumps(profile("*")))
            fingerprint = hashlib.sha1(b"fixture-certificate").hexdigest().upper()
            metadata = dict(id=fingerprint, name="Fixture", team=TEAM, expires="2030-01-01T00:00:00Z")
            with patch.object(m, "SUPPORT", root / "Support"), patch.object(m, "certificate_tool", return_value=metadata) as helper, \
                 patch.object(m, "decode_profile", side_effect=lambda p: plistlib.loads(p.read_bytes())), patch.object(m, "emit"):
                m.import_signing_assets(dict(p12="not-copied.p12", password="never-saved", profiles=[str(path)]))
                self.assertEqual(helper.call_args[0][1], "never-saved")
                result = m.load_signing_assets()
                self.assertEqual(result[0]["profiles"][0]["bundlePattern"], "*")
                for p in m.assets_directory().rglob("*"):
                    if p.is_file():
                        self.assertEqual(p.stat().st_mode & 0o777, 0o600)
                        self.assertNotIn(b"never-saved", p.read_bytes())
                        self.assertNotEqual(p.suffix, ".p12")
                m.import_signing_assets(dict(certificate=fingerprint, profiles=[str(path)]))
                self.assertEqual(len(m.load_signing_assets()[0]["profiles"]), 1)
                self.assertEqual(helper.call_count, 1)

    def test_mixed_certificates_are_rejected_before_key_import(self):
        with tempfile.TemporaryDirectory() as temp:
            paths = [Path(temp) / "a.mobileprovision", Path(temp) / "b.mobileprovision"]
            first, second = profile(), profile()
            second["DeveloperCertificates"] = [b"other-certificate"]
            for p, data in zip(paths, [first, second]): p.write_bytes(plistlib.dumps(data))
            with patch.object(m, "decode_profile", side_effect=lambda p: plistlib.loads(p.read_bytes())), \
                 patch.object(m, "certificate_tool") as helper, self.assertRaises(m.InstallerError):
                m.import_signing_assets(dict(p12="fixture.p12", profiles=[str(p) for p in paths]))
            helper.assert_not_called()

    def test_debugging_flag_comes_from_profile_independently_of_eva(self):
        for debug in [False, True]:
            prof = profile("*"); prof["Entitlements"].update({"get-task-allow": debug, m.EVA: True})
            effective = m.external_effective(prof, {m.EVA: True}, TEAM, "com.example.App")
            self.assertIs(effective["get-task-allow"], debug)
            self.assertIs(effective[m.EVA], True)
            bad = dict(effective, **{"get-task-allow": not debug})
            with self.assertRaises(m.InstallerError): m.assert_capabilities(bad, prof, {}, "com.example.App")

    def test_expired_or_wrong_device_profiles_do_not_authorize_signing(self):
        import datetime as dt
        for change in [dict(ProvisionedDevices=["different-device"]), dict(ExpirationDate=dt.datetime(2000, 1, 1))]:
            prof = dict(profile("*"), **change)
            with self.assertRaises(m.InstallerError): m.assert_profile(prof, TEAM, "com.example.App", UDID, True)

    def test_imported_paths_cannot_escape_asset_directory(self):
        for value in ["../private", "A" * 39, "g" * 40, "/tmp/keychain"]:
            with self.assertRaises(m.InstallerError): m.asset_directory(value)

    def test_compatibility_filter_preserves_authorized_groups_and_required_memory(self):
        prof = profile("com.example.App")
        ent = prof["Entitlements"]
        ent["keychain-access-groups"] = [TEAM + ".com.example.App"]
        ent["com.apple.security.application-groups"] = ["group.allowed"]
        requested = {m.MEMORY: True, "com.apple.developer.carplay-audio": True,
                     "keychain-access-groups": [TEAM + ".original.oldgroup"],
                     "com.apple.security.application-groups": ["group.allowed", "group.denied"]}
        effective, excluded = m.compatible_capabilities(prof, requested, TEAM, "com.example.App")
        self.assertIs(effective[m.MEMORY], True)
        self.assertEqual(effective["keychain-access-groups"], [TEAM + ".com.example.App"])
        self.assertEqual(effective["com.apple.security.application-groups"], ["group.allowed"])
        self.assertNotIn("com.apple.developer.carplay-audio", effective)
        self.assertIn("com.apple.developer.carplay-audio", excluded)
        self.assertIn("keychain-access-groups", excluded)
        ent.pop(m.MEMORY)
        with self.assertRaises(m.InstallerError): m.compatible_capabilities(prof, requested, TEAM, "com.example.App")

    def test_strict_profile_capabilities_never_silently_drop_permissions(self):
        prof = profile("*")
        with self.assertRaises(m.InstallerError):
            m.compatible_capabilities(prof, {"com.apple.developer.carplay-audio": True}, TEAM, "com.example.App", strict=True)


if __name__ == "__main__": unittest.main()
