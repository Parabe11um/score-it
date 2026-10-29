#!/usr/bin/env python3
"""Keep the raw v3 HIGH row; bind its reviewed fix to saved image bytes."""
import argparse
import hashlib
import json
from pathlib import Path
from check_caddy_runtime import check_archive
from caddy_jose_policy import assess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--image',required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    _,proof,identity,digest=check_archive(a.archive,a.image,include_proof=True)
    raw=a.report.read_bytes();report=json.loads(raw)
    covered=assess(report,proof,identity,digest)
    result={'check':'caddy_jose_review','image':a.image,'image_config_id':identity,
            'binary_sha256':digest,'report_sha256':hashlib.sha256(raw).hexdigest(),
            'raw_high_backports':covered,'raw_report_modified':False,'build_proof':proof}
    a.output.write_text(json.dumps(result,sort_keys=True)+'\n')
    print('CADDY_JOSE_BACKPORT_BOUND_TO_SAVED_IMAGE raw_high_rows='+str(len(covered)),flush=True)


if __name__=='__main__':main()
