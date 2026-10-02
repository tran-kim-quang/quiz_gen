# Quiz Generator Implementation Plan

> Execute inline using superpowers:executing-plans and test-driven-development.

**Goal:** Sinh JSON quiz tuần tự từ tài liệu, giữ nguyên cây thư mục.
**Architecture:** Tách bộ đọc tài liệu, Ollama client/validator và pipeline/state; CLI ghép các thành phần.
**Tech Stack:** Python 3.10+, httpx, python-dotenv, PyMuPDF, Pillow, pytest.
**Spec:** `docs/superpowers/specs/2026-10-02-quiz-generator-design.md`

## Global Constraints
- Giữ nguyên `prompt.py` và `.env`; không ghi key ra log.
- Một tài liệu mỗi quiz; không cắt nguồn âm thầm.
- Ollama Cloud không dùng JSON schema/format parameter.
- Output đúng schema prompt, UTF-8, ghi atomic và mirror đường dẫn nguồn.

## Review Focus
- PDF scan/ảnh minh họa: gửi ảnh trang, không bỏ qua nguồn.
- Nguồn đổi/JSON output hỏng: chạy lại, không skip sai.
- File cùng stem khác đuôi: output không trùng.
- Lỗi auth/quota/JSON: retry đúng loại; không giữ JSON sai.
- Watch: thêm file mới được xử lý, file lỗi có cooldown, Ctrl+C không hỏng state.

## Tasks
1. **Đọc tài liệu:** `quiz_generator/documents.py`, `tests/test_documents.py`. Viết test đệ quy, PDF text/scan, PPTX theo thứ tự slide và giới hạn nguồn; xác nhận thất bại; triển khai `discover_documents(root)`, `read_document(path, ...) -> Document`.
2. **LLM và schema:** `quiz_generator/llm.py`, `tests/test_llm.py`. Test exact JSON keys/count/booleans/title, cân bằng đáp án, httpx MockTransport cho request/retry/auth. Xác nhận thất bại rồi triển khai `validate_quiz`, `OllamaGenerator.generate(document, relative_path)`.
3. **Pipeline và CLI:** `quiz_generator/pipeline.py`, `generate_quiz.py`, `tests/test_pipeline.py`. Test mirror, skip/resume, fingerprint, error isolation, watch và khóa tiến trình; xác nhận thất bại rồi triển khai `QuizRunner.run_once()` và CLI.
4. **Hướng dẫn và kiểm tra:** `requirements.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore`, `README.md`. Cài venv, chạy toàn bộ pytest, kiểm tra CLI, chạy thử thật một tài liệu và kiểm tra output. Không có Git repository nên không tạo worktree/commit.
