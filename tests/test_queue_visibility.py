from fastapi.testclient import TestClient
from backend.main import app
from backend.db import connection
from backend.jobs import create_job, process_job


HTML = b'<html><body>queue regression fixture, no model call</body></html>'


def test_duplicate_active_upload_reuses_running_task(database):
    jid = create_job([('a.html', HTML)], 'rule', initial_status='running')
    client = TestClient(app)  # No lifespan worker: keep the running job controlled.
    response = client.post('/api/imports', files={'files': ('a.html', HTML, 'text/html')}, data={'mode': 'rule'})
    assert response.status_code == 202
    assert response.json()['id'] == jid and response.json()['status'] == 'running'
    with connection() as db:
        assert db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1


def test_upload_queue_position_and_current_file(database):
    running = create_job([('a.html', HTML)], 'rule', initial_status='running')
    first = create_job([('b.html', HTML)], 'rule')
    second = create_job([('c.html', HTML)], 'rule')
    with connection() as db:
        db.execute("INSERT INTO documents(id,job_id,name,sha256,status) VALUES('current',?,'a.html','hash','extracting')", (running,))
    client = TestClient(app)
    data = client.get('/api/imports/' + second).json()
    assert data['queue_ahead'] == 2 and data['active_job']['id'] == running
    assert data['active_job']['current_document'] == 'a.html'
    assert data['document_count'] == 0
    assert client.get('/api/imports/' + first).json()['queue_ahead'] == 1


def test_active_identity_includes_mode_sample_mapping_and_content(database):
    original = create_job([('a.html', HTML)], 'rule', deduplicate_active=True)
    assert create_job([('a.html', HTML)], 'rule', deduplicate_active=True) == original
    variants = [
        create_job([('a.html', HTML)], 'model', deduplicate_active=True),
        create_job([('a.html', HTML)], 'rule', True, deduplicate_active=True),
        create_job([('a.html', HTML+b'changed')], 'rule', deduplicate_active=True),
        create_job([('a.html', HTML)], 'rule', association={'groups': []}, deduplicate_active=True),
    ]
    assert len(set([original]+variants)) == 5


def test_finished_task_can_be_resubmitted_and_results_reused(database):
    first = create_job([('a.html', HTML)], 'rule', deduplicate_active=True)
    process_job(first)
    second = create_job([('a.html', HTML)], 'rule', deduplicate_active=True)
    assert first != second
    process_job(second)
    detail = TestClient(app).get('/api/imports/' + second).json()
    assert detail['status'] == 'completed'
    assert detail['document_status_counts'] == {'duplicate': 1}


def test_merged_duplicate_exposes_existing_task_without_deleting_original(database):
    original = create_job([('a.html', HTML)], 'rule', initial_status='running')
    duplicate = create_job([('a.html', HTML)], 'rule')
    import json
    with connection() as db:
        row = db.execute('SELECT manifest FROM jobs WHERE id=?', (duplicate,)).fetchone()
        manifest = json.loads(row[0]); manifest['reused_job_id'] = original
        db.execute("UPDATE jobs SET status='superseded',manifest=? WHERE id=?", (json.dumps(manifest), duplicate))
    detail = TestClient(app).get('/api/imports/' + duplicate).json()
    assert detail['reused_job_id'] == original and len(detail['files']) == 1


def test_retry_reuses_same_active_batch(database):
    active = create_job([('a.html', HTML)], 'rule', initial_status='running')
    failed = create_job([('a.html', HTML)], 'rule')
    with connection() as db:
        db.execute("UPDATE jobs SET status='failed' WHERE id=?", (failed,))
    response = TestClient(app).post('/api/imports/' + failed + '/retry')
    assert response.status_code == 202
    assert response.json() == {'id': active, 'status': 'running', 'reused': True}
    with connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (failed,)).fetchone()[0] == 'failed'
