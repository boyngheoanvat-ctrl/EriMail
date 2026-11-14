"""Offline regression tests for ERI ipatool + signing safeguards."""
import importlib.util
import io
import os
import plistlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("eri_ipatool_pipeline.py")
spec = importlib.util.spec_from_file_location("eri_ipatool_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def tiny_ipa(*, cryptid=0, metadata=False, bundle="com.example.eriapp"):
    executable = (
        struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, 24, 0, 0) +
        struct.pack("<IIIIII", 0x2C, 24, 0, 0, cryptid, 0)
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Payload/ERI.app/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": bundle, "CFBundleExecutable": "ERI",
            "CFBundleShortVersionString": "1.2.3",
        }))
        z.writestr("Payload/ERI.app/ERI", executable)
        if metadata:
            z.writestr("iTunesMetadata.plist", b"private-account-data")
    return stream.getvalue()


class ERIPipelineTests(unittest.TestCase):
    def test_appstore_url(self):
        self.assertEqual(pipeline.app_id_from_url(
            "https://apps.apple.com/vn/app/garena-li%C3%AAn-qu%C3%A2n-mobile/id1150288115?l=vi"),
            "1150288115")
        for url in ("https://evil.example/vn/app/test/id1150288115",
                    "http://apps.apple.com/vn/app/test/id1150288115",
                    "https://apps.apple.com.evil.com/vn/app/test/id1150288115",
                    "https://apps.apple.com/invalid", "https://user@apps.apple.com/app/id1150288115"):
            with self.subTest(url=url), self.assertRaises(pipeline.IPAError):
                pipeline.app_id_from_url(url)

    def test_clean_ipa_and_encrypted_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.ipa"
            path.write_bytes(tiny_ipa())
            self.assertEqual(pipeline.inspect_ipa(path)["cryptid"], 0)
            self.assertEqual(pipeline.inspect_ipa(path)["bundle_id"], "com.example.eriapp")
            for content in (tiny_ipa(cryptid=1), tiny_ipa(metadata=True),
                            tiny_ipa(bundle="bad bundle"), b"not an ipa"):
                with self.subTest(size=len(content)), self.assertRaises(pipeline.IPAError):
                    path.write_bytes(content)
                    pipeline.inspect_ipa(path)

    def test_unknown_macho_is_rejected(self):
        self.assertIsNone(pipeline._mach_o_encryption(b"not-mach-o"))
        self.assertIsNone(pipeline._mach_o_encryption(b"\xcf\xfa\xed\xfe"))
        self.assertTrue(pipeline._mach_o_encryption(
            struct.pack("<IiiIIIII", 0xFEEDFACF, 0, 0, 2, 1, 24, 0, 0) +
            struct.pack("<IIIIII", 0x2C, 24, 0, 0, 1, 0)))

    def test_ipatool_arguments_do_not_purchase(self):
        variables = {
            "ERI_APPLE_ID": "eri@example.com", "ERI_APPLE_PASSWORD": "not-a-real-pass",
            "ERI_IPATOOL_KEYCHAIN_PASSPHRASE": "not-a-real-passphrase",
            "ERI_APPLE_2FA_CODE": "123456",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, variables), \
                patch.object(pipeline, "_run_quiet") as runner:
            dest = Path(tmp) / "app.ipa"
            # The mocked download has no actual IPA; this is a command-generation test.
            with self.assertRaises(pipeline.IPAError):
                pipeline.download_with_ipatool("https://apps.apple.com/vn/app/id1150288115",
                                               dest, "123456789")
            calls = [args.args[0] for args in runner.call_args_list]
            self.assertEqual(len(calls), 2)
            self.assertIn("--non-interactive", calls[0])
            self.assertIn("--auth-code", calls[0])
            self.assertIn("--app-id", calls[1])
            self.assertIn("1150288115", calls[1])
            self.assertIn("--external-version-id", calls[1])
            self.assertNotIn("--purchase", calls[1])

    def test_no_credentials_and_no_incorrect_version_id(self):
        variables = {"ERI_APPLE_ID": "", "ERI_APPLE_PASSWORD": "",
                     "ERI_IPATOOL_KEYCHAIN_PASSPHRASE": "", "ERI_APPLE_2FA_CODE": ""}
        with patch.dict(os.environ, variables):
            with self.assertRaises(pipeline.IPAError):
                pipeline.download_with_ipatool("https://apps.apple.com/vn/app/id1150288115",
                                               Path("missing.ipa"))
        variables.update({"ERI_APPLE_ID": "a", "ERI_APPLE_PASSWORD": "b",
                          "ERI_IPATOOL_KEYCHAIN_PASSPHRASE": "c"})
        with patch.dict(os.environ, variables), self.assertRaises(pipeline.IPAError):
            pipeline.download_with_ipatool("https://apps.apple.com/vn/app/id1150288115",
                                           Path("missing.ipa"), "v123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
