from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pymupdf
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'generate_quiz.py'


def test_list_does_not_require_api_key(tmp_path):
    source = tmp_path / 'documents'
    source.mkdir()
    (source / 'input.PDF').touch()
    result = subprocess.run([sys.executable, str(SCRIPT), '--documents', str(source),
                             '--env-file', str(tmp_path / 'missing.env'), '--list'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'input.PDF' in result.stdout


@pytest.mark.parametrize('initial_name,next_name,fail_first,expected_code', [
    ('A/a.pdf', 'B/b.pdf', False, 0),
    ('A/b.pdf', 'B/a.pdf', True, 1),
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
        (source / name).parent.mkdir(parents=True, exist_ok=True)
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
        initial_folder = Path(initial_name).parent
        initial_output = output / initial_folder / (initial_folder.name + '.json')
        assert initial_output.exists() is not fail_first
        add(next_name)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == expected_code, stderr
        next_folder = Path(next_name).parent
        assert (output / next_folder / (next_folder.name + '.json')).exists()
        assert len(requests) == 2 and next_name in requests[1]
        assert 'test-secret' not in stdout + stderr
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        server.shutdown()
        server.server_close()
