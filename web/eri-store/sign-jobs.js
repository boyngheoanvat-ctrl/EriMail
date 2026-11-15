"use strict";
// Single-request IPA signing. Upload IPA, P12 and mobileprovision together.
// The browser can report bytes transmitted; only a successful API response
// confirms that ERI received, validated and signed the IPA.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN_KEY = "eri_store_buyer_token";
  const MAX_IPA = 512 * 1024 * 1024;
  const $ = id => document.getElementById(id);
  const credential = () =>
    sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || "";
  const fmt = bytes => (bytes / 1048576).toFixed(1) + " MB";
  let busy = false;
  let maxTransmitted = 0;
  let signedOk = false;

  function status(message, level = "info") {
    const node = $("ipa-sign-status");
    if (!node) return;
    node.replaceChildren();
    if(message)node.append(document.createTextNode(message));
    node.dataset.state = level;
  }

  function action(text, callback) {
    const target = $("ipa-sign-status");
    if (!target) return;
    const control = document.createElement("button");
    control.type = "button";
    control.className = "btn outline";
    control.textContent = text;
    control.addEventListener("click", callback);
    target.append(document.createElement("p"), control);
  }

  function progress(bytes, total, stage) {
    const pane = $("ipa-sign-progress");
    const bar = $("ipa-sign-progress-bar");
    const percent = $("ipa-sign-progress-percent");
    const label = $("ipa-sign-progress-label");
    if (!pane || !bar || !percent || !label) return;
    // Never erase visible upload progress on a network or auth failure.
    // These are browser-transmitted bytes, NOT bytes durably saved on Render.
    if (stage === "preparing") maxTransmitted = 0;
    else if (stage === "error") bytes = maxTransmitted;
    else maxTransmitted = Math.max(maxTransmitted, bytes);
    const value = Math.min(100, Math.max(0,
      Math.round(1000 * bytes / Math.max(total, 1)) / 10));
    pane.classList.remove("hidden");
    bar.value = value;
    percent.textContent = value.toFixed(1) + "%";
    label.textContent =
      stage === "complete" ? "Đã ký IPA" :
      stage === "signing" ? "Đang ký IPA" :
      stage === "error" ? "Lỗi ký IPA" : "Đang tải IPA";
  }

  function renderLinks(result) {
    const link = result && result.download_path;
    if (typeof link !== "string" ||
        !/^\/api\/v1\/tools\/ipa\/install\/[A-Za-z0-9_-]{32,64}\/app\.ipa$/.test(link)) {
      throw new Error("Máy chủ chưa cung cấp tệp IPA đã ký hợp lệ.");
    }
    const tokenId = link.split("/")[6];
    const target = $("ipa-sign-status");
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
    download.rel = "noopener noreferrer";
    download.textContent = "Tải IPA đã ký";
    row.append(install, download);
    target.append(row);
  }

  function serverError(response, httpStatus) {
    if (httpStatus === 401) return "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại và tải IPA từ đầu.";
    if (httpStatus === 413) return "IPA vượt giới hạn tải lên của máy chủ.";
    if (httpStatus === 429) return "Máy chủ đang giới hạn yêu cầu. Hãy thử lại.";
    if (typeof response?.detail === "string") return response.detail;
    if (httpStatus >= 500) return "Máy chủ bận hoặc giới hạn thời gian xử lý yêu cầu lớn.";
    return "Máy chủ không hoàn thành yêu cầu (HTTP " + httpStatus + ").";
  }

  async function uploadWholeIPA(form) {
    if (busy) return;
    const ipa = form.elements.namedItem("ipa")?.files?.[0];
    const p12 = form.elements.namedItem("p12")?.files?.[0];
    const profile = form.elements.namedItem("mobileprovision")?.files?.[0];
    const password = form.elements.namedItem("password")?.value;
    if (!ipa || !p12 || !profile || !password) {
      status("Chọn đủ IPA, P12, mobileprovision và mật khẩu P12.", "error");
      return;
    }
    if (!ipa.name.toLowerCase().endsWith(".ipa") || ipa.size < 1 || ipa.size > MAX_IPA) {
      status("Tệp IPA phải hợp lệ và không vượt 512 MB.", "error");
      return;
    }
    const auth = credential();
    if (!auth) {
      status("Vui lòng đăng nhập trước khi ký IPA.", "error");
      window.dispatchEvent(new Event("eri-store:auth-expired"));
      return;
    }
    busy = true;
    signedOk = false;
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = true;
    // Authenticate via a tiny request BEFORE sending the large IPA. A Render
    // restart may invalidate a session stored in its ephemeral SQLite DB.
    const controller = new AbortController();
    const preflightTimer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(API + "/api/v1/auth/me", {
        method: "GET",
        cache: "no-store",
        credentials: "omit",
        headers: {"Authorization": "Bearer " + auth},
        signal: controller.signal
      });
      if (response.status === 401 || response.status === 403) {
        status("Phiên đăng nhập hết hạn. Đăng nhập lại rồi bấm ký; IPA đã chọn vẫn giữ.", "error");
        window.dispatchEvent(new Event("eri-store:auth-expired"));
        return;
      }
      if (!response.ok) throw new Error("Không xác minh được phiên đăng nhập (HTTP " + response.status + ").");
    } catch (error) {
      const timeout = error?.name === "AbortError";
      status(timeout ? "Kết nối máy chủ quá lâu. Thử lại, IPA đã chọn vẫn giữ." :
        "Không thể kết nối máy chủ để kiểm tra đăng nhập. Thử lại.", "error");
      return;
    } finally {
      clearTimeout(preflightTimer);
      // Only release the form here when verification failed; successful
      // upload takes ownership until XHR onloadend.
      if ($("ipa-sign-status")?.dataset.state === "error") {
        busy = false;
        if (submit) submit.disabled = false;
      }
    }
    progress(0, ipa.size, "preparing");
    status("");
    const payload = new FormData();
    payload.append("ipa", ipa, ipa.name);
    payload.append("p12", p12, p12.name);
    payload.append("mobileprovision", profile, profile.name);
    payload.append("password", password);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", API + "/api/v1/tools/ipa/sign", true);
    // Browser timeout is disabled for large files; the reverse proxy may still
    // enforce its own connection/request time limits.
    xhr.timeout = 0;
    xhr.setRequestHeader("Authorization", "Bearer " + auth);
    xhr.upload.onprogress = event => {
      if (!event.lengthComputable || !event.total) {
        status("");
        return;
      }
      // Multipart totals contain P12/profile overhead; display an IPA-scaled
      // estimate rather than claiming the server has acknowledged those bytes.
      const portion = Math.min(1, event.loaded / event.total);
      const approximate = Math.round(portion * ipa.size);
      progress(approximate, ipa.size, "uploading");
      status("");
    };
    xhr.upload.onload = () => {
      progress(ipa.size, ipa.size, "signing");
      status("");
    };
    xhr.onload = () => {
      let body = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch {}
      if (xhr.status < 200 || xhr.status >= 300) {
        progress(maxTransmitted, ipa.size, "error");
        status(serverError(body, xhr.status) + " IPA đã chọn vẫn giữ để thử lại.", "error");
        if (xhr.status === 401 || xhr.status === 403)
          window.dispatchEvent(new Event("eri-store:auth-expired"));
        return;
      }
      try {
        renderLinksAfterSuccess(body);
      } catch (error) {
        progress(maxTransmitted, ipa.size, "error");
        status(error.message || "Không có kết quả ký hợp lệ.", "error");
      }
    };
    function renderLinksAfterSuccess(body) {
      const result = body && body.download_path;
      if (typeof result !== "string" ||
          !/^\/api\/v1\/tools\/ipa\/install\/[A-Za-z0-9_-]{32,64}\/app\.ipa$/.test(result)) {
        throw new Error("Máy chủ chưa trả về đường dẫn IPA đã ký.");
      }
      signedOk = true;
      progress(ipa.size, ipa.size, "complete");
      status("Ký IPA thành công", "success");
      renderLinks(body);
    }
    xhr.onerror = () => {
      progress(maxTransmitted, ipa.size, "error");
      status("Mất kết nối. IPA đã chọn vẫn giữ; thử lại sẽ gửi từ đầu.", "error");
    };
    xhr.onabort = () => {
      progress(maxTransmitted, ipa.size, "error");
      status("Yêu cầu đã hủy. IPA vẫn được chọn, có thể thử lại.", "error");
    };
    xhr.ontimeout = () => {
      progress(maxTransmitted, ipa.size, "error");
      status("Máy chủ hết thời gian xử lý. IPA đã chọn vẫn giữ để thử lại.", "error");
    };
    xhr.onloadend = () => {
      busy = false;
      if (submit) submit.disabled = false;
      const pwd = form.elements.namedItem("password");
      if (pwd && signedOk) pwd.value = "";
    };
    try { xhr.send(payload); }
    catch (error) {
      busy = false;
      if (submit) submit.disabled = false;
      status("Không khởi tạo được yêu cầu tải IPA: " + error.message, "error");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    const form = $("ipa-sign-form");
    if (!form) return;
    form.addEventListener("submit", event => {
      event.preventDefault();
      uploadWholeIPA(form);
    });
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.textContent = "Tải toàn bộ IPA một lần và ký";
  });
})();
