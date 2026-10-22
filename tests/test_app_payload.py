"""Keep build/test source out of all delivered layers, including overwritten files."""

import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from check_app_payload import REQUIRED, check_app_archive
from test_caddy_payload import layer_bytes, saved_image

IMAGE = 'score-it:app-payload-test'


class AppPayloadTests(unittest.TestCase):
    def verify(self, layers, oci=False):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'app.tar'
            saved_image(archive, layers, oci=oci, image=IMAGE)
            check_app_archive(archive, IMAGE)

    def test_accepts_runtime_source_static_and_maintenance_entrypoints(self):
        files = dict.fromkeys(REQUIRED, b'content')
        files['app/poker/migrations/0016_eva_relationship_names.py'] = b'migration'
        files['app/staticfiles/admin/js/vendor/jquery/jquery.min.js'] = b'vendor'
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.verify([layer_bytes(files, link=False)], oci=oci)

    def test_rejects_obsolete_source_even_if_deleted_by_later_layer(self):
        for path in ['app/tests/browser/fixture.py', 'app/tests/browser/package.json',
                     'app/poker/tests/test_views.py', 'app/.github/workflows/tests.yml',
                     'app/Dockerfile.production', 'app/compose.yaml', 'app/README.md',
                     'app/deploy/make_release.sh', 'app/deploy/SECURITY.md',
                     'app/passenger_wsgi.py', 'app/requirements.production.txt',
                     'app/data/private.json']:
            for oci in (False, True):
                with self.subTest(path=path, oci=oci), self.assertRaisesRegex(RuntimeError, 'Non-runtime'):
                    self.verify([
                        layer_bytes({path: b'old'}, link=False),
                        layer_bytes(dict.fromkeys(REQUIRED, b'content'), link=False),
                    ], oci=oci)

    def test_rejects_missing_runtime_source(self):
        files = dict.fromkeys(REQUIRED, b'content')
        del files['app/deploy/configure_db_roles.py']
        with self.assertRaisesRegex(RuntimeError, 'Required application files missing'):
            self.verify([layer_bytes(files, link=False)])

    def test_rejects_application_links_to_external_tooling(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as archive:
            for name in REQUIRED:
                entry = tarfile.TarInfo(name)
                archive.addfile(entry, io.BytesIO())
            entry = tarfile.TarInfo('app/config/unused.py')
            entry.type = tarfile.SYMTYPE
            entry.linkname = '/usr/bin/python'
            archive.addfile(entry)
        with self.assertRaisesRegex(RuntimeError, 'contains a link'):
            self.verify([output.getvalue()])


if __name__ == '__main__':
    unittest.main()
