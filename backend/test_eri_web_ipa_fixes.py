"""Static frontend contract checks: progress status + missing IPA request links.

These tests intentionally require no Apple account, signing secret or live IPA.
"""
import re
from pathlib import Path

web = Path(__file__).resolve().parents[1] / "web" / "eri-store"
html = (web / "index.html").read_text()
tools = (web / "ipa-tools.js").read_text()
chunks = (web / "sign-jobs.js").read_text()
decrypt = (web / "decrypt-store.js").read_text()
service = (web / "service-worker.js").read_text()

for key in (
    "ipa-sign-progress", "ipa-sign-progress-bar", "ipa-sign-progress-percent",
    "eri-decrypt-direct-request", "eri-decrypt-query", "eri-decrypt-search-form",
):
    assert f'id="{key}"' in html, key

assert 'updateLegacyProgress(ev.loaded, total, "uploading")' in tools
assert 'xhr.upload.onprogress' in tools
assert 'value.toFixed(1) + "%"' in tools
assert 'percent.textContent = value.toFixed(1) + "%"' in chunks
assert 'confirmedBytes()' in chunks
assert '<div id="ipa-sign-progress" class="ipa-sign-progress"' in html
assert 'status("Đã nhận " + pct + "%' in chunks
assert 'percentage + "% · "' in tools
assert 'received.size * job.chunk_bytes' not in chunks

assert 'Xử lý IPA' in tools
assert 'if (createRequest) $("eri-decrypt-direct-request")?.click();' in tools
assert '/api/v1/tools/ipa/decrypt/vault?q=' in tools
assert '/api/v1/mods' in tools, "Legacy endpoint must remain as fallback"
assert "eri-store-v28" in service
assert "?v=20261115p3" in html
assert "ipa-tools.js?v=20261115native1" in html
assert "decrypt-store.js?v=20261115native3" in html
assert 'request("/api/v1/tools/ipa/decrypt/start"' not in decrypt
assert 'request("/api/v1/tools/ipa/decrypt/job/"' not in decrypt
assert '/api/v1/tools/ipa/decrypt/requests"' in decrypt
assert '/api/v1/tools/ipa/decrypt/requests/"' in decrypt
assert 'const job = await request("/api/v1/tools/ipa/decrypt/requests"' in decrypt
assert "nativeSourceStatus" in decrypt
assert "ERI đã có nguồn IPA được đăng ký" in decrypt
assert '"/api/v1/tools/ipa/decrypt/vault?q="' in tools
assert "/api/v1/tools/ipa/decrypt/external?" not in tools
assert "/api/v1/tools/ipa/decrypt/f4/" not in decrypt
assert "F4TEAM" not in decrypt
assert "F4TEAM" not in tools

# No bogus IPA URL from App Store link is ever generated.
assert not re.search(r'ipa.*download.*apps\.apple\.com', tools, re.I)
assert 'Link App Store không chứa IPA đã giải mã FairPlay' not in tools

# Some deployments intentionally do not include the optional admin uploader.
# Only enforce the admin UI contract when both source files are checked in.
admin_html = web / "ipa-admin.html"
admin_js = web / "ipa-admin.js"
if admin_html.is_file() and admin_js.is_file():
    admin_page = (web / "ipa-admin.html").read_text()
    admin_script = (web / "ipa-admin.js").read_text()
    assert 'id="admin-key"' in admin_page
    assert 'id="ipa-file"' in admin_page
    assert 'id="upload-progress"' in admin_page
    assert 'ipa-admin.js?v=20261115p3' in admin_page
    assert 'verify_free_ipa: true' in admin_script
    assert '/fast-parts/' in admin_script
    assert 'crypto.subtle.digest("SHA-256"' in admin_script
    assert 'localStorage.setItem(KEY, JSON.stringify(data))' in admin_script
    assert 'ERI_ADMIN_KEY=' not in admin_script
    import subprocess
    subprocess.run(["node", "--check", str(web / "ipa-admin.js")], check=True)
    
else:
    assert not (admin_html.is_file() ^ admin_js.is_file()), "Admin upload UI must include both HTML and JS"
    print("ERI Admin uploader is absent from this checkout; skipping its independent checks.")

print("ERI STORE: native IPA queue, progress, cache-bust, backend vault lookup and request UI OK")
