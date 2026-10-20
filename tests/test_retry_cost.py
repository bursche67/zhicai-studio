from backend.jobs import create_job, process_job
from backend.db import connection
from backend.models import Extraction


def test_retry_does_not_rebill_successful_documents(database, monkeypatch):
    calls = []
    fail = [True]

    def fake_extract(parsed, trace):
        text = '\n'.join(b.text for b in parsed.blocks)
        calls.append(text)
        if 'second' in text and fail[0]:
            raise ValueError('simulated provider failure')
        return Extraction(project_name=text)

    monkeypatch.setattr('backend.jobs.model_unavailable_reason', lambda: None)
    monkeypatch.setattr('backend.jobs.model_extract', fake_extract)
    jid = create_job([('a.html', b'<body>first</body>'), ('b.html', b'<body>second</body>')], 'model')
    process_job(jid)
    assert len(calls) == 2
    fail[0] = False
    process_job(jid)
    assert len(calls) == 3
    assert 'second' in calls[-1]
    with connection() as db:
        assert db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'completed'
        assert db.execute('SELECT count(*) FROM projects').fetchone()[0] == 2
    process_job(jid)
    assert len(calls) == 3


def test_restart_marks_inflight_document_retryable(database):
    from backend.jobs import Worker
    jid = create_job([('a.html', b'<body>x</body>')], 'rule', initial_status='running')
    with connection() as db:
        db.execute('INSERT INTO documents(id,job_id,name,sha256,status) VALUES(?,?,?,?,?)', ('interrupted-doc', jid, 'a.html', 'hash', 'extracting'))
    worker = Worker()
    worker.start()
    worker.close()
    with connection() as db:
        assert db.execute('SELECT status FROM documents WHERE id=?', ('interrupted-doc',)).fetchone()[0] == 'failed'
        assert db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'interrupted'
