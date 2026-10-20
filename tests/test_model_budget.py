import pytest
from backend.model_budget import reserve, settle, status, BudgetExceeded, reprice_known_usage


@pytest.fixture
def budget(database, monkeypatch):
    monkeypatch.setenv('MODEL_BUDGET_CNY', '0.1')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://api.deepseek.com')
    return {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': '测试'}], 'max_tokens': 4096}


def test_reservation_persists_and_stops_before_overspend(budget):
    reserve(budget)
    first = status()
    assert first['reserved_attempts'] == 1
    assert 0 < first['reserved_cny'] < 0.2
    with pytest.raises(BudgetExceeded, match='额度不足'):
        reserve(budget)
    assert status() == first


def test_more_input_reserves_more(database, monkeypatch):
    monkeypatch.setenv('MODEL_BUDGET_CNY', '50')
    monkeypatch.setenv('MODEL_BASE_URL', 'https://api.deepseek.com')
    small = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'a'}], 'max_tokens': 4096}
    large = {**small, 'messages': [{'role': 'user', 'content': '汉' * 12000}]}
    assert reserve(large)['reserved_cny'] > reserve(small)['reserved_cny']


def test_unpriced_model_blocked(budget):
    budget['model'] = 'other-model'
    with pytest.raises(BudgetExceeded, match='重新核价'):
        reserve(budget)
    assert status()['reserved_attempts'] == 0


def test_unconfigured_guard_preserves_local_adapters(database, monkeypatch):
    monkeypatch.delenv('MODEL_BUDGET_CNY', raising=False)
    assert reserve({}) is None
    assert status() == {'enabled': False}


def test_disabling_limit_preserves_ledger_but_no_longer_blocks(budget,monkeypatch):
    from backend.db import connection
    reserve(budget)
    previous=status()
    monkeypatch.setenv('MODEL_BUDGET_CNY','')
    assert status()=={'enabled':False}
    assert reserve({**budget,'max_tokens':10000000}) is None
    with connection() as db:
        assert db.execute('SELECT count(*) FROM model_budget_reservations').fetchone()[0]==1
    monkeypatch.setenv('MODEL_BUDGET_CNY','0.1')
    assert status()==previous


def test_settle_real_usage_once(budget):
    r = reserve(budget)
    settle(r, {'prompt_tokens': 2000, 'completion_tokens': 1000})
    assert status()['reserved_cny'] == 0.02
    assert settle(r, {'prompt_tokens': 1, 'completion_tokens': 1}) is None
    assert status()['reserved_cny'] == 0.02
    reserve(budget)  # Proven unused allowance is available again.


def test_missing_or_invalid_usage_keeps_full_reservation(budget):
    r = reserve(budget)
    before = status()
    for usage in [None, {}, {'prompt_tokens': -1, 'completion_tokens': 1}, {'prompt_tokens': True, 'completion_tokens': 0}, {'prompt_tokens': 999999, 'completion_tokens': 999999}]:
        assert settle(r, usage) is None
    assert status() == before


def test_legacy_reconciliation_is_idempotent(budget):
    r = reserve(budget)
    legacy = {'reserved_cny': r['reserved_cny']}
    assert settle(legacy, {'prompt_tokens': 100, 'completion_tokens': 100}, 'trace-1')
    reserve(budget)
    before = status()
    assert settle(legacy, {'prompt_tokens': 100, 'completion_tokens': 100}, 'trace-1') is None
    assert status() == before


def test_reprice_only_proven_legacy_usage_once(budget):
    from backend.db import connection
    with connection() as db:
        # Simulate a genuinely old table, before the rate-version column existed.
        db.execute('CREATE TABLE model_budget_reservations(id INTEGER PRIMARY KEY, created_at TEXT, reserved_micros INTEGER NOT NULL, original_micros INTEGER, settled INTEGER NOT NULL DEFAULT 0, receipt TEXT)')
        db.execute('INSERT INTO model_budget_reservations(reserved_micros,original_micros,settled) VALUES(40000,150000,1)')
        db.execute('INSERT INTO model_budget_reservations(reserved_micros,original_micros,settled) VALUES(150000,150000,0)')
    result = reprice_known_usage()
    assert result == {'repriced_attempts': 1, 'released_cny': 0.02}
    assert status()['reserved_cny'] == 0.17
    assert status()['reserved_attempts'] == 2
    assert reprice_known_usage()['repriced_attempts'] == 0
    with connection() as db:
        assert [r[0] for r in db.execute('SELECT original_micros FROM model_budget_reservations ORDER BY id')] == [150000,150000]


def test_reprice_does_not_halve_current_settlements(budget):
    r = reserve(budget)
    settle(r, {'prompt_tokens': 2000, 'completion_tokens': 1000})
    before = status()
    assert reprice_known_usage()['repriced_attempts'] == 0
    assert status() == before
