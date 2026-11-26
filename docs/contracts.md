# Hợp đồng giao tiếp giữa các module (contracts)

Bản v1.1 (DU3, sau góp ý của thầy) – Duyên soạn, khớp code trên `main` sau các PR DU1, DU2, PA1, QA1, MA1, MA2, YE1, YE2.
Muốn đổi chữ ký hàm phải báo trưởng nhóm **và** những người dùng hàm đó. Tóm tắt thay đổi v1.1: Mục 13.

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

`EVENT_TYPES`: RUN_STARTED, RUN_SUCCEEDED, RUN_FAILED, RUN_REJECTED_DUPLICATE, ALERT_CREATED, OPTION_SELECTED, ALERT_DISMISSED, PO_CREATED, PO_APPROVED, PO_REJECTED, PO_CANCELLED, PO_SEND_FAILED, PO_SENT, DELIVERY_DELAYED, DELIVERY_RECEIVED, BATCH_RECEIVED, STOCK_ISSUED, GUARDRAIL_BLOCKED, CHAT_ANSWERED, CHAT_REFUSED, CHAT_FAILED.
- `CHAT_FAILED`: chat trả câu dự phòng do code sinh (Gemini lỗi, JSON sai, số liệu lệch, hết vòng tool). Không ghi kèm `CHAT_ANSWERED`.

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
     "arrival_at": "2026-11-21T08:00:00", "meets_earliest_preorder": false, "cost_diff_pct_vs_A": 0.0,
     "reason": "Rẻ nhất, về chậm hơn B 22 giờ"},
    {"option": "B", "sup_id": "NCC_HOA_TOC", "sup_name": "NCC Hỏa tốc", "unit_price": 103500,
     "order_qty": 4.0, "total_cost": 414000, "lead_time_hours": 2, "lead_time_days": 1,
     "arrival_at": "2026-11-20T10:00:00", "meets_earliest_preorder": true, "cost_diff_pct_vs_A": 15.0,
     "reason": "Nhanh nhất, đắt hơn A 15% (54.000đ)"}
  ],
  "note": null,
  "snapshot_hash": "…"
}
```
- `causes[].code` ∈ PREORDER, DAILY_USAGE, EXPIRING_LOT, SUPPLIER_DELAY, OPEN_PO.
- Có PO đang mở → `open_po_id` có giá trị, `options = []`, có mã OPEN_PO.
- A và B cùng NCC hoặc chỉ 1 NCC hợp lệ → `options` có 1 phần tử, `note` ghi lý do. Không có NCC hợp lệ → `options = []`, `note = "Cần xử lý thủ công"`.
- `snapshot_hash` tính bằng `repository.compute_snapshot_hash` **lúc calc chạy, trước khi gọi Gemini** (Mục 10).

**4 quy tắc chọn phương án** (đặc tả Mục 5.4, code `calc.py`, không qua AI; giao diện in các quy tắc này dưới bảng A/B):
1. **A – rẻ nhất:** `total_cost` thấp nhất; hòa thì `lead_time_hours` ngắn hơn.
2. **B – nhanh nhất:** `lead_time_hours` ngắn nhất; hòa thì `total_cost` thấp hơn.
   Cả A và B: vẫn hòa thì chọn NCC có `reliability` cao hơn, cuối cùng theo `sup_id`.
3. **Số lượng đề xuất** của từng NCC: `D = max(3, lead_time_days)`, thiếu = `min_stock − projected_stock(D)`,
   `order_qty` = làm tròn lên bội số MOQ của NCC đó (nên A và B có thể khác số lượng, vd Bơ lạt 15 và 14 kg).
4. **Kịp đơn đặt trước:** `meets_earliest_preorder = arrival_at ≤ ngày giao đơn đặt trước sớm nhất lúc 07:00`
   (`calc.PREORDER_CUTOFF`: phải có nguyên liệu trước giờ làm bánh); không có đơn đặt trước → `true`.
   Ví dụ Kem tươi: A về 08:00 ngày 21/11, đơn sớm nhất giao 21/11 → **không kịp**; B về 10:00 ngày 20/11 → kịp.

## 6. agent/tools.py – Quỳnh Anh (Q03)

`READ_TOOLS` gồm đúng 4 hàm, tham số như Mục 4 đặc tả:
`check_inventory_fefo(ingredient_id=None, at=None, days=0)`, `calc_preorder_demand(ingredient_id=None, days=3)`,
`check_delayed_po(ingredient_id=None, days=3)`, `compare_suppliers(ingredient_id)`. Mỗi hàm trả `dict` JSON được.
**Không** có `create_draft_po`, `write_audit_log` trong registry.

**Kết quả trả về** (tên trường dùng chung cho tool giả trong test, prompt và giao diện; số lượng theo `unit` của nguyên liệu):

| Tool | Trường chính |
|---|---|
| Mọi tool | `ok: true`. Lỗi đầu vào: `{"ok": false, "error_code", "message"}`, không raise |
| `check_inventory_fefo` (có `ingredient_id`) | `ingredient_id`, `ingredient_name`, `unit`, `at`, `days`, `min_stock`, **`on_hand_qty`**, `expiring_qty`, `batches: [{batch_id, qty, unit, expiry_date, received_at, days_left, expires_within_days}]`; khi `days > 0` thêm `fefo_projection: {expired_qty, unmet_qty, remaining_qty, projected_stock, expired, plan}` (Mục 4) |
| `check_inventory_fefo` (không có `ingredient_id`) | `at`, `days`, `items: [{ingredient_id, ingredient_name, unit, min_stock, on_hand_qty, expiring_qty, batch_count}]` |
| `calc_preorder_demand` | `days`, `from_date`, `to_date`, `ingredient_id`, `ingredient_name`, `unit`, `preorder_demand`, `daily_usage`, `retail_demand`, `earliest_preorder_date`, `orders: [{order_id, cake_name, quantity, qty_per_cake, need_qty, delivery_date}]`; không có `ingredient_id` thì các trường nguyên liệu nằm trong `items: [...]` |
| `check_delayed_po` | `days`, `from_date`, `to_date`, `incoming_pos`, `delayed_pos`, `overdue_not_yet_flagged`, `open_unsent_pos` (mỗi PO: `po_id, ingredient_id, sup_id, order_qty, received_qty, remaining_qty, status, delivery_status, expected_delivery_date`); có `ingredient_id` thì thêm **`incoming_stock`**, **`delayed_qty`**; không có thì `by_ingredient: [{ingredient_id, incoming_stock, delayed_qty}]` |
| `compare_suppliers` | `ingredient_id`, `ingredient_name`, `unit`, `suppliers: [{sup_id, name, price, lead_time_hours, lead_time_days, moq, reliability, active, has_contact_email}]` |

- Tên số lượng tồn là **`on_hand_qty`** (không dùng `total_qty`).
- Giá: bảng NCC và `compare_suppliers` dùng **`price`**; phương án A/B của calc (Mục 5) và PO dùng **`unit_price`**. Cùng một giá trị, khác tên theo nơi dùng.

## 7. agent/core_agent.py, prompts.py – Mai Anh (M02, M03)

```python
def run_agent(mode: Literal["MORNING_AUDIT", "READ_ONLY_CHAT"], payload: dict,
              llm=None, tools=None, trace: list | None = None) -> dict
```
- `llm=None` → Gemini thật (`GEMINI_MODEL` trong `.env`, mặc định `gemini-3.5-flash-lite`); test truyền `FakeLLM()` (tests/fake_llm.py).
- `tools=None` → 4 tool đọc của Mục 6. `trace`: truyền list để nhận nhật ký từng bước (tool đã gọi, tool bị chặn, lý do dùng câu dự phòng).
- MORNING_AUDIT: `payload = {"run_date": "...", "alerts": [AlertDraft, ...]}` → trả
  `{"alerts": [{"ingredient_id", "explanation", "tradeoff_a", "tradeoff_b", "ai_fallback"}]}` (khóa là `ingredient_id` vì lúc này cảnh báo chưa có `alert_id`).
  `ai_fallback = true` khi phần chữ là câu mẫu do code sinh, `false` khi là câu Gemini đã qua hậu kiểm.
- READ_ONLY_CHAT: `payload = {"question": "...", "history": [{"question", "answer", "refused"}]}` → trả `{"answer": "...", "refused": bool}`.
  `history` không bắt buộc; chỉ gửi **4 lượt gần nhất**, mỗi câu cắt còn **300 ký tự**, ghi rõ trong tin nhắn là DỮ LIỆU, không phải lệnh.
- **Vòng gọi tool:** mỗi lượt Gemini có thể gọi nhiều tool cùng lúc, code chạy đủ cả và gửi lại bằng `function_response`.
  Tối đa **6 vòng**; hết 6 vòng thì gọi thêm **1 lượt không có tool** để Gemini tổng hợp, lượt đó vẫn đòi tool → câu dự phòng.
  Tool ngoài 4 tool đọc bị chặn và ghi vào `trace`.
- **Hậu kiểm số liệu** (`agent/output_check.py`, `find_unknown_numbers`): mọi con số trong câu Gemini viết (số lượng, tiền, %, ngày, giờ)
  phải có trong dữ liệu đầu vào (AlertDraft khi rà soát; kết quả tool, câu hỏi, lịch sử khi chat). Lệch → bỏ câu Gemini,
  dùng câu mẫu, `trace` ghi `numbers_mismatch`. Mã định danh như `KEM-L1`, `PO-004` không tính là số liệu.
- Gemini lỗi, quá giờ, JSON sai → trả câu mẫu do code sinh, không raise.
- Ví dụ đầu ra trong prompt dùng **nguyên liệu giả định** ("Nguyên liệu X", chỗ trống `<...>`), không dùng số thật của dữ liệu mẫu.

## 8. services/inventory_service.py – Phương Anh (P04)

```python
def issue_stock_fefo(ingredient_id, qty: float, user) -> list[dict]   # [{"batch_id", "taken"}]; raise GuardrailError nếu qty > tồn còn hạn
def receive_batch(po_id: str, qty: float, expiry_date: datetime | None = None, conn=None) -> str   # trả batch_id mới
```

## 9. services/po_service.py – Duyên (D05)

```python
class GuardrailError(Exception): code: str; message: str
def create_draft_po(alert_id: int, option: Literal["A", "B"], qty: float | None, user) -> str   # trả po_id mới, vd "PO-005"
                                                # qty = None: dùng order_qty đề xuất của phương án đã chọn (vẫn kiểm MOQ)
def approve_po(po_id: str, user) -> None
def reject_po(po_id: str, user, reason: str = "") -> None       # đóng PO: closed_reason = REJECTED; cảnh báo → RESOLVED
def cancel_po(po_id: str, user) -> None                         # đóng PO: closed_reason = CANCELLED; cảnh báo → RESOLVED
def send_po(po_id: str, user) -> str           # trả đường dẫn file CSV trong outbox/
def mark_received(po_id: str, qty: float, user, expiry_date: datetime | None = None) -> str   # trả batch_id
def dismiss_alert(alert_id: int, user) -> None  # quản lý bấm "Bỏ qua": cảnh báo OPEN → DISMISSED, ghi resolved_at,
                                                # log ALERT_DISMISSED; lỗi NOT_MANAGER / ALERT_NOT_OPEN
```
- `user` là dict do app tạo khi đăng nhập: `{"username": "...", "role": "MANAGER" | "STAFF"}`. Mọi hàm trên
  (kể cả `create_draft_po`, `mark_received`, `dismiss_alert`) chỉ cho `role == "MANAGER"`, sai thì `NOT_MANAGER`.
- Bị chặn → không ghi gì, raise `GuardrailError`, ghi `GUARDRAIL_BLOCKED`. Riêng gửi thất bại: PO vẫn APPROVED,
  tăng `send_attempts`, lưu `last_send_error`, ghi `PO_SEND_FAILED`, rồi raise `GuardrailError("SEND_FAILED")`.
- `send_po` đặt `delivery_status = PENDING`, `expected_delivery_date = sent_at + lead_time_hours`
  (**chỉ có ngày** `YYYY-MM-DD`, như Mục 0); file `outbox/PO-xxx.csv` (UTF-8 có BOM để Excel đọc đúng tiếng Việt).
- Cảnh báo của PO → RESOLVED khi PO SENT, REJECTED hoặc CANCELLED.
- `mark_received` chỉ nhận một lần đủ số lượng còn lại (khác → `QTY_INVALID`), gọi
  `inventory_service.receive_batch(po_id, qty, expiry_date, conn=conn)` để tạo lô. **`receive_batch` ghi `BATCH_RECEIVED`**,
  `mark_received` ghi `DELIVERY_RECEIVED` và đổi `delivery_status = RECEIVED`, `received_qty`.
  **Một giao dịch `BEGIN IMMEDIATE`** cho cả: kiểm tra → PO RECEIVED → tạo lô → 2 dòng log. Bước nào lỗi thì
  rollback hết (không có PO đã nhận mà thiếu lô, hay lô thiếu log). Có `conn` thì `receive_batch` không tự
  BEGIN/commit/rollback/close; không truyền `conn` thì tự mở giao dịch riêng như cũ.
  `receive_batch` không tự đổi trạng thái PO và **không kiểm `delivery_status`**: chống nhận trùng do
  `mark_received` lo (lần bấm sau chờ khóa, thấy PO đã RECEIVED nên bị chặn `INVALID_TRANSITION`).
  `receive_batch` chỉ cần đọc PO để lấy `ingredient_id`, tạo lô (hạn mặc định = ngày nhận + shelf_life_days), trả `batch_id`.

Mã lỗi `GuardrailError.code`: QTY_INVALID, INGREDIENT_NOT_FOUND, SUPPLIER_INVALID, MOQ_VIOLATION, ALERT_NOT_OPEN, DUPLICATE_PO, NOT_MANAGER, INVALID_TRANSITION, SNAPSHOT_CHANGED, SEND_FAILED.

### Đăng nhập – `services/auth.py` (Duyên, DU2)

```python
class LoginResult(NamedTuple): user: dict | None; message: str   # message là câu tiếng Việt cho giao diện
def login_manager(name, pin) -> LoginResult   # tên bắt buộc; PIN riêng theo MANAGER_PINS, dự phòng MANAGER_PIN chung
def login_staff(name) -> LoginResult          # nhân viên chỉ xem: không PIN, tên bắt buộc
def manager_names() -> list[str]              # tên các quản lý đã cấu hình (để giao diện cho chọn)
def is_configured() -> bool                   # đã có MANAGER_PINS hoặc MANAGER_PIN
```
- `.env`: `MANAGER_PINS=Tên:PIN,Tên:PIN`. Tên so không phân biệt hoa thường và khoảng trắng thừa; `user["username"]` là tên như trong cấu hình.
- Sai PIN 5 lần liên tiếp thì tên đó bị khóa 5 phút (đếm trong bộ nhớ app, theo giờ thật, không theo DEMO_DATE). Đăng nhập đúng thì đếm lại từ đầu.
- Thông báo không chứa PIN nào. Vai trò lấy từ kết quả đăng nhập, không từ tham số giao diện.

## 10. services/audit_service.py, chat_service.py – Mai Anh (M04, M05)

```python
def run_morning_audit(trigger: Literal["SCHEDULED", "MANUAL"], rerun: bool = False, user=None) -> dict
# {"run_id": int | None, "status": "SUCCEEDED" | "FAILED" | "REJECTED_DUPLICATE" | "ALREADY_DONE",
#  "alert_ids": [int], "message": str}
def ask_chat(question: str, user, history=None, llm=None) -> dict
# {"answer": str, "refused": bool, "fallback": bool}
```
- Câu hỏi rỗng hoặc dài quá 500 ký tự bị chặn **trước** khi gọi Gemini.
- `history`: các lượt trước trong phiên `[{"question", "answer", "refused"}]` (như `st.session_state["chat_history"]`); core_agent tự cắt 4 lượt / 300 ký tự.
- Mỗi câu hỏi ghi **đúng 1** dòng log: `CHAT_REFUSED` (rỗng, quá dài, đòi ghi dữ liệu, ngoài phạm vi), `CHAT_FAILED`
  (dùng câu dự phòng, `fallback = true`), hoặc `CHAT_ANSWERED`. `detail` gồm câu hỏi, câu trả lời (cắt ngắn), tool đã gọi / bị chặn,
  số lượt lịch sử, lý do dự phòng, các số bị lệch nếu có.

Rà soát (`run_morning_audit`):
- `snapshot_hash` lưu vào cảnh báo = `AlertDraft["snapshot_hash"]` do calc tính **trước khi gọi Gemini**, không tính lại sau.
  Gemini chậm mà kho đổi trong lúc chờ thì hash đã lưu khác hash hiện tại, `create_draft_po` chặn `SNAPSHOT_CHANGED` (TC15).
- Bảng `alerts` có cột **`ai_fallback`** (0/1, mặc định 0): 1 khi diễn giải là câu mẫu. Log `ALERT_CREATED` có khóa `ai_fallback`.
  DB tạo trước MA1 không có cột này: chạy lại `python -m data.init_db`.

Lưu kết quả Gemini vào bảng `alerts` (bảng không có cột riêng cho đánh đổi):
- `explanation` ← `explanation` của Gemini (hoặc câu mẫu dự phòng).
- `options_json` ← danh sách phương án của calc, mỗi phương án thêm khóa `"tradeoff"`: A nhận `tradeoff_a`, B nhận `tradeoff_b`.

Phần còn lại của cảnh báo lấy nguyên từ AlertDraft (Mục 5):
- `causes` ← lưu JSON **đủ dạng có số lượng** như AlertDraft, ví dụ
  `[{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0}, {"code": "DAILY_USAGE", "qty": 3.0}]`.
  Không lưu dạng chỉ có mã `["EXPIRING_LOT", ...]`, vì Tab 2 cần hiện số lượng của từng nguyên nhân.
  Mã OPEN_PO, SUPPLIER_DELAY nếu không có số lượng thì để `"qty": null`.
- `main_cause`, `projected_stock`, `shortage_qty`, `snapshot_hash` ← lấy đúng giá trị cùng tên trong AlertDraft.

Đóng cảnh báo cũ khi có phiên mới (SUPERSEDED):
- Khi một phiên rà soát chạy **SUCCEEDED** (kể cả không thiếu hàng), mọi cảnh báo **OPEN** của các phiên trước
  chuyển **DISMISSED**, ghi `resolved_at`, cùng transaction với `RUN_SUCCEEDED`.
- Mỗi cảnh báo bị đóng ghi log `ALERT_DISMISSED`, `actor = "system"`,
  `detail = {"reason": "SUPERSEDED", "by_run": <run_id mới>}`. Log `RUN_SUCCEEDED` thêm khóa
  `"superseded": [alert_id, ...]`.
- Cảnh báo **SELECTED** (đã có PO đang xử lý), **RESOLVED**, **DISMISSED** giữ nguyên.
- Quản lý tự bấm "Bỏ qua" (`dismiss_alert`, Mục 9) thì log `ALERT_DISMISSED` **không** có `reason = "SUPERSEDED"`;
  giao diện dựa vào khóa này để ghi "Quản lý bỏ qua" hay "Hệ thống tự đóng".

## 11. Hàm cho giao diện – `services/ui_api.py` (Yến) và stubs

`app.py` **chỉ import `services/ui_api.py`**. File này gom các hàm giao diện cần, gọi hàm thật ở các module khác và
đổi dữ liệu sang dạng dễ hiển thị (giải mã JSON, ghép tên nguyên liệu và tên NCC). Khi nối hàm thật (Y03, Y09)
chỉ sửa `ui_api.py`, không sửa `app.py`. Trước khi hàm thật xong, `ui_api.py` gọi `services/stubs.py`;
stubs bị xóa ở việc Y09.

| Hàm trong ui_api | Lấy từ hàm thật | Ghi chú |
|---|---|---|
| `get_ingredients()` | `repository.get_ingredients()` | |
| `get_batches_fefo(ingredient_id, at)` | `repository.get_batches_fefo` | `at` = `clock.now()` |
| `list_preorders(from_date=None, to_date=None)` | `repository.get_pre_orders` | |
| `list_pos(status=None, ingredient_id=None)` | `repository.get_purchase_orders` | ui_api ghép thêm `sup_name` từ `repository.get_supplier(sup_id)` |
| `list_alerts(run_id=None, status=None)` | `repository.get_alerts` | ui_api giải mã `causes`, `options_json` (JSON), ghép `ingredient_name`, `unit`, `min_stock` từ `repository.get_ingredient`, thêm `tradeoff_a`, `tradeoff_b`, `ai_fallback` (bool) |
| `get_today_run()` | `repository.get_latest_audit_run(run_date=clock.today())` | trả `None` nếu hôm nay chưa rà soát |
| `run_morning_audit(...)` | `audit_service.run_morning_audit` | |
| `ask_chat(question, user, history=None)` | `chat_service.ask_chat` | Gửi tối đa `CHAT_HISTORY_TURNS = 4` lượt gần nhất; trả `{"answer", "refused", "fallback"}`; giao diện gắn nhãn "Câu dự phòng" khi `fallback` |
| `issue_stock_fefo(...)` | `inventory_service.issue_stock_fefo` | |
| `create_draft_po`, `approve_po`, `reject_po`, `cancel_po`, `send_po`, `mark_received`, `dismiss_alert` | `po_service` (Mục 9) | |
| `GuardrailError` | `po_service.GuardrailError` | `app.py` bắt lỗi này để báo đỏ |
| `login_manager(name, pin)`, `login_staff(name)` | `auth` (Mục 9, phần Đăng nhập) | Trả `(user hoặc None, message)`; trang đăng nhập lưu `user` vào phiên, `None` thì hiện `message` màu đỏ |
| `get_moq`, `round_up_moq`, `order_qty_warning`, `PREORDER_CUTOFF` | `calc` | Form tạo PO và chú thích A/B; không có NCC thì `get_moq` trả `None` và khóa nút Tạo PO |
| `get_audit_logs(...)`, `get_audit_runs(...)` | `repository.get_audit_logs`, `repository.get_audit_runs` | Tab 4 Nhật ký |

## 12. Ghi nhận góp ý

| Người | Phần | Đồng ý / góp ý |
|---|---|---|
| Phương Anh | 3, 8 | |
| Quỳnh Anh | 4, 5, 6 | |
| Mai Anh | 2, 7, 10 | |
| Yến | 9, 10, 11 | |

## 13. Thay đổi v1.1 (sửa theo góp ý của thầy, 23–24/11)

| Gói | Thay đổi hợp đồng | Mục |
|---|---|---|
| DU1 | Thêm sự kiện `CHAT_FAILED`; `GEMINI_MODEL` trong `.env`; `create_draft_po(qty=None)` lấy số lượng đề xuất | 2, 7, 9 |
| DU2 + PA1 | `mark_received` một giao dịch, `receive_batch(..., conn=None)`; đăng nhập `services/auth.py` (PIN riêng, tên bắt buộc, khóa 5 phút) | 8, 9 |
| QA1 | 4 quy tắc A/B, giờ chốt 07:00, `reason` A/B so sánh với nhau, tools gọi calc thay vì tự tính | 5, 6 |
| MA1 | `snapshot_hash` trước Gemini, hậu kiểm số liệu, cột `ai_fallback`, ví dụ prompt giả định | 5, 7, 10 |
| MA2 | Chat có `history`, `fallback`, vòng tool đủ lệnh song song + 1 lượt tổng hợp | 7, 10 |
| YE1 + YE2 | `ui_api` thêm hàm đăng nhập, `get_moq` trả `None`, chú thích A/B, tách khối số liệu / AI | 11 |
