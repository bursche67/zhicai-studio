import zipfile
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from scripts.package_release import package

def test_public_mode_requires_credentials(database,monkeypatch):
    monkeypatch.setenv('APP_PUBLIC_MODE','true')
    monkeypatch.delenv('APP_ACCESS_USER',raising=False);monkeypatch.delenv('APP_ACCESS_PASSWORD',raising=False)
    with pytest.raises(RuntimeError,match='公网模式'):
        with TestClient(app):pass

def test_user_access(database,monkeypatch):
    monkeypatch.setenv('APP_PUBLIC_MODE','true');monkeypatch.setenv('APP_ACCESS_USER','user');monkeypatch.setenv('APP_ACCESS_PASSWORD','test-only-strong-password')
    with TestClient(app) as client:
        assert client.get('/api/health').status_code==401
        assert client.get('/api/health',auth=('user','wrong')).status_code==401
        assert client.get('/api/health',auth=('user','test-only-strong-password')).status_code==200
        assert client.post('/api/imports').status_code==401
        assert client.get('/api/export/standard.xlsx').status_code==401

def test_release_package_excludes_secrets(database):
    target=package(database/'source.zip')
    with zipfile.ZipFile(target) as z:
        names=z.namelist()
        assert '.env' not in names and '.env.example' in names
        assert 'compose.yaml' in names and 'Dockerfile' in names
        assert not any(n.startswith(('data/','data\\','.venv/','.venv\\')) for n in names)

def test_tunnel_credentials_never_enter_source_archive(database,monkeypatch):
    from scripts import package_release
    monkeypatch.setattr(package_release,'ROOT',database)
    private=database/'deploy/private'; private.mkdir(parents=True)
    (private/'tunnel_token').write_text('test-only-private-token',encoding='utf-8')
    proxy=database/'deploy/cloudflare'; proxy.mkdir()
    (proxy/'wrangler.jsonc').write_text('{"privateConfig":true}',encoding='utf-8')
    (proxy/'wrangler.example.jsonc').write_text('{}',encoding='utf-8')
    (proxy/'proxy.mjs').write_text('export default {}',encoding='utf-8')
    with zipfile.ZipFile(package_release.package(database/'source.zip')) as archive:
        names=[n.replace('\\','/') for n in archive.namelist()]
        assert 'deploy/cloudflare/proxy.mjs' in names
        assert 'deploy/cloudflare/wrangler.example.jsonc' in names
        assert not any('/private/' in n or n.endswith('/wrangler.jsonc') for n in names)
        assert all(b'test-only-private-token' not in archive.read(n) for n in archive.namelist())
