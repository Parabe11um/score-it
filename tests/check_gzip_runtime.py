#!/usr/bin/env python3
"""Bind the complete gzip source fix to every saved layer and runtime binary.

Only disposable containers with no network or host mounts are used.
"""
import argparse
import base64
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from check_caddy_runtime import Archive, docker, layer_names, require


PREFIX = 'usr/share/score-it/'
FOLLOWUP = 'e7378c2d421be6a286922374425680bbe9ad8b7d'
FOLLOWUP_SHA256 = '1e217051be23a32a8653c630f5f03a96e8070e8d9557f885e252609b3e905e00'
PROFILES = {'app': ('trixie', '1.13-1+deb13u1+scoreit13.1'),
            'postgres': ('bookworm', '1.13-1+deb13u1+scoreit12.1')}


def package_version(raw):
    versions = []
    for paragraph in raw.decode().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Package') == 'gzip' and fields.get('Status') == 'install ok installed':
            versions.append(fields.get('Version'))
    require(len(versions) == 1, 'Missing or ambiguous gzip package inventory')
    return versions[0]


def check_archive(path, image, profile):
    distribution, version = PROFILES[profile]
    found = {}
    inventories = 0
    with Archive(path) as source:
        _, layers = layer_names(source, image, 'linux/amd64')
        for name in layers:
            member = source.members.get(str(PurePosixPath(name)))
            require(member is not None and member.isfile(), 'Missing gzip image layer')
            with source.tar.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r|*') as layer:
                for item in layer:
                    name = str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                            'Unsafe gzip layer path')
                    require(name not in {'bin/.wh.gzip', 'usr/bin/.wh.gzip'}, 'Deleted gzip retained in layers')
                    if name == 'var/lib/dpkg/status':
                        require(item.isfile() and item.size < 16 * 1024 * 1024, 'Invalid gzip package inventory')
                        with layer.extractfile(item) as content:
                            require(package_version(content.read()) == version, 'Stale gzip package retained in layers')
                        inventories += 1
                    if name in {'bin/gzip', 'usr/bin/gzip'}:
                        key = 'binary'
                    elif name.startswith(PREFIX):
                        key = name.removeprefix(PREFIX)
                    else:
                        continue
                    if key not in {'binary', 'gzip-source.txt', 'gzip-binary.sha256', 'gzip-regression.txt'}:
                        continue
                    require(item.isfile() and item.size < 2 * 1024 * 1024, 'Invalid gzip payload type or size')
                    require(key not in found, 'Overwritten gzip payload retained in layers: ' + key)
                    with layer.extractfile(item) as content:
                        found[key] = content.read()
    require(inventories and set(found) == {'binary', 'gzip-source.txt', 'gzip-binary.sha256', 'gzip-regression.txt'},
            'Missing gzip binary/build evidence')
    expected = dict(line.split('=', 1) for line in found['gzip-source.txt'].decode().splitlines())
    require(expected.get('build_distribution') == distribution and expected.get('runtime_package') == version,
            'gzip distribution/version changed')
    require(expected.get('followup_commit') == FOLLOWUP and expected.get('followup_sha256') == FOLLOWUP_SHA256,
            'Missing complete gzip upstream follow-up')
    match = re.fullmatch(rb'([0-9a-f]{64})  /usr/bin/gzip\n', found['gzip-binary.sha256'])
    require(match is not None and found['binary'].startswith(b'\x7fELF')
            and hashlib.sha256(found['binary']).hexdigest() == match[1].decode(), 'gzip binary/build hash differs')
    evidence = found['gzip-regression.txt'].decode()
    require({'GZIP_LZH_STATE_RESET_OK', 'GZIP_FIRST_PATCH_CONTROL_REJECTED exit=42',
             'GZIP_UPSTREAM_REGRESSION_OK'} <= set(evidence.splitlines()), 'Incomplete gzip regression evidence')
    require(re.search(r'^# TOTAL: +[1-9][0-9]*$', evidence, re.M)
            and re.search(r'^# FAIL: +0$', evidence, re.M)
            and re.search(r'^# ERROR: +0$', evidence, re.M), 'gzip upstream tests did not pass')
    return {'version': version, 'binary_sha256': match[1].decode(), 'distribution': distribution}


def check_runtime(identity, profile, evidence):
    payload = ('CREATE TABLE gzip_probe (id integer, note text);\n'
               "INSERT INTO gzip_probe VALUES (1, 'проверка gzip');\n").encode()
    script = r'''
ulimit -t 3
ulimit -f 2048
base64 -d >/tmp/valid.gz
gzip -t /tmp/valid.gz
gzip -dc /tmp/valid.gz >/tmp/plain
gzip -n -c /tmp/plain >/tmp/roundtrip.gz
gzip -dc /tmp/roundtrip.gz >/tmp/roundtrip
cmp /tmp/plain /tmp/roundtrip
head -c 8 /tmp/valid.gz >/tmp/truncated.gz
if gzip -dc /tmp/truncated.gz >/tmp/rejected 2>/dev/null; then exit 21; fi
printf invalid >/tmp/invalid.gz
if gzip -dc /tmp/invalid.gz >/tmp/rejected 2>/dev/null; then exit 22; fi
printf '\037\235' >/tmp/truncated.Z
if gzip -dc /tmp/truncated.Z >/tmp/rejected 2>/dev/null; then exit 23; fi
printf '\037\235\220\130\000' >/tmp/first.Z
printf '\037\240\000\000\000\000' >/tmp/second.lzh
gzip -cd /tmp/first.Z /tmp/second.lzh >/tmp/legacy
test "$(cat /tmp/legacy)" = X
printf 'VERSION='; dpkg-query -W -f='${Version}\n' gzip
printf 'BINARY='; sha256sum /usr/bin/gzip
printf 'PLAIN='; sha256sum /tmp/plain
printf 'UID='; id -u
'''
    user = ('--user', 'postgres') if profile == 'postgres' else ()
    output = docker('run', '--rm', '-i', '--pull=never', '--network', 'none', '--read-only',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                    '--memory', '128m', '--pids-limit', '32',
                    '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777', *user,
                    '--entrypoint', 'sh', identity, '-ec', script,
                    input=base64.b64encode(gzip.compress(payload, mtime=0)).decode(), timeout=30)
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    require(fields.get('VERSION') == evidence['version'], 'Loaded gzip package differs')
    require(fields.get('BINARY', '').split()[0] == evidence['binary_sha256'], 'Loaded gzip binary differs')
    require(fields.get('PLAIN', '').split()[0] == hashlib.sha256(payload).hexdigest(), 'gzip decompressed content differs')
    require(fields.get('UID') == '10001' if profile == 'app' else int(fields.get('UID', '0')) > 0,
            'gzip runtime probe used the wrong user')
    return int(fields['UID'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--profile', choices=PROFILES, required=True)
    args = parser.parse_args()
    identity = json.loads(docker('image', 'inspect', args.image))[0]['Id']
    with tempfile.TemporaryDirectory(prefix='score-it-gzip-') as temp:
        archive = Path(temp) / 'image.tar'
        docker('image', 'save', '--output', str(archive), args.image, timeout=120)
        evidence = check_archive(archive, args.image, args.profile)
    uid = check_runtime(identity, args.profile, evidence)
    require(json.loads(docker('image', 'inspect', args.image))[0]['Id'] == identity, 'gzip image tag changed')
    print(json.dumps({'image': args.image, 'runtime_image_id': identity, 'uid': uid, **evidence}, sort_keys=True))
    print('GZIP_ARCHIVE_AND_RUNTIME_OK=' + args.profile, flush=True)


if __name__ == '__main__':
    main()
