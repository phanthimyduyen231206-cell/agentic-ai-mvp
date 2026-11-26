# Agent hỗ trợ quản trị cung ứng và kiểm soát tồn kho nguyên vật liệu – Tiệm bánh

MVP **Single AI Agent** cho tiệm bánh nhỏ.
- **Rà soát lúc 08:00:** Agent tự rà soát tồn kho theo FEFO và đơn đặt trước, tìm nguyên liệu sắp thiếu.
- **Đề xuất nhập hàng:** với mỗi nguyên liệu thiếu, Agent đưa ra 2 phương án nhập (A rẻ nhất, B nhanh nhất). Quản lý chọn, duyệt rồi gửi PO.
- **Hỏi đáp:** khung chat chỉ đọc để hỏi về tồn kho.

Mọi con số do code tính. Gemini chỉ diễn giải, và không gọi được hàm ghi dữ liệu nào.

- Ngôn ngữ, thư viện: Python 3.12, Streamlit, SQLite, Gemini (`google-genai`, model mặc định `gemini-3.5-flash-lite`).
- Agent tự viết, không dùng LangChain hay LangGraph. Có 2 luồng: `MORNING_AUDIT` (rà soát) và `READ_ONLY_CHAT` (hỏi đáp), cùng 4 tool đọc.
- Tài liệu:
  - Đặc tả: `Dac_ta_chi_tiet_MVP_Agent_Tiem_Banh.docx`, trên Drive nhóm › 01_Contracts.
  - Hợp đồng giữa các module: [docs/contracts.md](docs/contracts.md).
  - Kịch bản demo: [docs/demo_script.md](docs/demo_script.md).

---

## 1. Cài đặt (Windows, PowerShell)

Cần Python 3.11 trở lên và Git.

```powershell
git clone https://github.com/phanthimyduyen231206-cell/agentic-ai-mvp.git
cd agentic-ai-mvp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Nếu PowerShell báo không cho chạy script khi bật `.venv`, chạy lệnh dưới một lần rồi bật lại:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

macOS / Linux: dùng `source .venv/bin/activate` thay cho dòng `Activate.ps1`.

## 2. Tạo file `.env`

Sao chép file mẫu bằng lệnh, **đừng** tạo bằng Notepad:

```powershell
Copy-Item .env.example .env
```

> ⚠️ Notepad hay tự thêm đuôi thành `.env.txt`. Khi đó app không đọc được cấu hình: báo "Chưa cấu hình PIN"
> và Gemini không chạy. Kiểm tra bằng `Get-ChildItem -Force .env*`: phải thấy đúng tên `.env`.

Mở `.env` và điền:

| Biến | Ý nghĩa | Ví dụ dạng |
|---|---|---|
| `GEMINI_API_KEY` | Khóa Gemini của bạn. Để trống thì app vẫn chạy, phần diễn giải dùng **câu dự phòng** do code sinh | (khóa riêng) |
| `GEMINI_MODEL` | Tên model, để trống thì dùng `gemini-3.5-flash-lite` | `gemini-3.5-flash-lite` |
| `MANAGER_PINS` | Mỗi quản lý một PIN riêng, dạng `Tên:PIN`, cách nhau bằng dấu phẩy | `Tên A:mã A,Tên B:mã B` |
| `MANAGER_PIN` | PIN chung dự phòng, chỉ dùng khi chưa có `MANAGER_PINS` | |
| `BAKERY_DB_PATH` | Đường dẫn database | `data/bakery.db` |
| `DEMO_DATE` | Giờ demo cố định, **luôn đặt khi demo** | `2026-11-20T08:00` |

- Tự đặt PIN, không dùng PIN dễ đoán như 1234.
- **Không commit `.env`** (đã có trong `.gitignore`), không gửi file này cho ai, và không dán API key vào chat, issue hay PR.
- Khi dùng Copilot hoặc trợ lý AI trong VS Code, đừng đính kèm file `.env`.

## 3. Tạo dữ liệu mẫu – BẮT BUỘC trước khi mở app

```powershell
python data/init_db.py
```

- Lệnh này **xóa và tạo lại** `data/bakery.db` với dữ liệu mẫu: 12 nguyên liệu, lô hàng, đơn đặt trước, NCC, PO-001…PO-004.
- Hạn lô được tính theo `DEMO_DATE` trong `.env`. Vì vậy đặt `DEMO_DATE` trước, rồi mới chạy `init_db`.
- Phải chạy lại `init_db` trong các trường hợp:
  - lần đầu cài;
  - sau khi kéo code mới có đổi bảng (ví dụ cột `ai_fallback` từ v1.1);
  - trước mỗi lần demo, để dữ liệu sạch.
- Bộ dữ liệu đủ hàng, không có cảnh báo (dùng cho TC01): `python data/init_db.py --scenario no_shortage`.

## 4. Chạy app

```powershell
streamlit run app.py
```

Mở http://localhost:8501.
- **Nhân viên:** nhập tên là vào, chỉ được xem.
- **Quản lý:** nhập tên đúng như trong `MANAGER_PINS` cùng PIN của mình. Nhập sai PIN 5 lần liên tiếp thì tên đó bị khóa 5 phút. Bộ đếm nằm trong bộ nhớ, khởi động lại app thì đếm lại từ đầu.

App có 4 tab:

| Tab | Nội dung |
|---|---|
| Kho & Đơn đặt trước | Tồn dự kiến 3 ngày, lô theo FEFO, đơn đặt trước, xuất kho, PO và nhận hàng |
| Rà soát | Chạy rà soát, xem cảnh báo. Mỗi cảnh báo có khối **Số liệu hệ thống** (code tính) và khối **Phân tích AI**. Quản lý chọn A/B, rồi tạo, duyệt, gửi PO |
| Hỏi đáp tồn kho | Chat chỉ đọc, nhớ 4 lượt gần nhất. Câu đòi tạo hay sửa PO bị từ chối |
| Nhật ký hệ thống | Mọi sự kiện. Dòng đỏ là bị chặn hoặc lỗi, dòng cam cần chú ý (giao trễ, chat từ chối, chat dùng câu dự phòng) |

## 5. Rà soát tự động lúc 08:00

```powershell
python scheduler.py          # chạy nền, rà soát lúc 08:00 mỗi ngày (Ctrl + C để dừng)
python scheduler.py --now    # rà soát 1 lần ngay rồi thoát (dùng khi demo)
```

- Mỗi ngày chỉ có 1 phiên rà soát thành công. Gọi lại khi đã xong thì nhận `ALREADY_DONE`, gọi khi đang có phiên chạy thì nhận `REJECTED_DUPLICATE`.
- Quản lý bấm "Chạy lại" trên tab Rà soát thì phiên mới đóng các cảnh báo OPEN cũ, đánh dấu SUPERSEDED.
- PO gửi đi được xuất thành file `outbox/PO-xxx.csv` (UTF-8, mở đúng tiếng Việt bằng Excel).

## 6. Kiểm thử

```powershell
python -m pytest            # toàn bộ: unit + 17 TC
python -m pytest tests/acceptance -v   # riêng 17 TC (test_TC01.py … test_TC17.py)
```

- Test dùng `FakeLLM` (tests/fake_llm.py), **không cần mạng và không tốn lượt Gemini**. Mỗi test dùng một database tạm riêng, không đụng `data/bakery.db`.
- Kết quả mong đợi: tất cả `passed`, không có `failed`.
- Thử với Gemini thật (cần `GEMINI_API_KEY`): `python scripts/try_core_agent_gemini.py`.

## 7. Cấu trúc thư mục

```
app.py                 giao diện Streamlit (chỉ hiển thị, gọi services/ui_api.py)
scheduler.py           lịch rà soát 08:00
agent/                 core_agent.py (vòng gọi Gemini + tool), prompts.py, tools.py (4 tool đọc), output_check.py (hậu kiểm số liệu)
services/              calc.py (tồn dự kiến, A/B, MOQ), fefo.py, po_service.py (vòng đời PO + guardrail), inventory_service.py,
                       audit_service.py (rà soát), chat_service.py, auth.py (đăng nhập), audit_log.py, clock.py, ui_api.py
data/                  init_db.py (tạo DB + dữ liệu mẫu), repository.py (đọc/ghi SQLite)
tests/                 unit/ (từng module), acceptance/ (17 TC), fake_llm.py
docs/                  contracts.md, demo_script.md
outbox/                file CSV của PO đã gửi
```

## 8. Lỗi thường gặp

| Hiện tượng | Cách xử lý |
|---|---|
| Đăng nhập quản lý báo "Chưa cấu hình PIN" | Kiểm tra file tên đúng `.env` (không phải `.env.txt`) và có dòng `MANAGER_PINS=` hoặc `MANAGER_PIN=` |
| Rà soát báo FAILED, Tab Nhật ký ghi lỗi `Bang alerts khong co cot: ['ai_fallback']` | Database tạo từ bản cũ: chạy lại `python data/init_db.py` |
| Số liệu lạ (tồn âm bất thường, không có cảnh báo Kem tươi) | `init_db` và app đang chạy với `DEMO_DATE` khác nhau. Đặt `DEMO_DATE` trong `.env`, chạy lại `init_db` |
| Cảnh báo ghi "Câu dự phòng – AI không phản hồi" | Không có mạng, sai hoặc hết hạn mức `GEMINI_API_KEY`, hoặc Gemini viết số không có trong dữ liệu. Số liệu và phương án A/B vẫn đúng vì do code tính |
| `streamlit` không nhận lệnh | Chưa bật `.venv`, hoặc chưa `pip install -r requirements.txt` |

## 9. Nhóm thực hiện

| Thành viên | Phần phụ trách |
|---|---|
| Duyên (trưởng nhóm) | Đặc tả, contracts, clock, audit_log, po_service, auth, ghép code |
| Phương Anh | Dữ liệu mẫu, init_db, repository, inventory_service |
| Quỳnh Anh | fefo, calc, 4 tool đọc |
| Mai Anh | core_agent, prompts, kiểm số liệu, audit_service, chat_service |
| Yến | Giao diện app.py, ui_api, scheduler |

Lịch sử thay đổi theo từng gói việc: [commits trên main](https://github.com/phanthimyduyen231206-cell/agentic-ai-mvp/commits/main)
và [các pull request](https://github.com/phanthimyduyen231206-cell/agentic-ai-mvp/pulls?q=is%3Apr).
