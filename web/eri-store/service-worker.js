// ERI STORE private download streaming (native Safari downloads, no large JS Blob).
// Each capability nonce lives only in this service worker memory and is never
// a customer bearer token. No private file or auth token is cached.
const VERSION="eri-store-v10";
const STATIC=["/","/index.html","/styles.css","/app.js","/udid.js","/icon.svg","/manifest.webmanifest"];
const DOWNLOAD_PREFIX="/_eri_download/";
const API="https://eri-api-dag8.onrender.com";
const MAX_PREPARE_AGE_MS=10*60*1000;
const grants=new Map();
function b64url(bytes){
  let result="";
  for(const part of bytes)result+=String.fromCharCode(part);
  return btoa(result).replace(/\+/g,"-").replace(/\//g,"_").replace(/=/g,"");
}
function cleanExpiredGrants(){
  const now=Date.now();
  for(const [key,grant] of grants)if(grant.expires<now)grants.delete(key);
}
self.addEventListener("install",event=>{
  event.waitUntil(caches.open(VERSION).then(cache=>cache.addAll(STATIC)).then(()=>self.skipWaiting()));
});
self.addEventListener("activate",event=>{
  event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)))).then(()=>self.clients.claim()));
});
self.addEventListener("message",event=>{
  if(event.data?.type!=="ERI_PREPARE_DOWNLOAD")return;
  const port=event.ports?.[0];
  if(!port)return;
  const source=event.source;
  if(!source?.url||new URL(source.url).origin!==self.location.origin){
    port.postMessage({ok:false,error:"Yêu cầu không hợp lệ"});return;
  }
  const {orderId,token,filename}=event.data;
  if(typeof orderId!=="string"||!/^[A-F0-9]{12}$/.test(orderId)||
     typeof token!=="string"||token.length<40||token.length>128||
     typeof filename!=="string"||!filename||filename.length>180){
    port.postMessage({ok:false,error:"Thông tin tải file chưa hợp lệ"});return;
  }
  cleanExpiredGrants();
  const nonce=b64url(crypto.getRandomValues(new Uint8Array(24)));
  grants.set(nonce,{orderId,token,filename,expires:Date.now()+MAX_PREPARE_AGE_MS});
  port.postMessage({ok:true,url:DOWNLOAD_PREFIX+nonce});
});
function safeDisposition(filename){
  const safe=filename.replace(/[\r\n\/\\"]/g,"_").slice(0,180)||"eri-delivery";
  const fallback=safe.replace(/[^\x20-\x7e]/g,"_");
  return `attachment; filename="${fallback}"; filename*=UTF-8\'\'${encodeURIComponent(safe)}`;
}
function downloadMime(filename){
  const ext=(filename.split(".").pop()||"").toLowerCase();
  const formats={ipa:"application/x-itunes-ipa",zip:"application/zip",
    pdf:"application/pdf",p12:"application/x-pkcs12",
    png:"image/png",jpg:"image/jpeg",jpeg:"image/jpeg",
    txt:"text/plain",json:"application/json"};
  return formats[ext]||"application/octet-stream";
}
async function streamPrivateFile(event,nonce){
  cleanExpiredGrants();
  const grant=grants.get(nonce);
  if(!grant)return new Response("Liên kết tải đã hết hạn. Quay lại ERI STORE và bấm Tải file lần nữa.",{
    status:410,headers:{"Content-Type":"text/plain; charset=utf-8","Cache-Control":"no-store"}
  });
  const headers={"Authorization":"Bearer "+grant.token};

  let upstream;
  try{
    upstream=await fetch(API+"/api/v1/me/orders/"+grant.orderId+"/delivery",{
      headers,cache:"no-store",mode:"cors",credentials:"omit",redirect:"error"
    });
  }catch(_){
    return new Response("Lỗi kết nối khi tải file. Vui lòng thử lại.",{
      status:503,headers:{"Content-Type":"text/plain; charset=utf-8","Cache-Control":"no-store"}
    });
  }
  if(!upstream.ok||!upstream.body){
    const message=upstream.status===410?"File hiện không còn trên máy chủ. Vui lòng liên hệ hỗ trợ.":
      upstream.status===401?"Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.":
      "Không thể tải file. Vui lòng thử lại.";
    return new Response(message,{status:upstream.status||502,
      headers:{"Content-Type":"text/plain; charset=utf-8","Cache-Control":"no-store"}});
  }
  const out={"Content-Type":downloadMime(grant.filename),
    "Content-Disposition":safeDisposition(grant.filename),
    "X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store",
    "Referrer-Policy":"no-referrer"};
  const length=upstream.headers.get("Content-Length");
  if(length&&/^\d+$/.test(length))out["Content-Length"]=length;
  // Pass the upstream ReadableStream through without buffering the whole IPA.
  // Browser download manager owns the stream and its lifetime.
  return new Response(upstream.body,{status:upstream.status,headers:out});
}
self.addEventListener("fetch",event=>{
  const url=new URL(event.request.url);
  if(url.origin!==self.location.origin||event.request.method!=="GET")return;
  if(url.pathname.startsWith(DOWNLOAD_PREFIX)){
    const nonce=url.pathname.slice(DOWNLOAD_PREFIX.length);
    if(/^[A-Za-z0-9_-]{20,60}$/.test(nonce)){
      event.respondWith(streamPrivateFile(event,nonce));
    }else{
      event.respondWith(new Response("Liên kết không hợp lệ",{status:404}));
    }
    return;
  }
  if(!STATIC.includes(url.pathname))return;
  event.respondWith(fetch(event.request).then(response=>{
    if(response.ok){
      const copy=response.clone();
      caches.open(VERSION).then(cache=>cache.put(event.request,copy));
    }
    return response;
  }).catch(()=>caches.match(event.request)));
});
