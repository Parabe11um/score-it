#!/usr/bin/env bash
# Keep the signing private key outside the release and outside Git.
set -euo pipefail
if [ "$#" -ne 2 ]; then
  echo 'Usage: bash sign_release.sh /absolute/release-directory /private/signing-key.pem' >&2
  exit 1
fi
ROOT="$(realpath "$1")"
KEY="$(realpath "$2")"
case "$KEY" in "$ROOT"/*) echo 'Private key must be outside the release.' >&2; exit 1 ;; esac
(cd "$ROOT" && sha256sum --check SHA256SUMS)
openssl dgst -sha256 -sign "$KEY" -out "$ROOT/SHA256SUMS.sig" "$ROOT/SHA256SUMS"
echo 'RELEASE_SIGNED: deliver the trusted public key through a separate channel.'
