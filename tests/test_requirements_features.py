"""Self-authored regression fixtures; no official accuracy claims or model calls."""
import json
from decimal import Decimal
from fastapi.testclient import TestClient
from backend.main import app
from backend.db import connection
from backend.jobs import create_job, process_job
from backend.export import standard_rows
from backend.models import Extraction, Package, Item, Bid
from test_core import HTML, insert, relation_data


def test_all_seven_fields_and_exact_decimal_ranges(database):
    out=Extraction(project_name='筛选自建样例',packages=[Package(code='1',items=[
        Item(name='alpha',category='仪器',brand='示例牌',specification='SPEC-X',unit_price='10.01',quantity='1.0000000000000001',total_price='20.02',product_supplier='显式供货商'),
        Item(name='unknown')])])
    with connection() as db: insert(db,'filter-source',out)
    client=TestClient(app);scope={'mode':'model','sample':True}
    for value in ['alpha','仪器','示例牌','SPEC-X','10.01','20.02','1.0000000000000001','显式供货商']:
        data=client.get('/api/items',params={**scope,'q':value}).json()
        assert data['total']==1, value
    params={**scope,'unit_price_min':'10.005','unit_price_max':'10.015','quantity_min':'1.0000000000000001','quantity_max':'1.0000000000000001'}
    assert client.get('/api/items',params=params).json()['total']==1
    assert client.get('/api/items',params={**scope,'quantity_min':'1.0000000000000002'}).json()['total']==0
    assert client.get('/api/items',params={**scope,'total_price_max':'20.019'}).json()['total']==0
    for p in [{'unit_price_min':'2','unit_price_max':'1'},{'quantity_min':'NaN'},{'total_price_min':'-1'}]:
        assert client.get('/api/items',params={**scope,**p}).status_code==422


def test_quality_reports_template_blue_columns_without_inventing_precision(database):
    with connection() as db:
        insert(db,'quality-source',Extraction(packages=[Package(code='1',items=[Item(name='自建小数数量',unit_price=10,quantity='1.5')])]))
    data=TestClient(app).get('/api/quality?mode=model&sample=true').json()
    assert data['accuracy'] is None and data['precision'] is None
    assert data['seven_fields']['total_price']['non_null']==0  # Export multiplication isn't original disclosure.
    assert data['missing_url_notices']==1
    assert data['nonconforming_quantity_rows'][0]['quantity']==1.5
    assert '得分' not in data['blue_field_missing_counts']['投标人表']  # Gray in actual template.
    assert '通过审查' in data['blue_field_missing_counts']['投标人表']


def test_source_mapping_scoped_persistent_and_conflict_transaction(database):
    raw=HTML.replace(b'<link rel="canonical" href="https://www.ccgp.gov.cn/test/t20260101_123.html">',b'')
    jid=create_job([('t20260101_123.html',raw)],'rule',True);process_job(jid)
    client=TestClient(app);url='https://www.ccgp.gov.cn/declared-source.html'
    def post(entries,**kwargs):
        return client.post('/api/sources/map?mode=rule&sample=true',json={'entries':entries,**kwargs})
    r=post([{'uuid':'t20260101_123','url':url}]);assert r.status_code==200
    assert standard_rows('rule',True)[0]['总表'][0][1]==url
    assert post([{'uuid':'future','url':'https://example.com/future'},{'uuid':'t20260101_123','url':'https://example.com/conflict'}]).status_code==409
    with connection() as db:
        assert db.execute("SELECT count(*) FROM source_url_mappings WHERE source_uuid='future'").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM documents WHERE job_id=? AND source_url=?",(jid,url)).fetchone()[0]==1
    assert post([{'uuid':'later','url':'https://example.com/later'}]).status_code==200
    later=create_job([('later.html',raw)],'rule',True);process_job(later)
    assert standard_rows('rule',True,later)[0]['总表'][0][1]=='https://example.com/later'
    from pathlib import Path
    assert next((database/'uploads'/jid).glob('*.bin')).read_bytes()==raw
    for value in ['javascript:alert(1)','https://user:password@example.com','file:///tmp/data']:
        assert post([{'uuid':'unsafe','url':value}]).status_code==422


def test_project_shows_package_roles_and_score_fields(database):
    out=Extraction(packages=[Package(code='包1',name='一号包',bids=[Bid(supplier='甲',result='won',score=92,bid_price=20,review_status='通过')],items=[Item(name='设备',brand='品牌',product_supplier='供货企业')])])
    with connection() as db: pid=insert(db,'roles',out)
    result=TestClient(app).get('/api/projects/'+pid).json()
    assert result['bids'][0]['package_code']=='包1' and result['bids'][0]['score']=='92'
    assert result['items'][0]['product_supplier']=='供货企业'


def test_browser_evaluation_rejects_unreviewed_and_wrong_dataset(database):
    jid=create_job([('t20260101_123.html',HTML)],'rule',True);process_job(jid)
    with connection() as db:
        d=db.execute('SELECT extraction FROM documents WHERE job_id=?',(jid,)).fetchone()
        manifest=json.loads(db.execute('SELECT manifest FROM jobs WHERE id=?',(jid,)).fetchone()[0])
    out=Extraction.model_validate_json(d[0]);p=out.packages[0];i=p.items[0]
    from backend.evaluation import FIELDS
    gold={'reviewed':True,'dataset_sha256':manifest['files'][0]['sha256'],'evaluation_uuids':['t20260101_123'],'items':[{'uuid':'t20260101_123','package':p.name or p.code,**{f:str(getattr(i,f)) if getattr(i,f) is not None else None for f in FIELDS}}]}
    client=TestClient(app)
    def run(value):
        return client.post('/api/evaluation',files={'gold':('gold.json',json.dumps(value).encode(),'application/json')},data={'mode':'rule','sample':'true'})
    result=run(gold);assert result.status_code==200,result.text
    assert result.json()['overall']['composite']==1 and result.json()['reference_score'] is False
    assert run({**gold,'reviewed':False}).status_code==422
    assert run({**gold,'dataset_sha256':'different'}).status_code==422
    assert run({**gold,'items':[]}).status_code==422
    quality=client.get('/api/imports/'+jid+'/quality').json()
    assert quality['performance']['reference_eligible'] is False
    assert client.get('/api/capabilities').json()['pdf_text'] is True


def test_budget_and_award_amounts_are_independent_in_scenarios(relation_data):
    client=TestClient(app)
    r=client.post('/api/analysis/common-projects?mode=model&sample=true',json={'suppliers':['甲','乙']}).json()
    assert r['known_amount_cents']==60000 and r['known_award_amount_cents']==31000
    other=client.get('/api/analysis/supplier-competitors?mode=model&sample=true&supplier=甲').json()
    assert next(x for x in other['all'] if x['supplier']=='乙')['known_award_amount_cents']==31000


def test_package_graph_retains_different_bid_results(relation_data):
    data=TestClient(app).get('/api/graph?mode=model&sample=true&limit=200').json()
    nodes={n['id']:n for n in data['nodes']}
    packages={n['id'] for n in data['nodes'] if n['category']=='采购包'}
    assert packages
    assert any(e['relation']=='包含采购包' and e['target'] in packages for e in data['links'])
    assert any(e['relation']=='中标' and e['target'] in packages for e in data['links'])
    assert any(e['relation']=='投标' and e['target'] in packages for e in data['links'])
    assert all(e['source'] in nodes and e['target'] in nodes for e in data['links'])
