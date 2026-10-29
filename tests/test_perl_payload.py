"""Reject partial Perl backports, stale layers and inconsistent native evidence."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from check_perl_runtime import CORE, MODULES, PINS, PREFIX, PROOF, VERSION, check_archive
from test_caddy_payload import layer_bytes, saved_image


IMAGE = 'score-it:perl-test'


def payload(profile='postgres'):
    packages = dict.fromkeys(CORE, VERSION) if profile == 'postgres' else {'perl-base': '5.40.1-6+deb13u1+scoreit1'}
    files = {'var/lib/dpkg/status': ''.join('Package: ' + name + '\nStatus: install ok installed\nVersion: '
              + version + '\n\n' for name, version in packages.items()).encode()}
    if profile == 'app':
        return files
    for module, name in MODULES.items():
        files[PREFIX + name] = ('reviewed ' + module + '\n').encode()
    hashes = {PREFIX + name: hashlib.sha256(files[PREFIX + name]).hexdigest() for name in MODULES.values()}
    probe = Path(__file__).resolve().parents[1] / 'deploy/perl_security_probe.pl'
    fields = {**PINS, 'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest()}
    files[PROOF + 'perl-security-source.txt'] = ''.join(k + '=' + v + '\n' for k, v in fields.items()).encode()
    files[PROOF + 'perl-modules.sha256'] = ''.join(sha + '  /' + path + '\n' for path, sha in hashes.items()).encode()
    markers = ['PERL_UPSTREAM_TESTS_OK static shared', 'PERL_SECURITY_REGRESSION_OK',
        'PERL_NATIVE_PROBE_NONROOT_OK', 'PERL_PROBE_UID=100', 'PERL_TAR_SIZE_AND_ALIGNMENT_OK',
        'PERL_TAR_CONTROL_REJECTED=original-control exit=42',
        'PERL_TAR_CONTROL_REJECTED=first-control exit=42']
    for module, name in MODULES.items():
        if module != 'Archive::Tar':
            markers += ['PERL_POD_MARGIN_OK=' + module, 'PERL_POD_CONTROL_REJECTED=' + module + ' exit=42']
        markers.append('PERL_MODULE\t' + module + '\t/build/' + name + '\t' + hashes[PREFIX + name])
    files[PROOF + 'perl-security-tests.txt'] = ('\n'.join(markers) + '\n').encode()
    return files


class PerlPayloadTests(unittest.TestCase):
    def verify(self, files, profile='postgres', oci=False, prior=None):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'saved.tar'
            layers = ([layer_bytes(prior, link=False)] if prior else []) + [layer_bytes(files, link=False)]
            saved_image(archive, layers, oci=oci, image=IMAGE)
            return check_archive(archive, IMAGE, profile)

    def test_accepts_docker_and_oci_with_bound_native_evidence(self):
        for oci in (False, True):
            evidence = self.verify(payload(), oci=oci)
            self.assertEqual(set(evidence['packages']), CORE)
            self.assertEqual(len(evidence['module_sha256']), 5)
            self.assertFalse(self.verify(payload('app'), profile='app', oci=oci)['module_sha256'])

    def test_overwritten_deleted_and_alternative_modules_are_rejected(self):
        for name in (PREFIX + 'Archive/Tar.pm', PREFIX + 'Archive/.wh.Tar.pm',
                     'usr/local/lib/perl/Archive/Tar.pm', PREFIX + 'Pod/Text.pm.dpkg-old'):
            with self.subTest(path=name), self.assertRaises(RuntimeError):
                self.verify(payload(), prior={name: b'old module'})

    def test_every_formatter_hash_and_presence_are_required(self):
        for path in MODULES.values():
            files = payload(); files[PREFIX + path] = b'old module'
            with self.subTest(path=path), self.assertRaises(RuntimeError): self.verify(files)
            files = payload(); del files[PREFIX + path]
            with self.assertRaises(RuntimeError): self.verify(files)

    def test_original_package_in_any_layer_is_rejected(self):
        prior = {'var/lib/dpkg/status': payload()['var/lib/dpkg/status'].replace(VERSION.encode(), b'5.36.0-7+deb12u4')}
        with self.assertRaisesRegex(RuntimeError, 'stale Perl packages'): self.verify(payload(), prior=prior)

    def test_first_fix_or_missing_native_controls_are_rejected(self):
        for before in (b'PERL_UPSTREAM_TESTS_OK static shared', b'PERL_NATIVE_PROBE_NONROOT_OK',
                       b'PERL_TAR_CONTROL_REJECTED=first-control exit=42',
                       b'PERL_POD_MARGIN_OK=Pod::Text::Color'):
            files = payload(); name = PROOF + 'perl-security-tests.txt'; files[name] = files[name].replace(before, b'missing')
            with self.subTest(marker=before), self.assertRaises(RuntimeError): self.verify(files)
        files = payload(); name = PROOF + 'perl-security-source.txt'
        files[name] = files[name].replace(PINS['tar_alignment_sha256'].encode(), b'first-fix-only')
        with self.assertRaises(RuntimeError): self.verify(files)

    def test_built_source_and_packaged_module_must_match(self):
        files = payload(); name = PROOF + 'perl-security-tests.txt'
        digest = hashlib.sha256(files[PREFIX + 'Pod/Text.pm']).hexdigest().encode()
        files[name] = files[name].replace(digest, b'0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'tested source'): self.verify(files)

    def test_root_native_probe_is_rejected(self):
        files = payload(); name = PROOF + 'perl-security-tests.txt'
        files[name] = files[name].replace(b'PERL_PROBE_UID=100', b'PERL_PROBE_UID=0')
        with self.assertRaisesRegex(RuntimeError, 'normal user'): self.verify(files)

    def test_app_cannot_retain_the_absent_modules_in_old_layers(self):
        for path in MODULES.values():
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.verify(payload('app'), profile='app', prior={'usr/share/perl/5.40/' + path: b'old'})


if __name__ == '__main__':
    unittest.main()
