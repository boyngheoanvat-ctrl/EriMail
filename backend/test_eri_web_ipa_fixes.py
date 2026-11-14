"""Static frontend contract checks: progress status + missing IPA request links.

These tests intentionally require no Apple account, signing secret or live IPA.
"""
import re
from pathlib import Path

web = Path(__file__).resolve().parents[1] / "web" / "eri-store"
html = (web / "index.html").read_text()
tools = (web / "ipa-tools.js").read_text()
chunks = (web / "sign-jobs.js").read_text()
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
assert "eri-store-v24" in service
assert "?v=20261115p3" in html
assert "eri-decrypt-open-f4" not in html
assert "eri-dec-f4-import" not in html

# No bogus IPA URL from App Store link is ever generated.
assert not re.search(r'ipa.*download.*apps\.apple\.com', tools, re.I)
assert 'Link App Store không chứa IPA đã giải mã FairPlay' not in tools

print("ERI STORE: precise progress, cache-bust, backend vault lookup and IPA request UI OK")
