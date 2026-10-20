"""SQLite-backed single-worker queue. Imports remain inspectable after restart."""
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
from .config import storage_path
from .db import connection, init_db, json_value, persist, stable
from .parsers import parse_document, unpack_zip, Parsed, sha
from .extraction import rule_extract, model_extract, model_unavailable_reason

def now():
    return datetime.now(timezone.utc).isoformat()

def prepare_groups(entries, association=None):
    files, issues = {}, []
    for name, content in entries:
        if name.lower().endswith('.zip'):
            try:
                for path, data in unpack_zip(name, content):
                    if path in files:
                        raise ValueError('ZIP存在重复路径')
                    files[path] = data
            except Exception as exc:
                issues.append({'name': name, 'error': str(exc)})
        else:
            if name in files:
                raise ValueError('重复上传名称')
            files[name] = content
    groups, used = [], set()
    if association:
        for group in association.get('groups', []):
            names = [group['html']] + group.get('attachments', [])
            if any(n not in files for n in names) or any(n in used for n in names):
                raise ValueError('关联清单包含不存在或重复分配的文件；ZIP内部名称使用 ZIP名/内部路径')
            if Path(names[0]).suffix.lower() not in ('.html', '.htm'):
                raise ValueError('关联组必须以HTML公告为主文件')
            groups.append(names)
            used.update(names)
    htmls = [n for n in files if Path(n).suffix.lower() in ('.html', '.htm') and n not in used]
    by_stem = {}
    for name in htmls:
        by_stem.setdefault(Path(name).stem, []).append(name)
    html_set = set(htmls)
    automatic = {name: [name] for name in htmls}
    for name in files:
        if name in used or name in html_set:
            continue
        components = {Path(x).stem for x in name.split('/')}
        candidates = {h for stem in components for h in by_stem.get(stem, [])}
        if len(candidates) == 1:
            automatic[next(iter(candidates))].append(name)
    for members in automatic.values():
        groups.append(members)
        used.update(members)
    for name in files:
        if name not in used:
            issues.append({'name': name, 'error': '待关联：没有唯一同前缀HTML；请提供关联清单'})
    return files, groups, issues

def matching_active_job(db, mode, manifest, exclude_id=None):
    signature = sorted((f['name'], f['sha256']) for f in manifest['files'])
    for row in db.execute("SELECT id,status,manifest FROM jobs WHERE mode=? AND status IN ('queued','running') ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,created_at,id", (mode,)):
        if row['id'] == exclude_id:
            continue
        old = json.loads(row['manifest'])
        if old['sample'] == manifest['sample'] and old.get('association') == manifest.get('association') and sorted((f['name'], f['sha256']) for f in old['files']) == signature:
            return row
    return None


def create_job(entries, mode, sample=False, association=None, initial_status='queued', deduplicate_active=False):
    if initial_status not in ('queued', 'running'):
        raise ValueError('非法初始任务状态')
    entries = [(name, content, sha(content)) for name, content in entries]
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if deduplicate_active and initial_status == 'queued':
            candidate = {'files': [{'name': name, 'sha256': digest} for name, _, digest in entries], 'sample': sample, 'association': association}
            existing = matching_active_job(db, mode, candidate)
            if existing:
                return existing['id']
        job_id = uuid.uuid4().hex
        folder = storage_path() / job_id
        folder.mkdir(parents=True, exist_ok=False)
        manifest = {'files': [], 'sample': sample, 'association': association, 'attempt_number': 1, 'attempts': []}
        for index, (name, content, digest) in enumerate(entries):
            path = folder / f'{index:04d}.bin'
            path.write_bytes(content)
            manifest['files'].append({'name': name, 'path': str(path.resolve()), 'sha256': digest})
        manifest['import_completed_at'] = manifest['queued_at'] = now()
        db.execute('INSERT INTO jobs(id,mode,status,created_at,manifest) VALUES(?,?,?,?,?)', (job_id, mode, initial_status, now(), json_value(manifest)))
    return job_id

def process_job(job_id, queued_only=False):
    started = time.perf_counter()
    with connection() as db:
        job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        if (queued_only and job['status'] != 'queued') or job['status'] == 'running':
            return
        db.execute("UPDATE jobs SET status='running',started_at=?,error=NULL WHERE id=?", (now(), job_id))
        # Retried document IDs are stable, so completed results are atomically replaced.
    manifest = json.loads(job['manifest'])
    try:
        if job['mode'] == 'model' and (reason := model_unavailable_reason()):
            raise ValueError(reason + '；整批任务未执行模型抽取')
        entries = [(f['name'], Path(f['path']).read_bytes()) for f in manifest['files']]
        files, groups, issues = prepare_groups(entries, manifest.get('association'))
        manifest['notice_count'] = len(groups)
        with connection() as db:
            db.execute('UPDATE jobs SET manifest=? WHERE id=?', (json_value(manifest),job_id))
        for index, issue in enumerate(issues):
            with connection() as db:
                db.execute('INSERT OR REPLACE INTO documents(id,job_id,name,sha256,status,errors,is_sample) VALUES(?,?,?,?,?,?,?)',
                           (stable(job_id + 'issue' + str(index)), job_id, issue['name'], '', 'needs_attention', json_value([issue]), int(manifest['sample'])))
        successes, failures = 0, len(issues)
        for index, names in enumerate(groups):
            doc_id = stable(job_id + 'group' + str(index))
            digest = sha(b''.join(n.encode('utf-8') + b'\0' + files[n] for n in names))
            source_uuid = Path(names[0]).stem
            soup = BeautifulSoup(files[names[0]], 'html.parser')
            canonical = soup.find('link', rel='canonical') or soup.find('meta', attrs={'property': 'og:url'})
            source_url = (canonical.get('href') or canonical.get('content')) if canonical else None
            if not source_url:
                # Preserve a declared source URL; do not invent URL paths from filenames.
                import re
                candidates = re.findall(r'https?://(?:www\.)?ccgp\.gov\.cn/[^\s\"<>]+/' + re.escape(source_uuid) + r'\.html?', str(soup))
                source_url = candidates[0] if candidates else None
            with connection() as db:
                mapped_url = db.execute('SELECT url FROM source_url_mappings WHERE source_uuid=? AND mode=? AND is_sample=?', (source_uuid, job['mode'], int(manifest['sample']))).fetchone()
                if not source_url and mapped_url:
                    source_url = mapped_url[0]
                previous = db.execute('SELECT status,extraction FROM documents WHERE id=? AND sha256=?', (doc_id, digest)).fetchone()
                if previous and previous['status'] in ('completed', 'partial', 'duplicate') and previous['extraction'] is not None:
                    # Retry only unfinished/failed groups. Never rebill a successful
                    # group merely because a different group failed in this job.
                    successes += 1
                    failures += int(previous['status'] == 'partial')
                    continue
                db.execute('INSERT INTO documents(id,job_id,name,sha256,status,is_sample,source_uuid,source_url) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,errors=\'[]\'',
                           (doc_id, job_id, names[0], digest, 'parsing', int(manifest['sample']), source_uuid, source_url))
                duplicate = db.execute('''SELECT d.project_id,d.extraction FROM documents d JOIN jobs j ON j.id=d.job_id
                  WHERE d.sha256=? AND j.mode=? AND d.is_sample=? AND d.id<>? AND d.status IN ('completed','partial','duplicate') AND d.extraction IS NOT NULL ORDER BY j.created_at DESC,d.id DESC LIMIT 1''',
                  (digest, job['mode'], int(manifest['sample']), doc_id)).fetchone()
                if duplicate:
                    db.execute("UPDATE documents SET status='duplicate',project_id=?,extraction=? WHERE id=?", (duplicate['project_id'], duplicate['extraction'], doc_id))
                    successes += 1
                    continue
            parsed, trace = Parsed(), []
            trace_path = storage_path() / job_id / f'{doc_id}.trace.json'
            try:
                for name in names:
                    parsed.extend(parse_document(name, files[name]))
                if not parsed.blocks:
                    raise ValueError('公告及附件均未提取到可读内容')
                with connection() as db:
                    db.execute("UPDATE documents SET status='extracting' WHERE id=?", (doc_id,))
                result = rule_extract(parsed) if job['mode'] == 'rule' else model_extract(parsed, trace)
                if not source_url:
                    result.warnings.append('源HTML未声明可核验URL，标准导出URL留空')
                status = 'partial' if parsed.errors else 'completed'
                with connection() as db:
                    persist(db, doc_id, result, job['mode'], manifest['sample'])
                    db.execute('UPDATE documents SET status=?,errors=?,trace_path=? WHERE id=?', (status, json_value(parsed.errors), str(trace_path), doc_id))
                successes += 1
                if parsed.errors:
                    failures += 1
            except Exception as exc:
                failures += 1
                with connection() as db:
                    db.execute("UPDATE documents SET status='failed',errors=?,trace_path=? WHERE id=?", (json_value(parsed.errors + [{'error': str(exc)}]), str(trace_path), doc_id))
            finally:
                trace_path.write_text(json_value({'files': [{'name': n, 'sha256': sha(files[n])} for n in names], 'model_calls': trace, 'parse_errors': parsed.errors}), encoding='utf-8')
        status = 'partial' if failures and successes else 'failed' if failures or not groups else 'completed'
        with connection() as db:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(manifest['queued_at'])).total_seconds() if manifest.get('queued_at') else time.perf_counter() - started
            db.execute('UPDATE jobs SET status=?,finished_at=?,elapsed_seconds=?,error=NULL WHERE id=?', (status, now(), elapsed, job_id))
    except Exception as exc:
        with connection() as db:
            db.execute("UPDATE jobs SET status='failed',finished_at=?,elapsed_seconds=?,error=? WHERE id=?", (now(), time.perf_counter() - started, str(exc), job_id))

class Worker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        init_db()
        with connection() as db:
            db.execute("UPDATE jobs SET status='interrupted',error='服务重启导致任务中断；可手动重试' WHERE status='running'")
            db.execute("UPDATE documents SET status='failed',errors=? WHERE status IN ('parsing','extracting') AND job_id IN (SELECT id FROM jobs WHERE status='interrupted')", (json_value([{'error': '服务更新中断此条处理，可重试；已成功记录保留'}]),))
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            with connection() as db:
                row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row:
                process_job(row['id'], queued_only=True)
            else:
                self.stop.wait(.4)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)
