"""Isolated child process for customer-requested IPA signing.

Receives the P12 password only via stdin (not argv, env, logs, or files).
A worker crash must not terminate the FastAPI server process.
"""
import json
import os
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 5:
        raise SystemExit(2)
    source, certificate, provision, output = map(Path, sys.argv[1:])
    password = sys.stdin.readline().rstrip("\r\n")
    if not password:
        raise SystemExit(3)
    import ipasign
    signed = ipasign.Key(str(certificate), str(provision), password).sign(
        str(source), str(output))
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(4)
    print(json.dumps({"bundle_id": str(signed.bundle_id)[:255]}))


if __name__ == "__main__":
    main()
