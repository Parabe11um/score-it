#!/usr/bin/env bash
# Scan the exact saved image bytes, without changing tags or running the apps.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo 'Usage: bash deploy/scan_release.sh /absolute/release-directory' >&2
  exit 1
fi
ROOT="$(realpath "$1")"
. "$ROOT/images.env"
TRIVY_TLS_ARGS=()
case "${RELEASE_TLS_COMPAT:-0}" in
  0) ;;
  1) TRIVY_TLS_ARGS=(-e GODEBUG=tlsmlkem=0,tlssecpmlkem=0) ;;
  *) echo 'RELEASE_TLS_COMPAT must be 0 or 1.' >&2; exit 1 ;;
esac
mkdir -p "$ROOT/security"
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT
docker version > "$ROOT/security/docker-version.txt"
docker run --rm "${TRIVY_TLS_ARGS[@]}" "$TRIVY_IMAGE" --version > "$ROOT/security/trivy-version.txt"
while IFS=$'\t' read -r IMAGE CONFIG PLATFORM; do
  case "$IMAGE" in
    score-it:*) NAME=app ;;
    score-it-postgres:*) NAME=postgres ;;
    score-it-caddy:*) NAME=caddy ;;
    *) echo 'Unexpected image in the release manifest' >&2; exit 1 ;;
  esac
  docker image save --output "$TEMP/$NAME.tar" "$IMAGE"
  # Prove the scanned tag still has exactly the config saved in the release.
  python3 "$ROOT/verify_release.py" manifest --archive "$TEMP/$NAME.tar" \
    --output "$TEMP/$NAME.tsv" --platform "$PLATFORM" --image "$IMAGE"
  test "$(cut -f 2 "$TEMP/$NAME.tsv")" = "$CONFIG"
  docker run --rm "${TRIVY_TLS_ARGS[@]}" -v "$TEMP:/input:ro" -v "$ROOT/security:/reports" \
    -v score_it_trivy_cache:/root/.cache/trivy "$TRIVY_IMAGE" image \
    --input "/input/$NAME.tar" --scanners vuln,secret,misconfig \
    --image-config-scanners secret,misconfig --format json --output "/reports/$NAME.json"
  docker run --rm "${TRIVY_TLS_ARGS[@]}" -v "$ROOT/security:/reports" "$TRIVY_IMAGE" convert \
    --format spdx-json --output "/reports/$NAME.spdx.json" "/reports/$NAME.json"
done < "$ROOT/images.tsv"
python3 - "$ROOT/security" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
blocked = []
summary = []
for name in ('app', 'postgres', 'caddy'):
    report = json.loads((root / (name + '.json')).read_text())
    vulnerabilities = [v for result in report.get('Results', []) for v in result.get('Vulnerabilities') or []]
    secrets = [v for result in report.get('Results', []) for v in result.get('Secrets') or []]
    summary.append(f'{name}: vulnerability_records={len(vulnerabilities)} secrets={len(secrets)}')
    if secrets:
        blocked.append(name + ': secret findings require review')
    if name in ('app', 'postgres'):
        sqlite = [p for result in report.get('Results', [])
                  for p in result.get('Packages') or []
                  if p.get('Name') in ('libsqlite3-0', 'pysqlite3', 'pysqlite3-binary')]
        if sqlite:
            blocked.append(name + ': SQLite package is not allowed in PostgreSQL-only runtime')
    # Explicit release gate for all of the public proxy's embedded Go modules.
    if name in ('caddy', 'postgres'):
        high_go = [v for result in report.get('Results', []) if result.get('Type') == 'gobinary'
                   for v in result.get('Vulnerabilities') or [] if v['Severity'] in ('HIGH', 'CRITICAL')]
        if high_go:
            blocked.append(name + ': unresolved Go module HIGH/CRITICAL findings')
(root / 'summary.txt').write_text('\n'.join(summary + blocked) + '\n')
print('\n'.join(summary + blocked))
if blocked:
    raise SystemExit('RELEASE_BLOCKED: review security reports; no release is ready.')
print('SCAN_COMPLETE: remaining OS/package findings still need assessment; this is not bank approval.')
PY
