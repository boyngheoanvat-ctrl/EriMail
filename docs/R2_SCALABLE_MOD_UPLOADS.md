# Scalable, opt-in ERI Mod IPA/APK uploads through Cloudflare R2

This branch changes the iPhone upload path **only when explicitly enabled**
on the backend. This is a staging design, not a promise of infinite storage.
The physical Cloudflare R2 multipart object limit is about 4.995 TiB
(5 TiB minus 5 GiB), 10,000 parts per upload.

## Supported workflow

- ERI ADMIN starts a private multipart upload through authenticated HTTPS.
- The API issues a temporary URL for one part. The iPhone PUTs that part
  directly to Cloudflare R2, not through Render's ephemeral storage.
- iPhone holds at most one staged multipart part, avoids prebuilding an entire
  second copy of the IPA, and uploads using one request at a time.
- The API lists acknowledged parts from R2 when a user resumes. It does not
  trust a local progress bar as proof of a successful upload.
- After R2 completes, the API checks exact byte length, computes a streaming
  SHA-256, and validates IPA Info.plist or APK AndroidManifest.xml before
  publishing the product. This is not a check for all types of game encryption.
- Authorized FREE products can download through short-lived private R2 URLs.
  The paid-order delivery flow remains separate and unchanged.

## Configuration (not enabled on live Render)

Create a private R2 bucket in your own Cloudflare account and configure the
following environment variables **only in Render's encrypted settings**, never
in your IPA, GitHub, screenshots, chat, or public web assets:

- `ERI_R2_ACCOUNT_ID`
- `ERI_R2_BUCKET`
- `ERI_R2_ACCESS_KEY_ID`
- `ERI_R2_SECRET_ACCESS_KEY`
- `ERI_R2_MOD_UPLOADS=enabled`

Use a least-privilege R2 token restricted to the required bucket and object
operations. Keep the bucket private. The iPhone sees only 15-minute presigned
multipart URLs. If credentials or opt-in are missing, the new endpoint returns
an explicit configuration error and the app falls back to the existing path.

**Cost:** R2 Free includes a limited amount of storage and operations, not an
unlimited free allowance. Billable storage and operation fees may apply beyond
the free amount. No plan is upgraded or charges authorized in this branch.

## Release blockers

1. **Backup and restore test:** the live Render API still uses a Free ephemeral
   file system, so SQLite sessions, customer records, orders, and product
   metadata are at risk if redeployed without verified durable persistence.
   Export the real live DB, copy/verify original product files, and test
   restoring *before* touching the main deployment.
2. **R2 account/credentials:** verify the private bucket, sufficient quota,
   permissions and spending policy. No real R2 configuration has been
   validated by the offline tests.
3. **End-to-end testing:** check iPhone upload, restart/resume, tampered
   parts, completion timeout, archive validation, free download and deletion.
   The full-object SHA-256 read during finalization can take a very long
   time for multi-terabyte objects and may hit the current HTTP request
   timeout. Use durable asynchronous validation jobs before treating this
   path as suitable for objects near R2's maximum.
4. **Background behavior:** this initial direct-R2 implementation can resume
   interrupted uploads after reopening ERI ADMIN, but it does not guarantee
   uploading new parts while the app remains suspended or force-closed.
5. **Data retention:** staging upload-session rows are in SQLite. Install a
   persistent database before advertising production resumability.
6. **Rollout:** deploy the compatible backend first with
   `ERI_R2_MOD_UPLOADS` still disabled. After verified backup and controlled
   test, enable the feature and install the matching signed unsigned-IPA build.

## Local CI tests (all fake R2; no real files transferred)

```bash
cd backend
python test_large_ipa_limits.py
python test_r2_large_mods.py
python test_fast_upload.py
python -m unittest -v test_eri_vault.py
```

Cloudflare R2 references:

- https://developers.cloudflare.com/r2/objects/upload-objects/
- https://developers.cloudflare.com/r2/platform/limits/
- https://developers.cloudflare.com/r2/pricing/
