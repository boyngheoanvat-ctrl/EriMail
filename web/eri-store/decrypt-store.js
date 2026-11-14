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
  let historyInFlight = false;
  let historyQueued = false;
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
  // Compatibility while Render API is still on its old release.
  // Never invent IPA URLs: use only authorized FREE files returned by /mods.
  async function legacyVault(q = "", sort = "newest", offset = 0, limit = 24) {
    const catalog = await request("/api/v1/mods");
    const keyword = String(q).trim().toLocaleLowerCase("vi");
    let items = (Array.isArray(catalog.items) ? catalog.items : [])
      .filter(item => item.platform === "ipa" && Number(item.price_vnd) === 0
        && !!apiUrl(item.download_path))
      .filter(item => !keyword || [item.name, item.bundle_id]
        .some(value => String(value || "").toLocaleLowerCase("vi").includes(keyword)));
    if (sort === "title") items.sort((a,b) => String(a.name).localeCompare(String(b.name),"vi"));
    else if (sort === "size_desc") items.sort((a,b) => Number(b.size_bytes||0)-Number(a.size_bytes||0));
    else if (sort === "size_asc") items.sort((a,b) => Number(a.size_bytes||0)-Number(b.size_bytes||0));
    else items.sort((a,b) => Number(b.id)-Number(a.id));
    return {items:items.slice(offset,offset+limit),total:items.length};
  }
  async function legacySearch(q,country) {
    if (!safeStoreLink(q)) throw new Error(
      "Máy chủ ERI chưa cập nhật API tìm theo tên/Bundle ID. Bạn có thể tra cứu bằng link App Store, hoặc dùng kho IPA có sẵn.");
    const [app, vault] = await Promise.all([
      request("/api/v1/tools/ipa/appstore?url=" + encodeURIComponent(q)),
      legacyVault("", "newest", 0, 100)
    ]);
    const cached = vault.items.find(item =>
      String(item.bundle_id||"").toLowerCase() === String(app.bundle_id||"").toLowerCase()
      && String(item.version) === String(app.version)) || null;
    return {items:[{
      app_id:Number(new URL(q).pathname.match(/id([0-9]+)/)?.[1]||0),
      name:app.name,developer:app.developer,bundle_id:app.bundle_id,
      version:app.version,country:(country === "auto" ? "VN" : country.toUpperCase()),
      icon_url:app.icon_url||"",app_store_url:app.app_store_url||q,
      cached,ipa_available:!!cached
    }],total:1,source:"ERI legacy App Store Lookup + kho IPA có tệp thật"};
  }

  function installActions(parent, item) {
    const url = apiUrl(item?.download_path);
    if (!url) return;
    const actions = card("div", "eri-decrypt-actions");
    actions.append(link("Tải IPA " + (item.size_bytes ? "(" + pretty(item.size_bytes) + ")" : ""), url, true));
    parent.append(actions);
    const enc = encodeURIComponent(url);
    const sideload = card("div","eri-dec-sideload");
    sideload.append(link("TrollStore", "apple-magnifier://install?url=" + enc));
    sideload.append(link("ESign", "esign://install?url=" + enc));
    sideload.append(link("Scarlet", "scarlet://install?url=" + enc));
    sideload.append(link("Feather", "feather://install?url=" + enc));
    parent.append(sideload);
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
    const meta = card("div","eri-dec-app-meta");
    meta.append(card("span","",item.genre || "App Store"));
    meta.append(card("span","",item.country || "VN"));
    if (item.size_bytes && Number(item.size_bytes) > 0)
      meta.append(card("span","",pretty(item.size_bytes)));
    if (item.cached?.download_path) meta.append(card("span","", "Có sẵn IPA"));
    root.append(meta);
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
      let data;
      try {
        data = await request("/api/v1/tools/ipa/decrypt/search?" +
          new URLSearchParams({q, country, limit:"15"}));
      } catch (error) {
        if (!/HTTP 404/.test(error.message)) throw error;
        data = await legacySearch(q,country);
      }
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
    if ($("eri-decrypt-version-filter")) $("eri-decrypt-version-filter").value = "";
    notice("Đang kiểm tra những phiên bản có thể xác nhận...");
    try {
      let data;
      try {
        data = await request("/api/v1/tools/ipa/decrypt/versions?" +
          new URLSearchParams({target:item.app_store_url || String(item.app_id), country:item.country.toLowerCase()}));
      } catch (error) {
        if (!/HTTP 404/.test(error.message)) throw error;
        // Legacy API cannot discover historical Apple versions. Show current
        // metadata (if available) and actual versions already on the ERI API.
        const vault = await legacyVault("", "newest", 0, 100);
        let app = item;
        if (!app.bundle_id && safeStoreLink(item.app_store_url)) {
          const oldLookup = await legacySearch(item.app_store_url, item.country.toLowerCase());
          app = oldLookup.items[0] || item;
        }
        const matched = vault.items.filter(x =>
          x.bundle_id && app.bundle_id && x.bundle_id.toLowerCase() === app.bundle_id.toLowerCase());
        const versions = matched.map(x => ({
          version:x.version, cached:x, source:"ERI authorized library"
        }));
        if (app.version && !versions.some(x => x.version === app.version))
          versions.unshift({version:app.version,cached:null,source:"Apple current version metadata"});
        data = {versions,notice:"Máy chủ đang dùng API cũ. Chỉ hiển thị phiên bản hiện tại và những IPA có tệp thật trong kho ERI."};
      }
      const entries = Array.isArray(data.versions) ? data.versions : [];
      for (const entry of entries) {
        const node = card("article","eri-decrypt-card");
        node.dataset.version = entry.version || "";
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
      let result;
      try {
        result = await request("/api/v1/tools/ipa/decrypt/vault?" +
          new URLSearchParams({q,sort,limit:String(vaultLimit),offset:String(vaultOffset)}));
      } catch(error) {
        if (!/HTTP 404/.test(error.message)) throw error;
        result = await legacyVault(q,sort,vaultOffset,vaultLimit);
      }
      if (serial !== vaultSerial) return;
      vaultTotal = Number(result.total) || 0;
      text("eri-decrypt-vault-count", vaultTotal + " IPA");
      text("eri-decrypt-header-vault-count", String(vaultTotal));
      if (grid) grid.replaceChildren();
      if (!result.items?.length) grid?.append(card("p", "small",
        "Chưa có IPA được cấp quyền phân phối và còn file trên máy chủ phù hợp bộ lọc này."));
      else for(const item of result.items) {
        const node = card("article","eri-decrypt-card");
        node.append(card("div","eri-dec-vault-label","IPA đang có trong kho"));
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
        text("eri-decrypt-header-vault-count","—");
      }
    }
  }
  async function loadHistory() {
    if (historyInFlight) { historyQueued = true; return; }
    const grid = $("eri-decrypt-history-grid");
    if (!grid) return;
    if (!getToken()) {
      grid.replaceChildren(card("p","small","Bạn cần đăng nhập ERI STORE để xem các yêu cầu IPA đã lưu."));
      return;
    }
    historyInFlight = true;
    const startingToken = getToken();
    grid.replaceChildren(card("p","small","Đang khôi phục lịch sử yêu cầu từ máy chủ..."));
    try {
      const data = await request("/api/v1/tools/ipa/decrypt/requests",{auth:true});
      if (startingToken !== getToken()) return; // Account changed mid-request.
      grid.replaceChildren();
      if (!data.items?.length) {
        grid.append(card("p","small","Bạn chưa có yêu cầu IPA nào."));
        return;
      }
      for (const job of data.items) {
        const node = card("article","eri-decrypt-card");
        node.append(card("strong","",job.name || job.bundle_id));
        node.append(card("p","small",(job.bundle_id||"") + " · v" +
          (job.version||"—") + " · " + (job.country||"VN")));
        const complete = job.state === "completed" && !!apiUrl(job.result?.download_path);
        node.append(card("p",complete ? "eri-decrypt-available" : "eri-decrypt-unavailable",
          complete ? "Đã có IPA hợp lệ trong kho — tải được ngay" :
            "Đã lưu yêu cầu. Chưa có IPA được phép phân phối trên máy chủ."));
        const actions = card("div","eri-decrypt-actions");
        actions.append(button("Xem trạng thái",() => {
          activeJob=job.job_id;
          try {localStorage.setItem(ACTIVE_JOB,activeJob);}catch{}
          renderJob(job);
          $("eri-decrypt-request-section")?.scrollIntoView({behavior:"smooth",block:"center"});
        }));
        node.append(actions);
        if (complete) installActions(node,job.result);
        grid.append(node);
      }
    } catch (error) {
      if (startingToken === getToken()) grid.replaceChildren(card("p","small",error.message));
    } finally {
      historyInFlight = false;
      if (historyQueued) { historyQueued = false; loadHistory(); }
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
      await loadHistory();
    } catch(error) {
      notice(/HTTP 404/.test(error.message)
        ? "Máy chủ ERI chưa cập nhật tính năng gửi yêu cầu IPA. Bộ tra cứu và kho IPA vẫn sử dụng được; cần cập nhật backend để lưu yêu cầu."
        : error.message, "error");
    }
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
      // Refresh history from the server after a completed request is observed.
      if (result.state === "completed") loadHistory();
    } catch (error) {
      if (/không tìm thấy yêu cầu|404/.test(error.message)) {
        activeJob = null;
        try{localStorage.removeItem(ACTIVE_JOB);}catch{}
      } else {
        notice(error.message, "error");
      }
    } finally { pollInFlight=false; }
  }
  // A version-picker is information lookup; no invented Apple CDN IPA URLs.

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
    $("eri-decrypt-direct-version")?.addEventListener("click", () => {
      const q = $("eri-decrypt-query").value.trim();
      if (!q) { notice("Nhập tên game, Bundle ID hoặc link App Store để chọn phiên bản.", "error"); $("eri-decrypt-query").focus(); return; }
      loadVersions({name:q,app_store_url:q,app_id:q,country:$("eri-decrypt-region").value});
    });
    $("eri-decrypt-direct-request")?.addEventListener("click", () => {
      const q = $("eri-decrypt-query").value.trim();
      if (!q) { notice("Nhập tên game, Bundle ID hoặc link App Store trước khi yêu cầu IPA.", "error"); $("eri-decrypt-query").focus(); return; }
      createJob(q,$("eri-decrypt-region").value,"");
    });
    $("eri-decrypt-theme-toggle")?.addEventListener("click", () => {
      const panel = $("ipa-decrypt-panel");
      const next = panel.dataset.theme === "dark" ? "light" : "dark";
      panel.dataset.theme = next;
      $("eri-decrypt-theme-toggle").textContent = next === "dark" ? "☀" : "☾";
      try {localStorage.setItem("eri.decrypt.theme",next);}catch{}
    });
    try {
      const saved = localStorage.getItem("eri.decrypt.theme");
      if (saved === "light" || saved === "dark") $("ipa-decrypt-panel").dataset.theme = saved;
    }catch{}
    $("eri-decrypt-theme-toggle").textContent =
      $("ipa-decrypt-panel").dataset.theme === "dark" ? "☀" : "☾";
    $("eri-decrypt-results-close")?.addEventListener("click",
      () => $("eri-decrypt-result-section")?.classList.add("hidden"));
    $("eri-decrypt-version-filter")?.addEventListener("input", event => {
      const query = event.target.value.trim().toLowerCase();
      $("eri-decrypt-versions-grid")?.querySelectorAll("[data-version]").forEach(node => {
        node.hidden = !!query && !node.dataset.version.toLowerCase().includes(query);
      });
    });
    document.querySelectorAll("[data-eri-decrypt-preset]").forEach(node => {
      node.addEventListener("click", () => {
        $("eri-decrypt-query").value = node.dataset.eriDecryptPreset;
        try {localStorage.setItem(QUERY_DRAFT,$("eri-decrypt-query").value);}catch{}
        search(node.dataset.eriDecryptPreset,$("eri-decrypt-region").value);
      });
    });
    // F4TEAM has not published a verified third-party API contract.
    // Open its website directly instead of proxying protected endpoints,
    // and copy only the public search term to ease the handoff.
    async function copyExternalQuery() {
      const q = ($("eri-decrypt-query")?.value || "").trim();
      if (!q) {
        notice("Nhập tên ứng dụng, Bundle ID hoặc link App Store trước khi sao chép.", "error");
        return;
      }
      try {
        await navigator.clipboard.writeText(q);
        notice("Đã sao chép tên hoặc link. Bạn có thể dán vào ô tìm kiếm ở F4TEAM hoặc kho IPA ngoài.", "success");
      } catch {
        notice("Safari chưa cho phép sao chép tự động. Hãy chọn và sao chép nội dung trong ô tìm kiếm.", "error");
        $("eri-decrypt-query")?.focus();
      }
    }
    $("eri-decrypt-copy-query")?.addEventListener("click",copyExternalQuery);
    for (const id of ["eri-decrypt-open-f4","eri-decrypt-open-public"]) {
      $(id)?.addEventListener("click",() => {
        const q = ($("eri-decrypt-query")?.value || "").trim();
        if (q && navigator.clipboard?.writeText) {
          // No await: allow the trusted external link to open on the same tap.
          navigator.clipboard.writeText(q).catch(() => {});
        }
      });
    }
    $("eri-decrypt-open-vault")?.addEventListener("click",() => {
      $("eri-decrypt-vault-section")?.scrollIntoView({behavior:"smooth",block:"start"});
      loadVault();
    });
    $("eri-decrypt-vault-refresh")?.addEventListener("click",() => loadVault());
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
    loadHistory();
    if (activeJob && getToken()) refreshJob();
    $("eri-decrypt-refresh-history")?.addEventListener("click",loadHistory);
    window.addEventListener("eri-store:session-changed", () => {
      // Never leave a previous customer's requests visible after logout.
      if (!getToken()) {
        activeJob = null;
        $("eri-decrypt-request-section")?.classList.add("hidden");
        try {localStorage.removeItem(ACTIVE_JOB);}catch{}
      }
      loadHistory();
      if (activeJob && getToken()) refreshJob();
    });
    document.addEventListener("visibilitychange",() => {
      if (document.visibilityState === "visible") {
        loadHistory();
        if (activeJob) refreshJob();
      }
    });
    setInterval(() => {
      if (activeJob && document.visibilityState === "visible" && !document.hidden) refreshJob();
    },30000);
  }
  document.addEventListener("DOMContentLoaded",init);
})();
