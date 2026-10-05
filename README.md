# LLM Quiz Generator

Sinh quiz bằng model và API key trong `.env`, dùng nguyên `sys_prompt` trong `prompt.py`. Script quét đệ quy `documents`, tìm các **folder lá** (folder không còn folder con), rồi dùng toàn bộ PDF/PPTX trực tiếp trong từng folder lá để sinh một quiz. Mỗi request LLM chỉ chứa dữ liệu của đúng một folder lá.

## Giao diện Quiz Studio

Sau khi cài dependencies, chạy:

```bash
.venv/bin/python app.py
```

Mở **http://127.0.0.1:8000**. Có thể đổi port với `--port 8001`, hoặc dùng file cấu hình khác với `--env-file /path/.env`.

1. Chọn **Một file** để tải một PDF/PPTX, hoặc **Cả thư mục** để tải các tài liệu trong folder và toàn bộ folder con.
2. Xem cây thư mục ở **Tên gốc** hoặc **Tên xử lý**, rồi bấm **Bắt đầu tạo quiz**. Mỗi folder lá được sinh lần lượt theo số `T`.
3. Theo dõi trạng thái từng folder lá, bấm **Xem quiz**, tải JSON riêng hoặc **Tải toàn bộ ZIP**.

Nút **Dừng sau folder này** cho folder đang gọi LLM hoàn thành rồi dừng hàng đợi. Các quiz đã xong vẫn tải được. Reload tab giữ lại lượt gần nhất; sidebar lưu các lượt gần đây. Nếu server khởi động lại giữa lượt, lượt đó được đánh dấu gián đoạn và kết quả hoàn thành vẫn được giữ.

Giao diện dùng cùng `.env` và `prompt.py` với CLI; không đưa API key lên trình duyệt. Có thể upload thêm file/folder khi một lượt đang chạy: mỗi batch mới vào hàng đợi FIFO, và toàn hệ thống chỉ gọi LLM cho một folder lá tại một thời điểm.

### Normalize input và output cho tool upload hàng loạt

Giao diện **copy** nguồn vào vùng làm việc, bỏ dấu trong từng tên file/thư mục trước khi bắt đầu xử lý. Giữ nguyên chữ hoa, khoảng trắng và các cấp thư mục. Tài liệu gốc trên máy không bị đổi tên.

```text
Input bạn chọn:
Lớp 7/Môn/Toán/Bài 12/Tiết 36/Đề.pdf
Lớp 7/Môn/Toán/Bài 12/Tiết 36/Bài tập.pptx

Bản làm việc:
Lop 7/Mon/Toan/Bai 12/Tiet 36/De.pdf
Lop 7/Mon/Toan/Bai 12/Tiet 36/Bai tap.pptx

Output trên đĩa:
outputs/ui/<job-id>/Toan7/Bài 12_Tổng các góc trong một tam giác/T36_Tổng các góc trong một tam giác/quiz.json

Bên trong ZIP:
Toan7/Bài 12_Tổng các góc trong một tam giác/T36_Tổng các góc trong một tam giác/quiz.json
```

Bảng ánh xạ input được lưu tại `.quiz-ui/jobs/<job-id>/path-mapping.json`. Output lấy môn, lớp, bài, tiết và tên bài từ `title` đã được kiểm tra, rồi dùng tên course folder theo cây `QUIZ LAM LAI`: `Toan7`, `VatLy7`, `Hoa7`, `Sinh7`, `Van7`, `Su7`, `DiaLy7`, `Anh7`, `Tin7`, `CongNghe7`, `GDCD7`; lớp 10–12 dùng `GDKTPL10`–`GDKTPL12` cho Giáo dục kinh tế và pháp luật.

Bài đặc biệt không có số được giữ nguyên tên folder nguồn, ví dụ `Luyện tập chung 1 _ chương II/T22_.../quiz.json` hoặc `Bài tập cuối chương II/T24_.../quiz.json`. Với nguồn dạng này, đặt folder bài ngay phía trên folder `Tiet <số>`; validator buộc model chép nguyên tên folder vào title và formatter tạo slug giống tool upload Node.

Mỗi JSON có `type: "LESSON"`, `order` 1-based cho câu hỏi và đáp án. ZIP chỉ chứa JSON quiz theo cấu trúc `Course/Bài/Tiết/quiz.json`, không chứa input, state hoặc key; có thể giải nén trực tiếp vào thư mục `quiz` của tool upload hàng loạt.

Ứng dụng không đặt giới hạn số file hoặc tổng dung lượng upload. Khả năng thực tế phụ thuộc dung lượng ổ đĩa, bộ nhớ, giới hạn HTTP/trình duyệt và context của model. Folder có file khác PDF/PPTX sẽ bỏ qua các file đó và thông báo số lượng. Cây thư mục không thể lưu output do tên file/thư mục xung đột sẽ được báo trước khi gọi LLM. Nút chọn folder nên dùng Chrome/Edge/Firefox bản mới hỗ trợ directory picker.

Khi upload nhiều:

- Từng batch hiện trạng thái **Đang chờ trong hàng đợi** và vị trí FIFO.
- Trong một batch, các folder lá được xử lý lần lượt theo số `T` tăng dần; folder không có số `T` dùng thứ tự tên tự nhiên.
- Batch tiếp theo chỉ bắt đầu sau khi batch hiện tại hoàn thành hoặc dừng.
- Có thể huỷ một batch đang chờ mà không gọi LLM; với batch đang chạy, nút dừng hoàn tất folder hiện tại rồi ngừng.

## Cài đặt

Yêu cầu Python 3.10+ trên Windows, Linux hoặc macOS.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

File `.env` hiện có được giữ nguyên. Khi thiết lập dự án ở máy khác, copy `.env.example` thành `.env` và điền:

```dotenv
OLLAMA_API_KEY=your_ollama_api_key
LLM_MODEL_NAME=gemma4:31b
```

Gọi trực tiếp `https://ollama.com/api/chat`, không cần chạy Ollama local. Có thể đổi `OLLAMA_BASE_URL` trong `.env`. Biến môi trường của shell được ưu tiên hơn file `.env`. Key không được ghi vào JSON/state/log.

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
python generate_quiz.py --documents /path/documents --output /path/outputs \
  --env-file /path/.env --watch --interval 15

python generate_quiz.py --help
```

Đường dẫn mặc định được tính từ thư mục chứa script, nên có thể chạy script từ thư mục khác. Thư mục nguồn cần tồn tại; với `--watch` có thể bắt đầu từ thư mục rỗng.

## Input và output

Hỗ trợ `.pdf` và `.pptx`, kể cả đuôi viết hoa, ở mọi cấp thư mục. Toàn bộ tài liệu trực tiếp trong cùng một folder lá được gộp làm nguồn cho một quiz. Không đặt tài liệu trực tiếp trong folder vẫn còn folder con; ứng dụng sẽ báo lỗi cấu trúc thay vì âm thầm bỏ qua.

```text
documents/
└── Lop/Lop7/Mon/Toan7/Bai12/Tiet36/
    ├── lesson.pdf
    └── TOAN7_C4_B12_T36_2_fixed.pptx

outputs/
├── .quiz-worker.lock
└── Toan7/
    └── Bài 12_Tổng các góc trong một tam giác/
        └── T36_Tổng các góc trong một tam giác/
            └── quiz.json

.outputs-quiz-state.json
```

Đường dẫn JSON lấy từ `title` hợp lệ và tuân thủ đúng bốn tầng mà `scripts/local-quiz-manifest.mjs` yêu cầu. Với title có cả tên tiết và tên bài, ví dụ `Tiết 3: Getting Started - Family Life - Family Life`, output là `Anh10/Bài 1_Family Life/T3_Getting Started - Family Life/quiz.json`. Nếu hai nguồn ánh xạ tới cùng khóa học/bài/tiết theo slug của importer, lượt thứ hai báo lỗi thay vì ghi đè quiz trước.

- **PDF:** gửi text trích xuất và ảnh của từng trang để model đọc cả công thức, hình minh họa, bản scan. PDF có mật khẩu cần được mở khóa trước.
- **PPTX:** gửi text và ảnh nhúng theo thứ tự slide. PPTX đang có trong dự án gồm 20 slide ảnh và được hỗ trợ theo cách này. Shape, biểu đồ hoặc bố cục PPTX phức tạp không được render hoàn chỉnh; hãy xuất thành PDF để giữ đủ hình thức trình bày.
- `--text-only` chỉ gửi text để giảm dữ liệu, thích hợp với nguồn không cần hình. Tài liệu hoàn toàn dạng ảnh sẽ báo lỗi ở chế độ này.

Model lấy môn, lớp, bài, tiết và **tên bài từ nhóm tài liệu trong folder lá** cùng đường dẫn nguồn. Script kiểm tra lớp/bài/tiết nếu thư mục có dạng `Lop7`, `Bai12`, `Tiet36` (cũng hỗ trợ chữ có dấu và khoảng trắng). Không dùng tên bài trong ví dụ của prompt làm tên mặc định.

JSON model trả về đúng các khóa của prompt: `title`, `description`, `questions`; đúng 10 câu, mỗi câu 4 lựa chọn và một đáp án đúng. Trước khi lưu, chương trình thêm `type: "LESSON"` cùng `order` cho câu hỏi và đáp án để tương thích tool upload. Validator kiểm tra kiểu boolean, text rỗng, khóa thừa/thiếu/trùng, câu hỏi/phương án trùng, mẫu tiêu đề và ký tự LaTeX. Chương trình xáo trộn phương án để đáp án đúng xuất hiện 2–3 lần ở mỗi vị trí, giữ nguyên thứ tự độ khó của câu hỏi.

Validator kiểm tra cấu trúc; độ chính xác kiến thức, mức độ khó và chất lượng phương án nhiễu vẫn cần được xem lại trước khi dùng cho học sinh.

## Retry, state và giới hạn

Lỗi mạng, timeout, HTTP 408/429/5xx và JSON sai được thử lại tối đa 3 lần, tính cả lần đầu. Nếu JSON sai, request tiếp theo giữ nguồn và yêu cầu model sửa. Lỗi key/quyền truy cập/model hoặc endpoint (401/403/404) dừng worker. Folder khác bị lỗi được ghi nhận và không ngăn folder tiếp theo.

State lưu fingerprint của nội dung nguồn, system prompt, model, host, cấu hình sinh và cấu hình đọc. Nguồn/prompt/model đổi, output bị mất hoặc JSON sai thì sinh lại. State CLI nằm cạnh thư mục output (`.outputs-quiz-state.json` với output mặc định); state của UI nằm trong `.quiz-ui/jobs`. Vì vậy cây output chỉ chứa JSON mà tool upload có thể nhập. JSON và state được ghi atomic. Chỉ một worker được dùng cùng thư mục output; khóa tự nhả khi tiến trình kết thúc.

Khi một folder nguồn bị xóa hoặc đổi tên, state và JSON do folder cũ sở hữu được gỡ khỏi cây output ở lượt quét tiếp theo. Folder mới sau đó có thể tạo lại đúng target mà không bị chặn bởi kết quả đã stale.

Trong `--watch`, file lỗi được thử lại sau 300 giây; đổi bằng `--failure-cooldown`. Nguồn thay đổi được thử lại ngay ở lượt quét sau. Để thử lại ngay cả khi chưa hết cooldown:

```bash
python generate_quiz.py --force
```

Giới hạn mặc định cho toàn bộ nhóm tài liệu trong một folder lá: 80 trang/slide, 200.000 ký tự text, 20 MB tổng ảnh base64; cạnh dài mỗi ảnh tối đa 1.600 pixel. Vượt giới hạn sẽ báo lỗi, **không cắt bỏ một phần nguồn**. Có thể điều chỉnh:

```bash
python generate_quiz.py --max-pages 120 --max-chars 300000 \
  --max-image-mb 30 --image-edge 2000
```

Các giới hạn của dịch vụ/model vẫn áp dụng khi tăng các giá trị này. Timeout, số lần thử, thời gian backoff, temperature và giới hạn token output có trong `.env.example`.

Mã thoát: `0` lượt hoàn tất, `1` có folder lỗi/đang chờ retry, `2` lỗi cấu hình/API nghiêm trọng, `130` dừng bằng Ctrl+C. `--limit` tính số folder lá đã thử xử lý, gồm cả folder lỗi; retry cùng folder không tính thành lượt mới.

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
