import json
import pytest
from backend.extraction import validate_model_response
from backend.parsers import parse_document


@pytest.mark.parametrize('score,expected', [('85.00、84.00、85.00', None), ('85', '85'), ('85, 84', None)])
def test_users_are_not_averaged(score, expected):
    text = '投标人甲评分：' + score
    parsed = parse_document('score.html', ('<body>' + text + '</body>').encode())
    b = next(b for b in parsed.blocks if b.locator == 'body')
    payload = {'packages': [{'bids': [{'supplier': '投标人甲', 'score': score,
        'evidence': [{'source': b.source, 'locator': b.locator, 'quote': text}]}]}]}
    result = validate_model_response(parsed, json.dumps(payload))
    value = result.packages[0].bids[0].score
    assert (str(value) if value is not None else None) == expected
    if expected is None:
        assert score in result.warnings[0]


def test_invalid_score_not_silently_accepted():
    with pytest.raises(ValueError):
        validate_model_response(parse_document('s.html', b'<body>x</body>'), json.dumps({'packages': [{'bids': [{'supplier': 'x', 'score': 'made-up'}]}]}))
