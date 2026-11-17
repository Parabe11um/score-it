#!/bin/sh
# Native backport on Debian Bookworm, without changing the libz SONAME or ABI.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive DEB_BUILD_PROFILES=nobiarch
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
test "$(dpkg --print-architecture)" = amd64
SOURCE='1:1.2.13.dfsg-1'
VERSION="$SOURCE+scoreit1"
UPSTREAM=df84af25dc1942490e1d1c899a07619152a46148
PATCH=/run/score-it-zlib.patch
PROBE=/run/score-it-zlib-probe.c
PATCH_SHA256=9313965453827a594b1479187562bcf991390abde209da3f000a4fc689822ff0
PROBE_SHA256=1405fc428b66db6adb85d66367c0115aa06be50d32b74d53cbcfd4636859c755
printf '%s  %s\n' "$PATCH_SHA256" "$PATCH" "$PROBE_SHA256" "$PROBE" | sha256sum --check

# Read Debian's signed source indices using the existing Bookworm keyring.
sed 's/^Types: deb$/Types: deb-src/' /etc/apt/sources.list.d/debian.sources \
    >/etc/apt/sources.list.d/score-it-zlib.sources
grep -qx 'Types: deb-src' /etc/apt/sources.list.d/score-it-zlib.sources
apt-get update
apt-get install -y --no-install-recommends build-essential debhelper dpkg-dev
test "$(dpkg-query -W -f='${Version}' zlib1g)" = "$SOURCE"

mkdir -p /src/zlib /out
cd /src/zlib
apt-get source --download-only --only-source "zlib=$SOURCE"
dpkg-source -x "zlib_${SOURCE#*:}.dsc" source
cd source
test "$(dpkg-parsechangelog -S Version)" = "$SOURCE"

# Apply the Bookworm-adapted upstream state reset via the Debian patch series.
install -m 0644 "$PATCH" debian/patches/score-it-gzwrite-error-state.patch
printf '%s\n' score-it-gzwrite-error-state.patch >>debian/patches/series
sed -i "1s/($SOURCE)/($VERSION)/" debian/changelog
test "$(dpkg-parsechangelog -S Version)" = "$VERSION"
dpkg-source --before-build .
dpkg-buildpackage --build=any --no-sign
# Debian's build recipe does not fail on an unsuccessful upstream test.
make test >../upstream-tests.log 2>&1
cat ../upstream-tests.log
grep -F 'zlib test OK' ../upstream-tests.log
grep -F 'zlib shared test OK' ../upstream-tests.log
if grep -F 'test FAILED' ../upstream-tests.log; then exit 1; fi

PACKAGE="../zlib1g_${VERSION#*:}_amd64.deb"
test "$(dpkg-deb --field "$PACKAGE" Package)" = zlib1g
test "$(dpkg-deb --field "$PACKAGE" Version)" = "$VERSION"
test "$(dpkg-deb --field "$PACKAGE" Architecture)" = amd64
dpkg-deb --extract "$PACKAGE" ../package
LIBRARY=$(find "$(cd ../package && pwd)" -type f -name 'libz.so.1.2.13' -print)
test -f "$LIBRARY"
DIR=$(dirname "$LIBRARY")
test "$(readlink -f "$DIR/libz.so.1")" = "$LIBRARY"

# Compare the versioned exported ABI and test the exact packaged shared object.
exports() {
    nm -D --defined-only --with-symbol-versions "$1" | awk 'NF >= 3 {print $3}' | sort -u
}
exports /lib/x86_64-linux-gnu/libz.so.1.2.13 >../stock-symbols
exports "$LIBRARY" >../patched-symbols
test -s ../stock-symbols
cmp ../stock-symbols ../patched-symbols
cc -O2 -Wall -Wextra -DHAVE_UNISTD_H -D_LARGEFILE64_SOURCE -I. "$PROBE" -Wl,-l:libz.so.1 -o ../probe
test "$(LD_LIBRARY_PATH="$DIR" ldd ../probe | awk '$1 == "libz.so.1" {print $3}')" = "$DIR/libz.so.1"
for MODE in wbT wb0; do
    STATUS=0
    timeout 10 ../probe "$MODE" || STATUS=$?
    test "$STATUS" -eq 42
    echo "ZLIB_STOCK_CONTROL_REJECTED mode=$MODE exit=$STATUS"
    LD_LIBRARY_PATH="$DIR" timeout 10 ../probe "$MODE"
done

LIB_SHA256=$(sha256sum "$LIBRARY" | cut -d' ' -f1)
cat >/out/zlib-bookworm-backport.txt <<PROOF
source=$SOURCE
version=$VERSION
cve=CVE-2026-85091
upstream=$UPSTREAM
patch_sha256=$PATCH_SHA256
probe_sha256=$PROBE_SHA256
stock=wbT:42,wb0:42
patched=wbT:0,wb0:0
exports=identical
upstream_tests=passed
lib_sha256=$LIB_SHA256
PROOF
cp "$PACKAGE" /out/zlib1g_bookworm_amd64.deb
cat /out/zlib-bookworm-backport.txt
