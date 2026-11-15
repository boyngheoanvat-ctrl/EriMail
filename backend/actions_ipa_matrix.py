"""Select approved IPA source IDs for auto-triggered GitHub Actions.

Pushes to the reviewed manifest trigger processing automatically; operators
may still dispatch a single ID. Empty catalogs cause a clean skipped job.
"""
import argparse
import json
import os
import re
from pathlib import Path


def get_ids(manifest, requested=""):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError("Manifest phải có items")
    approved = []
    for item in items:
        if not isinstance(item, dict) or item.get("distribution_authorized") is not True:
            continue
        source_id = item.get("id")
        if not isinstance(source_id, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{0,63}", source_id):
            raise ValueError("Manifest có source_id không hợp lệ")
        if source_id in approved:
            raise ValueError("Source ID trùng nhau")
        approved.append(source_id)
    if requested:
        if requested not in approved:
            raise ValueError("source_id chưa nằm trong danh sách được phê duyệt")
        return [requested]
    # Avoid a surprise cost explosion when a catalog is updated.
    if len(approved) > 64:
        raise ValueError("Tối đa 64 nguồn IPA mỗi workflow tự động")
    return approved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="backend/eri_actions_sources.json")
    parser.add_argument("--source-id", default="")
    args = parser.parse_args()
    result = get_ids(args.manifest, args.source_id)
    output = {"source_ids": json.dumps(result), "has_sources": str(bool(result)).lower()}
    dest = os.getenv("GITHUB_OUTPUT")
    if dest:
        with open(dest, "a", encoding="utf-8") as stream:
            for k, v in output.items():
                stream.write(k + "=" + v + "\n")
    print(json.dumps({"count": len(result), "has_sources": bool(result)}))


if __name__ == "__main__":
    main()
