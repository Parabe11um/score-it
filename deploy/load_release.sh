#!/usr/bin/env bash
# Offline import only: does not start services or modify application data.
set -euo pipefail
cd "$(dirname "$0")"
command -v python3 >/dev/null
sha256sum --check SHA256SUMS
docker image load --input images.tar
python3 verify_release.py verify --release-dir .
echo 'IMAGES_LOADED — containers have not been started; database has not been changed.'
