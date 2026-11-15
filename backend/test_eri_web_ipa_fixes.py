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
    "eri-decrypt-query", "eri-decrypt-search-form",
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

assert 'download.textContent = "Tải IPA"' in tools
assert "window.ERIStoreIPA?.download" in tools
assert "https://decrypt.day/app/id" not in tools
assert 'Hãy tạo yêu cầu IPA' not in tools
assert 'id="eri-decrypt-direct-request"' not in html
assert "createRequest" not in tools
assert '/api/v1/tools/ipa/decrypt/vault?q=' in tools
assert '/api/v1/mods' in tools, "Legacy endpoint must remain as fallback"
assert "eri-store-v33" in service
assert "?v=20261115p3" in html
assert "ipa-tools.js?v=20261115direct1" in html
assert "decrypt-store.js?v=20261115f4bridge1" in html
assert "eri-decrypt-layout.css?v=20261115dashboard2" in html

# ERI hosts a branded view, but provider API data must stay sourced from
# the F4TEAM host that served the supplied page, not empty ERI metadata.
assert 'const PROVIDER = "https://eri-f4-bridge.onrender.com"' in decrypt
assert "connect-src" in html and "https://eri-f4-bridge.onrender.com" in html
assert "Máy chủ nguồn chưa cho phép ERI đọc API" not in decrypt
assert '/api/decrypt/search?' in decrypt
assert '/api/decrypt/library?' in decrypt
assert '/api/decrypt/versions?' in decrypt
assert '/api/decrypt/app-info?' in decrypt
assert "window.ERIF4Decrypt" in decrypt
assert "window.ERIStoreIPA" in decrypt
assert "sourceDownload" in decrypt
assert 'credentials:"omit"' in decrypt
assert "X-CSRF-TOKEN" not in decrypt
assert "4c00c79dc63e3c5600328e1616518ad15c53fd67bdf39da2a0a1b7d23327bcd6" not in html

# The new layout closely follows the supplied F4TEAM UI and is uniquely
# branded ERI, with genuine catalog numbers instead of hardcoded 621 IPA.
for identifier in (
    "eri-decrypt-region", "eri-decrypt-query", "eri-decrypt-search-button",
    "eri-decrypt-result-section", "eri-decrypt-vault-section",
    "eri-decrypt-vault-filter", "eri-decrypt-vault-sort",
    "eri-v-category-bar", "eri-v-detail-modal",
    "eri-v-versions-modal", "eri-v-job-panel",
    "eri-v-direct-versions", "eri-v-direct-check",
):
    assert f'id="{identifier}"' in html, identifier
assert "621" not in html
assert "F4TEAM" not in html
assert "F4Decrypt" not in html
assert "ERI DECRYPT" in html
assert "BY ERI NGUYỄN" in html

# Existing signing, upload, and App Store tool behavior remain intact.
assert 'id="eri-decrypt-direct-request"' not in html
assert 'id="eri-decrypt-request-section"' not in html
assert 'id="eri-decrypt-history-section"' not in html
assert "downloadFeedback.hidden = true" in tools
assert "window.ERIStoreIPA?.download" in tools
assert "/api/v1/tools/ipa/decrypt/external?" not in tools
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

print("ERI STORE: ERI-branded F4TEAM public catalog UI and safe source links OK")
