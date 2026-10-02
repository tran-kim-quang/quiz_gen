from io import BytesIO
import json
from pathlib import PurePosixPath
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
            assert set(zipfile.namelist()) == {
                str(PurePosixPath(path).with_suffix('.json')) for path in paths
            }
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
            assert archive.namelist() == ['Đề.json']
        response = submit(client, ['Root/Má/Đề.pdf', 'Root/Ma/Đề.pdf', 'Root/Má/De.pdf'])
        assert response.status_code == 400


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

def test_preview_merge_pair_is_one_logical_document_without_writes(tmp_path, quiz):
    from fastapi.testclient import TestClient

    merged = tmp_path / 'merged'
    paths = [
        'Root/Toan7/Bài 12/T36_1.pptx',
        'Root/Toan7/Bài 12/T36_2.pptx',
    ]
    app = create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=merged,
        generator_factory=factory(quiz),
    )
    with TestClient(app) as client:
        response = client.post('/api/preview', json={'kind': 'folder', 'paths': paths})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [file['path'] for file in body['files']] == paths
    assert len(body['documents']) == 1
    assert body['documents'][0]['action'] == 'merge'
    assert body['documents'][0]['output_path'] == 'Toan7/Bài 12/T36/T36.json'
    assert body['documents'][0]['members'] == paths
    assert not merged.exists()


def test_preview_reuse_reports_existing_shared_target(tmp_path, quiz):
    from fastapi.testclient import TestClient

    merged = tmp_path / 'merged'
    target = merged / 'Toan7/Bài 12/T36/T36.pptx'
    target.parent.mkdir(parents=True)
    target.touch()
    paths = [
        'Root/Toan7/Bài 12/T36_1.pptx',
        'Root/Toan7/Bài 12/T36_2.pptx',
    ]
    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=merged,
        generator_factory=factory(quiz),
    )) as client:
        body = client.post(
            '/api/preview', json={'kind': 'folder', 'paths': paths}
        ).json()

    assert body['documents'][0]['action'] == 'reuse'
    assert target.read_bytes() == b''


def test_preview_part_irregularities_are_standalone_with_warnings(tmp_path, quiz):
    from fastapi.testclient import TestClient

    paths = [
        'Root/Toan7/Bài 12/T35_1.pptx',
        'Root/Toan7/Bài 12/T36_1.pptx',
        'Root/Toan7/Bài 12/alt_T36_1.pptx',
        'Root/Toan7/Bài 12/T36_2.pptx',
    ]
    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        generator_factory=factory(quiz),
    )) as client:
        response = client.post(
            '/api/preview', json={'kind': 'folder', 'paths': paths}
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body['documents']) == 4
    assert {document['action'] for document in body['documents']} == {'standalone'}
    assert all(document['warning'] for document in body['documents'])
    assert {issue['code'] for issue in body['issues']} == {
        'missing_part', 'duplicate_part',
    }


@pytest.mark.parametrize('paths', [
    ['Root/Toan7/Bài 12/notes.pdf', 'Root/Toan7/Bài 12/notes.pptx'],
    ['Root/../bad.pdf'],
])
def test_preview_unsafe_or_colliding_logical_paths_return_400(tmp_path, quiz, paths):
    from fastapi.testclient import TestClient

    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        generator_factory=factory(quiz),
    )) as client:
        response = client.post(
            '/api/preview', json={'kind': 'folder', 'paths': paths}
        )

    assert response.status_code == 400

def pptx_bytes(text='Triangle slide'):
    archive = BytesIO()
    with ZipFile(archive, 'w') as zipfile:
        zipfile.writestr(
            'ppt/slides/slide1.xml',
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:t>{text}</a:t></p:sld>',
        )
    return archive.getvalue()


def submit_pptx_pair(client, paths):
    return client.post(
        '/api/jobs',
        data={'kind': 'folder', 'paths': json.dumps(paths)},
        files=[
            ('files', (path.rsplit('/', 1)[-1], pptx_bytes(path), 'application/vnd.openxmlformats-officedocument.presentationml.presentation'))
            for path in paths
        ],
    )


class FakeMerger:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def merge(self, part1, part2, output):
        from quiz_generator.pptx_merge import MergeResult

        self.calls.append((part1, part2, output))
        if self.failure:
            raise self.failure
        output.write_bytes(pptx_bytes('Merged slide'))
        return MergeResult(1, 1, 2)


def pair_paths():
    return [
        'Root/Toan7/Bài 12/T36_1.pptx',
        'Root/Toan7/Bài 12/T36_2.pptx',
    ]


def test_pair_job_materializes_once_and_outputs_one_logical_json(tmp_path, quiz):
    from fastapi.testclient import TestClient

    requests = []
    merger = FakeMerger()
    app = create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        merger=merger,
        generator_factory=factory(quiz, lambda request: (
            requests.append(request),
            httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}}),
        )[1]),
    )
    with TestClient(app) as client:
        response = submit_pptx_pair(client, pair_paths())
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json()['id'])

    assert job['status'] == 'completed'
    assert len(merger.calls) == 1
    assert len(requests) == 1
    assert len(job['sources']) == 2
    assert len(job['files']) == 1
    assert job['files'][0]['action'] == 'merge'
    assert job['files'][0]['members'] == pair_paths()
    assert job['files'][0]['output'].endswith('T36.json')


def test_existing_merge_skips_worker(tmp_path, quiz):
    from fastapi.testclient import TestClient

    merged = tmp_path / 'merged'
    target = merged / 'Toan7/Bài 12/T36/T36.pptx'
    target.parent.mkdir(parents=True)
    target.write_bytes(pptx_bytes('Existing merged'))
    merger = FakeMerger()
    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=merged,
        merger=merger,
        generator_factory=factory(quiz),
    )) as client:
        response = submit_pptx_pair(client, pair_paths())
        job = wait_job(client, response.json()['id'])

    assert job['status'] == 'completed'
    assert merger.calls == []
    assert len(job['files']) == 1
    assert job['files'][0]['action'] == 'reuse'


def test_source_merge_failure_becomes_two_standalone_rows(tmp_path, quiz):
    from fastapi.testclient import TestClient
    from quiz_generator.pptx_merge import SourceMergeError

    merger = FakeMerger(SourceMergeError('unsupported shape'))
    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        merger=merger,
        generator_factory=factory(quiz),
    )) as client:
        response = submit_pptx_pair(client, pair_paths())
        job = wait_job(client, response.json()['id'])

    assert job['status'] == 'completed'
    assert len(job['files']) == 2
    assert {file['action'] for file in job['files']} == {'standalone'}
    assert all('unsupported shape' in file['warning'] for file in job['files'])
    assert {file['output'] for file in job['files']} == {
        'Toan7/Bài 12/T36/T36_1.json',
        'Toan7/Bài 12/T36/T36_2.json',
    }


def test_infrastructure_merge_failure_fails_job(tmp_path, quiz):
    from fastapi.testclient import TestClient
    from quiz_generator.pptx_merge import MergeInfrastructureError

    requests = []
    merger = FakeMerger(MergeInfrastructureError('node unavailable'))
    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        merger=merger,
        generator_factory=factory(quiz, lambda request: requests.append(request)),
    )) as client:
        response = submit_pptx_pair(client, pair_paths())
        job = wait_job(client, response.json()['id'])

    assert job['status'] == 'failed'
    assert len(merger.calls) == 1
    assert requests == []
    assert job['files'][0]['status'] == 'failed'
    assert 'node unavailable' in job['error']


def test_zip_contains_only_final_logical_json_tree(tmp_path, quiz):
    from fastapi.testclient import TestClient

    with TestClient(create_app(
        work_root=tmp_path / 'work',
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        merger=FakeMerger(),
        generator_factory=factory(quiz),
    )) as client:
        response = submit_pptx_pair(client, pair_paths())
        job = wait_job(client, response.json()['id'])
        archive = client.get(f"/api/jobs/{job['id']}/download")

    with ZipFile(BytesIO(archive.content)) as zipfile:
        assert zipfile.namelist() == ['Toan7/Bài 12/T36/T36.json']


def test_restart_resets_merging_row_to_pending_and_marks_interrupted(tmp_path, quiz):
    from fastapi.testclient import TestClient

    work = tmp_path / 'work'
    job_id = 'a' * 32
    metadata = work / job_id / 'job.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        'id': job_id,
        'kind': 'folder',
        'name': 'Root',
        'status': 'running',
        'created_at': 1,
        'updated_at': 1,
        'error': '',
        'sources': [{
            'id': 'source-0',
            'path': pair_paths()[0],
            'normalized_path': pair_paths()[0],
            'size': 1,
        }],
        'files': [{
            'id': 'logical-0',
            'path': 'Toan7/Bai 12/T36/T36.pptx',
            'normalized_path': 'Toan7/Bai 12/T36/T36.pptx',
            'members': pair_paths(),
            'action': 'merge',
            'warning': '',
            'size': 2,
            'status': 'merging',
            'error': '',
            'output': None,
        }],
    }))
    with TestClient(create_app(
        work_root=work,
        output_root=tmp_path / 'out',
        merged_root=tmp_path / 'merged',
        merger=FakeMerger(),
        generator_factory=factory(quiz),
    )) as client:
        job = client.get('/api/jobs/' + job_id).json()

    assert job['status'] == 'interrupted'
    assert job['files'][0]['status'] == 'pending'
    assert 'khởi động lại' in job['error']

def test_app_cli_passes_merged_documents_override(tmp_path, monkeypatch):
    import app as launcher

    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        launcher,
        'create_app',
        lambda **options: captured.update(options) or sentinel,
    )
    monkeypatch.setattr(
        launcher.uvicorn,
        'run',
        lambda application, **options: captured.update(
            application=application, server=options
        ),
    )

    launcher.main([
        '--merged-documents', str(tmp_path / 'shared-merged'),
        '--host', '127.0.0.2',
        '--port', '8765',
    ])

    assert captured['merged_root'] == tmp_path / 'shared-merged'
    assert captured['application'] is sentinel
    assert captured['server'] == {'host': '127.0.0.2', 'port': 8765}
