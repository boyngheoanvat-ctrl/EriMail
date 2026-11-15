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
  let currentJob = null;
  // The user must re-select their local IPA after reloading Safari.
  // Only confirmed server chunks count towards the progress bar.

  function showProgress(bytes, total, stage) {
    const panel = $("ipa-sign-progress");
    const bar = $("ipa-sign-progress-bar");
    const percent = $("ipa-sign-progress-percent");
    const heading = $("ipa-sign-progress-label");
    const note = $("ipa-sign-progress-stage");
    if (!panel || !bar || !percent || !heading || !note) return;
    const value = Math.max(0, Math.min(100, Math.round(1000 * bytes / Math.max(total, 1)) / 10));
    panel.classList.remove("hidden");
    bar.value = value;
    percent.textContent = value.toFixed(1) + "%";
    heading.textContent = stage === "preparing" ? "Đang chuẩn bị phiên tải IPA" :
      stage === "signing" ? "Đã tải đủ IPA — đang ký trên máy chủ" :
      stage === "complete" ? "Đã ký IPA thành công" :
      "Đang tải IPA lên máy chủ";
    note.textContent = stage === "preparing"
      ? "Chưa gửi tệp. Đang kiểm tra đăng nhập và kết nối máy chủ..."
      : stage === "signing"
      ? "100% là tiến độ gửi file, không phải tiến độ ký. Máy chủ đang xử lý chữ ký."
      : stage === "complete"
        ? "Chọn Cài trực tiếp lên iPhone hoặc Tải IPA đã ký bên dưới."
        : "Đã nhận " + fmt(bytes) + " / " + fmt(total) +
          " — chỉ cập nhật sau khi máy chủ xác nhận dữ liệu.";
  }

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
    if (statusCode === 401) return "Phiên đăng nhập không còn hợp lệ. Đăng nhập lại rồi chọn Tiếp tục tải IPA.";
    if (statusCode === 403) return "Tài khoản không có quyền truy cập phiên ký IPA này.";
    if (statusCode === 404 || statusCode === 410) return "Phiên tải IPA cũ không còn trên máy chủ. Hãy tạo phiên mới.";
    if (statusCode === 409) return "Phiên IPA không ở trạng thái mong đợi. Đang cần kiểm tra lại phiên.";
    if (statusCode === 413) return "Phần IPA vượt giới hạn máy chủ. Hãy chọn file hợp lệ.";
    if (statusCode === 429) return "Máy chủ đang giới hạn yêu cầu. Vui lòng thử lại.";
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
  async function watch(jobId, activeSigner = false) {
    if (polling || (busy && !activeSigner)) return;
    polling = true;
    try {
      // Poll only while this page is open; processing continues server-side.
      while (document.visibilityState !== "hidden") {
        const job = await api("/api/v1/tools/ipa/sign-jobs/" + jobId);
        if (job.state === "completed") {
          clear();
          showProgress(job.size_bytes, job.size_bytes, "complete");
          status("Ký IPA thành công! Bấm Cài trực tiếp lên iPhone để iOS hỏi xác nhận cài đặt, hoặc tải IPA đã ký.", "success");
          resultLinks(job.result);
          return;
        }
        if (job.state === "failed") {
          status(job.error || "Ký IPA thất bại.", "error");
          $("ipa-sign-progress")?.classList.add("hidden");
          clear();
          return;
        }
        if (job.state === "uploading") {
          // Background status checks may not overwrite the active upload's UI.
          if (!busy) {
            showProgress(job.uploaded_bytes, job.size_bytes, "uploading");
            const savedPct = (100 * job.uploaded_bytes / Math.max(1, job.size_bytes)).toFixed(1);
            status("Máy chủ đã nhận " + savedPct + "% · " + fmt(job.uploaded_bytes) + " / " + fmt(job.size_bytes) +
              ". Chọn lại cùng tệp IPA và bấm Tiếp tục tải IPA để gửi những phần còn thiếu.");
            const form = $("ipa-sign-form");
            const resumeFile = form?.elements.namedItem("ipa")?.files?.[0];
            if (resumeFile?.name === getSaved()?.name && resumeFile.size === getSaved()?.size) {
              action("Tiếp tục tải IPA", () => run(form));
            }
          }
          return;
        }
        showProgress(job.size_bytes, job.size_bytes, "signing");
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
      showProgress(0, file.size, "preparing");
      status("Đang kiểm tra đăng nhập và phiên tải IPA cũ. Chưa gửi dữ liệu tới máy chủ.");
      const ready = getSaved();
      let job;
      if (ready && ready.name === file.name && ready.size === file.size) {
        try {
          status("Đang kết nối máy chủ để xác minh các phần IPA đã tải của phiên trước...");
          job = await api("/api/v1/tools/ipa/sign-jobs/" + ready.id);
          if (job.state === "failed") job = null;
          if (job?.state === "completed") {
            clear(); showProgress(job.size_bytes, job.size_bytes, "complete");
            status("Tác vụ đã ký xong. Chọn cài trực tiếp hoặc tải IPA.", "success");
            resultLinks(job.result); return;
          }
          if (job?.state === "processing") { await watch(ready.id, true); return; }
        } catch (e) {
          if (/không còn|404|410|tải lại/.test(e.message.toLowerCase())) job = null;
          else throw e;
        }
      }
      if (!job) {
        clear();
        status("Đang yêu cầu máy chủ mở phiên ký IPA. Nếu Render Free đang khởi động, bước này có thể mất thêm thời gian...");
        job = await api("/api/v1/tools/ipa/sign-jobs", {
          method:"POST", body:JSON.stringify({filename:file.name,size_bytes:file.size}),
          contentType:"application/json"
        });
        save({id:job.job_id,name:file.name,size:file.size});
        currentJob = job.job_id;
      }
      currentJob = job.job_id;
      let received = new Set(job.received || []);
      const chunks = Math.ceil(file.size / job.chunk_bytes);
      if (!(Number.isInteger(job.chunk_bytes) && job.chunk_bytes>0 && job.chunk_bytes<=8*1048576 &&
            Number.isInteger(chunks) && chunks>0)) throw new Error("Máy chủ trả kích thước phần IPA không hợp lệ.");
      const confirmedBytes = () => Array.from(received).reduce((bytes, n) =>
        bytes + Math.max(0, Math.min(job.chunk_bytes, file.size - n * job.chunk_bytes)), 0);
      showProgress(confirmedBytes(), file.size, "uploading");
      for (let i=0;i<chunks;i++) {
        if (received.has(i)) continue;
        if (!token()) throw new Error("Phiên đăng nhập đã mất. Đăng nhập lại rồi tiếp tục tải IPA.");
        const slice = file.slice(i * job.chunk_bytes, Math.min(file.size,(i+1)*job.chunk_bytes));
        status("Đang tính SHA-256 cho phần " + (i+1) + "/" + chunks +
          " (" + fmt(slice.size) + "). Đã xác nhận " + fmt(confirmedBytes()) +
          " / " + fmt(file.size) + ".");
        const digest = await sha256(slice);
        let sent = false;
        for (let retry=0;retry<3&&!sent;retry++) {
          try {
            status("Đang gửi phần IPA " + (i+1) + "/" + chunks +
              " (" + fmt(slice.size) + ") tới máy chủ" +
              (retry?" · lần thử "+(retry+1):"")+
              ". Đã xác nhận " + fmt(confirmedBytes()) + " / " + fmt(file.size) + ".");
            await putChunk(job.job_id,i,slice,digest);
            sent=true;
          } catch(e) {
            // A lost response doesn't mean the server lost the bytes.
            const remote = await api("/api/v1/tools/ipa/sign-jobs/" + job.job_id);
            if ((remote.received || []).includes(i)) sent=true;
            else if (retry===2) throw e;
            else await new Promise(done => setTimeout(done, 500 * (retry+1)));
          }
        }
        received.add(i);
        // Count real acknowledged ranges; final chunk can be shorter than 2 MiB.
        const uploaded = confirmedBytes();
        showProgress(uploaded, file.size, "uploading");
        const pct = (100 * uploaded / Math.max(1, file.size)).toFixed(1);
        status("Đã nhận " + pct + "% · " + fmt(uploaded) + " / " + fmt(file.size) +
          " (phần " + (i+1) + "/" + chunks + "). Tệp vẫn đang được tải, đừng đóng Safari.");
        // Give Safari time to update the display and process touch/visibility events.
        await new Promise(done => setTimeout(done, 0));
      }
      showProgress(file.size, file.size, "signing");
      status("Đã nhận 100% dữ liệu IPA. Đang gửi chứng chỉ để bắt đầu ký; chữ ký chưa hoàn tất.");
      const args = new FormData();
      args.append("p12",p12);
      args.append("mobileprovision",profile);
      args.append("password",password);
      await api("/api/v1/tools/ipa/sign-jobs/" + job.job_id + "/start", {
        method:"POST",body:args,timeout:90000
      });
      form.elements.namedItem("password").value = "";
      status("Đã giao máy chủ xử lý ký. Có thể đóng website và mở lại để xem kết quả.");
      await watch(job.job_id, true);
    } catch(e) {
      status((e.message || "Không thể gửi IPA.") +
        " Các phần máy chủ đã xác nhận vẫn có thể tiếp tục nếu phiên tải còn tồn tại.", "error");
      if (/hết hạn|đăng nhập|không còn hợp lệ|quyền truy cập/.test(e.message?.toLowerCase()||"")) {
        action("Đăng nhập lại", () => document.querySelector('[data-view="account"]')?.click());
      }
      action("Tiếp tục tải IPA", () => run(form));
      if (/không còn|404|410|tạo phiên mới/.test(e.message?.toLowerCase()||"")) {
        // Expired jobs are not resumable; a fresh upload is required.
        clear();
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
      status("Đã lưu phiên tải IPA trước đó. Chọn lại đúng tệp IPA, P12, mobileprovision và bấm Tiếp tục tải IPA.");
      // A status request must never compete with an upload started by the user.
      if (token()) watch(pending.id);
    }
    form.elements.namedItem("ipa")?.addEventListener("change", () => {
      const file = form.elements.namedItem("ipa")?.files?.[0];
      const saved = getSaved();
      const submit = form.querySelector('button[type="submit"]');
      if (submit) submit.textContent = file && saved?.name === file.name && saved.size === file.size
        ? "Tiếp tục tải IPA và ký" : "Ký IPA và cài đặt";
    });
    document.addEventListener("visibilitychange",() => {
      if (document.visibilityState === "visible" && !busy && getSaved()?.id && token())
        watch(getSaved().id);
    });
  });
})();
