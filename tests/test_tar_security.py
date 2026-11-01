"""Reject incomplete native tar proof and changed source/probe identities."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from check_noacl_runtime import TAR_MANIFEST, TAR_PROBE
from tar_security import CVES, check_evidence, unprivileged, verify_tree
from test_noacl_payload import tar_evidence


class TarEvidenceGuards(unittest.TestCase):
    def verify(self, value, profile='trixie', binary='a'*64):
        return check_evidence(value,TAR_MANIFEST,profile,binary,hashlib.sha256(TAR_PROBE.encode()).hexdigest())

    def test_accepts_bound_proof_for_both_profiles(self):
        for image,distro in [('app','trixie'),('postgres','bookworm')]:
            self.assertEqual(self.verify(tar_evidence(image,'a'*64),distro)['cves'],list(CVES))

    def test_rejects_reused_root_or_unknown_native_identity(self):
        for key,value in [('uid',0),('uid',True),('profile','bookworm'),('state','NOT_RUN'),
                          ('binary_sha256','b'*64),('package_version','1.35+dfsg-3.1+~scoreitnoacl1')]:
            proof=tar_evidence('app','a'*64);proof[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(RuntimeError):self.verify(proof)

    def test_rejects_each_missing_control_or_a_nonvulnerable_control_result(self):
        for cve in CVES:
            for mutation in ('missing','exit','reason'):
                proof=tar_evidence('app','a'*64);cases=proof['negative_controls']['cases']
                if mutation=='missing':del cases[cve]
                elif mutation=='exit':cases[cve]['exit']=0
                else:cases[cve]['reason']='unrelated_failure'
                with self.subTest(cve=cve,mutation=mutation),self.assertRaises(RuntimeError):self.verify(proof)

    def test_rejects_changed_source_patch_probe_or_control_binding(self):
        for key in ('manifest_sha256','patch_sha256','probe_sha256'):
            proof=tar_evidence('app','a'*64);proof[key]='0'*64
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.verify(proof)
        for key in ('manifest_sha256','probe_sha256','uid','source_version','binary_sha256'):
            proof=tar_evidence('app','a'*64)
            proof['negative_controls'][key]=0 if key=='uid' else 'a'*64
            with self.subTest(control=key),self.assertRaises(RuntimeError):self.verify(proof)

    def test_rejects_partial_compiled_source_or_probe_output(self):
        for name in ('source_after','stable_source_verified'):
            proof=tar_evidence('app','a'*64)
            proof[name] = {} if name=='source_after' else []
            with self.subTest(name=name),self.assertRaises(RuntimeError):self.verify(proof)
        for cve in CVES:
            proof=tar_evidence('app','a'*64)
            proof['probe_output']=proof['probe_output'].replace('TAR_SECURITY_CASE_OK cve='+cve+'\n','')
            with self.subTest(cve=cve),self.assertRaises(RuntimeError):self.verify(proof)

    def test_rejects_missing_or_partial_full_suite(self):
        for key,value in [('passed',False),('successful',0),('successful',True),('sha256','not-a-hash')]:
            proof=tar_evidence('app','a'*64);proof['native_suite'][key]=value
            with self.subTest(key=key),self.assertRaises(RuntimeError):self.verify(proof)

    def test_rejects_a_changed_saved_probe_even_with_unchanged_binary(self):
        with self.assertRaises(RuntimeError):
            check_evidence(tar_evidence('app','a'*64),TAR_MANIFEST,'trixie','a'*64,'0'*64)


class TarSourceGuards(unittest.TestCase):
    def test_source_hash_missing_file_and_preexisting_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'source.c').write_bytes(b'reviewed')
            verify_tree(root,{'source.c':hashlib.sha256(b'reviewed').hexdigest(),'new.c':None})
            for expected in ({'source.c':'0'*64},{'missing.c':'0'*64},{'source.c':None},{'../escape':None}):
                with self.subTest(expected=expected),self.assertRaises(RuntimeError):verify_tree(root,expected)

    def test_source_symlink_cannot_substitute_for_reviewed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'real.c').write_bytes(b'reviewed');(root/'link.c').symlink_to('real.c')
            with self.assertRaises(RuntimeError):verify_tree(root,{'link.c':hashlib.sha256(b'reviewed').hexdigest()})

    def test_native_proof_cannot_be_created_with_root_or_boolean_uid(self):
        for value in (0,True):
            with self.assertRaises(RuntimeError):unprivileged(value)


if __name__=='__main__':
    unittest.main()
