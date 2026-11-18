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
assert 'function uploadWholeIPA(form)' in chunks
assert '<div id="ipa-sign-progress" class="ipa-sign-progress"' in html
# Legacy standalone IPA inspector is removed; signing is the ERI-only tool.
assert 'id="eri-nonjb-panel"' not in html
assert 'id="eri-nonjb-form"' not in html
assert "Kiểm tra và tạo link tải IPA" not in html
assert 'id="ipa-sign-panel"' in html
assert 'name="ipa"' in html and 'name="p12"' in html
assert 'name="mobileprovision"' in html and 'name="password"' in html
assert 'sign-jobs.js?v=20261116uploadfix2' in html
assert 'const MAX_IPA = 512 * 1024 * 1024' in chunks
assert 'function uploadWholeIPA(form)' in chunks
assert 'xhr.open("POST", API + "/api/v1/tools/ipa/sign", true);' in chunks
assert 'xhr.upload.onprogress = event =>' in chunks
assert 'xhr.send(payload);' in chunks
assert 'payload.append("ipa", ipa, ipa.name);' in chunks
assert 'payload.append("p12", p12, p12.name);' in chunks
assert 'payload.append("mobileprovision", profile, profile.name);' in chunks
assert 'payload.append("password", password);' in chunks
assert 'xhr.timeout = 0;' in chunks
assert 'renderLinks(body)' in chunks
assert 'sign-jobs/' not in chunks
assert 'putChunk(' not in chunks
assert 'sha256(' not in chunks
assert 'Đang gửi phần IPA ' not in chunks
assert 'chuyển sang Tài khoản' in tools
assert 'https://eri-api-dag8.onrender.com' in chunks
assert 'decrypt.160-191-242-70.sslip.io' not in chunks
assert 'https://eri-f4-bridge.onrender.com' not in chunks
assert 'value.toFixed(1) + "%"' in chunks
assert 'result.download_path' in chunks
assert 'xhr.onloadend' in chunks
# Authenticating BEFORE transmitting bytes prevents 401 after partial upload.
assert 'await fetch(API + "/api/v1/auth/me"' in chunks
assert 'window.dispatchEvent(new Event("eri-store:auth-expired"))' in chunks
assert 'window.addEventListener("eri-store:auth-expired"' in (web / "app.js").read_text()
assert 'progress(maxTransmitted, ipa.size, "error")' in chunks
assert 'if (pwd && signedOk) pwd.value = ""' in chunks
assert 'xhr.open("POST", API + "/api/v1/tools/ipa/sign", true)' in chunks
assert 'putChunk(' not in chunks

assert 'download.textContent = "Tải IPA"' in tools
assert "window.ERIStoreIPA?.download" in tools
assert "https://decrypt.day/app/id" not in tools
assert 'Hãy tạo yêu cầu IPA' not in tools
assert 'id="eri-decrypt-direct-request"' not in html
assert "createRequest" not in tools
assert '/api/v1/tools/ipa/decrypt/vault?q=' in tools
assert '/api/v1/mods' in tools, "Legacy endpoint must remain as fallback"
assert "eri-store-v45" in service
assert "?v=20261115p3" in html
assert "ipa-tools.js?v=20261115direct1" in html
assert "decrypt-store.js?v=20261116brand1" in html
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
assert '["Cửa hàng","Eri Nguyễn"]' in decrypt
assert '["Nguồn","F4TEAM"]' not in decrypt
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
assert 'id="hero-ipa"' in html
# One Chứng chỉ top-level tab with FREE/BÁN children, like Mod Game.
assert html.count('data-kind="certificate"') == 1
assert 'data-kind="certfree"' not in html
assert 'id="certificate-subtabs"' in html
assert 'data-cert-mode="free"' in html
assert 'data-cert-mode="paid"' in html
assert 'id="certificate-free-panel"' in html
assert 'id="certificate-udid"' in html
assert 'https://website.khoindvn.io.vn/apps.js' not in html
assert 'eri-home-extras.css?v=20261115certificatetab1' in html
assert 'app.js?v=20261116mobtabs1' in html
app = (web / "app.js").read_text()
assert 'openCategory("certificate",tab.dataset.certMode)' in app
assert 'freeCertificates=category==="certificate"&&certMode==="free"' in app
assert 'category==="certificate"?"Chứng chỉ BÁN"' in app
assert 'id="ipa-sign-form"' in html
assert 'id="ipa-sign-iphone"' not in html
assert 'id="ipa-sign-server"' not in html
assert 'data-sign-mode=' not in html
assert 'id="open-sign-local"' not in html
assert '[data-sign-mode]' not in app
free_js = (web / "free-certificates.js").read_text()
assert 'eri-free-card' in free_js and 'product--certificate' in free_js
assert 'install.setAttribute("href",item.url)' in free_js
assert 'window.ERIStoreIPA=Object.freeze' in decrypt
assert 'const found=await findAvailableIPA(target);' in decrypt
assert 'Đang kết nối kho IPA ERI Nguyễn...' in decrypt
assert 'Đang kết nối kho IPA F4TEAM...' not in decrypt
assert 'stage === "signing"' in chunks
assert 'ipa-sign-progress-stage' not in html
assert 'ipa-sign-progress-stage' not in chunks
assert 'Đang gửi IPA một lần:' not in chunks
assert 'Trình duyệt đã truyền khoảng' not in chunks
assert 'node.replaceChildren();' in chunks
assert 'status("");' in chunks
assert 'target.append(row);' in chunks
assert 'Ký IPA thành công' in chunks
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
