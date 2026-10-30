#!/bin/sh
# Build host only: Debian Bookworm DLA-4859-1, preserving the database ABI/base.
# https://security-tracker.debian.org/tracker/DLA-4859-1
# Bookworm tzdata DLA-4867-1 is a data update, preserving libc/ICU.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
SSL_VERSION='3.0.22-1~deb12u2'
TZ_VERSION='2026f-0+deb12u1'
TZ_SHA256='3254eb870c8a949518214805aee5e4276af7699988ea8b9a3a583cd0f1eab31c'
test "$#" -eq 1
TZ_PACKAGE="$1"
case "$TZ_PACKAGE" in /*) ;; *) echo 'Expected an absolute package path.' >&2; exit 2 ;; esac
test -f "$TZ_PACKAGE" && test ! -L "$TZ_PACKAGE"
# Recheck the read-only build-stage input before any package transaction. The
# runtime base deliberately does not need a CA bundle or an HTTPS downloader.
printf '%s  %s\n' "$TZ_SHA256" "$TZ_PACKAGE" | sha256sum --check
test "$(dpkg-deb --field "$TZ_PACKAGE" Package)" = tzdata
test "$(dpkg-deb --field "$TZ_PACKAGE" Version)" = "$TZ_VERSION"
test "$(dpkg-deb --field "$TZ_PACKAGE" Architecture)" = all
printf 'POSTGRES_TZDATA_PACKAGE_VERIFIED version=%s sha256=%s\n' "$TZ_VERSION" "$TZ_SHA256"
for PACKAGE in libssl3 openssl tzdata; do
    test "$(dpkg-query -W -f='${db:Status-Status}' "$PACKAGE")" = installed
done
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT HUP INT TERM
PLAN="$WORK/apt-plan.txt"
apt-get update
set -- "libssl3=$SSL_VERSION" "openssl=$SSL_VERSION" "$TZ_PACKAGE"
apt-get --simulate --no-install-recommends --no-remove --only-upgrade install "$@" >"$PLAN"
cat "$PLAN"
awk '
    $1 == "Remv" || $1 == "Purg" { exit 1 }
    $1 == "Inst" || $1 == "Conf" {
        sub(/:.*/, "", $2)
        if ($2 != "libssl3" && $2 != "openssl" && $2 != "tzdata") exit 1
    }
' "$PLAN"
apt-get install -y --no-install-recommends --no-remove --only-upgrade "$@"
for SPEC in "libssl3=$SSL_VERSION" "openssl=$SSL_VERSION" "tzdata=$TZ_VERSION"; do
    test "$(dpkg-query -W -f='${Version}' "${SPEC%%=*}")" = "${SPEC#*=}"
done
apt-get check
test -z "$(dpkg --audit)"
rm -rf /var/lib/apt/lists/*
rm -f /var/lib/dpkg/status-old
echo POSTGRES_OPENSSL_SECURITY_UPDATE_OK
