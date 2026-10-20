#!/usr/bin/env bash
# Offline import only: does not start services or modify application data.
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null
if [ "$#" -gt 1 ]; then
  echo 'Usage: bash load_release.sh [/absolute/trusted-public-key.pem]' >&2
  exit 1
fi
if [ "$#" -eq 1 ]; then
  openssl dgst -sha256 -verify "$1" -signature SHA256SUMS.sig SHA256SUMS
elif [ -f SHA256SUMS.sig ]; then
  echo 'A signed release requires the trusted public-key path as the argument.' >&2
  exit 1
fi
sha256sum --check SHA256SUMS
docker image load --input images.tar
python3 verify_release.py verify --release-dir .
echo 'IMAGES_LOADED — containers have not been started; database has not been changed.'
