import pytest
from pathlib import Path
from backend.db import init_db

def pytest_configure():
    Path('tmp').mkdir(exist_ok=True)

@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB', str(tmp_path / 'test.db'))
    monkeypatch.setenv('APP_STORAGE', str(tmp_path / 'uploads'))
    monkeypatch.delenv('MODEL_NAME', raising=False)
    init_db()
    return tmp_path
