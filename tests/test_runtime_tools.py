"""Detect retired tools and privilege bits even under later deletion/replacement."""
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from check_sqlite_payload import check_archive
from test_caddy_payload import layer_bytes, saved_image


IMAGE = 'score-it:runtime-tools-test'
STATUS = {'var/lib/dpkg/status': b'Package: libc6\nStatus: install ok installed\n\n'}


class RuntimeToolsTests(unittest.TestCase):
    def verify(self, layers, oci=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'runtime.tar'
            saved_image(path, layers, oci=oci, image=IMAGE)
            check_archive(path, IMAGE)

    def test_accepts_kept_libraries_and_normal_gosu_without_privilege_bits(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.verify([layer_bytes({**STATUS, 'usr/lib/libtinfo.so.6': b'library',
                                          'usr/local/bin/gosu': b'normal executable',
                                          'usr/bin/passwd': b'normal executable'}, link=False)], oci)

    def test_rejects_tools_even_if_deleted_by_a_later_layer(self):
        for name in ('usr/bin/mount', 'bin/umount', 'usr/bin/nsenter', 'usr/bin/infocmp', 'usr/bin/tic'):
            for oci in (False, True):
                with self.subTest(name=name, oci=oci), self.assertRaisesRegex(RuntimeError, 'Retired OS command'):
                    self.verify([layer_bytes({name: b'old'}, link=False),
                                 layer_bytes({**STATUS, str(Path(name).parent / ('.wh.' + Path(name).name)): b''},
                                             link=False)], oci)

    def test_rejects_removed_package_inventory(self):
        for package in ('mount', 'ncurses-bin', 'util-linux', 'util-linux-extra'):
            with self.subTest(package=package), self.assertRaisesRegex(RuntimeError, 'Retired OS package'):
                self.verify([layer_bytes({'var/lib/dpkg/status':
                    f'Package: {package}\nStatus: install ok installed\n\n'.encode()}, link=False)])

    def test_rejects_earlier_setuid_and_setgid_even_if_overwritten(self):
        for mode in (0o4755, 0o2755, 0o6755):
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode='w') as tar:
                member = tarfile.TarInfo('usr/bin/passwd')
                member.mode = mode
                tar.addfile(member)
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'setuid/setgid'):
                self.verify([output.getvalue(), layer_bytes({**STATUS, 'usr/bin/passwd': b'normal'}, link=False)])

    def test_rejects_saved_file_capabilities(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT) as tar:
            member = tarfile.TarInfo('usr/local/bin/helper')
            member.mode = 0o755
            member.pax_headers = {'SCHILY.xattr.security.capability': 'fixture'}
            tar.addfile(member)
        with self.assertRaisesRegex(RuntimeError, 'File capabilities'):
            self.verify([layer_bytes(STATUS, link=False), output.getvalue()])

    def test_does_not_confuse_setgid_directories_or_sticky_tmp_with_executables(self):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as tar:
            for name, mode in [('tmp', 0o1777), ('var/mail', 0o2775)]:
                member = tarfile.TarInfo(name)
                member.type = tarfile.DIRTYPE
                member.mode = mode
                tar.addfile(member)
        self.verify([layer_bytes(STATUS, link=False), output.getvalue()])


if __name__ == '__main__':
    unittest.main()
