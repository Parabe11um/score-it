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
. deploy/images.env
PG_SOURCE="$POSTGRES_SOURCE_IMAGE"
APP_IMAGE="score-it:$RELEASE"
PG_IMAGE="score-it-postgres:17-$RELEASE"
CADDY_IMAGE="score-it-caddy:$RELEASE"
python3 - <<'PY'
import json, re, subprocess
version = json.loads(subprocess.check_output(['docker', 'version', '--format', '{{json .Server}}']))['Version']
match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
if not match or tuple(map(int, match.groups())) < (29, 8, 2):
    raise SystemExit('Update build-host Docker Engine/containerd/runc first (Docker >= 29.8.2).')
PY
OUTPUT="$(realpath -m "$1")"
mkdir -p "$OUTPUT"
DEST="$OUTPUT/score-it-$RELEASE"
mkdir "$DEST"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
git archive HEAD | tar -x -C "$BUILD_DIR"
docker build --pull --platform "$PLATFORM" -f "$BUILD_DIR/Dockerfile.production" \
  --build-arg "PYTHON_IMAGE=$PYTHON_SOURCE_IMAGE" \
  --label "org.opencontainers.image.revision=$RELEASE" \
  -t "$APP_IMAGE" "$BUILD_DIR"
docker build --pull --platform "$PLATFORM" -f "$BUILD_DIR/deploy/Dockerfile.postgres" \
  --build-arg "POSTGRES_IMAGE=$PG_SOURCE" \
  --label "org.opencontainers.image.revision=$RELEASE" -t "$PG_IMAGE" "$BUILD_DIR"
docker build --pull --platform "$PLATFORM" -f "$BUILD_DIR/deploy/Dockerfile.caddy" \
  --build-arg "GO_IMAGE=$GO_SOURCE_IMAGE" --build-arg "CADDY_IMAGE=$CADDY_SOURCE_IMAGE" \
  --label "org.opencontainers.image.revision=$RELEASE" -t "$CADDY_IMAGE" "$BUILD_DIR"
docker image save -o "$DEST/images.tar" "$APP_IMAGE" "$PG_IMAGE" "$CADDY_IMAGE"
cp compose.production.yaml compose.proxy.yaml compose.local-test.yaml compose.maintenance.yaml compose.caddy-image.yaml "$DEST/"
cp deploy/env.production.example "$DEST/.env.production.example"
cp deploy/load_release.sh "$DEST/"
cp deploy/verify_release.py "$DEST/"
cp deploy/README.md "$DEST/README.md"
cp deploy/SECURITY.md deploy/Caddyfile.score-it.example deploy/images.env deploy/sign_release.sh "$DEST/"
printf 'SCORE_IT_IMAGE=%s\nPOSTGRES_IMAGE=%s\nCADDY_IMAGE=%s\n' "$APP_IMAGE" "$PG_IMAGE" "$CADDY_IMAGE" > "$DEST/release.env"
python3 deploy/verify_release.py manifest --archive "$DEST/images.tar" \
  --output "$DEST/images.tsv" --platform "$PLATFORM" \
  --image "$APP_IMAGE" --image "$PG_IMAGE" --image "$CADDY_IMAGE"
printf 'RELEASE=%s\nPLATFORM=%s\n' "$RELEASE" "$PLATFORM" > "$DEST/VERSION"
bash deploy/scan_release.sh "$DEST"
python3 - "$DEST" <<'PY'
import hashlib
from pathlib import Path
import sys
root = Path(sys.argv[1])
with (root / 'SHA256SUMS').open('w') as output:
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS':
            sha = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    sha.update(block)
            output.write(f'{sha.hexdigest()}  {path.relative_to(root)}\n')
PY
echo "RELEASE_READY=$DEST"
