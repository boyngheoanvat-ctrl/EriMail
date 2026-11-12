#!/usr/bin/env bash
set -euo pipefail
# Run locally on a trusted Mac. NEVER commit the generated private key.
OUT="${1:-$HOME/.eri-mail/signing}"
mkdir -p "$OUT"
chmod 700 "$OUT"
umask 077
openssl genrsa -out "$OUT/eri-mail.key" 2048
openssl req -new -key "$OUT/eri-mail.key" -out "$OUT/eri-mail.csr" -subj "/CN=Eri Mail Signing Request"
echo "Created CSR: $OUT/eri-mail.csr"
echo "Private key: $OUT/eri-mail.key (KEEP SECRET)"
echo "Submit the CSR to Apple Developer Certificates; Apple must issue the certificate."
echo "Download a matching provisioning profile and export an authorized signing identity to a secure keychain."
