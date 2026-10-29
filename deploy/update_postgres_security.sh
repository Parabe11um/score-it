#!/bin/sh
# Build host only: Debian Bookworm DLA-4859-1, preserving the database ABI/base.
# https://security-tracker.debian.org/tracker/DLA-4859-1
# Bookworm tzdata DLA-4854-1 is a data update, preserving libc/ICU.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
SSL_VERSION='3.0.22-1~deb12u2'
TZ_VERSION='2026e-0+deb12u1'
TZ_SHA256='ac306a4def5f43b8b7c36485803eaea952977c5d58b3a4ca75b3ee08e101244c'
TZ_POOL='https://security.debian.org/debian-security/pool/updates/main/t/tzdata'
TZ_SNAPSHOT='https://snapshot.debian.org/archive/debian-security/20261024T000000Z/pool/updates/main/t/tzdata'
for PACKAGE in libssl3 openssl tzdata; do
    test "$(dpkg-query -W -f='${db:Status-Status}' "$PACKAGE")" = installed
done
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT HUP INT TERM
# The public package stays in a private directory writable by APT's downloader.
# No extra downloader, repository, trust key or runtime APT setting is added.
chown _apt "$WORK"
PLAN="$WORK/apt-plan.txt"
TZ_PACKAGE="$WORK/tzdata_${TZ_VERSION}_all.deb"
apt-get update
# A newer tzdata may replace the pinned version in the live APT index. Fetch
# the exact reviewed bytes independently of that index; the dated official
# snapshot preserves them if the live security pool later drops the file.
download_tzdata() {
    /usr/lib/apt/apt-helper -o Acquire::Retries=2 -o Acquire::https::Timeout=20 \
        download-file "$1/tzdata_${TZ_VERSION}_all.deb" "$TZ_PACKAGE" "SHA256:$TZ_SHA256"
}
if ! download_tzdata "$TZ_POOL"; then
    rm -f -- "$TZ_PACKAGE"
    download_tzdata "$TZ_SNAPSHOT"
fi
printf '%s  %s\n' "$TZ_SHA256" "$TZ_PACKAGE" | sha256sum --check
test "$(dpkg-deb --field "$TZ_PACKAGE" Package)" = tzdata
test "$(dpkg-deb --field "$TZ_PACKAGE" Version)" = "$TZ_VERSION"
test "$(dpkg-deb --field "$TZ_PACKAGE" Architecture)" = all
printf 'POSTGRES_TZDATA_PACKAGE_VERIFIED version=%s sha256=%s\n' "$TZ_VERSION" "$TZ_SHA256"
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
