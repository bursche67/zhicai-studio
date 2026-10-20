import csv
import io
import json
import os
import time
import base64
import secrets
from decimal import Decimal
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from .db import connection
from .jobs import Worker, create_job, now, matching_active_job
from .models import SupplierSelection, SourceURLMapping
from .analysis import cooperation, buyer_bidders, supplier_competitors, common_buyers, common_projects, query
from .graph import graph_adapter
from .export import workbook_bytes, standard_rows
from .config import ROOT
from .extraction import model_unavailable_reason
from .model_budget import status as budget_status
from .model_balance import balance_status
from .quality import quality_report, capabilities
from .evaluation import evaluate as evaluate_fields, FIELDS as EVALUATION_FIELDS

worker = Worker()

@asynccontextmanager
async def lifespan(app):
    if os.getenv('APP_PUBLIC_MODE', 'false').lower() == 'true':
        if not os.getenv('APP_ACCESS_USER') or len(os.getenv('APP_ACCESS_PASSWORD', '')) < 12:
            raise RuntimeError('公网模式必须配置APP_ACCESS_USER及至少12字符APP_ACCESS_PASSWORD')
    worker.start()
    yield
    worker.close()

app = FastAPI(title='行业数据实体挖掘与关系建模', version='0.1.0', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
Mode = Literal['rule', 'model']

@app.middleware('http')
async def timing(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    response.headers['X-Response-Time-Ms'] = f'{(time.perf_counter()-start)*1000:.3f}'
    return response

@app.middleware('http')
async def access_control(request, call_next):
    expected_user = os.getenv('APP_ACCESS_USER', '')
    expected_password = os.getenv('APP_ACCESS_PASSWORD', '')
    public = os.getenv('APP_PUBLIC_MODE', 'false').lower() == 'true'
    if expected_user or expected_password or public:
        accepted = False
        try:
            scheme, token = request.headers.get('authorization', '').split(' ', 1)
            if scheme.lower() == 'basic':
                user, password = base64.b64decode(token, validate=True).decode('utf-8').split(':', 1)
                accepted = secrets.compare_digest(user.encode(), expected_user.encode()) and secrets.compare_digest(password.encode(), expected_password.encode()) and bool(expected_user and expected_password)
        except (ValueError, UnicodeError):
            pass
        if not accepted:
            return Response('需要访问账号', status_code=401, headers={'WWW-Authenticate': 'Basic realm="Zhicai Studio", charset="UTF-8"', 'Cache-Control': 'no-store'})
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response

@app.get('/api/health')
def health():
    return {'status': 'ok', 'model_configured': bool(os.getenv('MODEL_BASE_URL') and os.getenv('MODEL_NAME')), 'model_processing_available': model_unavailable_reason() is None, 'model_unavailable_reason': model_unavailable_reason(), 'model_remote': urlparse(os.getenv('MODEL_BASE_URL', '')).hostname not in (None, 'localhost', '127.0.0.1', '::1'), 'remote_processing_enabled': os.getenv('MODEL_ALLOW_REMOTE', 'false').lower() == 'true', 'model_name': os.getenv('MODEL_NAME') or None, 'model_json_mode': os.getenv('MODEL_JSON_MODE', 'false').lower() == 'true', 'model_max_tokens': int(os.getenv('MODEL_MAX_TOKENS', '4096')), 'parameter_size': os.getenv('MODEL_PARAMETER_SIZE') or None, 'model_compliance': '请核验模型服务授权', 'graph_backend': 'sqlite'}

@app.get('/api/model-budget')
def model_budget():
    return budget_status()


@app.get('/api/model-balance')
def model_balance(response: Response, refresh: bool = False):
    response.headers['Cache-Control'] = 'no-store, private'
    return {**balance_status(refresh), 'internal_budget': budget_status()}


@app.get('/api/capabilities')
def parser_capabilities():
    return capabilities()


@app.get('/api/quality')
def quality(mode: Mode = 'model', sample: bool = False):
    return quality_report(mode, sample)


@app.get('/api/imports/{job_id}/quality')
def job_quality(job_id: str):
    with connection() as db:
        row = db.execute('SELECT mode,manifest FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '任务不存在')
    return quality_report(row['mode'], json.loads(row['manifest'])['sample'], job_id)


@app.post('/api/sources/map')
def map_source_urls(body: SourceURLMapping, mode: Mode = 'model', sample: bool = False):
    changed = 0
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for entry in body.entries:
            existing = db.execute('SELECT DISTINCT source_url FROM documents WHERE source_uuid=? AND is_sample=? AND job_id IN (SELECT id FROM jobs WHERE mode=?) AND source_url IS NOT NULL', (entry.uuid,int(sample),mode)).fetchall()
            stored = db.execute('SELECT url FROM source_url_mappings WHERE source_uuid=? AND mode=? AND is_sample=?', (entry.uuid,mode,int(sample))).fetchone()
            if not body.overwrite and (any(r[0] != entry.url for r in existing) or (stored and stored[0] != entry.url)):
                raise HTTPException(409, f'{entry.uuid}已有不同来源地址；核验后可显式设置overwrite=true')
            db.execute('INSERT INTO source_url_mappings VALUES(?,?,?,?,?) ON CONFLICT(source_uuid,mode,is_sample) DO UPDATE SET url=excluded.url,updated_at=excluded.updated_at', (entry.uuid,mode,int(sample),entry.url,now()))
            changed += db.execute('UPDATE documents SET source_url=? WHERE source_uuid=? AND is_sample=? AND job_id IN (SELECT id FROM jobs WHERE mode=?)', (entry.url,entry.uuid,int(sample),mode)).rowcount
    return {'mapping_count':len(body.entries),'updated_document_versions':changed,'note':'用户提供的来源映射已保存；未请求或独立核验网页，不改写原HTML。'}


@app.post('/api/evaluation')
async def evaluate_upload(gold: UploadFile = File(...), mode: Mode = Form('model'), sample: bool = Form(False)):
    data = await gold.read(2*1024*1024+1)
    if len(data) > 2*1024*1024:
        raise HTTPException(413, '金标JSON最多2MB')
    try:
        reference = json.loads(data.decode('utf-8-sig'))
        if not isinstance(reference,dict) or not isinstance(reference.get('items'),list) or not 1 <= len(reference['items']) <= 5000:
            raise ValueError('金标必须包含1至5000条人工核验商品记录')
        scope = reference.get('evaluation_uuids')
        if not isinstance(scope,list) or not scope or not all(isinstance(v,str) for v in scope):
            raise ValueError('必须声明非空evaluation_uuids公告范围')
        for item in reference['items']:
            if not isinstance(item,dict) or not item.get('uuid') or any(f not in item for f in EVALUATION_FIELDS):
                raise ValueError('每条金标必须包含uuid与七字段；缺失字段显式填null')
            for f in ('unit_price','total_price','quantity'):
                value=item[f]
                if value not in (None,'') and not Decimal(str(value)).is_finite():
                    raise ValueError('金标数值必须有限')
        with connection() as db:
            rows=query(db, '''SELECT d.*,j.manifest FROM documents d JOIN jobs j ON j.id=d.job_id
              WHERE j.mode=? AND d.is_sample=? AND d.extraction IS NOT NULL
              AND d.status IN ('completed','partial','duplicate') ORDER BY j.created_at DESC,d.id DESC''', (mode,int(sample)))
        predictions,seen,used=[],set(),[]
        for doc in rows:
            if not any(f.get('sha256')==reference.get('dataset_sha256') for f in json.loads(doc['manifest']).get('files',[])):
                continue
            uuid=doc['source_uuid']
            if uuid not in scope or uuid in seen:
                continue
            seen.add(uuid);used.append(doc['id'])
            from .models import Extraction
            out=Extraction.model_validate_json(doc['extraction'])
            for package in out.packages:
                for item in package.items:
                    predictions.append({'uuid':uuid,'package':package.name or package.code,**{f:getattr(item,f) for f in EVALUATION_FIELDS}})
        if not used:
            raise ValueError('当前范围没有与金标dataset_sha256匹配的实际提取记录，请核对数据版本和方案')
        from collections import Counter
        groups=Counter((i['uuid'],i.get('package'),i.get('name')) for i in reference['items']+predictions)
        if any(v>200 for v in groups.values()):
            raise ValueError('同名商品匹配组超过200条，请拆分核验范围')
        result=evaluate_fields(reference,{'dataset_sha256':reference['dataset_sha256'],'evaluation_uuids':scope,'items':predictions})
        return {**result,'mode':mode,'sample':sample,'source_document_ids':used,'gold_notice_count':len(set(scope)),'notices_with_predictions':len(seen),'evaluated_at':now(),'reference_score':False,'note':'基于用户声明已人工核验的金标，按本地匹配口径计算；仅供本地质量分析。未额外调用模型。'}
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        raise HTTPException(422, str(exc)) from exc

@app.post('/api/imports', status_code=202)
async def upload(files: list[UploadFile] = File(...), mode: Mode = Form('model'), sample: bool = Form(False), association: str | None = Form(None)):
    if mode == 'model' and (reason := model_unavailable_reason()):
        raise HTTPException(503, reason + '；任务未创建。可选择规则基线验证工程流程。')
    entries, total = [], 0
    for file in files:
        name = Path((file.filename or '').replace('\\', '/')).name
        if not name:
            raise HTTPException(400, '文件名为空')
        data = await file.read(100 * 1024 * 1024 + 1)
        total += len(data)
        if len(data) > 100 * 1024 * 1024 or total > 200 * 1024 * 1024 or len(files) > 100:
            raise HTTPException(413, '单文件最多100MB、总计200MB、最多100个上传文件')
        entries.append((name, data))
    if len({n for n, _ in entries}) != len(entries):
        raise HTTPException(400, '上传文件名重复')
    try:
        mapping = json.loads(association) if association else None
        if mapping is not None and (not isinstance(mapping, dict) or not isinstance(mapping.get('groups'), list)):
            raise ValueError('关联清单须为包含groups数组的对象')
        job_id = create_job(entries, mode, sample, mapping, deduplicate_active=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with connection() as db:
        status = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()[0]
    return {'id': job_id, 'status': status, 'mode': mode}

@app.get('/api/imports')
def imports(limit: int = Query(30, ge=1, le=100)):
    with connection() as db:
        return query(db, 'SELECT id,mode,status,created_at,elapsed_seconds,error FROM jobs ORDER BY created_at DESC LIMIT ?', (limit,))

@app.get('/api/imports/{job_id}')
def import_detail(job_id: str):
    with connection() as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, '任务不存在')
        result = dict(row)
        manifest = json.loads(result.pop('manifest'))
        result['files'] = [{'name': f['name'], 'sha256': f['sha256']} for f in manifest['files']]
        result['sample'] = manifest['sample']
        result['reused_job_id'] = manifest.get('reused_job_id')
        result['queue_ahead'] = 0
        result['active_job'] = None
        if result['status'] == 'queued':
            pending = query(db, "SELECT id,status FROM jobs WHERE status IN ('queued','running') ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END,created_at,id")
            ids = [j['id'] for j in pending]
            result['queue_ahead'] = ids.index(job_id) if job_id in ids else 0
            active = next((j for j in pending if j['status'] == 'running'), None)
            if active:
                active['current_document'] = db.execute("SELECT name FROM documents WHERE job_id=? AND status IN ('parsing','extracting') LIMIT 1", (active['id'],)).fetchone()
                active['current_document'] = active['current_document'][0] if active['current_document'] else None
                result['active_job'] = active
        result['documents'] = query(db, 'SELECT id,name,status,errors,project_id,source_uuid,source_url FROM documents WHERE job_id=? ORDER BY name', (job_id,))
        for doc in result['documents']:
            doc['errors'] = json.loads(doc['errors'])
        result['document_count'] = len(result['documents'])
        result['document_status_counts'] = {s: sum(d['status'] == s for d in result['documents']) for s in {d['status'] for d in result['documents']}}
        notice_count = sum(bool(d['source_uuid']) for d in result['documents'])
        result['notice_count'] = notice_count
        result['average_seconds_per_notice'] = (result['elapsed_seconds'] / notice_count) if result['elapsed_seconds'] is not None and notice_count else None
        return result

@app.post('/api/imports/{job_id}/retry', status_code=202)
def retry(job_id: str):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status,manifest,mode,started_at,finished_at,elapsed_seconds FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在')
        if row[0] in ('queued', 'running'):
            raise HTTPException(409, '任务已在队列或处理中')
        if row['mode'] == 'model' and (reason := model_unavailable_reason()):
            raise HTTPException(503, reason + '；任务未重新排队。')
        manifest = json.loads(row['manifest'])
        existing = matching_active_job(db, row['mode'], manifest, job_id)
        if existing:
            return {'id': existing['id'], 'status': existing['status'], 'reused': True}
        if 'attempt_number' not in manifest:
            manifest['first_attempt_unknown'] = True
        manifest.setdefault('attempts',[]).append({'number':manifest.get('attempt_number'), 'status':row['status'],'started_at':row['started_at'],'finished_at':row['finished_at'],'elapsed_seconds':row['elapsed_seconds']})
        manifest['attempt_number'] = manifest.get('attempt_number',1)+1
        manifest['queued_at'] = now()
        db.execute("UPDATE jobs SET status='queued',error=NULL,elapsed_seconds=NULL,finished_at=NULL,manifest=? WHERE id=?", (json.dumps(manifest, ensure_ascii=False), job_id))
    return {'id': job_id, 'status': 'queued'}

@app.get('/api/organizations')
def organizations(q: str = '', role: Literal['all', 'buyer', 'bidder'] = 'all', mode: Mode = 'model', sample: bool = False):
    with connection() as db:
        if role == 'buyer':
            sql = 'SELECT DISTINCT o.id,o.name FROM organizations o JOIN projects p ON p.buyer_id=o.id WHERE p.mode=? AND p.is_sample=? AND o.name LIKE ? ORDER BY o.name LIMIT 100'
        elif role == 'bidder':
            sql = 'SELECT DISTINCT o.id,o.name FROM organizations o JOIN bids b ON b.supplier_id=o.id JOIN projects p ON p.id=b.project_id WHERE p.mode=? AND p.is_sample=? AND o.name LIKE ? ORDER BY o.name LIMIT 100'
        else:
            sql = '''SELECT DISTINCT o.id,o.name FROM organizations o WHERE o.name LIKE ? AND (o.id IN
            (SELECT buyer_id FROM projects WHERE mode=? AND is_sample=?) OR o.id IN
            (SELECT b.supplier_id FROM bids b JOIN projects p ON p.id=b.project_id WHERE p.mode=? AND p.is_sample=?) OR o.id IN
            (SELECT i.product_supplier_id FROM items i JOIN projects p ON p.id=i.project_id WHERE p.mode=? AND p.is_sample=?)) ORDER BY o.name LIMIT 100'''
            return query(db, sql, ['%' + q + '%', mode, int(sample), mode, int(sample), mode, int(sample)])
        return query(db, sql, [mode, int(sample), '%' + q + '%'])

def item_rows(db, q, category, brand, mode, sample, page, page_size, filters=None):
    where = ['p.mode=?', 'p.is_sample=?']
    args = [mode, int(sample)]
    if q:
        columns = ['i.name','i.specification','i.category','i.brand','i.quantity','CAST(i.unit_price/100.0 AS TEXT)','CAST(i.total_price/100.0 AS TEXT)','p.name','p.number','o.name','supplier.name']
        where.append('(' + ' OR '.join(c+' LIKE ?' for c in columns) + ')')
        args.extend(['%' + q + '%'] * len(columns))
    for col, val in [('i.category', category), ('i.brand', brand)]:
        if val:
            where.append(col + ' LIKE ?')
            args.append('%' + val + '%')
    def compare(a,b):
        return None if a is None else (Decimal(str(a)) > Decimal(str(b))) - (Decimal(str(a)) < Decimal(str(b)))
    db.create_function('decimal_compare', 2, compare, deterministic=True)
    for key in ('unit_price','total_price','quantity'):
        for suffix,op in (('_min','>='),('_max','<=')):
            value = (filters or {}).get(key+suffix)
            if value is not None:
                if key == 'quantity':
                    where.append('decimal_compare(i.quantity,?) '+op+' 0')
                    args.append(str(value))
                else:
                    where.append('i.'+key+' '+op+' ?')
                    args.append(int(value*100) if suffix=='_max' else int((value*100).to_integral_value(rounding='ROUND_CEILING')))
    base = ' FROM items i JOIN projects p ON p.id=i.project_id LEFT JOIN organizations o ON o.id=p.buyer_id LEFT JOIN organizations supplier ON supplier.id=i.product_supplier_id LEFT JOIN packages k ON k.id=i.package_id WHERE ' + ' AND '.join(where)
    total = db.execute('SELECT COUNT(*)' + base, args).fetchone()[0]
    rows = query(db, 'SELECT i.*,p.name project_name,p.mode,p.is_sample,o.name buyer,supplier.name product_supplier,k.code package_code,k.name package_name' + base + ' ORDER BY p.id,k.code,i.id LIMIT ? OFFSET ?', args + [page_size, (page - 1) * page_size])
    for row in rows:
        row['evidence'] = json.loads(row['evidence'])
    return {'items': rows, 'total': total, 'page': page, 'page_size': page_size, 'money_unit': '人民币分'}

@app.get('/api/items')
def items(q: str = '', category: str = '', brand: str = '', mode: Mode = 'model', sample: bool = False, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), unit_price_min: Decimal | None = Query(None,ge=0,le=90000000000000), unit_price_max: Decimal | None = Query(None,ge=0,le=90000000000000), total_price_min: Decimal | None = Query(None,ge=0,le=90000000000000), total_price_max: Decimal | None = Query(None,ge=0,le=90000000000000), quantity_min: Decimal | None = Query(None,ge=0,le=90000000000000), quantity_max: Decimal | None = Query(None,ge=0,le=90000000000000)):
    filters = dict(unit_price_min=unit_price_min,unit_price_max=unit_price_max,total_price_min=total_price_min,total_price_max=total_price_max,quantity_min=quantity_min,quantity_max=quantity_max)
    for key in ('unit_price','total_price','quantity'):
        lower,upper=filters[key+'_min'],filters[key+'_max']
        if lower is not None and upper is not None and lower > upper:
            raise HTTPException(422, '筛选下限不能大于上限')
    with connection() as db:
        return item_rows(db, q, category, brand, mode, sample, page, page_size, filters)

@app.get('/api/projects/{pid}')
def project(pid: str):
    with connection() as db:
        row = db.execute('SELECT p.*,o.name buyer FROM projects p LEFT JOIN organizations o ON o.id=p.buyer_id WHERE p.id=?', (pid,)).fetchone()
        if not row:
            raise HTTPException(404, '项目不存在')
        result = dict(row)
        result['warnings'] = json.loads(result['warnings'])
        result['packages'] = query(db, 'SELECT * FROM packages WHERE project_id=?', (pid,))
        result['bids'] = query(db, 'SELECT b.*,o.name supplier,k.code package_code,k.name package_name FROM bids b JOIN organizations o ON o.id=b.supplier_id JOIN packages k ON k.id=b.package_id WHERE b.project_id=? ORDER BY k.code,o.name', (pid,))
        result['items'] = query(db, 'SELECT i.*,o.name product_supplier,k.code package_code,k.name package_name FROM items i LEFT JOIN organizations o ON o.id=i.product_supplier_id JOIN packages k ON k.id=i.package_id WHERE i.project_id=?', (pid,))
        result['sources'] = query(db, 'SELECT d.id,d.name,d.source_uuid,d.source_url,d.sha256 FROM documents d JOIN sources s ON s.document_id=d.id WHERE s.project_id=?', (pid,))
        return result

@app.get('/api/analysis/options')
def analysis_options(mode: Mode = 'model', sample: bool = False, q: str = '', limit: int = Query(100, ge=1, le=200)):
    where = 'p.mode=? AND p.is_sample=?'
    args = [mode, int(sample)]
    with connection() as db:
        stats = dict(db.execute(f'''SELECT COUNT(*) project_count,
          COALESCE(SUM(EXISTS(SELECT 1 FROM bids b WHERE b.project_id=p.id)),0) bid_project_count,
          COALESCE(SUM(EXISTS(SELECT 1 FROM bids b WHERE b.project_id=p.id AND b.result='won')),0) award_project_count
          FROM projects p WHERE {where}''', args).fetchone())
        stats['bid_record_count'] = db.execute(f'SELECT COUNT(*) FROM bids b JOIN projects p ON p.id=b.project_id WHERE {where}', args).fetchone()[0]
        buyers = query(db, f'''SELECT o.id,o.name,COUNT(*) project_count,
          SUM(EXISTS(SELECT 1 FROM bids b WHERE b.project_id=p.id)) bid_project_count,
          SUM(EXISTS(SELECT 1 FROM bids b WHERE b.project_id=p.id AND b.result='won')) award_project_count
          FROM projects p JOIN organizations o ON o.id=p.buyer_id WHERE {where} AND o.name LIKE ?
          GROUP BY o.id ORDER BY award_project_count DESC,bid_project_count DESC,project_count DESC,o.name LIMIT ?''', args + ['%' + q + '%', limit])
        suppliers = query(db, f'''SELECT o.id,o.name,COUNT(DISTINCT b.project_id) project_count,
          COUNT(DISTINCT CASE WHEN b.result='won' THEN b.project_id END) award_project_count
          FROM bids b JOIN projects p ON p.id=b.project_id JOIN organizations o ON o.id=b.supplier_id
          WHERE {where} AND o.name LIKE ? GROUP BY o.id
          ORDER BY award_project_count DESC,project_count DESC,o.name LIMIT ?''', args + ['%' + q + '%', limit])
    return {'mode': mode, 'sample': sample, 'stats': stats, 'buyers': buyers, 'suppliers': suppliers,
            'limit': limit, 'note': '选项来自当前模式及样例范围的真实入库记录；项目存在不等于投标主体已完整抽取。'}

@app.get('/api/analysis/cooperation')
def api_cooperation(buyer: str = Query(min_length=1), mode: Mode = 'model', sample: bool = False):
    return cooperation(buyer, mode, sample)

@app.get('/api/analysis/buyer-bidders')
def api_buyer_bidders(buyer: str = Query(min_length=1), mode: Mode = 'model', sample: bool = False):
    return buyer_bidders(buyer, mode, sample)

@app.get('/api/analysis/supplier-competitors')
def api_competitors(supplier: str = Query(min_length=1), mode: Mode = 'model', sample: bool = False):
    return supplier_competitors(supplier, mode, sample)

@app.post('/api/analysis/common-buyers')
def api_common_buyers(body: SupplierSelection, mode: Mode = 'model', sample: bool = False):
    return common_buyers(body.suppliers, mode, sample)

@app.post('/api/analysis/common-projects')
def api_common_projects(body: SupplierSelection, mode: Mode = 'model', sample: bool = False):
    return common_projects(body.suppliers, mode, sample)

@app.get('/api/graph')
def graph(mode: Mode = 'model', sample: bool = False, project_id: str | None = None, organization: str | None = None, limit: int = Query(200, ge=10, le=1000)):
    return graph_adapter.get_graph(mode, sample, project_id, organization, limit)

@app.get('/api/export/standard.xlsx')
def export_xlsx(mode: Mode = 'model', sample: bool = False, job_id: str | None = None):
    return Response(workbook_bytes(mode, sample, job_id), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="extraction.xlsx"'})

@app.get('/api/export/json')
def export_json(mode: Mode = 'model', sample: bool = False, job_id: str | None = None):
    tables, warnings = standard_rows(mode, sample, job_id)
    from .export import HEADERS
    return {'headers': HEADERS, 'tables': tables, 'warnings': warnings, 'mode': mode, 'sample': sample, 'amount_unit': '万元', 'note': '未知包名留空；灰色类别存在规范冲突，未自动填写'}

@app.get('/api/export/items.csv')
def export_csv(mode: Mode = 'model', sample: bool = False):
    from .export import HEADERS, safe
    tables, _ = standard_rows(mode, sample)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS['标的表'])
    writer.writerows([[safe(v) for v in row] for row in tables['标的表']])
    return Response(buffer.getvalue().encode('utf-8-sig'), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="items.csv"'})

dist = ROOT / 'frontend/dist'
if dist.exists():
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/')
    def index():
        return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-store'})
