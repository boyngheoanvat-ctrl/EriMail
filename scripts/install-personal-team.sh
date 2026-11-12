#!/usr/bin/env bash
set -euo pipefail
# Eri Mail: build and install your own Xcode project with a free Personal Team.
# Usage: bash scripts/install-personal-team.sh /path/to/EriMail.xcodeproj EriMail
PROJECT="${1:?Usage: $0 /path/to/project.xcodeproj Scheme [BundleID]}"
SCHEME="${2:?Specify the Xcode scheme}"
BUNDLE_ID="${3:-}"
[[ -d "$PROJECT" ]] || { echo "Xcode project not found: $PROJECT" >&2; exit 1; }
command -v xcodebuild >/dev/null || { echo "Install Xcode on a Mac first." >&2; exit 1; }
echo "Connect and unlock your iPhone. Enable Developer Mode if prompted."
echo "Sign into Xcode > Settings > Accounts, then select your Personal Team."
echo "Available destinations:"
xcodebuild -showdestinations -project "$PROJECT" -scheme "$SCHEME" | head -100
echo "Open this project in Xcode and select your physical iPhone as destination."
echo "In Signing & Capabilities: enable Automatically manage signing and choose Personal Team."
echo "Press Run (Cmd+R). Xcode obtains the development certificate/profile and installs on the phone."
echo "A free Personal Team profile expires after 7 days; rerun from Xcode to renew."
echo "This script deliberately does NOT export a distributable signed IPA or request Apple ID credentials."
