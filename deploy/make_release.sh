#!/usr/bin/env bash
# Run on a connected build host, never on the offline application server.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ "$#" -ne 1 ]; then
  echo 'Usage: bash deploy/make_release.sh /absolute/output-directory' >&2
  exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
  git status --short
  echo 'Release build requires a clean, committed working tree.' >&2
  exit 1
fi
RELEASE="$(git rev-parse HEAD)"
PLATFORM="${RELEASE_PLATFORM:-linux/amd64}"
PG_SOURCE="${POSTGRES_SOURCE_IMAGE:-postgres:17-bookworm}"
APP_IMAGE="score-it:$RELEASE"
PG_IMAGE="score-it-postgres:17-$RELEASE"
OUTPUT="$(realpath -m "$1")"
mkdir -p "$OUTPUT"
DEST="$OUTPUT/score-it-$RELEASE"
mkdir "$DEST"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
git archive HEAD | tar -x -C "$BUILD_DIR"
docker build --platform "$PLATFORM" -f "$BUILD_DIR/Dockerfile.production" \
  --label "org.opencontainers.image.revision=$RELEASE" \
  -t "$APP_IMAGE" "$BUILD_DIR"
docker pull --platform "$PLATFORM" "$PG_SOURCE"
docker tag "$PG_SOURCE" "$PG_IMAGE"
docker image save -o "$DEST/images.tar" "$APP_IMAGE" "$PG_IMAGE"
cp compose.production.yaml compose.proxy.yaml compose.local-test.yaml "$DEST/"
cp deploy/env.production.example "$DEST/.env.production.example"
cp deploy/load_release.sh "$DEST/"
cp deploy/verify_release.py "$DEST/"
cp deploy/README.md "$DEST/README.md"
printf 'SCORE_IT_IMAGE=%s\nPOSTGRES_IMAGE=%s\n' "$APP_IMAGE" "$PG_IMAGE" > "$DEST/release.env"
python3 deploy/verify_release.py manifest --archive "$DEST/images.tar" \
  --output "$DEST/images.tsv" --platform "$PLATFORM" \
  --image "$APP_IMAGE" --image "$PG_IMAGE"
printf 'RELEASE=%s\nPLATFORM=%s\n' "$RELEASE" "$PLATFORM" > "$DEST/VERSION"
python3 - "$DEST" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
with (root / 'SHA256SUMS').open('w') as output:
    for path in sorted(root.iterdir()):
        if path.is_file() and path.name != 'SHA256SUMS':
            sha = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    sha.update(block)
            output.write(f'{sha.hexdigest()}  {path.name}\n')
PY
echo "RELEASE_READY=$DEST"
