import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('browser_security', Path(__file__).with_name('check_browser_security.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BrowserStageGuardTests(unittest.TestCase):
    def setUp(self):
        self.args = SimpleNamespace(project='score-it-check-guard', release='a' * 40, database='score_it_container_ci')
        self.items = []
        for service, image in [('web', 'score-it:'), ('db', 'score-it-postgres:17-')]:
            self.items.append({'Name': '/test-' + service,
                               'Config': {'Image': image + self.args.release, 'Labels': {
                                   'com.docker.compose.project': self.args.project,
                                   'com.docker.compose.service': service}},
                               'State': {'Health': {'Status': 'healthy'}},
                               'HostConfig': {'PortBindings': {}},
                               'NetworkSettings': {'Networks': {'private-backend': {}}},
                               'Mounts': []})
        self.items[1]['Mounts'] = [{'Destination': '/var/lib/postgresql/data', 'Type': 'volume', 'Name': 'test-volume'}]
        self.network = {'Internal': True, 'Labels': {'com.docker.compose.project': self.args.project}}
        self.volume = {'Labels': {'com.docker.compose.project': self.args.project}}
        self.mock = patch.object(module, 'docker', side_effect=self.docker).start()
        self.addCleanup(patch.stopall)

    def docker(self, *args):
        if args[0] == 'ps':
            return 'web-id\ndb-id\n'
        if args[0] == 'inspect':
            return json.dumps(self.items)
        if args[:2] == ('network', 'inspect'):
            return json.dumps([self.network])
        if args[:2] == ('volume', 'inspect'):
            return json.dumps([self.volume])
        raise AssertionError('Guard must not write or create resources')

    def test_private_test_project_is_accepted(self):
        self.assertEqual(set(module.preflight(self.args)), {'web', 'db'})

    def test_production_project_is_rejected_before_docker(self):
        self.args.project = 'score-it'
        with self.assertRaisesRegex(RuntimeError, 'isolated'):
            module.preflight(self.args)
        self.mock.assert_not_called()

    def test_production_database_name_is_rejected_before_docker(self):
        self.args.database = 'score_it_production'
        with self.assertRaisesRegex(RuntimeError, 'test database'):
            module.preflight(self.args)
        self.mock.assert_not_called()

    def test_published_app_or_database_port_is_rejected(self):
        self.items[0]['HostConfig']['PortBindings'] = {'8000/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8000'}]}
        with self.assertRaisesRegex(RuntimeError, 'published ports'):
            module.preflight(self.args)

    def test_external_network_is_rejected(self):
        self.network['Internal'] = False
        with self.assertRaisesRegex(RuntimeError, 'non-private'):
            module.preflight(self.args)

    def test_production_volume_is_rejected(self):
        self.volume['Labels']['com.docker.compose.project'] = 'score-it'
        with self.assertRaisesRegex(RuntimeError, 'volume does not belong'):
            module.preflight(self.args)

    def test_unexpected_release_is_rejected(self):
        self.items[0]['Config']['Image'] = 'score-it:other'
        with self.assertRaisesRegex(RuntimeError, 'image differs'):
            module.preflight(self.args)

    def test_duplicate_web_is_rejected(self):
        self.items.append(copy.deepcopy(self.items[0]))
        with self.assertRaisesRegex(RuntimeError, 'one staging web'):
            module.preflight(self.args)


if __name__ == '__main__':
    unittest.main()
