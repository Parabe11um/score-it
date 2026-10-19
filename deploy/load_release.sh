#!/usr/bin/env bash
# Offline import only: does not start services or modify application data.
set -euo pipefail
cd "$(dirname "$0")"
sha256sum --check SHA256SUMS
docker image load --input images.tar
while IFS=$'\t' read -r IMAGE EXPECTED_ID PLATFORM; do
  case "$IMAGE" in
    score-it:*|score-it-postgres:*) ;;
    *) echo 'Unexpected image name in release manifest.' >&2; exit 1 ;;
  esac
  ACTUAL_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE")"
  ACTUAL_PLATFORM="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$IMAGE")"
  [ "$ACTUAL_ID" = "$EXPECTED_ID" ] && [ "$ACTUAL_PLATFORM" = "$PLATFORM" ] || {
    echo 'Image differs from release manifest.' >&2; exit 1;
  }
done < images.tsv
echo 'IMAGES_LOADED — containers have not been started; database has not been changed.'
