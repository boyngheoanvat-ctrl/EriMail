"use strict";
// Customer-initiated UDID collection for an authenticated certificate order.
// Never try to read the device identifier silently in a browser.
let udidPollTimer=null;
function udidStopPoll(){
  if(udidPollTimer!==null){clearInterval(udidPollTimer);udidPollTimer=null}
}
function udidSaved(order,root){
  if(!order.device_udid)return;
  root.append(element("p",null,"UDID đã gửi: "+order.device_udid));
  root.append(element("p","small",order.device_udid_source==="apple_profile"
    ?"Hồ sơ Apple đã xác minh chữ ký."
    :"Khách tự nhập (chưa xác thực thiết bị)."));
}
function startUdidForOrder(order){
  if(!state.buyer){authModal();return}
  if(order.kind!=="certificate")return;
  udidStopPoll();
  openDialog("Lấy UDID iPhone / iPad");
  const root=$("dialog-body");
  root.append(element("h3",null,order.product_name));
  root.append(element("p","small","Mã đơn: "+order.id));
  udidSaved(order,root);
  root.append(element("p",null,"Chỉ khi bạn chủ động tải và cài hồ sơ trong Cài đặt, iPhone mới gửi UDID về cửa hàng. Hồ sơ chỉ yêu cầu UDID; không lấy IMEI, số sê-ri hay mật khẩu."));
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
    start.disabled=true;feedback.textContent="Đang tạo hồ sơ iPhone...";
    try{
      const response=await request(
        "/api/v1/me/certificate-orders/"+order.id+"/udid-sessions",
        {method:"POST",authorized:true,data:{}});
      const p=API+"/api/v1/cert-udid/";
      if(!response.scan_token||!String(response.profile_url).startsWith(p)||
         !String(response.qr_url).startsWith(p))throw new Error("Không tạo được hồ sơ.");
      steps.replaceChildren();
      steps.append(element("p",null,"1. Mở trên Safari của iPhone cần đăng ký và tải hồ sơ."));
      const a=element("a","btn primary","⇩ Tải hồ sơ UDID");
      a.href=response.profile_url;a.rel="noreferrer";
      steps.append(a);
      steps.append(element("p",null,
        "2. Vào Cài đặt → Hồ sơ đã tải về → Cài đặt. Đọc kỹ và đồng ý chia sẻ UDID."));
      const img=element("img","udid-qr");
      img.src=response.qr_url;img.alt="Mã QR để iPhone khác mở hồ sơ lấy UDID";
      steps.append(img);
      steps.append(element("p",null,
        "Bạn cũng có thể dùng camera iPhone quét mã QR này trên thiết bị khác."));
      steps.append(element("p",null,
        "3. Quay lại ERI STORE. Sau khi iPhone gửi dữ liệu được xác minh, UDID tự xuất hiện và chuyển tới ERI ADMIN."));
      feedback.textContent="Đang chờ phản hồi từ iPhone (hồ sơ có hiệu lực 15 phút)...";
      udidStopPoll();let checking=false;
      udidPollTimer=setInterval(async()=>{
        if(checking)return;
        if($("overlay").classList.contains("hidden") ||
           !$("dialog-title").textContent.includes("UDID")){udidStopPoll();return}
        checking=true;
        try{
          const result=await request(
            "/api/v1/me/certificate-orders/"+order.id+
            "/udid-sessions/"+encodeURIComponent(response.scan_token),
            {authorized:true});
          if(result.complete){
            udidStopPoll();
            feedback.textContent="✓ Đã quét thành công và gửi UDID đến ERI ADMIN.";
            steps.replaceChildren(element("h3",null,"UDID đã ghi nhận"),element("p",null,result.udid));
            steps.append(button("Sao chép UDID","btn outline",()=>{
              navigator.clipboard?.writeText(result.udid).catch(()=>{});
            }));
          }
        }catch(err){
          if(/hết hạn|expired|410/i.test(errorMessage(err))){
            udidStopPoll();feedback.textContent="Hồ sơ đã hết hạn. Hãy tạo lượt quét mới.";
          }
        }finally{checking=false}
      },2500);
    }catch(e){feedback.textContent=errorMessage(e);start.disabled=false}
  });
  start.disabled=true;
  agreement.addEventListener("change",()=>{start.disabled=!agreement.checked});
  root.append(start);
  const manual=element("details","udid-manual");
  manual.append(element("summary",null,"Không cài được hồ sơ? Nhập UDID thủ công"));
  manual.append(element("p","small","UDID tự nhập được ghi nhận là chưa xác minh."));
  const field=element("input","udid-manual-input");
  field.placeholder="VD: 00008020-001830D911A2002E";field.maxLength=45;
  field.autocapitalize="characters";
  manual.append(field);
  const message=element("p","small");manual.append(message);
  manual.append(button("Gửi UDID tự nhập","btn outline",async()=>{
    if(!agreement.checked){message.textContent="Vui lòng đồng ý chia sẻ UDID trước.";return}
    try{
      const result=await request("/api/v1/me/certificate-orders/"+order.id+"/udid",
        {method:"PUT",authorized:true,data:{udid:field.value.trim(),consent:true}});
      udidStopPoll();
      message.textContent="✓ Đã gửi cho ERI ADMIN: "+result.udid+" (chưa xác minh).";
    }catch(err){message.textContent=errorMessage(err)}
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
      x=>x.kind==="certificate" && x.status!=="cancelled");
    root.replaceChildren();
    if(!orders.length){
      root.append(element("p","muted",
        "Bạn chưa có đơn Chứng chỉ. Vui lòng đặt mua chứng chỉ trước khi lấy UDID."));
      root.append(button("Chọn chứng chỉ","btn outline",()=>{closeDialog();setView("store")}));
      return;
    }
    root.append(element("p","small","Chọn chính xác đơn hàng cần đăng ký UDID."));
    for(const order of orders){
      const row=element("div","udid-order-choice");
      row.append(element("strong",null,order.product_name));
      row.append(element("p","small","Mã đơn: "+order.id));
      udidSaved(order,row);
      row.append(button("Lấy UDID cho đơn này","btn outline",
        ()=>startUdidForOrder(order)));
      root.append(row);
    }
  }catch(err){root.replaceChildren(element("p","form-error",errorMessage(err)))}
}
document.addEventListener("DOMContentLoaded",()=>{
  const action=$("start-udid");
  if(action)action.addEventListener("click",selectCertificateUdidOrder);
});
