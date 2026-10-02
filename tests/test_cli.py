from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from zipfile import ZipFile
import time

import pymupdf
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'generate_quiz.py'
REPO_ROOT = SCRIPT.parent


def test_setup_ubuntu_script_has_valid_syntax_and_install_contract():
    setup = REPO_ROOT / 'setup-ubuntu.sh'

    result = subprocess.run(
        ['bash', '-n', str(setup)], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    content = setup.read_text()
    assert 'Python 3.10' in content
    assert 'Node.js 20' in content
    assert 'requirements.txt' in content
    assert 'npm ci' in content
    assert '.env' not in content


def test_env_example_documents_quiz_paths_without_secret():
    content = (REPO_ROOT / '.env.example').read_text()

    assert 'QUIZ_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/documents' in content
    assert (
        'QUIZ_MERGED_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/merged-documents'
        in content
    )
    assert 'QUIZ_OUTPUT_DIR=/home/meconlonton/Documents/quiz/documents/output' in content
    assert '123' not in content


def test_list_does_not_require_api_key(tmp_path):
    source = tmp_path / 'documents'
    source.mkdir()
    (source / 'input.PDF').touch()
    result = subprocess.run([sys.executable, str(SCRIPT), '--documents', str(source),
                             '--env-file', str(tmp_path / 'missing.env'), '--list'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'input.PDF' in result.stdout


@pytest.mark.parametrize('initial_name,next_name,fail_first,expected_code', [
    ('a.pdf', 'b.pdf', False, 0),
    ('b.pdf', 'a.pdf', True, 1),
])
def test_watch_cli_processes_new_files_and_exits_at_limit(tmp_path, quiz, initial_name, next_name, fail_first, expected_code):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body['messages'][1]['content'])
            content = '{}' if fail_first and len(requests) == 1 else json.dumps(quiz)
            payload = json.dumps({'done': True, 'message': {'content': content}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source, output = tmp_path / 'documents', tmp_path / 'outputs'
    source.mkdir()
    def add(name):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 40), 'Triangle lesson')
            pdf.save(source / name)
    add(initial_name)
    env_file = tmp_path / '.env'
    env_file.write_text(f'OLLAMA_API_KEY=test-secret\nLLM_MODEL_NAME=gemma4:31b\nOLLAMA_BASE_URL=http://127.0.0.1:{server.server_port}\nLLM_MAX_ATTEMPTS=1\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OLLAMA_', 'LLM_'))}
    process = subprocess.Popen([sys.executable, str(SCRIPT), '--documents', str(source), '--output', str(output),
                                '--env-file', str(env_file), '--watch', '--interval', '0.05', '--limit', '2'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    try:
        deadline = time.monotonic() + 10
        while not (output / '.quiz-state.json').exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert (output / '.quiz-state.json').exists()
        assert (output / Path(initial_name).with_suffix('.json')).exists() is not fail_first
        add(next_name)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == expected_code, stderr
        assert (output / Path(next_name).with_suffix('.json')).exists()
        assert len(requests) == 2 and next_name in requests[1]
        assert 'test-secret' not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        server.shutdown()
        server.server_close()

def clear_quiz_path_env(monkeypatch):
    for name in ('QUIZ_DOCUMENTS_DIR', 'QUIZ_MERGED_DOCUMENTS_DIR', 'QUIZ_OUTPUT_DIR'):
        monkeypatch.delenv(name, raising=False)


def test_config_uses_exact_default_quiz_paths(tmp_path, monkeypatch):
    from quiz_generator.config import resolve_quiz_paths

    clear_quiz_path_env(monkeypatch)
    paths = resolve_quiz_paths(tmp_path / 'missing.env')

    assert paths.documents == Path('/home/meconlonton/Documents/quiz/documents')
    assert paths.merged_documents == Path('/home/meconlonton/Documents/quiz/merged-documents')
    assert paths.output == Path('/home/meconlonton/Documents/quiz/documents/output')


def test_config_precedence_cli_then_shell_then_env_file(tmp_path, monkeypatch):
    from quiz_generator.config import resolve_quiz_paths

    clear_quiz_path_env(monkeypatch)
    env_file = tmp_path / '.env'
    env_file.write_text(
        'QUIZ_DOCUMENTS_DIR=/env/documents\n'
        'QUIZ_MERGED_DOCUMENTS_DIR=/env/merged\n'
        'QUIZ_OUTPUT_DIR=/env/output\n'
    )
    monkeypatch.setenv('QUIZ_DOCUMENTS_DIR', '/shell/documents')
    monkeypatch.setenv('QUIZ_MERGED_DOCUMENTS_DIR', '/shell/merged')
    monkeypatch.setenv('QUIZ_OUTPUT_DIR', '/shell/output')

    shell = resolve_quiz_paths(env_file)
    cli = resolve_quiz_paths(
        env_file,
        documents=tmp_path / 'cli-documents',
        merged_documents=tmp_path / 'cli-merged',
        output=tmp_path / 'cli-output',
    )

    assert shell.documents == Path('/shell/documents')
    assert shell.merged_documents == Path('/shell/merged')
    assert shell.output == Path('/shell/output')
    assert cli.documents == (tmp_path / 'cli-documents').resolve()
    assert cli.merged_documents == (tmp_path / 'cli-merged').resolve()
    assert cli.output == (tmp_path / 'cli-output').resolve()

def pptx(path, text='Triangle slide'):
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w') as archive:
        archive.writestr(
            'ppt/slides/slide1.xml',
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            f'<a:t>{text}</a:t></p:sld>',
        )


def quiz_server(quiz, requests):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body['messages'][1]['content'])
            payload = json.dumps({
                'done': True,
                'message': {'content': json.dumps(quiz)},
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def cli_env_file(tmp_path, port):
    path = tmp_path / '.env'
    path.write_text(
        'OLLAMA_API_KEY=test-secret\n'
        'LLM_MODEL_NAME=gemma4:31b\n'
        f'OLLAMA_BASE_URL=http://127.0.0.1:{port}\n'
        'LLM_MAX_ATTEMPTS=1\n'
    )
    return path


def clean_env():
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(('OLLAMA_', 'LLM_', 'QUIZ_'))
    }


def test_list_reports_merge_reuse_and_standalone_without_running_worker(tmp_path):
    source = tmp_path / 'documents'
    merged = tmp_path / 'merged'
    lesson = source / 'Toan7/Bài 12'
    for name in ('T36_1.pptx', 'T36_2.pptx', 'T37_1.pptx', 'T37_2.pptx',
                 'T38_1.pptx', 'notes.pdf'):
        path = lesson / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    reused = merged / 'Toan7/Bài 12/T37/T37_Slide.pptx'
    reused.parent.mkdir(parents=True)
    reused.touch()

    result = subprocess.run([
        sys.executable, str(SCRIPT),
        '--documents', str(source),
        '--merged-documents', str(merged),
        '--output', str(tmp_path / 'output'),
        '--env-file', str(tmp_path / 'missing.env'),
        '--list',
    ], capture_output=True, text=True, env=clean_env())

    assert result.returncode == 0, result.stderr
    assert 'merge' in result.stdout
    assert 'reuse merged' in result.stdout
    assert 'standalone' in result.stdout
    assert 'Thiếu part' in result.stdout
    assert 'Tổng: 4' in result.stdout


def test_cli_generates_one_json_for_existing_merged_pair(tmp_path, quiz):
    source = tmp_path / 'documents'
    merged = tmp_path / 'merged'
    output = tmp_path / 'output'
    lesson = source / 'Toan7/Bài 12'
    for name in ('T36_1.pptx', 'T36_2.pptx'):
        path = lesson / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    target = merged / 'Toan7/Bài 12/T36/T36_Slide.pptx'
    pptx(target)
    requests = []
    server = quiz_server(quiz, requests)
    try:
        result = subprocess.run([
            sys.executable, str(SCRIPT),
            '--documents', str(source),
            '--merged-documents', str(merged),
            '--output', str(output),
            '--env-file', str(cli_env_file(tmp_path, server.server_port)),
        ], capture_output=True, text=True, env=clean_env(), timeout=10)
    finally:
        server.shutdown()
        server.server_close()

    assert result.returncode == 0, result.stderr
    assert (output / 'Toan7/Bài 12/T36/T36_Slide.json').is_file()
    assert len(requests) == 1
    assert not (output / 'Toan7/Bài 12/T36/T36_1.json').exists()
    assert not (output / 'Toan7/Bài 12/T36/T36_2.json').exists()


def test_watch_replans_when_second_part_arrives(tmp_path, quiz):
    source = tmp_path / 'documents'
    merged = tmp_path / 'merged'
    output = tmp_path / 'output'
    lesson = source / 'Toan7/Bài 12'
    part1 = lesson / 'T36_1.pptx'
    pptx(part1, 'Part one')
    requests = []
    server = quiz_server(quiz, requests)
    process = subprocess.Popen([
        sys.executable, str(SCRIPT),
        '--documents', str(source),
        '--merged-documents', str(merged),
        '--output', str(output),
        '--env-file', str(cli_env_file(tmp_path, server.server_port)),
        '--watch', '--interval', '0.05', '--limit', '2',
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=clean_env())
    try:
        standalone = output / 'Toan7/Bài 12/T36/T36_1.json'
        deadline = time.monotonic() + 10
        while not standalone.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        assert standalone.is_file()
        pptx(merged / 'Toan7/Bài 12/T36/T36_Slide.pptx', 'Merged pair')
        pptx(lesson / 'T36_2.pptx', 'Part two')
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        assert (output / 'Toan7/Bài 12/T36/T36_Slide.json').is_file()
        assert len(requests) == 2
        assert 'test-secret' not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        server.shutdown()
        server.server_close()
