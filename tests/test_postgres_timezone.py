"""Prove stale/lower-layer zone data cannot satisfy the pinned update gate."""
from pathlib import Path
import hashlib
import io
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import check_postgres_timezone as checker
from test_caddy_payload import layer_bytes,saved_image


def linked_layer(files,links):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as t:
        for name,raw in files.items():
            m=tarfile.TarInfo(name);m.size=len(raw);t.addfile(m,io.BytesIO(raw))
        for name,target,kind in links:
            m=tarfile.TarInfo(name);m.type=kind;m.linkname=target;t.addfile(m)
    return out.getvalue()


class TimezoneTests(unittest.TestCase):
    def verify(self,mutation=None,prior=(),oci=False):
        data={name:(b'# version 2026f\n' if name.endswith('tzdata.zi') else b'TZif'+name.encode()) for name in checker.FILES}
        pins={n:hashlib.sha256(v).hexdigest() for n,v in data.items()}
        data['var/lib/dpkg/status']=('Package: tzdata\nVersion: '+checker.VERSION+'\nStatus: install ok installed\n\n').encode()
        if mutation:mutation(data)
        with tempfile.TemporaryDirectory() as td,patch.object(checker,'FILES',pins):
            p=Path(td)/'image.tar';saved_image(p,[*prior,layer_bytes(data,link=False)],oci=oci,image='score-it-postgres:tz')
            return checker.check_archive(p,'score-it-postgres:tz')

    def test_classic_and_oci_verified(self):
        for oci in (False,True):self.assertEqual(len(self.verify(oci=oci)),7)

    def test_old_package_or_old_data_in_a_lower_layer_stops(self):
        for prior in ({'var/lib/dpkg/status':b'Package: tzdata\nVersion: 2026c-0+deb12u1\nStatus: install ok installed\n\n'},
                      {'usr/share/zoneinfo/America/Winnipeg':b'old'},
                      {'usr/share/zoneinfo/America/Havana':b'old'},
                      {'usr/share/zoneinfo/Cuba':b'old'},
                      {'usr/share/zoneinfo/America/.wh.Winnipeg':b''}):
            with self.assertRaises(RuntimeError):self.verify(prior=[layer_bytes(prior,link=False)])

    def test_missing_changed_version_or_file_stops(self):
        for name in checker.FILES:
            with self.subTest(name=name),self.assertRaises(RuntimeError):self.verify(lambda d:d.pop(name))
            with self.subTest(changed=name),self.assertRaises(RuntimeError):self.verify(lambda d:d.__setitem__(name,b'changed'))
        with self.assertRaises(RuntimeError):self.verify(lambda d:d.__setitem__('var/lib/dpkg/status',b''))

    def test_relative_symlinks_and_hardlinks_bind_to_exact_payload(self):
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):
            for oci in (False,True):
                self.linked(kind,oci=oci)

    def linked(self,kind,oci=False,target=None,mode=None):
        files={n:(b'# version 2026f\n' if n.endswith('tzdata.zi') else b'TZif'+n.encode()) for n in checker.FILES}
        pins={n:hashlib.sha256(raw).hexdigest() for n,raw in files.items()}
        name='usr/share/zoneinfo/America/Yellowknife';alias='usr/share/zoneinfo/America/Edmonton'
        files[alias]=files.pop(name)
        if mode=='changed':files[alias]=b'old zone data'
        if mode=='missing':files.pop(alias)
        dest=target or ('Edmonton' if kind==tarfile.SYMTYPE else alias)
        links=[(name,dest,kind)]
        if mode=='cycle':
            files.pop(alias);links.append((alias,'Yellowknife' if kind==tarfile.SYMTYPE else name,kind))
        files['var/lib/dpkg/status']=('Package: tzdata\nVersion: '+checker.VERSION+'\nStatus: install ok installed\n\n').encode()
        with tempfile.TemporaryDirectory() as td,patch.object(checker,'FILES',pins):
            p=Path(td)/'image.tar';saved_image(p,[linked_layer(files,links)],oci=oci,image='score-it-postgres:tz')
            self.assertEqual(checker.check_archive(p,'score-it-postgres:tz'),pins)

    def test_bad_missing_cyclic_or_escaping_alias_stops(self):
        for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):
            for mode in ('changed','missing','cycle'):
                with self.subTest(kind=kind,mode=mode),self.assertRaises(RuntimeError):self.linked(kind,mode=mode)
            for target in ('/etc/passwd','../../../../etc/passwd'):
                with self.subTest(kind=kind,target=target),self.assertRaises(RuntimeError):self.linked(kind,target=target)

    def test_cuba_root_alias_resolves_to_the_pinned_havana_payload(self):
        for oci in (False,True):
            for kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):
                files={name:(b'# version 2026f\n' if name.endswith('tzdata.zi') else b'TZif'+name.encode())
                       for name in checker.FILES}
                cuba='usr/share/zoneinfo/Cuba';havana='usr/share/zoneinfo/America/Havana'
                files[cuba]=files[havana]
                pins={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
                del files[cuba]
                files['var/lib/dpkg/status']=('Package: tzdata\nVersion: '+checker.VERSION+
                    '\nStatus: install ok installed\n\n').encode()
                target='America/Havana' if kind==tarfile.SYMTYPE else havana
                with tempfile.TemporaryDirectory() as td,patch.object(checker,'FILES',pins):
                    archive=Path(td)/'image.tar'
                    saved_image(archive,[linked_layer(files,[(cuba,target,kind)])],oci=oci,image='score-it-postgres:tz')
                    self.assertEqual(checker.check_archive(archive,'score-it-postgres:tz'),pins)


if __name__=='__main__':unittest.main()
