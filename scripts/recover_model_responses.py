"""Revalidate captured responses against original files. Makes ZERO API calls.

Never complete a document with missing chunks, truncated JSON or bad evidence.
Run while no import is running. Dry run by default; --apply persists recoveries.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.db import connection, persist, json_value
from backend.jobs import prepare_groups, now
from backend.parsers import Parsed, parse_document, sha
from backend.extraction import validate_model_response, merge_extractions, model_input_blocks, chunks, model_chunks, evidence_input, model_error_message


def revalidate_trace(parsed, trace):
    calls = trace['model_calls']
    limit = calls[0].get('chunk_chars', 12000)
    expected = list(model_chunks(parsed, limit) if calls[0].get('chunk_strategy') == 'table-rows-v1'
                    else chunks(model_input_blocks(parsed), limit))
    if calls[0].get('evidence_strategy') == 'span-ref-v1':
        expected = [evidence_input(parsed, chunk) for chunk in expected]
    if [c.get('input') for c in calls] != expected:
        raise ValueError('历史响应未覆盖全部原文分块；保留失败，需重新抽取')
    results = []
    for call in calls:
        choice = call.get('raw_response', {}).get('choices', [{}])[0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('模型响应被截断或未完成；结果未入库，需重新抽取')
        results.append(validate_model_response(parsed, choice['message']['content'], call['input']))
    return merge_extractions(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    counts = Counter()
    recovered = []
    with connection() as db:
        if db.execute("SELECT 1 FROM jobs WHERE status IN ('running','queued') LIMIT 1").fetchone():
            raise SystemExit('An import is active; recovery aborted without changes')
        if args.apply:
            db.execute("UPDATE documents SET status='failed',errors=? WHERE status IN ('parsing','extracting') AND job_id IN (SELECT id FROM jobs WHERE mode='model')", (json_value([{'error': '服务更新中断此条处理，可重试；已成功记录保留'}]),))
        jobs = [dict(r) for r in db.execute("SELECT * FROM jobs WHERE mode='model'")]
    for job in jobs:
        with connection() as db:
            docs = [dict(r) for r in db.execute("SELECT * FROM documents WHERE job_id=? AND status='failed' AND trace_path IS NOT NULL", (job['id'],))]
        candidates = []
        for doc in docs:
            trace_path = Path(doc['trace_path'])
            if not trace_path.exists():
                continue
            trace = json.loads(trace_path.read_text(encoding='utf-8'))
            if trace.get('model_calls') and any(c.get('raw_response') for c in trace['model_calls']):
                candidates.append((doc, trace_path, trace))
        if not candidates:
            continue
        manifest = json.loads(job['manifest'])
        entries = []
        for f in manifest['files']:
            content = Path(f['path']).read_bytes()
            if sha(content) != f['sha256']:
                raise ValueError('Original file hash mismatch; no guessed recovery')
            entries.append((f['name'], content))
        files, groups, _ = prepare_groups(entries, manifest.get('association'))
        by_name = {g[0]: g for g in groups}
        for doc, trace_path, trace in candidates:
            counts['examined'] += 1
            try:
                names = by_name[doc['name']]
                parsed = Parsed()
                for name in names:
                    parsed.extend(parse_document(name, files[name]))
                result = revalidate_trace(parsed, trace)
                result.warnings.append('已保存模型响应离线复验入库：未再次调用API；不计入正式处理速度，尚未人工精度验收')
                counts['recoverable'] += 1
                if args.apply:
                    with connection() as db:
                        persist(db, doc['id'], result, 'model', bool(doc['is_sample']))
                        db.execute('UPDATE documents SET status=?,errors=? WHERE id=?', ('partial' if parsed.errors else 'completed', json_value(parsed.errors), doc['id']))
                    trace['offline_recovery'] = {'at': now(), 'original_errors': json.loads(doc['errors']), 'api_calls': 0}
                    trace_path.write_text(json_value(trace), encoding='utf-8')
                recovered.append({'job_id': job['id'], 'document_id': doc['id'], 'name': doc['name']})
            except Exception as exc:
                counts['still_failed'] += 1
                message = model_error_message(exc)
                if args.apply:
                    with connection() as db:
                        db.execute('UPDATE documents SET errors=? WHERE id=?', (json_value([{'error': message}]), doc['id']))
        if args.apply:
            with connection() as db:
                rows = db.execute('SELECT status FROM documents WHERE job_id=?', (job['id'],)).fetchall()
                good = sum(r['status'] in ('completed', 'duplicate', 'partial') for r in rows)
                bad = sum(r['status'] not in ('completed', 'duplicate') for r in rows)
                state = 'partial' if good and bad else 'completed' if good else 'failed'
                db.execute('UPDATE jobs SET status=?,error=NULL WHERE id=?', (state, job['id']))
    print(json.dumps({'apply': args.apply, 'api_calls': 0, 'counts': dict(counts), 'recovered': recovered}, ensure_ascii=False))


if __name__ == '__main__':
    main()
