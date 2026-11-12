# ERI macOS IPA signing worker (experimental)

This is a **local CLI worker**, not a deployed public signing service. It must run on a trusted macOS machine with Xcode command line tools, `security`, and `codesign`. Only sign apps you own or are authorized to distribute. It does not bypass Apple's certificate, device registration, or provisioning rules.

Example:

```sh
export ERI_P12_PASSWORD='password-from-secure-secret-store'
python3 sign_worker.py ./app.ipa ./certificate.p12 ./profile.mobileprovision ./signed.ipa
unset ERI_P12_PASSWORD
```

The script imports the P12 into a disposable keychain, checks for exactly one signing identity, extracts a bounded IPA, installs the provisioning profile, signs nested bundles and the main app, verifies the signature, and produces a new IPA. Temporary files and keychain are removed on exit.

**Not production ready:** Not tested on a real macOS signing environment; complex apps with extensions, frameworks, dylibs, watch components, app groups, or differing entitlements may require additional handling. The P12 password passed through an environment variable may be visible to local privileged processes; use a dedicated isolated macOS worker with secret injection and access controls. Do not run this CLI in an internet-facing FastAPI process or on shared runners with untrusted uploads. The app's native signing screen is not yet connected to this worker.

Before production: build a separate authenticated job API and queue, enforce per-user quotas and storage limits, validate certificate/profile/team/bundle compatibility, encrypt short-lived artifacts, add audit trails without secrets, clean files automatically, and run end-to-end signing/install tests on a real device. Never log or persist P12 passwords.
