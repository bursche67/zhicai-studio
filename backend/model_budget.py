"""Persistent conservative reservations, not a provider billing statement.

Reserve before each HTTP attempt; keep full reservations for unknown usage. UTF-8 bytes
upper-bound text tokens with extra framing allowance. No cache/discount credit.
"""
import json
import os
from decimal import Decimal, ROUND_CEILING
from .db import connection

# Audited 2026-10-18: official Flash CNY peak rates are 2/M input and
# 8/M output. Retain headroom at 5/M and 10/M, ignoring cache/off-peak discounts.
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
RATE_VERSION = 'flash-cny-5-10-v2'
INPUT_RATE = 5
OUTPUT_RATE = 10


class BudgetExceeded(ValueError):
    pass


def _limit():
    raw = os.getenv('MODEL_BUDGET_CNY', '').strip()
    if not raw:
        return None
    value = Decimal(raw)
    if not value.is_finite() or value < 0:
        raise ValueError('MODEL_BUDGET_CNY必须是非负有限金额')
    return int(value * 1_000_000)


def _table(db):
    db.execute('CREATE TABLE IF NOT EXISTS model_budget_reservations(id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, reserved_micros INTEGER NOT NULL)')
    columns = {r[1] for r in db.execute('PRAGMA table_info(model_budget_reservations)')}
    for name, definition in [('original_micros', 'INTEGER'), ('settled', 'INTEGER NOT NULL DEFAULT 0'), ('receipt', 'TEXT'), ('rate_version', "TEXT NOT NULL DEFAULT 'legacy-10-20'")]:
        if name not in columns:
            db.execute(f'ALTER TABLE model_budget_reservations ADD COLUMN {name} {definition}')
    db.execute('UPDATE model_budget_reservations SET original_micros=reserved_micros WHERE original_micros IS NULL')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_model_budget_receipt ON model_budget_reservations(receipt)')


def reprice_known_usage():
    """One-time audited rate correction; never release unknown-usage reserves.

    Legacy settled amounts are exactly 10*prompt + 20*completion micros.
    Halving gives exactly the new 5/10 safety rates without recovering tokens.
    Preserve original reservations for provenance and round upwards.
    """
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _table(db)
        rows = db.execute("SELECT id,reserved_micros FROM model_budget_reservations WHERE settled=1 AND rate_version='legacy-10-20'").fetchall()
        released = 0
        for row in rows:
            amount = (row['reserved_micros'] + 1) // 2
            released += row['reserved_micros'] - amount
            db.execute('UPDATE model_budget_reservations SET reserved_micros=?,rate_version=? WHERE id=?', (amount, RATE_VERSION, row['id']))
    return {'repriced_attempts': len(rows), 'released_cny': released / 1_000_000}


def status():
    limit = _limit()
    if limit is None:
        return {'enabled': False}
    with connection() as db:
        _table(db)
        used, calls = db.execute('SELECT coalesce(sum(reserved_micros),0),count(*) FROM model_budget_reservations').fetchone()
    return {'enabled': True, 'limit_cny': limit / 1_000_000,
            'reserved_cny': used / 1_000_000, 'remaining_cny': max(0, limit-used) / 1_000_000,
            'reserved_attempts': calls, 'rate_version': RATE_VERSION,
            'input_safety_cny_per_million': INPUT_RATE, 'output_safety_cny_per_million': OUTPUT_RATE,
            'note': '系统预算与DeepSeek充值余额独立；有用量按安全费率核算，未知用量保留全额预留；非实际账单'}


def reserve(payload):
    limit = _limit()
    if limit is None:
        return None
    # Intentionally above the verified Flash peak price. Restrict the guard to
    # its audited endpoint/model rather than silently applying to other models.
    if os.getenv('MODEL_BASE_URL', '').rstrip('/') != 'https://api.deepseek.com' or payload['model'] != 'deepseek-flash':
        raise BudgetExceeded('预算保护仅核验了DeepSeek官方deepseek-flash，其他模型需重新核价')
    input_bound = len(json.dumps(payload['messages'], ensure_ascii=False).encode('utf-8')) + 4096
    amount = int((Decimal(input_bound) * INPUT_RATE + Decimal(payload['max_tokens']) * OUTPUT_RATE).to_integral_value(rounding=ROUND_CEILING))
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _table(db)
        used = db.execute('SELECT coalesce(sum(reserved_micros),0) FROM model_budget_reservations').fetchone()[0]
        if used + amount > limit:
            raise BudgetExceeded('模型预算预留额度不足，已在请求前停止；请查看预算状态，不会自动追加费用')
        rid = db.execute('INSERT INTO model_budget_reservations(reserved_micros,original_micros,rate_version) VALUES(?,?,?)', (amount, amount, RATE_VERSION)).lastrowid
    return {'id': rid, 'reserved_cny': amount / 1_000_000}


def settle(reservation, usage, receipt=None):
    """Release only proven unused allowance; retain conservative token rates.

    Legacy trace replay matches one unsettled reservation of the exact amount.
    Receipt uniqueness makes that reconciliation safe to repeat.
    """
    if not reservation or not isinstance(usage, dict):
        return None
    prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
    if any(type(n) is not int or n < 0 for n in (prompt, output)) or prompt + output == 0:
        return None
    amount = prompt * INPUT_RATE + output * OUTPUT_RATE
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        _table(db)
        if receipt and db.execute('SELECT 1 FROM model_budget_reservations WHERE receipt=?', (receipt,)).fetchone():
            return None
        rid = reservation.get('id')
        if rid is None:
            if not receipt:
                return None
            original = int(Decimal(str(reservation['reserved_cny'])) * 1_000_000)
            row = db.execute('SELECT id FROM model_budget_reservations WHERE settled=0 AND original_micros=? ORDER BY id LIMIT 1', (original,)).fetchone()
            if row is None:
                return None
            rid = row[0]
        row = db.execute('SELECT original_micros,settled FROM model_budget_reservations WHERE id=?', (rid,)).fetchone()
        if not row or row['settled'] or amount > row['original_micros']:
            return None
        db.execute('UPDATE model_budget_reservations SET reserved_micros=?,settled=1,receipt=?,rate_version=? WHERE id=?', (amount, receipt, RATE_VERSION, rid))
    return {'accounted_cny': amount / 1_000_000, 'prompt_tokens': prompt, 'completion_tokens': output}
