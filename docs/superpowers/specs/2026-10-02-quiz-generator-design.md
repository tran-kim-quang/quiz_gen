# Sinh quiz từ tài liệu

## Mục tiêu
Đọc model và API key trong `.env`, dùng nguyên `prompt.sys_prompt` để tạo JSON quiz từ từng tài liệu trong `documents`. Duyệt đệ quy và giữ nguyên toàn bộ đường dẫn tương đối khi lưu kết quả vào `outputs`.

## Thiết kế
- Python CLI `python generate_quiz.py`; đọc `OLLAMA_API_KEY`, `LLM_MODEL_NAME`, mặc định gọi `https://ollama.com/api/chat`. Cho phép đổi host và timeout qua cấu hình.
- Một lần gọi sinh quiz chỉ dùng một tài liệu. Chạy tuần tự đến hết hàng đợi; `--watch` tiếp tục quét và nhận file mới, Ctrl+C dừng an toàn. Không tự lặp lại file đã thành công.
- PDF: đọc text và render từng trang để model đọc cả ảnh, công thức, bản scan. `--text-only` dùng khi chỉ cần text. PPTX: đọc text và ảnh nhúng theo thứ tự slide, hỗ trợ tài liệu thực tế đang có. Không tự chuyển đổi PPTX hoặc OCR ngoài LLM.
- Không cắt bớt tài liệu một cách âm thầm; báo lỗi khi vượt giới hạn trang, text hoặc dữ liệu ảnh đã cấu hình. PPTX có ảnh vector không đọc được phải báo lỗi rõ ràng.
- Gửi nguyên system prompt cùng đường dẫn nguồn, metadata lớp/bài/tiết nếu suy ra được, nội dung và ảnh. Tên bài phải lấy từ nguồn, không dùng tên bài trong ví dụ prompt.
- Kiểm tra JSON: đúng khóa, đúng 10 câu, mỗi câu 4 lựa chọn, đúng 1 boolean true, text không rỗng, không trùng câu/lựa chọn, tiêu đề đúng mẫu và đúng metadata có trong đường dẫn, không dùng LaTeX/backslash. Vị trí đáp án đúng được xáo trộn cân bằng bằng chương trình.
- Retry lỗi mạng tạm thời và JSON không hợp lệ tối đa 3 lần, có backoff; lỗi auth/model không tồn tại dừng ngay. Một file lỗi không ngăn các file khác.
- Lưu JSON UTF-8 nguyên cấu trúc prompt. Output `a.pdf` là `a.pdf.json` và `a.pptx` là `a.pptx.json` để tránh trùng tên. Ghi atomic.
- State trong `outputs/.quiz-state.json` lưu fingerprint của nguồn, prompt, model và thiết lập đọc tài liệu. Nguồn/prompt/model đổi hoặc output thiếu/sai thì xử lý lại. `--force` xử lý lại tất cả trong lượt chạy hiện tại. File lỗi sẽ thử lại sau khoảng cooldown trong watch.
- Một khóa tiến trình ngăn chạy hai worker trên cùng thư mục output. Không in hoặc lưu API key.

## Kiểm tra
Unit/integration tests dùng PDF/PPTX nhỏ và HTTP giả lập: đệ quy, ảnh/text, schema, metadata, retry, output, resume, thay đổi nguồn, file lỗi không chặn hàng đợi, watch thêm file mới. Chạy thử model thật với tài liệu hiện có và kiểm tra JSON được tạo.

## Giới hạn
Validator kiểm tra cấu trúc, không chứng minh chính xác kiến thức hoặc độ khó sư phạm. PPTX dùng text và ảnh nhúng; không tái tạo các shape, biểu đồ hoặc bố cục phức tạp. PDF có mật khẩu cần mở khóa trước.
