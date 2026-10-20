import json
import sqlite3
import unicodedata
import hashlib
from contextlib import contextmanager
from .config import db_path
from .models import Extraction, cents

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,mode TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,elapsed_seconds REAL,manifest TEXT NOT NULL,error TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES jobs(id),name TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,errors TEXT NOT NULL DEFAULT '[]',project_id TEXT,extraction TEXT,trace_path TEXT,is_sample INTEGER NOT NULL DEFAULT 0,source_uuid TEXT,source_url TEXT);
CREATE TABLE IF NOT EXISTS organizations(id INTEGER PRIMARY KEY,name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,identity_key TEXT NOT NULL UNIQUE,name TEXT,number TEXT,buyer_id INTEGER REFERENCES organizations(id),project_amount INTEGER,award_amount INTEGER,mode TEXT NOT NULL,is_sample INTEGER NOT NULL DEFAULT 0,warnings TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS sources(document_id TEXT PRIMARY KEY REFERENCES documents(id),project_id TEXT NOT NULL REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS packages(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,code TEXT,name TEXT,award_amount INTEGER,evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bids(id INTEGER PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,supplier_id INTEGER NOT NULL REFERENCES organizations(id),result TEXT NOT NULL,award_amount INTEGER,evidence TEXT NOT NULL,review_status TEXT,bid_price INTEGER,score TEXT,loss_reason TEXT,consortium TEXT,consortium_score TEXT,consortium_award_amount INTEGER,consortium_review_status TEXT,consortium_bid_price INTEGER,UNIQUE(package_id,supplier_id));
CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,package_id TEXT NOT NULL REFERENCES packages(id) ON DELETE CASCADE,name TEXT,category TEXT,brand TEXT,product_supplier_id INTEGER REFERENCES organizations(id),specification TEXT,unit_price INTEGER,quantity TEXT,unit TEXT,total_price INTEGER,evidence TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_buyer ON projects(buyer_id);
CREATE INDEX IF NOT EXISTS idx_bid_supplier ON bids(supplier_id,project_id,result);
CREATE INDEX IF NOT EXISTS idx_items_project ON items(project_id);
CREATE TABLE IF NOT EXISTS source_url_mappings(source_uuid TEXT NOT NULL,mode TEXT NOT NULL,is_sample INTEGER NOT NULL,url TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(source_uuid,mode,is_sample));
'''

@contextmanager
def connection():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA synchronous=NORMAL')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    with connection() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript(SCHEMA)
        columns = {r[1] for r in db.execute('PRAGMA table_info(bids)')}
        for name, kind in [('consortium', 'TEXT'), ('consortium_score', 'TEXT'), ('consortium_award_amount', 'INTEGER'), ('consortium_review_status', 'TEXT'), ('consortium_bid_price', 'INTEGER')]:
            if name not in columns:
                db.execute(f'ALTER TABLE bids ADD COLUMN {name} {kind}')

def norm(name):
    return ' '.join(unicodedata.normalize('NFKC', name).split()) if name else None

def org(db, name):
    name = norm(name)
    if not name:
        return None
    db.execute('INSERT OR IGNORE INTO organizations(name) VALUES(?)', (name,))
    return db.execute('SELECT id FROM organizations WHERE name=?', (name,)).fetchone()[0]

def stable(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]

def json_value(value):
    return json.dumps(value, ensure_ascii=False, default=str)

def persist(db, doc_id, extraction: Extraction, mode, is_sample=False):
    buyer = org(db, extraction.buyer)
    identity = (f'{norm(extraction.buyer)}|{norm(extraction.project_number)}' if buyer and extraction.project_number else 'document:' + doc_id) + '|' + mode + '|' + str(int(is_sample))
    pid = stable(identity)
    previous_project = db.execute('SELECT project_id FROM sources WHERE document_id=?', (doc_id,)).fetchone()
    if previous_project and previous_project[0] != pid:
        old_pid = previous_project[0]
        db.execute('DELETE FROM sources WHERE document_id=?', (doc_id,))
        remaining = db.execute('SELECT d.id,d.extraction,p.mode,p.is_sample FROM sources s JOIN documents d ON d.id=s.document_id JOIN projects p ON p.id=s.project_id WHERE s.project_id=? LIMIT 1', (old_pid,)).fetchone()
        if remaining:
            persist(db, remaining['id'], Extraction.model_validate_json(remaining['extraction']), remaining['mode'], bool(remaining['is_sample']))
        else:
            db.execute('DELETE FROM projects WHERE id=?', (old_pid,))
    existing = db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
    original_json = extraction.model_dump_json()
    old_sources = db.execute('SELECT document_id FROM sources WHERE project_id=? AND document_id<>?', (pid, doc_id)).fetchall()
    warnings = list(extraction.warnings)
    if existing and old_sources:
        # Different notices can supplement or conflict: never silently sum their rows.
        previous = [dict(r) for r in db.execute('''SELECT d.id,d.source_uuid,d.extraction,j.created_at
          FROM sources s JOIN documents d ON d.id=s.document_id JOIN jobs j ON j.id=d.job_id
          WHERE s.project_id=? AND d.id<>? AND d.extraction IS NOT NULL''', (pid, doc_id))]
        current = dict(db.execute('SELECT d.id,d.source_uuid,j.created_at FROM documents d JOIN jobs j ON j.id=d.job_id WHERE d.id=?', (doc_id,)).fetchone())
        current['extraction'] = original_json
        # A rerun of the same notice replaces its version. Keep every source
        # link, but never union historical versions of one UUID into the graph.
        latest = {}
        for record in sorted(previous + [current], key=lambda r: (r['created_at'], r['id']), reverse=True):
            latest.setdefault(record['source_uuid'] or record['id'], record)
        if previous:
            from .extraction import merge_extractions
            extraction = merge_extractions([Extraction.model_validate_json(r['extraction']) for r in latest.values()])
            warnings = extraction.warnings + ['同UUID仅使用最新成功版本；不同公告可补充同一项目，冲突见警告，金额不累加']
    db.execute('INSERT INTO projects VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,number=excluded.number,buyer_id=excluded.buyer_id,project_amount=excluded.project_amount,award_amount=excluded.award_amount,mode=excluded.mode,is_sample=excluded.is_sample,warnings=excluded.warnings',
               (pid, identity, extraction.project_name, extraction.project_number, buyer, cents(extraction.project_amount), cents(extraction.award_amount), mode, int(is_sample), json_value(warnings)))
    db.execute('DELETE FROM packages WHERE project_id=?', (pid,))
    for index, package in enumerate(extraction.packages):
        package_id = stable(pid + '|' + (package.code if package.code is not None else f'unknown:{index}'))
        db.execute('INSERT INTO packages VALUES(?,?,?,?,?,?)', (package_id, pid, package.code, package.name, cents(package.award_amount), json_value([e.model_dump() for e in package.evidence])))
        unique_bids = {}
        for bid in package.bids:
            sid = org(db, bid.supplier)
            if sid in unique_bids:
                from .extraction import merge_bid
                unique_bids[sid] = merge_bid(unique_bids[sid], bid, warnings)
            else:
                unique_bids[sid] = bid
        for sid, bid in unique_bids.items():
            db.execute('INSERT INTO bids(project_id,package_id,supplier_id,result,award_amount,evidence,review_status,bid_price,score,loss_reason,consortium,consortium_score,consortium_award_amount,consortium_review_status,consortium_bid_price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid, package_id, sid, bid.result, cents(bid.award_amount), json_value([e.model_dump() for e in bid.evidence]), bid.review_status, cents(bid.bid_price), str(bid.score) if bid.score is not None else None, bid.loss_reason, bid.consortium, str(bid.consortium_score) if bid.consortium_score is not None else None, cents(bid.consortium_award_amount), bid.consortium_review_status, cents(bid.consortium_bid_price)))
        for i, item in enumerate(package.items):
            # Evidence identifies the row; identical-looking distinct rows are preserved.
            key = json_value(item.model_dump(mode='json'))
            iid = stable(package_id + key)
            db.execute('INSERT OR IGNORE INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (iid, pid, package_id, item.name, item.category, item.brand, org(db, item.product_supplier), item.specification, cents(item.unit_price), str(item.quantity) if item.quantity is not None else None, item.unit, cents(item.total_price), json_value([e.model_dump() for e in item.evidence])))
    db.execute('UPDATE projects SET warnings=? WHERE id=?', (json_value(warnings), pid))
    db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (doc_id, pid))
    db.execute('UPDATE documents SET project_id=?,extraction=? WHERE id=?', (pid, original_json, doc_id))
    return pid
