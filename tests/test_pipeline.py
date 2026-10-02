import json
from pathlib import Path

import httpx
import pymupdf
import pytest

from quiz_generator.llm import OllamaGenerator


def pipeline():
    from quiz_generator import pipeline
    return pipeline


def pdf(path, text='Triangle source'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), text)
        doc.save(path)


def generator(quiz, handler=None, prompt='SYS'):
    if handler is None:
        handler = lambda request: httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    return OllamaGenerator('test', 'gemma4:31b', prompt,
                           client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delay=0, max_attempts=1)


def test_mirrors_deep_tree_and_resumes_without_api_calls(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/a.pdf')
    pdf(source / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/b.PDF')
    requests = []
    def handler(request):
        requests.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    gen = generator(quiz, handler)
    runner = pipeline().QuizRunner(source, output, gen)
    assert runner.run_once().generated == 2
    first = output / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/a.pdf.json'
    second = output / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/b.PDF.json'
    assert first.is_file() and second.is_file()
    assert 'Tổng các góc' in first.read_text()
    assert json.loads(second.read_text())['title'] == quiz['title']
    state = json.loads((output / '.quiz-state.json').read_text())
    assert len(state['files']) == 2
    assert 'test' not in (output / '.quiz-state.json').read_text()
    assert pipeline().QuizRunner(source, output, gen).run_once().skipped == 2
    assert len(requests) == 2
    assert 'a.pdf' in requests[0] and 'b.PDF' in requests[1]


def test_source_prompt_and_output_changes_trigger_regeneration(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    path = source / 'a.pdf'
    pdf(path)
    gen = generator(quiz)
    runner = pipeline().QuizRunner(source, output, gen)
    assert runner.run_once().generated == 1
    pdf(path, 'New reference text')
    assert runner.run_once().generated == 1
    assert pipeline().QuizRunner(source, output, generator(quiz, prompt='NEW SYS')).run_once().generated == 1
    (output / 'a.pdf.json').write_text('{}')
    assert pipeline().QuizRunner(source, output, generator(quiz, prompt='NEW SYS')).run_once().generated == 1
    (output / 'a.pdf.json').unlink()
    assert pipeline().QuizRunner(source, output, generator(quiz, prompt='NEW SYS')).run_once().generated == 1


def test_failure_does_not_block_next_document_and_has_cooldown(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'a.pdf')
    pdf(source / 'b.pdf')
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        return httpx.Response(200, json={'done': True, 'message': {'content': '{}' if 'a.pdf' in content else json.dumps(quiz)}})
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler), failure_cooldown=300)
    summary = runner.run_once()
    assert (summary.failed, summary.generated) == (1, 1)
    assert not (output / 'a.pdf.json').exists()
    assert (output / 'b.pdf.json').exists()
    assert runner.run_once().deferred == 1
    assert runner.run_once(force=True).failed == 1


def test_limit_advances_to_next_file_on_following_pass(tmp_path, quiz):
    source = tmp_path / 'documents'
    pdf(source / 'a.pdf')
    pdf(source / 'b.pdf')
    runner = pipeline().QuizRunner(source, tmp_path / 'outputs', generator(quiz))
    assert runner.run_once(limit=1).generated == 1
    assert runner.run_once(limit=1).generated == 1
    assert runner.run_once().skipped == 2


def test_watch_discovers_file_added_after_first_pass(tmp_path, quiz):
    source = tmp_path / 'documents'
    pdf(source / 'a.pdf')
    runner = pipeline().QuizRunner(source, tmp_path / 'outputs', generator(quiz))
    assert runner.run_once().generated == 1
    pdf(source / 'deeper/b.pdf')
    result = runner.run_once()
    assert (result.generated, result.skipped) == (1, 1)
    assert (tmp_path / 'outputs/deeper/b.pdf.json').is_file()


def test_lock_prevents_duplicate_workers_and_releases_on_exit(tmp_path):
    with pipeline().OutputLock(tmp_path):
        with pytest.raises(RuntimeError, match='worker|tiến trình'):
            with pipeline().OutputLock(tmp_path):
                pass
    with pipeline().OutputLock(tmp_path):
        pass


def test_changed_source_during_generation_is_not_marked_done(tmp_path, quiz):
    source = tmp_path / 'documents'
    path = source / 'a.pdf'
    output = tmp_path / 'outputs'
    pdf(path)
    def handler(request):
        pdf(path, 'Changed while request is running')
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler))
    assert runner.run_once().failed == 1
    assert not (output / 'a.pdf.json').exists()


def test_corrupt_state_recovers_and_auth_error_stops_queue(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'a.pdf')
    pdf(source / 'b.pdf')
    output.mkdir()
    (output / '.quiz-state.json').write_text('invalid json')
    def handler(request):
        return httpx.Response(401)
    from quiz_generator.llm import FatalAPIError
    with pytest.raises(FatalAPIError):
        pipeline().QuizRunner(source, output, generator(quiz, handler)).run_once()


def test_pipeline_uses_normalized_input_and_restores_original_output(tmp_path, quiz):
    import threading
    source, output = tmp_path / 'input', tmp_path / 'output'
    relative = 'Lop 7/Toan/Bai 12/Tiet 36/De.pdf'
    original = 'Lớp 7/Toán/Bài 12/Tiết 36/Đề.pdf'
    pdf(source / relative)
    events = []
    requests = []
    def handler(request):
        requests.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler),
                                   path_mapping={relative: original},
                                   on_progress=lambda event: events.append(event), stop_event=threading.Event())
    assert runner.run_once().generated == 1
    assert (output / (original + '.json')).is_file()
    assert not (output / (relative + '.json')).exists()
    assert relative in requests[0]
    assert [event['status'] for event in events] == ['reading', 'generating', 'done']
    assert events[-1]['output'] == original + '.json'


def test_stop_finishes_current_file_but_skips_next(tmp_path, quiz):
    import threading
    source, output = tmp_path / 'input', tmp_path / 'output'
    pdf(source / 'a.pdf')
    pdf(source / 'b.pdf')
    stop = threading.Event()
    def handler(request):
        stop.set()
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler), stop_event=stop)
    assert runner.run_once().generated == 1
    assert (output / 'a.pdf.json').is_file()
    assert not (output / 'b.pdf.json').exists()
