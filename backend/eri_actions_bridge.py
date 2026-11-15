"""Admin-only bridge to dispatch approved IPA jobs on GitHub Actions.

Does not grant public users GitHub credentials or claim that an Actions
artifact is a permanent, directly downloadable ERI Store IPA.
"""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from actions_ipa_worker import selected_source

SOURCE_MANIFEST = Path(__file__).with_name("eri_actions_sources.json")
WORKFLOW = "process-ipa.yml"


class DispatchInput(BaseModel):
    source_id: str = Field(min_length=1, max_length=64)


def _settings():
    repo = os.getenv("ERI_GH_ACTIONS_REPO", "").strip()
    token = os.getenv("ERI_GH_ACTIONS_TOKEN", "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise HTTPException(503, "ERI chưa cấu hình GitHub Actions token/repository cho tác vụ admin")
    return repo, token


def _github(method, path, payload=None):
    repo, token = _settings()
    url = "https://api.github.com/repos/" + repo + "/" + path
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ERI-Store-Actions-Bridge/1.0",
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=body, headers=headers)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as response:
            status = response.status
            if method == "POST" and status == 204:
                return {"ok": True}
            if status != 200:
                raise HTTPException(502, "GitHub Actions phản hồi không hợp lệ")
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise HTTPException(502, "GitHub Actions response quá lớn")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise HTTPException(502, "GitHub Actions response không hợp lệ")
        return data
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(503, "GitHub từ chối token hoặc quyền Actions") from exc
        if exc.code == 404:
            raise HTTPException(503, "GitHub không tìm thấy repository hoặc workflow") from exc
        if exc.code == 422:
            raise HTTPException(422, "GitHub từ chối yêu cầu dispatch") from exc
        raise HTTPException(502, "GitHub API HTTP " + str(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise HTTPException(503, "Không kết nối được GitHub Actions") from exc


def install_actions_bridge(app, require_admin):
    router = APIRouter(
        prefix="/api/v1/admin/decrypt/actions",
        dependencies=[Depends(require_admin)],
    )

    @router.get("/sources")
    def sources():
        root = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
        items = root.get("items") if isinstance(root, dict) else []
        return {"items": [
            {"source_id": item["id"], "name": str(item.get("name", ""))[:160],
             "bundle_id": item.get("bundle_id"), "version": item.get("version")}
            for item in items if isinstance(item, dict)
            and item.get("distribution_authorized") is True
            and isinstance(item.get("id"), str)
        ]}

    @router.post("/dispatch", status_code=202)
    def dispatch(body: DispatchInput):
        try:
            selected_source(SOURCE_MANIFEST, body.source_id)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(422, "Nguồn IPA chưa được ERI phê duyệt") from exc
        _github("POST", "actions/workflows/" + WORKFLOW + "/dispatches", {
            "ref": "main", "inputs": {"source_id": body.source_id},
        })
        repo, _token = _settings()
        return {
            "accepted": True,
            "source_id": body.source_id,
            "workflow_url": "https://github.com/" + repo + "/actions/workflows/" + WORKFLOW,
            "message": "GitHub đã nhận tác vụ. Chưa có file tải đến khi workflow kiểm tra thành công.",
        }

    @router.get("/runs")
    def runs():
        data = _github("GET", "actions/workflows/" + WORKFLOW + "/runs?per_page=20")
        rows = data.get("workflow_runs")
        if not isinstance(rows, list):
            raise HTTPException(502, "Phản hồi danh sách job không hợp lệ")
        result = []
        for item in rows[:20]:
            if not isinstance(item, dict):
                continue
            candidate = str(item.get("html_url") or "")
            if not candidate.startswith("https://github.com/"):
                candidate = ""
            result.append({
                "run_id": item.get("id"),
                "status": item.get("status"),
                "conclusion": item.get("conclusion"),
                "url": candidate,
                "created_at": item.get("created_at"),
            })
        return {"items": result, "total": len(result)}

    app.include_router(router)
    return router
