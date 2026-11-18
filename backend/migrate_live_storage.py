#!/usr/bin/env python3
"""One-time ERI SQLite + private IPA migration; read-only by default.

Run ONLY where the current live SQLite and upload directory are readable,
with storefront writes paused. Never auto-seed an empty cloud on deploy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from eri_vault import ERIVault, VaultError, MAX_PRODUCT_BYTES

SAFE_NAME = re.compile(r"^[a-f0-9]{32}\.[A-Za-z0-9]{1,16}$")


def inventory(db_path: Path, uploads: Path):
    if not db_path.is_file():
        raise VaultError("No existing SQLite database; refusing a blank migration")
    # mode=ro: this inventory must not initialize or mutate the old DB.
    connection = sqlite3.connect(
        db_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise VaultError("Current SQLite database is corrupt")
        for table in ("customers", "private_product_uploads", "order_deliveries"):
            if not connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                      (table,)).fetchone():
                raise VaultError("Missing required SQLite table: " + table)
        customer_count = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        products = connection.execute(
            "SELECT storage_name,size_bytes,sha256 FROM private_product_uploads"
        ).fetchall()
        deliveries = connection.execute(
            "SELECT storage_name,size_bytes,sha256 FROM order_deliveries"
        ).fetchall()
    finally:
        connection.close()
    uploads_by_name = {}
    for entry in list(products) + list(deliveries):
        name, size, sha = entry["storage_name"], entry["size_bytes"], entry["sha256"]
        if not SAFE_NAME.fullmatch(name):
            raise VaultError("Unsafe private object name in database")
        if not (type(size) is int and size > 0
                and (MAX_PRODUCT_BYTES == 0 or size <= MAX_PRODUCT_BYTES)
                and isinstance(sha,str) and re.fullmatch(r"[a-f0-9]{64}",sha)):
            raise VaultError("Invalid object metadata in database")
        if name in uploads_by_name:
            if uploads_by_name[name] != (size,sha):
                raise VaultError("Conflicting duplicate private object metadata")
            continue
        path = uploads/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            raise VaultError("Private object missing or inconsistent: " + name)
        uploads_by_name[name] = (size,sha)
    return customer_count, uploads_by_name


def publish(db_path: Path, uploads: Path, *, allow_empty=False):
    customers, files = inventory(db_path,uploads)
    if not allow_empty and customers==0 and len(files)==0:
        raise VaultError("No customer/file records; require --allow-empty to initialize new system")
    vault = ERIVault()
    if vault.read_snapshot_pointer() is not None:
        raise VaultError("Cloud database already initialized: refusing to replace newer data")
    for index, (name,(size,sha)) in enumerate(files.items(), start=1):
        result = vault.upload_file(uploads/name, name[:32], expected_sha256=sha)
        if result["size"] != size:
            raise VaultError("Remote size mismatch for " + name)
        print(f"Cloud file validated {index}/{len(files)}",flush=True)
    # Publish the SQLite snapshot last, never before all referenced files.
    snapshot = vault.snapshot_sqlite(db_path)
    if vault.read_snapshot_pointer() != snapshot:
        raise VaultError("Failed to confirm committed cloud DB pointer")
    return {"customers":customers,"files":len(files),
            "snapshot_generation":snapshot["generation"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,required=True)
    parser.add_argument("--uploads",type=Path,required=True)
    parser.add_argument("--commit",action="store_true",
                        help="Actually publish private files and SQLite snapshot")
    parser.add_argument("--allow-empty",action="store_true",
                        help="Explicitly allow a brand new store with no accounts/uploads")
    opts=parser.parse_args()
    try:
        customers, files=inventory(opts.db,opts.uploads)
        if not opts.commit:
            print(json.dumps({"mode":"dry-run","customers":customers,
                              "private_files":len(files),"ready_to_commit":bool(customers or files)},
                             indent=2))
            return
        result=publish(opts.db,opts.uploads,allow_empty=opts.allow_empty)
        print(json.dumps({"ok":True,**result},indent=2))
    except (VaultError,OSError,sqlite3.Error) as exc:
        print("MIGRATION BLOCKED:",str(exc),file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
