"""Exercise the CI runtime guard itself; no Docker or network is used."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT=Path(__file__).resolve().parents[1]


class SecurityStageTests(unittest.TestCase):
    def invoke_guard(self, mode=''):
        workflow=(ROOT/'.github/workflows/tests.yml').read_text()
        command=workflow.split('--entrypoint sh "$IMAGE" -ec ',1)[1].split("' | tee",1)[0]
        self.assertTrue(command.startswith("'"))
        command=command[1:]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            query=root/'dpkg-query'
            query.write_text('''#!/usr/bin/env python3
import os,sys
a=sys.argv[1:]
if len(a)!=3 or a[0]!='-W':raise SystemExit(90)
fmt,package=a[1:]
if package=='ca-certificates':
    if fmt!='-f=${db:Status-Status}':raise SystemExit(91)
    print('installed' if os.environ.get('GUARD_MODE')=='ca-package' else 'not-installed')
else:
    if fmt!='-f=${Version}':raise SystemExit(92)
    print('old' if os.environ.get('GUARD_MODE')==package else
          '2026e-0+deb12u1' if package=='tzdata' else '3.0.22-1~deb12u2')
''')
            query.chmod(0o755)
            ca=root/'ca.crt';out=root/'out'
            command=command.replace('/etc/ssl/certs/ca-certificates.crt',str(ca))
            command=command.replace('test ! -e /out','test ! -e '+str(out))
            if mode=='ca-file':ca.write_text('unexpected copied roots')
            if mode=='out':out.mkdir()
            env={k:v for k,v in os.environ.items() if k not in ('OPENSSL_CONF','GODEBUG')}
            env.update(PATH=str(root)+':'+env['PATH'],GUARD_MODE=mode)
            if mode=='tls-env':env['OPENSSL_CONF']='unexpected-runtime-setting'
            return subprocess.run(['sh','-ec',command],env=env,text=True,capture_output=True,timeout=5)

    def test_runtime_guard_executes_literal_dpkg_formats_and_emits_success(self):
        result=self.invoke_guard()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(result.stdout,'POSTGRES_SECURITY_STAGE_RUNTIME_OK\n')

    def test_wrong_packages_copied_roots_or_tls_settings_cannot_report_success(self):
        for mode in ('tzdata','libssl3','openssl','ca-package','ca-file','out','tls-env'):
            with self.subTest(mode=mode):
                result=self.invoke_guard(mode)
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertNotIn('POSTGRES_SECURITY_STAGE_RUNTIME_OK',result.stdout)

    def test_missing_download_trust_stops_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);helper=root/'helper';called=root/'called'
            helper.write_text('#!/bin/sh\ntouch '+str(called)+'\nexit 99\n');helper.chmod(0o755)
            source=(ROOT/'deploy/fetch_postgres_tzdata.sh').read_text()
            source=source.replace('/etc/ssl/certs/ca-certificates.crt',str(root/'missing.crt'))
            source=source.replace('/usr/lib/apt/apt-helper',str(helper))
            result=subprocess.run(['sh','-s','--',str(root/'out')],input=source,
                                  text=True,capture_output=True,timeout=5)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(called.exists())


if __name__=='__main__':unittest.main()
