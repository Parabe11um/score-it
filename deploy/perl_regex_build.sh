#!/bin/sh
# Shared build-host-only application and proof of the complete regex fix.
set -eu
MODE=$1
PROFILE=$2
POLICY=/run/score-it-perl-regex-sources.json
PATCHES=/run/score-it-perl-patches
COUNTER=/run/score-it-perl-regex-cache.py
RUNTIME=/run/score-it-perl-regex-runtime.pl
case "$PROFILE" in app) SERIES=5.40; ABI=5.40.1 ;; postgres) SERIES=5.36; ABI=5.36.0 ;; *) exit 2 ;; esac
case "$MODE" in
apply)
    python3 - "$POLICY" "$PROFILE" "$PATCHES" "$COUNTER" "$RUNTIME" <<'PY'
import hashlib,json,sys
from pathlib import Path
p=json.loads(Path(sys.argv[1]).read_text())[sys.argv[2]]
for name,sha in p['patch_sha256'].items():
    assert hashlib.sha256((Path(sys.argv[3])/name).read_bytes()).hexdigest()==sha
for path,key in ((sys.argv[4],'counter_probe_sha256'),(sys.argv[5],'runtime_probe_sha256')):
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==p[key]
PY
    mkdir ../regex-original ../regex-first
    cp regexec.c regexp.h ../regex-original/
    for NAME in regex-countdown regex-wide-cache; do
        cp "$PATCHES/$NAME-$SERIES.patch" "debian/patches/score-it-$NAME.patch"
        printf '%s\n' "score-it-$NAME.patch" >>debian/patches/series
        dpkg-source --before-build .
        if [ "$NAME" = regex-countdown ]; then cp regexec.c regexp.h ../regex-first/; fi
    done
    ;;
test)
    test -f perl-test-static-stamp
    test -f perl-test-shared-stamp
    UID_EXPECTED="$(id -u perlbuild)"
    test "$UID_EXPECTED" -ne 0
    printf '%s\n' 'PERL_REGEX_UPSTREAM_TESTS_OK static shared' > /out/perl-regex-tests.txt
    for CONTROL in original first; do
        STATUS=0
        runuser -u perlbuild -- sh -ec 'ulimit -v 262144; ulimit -t 15; exec python3 "$@"' \
            sh "$COUNTER" --source "../regex-$CONTROL" >../regex-control.log 2>&1 || STATUS=$?
        test "$STATUS" = 42
        grep -Fx PERL_REGEX_ARITHMETIC_PROBE_READY ../regex-control.log
        grep -Fx 'PERL_REGEX_CACHE_UNDERSIZED length=286331153 nodes=15' ../regex-control.log
        printf 'PERL_REGEX_CONTROL_REJECTED=%s exit=42\n' "$CONTROL" >>/out/perl-regex-tests.txt
    done
    runuser -u perlbuild -- sh -ec 'ulimit -v 262144; ulimit -t 15; exec python3 "$@"' \
        sh "$COUNTER" --source . >>/out/perl-regex-tests.txt
    runuser -u perlbuild -- sh -ec 'ulimit -v 262144; ulimit -t 10; exec build-static/perl -Ibuild-static/lib "$@"' \
        sh "$RUNTIME" "$UID_EXPECTED" "$ABI" >>/out/perl-regex-tests.txt
    printf 'PERL_REGEX_NATIVE_UID=%s\n' "$UID_EXPECTED" >>/out/perl-regex-tests.txt
    ;;
manifest)
    STAGE="$(mktemp -d)"
    trap 'rm -rf "$STAGE"' EXIT HUP INT TERM
    VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]]["runtime_version"])' "$POLICY" "$PROFILE")"
    dpkg-deb --extract "/out/perl-base_${VERSION}_amd64.deb" "$STAGE"
    if [ "$PROFILE" = postgres ]; then dpkg-deb --extract "/out/libperl5.36_${VERSION}_amd64.deb" "$STAGE"; fi
    python3 - "$POLICY" "$PROFILE" "$STAGE" /out/perl-regex-tests.txt /out/perl-regex-build.json <<'PY'
import hashlib,json,re,sys
from pathlib import Path
policy,profile,stage,tests,output=sys.argv[1:]
p=json.loads(Path(policy).read_text())[profile]
log=Path(tests).read_text();lines=log.splitlines()
required={'PERL_REGEX_UPSTREAM_TESTS_OK static shared','PERL_REGEX_CONTROL_REJECTED=original exit=42',
          'PERL_REGEX_CONTROL_REJECTED=first exit=42','PERL_REGEX_CACHE_ARITHMETIC_OK checks=301 counter_bits=64'}
assert required<=set(lines)
uid=re.findall(r'^PERL_REGEX_NATIVE_UID=([1-9][0-9]*)$',log,re.M);assert len(uid)==1
assert 'PERL_REGEX_RUNTIME_OK cases=8 uid='+uid[0]+' version=v'+p['abi'] in lines
records=[json.loads(line) for line in lines if line.startswith('{')];assert len(records)==1
assert records[0]['check']=='perl_regex_source' and records[0]['source_sha256']==p['patched_source_sha256']
assert records[0]['probe_sha256']==p['counter_probe_sha256']
hashes={}
for name in p['binary_paths']:
    path=Path(stage)/name
    raw=path.read_bytes();assert raw[:4]==b'\x7fELF',name
    hashes[name]=hashlib.sha256(raw).hexdigest()
result={'check':'perl_regex_build','profile':profile,'policy':p,'binary_sha256':hashes,
        'native_tests':{'uid':int(uid[0]),'counter_bits':64,'boundary_checks':301,'negative_controls':2,
                        'runtime_cases':8,'upstream_targets':['static','shared']},'native_log':lines}
Path(output).write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print('PERL_REGEX_BUILD_PROOF_OK profile='+profile)
PY
    ;;
*) exit 2 ;;
esac
