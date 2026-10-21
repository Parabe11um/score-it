#!/bin/sh
# Apply DLA-4845-1 without upgrading unrelated runtime libraries.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
VERSION='20250419~deb12u2'
PLAN="$(mktemp)"
trap 'rm -f "$PLAN"' EXIT HUP INT TERM
apt-get update
apt-get --simulate --no-install-recommends --no-remove --only-upgrade \
    install "ca-certificates=$VERSION" >"$PLAN"
cat "$PLAN"
awk '
  $1 == "Remv" || $1 == "Purg" { exit 1 }
  $1 == "Inst" || $1 == "Conf" { if ($2 != "ca-certificates") exit 1 }
' "$PLAN"
apt-get install -y --no-install-recommends --no-remove --only-upgrade "ca-certificates=$VERSION"
test "$(dpkg-query -W -f='${Version}' ca-certificates)" = "$VERSION"
apt-get check
rm -rf /var/lib/apt/lists/*
rm -f /var/lib/dpkg/status-old
echo CA_ROOTS_SECURITY_UPDATE_OK
