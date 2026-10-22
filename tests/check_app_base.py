#!/usr/bin/env python3
"""Record the exact app base and reject a missing OpenSSL/CA security update."""
import argparse
import inspect
import json
import subprocess

from check_caddy_runtime import docker, require

MINIMUM = {
    "ca-certificates": "20250419+deb13u1",
    "libssl3t64": "3.5.7-1~deb13u3",
    "openssl": "3.5.7-1~deb13u3",
}
INVENTORY = r'''
import json, os, platform, ssl, subprocess
from pathlib import Path
release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
raw = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}\t${Version}\t${db:Status-Status}\n'], text=True)
packages = {name: version for name, version, status in (line.split('\t') for line in raw.splitlines()) if status == 'installed'}
report = {'os_id': release['ID'].strip('"'), 'codename': release['VERSION_CODENAME'].strip('"'),
          'python': platform.python_version(), 'openssl': ssl.OPENSSL_VERSION,
          'openssl_version': list(ssl.OPENSSL_VERSION_INFO), 'uid': os.geteuid(), 'packages': packages}
'''


def check_inventory(report):
    require((report['os_id'], report['codename']) == ('debian', 'trixie'), 'Expected Debian 13 app base')
    require(report['python'] == '3.12.15' and report['uid'] == 10001, 'Python/user contract changed')
    require(tuple(report['openssl_version'][:3]) >= (3, 5, 7), 'Python loaded an old OpenSSL library')
    for package, minimum in MINIMUM.items():
        version = report['packages'].get(package)
        require(bool(version) and subprocess.run(['dpkg', '--compare-versions', version, 'ge', minimum],
                                                check=False).returncode == 0,
                'Missing app security update: ' + package)
    require(not {'libsqlite3-0', 'libssl3', 'util-linux', 'liblastlog2-2', 'libwtmpdb0'} & report['packages'].keys(),
            'Old/removed runtime package retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--platform', default='linux/amd64')
    args = parser.parse_args()
    identity = json.loads(docker('image', 'inspect', args.image))[0]['Id']
    # Use Debian's comparator inside the image; no host dpkg is needed.
    program = ('import subprocess\nMINIMUM = ' + repr(MINIMUM) + '\n'
               + inspect.getsource(require) + '\n' + inspect.getsource(check_inventory)
               + '\n' + INVENTORY + '\ncheck_inventory(report)\nprint(json.dumps(report))')
    report = json.loads(docker('run', '--rm', '--pull=never', '--platform', args.platform,
                              '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                              '--security-opt', 'no-new-privileges:true', '--entrypoint', 'python',
                              identity, '-c', program))
    print(json.dumps({'image': args.image, 'image_id': identity, **report}, indent=2, sort_keys=True))
    print('APP_BASE_SECURITY_OK', flush=True)


if __name__ == '__main__':
    main()
