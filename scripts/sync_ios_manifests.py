#!/usr/bin/env python3
"""Mirror and validate the 34 existing public iOS OTA manifests, not their IPA files."""
import concurrent.futures
import json
import plistlib
import re
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "ipa-catalog/install-tools.json"
WEB_CATALOG = ROOT / "web/eri-store/install-tools.json"
SITE = "https://eri-store-web.onrender.com"
UPSTREAM = "https://website.khoindvn.io.vn"
PREFIX = "itms-services://?action=download-manifest&url="

def manifest_id(entry):
    link = entry["url"]
    if not isinstance(link, str) or not link.startswith(PREFIX):
        raise ValueError("Unexpected installer URL")
    u = urlsplit(link[len(PREFIX):])
    if (u.scheme != "https" or u.hostname not in
            ("website.khoindvn.io.vn", "eri-store-web.onrender.com")
            or u.query or u.fragment or u.username or u.password):
        raise ValueError("Invalid installer origin")
    found = re.fullmatch(r"/plists/([a-z]{12})\.plist", u.path)
    if not found:
        raise ValueError("Unexpected manifest path")
    return found.group(1)

def validate(data, slug):
    if len(data) > 512*1024:
        raise ValueError(f"Manifest {slug} too large")
    doc = plistlib.loads(data)
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError(f"No items in manifest {slug}")
    for item in items:
        metadata = item.get("metadata") or {}
        if not metadata.get("bundle-identifier") or not metadata.get("bundle-version"):
            raise ValueError(f"No bundle ID/version: {slug}")
        packages = [a for a in (item.get("assets") or [])
                    if a.get("kind") == "software-package"]
        if not packages:
            raise ValueError(f"No software package: {slug}")
        for asset in packages:
            u = urlsplit(asset.get("url") or "")
            if u.scheme != "https" or not u.hostname or u.username or u.password:
                raise ValueError(f"Invalid package URL: {slug}")
    return plistlib.dumps(doc, fmt=plistlib.FMT_XML, sort_keys=False)

def fetch_one(slug):
    url = f"{UPSTREAM}/plists/{slug}.plist"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/xml,text/xml,application/x-plist,*/*",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
            })
            with urllib.request.urlopen(req, timeout=25) as response:
                final = urlsplit(response.geturl())
                if final.scheme != "https" or final.hostname != "website.khoindvn.io.vn":
                    raise ValueError("Unexpected redirect")
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                data = response.read(512*1024 + 1)
            return slug, validate(data, slug)
        except Exception as error:
            if attempt == 2:
                raise RuntimeError(f"Unable to validate {slug}: {error}") from error
            time.sleep(1.5*(attempt+1))

def main():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if not all(len(catalog.get(k, [])) == 17 for k in ("ksign", "esign")):
        raise ValueError("Expected 17 KSign and 17 ESign variants")
    ids = [manifest_id(item) for k in ("ksign", "esign") for item in catalog[k]]
    if len(set(ids)) != 34:
        raise ValueError("Expected 34 unique manifest IDs")
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetch_one, slug) for slug in ids]
        for future in concurrent.futures.as_completed(futures):
            slug, xml = future.result()
            results[slug] = xml
            print("VALID", slug, len(xml), flush=True)
    # Fail closed: don't update the site if even one source is unavailable.
    dest = ROOT / "web/eri-store/plists"
    dest.mkdir(parents=True, exist_ok=True)
    for slug in ids:
        (dest / f"{slug}.plist").write_bytes(results[slug])
    for k in ("ksign", "esign"):
        for item in catalog[k]:
            slug = manifest_id(item)
            item["url"] = f"{PREFIX}{SITE}/plists/{slug}.plist"
    content = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
    for path in (CATALOG, WEB_CATALOG):
        path.write_text(content, encoding="utf-8")
    js_path = ROOT / "web/eri-store/free-certificates.js"
    js = js_path.read_text("utf-8")
    if 'hostname==="website.khoindvn.io.vn"' in js:
        js = js.replace('hostname==="website.khoindvn.io.vn"',
                        'hostname==="eri-store-web.onrender.com"')
    elif 'hostname==="eri-store-web.onrender.com"' not in js:
        raise ValueError("Installer host validation missing")
    js_path.write_text(js, encoding="utf-8")
    html_path = ROOT / "web/eri-store/index.html"
    html = html_path.read_text("utf-8")
    html = html.replace("free-certificates.js?v=20261115cert1",
                        "free-certificates.js?v=20261115cert2")
    html = html.replace("Liên kết cài đặt được cung cấp từ website.khoindvn.io.vn.",
                        "Manifest do ERI cung cấp; ứng dụng và chữ ký số vẫn do nguồn bên ngoài cung cấp.")
    html_path.write_text(html, encoding="utf-8")
    sw_path = ROOT / "web/eri-store/service-worker.js"
    sw = sw_path.read_text("utf-8").replace(
        'const VERSION="eri-store-v36";', 'const VERSION="eri-store-v37";')
    sw_path.write_text(sw, encoding="utf-8")
    print("PASS: 34 local manifests. Original IPA package URLs were preserved.")

if __name__ == "__main__":
    main()
