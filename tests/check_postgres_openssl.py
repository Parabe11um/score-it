#!/usr/bin/env python3
"""Bind the Bookworm OpenSSL security packages to every saved layer and runtime."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

from check_caddy_runtime import Archive, docker, layer_names, require
from elf_dependencies import elf

VERSION = '3.0.22-1~deb12u2'
PACKAGES = {'libssl3': VERSION, 'openssl': VERSION}
FILES = {'usr/bin/openssl', 'usr/lib/x86_64-linux-gnu/libssl.so.3',
         'usr/lib/x86_64-linux-gnu/libcrypto.so.3'}
MANIFESTS = {'var/lib/dpkg/info/libssl3:amd64.md5sums', 'var/lib/dpkg/info/openssl.md5sums'}


def package_inventory(raw):
    packages = {}
    for block in raw.decode().split('\n\n'):
        fields = dict(row.split(': ', 1) for row in block.splitlines()
                      if ': ' in row and not row.startswith(' '))
        name = fields.get('Package')
        if fields.get('Status') == 'install ok installed' and name in PACKAGES:
            require(name not in packages, 'Duplicate OpenSSL package')
            packages[name] = fields.get('Version')
    require(packages == PACKAGES, 'Old or incomplete PostgreSQL OpenSSL packages')


def check_archive(path, image):
    hashes, md5s, packaged, manifests, libraries, inventories = {}, {}, {}, set(), [], 0
    with Archive(path) as archive:
        _, layers = layer_names(archive, image, 'linux/amd64')
        for layer_name in layers:
            member = archive.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(), 'Missing OpenSSL image layer')
            with archive.tar.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r|*') as layer:
                for item in layer:
                    name = str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                            'Unsafe OpenSSL layer path')
                    require(name not in {'var/lib/dpkg/status-old', *(str(PurePosixPath(p).parent / ('.wh.'+PurePosixPath(p).name)) for p in FILES)},
                            'Deleted old OpenSSL evidence retained')
                    if name in MANIFESTS:
                        require(item.isfile() and item.size < 4*1024*1024 and name not in manifests,
                                'Invalid or overwritten OpenSSL package manifest')
                        manifests.add(name)
                        for row in layer.extractfile(item).read().decode().splitlines():
                            fields = row.split()
                            if len(fields) == 2 and fields[1] in FILES:
                                require(re.fullmatch('[0-9a-f]{32}', fields[0]) and fields[1] not in packaged,
                                        'Invalid or duplicate packaged OpenSSL hash')
                                packaged[fields[1]] = fields[0]
                    elif name == 'var/lib/dpkg/status':
                        require(item.isfile() and item.size < 16*1024*1024, 'Invalid OpenSSL package inventory')
                        package_inventory(layer.extractfile(item).read())
                        inventories += 1
                    elif item.isfile():
                        with layer.extractfile(item) as content:
                            first = content.read(4)
                            if name in FILES or first == b'\x7fELF':
                                require(item.size < 256*1024*1024, 'Oversized OpenSSL/ELF file')
                                raw = first + content.read()
                                digest = hashlib.sha256(raw).hexdigest()
                                if name in FILES:
                                    require(name not in hashes and first == b'\x7fELF', 'Overwritten or invalid OpenSSL file')
                                    hashes[name] = digest
                                    md5s[name] = hashlib.md5(raw).hexdigest()
                                if first == b'\x7fELF':
                                    soname = elf(raw)['soname']
                                    if soname in {'libssl.so.3','libcrypto.so.3'}:
                                        libraries.append((soname, digest))
                    elif name in FILES:
                        require(False, 'OpenSSL file is not a regular file')
    require(inventories > 0 and set(hashes) == FILES, 'Missing saved OpenSSL files/package inventory')
    # Slim Debian intentionally omits documentation/manpages. Verify the exact
    # executable/library payload using its package manifests, not absent docs.
    require(manifests == MANIFESTS and packaged == md5s, 'OpenSSL files differ from package manifests')
    require({name for name,_ in libraries} == {'libssl.so.3','libcrypto.so.3'}, 'Missing OpenSSL library SONAME')
    require(all(digest == hashes['usr/lib/x86_64-linux-gnu/'+name] for name,digest in libraries),
            'Renamed or old OpenSSL library retained')
    return hashes


RUNTIME = r'''
set -eu
stage=user
trap 'status=$?; if [ "$status" -ne 0 ]; then printf "OPENSSL_RUNTIME_CHECK_FAILED stage=%s exit=%s\n" "$stage" "$status" >&2; fi' EXIT
test "$(id -u)" = 999
stage=distribution
. /etc/os-release
test "$ID:$VERSION_CODENAME" = debian:bookworm
printf 'POSTGRES_OPENSSL_UID=%s\n' "$(id -u)"
for package in libssl3 openssl; do
    stage="package:$package"
    test "$(dpkg-query -W -f='${db:Status-Status}' "$package")" = installed
    printf '%s\t%s\n' "$package" "$(dpkg-query -W -f='${Version}' "$package")"
done
stage=openssl_version
openssl version
stage=sha256
sha256sum /usr/bin/openssl /usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/lib/x86_64-linux-gnu/libcrypto.so.3
'''


def check_runtime(output, hashes):
    rows = output.splitlines()
    require(rows and rows[0] == 'POSTGRES_OPENSSL_UID=999', 'OpenSSL probe is not postgres')
    versions = [row.split('\t') for row in rows if '\t' in row]
    require(len(versions) == 2 and all(len(row) == 2 for row in versions)
            and dict(versions) == PACKAGES, 'Runtime OpenSSL package versions differ')
    found = re.findall(r'^([0-9a-f]{64})  /([^\n]+)$', output, re.M)
    require(len(found) == len(FILES) and {name:digest for digest,name in found} == hashes,
            'Saved/runtime OpenSSL file hashes differ')
    version = [row for row in rows if row.startswith('OpenSSL ')]
    require(len(version) == 1 and version[0].startswith('OpenSSL 3.0.22 '), 'Unexpected loaded OpenSSL version')
    return version[0]


def verify(image, archive):
    identity = json.loads(docker('image','inspect',image))[0]['Id']
    hashes = check_archive(archive, image)
    output = docker('run','--rm','--pull=never','--network','none','--read-only',
        '--user','999:999','--cap-drop','ALL','--security-opt','no-new-privileges:true',
        '--entrypoint','sh',identity,'-ec',RUNTIME)
    version = check_runtime(output, hashes)
    require(json.loads(docker('image','inspect',image))[0]['Id'] == identity, 'OpenSSL image tag changed')
    print(json.dumps({'check':'postgres_openssl','image':image,'runtime_image_id':identity,
        'packages':PACKAGES,'file_sha256':hashes,'openssl_version':version,'uid':999}, sort_keys=True), flush=True)
    print('POSTGRES_OPENSSL_ARCHIVE_AND_RUNTIME_OK', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--archive',type=Path,required=True)
    args = parser.parse_args()
    verify(args.image,args.archive)
