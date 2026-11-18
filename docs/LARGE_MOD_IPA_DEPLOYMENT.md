# Capacity-aware ERI ADMIN IPA/APK uploads — staging and rollout

## Scope

This branch removes the **fixed application cap** for IPA/APK uploads, not
physical limits. IPA/APK are uploaded in 4 MiB checksummed blocks with resume
support. The API accepts a size only when sufficient **local temporary space**
is available for its current full-file ZIP/SHA-256 verification. A full disk
returns HTTP **507 Insufficient Storage** instead of acknowledging a file and
silently losing the contents.

The iOS picker accepts files with no hardcoded 2 GiB ceiling. For files over
1 GiB, iOS uses the existing single-stream, checksummed foreground uploader,
which avoids making a second full-sized copy of every upload part. Keep ERI
ADMIN open during that path. The background uploader remains available for
smaller files; that path prepares separate part files and requires additional
device space. Neither mode can exceed the free storage on the device.

The limits for certificates, AI products and protected SQLite snapshots are
**unchanged**. SQLite file sizes are still restricted to signed 64-bit values.

## Supabase status

On 2026-11-18 the existing `eri-private-vault` Edge Function was deployed as
**v6** and its preview counterpart as **v3**. Their authenticated file
namespace now permits 0..999,999,999 chunk indices. Snapshot and session
metadata remain limited to 0..127. The chunk body remains limited to 4 MiB,
with SHA-256 validation; deployment did not delete existing stored objects.

**This is a logical addressing capability, not free, unlimited storage.**
The connected project is on Supabase Free (1 GB included); its private
bucket was observed to contain approximately 1,052,985,043 bytes before the
change. No storage plan was upgraded and no existing file was deleted.

## Backend rollout — NOT YET DEPLOYED

The live Render `eri-api-dag8` was still running
`b2ee7719cad5cb2171d51597ad07376e047d8d60`, which rejects files
above 512 MiB. The live service auto-deploys from `main`, so merging this
branch may restart its ephemeral filesystem and cause customer-data loss.

Do **not** merge/deploy the API until all of these can be proven:

1. Export the **live** SQLite DB via
   `GET /api/v1/admin/backups/sqlite` using existing admin credentials.
   Verify `PRAGMA integrity_check` and record counts for customer accounts,
   orders, certificates, products, and upload records before and after.
   Save the backup **privately**, not in GitHub, chat or public R2 buckets.
2. Back up the original IPA/APK objects and paid order-delivery files; verify
   object sizes and SHA-256. Ensure the cloud snapshot actually includes the
   latest live customer records, rather than relying on the pointer timestamp.
3. Provision persistent storage with enough quota and writable temporary
   capacity for file assembly. The current Render Free disk + Supabase Free
   bucket cannot promise uploads of arbitrary size. Storage billing changes
   require the owner's approval. A direct-to-object-store multipart design
   is required for files larger than available Render temporary disk.
4. End-to-end test on an isolated backend with a real >512 MiB IPA and
   restore/download checks, including interruption and retry. Offline tests
   use sparse placeholders and are **not** end-to-end.
5. Deploy the backend in a controlled migration, verify all existing data,
   then install the newly signed iOS Admin app from the same Git commit.

## Configuration

`ERI_MOD_UPLOAD_MAX_BYTES=0` (default on this branch) means no fixed
application-level IPA/APK size cap. Set a positive byte count to impose a
safety policy ceiling. `ERI_PRIVATE_MAX_PRODUCT_BYTES=0` similarly removes
the Python vault client's fixed product cap. This does **not** override
Supabase subscription quota, Render disk capacity, Edge Function per-request
limits, iPhone free space or network conditions.

Protected endpoints:
- `GET /api/v1/admin/upload-capacity` reports local free bytes and whether
  the Python private-vault client is configured.
- `POST /api/v1/admin/upload-sessions` returns HTTP 507 when its announced
  size cannot be accommodated by the local file-assembly disk.

## Verification

Run `cd backend && python test_large_ipa_limits.py`, followed by existing
fast-upload and vault regression tests. GitHub Actions builds an unsigned
iOS IPA from this branch. Passing unit tests cannot guarantee a real upload
of unlimited size or durable retention on a Free-tier environment.
