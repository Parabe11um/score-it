#!/bin/sh
# Rebuild the same Bookworm Perl ABI with reviewed pure-Perl module backports.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
unset PERL5LIB PERLLIB PERL5OPT
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
test "$(dpkg --print-architecture)" = amd64
SOURCE_VERSION=5.36.0-7+deb12u4
VERSION="$SOURCE_VERSION+scoreit2"
PATCHES=/run/score-it-perl-patches
PROBE=/run/score-it-perl-probe.pl
apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates curl dpkg-dev \
    debhelper fakeroot file cpio python3 libdb-dev libgdbm-dev libgdbm-compat-dev netbase procps \
    zlib1g-dev libbz2-dev dist util-linux
mkdir -p /src/perl /out
cd /src/perl
for FILE in perl_5.36.0.orig.tar.xz perl_5.36.0.orig-regen-configure.tar.xz; do
    curl --fail --show-error --location --retry 3 \
        "https://deb.debian.org/debian/pool/main/p/perl/$FILE" --output "$FILE"
done
for FILE in "perl_$SOURCE_VERSION.debian.tar.xz" "perl_$SOURCE_VERSION.dsc"; do
    curl --fail --show-error --location --retry 3 \
        "https://deb.debian.org/debian-security/pool/updates/main/p/perl/$FILE" --output "$FILE"
done
printf '%s  %s\n' \
    0f386dccbee8e26286404b2cca144e1005be65477979beb9b1ba272d4819bcf0 perl_5.36.0.orig.tar.xz \
    10ac353bc5a933403afe60ed1817e7a456f99bdbcaf80c1cdb0eb3a08ea56d4e perl_5.36.0.orig-regen-configure.tar.xz \
    f49d224fa9a6a356e1140f95fb5c864864b5423c9aece94cc68b4d348a3694b6 "perl_$SOURCE_VERSION.debian.tar.xz" \
    6f71e571ae69894ad2115013bbc3207e77dc796581e30a62be0297e7d90ee7b9 "perl_$SOURCE_VERSION.dsc" \
    069672b4beaf96634abd544c3945bd93161721eeba30dab1d90d6bb71fdbb99a "$PATCHES/archive-tar-size-cap.patch" \
    3490fb6744d37105db4784c87cdd072c85a37a4c0457630c5871e669cb1d3d5e "$PATCHES/archive-tar-stream-alignment.patch" \
    a16ea9cd88eb088d852441ac39f9b85d37596754a57b026d78165425f46d2b14 "$PATCHES/pod-text-margins.patch" | sha256sum --check
# All source components are checked against the explicit pins above.
dpkg-source --no-check -x "perl_$SOURCE_VERSION.dsc"
cd perl-5.36.0
mkdir -p ../original-control/Archive ../original-control/Pod/Text ../first-control/Archive
cp cpan/Archive-Tar/lib/Archive/Tar.pm ../original-control/Archive/
cp cpan/podlators/lib/Pod/Text.pm ../original-control/Pod/
cp cpan/podlators/lib/Pod/Text/Color.pm cpan/podlators/lib/Pod/Text/Overstrike.pm \
   cpan/podlators/lib/Pod/Text/Termcap.pm ../original-control/Pod/Text/
for NAME in archive-tar-size-cap archive-tar-stream-alignment pod-text-margins; do
    cp "$PATCHES/$NAME.patch" "debian/patches/score-it-$NAME.patch"
    printf '%s\n' "score-it-$NAME.patch" >>debian/patches/series
    dpkg-source --before-build .
    if [ "$NAME" = archive-tar-size-cap ]; then
        cp cpan/Archive-Tar/lib/Archive/Tar.pm ../first-control/Archive/
    fi
done
sh /run/score-it-perl-regex-build.sh apply postgres
sed -i "1s/$SOURCE_VERSION/$VERSION/" debian/changelog
useradd --system --user-group --no-create-home perlbuild
chown -R perlbuild:perlbuild /src/perl
# Keep the standard Debian static/shared test targets, running as a normal user.
export DEB_BUILD_OPTIONS="parallel=$(nproc)"
runuser -u perlbuild -- dpkg-buildpackage --build=binary --no-sign --no-pre-clean
test -f perl-test-static-stamp
test -f perl-test-shared-stamp
: >/out/perl-security-tests.txt
printf '%s\n' 'PERL_UPSTREAM_TESTS_OK static shared' >>/out/perl-security-tests.txt
probe() {
    runuser -u perlbuild -- sh -ec 'ulimit -v 262144; ulimit -t 10; exec "$@"' sh build-static/perl "$@"
}
for CONTROL in original-control first-control; do
    STATUS=0
    probe -I"../$CONTROL" -Ibuild-static/lib "$PROBE" tar >../control.log 2>&1 || STATUS=$?
    test "$STATUS" = 42
    grep -Fx 'PERL_TAR_PROBE_READY' ../control.log
    printf 'PERL_TAR_CONTROL_REJECTED=%s exit=42\n' "$CONTROL" >>/out/perl-security-tests.txt
done
for CLASS in Pod::Text Pod::Text::Color Pod::Text::Overstrike Pod::Text::Termcap; do
    STATUS=0
    probe -I../original-control -Ibuild-static/lib "$PROBE" pod "$CLASS" >../control.log 2>&1 || STATUS=$?
    test "$STATUS" = 42
    grep -Fx "PERL_POD_PROBE_READY=$CLASS" ../control.log
    printf 'PERL_POD_CONTROL_REJECTED=%s exit=42\n' "$CLASS" >>/out/perl-security-tests.txt
done
probe -Ibuild-static/lib "$PROBE" all >>/out/perl-security-tests.txt
grep -E '^PERL_PROBE_UID=[1-9][0-9]*$' /out/perl-security-tests.txt
printf '%s\n' PERL_NATIVE_PROBE_NONROOT_OK >>/out/perl-security-tests.txt
for PACKAGE in perl-base perl-modules-5.36 libperl5.36 perl; do
    case "$PACKAGE" in perl-modules-5.36) ARCH=all ;; *) ARCH=amd64 ;; esac
    FILE="../${PACKAGE}_${VERSION}_${ARCH}.deb"
    test "$(dpkg-deb --field "$FILE" Version)" = "$VERSION"
    cp "$FILE" /out/
done
STAGED=debian/perl-modules-5.36/usr/share/perl/5.36.0
: >/out/perl-modules.sha256
for FILE in Archive/Tar.pm Pod/Text.pm Pod/Text/Color.pm Pod/Text/Overstrike.pm Pod/Text/Termcap.pm; do
    sha256sum "$STAGED/$FILE" | awk -v path="/usr/share/perl/5.36.0/$FILE" '{print $1 "  " path}' \
        >>/out/perl-modules.sha256
done
printf '%s\n' "debian_source=$SOURCE_VERSION" "runtime_package=$VERSION" 'abi=5.36.0' \
    'build_distribution=bookworm' \
    'orig_sha256=0f386dccbee8e26286404b2cca144e1005be65477979beb9b1ba272d4819bcf0' \
    'regen_sha256=10ac353bc5a933403afe60ed1817e7a456f99bdbcaf80c1cdb0eb3a08ea56d4e' \
    'debian_sha256=f49d224fa9a6a356e1140f95fb5c864864b5423c9aece94cc68b4d348a3694b6' \
    'dsc_sha256=6f71e571ae69894ad2115013bbc3207e77dc796581e30a62be0297e7d90ee7b9' \
    'tar_cap_sha256=069672b4beaf96634abd544c3945bd93161721eeba30dab1d90d6bb71fdbb99a' \
    'tar_alignment_sha256=3490fb6744d37105db4784c87cdd072c85a37a4c0457630c5871e669cb1d3d5e' \
    'pod_sha256=a16ea9cd88eb088d852441ac39f9b85d37596754a57b026d78165425f46d2b14' \
    >/out/perl-security-source.txt
sha256sum "$PROBE" | awk '{print "probe_sha256=" $1}' >>/out/perl-security-source.txt
cat /out/perl-security-source.txt /out/perl-modules.sha256 /out/perl-security-tests.txt

sh /run/score-it-perl-regex-build.sh test postgres
sh /run/score-it-perl-regex-build.sh manifest postgres
