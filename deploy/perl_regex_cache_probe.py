#!/usr/bin/env python3
"""Compile cache arithmetic from the actual Perl source, using no large input.

The two counter declarations, initializer and cache-size expression come from
regexp.h/regexec.c verbatim. The input pointer subtraction alone is adapted to
a scalar subject length, so boundary values need no huge allocation or exploit.
Original and countdown-only sources must fail the same independent size oracle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def require(value, message):
    if not value:
        raise RuntimeError(message)


def initializer(source):
    needle='if (!reginfo->poscache_maxiter) {'
    require(source.count(needle)==1,'Ambiguous cache initializer')
    start=source.index(needle);position=source.index('{',start);depth=1;end=position+1
    while depth and end<len(source):
        depth += (source[end]=='{')-(source[end]=='}');end+=1
    require(depth==0,'Unterminated cache initializer')
    return source[start:end]


def generate(regexec, header):
    declarations=re.findall(r'^\s*(?:I32|STRLEN)\s+poscache_(?:maxiter|iter);[^\n]*',header,re.M)
    require(len(declarations)==2,'Expected both actual cache counter fields')
    code=initializer(regexec)
    old='(reginfo->strend - reginfo->strbeg + 1)'
    new='reginfo->strend - reginfo->strbeg'
    if old in code:
        require(code.count(old)==1,'Ambiguous original length expression')
        code=code.replace(old,'(subject_length + 1)')
    else:
        require(code.count(new)==1,'Missing subject length expression')
        code=code.replace(new,'subject_length')
    size=re.findall(r'const\s+(?:SSize_t|STRLEN)\s+size\s*=\s*\(reginfo->poscache_maxiter \+ 7\)/8;',regexec)
    require(len(size)==1,'Ambiguous actual cache allocation expression')
    template=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <limits.h>
#include <inttypes.h>
typedef int32_t I32;
typedef uint8_t U8;
typedef size_t STRLEN;
typedef ptrdiff_t SSize_t;
#define I32_MAX INT32_MAX
#define FLAGS(p) ((p)->flags)
struct counters { DECLARATIONS };
struct node { unsigned char flags; };
static int check_length(STRLEN subject_length, unsigned nodes) {
    struct counters info = {0,0};
    struct counters *reginfo = &info;
    struct node item = {(unsigned char)(nodes << 4)};
    struct node *scan = &item;
    INITIALIZER
    __uint128_t bits = ((__uint128_t)subject_length + 1) * nodes;
    int fits = bits + 7 <= SIZE_MAX;
    if (!fits) return reginfo->poscache_maxiter == 0;
    if ((__uint128_t)reginfo->poscache_maxiter != bits ||
        (__uint128_t)reginfo->poscache_iter != bits) return 0;
    SIZE_EXPRESSION
    return (__uint128_t)size == (bits + 7)/8;
}
int main(void) {
    if (sizeof(STRLEN) != 8) return 90;
    const STRLEN lengths[] = {0,1,7,8,9,1024,134217728,268435456,286331153,
        INT32_MAX,UINT32_MAX,(STRLEN)UINT32_MAX+1,SIZE_MAX/16,SIZE_MAX/15,
        SIZE_MAX/15-1,SIZE_MAX/2-1,SIZE_MAX/2,SIZE_MAX-8,SIZE_MAX-7,SIZE_MAX};
    unsigned checks=1;
    puts("PERL_REGEX_ARITHMETIC_PROBE_READY");
    /* The reported positive signed-overflow vector produced a two-byte cache. */
    if (!check_length(286331153,15)) {
        puts("PERL_REGEX_CACHE_UNDERSIZED length=286331153 nodes=15");
        return 42;
    }
    for (unsigned n=1;n<=15;n++)
        for (unsigned i=0;i<sizeof(lengths)/sizeof(lengths[0]);i++) {
            checks++;
            if (!check_length(lengths[i],n)) {
                printf("PERL_REGEX_CACHE_UNDERSIZED length=%" PRIuMAX " nodes=%u\n",(uintmax_t)lengths[i],n);
                return 42;
            }
        }
    printf("PERL_REGEX_CACHE_ARITHMETIC_OK checks=%u counter_bits=%zu\n",checks,
           8*sizeof(((struct counters*)0)->poscache_iter));
    return 0;
}
'''
    return template.replace('DECLARATIONS','\n'.join(declarations)).replace('INITIALIZER',code).replace('SIZE_EXPRESSION',size[0])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    args=parser.parse_args()
    regexec=(args.source/'regexec.c').read_text();header=(args.source/'regexp.h').read_text()
    with tempfile.TemporaryDirectory(prefix='perl-regex-cache-') as td:
        p=Path(td);(p/'probe.c').write_text(generate(regexec,header))
        compiled=subprocess.run(['cc','-std=c11','-O0','-fwrapv','-Wall','-Wextra','-Werror',str(p/'probe.c'),'-o',str(p/'probe')],
                                capture_output=True,text=True,timeout=30)
        if compiled.returncode:
            print(compiled.stderr[-4000:],flush=True)
            return 91
        result=subprocess.run([str(p/'probe')],capture_output=True,text=True,timeout=10)
        print(result.stdout,end='',flush=True)
        if result.returncode:
            return result.returncode
    print(json.dumps({'check':'perl_regex_source','source_sha256':{
        n:hashlib.sha256((args.source/n).read_bytes()).hexdigest() for n in ('regexec.c','regexp.h')},
        'probe_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},sort_keys=True),flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
