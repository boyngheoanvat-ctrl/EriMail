# Eri Mail — Signing and OTA distribution

1. Run `bash scripts/create-signing-request.sh` on a trusted Mac to generate a private key and CSR locally.
2. In your own Apple Developer account, request an appropriate Apple Distribution certificate using the CSR. Obtain a matching provisioning profile for the intended app, bundle ID and eligible devices/distribution channel.
3. Build or obtain an IPA you have permission to redistribute, then sign/export it through Xcode with the correct team and provisioning settings. Check the signature and embedded profile before publishing. An arbitrary existing IPA cannot be made valid by changing its filename or manifest.
4. Host the signed IPA and OTA manifest on your HTTPS server. The manifest's `software-package` URL must point to the actual signed IPA. Trigger OTA installation only when the signing and distribution conditions are satisfied.
5. Populate `catalog/eri-mail-catalog.example.json` with verified metadata and URLs. The example entries are deliberately unconfigured.

**Security:** Never commit private keys, .p12/.pfx, passwords, signing credentials, or private provisioning profiles. Do not upload a third party's private keys. An Apple Developer certificate cannot be self-issued by OpenSSL. Public certificates alone do not sign an IPA. Enterprise distribution is for eligible internal organization use; Ad Hoc requires registered devices. Signing status cannot be guaranteed for all devices.
