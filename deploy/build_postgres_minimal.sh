#!/bin/sh
# Rebuild the pinned Debian source package, retaining its patches and layout.
# Build dependencies stay in this stage; the runtime receives only the .deb.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
test "$PG_VERSION" = '17.11-1.pgdg12+2'
mkdir -p /src /out
cd /src
SOURCE=https://apt.postgresql.org/pub/repos/apt/pool/main/p/postgresql-17
for FILE in postgresql-17_17.11.orig.tar.bz2 postgresql-17_17.11-1.pgdg12+2.debian.tar.xz; do
    curl --fail --show-error --location --retry 3 "$SOURCE/$FILE" --output "$FILE"
done
printf '%s  %s\n' \
    dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979 postgresql-17_17.11.orig.tar.bz2 \
    a39663c19ad0bcb05b6c58843104c6633373271383beebb323c291ab990f9d63 postgresql-17_17.11-1.pgdg12+2.debian.tar.xz | sha256sum --check
tar -xjf postgresql-17_17.11.orig.tar.bz2
tar -xJf postgresql-17_17.11-1.pgdg12+2.debian.tar.xz -C postgresql-17.11
cd postgresql-17.11
# Check that future packaging changes cannot silently re-enable these features.
test -f /usr/share/postgresql-common/server/postgresql.mk
grep -F -- '--with-libxml' /usr/share/postgresql-common/server/postgresql.mk >/dev/null
grep -F -- '--with-libxslt' /usr/share/postgresql-common/server/postgresql.mk >/dev/null
cat >>debian/rules <<'MAKE'

# score-it does not use SQL/XML, XSLT or LLVM/JIT.
CONFIGURE_FLAGS := $(filter-out --with-libxml --with-libxslt,$(CONFIGURE_FLAGS)) --without-libxml --without-libxslt --without-llvm
MAKE
sed -i '1s/17.11-1.pgdg12+2/17.11-1.pgdg12+2+scoreit1/' debian/changelog
# Apply the distribution patches before configuring; no local CVE suppressions.
dpkg-source --before-build .
export DEB_BUILD_PROFILES='pkg.postgresql.nollvm nodoc nocheck'
export DEB_BUILD_OPTIONS="nocheck parallel=$(nproc)"
dpkg-buildpackage --build=binary --no-sign --no-check-builddeps
# Run the upstream core regression suite as an unprivileged user as well as the
# image compatibility tests run by CI. Only the broad packaging suite is skipped.
chown -R postgres:postgres /src
gosu postgres make -C build check MAX_CONNECTIONS=4
PACKAGE=../postgresql-17_17.11-1.pgdg12+2+scoreit1_amd64.deb
test -f "$PACKAGE"
if dpkg-deb --field "$PACKAGE" Depends | grep -E 'libxml|libxslt|libllvm'; then
    echo 'Removed PostgreSQL features still have runtime dependencies.' >&2
    exit 1
fi
cp "$PACKAGE" /out/
build/src/bin/pg_config/pg_config --configure >/out/postgres-configure.txt
grep -F -- '--without-libxml' /out/postgres-configure.txt
grep -F -- '--without-libxslt' /out/postgres-configure.txt
grep -F -- '--without-llvm' /out/postgres-configure.txt
printf '%s\n' 'POSTGRES_UPSTREAM_REGRESSION_OK' >/out/postgres-regression.txt
dpkg-query -W -f='${Package}\t${Version}\n' >/out/postgres-build-packages.tsv
printf '%s\n' \
    'upstream=17.11' 'debian_source=17.11-1.pgdg12+2' \
    'runtime_package=17.11-1.pgdg12+2+scoreit1' \
    'orig_sha256=dd27f2b3c59e73ed14aa3324901242bf69a032a6347805f274e6260322d42979' \
    'debian_sha256=a39663c19ad0bcb05b6c58843104c6633373271383beebb323c291ab990f9d63' \
    >/out/postgres-source.txt
