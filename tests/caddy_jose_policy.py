"""Review exactly one source backport; do not suppress raw scanner findings."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'deploy/caddy_jose_sources.json').read_text())
TARGET='CVE-2026-116587'
MODULE='github.com/go-jose/go-jose/v3'

def require(ok,message):
    if not ok:raise RuntimeError(message)

def verify_proof(proof,binary_hash):
    require(isinstance(proof,dict),'Missing Caddy JOSE build proof')
    require(proof.get('check')=='caddy_jose_backport' and proof.get('policy')==POLICY,'Caddy JOSE source policy differs')
    require(proof.get('binary_sha256')==binary_hash,'Caddy JOSE proof belongs to another binary')
    native=proof.get('native_tests',{})
    require(native.get('go')=='go1.26.10' and native.get('probe_cases')==16
            and native.get('negative_controls')==2 and native.get('xnet_html') is True,'Incomplete JOSE native controls')
    suites=native.get('suite_packages',{})
    require(set(suites)=={'v3','v4'},'Missing JOSE native suite')
    for major,expected in POLICY['suite_packages'].items():
        require(isinstance(suites[major],list) and len(suites[major])==len(set(suites[major]))
                and set(expected)<=set(suites[major]),'Incomplete JOSE upstream suite: '+major)
    for name,digest in POLICY['patch_sha256'].items():
        require(hashlib.sha256((ROOT/'deploy/patches/go-jose'/name).read_bytes()).hexdigest()==digest,'JOSE patch changed')
    for name,digest in POLICY['test_sha256'].items():
        require(hashlib.sha256((ROOT/'deploy'/name).read_bytes()).hexdigest()==digest,'JOSE regression source changed')

def assess(report,proof,config_id,binary_hash):
    verify_proof(proof,binary_hash)
    require(report['Metadata']['ImageID']==config_id,'Caddy scan/archive identities differ')
    require(not any(r.get('Secrets') for r in report['Results']),'Caddy secret findings require review')
    values=[v for r in report['Results'] for v in r.get('Vulnerabilities') or []]
    fixed={'CVE-2026-114437','CVE-2026-78661','CVE-2026-97045'}
    require(not any(v['VulnerabilityID'] in fixed for v in values),'Fixed Caddy finding returned')
    covered=[]
    for row in values:
        if row['Severity'] not in ('HIGH','CRITICAL'):continue
        require(row['VulnerabilityID']==TARGET and row['PkgName']==MODULE
                and row['InstalledVersion']=='v3.0.5' and row['Severity']=='HIGH','Unreviewed Caddy HIGH/CRITICAL')
        covered.append({'id':TARGET,'package':MODULE,'installed_version':'v3.0.5'})
    return covered
