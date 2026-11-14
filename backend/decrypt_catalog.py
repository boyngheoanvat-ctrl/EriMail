"""ERI STORE App Store discovery + licensed IPA library + durable request tracking.

Apple Search/Lookup APIs return metadata, never downloadable decrypted IPAs.
The public library exposes ONLY explicitly authorized, free IPA files already
present in this installation. No fake cryptid=0 claims or fake progress.
"""
import json
from concurrent.futures import ThreadPoolExecutor
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from eri_ipa_pipeline import (
    setup as setup_pipeline, put_source, schedule as schedule_pipeline,
    schedule_matching as schedule_matching_pipeline
)

REGIONS = ("vn", "us", "jp", "kr", "tw", "cn", "hk", "sg", "th", "gb")
# Limit auto queries to three regions; Apple documents a ~20 calls/minute limit.
AUTO_REGIONS = ("vn", "us", "jp")
_search_cache = {}
_cache_lock = threading.RLock()
CACHE_TTL = 5 * 60


class IPARequest(BaseModel):
    target: str = Field(min_length=2, max_length=500)
    country: str = Field(default="auto", max_length=4)
    version: str = Field(default="", max_length=60)


class AuthorizedIPASource(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    bundle_id: str = Field(min_length=3, max_length=255)
    version: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=12, max_length=1800)
    sha256: str = Field(min_length=64, max_length=64)
    distribution_authorized: bool = False


def _region(value: str) -> str:
    region = str(value or "auto").lower().strip()
    if region not in (*REGIONS, "auto"):
        raise HTTPException(422, "Khu vực App Store không được hỗ trợ")
    return region


def _apple_json(endpoint: str, params: dict):
    if endpoint not in ("lookup", "search"):
        raise ValueError("Unexpected Apple API")
    address = "https://itunes.apple.com/" + endpoint + "?" + urlencode(params)
    with _cache_lock:
        item = _search_cache.get(address)
        if item and time.monotonic() - item[0] < CACHE_TTL:
            return item[1]
    request = urllib.request.Request(
        address, headers={"Accept": "application/json", "User-Agent": "ERIStore-Catalog/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=9) as response:
            raw = response.read(768 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "Apple App Store chưa phản hồi, vui lòng thử lại") from exc
    if len(raw) > 768 * 1024:
        raise HTTPException(502, "Phản hồi Apple vượt giới hạn")
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(502, "Dữ liệu Apple App Store không hợp lệ") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("results"), list):
        raise HTTPException(502, "Phản hồi Apple thiếu danh sách ứng dụng")
    with _cache_lock:
        if len(_search_cache) > 150:
            _search_cache.clear()
        _search_cache[address] = (time.monotonic(), parsed)
    return parsed


def _parse_target(target: str):
    text = target.strip()
    if text.startswith("http://") or text.startswith("https://"):
        url = urlsplit(text)
        if url.scheme != "https" or url.netloc != "apps.apple.com":
            raise HTTPException(422, "Chỉ chấp nhận link HTTPS apps.apple.com")
        match = re.search(r"/id([0-9]{6,15})(?:/|$)", url.path)
        if not match or not re.fullmatch(r"/(?:[a-zA-Z]{2}/)?app/.{1,220}", url.path):
            raise HTTPException(422, "Link phải chứa mã ứng dụng App Store")
        region_match = re.match(r"^/([a-zA-Z]{2})/app/", url.path)
        region = region_match.group(1).lower() if region_match else None
        if region and region not in REGIONS:
            region = None
        return "id", match.group(1), region
    if re.fullmatch(r"(?:id)?[0-9]{6,15}", text, flags=re.I):
        return "id", re.sub(r"^id", "", text, flags=re.I), None
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2,}", text):
        return "bundle", text, None
    if (not 2 <= len(text) <= 120 or
            not any(ch.isalnum() for ch in text) or
            any(ord(ch) < 32 for ch in text)):
        raise HTTPException(422, "Nhập tên ứng dụng, Bundle ID hoặc link App Store hợp lệ")
    return "term", text, None


def _normalize(item: dict, country: str):
    if item.get("kind") not in ("software", "iPadSoftware"):
        return None
    app_id = item.get("trackId")
    if not isinstance(app_id, int) or app_id <= 0:
        return None
    app_url = str(item.get("trackViewUrl", ""))
    if urlsplit(app_url).netloc != "apps.apple.com" or not app_url.startswith("https://"):
        app_url = "https://apps.apple.com/" + country + "/app/id" + str(app_id)
    icon = str(item.get("artworkUrl100", ""))
    if (urlsplit(icon).scheme != "https" or
            not (urlsplit(icon).hostname or "").endswith(".mzstatic.com")):
        icon = ""
    return {
        "app_id": app_id, "name": str(item.get("trackName", ""))[:160],
        "developer": str(item.get("artistName", ""))[:160],
        "bundle_id": str(item.get("bundleId", ""))[:255],
        "version": str(item.get("version", ""))[:60],
        "country": country.upper(), "app_store_url": app_url,
        "icon_url": icon,
        "genre": str(item.get("primaryGenreName", ""))[:80],
        "price": float(item.get("price") or 0) if str(item.get("price") or 0).replace(".", "", 1).isdigit() else 0,
        "size_bytes": item.get("fileSizeBytes") if isinstance(item.get("fileSizeBytes"), str) else None,
    }


def _search_apps(target: str, region: str, limit: int = 15):
    kind, value, url_region = _parse_target(target)
    region = _region(region)
    countries = [region] if region != "auto" else list(AUTO_REGIONS)
    if url_region and region == "auto":
        countries = [url_region]
    results = []
    seen = set()
    last_error = None
    def query_country(country):
        if kind == "id":
            endpoint, params = "lookup", {"id": value, "country": country, "entity": "software"}
        elif kind == "bundle":
            endpoint, params = "lookup", {"bundleId": value, "country": country, "entity": "software"}
        else:
            endpoint, params = "search", {"term": value, "country": country,
                                           "media": "software", "entity": "software",
                                           "limit": min(limit, 25)}
        return _apple_json(endpoint, params)

    # Apple Search API requests are independent by region. Resolve them
    # concurrently for the auto setting to avoid three serial mobile timeouts.
    if len(countries) > 1 and kind == "term":
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {country: pool.submit(query_country, country) for country in countries}
            replies = []
            for country in countries:
                try:
                    replies.append((country, futures[country].result()))
                except HTTPException as exc:
                    last_error = exc
    else:
        replies = []
        for country in countries:
            try:
                replies.append((country, query_country(country)))
                if kind in ("id", "bundle") and replies[-1][1].get("results"):
                    break
            except HTTPException as exc:
                last_error = exc
    for country, source in replies:
        for entry in source["results"]:
            if not isinstance(entry, dict):
                continue
            normalized = _normalize(entry, country)
            if not normalized or normalized["app_id"] in seen:
                continue
            if kind == "bundle" and normalized["bundle_id"].casefold() != value.casefold():
                continue
            seen.add(normalized["app_id"])
            results.append(normalized)
            if len(results) >= limit:
                break
        if len(results) >= limit:
            break
    if not results and last_error:
        raise last_error
    return results


def _all_free_ipa(connection, upload_dir, mod_file, q: str = "", limit: int = 500):
    pattern = "%" + q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with connection() as db:
        rows = db.execute(
            "SELECT i.id,i.name,i.version,i.bundle_id,i.description,i.price_vnd,i.platform,"
            "p.storage_name,p.size_bytes,p.original_name,p.created_at "
            "FROM ipa i JOIN private_product_uploads p ON "
            "p.product_id=i.id AND p.kind='ipa' "
            "WHERE i.authorized=1 AND i.price_vnd=0 AND i.platform='ipa' "
            "AND (LOWER(i.name) LIKE ? ESCAPE '\\' OR LOWER(i.bundle_id) LIKE ? ESCAPE '\\') "
            "ORDER BY i.id DESC LIMIT ?",
            (pattern, pattern, limit),
        ).fetchall()
    items = []
    for row in rows:
        if mod_file(row) is None:
            continue
        items.append({
            "id": row["id"], "name": row["name"], "version": row["version"],
            "bundle_id": row["bundle_id"], "description": row["description"],
            "size_bytes": row["size_bytes"], "created_at": row["created_at"],
            "download_path": "/api/v1/mods/" + str(row["id"]) + "/download",
            # Uploaded and authorized does NOT automatically mean FairPlay-decrypted.
            "fairplay_verified": False,
        })
    return items


def _match_vault(items, bundle_id: str, version: str):
    if not bundle_id:
        return None
    found = [item for item in items if item["bundle_id"].casefold() == bundle_id.casefold()]
    if version:
        found = [item for item in found if item["version"] == version]
    return found[0] if found else None


def register_decrypt_catalog(app, connection, upload_dir, mod_file, require_customer, require_admin=None):
    router = APIRouter()
    setup_pipeline(connection)

    def source_exists(bundle_id: str, version: str) -> bool:
        if not bundle_id or not version:
            return False
        with connection() as db:
            return db.execute(
                "SELECT 1 FROM ipa_decrypt_sources WHERE bundle_id=? COLLATE NOCASE "
                "AND version=? LIMIT 1", (bundle_id, version)
            ).fetchone() is not None

    def decorate_with_vault(items):
        free = _all_free_ipa(connection, upload_dir, mod_file)
        for item in items:
            match = _match_vault(free, item["bundle_id"], item["version"])
            item["cached"] = match
            item["ipa_available"] = match is not None
            item["source_available"] = source_exists(item["bundle_id"], item["version"])
        return items

    @router.post("/api/v1/admin/decrypt/sync-manifest",
                 dependencies=[Depends(require_admin)] if require_admin is not None else [])
    def sync_manifest():
        """Synchronize a publisher-authorized IPA catalog without manual uploads.

        ERI_IPA_MANIFEST_URL must be a HTTPS URL on ERI_IPA_SOURCE_HOSTS.
        Each entry needs name, bundle_id, version, url, sha256, and
        distribution_authorized=true. Downloads still pass worker validation.
        """
        if require_admin is None:
            raise HTTPException(503, "Chưa cấu hình quản trị viên")
        from eri_ipa_pipeline import validate_url, _SafeRedirect
        import os
        manifest_url = os.getenv("ERI_IPA_MANIFEST_URL", "").strip()
        if not manifest_url:
            raise HTTPException(503, "Chưa cấu hình ERI_IPA_MANIFEST_URL")
        try:
            validate_url(manifest_url)
            opener = urllib.request.build_opener(_SafeRedirect())
            with opener.open(urllib.request.Request(manifest_url, headers={
                "Accept": "application/json", "User-Agent": "ERIStore-Manifest/1.0"
            }), timeout=15) as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("Manifest quá lớn")
            payload = json.loads(raw)
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list) or len(items) > 1000:
                raise ValueError("Manifest cần items là danh sách tối đa 1000 nguồn")
        except (ValueError, OSError, urllib.error.URLError, TimeoutError) as exc:
            raise HTTPException(422, "Không tải được manifest IPA hợp lệ") from exc
        added, rejected = 0, []
        for index, item in enumerate(items):
            try:
                if not isinstance(item, dict) or item.get("distribution_authorized") is not True:
                    raise ValueError("Nguồn chưa xác nhận quyền phân phối")
                put_source(connection, name=item["name"], bundle_id=item["bundle_id"],
                           version=item["version"], url=item["url"], sha256=item["sha256"])
                schedule_matching_pipeline(connection, upload_dir, item["bundle_id"], item["version"])
                added += 1
            except (ValueError, KeyError, TypeError) as exc:
                rejected.append({"index": index, "reason": str(exc)[:120]})
        return {"registered": added, "rejected": rejected[:25],
                "message": "Đã đồng bộ nguồn IPA hợp lệ; tác vụ yêu cầu tương ứng được xếp xử lý."}

    @router.get("/api/v1/tools/ipa/decrypt/official")
    def official_ipa(bundle_id: str = Query(min_length=3, max_length=255)):
        """Discover original developer IPA links, without mirroring or repackaging."""
        # Only explicitly mapped first-party publishers; never trust an arbitrary IPA mirror.
        publishers = {
            "com.rileytestut.delta": "rileytestut/Delta",
            "com.utmapp.utm": "utmapp/UTM",
        }
        repo = publishers.get(bundle_id.casefold().strip())
        if not repo:
            return {"available": False, "notice": "Chưa có nguồn IPA chính chủ được xác minh cho ứng dụng này."}
        endpoint = "https://api.github.com/repos/" + repo + "/releases?per_page=5"
        try:
            req = urllib.request.Request(endpoint, headers={
                "Accept": "application/vnd.github+json", "User-Agent": "ERIStore/1.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                raw = response.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError("GitHub response too large")
            releases = json.loads(raw)
            if not isinstance(releases, list):
                raise ValueError("Unexpected releases payload")
            for release in releases:
                if not isinstance(release, dict) or release.get("draft"):
                    continue
                for asset in release.get("assets", []):
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name", ""))
                    link = str(asset.get("browser_download_url", ""))
                    parts = urlsplit(link)
                    if (not name.lower().endswith(".ipa") or
                        parts.scheme != "https" or parts.hostname != "github.com" or
                        not parts.path.startswith("/" + repo + "/releases/download/") or
                        not parts.path.lower().endswith(".ipa") or
                        int(asset.get("size", 0) or 0) < 1024):
                        continue
                    return {"available": True, "download_url": link,
                            "name": name[:160], "version": str(release.get("tag_name", ""))[:60],
                            "source": "GitHub Releases · " + repo,
                            "notice": "IPA công khai từ kho mã nguồn chính chủ; không lưu trữ lại."}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
            pass
        return {"available": False, "notice": "Chưa tìm được bản IPA công khai từ nhà phát triển."}

    @router.get("/api/v1/tools/ipa/decrypt/external")
    def external_ipa(target: str = Query(min_length=12, max_length=500),
                     country: str = "auto"):
        """Read-only discovery. Never treat an upstream 200 as a verified IPA."""
        safe_target = urlsplit(target)
        if (safe_target.scheme != "https" or safe_target.hostname != "apps.apple.com"
                or not re.search(r"/id[0-9]{6,15}(?:/|$)", safe_target.path)):
            raise HTTPException(422, "Cần link ứng dụng HTTPS từ App Store")
        region = _region(country)
        endpoint = ("https://decrypt.160-191-242-70.sslip.io/api/decrypt/search?"
                    + urlencode({"q": target, "country": region, "mode": "ios"}))
        req = urllib.request.Request(endpoint, headers={
            "Accept": "application/json", "User-Agent": "ERIStore/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status != 200:
                    raise HTTPException(502, "Nguồn IPA bên ngoài không phản hồi thành công")
                raw = response.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024:
                raise HTTPException(502, "Phản hồi nguồn IPA quá lớn")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Expected JSON object")
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise HTTPException(503, "Chưa xác minh được nguồn IPA bên ngoài") from exc
        entry = data.get("vault_entry")
        if not isinstance(entry, dict):
            entry = {}
        # A third-party URL is not evidence that the IPA is decrypted or safe.
        # Only expose the known download host and expected file path, never
        # an arbitrary redirect/URL supplied by upstream JSON.
        raw_url = str(entry.get("download_url") or entry.get("dl_url") or "")
        parts = urlsplit(raw_url)
        valid_url = (
            parts.scheme == "https"
            and parts.hostname == "160-191-242-70.sslip.io"
            and parts.port in (None, 443)
            and bool(re.fullmatch(r"/dl_dec/[A-Za-z0-9_-]{8,128}/[0-9]{8,15}/[A-Za-z0-9._%-]+\\.ipa", parts.path, re.I))
            and not parts.username and not parts.password
            and not parts.query and not parts.fragment
        )
        return {
            "found": bool(entry),
            "download_ready": bool(valid_url),
            "download_url": raw_url if valid_url else None,
            "source": "F4TEAM (external)",
            "title": str(entry.get("title") or "")[:160],
            "bundle_id": str(entry.get("bundle_id") or "")[:255],
            "version": str(entry.get("version") or "")[:60],
            "size_bytes": entry.get("total_size") if isinstance(entry.get("total_size"), int) else None,
            "notice": ("Nguồn ngoài có đường dẫn IPA; ERI chưa kiểm định nội dung file."
                       if valid_url else "Nguồn ngoài chưa trả đường dẫn IPA."),
            "verification": {
                "file_exists": False,
                "distribution_authorized": True,
                "plaintext_verified": entry.get("plaintext_verified") is True,
                "account_data_stripped": entry.get("account_data_stripped") is True,
            },
        }

    @router.get("/api/v1/tools/ipa/decrypt/search")
    def search(q: str = Query(min_length=2, max_length=500),
               country: str = "auto", limit: int = Query(default=15, ge=1, le=25)):
        try:
            items = _search_apps(q, country, limit)
            return {"items": decorate_with_vault(items), "total": len(items),
                    "source": "Apple Search API + authorized ERI IPA inventory"}
        except HTTPException as exc:
            # A licensed IPA can still be downloaded if Apple metadata is down.
            kind, value, _ = _parse_target(q)
            if kind == "id":
                raise exc
            matches = _all_free_ipa(connection, upload_dir, mod_file, q=value, limit=300)
            if not matches:
                raise exc
            local = [{
                "app_id": 0, "name": item["name"], "developer": "Kho ERI",
                "bundle_id": item["bundle_id"], "version": item["version"],
                "country": "ERI", "app_store_url": "", "icon_url": "",
                "genre": "", "price": 0, "size_bytes": item["size_bytes"],
                "cached": item, "ipa_available": True,
            } for item in matches[:limit]]
            return {"items": local, "total": len(local),
                    "source": "ERI local vault; Apple metadata unavailable"}

    @router.get("/api/v1/tools/ipa/decrypt/vault")
    def vault(q: str = Query(default="", max_length=100),
              sort: str = "newest",
              limit: int = Query(default=24, ge=1, le=50),
              offset: int = Query(default=0, ge=0)):
        if sort not in ("newest", "size_desc", "size_asc", "title"):
            raise HTTPException(422, "Cách sắp xếp không hợp lệ")
        items = _all_free_ipa(connection, upload_dir, mod_file, q=q)
        if sort == "size_desc": items.sort(key=lambda x: x["size_bytes"], reverse=True)
        elif sort == "size_asc": items.sort(key=lambda x: x["size_bytes"])
        elif sort == "title": items.sort(key=lambda x: x["name"].casefold())
        return {"items": items[offset:offset + limit], "total": len(items),
                "offset": offset, "limit": limit,
                "notice": "Chỉ hiện IPA miễn phí được quản trị viên cấp quyền và còn file thật."}

    @router.get("/api/v1/tools/ipa/decrypt/versions")
    def versions(target: str = Query(min_length=2, max_length=500), country: str = "auto"):
        apps = _search_apps(target, country, limit=1)
        if not apps:
            raise HTTPException(404, "Không tìm thấy ứng dụng trên App Store")
        app = apps[0]
        free = _all_free_ipa(connection, upload_dir, mod_file)
        stored = [x for x in free if x["bundle_id"].casefold() == app["bundle_id"].casefold()]
        versions_list = []
        seen = set()
        for x in stored:
            if x["version"] not in seen:
                seen.add(x["version"])
                versions_list.append({"version": x["version"], "cached": x,
                                      "source": "ERI authorized library",
                                      "source_available": True})
        if app["version"] not in seen:
            versions_list.insert(0, {"version": app["version"], "cached": None,
                                     "source": "Apple current version metadata"})
        return {"app": app, "versions": versions_list,
                "notice": "Apple Search API không công bố lịch sử mọi phiên bản; chỉ hiện bản hiện tại và bản đã có trong kho ERI."}

    with connection() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS ipa_decrypt_requests ("
            "id TEXT PRIMARY KEY, customer_id INTEGER NOT NULL,"
            "app_id INTEGER NOT NULL, bundle_id TEXT NOT NULL,"
            "name TEXT NOT NULL, version TEXT NOT NULL,"
            "country TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ipa_decrypt_requests_customer "
            "ON ipa_decrypt_requests(customer_id, created_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ipa_decrypt_requests_app "
            "ON ipa_decrypt_requests(bundle_id, version, created_at)"
        )
        columns = {x["name"] for x in db.execute("PRAGMA table_info(ipa_decrypt_requests)")}
        for name, sql_type in (
            ("state", "TEXT NOT NULL DEFAULT 'waiting_for_source'"),
            ("progress", "INTEGER"),
            ("message", "TEXT NOT NULL DEFAULT ''"),
            ("updated_at", "TEXT"),
        ):
            if name not in columns:
                db.execute("ALTER TABLE ipa_decrypt_requests ADD COLUMN " + name + " " + sql_type)

    def job_payload(row, free=None):
        if free is None:
            free = _all_free_ipa(connection, upload_dir, mod_file)
        match = _match_vault(free, row["bundle_id"], row["version"])
        state = "completed" if match else row["state"] or "waiting_for_source"
        return {
            "job_id": row["id"], "app_id": row["app_id"], "name": row["name"],
            "bundle_id": row["bundle_id"], "version": row["version"],
            "country": row["country"], "created_at": row["created_at"],
            "state": state,
            "progress": 100 if match else row["progress"],
            "result": match,
            "message": ("IPA đã có trong kho ERI." if match
                        else row["message"] or "Chưa có nguồn IPA tương ứng trên máy chủ ERI."),
        }

    @router.post("/api/v1/tools/ipa/decrypt/requests", status_code=201)
    def create_request(payload: IPARequest, customer=Depends(require_customer)):
        apps = _search_apps(payload.target, payload.country, limit=1)
        if not apps:
            raise HTTPException(404, "Không tìm thấy ứng dụng trên App Store")
        app_info = apps[0]
        chosen_version = payload.version or app_info["version"]
        if not re.fullmatch(r"[A-Za-z0-9._+-]{1,60}", chosen_version):
            raise HTTPException(422, "Phiên bản không hợp lệ")
        with connection() as db:
            existing = db.execute(
                "SELECT * FROM ipa_decrypt_requests "
                "WHERE customer_id=? AND bundle_id=? AND version=? "
                "ORDER BY created_at DESC LIMIT 1",
                (customer["id"], app_info["bundle_id"], chosen_version),
            ).fetchone()
            if existing is not None:
                # Retry/resume when an operator-approved source is now available.
                schedule_pipeline(connection, upload_dir, existing["id"])
                return {**job_payload(existing), "reused": True}
            current = db.execute(
                "SELECT COUNT(*) FROM ipa_decrypt_requests WHERE customer_id=?",
                (customer["id"],)
            ).fetchone()[0]
            if current >= 30:
                raise HTTPException(429, "Tài khoản đã tạo quá 30 yêu cầu IPA")
            job_id = secrets.token_hex(16)
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "INSERT INTO ipa_decrypt_requests(id,customer_id,app_id,bundle_id,name,"
                "version,country,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, customer["id"], app_info["app_id"], app_info["bundle_id"],
                 app_info["name"], chosen_version, app_info["country"], now)
            )
            row = db.execute("SELECT * FROM ipa_decrypt_requests WHERE id=?", (job_id,)).fetchone()
        schedule_pipeline(connection, upload_dir, job_id)
        return job_payload(row)

    @router.get("/api/v1/tools/ipa/decrypt/requests")
    def list_requests(customer=Depends(require_customer)):
        # User-scoped work history survives browser reloads and lost localStorage
        # as long as the configured database remains persistent.
        with connection() as db:
            rows = db.execute(
                "SELECT * FROM ipa_decrypt_requests WHERE customer_id=? "
                "ORDER BY created_at DESC LIMIT 30", (customer["id"],)
            ).fetchall()
        free = _all_free_ipa(connection, upload_dir, mod_file)
        for row in rows:
            if row["state"] not in ("completed", "failed"):
                schedule_pipeline(connection, upload_dir, row["id"])
        return {"items": [job_payload(row, free) for row in rows], "total": len(rows)}

    if require_admin is not None:
        @router.post("/api/v1/admin/decrypt/sources",
                     dependencies=[Depends(require_admin)], status_code=201)
        def register_source(payload: AuthorizedIPASource):
            if not payload.distribution_authorized:
                raise HTTPException(422, "Cần xác nhận quyền phân phối IPA")
            try:
                put_source(
                    connection, name=payload.name, bundle_id=payload.bundle_id,
                    version=payload.version, url=payload.url, sha256=payload.sha256
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            schedule_matching_pipeline(
                connection, upload_dir, payload.bundle_id, payload.version
            )
            return {"ok": True, "bundle_id": payload.bundle_id,
                    "version": payload.version, "message": "Đã đăng ký nguồn IPA ERI"}

        @router.get("/api/v1/admin/decrypt/requests",
                    dependencies=[Depends(require_admin)])
        def admin_requests(limit: int = Query(default=50, ge=1, le=200)):
            with connection() as db:
                rows = db.execute(
                    "SELECT r.*,c.username FROM ipa_decrypt_requests r "
                    "JOIN customers c ON c.id=r.customer_id "
                    "ORDER BY r.created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            free = _all_free_ipa(connection, upload_dir, mod_file)
            return {"items": [
                {**job_payload(row, free), "requester": row["username"]}
                for row in rows
            ], "total": len(rows)}

    @router.get("/api/v1/tools/ipa/decrypt/requests/{job_id}")
    def get_request(job_id: str, customer=Depends(require_customer)):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise HTTPException(404, "Không tìm thấy yêu cầu")
        with connection() as db:
            row = db.execute(
                "SELECT * FROM ipa_decrypt_requests WHERE id=? AND customer_id=?",
                (job_id, customer["id"])
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Không tìm thấy yêu cầu")
        if row["state"] not in ("completed", "failed"):
            schedule_pipeline(connection, upload_dir, job_id)
        return job_payload(row)

    app.include_router(router)
    return router
