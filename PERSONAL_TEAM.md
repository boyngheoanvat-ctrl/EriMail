# Eri Mail — Free Apple ID / Personal Team

This mode is for **personal development and testing**, not public OTA distribution.

## On your Mac

1. Install Xcode and sign in with your free Apple Account via **Xcode → Settings → Accounts**.
2. Connect and unlock your iPhone, trust the Mac, and enable Developer Mode if requested.
3. Open the Eri Mail Xcode project, choose the app target → **Signing & Capabilities**.
4. Enable **Automatically manage signing** and select your **Personal Team**.
5. Choose the physical iPhone as run destination and press **Run (⌘R)**. Xcode manages the development signing certificate, App ID, device registration and provisioning profile.
6. Rebuild/reinstall when the provisioning profile expires (typically 7 days).

Use `bash scripts/install-personal-team.sh /path/to/EriMail.xcodeproj EriMail` for a local readiness check and instructions.

Apple's published free-team limits: up to 3 apps per device, 10 App IDs and 3 registered devices; App IDs/devices/profiles expire after 7 days. Not every capability is supported.

## ESign/KSign/Feather/GBox/Scarlet

The free Personal Team does **not** create a reusable public signing certificate, does not make signed IPAs installable by arbitrary users and does not enable a public **Cài trực tiếp** OTA service. The five tools can remain in the IPA catalog for importing/exporting authorized packages, but automatic signing/repacking third-party IPAs is **not implemented**. Never put Apple Account credentials or signing private keys in GitHub Actions secrets for a public repository.

Official guidance:
- https://developer.apple.com/help/account/basics/about-your-developer-account
- https://help.apple.com/xcode/mac/current/en.lproj/dev60b6fbbc7.html
