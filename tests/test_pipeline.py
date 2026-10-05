import json
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

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


def pptx(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        archive.writestr('ppt/slides/slide1.xml', f'''<p:sld
            xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
            <a:t>{text}</a:t></p:sld>''')


def generator(quiz, handler=None, prompt='SYS'):
    if handler is None:
        handler = lambda request: httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    return OllamaGenerator('test', 'gemma4:31b', prompt,
                           client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delay=0, max_attempts=1)


def titled_quiz(quiz, name, period=36):
    result = deepcopy(quiz)
    result['title'] = f'Quiz Toán 7 - Bài 12: Tiết {period} - {name}'
    return result


def bulk_path(name='Tổng các góc trong một tam giác', period=36):
    return f'Toan7/Bài 12_{name}/T{period}_{name}/quiz.json'


def test_leaf_folder_combines_documents_into_one_quiz_and_resumes(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pptx(source / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/a.pptx', 'Source A')
    pptx(source / 'Lop/Lop7/Mon/Toan7/Bai12/Tiet36/b.PPTX', 'Source B')
    requests = []
    def handler(request):
        requests.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    gen = generator(quiz, handler)
    runner = pipeline().QuizRunner(source, output, gen)
    assert runner.run_once().generated == 1
    result = output / bulk_path()
    assert result.is_file()
    assert json.loads(result.read_text(encoding='utf-8'))['title'] == quiz['title']
    state_path = tmp_path / '.outputs-quiz-state.json'
    state = json.loads(state_path.read_text())
    assert len(state['files']) == 1
    assert 'test' not in state_path.read_text()
    assert pipeline().QuizRunner(source, output, gen).run_once().skipped == 1
    assert len(requests) == 1
    assert all(value in requests[0] for value in ('a.pptx', 'b.PPTX', 'Source A', 'Source B'))


def test_persists_bulk_uploader_tree_and_schema_without_json_metadata(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'Lớp 7/Toán/Bài 12/Tiết 36/Đề.pdf')

    runner = pipeline().QuizRunner(source, output, generator(quiz))

    assert runner.run_once().generated == 1
    result = output / (
        'Toan7/Bài 12_Tổng các góc trong một tam giác/'
        'T36_Tổng các góc trong một tam giác/quiz.json'
    )
    payload = json.loads(result.read_text(encoding='utf-8'))
    assert payload['type'] == 'LESSON'
    assert [question['order'] for question in payload['questions']] == list(range(1, 11))
    assert all(
        [option['order'] for option in question['options']] == [1, 2, 3, 4]
        for question in payload['questions']
    )
    assert not (output / '.quiz-state.json').exists()
    assert (tmp_path / '.outputs-quiz-state.json').is_file()


def test_rejects_titles_that_share_the_same_uploader_target(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'A/a.pdf')
    pdf(source / 'B/b.pdf')

    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        name = 'Foo-Bar' if 'A/a.pdf' in content else 'Foo Bar'
        return httpx.Response(200, json={
            'done': True,
            'message': {'content': json.dumps(titled_quiz(quiz, name))},
        })

    summary = pipeline().QuizRunner(source, output, generator(quiz, handler)).run_once()

    assert (summary.generated, summary.failed) == (1, 1)
    assert len(list(output.rglob('quiz.json'))) == 1


def test_failed_regeneration_keeps_target_reserved(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    first = source / 'A/a.pdf'
    pdf(first)
    responses = {'invalid': False}

    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        if responses['invalid'] and 'A/a.pdf' in content:
            body = '{}'
        else:
            body = json.dumps(titled_quiz(quiz, 'Dùng chung'))
        return httpx.Response(200, json={'done': True, 'message': {'content': body}})

    runner = pipeline().QuizRunner(source, output, generator(quiz, handler), failure_cooldown=300)
    assert runner.run_once().generated == 1
    original = output / bulk_path('Dùng chung')
    original_bytes = original.read_bytes()
    pdf(first, 'Changed source')
    responses['invalid'] = True
    assert runner.run_once().failed == 1
    pdf(source / 'B/b.pdf')

    summary = runner.run_once()

    assert summary.failed == 1
    assert original.read_bytes() == original_bytes


def test_migrates_legacy_state_even_without_sources(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    source.mkdir()
    output.mkdir()
    legacy = output / '.quiz-state.json'
    legacy.write_text(json.dumps({'version': 1, 'files': {}}), encoding='utf-8')

    runner = pipeline().QuizRunner(source, output, generator(quiz))
    runner.run_once()

    assert not legacy.exists()
    assert json.loads((tmp_path / '.outputs-quiz-state.json').read_text(encoding='utf-8')) == {
        'version': 1,
        'files': {},
    }


def test_removed_source_releases_its_target_for_renamed_source(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    old = source / 'Old/a.pdf'
    pdf(old)
    runner = pipeline().QuizRunner(source, output, generator(quiz))
    assert runner.run_once().generated == 1
    new = source / 'New/a.pdf'
    new.parent.mkdir(parents=True)
    old.replace(new)

    summary = runner.run_once()

    assert (summary.generated, summary.failed) == (1, 0)
    assert len(list(output.rglob('quiz.json'))) == 1


def test_output_symlink_cannot_escape_output_root(tmp_path, quiz):
    import subprocess
    import sys

    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    outside = tmp_path / 'outside'
    pdf(source / 'A/a.pdf')
    output.mkdir()
    outside.mkdir()
    try:
        (output / 'Toan7').symlink_to(outside, target_is_directory=True)
    except OSError:
        if sys.platform != 'win32':
            pytest.skip('Môi trường không cho phép tạo directory symlink.')
        junction = subprocess.run(
            ['cmd.exe', '/c', 'mklink', '/J', str(output / 'Toan7'), str(outside)],
            capture_output=True,
        )
        if junction.returncode:
            pytest.skip('Môi trường không cho phép tạo directory junction.')

    summary = pipeline().QuizRunner(source, output, generator(quiz)).run_once()

    assert summary.failed == 1
    assert not list(outside.rglob('quiz.json'))


def test_leaf_folders_are_generated_by_numeric_period_order(tmp_path, quiz):
    source = tmp_path / 'documents'
    for period in (17, 2, 10, 1):
        pdf(source / f'Bai/T{period}_Tiet {period}/slide.pdf')
    order = []
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        period = next(period for period in (17, 2, 10, 1) if f'T{period}_Tiet' in content)
        order.append(period)
        return httpx.Response(200, json={
            'done': True,
            'message': {'content': json.dumps(titled_quiz(quiz, f'Tiết {period}', period))},
        })
    assert pipeline().QuizRunner(source, tmp_path / 'outputs', generator(quiz, handler)).run_once().generated == 4
    assert order == [1, 2, 10, 17]


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
    result = output / bulk_path()
    result.write_text('{}')
    assert pipeline().QuizRunner(source, output, generator(quiz, prompt='NEW SYS')).run_once().generated == 1
    result.unlink()
    assert pipeline().QuizRunner(source, output, generator(quiz, prompt='NEW SYS')).run_once().generated == 1


def test_failure_does_not_block_next_document_and_has_cooldown(tmp_path, quiz):
    source = tmp_path / 'documents'
    output = tmp_path / 'outputs'
    pdf(source / 'A/a.pdf')
    pdf(source / 'B/b.pdf')
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        return httpx.Response(200, json={
            'done': True,
            'message': {'content': '{}' if 'A/a.pdf' in content else json.dumps(titled_quiz(quiz, 'Nhóm B'))},
        })
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler), failure_cooldown=300)
    summary = runner.run_once()
    assert (summary.failed, summary.generated) == (1, 1)
    assert not (output / 'A/A.json').exists()
    assert (output / bulk_path('Nhóm B')).exists()
    assert runner.run_once().deferred == 1
    assert runner.run_once(force=True).failed == 1


def test_limit_advances_to_next_file_on_following_pass(tmp_path, quiz):
    source = tmp_path / 'documents'
    pdf(source / 'A/a.pdf')
    pdf(source / 'B/b.pdf')
    calls = []
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        name = 'Nhóm A' if 'A/a.pdf' in content else 'Nhóm B'
        calls.append(name)
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(titled_quiz(quiz, name))}})
    runner = pipeline().QuizRunner(source, tmp_path / 'outputs', generator(quiz, handler))
    assert runner.run_once(limit=1).generated == 1
    assert runner.run_once(limit=1).generated == 1
    assert runner.run_once().skipped == 2


def test_watch_discovers_file_added_after_first_pass(tmp_path, quiz):
    source = tmp_path / 'documents'
    pdf(source / 'A/a.pdf')
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        name = 'Nhóm A' if 'A/a.pdf' in content else 'Nhóm B'
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(titled_quiz(quiz, name))}})
    runner = pipeline().QuizRunner(source, tmp_path / 'outputs', generator(quiz, handler))
    assert runner.run_once().generated == 1
    pdf(source / 'B/b.pdf')
    result = runner.run_once()
    assert (result.generated, result.skipped) == (1, 1)
    assert (tmp_path / 'outputs' / bulk_path('Nhóm B')).is_file()


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
    expected = bulk_path()
    assert (output / expected).is_file()
    assert not (output / 'Lop 7/Toan/Bai 12/Tiet 36/Tiet 36.json').exists()
    assert relative in requests[0]
    assert [event['status'] for event in events] == ['reading', 'generating', 'done']
    assert events[-1]['output'] == expected


def test_stop_finishes_current_file_but_skips_next(tmp_path, quiz):
    import threading
    source, output = tmp_path / 'input', tmp_path / 'output'
    pdf(source / 'A/a.pdf')
    pdf(source / 'B/b.pdf')
    stop = threading.Event()
    def handler(request):
        stop.set()
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    runner = pipeline().QuizRunner(source, output, generator(quiz, handler), stop_event=stop)
    assert runner.run_once().generated == 1
    assert (output / bulk_path()).is_file()
    assert not (output / 'B/B.json').exists()
