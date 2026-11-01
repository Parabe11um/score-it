#!/usr/bin/env python3
"""Build and verify the pinned GNU tar backports in disposable builders."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess


CVES = ('CVE-2026-5704', 'CVE-2026-18508', 'CVE-2026-18477')
CONTROL_REASONS = dict(zip(CVES, ('listing_extraction_disagree',
                                'escaped_hardlink_accepted', 'temporary_directory_escaped')))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_manifest(directory, profile):
    raw = (directory / 'sources.json').read_bytes()
    manifest = json.loads(raw)
    require(manifest.get('schema') == 1 and manifest.get('cves') == list(CVES), 'Unknown tar source policy')
    require(profile in {'bookworm', 'trixie'}, 'Unknown tar build profile')
    selected = manifest['profiles'][profile]
    patch = directory / selected['patch']
    require(patch.is_file() and not patch.is_symlink()
            and digest(patch.read_bytes()) == selected['patch_sha256'], 'Tar patch hash differs')
    return raw, selected, patch


def verify_tree(source, expected):
    for name, wanted in expected.items():
        path = source / name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'Unsafe tar source path')
        require(not path.is_symlink(), 'Symlink substituted for tar source: ' + name)
        if wanted is None:
            require(not path.exists(), 'Tar patch target already exists: ' + name)
        else:
            require(path.is_file() and digest(path.read_bytes()) == wanted,
                    'Tar source hash differs: ' + name)


def run(command, cwd=None, timeout=300, capture=False):
    result = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL,
                            capture_output=capture, text=True, timeout=timeout,
                            env={**os.environ, 'LC_ALL': 'C', 'TAR_OPTIONS': ''})
    if not capture:
        require(result.returncode == 0, 'Tar build command failed: ' + command[0])
    return result


def unprivileged(expected):
    require(type(expected) is int and expected > 0 and os.getuid() == expected,
            'Tar native tests must use the expected nonroot UID')


def control(args):
    unprivileged(args.uid)
    raw, profile, _ = read_manifest(args.patch_dir, args.profile)
    verify_tree(args.source, profile['before'])
    require(not args.build.exists(), 'Tar control directory already exists')
    args.build.mkdir()
    source = args.build / 'source'
    shutil.copytree(args.source, source, symlinks=True)
    build = args.build / 'build'
    build.mkdir()
    # A separate pristine copy prevents configure/regeneration in the control
    # build from changing the production package's source tree.
    run([str(source / 'configure'), '--disable-acl', '--without-selinux', '--disable-nls'], cwd=build)
    parallel = str(min(os.cpu_count() or 1, 4))
    for name in ('gnu', 'lib', 'src'):
        run(['make', '-j' + parallel, '-C', name], cwd=build)
    binary = build / 'src/tar'
    cases = {}
    for cve in CVES:
        result = run(['sh', str(args.probe), str(args.uid), str(binary), cve], capture=True, timeout=30)
        wanted = 'TAR_SECURITY_PROBE_FAILED case=' + cve + ' reason=' + CONTROL_REASONS[cve]
        require(result.returncode == 42 and result.stderr.strip() == wanted and not result.stdout,
                'Pristine tar did not reproduce the bounded regression: ' + cve)
        cases[cve] = {'exit': result.returncode, 'reason': CONTROL_REASONS[cve]}
        print('TAR_SECURITY_NEGATIVE_CONTROL_OK cve=' + cve + ' exit=42', flush=True)
    value = {'schema': 1, 'profile': args.profile, 'source_version': profile['source_version'],
             'manifest_sha256': digest(raw), 'probe_sha256': digest(args.probe.read_bytes()),
             'uid': args.uid, 'binary_sha256': digest(binary.read_bytes()), 'cases': cases}
    args.output.write_text(json.dumps(value, indent=2) + '\n')


def apply(args):
    raw, profile, patch = read_manifest(args.patch_dir, args.profile)
    verify_tree(args.source, profile['before'])
    patches = args.source / 'debian/patches'
    target = patches / 'score-it-archive-security.patch'
    series = patches / 'series'
    require(not target.exists() and 'score-it-archive-security.patch' not in series.read_text(),
            'Tar security patch already registered')
    target.write_bytes(patch.read_bytes())
    with series.open('a') as output:
        output.write('\nscore-it-archive-security.patch\n')
    # Apply through quilt so the existing Debian patch stack remains intact.
    result = subprocess.run(['quilt', '--quiltrc=/dev/null', 'push', '--fuzz=0'],
                            cwd=args.source, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=30,
                            env={**os.environ, 'LC_ALL': 'C', 'QUILT_PATCHES': 'debian/patches'})
    require(result.returncode == 0 and 'fuzz' not in (result.stdout + result.stderr).lower(),
            'Tar quilt application failed: ' + result.stdout + result.stderr)
    verify_tree(args.source, profile['after'])
    value = {'manifest_sha256': digest(raw), 'patch_sha256': profile['patch_sha256'],
             'source_after': profile['after']}
    args.output.write_text(json.dumps(value, indent=2) + '\n')
    print('TAR_SECURITY_SOURCE_APPLIED profile=' + args.profile, flush=True)


def check_evidence(evidence, manifest_raw, profile_name, tar_sha256, probe_sha256):
    """Pure release policy: bind native proof to the archived/runtime binary."""
    manifest = json.loads(manifest_raw)
    require(manifest.get('schema') == 1 and manifest.get('cves') == list(CVES), 'Unknown tar evidence policy')
    profile = manifest['profiles'][profile_name]
    require(evidence.get('schema') == 1 and evidence.get('state') == 'TAR_SECURITY_NATIVE_VERIFIED'
            and evidence.get('profile') == profile_name
            and evidence.get('package_version') == profile['package_version'], 'Tar evidence profile/version differs')
    require(evidence.get('manifest_sha256') == digest(manifest_raw)
            and evidence.get('patch_sha256') == profile['patch_sha256']
            and evidence.get('probe_sha256') == probe_sha256
            and evidence.get('source_after') == profile['after'], 'Tar evidence source differs')
    uid = evidence.get('uid')
    require(type(uid) is int and uid > 0, 'Tar native tests ran as root or UID is absent')
    require(re.fullmatch('[0-9a-f]{64}', tar_sha256) is not None
            and evidence.get('binary_sha256') == tar_sha256, 'Tar native/archive binary differs')
    controls = evidence.get('negative_controls', {})
    require(controls.get('schema') == 1 and controls.get('profile') == profile_name
            and controls.get('source_version') == profile['source_version']
            and controls.get('manifest_sha256') == digest(manifest_raw)
            and controls.get('probe_sha256') == probe_sha256 and controls.get('uid') == uid
            and re.fullmatch('[0-9a-f]{64}', controls.get('binary_sha256', '')) is not None
            and controls['binary_sha256'] != tar_sha256, 'Tar pristine control differs')
    expected_controls = {cve: {'exit': 42, 'reason': CONTROL_REASONS[cve]} for cve in CVES}
    require(controls.get('cases') == expected_controls, 'Missing tar negative controls')
    expected_output = ''.join('TAR_SECURITY_CASE_OK cve=' + cve + '\n' for cve in CVES)
    expected_output += 'TAR_SECURITY_PROBE_OK uid=' + str(uid) + '\n'
    require(evidence.get('probe_output') == expected_output, 'Incomplete tar native probes')
    suite = evidence.get('native_suite', {})
    require(suite.get('passed') is True and type(suite.get('successful')) is int
            and suite['successful'] >= 100
            and re.fullmatch('[0-9a-f]{64}', suite.get('sha256', '')) is not None,
            'Missing complete native tar suite')
    require(evidence.get('stable_source_verified') == sorted(
        name for name in profile['after'] if name.endswith(('.c', '.h', '.at'))),
        'Tar compiled-source verification differs')
    return {'package_version': profile['package_version'], 'cves': list(CVES)}


def record(args):
    unprivileged(args.uid)
    manifest_raw, profile, _ = read_manifest(args.patch_dir, args.profile)
    applied = json.loads(args.applied.read_text())
    require(applied == {'manifest_sha256': digest(manifest_raw), 'patch_sha256': profile['patch_sha256'],
                        'source_after': profile['after']}, 'Tar apply receipt differs')
    # autoreconf legitimately regenerates configure/Makefile inputs. C, headers
    # and the actual regression tests must still be the reviewed patched bytes.
    stable = {name: value for name, value in profile['after'].items() if name.endswith(('.c', '.h', '.at'))}
    verify_tree(args.source, stable)
    logs = [p for p in args.source.rglob('testsuite.log') if p.parent.name == 'tests'
            and '.pc' not in p.parts and 'debian' not in p.parts]
    require(len(logs) == 1, 'Missing or ambiguous complete tar testsuite log')
    suite_raw = logs[0].read_bytes()
    summary = suite_raw.decode(errors='strict')
    successful = re.findall(r'^(?:All )?(\d+) tests? (?:were|was) (?:successful|behaved as expected)\.$', summary, re.M)
    require(len(successful) == 1 and int(successful[0]) >= 100
            and not re.search(r'\b[1-9][0-9]* tests? (?:were|was) (?:failed|unexpected)', summary),
            'Complete tar native suite did not pass')
    result = run(['sh', str(args.probe), str(args.uid), str(args.binary)], capture=True, timeout=30)
    if result.returncode != 0 or result.stderr:
        print('TAR_SECURITY_PACKAGED_PROBE_DIAGNOSTIC ' + json.dumps({
            'exit': result.returncode, 'stdout': result.stdout[-4096:],
            'stderr': result.stderr[-4096:]}), flush=True)
    require(result.returncode == 0 and not result.stderr, 'Patched packaged tar failed bounded probes')
    value = {'schema': 1, 'state': 'TAR_SECURITY_NATIVE_VERIFIED', 'profile': args.profile,
             'package_version': profile['package_version'], 'uid': args.uid,
             'manifest_sha256': digest(manifest_raw), 'patch_sha256': profile['patch_sha256'],
             'probe_sha256': digest(args.probe.read_bytes()), 'source_after': profile['after'],
             'stable_source_verified': sorted(stable), 'binary_sha256': digest(args.binary.read_bytes()),
             'negative_controls': json.loads(args.control.read_text()), 'probe_output': result.stdout,
             'native_suite': {'passed': True, 'successful': int(successful[0]), 'sha256': digest(suite_raw)}}
    check_evidence(value, manifest_raw, args.profile, value['binary_sha256'], value['probe_sha256'])
    args.output.write_text(json.dumps(value, indent=2) + '\n')
    print('TAR_SECURITY_NATIVE_VERIFIED profile=' + args.profile, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('control', 'apply', 'record'):
        command = commands.add_parser(name)
        command.add_argument('--source', type=Path, required=True)
        command.add_argument('--profile', choices=('bookworm', 'trixie'), required=True)
        command.add_argument('--patch-dir', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name != 'apply':
            command.add_argument('--uid', type=int, required=True)
            command.add_argument('--probe', type=Path, required=True)
        if name == 'control':
            command.add_argument('--build', type=Path, required=True)
        if name == 'record':
            command.add_argument('--binary', type=Path, required=True)
            command.add_argument('--control', type=Path, required=True)
            command.add_argument('--applied', type=Path, required=True)
    args = parser.parse_args()
    globals()[args.command](args)


if __name__ == '__main__':
    main()
