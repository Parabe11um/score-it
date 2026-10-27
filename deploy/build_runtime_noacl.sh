#!/bin/sh
# Build the same Debian tool versions without unused extended-ACL support.
# Runs only in disposable builders; the server loads the finished images.
set -eu
export LC_ALL=C DEBIAN_FRONTEND=noninteractive
. /etc/os-release
case "$ID:$VERSION_CODENAME" in
    debian:bookworm|debian:trixie) ;;
    *) echo 'Unexpected no-ACL tools build base.' >&2; exit 1 ;;
esac
test "$(dpkg --print-architecture)" = amd64
PINS=/run/score-it-acl-sources.sha256
test -s "$PINS"
apt-get update
apt-get install -y --no-install-recommends build-essential ca-certificates curl dpkg-dev \
    debhelper gettext texinfo groff libattr1-dev libacl1-dev libselinux1-dev \
    gperf bison libgmp3-dev libssl-dev libsystemd-dev autoconf automake autopoint \
    dh-exec python3 xz-utils fakeroot
useradd --system --user-group --no-create-home toolscheck
mkdir -p /src/noacl /out
printf 'distribution=%s\nconfigure=--disable-acl\n' "$VERSION_CODENAME" >/out/acl-tools-source.txt
: >/out/acl-tools-tests.txt
: >/out/acl-tools-binaries.sha256

for PACKAGE in coreutils sed tar; do
    case "$VERSION_CODENAME:$PACKAGE" in
        bookworm:coreutils) VERSION=9.1-1 ;;
        bookworm:sed) VERSION=4.9-1+deb12u1 ;;
        bookworm:tar) VERSION=1.34+dfsg-1.2+deb12u1 ;;
        trixie:coreutils) VERSION=9.7-3 ;;
        trixie:sed) VERSION=4.9-2+deb13u1 ;;
        trixie:tar) VERSION=1.35+dfsg-3.1 ;;
    esac
    REVISION="$VERSION+~scoreitnoacl1"
    mkdir "/src/noacl/$PACKAGE"
    cd "/src/noacl/$PACKAGE"
    PREFIX="$VERSION_CODENAME/$PACKAGE/"
    awk -v prefix="$PREFIX" 'index($2, prefix) == 1 { sub(prefix, "", $2); print $1 "  " $2 }' "$PINS" >sources.sha256
    test "$(wc -l <sources.sha256)" -ge 3
    while read -r DIGEST FILE; do
        case "$FILE" in "$PACKAGE"_*) ;; *) exit 1 ;; esac
        curl --fail --show-error --location --retry 3 \
            "https://deb.debian.org/debian/pool/main/$(printf %s "$PACKAGE" | cut -c 1)/$PACKAGE/$FILE" \
            --output "$FILE"
    done <sources.sha256
    sha256sum --check sources.sha256
    # Every file named by the DSC is present and pinned above, including the
    # upstream signature when supplied. Do not mix distro source packages.
    dpkg-source --no-check -x "${PACKAGE}_${VERSION}.dsc" source
    cd source
    test "$(dpkg-parsechangelog -S Version)" = "$VERSION"
    python3 - "$VERSION" "$REVISION" <<'PY'
from pathlib import Path
import sys
old, new = sys.argv[1:]
changelog = Path('debian/changelog')
text = changelog.read_text()
first, rest = text.split('\n', 1)
assert first.count('(' + old + ')') == 1
changelog.write_text(first.replace('(' + old + ')', '(' + new + ')') + '\n' + rest)
rules = Path('debian/rules')
text = rules.read_text()
matches = [(before, after) for before, after in (
    ('dh_auto_configure -- ', 'dh_auto_configure -- --disable-acl '),
    ('./configure \\\n', './configure --disable-acl \\\n'),
) if before in text]
assert len(matches) == 1, 'Ambiguous configure rule'
before, after = matches[0]
assert text.count(before) == 1 and '--disable-acl' not in text
rules.write_text(text.replace(before, after))
PY
    chown -R toolscheck:toolscheck "/src/noacl/$PACKAGE"
    # tar's complete suite rejects root. sed/tar run the normal packaging tests;
    # Debian coreutils intentionally disables its full suite in debian/rules.
    # No nocheck option is used. Focused installed-tool probes run below too.
    runuser -u toolscheck -- env "DEB_BUILD_OPTIONS=parallel=$(nproc)" \
        dpkg-buildpackage --build=binary --no-sign --no-pre-clean
    python3 - "$PACKAGE" "$REVISION" "$VERSION_CODENAME" <<'PY'
from pathlib import Path
import hashlib
import re
import subprocess
import sys
package, version, distro = sys.argv[1:]
configs = [p for p in Path('.').rglob('config.h') if 'debian' not in p.parts]
assert configs and all(re.search(r'^#define USE_ACL 0$', p.read_text(), re.M) for p in configs), 'ACL remains enabled'
root = Path('debian') / package
assert root.is_dir()
elf_count = 0
for path in root.rglob('*'):
    if path.is_symlink() or not path.is_file():
        continue
    with path.open('rb') as source:
        if source.read(4) != b'\x7fELF':
            continue
    dynamic = subprocess.check_output(['readelf', '--dynamic', '--wide', str(path)], text=True)
    symbols = subprocess.check_output(['readelf', '--dyn-syms', '--wide', str(path)], text=True)
    assert 'libacl' not in dynamic, str(path)
    assert not re.search(r'\bUND\s+acl_', symbols), str(path)
    elf_count += 1
assert elf_count > 0
deb = Path('..') / f'{package}_{version}_amd64.deb'
assert subprocess.check_output(['dpkg-deb', '--field', str(deb), 'Version'], text=True).strip() == version
for field in ('Depends', 'Pre-Depends'):
    value = subprocess.check_output(['dpkg-deb', '--field', str(deb), field], text=True)
    assert not re.search(r'\blibacl\d*\b', value), 'Package still depends on libacl'
with Path('/out/acl-tools-source.txt').open('a') as out:
    out.write(f'{package}={version}\n')
with Path('/out/acl-tools-tests.txt').open('a') as out:
    out.write(f'NOACL_PACKAGE_ELF_OK package={package} elf_files={elf_count}\n')
critical = {'coreutils': ('cp', 'install', 'mv'), 'sed': ('sed',), 'tar': ('tar',)}[package]
with Path('/out/acl-tools-binaries.sha256').open('a') as out:
    for name in critical:
        files = [p for p in root.rglob(name) if p.is_file() and not p.is_symlink()
                 and p.parent.name in ('bin', 'sbin')]
        assert len(files) == 1, (name, files)
        out.write(hashlib.sha256(files[0].read_bytes()).hexdigest() + '  /usr/bin/' + name + '\n')
PY
    cp "../${PACKAGE}_${REVISION}_amd64.deb" /out/
    # Keep a concise log of successful native package tests and their source
    # identities. Full build output remains available in the build/CI log.
    printf 'NOACL_PACKAGE_BUILD_OK package=%s version=%s uid=%s\n' \
        "$PACKAGE" "$REVISION" "$(id -u toolscheck)" >>/out/acl-tools-tests.txt
    sed "s#  #  $VERSION_CODENAME/$PACKAGE/#" ../sources.sha256 >>/out/acl-tools-source.txt
done
# Exercise the packaged programs as well as the later final-image probes.
dpkg -i /out/coreutils_*_amd64.deb /out/sed_*_amd64.deb /out/tar_*_amd64.deb
runuser -u toolscheck -- sh /run/score-it-noacl-probe.sh "$(id -u toolscheck)" >>/out/acl-tools-tests.txt
cat /out/acl-tools-source.txt /out/acl-tools-tests.txt /out/acl-tools-binaries.sha256
