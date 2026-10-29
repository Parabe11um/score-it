#!/bin/sh
# Rebuild Trixie's exact Perl ABI; only perl-base enters the APP image.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
unset PERL5LIB PERLLIB PERL5OPT
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:trixie
test "$(dpkg --print-architecture)" = amd64
SOURCE_VERSION=5.40.1-6+deb13u1
VERSION="$SOURCE_VERSION+scoreit1"
apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates curl dpkg-dev python3 \
    debhelper fakeroot file cpio libcrypt-dev libdb-dev libgdbm-dev libgdbm-compat-dev \
    netbase procps zlib1g-dev libbz2-dev dist util-linux
mkdir -p /src/perl /out
cd /src/perl
python3 - <<'PY' >source.sha256
import json
for name,sha in json.load(open('/run/score-it-perl-regex-sources.json'))['app']['source_files'].items():
    print(sha+'  '+name)
PY
while read -r SHA FILE; do
    curl --fail --show-error --location --retry 3 \
        "https://deb.debian.org/debian/pool/main/p/perl/$FILE" --output "$FILE"
done <source.sha256
sha256sum --check source.sha256
dpkg-source --no-check -x "perl_$SOURCE_VERSION.dsc"
cd perl-5.40.1
sh /run/score-it-perl-regex-build.sh apply app
sed -i "1s/$SOURCE_VERSION/$VERSION/" debian/changelog
useradd --system --user-group --no-create-home perlbuild
chown -R perlbuild:perlbuild /src/perl
export DEB_BUILD_OPTIONS="parallel=$(nproc)"
runuser -u perlbuild -- dpkg-buildpackage --build=binary --no-sign --no-pre-clean
sh /run/score-it-perl-regex-build.sh test app
test "$(dpkg-deb --field "../perl-base_${VERSION}_amd64.deb" Version)" = "$VERSION"
cp "../perl-base_${VERSION}_amd64.deb" /out/
sh /run/score-it-perl-regex-build.sh manifest app
