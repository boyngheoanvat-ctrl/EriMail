"use strict";
const API="https://eri-api-dag8.onrender.com";
const KINDS={
  mod:{endpoint:"mods",title:"Mod Game",icon:"🎮",type:"mod"},
  ai:{endpoint:"products",title:"AI Plus",icon:"✦",type:"ai"},
  certificate:{endpoint:"certificates",title:"Chứng chỉ",icon:"✓",type:"certificate"}
};
const $=id=>document.getElementById(id);
const state={category:"mod",view:"store",items:[],token:localStorage.getItem("eri_store_buyer_token")||"",buyer:null,pending:null};
const money=n=>new Intl.NumberFormat("vi-VN",{maximumFractionDigits:0}).format(Number(n)||0)+"đ";
const element=(tag,cls,content)=>{
  const node=document.createElement(tag);
  if(cls)node.className=cls;
  if(content!==undefined&&content!==null)node.textContent=String(content);
  return node;
};
function errorMessage(err){
  if(err instanceof TypeError)return "Không thể kết nối API. Máy chủ có thể chưa mở CORS cho website hoặc đang tạm ngừng.";
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
    if(response.status===404&&path.startsWith("/api/v1/auth/"))throw new Error("Máy chủ chưa triển khai chức năng tài khoản. Cần cập nhật backend Render.");
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
    if(!data.items.length){grid.append(element("div","empty","Danh mục chưa có sản phẩm. Admin có thể đăng sản phẩm để bán."));return}
    data.items.forEach(item=>{
      const card=element("article","product");
      const visual=element("div","product-art",KINDS[state.category].icon);
      const body=element("div","product-body");
      body.append(element("h3",null,item.name));
      body.append(element("p","product-desc",item.description||"Sản phẩm từ ERI STORE"));
      const extra=state.category==="mod"?(item.version?"Phiên bản "+item.version:"Giao sau xác nhận thanh toán"):state.category==="certificate"?(item.issuer||"Giao hàng thủ công"):"Giao hàng thủ công";
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
  state.token="";state.buyer=null;localStorage.removeItem("eri_store_buyer_token");updateHeader();
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
      localStorage.setItem("eri_store_buyer_token",result.token);updateHeader();showBanner("");
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
      root.append(element("p","form-error","Chủ cửa hàng chưa liên kết mã QR MoMo. Vui lòng liên hệ trước khi chuyển tiền."));
    }
    root.append(element("p",null,order.payment_instructions||"Chuyển đúng số tiền và đợi người bán xác nhận."));
    if(order.transfer_note){
      root.append(element("p",null,"Nội dung chuyển khoản: "+order.transfer_note));
      root.append(button("Sao chép nội dung","btn outline",async()=>{try{await navigator.clipboard.writeText(order.transfer_note)}catch{}}));
    }
  }else{
    const statuses={paid:"Đã nhận tiền, đang chờ giao",fulfilled:"Đã giao hàng",cancelled:"Đơn đã hủy"};
    root.append(element("p",null,statuses[order.status]||"Trạng thái: "+order.status));
  }
  root.append(element("p","small","Lưu mã đơn và liên hệ Admin để nhận hàng. Việc thanh toán không được xác nhận tự động; Admin chỉ đánh dấu Đã nhận tiền sau khi kiểm tra giao dịch MoMo."));
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
  root.append(button("Xem đơn của tôi","btn primary",()=>setView("orders")));
  root.append(" ");
  root.append(button("Đăng xuất","btn danger",()=>signOut()));
}
function showInstall(){
  openDialog("Thêm ERI STORE vào iPhone");
  const root=$("dialog-body");
  const ua=navigator.userAgent;
  if(/iPhone|iPad|iPod/.test(ua)){
    root.append(element("p",null,"1. Mở website ERI STORE bằng trình duyệt Safari."));
    root.append(element("p",null,"2. Chạm nút Chia sẻ (hình vuông có mũi tên lên)."));
    root.append(element("p",null,"3. Chọn Thêm vào Màn hình chính → Thêm."));
    root.append(element("p","small","iPhone không cần mua chứng chỉ hoặc cài IPA. Nếu đang mở bằng trình duyệt trong ứng dụng khác, hãy mở lại bằng Safari."));
  }else{
    root.append(element("p",null,"Trong Chrome hoặc trình duyệt tương thích, mở menu ⋮ và chọn Cài đặt ứng dụng hoặc Thêm vào Màn hình chính."));
    root.append(element("p","small","Không cần cài APK. Website hoạt động trên điện thoại và máy tính có kết nối mạng."));
  }
  root.append(button("Đã hiểu","btn primary",closeDialog));
}
document.addEventListener("DOMContentLoaded",()=>{
  $("dialog-close").addEventListener("click",closeDialog);
  $("overlay").addEventListener("click",event=>{if(event.target===$("overlay"))closeDialog()});
  document.addEventListener("keydown",event=>{if(event.key==="Escape")closeDialog()});
  $("header-account").addEventListener("click",()=>setView("account"));
  $("explore").addEventListener("click",()=>{$("store").scrollIntoView({behavior:"smooth"})});
  for(const id of ["hero-install","install-button"])$(id).addEventListener("click",showInstall);
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
