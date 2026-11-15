"use strict";
/* ERI Nguyễn native certificate-free picker. Source manifests are public links;
   NO execution of third-party apps.js, third-party credentials, or proxy uploads. */
(() => {
  const root = document.getElementById("certificate-free-panel");
  if(!root) return;
  const grid = document.getElementById("eri-free-install-list");
  const status = document.getElementById("eri-free-install-status");
  const buttons = Array.from(root.querySelectorAll("[data-free-app]"));
  const sourceHost = "website.khoindvn.io.vn";
  let inventory = null;
  let active = "esign";
  const labels = {esign:"ESign",ksign:"KSign"};

  function verifiedManifest(value){
    try {
      const url = new URL(value);
      if(url.protocol !== "https:" || url.hostname !== sourceHost ||
        !/^\/plists\/[a-z0-9_-]{4,80}\.plist$/i.test(url.pathname) ||
        url.search || url.hash || url.username || url.password) return null;
      return url.href;
    } catch { return null; }
  }
  function node(tag,cls,text){
    const n = document.createElement(tag);
    if(cls) n.className=cls;
    if(text != null) n.textContent=String(text);
    return n;
  }
  function render(){
    if(!grid) return;
    grid.replaceChildren();
    buttons.forEach(b=>{
      const enabled=b.dataset.freeApp===active;
      b.classList.toggle("is-active",enabled);
      b.setAttribute("aria-selected",String(enabled));
    });
    const items=inventory?.[active];
    if(!Array.isArray(items)){
      grid.append(node("div","eri-free-empty","Đang tải danh sách chứng chỉ Free..."));
      return;
    }
    if(items.length===0){
      grid.append(node("div","eri-free-empty","Không có lựa chọn cài đặt hợp lệ trong danh sách nguồn."));
      return;
    }
    for(const item of items){
      const manifest=verifiedManifest(item.manifest_url);
      const card=node("article","eri-free-card");
      const icon=node("span","eri-free-app-icon "+active,active==="esign"?"E":"K");
      icon.setAttribute("aria-hidden","true");
      const info=node("div","eri-free-card-info");
      info.append(node("strong","eri-free-card-title",item.name||labels[active]));
      info.append(node("span","eri-free-card-issuer",item.issuer||"Chưa rõ tên chứng chỉ"));
      const install=node("a","eri-free-install-btn",manifest?"CÀI ĐẶT":"Không khả dụng");
      if(manifest){
        install.href="itms-services://?action=download-manifest&url="+encodeURIComponent(manifest);
        install.setAttribute("aria-label","Cài "+(item.name||labels[active])+" — "+(item.issuer||""));
        install.addEventListener("click",()=>{
          if(status)status.textContent="Đang yêu cầu iOS cài "+(item.name||labels[active])+
            ". Nếu iPhone từ chối, thử chứng chỉ khác; không có bảo đảm chứng chỉ còn hiệu lực.";
        });
      }else{
        install.removeAttribute("href");
        install.classList.add("is-disabled");
        install.setAttribute("aria-disabled","true");
      }
      card.append(icon,info,install);
      grid.append(card);
    }
    if(status)status.textContent="Chọn một bản ESign hoặc KSign và nhấn CÀI ĐẶT. "+
      "Các chứng chỉ cộng đồng có thể hết hạn hoặc bị Apple thu hồi.";
  }
  buttons.forEach(b=>b.addEventListener("click",()=>{
    active=b.dataset.freeApp==="ksign"?"ksign":"esign";
    render();
  }));
  const trigger=document.getElementById("eri-free-refresh");
  async function load(){
    if(status)status.textContent="Đang mở danh sách ESign và KSign của ERI...";
    if(trigger)trigger.disabled=true;
    try{
      const response=await fetch("/cert-free-data.json?v=20261115a",{cache:"no-store"});
      if(!response.ok)throw new Error("Không tải được danh sách chứng chỉ");
      const data=await response.json();
      if(!data || !Array.isArray(data.esign) || !Array.isArray(data.ksign))
        throw new Error("Dữ liệu nguồn không đúng định dạng");
      inventory=data;
      const stamp=document.getElementById("eri-free-source-stamp");
      if(stamp)stamp.textContent=data.updated_at?
        "Dữ liệu nguồn: "+data.updated_at+" · Khoindvn":"Nguồn: Khoindvn";
      render();
    }catch(err){
      if(grid)grid.replaceChildren(node("div","eri-free-empty",
        "Không mở được danh sách. Vui lòng tải lại ERI Store và thử lại."));
      if(status)status.textContent=err.message||"Lỗi kết nối.";
    }finally{if(trigger)trigger.disabled=false;}
  }
  trigger?.addEventListener("click",load);
  load();
})();
