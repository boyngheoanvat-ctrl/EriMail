"""Offline regressions for the ERI GitHub Actions IPA processor."""
import hashlib
import io
import json
import os
import plistlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import actions_ipa_worker as worker


def sample_ipa(bundle="com.eri.sample", version="1.2.3", cryptid=0, traversal=False):
    binary = struct.pack("<IIIIIIII", 0xfeedfacf, 0x0100000c, 0, 2, 1, 24, 0, 0)
    binary += struct.pack("<IIIIII", 0x2c, 24, 0, 0, cryptid, 0)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("Payload/ERI.app/Info.plist", plistlib.dumps({
            "CFBundleDisplayName": "ERI Sample",
            "CFBundleIdentifier": bundle,
            "CFBundleExecutable": "ERIBin",
            "CFBundleShortVersionString": version,
        }))
        archive.writestr("Payload/ERI.app/ERIBin", binary)
        if traversal:
            archive.writestr("../escape", "not allowed")
    return payload.getvalue()


class Reply(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}


class Opener:
    def __init__(self, data):
        self.data = data

    def open(self, request, timeout=30):
        assert request.full_url == "https://files.eri.example/sample.ipa"
        return Reply(self.data)


class ActionsTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "ERI_IPA_SOURCE_HOSTS": "files.eri.example",
            "ERI_ACTIONS_MAX_IPA_BYTES": "1048576",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = self.root / "sources.json"
        self.out = self.root / "out"
        self.payload = sample_ipa()
        self.entry = {
            "id": "eri-test",
            "name": "ERI Sample",
            "bundle_id": "com.eri.sample",
            "version": "1.2.3",
            "url": "https://files.eri.example/sample.ipa",
            "sha256": hashlib.sha256(self.payload).hexdigest(),
            "distribution_authorized": True,
        }
        self.save([self.entry])

    def save(self, items):
        self.manifest.write_text(json.dumps({"items": items}), encoding="utf-8")

    def test_success_preserves_bytes(self):
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=Opener(self.payload)):
            result = worker.process(self.manifest, "eri-test", self.out)
        self.assertEqual(result["sha256"], hashlib.sha256(self.payload).hexdigest())
        self.assertEqual((self.out / "eri-eri-test.ipa").read_bytes(), self.payload)
        self.assertTrue(result["signature_preserved"])
        self.assertFalse(result["fairplay_encrypted"])
        self.assertEqual(json.loads((self.out / "validation.json").read_text())["version"], "1.2.3")

    def test_fail_closed_if_no_authorization(self):
        self.entry["distribution_authorized"] = False
        self.save([self.entry])
        with self.assertRaisesRegex(ValueError, "quyền phân phối"):
            worker.selected_source(self.manifest, "eri-test")

    def test_unlisted_host_and_injection(self):
        for url in ("http://files.eri.example/sample.ipa",
                    "https://evil.example/sample.ipa",
                    "https://127.0.0.1/sample.ipa",
                    "https://files.eri.example:8443/sample.ipa",
                    "https://files.eri.example/sample.ipa#secret"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                worker.validate_source(url)
        for bad_id in ("", "../secret", '"; echo bad', "many spaces"):
            with self.subTest(source_id=bad_id), self.assertRaises(ValueError):
                worker.selected_source(self.manifest, bad_id)

    def test_unexpected_hash_no_artifact(self):
        self.entry["sha256"] = "0" * 64
        self.save([self.entry])
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=Opener(self.payload)), \
             self.assertRaisesRegex(ValueError, "SHA-256"):
            worker.process(self.manifest, "eri-test", self.out)
        self.assertFalse((self.out / "eri-eri-test.ipa").exists())

    def test_encrypted_macho_rejected(self):
        bad = sample_ipa(cryptid=1)
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=Opener(bad)):
            self.entry["sha256"] = hashlib.sha256(bad).hexdigest()
            self.save([self.entry])
            with self.assertRaisesRegex(ValueError, "FairPlay"):
                worker.process(self.manifest, "eri-test", self.out)
        self.assertFalse((self.out / "eri-eri-test.ipa").exists())

    def test_bundle_mismatch_rejected(self):
        with patch.object(worker.urllib.request, "build_opener",
                          return_value=Opener(sample_ipa(bundle="com.other.app"))):
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                worker.process(self.manifest, "eri-test", self.out)
        self.assertFalse((self.out / "eri-eri-test.ipa").exists())

    def test_zip_traversal_rejected(self):
        unsafe = sample_ipa(traversal=True)
        temp = self.root / "unsafe.ipa"
        temp.write_bytes(unsafe)
        with self.assertRaises(Exception):
            worker.verify_ipa(temp, "com.eri.sample", "1.2.3")

    def test_no_source_means_no_fake_ipa(self):
        self.save([])
        with self.assertRaisesRegex(ValueError, "Không tìm thấy"):
            worker.process(self.manifest, "eri-test", self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
