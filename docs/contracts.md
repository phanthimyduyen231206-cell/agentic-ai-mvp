# Hợp đồng giao tiếp giữa các module (contracts)

Bản nháp D02 – Duyên soạn, hạn chốt **17/11 12:00**. Mỗi chủ module đọc phần của mình và góp ý trong PR `feature/contracts`.
Sau khi merge, muốn đổi chữ ký hàm phải báo trưởng nhóm **và** những người dùng hàm đó.

Nguồn: bản đặc tả `Dac_ta_chi_tiet_MVP_Agent_Tiem_Banh.docx`, Mục 4–9.

## 0. Quy ước chung

| Nội dung | Quy ước |
|---|---|
| Ngày giờ | Giờ Việt Nam, không ghi múi giờ. Trong Python dùng `datetime` / `date`; trong DB và JSON lưu chuỗi ISO **có chữ T**: `YYYY-MM-DDTHH:MM:SS` (tạo bằng `dt.isoformat(timespec="seconds")`). Cột chỉ có ngày (`delivery_date`, `expected_delivery_date`, `run_date`) lưu `YYYY-MM-DD`. Lấy "bây giờ" **chỉ** qua `services.clock`. |
| Giờ demo | Khi chạy `data/init_db.py` và app để phát triển hoặc demo, luôn đặt `DEMO_DATE=<ngày>T08:00` trong `.env`. Dữ liệu mẫu tính hạn lô theo giờ lúc tạo, nên tạo sau 20:00 thì kịch bản Kem tươi sai. |
| Số lượng | `float`, theo đơn vị của nguyên liệu (`ingredients.unit`: kg, lít, quả, ml). |
| Tiền | Số VNĐ (DB lưu REAL, JSON trả số). |
| Mã | Mã dạng chữ (`str`), theo dữ liệu mẫu P02: `ingredient_id` (vd `KEM_TUOI`, `BOT_MI`), `sup_id` (vd `NCC_CHINH`, `NCC_HOA_TOC`, `NCC_ANH_DUONG`), `batch_id` (vd `KEM-L1`), `order_id` (vd `DH01`), **`po_id` (vd `PO-004`; PO mới do `create_draft_po` sinh mã tiếp theo `PO-005`, `PO-006`…)**. `alert_id`, `run_id`, `log_id` là `int` tự tăng. |
| Người thực hiện (`actor`, `*_by`) | `"system"`, `"scheduler"`, `"gemini"` hoặc `username` của người đăng nhập. |
| Người dùng | `user = {"username": str, "role": "MANAGER" \| "STAFF"}`, lấy từ phiên đăng nhập trong `app.py`, **không** lấy từ tham số giao diện gửi lên. |
| Database | `data/bakery.db`, đổi được bằng **một** biến môi trường duy nhất `BAKERY_DB_PATH`. Lấy đường dẫn bằng `repository.get_db_path()`, mở kết nối bằng `repository.get_connection()`; không module nào tự đọc biến môi trường này. |
| Lỗi nghiệp vụ | Hàm ghi gặp lỗi thì **raise `GuardrailError(code, message)`** (định nghĩa trong `services/po_service.py`), không ghi gì, và ghi log `GUARDRAIL_BLOCKED`. |

## 1. services/clock.py – Duyên (D03)

```python
def now() -> datetime        # giờ VN hiện tại, hoặc ngày giả nếu có DEMO_DATE
def today() -> date          # = now().date()
```
`DEMO_DATE` nhận `YYYY-MM-DD` (giờ lấy theo giờ thật) hoặc `YYYY-MM-DDTHH:MM`.

## 2. services/audit_log.py – Duyên (D04)

```python
EVENT_TYPES: tuple[str, ...]   # danh sách sự kiện Mục 7
def write_audit_log(actor: str, event_type: str, entity_type: str | None = None,
                    entity_id: str | int | None = None, run_id: int | None = None,
                    detail: dict | None = None, conn: sqlite3.Connection | None = None) -> int  # trả log_id
```
- `actor`: `"system"`, `"scheduler"`, `"gemini"` hoặc `username`.
- `event_type` không có trong `EVENT_TYPES` → `ValueError`, không ghi.
- Truyền `conn` khi đang trong một transaction để log nằm chung transaction.

`EVENT_TYPES`: RUN_STARTED, RUN_SUCCEEDED, RUN_FAILED, RUN_REJECTED_DUPLICATE, ALERT_CREATED, OPTION_SELECTED, ALERT_DISMISSED, PO_CREATED, PO_APPROVED, PO_REJECTED, PO_CANCELLED, PO_SEND_FAILED, PO_SENT, DELIVERY_DELAYED, DELIVERY_RECEIVED, BATCH_RECEIVED, STOCK_ISSUED, GUARDRAIL_BLOCKED, CHAT_ANSWERED, CHAT_REFUSED.

## 3. data/repository.py – Phương Anh (P03)

Tên hàm dưới đây là tên **thật** trong `data/repository.py` (đã merge, PR #6). Mọi hàm nhận thêm `conn=None`:
không truyền thì hàm tự mở kết nối và tự commit; truyền `conn` thì hàm không commit, người gọi tự commit/rollback.
Kết quả trả về là `dict` (1 dòng) hoặc `list[dict]`.

```python
def get_db_path() -> str                             # đọc BAKERY_DB_PATH, mặc định data/bakery.db
def get_connection(db_path=None) -> sqlite3.Connection   # row_factory = sqlite3.Row, bật foreign_keys
def get_ingredients() -> list[dict]                  # id, name, unit, min_stock, shelf_life_days, daily_usage
def get_ingredient(ingredient_id) -> dict | None
def get_batches_fefo(ingredient_id, at) -> list[Batch]   # chỉ lô còn hạn tại `at` (expiry_date > at), qty > 0,
                                                         # sắp theo expiry_date rồi received_at
def get_pre_order_needs(ingredient_id, from_date=None, to_date=None) -> list[dict]
    # mỗi dòng = 1 đơn đặt trước dùng nguyên liệu này: các cột của pre_orders + qty_per_cake + need_qty
    # need_qty = quantity × qty_per_cake; lọc delivery_date trong [from_date, to_date]
def get_purchase_orders(status=None, ingredient_id=None, open_only=False) -> list[dict]
    # PO đã gửi của 1 nguyên liệu: get_purchase_orders(status="SENT", ingredient_id=...)
def get_open_purchase_orders(ingredient_id=None) -> list[dict]
    # PO chưa đóng (closed_reason IS NULL) và chưa nhận hàng, gồm cả DRAFT, APPROVED và SENT chưa giao.
    # Muốn riêng PO chưa gửi (mã OPEN_PO) thì lọc thêm status in ("DRAFT", "APPROVED").
def get_suppliers(ingredient_id=None, active_only=False) -> list[dict]   # sup_id, name, price, lead_time_hours, moq, reliability, contact_email, active
def compute_snapshot_hash(ingredient_id) -> str      # sha256 của nguyên liệu + lô + đơn đặt + PO đang mở + NCC
```
Ngoài ra còn các hàm đọc/ghi cho từng bảng (`insert_batch`, `update_batch_qty`, `get_alert`, `insert_alert`, `update_alert`,
`insert_purchase_order`, `update_purchase_order`, `get_audit_runs`, `insert_audit_run`, `update_audit_run`…), xem docstring trong file.

`Batch = {"batch_id", "ingredient_id", "qty", "expiry_date", "received_at", "po_id"}`

## 4. services/fefo.py – Quỳnh Anh (Q01)

```python
def fefo_projection(ingredient_id, days: int, at: datetime | None = None) -> dict
```
```json
{
  "remaining_qty": 0.0, "expired_qty": 6.0, "unmet_qty": 2.0, "projected_stock": -2.0,
  "plan": [{"day": "2026-11-20", "batch_id": "KEM-L1", "take_qty": 1.0},
           {"day": "2026-11-21", "batch_id": "KEM-L2", "take_qty": 3.0}],
  "expired": [{"day": "2026-11-20", "batch_id": "KEM-L1", "qty": 6.0}]
}
```

## 5. services/calc.py – Quỳnh Anh (Q02)

```python
def compute_alerts(run_date: date) -> list[AlertDraft]   # chỉ nguyên liệu có projected_stock(H=3) < min_stock
```
`AlertDraft` (JSON mẫu, Kem tươi với dữ liệu mẫu P02, chạy lúc `DEMO_DATE=2026-11-20T08:00`; tính theo `data/expected.md`):
```json
{
  "ingredient_id": "KEM_TUOI", "ingredient_name": "Kem tươi Whipping Cream", "unit": "lít",
  "projected_stock": -2.0, "min_stock": 2.0, "shortage_qty": 4.0,
  "causes": [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0}, {"code": "DAILY_USAGE", "qty": 3.0}],
  "main_cause": "EXPIRING_LOT",
  "open_po_id": null,
  "options": [
    {"option": "A", "sup_id": "NCC_CHINH", "sup_name": "NCC Chính", "unit_price": 90000,
     "order_qty": 4.0, "total_cost": 360000, "lead_time_hours": 24, "lead_time_days": 1,
     "arrival_at": "2026-11-21T08:00:00", "meets_earliest_preorder": true, "cost_diff_pct_vs_A": 0,
     "reason": "Tổng chi phí thấp nhất trong các NCC hợp lệ"},
    {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 103500,
     "order_qty": 4.0, "total_cost": 414000, "lead_time_hours": 2, "lead_time_days": 1,
     "arrival_at": "2026-11-20T10:00:00", "meets_earliest_preorder": true, "cost_diff_pct_vs_A": 15.0,
     "reason": "Giao nhanh nhất (2 giờ), có hàng ngay trong sáng nay"}
  ],
  "note": null,
  "snapshot_hash": "…"
}
```
- `causes[].code` ∈ PREORDER, DAILY_USAGE, EXPIRING_LOT, SUPPLIER_DELAY, OPEN_PO.
- Có PO đang mở → `open_po_id` có giá trị, `options = []`, có mã OPEN_PO.
- A và B cùng NCC hoặc chỉ 1 NCC hợp lệ → `options` có 1 phần tử, `note` ghi lý do. Không có NCC hợp lệ → `options = []`, `note = "Cần xử lý thủ công"`.

## 6. agent/tools.py – Quỳnh Anh (Q03)

`READ_TOOLS` gồm đúng 4 hàm, tham số như Mục 4 đặc tả:
`check_inventory_fefo(ingredient_id=None, at=None, days=0)`, `calc_preorder_demand(ingredient_id=None, days=3)`,
`check_delayed_po(ingredient_id=None, days=3)`, `compare_suppliers(ingredient_id)`. Mỗi hàm trả `dict` JSON được.
**Không** có `create_draft_po`, `write_audit_log` trong registry.

## 7. agent/core_agent.py, prompts.py – Mai Anh (M02, M03)

```python
def run_agent(mode: Literal["MORNING_AUDIT", "READ_ONLY_CHAT"], payload: dict, llm=None) -> dict
```
- `llm=None` → Gemini thật; test truyền `FakeLLM()` (tests/fake_llm.py, M01).
- MORNING_AUDIT: `payload = {"run_date": "...", "alerts": [AlertDraft, ...]}` → trả
  `{"alerts": [{"ingredient_id", "explanation", "tradeoff_a", "tradeoff_b"}]}` (khóa là `ingredient_id` vì lúc này cảnh báo chưa có `alert_id`).
- READ_ONLY_CHAT: `payload = {"question": "..."}` → trả `{"answer": "...", "refused": bool}`.
- Lỗi hoặc JSON sai → trả câu mẫu do code sinh, không raise.

## 8. services/inventory_service.py – Phương Anh (P04)

```python
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]   # [{"batch_id", "taken"}]; raise GuardrailError nếu qty > tồn còn hạn
def receive_batch(po_id: str, qty: float, expiry_date: datetime | None = None) -> str   # trả batch_id mới
```

## 9. services/po_service.py – Duyên (D05)

```python
class GuardrailError(Exception): code: str; message: str
def create_draft_po(alert_id: int, option: Literal["A", "B"], qty: float, user) -> str   # trả po_id mới, vd "PO-005"
def approve_po(po_id: str, user) -> None
def reject_po(po_id: str, user, reason: str = "") -> None       # đóng PO: closed_reason = REJECTED; cảnh báo → RESOLVED
def cancel_po(po_id: str, user) -> None                         # đóng PO: closed_reason = CANCELLED; cảnh báo → RESOLVED
def send_po(po_id: str, user) -> str           # trả đường dẫn file CSV trong outbox/
def mark_received(po_id: str, qty: float, user, expiry_date: datetime | None = None) -> str   # trả batch_id
```
Mã lỗi `GuardrailError.code`: QTY_INVALID, INGREDIENT_NOT_FOUND, SUPPLIER_INVALID, MOQ_VIOLATION, ALERT_NOT_OPEN, DUPLICATE_PO, NOT_MANAGER, INVALID_TRANSITION, SNAPSHOT_CHANGED, SEND_FAILED.

## 10. services/audit_service.py, chat_service.py – Mai Anh (M04, M05)

```python
def run_morning_audit(trigger: Literal["SCHEDULED", "MANUAL"], rerun: bool = False, user=None) -> dict
# {"run_id": int | None, "status": "SUCCEEDED" | "FAILED" | "REJECTED_DUPLICATE" | "ALREADY_DONE",
#  "alert_ids": [int], "message": str}
def ask_chat(question: str, user) -> dict      # {"answer": str, "refused": bool}
```

## 11. Hàm giả cho giao diện (stubs) – Yến

Trước khi hàm thật xong, `services/stubs.py` trả đúng các JSON mẫu ở trên. File này bị xóa ở việc Y09.

## 12. Ghi nhận góp ý

| Người | Phần | Đồng ý / góp ý |
|---|---|---|
| Phương Anh | 3, 8 | |
| Quỳnh Anh | 4, 5, 6 | |
| Mai Anh | 2, 7, 10 | |
| Yến | 9, 10, 11 | |
