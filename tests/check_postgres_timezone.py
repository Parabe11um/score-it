#!/usr/bin/env python3
"""Verify Bookworm tzdata data files and real GNU date behavior as postgres."""
import argparse
import hashlib
import json
from pathlib import Path,PurePosixPath
import posixpath
import re
import tarfile

from check_caddy_runtime import Archive,docker,layer_names,require

VERSION='2026e-0+deb12u1'
PINS=Path(__file__).resolve().parents[1]/'deploy/postgres_tzdata_files.sha256'
FILES={line.split('  ',1)[1]:line.split('  ',1)[0] for line in PINS.read_text().splitlines()}
OFFSETS={'Etc/UTC':'+0000','Europe/Moscow':'+0300','America/Winnipeg':'-0500','America/Yellowknife':'-0600'}


def check_archive(path,image):
    payload={};links={};watched=set();version=None
    version_headers={}
    with Archive(path) as source:
        _,layers=layer_names(source,image,'linux/amd64')
        for layer_name in layers:
            member=source.members.get(str(PurePosixPath(layer_name)))
            require(member is not None and member.isfile(),'Missing timezone layer')
            with source.tar.extractfile(member) as stream,tarfile.open(fileobj=stream,mode='r|*') as layer:
                for item in layer:
                    name=str(PurePosixPath(item.name))
                    require(not PurePosixPath(name).is_absolute() and '..' not in PurePosixPath(name).parts,'Unsafe timezone path')
                    require(name.replace('/.wh.','/') not in FILES or name in FILES,'Deleted timezone payload retained')
                    if name=='var/lib/dpkg/status':
                        require(item.isfile() and item.size<16*1024**2,'Invalid timezone package inventory')
                        rows=[]
                        for stanza in layer.extractfile(item).read().decode().split('\n\n'):
                            fields=dict(row.split(': ',1) for row in stanza.splitlines() if ': ' in row and not row.startswith(' '))
                            if fields.get('Package')=='tzdata' and fields.get('Status')=='install ok installed':rows.append(fields.get('Version'))
                        require(rows==[VERSION],'Old or ambiguous tzdata package')
                        version=rows[0]
                        continue
                    if not name.startswith('usr/share/zoneinfo/') or item.isdir():
                        require(name not in FILES,'Timezone payload replaced by a directory')
                        require(name not in payload and name not in links,'Timezone alias replaced by a directory')
                        continue
                    require(not PurePosixPath(name).name.startswith('.wh.'),'Deleted timezone data retained')
                    if name in FILES:
                        require(name not in watched,'Overwritten timezone file retained: '+name)
                        require(item.isfile() or item.islnk() or item.issym(),'Invalid timezone file type: '+name)
                        watched.add(name)
                    if item.isfile():
                        require(item.size<2*1024**2 and name not in payload and name not in links,
                                'Invalid or overwritten timezone data: '+name)
                        raw=layer.extractfile(item).read();payload[name]=hashlib.sha256(raw).hexdigest()
                        version_headers[name]=raw.startswith(b'# version 2026e\n')
                    elif item.islnk():
                        target=str(PurePosixPath(item.linkname))
                        require(not PurePosixPath(target).is_absolute() and '..' not in PurePosixPath(target).parts
                                and target.startswith('usr/share/zoneinfo/') and target!=name
                                and name not in payload and name not in links,'Unsafe timezone hardlink: '+name)
                        links[name]=target
                    elif item.issym():
                        require(name not in payload and name not in links,'Overwritten timezone alias: '+name)
                        # Resolve archive metadata only, never a host filesystem link.
                        target=posixpath.normpath(posixpath.join(str(PurePosixPath(name).parent),item.linkname))
                        links[name]=target
    found={}
    require(watched==set(FILES),'Incomplete timezone payload')
    for name in FILES:
        target=name;seen=set()
        while target in links:
            require(target not in seen,'Timezone hardlink cycle')
            seen.add(target);target=links[target]
            require(target.startswith('usr/share/zoneinfo/') and '..' not in PurePosixPath(target).parts,
                    'Timezone alias leaves its data directory')
        require(target in payload,'Missing timezone hardlink target: '+name)
        found[name]=payload[target]
        if name.endswith('tzdata.zi'):require(version_headers[target],'Timezone source version differs')
    require(version==VERSION and found==FILES,'Timezone files differ from reviewed official Bookworm package')
    return found


def verify(image,archive):
    identity=json.loads(docker('image','inspect',image))[0]['Id']
    hashes=check_archive(archive,image)
    script='''set -eu
test "$(id -u)" = 999
test "$(dpkg-query -W -f='${Version}' tzdata)" = 2026e-0+deb12u1
'''
    for zone,offset in OFFSETS.items():
        for date in ('2026-11-02 12:00:00 UTC','2030-01-15 12:00:00 UTC'):
            script+='test "$(TZ='+zone+" date --date='"+date+"' +%z)\" = '"+offset+"'\n"
    script+='sha256sum '+' '.join('/'+p for p in sorted(FILES))+'\n'
    output=docker('run','--rm','--pull=never','--network','none','--read-only','--user','999:999',
                  '--cap-drop','ALL','--security-opt','no-new-privileges:true','--memory','128m','--pids-limit','32',
                  '--entrypoint','sh',identity,'-ec',script)
    pairs=re.findall(r'^([0-9a-f]{64})  /([^\n]+)$',output,re.M)
    require(len(pairs)==len(FILES) and {p:h for h,p in pairs}==hashes,'Saved/runtime timezone hashes differ')
    require(json.loads(docker('image','inspect',image))[0]['Id']==identity,'Timezone image changed')
    print(json.dumps({'check':'postgres_timezone','image':image,'runtime_image_id':identity,'uid':999,
                      'package_version':VERSION,'file_sha256':hashes,'offsets':OFFSETS,'date_cases':8},sort_keys=True),flush=True)
    print('POSTGRES_TIMEZONE_ARCHIVE_AND_RUNTIME_OK',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--image',required=True)
    p.add_argument('--archive',type=Path,required=True);a=p.parse_args();verify(a.image,a.archive)
