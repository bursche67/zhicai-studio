from fastapi.testclient import TestClient
from backend.main import app
from backend.jobs import process_job
from test_core import HTML, relation_data

def test_api_end_to_end(database):
    # Explicit processing avoids timing dependence on daemon-worker scheduling.
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==200
        upload=client.post('/api/imports',files={'files':('sample.html',HTML,'text/html')},data={'mode':'rule','sample':'true'})
        assert upload.status_code==202
        jid=upload.json()['id']
        import time
        for _ in range(100):
            job=client.get('/api/imports/'+jid).json()
            if job['status'] not in ('queued','running'):break
            time.sleep(.03)
        assert job['status']=='completed'
        data=client.get('/api/items?mode=rule&sample=true').json()
        assert data['total']==1
        pid=data['items'][0]['project_id']
        assert client.get('/api/projects/'+pid).json()['sources'][0]['source_uuid']=='sample'
        assert client.get('/api/items?mode=rule&sample=true&brand=不存在').json()['total']==0
        assert client.get('/api/export/standard.xlsx?mode=rule&sample=true').content[:2]==b'PK'
        assert client.get('/api/export/items.csv?mode=rule&sample=true').status_code==200
        assert client.get('/api/analysis/cooperation',params={'buyer':'自建测试大学','mode':'rule','sample':True}).json()['winners']
        for route in ('common-buyers','common-projects'):
            assert client.post('/api/analysis/'+route+'?mode=rule&sample=true',json={'suppliers':['样例甲','样例乙']}).status_code==200
            assert client.post('/api/analysis/'+route,json={'suppliers':['甲','甲']}).status_code==422
        assert client.get('/api/analysis/buyer-bidders',params={'buyer':'自建测试大学','mode':'rule','sample':True}).status_code==200
        assert client.get('/api/analysis/supplier-competitors',params={'supplier':'样例甲','mode':'rule','sample':True}).status_code==200
        assert client.get('/api/graph?mode=rule&sample=true').json()['nodes']
        assert client.get('/api/items?page=0').status_code==422
        assert client.get('/api/imports/missing').status_code==404

def test_model_not_ready_rejects_before_creating_job(database, monkeypatch):
    from backend.db import connection
    from backend.jobs import create_job
    with TestClient(app) as client:
        assert client.get('/api/health').json()['model_processing_available'] is False
        with connection() as db: before=db.execute('select count(*) from jobs').fetchone()[0]
        response=client.post('/api/imports',files={'files':('sample.html',HTML,'text/html')},data={'mode':'model'})
        assert response.status_code==503 and '任务未创建' in response.json()['detail']
        with connection() as db: assert db.execute('select count(*) from jobs').fetchone()[0]==before
        jid=create_job([('sample.html',HTML)],'model',initial_status='running')
        with connection() as db: db.execute("update jobs set status='failed' where id=?",(jid,))
        assert client.post('/api/imports/'+jid+'/retry').status_code==503
        with connection() as db: assert db.execute('select status from jobs where id=?',(jid,)).fetchone()[0]=='failed'
        monkeypatch.setenv('MODEL_NAME','test-model');monkeypatch.setenv('MODEL_BASE_URL','https://model.example/v1');monkeypatch.setenv('MODEL_ALLOW_REMOTE','false')
        assert client.post('/api/imports',files={'files':('sample.html',HTML,'text/html')},data={'mode':'model'}).status_code==503

def test_worker_model_config_preflight_stops_whole_batch(database):
    from backend.db import connection
    from backend.jobs import create_job
    jid=create_job([('sample.html',HTML)],'model')
    process_job(jid)
    with connection() as db:
        row=db.execute('select status,error from jobs where id=?',(jid,)).fetchone()
        assert row['status']=='failed' and '整批任务未执行' in row['error']
        assert db.execute('select count(*) from documents where job_id=?',(jid,)).fetchone()[0]==0

def test_analysis_choices_are_scoped_and_counts_do_not_multiply(relation_data):
    with TestClient(app) as client:
        result=client.get('/api/analysis/options?mode=model&sample=true').json()
        assert result['stats']['project_count']==4
        assert result['stats']['bid_project_count']==4 and result['stats']['award_project_count']==4
        buyers={r['name']:r for r in result['buyers']}
        assert buyers['采购方一']['project_count']==2
        suppliers={r['name']:r for r in result['suppliers']}
        assert suppliers['甲']['project_count']==4 and suppliers['甲']['award_project_count']==3
        assert suppliers['丙']['award_project_count']==0
        assert client.get('/api/analysis/options?mode=rule&sample=true').json()['stats']['project_count']==0
        filtered=client.get('/api/analysis/options',params={'mode':'model','sample':True,'q':'采购方二'}).json()
        assert [r['name'] for r in filtered['buyers']]==['采购方二']
