"use strict";
// ERI STORE IPA utilities. Inputs are sent only after the user chooses an action.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN_KEY = "eri_store_buyer_token";
  const APPSTORE_DRAFT = "eri_store_appstore_draft_v1";
  const MAX_IPA = 512 * 1024 * 1024;
  const $ = id => document.getElementById(id);
  const readToken = () => sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || "";
  const pretty = bytes => (bytes / 1048576).toFixed(1) + " MB";
  const errorText = data => {
    if (typeof data?.detail === "string") return data.detail.slice(0, 400);
    if (Array.isArray(data?.detail)) return "Dữ liệu không hợp lệ. Vui lòng chọn đúng tệp và mật khẩu.";
    return "Không thể thực hiện. Vui lòng thử lại.";
  };
  const display = (element, message, type = "info") => {
    element.replaceChildren(document.createTextNode(message));
    element.dataset.state = type;
  };
  function makeDownload(result, target) {
    const path = String(result.download_path || "");
    const isSigned = /^\/api\/v1\/tools\/ipa\/install\/[A-Za-z0-9_-]{32,64}\/app\.ipa$/.test(path);
    const isDecrypted = /^\/api\/v1\/tools\/ipa\/download\/[A-Za-z0-9_-]{32,64}$/.test(path);
    if (!isSigned && !isDecrypted) return;
    const controls = document.createElement("div");
    controls.className = "ipa-tool-actions";
    const download = document.createElement("a");
    download.className = "btn outline";
    download.textContent = isSigned ? "Tải IPA đã ký" : "Tải IPA đã xử lý";
    download.href = API + path;
    download.rel = "noopener noreferrer";
    controls.append(download);
    if (isSigned) {
      const token = path.split("/")[6];
      const manifest = API + "/api/v1/tools/ipa/install/" + token + "/manifest.plist";
      const launch = document.createElement("a");
      launch.className = "btn primary";
      launch.href = "itms-services://?action=download-manifest&url=" + encodeURIComponent(manifest);
      launch.textContent = "Cài trực tiếp lên iPhone";
      launch.setAttribute("aria-label", "Cài IPA đã ký lên iPhone bằng Safari");
      controls.append(launch);
    }
    target.append(controls);
    const info = document.createElement("p");
    info.className = "small";
    info.textContent = isSigned
      ? "Mở bằng Safari trên iPhone. iOS chỉ cài khi chứng chỉ và provisioning profile hợp lệ cho thiết bị. Liên kết HTTPS có hiệu lực 60 phút, đừng đóng trang trước khi hoàn tất."
      : "Liên kết tải một lần, hết hạn sau 15 phút. Tệp không được lưu trên tài khoản cửa hàng.";
    target.append(info);
  }
  function submitUpload(endpoint, form, result) {
    const ipa = form.querySelector('input[name="ipa"]')?.files?.[0];
    if (!ipa || !ipa.name.toLowerCase().endsWith(".ipa")) {
      display(result, "Hãy chọn tệp .ipa hợp lệ.", "error");
      return;
    }
    if (ipa.size === 0 || ipa.size > MAX_IPA) {
      display(result, "IPA phải có dung lượng từ 1 byte đến 512 MB.", "error");
      return;
    }
    const token = readToken();
    if (!token) {
      display(result, "Bạn cần đăng nhập ERI STORE. Các file đang chọn vẫn được giữ khi bạn chuyển sang Tài khoản mà không tải lại trang.", "error");
      const account = document.createElement("button");
      account.type = "button";
      account.className = "btn outline";
      account.textContent = "Đi tới Đăng nhập";
      account.addEventListener("click", () => document.querySelector('[data-view="account"]')?.click());
      result.append(document.createElement("p"), account);
      return;
    }
    const buttons = Array.from(form.querySelectorAll("button"));
    buttons.forEach(b => b.disabled = true);
    const payload = new FormData(form);
    // Compatibility for a legacy HTTPS upload. Real-time bytes are browser
    // transmission progress, not confirmation of server-side signing.
    const updateLegacyProgress = (loaded, total, stage) => {
      const panel = $("ipa-sign-progress");
      const bar = $("ipa-sign-progress-bar");
      const pct = $("ipa-sign-progress-percent");
      const label = $("ipa-sign-progress-label");
      const note = $("ipa-sign-progress-stage");
      if (!panel || !bar || !pct || !label || !note) return;
      const value = total > 0
        ? Math.round(Math.max(0, Math.min(1, loaded / total)) * 1000) / 10 : 0;
      panel.classList.remove("hidden");
      bar.value = value;
      pct.textContent = value.toFixed(1) + "%";
      label.textContent = stage === "error" ? "Tải lên hoặc ký IPA thất bại" :
        stage === "complete" ? "Đã ký IPA" :
        stage === "processing" ? "Đang ký IPA trên máy chủ" : "Đang gửi IPA qua HTTPS";
      note.textContent = stage === "uploading"
        ? "Đã truyền " + pretty(loaded) + " / " + pretty(total) +
          " qua HTTPS; chờ máy chủ xác nhận."
        : stage === "processing"
          ? "Đã truyền đủ file; máy chủ đang kiểm tra và ký số."
          : stage === "error" ? "Tiến trình đã dừng. Có thể chọn lại và thử tiếp."
          : "File đã ký sẵn sàng để tải.";
    };
    updateLegacyProgress(0, ipa.size, "uploading");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API + endpoint, true);
    xhr.timeout = 600000;
    xhr.setRequestHeader("Authorization", "Bearer " + token);
    let uploadedBytes = 0;
    let uploadTotal = ipa.size;
    xhr.upload.onprogress = ev => {
      const total = ev.lengthComputable ? ev.total : ipa.size;
      uploadedBytes = ev.loaded;
      uploadTotal = total;
      updateLegacyProgress(ev.loaded, total, "uploading");
      display(result, "Đang gửi file qua HTTPS: " + pretty(ev.loaded) + " / " + pretty(total));
    };
    xhr.upload.onload = () => {
      updateLegacyProgress(ipa.size, ipa.size, "processing");
      display(result, "Đã gửi file; máy chủ đang kiểm tra và ký số...");
    };
    xhr.onload = () => {
      let response;
      try { response = JSON.parse(xhr.responseText || "{}"); } catch { response = {}; }
      if (xhr.status < 200 || xhr.status >= 300) {
        updateLegacyProgress(uploadedBytes, uploadTotal, "error");
        display(result, xhr.status === 401
          ? "Phiên đăng nhập đã hết hạn. Bạn hãy đăng nhập lại rồi bấm Ký; tệp đã chọn sẽ không bị xóa nếu không tải lại trang."
          : errorText(response) || "HTTP " + xhr.status, "error");
        if (xhr.status === 401) {
          const go = document.createElement("button");
          go.type = "button";
          go.className = "btn outline";
          go.textContent = "Đăng nhập lại";
          go.addEventListener("click", () => document.querySelector('[data-view="account"]')?.click());
          result.append(document.createElement("p"), go);
        }
        return;
      }
      updateLegacyProgress(ipa.size, ipa.size, "complete");
      display(result, response.message || "IPA đã được ký.", "success");
      makeDownload(response, result);
      const pwd = form.querySelector('input[name="password"]');
      if (pwd) pwd.value = "";
    };
    xhr.onerror = () => {
      updateLegacyProgress(uploadedBytes, uploadTotal, "error");
      display(result, "Không kết nối được máy chủ. Kiểm tra mạng và thử lại.", "error");
    };
    xhr.ontimeout = () => {
      updateLegacyProgress(uploadedBytes, uploadTotal, "error");
      display(result, "Quá thời gian xử lý trên máy chủ. Hãy dùng tính năng upload từng phần.", "error");
    };
    xhr.onabort = () => {
      updateLegacyProgress(uploadedBytes, uploadTotal, "error");
      display(result, "Yêu cầu đã bị hủy.", "error");
    };
    xhr.onloadend = () => { buttons.forEach(b => b.disabled = false); };
    display(result, "Đang kết nối máy chủ...");
    xhr.send(payload);
  }

  document.addEventListener("DOMContentLoaded", () => {
    // The two tools are first-level store tabs, controlled by app.js.
    const appStoreForm = $("ipa-appstore-form");
    const appStoreResult = $("ipa-appstore-result");
    if (appStoreForm && appStoreResult) {
      const urlField = appStoreForm.elements.namedItem("appstore_url");
      try { if (urlField) urlField.value = localStorage.getItem(APPSTORE_DRAFT) || ""; } catch {}
      urlField?.addEventListener("input", () => {
        try { localStorage.setItem(APPSTORE_DRAFT, urlField.value.slice(0, 500)); } catch {}
      });
      appStoreForm.addEventListener("submit", async ev => {
        ev.preventDefault();
        const link = String(appStoreForm.elements.namedItem("appstore_url")?.value || "").trim();
        let parsed;
        try { parsed = new URL(link); } catch { parsed = null; }
        if (!parsed || parsed.protocol !== "https:" || parsed.hostname !== "apps.apple.com") {
          display(appStoreResult, "Chỉ chấp nhận link HTTPS của apps.apple.com.", "error");
          return;
        }
        const action = appStoreForm.querySelector('button[type="submit"]');
        action.disabled = true;
        display(appStoreResult, "Đang tra cứu thông tin ứng dụng trên App Store...");
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 20000);
        try {
          const reply = await fetch(API + "/api/v1/tools/ipa/appstore?url=" + encodeURIComponent(link),
                                    {cache:"no-store",signal:controller.signal});
          let data;
          try { data = await reply.json(); } catch { data = {}; }
          if (!reply.ok) {
            display(appStoreResult, errorText(data), "error");
            return;
          }
          const name = String(data.name || "Ứng dụng");
          const developer = String(data.developer || "Không rõ nhà phát triển");
          const bundle = String(data.bundle_id || "Không rõ");
          const version = String(data.version || "Không rõ");
          display(appStoreResult, name + " · " + developer +
            "\nBundle ID: " + bundle + " · Phiên bản: " + version +
            "\n" + (data.message || "Link App Store không phải tệp IPA có thể tải về."), "success");
          const actions = document.createElement("div");
          actions.className = "ipa-tool-actions";
          // The Apple Lookup API is metadata-only. Cross-check our actual,
          // authorized FREE inventory before offering a downloadable IPA.
          // Never fabricate a file URL based on an App Store ID.
          let existingIPA = null;
          try {
            // /mods is capped at 100 mixed APK/IPA products and can falsely
            // report "no IPA" even when a valid file exists. Search the ERI IPA
            // vault by exact Bundle ID first, then retain old-API compatibility.
            let inventoryReply = await fetch(
              API + "/api/v1/tools/ipa/decrypt/vault?q=" +
                encodeURIComponent(bundle) + "&limit=50&offset=0",
              {cache: "no-store", signal: controller.signal}
            );
            const modernVault = inventoryReply.ok;
            if (!modernVault && inventoryReply.status === 404) {
              inventoryReply = await fetch(API + "/api/v1/mods", {
                cache: "no-store", signal: controller.signal
              });
            }
            if (inventoryReply.ok) {
              const inventory = await inventoryReply.json();
              const candidates = (Array.isArray(inventory.items) ? inventory.items : [])
                .filter(item => String(item.bundle_id || "").toLowerCase() === bundle.toLowerCase()
                  && (modernVault || (item.platform === "ipa" && Number(item.price_vnd) === 0))
                  && /^\/api\/v1\/mods\/[1-9][0-9]*\/download$/.test(item.download_path || ""));
              existingIPA = candidates.find(item => item.version === version) || candidates[0] || null;
            }
          } catch (_) { /* Apple metadata still renders when inventory is offline. */ }

          if (existingIPA) {
            const download = document.createElement("a");
            download.className = "btn primary";
            download.href = API + existingIPA.download_path;
            download.rel = "noopener noreferrer";
            download.textContent = "Tải IPA từ kho ERI (v" + existingIPA.version + ")";
            actions.append(download);
            if (String(existingIPA.version) !== version) {
              const note = document.createElement("p");
              note.className = "small";
              note.textContent = "Kho ERI hiện có bản v" + existingIPA.version
                + ", khác bản App Store v" + version + ".";
              appStoreResult.append(note);
            }
          } else {
            const note = document.createElement("p");
            note.className = "small";
            note.textContent = "Kho ERI chưa có IPA hợp lệ cho " + name + " (v" + version +
              "). Bạn có thể gửi yêu cầu xử lý và theo dõi trạng thái. " +
              "Link App Store không chứa IPA đã giải mã FairPlay.";
            appStoreResult.append(note);
          }

          const decryptTab = document.querySelector('[data-kind="decrypt"]');
          if (decryptTab && $("eri-decrypt-query")) {
            const openDecrypt = createRequest => {
              const query = $("eri-decrypt-query");
              query.value = link;
              query.dispatchEvent(new Event("input", {bubbles: true}));
              decryptTab.click();
              if (createRequest) $("eri-decrypt-direct-request")?.click();
              else $("eri-decrypt-search-form")?.requestSubmit();
            };
            if (!existingIPA) {
              const request = document.createElement("button");
              request.type = "button";
              request.className = "btn primary";
              request.textContent = "Gửi yêu cầu xuất IPA";
              request.addEventListener("click", () => openDecrypt(true));
              actions.append(request);
            }
            const browse = document.createElement("button");
            browse.type = "button";
            browse.className = "btn outline";
            browse.textContent = "Kho IPA / chọn phiên bản";
            browse.addEventListener("click", () => openDecrypt(false));
            actions.append(browse);
          }
          if (typeof data.app_store_url === "string") {
            try {
              const destination = new URL(data.app_store_url);
              if (destination.protocol === "https:" && destination.hostname === "apps.apple.com") {
                const install = document.createElement("a");
                install.className = "btn primary";
                install.href = destination.href;
                install.rel = "noopener noreferrer";
                install.target = "_blank";
                install.textContent = "Cài trực tiếp qua App Store";
                actions.prepend(install);
              }
            } catch { /* No unsafe destination. */ }
          }
          appStoreResult.append(actions);
        } catch (error) {
          display(appStoreResult, error.name === "AbortError"
            ? "App Store phản hồi quá lâu. Hãy thử lại."
            : "Không tra cứu được App Store. Kiểm tra kết nối mạng.", "error");
        } finally {
          clearTimeout(timeout);
          action.disabled = false;
        }
      });
    }
    const signForm = $("ipa-sign-form");
    const signStatus = $("ipa-sign-status");
    if (!signForm || !signStatus) return;
    // Large file signing is handled by sign-jobs.js with resumable chunks.
    fetch(API + "/api/v1/tools/ipa/status", {cache:"no-store"})
      .then(r => r.ok ? r.json() : null)
      .then(info => {
        if (info && info.signing_available === false) {
          display(signStatus, "Bộ ký IPA trên máy chủ chưa sẵn sàng. Không gửi chứng chỉ khi hệ thống chưa bật.", "error");
          signForm.querySelector('button[type="submit"]').disabled = true;
        }
      }).catch(() => { /* Do not misreport signing availability based on a transient timeout. */ });
  });
})();
