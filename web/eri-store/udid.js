"use strict";
// Collect UDID only after buyer consent through Apple's temporary Profile
// Service. The profile may disappear after its one-time device callback.
const UDID_PENDING_KEY="eri_store_pending_udid_v2";
const UDID_LIFETIME_MS=15*60*1000;
let udidPollTimer=null;
let udidActiveOrder=null;
let udidPollBusy=false;

function udidStopPoll(){
  if(udidPollTimer!==null){clearInterval(udidPollTimer);udidPollTimer=null}
  udidActiveOrder=null;
}
function udidPending(){
  try{
    const raw=localStorage.getItem(UDID_PENDING_KEY);
    if(!raw)return null;
    const data=JSON.parse(raw);
    if(!/^[A-F0-9]{12}$/.test(data.orderId||"") ||
       !/^[A-Za-z0-9_-]{24,64}$/.test(data.token||"") ||
       !Number.isFinite(data.expiresAt)||Date.now()>data.expiresAt){
      localStorage.removeItem(UDID_PENDING_KEY);return null;
    }
    return data;
  }catch{return null}
}
function udidRemember(orderId,scan){
  try{
    localStorage.setItem(UDID_PENDING_KEY,JSON.stringify({
      orderId,token:scan.scan_token,
      expiresAt:Date.now()+Math.min(Number(scan.expires_in_seconds||900)*1000,UDID_LIFETIME_MS)
    }));
  }catch{
    // Browser storage can be unavailable. Polling still works in this tab.
  }
}
function udidClearPending(){
  try{localStorage.removeItem(UDID_PENDING_KEY)}catch{}
}
function udidIsIOS(){
  return /iPhone|iPod|iPad/.test(navigator.userAgent) ||
    (navigator.platform==="MacIntel"&&navigator.maxTouchPoints>1);
}
function udidStandalone(){
  return Boolean(navigator.standalone) ||
    window.matchMedia?.("(display-mode: standalone)")?.matches===true;
}
function udidSaved(order,root){
  if(!order.device_udid)return false;
  root.append(element("p",null,"UDID đã ghi nhận: "+order.device_udid));
  root.append(element("p","small",order.device_udid_source==="apple_profile"
    ?"Đã xác minh chữ ký thiết bị Apple và chuyển cho ERI ADMIN."
    :"Khách tự nhập. Chưa xác minh bằng hồ sơ Apple."));
  root.append(button("Sao chép UDID","btn outline",()=>{
    navigator.clipboard?.writeText(order.device_udid).catch(()=>{});
  }));
  return true;
}
function udidFinishPanel(root,udid,source){
  root.replaceChildren();
  root.append(element("h3",null,"✓ Đã nhận UDID thành công"));
  root.append(element("p",null,udid));
  root.append(element("p","small",source==="apple_profile"
    ?"UDID đã được iPhone gửi và máy chủ xác minh. Đã chuyển cho ERI ADMIN."
    :"UDID được nhập thủ công. Cửa hàng cần đối chiếu trước khi đăng ký chứng chỉ."));
  root.append(button("Sao chép UDID","btn outline",()=>{
    navigator.clipboard?.writeText(udid).catch(()=>{});
  }));
  root.append(" ");
  root.append(button("Xem đơn hàng","btn primary",()=>{
    closeDialog();setView("orders");
  }));
}
async function udidQueryScan(orderId,token){
  return await request("/api/v1/me/certificate-orders/"+orderId+
    "/udid-sessions/"+encodeURIComponent(token),{authorized:true});
}
async function udidQuerySavedOrder(orderId){
  const data=await request("/api/v1/me/orders",{authorized:true});
  return (data.items||[]).find(x=>x.kind==="certificate"&&x.id===orderId)||null;
}
async function udidCheckNow(orderId,token,feedback,steps){
  if(udidPollBusy)return;
  udidPollBusy=true;
  try{
    const progress=await udidQueryScan(orderId,token);
    if(progress.complete&&progress.udid){
      udidStopPoll();udidClearPending();
      feedback.textContent="✓ UDID đã gửi đến ERI ADMIN.";
      udidFinishPanel(steps,progress.udid,progress.source);
      return;
    }
    // The signed callback can finish after iOS has already left this tab.
    const saved=await udidQuerySavedOrder(orderId);
    if(saved?.device_udid && saved.device_udid_source==="apple_profile"){
      udidStopPoll();udidClearPending();
      feedback.textContent="✓ Đã nhận UDID từ iPhone.";
      udidFinishPanel(steps,saved.device_udid,saved.device_udid_source);
      return;
    }
    feedback.textContent="Chưa nhận được UDID. Nếu bạn đã cài hồ sơ, quay lại đây và nhấn Kiểm tra lại.";
  }catch(err){
    if(/hết hạn|expired|410/i.test(errorMessage(err))){
      udidStopPoll();udidClearPending();
      feedback.textContent="Lượt lấy UDID đã hết hạn. Hãy đóng và bắt đầu lại.";
    }else{
      feedback.textContent="Chưa kiểm tra được UDID: "+errorMessage(err);
    }
  }finally{udidPollBusy=false}
}
function udidStartPolling(orderId,token,feedback,steps){
  udidStopPoll();
  udidActiveOrder={orderId,token,feedback,steps};
  udidPollTimer=setInterval(()=>{
    if($("overlay").classList.contains("hidden")||
       !$("dialog-title").textContent.includes("UDID")){udidStopPoll();return}
    void udidCheckNow(orderId,token,feedback,steps);
  },3500);
}
function udidAddSafariHelp(container,url){
  const ios=udidIsIOS();
  const standalone=udidStandalone();
  if(ios){
    container.append(element("p",null,standalone?
      "Bạn đang mở ERI STORE như ứng dụng trên Màn hình chính. Để tải hồ sơ iOS, hãy dùng Safari trực tiếp.":
      "Dùng Safari trên chính iPhone/iPad cần đăng ký UDID. Nếu đang ở trình duyệt trong ứng dụng khác, hãy mở bằng Safari."));
    if(standalone){
      const action=element("a","btn outline","Mở ERI STORE bằng Safari");
      action.href="https://eri-store-web.onrender.com/";
      action.target="_blank";
      action.rel="noopener noreferrer";
      container.append(action);
    }
  }else{
    container.append(element("p",null,
      "Hồ sơ chỉ tải và cài trên iPhone/iPad. Hãy quét QR bằng Safari trên thiết bị cần lấy UDID."));
  }
  const copy=button("Sao chép liên kết hồ sơ","btn outline",async()=>{
    try{
      await navigator.clipboard.writeText(url);
      copy.textContent="✓ Đã sao chép, mở liên kết trong Safari";
    }catch{
      const text=element("p","small",url);
      container.append(text);
    }
  });
  container.append(copy);
}
function startUdidForOrder(order){
  if(!state.buyer){authModal();return}
  if(order.kind!=="certificate")return;
  udidStopPoll();
  openDialog("Lấy UDID iPhone / iPad");
  const root=$("dialog-body");
  root.append(element("h3",null,order.product_name));
  root.append(element("p","small","Mã đơn: "+order.id));
  if(order.device_udid){
    const exists=element("div","udid-saved");
    udidSaved(order,exists);
    root.append(exists);
  }
  root.append(element("p",null,
    "Hồ sơ này chỉ dùng một lần để lấy UDID, không phải chứng chỉ ký ứng dụng và không cần tồn tại trong Cài đặt sau khi UDID đã gửi."));
  root.append(element("p","small",
    "Bạn cần tự đồng ý cài hồ sơ. Thiết bị chỉ gửi UDID, không gửi IMEI, số sê-ri hoặc mật khẩu."));
  const consent=element("label","remember-row");
  const agreement=element("input");agreement.type="checkbox";
  consent.append(agreement,element("span",null,
    "Tôi đồng ý gửi UDID của thiết bị để xử lý đơn Chứng chỉ này."));
  root.append(consent);
  const steps=element("div","udid-steps");
  root.append(steps);
  const feedback=element("p","small");feedback.setAttribute("role","status");
  root.append(feedback);
  const start=button("Bắt đầu lấy UDID","btn primary",async()=>{
    if(!agreement.checked)return;
    start.disabled=true;feedback.textContent="Đang tạo hồ sơ iOS cho đơn hàng...";
    try{
      const scan=await request(
        "/api/v1/me/certificate-orders/"+order.id+"/udid-sessions",
        {method:"POST",authorized:true,data:{}});
      const origin=API+"/api/v1/cert-udid/";
      if(!/^[A-Za-z0-9_-]{24,64}$/.test(scan.scan_token||"")||
         !String(scan.profile_url||"").startsWith(origin)||
         !String(scan.qr_url||"").startsWith(origin))
        throw new Error("Không thể tạo hồ sơ UDID hợp lệ.");
      udidRemember(order.id,scan);
      steps.replaceChildren();
      const instructions=element("div","udid-directions");
      instructions.append(element("p",null,
        "1. Mở bằng Safari trên iPhone/iPad muốn lấy UDID."));
      udidAddSafariHelp(instructions,scan.profile_url);
      instructions.append(element("p",null,
        "2. Chạm Tải hồ sơ UDID và cho phép tải. Vào Cài đặt → Hồ sơ đã tải về (hoặc Cài đặt chung → VPN & Quản lý thiết bị) → Cài đặt."));
      instructions.append(element("p","small",
        "Apple chỉ giữ hồ sơ đang chờ trong khoảng 8 phút. Hãy cài ngay sau khi tải; chỉ tải một hồ sơ mỗi lần."));
      const link=element("a","btn primary","⇩ Tải hồ sơ UDID (Safari)");
      link.href=scan.profile_url;
      link.rel="noreferrer";
      instructions.append(link);
      instructions.append(element("p",null,
        "3. iPhone có thể tự chuyển về website và xóa hồ sơ tạm. Đây là bình thường nếu UDID đã được gửi. Quay lại ERI STORE để kiểm tra kết quả."));
      steps.append(instructions);
      const qr=element("img","udid-qr");
      qr.src=scan.qr_url;qr.alt="Mã QR mở hồ sơ UDID trong Safari trên iPhone/iPad";
      steps.append(qr);
      steps.append(element("p","small",
        "Nếu dùng hai thiết bị, hãy quét QR này bằng iPhone cần lấy UDID, không quét QR trên chính màn hình của nó."));
      const check=button("Kiểm tra đã nhận UDID","btn outline",()=>{
        void udidCheckNow(order.id,scan.scan_token,feedback,steps);
      });
      steps.append(check);
      feedback.textContent="Đang chờ phản hồi của iPhone... Lượt lấy UDID có hiệu lực 15 phút.";
      udidStartPolling(order.id,scan.scan_token,feedback,steps);
    }catch(err){
      feedback.textContent=errorMessage(err);
      start.disabled=false;
    }
  });
  start.disabled=true;
  agreement.addEventListener("change",()=>{start.disabled=!agreement.checked});
  root.append(start);
  const manual=element("details","udid-manual");
  manual.append(element("summary",null,
    "Không dùng được hồ sơ iOS? Nhập UDID thủ công"));
  manual.append(element("p","small",
    "Trên thiết bị không cho phép tải hồ sơ, lấy UDID bằng Finder/Apple Devices/iTunes khi kết nối máy tính. Không nhập số sê-ri hoặc IMEI. UDID tự nhập được đánh dấu chưa xác minh."));
  const field=element("input","udid-manual-input");
  field.placeholder="Ví dụ: 00008020-001830D911A2002E";
  field.maxLength=45;field.autocapitalize="characters";
  manual.append(field);
  const manualFeedback=element("p","small");
  manual.append(manualFeedback);
  manual.append(button("Gửi UDID tự nhập","btn outline",async()=>{
    if(!agreement.checked){
      manualFeedback.textContent="Bạn cần đồng ý gửi UDID trước.";return
    }
    try{
      const saved=await request("/api/v1/me/certificate-orders/"+order.id+"/udid",
        {method:"PUT",authorized:true,data:{udid:field.value.trim(),consent:true}});
      udidStopPoll();udidClearPending();
      manualFeedback.textContent="✓ Đã gửi tới ERI ADMIN: "+saved.udid+
        " (khách tự nhập, chưa xác minh bằng hồ sơ Apple).";
    }catch(err){manualFeedback.textContent=errorMessage(err)}
  }));
  root.append(manual);
}
async function selectCertificateUdidOrder(){
  if(!state.buyer){authModal();return}
  udidStopPoll();
  openDialog("Chọn đơn Chứng chỉ để lấy UDID");
  const root=$("dialog-body");
  root.append(element("p","muted","Đang tải đơn Chứng chỉ..."));
  try{
    const response=await request("/api/v1/me/orders",{authorized:true});
    const orders=(response.items||[]).filter(
      x=>x.kind==="certificate"&&x.status!=="cancelled");
    root.replaceChildren();
    if(!orders.length){
      root.append(element("p","muted",
        "Bạn chưa có đơn Chứng chỉ. Hãy đặt mua Chứng chỉ trước khi lấy UDID."));
      root.append(button("Đến mục Chứng chỉ","btn outline",()=>{
        closeDialog();setView("store");
        document.querySelector('[data-kind="certificate"]')?.click();
      }));
      return;
    }
    root.append(element("p","small",
      "Chọn đơn cần đăng ký UDID. Mỗi lượt xác minh chỉ dành cho một đơn hàng."));
    for(const order of orders){
      const row=element("div","udid-order-choice");
      row.append(element("strong",null,order.product_name));
      row.append(element("p","small","Mã đơn: "+order.id));
      udidSaved(order,row);
      row.append(button(order.device_udid?"Lấy lại UDID":"Lấy UDID cho đơn này",
        "btn outline",()=>startUdidForOrder(order)));
      root.append(row);
    }
  }catch(err){root.replaceChildren(element("p","form-error",errorMessage(err)))}
}
async function udidHandleProfileReturn(){
  const url=new URL(location.href);
  if(url.searchParams.get("udid_scan")!=="complete")return;
  url.searchParams.delete("udid_scan");
  history.replaceState(null,"",url.pathname+url.search+url.hash);
  // A profile-service callback only redirects after saving verified UDID.
  // The browser context may be a fresh Safari tab, not the originating PWA.
  if(!state.token){
    openDialog("Đã nhận phản hồi từ iPhone");
    $("dialog-body").append(element("p",null,
      "Thiết bị đã gửi phản hồi. Đăng nhập bằng tài khoản đã đặt mua Chứng chỉ để xem UDID trong mục Đơn hàng."));
    $("dialog-body").append(button("Đăng nhập","btn primary",()=>authModal()));
    return;
  }
  try{
    const buyer=await request("/api/v1/auth/me",{authorized:true});
    state.buyer=buyer;updateHeader();
    const pending=udidPending();
    const result=await request("/api/v1/me/orders",{authorized:true});
    const orders=(result.items||[]).filter(x=>
      x.kind==="certificate"&&x.device_udid&&x.device_udid_source==="apple_profile");
    // Do not claim an old verified UDID belongs to this callback if Safari
    // opened in a separate storage context without the pending order.
    const match=pending?orders.find(x=>x.id===pending.orderId):null;
    openDialog(match?"UDID đã được nhận":"Kiểm tra UDID thiết bị");
    const root=$("dialog-body");
    if(match){
      udidStopPoll();udidClearPending();
      root.append(element("h3",null,"✓ Đã nhận UDID thành công"));
      root.append(element("p",null,match.device_udid));
      root.append(element("p","small",
        "Đã gắn với đơn "+match.id+" và chuyển tới ERI ADMIN. Hồ sơ lấy UDID không cần tồn tại trong Cài đặt."));
      root.append(button("Sao chép UDID","btn outline",()=>{
        navigator.clipboard?.writeText(match.device_udid).catch(()=>{});
      }));
    }else{
      root.append(element("p",null,
        "iPhone đã quay về website. Chưa đối chiếu được UDID với tài khoản này. Hãy kiểm tra lịch sử đơn Chứng chỉ hoặc đăng nhập đúng tài khoản đã mua."));
    }
    root.append(" ");
    root.append(button("Xem đơn hàng","btn primary",()=>{
      closeDialog();setView("orders");
    }));
  }catch(err){
    openDialog("Kiểm tra UDID");
    $("dialog-body").append(element("p","form-error",errorMessage(err)));
    $("dialog-body").append(button("Đăng nhập","btn outline",()=>authModal()));
  }
}
document.addEventListener("DOMContentLoaded",()=>{
  const action=$("start-udid");
  if(action)action.addEventListener("click",selectCertificateUdidOrder);
  void udidHandleProfileReturn();
});
document.addEventListener("visibilitychange",()=>{
  if(document.visibilityState==="visible"&&udidActiveOrder&&
     !$("overlay").classList.contains("hidden")){
    const a=udidActiveOrder;
    void udidCheckNow(a.orderId,a.token,a.feedback,a.steps);
  }
});
window.addEventListener("pageshow",()=>{
  if(udidActiveOrder&& !$("overlay").classList.contains("hidden")){
    const a=udidActiveOrder;
    void udidCheckNow(a.orderId,a.token,a.feedback,a.steps);
  }
});
