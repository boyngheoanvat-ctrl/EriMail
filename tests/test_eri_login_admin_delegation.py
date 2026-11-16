"""Offline tests for the ERI Login Render proxy's expiring admin credentials."""
import importlib.util
import os
import unittest
from unittest.mock import patch
from pathlib import Path

os.environ.setdefault("ERI_ADMIN_KEY", "x" * 48)
spec = importlib.util.spec_from_file_location("eri_login_proxy", Path(__file__).resolve().parents[1] / "eri_login_proxy.py")
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)

class AdminDelegationTests(unittest.TestCase):
    def setUp(self):
        self.previous = proxy.ADMIN
        proxy.ADMIN = "test_primary_admin_secret_" + "a" * 42

    def tearDown(self):
        proxy.ADMIN = self.previous

    def test_key_is_valid_until_expiration(self):
        token, expiration = proxy.issue_admin_token(7)
        self.assertTrue(token.startswith("ERIADM1."))
        self.assertTrue(proxy.verify_admin_token(token))
        with patch.object(proxy.time, "time", return_value=expiration):
            self.assertFalse(proxy.verify_admin_token(token))

    def test_tamper_rejected(self):
        token, _ = proxy.issue_admin_token()
        self.assertFalse(proxy.verify_admin_token(token[:-1] + ("a" if token[-1] != "a" else "b")))
        self.assertFalse(proxy.verify_admin_token("invalid"))

    def test_invalid_duration(self):
        for days in (0, 91, -5, True, 7.5, "30"):
            with self.assertRaises(ValueError):
                proxy.issue_admin_token(days)

    def test_rotating_main_secret_revokes_extra_key(self):
        token, _ = proxy.issue_admin_token()
        proxy.ADMIN = "test_other_primary_admin_secret_" + "b" * 42
        self.assertFalse(proxy.verify_admin_token(token))

    def test_primary_vs_delegated(self):
        token, _ = proxy.issue_admin_token()
        handler = proxy.Handler.__new__(proxy.Handler)
        handler.headers = {"X-ERI-ADMIN-KEY": token}
        self.assertTrue(handler.is_admin())
        self.assertFalse(handler.is_primary_admin())
        handler.headers = {"X-ERI-ADMIN-KEY": proxy.ADMIN}
        self.assertTrue(handler.is_primary_admin())

if __name__ == "__main__":
    unittest.main()
