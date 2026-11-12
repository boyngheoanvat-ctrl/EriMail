"""ERI signing worker: macOS only. Run locally, not exposed to the Internet.

Usage: python3 sign_worker.py input.ipa cert.p12 profile.mobileprovision output.ipa
Requires: ERI_P12_PASSWORD environment variable, Xcode command line tools.
Uses a disposable keychain; all temporary signing material is removed after use.
"""
import os
import sys
import plistlib
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

MAX_IPA_BYTES = 2 * 1024**3

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout

def sign(ipa: Path, p12: Path, profile: Path, output: Path):
    if sys.platform != "darwin":
        raise RuntimeError("IPA signing requires macOS with Apple codesign tools")
    if ipa.suffix.lower() != ".ipa" or p12.suffix.lower() != ".p12" or profile.suffix.lower() != ".mobileprovision":
        raise ValueError("Incorrect input file extension")
    if ipa.stat().st_size > MAX_IPA_BYTES:
        raise ValueError("IPA exceeds 2 GiB limit")
    password = os.environ.get("ERI_P12_PASSWORD")
    if not password:
        raise RuntimeError("Set ERI_P12_PASSWORD in worker environment")
    with tempfile.TemporaryDirectory(prefix="eri-sign-") as tmp:
        root = Path(tmp)
        work = root / "work"
        work.mkdir()
        keychain = root / "signing.keychain-db"
        keychain_password = secrets.token_urlsafe(32)
        run("security", "create-keychain", "-p", keychain_password, str(keychain))
        try:
            run("security", "unlock-keychain", "-p", keychain_password, str(keychain))
            run("security", "set-keychain-settings", "-lut", "1800", str(keychain))
            run("security", "import", str(p12.resolve()), "-k", str(keychain),
                "-P", password, "-T", "/usr/bin/codesign")
            run("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
                "-s", "-k", keychain_password, str(keychain))
            identities = run("security", "find-identity", "-v", "-p", "codesigning", str(keychain))
            import re
            matches = re.findall(r'\b([0-9A-F]{40})\b', identities)
            if len(matches) != 1:
                raise RuntimeError("P12 must contain exactly one valid code signing identity")
            identity = matches[0]
            with zipfile.ZipFile(ipa) as archive:
                total = 0
                for info in archive.infolist():
                    path = Path(info.filename)
                    if path.is_absolute() or ".." in path.parts:
                        raise ValueError("Unsafe IPA archive entry")
                    total += info.file_size
                    if total > 6 * 1024**3:
                        raise ValueError("IPA expanded size too large")
                    if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                        raise ValueError("IPA contains symbolic links")
                archive.extractall(work)
            apps = list((work / "Payload").glob("*.app"))
            if len(apps) != 1:
                raise ValueError("IPA must contain exactly one Payload/*.app")
            app = apps[0]
            shutil.copy2(profile, app / "embedded.mobileprovision")
            profile_data = subprocess.run(
                ["security", "cms", "-D", "-i", str(profile.resolve())],
                check=True, capture_output=True).stdout
            profile_info = plistlib.loads(profile_data)
            entitlements = profile_info.get("Entitlements", {})
            entitlements_path = root / "entitlements.plist"
            with entitlements_path.open("wb") as out:
                plistlib.dump(entitlements, out)
            # Sign nested bundles before their containing application.
            nested = sorted(
                [p for p in app.rglob("*") if p.suffix in {".framework", ".appex", ".app", ".xpc"}],
                key=lambda p: len(p.parts), reverse=True)
            for bundle in nested:
                run("codesign", "--force", "--sign", identity, "--keychain", str(keychain), str(bundle))
            run("codesign", "--force", "--sign", identity, "--keychain", str(keychain),
                "--entitlements", str(entitlements_path), str(app))
            run("codesign", "--verify", "--verbose=2", str(app))
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as dest:
                for path in work.rglob("*"):
                    if path.is_file():
                        dest.write(path, path.relative_to(work))
        finally:
            subprocess.run(["security", "delete-keychain", str(keychain)], capture_output=True)

if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit("Usage: sign_worker.py input.ipa cert.p12 profile.mobileprovision output.ipa")
    sign(*(Path(p) for p in sys.argv[1:]))
    print("Signed IPA created")
