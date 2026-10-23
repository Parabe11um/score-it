#!/bin/sh
# Rebuild libpq at its existing upstream major, retaining the Debian packaging.
# Signed APT source indices authenticate all source bytes before extraction.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
VERSION=18.6-3.pgdg12+1
REVISION=${VERSION}+scoreit1
mkdir -p /src/libpq /out
cd /src/libpq
apt-get source --download-only --only-source "postgresql-18=$VERSION"
printf '%s  %s\n' \
    555610c24d53e4316da5b7d3fc25c279d96856d5e0e23ee308c328c5fa881d9f \
    postgresql-18_18.6.orig.tar.bz2 | sha256sum --check
dpkg-source --extract "postgresql-18_${VERSION}.dsc" postgresql-18.6
cd postgresql-18.6
cat >>debian/rules <<'MAKE'

# Only libpq5 is delivered from this build. Keep TLS/GSSAPI and its ABI.
CONFIGURE_FLAGS := $(filter-out --with-ldap --with-llvm,$(CONFIGURE_FLAGS)) --without-ldap --without-llvm
MAKE
sed -i "1s/$VERSION/$REVISION/" debian/changelog
export DEB_BUILD_PROFILES='pkg.postgresql.nollvm nodoc nocheck'
export DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)"
dpkg-buildpackage --build=binary --no-sign --no-check-builddeps
PACKAGE=../libpq5_${REVISION}_amd64.deb
test -f "$PACKAGE"
if dpkg-deb --field "$PACKAGE" Depends | grep -E 'libldap|liblber|libsasl'; then
    echo 'libpq still depends on LDAP libraries.' >&2
    exit 1
fi
cp "$PACKAGE" /out/
build/src/bin/pg_config/pg_config --configure >/out/libpq-configure.txt
grep -F -- '--without-ldap' /out/libpq-configure.txt
printf '%s\n' 'upstream=18.6' "debian_source=$VERSION" "runtime_package=$REVISION" \
    >/out/libpq-source.txt
cd ..
sha256sum "postgresql-18_${VERSION}.dsc" postgresql-18_18.6.orig.tar.bz2 \
    "postgresql-18_${VERSION}.debian.tar.xz" >>/out/libpq-source.txt
