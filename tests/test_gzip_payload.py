"""Reject a source-only or partial gzip fix, including overwritten old files."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from check_gzip_runtime import FOLLOWUP, FOLLOWUP_SHA256, PREFIX, check_archive
from test_caddy_payload import binary, layer_bytes, saved_image


IMAGE = 'score-it:gzip-test'


def payload():
    raw = binary()
    return {'usr/bin/gzip': raw,
        'var/lib/dpkg/status': b'Package: gzip\nStatus: install ok installed\nVersion: 1.13-1+deb13u1+scoreit12.1\n\n',
        PREFIX + 'gzip-source.txt': ('build_distribution=bookworm\nruntime_package=1.13-1+deb13u1+scoreit12.1\n'
            'followup_commit=' + FOLLOWUP + '\nfollowup_sha256=' + FOLLOWUP_SHA256 + '\n').encode(),
        PREFIX + 'gzip-binary.sha256': (hashlib.sha256(raw).hexdigest() + '  /usr/bin/gzip\n').encode(),
        PREFIX + 'gzip-regression.txt': b'GZIP_LZH_STATE_RESET_OK\nGZIP_FIRST_PATCH_CONTROL_REJECTED exit=42\n'
            b'GZIP_UPSTREAM_REGRESSION_OK\nGZIP_UPSTREAM_NONROOT_OK\n# TOTAL: 28\n# SKIP: 0\n# FAIL: 0\n# ERROR: 0\n'}


class GzipPayloadTests(unittest.TestCase):
    def verify(self, files, oci=False, prior=None, profile='postgres'):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'saved.tar'
            layers = ([layer_bytes(prior, link=False)] if prior else []) + [layer_bytes(files, link=False)]
            saved_image(path, layers, oci=oci, image=IMAGE)
            return check_archive(path, IMAGE, profile)

    def test_accepts_bound_payload_in_docker_and_oci(self):
        for oci in (False, True):
            self.assertEqual(self.verify(payload(), oci)['version'], '1.13-1+deb13u1+scoreit12.1')

    def test_rejects_original_binary_even_after_overwrite_or_deletion(self):
        for prior in ({'usr/bin/gzip': b'original'}, {'usr/bin/.wh.gzip': b''}):
            with self.assertRaises(RuntimeError): self.verify(payload(), prior=prior)

    def test_rejects_hash_mismatch(self):
        files = payload(); files['usr/bin/gzip'] = b'\x7fELFother'
        with self.assertRaisesRegex(RuntimeError, 'hash differs'): self.verify(files)

    def test_rejects_stale_package_record_in_an_old_layer(self):
        prior = {'var/lib/dpkg/status': b'Package: gzip\nStatus: install ok installed\nVersion: 1.12-1\n\n'}
        with self.assertRaisesRegex(RuntimeError, 'Stale gzip package'): self.verify(payload(), prior=prior)

    def test_rejects_first_fix_only_and_missing_regression(self):
        files = payload(); files[PREFIX + 'gzip-source.txt'] = files[PREFIX + 'gzip-source.txt'].replace(FOLLOWUP.encode(), b'first-fix-only')
        with self.assertRaisesRegex(RuntimeError, 'complete gzip'): self.verify(files)
        files = payload(); files[PREFIX + 'gzip-regression.txt'] = files[PREFIX + 'gzip-regression.txt'].replace(b'GZIP_LZH_STATE_RESET_OK', b'incomplete')
        with self.assertRaisesRegex(RuntimeError, 'regression evidence'): self.verify(files)

    def test_rejects_failed_tests_wrong_distribution_and_missing_binary(self):
        files = payload(); files[PREFIX + 'gzip-regression.txt'] = files[PREFIX + 'gzip-regression.txt'].replace(b'# FAIL: 0', b'# FAIL: 1')
        with self.assertRaisesRegex(RuntimeError, 'tests did not pass'): self.verify(files)
        with self.assertRaisesRegex(RuntimeError, 'Stale gzip package'): self.verify(payload(), profile='app')
        files = payload(); del files['usr/bin/gzip']
        with self.assertRaisesRegex(RuntimeError, 'Missing gzip binary'): self.verify(files)


if __name__ == '__main__':
    unittest.main()
