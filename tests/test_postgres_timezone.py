"""Prove stale/lower-layer zone data cannot satisfy the pinned update gate."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import check_postgres_timezone as checker
from test_caddy_payload import layer_bytes,saved_image


class TimezoneTests(unittest.TestCase):
    def verify(self,mutation=None,prior=(),oci=False):
        data={name:(b'# version 2026e\n' if name.endswith('tzdata.zi') else b'TZif'+name.encode()) for name in checker.FILES}
        pins={n:hashlib.sha256(v).hexdigest() for n,v in data.items()}
        data['var/lib/dpkg/status']=('Package: tzdata\nVersion: '+checker.VERSION+'\nStatus: install ok installed\n\n').encode()
        if mutation:mutation(data)
        with tempfile.TemporaryDirectory() as td,patch.object(checker,'FILES',pins):
            p=Path(td)/'image.tar';saved_image(p,[*prior,layer_bytes(data,link=False)],oci=oci,image='score-it-postgres:tz')
            return checker.check_archive(p,'score-it-postgres:tz')

    def test_classic_and_oci_verified(self):
        for oci in (False,True):self.assertEqual(len(self.verify(oci=oci)),5)

    def test_old_package_or_old_data_in_a_lower_layer_stops(self):
        for prior in ({'var/lib/dpkg/status':b'Package: tzdata\nVersion: 2026c-0+deb12u1\nStatus: install ok installed\n\n'},
                      {'usr/share/zoneinfo/America/Winnipeg':b'old'},
                      {'usr/share/zoneinfo/America/.wh.Winnipeg':b''}):
            with self.assertRaises(RuntimeError):self.verify(prior=[layer_bytes(prior,link=False)])

    def test_missing_changed_version_or_file_stops(self):
        for name in checker.FILES:
            with self.subTest(name=name),self.assertRaises(RuntimeError):self.verify(lambda d:d.pop(name))
            with self.subTest(changed=name),self.assertRaises(RuntimeError):self.verify(lambda d:d.__setitem__(name,b'changed'))
        with self.assertRaises(RuntimeError):self.verify(lambda d:d.__setitem__('var/lib/dpkg/status',b''))


if __name__=='__main__':unittest.main()
