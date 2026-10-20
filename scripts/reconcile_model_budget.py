"""Reconcile historical single-attempt traces. ZERO model/network calls."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import storage_path
from backend.model_budget import settle, status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    before = status()
    eligible, settled, skipped = 0, 0, 0
    for path in sorted(storage_path().glob('*/*.trace.json')):
        try:
            trace = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            skipped += 1
            continue
        for i, call in enumerate(trace.get('model_calls', [])):
            reservations = call.get('budget_reservations') or []
            response = call.get('raw_response') or {}
            if len(reservations) != 1 or not reservations[0] or not response.get('usage'):
                continue  # Unknown billing or retry attribution: retain allowance.
            eligible += 1
            receipt = hashlib.sha256((str(path) + ':' + str(i) + ':' + str(response.get('id'))).encode()).hexdigest()
            if args.apply and settle(reservations[0], response['usage'], receipt):
                settled += 1
    print(json.dumps({'apply': args.apply, 'eligible': eligible, 'settled': settled,
                      'unreadable_traces': skipped, 'before': before, 'after': status()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
