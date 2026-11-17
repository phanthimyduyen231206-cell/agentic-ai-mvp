# Số liệu kỳ vọng của bộ dữ liệu mẫu

Người lập: Phương Anh (P02). Dùng để Quỳnh Anh, Mai Anh, Duyên đối chiếu kết quả `fefo.py`, `calc.py`, `audit_service.py`.

**Quy ước tính:** H = 3 ngày, mô phỏng ngày t = 0, 1, 2 (ngày 0 = hôm nay), đúng Mục 5.2. Chỉ tính đơn đặt có `delivery_date` rơi vào ngày 0, 1 hoặc 2. Các đơn khác đều giao từ ngày +5 trở đi nên không ảnh hưởng.
Chạy rà soát **trước 20:00**, vì lô `KEM-L1` hết hạn lúc 20:00 hôm nay. Sau 20:00 thì lô này không còn tính là "còn hạn" nữa và số liệu Kem tươi sẽ khác.

`projected = current − preorder − daily_usage × 3 + incoming − expired`

## Bộ chuẩn: `python data/init_db.py`

| Nguyên liệu | current | preorder (3 ngày) | daily × 3 | incoming | expired | **projected** | min_stock | Cảnh báo? | Mã nguyên nhân (chính in đậm) | TC |
|---|---|---|---|---|---|---|---|---|---|---|
| Kem tươi (KEM_TUOI) | 10 | 3 | 3 | 0 | 6 | **−2** | 2 | Có | **EXPIRING_LOT** (6), PREORDER (3), DAILY_USAGE (3) | TC02, TC16 |
| Bơ lạt (BO_LAT) | 5 | 12 | 3 | 0 | 0 | **−10** | 3 | Có | **SUPPLIER_DELAY** (20), PREORDER (12), DAILY_USAGE (3) | TC04, TC14 |
| Bột mì (BOT_MI) | 40 | 4 | 36 | 0 | 0 | **0** | 10 | Có | **DAILY_USAGE** (36), PREORDER (4) | TC03 |
| Mascarpone (MASCARPONE) | 1 | 1 | 0,6 | 0 | 0 | **−0,6** | 0,5 | Có | **PREORDER** (1), DAILY_USAGE (0,6) | TC12 |
| Trứng gà (TRUNG) | 150 | 40 | 60 | 0 | 0 | 50 | 30 | Không | — | TC17 |
| Đường (DUONG) | 25 | 2,5 | 6 | 0 | 0 | 16,5 | 5 | Không | — | |
| Sữa tươi (SUA_TUOI) | 20 | 2,75 | 9 | 0 | 0 | 8,25 | 4 | Không | — | |
| Men nở (MEN_NO) | 2 | 0,2 | 0,3 | 0 | 0 | 1,5 | 0,5 | Không | — | |
| Bột cacao (BOT_CACAO) | 2 | 0,1 | 0,3 | 0 | 0 | 1,6 | 0,5 | Không | — | |
| Socola (SOCOLA) | 5 | 0 | 1,5 | 0 | 0 | 3,5 | 1 | Không | — | |
| Vani (VANI, ml) | 500 | 75 | 60 | 0 | 0 | 365 | 100 | Không | — | |
| Cream cheese (PHO_MAI_KEM) | 6 | 0 | 1,5 | 0 | 0 | 4,5 | 2 | Không | — | |

→ Đúng **4 cảnh báo**: Kem tươi, Bơ lạt, Bột mì, Mascarpone.

### Diễn giải FEFO Kem tươi (khớp bảng Mục 5.2)

| Ngày | Nhu cầu | Lấy từ lô | Sau ngày |
|---|---|---|---|
| 0 | 1 (daily) | 1 lít từ KEM-L1 (7 lít, hết 20:00 hôm nay) | KEM-L1 còn 6 lít → hết hạn → expired = 6 |
| 1 | 3 (15 bánh kem × 0,2) + 1 = 4 | 3 lít từ KEM-L2 | thiếu 1 → unmet = 1 |
| 2 | 1 | không còn lô | unmet = 2 |
| | | | projected = 0 − 2 = **−2** |

### Ghi chú cho từng kịch bản

- **Bơ lạt / PO-004:** seed để `status = SENT`, `delivery_status = PENDING`, `expected_delivery_date = hôm qua`, `received_qty = 0`. Khi chạy `run_morning_audit()`, bước 3 phải đổi nó thành `DELAYED` (TC14). PO-004 không tính vào incoming. Nếu test `calc.py` riêng lẻ thì tự đặt `delivery_status = 'DELAYED'` trước khi gọi. Cột delayed_qty = 20.
- **Bơ lạt có đúng 2 NCC hợp lệ (TC04):** NCC Chính (250.000đ, MOQ 5, 24h) và NCC Hỏa tốc (287.500đ, MOQ 2, 2h). Thiếu 3 − (−10) = 13 kg → Chính đặt 15 kg (tổng 3.750.000đ), Hỏa tốc đặt 14 kg (tổng 4.025.000đ).
- **Bột mì:** NCC Chính MOQ 10; NCC Ánh Dương MOQ 25 (thiếu 10 → phải đặt 25, minh họa làm tròn MOQ). NCC Hỏa tốc **trống contact_email**, nên không phải NCC hợp lệ (dùng cho TC11).
- **Mascarpone:** chỉ NCC Chính bán → chỉ 1 phương án (TC12).
- **Trứng (TC17):** TRUNG-L1 60 quả hết hạn sau 3 ngày, TRUNG-L2 90 quả hết hạn sau 10 ngày. Trên DB vừa tạo, xuất 80 quả → TRUNG-L1 = 0, TRUNG-L2 = 70.
- **Không có PO DRAFT/APPROVED** trong dữ liệu ban đầu, nên không cảnh báo nào có mã OPEN_PO.

## Bộ đủ hàng: `python data/init_db.py --scenario no_shortage` (TC01)

Khác bộ chuẩn ở các điểm sau: Kem tươi có 2 lô đều còn hạn hơn 3 ngày; Bơ lạt tồn 25 kg; Bột mì tồn 80 kg; Mascarpone tồn 3 kg; **không có PO-004**.

| Nguyên liệu | projected | min_stock |
|---|---|---|
| Kem tươi | 4 | 2 |
| Bơ lạt | 10 | 3 |
| Bột mì | 40 | 10 |
| Mascarpone | 1,4 | 0,5 |
| Các nguyên liệu còn lại | như bộ chuẩn | |

→ **0 cảnh báo**, không tạo PO, phiên SUCCEEDED.
