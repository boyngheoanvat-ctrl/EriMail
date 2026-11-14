"use strict";
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const KEY = "eri.admin.ipa.upload-session.v1";
  const CHUNK_SIZE = 4 * 1024 * 1024;
  const $ = id => document.getElementById(id);
  let busy = false;

  function status(id, message, kind = "") {
    const element = $(id);
    if (element) { element.textContent = message; element.dataset.kind = kind; }
  }
  function requireKey() {
    const key = $("admin-key").value.trim();
    if (!key) throw new Error("Nhập ERI_ADMIN_KEY.");
    return key;
  }
  async function call(path, {method = "GET", body, headers = {}} = {}) {
    const response = await fetch(API + path, {
      method, headers: {"X-ERI-Admin-Key": requireKey(), ...headers},
      body, cache: "no-store", credentials: "omit", redirect: "error"
    });
    const text = await response.text();
    let data = {};
    try { data = JSON.parse(text); } catch {}
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "string" ? detail :
        response.status === 403 ? "Khóa admin không hợp lệ." :
        response.status === 404 ? "Phiên tải không còn. Hãy bấm tải lại." :
        "Máy chủ trả mã " + response.status);
    }
    return data;
  }
  function savedSession() {
    try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; }
  }
  function saveSession(data) {
    try { localStorage.setItem(KEY, JSON.stringify(data)); } catch {}
  }
  function clearSession() {
    try { localStorage.removeItem(KEY); } catch {}
  }
  function progress(bytes, total) {
    $("upload-progress-section").hidden = false;
    const percentage = Math.min(99, Math.floor(bytes * 100 / total));
    $("upload-progress").value = percentage;
    $("upload-pct").textContent = percentage + "%";
    status("upload-status", "Đã nhận " + (bytes / 1048576).toFixed(1) + " / " +
      (total / 1048576).toFixed(1) + " MiB");
  }
  async function sha256(blob) {
    const bytes = await blob.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    return [...new Uint8Array(digest)].map(x => x.toString(16).padStart(2, "0")).join("");
  }
  async function transfer(file) {
    const saved = savedSession();
    let session = null;
    if (saved && saved.name === file.name && saved.size === file.size) {
      try {
        const existing = await call("/api/v1/admin/upload-sessions/" + saved.id +
          "/fast-parts");
        if (existing.size_bytes === file.size &&
            existing.chunk_bytes === CHUNK_SIZE) {
          session = {upload_id:saved.id, size_bytes:file.size, chunk_bytes:CHUNK_SIZE};
        }
      } catch { clearSession(); }
    } else if (saved) {
      clearSession();
    }
    if (!session) {
      session = await call("/api/v1/admin/upload-sessions", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          kind: "ipa", filename: file.name, size_bytes: file.size,
          name: $("ipa-name").value.trim() || file.name.replace(/\.ipa$/i, ""),
          description: "", price_vnd: 0, authorized: true,
          fast: true, verify_free_ipa: true
        })
      });
      saveSession({id:session.upload_id, name:file.name, size:file.size});
    }
    const base = "/api/v1/admin/upload-sessions/" + session.upload_id;
    const partsStatus = await call(base + "/fast-parts");
    const received = new Set(partsStatus.received);
    const totalParts = Math.ceil(file.size / CHUNK_SIZE);
    let confirmed = 0;
    for (let i = 0; i < totalParts; i++) {
      if (received.has(i))
        confirmed += Math.min(CHUNK_SIZE,file.size-i*CHUNK_SIZE);
    }
    progress(confirmed,file.size);
    for (let i = 0; i < totalParts; i++) {
      if (received.has(i)) continue;
      const start = i * CHUNK_SIZE;
      const blob = file.slice(start, Math.min(file.size,start + CHUNK_SIZE));
      const hash = await sha256(blob);
      let success = false;
      for (let attempt = 0; attempt < 3 && !success; attempt++) {
        try {
          await call(base + "/fast-parts/" + i, {
            method: "POST", body: blob,
            headers: {"Content-Type":"application/octet-stream",
                      "X-ERI-Chunk-SHA256":hash}
          });
          success = true;
        } catch (error) {
          if (attempt === 2) throw error;
          status("upload-status", "Đang thử lại phần " + (i + 1) + "/" + totalParts + "…");
        }
      }
      confirmed += blob.size;
      progress(confirmed,file.size);
    }
    status("upload-status", "Đã nhận toàn bộ dữ liệu · Đang xác minh IPA…");
    return await call(base + "/complete", {method:"POST"});
  }
  async function upload() {
    if (busy) return;
    const file = $("ipa-file").files[0];
    if (!file || !file.name.toLowerCase().endsWith(".ipa") || file.size < 1) {
      status("upload-status","Chọn tệp .ipa hợp lệ.", "error"); return;
    }
    if (!$("ipa-authorized").checked) {
      status("upload-status","Cần xác nhận quyền phân phối.", "error"); return;
    }
    if (file.size > 512 * 1024 * 1024) {
      status("upload-status","Tệp vượt 512 MiB.", "error"); return;
    }
    busy = true; $("upload-button").disabled = true;
    $("uploaded-link").replaceChildren();
    try {
      requireKey();
      const result = await transfer(file);
      clearSession();
      $("upload-progress-section").hidden = false;
      $("upload-progress").value = 100;
      $("upload-pct").textContent = "100%";
      status("upload-status","Đã xác minh và đăng IPA vào kho ERI.", "success");
      const link = document.createElement("a");
      link.textContent = "Tải IPA đã đăng · " + (result.name || file.name);
      link.href = API + "/api/v1/mods/" + result.id + "/download";
      link.rel = "noopener noreferrer";
      $("uploaded-link").append(link);
      refresh();
    } catch (error) {
      status("upload-status",error.message + " (có thể bấm tải lên để tiếp tục).","error");
    } finally {
      busy = false; $("upload-button").disabled = false;
    }
  }
  async function source() {
    const button = $("source-button");
    button.disabled = true;
    try {
      requireKey();
      if (!$("source-authorized").checked) throw new Error("Cần xác nhận quyền phân phối.");
      await call("/api/v1/admin/decrypt/sources",{
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          name:$("source-name").value.trim(),
          bundle_id:$("source-bundle").value.trim(),
          version:$("source-version").value.trim(),
          url:$("source-url").value.trim(),
          sha256:$("source-sha").value.trim(),
          distribution_authorized:true
        })
      });
      status("source-status","Đã đăng ký nguồn. Worker ERI sẽ kiểm tra IPA.", "success");
      refresh();
    } catch(error) {status("source-status",error.message,"error");}
    finally {button.disabled = false;}
  }
  async function refresh() {
    try {
      const data = await call("/api/v1/admin/decrypt/requests?limit=100");
      const list = $("requests-list");
      list.replaceChildren();
      for (const job of data.items || []) {
        const item = document.createElement("article");
        item.className = "item";
        const title = document.createElement("strong");
        title.textContent = (job.name || job.bundle_id) + " · v" + job.version;
        const sub = document.createElement("small");
        const stage = job.state === "completed" ? "Sẵn sàng tải" :
          job.state === "processing" ? "Đang xử lý · " +
            (job.progress === null ? "..." : job.progress + "%") :
          job.state === "failed" ? "Lỗi: " + job.message : "Chưa có nguồn IPA";
        sub.textContent = job.requester + " · " + stage;
        item.append(title, sub);
        if (job.result?.download_path &&
            /^\/api\/v1\/mods\/\d+\/download$/.test(job.result.download_path)) {
          const link = document.createElement("a");
          link.href = API + job.result.download_path;
          link.textContent = "Tải IPA";
          item.append(link);
        }
        list.append(item);
      }
      status("requests-status", data.total ? data.total + " yêu cầu" : "Chưa có yêu cầu");
    } catch(error) {status("requests-status",error.message,"error");}
  }
  document.addEventListener("DOMContentLoaded",() => {
    $("ipa-file").addEventListener("change", () => {
      const f=$("ipa-file").files[0];
      if (f && !$("ipa-name").value) $("ipa-name").value=f.name.replace(/\.ipa$/i,"");
      const saved=savedSession();
      if (f && saved && saved.name===f.name && saved.size===f.size)
        status("upload-status","Đã tìm thấy phiên tải cũ. Bấm tải lên để tiếp tục.");
    });
    $("upload-button").addEventListener("click",upload);
    $("source-button").addEventListener("click",source);
    $("refresh-button").addEventListener("click",refresh);
  });
})();