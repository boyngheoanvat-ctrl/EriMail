#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ERI Login public API proxy — Render Free, Python stdlib."""
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FN = os.environ.get("ERI_SUPABASE_FUNCTION_URL", "").rstrip("/")
INTERNAL = os.environ.get("ERI_INTERNAL_KEY", "")
ADMIN = os.environ.get("ERI_ADMIN_KEY", "")

ADMIN_HTML = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ERI Login Admin</title><style>
:root{color-scheme:dark;font-family:system-ui,-apple-system,sans-serif}
body{max-width:620px;margin:30px auto;padding:0 16px;background:#0e1422;color:#f5f7ff}
main{padding:24px;border:1px solid #2b3a51;border-radius:18px;background:#162037}
label{display:block;margin:12px 0 5px;color:#b7c6e2;font-size:14px}
input{width:100%;box-sizing:border-box;padding:11px;border-radius:8px;border:1px solid #475571;background:#111b2d;color:white;font-size:16px}
button{padding:11px 14px;margin:7px 6px 0 0;border:0;border-radius:8px;background:#4e86f5;color:white;font-weight:600}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#0c1322;border-radius:10px;padding:14px;min-height:32px}
</style></head><body><main>
<h2>ERI Login — Admin</h2><p>Quản lý tài khoản dùng cho IPA đã thêm ERI Login.</p>
<label>Khóa quản trị (ERI_ADMIN_KEY)</label><input id="key" type="password" autocomplete="off">
<label>App ID</label><input id="app" placeholder="vn.eri.store" value="vn.eri.store">
<label>Tài khoản</label><input id="user" placeholder="user01">
<label>Mật khẩu tạo mới (ít nhất 8 ký tự)</label><input id="pass" type="password">
<label>Số ngày sử dụng / gia hạn</label><input id="days" type="number" min="1" max="3650" value="30">
<div style="margin-top:16px">
<button onclick="go('create')">Tạo tài khoản</button>
<button onclick="go('extend')">Gia hạn</button>
<button onclick="go('disable')">Khóa</button>
<button onclick="go('enable')">Mở khóa</button>
<button onclick="go('delete')">Xóa</button>
<button onclick="go('list')">Xem danh sách</button></div>
<h3>Kết quả</h3><pre id="out">Chưa có yêu cầu.</pre>
</main><script>
async function go(action){
 const val=id=>document.getElementById(id).value.trim();
 const out=document.getElementById('out');
 const app_id=val('app'), username=val('user');
 const payload={app_id,username,password:document.getElementById('pass').value,days:Number(val('days'))};
 const url=action==='list'?'/api/admin/list?app_id='+encodeURIComponent(app_id):'/api/admin/'+action;
 out.textContent='Đang gửi yêu cầu…';
 try{
   const res=await fetch(url,{method:action==='list'?'GET':'POST',
    headers:{'Content-Type':'application/json','X-ERI-ADMIN-KEY':val('key')},
    ...(action==='list'?{}:{body:JSON.stringify(payload)})});
   const data=await res.json();out.textContent=(res.ok?'Thành công\n':'Lỗi HTTP '+res.status+'\n')+JSON.stringify(data,null,2);
 }catch(e){out.textContent='Lỗi kết nối: '+e.message}
}
</script></body></html>"""

POSTS = {
    "/api/login", "/api/verify", "/api/admin/create", "/api/admin/extend",
    "/api/admin/disable", "/api/admin/enable", "/api/admin/delete",
}

class Handler(BaseHTTPRequestHandler):
    def send(self, status, payload, kind="application/json; charset=utf-8"):
        blob = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-ERI-ADMIN-KEY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send(200, b"")

    def is_admin(self):
        key = self.headers.get("X-ERI-ADMIN-KEY", "")
        return len(ADMIN) >= 32 and len(key) > 0 and hmac.compare_digest(key, ADMIN)

    def forward(self, path, method, body=None):
        if not FN.startswith("https://") or len(INTERNAL) < 32:
            return self.send(503, {"error":"Máy chủ chưa được cấu hình đầy đủ"})
        destination = FN + path.removeprefix("/api")
        req = urllib.request.Request(destination,
            data=body,
            headers={"Content-Type":"application/json", "X-ERI-INTERNAL-SECRET": INTERNAL},
            method=method)
        try:
            with urllib.request.urlopen(req, timeout=70) as response:
                raw = response.read(65536)
                self.send(response.status, raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read(65536)
            self.send(exc.code, raw if raw else b'{"error":"Request failed"}')
        except (urllib.error.URLError, TimeoutError, OSError):
            self.send(503, {"error":"Không thể kết nối cơ sở dữ liệu ERI"})

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            return self.send(200, {"service":"ERI Login","admin":"/admin","health":"/api/health"})
        if path == "/admin":
            return self.send(200, ADMIN_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/health":
            return self.forward("/api/health", "GET")
        if path == "/api/admin/list":
            if not self.is_admin():
                return self.send(403, {"error":"Không có quyền Admin"})
            parsed = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            app_id = query.get("app_id", [""])[0][:128]
            return self.forward("/api/admin/list?app_id="+urllib.parse.quote(app_id, safe=""), "GET")
        self.send(404, {"error":"Không tìm thấy API"})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in POSTS:
            return self.send(404, {"error":"Không tìm thấy API"})
        if path.startswith("/api/admin/") and not self.is_admin():
            return self.send(403, {"error":"Không có quyền Admin"})
        try:
            size = int(self.headers.get("Content-Length","0"))
        except ValueError:
            size = -1
        if not 1 <= size <= 16384:
            return self.send(413, {"error":"JSON không hợp lệ hoặc quá lớn"})
        body = self.rfile.read(size)
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, UnicodeDecodeError):
            return self.send(400, {"error":"JSON không hợp lệ"})
        self.forward(path, "POST", body)

def backend_health_probe():
    if not FN or len(INTERNAL) < 32:
        print("ERI backend readiness: missing config", flush=True)
        return
    try:
        req = urllib.request.Request(FN + "/health",
            headers={"X-ERI-INTERNAL-SECRET": INTERNAL}, method="GET")
        with urllib.request.urlopen(req, timeout=20) as response:
            data = json.loads(response.read(2048))
            print("ERI backend readiness:", response.status, data.get("status"), data.get("service"), flush=True)
    except Exception as exc:
        print("ERI backend readiness failed:", type(exc).__name__, str(exc), flush=True)

if __name__ == "__main__":
    backend_health_probe()
    port = int(os.environ.get("PORT", "10000"))
    print("ERI Login listening on 0.0.0.0:"+str(port), flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
