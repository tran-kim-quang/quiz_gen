# Quiz Studio: giao diện file/folder

## Mục tiêu và phạm vi
Giao diện web local tiếng Việt cho bộ sinh quiz hiện có. Người dùng chọn một PDF/PPTX hoặc cả folder có thư mục con, bấm xử lý, xem tiến độ và tải JSON/ZIP. Model/key và system prompt dùng cấu hình hiện có. Không sửa tên tài liệu gốc.

## Luồng
- Hai loại input: File (một file) và Folder (browser directory picker). Folder giữ cả tên root đã chọn và mọi cấp con qua `webkitRelativePath`.
- Upload multipart với từng file và danh sách đường dẫn gốc. Loại bỏ file không hỗ trợ trong folder và thông báo số file bỏ qua. Ứng dụng không áp giới hạn số file/tổng dung lượng; giới hạn thực tế do ổ đĩa, HTTP, trình duyệt và model.
- Copy vào vùng làm việc `.quiz-ui/jobs/<id>/input`, normalize từng segment thành không dấu, giữ chữ hoa và khoảng trắng. `đ/Đ` thành `d/D`. Bảng ánh xạ lưu chính xác tên gốc Unicode. Tên trùng sau normalize được thêm suffix hash ổn định; không ghi đè và không suy đoán dấu ngược.
- Kiểm tra đường dẫn tương đối, cấm `..`, đường dẫn tuyệt đối, slash/backslash trong segment, control chars và trùng đường dẫn upload. Không sửa nội dung tài liệu. Chỉ bắt đầu xử lý sau khi toàn bộ batch đã được upload, kiểm tra và normalize.
- Mỗi job xử lý từng file tuần tự bằng `QuizRunner` hiện có. Upload mới được nhận vào queue FIFO; một worker duy nhất xử lý một job và một tài liệu tại một thời điểm. Pipeline thêm mapping output và progress callback/stop event, không đổi luồng CLI mặc định.
- Output trong `outputs/ui/<id>/<tên folder gốc>/.../<tên file gốc>.<ext>.json`. File đơn có basename gốc. ZIP chứa đúng cây output gốc, chỉ JSON quiz; không chứa state, key hoặc bản input.
- Nút dừng yêu cầu hoàn thành file đang xử lý rồi dừng hàng đợi; kết quả đã xong có thể tải ngay. Retry LLM giữ nguyên.
- Poll progress 1 giây; hiển thị từng file, trạng thái, số thành công/lỗi và thanh tiến độ. Cho xem quiz trong modal và tải JSON từng file. Khôi phục job gần nhất khi reload tab qua localStorage. Job metadata được ghi xuống đĩa; restart server đánh dấu job đang chạy là interrupted, cho tải kết quả hoàn thành.

## Giao diện
Sidebar nhỏ, header tên sản phẩm/model cấu hình, vùng chọn nguồn với hai tab File/Folder, vùng xem cây tên gốc/tên không dấu, nút bắt đầu. Phần kết quả có bảng file, tiến độ, nút dừng và tải ZIP. Responsive màn hình nhỏ, input có label, focus visible, cập nhật trạng thái có aria-live. Dùng HTML/CSS/JS và SVG; không cần framework frontend.

## Cấu trúc
FastAPI + uvicorn + python-multipart. `quiz_generator/inputs.py` phụ trách normalize/mapping, `config.py` tạo generator chung CLI/UI, `jobs.py` quản lý worker/jobs/progress, `web.py` REST/static. Entry `app.py` mặc định 127.0.0.1:8000.

## Kiểm tra
Test normalize Unicode/collision/traversal; pipeline map output và stop/progress; REST multipart/ZIP/single file/auth exposure/active job limits; server restart phục hồi metadata. Kiểm tra trình duyệt thật chọn file/folder, tree preview, tiến độ, xem quiz, download và responsive. API LLM giả lập cho UI tests; key thật chỉ dùng nếu cần một smoke test.
