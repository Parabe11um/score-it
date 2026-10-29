"""Reject partial fixes, stale executables and weakened native/runtime evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from check_perl_regex import POLICY,PROOF,check_archive,check_runtime
from test_caddy_payload import saved_image
from test_runtime_tools import elf_bytes


def payload(profile):
    p=copy.deepcopy(POLICY[profile]);files={}
    for name in p['binary_paths']:files[name]=elf_bytes([])+name.encode()
    files['var/lib/dpkg/status']=''.join('Package: '+n+'\nStatus: install ok installed\nVersion: '+v+'\n\n' for n,v in p['packages'].items()).encode()
    record={'check':'perl_regex_source','source_sha256':p['patched_source_sha256'],'probe_sha256':p['counter_probe_sha256']}
    log=['PERL_REGEX_UPSTREAM_TESTS_OK static shared','PERL_REGEX_CONTROL_REJECTED=original exit=42',
         'PERL_REGEX_CONTROL_REJECTED=first exit=42','PERL_REGEX_CACHE_ARITHMETIC_OK checks=301 counter_bits=64',
         'PERL_REGEX_NATIVE_UID=100','PERL_REGEX_RUNTIME_OK cases=8 uid=100 version=v'+p['abi'],json.dumps(record)]
    proof={'check':'perl_regex_build','profile':profile,'policy':p,
           'binary_sha256':{n:hashlib.sha256(files[n]).hexdigest() for n in p['binary_paths']},
           'native_tests':{'uid':100,'counter_bits':64,'boundary_checks':301,'negative_controls':2,'runtime_cases':8,
                           'upstream_targets':['static','shared']},'native_log':log}
    files[PROOF]=json.dumps(proof).encode()
    return files


def layer(files,links=()):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as t:
        for name,raw in files.items():
            p=tarfile.TarInfo(name);p.size=len(raw);t.addfile(p,io.BytesIO(raw))
        for name,target in links:
            p=tarfile.TarInfo(name);p.type=tarfile.LNKTYPE;p.linkname=target;t.addfile(p)
    return out.getvalue()


class PerlRegexTests(unittest.TestCase):
    def verify(self,profile='postgres',mutation=None,prior=(),oci=False):
        files=payload(profile)
        if mutation:mutation(files)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'images.tar';saved_image(p,[*prior,layer(files)],oci=oci,image='score-it:regex')
            return check_archive(p,'score-it:regex',profile)

    def change_proof(self,files,change):
        d=json.loads(files[PROOF]);change(d);files[PROOF]=json.dumps(d).encode()

    def test_both_abis_and_archive_formats(self):
        for profile in POLICY:
            for oci in (False,True):
                with self.subTest(profile=profile,oci=oci):
                    result=self.verify(profile,oci=oci)
                    self.assertEqual(result['packages'],POLICY[profile]['packages'])
                    self.assertEqual(result['native_tests']['boundary_checks'],301)

    def test_missing_partial_native_root_or_changed_source_proof_stops(self):
        mutations=[lambda d:d['native_tests'].__setitem__('uid',0),
                   lambda d:d['native_tests'].__setitem__('negative_controls',1),
                   lambda d:d['native_tests'].__setitem__('counter_bits',32),
                   lambda d:d['native_log'].remove('PERL_REGEX_CONTROL_REJECTED=first exit=42'),
                   lambda d:d['policy']['patch_sha256'].pop(next(iter(d['policy']['patch_sha256']))),
                   lambda d:d['policy']['patched_source_sha256'].__setitem__('regexec.c','0'*64)]
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                self.verify(mutation=lambda files:self.change_proof(files,change))

    def test_old_status_binary_whiteout_and_renamed_libperl_stop(self):
        status=payload('postgres')['var/lib/dpkg/status'].replace(b'+scoreit2',b'+scoreit1')
        with self.assertRaises(RuntimeError):self.verify(prior=[layer({'var/lib/dpkg/status':status})])
        for name,raw in (('usr/bin/perl',b'old'),('usr/bin/.wh.perl',b''),
                         ('opt/renamed.so',elf_bytes([], 'libperl.so.5.36'))):
            with self.subTest(name=name),self.assertRaises(RuntimeError):self.verify(prior=[layer({name:raw})])

    def test_stale_version_missing_or_changed_binary_stops(self):
        for profile in POLICY:
            for mode in ('version','missing','changed'):
                def mutate(files):
                    if mode=='version':files['var/lib/dpkg/status']=files['var/lib/dpkg/status'].replace(b'+scoreit',b'+oldscoreit')
                    elif mode=='missing':files.pop(POLICY[profile]['binary_paths'][0])
                    else:files[POLICY[profile]['binary_paths'][0]]+=b'changed'
                with self.subTest(profile=profile,mode=mode),self.assertRaises(RuntimeError):self.verify(profile,mutate)

    def test_hardlink_alias_is_bound_to_the_actual_payload(self):
        profile='app';files=payload(profile);alias='usr/bin/perl'+POLICY[profile]['abi']
        files[alias]=files['usr/bin/perl'];d=json.loads(files[PROOF]);d['binary_sha256'][alias]=hashlib.sha256(files[alias]).hexdigest();files[PROOF]=json.dumps(d).encode()
        del files[alias]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'image.tar';saved_image(p,[layer(files,[(alias,'usr/bin/perl')])],image='score-it:regex')
            evidence=check_archive(p,'score-it:regex',profile)
            self.assertEqual(evidence['binary_sha256'][alias],evidence['binary_sha256']['usr/bin/perl'])

    def test_runtime_uid_package_hash_and_functional_result_are_required(self):
        for profile in POLICY:
            evidence=self.verify(profile);uid=10001 if profile=='app' else 999
            output='PERL_REGEX_RUNTIME_UID='+str(uid)+'\n'
            output+=''.join(n+'\t'+v+'\n' for n,v in evidence['packages'].items())
            output+=''.join(h+'  /'+n+'\n' for n,h in evidence['binary_sha256'].items())
            output+='PERL_REGEX_RUNTIME_OK cases=8 uid='+str(uid)+' version=v'+evidence['abi']+'\n'
            check_runtime(output,profile,evidence)
            for before,after in (('UID='+str(uid),'UID=0'),('cases=8','cases=0'),('+scoreit','+oldscoreit'),
                                 (next(iter(evidence['binary_sha256'].values())),'0'*64)):
                with self.subTest(profile=profile,before=before),self.assertRaises(RuntimeError):
                    check_runtime(output.replace(before,after),profile,evidence)


if __name__=='__main__':unittest.main()
