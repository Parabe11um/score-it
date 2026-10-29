"""A source backport must never turn the Caddy gate into a general allowlist."""
import copy
import hashlib
import json
import unittest

from caddy_jose_policy import assess,verify_proof,MODULE,TARGET
from test_caddy_payload import payload


class CaddyJOSETests(unittest.TestCase):
    def setUp(self):
        files=payload();self.proof=json.loads(files['usr/share/caddy/jose-backport.json'])
        self.digest=hashlib.sha256(files['usr/bin/caddy']).hexdigest()
        self.identity='sha256:'+'a'*64
        self.row={'VulnerabilityID':TARGET,'PkgName':MODULE,'InstalledVersion':'v3.0.5','Severity':'HIGH'}
        self.report={'Metadata':{'ImageID':self.identity},'Results':[{'Vulnerabilities':[self.row]}]}

    def check(self):return assess(self.report,self.proof,self.identity,self.digest)

    def test_only_exact_v3_high_backport_is_reviewed_and_raw_report_stays_intact(self):
        raw=json.dumps(self.report);result=self.check()
        self.assertEqual(result,[{'id':TARGET,'package':MODULE,'installed_version':'v3.0.5'}])
        self.assertEqual(json.dumps(self.report),raw)

    def test_v4_critical_wrong_version_and_other_highs_stop(self):
        for key,value in [('PkgName',MODULE.replace('/v3','/v4')),('InstalledVersion','v3.0.4'),
                          ('Severity','CRITICAL'),('VulnerabilityID','CVE-other')]:
            with self.subTest(key=key),self.assertRaises(RuntimeError):
                old=self.row.copy();self.row[key]=value
                try:self.check()
                finally:self.row.clear();self.row.update(old)
        self.report['Results'][0]['Vulnerabilities'].append({**self.row,'VulnerabilityID':'CVE-other'})
        with self.assertRaises(RuntimeError):self.check()

    def test_missing_partial_or_changed_source_controls_stop(self):
        for change in (lambda p:p['native_tests'].__setitem__('negative_controls',1),
                       lambda p:p['native_tests'].__setitem__('probe_cases',15),
                       lambda p:p['native_tests']['suite_packages'].__setitem__('v3',[]),
                       lambda p:p['policy'].__setitem__('upstream_fix','wrong'),
                       lambda p:p.__setitem__('binary_sha256','b'*64)):
            proof=copy.deepcopy(self.proof);change(proof)
            with self.assertRaises(RuntimeError):verify_proof(proof,self.digest)
        with self.assertRaises(RuntimeError):verify_proof(None,self.digest)

    def test_other_scan_image_and_reintroduced_fixed_findings_stop(self):
        self.report['Metadata']['ImageID']='sha256:'+'b'*64
        with self.assertRaises(RuntimeError):self.check()
        self.report['Metadata']['ImageID']=self.identity
        for cve in ('CVE-2026-78661','CVE-2026-97045','CVE-2026-114437'):
            with self.subTest(cve=cve),self.assertRaises(RuntimeError):
                self.row['VulnerabilityID']=cve;self.row['Severity']='UNKNOWN';self.check()

    def test_secret_findings_cannot_pass_the_proxy_gate(self):
        self.report['Results'][0]['Secrets']=[{'RuleID':'synthetic-control'}]
        with self.assertRaisesRegex(RuntimeError,'secret findings'):self.check()


if __name__=='__main__':unittest.main()
