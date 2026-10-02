from collections import Counter
from copy import deepcopy
import json
import random

import httpx
import pytest


def api():
    from quiz_generator import llm
    return llm


def test_valid_quiz_and_path_metadata(quiz):
    assert api().validate_quiz(json.dumps(quiz), 'Lop7/Mon/Toan7/Bai12/Tiet36/source.pdf') == quiz


@pytest.mark.parametrize('change', [
    lambda q: q.update(extra='not allowed'),
    lambda q: q['questions'].pop(),
    lambda q: q['questions'][0]['options'].pop(),
    lambda q: q['questions'][0]['options'][1].update(isCorrect=True),
    lambda q: q['questions'][0]['options'][0].update(isCorrect=1),
    lambda q: q['questions'][0].update(explanation=''),
    lambda q: q['questions'][0].update(prompt='\\frac{1}{2}'),
    lambda q: q['questions'][0]['options'][1].update(text='Phương án 1'),
    lambda q: q['questions'][1].update(prompt='Câu hỏi 1?'),
    lambda q: q.update(title='Quiz Toán 8 - Bài 12: Tiết 36 - Tổng các góc trong một tam giác'),
    lambda q: q.update(title='Quiz Toán 7 - Bài 13: Tiết 36 - Tổng các góc trong một tam giác'),
    lambda q: q.update(title='Quiz Toán 7 - Bài 12: Tiết 37 - Tổng các góc trong một tam giác'),
])
def test_rejects_invalid_quiz_contract(quiz, change):
    change(quiz)
    with pytest.raises(ValueError):
        api().validate_quiz(json.dumps(quiz), 'Lop7/Toan7/Bai12/Tiet36/source.pdf')


def test_rejects_markdown_and_duplicate_json_keys(quiz):
    with pytest.raises(ValueError):
        api().validate_quiz('```json\n' + json.dumps(quiz) + '\n```')
    with pytest.raises(ValueError):
        api().validate_quiz(json.dumps(quiz).replace('"title":', '"title": "fake", "title":', 1))


def test_answer_shuffle_preserves_correct_text_and_balances_positions(quiz):
    original = deepcopy(quiz)
    api().balance_answers(quiz, random.Random(2))
    positions = Counter()
    for question in quiz['questions']:
        correct = [i for i, option in enumerate(question['options']) if option['isCorrect']]
        positions[correct[0]] += 1
        assert question['options'][correct[0]]['text'] == 'Phương án 1'
    assert sorted(positions.values()) == [2, 2, 3, 3]
    assert [q['prompt'] for q in quiz['questions']] == [q['prompt'] for q in original['questions']]


def test_ollama_request_has_prompt_source_images_and_retries_invalid_json(quiz):
    from quiz_generator.documents import Document
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers['Authorization'] == 'Bearer secret-test'
        assert request.url == 'https://ollama.com/api/chat'
        assert body['model'] == 'gemma4:31b'
        assert body['stream'] is False
        assert 'format' not in body
        assert body['messages'][0] == {'role': 'system', 'content': 'SYSTEM PROMPT'}
        assert 'triangle source' in body['messages'][1]['content']
        assert 'Bai12/Tiet36' in body['messages'][1]['content']
        assert body['messages'][1]['images'] == ['base64-image']
        content = '{}' if len(requests) == 1 else json.dumps(quiz)
        return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': content}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generator = api().OllamaGenerator('secret-test', 'gemma4:31b', 'SYSTEM PROMPT', client=client, retry_delay=0)
        result = generator.generate(Document('triangle source', ['base64-image'], 1), 'Lop7/Toan7/Bai12/Tiet36/a.pdf')
    assert result['title'] == quiz['title']
    assert len(requests) == 2
    assert requests[1]['messages'][-1]['role'] == 'user'
    assert '{}' in [m['content'] for m in requests[1]['messages']]


@pytest.mark.parametrize('status', [429, 500, 503])
def test_retries_transient_http_errors(quiz, status):
    from quiz_generator.documents import Document
    attempts = []
    def handler(request):
        attempts.append(request)
        if len(attempts) < 3:
            return httpx.Response(status, headers={'Retry-After': '0'})
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generator = api().OllamaGenerator('test', 'gemma4:31b', 'SYS', client=client, retry_delay=0)
        assert len(generator.generate(Document('source', [], 1), 'source.pdf')['questions']) == 10
    assert len(attempts) == 3


def test_auth_failure_does_not_retry_or_expose_key():
    from quiz_generator.documents import Document
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(401, text='secret-test')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generator = api().OllamaGenerator('secret-test', 'gemma4:31b', 'SYS', client=client, retry_delay=0)
        with pytest.raises(api().FatalAPIError) as error:
            generator.generate(Document('source', [], 1), 'source.pdf')
    assert len(attempts) == 1
    assert 'secret-test' not in str(error.value)


def test_stops_after_retry_budget_and_rejects_truncation():
    from quiz_generator.documents import Document
    attempts = []
    def handler(request):
        attempts.append(request)
        return httpx.Response(200, json={'done': True, 'done_reason': 'length', 'message': {'content': '{}'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generator = api().OllamaGenerator('test', 'gemma4:31b', 'SYS', client=client, retry_delay=0, max_attempts=2)
        with pytest.raises(ValueError):
            generator.generate(Document('source', [], 1), 'source.pdf')
    assert len(attempts) == 2
