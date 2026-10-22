"""The real runtime gate must reject a stale library, trust store or wrong base."""
import copy
import unittest

from check_app_base import MINIMUM, check_inventory


class AppBaseTests(unittest.TestCase):
    def setUp(self):
        self.report = {'os_id': 'debian', 'codename': 'trixie', 'python': '3.12.15',
                       'uid': 10001, 'openssl_version': [3, 5, 7, 0, 15],
                       'packages': dict(MINIMUM)}

    def test_accepts_reviewed_versions_and_security_revisions(self):
        check_inventory(self.report)
        self.report['packages']['libssl3t64'] = '3.5.7-1~deb13u4'
        check_inventory(self.report)

    def test_rejects_old_or_absent_security_packages(self):
        for package, old in (('ca-certificates', '20250419'),
                             ('openssl', '3.5.7-1~deb13u2'),
                             ('libssl3t64', '3.5.6-1')):
            for version in (old, None):
                with self.subTest(package=package, version=version):
                    report = copy.deepcopy(self.report)
                    if version is None:
                        del report['packages'][package]
                    else:
                        report['packages'][package] = version
                    with self.assertRaisesRegex(RuntimeError, 'Missing app security update'):
                        check_inventory(report)

    def test_rejects_loading_an_old_library_despite_new_package_metadata(self):
        self.report['openssl_version'] = [3, 0, 22, 0, 15]
        with self.assertRaisesRegex(RuntimeError, 'Python loaded an old'):
            check_inventory(self.report)

    def test_rejects_old_base_root_and_removed_packages(self):
        cases = ({'codename': 'bookworm'}, {'os_id': 'ubuntu'},
                 {'uid': 0}, {'python': '3.13.0'},
                 {'packages': {**MINIMUM, 'libsqlite3-0': '3.46.1-7'}},
                 {'packages': {**MINIMUM, 'util-linux': '2.41.5-0+deb13u1'}},
                 {'packages': {**MINIMUM, 'liblastlog2-2': '2.41.5-0+deb13u1'}},
                 {'packages': {**MINIMUM, 'libssl3': '3.0.22-1~deb12u1'}})
        for change in cases:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                check_inventory({**self.report, **change})


if __name__ == '__main__':
    unittest.main()
