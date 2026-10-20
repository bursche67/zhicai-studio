import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from backend import model_balance as balance
from backend.main import app


@pytest.fixture
def account(monkeypatch):
    monkeypatch.setenv('MODEL_BASE_URL', 'https://api.deepseek.com')
    monkeypatch.setenv('MODEL_API_KEY', 'test-only-balance-credential')
    monkeypatch.setenv('MODEL_ALLOW_REMOTE', 'true')
    monkeypatch.delenv('MODEL_BUDGET_CNY', raising=False)
    balance._cache.clear()
    yield
    balance._cache.clear()


def use_transport(monkeypatch, handler):
    original = httpx.Client
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(balance, 'httpx', SimpleNamespace(Client=client,
        TimeoutException=httpx.TimeoutException, RequestError=httpx.RequestError))


def payload():
    return {'is_available': True, 'balance_infos': [{'currency': 'CNY',
        'total_balance': '47.0123', 'granted_balance': '0.00', 'topped_up_balance': '47.0123'}]}


def test_official_balance_is_separate_from_internal_budget_and_cached(account, monkeypatch):
    calls=[]
    def handler(request):
        assert str(request.url)=='https://api.deepseek.com/user/balance'
        assert request.headers['authorization']=='Bearer test-only-balance-credential'
        calls.append(request)
        return httpx.Response(200,json=payload())
    use_transport(monkeypatch, handler)
    first=balance.balance_status()
    assert first['balance_infos'][0]['total_balance']=='47.0123'
    assert first['is_available'] is True and first['stale'] is False
    assert balance.balance_status(refresh=True)==first
    assert len(calls)==1
    assert 'test-only-balance-credential' not in json.dumps(first)


def test_failure_is_unknown_not_zero_and_does_not_disclose_provider_body(account,monkeypatch):
    use_transport(monkeypatch,lambda r:httpx.Response(401,text='test-only-balance-credential'))
    result=balance.balance_status()
    assert result['status']=='unavailable' and result['is_available'] is None
    assert result['balance_infos']==[] and result['checked_at'] is None
    assert 'test-only-balance-credential' not in json.dumps(result)


def test_timeout_retains_clearly_marked_old_balance_and_key_change_drops_it(account,monkeypatch):
    clock=[100.0];monkeypatch.setattr(balance.time,'monotonic',lambda:clock[0])
    use_transport(monkeypatch,lambda r:httpx.Response(200,json=payload()))
    first=balance.balance_status()
    clock[0]+=61
    def timeout(request):raise httpx.ReadTimeout('do not disclose test-only-balance-credential',request=request)
    use_transport(monkeypatch,timeout)
    stale=balance.balance_status()
    assert stale['status']=='unavailable' and stale['stale'] is True
    assert stale['checked_at']==first['checked_at'] and stale['balance_infos']==first['balance_infos']
    assert stale['is_available'] is None and 'test-only-balance-credential' not in json.dumps(stale)
    monkeypatch.setenv('MODEL_API_KEY','another-test-credential')
    assert balance.balance_status()['balance_infos']==[]


@pytest.mark.parametrize('base',['https://api.deepseek.com.evil','http://api.deepseek.com',
    'https://api.deepseek.com@other.example','https://api.deepseek.com/v1?key=bad'])
def test_untrusted_endpoint_never_receives_key(account,monkeypatch,base):
    monkeypatch.setenv('MODEL_BASE_URL',base)
    use_transport(monkeypatch,lambda r:pytest.fail('must not send credential'))
    assert balance.balance_status()['status']=='unsupported'


@pytest.mark.parametrize('amount',['NaN','-1','not-a-number'])
def test_invalid_amount_is_unknown(account,monkeypatch,amount):
    p=payload();p['balance_infos'][0]['total_balance']=amount
    use_transport(monkeypatch,lambda r:httpx.Response(200,json=p))
    assert balance.balance_status()['balance_infos']==[]


def test_api_is_private_and_reports_disabled_limit(account,database,monkeypatch):
    use_transport(monkeypatch,lambda r:httpx.Response(200,json=payload()))
    with TestClient(app) as client:
        r=client.get('/api/model-balance')
        assert r.status_code==200 and r.headers['cache-control']=='no-store, private'
        assert r.json()['internal_budget']=={'enabled':False}
        monkeypatch.setenv('APP_ACCESS_USER','user')
        monkeypatch.setenv('APP_ACCESS_PASSWORD','test-only-strong-password')
        assert client.get('/api/model-balance').status_code==401


def test_missing_key_or_remote_permission_never_looks_like_zero(account,monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY','')
    assert balance.balance_status()['status']=='not_configured'
    monkeypatch.setenv('MODEL_API_KEY','test-only-balance-credential')
    monkeypatch.setenv('MODEL_ALLOW_REMOTE','false')
    assert balance.balance_status()['status']=='remote_disabled'
