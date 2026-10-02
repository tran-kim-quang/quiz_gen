from io import BytesIO
import json
import threading
import time
from zipfile import ZipFile

import httpx
import pymupdf
import pytest

from quiz_generator.llm import OllamaGenerator


def create_app(*args, **kwargs):
    from quiz_generator.web import create_app as factory
    return factory(*args, **kwargs)


def pdf_bytes():
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), 'Triangle reference')
        return document.tobytes()


def factory(quiz, handler=None):
    if handler is None:
        handler = lambda request: httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    return lambda: OllamaGenerator('secret-not-in-ui', 'gemma4:31b', 'SYSTEM',
                                  client=httpx.Client(transport=httpx.MockTransport(handler)),
                                  retry_delay=0, max_attempts=1)


def submit(client, paths, kind='folder'):
    return client.post('/api/jobs', data={'kind': kind, 'paths': json.dumps(paths)},
                       files=[('files', (path.rsplit('/', 1)[-1], pdf_bytes(), 'application/pdf')) for path in paths])


def wait_job(client, job_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get('/api/jobs/' + job_id).json()
        if job['status'] not in ('queued', 'running', 'stopping'):
            return job
        time.sleep(0.01)
    pytest.fail('Job did not finish')


def test_folder_upload_normalizes_before_llm_and_downloads_original_tree(tmp_path, quiz):
    from fastapi.testclient import TestClient
    seen = []
    def handler(request):
        seen.append(json.loads(request.content)['messages'][1]['content'])
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    app = create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'outputs', generator_factory=factory(quiz, handler))
    paths = ['Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề.pdf', 'Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề khác.pdf']
    with TestClient(app) as client:
        response = submit(client, paths)
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()['id'])
        assert job['status'] == 'completed'
        assert job['counts']['done'] == 2
        assert len(seen) == 2
        assert all('Lop 7/Mon/Toan/Bai 12/Tiet 36/' in content for content in seen)
        assert all('Lớp 7/' not in content for content in seen)
        archive = client.get(f"/api/jobs/{job['id']}/download")
        with ZipFile(BytesIO(archive.content)) as zipfile:
            assert set(zipfile.namelist()) == {p + '.json' for p in paths}
            for name in zipfile.namelist():
                assert json.loads(zipfile.read(name))['title'] == quiz['title']
        file_id = job['files'][0]['id']
        file = client.get(f"/api/jobs/{job['id']}/files/{file_id}")
        assert file.status_code == 200
        assert file.json()['title'] == quiz['title']
        assert 'secret-not-in-ui' not in json.dumps(job) + client.get('/api/config').text
        assert client.get('/').status_code == 200


def test_single_file_and_accent_collisions(tmp_path, quiz):
    from fastapi.testclient import TestClient
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out', generator_factory=factory(quiz))) as client:
        response = submit(client, ['Đề.pdf'], 'file')
        job = wait_job(client, response.json()['id'])
        with ZipFile(BytesIO(client.get(f"/api/jobs/{job['id']}/download").content)) as archive:
            assert archive.namelist() == ['Đề.pdf.json']
        response = submit(client, ['Root/Má/Đề.pdf', 'Root/Ma/Đề.pdf', 'Root/Má/De.pdf'])
        job = wait_job(client, response.json()['id'])
        assert len({file['normalized_path'] for file in job['files']}) == 3
        assert job['counts']['done'] == 3


@pytest.mark.parametrize('kind,paths', [
    ('folder', ['../bad.pdf']), ('folder', ['Root/a.pdf', 'Root/a.pdf']),
    ('file', ['Root/a.pdf']), ('folder', ['a.pdf']), ('folder', ['A/a.pdf', 'B/b.pdf']),
    ('file', ['a.pdf', 'b.pdf']), ('folder', ['Root/a.txt']),
    ('folder', ['Lớp/Đề.pdf', 'Lớp/Đề.pdf.json/Bài.pdf']),
    ('folder', ['.quiz-state.json/Đề.pdf']),
    ('folder', ['.quiz-worker.lock/Đề.pdf']),
])
def test_invalid_upload_paths_never_start_job(tmp_path, quiz, kind, paths):
    from fastapi.testclient import TestClient
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out', generator_factory=factory(quiz))) as client:
        response = submit(client, paths, kind)
        assert response.status_code == 400
        assert client.get('/api/jobs').json() == []
        assert not list((tmp_path / 'out').rglob('*.json'))


def test_multiple_jobs_are_accepted_and_processed_in_fifo_order(tmp_path, quiz):
    from fastapi.testclient import TestClient
    started, release = threading.Event(), threading.Event()
    order = []
    def handler(request):
        content = json.loads(request.content)['messages'][1]['content']
        name = 'a.pdf' if 'a.pdf' in content else 'b.pdf'
        order.append(name)
        if name == 'a.pdf':
            started.set()
            assert release.wait(timeout=5)
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out', generator_factory=factory(quiz, handler))) as client:
        first = submit(client, ['a.pdf'], 'file')
        first_id = first.json()['id']
        assert started.wait(timeout=5)
        try:
            second = submit(client, ['b.pdf'], 'file')
            assert second.status_code == 202
            second_job = client.get('/api/jobs/' + second.json()['id']).json()
            assert second_job['status'] == 'queued'
            assert second_job['queue_position'] == 1
        finally:
            release.set()
        assert wait_job(client, first_id)['status'] == 'completed'
        assert wait_job(client, second.json()['id'])['status'] == 'completed'
        assert order == ['a.pdf', 'b.pdf']


def test_cancel_queued_job_without_calling_llm(tmp_path, quiz):
    from fastapi.testclient import TestClient
    started, release = threading.Event(), threading.Event()
    calls = []
    def handler(request):
        calls.append(json.loads(request.content)['messages'][1]['content'])
        started.set()
        assert release.wait(timeout=5)
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out', generator_factory=factory(quiz, handler))) as client:
        first = submit(client, ['a.pdf'], 'file')
        assert started.wait(timeout=5)
        second = submit(client, ['b.pdf'], 'file')
        stopped = client.post(f"/api/jobs/{second.json()['id']}/stop").json()
        assert stopped['status'] == 'stopped'
        release.set()
        assert wait_job(client, first.json()['id'])['status'] == 'completed'
        time.sleep(0.05)
        assert len(calls) == 1


def test_application_upload_limits_are_open_and_preview_is_checked_on_server(tmp_path, quiz):
    from fastapi.testclient import TestClient
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out',
                              generator_factory=factory(quiz))) as client:
        config = client.get('/api/config').json()
        assert config['max_files'] is None
        assert config['max_upload_bytes'] is None
        many_paths = [f'Lớp 7/Bài {index}/Đề.pdf' for index in range(600)]
        preview = client.post('/api/preview', json={'kind': 'folder', 'paths': many_paths})
        assert preview.status_code == 200
        assert len(preview.json()['files']) == 600
        assert preview.json()['files'][0]['normalized_path'] == 'Lop 7/Bai 0/De.pdf'
        assert client.get('/api/jobs/missing').status_code == 404


def test_upload_accepts_more_than_previous_500_file_limit(tmp_path, quiz):
    from fastapi.testclient import TestClient
    started, release = threading.Event(), threading.Event()
    def handler(request):
        started.set()
        assert release.wait(timeout=5)
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    paths = [f'Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề {index}.pdf' for index in range(501)]
    with TestClient(create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'out',
                              generator_factory=factory(quiz, handler))) as client:
        response = submit(client, paths)
        assert response.status_code == 202, response.text
        assert response.json()['counts']['total'] == 501
        assert started.wait(timeout=5)
        client.post(f"/api/jobs/{response.json()['id']}/stop")
        release.set()
        job = wait_job(client, response.json()['id'])
        assert job['status'] == 'stopped'
        assert job['counts']['done'] == 1
        assert job['counts']['pending'] == 500


def test_restart_retains_completed_downloads(tmp_path, quiz):
    from fastapi.testclient import TestClient
    options = dict(work_root=tmp_path / 'work', output_root=tmp_path / 'out', generator_factory=factory(quiz))
    with TestClient(create_app(**options)) as client:
        response = submit(client, ['Đề.pdf'], 'file')
        job_id = response.json()['id']
        assert wait_job(client, job_id)['status'] == 'completed'
    with TestClient(create_app(**options)) as client:
        assert client.get('/api/jobs/' + job_id).json()['counts']['done'] == 1
        assert client.get(f'/api/jobs/{job_id}/download').status_code == 200
