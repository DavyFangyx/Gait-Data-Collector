import argparse
import json
from pathlib import Path
from .io import discover_recordings
from .schema import METHODS, Profile, Rules
from .service import run_pipeline


def main():
    p=argparse.ArgumentParser(description="Offline pressure inspection; never overwrites raw recordings")
    p.add_argument('input',type=Path)
    p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--method',choices=METHODS,default='original')
    p.add_argument('--layout-dir',type=Path)
    p.add_argument('--suspect-left',default='')
    p.add_argument('--suspect-right',default='')
    p.add_argument('--long-gap-ms',type=float,default=300)
    p.add_argument('--model-reference',type=float,default=1023)
    args=p.parse_args()
    root=args.input.resolve(strict=True); out=args.output_root.resolve()
    if root==out or root in out.parents: p.error('Output must be outside the input tree')
    def cells(text): return tuple(int(x.strip()) for x in text.split(',') if x.strip())
    profile=Profile(args.method,cells(args.suspect_left),cells(args.suspect_right)); profile.validate()
    rules=Rules(long_gap_us=args.long_gap_ms*1000,model_reference=args.model_reference); rules.validate()
    recordings=discover_recordings(root)
    if not recordings: p.error('No recording directories found')
    failed=False
    for recording in recordings:
        try:
            run=run_pipeline(recording,out,profile=profile,rules=rules,layout_dir=args.layout_dir)
            receipt=json.loads((run/'raw_check_receipt.json').read_text(encoding='utf-8'))
            print(json.dumps(dict(recording=str(recording),run=str(run),data_status=receipt['data_status']),ensure_ascii=False))
            failed |= receipt['data_status']=='error'
        except Exception as exc:
            print(json.dumps(dict(recording=str(recording),status='failed',error=str(exc)),ensure_ascii=False))
            failed=True
    return 2 if failed else 0


if __name__=='__main__':
    raise SystemExit(main())
