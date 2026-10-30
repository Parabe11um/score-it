#!/bin/sh
# Connected build stage only. Its CA package/files never enter the runtime.
set -eu
export LC_ALL=C
test "$#" -eq 1
DEST="$1"
case "$DEST" in /*) ;; *) echo 'Expected an absolute output directory.' >&2; exit 2 ;; esac
test ! -L "$DEST"
test -s /etc/ssl/certs/ca-certificates.crt
TZ_VERSION='2026f-0+deb12u1'
TZ_SHA256='3254eb870c8a949518214805aee5e4276af7699988ea8b9a3a583cd0f1eab31c'
TZ_POOL='https://security.debian.org/debian-security/pool/updates/main/t/tzdata'
TZ_SNAPSHOT='https://snapshot.debian.org/archive/debian-security/20261030T000000Z/pool/updates/main/t/tzdata'
WORK="$(mktemp -d)"
trap 'rm -rf -- "$WORK"' EXIT HUP INT TERM
chown _apt "$WORK"
TZ_PACKAGE="$WORK/tzdata_${TZ_VERSION}_all.deb"
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
mkdir -p "$DEST"
install -m 0644 "$TZ_PACKAGE" "$DEST/tzdata_${TZ_VERSION}_all.deb"
printf 'POSTGRES_TZDATA_DOWNLOAD_VERIFIED version=%s sha256=%s\n' "$TZ_VERSION" "$TZ_SHA256"
