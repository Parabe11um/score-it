"""Ensure a layer audit keeps deleted bytes and never follows image links."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from check_secret_audit import materialize_layers, scan
from test_caddy_payload import layer_bytes, saved_image


class SecretAuditTests(unittest.TestCase):
    def test_deleted_payload_is_scanned_in_classic_and_oci_archives(self):
        for oci in (False, True):
            with self.subTest(oci=oci), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / 'contents'
                target.mkdir()
                archive = root / 'image.tar'
                saved_image(archive, [layer_bytes({'old.env': b'earlier secret'}, link=False),
                                      layer_bytes({'.wh.old.env': b''}, link=True)],
                            oci=oci, image='score-it:probe')
                result = materialize_layers(archive, 'score-it:probe', target)
                self.assertEqual(result['regular_payload_bytes'], len(b'earlier secret'))
                self.assertEqual((target / 'layer-0000/000000-old.env').read_bytes(),
                                 b'earlier secret')
                self.assertFalse(any(p.is_symlink() for p in target.rglob('*')))
                self.assertTrue((target / 'image-config.json').is_file())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved_image(root / 'image.tar', [layer_bytes({'../escape': b'x'}, link=False)],
                        image='score-it:probe')
            with self.assertRaisesRegex(RuntimeError, 'Unsafe image path'):
                materialize_layers(root / 'image.tar', 'score-it:probe', root)

    def test_report_excludes_matched_secret_and_source_line(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def fake_run(*args):
                if args[-1] == 'version':
                    return CompletedProcess(args, 0, '8.30.1\n', '')
                (root / 'raw-redacted.json').write_text(json.dumps([
                    {'RuleID': 'probe', 'File': 'old.env', 'StartLine': 1,
                     'Secret': 'sensitive-value', 'Match': 'source-line'}]))
                return CompletedProcess(args, 1, '', '')
            with patch('check_secret_audit.run', side_effect=fake_run):
                result = scan('gitleaks', 'dir', root, root)
            self.assertEqual(result, [{'RuleID': 'probe', 'File': 'old.env', 'StartLine': 1}])


if __name__ == '__main__':
    unittest.main()
