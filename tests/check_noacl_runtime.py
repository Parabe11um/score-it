#!/usr/bin/env python3
"""Bind ACL-free Debian tools and their build evidence to saved layers/runtime."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

from check_caddy_runtime import Archive, docker, layer_names, require
from check_runtime_tools import check_runtime_binary, check_runtime_member, check_runtime_packages

PREFIX = 'usr/share/score-it/'
FILES = {'cp','install','mv','sed','tar'}
VERSIONS = {
    'app': ('trixie', {'coreutils':'9.7-3+~scoreitnoacl1', 'sed':'4.9-2+deb13u1+~scoreitnoacl1',
                      'tar':'1.35+dfsg-3.1+~scoreitnoacl1'}),
    'postgres': ('bookworm', {'coreutils':'9.1-1+~scoreitnoacl1', 'sed':'4.9-1+deb12u1+~scoreitnoacl1',
                           'tar':'1.34+dfsg-1.2+deb12u1+~scoreitnoacl1'}),
}
PROBE = (Path(__file__).resolve().parents[1] / 'deploy/runtime_tools_probe.sh').read_text()


def package_inventory(raw, profile):
    packages = {}
    for block in raw.decode().split('\n\n'):
        fields = dict(row.split(': ', 1) for row in block.splitlines()
                      if ': ' in row and not row.startswith(' '))
        if fields.get('Status') == 'install ok installed':
            name = fields.get('Package')
            require(name and name not in packages, 'Duplicate no-ACL package inventory')
            packages[name] = fields.get('Version')
    check_runtime_packages(set(packages))
    require({name:packages.get(name) for name in VERSIONS[profile][1]} == VERSIONS[profile][1],
            'Old or incomplete ACL-dependent tool packages')
    require(profile != 'app' or not {'passwd','adduser'}.intersection(packages),
            'APP retains ACL-dependent account tools')


def check_archive(path, image, profile):
    distro, packages = VERSIONS[profile]
    found, hashes, inventories, elf_count = {}, {}, 0, 0
    evidence_files = {'acl-tools-source.txt','acl-tools-tests.txt','acl-tools-binaries.sha256'}
    with Archive(path) as source:
        _, layers = layer_names(source, image, 'linux/amd64')
        for layer_name in layers:
            member = source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(), 'Missing no-ACL image layer')
            with source.tar.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r|*') as layer:
                for item in layer:
                    check_runtime_member(item)
                    name = PurePosixPath(item.name)
                    key = name.name
                    command = str(name.parent) in {'bin','usr/bin'} and key in FILES
                    require(str(name) != 'var/lib/dpkg/status-old', 'Old no-ACL package database retained')
                    require(not (str(name.parent) in {'bin','usr/bin'} and key.removeprefix('.wh.') in FILES
                                 and key.startswith('.wh.')), 'Deleted old ACL tool retained')
                    if str(name) == 'var/lib/dpkg/status':
                        require(item.isfile() and item.size < 16*1024*1024, 'Invalid no-ACL package inventory')
                        package_inventory(layer.extractfile(item).read(), profile)
                        inventories += 1
                    elif str(name.parent) == PREFIX.rstrip('/') and key in evidence_files:
                        require(item.isfile() and item.size < 1024*1024 and key not in found,
                                'Invalid or overwritten no-ACL build evidence')
                        found[key] = layer.extractfile(item).read()
                    elif item.isfile():
                        with layer.extractfile(item) as content:
                            first = content.read(4)
                            if first == b'\x7fELF':
                                require(item.size < 256*1024*1024, 'Oversized no-ACL ELF')
                                raw = first + content.read()
                                check_runtime_binary(raw)
                                elf_count += 1
                                if command:
                                    require(key not in hashes, 'Overwritten ACL tool in saved layers')
                                    hashes[key] = hashlib.sha256(raw).hexdigest()
                            else:
                                require(not command, 'No-ACL command is not an ELF file')
                    elif command:
                        require(False, 'No-ACL command is not a regular file')
    require(inventories > 0 and set(found) == evidence_files and set(hashes) == FILES,
            'Missing ACL-free tools or build evidence')
    rows = found['acl-tools-source.txt'].decode().splitlines()
    require(rows[:2] == ['distribution='+distro,'configure=--disable-acl'], 'No-ACL build profile differs')
    for package, version in packages.items():
        require(rows.count(package+'='+version) == 1, 'No-ACL source package differs')
    pins = Path(__file__).resolve().parents[1] / 'deploy/acl_tools_sources.sha256'
    selected = [row for row in pins.read_text().splitlines() if row.split()[1].startswith(distro+'/')]
    recorded = [row for row in rows if re.match(r'^[0-9a-f]{64}  ', row)]
    require(sorted(recorded) == sorted(selected), 'No-ACL source hashes differ from reviewed pins')
    matches = re.findall(r'^([0-9a-f]{64})  /usr/bin/(cp|install|mv|sed|tar)$',
                         found['acl-tools-binaries.sha256'].decode(), re.M)
    require(len(matches) == len(FILES) and {key:value for value,key in matches} == hashes,
            'ACL-free command hashes differ from build evidence')
    tests = found['acl-tools-tests.txt'].decode()
    for package, version in packages.items():
        require(re.search(r'^NOACL_PACKAGE_ELF_OK package='+package+r' elf_files=[1-9][0-9]*$', tests, re.M)
                and re.search(r'^NOACL_PACKAGE_BUILD_OK package='+package+' version='+re.escape(version)+r' uid=[1-9][0-9]*$', tests, re.M),
                'Missing native no-ACL package evidence')
    require(re.search(r'^NOACL_POSIX_TOOLS_OK uid=[1-9][0-9]*$', tests, re.M),
            'Missing native packaged-tool roundtrip')
    return {'distribution':distro, 'packages':packages, 'binary_sha256':hashes, 'elf_files':elf_count}


def parse_runtime(output, profile, evidence):
    uid = '10001' if profile == 'app' else '999'
    rows = output.splitlines()
    require(rows and rows[0] == 'NOACL_POSIX_TOOLS_OK uid='+uid, 'No-ACL probe user/roundtrip differs')
    versions = [row.split('\t') for row in rows if '\t' in row]
    require(len(versions) == 3 and all(len(row) == 2 for row in versions)
            and dict(versions) == VERSIONS[profile][1], 'Runtime no-ACL package versions differ')
    matches = re.findall(r'^([0-9a-f]{64})  /usr/bin/(cp|install|mv|sed|tar)$', output, re.M)
    require(len(matches) == len(FILES) and {key:value for value,key in matches} == evidence['binary_sha256'],
            'Saved/runtime no-ACL command hashes differ')
    return int(uid)


def verify(image, archive, profile):
    identity = json.loads(docker('image','inspect',image))[0]['Id']
    evidence = check_archive(archive,image,profile)
    uid = '10001' if profile == 'app' else '999'
    runtime = PROBE + r'''
for package in coreutils sed tar; do
    printf '%s\t%s\n' "$package" "$(dpkg-query -W -f='${Version}' "$package")"
done
sha256sum /usr/bin/cp /usr/bin/install /usr/bin/mv /usr/bin/sed /usr/bin/tar
'''
    output = docker('run','--rm','--pull=never','--network','none','--read-only',
        '--user',uid+':'+uid,'--cap-drop','ALL','--security-opt','no-new-privileges:true',
        '--memory','128m','--pids-limit','32','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=16m,mode=1777',
        '--entrypoint','sh',identity,'-ec',runtime,'sh',uid)
    actual_uid = parse_runtime(output,profile,evidence)
    require(json.loads(docker('image','inspect',image))[0]['Id'] == identity, 'No-ACL image tag changed')
    print(json.dumps({'check':'noacl_tools','image':image,'runtime_image_id':identity,
                     'uid':actual_uid,**evidence}, sort_keys=True), flush=True)
    print('NOACL_ARCHIVE_AND_RUNTIME_OK='+profile, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--profile',choices=VERSIONS,required=True)
    args = parser.parse_args()
    verify(args.image,args.archive,args.profile)
