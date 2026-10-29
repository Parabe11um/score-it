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
    (WORK/('jose-'+module+'-tests.jsonl')).write_text(log)
    return packages

if sys.argv[1]=='prepare':
    assert (WORK/'go.mod').read_text().startswith('module score-it-caddy-build\n')
    assert call(['go','version']).startswith('go version go1.26.10 ')
    for name in ('jose_jwk_probe.go','patches/go-jose/security_test.go'):
        assert sha(ASSETS/name)==POLICY['test_sha256'][name]
    originals={}
    for key,item in POLICY['modules'].items():
        selected=json.loads(call(['go','list','-m','-json',item['path']]))
        assert selected['Version']==item['version'] and not selected.get('Replace')
        downloaded=json.loads(call(['go','mod','download','-json',item['path']+'@'+item['version']]))
        assert downloaded['Sum']==item['sum'] and downloaded.get('Error') is None
        originals[key]=Path(downloaded['Dir'])
    base=originals['v3']
    assert {n:sha(base/n) for n in POLICY['original_sha256']}==POLICY['original_sha256']
    for label in ('original','bounds','fixed'):
        target=WORK/('jose-'+label);clone(base,target)
        if label!='original':apply(target,'rsa-bounds.patch')
        if label=='fixed':
            apply(target,'public-headers.patch')
            assert {n:sha(target/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
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
    suites={'v3':suite(fixed,'v3',POLICY['suite_packages']['v3'])}
    v4=WORK/'jose-v4';clone(originals['v4'],v4)
    suites['v4']=suite(v4,'v4',POLICY['suite_packages']['v4'])
    html=call(['go','test','-mod=mod','-count=1','golang.org/x/net/html'])
    assert 'ok' in html and 'golang.org/x/net/html' in html
    call(['go','mod','verify']);call(['go','mod','vendor'])
    vendor=WORK/'vendor'/POLICY['modules']['v3']['path']
    # Every vendored v3 file is official except the three reviewed source files.
    for p in vendor.rglob('*'):
        if p.is_file():
            name=str(p.relative_to(vendor));original=base/name
            assert original.is_file() and sha(p)==sha(original),'Unexpected vendor file: '+name
    for name in POLICY['patched_sha256']:shutil.copyfile(fixed/name,vendor/name)
    assert {n:sha(vendor/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
    evidence={'check':'caddy_jose_backport','policy':POLICY,'native_tests':{
        'go':'go1.26.10','probe_cases':16,'negative_controls':2,'suite_packages':suites,'xnet_html':True}}
    (WORK/'jose-evidence.json').write_text(json.dumps(evidence,sort_keys=True)+'\n')
    print('CADDY_JOSE_VENDOR_SOURCE_AND_NATIVE_TESTS_OK',flush=True)
elif sys.argv[1]=='finish':
    proof=json.loads((WORK/'jose-evidence.json').read_text())
    vendor=WORK/'vendor'/POLICY['modules']['v3']['path']
    assert {n:sha(vendor/n) for n in POLICY['patched_sha256']}==POLICY['patched_sha256']
    proof['binary_sha256']=sha(Path(sys.argv[3]))
    Path(sys.argv[4]).write_text(json.dumps(proof,sort_keys=True)+'\n')
else:raise SystemExit('Use prepare or finish')
