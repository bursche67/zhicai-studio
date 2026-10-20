"""Offline source-guard repairs and graph rebuild. ZERO model/API calls.

Dry run by default. --apply first snapshots SQLite, keeps a per-document log,
then rebuilds derived project rows from latest versions of distinct notices.
Original uploaded files and raw model traces are never changed.
"""
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import connection, init_db, persist, json_value
from backend.config import db_path
from backend.models import Extraction
from backend.parsers import Parsed, parse_document, sha
from backend.jobs import prepare_groups
from backend.extraction import source_guard


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--apply',action='store_true')
    ap.add_argument('--rebuild-only',action='store_true',help='Rebuild derived relations after a merge-policy fix; keep document extractions unchanged')
    args=ap.parse_args()
    with connection() as db:
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','queued') LIMIT 1").fetchone():
            raise SystemExit('Active import; repair deferred without changes')
        docs=[dict(r) for r in db.execute("SELECT d.*,j.manifest FROM documents d JOIN jobs j ON j.id=d.job_id WHERE j.mode='model' AND d.is_sample=0 AND d.extraction IS NOT NULL AND d.status IN ('completed','duplicate','partial')")]
    folder=db_path().parent/'quality_backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if args.apply:
        import sqlite3,os
        folder.mkdir(parents=True,exist_ok=False)
        with connection() as db, sqlite3.connect(folder/'before.db') as target:
            db.backup(target)
        os.chmod(folder/'before.db',0o600)
        init_db()
    cache={};content_cache={};parsed_cache={};changes=[];projects=set()
    for doc in docs:
        if args.rebuild_only:
            if doc['project_id']:projects.add(doc['project_id'])
            continue
        if doc['job_id'] not in cache:
            manifest=json.loads(doc['manifest']);entries=[]
            signature=json_value({'files':[(f['name'],f['sha256']) for f in manifest['files']],'association':manifest.get('association')})
            if signature not in content_cache:
                for f in manifest['files']:
                    data=Path(f['path']).read_bytes()
                    if sha(data)!=f['sha256']:raise ValueError('Original upload hash mismatch')
                    entries.append((f['name'],data))
                files,groups,_=prepare_groups(entries,manifest.get('association'))
                content_cache[signature]=(files,{g[0]:g for g in groups})
            cache[doc['job_id']]=content_cache[signature]
        files,groups=cache[doc['job_id']]
        if doc['name'] not in groups:continue
        key=tuple((name,sha(files[name])) for name in groups[doc['name']])
        if key not in parsed_cache:
            parsed=Parsed()
            for name in groups[doc['name']]:parsed.extend(parse_document(name,files[name]))
            parsed_cache[key]=parsed
        parsed=parsed_cache[key]
        if parsed.errors:continue  # Never interpret failed parsing as nondisclosure.
        old=Extraction.model_validate_json(doc['extraction'])
        new=source_guard(old.model_copy(deep=True),parsed)
        if old.model_dump()!=new.model_dump():
            changes.append({'document_id':doc['id'],'uuid':doc['source_uuid'],
                'before':old.model_dump(mode='json'),'after':new.model_dump(mode='json')})
            if args.apply:
                with connection() as db:db.execute('UPDATE documents SET extraction=? WHERE id=?',(new.model_dump_json(),doc['id']))
        if doc['project_id']:projects.add(doc['project_id'])
    rebuilt=0
    if args.apply:
        (folder/'document_changes.json').write_text(json_value(changes),encoding='utf-8')
        for pid in sorted(projects):
            with connection() as db:
                row=db.execute('''SELECT d.id,d.extraction,p.mode,p.is_sample FROM documents d
                  JOIN jobs j ON j.id=d.job_id JOIN projects p ON p.id=d.project_id
                  WHERE d.project_id=? AND d.extraction IS NOT NULL AND d.status IN ('completed','partial','duplicate') ORDER BY j.created_at DESC,d.id DESC LIMIT 1''',(pid,)).fetchone()
                if row:
                    persist(db,row['id'],Extraction.model_validate_json(row['extraction']),row['mode'],bool(row['is_sample']))
                    rebuilt+=1
    print(json.dumps({'apply':args.apply,'rebuild_only':args.rebuild_only,'api_calls':0,'documents_checked':len(docs),
        'changed_documents':len(changes),'changed_uuids':sorted({x['uuid'] for x in changes}),
        'rebuilt_projects':rebuilt,'backup':str(folder) if args.apply else None},ensure_ascii=False))


if __name__=='__main__':main()
