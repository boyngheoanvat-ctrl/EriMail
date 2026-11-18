"""Offline R2 direct multipart regression tests: no external credentials or network."""
import io
import math
import os
import plistlib
import tempfile
import zipfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="eri-r2-large-test-") as folder:
    os.environ["ERI_DB_PATH"] = str(Path(folder) / "eri.sqlite3")
    os.environ["ERI_UPLOAD_DIR"] = str(Path(folder) / "uploads")
    os.environ["ERI_ADMIN_KEY"] = "testing-only-r2-admin-key-1234567890"
    os.environ["ERI_VAULT_MODE"] = "off"
    os.environ["ERI_R2_MOD_UPLOADS"] = "disabled"

    from fastapi.testclient import TestClient
    import eri_r2_mods as r2
    from app import app, connection

    class FakeBody(io.BytesIO):
        def iter_chunks(self, chunk_size):
            while True:
                data = self.read(chunk_size)
                if not data:
                    return
                yield data

    class FakeR2:
        def __init__(self):
            self.uploads, self.objects = {}, {}

        def create_multipart_upload(self, Bucket, Key, **kwargs):
            token = "fake-upload-" + str(len(self.uploads))
            self.uploads[(Key,token)] = {}
            return {"UploadId":token}

        def generate_presigned_url(self, method, Params, ExpiresIn):
            assert method in ("get_object","upload_part")
            assert ExpiresIn in (600,900)
            return "https://example.r2.cloudflarestorage.com/" + Params["Key"]

        def list_parts(self, Bucket, Key, UploadId, **kwargs):
            pairs = self.uploads[(Key,UploadId)]
            return {"Parts":[{"PartNumber":i,"ETag":f'"fake-{i}"',"Size":len(data)}
                             for i,data in sorted(pairs.items())],
                    "IsTruncated":False}

        def head_object(self, Bucket, Key):
            if Key not in self.objects:
                raise FileNotFoundError(Key)
            return {"ContentLength":len(self.objects[Key])}

        def complete_multipart_upload(self, Bucket,Key,UploadId,MultipartUpload):
            parts=self.uploads.pop((Key,UploadId))
            indexes=[p["PartNumber"] for p in MultipartUpload["Parts"]]
            self.objects[Key] = b"".join(parts[i] for i in indexes)
            return {"ETag":'"complete"'}

        def get_object(self, Bucket,Key,Range=None):
            data=self.objects[Key]
            if Range is not None:
                assert Range.startswith("bytes=")
                first,last=map(int,Range[6:].split("-"))
                data=data[first:last+1]
            return {"Body":FakeBody(data)}

        def delete_object(self, Bucket, Key):
            self.objects.pop(Key,None)
            return {"ResponseMetadata":{"HTTPStatusCode":204}}

        def abort_multipart_upload(self, Bucket,Key,UploadId):
            self.uploads.pop((Key,UploadId),None)

    fake=FakeR2()
    client=TestClient(app)
    key={"X-ERI-Admin-Key":os.environ["ERI_ADMIN_KEY"]}

    # Disabled by default. No provider config or secret appears in app responses.
    disabled=client.get("/api/v1/admin/r2-mods/config",headers=key)
    assert disabled.status_code==200 and disabled.json()["enabled"] is False
    assert client.get("/api/v1/admin/r2-mods/config").status_code in (401,403)
    body={"kind":"ipa","filename":"demo.ipa","size_bytes":724_100_000,
          "name":"Large Mod","price_vnd":200000,"authorized":True}
    assert client.post("/api/v1/admin/r2-mods/start",json=body,headers=key).status_code==503

    os.environ["ERI_R2_MOD_UPLOADS"]="enabled"
    r2._r2_settings=lambda: ("a"*32,"eri-test-bucket","access","secret")
    r2._client=lambda:fake
    config=client.get("/api/v1/admin/r2-mods/config",headers=key).json()
    assert config["enabled"] and config["max_object_bytes"]==r2.R2_MAX_OBJECT

    # Exercise the requested 3.73 GB IPA/APK and a >4 GiB object.
    # This creates metadata only, never allocates the actual large bytes.
    for kind in ("ipa", "apk"):
        for size in (3_730_000_000, 4 * 1024**3 + 1):
            sample={**body,"kind":kind,"filename":"large."+kind,"size_bytes":size}
            response=client.post("/api/v1/admin/r2-mods/start",json=sample,headers=key)
            assert response.status_code==201,(kind,size,response.text)
            plan=response.json()
            assert plan["size_bytes"]==size
            assert plan["total_parts"]==math.ceil(size / plan["part_bytes"])
            assert 1 < plan["total_parts"] <= 10_000
    # Big files are planned without consuming their size on Render.
    begin=client.post("/api/v1/admin/r2-mods/start",json=body,headers=key)
    assert begin.status_code==201,begin.text
    init=begin.json()
    assert init["total_parts"]==math.ceil(body["size_bytes"]/init["part_bytes"])
    assert init["part_bytes"] >= 8*1024*1024
    assert not list(Path(folder).rglob("*.part")), "Render should not create the file"
    status=client.get("/api/v1/admin/r2-mods/"+init["upload_id"]+"/status",headers=key)
    assert status.status_code==200 and status.json()["received"]==[]
    assert client.post("/api/v1/admin/r2-mods/"+init["upload_id"]+"/complete",
                       headers=key).status_code==409
    # Pending uploads are visible only to the admin and can be explicitly
    # cancelled without deleting a published product.
    unfinished=client.get("/api/v1/admin/r2-mods/unfinished",headers=key)
    assert unfinished.status_code==200,unfinished.text
    assert init["upload_id"] in [x["id"] for x in unfinished.json()["items"]]
    assert client.get("/api/v1/admin/r2-mods/unfinished").status_code in (401,403)
    cancelled=client.post("/api/v1/admin/r2-mods/"+init["upload_id"]+"/abort",headers=key)
    assert cancelled.status_code==200 and cancelled.json()["aborted"],cancelled.text
    assert client.post("/api/v1/admin/r2-mods/"+init["upload_id"]+"/abort",headers=key).status_code==200
    assert client.get("/api/v1/admin/r2-mods/"+init["upload_id"]+"/status",headers=key).status_code==410
    assert client.post("/api/v1/admin/r2-mods/"+init["upload_id"]+"/complete",
                       headers=key).status_code==410
    assert init["upload_id"] not in [x["id"] for x in
                                   client.get("/api/v1/admin/r2-mods/unfinished",headers=key).json()["items"]]

    too_big={**body,"size_bytes":r2.R2_MAX_OBJECT+1}
    denied=client.post("/api/v1/admin/r2-mods/start",json=too_big,headers=key)
    assert denied.status_code==413,denied.text
    assert math.ceil(r2.R2_MAX_OBJECT/r2.choose_part_size(r2.R2_MAX_OBJECT))<=10_000

    # A small real ZIP/IPA reaches the same verification and catalog code.
    buff=io.BytesIO()
    with zipfile.ZipFile(buff,"w",compression=zipfile.ZIP_DEFLATED) as pkg:
        pkg.writestr("Payload/Test.app/Info.plist",plistlib.dumps({
            "CFBundleIdentifier":"com.eri.test",
            "CFBundleShortVersionString":"1.0",
            "CFBundleExecutable":"Test",
        }))
        pkg.writestr("Payload/Test.app/Test",b"FAKE_MAC_HEAD")
    data=buff.getvalue()
    small={**body,"name":"Free R2 demo","size_bytes":len(data),"price_vnd":0}
    created=client.post("/api/v1/admin/r2-mods/start",json=small,headers=key)
    assert created.status_code==201,created.text
    session=created.json()
    sid=session["upload_id"]
    prefix="/api/v1/admin/r2-mods/"+sid
    ticket=client.get(prefix+"/part/0",headers=key)
    assert ticket.status_code==200,ticket.text
    assert ticket.json()["bytes"]==len(data)
    assert ticket.json()["url"].startswith("https://")
    assert client.get(prefix+"/part/1",headers=key).status_code==422
    upload_token=next(token for key,token in fake.uploads if key==r2.object_key(sid,"ipa"))
    fake.uploads[(r2.object_key(sid,"ipa"),upload_token)][1]=data
    resumed=client.get(prefix+"/status",headers=key)
    assert resumed.json()["received"]==[0]
    done=client.post(prefix+"/complete",headers=key)
    assert done.status_code==201,done.text
    product_id=done.json()["id"]
    again=client.post(prefix+"/complete",headers=key)
    assert again.status_code==201 and again.json()["id"]==product_id
    assert client.get(prefix+"/status",headers=key).json()["state"]=="published"
    assert client.post(prefix+"/abort",headers=key).status_code==409
    with connection() as db:
        row=db.execute("SELECT size_bytes,storage_name FROM private_product_uploads WHERE product_id=?",(product_id,)).fetchone()
        assert row["size_bytes"]==len(data)
        assert row["storage_name"]==r2.storage_name(sid,"ipa")
    shop=client.get("/api/v1/mods")
    assert shop.status_code==200 and shop.json()["items"][0]["download_path"]
    download=client.get("/api/v1/mods/"+str(product_id)+"/download",follow_redirects=False)
    assert download.status_code==307,download.text
    assert ".r2.cloudflarestorage.com" in download.headers["location"]

    print("Optional R2 direct large uploads, IPA verification, resume and store routing: OK")
