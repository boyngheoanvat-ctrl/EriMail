# Eri Mail — One-tap IPA signing service contract

**Status: architecture only; signing is not operational until a trusted signing worker and Apple-authorized credentials are configured.**

## Intended UX
1. User selects an IPA in the Eri Mail iOS app.
2. App uploads it to the owner's HTTPS signing service after explicit consent.
3. Service checks archive integrity, authorization, supported app ID/entitlements, and whether a suitable Apple-issued certificate and provisioning profile exist for the target device.
4. A trusted macOS worker signs/repackages the IPA and validates its code signature.
5. The service returns a short-lived authenticated HTTPS download link; an OTA manifest is provided only if distribution rules allow it.
6. The iOS app downloads the signed IPA and enables **Xuất IPA**. **Cài trực tiếp** appears only if a valid install manifest is available.

## API proposal
- `POST /v1/signing/jobs` (authenticated multipart IPA upload, explicit consent)
  returns `{ "jobId": "...", "status": "queued" }`
- `GET /v1/signing/jobs/{jobId}` returns
  `{ "status": "queued|processing|signed|failed", "message": "...", "downloadURL": null, "manifestURL": null }`
- `DELETE /v1/signing/jobs/{jobId}` deletes uploaded artifacts.

## Security and requirements
- No private keys, Apple passwords, .p12, or signing credentials in the IPA app, public repo, or browser.
- Store signing identity in a locked-down macOS keychain; worker has scoped access.
- Encrypt uploads, use authentication, expiring download URLs, file size limits, malware/zip-bomb checks, audit logging and scheduled deletion.
- Require redistribution rights for third-party IPAs.
- **Free Apple ID / Personal Team cannot supply a general-purpose signing identity for a remote public service.** For a free account, Xcode on the user's Mac can automatically sign their own development app for a registered device, typically for seven days. The service must reject a job when no valid authorized signing identity exists.
- Never report an IPA as signed or installable until signature validation succeeds.
