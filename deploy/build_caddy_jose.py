#!/usr/bin/env python3
"""Test a bounded v3 security backport, then bind the vendor code to Caddy."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ASSETS=Path(sys.argv[2]);WORK=Path.cwd()
POLICY=json.loads((ASSETS/'caddy_jose_sources.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def call(args,cwd=WORK,timeout=600):
    r=subprocess.run(args,cwd=cwd,capture_output=True,text=True,timeout=timeout)
    if r.returncode:raise RuntimeError('Command failed: '+str(args)+'\n'+r.stdout[-3000:]+r.stderr[-3000:])
    return r.stdout

def clone(src,dst):
    if dst.exists():
        dst.chmod(0o700)
        for path in dst.rglob('*'):
            if path.is_dir():path.chmod(0o700)
        shutil.rmtree(dst)
    shutil.copytree(src,dst,copy_function=shutil.copyfile)
    # Go's module cache directories are read-only; the isolated test copy is not.
    dst.chmod(0o700)
    for path in dst.rglob('*'):
        if path.is_dir():path.chmod(0o700)

def apply(root,name):
    p=ASSETS/'patches/go-jose'/name
    assert sha(p)==POLICY['patch_sha256'][name]
    call(['patch','--batch','--fuzz=0','-p1','-i',str(p)],cwd=root)

def suite(root,module,required):
    log=call(['go','test','-mod=mod','-count=1','-json','./...'],cwd=root)
    rows=[json.loads(line) for line in log.splitlines() if line.startswith('{')]
    assert not any(r.get('Action')=='fail' for r in rows)
    packages=sorted(r['Package'] for r in rows if r.get('Action')=='pass' and 'Test' not in r)
    assert set(required)<=set(packages),(module,packages)
    if module=='v3':
        for package, names in POLICY['required_regression_tests'].items():
            for name in names:
                assert any(r.get('Package')==package and r.get('Test')==name
                           and r.get('Action')=='pass' for r in rows),('Missing native test',package,name)
    (WORK/('jose-'+module+'-tests.jsonl')).write_text(log)
    return packages

def dos_probe(root,label,negative=False,vendor=False):
    probe=root/'.scoreit-dos-probe';probe.mkdir()
    source=(ASSETS/'jose_dos_probe.go').read_text()
    if label=='v4':
        source=source.replace('go-jose/go-jose/v3','go-jose/go-jose/v4')
        # v4 requires explicit algorithm lists; keep all probe inputs identical.
        for old,new in (
            ('return jose.ParseSigned(raw)',
             'return jose.ParseSigned(raw, []jose.SignatureAlgorithm{jose.RS256})'),
            ('return jose.ParseEncrypted(raw)',
             'return jose.ParseEncrypted(raw, []jose.KeyAlgorithm{jose.DIRECT}, []jose.ContentEncryption{jose.A128GCM})')):
            assert source.count(old)==1
            source=source.replace(old,new)
    (probe/'main.go').write_text(source)
    executable=WORK/('probe-dos-'+label)
    call(['go','build','-buildvcs=false','-mod='+('vendor' if vendor else 'mod'),
          '-o',str(executable),'./.scoreit-dos-probe'],cwd=root)
    results={}
    for mode,count in (('cbc',15),('json',34)):
        r=subprocess.run([str(executable),mode],capture_output=True,text=True,timeout=30)
        ready='JOSE_DOS_PROBE_READY mode='+mode
        assert r.stdout.splitlines() and r.stdout.splitlines()[0]==ready,('DoS probe did not start',label,mode)
        if negative:
            reason='cbc empty ciphertext panicked' if mode=='cbc' else 'json depth limit not enforced'
            assert r.returncode==42 and r.stderr.strip()=='JOSE_DOS_PROBE_REJECTED '+reason,(label,mode,r.returncode,r.stdout,r.stderr)
            assert r.stdout.splitlines()==[ready],('Negative control unexpectedly passed',label,mode)
            print('JOSE_DOS_CONTROL_REJECTED mode='+mode+' exit=42',flush=True)
        else:
            expected='JOSE_DOS_'+mode.upper()+'_RUNTIME_OK cases='+str(count)
            if mode=='json':expected+=' max_depth=10000'
            assert r.returncode==0 and r.stdout.splitlines()==[ready,expected] and not r.stderr,(label,mode,r.returncode,r.stdout,r.stderr)
            results[mode]=count
            print('JOSE_DOS_NATIVE_OK profile='+label+' mode='+mode+' cases='+str(count),flush=True)
    return results

if sys.argv[1]=='prepare':
    assert (WORK/'go.mod').read_text().startswith('module score-it-caddy-build\n')
    assert call(['go','version']).startswith('go version go1.26.10 ')
    for name,digest in POLICY['test_sha256'].items():
        assert sha(ASSETS/name)==digest
    originals={}
    for key,item in POLICY['modules'].items():
        selected=json.loads(call(['go','list','-m','-json',item['path']]))
        assert selected['Version']==item['version'] and not selected.get('Replace')
        downloaded=json.loads(call(['go','mod','download','-json',item['path']+'@'+item['version']]))
        assert downloaded['Sum']==item['sum'] and downloaded.get('Error') is None
        originals[key]=Path(downloaded['Dir'])
    base=originals['v3']
    assert {n:sha(base/n) for n in POLICY['original_sha256']}==POLICY['original_sha256']
    assert {n:sha(base/n) for n in POLICY['upstream_test_sha256']}==POLICY['upstream_test_sha256']
    for label in ('original','bounds','fixed'):
        target=WORK/('jose-'+label);clone(base,target)
        if label!='original':apply(target,'rsa-bounds.patch')
        if label=='fixed':
            apply(target,'public-headers.patch')
            assert {n:sha(target/n) for n in ('jwk.go','shared.go','jws.go')}=={
                n:POLICY['patched_sha256'][n] for n in ('jwk.go','shared.go','jws.go')}
            shutil.copyfile(ASSETS/'patches/go-jose/security_test.go',target/'scoreit_security_test.go')
        probe=target/'.scoreit-probe';probe.mkdir()
        shutil.copyfile(ASSETS/'jose_jwk_probe.go',probe/'main.go')
        executable=WORK/('probe-'+label)
        call(['go','build','-buildvcs=false','-o',str(executable),'./.scoreit-probe'],cwd=target)
        r=subprocess.run([str(executable)],capture_output=True,text=True,timeout=20)
        assert 'JOSE_PROBE_READY' in r.stdout
        if label=='fixed':
            assert r.returncode==0 and 'JOSE_BACKPORT_RUNTIME_OK cases=16' in r.stdout
        else:
            reason='RSA modulus bound' if label=='original' else 'embedded JWK before validation'
            assert r.returncode==42 and 'JOSE_PROBE_REJECTED '+reason in r.stderr
        print('JOSE_CONTROL='+label+' exit='+str(r.returncode),flush=True)
    fixed=WORK/'jose-fixed'
    # The RSA-only version must fail each separate bounded DoS regression.
    dos_probe(fixed,'rsa-only',negative=True)
    apply(fixed,'cbc-empty.patch')
    apply(fixed,'json-depth.patch')
    assert {n:sha(fixed/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
    assert {n:sha(fixed/n) for n in POLICY['patched_test_sha256']}==POLICY['patched_test_sha256']
    shutil.copyfile(ASSETS/'patches/go-jose/json_depth_test.go',fixed/'json/scoreit_depth_test.go')
    shutil.rmtree(fixed/'.scoreit-dos-probe')
    dos={'v3':dos_probe(fixed,'fixed')}
    suites={'v3':suite(fixed,'v3',POLICY['suite_packages']['v3'])}
    v4=WORK/'jose-v4';clone(originals['v4'],v4)
    dos['v4']=dos_probe(v4,'v4')
    suites['v4']=suite(v4,'v4',POLICY['suite_packages']['v4'])
    html=call(['go','test','-mod=mod','-count=1','golang.org/x/net/html'])
    assert 'ok' in html and 'golang.org/x/net/html' in html
    call(['go','mod','verify']);call(['go','mod','vendor'])
    vendor=WORK/'vendor'/POLICY['modules']['v3']['path']
    # Every vendored v3 file is official except the five reviewed source files.
    for p in vendor.rglob('*'):
        if p.is_file():
            name=str(p.relative_to(vendor));original=base/name
            assert original.is_file() and sha(p)==sha(original),'Unexpected vendor file: '+name
    for name in POLICY['patched_sha256']:shutil.copyfile(fixed/name,vendor/name)
    assert {n:sha(vendor/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
    dos['vendor']=dos_probe(WORK,'vendor',vendor=True)
    evidence={'check':'caddy_jose_backport','policy':POLICY,'native_tests':{
        'go':'go1.26.10','probe_cases':65,'negative_controls':4,'suite_packages':suites,
        'dos_probes':dos,'xnet_html':True}}
    (WORK/'jose-evidence.json').write_text(json.dumps(evidence,sort_keys=True)+'\n')
    print('CADDY_JOSE_VENDOR_SOURCE_AND_NATIVE_TESTS_OK',flush=True)
elif sys.argv[1]=='finish':
    proof=json.loads((WORK/'jose-evidence.json').read_text())
    vendor=WORK/'vendor'/POLICY['modules']['v3']['path']
    assert {n:sha(vendor/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
    proof['binary_sha256']=sha(Path(sys.argv[3]))
    Path(sys.argv[4]).write_text(json.dumps(proof,sort_keys=True)+'\n')
else:raise SystemExit('Use prepare or finish')
