"use strict";
// ERI STORE IPA utilities. Inputs are sent only after the user chooses an action.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN_KEY = "eri_store_buyer_token";
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
    if (typeof result.download_path !== "string" ||
        !/^\/api\/v1\/tools\/ipa\/download\/[A-Za-z0-9_-]{32,64}$/.test(result.download_path)) return;
    const button = document.createElement("a");
    button.className = "btn primary";
    button.textContent = "Tải IPA đã xử lý";
    button.href = API + result.download_path;
    button.rel = "noreferrer";
    target.append(document.createElement("p"), button);
    const info = document.createElement("p");
    info.className = "small";
    info.textContent = "Liên kết tải một lần, hết hạn sau 15 phút. Tệp không được lưu trên tài khoản cửa hàng.";
    target.append(info);
  }
  function submitUpload(endpoint, form, result, {onlyInspect = false} = {}) {
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
      display(result, "Bạn cần đăng nhập tài khoản ERI STORE trước khi sử dụng công cụ.", "error");
      return;
    }
    const buttons = Array.from(form.querySelectorAll("button"));
    const inspectBtn = $("ipa-inspect-btn");
    if (onlyInspect && inspectBtn) buttons.push(inspectBtn);
    buttons.forEach(b => b.disabled = true);
    const payload = new FormData(form);
    if (onlyInspect) payload.delete("password");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", API + endpoint, true);
    xhr.timeout = 600000;
    xhr.setRequestHeader("Authorization", "Bearer " + token);
    xhr.upload.onprogress = ev => {
      const progress = ev.lengthComputable ? pretty(ev.loaded) + " / " + pretty(ev.total) : pretty(ev.loaded);
      display(result, "Đang gửi file qua HTTPS: " + progress);
    };
    xhr.upload.onload = () => display(result, "Đã gửi tệp; máy chủ đang kiểm tra và xử lý...");
    xhr.onload = () => {
      let response;
      try { response = JSON.parse(xhr.responseText || "{}"); } catch { response = {}; }
      if (xhr.status < 200 || xhr.status >= 300) {
        display(result, errorText(response) || "HTTP " + xhr.status, "error");
        return;
      }
      if (onlyInspect) {
        const encrypted = response.fairplay_encrypted;
        const fairplay = encrypted === true ? "Có mã hóa FairPlay." :
          encrypted === false ? "Không phát hiện mã hóa FairPlay trên tệp thực thi chính." :
          "Chưa xác định được tình trạng FairPlay từ tệp này.";
        const zipped = response.zip_password_protected ?
          "Lớp ZIP có mật khẩu, có thể mở bằng mật khẩu hợp lệ." : "Lớp ZIP không có mật khẩu.";
        const name = response.app?.name ? "Ứng dụng: " + response.app.name + ". " : "";
        display(result, name + zipped + " " + fairplay + " " + (response.note || ""), "success");
      } else {
        display(result, response.message || "Xử lý IPA thành công.", "success");
        makeDownload(response, result);
      }
    };
    xhr.onerror = () => display(result, "Không kết nối được máy chủ. Kiểm tra mạng và thử lại.", "error");
    xhr.ontimeout = () => display(result, "Quá thời gian xử lý trên máy chủ. Tệp lớn có thể cần dịch vụ xử lý chuyên dụng.", "error");
    xhr.onabort = () => display(result, "Yêu cầu đã bị hủy.", "error");
    xhr.onloadend = () => {
      buttons.forEach(b => b.disabled = false);
      if (!onlyInspect) {
        const pwd = form.querySelector('input[name="password"]');
        if (pwd) pwd.value = "";
      }
    };
    display(result, "Đang kết nối máy chủ...");
    xhr.send(payload);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const buttons = document.querySelectorAll("[data-ipa-tool]");
    const panels = {
      decrypt: $("ipa-decrypt-panel"),
      sign: $("ipa-sign-panel")
    };
    buttons.forEach(button => button.addEventListener("click", () => {
      const selection = button.dataset.ipaTool === "sign" ? "sign" : "decrypt";
      buttons.forEach(item => {
        const selected = item === button;
        item.classList.toggle("selected", selected);
        item.setAttribute("aria-selected", String(selected));
      });
      for (const [key, panel] of Object.entries(panels)) panel?.classList.toggle("hidden", selection !== key);
    }));
    const decryptForm = $("ipa-decrypt-form");
    const signForm = $("ipa-sign-form");
    const decryptStatus = $("ipa-decrypt-status");
    const signStatus = $("ipa-sign-status");
    if (!decryptForm || !signForm || !decryptStatus || !signStatus) return;
    $("ipa-inspect-btn")?.addEventListener("click", () => {
      submitUpload("/api/v1/tools/ipa/analyze", decryptForm, decryptStatus, {onlyInspect:true});
    });
    decryptForm.addEventListener("submit", ev => {
      ev.preventDefault();
      if (!decryptForm.reportValidity()) return;
      const pwd = decryptForm.elements.namedItem("password")?.value || "";
      if (!pwd) {
        display(decryptStatus, "Hãy kiểm tra IPA trước. Chỉ IPA ZIP có mật khẩu mới giải mã bằng mật khẩu ZIP.", "error");
        return;
      }
      submitUpload("/api/v1/tools/ipa/decrypt", decryptForm, decryptStatus);
    });
    signForm.addEventListener("submit", ev => {
      ev.preventDefault();
      if (!signForm.reportValidity()) return;
      for (const [name, suffix] of [["p12", ".p12"], ["mobileprovision", ".mobileprovision"]]) {
        const file = signForm.elements.namedItem(name)?.files?.[0];
        if (!file || !file.name.toLowerCase().endsWith(suffix)) {
          display(signStatus, "Vui lòng chọn đúng file " + suffix, "error");
          return;
        }
        if (file.size > 10 * 1024 * 1024) {
          display(signStatus, "Chứng chỉ hoặc mobileprovision quá 10 MB.", "error");
          return;
        }
      }
      submitUpload("/api/v1/tools/ipa/sign", signForm, signStatus);
    });
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
