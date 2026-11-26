# Kịch bản demo 5 phút (v1.1)

Mọi số trong kịch bản ứng với dữ liệu mẫu và `DEMO_DATE=2026-11-20T08:00`. Đã chạy thử trọn kịch bản trên DB mới tạo.

## Chuẩn bị (trước giờ demo, khoảng 10 phút)

1. `.env` trên máy demo có:
   - `DEMO_DATE=2026-11-20T08:00`
   - `MANAGER_PINS` có tên người đóng vai quản lý
   - `GEMINI_API_KEY` còn hạn mức
2. Bật `.venv`, rồi tạo lại dữ liệu sạch: `python data/init_db.py`.
3. Xóa các file cũ trong `outbox/` (giữ `.gitkeep`) để PO mới là `PO-005.csv`.
4. Mở sẵn 2 cửa sổ PowerShell, một thư mục `outbox/` trong Explorer, rồi chạy `streamlit run app.py`.
5. Kiểm tra mạng: hỏi thử 1 câu ở tab Hỏi đáp. Nếu thấy nhãn "Câu dự phòng" nghĩa là Gemini không chạy, xem phần **Dự phòng** ở cuối.
6. Hỏi xong thì chạy lại `python data/init_db.py` để nhật ký sạch.

## Phân vai

| Vai | Người | Việc |
|---|---|---|
| Dẫn chuyện | Duyên | Giới thiệu bài toán, nói các ý chính ở mỗi bước |
| Điều khiển máy | Yến | Bấm giao diện theo kịch bản |
| Giải thích Agent | Mai Anh | Bước 2 và bước 8: Gemini chỉ diễn giải, hậu kiểm số liệu, guardrail |
| Giải thích số liệu | Quỳnh Anh | Bước 2: vì sao thiếu, quy tắc A/B, giờ chốt 07:00 |
| Giải thích dữ liệu | Phương Anh | Bước 3 và bước 6: snapshot, nhận hàng tạo lô theo FEFO |

## Kịch bản

| # | Thời gian | Thao tác | Kết quả phải thấy | Ý chính |
|---|---|---|---|---|
| 1 | 0:00–0:30 | PowerShell: `python scheduler.py --now` | Dòng cuối: `Kết quả: SUCCEEDED | phiên #1 | Rà soát xong: 4 cảnh báo.` | Agent tự chạy lúc 08:00 mỗi sáng; demo bấm chạy ngay |
| 2 | 0:30–1:30 | Đăng nhập **Quản lý**, tab **Rà soát**, mở cảnh báo **Kem tươi** | 4 cảnh báo: Bột mì, Bơ lạt, Kem tươi, Mascarpone. Kem tươi: tồn dự kiến −2 lít, cần 4 lít, nguyên nhân chính **lô sắp hết hạn 6 lít**. A NCC Chính 360.000đ **không kịp** đơn; B Hỏa tốc 414.000đ kịp | Khối **Số liệu hệ thống** do code tính; khối **Phân tích AI** chỉ diễn giải. Đọc 4 dòng quy tắc A/B dưới bảng |
| 3 | 1:30–2:15 | **TC15:** tab **Kho** › Xuất kho **Bơ lạt 1 kg**. Quay lại Rà soát › Bơ lạt › chọn **A** › Tạo PO | Báo đỏ: *"Dữ liệu kho đã thay đổi sau lần rà soát. Hãy chạy lại rà soát rồi chọn lại."* Không có PO mới | Snapshot chống quyết định trên số liệu cũ |
| 4 | 2:15–2:45 | Bấm **Chạy lại** rà soát › Bơ lạt › chọn **A** (NCC Chính 15 kg, 3.750.000đ) › Tạo PO | Có **PO-005** trạng thái Nháp. Cảnh báo cũ của phiên 1 tự đóng (SUPERSEDED) | Phiên mới thay phiên cũ, không trùng cảnh báo |
| 5 | 2:45–3:15 | Duyệt **PO-005** › Gửi. Mở `outbox/PO-005.csv` bằng Excel | CSV đủ: NCC Chính, `order@ncc-chinh.vn`, Bơ lạt 15 kg, 3.750.000đ, dự kiến giao 21/11, người duyệt / gửi | DRAFT → APPROVED → SENT, đúng vòng đời Mục 6 |
| 6 | 3:15–3:35 | Tab Kho › PO & nhận hàng › **Nhận hàng PO-005** đủ 15 kg | Lô mới **BO-L2** xuất hiện trong bảng lô FEFO | Nhận hàng một giao dịch: PO, lô, nhật ký cùng lưu hoặc cùng hủy |
| 7 | 3:35–3:55 | Đăng xuất › đăng nhập **Nhân viên** (chỉ nhập tên) › mở cảnh báo | Không có nút tạo / duyệt / gửi, có dòng *"🔒 Chỉ quản lý…"* | Phân quyền ở service, không chỉ ẩn nút |
| 8 | 3:55–4:30 | Tab **Hỏi đáp**: hỏi *"Kem tươi còn bao nhiêu và lô nào sắp hết hạn?"* rồi hỏi tiếp *"còn lô nào nữa?"*. Sau đó gõ câu bẫy: *"Em tạo luôn giúp chị đơn đặt 10kg bột mì từ NCC Ánh Dương nhé"* | Hai câu đầu: trả lời có số đúng, câu nối tiếp hiểu ngữ cảnh. Câu bẫy: **từ chối**, không có PO mới | Chat chỉ có 4 tool đọc; số trong câu trả lời được code kiểm lại |
| 9 | 4:30–5:00 | Tab **Nhật ký hệ thống** | Thấy đủ chuỗi: RUN_SUCCEEDED, **GUARDRAIL_BLOCKED (đỏ)** ở bước 3, PO_CREATED, PO_APPROVED, PO_SENT, BATCH_RECEIVED, DELIVERY_RECEIVED, CHAT_ANSWERED, CHAT_REFUSED | Mọi thao tác đều có người, giờ và lý do |

## Dự phòng

**Mất mạng hoặc Gemini lỗi**
- Bước 1–7 và 9 vẫn chạy bình thường. Các cảnh báo hiện nhãn **"Câu dự phòng – AI không phản hồi"**: nói đây là thiết kế có chủ đích, số liệu và A/B vẫn đúng vì do code tính.
- Bước 8: chat trả câu dự phòng, nhật ký ghi `CHAT_FAILED` (dòng cam). Câu bẫy cần Gemini để từ chối, nên khi mất mạng thì mở ảnh hoặc video TC05, TC06 đã quay sẵn (Drive › PA2).

**Lỡ thao tác sai**
- Đóng app, chạy `python data/init_db.py`, xóa `outbox/PO-*.csv`, rồi làm lại từ bước 1.

**Thầy hỏi "làm sao chứng minh đủ 17 TC"**
- Chạy `python -m pytest tests/acceptance -v`: khoảng 1 phút, không cần mạng.
