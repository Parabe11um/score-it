#!/bin/sh
# Bootstrap the fixed APT on the pinned Debian base, before any other build downloads.
# CVE-2026-109518: DLA-4880-1 (bookworm), DSA-6607-1 (trixie).
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
case "$ID:$VERSION_CODENAME" in
    debian:bookworm) VERSION=2.6.2; LIBRARY=libapt-pkg6.0 ;;
    debian:trixie) VERSION=3.0.4; LIBRARY=libapt-pkg7.0 ;;
    *) echo 'Unsupported base for the reviewed APT update.' >&2; exit 1 ;;
esac

# The vulnerable APT needs retries to hit the hard-link reuse path. Keep
# signature/hash verification enabled and disable retries for this bootstrap.
apt_get() {
    apt-get -o Acquire::Retries=0 "$@"
}

for PACKAGE in apt "$LIBRARY"; do
    test "$(dpkg-query -W -f='${db:Status-Status}' "$PACKAGE")" = installed
done
PLAN="$(mktemp)"
trap 'rm -f -- "$PLAN"' EXIT HUP INT TERM
set -- "apt=$VERSION" "$LIBRARY=$VERSION"
apt_get update
apt_get --simulate --no-install-recommends --no-remove --only-upgrade install "$@" >"$PLAN"
cat "$PLAN"
awk -v library="$LIBRARY" '
    $1 == "Remv" || $1 == "Purg" { exit 1 }
    $1 == "Inst" || $1 == "Conf" {
        sub(/:.*/, "", $2)
        if ($2 != "apt" && $2 != library) exit 1
    }
' "$PLAN"
apt_get install -y --no-install-recommends --no-remove --only-upgrade "$@"
for PACKAGE in apt "$LIBRARY"; do
    test "$(dpkg-query -W -f='${Version}' "$PACKAGE")" = "$VERSION"
done
apt_get check
test -z "$(dpkg --audit)"
rm -rf /var/lib/apt/lists/*
rm -f /var/lib/dpkg/status-old
printf 'APT_SECURITY_UPDATE_OK codename=%s apt=%s library=%s\n' "$VERSION_CODENAME" "$VERSION" "$LIBRARY"
