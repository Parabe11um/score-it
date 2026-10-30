"""Reject obsolete gosu, deleted test keys and incompatible PostgreSQL metadata."""

import copy
import hashlib
import os
from pathlib import Path
import subprocess
import json
import tempfile
import unittest

from check_postgres_runtime import GOSU, check_archive, check_build_info, check_metadata
from runtime_policy import PG_HEALTHCHECK
from test_caddy_payload import binary, layer_bytes, saved_image
from check_postgres_openssl import FILES, PACKAGES, VERSION, check_archive as check_openssl_archive, check_runtime
from test_runtime_tools import elf_bytes


IMAGE = "score-it-postgres:payload-test"
BUILD_INFO = """/probe/gosu: go1.26.10
    path github.com/tianon/gosu
    dep github.com/moby/sys/user v0.4.1 h1:example
    dep golang.org/x/sys v0.48.0 h1:example
"""


class PostgresOpenSSLTests(unittest.TestCase):
    def files(self, version=VERSION):
        files = {'var/lib/dpkg/status': ''.join('Package: '+name+'\nVersion: '+version+
            '\nStatus: install ok installed\n\n' for name in PACKAGES).encode(),
            'usr/bin/openssl': elf_bytes(['libssl.so.3','libcrypto.so.3'])}
        for name in ('libssl.so.3','libcrypto.so.3'):
            files['usr/lib/x86_64-linux-gnu/'+name] = elf_bytes([],name)
        files['var/lib/dpkg/info/libssl3:amd64.md5sums'] = ''.join(hashlib.md5(files[name]).hexdigest()+'  '+name+'\n'
            for name in sorted(FILES) if name != 'usr/bin/openssl').encode()
        files['var/lib/dpkg/info/openssl.md5sums'] = (hashlib.md5(files['usr/bin/openssl']).hexdigest()+'  usr/bin/openssl\n'
            + '0'*32+'  usr/share/man/man1/openssl.1ssl.gz\n').encode()
        return files

    def verify(self, files, preceding=(), oci=False):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory)/'openssl.tar'
            saved_image(archive,[*preceding,layer_bytes(files,link=False)],oci=oci,image=IMAGE)
            return check_openssl_archive(archive,IMAGE)

    def test_updated_classic_and_oci_files_are_bound_to_package_inventory(self):
        files = self.files()
        for oci in (False,True):
            self.assertEqual(self.verify(files,oci=oci),
                {name:hashlib.sha256(files[name]).hexdigest() for name in FILES})

    def test_old_or_missing_package_inventory_stops(self):
        with self.assertRaisesRegex(RuntimeError,'Old or incomplete'):
            self.verify(self.files('3.0.22-1~deb12u1'))
        files = self.files(); files['var/lib/dpkg/status'] = b'Package: openssl\nVersion: '+VERSION.encode()+b'\nStatus: install ok installed\n\n'
        with self.assertRaisesRegex(RuntimeError,'Old or incomplete'):
            self.verify(files)

    def test_old_package_or_binary_in_an_earlier_layer_stops(self):
        with self.assertRaisesRegex(RuntimeError,'Old or incomplete'):
            self.verify(self.files(),preceding=[layer_bytes(self.files('3.0.22-1~deb12u1'),link=False)])
        with self.assertRaisesRegex(RuntimeError,'Overwritten'):
            self.verify(self.files(),preceding=[layer_bytes({'usr/bin/openssl':elf_bytes([])},link=False)])

    def test_renamed_old_library_and_whiteout_stop(self):
        for path,raw in (('opt/renamed.bin',elf_bytes([], 'libssl.so.3')+b'old'),
                         ('usr/lib/x86_64-linux-gnu/.wh.libssl.so.3',b'')):
            files = self.files(); files[path] = raw
            with self.subTest(path=path),self.assertRaises(RuntimeError):
                self.verify(files)

    def test_runtime_old_package_changed_binary_or_root_stop(self):
        files = self.files(); hashes = {name:hashlib.sha256(files[name]).hexdigest() for name in FILES}
        output = 'POSTGRES_OPENSSL_UID=999\n'+''.join(name+'\t'+version+'\n' for name,version in PACKAGES.items())
        output += 'OpenSSL 3.0.22 2026-06-09 (Library: OpenSSL 3.0.22 2026-06-09)\n'
        output += ''.join(value+'  /'+name+'\n' for name,value in hashes.items())
        self.assertTrue(check_runtime(output,hashes).startswith('OpenSSL 3.0.22 '))
        for before,after in ((VERSION,'3.0.22-1~deb12u1'),('UID=999','UID=0'),
                              (hashes['usr/bin/openssl'],'0'*64),('OpenSSL 3.0.22','OpenSSL 3.0.20')):
            with self.subTest(before=before),self.assertRaises(RuntimeError):
                check_runtime(output.replace(before,after),hashes)

    def test_missing_slim_documentation_is_accepted_but_changed_crypto_payload_is_rejected(self):
        files = self.files()
        self.verify(files)  # Package-listed manual intentionally absent.
        for name in FILES:
            changed = dict(files); changed[name] += b'corrupt'
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError,'package manifests'):
                self.verify(changed)
        del files['var/lib/dpkg/info/openssl.md5sums']
        with self.assertRaisesRegex(RuntimeError,'package manifests'):
            self.verify(files)


class PostgresOpenSSLUpdatePlanTests(unittest.TestCase):
    def invoke(self, extra='', wrong_version=False, mode='', package='tzdata',
               version='2026f-0+deb12u1', architecture='all', fetch=True):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            payload=root/'payload';(payload/'DEBIAN').mkdir(parents=True)
            (payload/'DEBIAN/control').write_text(
                f'Package: {package}\nVersion: {version}\nArchitecture: {architecture}\n'
                'Maintainer: Fixture <fixture@example.test>\nDescription: Synthetic tzdata package\n')
            fixture=root/'fixture.deb'
            subprocess.run(['dpkg-deb','--build','--root-owner-group',str(payload),str(fixture)],
                           check=True,capture_output=True)
            digest=hashlib.sha256(fixture.read_bytes()).hexdigest()
            (root/'plan').write_text('Inst libssl3 [old] ('+VERSION+' Debian:12-security [amd64])\n'
                'Inst openssl [old] ('+VERSION+' Debian:12-security [amd64])\n'
                'Inst tzdata [2026c-0+deb12u1] (2026f-0+deb12u1 local-deb [all])\n'+extra)
            (root/'apt-get').write_text('''#!/bin/sh
case "$1" in
 update|check) ;;
 --simulate) cat "$MOCK_ROOT/plan" ;;
 install) touch "$MOCK_ROOT/applied" ;;
 *) exit 90 ;;
esac
''')
            (root/'dpkg-query').write_text('''#!/bin/sh
case "$2" in
 *Status*) echo installed ;;
 *) case "$3" in tzdata) echo 2026f-0+deb12u1 ;; *) echo "${MOCK_VERSION}" ;; esac ;;
esac
''')
            (root/'dpkg').write_text('#!/bin/sh\nexit 0\n')
            (root/'chown').write_text('#!/bin/sh\nexit 0\n')
            (root/'apt-helper').write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
a=sys.argv[1:];url,target,pin=a[-3:]
r=pathlib.Path(os.environ['MOCK_ROOT'])
with (r/'download-calls').open('a') as f:f.write(json.dumps({'url':url,'pin':pin})+'\\n')
mode=os.environ.get('MOCK_DOWNLOAD_MODE','')
if mode=='unavailable' or mode=='fallback' and 'security.debian.org' in url:raise SystemExit(100)
raw=(r/'fixture.deb').read_bytes()
pathlib.Path(target).write_bytes(raw if mode!='corrupt' else raw+b'changed')
''')
            for name in ('apt-get','dpkg-query','dpkg','chown','apt-helper'): (root/name).chmod(0o755)
            project=Path(__file__).resolve().parents[1]
            source=(project/'deploy/update_postgres_security.sh').read_text()
            source=source.replace('. /etc/os-release','ID=debian\nVERSION_CODENAME=bookworm')
            source=source.replace('/var/lib/apt/lists/',str(root/'lists')+'/').replace('/var/lib/dpkg/status-old',str(root/'status-old'))
            # Exercise real sha256sum/dpkg-deb with a synthetic .deb. Production
            # uses the official pin and absolute APT helper, never these stubs.
            source=source.replace('3254eb870c8a949518214805aee5e4276af7699988ea8b9a3a583cd0f1eab31c',digest)
            env={**os.environ,'PATH':str(root)+':'+os.environ['PATH'],'MOCK_ROOT':str(root),
                 'MOCK_DOWNLOAD_MODE':mode,
                 'MOCK_VERSION':'3.0.22-1~deb12u1' if wrong_version else VERSION}
            package_path=fixture
            if fetch:
                fetch_source=(project/'deploy/fetch_postgres_tzdata.sh').read_text()
                fetch_source=fetch_source.replace('/usr/lib/apt/apt-helper',str(root/'apt-helper'))
                fetch_source=fetch_source.replace('3254eb870c8a949518214805aee5e4276af7699988ea8b9a3a583cd0f1eab31c',digest)
                # Trust material is a separate input of the download stage.
                ca=root/'ca.crt';ca.write_text('synthetic trust input\n')
                fetch_source=fetch_source.replace('/etc/ssl/certs/ca-certificates.crt',str(ca))
                destination=root/'downloaded'
                result=subprocess.run(['sh','-s','--',str(destination)],input=fetch_source,
                                      capture_output=True,text=True,timeout=10,env=env)
                calls=[json.loads(line) for line in (root/'download-calls').read_text().splitlines()]
                self.assertTrue(all(c['pin']=='SHA256:'+digest for c in calls))
                self.assertEqual(len(calls),2 if mode in ('unavailable','fallback') else 1)
                self.assertTrue(calls[0]['url'].startswith('https://security.debian.org/debian-security/'))
                if len(calls)==2:
                    self.assertTrue(calls[1]['url'].startswith('https://snapshot.debian.org/archive/debian-security/20261030T000000Z/'))
                if result.returncode:
                    return result,(root/'applied').exists()
                package_path=destination/'tzdata_2026f-0+deb12u1_all.deb'
                if mode=='missing_after_fetch':package_path.unlink()
                elif mode=='changed_after_fetch':package_path.write_bytes(package_path.read_bytes()+b'changed')
            result=subprocess.run(['sh','-s','--',str(package_path)],input=source,
                                  capture_output=True,text=True,timeout=10,env=env)
            return result,(root/'applied').exists()

    def test_only_the_coordinated_openssl_packages_are_allowed(self):
        result,applied=self.invoke()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(applied)
        self.assertIn('POSTGRES_OPENSSL_SECURITY_UPDATE_OK',result.stdout)

    def test_libc_icu_installation_and_any_removal_stop_before_apply(self):
        for extra in ('Inst libc6 [old] (new)\n','Conf libicu72 (new)\n',
                      'Remv unrelated [old]\n','Purg libssl3 [old]\n'):
            with self.subTest(extra=extra):
                result,applied=self.invoke(extra)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(applied)

    def test_incomplete_or_downgraded_result_is_rejected(self):
        result,_=self.invoke(wrong_version=True)
        self.assertNotEqual(result.returncode,0)
        self.assertNotIn('POSTGRES_OPENSSL_SECURITY_UPDATE_OK',result.stdout)

    def test_pinned_package_does_not_depend_on_its_live_apt_index_entry(self):
        result,applied=self.invoke(mode='fallback')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(applied)
        self.assertIn('POSTGRES_TZDATA_PACKAGE_VERIFIED version=2026f-0+deb12u1',result.stdout)

    def test_unavailable_or_corrupt_package_stops_before_apt_apply(self):
        for mode in ('unavailable','corrupt'):
            with self.subTest(mode=mode):
                result,applied=self.invoke(mode=mode)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(applied)
                self.assertNotIn('POSTGRES_OPENSSL_SECURITY_UPDATE_OK',result.stdout)

    def test_wrong_package_version_or_architecture_stops_before_apply(self):
        for fields in ({'package':'other-package'},{'version':'2026c-0+deb12u1'},{'architecture':'amd64'}):
            for fetch in (False,True):
                with self.subTest(fields=fields,fetch=fetch):
                    result,applied=self.invoke(**fields,fetch=fetch)
                    self.assertNotEqual(result.returncode,0)
                    self.assertFalse(applied)

    def test_missing_or_changed_staged_package_stops_before_transaction(self):
        for mode in ('missing_after_fetch','changed_after_fetch'):
            with self.subTest(mode=mode):
                result,applied=self.invoke(mode=mode)
                self.assertNotEqual(result.returncode,0)
                self.assertFalse(applied)


class PostgresPayloadTests(unittest.TestCase):
    def verify(self, layers, oci=False):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "postgres.tar"
            saved_image(path, layers, oci=oci, image=IMAGE)
            return check_archive(path, IMAGE)

    def test_accepts_classic_and_oci_payloads(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                self.assertEqual(self.verify([layer_bytes({GOSU: binary()}, link=False)], oci),
                                 binary())

    def test_rejects_original_gosu_under_a_new_binary(self):
        for oci in (False, True):
            with self.subTest(oci=oci):
                with self.assertRaisesRegex(RuntimeError, "Overwritten gosu"):
                    self.verify([layer_bytes({GOSU: b"original gosu"}, link=False),
                                 layer_bytes({GOSU: binary()}, link=False)], oci)

    def test_rejects_image_wide_key_even_after_deletion(self):
        with self.assertRaisesRegex(RuntimeError, "certificate/key retained"):
            self.verify([
                layer_bytes({"etc/ssl/private/ssl-cert-snakeoil.key": b"test-only"}, link=False),
                layer_bytes({"etc/ssl/private/.wh.ssl-cert-snakeoil.key": b"", GOSU: binary()},
                            link=False),
            ])

    def test_rejects_deleted_gosu_and_missing_gosu(self):
        for files, error in (({"usr/local/bin/.wh.gosu": b""}, "Deleted gosu"),
                             ({"etc/os-release": b"debian"}, "gosu is missing")):
            with self.subTest(error=error):
                with self.assertRaisesRegex(RuntimeError, error):
                    self.verify([layer_bytes(files, link=False)])

    def test_rejects_dynamic_loader_dependency(self):
        with self.assertRaisesRegex(RuntimeError, "dynamic loader"):
            self.verify([layer_bytes({GOSU: binary(interpreter=True)}, link=False)])

    def test_rejects_optional_libraries_even_after_deletion(self):
        for name in ("usr/lib/x86_64-linux-gnu/libxml2.so.2.9.14",
                     "usr/lib/x86_64-linux-gnu/libLLVM-19.so.1",
                     "usr/lib/x86_64-linux-gnu/libldap-2.5.so.0",
                     "usr/lib/x86_64-linux-gnu/liblber-2.5.so.0",
                     "usr/lib/x86_64-linux-gnu/libsasl2.so.2",
                     "usr/lib/postgresql/17/lib/pgxml.so",
                     "usr/lib/postgresql/17/lib/llvmjit.so"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "Removed PostgreSQL feature"):
                self.verify([layer_bytes({name: b"old"}, link=False),
                             layer_bytes({str(Path(name).parent / (".wh." + Path(name).name)): b"",
                                          GOSU: binary()}, link=False)])

    def test_rejects_removed_package_inventory(self):
        for package in ('libxml2', 'libldap-2.5-0', 'libsasl2-2', 'libsasl2-modules-db'):
            for oci in (False, True):
                with self.subTest(package=package, oci=oci), self.assertRaisesRegex(RuntimeError, "Removed PostgreSQL package"):
                    self.verify([layer_bytes({GOSU: binary(), "var/lib/dpkg/status":
                                             ('Package: ' + package + '\nStatus: install ok installed\n\n').encode()}, link=False)], oci)

    def test_rejects_obsolete_go_or_module_versions(self):
        check_build_info(BUILD_INFO)
        for before, after in (("go1.26.10", "go1.24.6"), ("v0.4.1", "v0.1.0"),
                              ("v0.48.0", "v0.1.0")):
            with self.subTest(before=before):
                with self.assertRaisesRegex(RuntimeError, "Unexpected gosu"):
                    check_build_info(BUILD_INFO.replace(before, after))

    def test_preserves_startup_storage_shutdown_and_environment(self):
        source = {
            "Entrypoint": ["docker-entrypoint.sh"], "Cmd": ["postgres"],
            "Env": ["PG_MAJOR=17", "PGDATA=/var/lib/postgresql/data", "LANG=en_US.utf8"],
            "StopSignal": "SIGINT", "Volumes": {"/var/lib/postgresql/data": {}},
            "ExposedPorts": {"5432/tcp": {}},
        }
        expected=copy.deepcopy(source);expected.update(User="999:999",Healthcheck=copy.deepcopy(PG_HEALTHCHECK))
        check_metadata(source, expected)
        # Docker stores the default root directory as either "" or "/".
        explicit_root = copy.deepcopy(expected)
        explicit_root["WorkingDir"] = "/"
        check_metadata(source, explicit_root)
        for key, value in (
            ("Entrypoint", ["sh"]), ("StopSignal", "SIGTERM"),
            ("WorkingDir", "/unexpected"),
            ("User", "0:0"), ("User", ""),
            ("Healthcheck", {"Test":["NONE"]}), ("Healthcheck", None),
            ("Volumes", {"/var/lib/postgresql": {}}),
            ("Env", ["PG_MAJOR=17", "PGDATA=/another/path", "LANG=en_US.utf8"]),
        ):
            with self.subTest(key=key):
                target = copy.deepcopy(expected)
                target[key] = value
                with self.assertRaises(RuntimeError):
                    check_metadata(source, target)

    def test_accepts_only_documented_server_package_revision(self):
        source = {"Env": ["PG_MAJOR=17", "PG_VERSION=17.11-1.pgdg12+2"]}
        target = {"Env": ["PG_MAJOR=17", "PG_VERSION=17.11-1.pgdg12+2+scoreit2"],"User":"999:999","Healthcheck":copy.deepcopy(PG_HEALTHCHECK)}
        check_metadata(source, target)
        with self.assertRaisesRegex(RuntimeError, "environment changed"):
            check_metadata(source, {**target,"Env":source["Env"]})


if __name__ == "__main__":
    unittest.main()
