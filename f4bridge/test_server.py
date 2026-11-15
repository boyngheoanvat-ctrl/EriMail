"""Offline security/behavior checks for the F4 read-only CORS bridge."""
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen
from unittest.mock import patch

import server


class FakeSource:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, value):
        self.io = io.BytesIO(json.dumps(value).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.io.close()

    def read(self, n):
        return self.io.read(n)


class TestBridge(unittest.TestCase):
    def test_allowlist(self):
        self.assertEqual(len(server.ALLOWED), 4)
        self.assertNotIn("/api/decrypt/start", server.ALLOWED)
        self.assertNotIn("/api/decrypt/sync-app", server.ALLOWED)
        self.assertTrue(server.UPSTREAM.startswith("https://"))
        self.assertNotIn("://user:", server.UPSTREAM)

    def test_query_validation(self):
        vals = parse_qs(server.clean_query("/api/decrypt/search",
                                          "q=Li%C3%AAn%20Qu%C3%A2n&country=vn&mode=android&x=1"))
        self.assertEqual(vals["q"], ["Liên Quân"])
        self.assertEqual(vals["mode"], ["ios"])
        self.assertEqual(vals["country"], ["vn"])
        self.assertNotIn("x", vals)
        with self.assertRaises(ValueError):
            server.clean_query("/api/decrypt/search", "")
        vals = parse_qs(server.clean_query("/api/decrypt/library",
                         "limit=9999&offset=-2&sort=wat&category=unknown"))
        self.assertEqual(vals["limit"], ["48"])
        self.assertEqual(vals["offset"], ["0"])
        self.assertEqual(vals["sort"], ["newest"])
        self.assertEqual(vals["category"], ["all"])
        with self.assertRaises(ValueError):
            server.clean_query("/api/decrypt/job", "")
        with self.assertRaises(ValueError):
            server.clean_query("/api/decrypt/versions", "")

    def test_upstream_is_fixed_and_json_only(self):
        value = {"ok": True, "total": 2, "items": [{"title": "Test app"}]}
        with patch.object(server._opener, "open", return_value=FakeSource(value)) as mocked:
            response = server.fetch_source("/api/decrypt/library", "limit=1&offset=0")
        self.assertEqual(json.loads(response), value)
        args, kwargs = mocked.call_args
        self.assertEqual(args[0].full_url.split("?")[0],
                         server.UPSTREAM + "/api/decrypt/library")
        self.assertEqual(args[0].get_method(), "GET")
        self.assertNotIn("Cookie", args[0].headers)
        self.assertNotIn("Authorization", args[0].headers)

    def test_http_cors_and_reject_post(self):
        source = {"ok": True, "items": [], "total": 0}
        handler = server.Handler
        with ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:%d" % httpd.server_port
            allowed = "https://eri-store-web.onrender.com"
            try:
                with urlopen(Request(base + "/health", headers={"Origin": allowed}),timeout=3) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers["Access-Control-Allow-Origin"], allowed)
                with urlopen(Request(base + "/api/decrypt/search?q=test",
                                     method="OPTIONS", headers={"Origin": allowed}),timeout=3) as resp:
                    self.assertEqual(resp.status, 204)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/decrypt/start", data=b"{}",
                                    headers={"Origin": allowed}),timeout=3)
                self.assertEqual(error.exception.code, 405)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(base + "/api/decrypt/job/123"),timeout=3)
                self.assertEqual(error.exception.code, 404)
                with patch.object(server._opener, "open", return_value=FakeSource(source)):
                    with urlopen(Request(base + "/api/decrypt/search?q=hello",
                                         headers={"Origin": allowed}),timeout=3) as resp:
                        self.assertEqual(json.loads(resp.read()), source)
                        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], allowed)
            finally:
                httpd.shutdown()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
