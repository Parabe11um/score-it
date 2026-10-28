#!/usr/bin/env python3
"""Bind the Perl module fixes to saved layers and a bounded disposable runtime."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import tempfile

from check_caddy_runtime import Archive, docker, layer_names, require


VERSION = '5.36.0-7+deb12u4+scoreit2'
CORE = {'perl', 'perl-base', 'perl-modules-5.36', 'libperl5.36'}
MODULES = {'Archive::Tar': 'Archive/Tar.pm', 'Pod::Text': 'Pod/Text.pm',
           'Pod::Text::Color': 'Pod/Text/Color.pm',
           'Pod::Text::Overstrike': 'Pod/Text/Overstrike.pm',
           'Pod::Text::Termcap': 'Pod/Text/Termcap.pm'}
PREFIX = 'usr/share/perl/5.36.0/'
PROOF = 'usr/share/score-it/'
PINS = {
    'debian_source': '5.36.0-7+deb12u4', 'runtime_package': VERSION, 'abi': '5.36.0',
    'build_distribution': 'bookworm',
    'orig_sha256': '0f386dccbee8e26286404b2cca144e1005be65477979beb9b1ba272d4819bcf0',
    'regen_sha256': '10ac353bc5a933403afe60ed1817e7a456f99bdbcaf80c1cdb0eb3a08ea56d4e',
    'debian_sha256': 'f49d224fa9a6a356e1140f95fb5c864864b5423c9aece94cc68b4d348a3694b6',
    'dsc_sha256': '6f71e571ae69894ad2115013bbc3207e77dc796581e30a62be0297e7d90ee7b9',
    'tar_cap_sha256': '069672b4beaf96634abd544c3945bd93161721eeba30dab1d90d6bb71fdbb99a',
    'tar_alignment_sha256': '3490fb6744d37105db4784c87cdd072c85a37a4c0457630c5871e669cb1d3d5e',
    'pod_sha256': 'a16ea9cd88eb088d852441ac39f9b85d37596754a57b026d78165425f46d2b14'}


def inventory(raw, profile):
    installed = {}
    for stanza in raw.decode().split('\n\n'):
        fields = dict(line.split(': ', 1) for line in stanza.splitlines()
                      if ': ' in line and not line.startswith(' '))
        name = fields.get('Package', '')
        if name in CORE or re.fullmatch(r'(?:perl-modules-|libperl)[0-9.]+', name):
            if fields.get('Status') == 'install ok installed':
                require(name not in installed, 'Duplicate Perl package inventory')
                installed[name] = fields.get('Version')
    expected = dict.fromkeys(CORE, VERSION) if profile == 'postgres' else {'perl-base': '5.40.1-6+deb13u1+scoreit1'}
    require(installed == expected, 'Unexpected or stale Perl packages in saved layer')
    return installed


def affected_path(name):
    # Include overwritten/deleted modules and suffixes such as .dpkg-old.
    name = name.replace('/.wh.', '/')
    return bool(re.search(r'(?:^|/)(?:Archive/Tar\.pm|Pod/Text\.pm|'
                          r'Pod/Text/(?:Color|Overstrike|Termcap)\.pm)(?:$|\.)', name))


def check_archive(path, image, profile):
    found, packages = {}, None
    with Archive(path) as source:
        _, layers = layer_names(source, image, 'linux/amd64')
        for layer_name in layers:
            member = source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(), 'Missing Perl image layer')
            with source.tar.extractfile(member) as stream, tarfile.open(fileobj=stream, mode='r|*') as layer:
                for item in layer:
                    name = str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,
                            'Unsafe Perl layer path')
                    if name == 'var/lib/dpkg/status':
                        require(item.isfile() and item.size < 16 * 1024 * 1024, 'Invalid Perl inventory')
                        with layer.extractfile(item) as content:
                            packages = inventory(content.read(), profile)
                    relevant = affected_path(name)
                    if relevant:
                        require(profile == 'postgres' and name in {PREFIX + p for p in MODULES.values()},
                                'Unexpected/deleted/absent-profile Perl module retained in layers')
                    if not relevant and name not in {PROOF + p for p in
                            ('perl-security-source.txt', 'perl-security-tests.txt', 'perl-modules.sha256')}:
                        continue
                    require(item.isfile() and item.size < 2 * 1024 * 1024, 'Invalid Perl module/build evidence')
                    require(name not in found, 'Overwritten Perl module/build evidence retained in layers')
                    with layer.extractfile(item) as content:
                        found[name] = content.read()
    require(packages is not None, 'Missing Perl package inventory')
    if profile == 'app':
        require(not found, 'Optional Perl code/build payload found in app')
        return {'packages': packages, 'module_sha256': {}}
    expected = {PREFIX + p for p in MODULES.values()}
    require(set(found) == expected | {PROOF + p for p in
            ('perl-security-source.txt', 'perl-security-tests.txt', 'perl-modules.sha256')},
            'Missing Perl module or build evidence')
    source = dict(line.split('=', 1) for line in found[PROOF + 'perl-security-source.txt'].decode().splitlines())
    require(all(source.get(key) == value for key, value in PINS.items()), 'Perl source/patch pins differ')
    probe = Path(__file__).resolve().parents[1] / 'deploy/perl_security_probe.pl'
    require(source.get('probe_sha256') == hashlib.sha256(probe.read_bytes()).hexdigest(), 'Perl probe source differs')
    hashes = {}
    for line in found[PROOF + 'perl-modules.sha256'].decode().splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  /(.+)', line)
        require(match is not None and match[2] in expected and match[2] not in hashes, 'Invalid Perl module hashes')
        hashes[match[2]] = match[1]
    require(set(hashes) == expected and all(hashlib.sha256(found[p]).hexdigest() == sha
            for p, sha in hashes.items()), 'Saved Perl module differs from packaged hash')
    tests = found[PROOF + 'perl-security-tests.txt'].decode().splitlines()
    require({'PERL_UPSTREAM_TESTS_OK static shared', 'PERL_SECURITY_REGRESSION_OK', 'PERL_NATIVE_PROBE_NONROOT_OK',
             'PERL_TAR_SIZE_AND_ALIGNMENT_OK',
             'PERL_TAR_CONTROL_REJECTED=original-control exit=42',
             'PERL_TAR_CONTROL_REJECTED=first-control exit=42'} <= set(tests), 'Incomplete Perl regression evidence')
    require(len([line for line in tests if re.fullmatch(r'PERL_PROBE_UID=[1-9][0-9]*', line)]) == 1,
            'Native Perl probe was not run as a normal user')
    require(all('PERL_POD_MARGIN_OK=' + module in tests
                and 'PERL_POD_CONTROL_REJECTED=' + module + ' exit=42' in tests
                for module in MODULES if module != 'Archive::Tar'), 'A Pod::Text formatter was not tested')
    loaded = [line.split('\t') for line in tests if line.startswith('PERL_MODULE\t')]
    require(len(loaded) == 5 and all(len(line) == 4 for line in loaded)
            and {line[1] for line in loaded} == set(MODULES)
            and all(line[3] == hashes[PREFIX + MODULES[line[1]]] for line in loaded),
            'Built Perl modules differ from tested source')
    return {'packages': packages, 'module_sha256': hashes, 'probe_sha256': source['probe_sha256']}


def check_runtime(identity, profile, evidence):
    options = ('--rm', '--pull=never', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges:true', '--memory', '256m', '--pids-limit', '32',
               '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777')
    if profile == 'app':
        script = ('for my $module (qw(Archive::Tar Pod::Text Pod::Text::Color '
                  'Pod::Text::Overstrike Pod::Text::Termcap)) {'
                  'eval "require $module"; die "Unexpected optional Perl module" unless $@ =~ /Can.t locate/;'
                  'print "PERL_MODULE_ABSENT=$module\\n";}')
        output = docker('run', *options, '--entrypoint', 'perl', identity, '-e', script)
        require(len(output.splitlines()) == 5, 'Missing app Perl absence runtime checks')
    else:
        probe = Path(__file__).resolve().parents[1] / 'deploy/perl_security_probe.pl'
        output = docker('run', *options, '-i', '--user', 'postgres', '--entrypoint', 'perl',
                        identity, '-', input=probe.read_text(), timeout=30)
        loaded = [line.split('\t') for line in output.splitlines() if line.startswith('PERL_MODULE\t')]
        require(len(loaded) == 5 and all(len(line) == 4 for line in loaded)
                and {line[1] for line in loaded} == set(MODULES)
                and all(line[2] == '/' + PREFIX + MODULES[line[1]]
                        and line[3] == evidence['module_sha256'][line[2][1:]] for line in loaded)
                and {'PERL_SECURITY_REGRESSION_OK', 'PERL_PROBE_UID=999'} <= set(output.splitlines()),
                'Loaded Perl module/regression/user differs')
        wrappers = docker('run', *options, '--user', 'postgres', '--entrypoint', 'sh', identity, '-ec',
                          '/usr/bin/psql --version; /usr/bin/pg_lsclusters --no-header')
        require('psql (PostgreSQL) 17.11' in wrappers, 'PostgreSQL Perl wrapper failed')
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--profile', choices=('app', 'postgres'), required=True)
    args = parser.parse_args()
    identity = json.loads(docker('image', 'inspect', args.image))[0]['Id']
    with tempfile.TemporaryDirectory(prefix='score-it-perl-') as temp:
        archive = Path(temp) / 'image.tar'
        docker('image', 'save', '--output', str(archive), args.image, timeout=120)
        evidence = check_archive(archive, args.image, args.profile)
    check_runtime(identity, args.profile, evidence)
    require(json.loads(docker('image', 'inspect', args.image))[0]['Id'] == identity, 'Perl image tag changed')
    print(json.dumps({'image': args.image, 'runtime_image_id': identity, **evidence}, sort_keys=True))
    print('PERL_ARCHIVE_AND_RUNTIME_OK=' + args.profile, flush=True)


if __name__ == '__main__':
    main()
