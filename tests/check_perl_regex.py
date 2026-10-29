#!/usr/bin/env python3
"""Bind the complete regex fix to actual packaged Perl executables/libraries."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

from check_caddy_runtime import Archive, docker, layer_names, require
from check_perl_runtime import inventory
from elf_dependencies import elf

ROOT=Path(__file__).resolve().parents[1]
POLICY=json.loads((ROOT/'deploy/perl_regex_sources.json').read_text())
PROOF='usr/share/score-it/perl-regex-build.json'


def perl_binary(name):
    return bool(re.fullmatch(r'usr/bin/perl(?:[0-9.]+(?:-x86_64-linux-gnu)?)?',name)
                or re.match(r'^libperl\.so(?:\.|$)',PurePosixPath(name).name))


def check_archive(path,image,profile):
    wanted=POLICY[profile];paths=set(wanted['binary_paths'])
    found={};links={};proof=None;packages=None
    with Archive(path) as source:
        config,layers=layer_names(source,image,'linux/amd64')
        for layer_name in layers:
            member=source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(),'Missing Perl regex layer')
            with source.tar.extractfile(member) as stream,tarfile.open(fileobj=stream,mode='r|*') as layer:
                for item in layer:
                    name=str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,'Unsafe Perl regex path')
                    require(not ('.wh.' in PurePosixPath(name).name and perl_binary(name.replace('/.wh.','/'))),'Deleted Perl regex binary retained')
                    if name=='var/lib/dpkg/status':
                        require(item.isfile() and item.size<16*1024**2,'Invalid regex package inventory')
                        packages=inventory(layer.extractfile(item).read(),profile)
                        require(packages==wanted['packages'],'Regex Perl package versions differ')
                        continue
                    if name==PROOF:
                        require(item.isfile() and proof is None and item.size<2*1024**2,'Invalid or overwritten regex build proof')
                        proof=json.loads(layer.extractfile(item).read())
                        continue
                    if name in paths:
                        require(name not in found and name not in links,'Overwritten regex executable retained')
                        if item.islnk():
                            target=str(PurePosixPath(item.linkname))
                            require(target in paths and target!=name,'Unexpected regex binary hardlink')
                            links[name]=target
                            continue
                        require(item.isfile() and item.size<256*1024**2,'Invalid regex executable')
                        raw=layer.extractfile(item).read()
                        require(raw[:4]==b'\x7fELF','Perl regex payload is not ELF')
                        found[name]=hashlib.sha256(raw).hexdigest()
                    elif perl_binary(name):
                        # Debian's shared libperl SONAME link is not a second payload.
                        allowed='usr/lib/x86_64-linux-gnu/libperl.so.5.36'
                        require(profile=='postgres' and name==allowed and item.issym()
                                and item.linkname=='libperl.so.5.36.0','Unexpected or old Perl executable/library')
                    elif item.isfile():
                        with layer.extractfile(item) as data:
                            first=data.read(4)
                            if first==b'\x7fELF':
                                require(item.size<256*1024**2,'Oversized ELF')
                                meta=elf(first+data.read())
                                require(not (meta['soname'] and meta['soname'].startswith('libperl.so')),
                                        'Renamed libperl payload retained')
    for name in list(links):
        seen={name};target=links[name]
        while target in links:
            require(target not in seen,'Perl binary hardlink cycle')
            seen.add(target);target=links[target]
        require(target in found,'Missing Perl binary hardlink target')
        found[name]=found[target]
    require(packages==wanted['packages'] and set(found)==paths and proof is not None,'Incomplete Perl regex payload')
    require(proof['check']=='perl_regex_build' and proof['profile']==profile and proof['policy']==wanted
            and proof['binary_sha256']==found,'Regex source/package/binary proof differs')
    tests=proof['native_tests'];uid=tests.get('uid')
    require(type(uid) is int and uid>0 and tests=={'uid':uid,'counter_bits':64,'boundary_checks':301,
            'negative_controls':2,'runtime_cases':8,'upstream_targets':['static','shared']},'Incomplete native regex tests')
    lines=proof['native_log']
    require({'PERL_REGEX_UPSTREAM_TESTS_OK static shared','PERL_REGEX_CONTROL_REJECTED=original exit=42',
             'PERL_REGEX_CONTROL_REJECTED=first exit=42','PERL_REGEX_CACHE_ARITHMETIC_OK checks=301 counter_bits=64',
             'PERL_REGEX_NATIVE_UID='+str(uid),'PERL_REGEX_RUNTIME_OK cases=8 uid='+str(uid)+' version=v'+wanted['abi']}
            <=set(lines),'Missing regex native controls or regression')
    records=[json.loads(line) for line in lines if line.startswith('{')]
    require(len(records)==1 and records[0]['check']=='perl_regex_source'
            and records[0]['source_sha256']==wanted['patched_source_sha256']
            and records[0]['probe_sha256']==wanted['counter_probe_sha256'],'Native regex source hashes differ')
    require(hashlib.sha256((ROOT/'deploy/perl_regex_cache_probe.py').read_bytes()).hexdigest()==wanted['counter_probe_sha256']
            and hashlib.sha256((ROOT/'deploy/perl_regex_runtime_probe.pl').read_bytes()).hexdigest()==wanted['runtime_probe_sha256'],
            'Regex probe source changed')
    return {'packages':packages,'binary_sha256':found,'abi':wanted['abi'],
            'native_tests':tests,'patched_source_sha256':wanted['patched_source_sha256']}


def check_runtime(output,profile,evidence):
    uid=10001 if profile=='app' else 999
    lines=output.splitlines()
    require(lines and lines[0]=='PERL_REGEX_RUNTIME_UID='+str(uid),'Regex runtime UID differs')
    versions=[line.split('\t') for line in lines if '\t' in line]
    require(len(versions)==len(evidence['packages']) and all(len(row)==2 for row in versions)
            and dict(versions)==evidence['packages'],'Regex runtime package versions differ')
    pairs=re.findall(r'^([0-9a-f]{64})  /([^\n]+)$',output,re.M)
    require(len(pairs)==len(evidence['binary_sha256']) and {name:sha for sha,name in pairs}==evidence['binary_sha256'],
            'Saved and runtime regex binaries differ')
    require(lines[-1]=='PERL_REGEX_RUNTIME_OK cases=8 uid='+str(uid)+' version=v'+evidence['abi'],
            'Native runtime regex regression incomplete')


def verify(image,archive,profile):
    identity=json.loads(docker('image','inspect',image))[0]['Id']
    evidence=check_archive(archive,image,profile)
    wanted=POLICY[profile];uid='10001' if profile=='app' else '999'
    script='''set -eu
printf 'PERL_REGEX_RUNTIME_UID=%s\\n' "$(id -u)"
ulimit -v 262144
ulimit -t 10
'''
    for package in sorted(wanted['packages']):
        script+="printf '%s\\t%s\\n' "+package+" \"$(dpkg-query -W -f='${Version}' "+package+")\"\n"
    script+='sha256sum '+' '.join('/'+name for name in sorted(wanted['binary_paths']))+'\n'
    script+='exec perl - '+uid+' '+wanted['abi']+'\n'
    probe=(ROOT/'deploy/perl_regex_runtime_probe.pl').read_text()
    output=docker('run','--rm','--pull=never','--network','none','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges:true','--memory','256m','--pids-limit','32',
        '--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777','--user',uid+':'+uid,
        '-i','--entrypoint','sh',identity,'-ec',script,input=probe,timeout=30)
    check_runtime(output,profile,evidence)
    require(json.loads(docker('image','inspect',image))[0]['Id']==identity,'Regex image tag changed')
    print(json.dumps({'check':'perl_regex','image':image,'runtime_image_id':identity,'uid':int(uid),**evidence},sort_keys=True),flush=True)
    print('PERL_REGEX_ARCHIVE_AND_RUNTIME_OK='+profile,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--profile',choices=POLICY,required=True)
    args=parser.parse_args();verify(args.image,args.archive,args.profile)
