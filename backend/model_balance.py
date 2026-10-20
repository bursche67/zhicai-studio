"""Read DeepSeek's official account balance. No model generation or fee estimate.

Credentials stay server-side. A short cache prevents polling stampedes; redirect
following is disabled so credentials never leave the explicitly allowed origin.
Balance lookup is informational and never gates extraction.
"""
import copy
import hashlib
import os
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
import httpx

_lock = threading.Lock()
_cache = {}
SUCCESS_TTL = 60
ERROR_TTL = 10
REFRESH_MIN_INTERVAL = 5


def _empty(status, message):
    return {'provider': 'deepseek', 'status': status, 'is_available': None,
            'balance_infos': [], 'checked_at': None, 'stale': False,
            'error': message, 'note': 'DeepSeek官方账户金额余额，不是剩余token或本系统预留估算。'}


def _parse(payload):
    if not isinstance(payload, dict) or type(payload.get('is_available')) is not bool:
        raise ValueError('invalid balance response')
    rows = payload.get('balance_infos')
    if not isinstance(rows, list) or not rows:
        raise ValueError('missing balance rows')
    clean = []
    for row in rows:
        if not isinstance(row, dict) or row.get('currency') not in ('CNY', 'USD'):
            raise ValueError('invalid currency')
        out = {'currency': row['currency']}
        for field in ('total_balance', 'granted_balance', 'topped_up_balance'):
            value = row.get(field)
            if not isinstance(value, str):
                raise ValueError('invalid amount')
            amount = Decimal(value)
            if not amount.is_finite() or amount < 0:
                raise ValueError('invalid amount')
            out[field] = format(amount, 'f')
        clean.append(out)
    return {'provider': 'deepseek', 'status': 'ok', 'is_available': payload['is_available'],
            'balance_infos': clean, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'stale': False, 'error': None,
            'note': 'DeepSeek官方账户金额余额，不是剩余token或本系统预留估算。'}


def balance_status(refresh=False):
    base = urlparse(os.getenv('MODEL_BASE_URL', '').strip().rstrip('/'))
    # Exact official origin only, including its documented /v1 compatibility path.
    if (base.scheme != 'https' or base.netloc != 'api.deepseek.com'
            or base.path not in ('', '/v1') or base.query or base.fragment):
        return _empty('unsupported', '当前不是DeepSeek官方接口，无法读取官方账户余额。')
    key = os.getenv('MODEL_API_KEY', '').strip()
    if not key:
        return _empty('not_configured', '未配置DeepSeek API密钥，无法查询余额。')
    if os.getenv('MODEL_ALLOW_REMOTE', 'false').lower() != 'true':
        return _empty('remote_disabled', '远端模型访问未启用，未查询账户余额。')
    identity = hashlib.sha256(key.encode()).hexdigest()
    with _lock:
        now = time.monotonic()
        if _cache.get('identity') != identity:
            _cache.clear()
        age = now - _cache.get('attempted_at', -float('inf'))
        ttl = SUCCESS_TTL if _cache.get('data', {}).get('status') == 'ok' else ERROR_TTL
        if 'data' in _cache and age < (REFRESH_MIN_INTERVAL if refresh else ttl):
            return copy.deepcopy(_cache['data'])
        try:
            with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
                response = client.get('https://api.deepseek.com/user/balance',
                                      headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'})
            if response.status_code != 200:
                messages = {401: 'DeepSeek密钥认证失败，请检查服务器配置。',
                            403: 'DeepSeek余额接口拒绝访问。',
                            429: 'DeepSeek余额查询繁忙，请稍后刷新。'}
                # Never expose provider response bodies or exceptions containing credentials.
                raise LookupError(messages.get(response.status_code, 'DeepSeek余额服务暂不可用，请稍后刷新。'))
            result = _parse(response.json())
            _cache['last_good'] = copy.deepcopy(result)
        except httpx.TimeoutException:
            result = _empty('unavailable', 'DeepSeek余额查询超时，当前余额未知。')
        except httpx.RequestError:
            result = _empty('unavailable', '无法连接DeepSeek余额服务，当前余额未知。')
        except LookupError as exc:
            result = _empty('unavailable', str(exc))
        except (ValueError, InvalidOperation, TypeError):
            result = _empty('unavailable', 'DeepSeek余额返回格式异常，当前余额未知。')
        if result['status'] != 'ok' and _cache.get('last_good'):
            result['balance_infos'] = copy.deepcopy(_cache['last_good']['balance_infos'])
            result['checked_at'] = _cache['last_good']['checked_at']
            result['stale'] = True
        _cache.update(identity=identity, attempted_at=time.monotonic(), data=copy.deepcopy(result))
        return result
