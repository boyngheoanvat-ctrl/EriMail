/* ERI Store: one iOS manifest link per KSign / ESign variant. */
(function(){
  "use strict";
  const panel=document.getElementById("certificate-free-panel");
  const cards=document.getElementById("eri-free-cards");
  const status=document.getElementById("eri-free-status");
  if(!panel||!cards||!status)return;
  const tabs=Array.from(panel.querySelectorAll("[data-sign-app]"));
  let selected="ksign",catalog=null,loading=null;
  function validEntry(item){
    const prefix="itms-services://?action=download-manifest&url=";
    if(!item||typeof item.name!=="string"||typeof item.descriptions!=="string"||typeof item.url!=="string"||!item.url.startsWith(prefix))return false;
    try{
      const manifest=new URL(item.url.slice(prefix.length));
      const path=manifest.pathname;
      return manifest.protocol==="https:"&&manifest.hostname==="website.khoindvn.io.vn"&&
        path.startsWith("/plists/")&&path.endsWith(".plist")&&
        /^[a-z]+$/.test(path.slice("/plists/".length,-".plist".length));
    }catch(_){return false;}
  }
  function element(tag,cls,txt){
    const el=document.createElement(tag);
    if(cls)el.className=cls;
    if(txt!==undefined)el.textContent=txt;
    return el;
  }
  function render(){
    for(const tab of tabs){
      const active=tab.dataset.signApp===selected;
      tab.classList.toggle("is-active",active);
      tab.setAttribute("aria-pressed",String(active));
    }
    cards.replaceChildren();
    if(!catalog)return;
    const entries=catalog[selected];
    status.textContent=(selected==="ksign"?"KSign":"ESign")+" · "+entries.length+" bản cài đặt";
    const fragment=document.createDocumentFragment();
    entries.forEach((item,index)=>{
      const card=element("article","eri-free-card");
      const mark=element("div","eri-free-card-mark",selected==="ksign"?"K":"E");
      mark.setAttribute("aria-hidden","true");
      const info=element("div","eri-free-card-body");
      info.append(element("h4","eri-free-card-title",item.name),element("p","eri-free-card-issuer","Chứng chỉ: "+item.descriptions));
      const number=element("span","eri-free-card-index",String(index+1).padStart(2,"0"));
      const install=element("a","btn primary eri-free-install","Cài đặt");
      install.setAttribute("href",item.url);
      install.setAttribute("aria-label","Cài "+item.name+" - "+item.descriptions);
      card.append(mark,info,number,install);
      fragment.append(card);
    });
    cards.append(fragment);
  }
  async function loadFreeCertificates(){
    if(catalog){render();return;}
    if(loading)return loading;
    status.textContent="Đang tải danh sách KSign / ESign...";
    loading=(async()=>{
      try{
        const response=await fetch("/install-tools.json",{cache:"no-store"});
        if(!response.ok)throw new Error("HTTP "+response.status);
        const parsed=await response.json();
        if(!parsed||!["ksign","esign"].every(type=>Array.isArray(parsed[type])&&parsed[type].length===17&&parsed[type].every(validEntry)))throw new Error("Danh sách không hợp lệ");
        const urls=[...parsed.ksign,...parsed.esign].map(item=>item.url);
        if(new Set(urls).size!==urls.length)throw new Error("Trùng đường dẫn cài đặt");
        catalog=parsed;
        document.getElementById("eri-ksign-count").textContent=String(catalog.ksign.length);
        document.getElementById("eri-esign-count").textContent=String(catalog.esign.length);
        render();
      }catch(_){
        cards.replaceChildren();
        status.textContent="Không tải được danh sách chứng chỉ Free. Vui lòng tải lại trang để thử lại.";
      }finally{loading=null;}
    })();
    return loading;
  }
  tabs.forEach(tab=>tab.addEventListener("click",()=>{
    selected=tab.dataset.signApp==="esign"?"esign":"ksign";
    if(catalog)render();else loadFreeCertificates();
  }));
  window.loadFreeCertificates=loadFreeCertificates;
})();
