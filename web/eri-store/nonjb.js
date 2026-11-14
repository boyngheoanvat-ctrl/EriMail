"use strict";
(() => {
 const API="https://eri-api-dag8.onrender.com";
 const form=document.getElementById("eri-nonjb-form");
 const status=document.getElementById("eri-nonjb-status");
 if(!form||!status)return;
 form.addEventListener("submit",async event=>{
  event.preventDefault();
  status.replaceChildren();
  const file=form.elements.ipa.files[0];
  const token=sessionStorage.getItem("eri_store_buyer_token")||localStorage.getItem("eri_store_buyer_token");
  if(!token){status.textContent="Vui lòng đăng nhập ERI STORE trước.";return;}
  if(!file||!file.name.toLowerCase().endsWith(".ipa")||file.size>512*1024*1024||file.size===0){
   status.textContent="Chọn file .ipa hợp lệ, tối đa 512 MB.";return;
  }
  const btn=form.querySelector('button[type="submit"]');
  btn.disabled=true;status.textContent="Đang kiểm tra IPA trên máy chủ...";
  try{
   const body=new FormData();body.append("ipa",file);
   const response=await fetch(API+"/api/v1/tools/ipa/nonjb/prepare",{
    method:"POST",headers:{"Authorization":"Bearer "+token},body
   });
   const result=await response.json();
   if(!response.ok)throw Error(typeof result.detail==="string"?result.detail:"Xử lý IPA thất bại ("+response.status+")");
   const path=String(result.download_path||"");
   if(!/^\/api\/v1\/tools\/ipa\/download\/[A-Za-z0-9_-]{32,64}$/.test(path))throw Error("Máy chủ không trả đường dẫn tải hợp lệ");
   status.textContent="Đã xác minh IPA không mã hóa FairPlay. File chưa được ký cho thiết bị. ";
   const a=document.createElement("a");a.className="btn primary";a.textContent="Tải IPA đã kiểm tra";a.href=API+path;
   status.append(a);
  }catch(error){status.textContent=error.message||"Lỗi kết nối máy chủ.";}
  finally{btn.disabled=false;}
 });
})();
