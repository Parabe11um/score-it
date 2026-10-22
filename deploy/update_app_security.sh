#!/bin/sh
# Build-host-only, reviewed Debian 13 updates; never run on the deployment host.
# https://security-tracker.debian.org/tracker/DSA-6531-1 (OpenSSL)
# https://security-tracker.debian.org/tracker/DSA-6572-1 (CA roots)
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:trixie

SSL_VERSION='3.5.7-1~deb13u3'
set -- 'ca-certificates=20250419+deb13u1' "libssl3t64=$SSL_VERSION" "openssl=$SSL_VERSION"
# Keep a provider already shipped by the base in sync, without adding one.
if [ "$(dpkg-query -W -f='${db:Status-Status}' openssl-provider-legacy 2>/dev/null || true)" = installed ]; then
    set -- "$@" "openssl-provider-legacy=$SSL_VERSION"
fi
ALLOWED=' '
for SPEC do
    PACKAGE="${SPEC%%=*}"
    test "$(dpkg-query -W -f='${db:Status-Status}' "$PACKAGE")" = installed
    ALLOWED="$ALLOWED$PACKAGE "
done
PLAN="$(mktemp)"
trap 'rm -f "$PLAN"' EXIT HUP INT TERM
apt-get update
apt-get --simulate --no-install-recommends --no-remove --only-upgrade install "$@" >"$PLAN"
cat "$PLAN"
awk -v allowed="$ALLOWED" '
    $1 == "Remv" || $1 == "Purg" { exit 1 }
    $1 == "Inst" || $1 == "Conf" {
        sub(/:.*/, "", $2)
        if (index(allowed, " " $2 " ") == 0) exit 1
    }
' "$PLAN"
apt-get install -y --no-install-recommends --no-remove --only-upgrade "$@"
for SPEC do
    test "$(dpkg-query -W -f='${Version}' "${SPEC%%=*}")" = "${SPEC#*=}"
done
apt-get check
test -z "$(dpkg --audit)"
rm -rf /var/lib/apt/lists/*
rm -f /var/lib/dpkg/status-old
echo APP_SECURITY_UPDATES_OK
