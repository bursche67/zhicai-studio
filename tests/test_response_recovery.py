import json
import pytest
from scripts.recover_model_responses import revalidate_trace
from backend.parsers import parse_document
from backend.extraction import chunks, model_input_blocks


def test_offline_replay_requires_all_chunks_and_finished_json():
    parsed = parse_document('a.html', b'<body>test</body>')
    inputs = list(chunks(model_input_blocks(parsed), 12000))
    trace = {'model_calls': [{'input': inputs[0], 'raw_response': {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'project_name': 'test'})}}]}}]}
    assert revalidate_trace(parsed, trace).project_name == 'test'
    choice = trace['model_calls'][0]['raw_response']['choices'][0]
    choice['finish_reason'] = 'length'
    with pytest.raises(ValueError, match='截断'): revalidate_trace(parsed, trace)
    choice['finish_reason'] = 'stop'
    choice['message']['content'] = '{incomplete'
    with pytest.raises(ValueError): revalidate_trace(parsed, trace)
    trace['model_calls'][0]['input'] = []
    with pytest.raises(ValueError, match='全部原文分块'): revalidate_trace(parsed, trace)
