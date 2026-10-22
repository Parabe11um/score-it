"""Run audit transport code locally; Docker and production resources are never used."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser = load('browser_tool_transport', 'check_browser_security.py')
imports = load('import_tool_transport', 'check_import_resources.py')


class ExternalAuditToolsTests(unittest.TestCase):
    def test_fixture_request_uses_stdin_and_needs_no_file_in_the_app(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / 'tests/browser/fixture.py'
            fixture.parent.mkdir(parents=True)
            fixture.write_text('import json,sys\nprint(json.dumps(json.load(sys.stdin)))\n')
            captured = []

            def docker(*arguments, source=None, **kwargs):
                captured.extend(arguments)
                result = subprocess.run([sys.executable, '-c', arguments[-1]], input=source,
                                        capture_output=True, text=True, check=True)
                return result.stdout

            args = SimpleNamespace(database='score_it_container_ci')
            private = {'password': 'synthetic-private-request'}
            with patch.object(browser, 'ROOT', root), patch.object(browser, 'docker', side_effect=docker):
                result = browser.fixture_action('isolated-test-web', args, 'verify', fixture=private)
            self.assertEqual(result['fixture'], private)
            self.assertNotIn('synthetic-private-request', ' '.join(captured))
            self.assertNotIn('/app/tests/', ' '.join(captured))

    def test_fixture_transport_mismatch_fails_before_execution(self):
        source = 'print("should-not-execute")'
        payload = {'source': source, 'sha256': '0' * 64, 'request': {}}
        result = subprocess.run([sys.executable, '-c', browser.FIXTURE_PROGRAM],
                                input=json.dumps(payload), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('should-not-execute', result.stdout)

    def test_import_tools_exist_only_in_a_temporary_directory_and_are_removed(self):
        source = (
            'import argparse,json,pathlib\n'
            'p=argparse.ArgumentParser();p.add_argument("--app-root");p.add_argument("--output")\n'
            'a=p.parse_args();root=pathlib.Path(__file__).parent\n'
            'pathlib.Path(a.output).write_text(json.dumps({"ok":True,"tools":str(root),'
            '"app_root":a.app_root,"corpus":(root/"import_security_corpus.py").read_text()}))\n'
            'print("IMPORT_RESOURCE_LIMITS_OK")\n'
        )
        payload = {'check_import_resources.py': source, 'import_security_corpus.py': '# synthetic corpus\n'}
        result = subprocess.run([sys.executable, '-c', imports.IMAGE_PROGRAM],
                                input=json.dumps(payload), capture_output=True, text=True, check=True)
        markers, _, raw = result.stdout.rpartition('IMPORT_RESOURCE_REPORT=')
        report = json.loads(raw)
        self.assertIn('IMPORT_RESOURCE_LIMITS_OK', markers)
        self.assertEqual(report['app_root'], '/app')
        self.assertEqual(report['corpus'], payload['import_security_corpus.py'])
        self.assertFalse(Path(report['tools']).exists())

    def test_import_tool_failure_removes_temporary_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = (
                'from pathlib import Path\n'
                f'Path({str(root / "path.txt")!r}).write_text(str(Path(__file__).parent))\n'
                'raise RuntimeError("synthetic failure")\n'
            )
            payload = {'check_import_resources.py': source, 'import_security_corpus.py': ''}
            result = subprocess.run([sys.executable, '-c', imports.IMAGE_PROGRAM],
                                    input=json.dumps(payload), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('IMPORT_RESOURCE_REPORT=', result.stdout)
            self.assertFalse(Path((root / 'path.txt').read_text()).exists())


if __name__ == '__main__':
    unittest.main()
