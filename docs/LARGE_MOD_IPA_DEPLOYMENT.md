# Staged 2 GiB IPA/APK upload support (not deployed)

The iOS Files picker previously failed at 512 MiB (displayed as 536.9 MB).
This branch raises IPA/APK and order-delivery ceilings to **2 GiB**, retaining
4 MiB resumable chunks, SHA-256 checks and private storage.

## Why this branch must not be merged or deployed yet

**eri-api-dag8** on Render deploys automatically from `main`, and its Free
filesystem is ephemeral. A backend redeploy may erase local SQLite data,
orders, user keys, and uploaded product files unless they have already been
verified in durable storage. Do not merge or redeploy the backend just to
change the file size limit. Back up and verify restores first.

The live Supabase function **eri-private-vault** currently accepts paths for
indices **0 through 127** (128 * 4 MiB = 512 MiB) and will reject index 128.
The Python client and backend changes in this branch **cannot bypass that**.

## Required coordinated deployment checklist

1. Export and verify a restorable **production** SQLite backup, uploaded
   product files, outstanding upload sessions and order-delivery files.
   Identify the current `ERI_VAULT_MODE` and confirm a complete durable
   snapshot, without printing or changing secrets.
2. Verify available Render disk space and Supabase Storage quota. These are
   real capacity constraints; increasing a code limit does not add storage.
   The Files picker and background staging also temporarily need additional
   free space on the user's iPhone.
3. Update the Edge Function's **files** chunk path validation to permit
   0..511, while keeping **snapshots** and **sessionmeta** limited to
   0..127. Keep authentication, upload integrity and snapshot pointer
   validation unchanged. The current implementation validates the sequence
   with `/^(0|[1-9][0-9]{0,2})$/`, which already admits 511, but additionally
   rejects `Number(seq) >= 128`. Replace that rejection with a
   kind-specific check; do not remove it entirely.
4. Test a file with 129 chunks using the isolated preview vault and API;
   confirm persistence after a restart and byte-for-byte restored SHA-256.
   Test that the 2 GiB + 1 byte boundary is refused.
5. Only after backups and successful isolated tests, safely deploy the backend
   and install a newly signed ERI ADMIN IPA from the matching source revision.
   A client-only update will encounter HTTP 413 against the old backend.
6. Verify that existing products, certificates, user accounts and orders
   survive deployment before enabling public uploads.

## Offline test

```sh
cd backend
python test_large_ipa_limits.py
```

This test only checks metadata, sparse file preallocation and vault chunk
count limits. It does **not** prove the full 2 GiB production upload,
Supabase quota, iOS available space, or live rollback/restore procedure.
