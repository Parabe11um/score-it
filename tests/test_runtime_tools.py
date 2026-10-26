"""Detect retired tools and privilege bits even under later deletion/replacement."""
import io
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest

from check_sqlite_payload import check_archive
from test_caddy_payload import layer_bytes, saved_image
from check_runtime_tools import check_runtime_binary


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
        for package in ('mount', 'ncurses-bin', 'util-linux', 'util-linux-extra',
                        'libmount1', 'libblkid1', 'e2fsprogs', 'acl', 'libacl1', 'libacl1-dev'):
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


    def test_rejects_mount_libraries_even_in_deleted_layers_and_alternate_paths(self):
        for name in ('usr/lib/x86_64-linux-gnu/libmount.so.1.1.0', 'lib/libblkid.so.1',
                     'opt/old/libmount.so.1.dpkg-old', 'usr/lib/libblkid.a',
                     'usr/lib/libacl.so.1', 'opt/old/libacl.so.1.dpkg-old', 'usr/lib/libacl.a'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Retired runtime library'):
                self.verify([layer_bytes({name: b'old'}, link=False),
                             layer_bytes({**STATUS, str(Path(name).parent / ('.wh.' + Path(name).name)): b''}, link=False)])

    def test_rejects_filesystem_tools_and_symlink_aliases(self):
        for name in ('usr/sbin/debugfs', 'sbin/e2fsck', 'usr/sbin/fsck.ext4', 'sbin/mkfs.ext3'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Retired OS command'):
                self.verify([layer_bytes({**STATUS, name: b'old'}, link=False)])

    def test_rejects_indirect_or_renamed_mount_library_binary(self):
        for needed, soname in ((['libblkid.so.1'], None), ([], 'libmount.so.1'),
                               (['libacl.so.1'], None), ([], 'libacl.so.1')):
            with self.subTest(needed=needed, soname=soname), self.assertRaisesRegex(RuntimeError, 'ELF library or dependency'):
                self.verify([layer_bytes({**STATUS, 'opt/renamed.bin': elf_bytes(needed, soname)}, link=False)])

    def test_accepts_ssl_and_libc_imports_without_reclassifying_their_findings(self):
        raw = elf_bytes(['libssl.so.3', 'libc.so.6'])
        self.assertEqual(check_runtime_binary(raw)['needed'], ['libc.so.6', 'libssl.so.3'])
        self.verify([layer_bytes({**STATUS, 'usr/bin/cp': raw}, link=False)])

    def test_rejects_acl_api_import_even_without_a_named_library_dependency(self):
        with self.assertRaisesRegex(RuntimeError, 'ACL API import'):
            self.verify([layer_bytes({**STATUS, 'opt/renamed': elf_bytes(['libc.so.6'], undefined=['acl_set_file'])}, link=False)])

    def test_rejects_truncated_or_sectionless_dynamic_elf_instead_of_assuming_no_imports(self):
        raw = bytearray(elf_bytes(['libc.so.6']))
        struct.pack_into('<Q', raw, 40, 0)
        struct.pack_into('<H', raw, 60, 0)
        for value in (bytes(raw), b'\x7fELF', elf_bytes(['libc.so.6'])[:-20]):
            with self.subTest(length=len(value)), self.assertRaises(RuntimeError):
                self.verify([layer_bytes({**STATUS, 'usr/bin/unknown': value}, link=False)])


def elf_bytes(needed, soname=None, undefined=()):
    strings, tags = b'\0', []
    for tag, value in [(1, name) for name in needed] + ([(14, soname)] if soname else []):
        tags.append((tag, len(strings)))
        strings += value.encode() + b'\0'
    symbols = b'\0' * 24
    for name in undefined:
        symbols += struct.pack('<IBBHQQ', len(strings), 0x12, 0, 0, 0, 0)
        strings += name.encode() + b'\0'
    dynamic = b''.join(struct.pack('<qQ', *row) for row in tags + [(0, 0)])
    string_offset = 64 + 56
    dynamic_offset = string_offset + len(strings)
    symbol_offset = dynamic_offset + len(dynamic)
    section_offset = symbol_offset + len(symbols)
    header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
        3, 62, 1, 0, 64, section_offset, 0, 64, 56, 1, 64, 4, 0)
    program = struct.pack('<IIQQQQQQ', 2, 4, dynamic_offset, 0, 0, len(dynamic), len(dynamic), 8)
    sections = b'\0' * 64
    sections += struct.pack('<IIQQQQIIQQ', 0, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0)
    sections += struct.pack('<IIQQQQIIQQ', 0, 6, 0, 0, dynamic_offset, len(dynamic), 1, 0, 8, 16)
    sections += struct.pack('<IIQQQQIIQQ', 0, 11, 0, 0, symbol_offset, len(symbols), 1, 0, 8, 24)
    return header + program + strings + dynamic + symbols + sections


class PurgePlanGuards(unittest.TestCase):
    def invoke(self, profile, extra='', missing=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = ['mount', 'ncurses-bin', 'libmount1', 'libblkid1', 'passwd', 'libacl1'] if profile == 'app' else [
                'mount', 'ncurses-bin', 'util-linux', 'util-linux-extra', 'e2fsprogs', 'libmount1', 'libblkid1', 'libacl1']
            plan = ''.join('Purg ' + package + ' [fixture]\n' for package in selected if package != missing) + extra
            (root / 'plan').write_text(plan)
            (root / 'apt-get').write_text('''#!/bin/sh
case "$1" in
 --simulate) cat "$MOCK_ROOT/plan" ;;
 -y) touch "$MOCK_ROOT/applied" ;;
 check) ;;
 *) exit 95 ;;
esac
''')
            for name in ('dpkg-query', 'dpkg', 'find'):
                (root / name).write_text('#!/bin/sh\nexit 0\n')
            for name in ('apt-get', 'dpkg-query', 'dpkg', 'find'):
                (root / name).chmod(0o755)
            source = (Path(__file__).resolve().parents[1] / 'deploy/prune_runtime_tools.sh').read_text()
            source = source.replace('. /etc/os-release', 'ID=debian\nVERSION_CODENAME=' + ('trixie' if profile == 'app' else 'bookworm'))
            source = source.replace('/var/lib/dpkg/status-old', str(root / 'status-old'))
            result = subprocess.run(['sh', '-s', profile], input=source, capture_output=True, text=True,
                env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'], 'MOCK_ROOT': str(root)}, timeout=10)
            return result, (root / 'applied').exists()

    def test_exact_profiles_allow_only_reviewed_removals(self):
        for profile in ('app', 'postgres'):
            result, applied = self.invoke(profile)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(applied)

    def test_unrelated_removal_or_installation_stops_before_purge(self):
        for extra in ('Purg coreutils [fixture]\n', 'Inst new-package [fixture]\n', 'Conf openssl [fixture]\n'):
            result, applied = self.invoke('postgres', extra=extra)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(applied)

    def test_missing_required_library_or_filesystem_package_stops_before_purge(self):
        for package in ('libmount1', 'libblkid1', 'e2fsprogs', 'libacl1'):
            result, applied = self.invoke('postgres', missing=package)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(applied)


if __name__ == '__main__':
    unittest.main()
