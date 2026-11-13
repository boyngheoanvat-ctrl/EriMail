"use strict";
const API="https://eri-api-dag8.onrender.com";
const KINDS={
  mod:{endpoint:"mods",title:"Mod Game",icon:"🎮",type:"mod"},
  ai:{endpoint:"products",title:"AI Plus",icon:"✦",type:"ai"},
  certificate:{endpoint:"certificates",title:"Chứng chỉ",icon:"✓",type:"certificate"}
};
const $=id=>document.getElementById(id);
const TOKEN_KEY="eri_store_buyer_token";
const state={
  category:"mod",view:"store",items:[],
  token:sessionStorage.getItem(TOKEN_KEY)||localStorage.getItem(TOKEN_KEY)||"",
  remembered:Boolean(localStorage.getItem(TOKEN_KEY)),buyer:null,pending:null
};
function saveAccountOnDevice(remember){
  if(!state.token)return false;
  try{
    if(remember){
      localStorage.setItem(TOKEN_KEY,state.token);
      sessionStorage.removeItem(TOKEN_KEY);
    }else{
      sessionStorage.setItem(TOKEN_KEY,state.token);
      localStorage.removeItem(TOKEN_KEY);
    }
    state.remembered=remember;
    return true;
  }catch{
    showBanner("Không thể lưu phiên đăng nhập trên thiết bị này. Hãy kiểm tra chế độ riêng tư của trình duyệt.");
    return false;
  }
}
const money=n=>new Intl.NumberFormat("vi-VN",{maximumFractionDigits:0}).format(Number(n)||0)+"đ";
const element=(tag,cls,content)=>{
  const node=document.createElement(tag);
  if(cls)node.className=cls;
  if(content!==undefined&&content!==null)node.textContent=String(content);
  return node;
};
function errorMessage(err){
  if(err instanceof TypeError)return "Không thể kết nối cửa hàng. Vui lòng thử lại sau.";
  return err.message||"Không thể thực hiện yêu cầu.";
}
function showBanner(message){
  $("banner").textContent=message||"";
  $("banner").classList.toggle("hidden",!message);
}
async function request(path,{method="GET",data,authorized=false}={}){
  const headers={};
  if(data!==undefined)headers["Content-Type"]="application/json";
  if(authorized){
    if(!state.token)throw new Error("Bạn cần đăng nhập trước khi mua.");
    headers.Authorization="Bearer "+state.token;
  }
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),25000);
  let response;
  try{
    response=await fetch(API+path,{method,headers,body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal,cache:"no-store"});
  }catch(e){clearTimeout(timeout);if(e.name==="AbortError")throw new Error("Máy chủ phản hồi quá lâu. Vui lòng thử lại.");throw e}
  clearTimeout(timeout);
  let result={};
  try{result=await response.json()}catch{result={}}
  if(!response.ok){
    if(response.status===401&&authorized){signOut(false);throw new Error("Phiên đăng nhập hết hạn. Vui lòng đăng nhập lại.")}
    if(response.status===404&&path.startsWith("/api/v1/auth/"))throw new Error("Tính năng tài khoản đang tạm thời không khả dụng. Vui lòng thử lại sau.");
    const detail=typeof result.detail==="string"?result.detail:"Máy chủ trả về HTTP "+response.status;
    throw new Error(detail);
  }
  return result;
}
function openDialog(title){
  $("dialog-title").textContent=title;
  $("dialog-body").replaceChildren();
  $("overlay").classList.remove("hidden");
  document.body.style.overflow="hidden";
}
function closeDialog(){
  $("overlay").classList.add("hidden");
  document.body.style.overflow="";
}
function button(label,cls,click){
  const b=element("button",cls,label);
  b.type="button";b.addEventListener("click",click);return b;
}
function setView(view){
  if(!["store","orders","account"].includes(view))view="store";
  state.view=view;
  $("store").classList.toggle("hidden",view!=="store");
  $("hero").classList.toggle("hidden",view!=="store");
  document.querySelector(".features").classList.toggle("hidden",view!=="store");
  $("orders-view").classList.toggle("hidden",view!=="orders");
  $("account-view").classList.toggle("hidden",view!=="account");
  document.querySelectorAll(".nav-button").forEach(b=>{
    const active=b.dataset.view===view;b.classList.toggle("selected",active);
    if(active)b.setAttribute("aria-current","page");else b.removeAttribute("aria-current");
  });
  if(view==="orders")loadOrders();
  if(view==="account")showProfile();
  window.scrollTo({top:0,behavior:"smooth"});
}
function updateHeader(){
  $("header-account").textContent=state.buyer?.display_name?.trim()?.slice(0,1)?.toUpperCase()||"ĐN";
}
async function loadCatalog(){
  $("catalog-message").textContent="Đang tải "+KINDS[state.category].title+"...";
  const grid=$("products");grid.replaceChildren();
  grid.append(element("div","placeholder"),element("div","placeholder"),element("div","placeholder"));
  try{
    const data=await request("/api/v1/"+KINDS[state.category].endpoint);
    if(!Array.isArray(data.items))throw new Error("Danh mục chưa đúng định dạng.");
    state.items=data.items;
    grid.replaceChildren();
    $("catalog-message").textContent=data.items.length?data.items.length+" sản phẩm đang hiển thị":"";
    if(!data.items.length){grid.append(element("div","empty","Danh mục hiện chưa có sản phẩm. Vui lòng quay lại sau."));return}
    data.items.forEach(item=>{
      const card=element("article","product");
      const visual=element("div","product-art",KINDS[state.category].icon);
      const body=element("div","product-body");
      body.append(element("h3",null,item.name));
      body.append(element("p","product-desc",item.description||"Sản phẩm từ ERI STORE"));
      const extra=state.category==="mod"?(item.version?"Phiên bản "+item.version:"Sản phẩm số"):state.category==="certificate"?(item.issuer||"Sản phẩm số"):"Sản phẩm số";
      body.append(element("div","product-info",extra));
      const footer=element("div","product-footer");
      footer.append(element("strong","price",money(item.price_vnd)));
      footer.append(button("Mua ngay","btn primary",()=>buy(item.id,state.category)));
      body.append(footer);card.append(visual,body);grid.append(card);
    });
  }catch(e){
    grid.replaceChildren(element("div","empty",errorMessage(e)));
    $("catalog-message").textContent="Không tải được sản phẩm";
    showBanner(errorMessage(e));
  }
}
async function restoreSession(){
  if(!state.token){updateHeader();return}
  try{state.buyer=await request("/api/v1/auth/me",{authorized:true})}
  catch(e){state.buyer=null;showBanner(errorMessage(e))}
  updateHeader();
}
function signOut(clear=true){
  const oldToken=state.token;
  state.token="";state.buyer=null;state.remembered=false;localStorage.removeItem(TOKEN_KEY);sessionStorage.removeItem(TOKEN_KEY);updateHeader();
  if(clear&&oldToken){
    fetch(API+"/api/v1/auth/logout",{method:"POST",headers:{Authorization:"Bearer "+oldToken}}).catch(()=>{});
  }
  if(state.view==="account")showProfile();
}
function authModal(register=false){
  openDialog(register?"Đăng ký ERI STORE":"Đăng nhập ERI STORE");
  const root=$("dialog-body");
  const form=element("form");
  const controls=[
    ["username","Tên đăng nhập","text",true],
    ["password","Mật khẩu","password",true]
  ];
  if(register)controls.push(["display_name","Tên người nhận","text",true],["contact","Liên hệ giao hàng (Zalo / SĐT)","text",true]);
  const inputs={};
  for(const [name,title,type,required] of controls){
    const label=element("label","field",title);
    const input=element("input");input.name=name;input.type=type;input.required=required;input.autocomplete=name==="username"?"username":name==="password"?(register?"new-password":"current-password"):"on";
    if(name==="username"){input.minLength=3;input.maxLength=32;input.pattern="[a-zA-Z0-9_.-]{3,32}"}
    if(name==="password"&&register)input.minLength=10;
    label.append(input);form.append(label);inputs[name]=input;
  }
  const rememberLabel=element("label","remember-row");
  const rememberBox=element("input");
  rememberBox.type="checkbox";rememberBox.checked=state.remembered;
  rememberLabel.append(rememberBox,element("span",null,"Lưu tài khoản trên thiết bị này"));
  form.append(rememberLabel);
  form.append(element("p","small","Giữ đăng nhập khi mở lại ERI STORE. Không lưu mật khẩu."));
  const failure=element("p","form-error");failure.setAttribute("role","alert");
  const actions=element("div","dialog-actions");
  const submit=element("button","btn primary",register?"Tạo tài khoản":"Đăng nhập");submit.type="submit";
  actions.append(submit,button(register?"Đã có tài khoản? Đăng nhập":"Chưa có tài khoản? Đăng ký","btn outline",()=>authModal(!register)));
  form.append(failure,actions);
  form.addEventListener("submit",async e=>{
    e.preventDefault();submit.disabled=true;failure.textContent="";
    try{
      const body={username:inputs.username.value.trim(),password:inputs.password.value};
      if(register){body.display_name=inputs.display_name.value.trim();body.contact=inputs.contact.value.trim()}
      const result=await request("/api/v1/auth/"+(register?"register":"login"),{method:"POST",data:body});
      if(!result.token||!result.customer)throw new Error("Phản hồi đăng nhập chưa đúng định dạng.");
      state.token=result.token;state.buyer=result.customer;
      saveAccountOnDevice(rememberBox.checked);updateHeader();
      closeDialog();if(state.view==="account")showProfile();
      if(state.pending){const previous=state.pending;state.pending=null;await buy(previous.id,previous.kind)}
    }catch(err){failure.textContent=errorMessage(err)}finally{submit.disabled=false}
  });
  root.append(form);
}
async function buy(id,kind){
  if(!state.buyer){state.pending={id,kind};authModal();return}
  openDialog("Đang tạo đơn");
  $("dialog-body").append(element("p","muted","Đang tạo đơn hàng và lấy thông tin MoMo..."));
  try{
    const order=await request("/api/v1/orders",{method:"POST",authorized:true,data:{kind,product_id:id}});
    showOrder(order);
  }catch(e){
    openDialog("Không thể đặt hàng");
    $("dialog-body").append(element("p","form-error",errorMessage(e)));
    $("dialog-body").append(button("Đóng","btn outline",closeDialog));
  }
}

// Prepare a short-lived, browser-native download through the same-origin
// service worker. The bearer token never appears in an URL or download link.
async function prepareNativeDownload(order){
  if(!("serviceWorker" in navigator) || !("MessageChannel" in window)){
    throw new Error("Trình duyệt chưa hỗ trợ tải file lớn. Hãy mở ERI STORE bằng Safari mới nhất.");
  }
  const ready=await Promise.race([
    navigator.serviceWorker.ready,
    new Promise((_,reject)=>setTimeout(()=>reject(new Error("Chưa khởi động được trình tải file. Hãy làm mới trang rồi thử lại.")),8000))
  ]);
  if(!ready.active)throw new Error("Trình tải file chưa sẵn sàng. Hãy làm mới trang.");
  if(!navigator.serviceWorker.controller){
    await Promise.race([
      new Promise(resolve=>navigator.serviceWorker.addEventListener("controllerchange",resolve,{once:true})),
      new Promise(resolve=>setTimeout(resolve,3500))
    ]);
  }
  const controller=navigator.serviceWorker.controller;
  if(!controller)throw new Error("Hãy làm mới trang ERI STORE một lần để bật tính năng tải file.");
  return await new Promise((resolve,reject)=>{
    const channel=new MessageChannel();
    const timer=setTimeout(()=>{
      channel.port1.close();
      reject(new Error("Không chuẩn bị được lượt tải. Hãy thử lại."));
    },8000);
    channel.port1.onmessage=event=>{
      clearTimeout(timer);channel.port1.close();
      const response=event.data||{};
      if(response.ok && typeof response.url==="string" &&
         /^\/_eri_download\/[A-Za-z0-9_-]{20,60}$/.test(response.url)){
        resolve(response.url);
      }else{
        reject(new Error(response.error||"Không thể chuẩn bị file."));
      }
    };
    controller.postMessage({
      type:"ERI_PREPARE_DOWNLOAD",
      orderId:order.id,
      token:state.token,
      filename:order.delivery.filename
    },[channel.port2]);
  });
}
async function downloadDelivery(order){
  if(!state.token){authModal();return}
  const path=order.delivery?.download_path;
  if(typeof path!=="string" ||
     path!=="/api/v1/me/orders/"+order.id+"/delivery" ||
     !/^\/api\/v1\/me\/orders\/[A-F0-9]{12}\/delivery$/.test(path)){
    showBanner("Đơn này chưa có file được giao.");return;
  }
  try{
    const localPath=await prepareNativeDownload(order);
    openDialog("Tải file đã giao");
    const root=$("dialog-body");
    root.append(element("p",null,"Tệp: "+(order.delivery.filename||"Sản phẩm đã mua")));
    if(Number(order.delivery.size_bytes)>0){
      root.append(element("p","small","Dung lượng: "+(Number(order.delivery.size_bytes)/1048576).toFixed(1)+" MB"));
    }
    const info=element("p",null,"Nhấn nút dưới đây để Safari tải file trực tiếp về thiết bị. File sẽ nằm trong mục Tệp → Tải về (Downloads).");
    root.append(info);
    const link=element("a","btn primary","⇩ Bắt đầu tải file");
    link.href=localPath;
    link.download=order.delivery.filename||"eri-delivery";
    root.append(link);
    root.append(element("p","small","Giữ ERI STORE đang mở trong lúc tải. Nếu iPhone hỏi xác nhận tải xuống, hãy chọn Tải về. Nếu không thấy file, kiểm tra ứng dụng Tệp → Tải về."));
    root.append(button("Đóng","btn outline",closeDialog));
    showBanner("");
  }catch(err){showBanner(errorMessage(err))}
}

function showOrder(order){
  openDialog("Đơn hàng "+(order.id||""));
  const root=$("dialog-body");
  root.append(element("h3",null,order.product_name||"Sản phẩm ERI"));
  const summary=element("div","order-highlight");
  summary.append(element("div","small","Số tiền cần thanh toán"));
  summary.append(element("b",null,money(order.amount_vnd)));
  summary.append(element("div","small","Mã đơn: "+order.id));
  root.append(summary);
  if(order.status==="pending"){
    if(order.qr_url){
      const path=String(order.qr_url);
      let imageURL="";
      if(path==="/api/v1/payment/momo-qr")imageURL=API+path;
      if(path.startsWith("https://img.vietqr.io/image/"))imageURL=path;
      if(imageURL){
        root.append(element("p",null,"Quét mã QR để chuyển khoản"));
        const img=element("img","qr");img.src=imageURL;img.alt="Mã QR thanh toán";
        img.onerror=()=>{img.replaceWith(element("p","form-error","Không tải được ảnh QR. Liên hệ cửa hàng trước khi chuyển tiền."))};
        root.append(img);
      }
    }else if(order.payment_setup_required){
      root.append(element("p","form-error","Mã QR thanh toán đang được cập nhật. Vui lòng thử lại sau."));
    }
    root.append(element("p",null,"Vui lòng chuyển đúng số tiền và nội dung thanh toán hiển thị trong đơn."));
    if(order.transfer_note){
      root.append(element("p",null,"Nội dung chuyển khoản: "+order.transfer_note));
      root.append(button("Sao chép nội dung","btn outline",async()=>{try{await navigator.clipboard.writeText(order.transfer_note)}catch{}}));
    }
  }else{
    const statuses={paid:"Đã nhận tiền, đang chờ giao",fulfilled:"Đã giao hàng",cancelled:"Đơn đã hủy"};
    root.append(element("p",null,statuses[order.status]||"Trạng thái: "+order.status));
  }
  if(order.status==="fulfilled"&&order.delivery?.download_path){
    root.append(element("p",null,"Tệp của bạn đã sẵn sàng: "+order.delivery.filename));
    root.append(button("⇩ Tải file đã giao","btn primary",()=>downloadDelivery(order)));
  }
  root.append(element("p","small","Lưu mã đơn để theo dõi thanh toán và nhận sản phẩm trong mục Đơn hàng."));
  const actions=element("div","dialog-actions");
  actions.append(button("Đóng","btn outline",closeDialog));
  actions.append(button("Xem đơn hàng","btn primary",()=>{closeDialog();setView("orders")}));
  root.append(actions);
}
async function loadOrders(){
  const root=$("orders");root.replaceChildren();
  if(!state.buyer){root.append(element("div","empty","Đăng nhập để xem lịch sử mua của riêng bạn."));root.append(button("Đăng nhập / Đăng ký","btn primary",()=>authModal()));return}
  root.append(element("p","muted","Đang tải đơn hàng..."));
  try{
    const r=await request("/api/v1/me/orders",{authorized:true});
    root.replaceChildren();
    if(!Array.isArray(r.items)||!r.items.length){root.append(element("div","empty","Bạn chưa có đơn hàng."));return}
    for(const order of r.items){
      const entry=element("article","order");
      entry.append(element("h3",null,order.product_name));
      entry.append(element("p",null,"Mã đơn: "+order.id));
      const names={pending:"Chờ xác nhận tiền",paid:"Đã nhận tiền — chờ giao",fulfilled:"Đã giao hàng",cancelled:"Đã hủy"};
      entry.append(element("p",null,money(order.amount_vnd)+" · "+(names[order.status]||order.status)));
      entry.append(button("Xem chi tiết","btn outline",async()=>{
        try{showOrder(await request("/api/v1/orders/"+encodeURIComponent(order.id),{authorized:true}))}
        catch(err){showBanner(errorMessage(err))}
      }));
      if(order.status==="fulfilled"&&order.delivery?.download_path){
        entry.append(" ");
        entry.append(button("⇩ Tải file","btn primary",()=>downloadDelivery(order)));
      }
      root.append(entry);
    }
  }catch(e){root.replaceChildren(element("div","empty",errorMessage(e)))}
}
function showProfile(){
  const root=$("profile");root.replaceChildren();
  if(!state.buyer){
    root.append(element("h3",null,"Đăng nhập ERI STORE"));
    root.append(element("p",null,"Mỗi khách có tài khoản và lịch sử đơn riêng. Đăng ký miễn phí để mua hàng."));
    root.append(button("Đăng nhập","btn primary",()=>authModal()));
    root.append(" ");
    root.append(button("Đăng ký","btn outline",()=>authModal(true)));
    return;
  }
  root.append(element("h3",null,state.buyer.display_name));
  root.append(element("p",null,"Tên đăng nhập: @"+state.buyer.username));
  root.append(element("p",null,"Liên hệ giao hàng: "+state.buyer.contact));
  const savedInfo=element("p","small",state.remembered
    ?"Tài khoản đã được lưu trên thiết bị này. Không lưu mật khẩu."
    :"Tài khoản chưa được lưu. Bạn sẽ cần đăng nhập lại sau khi đóng trình duyệt.");
  root.append(savedInfo);
  root.append(button(state.remembered?"Bỏ lưu tài khoản":"Lưu tài khoản","btn outline",()=>{
    const wasRemembered=state.remembered;
    if(saveAccountOnDevice(!wasRemembered)){showBanner("");showProfile()}
  }));
  root.append(" ");
  root.append(button("Xem đơn của tôi","btn primary",()=>setView("orders")));
  root.append(" ");
  root.append(button("Đăng xuất","btn danger",()=>signOut()));
}
document.addEventListener("DOMContentLoaded",()=>{
  $("dialog-close").addEventListener("click",closeDialog);
  $("overlay").addEventListener("click",event=>{if(event.target===$("overlay"))closeDialog()});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")closeDialog()});
  $("header-account").addEventListener("click",()=>setView("account"));
  $("explore").addEventListener("click",()=>{$("store").scrollIntoView({behavior:"smooth"})});
  $("hero-orders").addEventListener("click",()=>setView("orders"));
  $("reload").addEventListener("click",loadCatalog);
  $("refresh-orders").addEventListener("click",loadOrders);
  document.querySelectorAll("[data-kind]").forEach(tab=>tab.addEventListener("click",()=>{
    state.category=tab.dataset.kind;
    document.querySelectorAll("[data-kind]").forEach(b=>{const active=b===tab;b.classList.toggle("selected",active);b.setAttribute("aria-selected",String(active))});
    loadCatalog();
  }));
  document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>setView(b.dataset.view)));
  updateHeader();loadCatalog();restoreSession();
  if("serviceWorker" in navigator && location.protocol==="https:")navigator.serviceWorker.register("/service-worker.js").catch(()=>{});
});
