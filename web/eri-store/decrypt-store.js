"use strict";
// ERI STORE App Store discovery and authorized IPA vault.
// Never claim to decrypt Apple's FairPlay DRM from public metadata.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN = "eri_store_buyer_token";
  const QUERY_DRAFT = "eri.decrypt.query.v1";
  const REGION_DRAFT = "eri.decrypt.region.v1";
  const ACTIVE_JOB = "eri.decrypt.job.v1";
  const $ = id => document.getElementById(id);
  const getToken = () => sessionStorage.getItem(TOKEN) || localStorage.getItem(TOKEN) || "";
  let vaultOffset = 0;
  const vaultLimit = 24;
  let vaultTotal = 0;
  let activeJob = null;
  let pollInFlight = false;
  let searchSerial = 0;
  let vaultSerial = 0;

  function text(id, value) {
    const node = $(id);
    if (node) node.textContent = String(value ?? "");
  }
  function notice(value, state = "info") {
    const node = $("eri-decrypt-notice");
    if (!node) return;
    node.textContent = value;
    node.dataset.state = state;
  }
  function card(tag, className, value) {
    const node = document.createElement(tag);
    node.className = className;
    if (value !== undefined) node.textContent = String(value);
    return node;
  }
  function button(value, action, primary = false) {
    const node = card("button", "btn " + (primary ? "primary" : "outline"), value);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function link(value, href, primary = false) {
    const node = card("a", "btn " + (primary ? "primary" : "outline"), value);
    node.href = href;
    node.rel = "noopener noreferrer";
    return node;
  }
  function apiUrl(path) {
    if (!/^\/api\/v1\/mods\/[1-9][0-9]*\/download$/.test(path || "")) return null;
    return API + path;
  }
  function safeStoreLink(url) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "https:" && parsed.hostname === "apps.apple.com") return parsed.href;
    } catch {}
    return null;
  }
  function pretty(bytes) {
    const size = Number(bytes);
    if (!Number.isFinite(size) || size < 0) return "—";
    return size > 1e9 ? (size / 1e9).toFixed(2) + " GB" :
      (size / 1e6).toFixed(1) + " MB";
  }
  function errorMessage(result, code) {
    if (code === 401) return "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại trong tab Tài khoản rồi quay lại.";
    if (typeof result?.detail === "string") return result.detail;
    if (Array.isArray(result?.detail)) return "Dữ liệu nhập không hợp lệ.";
    return code === 503 ? "Máy chủ hoặc Apple App Store chưa phản hồi. Thử lại sau." :
      "Máy chủ trả HTTP " + code;
  }
  async function request(path, options = {}) {
    const headers = {"Accept": "application/json"};
    if (options.body) headers["Content-Type"] = "application/json";
    if (options.auth) {
      const token = getToken();
      if (!token) throw new Error("Bạn cần đăng nhập ERI STORE để lưu yêu cầu IPA.");
      headers.Authorization = "Bearer " + token;
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 18000);
    try {
      const result = await fetch(API + path, {
        method: options.body ? "POST" : "GET",
        headers, body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal, cache: "no-store"
      });
      let data = {};
      try { data = await result.json(); } catch {}
      if (!result.ok) throw new Error(errorMessage(data, result.status));
      return data;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("Kết nối quá thời gian, hãy thử lại.");
      if (error instanceof TypeError) throw new Error("Không kết nối được máy chủ ERI API. Kiểm tra mạng hoặc dịch vụ.");
      throw error;
    } finally { clearTimeout(timeout); }
  }
  function installActions(parent, item) {
    const url = apiUrl(item?.download_path);
    if (!url) return;
    const actions = card("div", "eri-decrypt-actions");
    actions.append(link("Tải IPA " + (item.size_bytes ? "(" + pretty(item.size_bytes) + ")" : ""), url, true));
    const enc = encodeURIComponent(url);
    actions.append(link("Mở bằng ESign", "esign://install?url=" + enc));
    actions.append(link("TrollStore", "apple-magnifier://install?url=" + enc));
    actions.append(link("Feather", "feather://install?url=" + enc));
    parent.append(actions);
    const foot = card("p", "small", "IPA có thể cần ký bằng chứng chỉ hợp lệ trước khi iOS cho phép cài. ESign / TrollStore / Feather phải có sẵn trên thiết bị.");
    parent.append(foot);
  }
  function icon(parent, url) {
    if (typeof url !== "string") return;
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== "https:" || !parsed.hostname.endsWith(".mzstatic.com")) return;
      const image = document.createElement("img");
      image.src = parsed.href;
      image.alt = "";
      image.loading = "lazy";
      image.width = 52;
      image.height = 52;
      image.className = "eri-decrypt-app-icon";
      parent.append(image);
    } catch {}
  }
  function searchResult(item) {
    const root = card("article", "eri-decrypt-card");
    const top = card("div", "eri-decrypt-card-top");
    icon(top, item.icon_url);
    const details = card("div", "eri-decrypt-details");
    details.append(card("strong", "", item.name || "Ứng dụng"));
    details.append(card("span", "", item.developer || item.bundle_id || ""));
    details.append(card("small", "", (item.bundle_id || "Bundle ID chưa rõ") +
      " · v" + (item.version || "—") + " · " + (item.country || "VN")));
    top.append(details);
    root.append(top);
    const actions = card("div", "eri-decrypt-actions");
    if (item.cached?.download_path) {
      root.append(card("div", "eri-decrypt-available", "Đã có IPA được phép phân phối trong kho ERI"));
      installActions(root, item.cached);
    } else {
      root.append(card("div", "eri-decrypt-unavailable",
        "Chưa có IPA bản này trong kho ERI. Link App Store chỉ chứa thông tin ứng dụng."));
      actions.append(button("Yêu cầu IPA", () => createJob(item.app_store_url || String(item.app_id), item.country, item.version), true));
    }
    if (Number(item.app_id) > 0)
      actions.append(button("Chọn phiên bản", () => loadVersions(item), false));
    const store = safeStoreLink(item.app_store_url);
    if (store) actions.append(link("App Store", store));
    root.append(actions);
    return root;
  }
  async function search(q, country) {
    const serial = ++searchSerial;
    const buttonNode = $("eri-decrypt-search-button");
    if (buttonNode) buttonNode.disabled = true;
    notice("Đang tìm ứng dụng bằng Apple Search API...");
    const section = $("eri-decrypt-result-section");
    section?.classList.remove("hidden");
    const grid = $("eri-decrypt-results");
    if (grid) grid.replaceChildren();
    try {
      const data = await request("/api/v1/tools/ipa/decrypt/search?" +
        new URLSearchParams({q, country, limit:"15"}));
      if (serial !== searchSerial) return;
      if (!data.items?.length) {
        notice("Không tìm thấy ứng dụng phù hợp ở khu vực đã chọn.", "error");
        return;
      }
      notice("Đã tìm thấy " + data.items.length + " ứng dụng. " +
        (String(data.source || "").startsWith("ERI local vault")
          ? "Đang hiển thị kho ERI vì Apple tạm thời không phản hồi."
          : "File tải chỉ hiện khi thực sự có trong kho ERI."), "success");
      data.items.forEach(item => grid?.append(searchResult(item)));
      section?.scrollIntoView({behavior:"smooth",block:"nearest"});
    } catch (error) {
      if (serial === searchSerial) notice(error.message, "error");
    } finally { if (buttonNode) buttonNode.disabled = false; }
  }
  async function loadVersions(item) {
    const section = $("eri-decrypt-versions-section");
    const grid = $("eri-decrypt-versions-grid");
    section?.classList.remove("hidden");
    if (grid) grid.replaceChildren();
    text("eri-decrypt-versions-title", "Phiên bản: " + item.name);
    notice("Đang kiểm tra những phiên bản có thể xác nhận...");
    try {
      const data = await request("/api/v1/tools/ipa/decrypt/versions?" +
        new URLSearchParams({target:item.app_store_url || String(item.app_id), country:item.country.toLowerCase()}));
      const entries = Array.isArray(data.versions) ? data.versions : [];
      for (const entry of entries) {
        const node = card("article","eri-decrypt-card");
        node.append(card("strong", "", "Phiên bản " + (entry.version || "—")));
        node.append(card("p", "small", entry.source === "ERI authorized library"
          ? "Đã có file ở kho ERI." : "Apple chỉ xác nhận thông tin phiên bản này, không cung cấp IPA."));
        if (entry.cached?.download_path) installActions(node, entry.cached);
        else node.append(button("Yêu cầu IPA bản này", () =>
          createJob(item.app_store_url || String(item.app_id), item.country, entry.version), true));
        grid?.append(node);
      }
      notice(data.notice || "Đã tải danh sách phiên bản.", "success");
      section?.scrollIntoView({behavior:"smooth",block:"nearest"});
    } catch(error) { notice(error.message, "error"); }
  }
  async function loadVault() {
    const serial = ++vaultSerial;
    const q = String($("eri-decrypt-vault-filter")?.value || "").trim().slice(0,100);
    const sort = $("eri-decrypt-vault-sort")?.value || "newest";
    const grid = $("eri-decrypt-vault-grid");
    if (grid) grid.replaceChildren(card("p", "small", "Đang tải kho IPA..."));
    try {
      const result = await request("/api/v1/tools/ipa/decrypt/vault?" +
        new URLSearchParams({q,sort,limit:String(vaultLimit),offset:String(vaultOffset)}));
      if (serial !== vaultSerial) return;
      vaultTotal = Number(result.total) || 0;
      text("eri-decrypt-vault-count", vaultTotal + " IPA");
      if (grid) grid.replaceChildren();
      if (!result.items?.length) grid?.append(card("p", "small",
        "Chưa có IPA được cấp quyền phân phối và còn file trên máy chủ phù hợp bộ lọc này."));
      else for(const item of result.items) {
        const node = card("article","eri-decrypt-card");
        node.append(card("strong","",item.name || "IPA"));
        node.append(card("p","small",(item.bundle_id || "") + " · v" +
          (item.version || "—") + " · " + pretty(item.size_bytes)));
        installActions(node,item);
        grid?.append(node);
      }
      const pages = Math.max(1,Math.ceil(vaultTotal / vaultLimit));
      const page = Math.min(pages,Math.floor(vaultOffset/vaultLimit)+1);
      text("eri-decrypt-page-label","Trang " + page + " / " + pages);
      $("eri-decrypt-previous").disabled = page <= 1;
      $("eri-decrypt-next").disabled = page >= pages;
    } catch(error) {
      if (serial === vaultSerial) {
        if(grid) grid.replaceChildren(card("p","small",error.message));
        text("eri-decrypt-vault-count","Chưa kết nối");
      }
    }
  }
  async function createJob(target, country, version) {
    if (!getToken()) {
      notice("Đăng nhập ERI STORE trong tab Tài khoản để gửi yêu cầu IPA.", "error");
      const msg = $("eri-decrypt-notice");
      msg?.append(document.createElement("p"),
        button("Đăng nhập", () => document.querySelector('[data-view="account"]')?.click(), true));
      return;
    }
    notice("Đang lưu yêu cầu IPA...");
    try {
      const job = await request("/api/v1/tools/ipa/decrypt/requests", {
        auth:true, body:{target,country:country?.toLowerCase()||"auto",version:version||""}
      });
      activeJob = job.job_id;
      try {localStorage.setItem(ACTIVE_JOB,activeJob);} catch {}
      renderJob(job);
    } catch(error) { notice(error.message, "error"); }
  }
  function renderJob(job) {
    const section = $("eri-decrypt-request-section");
    section?.classList.remove("hidden");
    const pct = job.state === "completed" && job.result?.download_path ? 100 : 0;
    text("eri-decrypt-job-pct",pct + "%");
    const bar = $("eri-decrypt-job-progress");
    if (bar) bar.value = pct;
    text("eri-decrypt-job-stage",(job.name || "Ứng dụng") +
      " · v" + (job.version || "—") + "\n" + (job.message || ""));
    const controls = $("eri-decrypt-job-actions");
    if (controls) controls.replaceChildren();
    if (pct === 100) {
      if (controls) installActions(controls,job.result);
      notice("Kho ERI đã có file thật cho yêu cầu này.", "success");
    } else {
      if (controls) controls.append(button("Kiểm tra lại",refreshJob));
      notice("Yêu cầu đã lưu. Bạn có thể đóng trang và mở lại để xem trạng thái.", "info");
    }
  }
  async function refreshJob() {
    if (pollInFlight || !activeJob || !getToken()) return;
    pollInFlight=true;
    try {
      const result = await request("/api/v1/tools/ipa/decrypt/requests/" + activeJob,{auth:true});
      renderJob(result);
    } catch (error) {
      if (/không tìm thấy yêu cầu|404/.test(error.message)) {
        activeJob = null;
        try{localStorage.removeItem(ACTIVE_JOB);}catch{}
      } else {
        notice(error.message, "error");
      }
    } finally { pollInFlight=false; }
  }
  function init() {
    const form = $("eri-decrypt-search-form");
    if (!form) return;
    try {
      $("eri-decrypt-query").value = localStorage.getItem(QUERY_DRAFT) || "";
      const saved = localStorage.getItem(REGION_DRAFT);
      if ([...$("eri-decrypt-region").options].some(o=>o.value===saved))
        $("eri-decrypt-region").value = saved;
      activeJob = localStorage.getItem(ACTIVE_JOB);
    } catch {}
    $("eri-decrypt-query").addEventListener("input", () => {
      try {localStorage.setItem(QUERY_DRAFT,$("eri-decrypt-query").value.slice(0,500));} catch{}
    });
    $("eri-decrypt-region").addEventListener("change",() => {
      try {localStorage.setItem(REGION_DRAFT,$("eri-decrypt-region").value);}catch{}
    });
    form.addEventListener("submit",event => {
      event.preventDefault();
      const q = $("eri-decrypt-query").value.trim();
      if (q) search(q,$("eri-decrypt-region").value);
    });
    document.querySelectorAll("[data-eri-decrypt-preset]").forEach(node => {
      node.addEventListener("click", () => {
        $("eri-decrypt-query").value = node.dataset.eriDecryptPreset;
        try {localStorage.setItem(QUERY_DRAFT,$("eri-decrypt-query").value);}catch{}
        search(node.dataset.eriDecryptPreset,$("eri-decrypt-region").value);
      });
    });
    $("eri-decrypt-open-vault")?.addEventListener("click",() => {
      $("eri-decrypt-vault-section")?.scrollIntoView({behavior:"smooth",block:"start"});
      loadVault();
    });
    let debounce;
    $("eri-decrypt-vault-filter")?.addEventListener("input",() => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {vaultOffset=0;loadVault();},250);
    });
    $("eri-decrypt-vault-sort")?.addEventListener("change",()=>{vaultOffset=0;loadVault();});
    $("eri-decrypt-previous")?.addEventListener("click",()=>{vaultOffset=Math.max(0,vaultOffset-vaultLimit);loadVault();});
    $("eri-decrypt-next")?.addEventListener("click",()=>{if(vaultOffset+vaultLimit<vaultTotal){vaultOffset+=vaultLimit;loadVault();}});
    $("eri-decrypt-versions-close")?.addEventListener("click",()=>$("eri-decrypt-versions-section")?.classList.add("hidden"));
    loadVault();
    if (activeJob && getToken()) refreshJob();
    document.addEventListener("visibilitychange",() => {
      if (document.visibilityState === "visible" && activeJob) refreshJob();
    });
    setInterval(() => {
      if (activeJob && document.visibilityState === "visible" && !document.hidden) refreshJob();
    },30000);
  }
  document.addEventListener("DOMContentLoaded",init);
})();
