# LLM Quiz Generator

Sinh quiz bằng model và API key trong `.env`, dùng nguyên `sys_prompt` trong `prompt.py`. Mỗi lần sinh chỉ tham chiếu **một tài liệu**. Script quét đệ quy `documents`, xử lý tuần tự từng file khác nhau, kiểm tra JSON rồi lưu theo đúng cây thư mục nguồn.

## Giao diện Quiz Studio

Sau khi cài dependencies, chạy:

```bash
.venv/bin/python app.py
```

Mở **http://127.0.0.1:8000**. Có thể đổi port với `--port 8001`, hoặc dùng file cấu hình khác với `--env-file /path/.env`.

1. Chọn **Một file** để tải một PDF/PPTX, hoặc **Cả thư mục** để tải các tài liệu trong folder và toàn bộ folder con.
2. Xem cây thư mục và kế hoạch tài liệu. Một cặp PPTX hợp lệ hiện thành một tài liệu **Sẽ ghép** với tên của cả hai part; file lẻ hiện thành **Tài liệu độc lập**.
3. Bấm **Bắt đầu tạo quiz**, theo dõi các trạng thái **Đang ghép**, **Dùng bản đã ghép**, **Đang đọc** và **Đang tạo**; sau đó xem quiz, tải JSON riêng hoặc tải toàn bộ ZIP.

Preview chỉ là kế hoạch theo tên file. Server lập lại kế hoạch sau khi upload an toàn; nếu PPTX không hỗ trợ ghép (ví dụ slide không có ảnh), hai part được cảnh báo và xử lý độc lập. Lỗi cấu hình Node/npm được báo lỗi thay vì âm thầm bỏ qua ghép.

Nút **Dừng sau file này** cho file đang gọi LLM hoàn thành rồi dừng hàng đợi. Các quiz đã xong vẫn tải được. Reload tab giữ lại lượt gần nhất; sidebar lưu các lượt gần đây. Nếu server khởi động lại giữa lượt, lượt đó được đánh dấu gián đoạn và kết quả hoàn thành vẫn được giữ.

Giao diện dùng cùng `.env` và `prompt.py` với CLI; không đưa API key lên trình duyệt. Có thể upload thêm file/folder khi một lượt đang chạy: mỗi batch mới vào hàng đợi FIFO, và toàn hệ thống chỉ gọi LLM cho một tài liệu tại một thời điểm.

### Normalize tên và giữ đúng dấu khi xuất

Giao diện **copy** nguồn vào vùng làm việc, bỏ dấu trong từng tên file/thư mục trước khi bắt đầu xử lý. Giữ nguyên chữ hoa, khoảng trắng và các cấp thư mục. Tài liệu gốc trên máy không bị đổi tên.

```text
Input bạn chọn:
Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề.pdf

Bản làm việc:
Lop 7/Mon/Toan/Bai 12/Tiet 36/De.pdf

Output trên đĩa:
outputs/ui/<job-id>/Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề.json

Bên trong ZIP:
Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề.json
```

Bảng ánh xạ được lưu tại `.quiz-ui/jobs/<job-id>/path-mapping.json`. Output lấy lại **tên gốc chính xác** từ bảng này, kể cả Unicode tổ hợp; không đoán ngược dấu từ tên đã normalize. Nếu các tên như `Má` và `Ma` trùng sau bỏ dấu, bản làm việc thêm suffix riêng, còn output vẫn giữ tên gốc.

Chế độ File đơn giữ basename gốc khi xuất, không thêm folder từ máy tính. Chế độ Folder giữ cả tên folder gốc đã chọn và mọi cấp con có tài liệu. ZIP chỉ chứa JSON quiz, không chứa input, state hoặc key.

Ứng dụng không đặt giới hạn số file hoặc tổng dung lượng upload. Khả năng thực tế phụ thuộc dung lượng ổ đĩa, bộ nhớ, giới hạn HTTP/trình duyệt và context của model. Folder có file khác PDF/PPTX sẽ bỏ qua các file đó và thông báo số lượng. Cây thư mục không thể lưu output do tên file/thư mục xung đột sẽ được báo trước khi gọi LLM. Nút chọn folder nên dùng Chrome/Edge/Firefox bản mới hỗ trợ directory picker.

Khi upload nhiều:

- Từng batch hiện trạng thái **Đang chờ trong hàng đợi** và vị trí FIFO.
- Trong một batch, tài liệu được xử lý lần lượt theo đường dẫn đã normalize.
- Batch tiếp theo chỉ bắt đầu sau khi batch hiện tại hoàn thành hoặc dừng.
- Có thể huỷ một batch đang chờ mà không gọi LLM; với batch đang chạy, nút dừng hoàn tất file hiện tại rồi ngừng.

## Cài đặt trên Ubuntu

Yêu cầu Python 3.10+ và Node.js 20+ trên Ubuntu. Python chạy pipeline/website; Node cùng `jszip` và `pptxgenjs` chạy worker ghép PPTX native trong repo này.

Cài tự động dependencies vào `.venv` và `node_modules`:

```bash
./setup-ubuntu.sh
```

Script kiểm tra phiên bản, tạo hoặc tái sử dụng `.venv`, cài `requirements.txt` và chạy `npm ci`. Script không tạo, ghi đè hoặc sửa file cấu hình bí mật. Việc khởi chạy ứng dụng cũng không tự cài package.

Cài thủ công tương đương:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
npm ci
```

File `.env` hiện có được giữ nguyên. Khi thiết lập dự án ở máy khác, copy `.env.example` thành `.env` và điền:

```dotenv
OLLAMA_API_KEY=your_ollama_api_key
LLM_MODEL_NAME=gemma4:31b
```

Gọi trực tiếp `https://ollama.com/api/chat`, không cần chạy Ollama local. Có thể đổi `OLLAMA_BASE_URL` trong `.env`. Biến môi trường của shell được ưu tiên hơn file `.env`. Key không được ghi vào JSON/state/log.

Ba đường dẫn dùng chung mặc định là:

```dotenv
QUIZ_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/documents
QUIZ_MERGED_DOCUMENTS_DIR=/home/meconlonton/Documents/quiz/merged-documents
QUIZ_OUTPUT_DIR=/home/meconlonton/Documents/quiz/documents/output
```

CLI flag ưu tiên cao hơn biến shell, biến shell ưu tiên cao hơn file cấu hình. CLI, watch và website phải trỏ chung `QUIZ_MERGED_DOCUMENTS_DIR` để tái sử dụng cùng bản ghép.

## Chạy

Sinh liên tục lần lượt cho các tài liệu hiện có; khi hết hàng đợi, kết thúc:

```bash
python generate_quiz.py
```

Chạy liên tục và quét nhận thêm file mới hoặc file đã thay đổi mỗi 10 giây:

```bash
python generate_quiz.py --watch
```

Ctrl+C để dừng. Chạy lại cùng lệnh để tiếp tục; file đã hoàn thành được bỏ qua. `--watch` không tự sinh vô hạn nhiều bản cho cùng tài liệu.

Các lệnh khác:

```bash
# Liệt kê file tìm thấy, không gọi API
python generate_quiz.py --list

# Chạy thử một file chưa hoàn thành
python generate_quiz.py --limit 1

# Sinh lại tất cả trong lượt đầu; ghi đè JSON tương ứng
python generate_quiz.py --force

# Đổi thư mục, file cấu hình và khoảng quét
python generate_quiz.py --documents /path/documents \
  --merged-documents /path/merged-documents --output /path/outputs \
  --env-file /path/.env --watch --interval 15

python generate_quiz.py --help
```

Đường dẫn mặc định được tính từ thư mục chứa script, nên có thể chạy script từ thư mục khác. Thư mục nguồn cần tồn tại; với `--watch` có thể bắt đầu từ thư mục rỗng.

## Tự nhận diện và ghép PPTX part

Đây là chế độ mặc định của CLI, `--watch` và website. Hệ thống quét đệ quy, nhận diện không phân biệt hoa/thường và dấu tiếng Việt. Tên dạng `T36_1.pptx`, `T36_2.pptx` hoặc `TOAN7_C4_B12_T36_1_fixed.pptx` được ghép khi có đúng một part 1 và một part 2 thuộc cùng lớp/bài/tiết. Thứ tự luôn là part 1 rồi part 2.

Ví dụ một cặp hợp lệ:

```text
documents/Toan7/Toan7 pptx/Bai 12/T36_1.pptx
documents/Toan7/Toan7 pptx/Bai 12/T36_2.pptx

merged-documents/Toan7/Bai 12/T36/T36_Slide.pptx
documents/output/Toan7/Bai 12/T36/T36_Slide.json
```

Nếu chỉ có một part, trùng part, tên không nhận diện được hoặc tiết trong folder mâu thuẫn với tên file, mỗi file được xử lý độc lập và có cảnh báo. Ví dụ `T36_1.pptx` đứng một mình tạo `Toan7/Bai 12/T36/T36_1.json`.

File ghép dùng chung là **bất biến** đối với workflow: nếu `T36_Slide.pptx` đã tồn tại, hệ thống dùng lại ngay, không so sánh timestamp/hash và không ghi đè dù part nguồn thay đổi. Muốn cố ý ghép lại, hãy dừng worker rồi tự chuyển hoặc xóa file ghép hiện có; lượt tiếp theo sẽ tạo lại từ part 1 và part 2.

`--list` chỉ in kế hoạch `merge`, `reuse merged`, `standalone` và cảnh báo; không tạo PPTX, JSON hay gọi API:

```bash
python generate_quiz.py --list
```

## Input và output

Hỗ trợ `.pdf` và `.pptx`, kể cả đuôi viết hoa, ở mọi cấp thư mục. Mỗi tài liệu logic sinh một quiz; một cặp PPTX part hợp lệ được tính là một tài liệu logic.

```text
documents/
└── Lop/Lop7/Mon/Toan7/Bai12/Tiet36/
    ├── lesson.pdf
    └── TOAN7_C4_B12_T36_2_fixed.pptx

documents/output/
├── .quiz-state.json
├── .quiz-worker.lock
└── Lop/Lop7/Mon/Toan7/Bai12/Tiet36/
    ├── lesson.json
    └── TOAN7_C4_B12_T36_2_fixed.json
```

JSON dùng stem nguồn. Planner báo lỗi trước khi xử lý nếu hai tài liệu dẫn đến cùng đường dẫn JSON, vì vậy không có file nào âm thầm ghi đè file khác. Tài liệu không nhận diện được lớp/bài/tiết giữ cây thư mục nguồn an toàn; cặp part hợp lệ dùng cấu trúc chuẩn lớp/bài/tiết như trên.

- **PDF:** gửi text trích xuất và ảnh của từng trang để model đọc cả công thức, hình minh họa, bản scan. PDF có mật khẩu cần được mở khóa trước.
- **PPTX:** gửi text và ảnh nhúng theo thứ tự slide. PPTX đang có trong dự án gồm 20 slide ảnh và được hỗ trợ theo cách này. Shape, biểu đồ hoặc bố cục PPTX phức tạp không được render hoàn chỉnh; hãy xuất thành PDF để giữ đủ hình thức trình bày.
- `--text-only` chỉ gửi text để giảm dữ liệu, thích hợp với nguồn không cần hình. Tài liệu hoàn toàn dạng ảnh sẽ báo lỗi ở chế độ này.

Model lấy môn, lớp, bài, tiết và **tên bài từ tài liệu** cùng đường dẫn nguồn. Script kiểm tra lớp/bài/tiết nếu thư mục có dạng `Lop7`, `Bai12`, `Tiet36` (cũng hỗ trợ chữ có dấu và khoảng trắng). Không dùng tên bài trong ví dụ của prompt làm tên mặc định.

JSON đúng các khóa của prompt: `title`, `description`, `questions`; đúng 10 câu, mỗi câu 4 lựa chọn và một đáp án đúng. Validator kiểm tra kiểu boolean, text rỗng, khóa thừa/thiếu/trùng, câu hỏi/phương án trùng, mẫu tiêu đề và ký tự LaTeX. Chương trình xáo trộn phương án để đáp án đúng xuất hiện 2–3 lần ở mỗi vị trí, giữ nguyên thứ tự độ khó của câu hỏi.

Validator kiểm tra cấu trúc; độ chính xác kiến thức, mức độ khó và chất lượng phương án nhiễu vẫn cần được xem lại trước khi dùng cho học sinh.

## Retry, state và giới hạn

Lỗi mạng, timeout, HTTP 408/429/5xx và JSON sai được thử lại tối đa 3 lần, tính cả lần đầu. Nếu JSON sai, request tiếp theo giữ nguồn và yêu cầu model sửa. Lỗi key/quyền truy cập/model hoặc endpoint (401/403/404) dừng worker. File khác bị lỗi được ghi nhận và không ngăn tài liệu tiếp theo.

State lưu fingerprint của nội dung nguồn, system prompt, model, host, cấu hình sinh và cấu hình đọc. Nguồn/prompt/model đổi, output bị mất hoặc JSON sai thì sinh lại. JSON và state được ghi atomic. Chỉ một worker được dùng cùng thư mục output; khóa tự nhả khi tiến trình kết thúc.

Trong `--watch`, file lỗi được thử lại sau 300 giây; đổi bằng `--failure-cooldown`. Nguồn thay đổi được thử lại ngay ở lượt quét sau. Để thử lại ngay cả khi chưa hết cooldown:

```bash
python generate_quiz.py --force
```

Giới hạn mặc định mỗi tài liệu: 80 trang/slide, 200.000 ký tự text, 20 MB tổng ảnh base64, cạnh dài ảnh tối đa 1.600 pixel. Vượt giới hạn sẽ báo lỗi, **không cắt bỏ một phần nguồn**. Có thể điều chỉnh:

```bash
python generate_quiz.py --max-pages 120 --max-chars 300000 \
  --max-image-mb 30 --image-edge 2000
```

Các giới hạn của dịch vụ/model vẫn áp dụng khi tăng các giá trị này. Timeout, số lần thử, thời gian backoff, temperature và giới hạn token output có trong `.env.example`.

Mã thoát: `0` lượt hoàn tất, `1` có file lỗi/đang chờ retry, `2` lỗi cấu hình/API nghiêm trọng, `130` dừng bằng Ctrl+C. `--limit` tính số file đã thử xử lý, gồm cả file lỗi; retry cùng file không tính thành file mới.

## Kiểm tra

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Tests dùng PDF/PPTX nhỏ, HTTP giả lập và subprocess CLI; không sử dụng key thật hay gọi Ollama Cloud.

Kiểm tra giao diện bằng Chromium thật:

```bash
python -m playwright install chromium
QUIZ_UI_BROWSER_TESTS=1 python -m pytest -q
```

Browser tests dùng LLM giả lập, kiểm tra file/folder upload, dấu và cây thư mục trong ZIP, JSON download, xem quiz, reload và layout mobile. Khi không bật biến trên, hai browser tests được bỏ qua để không yêu cầu cài browser cho kiểm tra backend.
