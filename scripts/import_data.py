"""Batch CLI; model requires explicit --mode model."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import init_db, connection
from backend.jobs import create_job, process_job

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('files',nargs='+',type=Path)
    parser.add_argument('--mode',choices=['rule','model'],default='rule')
    parser.add_argument('--sample',action='store_true')
    parser.add_argument('--association',type=Path)
    args=parser.parse_args()
    init_db()
    association=json.loads(args.association.read_text(encoding='utf-8')) if args.association else None
    jid=create_job([(p.name,p.read_bytes()) for p in args.files],args.mode,args.sample,association)
    print('job',jid,flush=True)
    process_job(jid)
    with connection() as db:
        row=dict(db.execute('SELECT id,status,elapsed_seconds,error FROM jobs WHERE id=?',(jid,)).fetchone())
        row['documents']=[dict(r) for r in db.execute('SELECT status,COUNT(*) count FROM documents WHERE job_id=? GROUP BY status',(jid,))]
    print(json.dumps(row,ensure_ascii=False,indent=2))
    return 1 if row['status']=='failed' else 0

if __name__=='__main__':
    raise SystemExit(main())
