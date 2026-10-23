#!/bin/sh
# Build the reviewed Debian source for the runtime's own distribution/ABI.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
case "$ID:$VERSION_CODENAME" in
    debian:bookworm) DISTRO=12 ;;
    debian:trixie) DISTRO=13 ;;
    *) echo 'Unexpected gzip build base.' >&2; exit 1 ;;
esac
test "$(dpkg --print-architecture)" = amd64
VERSION=1.13-1+deb13u1
REVISION="$VERSION+scoreit$DISTRO.1"
SOURCE=https://deb.debian.org/debian/pool/main/g/gzip
PATCH=/run/score-it-gzip-followup.patch
PROBE=/run/score-it-gzip-probe.c
apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates curl dpkg-dev debhelper texinfo less util-linux
mkdir -p /src/gzip /out
cd /src/gzip
for FILE in gzip_1.13.orig.tar.xz gzip_1.13-1+deb13u1.debian.tar.xz gzip_1.13-1+deb13u1.dsc; do
    curl --fail --show-error --location --retry 3 "$SOURCE/$FILE" --output "$FILE"
done
printf '%s  %s\n' \
    7454eb6935db17c6655576c2e1b0fabefd38b4d0936e0f87f48cd062ce91a057 gzip_1.13.orig.tar.xz \
    ffc69d13290009f42dbb4d10a34839350eff3fdaf4be1fda03d10b238ba2f77d gzip_1.13-1+deb13u1.debian.tar.xz \
    0c25763da4f7242fbe7e9185b69bcb3f986011b04446b66a84c52bbdf731d308 gzip_1.13-1+deb13u1.dsc \
    1e217051be23a32a8653c630f5f03a96e8070e8d9557f885e252609b3e905e00 "$PATCH" | sha256sum --check
# All three files have been checked against the explicit SHA-256 pins above.
dpkg-source --no-check -x gzip_1.13-1+deb13u1.dsc
cd gzip-1.13
# Keep the first Debian fix as a negative control, never as runtime payload.
mkdir ../baseline
cp unlzh.c ../baseline/
cp "$PATCH" debian/patches/score-it-lzh-state-reset.patch
printf '%s\n' score-it-lzh-state-reset.patch >>debian/patches/series
sed -i "1s/$VERSION/$REVISION/" debian/changelog
if [ "$DISTRO" = 12 ]; then
    # Preserve Bookworm's package paths when rebuilding Trixie source; do not
    # depend on a newer dpkg to migrate /bin aliases on a merged-/usr runtime.
    sed -i 's#--bindir=/usr/bin#--bindir=/bin#' debian/rules
    sed -i 's#/usr/bin/#/bin/#g' debian/gzip.links
fi
dpkg-source --before-build .
export DEB_BUILD_OPTIONS="parallel=$(nproc)"
# Do not use nocheck: Debian's build invokes the upstream regression suite.
dpkg-buildpackage --build=binary --no-sign --no-pre-clean
dpkg-source --before-build .
# The packaging build runs as root, which skips write-error. Rerun the entire
# suite unprivileged so that this normal filesystem-permission case is tested.
useradd --system --user-group --no-create-home gzipcheck
chown -R gzipcheck:gzipcheck /src/gzip
rm -f builddir/tests/*.log builddir/tests/*.trs
runuser -u gzipcheck -- make -C builddir check
test -f builddir/tests/test-suite.log
grep -E '^# TOTAL: +[1-9][0-9]*$' builddir/tests/test-suite.log
grep -E '^# FAIL: +0$' builddir/tests/test-suite.log
grep -E '^# ERROR: +0$' builddir/tests/test-suite.log
grep -E '^# SKIP: +0$' builddir/tests/test-suite.log
# Test the actual upstream decoder before its first input read. The first
# Debian patch must fail this control; the complete follow-up must pass.
cc -O2 -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -I../baseline -Ibuilddir -I. -Ilib -Ibuilddir/lib "$PROBE" -o ../baseline-probe
STATUS=0
../baseline-probe || STATUS=$?
test "$STATUS" = 42
cc -O2 -ffunction-sections -fdata-sections -Wl,--gc-sections \
    -Ibuilddir -I. -Ilib -Ibuilddir/lib "$PROBE" -o ../fixed-probe
../fixed-probe >/out/gzip-regression.txt
printf '%s\n' 'GZIP_FIRST_PATCH_CONTROL_REJECTED exit=42' 'GZIP_UPSTREAM_REGRESSION_OK' \
    'GZIP_UPSTREAM_NONROOT_OK' >>/out/gzip-regression.txt
cat builddir/tests/test-suite.log >>/out/gzip-regression.txt
PACKAGE="../gzip_${REVISION}_amd64.deb"
test "$(dpkg-deb --field "$PACKAGE" Version)" = "$REVISION"
test "$(dpkg-deb --field "$PACKAGE" Essential)" = yes
cp "$PACKAGE" /out/
case "$DISTRO" in
    12) BINARY=debian/gzip/bin/gzip ;;
    13) BINARY=debian/gzip/usr/bin/gzip ;;
esac
test -x "$BINARY"
sha256sum "$BINARY" | awk '{print $1 "  /usr/bin/gzip"}' >/out/gzip-binary.sha256
printf '%s\n' 'upstream=1.13' "debian_source=$VERSION" "runtime_package=$REVISION" \
    "build_distribution=$VERSION_CODENAME" \
    'orig_sha256=7454eb6935db17c6655576c2e1b0fabefd38b4d0936e0f87f48cd062ce91a057' \
    'debian_sha256=ffc69d13290009f42dbb4d10a34839350eff3fdaf4be1fda03d10b238ba2f77d' \
    'dsc_sha256=0c25763da4f7242fbe7e9185b69bcb3f986011b04446b66a84c52bbdf731d308' \
    'followup_commit=e7378c2d421be6a286922374425680bbe9ad8b7d' \
    'followup_sha256=1e217051be23a32a8653c630f5f03a96e8070e8d9557f885e252609b3e905e00' \
    >/out/gzip-source.txt
cat /out/gzip-source.txt /out/gzip-binary.sha256 /out/gzip-regression.txt
