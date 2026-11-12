# ERI private signing server on a personal Mac

This server is **separate** from the public ERI shop/catalog API. It accepts multipart POST `/api/v1/sign` from the native iOS signing screen, invokes the experimental `sign_worker.py` locally and returns `ERI-signed.ipa`. It is not deployed or tested on a real Mac yet.

## Start

Install Xcode command line tools and Python 3.11+ on the Mac:

```sh
xcode-select --install
git clone https://github.com/boyngheoanvat-ctrl/EriMail.git
cd EriMail/signing-worker
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "fastapi>=0.115,<1" "uvicorn[standard]>=0.30,<1" "python-multipart>=0.0.9"
export ERI_SIGN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
printf 'Save this token securely for your iPhone: %s\n' "$ERI_SIGN_TOKEN"
uvicorn sign_api:app --host 127.0.0.1 --port 8765 --no-access-log
```

Do not use `--reload` in production. Keep the Mac awake. For private remote access, use Tailscale Serve to publish the local port **only to your tailnet**, with HTTPS. Follow Tailscale's current instructions at https://tailscale.com/kb/1312/serve ; do not use Funnel unless you intentionally want public exposure and have implemented additional safeguards.

Set the native app's `ERI_CUSTOM_API_BASE_URL` to your Mac's Tailscale HTTPS URL. In the **Ký IPA** screen, enter the same `ERI_SIGN_TOKEN`, select an authorized IPA, P12, provisioning profile and P12 password, then tap **Ký IPA**. The native app requires HTTPS.

## Local API test

```sh
curl http://127.0.0.1:8765/health
curl -f -X POST http://127.0.0.1:8765/api/v1/sign \
  -H "X-ERI-SIGN-TOKEN: $ERI_SIGN_TOKEN" \
  -F "ipa=@/path/to/app.ipa" \
  -F "p12=@/path/to/certificate.p12" \
  -F "mobileprovision=@/path/to/profile.mobileprovision" \
  -F "password=YOUR_P12_PASSWORD" \
  -o ERI-signed.ipa
```

**Security:** The curl example places the P12 password in shell history; use it only with disposable test credentials or a secure input method. Do not publish credentials, P12s or provisioning files to GitHub. Keep the Mac user account dedicated and protected, limit access to the private tailnet, and rotate tokens if compromised.

**Limitations:** The API is a private proof of concept, not a production signing service. No account-based authentication, durable queue, per-user quotas, audit records, sandboxing, or automatic certificate/profile compatibility verification. Upload size is checked while streaming, but reverse proxy/request-body limits should also be configured. Each signing job may run up to 10 minutes. The worker uses a disposable keychain and temporary files; crashes may leave temporary data until system cleanup. Bundle ID/name changes are rejected. The `remove_embedded` option is accepted for client compatibility but the worker always replaces the embedded profile. Do not open this endpoint to untrusted customers.
