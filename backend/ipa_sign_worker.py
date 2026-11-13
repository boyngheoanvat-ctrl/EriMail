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
    # The signing worker, not the FastAPI API, owns the memory limit.
    # On Render Free this keeps a very large IPA from killing the API itself.
    if os.name == "posix":
        import resource
        limit_mb = max(256, min(int(os.getenv("ERI_SIGN_WORKER_MEMORY_MB", "448")), 1024))
        ceiling = limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
    import ipasign
    signed = ipasign.Key(str(certificate), str(provision), password).sign(
        str(source), str(output))
    if not output.is_file() or output.stat().st_size == 0:
        raise SystemExit(4)
    print(json.dumps({"bundle_id": str(signed.bundle_id)[:255]}))


if __name__ == "__main__":
    main()
