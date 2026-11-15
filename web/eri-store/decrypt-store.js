"use strict";
// ERI STORE App Store discovery and authorized IPA vault.
// Never claim to decrypt Apple's FairPlay DRM from public metadata.
(() => {
  const API = "https://eri-api-dag8.onrender.com";
  const TOKEN = "eri_store_buyer_token";
  const QUERY_DRAFT = "eri.decrypt.query.v1";
  const REGION_DRAFT = "eri.decrypt.region.v1";
  const ACTIVE_JOB = "eri.decrypt.job.v1";
  const ACTIVE_F4_JOB = "eri.decrypt.f4.job.v1";
  const $ = id => document.getElementById(id);
  const getToken = () => sessionStorage.getItem(TOKEN) || localStorage.getItem(TOKEN) || "";
  let vaultOffset = 0;
  const vaultLimit = 24;
  let vaultTotal = 0;
  let activeJob = null;
  let activeF4Job = null;
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
  async function externalStatus(root, item) {
    const url = safeStoreLink(item.app_store_url);
    if (!url || item.cached?.download_path) return;
    const status = card("p", "small", "Đang kiểm tra nguồn IPA ERI bên ngoài...");
    root.append(status);
    try {
      if (item.bundle_id) {
        try {
          const official = await request("/api/v1/tools/ipa/decrypt/official?" +
            new URLSearchParams({bundle_id:item.bundle_id}));
          if (official.available && /^https:\/\/github\.com\//.test(official.download_url || "")) {
            status.textContent = "Có IPA công khai từ nhà phát triển · " + (official.version || "");
            root.append(link("Tải IPA chính chủ", official.download_url, true));
            return;
          }
        } catch (_) {}
      }
      // Read-only upstream catalog lookup through ERI backend (no CSRF/session leakage).
      let external;
      try {
        external = await request("/api/v1/tools/ipa/decrypt/external?" +
          new URLSearchParams({target:url,country:"auto"}));
        if (external.download_ready && external.download_url) {
          const parsed = new URL(external.download_url);
          const f4DownloadPath = /^\/api\/decrypt\/download\/[a-f0-9]{32}$/i.test(parsed.pathname);
          const f4LegacyPath = /^\/dl_dec\/[A-Za-z0-9_-]{8,128}\/[0-9]{8,15}\/[A-Za-z0-9._%-]+\.ipa$/i.test(parsed.pathname);
          if (parsed.protocol === "https:" &&
              ["decrypt.160-191-242-70.sslip.io", "160-191-242-70.sslip.io"].includes(parsed.hostname) &&
              !parsed.username && !parsed.password && !parsed.search && !parsed.hash &&
              (f4DownloadPath || f4LegacyPath)) {
            status.textContent = "F4TEAM có IPA v" + (external.version || item.version || "—") +
              " · Nguồn ngoài, ERI chưa kiểm định nội dung file.";
            root.append(link("Tải IPA từ F4TEAM" +
              (external.size_bytes ? " (" + pretty(external.size_bytes) + ")" : ""),
              parsed.href,true));
            return;
          }
        }
      } catch (error) {
        status.textContent = "Không kiểm tra được kho F4TEAM lúc này. Thử lại sau; đây chưa phải xác nhận có hay không có IPA.";
        return;
      }
      status.textContent = external.found
        ? "F4TEAM đã nhận diện ứng dụng nhưng chưa trả về link tải IPA. Có thể bản này chưa có file trong kho F4."
        : "F4TEAM chưa trả về IPA của ứng dụng này. API tìm thấy ứng dụng không có nghĩa là file đã sẵn sàng.";
    } catch (_) {
      status.textContent = "Không thể kiểm tra nguồn IPA lúc này.";
    }
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
      root.append(card("div", "eri-decrypt-available", "IPA sẵn sàng"));
      installActions(root, item.cached);
    } else {
      root.append(card("div", "eri-decrypt-unavailable", "Chưa có IPA"));
      externalStatus(root, item);
      actions.append(button(item.source_available ? "Xử lý IPA" : "Yêu cầu IPA",
        () => createJob(item.app_store_url || String(item.app_id), item.country, item.version), true));
    }
    if (Number(item.app_id) > 0)
      actions.append(button("Chọn phiên bản", () => loadVersions(item), false));
    const store = safeStoreLink(item.app_store_url);
    if (store) actions.append(link("App Store", store));
    root.append(actions);
    return root;
  }
  // Public discovery metadata only. Never treat catalog matches as downloadable IPA.
  let publicCatalogPromise = null;
  function publicCatalog() {
    if (!publicCatalogPromise) publicCatalogPromise = fetch(
      "https://cdn.decrypt.day/resources/apps/indexed.json",
      {mode:"cors",cache:"default"}
    ).then(r => { if (!r.ok) throw new Error("Danh mục ứng dụng chưa sẵn sàng."); return r.json(); })
      .then(rows => { if (!Array.isArray(rows)) throw new Error("Danh mục không hợp lệ."); return rows; })
      .catch(error => { publicCatalogPromise = null; throw error; });
    return publicCatalogPromise;
  }
  function fold(value) {
    return String(value || "").normalize("NFD").replace(/[\\u0300-\\u036f]/g,"")
      .replace(/đ/g,"d").replace(/Đ/g,"D").toLowerCase();
  }
  async function catalogSearch(q,country) {
    const rows = await publicCatalog();
    const needle = fold(q).trim();
    const id = String(q).match(/id([0-9]{6,15})/)?.[1] ||
      String(q).match(/^([0-9]{6,15})$/)?.[1];
    const matches = rows.filter(x => id ? String(x.i) === "id"+id :
      [x.n,x.p,x.i,...(Array.isArray(x.t)?x.t:[])].some(v=>fold(v).includes(needle)))
      .slice(0,15);
    const vault = await legacyVault("", "newest", 0, 100).catch(() => ({items:[]}));
    const available = Array.isArray(vault.items) ? vault.items : [];
    return {items:matches.map(x=>({
      app_id:Number(String(x.i||"").replace(/^id/,""))||0,
      name:x.n||"Ứng dụng",developer:x.p||"",version:x.v||"",
      country:country==="auto"?"VN":country.toUpperCase(),
      app_store_url:"https://apps.apple.com/app/"+x.i,
      genre:Array.isArray(x.t)?(x.t.find(t=>/games/i.test(t))||x.t[0]||"App Store"):"App Store",
      cached:available.find(item => String(item.name || "").trim().toLowerCase() === String(x.n || "").trim().toLowerCase() && String(item.version || "") === String(x.v || "")) || null,
      ipa_available:available.some(item => String(item.name || "").trim().toLowerCase() === String(x.n || "").trim().toLowerCase() && String(item.version || "") === String(x.v || ""))
    })),total:matches.length,source:"Public catalog metadata + verified ERI vault files"};
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
        try { data = await catalogSearch(q,country); }
        catch (catalogError) {
          if (!/HTTP 404/.test(error.message)) throw error;
          data = await legacySearch(q,country);
        }
      }
      if (serial !== searchSerial) return;
      if (!data.items?.length) {
        notice("Không tìm thấy ứng dụng phù hợp ở khu vực đã chọn.", "error");
        return;
      }
      notice("Đã tìm thấy " + data.items.length + " ứng dụng.", "success");
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
          ? "Sẵn sàng tải." : "Chưa có IPA."));
        if (entry.cached?.download_path) installActions(node, entry.cached);
        else node.append(button(entry.source_available ? "Xử lý bản này" : "Yêu cầu bản này", () =>
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
      // F4 history must still load when there are no ERI-source requests.
      for (const job of (Array.isArray(data.items) ? data.items : [])) {
        const node = card("article","eri-decrypt-card");
        node.append(card("strong","",job.name || job.bundle_id));
        node.append(card("p","small",(job.bundle_id||"") + " · v" +
          (job.version||"—") + " · " + (job.country||"VN")));
        const complete = job.state === "completed" && !!apiUrl(job.result?.download_path);
        node.append(card("p",complete ? "eri-decrypt-available" : "eri-decrypt-unavailable",
          complete ? "Sẵn sàng tải IPA" :
            job.state === "processing" ? ("Đang xử lý" +
              (Number.isFinite(Number(job.progress)) && job.progress !== null ? " · " + job.progress + "%" : "")) :
              job.state === "failed" ? "Xử lý thất bại" : "Chưa có nguồn IPA"));
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
      // Include server-side F4 tasks in the same history, scoped to ERI user.
      try {
        const fromF4 = await request("/api/v1/tools/ipa/decrypt/f4/jobs",{auth:true});
        if (startingToken !== getToken()) return;
        for (const job of (Array.isArray(fromF4.items) ? fromF4.items : [])) {
          const node = card("article","eri-decrypt-card");
          node.append(card("strong","",job.name || job.bundle_id));
          node.append(card("p","small","F4TEAM · " + (job.bundle_id || "") +
            " · v" + (job.version || "—")));
          node.append(card("p",job.state === "completed" ?
            "eri-decrypt-available" : "eri-decrypt-unavailable",
            job.state === "completed" ? "F4 trả link IPA (chưa kiểm định)" :
              job.state === "failed" ? "F4 báo thất bại" : "F4 đang xử lý"));
          node.append(button("Xem tác vụ F4",() => {
            activeF4Job = job.job_id;
            activeJob = null;
            try {
              localStorage.setItem(ACTIVE_F4_JOB,activeF4Job);
              localStorage.removeItem(ACTIVE_JOB);
            } catch {}
            renderF4Job(job);
            $("eri-decrypt-request-section")?.scrollIntoView({behavior:"smooth",block:"center"});
          }));
          const f4Url = trustedF4Download(job.download_url);
          if (job.state === "completed" && f4Url) node.append(link("Tải IPA F4",f4Url,true));
          grid.append(node);
        }
      } catch (_) {
        // An older ERI backend or unavailable F4 provider should not hide ERI history.
      }
      if (!grid.children.length) grid.append(card("p","small","Bạn chưa có yêu cầu IPA nào."));
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
    notice("Đang kiểm tra quyền tác vụ F4TEAM...");
    let f4Enabled = false;
    try {
      const capabilities = await request("/api/v1/tools/ipa/decrypt/f4/capabilities");
      f4Enabled = capabilities.enabled === true;
    } catch (_) {
      // Legacy ERI API: preserve the existing authorized-source request flow.
    }
    if (f4Enabled) {
      try {
        const f4 = await request("/api/v1/tools/ipa/decrypt/f4/start", {
          auth:true, body:{target,country:country?.toLowerCase()||"auto",version:version||""}
        });
        activeF4Job = f4.job_id;
        activeJob = null;
        try {
          localStorage.setItem(ACTIVE_F4_JOB, activeF4Job);
          localStorage.removeItem(ACTIVE_JOB);
        } catch {}
        renderF4Job(f4);
        await loadHistory();
        return;
      } catch (error) {
        notice("F4TEAM không khởi tạo được tác vụ: " + error.message, "error");
        return;
      }
    }
    notice("Chưa có quyền tác vụ F4TEAM; đang lưu yêu cầu vào kho ERI.");
    try {
      const job = await request("/api/v1/tools/ipa/decrypt/requests", {
        auth:true, body:{target,country:country?.toLowerCase()||"auto",version:version||""}
      });
      activeJob = job.job_id;
      activeF4Job = null;
      try {
        localStorage.setItem(ACTIVE_JOB,activeJob);
        localStorage.removeItem(ACTIVE_F4_JOB);
      } catch {}
      renderJob(job);
      await loadHistory();
    } catch(error) {
      notice(error.message, "error");
    }
  }
  function trustedF4Download(value) {
    try {
      const url = new URL(value);
      const path = url.pathname;
      const allowedPath =
        /^\/api\/decrypt\/download\/[a-f0-9]{32}$/i.test(path) ||
        /^\/dl_dec\/[A-Za-z0-9_-]{8,128}\/[0-9]{8,15}\/[A-Za-z0-9._%-]+\.ipa$/i.test(path);
      return url.protocol === "https:" &&
        ["decrypt.160-191-242-70.sslip.io","160-191-242-70.sslip.io"].includes(url.hostname) &&
        !url.username && !url.password && !url.search && !url.hash && allowedPath
          ? url.href : null;
    } catch { return null; }
  }

  function renderF4Job(job) {
    $("eri-decrypt-request-section")?.classList.remove("hidden");
    const url = job.state === "completed" ? trustedF4Download(job.download_url) : null;
    const measured = job.state === "processing" && job.progress !== null &&
      Number.isFinite(Number(job.progress)) && Number(job.progress) >= 0 &&
      Number(job.progress) < 100;
    const pct = url ? 100 : measured ? Math.floor(Number(job.progress)) : null;
    text("eri-decrypt-job-pct",pct === null ?
      (job.state === "failed" ? "Lỗi F4" : "Chờ F4TEAM") : pct + "%");
    const bar = $("eri-decrypt-job-progress");
    if (bar) {
      bar.hidden = pct === null;
      bar.style.display = pct === null ? "none" : "";
      bar.value = pct === null ? 0 : pct;
    }
    const label = url ? "Sẵn sàng link từ F4" :
      job.state === "failed" ? "F4 báo thất bại" : "F4 đang xử lý / đang chờ";
    text("eri-decrypt-job-stage",
      (job.name || "Ứng dụng") + " · v" + (job.version || "—") +
      " · " + label + "\n" + (job.message || ""));
    const actions = $("eri-decrypt-job-actions");
    if (actions) actions.replaceChildren();
    if (url) {
      if (actions) {
        const download = link("Tải IPA từ F4TEAM (nguồn ngoài)", url, true);
        download.referrerPolicy = "no-referrer";
        actions.append(download);
      }
      notice("F4 đã trả link IPA; ERI chưa kiểm định nội dung hoặc khả năng cài đặt.", "success");
    } else if (job.state === "failed") {
      notice(job.message || "F4TEAM xử lý thất bại.", "error");
    } else {
      notice("Đã gửi tác vụ F4TEAM · Chỉ hiển thị tiến độ do F4 cung cấp.", "info");
    }
  }

  function renderJob(job) {
    const section = $("eri-decrypt-request-section");
    section?.classList.remove("hidden");
    const ready = job.state === "completed" && !!apiUrl(job.result?.download_path);
    const processing = job.state === "processing";
    const value = Number(job.progress);
    const measured = processing && job.progress != null &&
      Number.isFinite(value) && value >= 0 && value <= 99;
    const pct = ready ? 100 : measured ? value : null;
    text("eri-decrypt-job-pct", pct === null ?
      (processing ? "Đang xử lý" : job.state === "failed" ? "Lỗi" : "Chưa có nguồn") :
      String(Math.round(pct)) + "%");
    const bar = $("eri-decrypt-job-progress");
    if (bar) {
      bar.hidden = pct === null;
      bar.style.display = pct === null ? "none" : "";
      bar.value = pct === null ? 0 : pct;
    }
    const stateName = ready ? "Hoàn tất" : processing ? "Đang xử lý" :
      job.state === "failed" ? "Thất bại" : "Chưa có nguồn IPA";
    text("eri-decrypt-job-stage",(job.name || "Ứng dụng") +
      " · v" + (job.version || "—") + " · " + stateName +
      (job.message ? "\n" + job.message : ""));
    const controls = $("eri-decrypt-job-actions");
    if (controls) controls.replaceChildren();
    if (ready) {
      if (controls) installActions(controls,job.result);
      notice("IPA sẵn sàng tải.", "success");
    } else if (job.state === "failed") {
      if (controls) {
        controls.append(button("Thử lại", () =>
          createJob(job.bundle_id,job.country,job.version)));
      }
      notice("Xử lý IPA thất bại.", "error");
    } else if (processing) {
      notice("Máy chủ ERI đang xử lý IPA.", "info");
    } else {
      notice("Yêu cầu đã lưu · Chưa có nguồn IPA để xử lý.", "info");
    }
  }
  async function refreshJob() {
    if (pollInFlight || (!activeJob && !activeF4Job) || !getToken()) return;
    pollInFlight=true;
    try {
      if (activeF4Job) {
        const f4 = await request("/api/v1/tools/ipa/decrypt/f4/jobs/" + activeF4Job,{auth:true});
        renderF4Job(f4);
        if (f4.state === "completed" || f4.state === "failed") loadHistory();
        return;
      }
      const result = await request("/api/v1/tools/ipa/decrypt/requests/" + activeJob,{auth:true});
      renderJob(result);
      // Refresh history from the server after a completed request is observed.
      if (result.state === "completed") loadHistory();
    } catch (error) {
      if (/không tìm thấy yêu cầu|404/.test(error.message)) {
        activeJob = null;
        activeF4Job = null;
        try{
          localStorage.removeItem(ACTIVE_JOB);
          localStorage.removeItem(ACTIVE_F4_JOB);
        }catch{}
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
      activeF4Job = localStorage.getItem(ACTIVE_F4_JOB);
      if (activeF4Job) activeJob = null;
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
    if ((activeJob || activeF4Job) && getToken()) refreshJob();
    $("eri-decrypt-refresh-history")?.addEventListener("click",loadHistory);
    window.addEventListener("eri-store:session-changed", () => {
      // Never leave a previous customer's requests visible after logout.
      if (!getToken()) {
        activeJob = null;
        activeF4Job = null;
        $("eri-decrypt-request-section")?.classList.add("hidden");
        try {localStorage.removeItem(ACTIVE_JOB);}catch{}
      }
      loadHistory();
      if (activeJob && getToken()) refreshJob();
    });
    document.addEventListener("visibilitychange",() => {
      if (document.visibilityState === "visible") {
        loadHistory();
        if (activeJob || activeF4Job) refreshJob();
      }
    });
    setInterval(() => {
      if ((activeJob || activeF4Job) && document.visibilityState === "visible" && !document.hidden) refreshJob();
    },4000);
  }
  document.addEventListener("DOMContentLoaded",init);
})();
