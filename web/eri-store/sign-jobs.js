"use strict";
// Resumable 2-MiB signing uploads. Do not store P12, password, or customer keys
// in IndexedDB/localStorage. The job ID is non-secret and scoped by server auth.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN_KEY = "eri_store_buyer_token";
  const JOB_KEY = "eri_store_signing_job_v2";
  const $ = id => document.getElementById(id);
  const token = () => sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || "";
  const fmt = b => (b / 1048576).toFixed(1) + " MB";
  const getSaved = () => {
    try { return JSON.parse(localStorage.getItem(JOB_KEY) || "null"); }
    catch { return null; }
  };
  const save = data => { try { localStorage.setItem(JOB_KEY, JSON.stringify(data)); } catch {} };
  const clear = () => { try { localStorage.removeItem(JOB_KEY); } catch {} };
  let busy = false;
  let polling = false;

  function status(message, severity = "info") {
    const el = $("ipa-sign-status");
    if (!el) return;
    el.replaceChildren(document.createTextNode(message));
    el.dataset.state = severity;
  }
  function action(name, fn) {
    const el = $("ipa-sign-status");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn outline";
    button.textContent = name;
    button.addEventListener("click", fn);
    el?.append(document.createElement("p"), button);
  }
  function errorText(data, statusCode) {
    if (statusCode === 401) return "Phiên đăng nhập hết hạn. Hãy đăng nhập lại; tệp đã chọn không bị xóa.";
    if (typeof data?.detail === "string") return data.detail;
    if (statusCode >= 500) return "Máy chủ đang bận hoặc khởi động lại. Bạn có thể mở lại để tiếp tục.";
    return "HTTP " + statusCode + ": Không xử lý được yêu cầu.";
  }
  async function api(path, {method = "GET", body, contentType, timeout = 60000} = {}) {
    const credential = token();
    if (!credential) throw new Error("Vui lòng đăng nhập ERI STORE trước khi ký IPA.");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const headers = {"Authorization": "Bearer " + credential};
      if (contentType) headers["Content-Type"] = contentType;
      const response = await fetch(API + path, {
        method, headers, body, cache:"no-store", signal:controller.signal
      });
      let json = {};
      try { json = await response.json(); } catch {}
      if (!response.ok) throw new Error(errorText(json, response.status));
      return json;
    } catch (e) {
      if (e.name === "AbortError") throw new Error("Kết nối quá thời gian. Bấm tiếp tục để gửi lại phần chưa xác nhận.");
      throw e;
    } finally { clearTimeout(timer); }
  }
  async function putChunk(jobId, index, blob, sha) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);
    try {
      const response = await fetch(API + "/api/v1/tools/ipa/sign-jobs/" + jobId + "/chunks/" + index, {
        method: "PUT", cache: "no-store", signal: controller.signal,
        headers: {
          "Authorization": "Bearer " + token(), "Content-Type": "application/octet-stream",
          "X-ERI-Chunk-SHA256": sha
        },
        body: blob
      });
      let json = {};
      try { json = await response.json(); } catch {}
      if (!response.ok) throw new Error(errorText(json, response.status));
      if (json.index !== index || json.received_bytes !== blob.size) {
        throw new Error("Máy chủ chưa xác nhận đủ phần " + (index + 1));
      }
    } catch (e) {
      if (e.name === "AbortError") throw new Error("Gửi từng phần quá thời gian, có thể thử lại.");
      throw e;
    } finally { clearTimeout(timer); }
  }
  async function sha256(blob) {
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2,"0")).join("");
  }
  function resultLinks(result) {
    const el = $("ipa-sign-status");
    if (!el || !result) return;
    const link = result.download_path;
    if (typeof link !== "string" ||
        !/^\/api\/v1\/tools\/ipa\/install\/[A-Za-z0-9_-]{32,64}\/app\.ipa$/.test(link)) return;
    const tokenId = link.split("/")[6];
    const row = document.createElement("div");
    row.className = "ipa-tool-actions";
    const install = document.createElement("a");
    install.className = "btn primary";
    install.href = "itms-services://?action=download-manifest&url=" +
      encodeURIComponent(API + "/api/v1/tools/ipa/install/" + tokenId + "/manifest.plist");
    install.textContent = "Cài trực tiếp lên iPhone";
    const download = document.createElement("a");
    download.className = "btn outline";
    download.href = API + link;
    download.textContent = "Tải IPA đã ký";
    download.rel = "noopener noreferrer";
    row.append(install,download);
    el.append(row);
    const message = document.createElement("p");
    message.className = "small";
    message.textContent = "Cài bằng Safari; chứng chỉ cần hợp lệ với thiết bị. Liên kết có hiệu lực 60 phút và có thể mất khi Render khởi động lại.";
    el.append(message);
  }
  async function watch(jobId) {
    if (polling) return;
    polling = true;
    try {
      // Poll only while this page is open; processing continues server-side.
      while (document.visibilityState !== "hidden") {
        const job = await api("/api/v1/tools/ipa/sign-jobs/" + jobId);
        if (job.state === "completed") {
          clear();
          status("Đã ký IPA thành công. Chọn cài đặt hoặc tải tệp.", "success");
          resultLinks(job.result);
          return;
        }
        if (job.state === "failed") {
          status(job.error || "Ký IPA thất bại.", "error");
          clear();
          return;
        }
        if (job.state === "uploading") {
          status("Tệp đã gửi " + fmt(job.uploaded_bytes) + "/" + fmt(job.size_bytes) +
            ". Chọn lại IPA nếu đã thoát trang để tiếp tục.");
          return;
        }
        status("Máy chủ đang ký IPA. Bạn có thể thoát trang và mở lại để xem kết quả...");
        await new Promise(done => setTimeout(done, 3000));
      }
    } catch (e) {
      status(e.message + " Có thể mở lại trang để kiểm tra tác vụ.", "error");
    } finally { polling = false; }
  }
  async function run(form) {
    if (busy) return;
    busy = true;
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    try {
      const file = form.elements.namedItem("ipa")?.files?.[0];
      const p12 = form.elements.namedItem("p12")?.files?.[0];
      const profile = form.elements.namedItem("mobileprovision")?.files?.[0];
      const password = form.elements.namedItem("password")?.value;
      if (!file || !p12 || !profile || !password) throw new Error("Chọn đủ IPA, P12, mobileprovision và mật khẩu.");
      const ready = getSaved();
      let job;
      if (ready && ready.name === file.name && ready.size === file.size) {
        try {
          job = await api("/api/v1/tools/ipa/sign-jobs/" + ready.id);
          if (job.state === "failed") job = null;
          if (job?.state === "completed") { clear(); status("Tác vụ đã ký xong.", "success"); resultLinks(job.result); return; }
          if (job?.state === "processing") { await watch(ready.id); return; }
        } catch (e) {
          if (/không còn|404|410|tải lại/.test(e.message.toLowerCase())) job = null;
          else throw e;
        }
      }
      if (!job) {
        clear();
        status("Tạo phiên ký IPA mới...");
        job = await api("/api/v1/tools/ipa/sign-jobs", {
          method:"POST", body:JSON.stringify({filename:file.name,size_bytes:file.size}),
          contentType:"application/json"
        });
        save({id:job.job_id,name:file.name,size:file.size});
      }
      let received = new Set(job.received || []);
      const chunks = Math.ceil(file.size / job.chunk_bytes);
      for (let i=0;i<chunks;i++) {
        if (received.has(i)) continue;
        const slice = file.slice(i * job.chunk_bytes, Math.min(file.size,(i+1)*job.chunk_bytes));
        const digest = await sha256(slice);
        let sent = false;
        for (let retry=0;retry<3&&!sent;retry++) {
          try {
            await putChunk(job.job_id,i,slice,digest);
            sent=true;
          } catch(e) {
            // A lost response doesn't mean the server lost the bytes.
            const remote = await api("/api/v1/tools/ipa/sign-jobs/" + job.job_id);
            if ((remote.received || []).includes(i)) sent=true;
            else if (retry===2) throw e;
          }
        }
        received.add(i);
        const uploaded = Math.min(file.size,received.size * job.chunk_bytes);
        status("Đã lưu lên máy chủ " + fmt(uploaded) + " / " + fmt(file.size) +
          " (phần " + (i+1) + "/" + chunks + "). Có thể tiếp tục nếu mạng ngắt.");
      }
      status("Đã tải đủ IPA. Đang gửi chứng chỉ để bắt đầu ký...");
      const args = new FormData();
      args.append("p12",p12);
      args.append("mobileprovision",profile);
      args.append("password",password);
      await api("/api/v1/tools/ipa/sign-jobs/" + job.job_id + "/start", {
        method:"POST",body:args,timeout:90000
      });
      form.elements.namedItem("password").value = "";
      status("Đã giao máy chủ xử lý ký. Có thể đóng website và mở lại để xem kết quả.");
      await watch(job.job_id);
    } catch(e) {
      status(e.message || "Không thể gửi IPA.", "error");
      if (/hết hạn|đăng nhập/.test(e.message?.toLowerCase()||"")) {
        action("Đăng nhập lại", () => document.querySelector('[data-view="account"]')?.click());
      }
    } finally {
      busy=false;
      submit.disabled=false;
    }
  }
  document.addEventListener("DOMContentLoaded",() => {
    const form = $("ipa-sign-form");
    if (!form) return;
    form.addEventListener("submit",e => {e.preventDefault();run(form);});
    const pending = getSaved();
    if (pending?.id) {
      status("Đang khôi phục trạng thái IPA từ máy chủ...");
      watch(pending.id);
    }
    document.addEventListener("visibilitychange",() => {
      if (document.visibilityState === "visible" && getSaved()?.id) watch(getSaved().id);
    });
  });
})();
