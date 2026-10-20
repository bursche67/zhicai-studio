import json
import httpx
import pytest
from backend.extraction import exact_quote, model_chunks, evidence_input, model_extract, validate_model_response
from backend.parsers import Block, Parsed, parse_document
from scripts.recover_model_responses import revalidate_trace


def test_whitespace_is_only_allowed_evidence_difference():
    text = '供应商甲\u00a0\u3000\n87.60\r\n4'
    assert exact_quote(text, '供应商甲 87.60 4') == text
    for quote in ('供应商甲87.6044', '供应商乙87.604', '供应商甲87.604。', ''):
        assert exact_quote(text, quote) is None


def test_evidence_cannot_cite_unseen_chunk_or_join_disjoint_spans():
    p = Parsed(blocks=[Block('a', 'body', '甲\n乙\n丙')])
    def content(quote):
        return json.dumps({'evidence': [{'source': 'a', 'locator': 'body', 'quote': quote}]})
    blocks = [{'source': 'a', 'locator': 'body', 'text': '甲'},
              {'source': 'a', 'locator': 'body', 'text': '丙'}]
    assert validate_model_response(p, content('甲'), blocks).evidence[0].quote == '甲'
    for quote in ('乙', '甲丙'):
        with pytest.raises(ValueError, match='输入分块'):
            validate_model_response(p, content(quote), blocks)


def test_table_row_chunks_preserve_all_text_and_real_context():
    rows = [['名称', '数量', '金额']] + [[f'商品{i}', '2', '10'] for i in range(120)]
    table = '\n'.join('\n'.join(row) for row in rows)
    text = '一、项目编号：P1\n合同包1(设备):\n' + table + '\n结束'
    p = Parsed(blocks=[Block('a', 'body', text)], tables=[('a', 'table:1', rows)])
    inputs = list(model_chunks(p, 500))
    assert len(inputs) > 1
    assert ''.join(c[-1]['text'] for c in inputs) == text
    for chunk in inputs:
        assert len(chunk[-1]['text']) <= 500
        assert all(b['text'] in text for b in chunk)
    # All table rows stay intact in some main chunk (not merely context).
    for row in rows:
        assert any('\n'.join(row) in c[-1]['text'] for c in inputs)
    assert any(b.get('context_only') and b['text'] == '名称\n数量\n金额'
               for c in inputs[1:] for b in c)
    trace = {'model_calls': [{'input': c, 'chunk_strategy': 'table-rows-v1', 'chunk_chars': 500,
              'raw_response': {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}}
              for c in inputs]}
    assert revalidate_trace(p, trace).packages == []
    trace['model_calls'].pop()
    with pytest.raises(ValueError, match='全部原文分块'):
        revalidate_trace(p, trace)


@pytest.mark.parametrize('finish', ['length', 'content_filter', None])
def test_non_stop_responses_rejected_and_length_not_retried(monkeypatch, finish):
    import backend.extraction as module
    monkeypatch.setenv('MODEL_BASE_URL', 'http://localhost:8001/v1')
    monkeypatch.setenv('MODEL_NAME', 'mock')
    monkeypatch.setenv('MODEL_RETRIES', '0' if finish != 'length' else '2')
    monkeypatch.setenv('MODEL_MAX_TOKENS', '8192')
    monkeypatch.setattr(module, 'reserve', lambda _: None)
    monkeypatch.setattr(module, 'settle', lambda *_: None)
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs['json'])
        assert kwargs['json']['max_tokens'] == 8192
        return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': {'content': '{}'}}]})
    monkeypatch.setattr(httpx.Client, 'post', post)
    trace = []
    with pytest.raises(ValueError, match='未入库'):
        model_extract(parse_document('a.html', b'<body>test</body>'), trace)
    assert len(calls) == 1
    assert trace[0]['chunk_chars'] == 2048


@pytest.mark.parametrize('content', [None, '', '   '])
def test_empty_json_content_rejected(content):
    with pytest.raises(ValueError, match='空内容'):
        validate_model_response(Parsed(), content)


def test_reference_evidence_uses_exact_row_and_rejects_forgery():
    rows = [['供应商', '得分排名', '推荐排名', '综合得分'], ['供应商甲', '4', '', '87.60'], ['供应商乙', '5', '', '73.85']]
    text = '\n'.join('\n'.join(cell for cell in row if cell) for row in rows)
    parsed = Parsed(blocks=[Block('a', 'body', text)], tables=[('a', 'table:1', rows)])
    chunk = evidence_input(parsed, list(model_chunks(parsed, 2048))[0])
    span = next(s for s in chunk[0]['evidence_spans'] if '供应商甲\n4' in s['quote'])
    assert span['quote'] == '供应商甲\n4\n87.60'
    bid = {'supplier': '供应商甲', 'score': 87.6, 'evidence': [{'ref': span['ref']}]}
    def validate(value):
        return validate_model_response(parsed, json.dumps({'packages': [{'bids': [value]}]}), chunk)
    result = validate(bid)
    assert result.packages[0].bids[0].evidence[0].quote == span['quote']
    for change in ({'supplier': '供应商乙'}, {'score': 99}, {'evidence': [{'ref': 'not-issued'}]},
                   {'evidence': [{'ref': span['ref'], 'quote': '供应商甲\n4\n4\n87.60'}]}):
        with pytest.raises(ValueError):
            validate({**bid, **change})
    # Legacy quotes containing fabricated blank-cell values remain rejected.
    with pytest.raises(ValueError, match='输入分块'):
        validate({**bid, 'evidence': [{'source': 'a', 'locator': 'body', 'quote': '供应商甲\n4\n4\n87.60'}]})
    # An input reference tampered with upstream cannot bypass original text validation.
    chunk[0]['evidence_spans'][0]['quote'] = '不存在的原文'
    with pytest.raises(ValueError, match='不属于输入'):
        validate(bid)


def test_reference_replay_checks_exact_inputs():
    parsed = parse_document('a.html', b'<body>test</body>')
    chunk = evidence_input(parsed, list(model_chunks(parsed, 2048))[0])
    response = json.dumps({'evidence': [{'ref': chunk[0]['evidence_spans'][0]['ref']}]})
    call = {'input': chunk, 'chunk_strategy': 'table-rows-v1', 'chunk_chars': 2048,
            'evidence_strategy': 'span-ref-v1', 'raw_response': {'choices': [
                {'finish_reason': 'stop', 'message': {'content': response}}]}}
    assert revalidate_trace(parsed, {'model_calls': [call]}).evidence[0].quote == 'test'
    call['input'][0]['evidence_spans'].pop()
    with pytest.raises(ValueError, match='全部原文分块'):
        revalidate_trace(parsed, {'model_calls': [call]})
