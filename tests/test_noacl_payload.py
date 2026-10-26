"""Reject old ACL callers, missing native proof and saved/runtime substitution."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from check_noacl_runtime import FILES, PREFIX, VERSIONS, check_archive, parse_runtime
from test_caddy_payload import layer_bytes, saved_image
from test_runtime_tools import elf_bytes

IMAGE = 'score-it:noacl-test'


def fixture(profile='app'):
    distro, packages = VERSIONS[profile]
    files = {'var/lib/dpkg/status': ''.join(
        f'Package: {name}\nStatus: install ok installed\nVersion: {version}\n\n'
        for name,version in packages.items()).encode()}
    hashes = {}
    for i,name in enumerate(sorted(FILES)):
        raw = elf_bytes(['libc.so.6']) + bytes([i])
        files['usr/bin/'+name] = raw
        hashes[name] = hashlib.sha256(raw).hexdigest()
    pins = (Path(__file__).resolve().parents[1]/'deploy/acl_tools_sources.sha256').read_text().splitlines()
    source = ['distribution='+distro,'configure=--disable-acl']
    source += [k+'='+v for k,v in packages.items()]
    source += [row for row in pins if row.split()[1].startswith(distro+'/')]
    tests = []
    for package,version in packages.items():
        tests += [f'NOACL_PACKAGE_ELF_OK package={package} elf_files=1',
                  f'NOACL_PACKAGE_BUILD_OK package={package} version={version} uid=998']
    tests += ['NOACL_POSIX_TOOLS_OK uid=998']
    files[PREFIX+'acl-tools-source.txt'] = ('\n'.join(source)+'\n').encode()
    files[PREFIX+'acl-tools-tests.txt'] = ('\n'.join(tests)+'\n').encode()
    files[PREFIX+'acl-tools-binaries.sha256'] = ''.join(d+'  /usr/bin/'+n+'\n' for n,d in sorted(hashes.items())).encode()
    return files, {'binary_sha256':hashes}


class NoACLArchiveTests(unittest.TestCase):
    def verify(self, files, profile='app', oci=False, earlier=None):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'image.tar'
            saved_image(path, ([layer_bytes(earlier,link=False)] if earlier else []) + [layer_bytes(files,link=False)],
                        image=IMAGE,oci=oci)
            return check_archive(path,IMAGE,profile)

    def test_accepts_each_profile_in_classic_and_oci_archives(self):
        for profile in VERSIONS:
            for oci in (False,True):
                with self.subTest(profile=profile,oci=oci):
                    files,expected=fixture(profile)
                    result=self.verify(files,profile,oci)
                    self.assertEqual(result['binary_sha256'],expected['binary_sha256'])
                    self.assertEqual(result['elf_files'],5)

    def test_rejects_an_old_library_even_if_a_later_layer_is_clean(self):
        for name in ('usr/lib/libacl.so.1','opt/old/libacl.a','usr/lib/.wh.libacl.so.1'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError,'Retired runtime library'):
                self.verify(fixture()[0],earlier={name:b'old'})

    def test_rejects_renamed_library_and_unresolved_acl_import(self):
        for raw in (elf_bytes([],soname='libacl.so.1'),
                    elf_bytes(['libc.so.6'],undefined=['acl_get_file'])):
            files,_=fixture();files['opt/renamed']=raw
            with self.assertRaises(RuntimeError):self.verify(files)

    def test_rejects_original_or_incomplete_package_versions(self):
        for replacement in (b'9.7-3',b'9.7-3+wrong'):
            files,_=fixture()
            files['var/lib/dpkg/status']=files['var/lib/dpkg/status'].replace(b'9.7-3+scoreitnoacl1',replacement)
            with self.assertRaisesRegex(RuntimeError,'tool packages'):self.verify(files)
        files,_=fixture();files['var/lib/dpkg/status']=b'Package: libc6\nStatus: install ok installed\n\n'
        with self.assertRaisesRegex(RuntimeError,'tool packages'):self.verify(files)

    def test_rejects_retained_acl_or_app_account_package(self):
        for package in ('acl','libacl1','libacl1-dev','passwd'):
            files,_=fixture()
            files['var/lib/dpkg/status']+=f'Package: {package}\nStatus: install ok installed\nVersion: 1\n\n'.encode()
            with self.subTest(package=package), self.assertRaises(RuntimeError):self.verify(files)

    def test_rejects_replaced_or_deleted_old_command(self):
        files,_=fixture()
        for path in ('usr/bin/cp','usr/bin/.wh.cp'):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.verify(files,earlier={path:elf_bytes(['libc.so.6'])})

    def test_rejects_changed_command_hash(self):
        files,_=fixture();files['usr/bin/sed']+=b'changed'
        with self.assertRaisesRegex(RuntimeError,'hashes differ'):self.verify(files)

    def test_rejects_missing_native_test_evidence(self):
        for prefix in ('NOACL_PACKAGE_ELF_OK','NOACL_PACKAGE_BUILD_OK','NOACL_POSIX_TOOLS_OK'):
            files,_=fixture()
            files[PREFIX+'acl-tools-tests.txt']=b'\n'.join(
                row for row in files[PREFIX+'acl-tools-tests.txt'].splitlines() if not row.startswith(prefix.encode()))+b'\n'
            with self.subTest(prefix=prefix), self.assertRaises(RuntimeError):self.verify(files)

    def test_rejects_wrong_source_hash_or_distribution(self):
        for before,after in ((b'distribution=trixie',b'distribution=bookworm'),
                             (b'configure=--disable-acl',b'configure=--enable-acl'),
                             (b'c3a207e3',b'00000000')):
            files,_=fixture();files[PREFIX+'acl-tools-source.txt']=files[PREFIX+'acl-tools-source.txt'].replace(before,after)
            with self.subTest(before=before), self.assertRaises(RuntimeError):self.verify(files)

    def test_rejects_duplicate_package_records_and_old_inventory(self):
        files,_=fixture()
        files['var/lib/dpkg/status']+=files['var/lib/dpkg/status'].split(b'\n\n')[0]+b'\n\n'
        with self.assertRaisesRegex(RuntimeError,'Duplicate'):self.verify(files)
        files,_=fixture();files['var/lib/dpkg/status-old']=b'old'
        with self.assertRaisesRegex(RuntimeError,'Old no-ACL'):self.verify(files)


class NoACLRuntimeTests(unittest.TestCase):
    def output(self,profile):
        _,evidence=fixture(profile);uid=10001 if profile=='app' else 999
        text=f'NOACL_POSIX_TOOLS_OK uid={uid}\n'
        text+=''.join(p+'\t'+v+'\n' for p,v in VERSIONS[profile][1].items())
        text+=''.join(h+'  /usr/bin/'+n+'\n' for n,h in evidence['binary_sha256'].items())
        return text,evidence

    def test_accepts_each_unprivileged_runtime(self):
        for profile,uid in (('app',10001),('postgres',999)):
            output,evidence=self.output(profile)
            self.assertEqual(parse_runtime(output,profile,evidence),uid)

    def test_rejects_root_and_substituted_packages_or_binaries(self):
        output,evidence=self.output('app')
        for bad in (output.replace('uid=10001','uid=0'),
                    output.replace('+scoreitnoacl1',''),
                    output.replace(evidence['binary_sha256']['cp'],'0'*64)):
            with self.assertRaises(RuntimeError):parse_runtime(bad,'app',evidence)

    def test_rejects_missing_roundtrip_and_duplicate_hashes(self):
        output,evidence=self.output('postgres')
        for bad in ('\n'.join(output.splitlines()[1:]),output+output.splitlines()[-1]+'\n'):
            with self.assertRaises(RuntimeError):parse_runtime(bad,'postgres',evidence)


if __name__=='__main__':unittest.main()
