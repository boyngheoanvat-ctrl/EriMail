# ERI ADMIN: IPA/APK 3.73 GB and scalable private R2 uploads

**Status:** staging code only. Do not merge into or deploy to `eri-api-dag8`
until customer database and existing files have verified recoverable backups.

## What changes

- ERI ADMIN tries the authenticated `/api/v1/admin/r2-mods/config`
  endpoint for IPA/APK uploads. Only when R2 is fully enabled does it
  stream one 64 MiB part at a time **directly from iPhone to Cloudflare R2**.
- The Render API creates a private multipart upload, issues 15-minute
  presigned PUT URLs and verifies part order/size, full object SHA-256,
  and IPA/APK ZIP metadata before publishing the product.
- The local app retains the picked file and upload session info and can
  skip parts already uploaded **after reopening the app**. This R2 flow
  does not yet promise uninterrupted uploads while the app is suspended
  or force-closed. It does not promise infinite storage.
- IPA downloads from free listings use 10-minute signed GET redirects.
  The Admin R2 connection remains server-side. Old Supabase vault objects,
  accounts, orders, and product URLs are not rewritten.
- Physical max set below provider 5 TiB object ceiling, max 10,000 parts.
  For **3,730,000,000 bytes**, 64 MiB parts give approximately 56 parts.
  No 3.73 GB payload is allocated on Render to create the upload session.

## Deployment prerequisites (do NOT skip)

1. Export **live** SQLite and verify `PRAGMA integrity_check` and that
   actual current customer, order, catalog and IPA/file counts match.
   Securely back up all private file objects and currently in-progress
   order deliveries. A dated Supabase snapshot pointer alone cannot
   guarantee this. Render Free uses an ephemeral local filesystem.
2. Provision a private Cloudflare R2 bucket with adequate capacity
   and enough storage budget for existing files plus new uploads.
   No backend has unlimited free storage; object-storage charges and
   bandwidth depend on provider pricing. Keep the bucket private.
3. In the Render API service's **secret environment**, configure:
   `ERI_R2_ACCOUNT_ID`, `ERI_R2_BUCKET`,
   `ERI_R2_ACCESS_KEY_ID`, `ERI_R2_SECRET_ACCESS_KEY`,
   `ERI_R2_MOD_UPLOADS=enabled`. Scope the API token to the
   specific bucket; never put it into GitHub source or iOS binaries.
4. Use a **separate preview backend and private preview R2 bucket**
   first. Test creating a 3.73 GB multipart session, real PUT requests,
   stop/resume, final SHA-256 verification, IPA Info.plist parsing,
   listing and authenticated signed downloads. Also test storage quota
   errors and large-file disk-free checks on the iPhone.
5. Only after successful restore tests and preview uploads, deploy
   both updated `backend/app.py` and `apps/ERIAdmin/ERIAdminApp.swift`
   from the same commit, then sign/install the new unsigned IPA.
   Do not redeploy the production Render Free service without backups.
6. Monitor upload sessions and expire/abort incomplete multiparts:
   unfinished R2 uploads can incur storage costs. Implement explicit
   cleanup/retention, and confirm durable session persistence on restart,
   before opening this feature to production traffic.

## Test

```sh
cd backend
python test_r2_large_mods.py
python test_fast_upload.py
python -m unittest -v test_eri_vault.py
```

These tests use an isolated FakeR2. They verify the 3.73 GB **upload plan**,
permissions, resume status, small IPA metadata finalization and catalog
paths, not real 3.73 GB file transfer or long-running iPhone background
operation.

## Note on large ZIPs

IPA/APK archives are validated by metadata without downloading the whole
file to Render. This implementation is a staging prototype, not a
substitute for security scanning, ZIP bomb protection, malware review,
file content policy, authentication hardening, or storage lifecycle
management needed for a public distribution service.
