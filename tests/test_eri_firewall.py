"""Run with python -m unittest discover -s tests -p 'test_eri_firewall.py'."""
import unittest
from eri_login_firewall import ERIFirewall


class FirewallTests(unittest.TestCase):
    def test_default_allows_existing_admin_and_key_routes(self):
        guard = ERIFirewall(environ={})
        self.assertIsNone(guard.check("POST", "/api/admin/key/create", "1.2.3.4", {}))
        self.assertIsNone(guard.check("POST", "/api/key/verify", "1.2.3.4", {}))
        self.assertIsNone(guard.check("GET", "/api/health", "1.2.3.4", {}))

    def test_denied_cidr_returns_403(self):
        guard = ERIFirewall(environ={"ERI_FIREWALL_DENY_CIDRS": "203.0.113.0/24"})
        self.assertEqual(guard.check("POST", "/api/key/verify", "203.0.113.9", {})[0], 403)
        self.assertIsNone(guard.check("POST", "/api/key/verify", "198.51.100.4", {}))

    def test_allowlist_is_optional_and_explicit(self):
        guard = ERIFirewall(environ={"ERI_FIREWALL_ALLOW_CIDRS": "192.0.2.0/24"})
        self.assertIsNone(guard.check("GET", "/admin", "192.0.2.1", {}))
        self.assertEqual(guard.check("GET", "/admin", "203.0.113.9", {})[0], 403)

    def test_rate_limited_and_recovers(self):
        current = [1000.0]
        guard = ERIFirewall(environ={"ERI_FIREWALL_VERIFY_PER_MIN": "10"}, clock=lambda: current[0])
        for _ in range(10):
            self.assertIsNone(guard.check("POST", "/api/key/verify", "1.2.3.4", {}))
        self.assertEqual(guard.check("POST", "/api/key/verify", "1.2.3.4", {})[0], 429)
        self.assertIsNone(guard.check("POST", "/api/key/verify", "1.2.3.5", {}))
        current[0] += 61
        self.assertIsNone(guard.check("POST", "/api/key/verify", "1.2.3.4", {}))

    def test_does_not_trust_spoofed_forwarded_header_by_default(self):
        guard = ERIFirewall(environ={"ERI_FIREWALL_DENY_CIDRS": "203.0.113.0/24"})
        self.assertEqual(guard.check("GET", "/admin", "203.0.113.4",
                                     {"X-Forwarded-For": "192.0.2.5"})[0], 403)

    def test_trusted_render_proxy_uses_rightmost_forwarded_ip(self):
        guard = ERIFirewall(environ={
            "ERI_FIREWALL_TRUST_RENDER_PROXY": "1",
            "ERI_FIREWALL_DENY_CIDRS": "203.0.113.0/24"
        })
        self.assertEqual(guard.check("GET", "/admin", "127.0.0.1",
                                     {"X-Forwarded-For": "192.0.2.3, 203.0.113.4"})[0], 403)

    def test_large_path_rejected(self):
        guard = ERIFirewall(environ={})
        self.assertEqual(guard.check("GET", "/x" * 2000, "1.2.3.4", {})[0], 414)


if __name__ == "__main__":
    unittest.main()
