from io import BytesIO
import json
import os
import socket
import threading
import time
from zipfile import ZipFile

import httpx
import pymupdf
import pytest

pytestmark = [pytest.mark.browser,
              pytest.mark.skipif(os.environ.get('QUIZ_UI_BROWSER_TESTS') != '1', reason='Enable QUIZ_UI_BROWSER_TESTS=1 for browser checks')]


@pytest.fixture
def ui_server(tmp_path, quiz):
    import uvicorn
    from quiz_generator.llm import OllamaGenerator
    from quiz_generator.web import create_app
    def handler(request):
        time.sleep(0.05)
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(quiz)}})
    def factory():
        return OllamaGenerator('ui-test-key', 'gemma4:31b', 'SYSTEM',
                               client=httpx.Client(transport=httpx.MockTransport(handler)), retry_delay=0)
    config = tmp_path / '.env'
    config.write_text('OLLAMA_API_KEY=ui-test-key\nLLM_MODEL_NAME=gemma4:31b\n')
    app = create_app(work_root=tmp_path / 'work', output_root=tmp_path / 'output', generator_factory=factory, env_file=config)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    yield f'http://127.0.0.1:{port}'
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()


def make_pdf(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), 'Triangle source')
        document.save(path)


def test_browser_folder_restores_tree_and_downloads_zip(ui_server, tmp_path, quiz):
    from playwright.sync_api import sync_playwright, expect
    root = tmp_path / 'Lớp 7'
    first = root / 'Môn/Toán/Bài 12/Tiết 36/Đề.pdf'
    second = root / 'Môn/Toán/Bài 12/Tiết 36/Đề <kiểm tra>.pdf'
    make_pdf(first)
    make_pdf(second)
    (root / 'Ghi chú.txt').write_text('Ignored file')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1080}, accept_downloads=True)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(ui_server)
        page.get_by_role('tab', name='Cả thư mục').click()
        page.locator('#folder-input').set_input_files(str(root))
        expect(page.locator('#selection-summary')).to_contain_text('2 tài liệu')
        expect(page.locator('#selection-note')).to_contain_text('1 file')
        expect(page.locator('#path-tree')).to_contain_text('Lớp 7')
        page.get_by_role('button', name='Tên xử lý').click()
        expect(page.locator('#path-tree')).to_contain_text('Lop 7')
        page.get_by_role('button', name='Tên gốc').click()
        page.get_by_role('button', name='Bắt đầu tạo quiz').click()
        expect(page.locator('#job-status')).to_have_text('Hoàn tất', timeout=15000)
        expect(page.locator('#file-table tbody tr')).to_have_count(1)
        assert page.locator('kiểm').count() == 0
        page.get_by_role('button', name='Xem quiz').first.click()
        expect(page.locator('#quiz-dialog')).to_be_visible()
        expect(page.locator('#quiz-title')).to_have_text(quiz['title'])
        expect(page.locator('.quiz-question')).to_have_count(10)
        page.get_by_role('button', name='Đóng xem quiz').click()
        with page.expect_download() as download:
            page.get_by_role('link', name='Tải toàn bộ ZIP').click()
        with ZipFile(BytesIO(open(download.value.path(), 'rb').read())) as archive:
            assert set(archive.namelist()) == {
                'Lớp 7/Môn/Toán/Bài 12/Tiết 36/Tiết 36.json',
            }
        page.reload()
        expect(page.locator('#job-status')).to_have_text('Hoàn tất')
        assert errors == []
        browser.close()


def test_browser_single_file_unicode_and_mobile_layout(ui_server, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    source = tmp_path / 'Đề kiểm tra.pdf'
    make_pdf(source)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844}, accept_downloads=True)
        page.goto(ui_server)
        page.locator('#file-input').set_input_files(str(source))
        expect(page.locator('#selection-summary')).to_contain_text('1 tài liệu')
        page.get_by_role('button', name='Bắt đầu tạo quiz').click()
        expect(page.locator('#job-status')).to_have_text('Hoàn tất', timeout=15000)
        expect(page.locator('#file-table')).to_contain_text(source.name)
        assert page.locator('kiểm').count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        with page.expect_download() as download:
            page.get_by_role('link', name='Tải JSON').click()
        assert download.value.suggested_filename == source.name + '.json'
        browser.close()
