import json
import pytest
from backend.extraction import validate_model_response, model_error_message
from backend.parsers import parse_document


def response(item=None, bid=None):
    parsed = parse_document('a.html', '<body>采购1批，供应商甲报价6.5%。</body>'.encode())
    ev = {'source': 'a.html', 'locator': 'body', 'quote': '采购1批'}
    package = {}
    if item is not None: package['items'] = [{**item, 'evidence': [ev]}]
    if bid is not None: package['bids'] = [{**bid, 'evidence': [ev]}]
    return validate_model_response(parsed, json.dumps({'packages': [package]}))


@pytest.mark.parametrize('raw,number,unit', [('1338人', '1338', '人'), ('1批', '1', '批'), ('2.5平方米', '2.5', '平方米')])
def test_quantity_unit_separation(raw, number, unit):
    item = response(item={'quantity': raw}).packages[0].items[0]
    assert str(item.quantity) == number and item.unit == unit


def test_unknown_numbers_and_rate_not_money():
    r = response(item={'quantity': '详见附件', 'unit_price': '详见附件'}, bid={'supplier': '甲', 'bid_price': '6.5%'})
    assert r.packages[0].items[0].quantity is None
    assert r.packages[0].items[0].unit_price is None
    assert r.packages[0].bids[0].bid_price is None
    assert any('6.5%' in w for w in r.warnings)


def test_quantity_conflict_and_ranges_still_rejected():
    for item in [{'quantity': '2台', 'unit': '套'}, {'quantity': '1至3台'}, {'quantity': '1万台'}]:
        with pytest.raises(ValueError): response(item=item)


def test_whitespace_only_evidence_is_saved_as_exact_source_span():
    parsed = parse_document('a.html', '<body>型号：<p>A 123</p>数量：2</body>'.encode())
    ev = {'source': 'a.html', 'locator': 'body', 'quote': '型号：A123'}
    r = validate_model_response(parsed, json.dumps({'evidence': [ev]}))
    text = next(b.text for b in parsed.blocks if b.locator == 'body')
    assert r.evidence[0].quote in text
    ev['quote'] = '型号：B123'
    with pytest.raises(ValueError): validate_model_response(parsed, json.dumps({'evidence': [ev]}))
    ev['quote'] = ''
    with pytest.raises(ValueError): validate_model_response(parsed, json.dumps({'evidence': [ev]}))


def test_json_failure_has_actionable_message():
    try: json.loads('{broken')
    except json.JSONDecodeError as exc:
        assert '合法JSON' in model_error_message(exc)


@pytest.mark.parametrize('raw', ['投标下浮率（%）：10', '5.00元人民币/考生'])
def test_rate_and_unit_bid_are_not_total_amounts(raw):
    r = response(bid={'supplier': '甲', 'bid_price': raw})
    assert r.packages[0].bids[0].bid_price is None
    assert any(raw in w for w in r.warnings)


def test_thousands_and_literal_one():
    assert str(response(item={'quantity': '2,662.0000'}).packages[0].items[0].quantity) == '2662.0000'
    i = response(item={'quantity': '一批'}).packages[0].items[0]
    assert i.quantity == 1 and i.unit == '批'
