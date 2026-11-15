"use strict";
/* ERI visual rebrand of the F4TEAM-facing public catalog flow.
   Uses source API /api/decrypt/* instead of substituting ERI's own data.
   Data and files remain hosted by their source; no copied F4 CSRF/session.
   Public GET requests are routed through the separate authorized ERI bridge. */
(() => {
  const PROVIDER = "https://eri-f4-bridge.onrender.com";
  const $ = id => document.getElementById(id);
  const root = $("ipa-decrypt-panel");
  if (!root) return;
  const state = {country:"auto",category:"all",sort:"newest",offset:0,
    limit:24,total:0,searchItems:[],vaultItems:[],selected:null,versions:[],
    vaultAbort:null,searchAbort:null,appAbort:null};
  const fmt = n => Number.isFinite(Number(n)) ? Number(n).toLocaleString("vi-VN") : "—";
  function el(tag, cls="", text) {
    const e=document.createElement(tag);e.className=cls;
    if (text!==undefined) e.textContent=String(text);
    return e;
  }
  function add(parent,...nodes) { nodes.forEach(n=>n&&parent.append(n));return parent; }
  function clear(node) { if(node) node.replaceChildren();return node; }
  function action(label, fn, cls="btn outline") {
    const b=el("button",cls,label);b.type="button";b.addEventListener("click",fn);return b;
  }
  function link(label,url,cls="btn outline") {
    const a=el("a",cls,label);a.href=url;a.rel="noopener noreferrer";return a;
  }
  function notice(message,level="info") {
    const n=$("eri-decrypt-notice");if(n){n.textContent=message;n.dataset.state=level;}
  }
  function sayJob(title,stage,progress,kind="info") {
    const panel=$("eri-v-job-panel");panel?.classList.remove("hidden");
    if($("eri-v-job-title")) $("eri-v-job-title").textContent=title;
    if($("eri-v-job-stage")) $("eri-v-job-stage").textContent=stage;
    if($("eri-v-job-badge")) $("eri-v-job-badge").textContent=kind==="error"?"Lỗi":kind==="success"?"Hoàn tất":"ERI";
    if($("eri-v-progress-bar")) $("eri-v-progress-bar").style.width=(Math.max(0,Math.min(100,progress||0)))+"%";
  }
  function sourceDownload(value) {
    try{
      const u=new URL(value);
      if(u.protocol!=="https:"||u.username||u.password||u.port)return null;
      const ok=["160-191-242-70.sslip.io","decrypt.160-191-242-70.sslip.io"].includes(u.hostname);
      return ok && /^\/dl_dec\/[A-Za-z0-9_-]+\/[0-9]+\/[^?#]+\.ipa$/i.test(u.pathname)?u.href:null;
    }catch{return null;}
  }
  function storePage(url) {
    try{const u=new URL(url);return u.protocol==="https:" &&
      (u.hostname==="apps.apple.com"||u.hostname==="testflight.apple.com")?u.href:null;}catch{return null;}
  }
  function icon(url) {
    const img=el("img","eri-decrypt-app-icon");img.alt="Biểu tượng ứng dụng";
    img.loading="lazy";img.decoding="async";
    try{
      const u=new URL(url);
      if(u.protocol==="https:" && (u.hostname==="is1-ssl.mzstatic.com"||
        /^is[0-9]+-ssl\.mzstatic\.com$/.test(u.hostname)))img.src=u.href;
    }catch{}
    return img;
  }
  function sizeLabel(item) {
    if(item.size_human)return String(item.size_human);
    const n=Number(item.size_bytes||0);if(!Number.isFinite(n)||n<=0)return "";
    return n>1e9?(n/1e9).toFixed(2)+" GB":(n/1e6).toFixed(1)+" MB";
  }
  function normal(item={}) {
    const cached=item.vault_cached||item.cached||null;
    return {
      title:item.title||item.name||item.filename||"Ứng dụng",
      bundle_id:item.bundle_id||"",
      version:String(item.version||""),
      icon_url:item.icon_url||"",
      artist:item.artist||item.seller||item.developer||"",
      country:String(item.country||state.country||"VN").toUpperCase(),
      category:item.category||item.genre||item.category_label||"App Store",
      size_human:sizeLabel(item),
      app_id:item.app_id||item.item_id||"",
      store_url:item.store_url||item.app_store_url||"",
      dl_url:sourceDownload(item.dl_url||item.download_url||item.download_path||"") ||
        sourceDownload(cached?.dl_url||cached?.download_url||"") || null,
      cachedVersion:cached?.version||null,
      category_slug:item.category_slug||item.category||"",
      price:Number(item.price||0),
      raw:item
    };
  }
  function openDownload(url) {
    const checked=sourceDownload(url);
    if(!checked){notice("Nguồn chưa cấp liên kết IPA hợp lệ cho file này.","error");return;}
    window.location.assign(checked);
  }
  function sideload(url) {
    const checked=sourceDownload(url);if(!checked)return null;
    const wrap=el("div","eri-dec-sideload");const enc=encodeURIComponent(checked);
    [["TrollStore","apple-magnifier://install?url="],
      ["ESign","esign://install?url="],
      ["Scarlet","scarlet://install?url="],
      ["Feather","feather://install?url="]].forEach(([label,scheme])=>
       add(wrap,link(label,scheme+enc)));
    return wrap;
  }
  function modal(id,show) {
    const n=$(id);if(!n)return;
    n.classList.toggle("hidden",!show);n.setAttribute("aria-hidden",show?"false":"true");
    document.body.style.overflow=show?"hidden":"";
  }
  function vaultCard(src) {
    const item=normal(src), card=el("article","eri-decrypt-card");
    const tags=el("div","eri-dec-app-meta");
    add(tags,el("span","",item.category),el("span","",item.country));
    if(item.dl_url)add(tags,el("span","","IPA sẵn"));
    if(item.size_human)add(tags,el("span","",item.size_human));
    const intro=el("div","eri-v-vault-item-head");
    const info=el("div","eri-v-item-info");
    add(info,el("strong","",item.title),el("p","",item.bundle_id),
      el("p","","v"+(item.version||"—")));
    add(intro,icon(item.icon_url),info);
    add(card,tags,intro);
    const foot=el("div","eri-decrypt-actions");
    if(item.dl_url){
      add(foot,action("⬇ Tải IPA"+(item.size_human?" ("+item.size_human+")":""),
        ()=>openDownload(item.dl_url),"btn primary"));
      add(foot,action("◷",()=>showVersions(item),"btn outline eri-v-detail-action"));
      add(foot,action("ⓘ",()=>showDetails(item),"btn outline eri-v-detail-action"));
      add(foot,action("⎘",()=>navigator.clipboard?.writeText(item.dl_url)
        .then(()=>notice("Đã sao chép liên kết IPA.","success"))
        .catch(()=>notice("Không thể sao chép liên kết.","error")),"btn outline eri-v-detail-action"));
    }else{
      add(foot,action("Xem chi tiết",()=>showDetails(item),"btn primary"));
    }
    add(card,foot);
    const sl=sideload(item.dl_url);if(sl)add(card,sl);
    return card;
  }
  function resultCard(src) {
    const item=normal(src),card=el("article","eri-decrypt-card");
    const tags=el("div","eri-dec-app-meta");
    add(tags,el("span","",item.category),el("span","",item.country));
    if(item.size_human)add(tags,el("span","",item.size_human));
    if(item.dl_url)add(tags,el("span","","IPA có sẵn"));
    const info=el("div","eri-decrypt-details");
    add(info,el("strong","",item.title),el("span","",item.artist||item.bundle_id),
      el("small","",(item.bundle_id||"—")+" · v"+(item.version||"—")));
    add(card,tags,add(el("div","eri-decrypt-card-top"),icon(item.icon_url),info));
    const foot=el("div","eri-decrypt-actions");
    if(item.dl_url){
      add(foot,action("⬇ Tải IPA",()=>openDownload(item.dl_url),"btn primary"));
      const sl=sideload(item.dl_url);if(sl)card._sideload=sl;
    }else if(item.price>0){
      add(foot,action("Ứng dụng trả phí",()=>notice("Tải bản trả phí cần quyền phân phối phù hợp.","error"),"btn outline"));
    }else{
      add(foot,action("Kiểm tra IPA",()=>checkOrStart(item),"btn primary"));
    }
    add(foot,action("◷ Chọn Ver",()=>showVersions(item)));
    add(foot,action("Chi tiết",()=>showDetails(item)));
    if(storePage(item.store_url))add(foot,link("App Store",storePage(item.store_url)));
    add(card,foot);
    if(card._sideload)add(card,card._sideload);
    return card;
  }
  function showEmpty(grid,message) {
    clear(grid);add(grid,el("div","eri-v-empty",message));
  }
  async function providerRequest(path,{signal,method="GET",body}={}){
    // Public catalog and read-only details, not an undocumented private API bypass.
    const headers={Accept:"application/json"};
    if(body)headers["Content-Type"]="application/json";
    let res;
    try{
      res=await fetch(PROVIDER+path,{method,headers,signal,cache:"no-store",
        body:body?JSON.stringify(body):undefined,credentials:"omit"});
    }catch(error){
      if(error.name==="AbortError")throw error;
      throw new Error("Không kết nối được máy chủ trung gian ERI. Kiểm tra dịch vụ ERI F4 Bridge hoặc thử lại.");
    }
    let json;
    try{json=await res.json();}catch{throw new Error("Nguồn trả phản hồi không phải JSON.");}
    if(!res.ok||json.ok===false)
      throw new Error(String(json.error||json.message||"Nguồn trả HTTP "+res.status));
    return json;
  }
  async function loadVaultLibrary(offset=0,scroll=false) {
    const grid=$("eri-decrypt-vault-grid");if(!grid)return;
    state.vaultAbort?.abort();
    const ctrl=new AbortController();state.vaultAbort=ctrl;
    const query=$("eri-decrypt-vault-filter")?.value.trim()||"";
    const sort=$("eri-decrypt-vault-sort")?.value||"newest";
    state.sort=sort;state.offset=Math.max(0,offset);
    showEmpty(grid,"Đang kết nối kho IPA F4TEAM...");
    const qs=new URLSearchParams({q:query,category:state.category,sort,
      limit:String(state.limit),offset:String(state.offset)});
    try{
      const data=await providerRequest("/api/decrypt/library?"+qs,{signal:ctrl.signal});
      if(ctrl.signal.aborted)return;
      state.total=Number(data.total||0);
      state.vaultItems=Array.isArray(data.items)?data.items:[];
      const visible=state.vaultItems;
      clear(grid);if(!visible.length)showEmpty(grid,"Không có IPA phù hợp bộ lọc.");
      else visible.forEach(item=>grid.append(vaultCard(item)));
      const count=data.vault_total ?? data.total ?? 0;
      [$("eri-decrypt-vault-count"),$("eri-decrypt-header-vault-count")].forEach(n=>{
        if(n)n.textContent=fmt(count)+" IPA";
      });
      const summary=$("eri-v-vault-summary");
      if(summary)summary.textContent="Hiển thị "+fmt(state.total)+" kết quả từ kho IPA F4TEAM";
      const pages=Math.max(1,Math.ceil(state.total/state.limit));
      const page=Math.floor(state.offset/state.limit)+1;
      if($("eri-decrypt-page-label"))$("eri-decrypt-page-label").textContent="Trang "+page+" / "+pages;
      if($("eri-decrypt-previous"))$("eri-decrypt-previous").disabled=state.offset<=0;
      if($("eri-decrypt-next"))$("eri-decrypt-next").disabled=state.offset+state.limit>=state.total;
      if(scroll)$("eri-decrypt-vault-section")?.scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){
      if(error.name==="AbortError")return;
      showEmpty(grid,error.message);
      notice(error.message,"error");
    }
  }
  async function searchStore(q,stateCountry) {
    q=String(q||"").trim();if(!q)return;
    const country=(stateCountry||$("eri-decrypt-region")?.value||"auto").toLowerCase();
    state.country=country;
    state.searchAbort?.abort();
    const ctrl=new AbortController();state.searchAbort=ctrl;
    $("eri-decrypt-result-section")?.classList.remove("hidden");
    const grid=$("eri-decrypt-results");
    showEmpty(grid,"Đang tìm trên App Store...");
    const btn=$("eri-decrypt-search-button");
    if(btn)btn.disabled=true;
    try{
      const params=new URLSearchParams({q,country,mode:"ios"});
      const response=await providerRequest("/api/decrypt/search?"+params,{signal:ctrl.signal});
      if(ctrl.signal.aborted)return;
      state.searchItems=Array.isArray(response.items)?response.items:[];
      clear(grid);
      if(!state.searchItems.length)showEmpty(grid,"Không tìm thấy ứng dụng phù hợp.");
      else state.searchItems.forEach(item=>grid.append(resultCard(item)));
      $("eri-decrypt-result-section")?.scrollIntoView({behavior:"smooth",block:"start"});
      notice("Đã tìm thấy "+fmt(state.searchItems.length)+" kết quả.","success");
      return state.searchItems;
    }catch(error){
      if(error.name!=="AbortError"){
        showEmpty(grid,error.message);notice(error.message,"error");
      }
    }finally{if(btn)btn.disabled=false;}
  }
  function quickSearch(q,country) {
    if($("eri-decrypt-query"))$("eri-decrypt-query").value=q;
    if(country && $("eri-decrypt-region"))$("eri-decrypt-region").value=country;
    searchStore(q,country);
  }
  function cachedFile(item,versionFallback="") {
    if(!item)return null;
    const data=normal(item);
    if(!data.dl_url)return null;
    return {...data,version:String(data.cachedVersion||data.version||versionFallback||"")};
  }
  function sourceMatches(candidate,target) {
    const a=String(candidate.bundle_id||"").toLowerCase();
    const b=String(target.bundle_id||"").toLowerCase();
    if(a && b)return a===b;
    // Some store records have no Bundle ID; only use a strict title match then.
    return !b && String(candidate.title||"").toLowerCase()===
      String(target.title||"").toLowerCase();
  }
  async function findAvailableIPA(target) {
    const wanted=String(target.version||"").trim();
    const matches=new Map();
    const addCandidate=(src,versionFallback="")=>{
      const file=cachedFile(src,versionFallback);
      if(file && sourceMatches(file,target))matches.set(file.dl_url,file);
    };
    addCandidate(target.raw||target);
    // Searching the App Store gives metadata; the published IPA vault is a
    // separate dataset. It must be checked before reporting no download.
    const queries=[target.bundle_id,target.title].filter((q,i,a)=>q&&a.indexOf(q)===i);
    let accessible=false, lastError=null;
    for(const q of queries) {
      try {
        const params=new URLSearchParams({q,category:"all",sort:"newest",limit:"60",offset:"0"});
        const data=await providerRequest("/api/decrypt/library?"+params);
        accessible=true;
        (Array.isArray(data.items)?data.items:[]).forEach(x=>addCandidate(x));
      }catch(e){lastError=e;}
      if([...matches.values()].some(x=>!wanted||x.version===wanted))break;
    }
    // Version history is optional: failing it must not hide verified vault IPA.
    if(target.bundle_id && wanted && ![...matches.values()].some(x=>x.version===wanted)){
      try{
        const params=new URLSearchParams({target:target.bundle_id,
          country:String(target.country||"auto").toLowerCase()});
        const data=await providerRequest("/api/decrypt/versions?"+params);
        accessible=true;
        (Array.isArray(data.versions)?data.versions:[]).forEach(v=>{
          if(v.cached)addCandidate({...v.cached,
            bundle_id:target.bundle_id,title:target.title,
            version:String(v.cached.version||v.version||"")},v.version);
        });
      }catch(e){lastError=e;}
    }
    const list=[...matches.values()];
    const exact=list.find(x=>!wanted||x.version===wanted)||null;
    return {exact,alternate:list[0]||null,wanted,accessible,lastError};
  }
  let checking=false;
  async function checkOrStart(item) {
    if(checking)return;
    checking=true;
    const actions=$("eri-v-job-actions");clear(actions);
    sayJob("Kiểm tra IPA · "+item.title,
      "Đang đối chiếu ứng dụng và phiên bản với kho tệp đã công bố...",12);
    try{
      const found=await findAvailableIPA(item);
      if(found.exact){
        const file=found.exact;
        sayJob("IPA đã có trên nguồn · "+item.title,
          "Đã tìm thấy đường dẫn tải IPA "+(file.version?"v"+file.version:"")+".",100,"success");
        if(actions)add(actions,action("⬇ Tải IPA"+(file.version?" v"+file.version:""),
          ()=>openDownload(file.dl_url),"btn primary"));
        notice("Đã tìm thấy tệp IPA có sẵn trong kho nguồn.","success");
        openDownload(file.dl_url);
      }else if(found.alternate){
        const file=found.alternate;
        sayJob("Có IPA phiên bản khác · "+item.title,
          "Chưa thấy bản v"+(found.wanted||"được chọn")+
          ". Kho nguồn có bản "+(file.version?"v"+file.version:"không rõ phiên bản")+".",100);
        if(actions)add(actions,action("⬇ Tải bản "+(file.version?"v"+file.version:"khác"),
          ()=>openDownload(file.dl_url),"btn outline"));
        notice("Kho có IPA nhưng chưa xác minh được phiên bản yêu cầu.","info");
      }else if(found.accessible){
        sayJob("Đã kiểm tra kho IPA · "+item.title,
          "Nguồn chưa công bố tệp IPA tương ứng. Tạo bản giải mã mới cần quyền API riêng của F4TEAM.",100);
        notice("Chưa tìm thấy IPA phù hợp trong kho tệp nguồn.","info");
      }else {
        const msg=found.lastError?.message||"Không thể kết nối kho IPA.";
        sayJob("Không thể kiểm tra kho IPA",msg,0,"error");
        notice(msg,"error");
      }
    }catch(error){
      sayJob("Kiểm tra IPA thất bại",error.message,0,"error");
      notice(error.message,"error");
    }finally{checking=false;}
  }
  async function showVersions(item){
    state.selected=item;state.versions=[];
    modal("eri-v-versions-modal",true);
    if($("eri-v-versions-title"))$("eri-v-versions-title").textContent="Phiên bản · "+item.title;
    if($("eri-v-versions-filter"))$("eri-v-versions-filter").value="";
    const list=$("eri-v-versions-list");
    showEmpty(list,"Đang tải lịch sử phiên bản...");
    try{
      const target=item.bundle_id||item.store_url||String(item.app_id);
      const params=new URLSearchParams({target,country:item.country.toLowerCase()});
      const data=await providerRequest("/api/decrypt/versions?"+params);
      state.versions=Array.isArray(data.versions)?data.versions:[];
      renderVersions();
      if($("eri-v-versions-note"))$("eri-v-versions-note").textContent=
        fmt(state.versions.length)+" phiên bản được nguồn công bố. Chỉ bản có IPA sẵn mới tải được.";
    }catch(error){showEmpty(list,error.message);}
  }
  function renderVersions(){
    const list=$("eri-v-versions-list");clear(list);
    const q=$("eri-v-versions-filter")?.value.trim().toLowerCase()||"";
    const filtered=state.versions.filter(v=>
      !q||String(v.version||"").toLowerCase().includes(q)||
      String(v.external_version_id||"").includes(q));
    if(!filtered.length){showEmpty(list,"Không tìm thấy phiên bản phù hợp.");return;}
    filtered.slice(0,120).forEach(v=>{
      const row=el("div","eri-v-version-row");
      const label=el("div","");
      add(label,el("strong","","v"+String(v.version||v.external_version_id||"—")),
        el("small","",v.released_at||""));
      const cached=normal(v.cached||{});
      if(cached.dl_url) add(row,label,action("Tải IPA",()=>openDownload(cached.dl_url),"btn primary"));
      else add(row,label,el("span","eri-v-unavailable","Chưa có file tải"));
      list.append(row);
    });
  }
  async function showDetails(item){
    state.selected=item;modal("eri-v-detail-modal",true);
    if($("eri-v-detail-title"))$("eri-v-detail-title").textContent=item.title;
    const body=$("eri-v-detail-body");clear(body);
    const header=el("div","eri-v-detail-header");
    const detail=el("div","eri-decrypt-details");
    add(detail,el("strong","",item.title),el("span","",item.artist||item.bundle_id),
      el("small","",item.bundle_id));
    add(header,icon(item.icon_url),detail);add(body,header);
    const specs=el("div","eri-v-detail-specs");
    [["Phiên bản",item.version||"—"],["Quốc gia",item.country],
      ["Dung lượng",item.size_human||"—"],["Bundle ID",item.bundle_id||"—"],
      ["Nguồn","F4TEAM"]].forEach(([label,v])=>{
        const box=el("div");add(box,el("small","",label),el("strong","",v));specs.append(box);
      });
    add(body,specs);
    const actions=el("div","eri-v-detail-actions");
    if(item.dl_url){
      add(actions,action("⬇ Tải IPA",()=>openDownload(item.dl_url),"btn primary"));
      const sl=sideload(item.dl_url);if(sl)body.append(sl);
    }else add(actions,action("Kiểm tra IPA",()=>checkOrStart(item),"btn primary"));
    add(actions,action("Chọn phiên bản",()=>{modal("eri-v-detail-modal",false);showVersions(item);}));
    if(storePage(item.store_url))add(actions,link("App Store",storePage(item.store_url)));
    add(body,actions);
    try{
      const p=new URLSearchParams({bundle_id:item.bundle_id,country:item.country.toLowerCase(),title:item.title});
      const data=await providerRequest("/api/decrypt/app-info?"+p);
      if(state.selected!==item)return;
      const info=data.store_info||{};
      const content=el("div","eri-v-description");
      if(info.description)add(content,el("p","",info.description));
      if(Array.isArray(info.screenshots)&&info.screenshots.length){
        const shots=el("div","eri-v-shots");
        info.screenshots.slice(0,8).forEach(url=>{
          try{
            const u=new URL(url);
            if(u.protocol==="https:"&&u.hostname.endsWith("mzstatic.com")){
              const img=el("img");img.src=u.href;img.loading="lazy";img.alt="Ảnh ứng dụng";
              shots.append(img);
            }
          }catch{}
        });
        add(content,shots);
      }
      add(body,content);
    }catch {/* Metadata is optional; core downloaded IPA buttons remain. */}
  }
  function init(){
    if(!root)return;
    try{
      const theme=localStorage.getItem("eri.decrypted.theme");
      if(theme==="dark"||theme==="light")root.dataset.theme=theme;
    }catch{}
    $("eri-decrypt-theme-toggle")?.addEventListener("click",()=>{
      root.dataset.theme=root.dataset.theme==="light"?"dark":"light";
      try{localStorage.setItem("eri.decrypted.theme",root.dataset.theme);}catch{}
    });
    $("eri-decrypt-search-form")?.addEventListener("submit",e=>{
      e.preventDefault();searchStore($("eri-decrypt-query")?.value);
    });
    document.querySelectorAll("[data-eri-decrypt-preset]").forEach(btn=>
      btn.addEventListener("click",()=>quickSearch(btn.dataset.eriDecryptPreset,
        btn.dataset.eriDecryptPreset==="Roblox"?"us":"vn")));
    $("eri-decrypt-results-close")?.addEventListener("click",()=>
      $("eri-decrypt-result-section")?.classList.add("hidden"));
    $("eri-decrypt-open-vault")?.addEventListener("click",()=>
      $("eri-decrypt-vault-section")?.scrollIntoView({behavior:"smooth"}));
    $("eri-decrypt-vault-refresh")?.addEventListener("click",()=>loadVaultLibrary(0));
    let debounce;
    $("eri-decrypt-vault-filter")?.addEventListener("input",()=>{
      clearTimeout(debounce);debounce=setTimeout(()=>loadVaultLibrary(0),250);
    });
    $("eri-decrypt-vault-sort")?.addEventListener("change",()=>loadVaultLibrary(0));
    document.querySelectorAll("[data-eri-cat]").forEach(btn=>
      btn.addEventListener("click",()=>{
        document.querySelectorAll("[data-eri-cat]").forEach(b=>{
          const active=b===btn;b.classList.toggle("active",active);
          b.setAttribute("aria-pressed",String(active));
        });
        state.category=btn.dataset.eriCat||"all";
        loadVaultLibrary(0);
      }));
    $("eri-decrypt-previous")?.addEventListener("click",()=>loadVaultLibrary(Math.max(0,state.offset-state.limit),true));
    $("eri-decrypt-next")?.addEventListener("click",()=>{if(state.offset+state.limit<state.total)loadVaultLibrary(state.offset+state.limit,true)});
    const modals=[["eri-v-detail-modal","eri-v-detail-close"],["eri-v-versions-modal","eri-v-versions-close"]];
    modals.forEach(([id,close])=>{
      $(close)?.addEventListener("click",()=>modal(id,false));
      $(id)?.addEventListener("click",e=>{if(e.target===$(id))modal(id,false)});
    });
    document.addEventListener("keydown",e=>{
      if(e.key==="Escape")modals.forEach(([id])=>modal(id,false));
    });
    $("eri-v-versions-filter")?.addEventListener("input",renderVersions);
    $("eri-v-direct-versions")?.addEventListener("click",()=>{
      const q=$("eri-decrypt-query")?.value.trim();
      if(!q){notice("Hãy tìm ứng dụng trước khi chọn phiên bản.","error");return;}
      const first=state.searchItems[0];
      if(first)showVersions(normal(first));
      else notice("Tìm ứng dụng trước để xác định Bundle ID chính xác.","error");
    });
    $("eri-v-direct-check")?.addEventListener("click",async()=>{
      const query=String($("eri-decrypt-query")?.value||"").trim();
      if(!query){notice("Nhập tên hoặc Bundle ID ứng dụng trước khi kiểm tra.","error");return;}
      const item=state.searchItems.map(normal).find(x=>
        [x.bundle_id,x.title,x.store_url,String(x.app_id)].some(
          value=>String(value||"").toLowerCase()===query.toLowerCase()));
      if(item){checkOrStart(item);return;}
      const matches=await searchStore(query);
      if(Array.isArray(matches) && matches.length===1)checkOrStart(normal(matches[0]));
      else if(Array.isArray(matches)&&matches.length>1)
        notice("Có nhiều ứng dụng. Hãy chọn Kiểm tra IPA ở đúng kết quả bên dưới.","info");
    });
    // Original F4 homepage provided a server-rendered grid. The ERI-hosted
    // view must request live inventory rather than copying 621 IPA entries.
    loadVaultLibrary(0);
  }
  // Compatibility with the App Store lookup tab outside ERI DECRYPT.
  // Its download button performs a fresh source-catalog lookup, not a fake file redirect.
  window.ERIStoreIPA=Object.freeze({
    download:async(item,feedback)=>{
      const report=(message,kind="info")=>{
        notice(message,kind);
        if(feedback){feedback.hidden=false;feedback.textContent=message;feedback.dataset.state=kind;}
      };
      report("Đang kiểm tra kho IPA nguồn...");
      try{
        const q=String(item.bundle_id||item.app_store_url||item.app_id||"").trim();
        if(!q)throw new Error("Thiếu Bundle ID hoặc link App Store.");
        const params=new URLSearchParams({q,country:String(item.country||"auto").toLowerCase(),mode:"ios"});
        const response=await providerRequest("/api/decrypt/search?"+params);
        const match=(response.items||[]).map(normal).find(src=>
          src.bundle_id.toLowerCase()===String(item.bundle_id||"").toLowerCase() &&
          (!item.version||src.version===String(item.version))&&src.dl_url);
        if(!match)throw new Error("Nguồn chưa cung cấp file IPA đúng phiên bản.");
        report("Đã xác nhận link IPA từ nguồn. Đang bắt đầu tải...","success");
        openDownload(match.dl_url);
      }catch(error){report(error.message,"error");}
    }
  });
  if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init);
  else init();
  window.ERIF4Decrypt=Object.freeze({
    handleSearchSubmit:()=>searchStore($("eri-decrypt-query")?.value),
    quickSearch,loadVaultLibrary,showVersions,showDetails
  });
})();
