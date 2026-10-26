#!/bin/sh
# Build host only: Debian Bookworm DLA-4859-1, preserving the database ABI/base.
# https://security-tracker.debian.org/tracker/DLA-4859-1
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
SSL_VERSION='3.0.22-1~deb12u2'
set -- "libssl3=$SSL_VERSION" "openssl=$SSL_VERSION"
for SPEC do
    test "$(dpkg-query -W -f='${db:Status-Status}' "${SPEC%%=*}")" = installed
done
PLAN="$(mktemp)"
trap 'rm -f "$PLAN"' EXIT HUP INT TERM
apt-get update
apt-get --simulate --no-install-recommends --no-remove --only-upgrade install "$@" >"$PLAN"
cat "$PLAN"
awk '
    $1 == "Remv" || $1 == "Purg" { exit 1 }
    $1 == "Inst" || $1 == "Conf" {
        sub(/:.*/, "", $2)
        if ($2 != "libssl3" && $2 != "openssl") exit 1
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
echo POSTGRES_OPENSSL_SECURITY_UPDATE_OK
