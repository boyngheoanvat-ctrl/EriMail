#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ERI LOGIN — auto IPA/APK packager.

Targets: apps you own or are authorized to modify.
iOS: patch a *compatible, unencrypted, thin arm64* Mach-O executable with a
load command for ERILogin.dylib; IPA must be signed again before installation.
Android: requires apktool + an ERIAndroidGate.dex build artifact. Rebuild and
sign with Android SDK tools; Android build tools are not present in plain
Python-only iPhone environments. Unsupported cases always fail CLOSED.
"""
from __future__ import annotations
import os
import re
import sys
import struct
import plistlib
import zipfile
import shutil
import tempfile
import subprocess
import secrets
import json
import xml.etree.ElementTree as ET
from pathlib import Path

NAME = "ERILogin.dylib"
INSTALL_NAME = "@executable_path/Frameworks/" + NAME
MH_MAGIC_64 = 0xFEEDFACF
CPU_ARM64 = 0x100000C
MH_EXECUTE = 2
MH_DYLIB = 6
LC_SEGMENT_64 = 0x19
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_REEXPORT_DYLIB = 0x8000001F
LC_CODE_SIGNATURE = 0x1D
MAX_ARCHIVE_SIZE = 7 * 1024**3
ANDROID_NS = "http://schemas.android.com/apk/res/android"
A = "{%s}" % ANDROID_NS
ET.register_namespace("android", ANDROID_NS)

class ERIError(Exception): pass

def macho(data, filetype):
    if len(data) < 32:
        raise ERIError("Mach-O header thiếu dữ liệu")
    if struct.unpack_from("<I", data)[0] != MH_MAGIC_64:
        raise ERIError("Chỉ hỗ trợ Mach-O ARM64 64-bit không phải FAT")
    _, cpu, _, typ, num, size, _, _ = struct.unpack_from("<8I", data)
    if cpu != CPU_ARM64 or typ != filetype:
        raise ERIError("Sai loại executable/dylib ARM64")
    if num == 0 or num > 1000 or 32 + size > len(data):
        raise ERIError("Mach-O load commands lỗi")
    offset, first, crypt, deps, sign = 32, len(data), 0, [], False
    has_sections = False
    for _ in range(num):
        if offset + 8 > 32 + size: raise ERIError("Load command vượt header")
        cmd, length = struct.unpack_from("<II", data, offset)
        if length < 8 or length % 8 or offset + length > 32 + size:
            raise ERIError("Load command kích thước không hợp lệ")
        if cmd == LC_SEGMENT_64:
            if length < 72: raise ERIError("Mach-O segment không hợp lệ")
            sections = struct.unpack_from("<I",data,offset+64)[0]
            if 72 + sections*80 > length: raise ERIError("Mach-O section hỏng")
            for j in range(sections):
                s = offset + 72 + j*80
                sec_size = struct.unpack_from("<Q",data,s+40)[0]
                sec_off = struct.unpack_from("<I",data,s+48)[0]
                if sec_size and sec_off:
                    first = min(first,sec_off)
                    has_sections=True
        if cmd in (LC_ENCRYPTION_INFO,LC_ENCRYPTION_INFO_64) and length>=20:
            crypt = struct.unpack_from("<I",data,offset+16)[0]
        if cmd in (LC_LOAD_DYLIB,LC_ID_DYLIB,LC_LOAD_WEAK_DYLIB,LC_REEXPORT_DYLIB) and length>=24:
            nameoff = struct.unpack_from("<I",data,offset+8)[0]
            if 24<=nameoff<length:
                deps.append(data[offset+nameoff:offset+length].split(b"\0",1)[0].decode("utf-8","replace"))
        if cmd == LC_CODE_SIGNATURE: sign=True
        offset += length
    if offset != 32+size: raise ERIError("Lỗi sizeofcmds")
    if not has_sections or first<offset: raise ERIError("Thiếu vùng Mach-O hợp lệ sau header")
    return dict(num=num,size=size,end=offset,first=first,crypt=crypt,deps=deps,sign=sign)

def inject_binary(blob):
    m=macho(blob,MH_EXECUTE)
    if m["crypt"]:raise ERIError("IPA còn mã hóa FairPlay, không thể chèn")
    if INSTALL_NAME in m["deps"]:raise ERIError("ERILogin.dylib đã có, không chèn lặp")
    name=INSTALL_NAME.encode()+b"\0"
    sz=(24+len(name)+7)&~7
    if m["end"]+sz>m["first"]:
        raise ERIError("IPA không có đủ Mach-O load-command padding")
    if any(blob[m["end"]:m["end"]+sz]):
        raise ERIError("Vùng padding Mach-O không trống, không ghi đè")
    result=bytearray(blob)
    struct.pack_into("<6I",result,m["end"],LC_LOAD_DYLIB,sz,24,0,0,0)
    result[m["end"]+24:m["end"]+24+len(name)] = name
    struct.pack_into("<I",result,16,m["num"]+1)
    struct.pack_into("<I",result,20,m["size"]+sz)
    if INSTALL_NAME not in macho(result,MH_EXECUTE)["deps"]:
        raise ERIError("Kiểm tra load command thất bại")
    return bytes(result)

def app_entry(archive):
    found=[x for x in archive.namelist() if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist",x)]
    if len(found)!=1: raise ERIError("IPA phải có đúng một Payload/*.app/Info.plist")
    inf=plistlib.loads(archive.read(found[0]))
    exe,app=inf.get("CFBundleExecutable"),inf.get("CFBundleIdentifier")
    if not isinstance(exe,str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}",exe):
        raise ERIError("Không xác định được executable")
    if not isinstance(app,str) or len(app)>128: raise ERIError("Bundle ID lỗi")
    folder=found[0].rsplit("/",1)[0]+"/"
    exename=folder+exe
    if exename not in archive.namelist():raise ERIError("Không tìm thấy executable trong IPA")
    return exename,folder,app

def stream_copy(zi,zo,entry,override=None):
    if override is not None:
        zo.writestr(entry,override)
    elif entry.is_dir():
        zo.writestr(entry,b"")
    else:
        with zi.open(entry,"r") as sr, zo.open(entry,"w",force_zip64=True) as dest:
            shutil.copyfileobj(sr,dest,length=1024*1024)

def patch_ipa(source,lib,out):
    if not lib.is_file(): raise ERIError("Thiếu ERILogin.dylib cạnh LOGIN.py")
    library=lib.read_bytes()
    parsed=macho(library,MH_DYLIB)
    if INSTALL_NAME not in parsed["deps"]:
        raise ERIError("Dylib build sai install-name, không chèn")
    with zipfile.ZipFile(source,"r") as zi:
        exe,root,app=app_entry(zi)
        inject_to=root+"Frameworks/"+NAME
        if inject_to in zi.namelist():raise ERIError("IPA đã có ERILogin.dylib")
        patched=inject_binary(zi.read(exe))
        print("[IPA] App ID:",app)
        # Keep original ZIP metadata. Re-sign all executables after patching.
        with zipfile.ZipFile(out,"w",allowZip64=True) as zo:
            for entry in zi.infolist():
                path=entry.filename
                if path.startswith(root+"_CodeSignature/"):continue
                stream_copy(zi,zo,entry,override=patched if path==exe else None)
            entry=zipfile.ZipInfo(inject_to)
            entry.compress_type=zipfile.ZIP_DEFLATED
            entry.external_attr=0o100755<<16
            zo.writestr(entry,library)
    with zipfile.ZipFile(out) as result:
        exe2,root2,app2=app_entry(result)
        if app2!=app or root2+"Frameworks/"+NAME not in result.namelist():
            raise ERIError("Kiểm tra IPA sau đóng gói thất bại")
        if INSTALL_NAME not in macho(result.read(exe2),MH_EXECUTE)["deps"]:
            raise ERIError("LC_LOAD_DYLIB không xuất hiện trong IPA mới")
    print("[OK] Gắn dependency ERI Login vào IPA, kiểm tra ZIP + Mach-O đạt")
    print("[!] IPA MỚI CHƯA KÝ. Cần ký lại main executable, dylib và bundle bằng cùng chứng chỉ hợp lệ.")
    print("[!] Chưa thể khẳng định Login hiện trong game cho tới khi cài chạy trên iPhone.")

def find_tool(exe,folder):
    path=shutil.which(exe)
    if path:return [path]
    jar=folder/(exe+".jar")
    if jar.is_file() and shutil.which("java"):return [shutil.which("java"),"-jar",str(jar)]
    raise ERIError("Android cần "+exe+" (hoặc "+exe+".jar + Java)")

def run(args):
    p=subprocess.run([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors="replace")
    if p.returncode:
        raise ERIError("Lệnh build thất bại: "+str(args[0])+"\n"+p.stdout[-2500:])
    return p.stdout

def classname(pkg,short):
    if short.startswith("."):return pkg+short
    if "." not in short:return pkg+"."+short
    return short

def modify_manifest(manifest):
    tree=ET.parse(manifest)
    document=tree.getroot()
    pkg=document.get("package")
    if not pkg:raise ERIError("Manifest APK không có package")
    application=document.find("application")
    if application is None:raise ERIError("Manifest APK không có application")
    matches=[]
    for item in list(application):
        if item.tag not in ("activity","activity-alias"):continue
        for child in list(item.findall("intent-filter")):
            actions=[e.get(A+"name","") for e in child.findall("action")]
            cats=[e.get(A+"name","") for e in child.findall("category")]
            if "android.intent.action.MAIN" in actions and "android.intent.category.LAUNCHER" in cats:
                name=item.get(A+"targetActivity") if item.tag=="activity-alias" else item.get(A+"name")
                matches.append((item,child,name))
    if len(matches)!=1:raise ERIError("APK phải có đúng một MAIN/LAUNCHER Activity")
    old,filter_tag,target=matches[0]
    if not target:raise ERIError("Không tìm thấy Activity gốc")
    target=classname(pkg,target)
    old.remove(filter_tag)
    for e in document.findall("uses-permission"):
        if e.get(A+"name")=="android.permission.INTERNET":break
    else:ET.SubElement(document,"uses-permission",{A+"name":"android.permission.INTERNET"})
    gate=ET.SubElement(application,"activity",{
        A+"name":"vn.eri.login.ERILoginActivity",
        A+"exported":"true",
        A+"label":"ERI LOGIN",
        A+"theme":"@android:style/Theme.Material.Light.NoActionBar"
    })
    ET.SubElement(gate,"meta-data",{A+"name":"eri.target",A+"value":target})
    flt=ET.SubElement(gate,"intent-filter")
    ET.SubElement(flt,"action",{A+"name":"android.intent.action.MAIN"})
    ET.SubElement(flt,"category",{A+"name":"android.intent.category.LAUNCHER"})
    tree.write(manifest,encoding="utf-8",xml_declaration=True)
    print("[APK] Package:",pkg,"| Launcher gốc:",target)
    return pkg

def apk_signed(unsigned,root,apkname):
    zipalign=shutil.which("zipalign")
    apksigner=shutil.which("apksigner")
    keytool=shutil.which("keytool")
    if not all((zipalign,apksigner,keytool)):
        print("[!] Chưa có zipalign/apksigner/keytool: APK đã gắn Login nhưng vẫn CHƯA KÝ.")
        return unsigned
    keystore=root/"ERI_Android_Signing.jks"
    pwfile=root/".ERI_Android_Signing_password.txt"
    if keystore.is_file() and not pwfile.is_file():
        raise ERIError("Đã có keystore nhưng thiếu mật khẩu đã lưu; không ghi đè khóa")
    if not keystore.is_file():
        password=secrets.token_urlsafe(24)
        pwfile.write_text(password,encoding="utf-8")
        try:pwfile.chmod(0o600)
        except OSError:pass
        run([keytool,"-genkeypair","-keystore",str(keystore),"-alias","erilogin",
             "-keyalg","RSA","-keysize","3072","-validity","3650",
             "-storepass",password,"-keypass",password,
             "-dname","CN=ERI Login, O=ERI", "-noprompt"])
    password=pwfile.read_text(encoding="utf-8").strip()
    aligned=unsigned.with_name(unsigned.stem+"_aligned.apk")
    signed=apkname
    try:
        run([zipalign,"-f","-p","4",str(unsigned),str(aligned)])
        run([apksigner,"sign","--ks",str(keystore),"--ks-key-alias","erilogin",
             "--ks-pass","pass:"+password,"--key-pass","pass:"+password,
             "--out",str(signed),str(aligned)])
        run([apksigner,"verify","--verbose",str(signed)])
        print("[OK] Đã ký + xác minh chữ ký APK:",signed.name)
    finally:
        aligned.unlink(missing_ok=True)
    print("[!] Cất giữ keystore và password; mất chúng thì không cập nhật được APK cùng chữ ký.")
    return signed

def patch_apk(source,asset,out,root):
    if not asset.is_file():raise ERIError("Thiếu ERIAndroidGate.dex cạnh LOGIN.py")
    apktool=find_tool("apktool",root)
    with tempfile.TemporaryDirectory(prefix="eri_apk_") as temp:
        t=Path(temp)
        decoded=t/"decoded"
        print("[*] Đang giải mã manifest + dex bằng apktool")
        run(apktool+["d","-f",str(source),"-o",str(decoded)])
        pkg=modify_manifest(decoded/"AndroidManifest.xml")
        unsigned=t/"built.apk"
        print("[*] Đang build APK có Login")
        run(apktool+["b",str(decoded),"-o",str(unsigned)])
        with zipfile.ZipFile(unsigned,"r") as zi:
            dexes=[int(m.group(1) or "1") for n in zi.namelist()
                   if (m:=re.fullmatch(r"classes(\d*)\.dex",n))]
            new_name="classes"+str(max(dexes or [1])+1)+".dex"
            with zipfile.ZipFile(out,"w",allowZip64=True) as zo:
                for entry in zi.infolist():
                    if entry.filename.startswith("META-INF/"):continue
                    stream_copy(zi,zo,entry)
                entry=zipfile.ZipInfo(new_name)
                entry.compress_type=zipfile.ZIP_STORED
                zo.writestr(entry,asset.read_bytes())
        with zipfile.ZipFile(out) as verify:
            if new_name not in verify.namelist():raise ERIError("Không thấy Android ERI DEX trong APK")
        print("[OK] Đã thay launcher bằng ERI LOGIN, đóng gói DEX:",new_name)
        final=apk_signed(out,root,out.with_name(out.stem.replace("_unsigned","")+".apk"))
    return final

def choose_input(root):
    files=[]
    for p in root.iterdir():
        if not p.is_file() or p.suffix.lower() not in (".ipa",".apk"):continue
        if p.stem.endswith("_ERI_LOGIN") or p.stem.endswith("_ERI_LOGIN_unsigned"):continue
        if p.stat().st_size > MAX_ARCHIVE_SIZE:continue
        files.append(p)
    if not files:raise ERIError("Không tìm thấy IPA/APK trong cùng thư mục LOGIN.py")
    files.sort(key=lambda p:p.stat().st_mtime,reverse=True)
    return files[0]

def main():
    root=Path(__file__).resolve().parent
    print("\n=== ERI LOGIN — AUTO IPA / APK INJECTOR ===")
    print("[*] Cơ chế key: 1 key / 1 thiết bị, xác minh qua ERI API")
    try:
        source=choose_input(root)
        print("[*] Tự chọn:",source.name)
        dest=source.with_name(source.stem+"_ERI_LOGIN_unsigned"+source.suffix.lower())
        if dest.exists():raise ERIError("File đầu ra đã tồn tại, không ghi đè: "+dest.name)
        with zipfile.ZipFile(source) as check:
            bad=check.testzip()
            if bad:raise ERIError("File nguồn bị lỗi CRC: "+bad)
        try:
            if source.suffix.lower()==".ipa":
                patch_ipa(source,root/NAME,dest)
            elif source.suffix.lower()==".apk":
                patch_apk(source,root/"ERIAndroidGate.dex",dest,root)
            else:raise ERIError("Định dạng chưa được hỗ trợ")
        except Exception:
            dest.unlink(missing_ok=True)
            raise
        print("[OK] Hoàn thành bước sửa file:",dest.name)
        print("[!] Kiểm tra trên thiết bị thật trước khi đưa khách sử dụng.")
        return 0
    except (ERIError,ValueError,KeyError,zipfile.BadZipFile,struct.error,OSError) as exc:
        print("[LỖI] Dừng an toàn:",exc)
        return 1
    except Exception as exc:
        print("[LỖI] Sự cố chưa được hỗ trợ:",type(exc).__name__,str(exc))
        return 2

if __name__=="__main__":
    sys.exit(main())
