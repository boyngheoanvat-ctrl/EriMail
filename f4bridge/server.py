#!/usr/bin/env python3
"""ERI Store F4TEAM catalog relay.

Read-only, fixed-host bridge for public /api/decrypt GET endpoints only.
Not an open proxy, not a decryption service, not a CSRF bypass.
A dedicated deployment prevents restarting ERI Store's stateful shop API.
"""
import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

UPSTREAM = "https://decrypt.160-191-242-70.sslip.io"
ALLOWED_ORIGINS = {
    "https://eri-store-web.onrender.com",
    "https://eri-ipa-preview-web.onrender.com",
}
ALLOWED = {
    "/api/decrypt/search", "/api/decrypt/library",
    "/api/decrypt/versions", "/api/decrypt/app-info",
}
COUNTRIES = {"auto", "vn", "us", "jp", "kr", "tw", "cn", "hk", "sg", "th", "gb"}
CATEGORIES = {"all", "games", "apps", "education", "social", "photo_video",
              "utilities", "productivity"}
SORTS = {"newest", "size_desc", "size_asc", "title"}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
RATE_WINDOW = 60
RATE_MAX = 80
_requests = defaultdict(deque)
_guard = threading.Lock()


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlparse(newurl)
        if url.scheme != "https" or url.hostname != urlparse(UPSTREAM).hostname:
            raise HTTPError(req.full_url, 502, "Upstream redirected away from allowed host",
                            headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = build_opener(SameHostRedirect())


def clean_query(path, raw):
    params = parse_qs(raw, keep_blank_values=False, max_num_fields=16)
    def one(key, limit=500):
        value = params.get(key, [""])[0]
        return str(value).strip()[:limit]

    if path == "/api/decrypt/search":
        q = one("q", 500)
        if not q:
            raise ValueError("Thiếu từ khóa tìm kiếm.")
        country = one("country", 8).lower() or "auto"
        if country not in COUNTRIES:
            country = "auto"
        return urlencode({"q": q, "country": country, "mode": "ios"})

    if path == "/api/decrypt/library":
        q = one("q", 160)
        cat = one("category", 30).lower()
        sort = one("sort", 20).lower()
        try:
            limit = min(48, max(1, int(one("limit", 5) or 24)))
            offset = min(20000, max(0, int(one("offset", 8) or 0)))
        except ValueError:
            raise ValueError("Phân trang không hợp lệ.")
        return urlencode({"q": q, "category": cat if cat in CATEGORIES else "all",
                          "sort": sort if sort in SORTS else "newest",
                          "limit": limit, "offset": offset})

    if path == "/api/decrypt/versions":
        target = one("target", 500)
        if not target:
            raise ValueError("Thiếu ứng dụng cần tìm phiên bản.")
        country = one("country", 8).lower() or "auto"
        return urlencode({"target": target, "country": country if country in COUNTRIES else "auto"})

    if path == "/api/decrypt/app-info":
        bundle = one("bundle_id", 240)
        title = one("title", 200)
        if not bundle and not title:
            raise ValueError("Thiếu thông tin ứng dụng.")
        country = one("country", 8).lower() or "vn"
        return urlencode({"bundle_id": bundle, "country": country if country in COUNTRIES else "vn",
                          "title": title})
    raise ValueError("Endpoint không được phép.")


def rate_allowed(ip):
    now = time.monotonic()
    with _guard:
        history = _requests[ip]
        while history and now - history[0] > RATE_WINDOW:
            history.popleft()
        if len(history) >= RATE_MAX:
            return False
        history.append(now)
        if len(_requests) > 2000:
            for k in list(_requests)[:400]:
                if not _requests[k] or now - _requests[k][-1] > RATE_WINDOW:
                    _requests.pop(k, None)
        return True


def fetch_source(path, query):
    url = UPSTREAM + path + ("?" + query if query else "")
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "ERIStore-CatalogBridge/1.0 (+https://eri-store-web.onrender.com)",
    }, method="GET")
    try:
        with _opener.open(request, timeout=14) as response:
            if response.status != 200:
                raise RuntimeError("Nguồn trả HTTP " + str(response.status))
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" not in content_type:
                raise RuntimeError("Nguồn không trả JSON (có thể yêu cầu xác minh hoặc chặn truy cập).")
            data = response.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError("Dữ liệu nguồn vượt giới hạn phản hồi.")
            json.loads(data.decode("utf-8"))
            return data
    except HTTPError as exc:
        raise RuntimeError("Nguồn F4TEAM trả HTTP " + str(exc.code)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("Không kết nối được máy chủ nguồn F4TEAM.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Máy chủ nguồn trả JSON không hợp lệ.") from exc


class Handler(BaseHTTPRequestHandler):
    server_version = "ERICatalogBridge/1.0"

    def log_message(self, format, *args):
        # Do not log download IDs, query strings, cookies, or user credentials.
        print("[%s] %s" % (self.log_date_time_string(), format % args))

    def send_json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_reply(status, data)

    def send_reply(self, status, data):
        origin = self.headers.get("Origin", "")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Vary", "Origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Accept, Content-Type")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        if origin not in ALLOWED_ORIGINS:
            self.send_json(403, {"ok": False, "error": "Origin không được phép."})
        else:
            self.send_reply(204, b"")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self.send_json(200, {"ok": True, "service": "eri-f4-public-catalog-bridge"})
        if parsed.path not in ALLOWED:
            return self.send_json(404, {"ok": False, "error": "Đường dẫn không được hỗ trợ."})
        if not rate_allowed(self.client_address[0]):
            return self.send_json(429, {"ok": False, "error": "Thao tác quá nhanh. Vui lòng thử lại."})
        try:
            query = clean_query(parsed.path, parsed.query)
        except ValueError as exc:
            return self.send_json(400, {"ok": False, "error": str(exc)})
        try:
            data = fetch_source(parsed.path, query)
        except RuntimeError as exc:
            return self.send_json(502, {"ok": False, "error": str(exc)})
        return self.send_reply(200, data)

    def do_POST(self):
        self.send_json(405, {"ok": False, "error": "Chỉ hỗ trợ API GET công khai."})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print("Starting ERI F4TEAM catalog bridge on port", port, flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
