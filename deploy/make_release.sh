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
case "${RELEASE_PULL:-1}" in
  1) BUILD_PULL=--pull ;;
  0) BUILD_PULL=--pull=false ;;
  *) echo 'RELEASE_PULL must be 1 (refresh bases) or 0 (use cached bases).' >&2; exit 1 ;;
esac
TLS_COMPAT="${RELEASE_TLS_COMPAT:-0}"
case "$TLS_COMPAT" in
  0|1) ;;
  *) echo 'RELEASE_TLS_COMPAT must be 0 (default TLS) or 1 (build-time compatibility).' >&2; exit 1 ;;
esac
python3 - <<'PY'
import json, re, subprocess
version = json.loads(subprocess.check_output(['docker', 'version', '--format', '{{json .Server}}']))['Version']
match = re.match(r'^(\d+)\.(\d+)\.(\d+)', version)
if not match or tuple(map(int, match.groups())) < (29, 8, 2):
    raise SystemExit('Update build-host Docker Engine/containerd/runc first (Docker >= 29.8.2).')
PY
if [ "$BUILD_PULL" = --pull=false ]; then
  for SOURCE in "$PYTHON_SOURCE_IMAGE" "$POSTGRES_SOURCE_IMAGE" "$GO_SOURCE_IMAGE" "$CADDY_SOURCE_IMAGE"; do
    if [[ ! "$SOURCE" =~ @sha256:[a-f0-9]{64}$ ]]; then
      echo "Cached-base mode requires a SHA256-pinned source: $SOURCE" >&2
      exit 1
    fi
    if ! docker image inspect "$SOURCE" >/dev/null 2>&1; then
      echo "Base image is not cached; run: docker pull --platform $PLATFORM $SOURCE" >&2
      exit 1
    fi
  done
fi
OUTPUT="$(realpath -m "$1")"
mkdir -p "$OUTPUT"
DEST="$OUTPUT/score-it-$RELEASE"
mkdir "$DEST"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
git archive HEAD | tar -x -C "$BUILD_DIR"
docker build "$BUILD_PULL" --platform "$PLATFORM" -f "$BUILD_DIR/Dockerfile.production" \
  --build-arg "PYTHON_IMAGE=$PYTHON_SOURCE_IMAGE" \
  --build-arg "BUILD_TLS_COMPAT=$TLS_COMPAT" \
  --label "org.opencontainers.image.revision=$RELEASE" \
  -t "$APP_IMAGE" "$BUILD_DIR"
docker build "$BUILD_PULL" --platform "$PLATFORM" -f "$BUILD_DIR/deploy/Dockerfile.postgres" \
  --build-arg "POSTGRES_IMAGE=$PG_SOURCE" --build-arg "GO_IMAGE=$GO_SOURCE_IMAGE" \
  --build-arg "BUILD_TLS_COMPAT=$TLS_COMPAT" \
  --label "org.opencontainers.image.revision=$RELEASE" -t "$PG_IMAGE" "$BUILD_DIR"
docker build "$BUILD_PULL" --platform "$PLATFORM" -f "$BUILD_DIR/deploy/Dockerfile.caddy" \
  --build-arg "GO_IMAGE=$GO_SOURCE_IMAGE" --build-arg "CADDY_IMAGE=$CADDY_SOURCE_IMAGE" \
  --build-arg "BUILD_TLS_COMPAT=$TLS_COMPAT" \
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
mkdir -p "$DEST/security"
printf 'RELEASE_PULL=%s\nRELEASE_TLS_COMPAT=%s\n' "${RELEASE_PULL:-1}" "$TLS_COMPAT" > "$DEST/security/build-options.txt"
python3 tests/check_sqlite_payload.py --app-image "$APP_IMAGE" --postgres-image "$PG_IMAGE" \
  --python-source-image "$PYTHON_SOURCE_IMAGE" --platform "$PLATFORM" </dev/null | \
  tee "$DEST/security/sqlite-payload.txt"
python3 tests/check_postgres_runtime.py --image "$PG_IMAGE" --source-image "$PG_SOURCE" \
  --go-image "$GO_SOURCE_IMAGE" </dev/null | tee "$DEST/security/postgres-runtime.txt"
docker run --rm --pull=never --network none --read-only --entrypoint cat "$PG_IMAGE" \
  /usr/share/score-it/postgres-source.txt /usr/share/score-it/postgres-configure.txt \
  /usr/share/score-it/postgres-regression.txt >"$DEST/security/postgres-build.txt"
cp deploy/postgres_feature_preflight.sql "$DEST/"
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
