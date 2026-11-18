"""app.py - Giao diện Streamlit 4 tab (việc Y01 + Y02).

Chạy:  streamlit run app.py

Nguyên tắc: file này CHỈ nhận thao tác và hiển thị, KHÔNG chứa luật nghiệp vụ
(tính tồn, chọn A/B, kiểm tra quyền... đều do services/ làm). Mọi hàm backend
được lấy qua "ổ chia" `api` ngay bên dưới. Hiện tất cả đang trỏ về HÀM GIẢ
trong services/stubs.py. Ở việc Y03/Y09 chỉ sửa khối "ổ chia" này, phần còn
lại của file giữ nguyên.
"""
import hmac
import math
import os
from datetime import date, datetime, timedelta
from datetime import time as dtime
from types import SimpleNamespace

import pandas as pd
import streamlit as st

# ===========================================================================
# Ổ CHIA: nơi DUY NHẤT nối giao diện với backend
# ---------------------------------------------------------------------------
# Hiện mọi hàm trỏ về hàm giả (_fake). Khi hàm thật của ai đã merge vào main,
# thêm dòng import module đó rồi đổi đúng các dòng có ghi tên người đó, ví dụ:
#     from services import po_service
#     create_draft_po=po_service.create_draft_po,
# Y03: đổi nhóm Duyên (po_service) và Phương Anh (inventory_service, repository).
# Y09: đổi nốt nhóm còn lại, xóa dòng `reset`, xóa import _fake và stubs.py.
# ===========================================================================
from services import stubs as _fake

api = SimpleNamespace(
    # --- services/clock.py (Duyên, D03) ---
    now=_fake.now,
    # --- data/repository.py (Phương Anh, P03) ---
    list_ingredients=_fake.list_ingredients,
    get_batches_fefo=_fake.get_batches_fefo,
    get_suppliers=_fake.get_suppliers,
    # --- services/fefo.py (Quỳnh Anh, Q01) ---
    fefo_projection=_fake.fefo_projection,
    # --- services/inventory_service.py (Phương Anh, P04) ---
    issue_stock_fefo=_fake.issue_stock_fefo,
    # --- services/po_service.py (Duyên, D05) ---
    GuardrailError=_fake.GuardrailError,
    create_draft_po=_fake.create_draft_po,
    approve_po=_fake.approve_po,
    reject_po=_fake.reject_po,
    cancel_po=_fake.cancel_po,
    send_po=_fake.send_po,
    mark_received=_fake.mark_received,
    # --- services/audit_service.py (Mai Anh, M05) ---
    run_morning_audit=_fake.run_morning_audit,
    # --- CHƯA CÓ TRONG CONTRACTS: chờ nhóm chốt ai làm ---
    list_preorders=_fake.list_preorders,
    list_pos=_fake.list_pos,
    list_alerts=_fake.list_alerts,
    get_today_run=_fake.get_today_run,
    dismiss_alert=_fake.dismiss_alert,
    # --- Chỉ dùng khi phát triển (nút Reset ở thanh bên), XÓA ở Y09 ---
    reset=_fake.reset,
)

st.set_page_config(page_title="Trợ lý cung ứng tiệm bánh", page_icon="🥐", layout="wide")

CAUSE_LABELS = {
    "PREORDER": "Đơn đặt trước",
    "DAILY_USAGE": "Bán lẻ hằng ngày",
    "EXPIRING_LOT": "Lô hết hạn trước khi kịp dùng",
    "SUPPLIER_DELAY": "Nhà cung cấp giao trễ",
    "OPEN_PO": "Đã có PO đang mở chưa xử lý",
}
ALERT_STATUS_LABELS = {
    "OPEN": "🔴 Chờ quản lý xử lý",
    "SELECTED": "🟡 Đã chọn phương án, đang xử lý PO",
    "RESOLVED": "🟢 Đã xử lý xong",
    "DISMISSED": "⚪ Đã bỏ qua",
}

RED_STYLE = "background-color: #ffd6d6; color: #7a0000"
ORANGE_STYLE = "background-color: #ffe0b3; color: #7a3e00"


# ===========================================================================
# Hàm tiện ích hiển thị
# ===========================================================================
def load_env_file():
    """Đọc file .env (nếu có) vào biến môi trường, để lấy MANAGER_PIN."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def to_dt(value):
    """Nhận chuỗi ISO / date / datetime, trả datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, dtime.min)
    return datetime.fromisoformat(str(value))


def fmt_dt(value):
    return "—" if value in (None, "") else to_dt(value).strftime("%H:%M %d/%m/%Y")


def fmt_date(value):
    return "—" if value in (None, "") else to_dt(value).strftime("%d/%m/%Y")


def fmt_money(value):
    return f"{value:,.0f}đ".replace(",", ".")


def fmt_qty(value):
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "")  # bỏ dấu phẩy ngăn nghìn cho gọn


def flash(tab, kind, message):
    """Lưu một thông báo để hiện ở lần vẽ lại kế tiếp. kind: success|error|warning|info."""
    st.session_state["flash"] = {"tab": tab, "kind": kind, "msg": message}


def show_flash(tab):
    data = st.session_state.get("flash")
    if data and data["tab"] == tab:
        getattr(st, data["kind"])(data["msg"])
        st.session_state.pop("flash", None)


def run_action(tab, action, ok_message):
    """Chạy một thao tác GHI. Thành công -> báo xanh; GuardrailError -> báo đỏ. Rồi vẽ lại trang."""
    try:
        result = action()
        flash(tab, "success", ok_message(result) if callable(ok_message) else ok_message)
    except api.GuardrailError as err:
        flash(tab, "error", f"⛔ Bị chặn [{err.code}]: {err.message}")
    st.rerun()


# ===========================================================================
# Đăng nhập, thanh bên, banner
# ===========================================================================
def render_login():
    st.title("🥐 Trợ lý cung ứng nguyên vật liệu - Tiệm bánh")
    st.subheader("Đăng nhập")
    expected_pin = os.getenv("MANAGER_PIN", "")
    with st.form("login_form"):
        role = st.radio(
            "Vai trò", ["STAFF", "MANAGER"], horizontal=True,
            format_func=lambda r: "Nhân viên (STAFF)" if r == "STAFF" else "Quản lý (MANAGER)")
        name = st.text_input("Tên của bạn (không bắt buộc)")
        pin = st.text_input("Mã PIN quản lý (chỉ cần khi chọn MANAGER)", type="password")
        submitted = st.form_submit_button("Đăng nhập", type="primary")
    if submitted:
        if role == "MANAGER":
            if not expected_pin:
                st.error("Chưa có MANAGER_PIN trong file .env. Hãy mở file .env và thêm dòng MANAGER_PIN=1234.")
                return
            if not hmac.compare_digest(pin.encode("utf-8"), expected_pin.encode("utf-8")):
                st.error("Sai mã PIN.")
                return
        st.session_state["user"] = {"username": name.strip() or role.lower(), "role": role}
        st.rerun()


def render_sidebar(user):
    with st.sidebar:
        st.markdown(f"**Đang đăng nhập:** {user['username']}")
        st.caption(f"Vai trò: {user['role']}")
        if st.button("Đăng xuất"):
            st.session_state.clear()
            st.rerun()
        with st.expander("🛠 Công cụ dev (hàm giả)"):
            st.caption("Chỉ dùng khi phát triển, sẽ bỏ ở việc Y09.")
            if st.button("Reset: đã rà soát 08:00 (4 cảnh báo)", key="reset_alerts"):
                api.reset("with_alerts")
                st.session_state.pop("flash", None)
                st.rerun()
            if st.button("Reset: hôm nay chưa rà soát", key="reset_norun"):
                api.reset("no_run")
                st.session_state.pop("flash", None)
                st.rerun()


def render_banner():
    alerts = api.list_alerts()
    pos = api.list_pos()
    n_open = sum(1 for a in alerts if a["status"] == "OPEN")
    n_draft = sum(1 for p in pos if p["status"] == "DRAFT" and p["closed_reason"] is None)
    n_approved = sum(1 for p in pos if p["status"] == "APPROVED" and p["closed_reason"] is None)
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("🔔 Cảnh báo đang mở", n_open)
        c2.metric("📝 PO chờ duyệt", n_draft)
        c3.metric("📤 PO chờ gửi", n_approved)
        if n_open or n_draft or n_approved:
            st.caption("👉 Mở tab «Rà soát 08:00» để xử lý.")


# ===========================================================================
# ====== TAB 1 ======
# ===========================================================================
def highlight_rows(style_by_prefix):
    """Tạo hàm tô màu hàng: style_by_prefix = (tên cột, tiền tố, style)."""
    column, prefix, style = style_by_prefix

    def _apply(row):
        if str(row[column]).startswith(prefix):
            return [style] * len(row)
        return [""] * len(row)

    return _apply


def styled_table(df, style_by_prefix):
    """Tô màu hàng và hiện số gọn (10 thay vì 10.000000 như mặc định của pandas)."""
    def _fmt(value):
        if isinstance(value, float):
            return fmt_qty(value)
        return value

    return df.style.apply(highlight_rows(style_by_prefix), axis=1).format(_fmt)


def render_tab1(user):
    show_flash("tab1")
    now = api.now()
    ingredients = api.list_ingredients()
    ing_by_id = {i["id"]: i for i in ingredients}

    # ---- 1. Tồn theo nguyên liệu + projected_stock so với min_stock -------
    st.subheader("1. Tồn kho và dự báo 3 ngày tới")
    st.caption(
        "projected_stock = tồn dự kiến sau 3 ngày (đã trừ nhu cầu và lượng hết hạn trước khi dùng). "
        "Nhỏ hơn mức tối thiểu (min_stock) thì bị cảnh báo THIẾU.")
    summary_rows, lots_by_ing, proj_by_ing = [], {}, {}
    for ing in ingredients:
        lots = api.get_batches_fefo(ing["id"], now)
        proj = api.fefo_projection(ing["id"], 3, now)
        lots_by_ing[ing["id"]] = lots
        proj_by_ing[ing["id"]] = proj
        short = proj["projected_stock"] < ing["min_stock"]
        summary_rows.append({
            "Nguyên liệu": ing["name"], "Đơn vị": ing["unit"],
            "Tồn còn hạn": round(sum(b["qty"] for b in lots), 2),
            "Hết hạn trước khi dùng": round(proj["expired_qty"], 2),
            "projected_stock (3 ngày)": round(proj["projected_stock"], 2),
            "min_stock": ing["min_stock"],
            "Tình trạng": "⚠️ THIẾU" if short else "✅ Đủ",
        })
    df = pd.DataFrame(summary_rows)
    st.dataframe(styled_table(df, ("Tình trạng", "⚠️", RED_STYLE)), hide_index=True)

    # ---- 2. Bảng lô theo FEFO ---------------------------------------------
    st.subheader("2. Tồn theo lô (FEFO: hạn sớm nhất ở trên)")
    st.caption("Dòng ĐỎ = lô có lượng sẽ hết hạn trước khi kịp dùng.")
    choice = st.selectbox(
        "Xem nguyên liệu", ["__ALL__"] + [i["id"] for i in ingredients], key="lot_filter",
        format_func=lambda x: "Tất cả nguyên liệu" if x == "__ALL__" else ing_by_id[x]["name"])
    lot_rows = []
    for ing in ingredients:
        if choice not in ("__ALL__", ing["id"]):
            continue
        expired_map = {}
        for e in proj_by_ing[ing["id"]].get("expired", []):
            expired_map[e["batch_id"]] = expired_map.get(e["batch_id"], 0) + e["qty"]
        for lot in lots_by_ing[ing["id"]]:
            days_left = (to_dt(lot["expiry_date"]) - now).total_seconds() / 86400
            exp_qty = expired_map.get(lot["batch_id"], 0)
            lot_rows.append({
                "Nguyên liệu": ing["name"], "Mã lô": lot["batch_id"],
                "Số lượng": lot["qty"], "Đơn vị": ing["unit"],
                "Hạn dùng": fmt_dt(lot["expiry_date"]), "Còn lại (ngày)": round(days_left, 1),
                "Ghi chú": f"⚠️ {fmt_qty(exp_qty)} {ing['unit']} sẽ hết hạn trước khi kịp dùng" if exp_qty else "",
            })
    if lot_rows:
        st.dataframe(styled_table(pd.DataFrame(lot_rows), ("Ghi chú", "⚠️", RED_STYLE)),
                     hide_index=True)
    else:
        st.info("Không có lô nào còn hạn.")

    # ---- 3. Đơn đặt trước ---------------------------------------------------
    st.subheader("3. Đơn đặt trước")
    pre_rows = [{"Mã đơn": p["order_id"], "Bánh": p["cake_name"], "Số lượng": p["quantity"],
                 "Ngày giao": fmt_date(p["delivery_date"])}
                for p in sorted(api.list_preorders(), key=lambda p: p["delivery_date"])]
    st.dataframe(pd.DataFrame(pre_rows), hide_index=True)

    # ---- 4. Xuất kho ---------------------------------------------------------
    st.subheader("4. Xuất kho (tự trừ theo FEFO)")
    with st.form("form_issue"):
        ing_id = st.selectbox(
            "Nguyên liệu", [i["id"] for i in ingredients],
            format_func=lambda x: f"{ing_by_id[x]['name']} ({ing_by_id[x]['unit']})")
        qty = st.number_input("Số lượng xuất", min_value=0.0, value=1.0, step=1.0)
        submitted = st.form_submit_button("Xuất kho")
    if submitted:
        ing = ing_by_id[ing_id]

        def _ok(taken):
            detail = "; ".join(f"{t['batch_id']}: -{fmt_qty(t['taken'])}" for t in taken)
            return f"✅ Đã xuất {fmt_qty(qty)} {ing['unit']} {ing['name']}. Đã trừ từ lô: {detail}."

        run_action("tab1", lambda: api.issue_stock_fefo(ing_id, float(qty), user), _ok)

    # ---- 5. Danh sách PO -----------------------------------------------------
    st.subheader("5. Danh sách PO")
    st.caption("Hai cột tách riêng: status (vòng đời PO) và delivery_status (giao hàng). DELAYED tô cam.")
    pos = api.list_pos()
    if pos:
        po_rows = []
        for p in reversed(pos):
            po_rows.append({
                "Mã PO": p["po_id"],
                "Nguyên liệu": ing_by_id.get(p["ingredient_id"], {}).get("name", p["ingredient_id"]),
                "NCC": p.get("sup_name") or p["sup_id"],
                "Số lượng": p["order_qty"], "Tổng tiền": fmt_money(p["total_cost"]),
                "status": p["status"], "delivery_status": p["delivery_status"] or "—",
                "Dự kiến giao": fmt_date(p["expected_delivery_date"]),
                "Đóng (closed_reason)": p["closed_reason"] or "—",
            })
        st.dataframe(styled_table(pd.DataFrame(po_rows), ("delivery_status", "DELAYED", ORANGE_STYLE)),
                     hide_index=True)
    else:
        st.info("Chưa có PO nào.")

    # ---- 6. Nhận hàng ---------------------------------------------------------
    st.subheader("6. Xác nhận đã nhận hàng")
    receivable = [p for p in pos if p["status"] == "SENT" and p["delivery_status"] in ("PENDING", "DELAYED")]
    if not receivable:
        st.info("Không có PO nào đang chờ giao.")
    else:
        po_by_id = {p["po_id"]: p for p in receivable}
        po_id = st.selectbox(
            "Chọn PO đã nhận", list(po_by_id), key="recv_po",
            format_func=lambda x: (
                f"{x} - {ing_by_id.get(po_by_id[x]['ingredient_id'], {}).get('name', '')} - "
                f"{fmt_qty(po_by_id[x]['order_qty'])} ({po_by_id[x]['delivery_status']})"))
        st.caption("MVP: nhận một lần đủ số lượng đặt.")
        use_expiry = st.checkbox(
            "Nhập hạn dùng in trên bao bì (nếu bỏ trống, hệ thống tự tính theo hạn dùng mặc định)",
            key="recv_use_expiry")
        expiry_date = st.date_input("Hạn dùng", value=now.date() + timedelta(days=7),
                                    key="recv_expiry") if use_expiry else None
        if st.button("📥 Xác nhận đã nhận đủ hàng", key="btn_receive"):
            po = po_by_id[po_id]
            expiry_dt = datetime.combine(expiry_date, dtime(23, 59, 59)) if expiry_date else None
            run_action(
                "tab1",
                lambda: api.mark_received(po_id, float(po["order_qty"]), user, expiry_dt),
                lambda batch_id: f"✅ Đã nhận hàng {po_id}. Tạo lô mới {batch_id}.")


# ===========================================================================
# ====== TAB 2 ======
# ===========================================================================
def get_moq(ingredient_id, sup_id):
    for s in api.get_suppliers(ingredient_id):
        if s["sup_id"] == sup_id and s.get("moq"):
            return float(s["moq"])
    return 1.0


def round_up_moq(qty, moq):
    """Làm tròn LÊN theo bội số MOQ (vd 23 với MOQ 10 -> 30). Hàm ghi sẽ kiểm tra lại."""
    if qty <= 0 or moq <= 0:
        return qty
    return round(math.ceil(round(qty / moq, 9)) * moq, 6)


def render_run_block(user):
    st.subheader("Rà soát buổi sáng (08:00)")
    run = api.get_today_run()
    if run:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Phiên số", f"#{run['run_id']}")
        c2.metric("Trạng thái", run["status"])
        c3.metric("Cách chạy", run["trigger"] + (" (chạy lại)" if run["is_rerun"] else ""))
        c4.metric("Số cảnh báo", run["alert_count"])
        st.caption(f"Bắt đầu {fmt_dt(run['started_at'])} · kết thúc {fmt_dt(run['finished_at'])}")
        if run.get("error"):
            st.error(run["error"])
    else:
        st.info("Hôm nay chưa chạy rà soát. Bấm nút bên dưới để chạy.")
    rerun = st.checkbox("Chạy lại (rà soát thêm lần nữa trong ngày)", key="rerun_flag") if run else False
    if st.button("▶️ Chạy rà soát", type="primary", key="btn_run_audit"):
        with st.spinner("Đang rà soát..."):
            result = api.run_morning_audit("MANUAL", rerun=rerun, user=user)
        kind = {"SUCCEEDED": "success", "ALREADY_DONE": "info",
                "REJECTED_DUPLICATE": "warning", "FAILED": "error"}.get(result["status"], "info")
        flash("tab2", kind, f"{result['status']}: {result['message']}")
        st.rerun()


def render_options_table(alert):
    unit = alert["unit"]
    rows = []
    for o in alert["options"]:
        rows.append({
            "Phương án": o["option"], "NCC": o["sup_name"], "Đơn giá": fmt_money(o["unit_price"]),
            "Số lượng": f"{fmt_qty(o['order_qty'])} {unit}", "Tổng tiền": fmt_money(o["total_cost"]),
            "Thời gian giao": f"{o['lead_time_hours']} giờ", "Hàng về lúc": fmt_dt(o["arrival_at"]),
            "Kịp đơn sớm nhất?": "✅ Kịp" if o["meets_earliest_preorder"] else "❌ Không kịp",
            "Chênh lệch so với A": f"{o['cost_diff_pct_vs_A']:g}%", "Lý do": o["reason"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True)


def render_choice_form(alert, user):
    aid, unit = alert["alert_id"], alert["unit"]
    options = alert["options"]
    st.markdown("**Quyết định của quản lý**")
    labels = {o["option"]: f"Phương án {o['option']} - {o['sup_name']}" for o in options}
    letter = st.radio("Chọn phương án", list(labels), format_func=lambda k: labels[k],
                      horizontal=True, key=f"opt_{aid}")
    opt = next(o for o in options if o["option"] == letter)
    moq = get_moq(alert["ingredient_id"], opt["sup_id"])
    qty = st.number_input(f"Số lượng đặt ({unit})", min_value=0.0, value=float(opt["order_qty"]),
                          step=float(moq), key=f"qty_{aid}_{letter}")
    final_qty = round_up_moq(qty, moq)
    if qty <= 0:
        st.warning("Số lượng phải lớn hơn 0.")
    elif final_qty != qty:
        st.info(f"Số lượng sẽ được làm tròn LÊN thành **{fmt_qty(final_qty)} {unit}** "
                f"(MOQ của {opt['sup_name']} là {fmt_qty(moq)}).")
    else:
        st.caption(f"MOQ của {opt['sup_name']}: {fmt_qty(moq)} {unit}. Số lượng hợp lệ.")
    st.caption(f"Tổng tiền dự kiến: {fmt_money(final_qty * opt['unit_price'])}")
    c1, c2 = st.columns(2)
    if c1.button("✅ Tạo PO nháp", type="primary", key=f"create_{aid}", disabled=qty <= 0):
        run_action("tab2", lambda: api.create_draft_po(aid, letter, float(final_qty), user),
                   lambda po_id: f"✅ Đã tạo PO nháp {po_id}. Hãy duyệt ở mục «PO cần xử lý» bên dưới.")
    if c2.button("🚫 Bỏ qua cảnh báo", key=f"dismiss_{aid}"):
        run_action("tab2", lambda: api.dismiss_alert(aid, user),
                   f"Đã bỏ qua cảnh báo {alert['ingredient_name']}.")


def render_alert_card(alert, user):
    unit = alert["unit"]
    with st.container(border=True):
        st.markdown(f"### {alert['ingredient_name']}")
        st.caption(ALERT_STATUS_LABELS.get(alert["status"], alert["status"]))
        c1, c2, c3 = st.columns(3)
        c1.metric("Tồn dự kiến (3 ngày)", f"{fmt_qty(alert['projected_stock'])} {unit}")
        c2.metric("Tồn tối thiểu", f"{fmt_qty(alert['min_stock'])} {unit}")
        c3.metric("Cần bổ sung", f"{fmt_qty(alert['shortage_qty'])} {unit}")

        st.markdown("**Nguyên nhân** (mã do code tính, ⭐ = nguyên nhân chính)")
        lines = []
        for c in alert["causes"]:
            star = "⭐ " if c["code"] == alert["main_cause"] else ""
            qty_text = f": {fmt_qty(c['qty'])} {unit}" if c.get("qty") is not None else ""
            lines.append(f"- {star}**{CAUSE_LABELS.get(c['code'], c['code'])}** ({c['code']}){qty_text}")
        st.markdown("\n".join(lines))

        if alert.get("explanation"):
            st.info(f"🤖 **Diễn giải của Gemini:** {alert['explanation']}")

        if alert.get("open_po_id"):
            st.warning(f"Nguyên liệu này đang có PO chưa xử lý ({alert['open_po_id']}). "
                       "Hệ thống không đề xuất phương án mới, hãy xử lý PO đó ở mục «PO cần xử lý».")
        elif not alert["options"]:
            st.warning(alert.get("note") or "Cần xử lý thủ công")
        else:
            render_options_table(alert)
            if alert.get("note"):
                st.caption(f"ℹ️ {alert['note']}")
            if alert.get("tradeoff_a"):
                st.markdown(f"- **Đánh đổi A:** {alert['tradeoff_a']}")
            if alert.get("tradeoff_b"):
                st.markdown(f"- **Đánh đổi B:** {alert['tradeoff_b']}")

        status = alert["status"]
        if status == "OPEN" and alert["options"]:
            render_choice_form(alert, user)
        elif status == "OPEN":
            if st.button("🚫 Bỏ qua cảnh báo", key=f"dismiss_{alert['alert_id']}"):
                run_action("tab2", lambda: api.dismiss_alert(alert["alert_id"], user), "Đã bỏ qua cảnh báo.")
        elif status == "SELECTED":
            st.info(f"Đã chọn phương án {alert['selected_option']}, số lượng "
                    f"{fmt_qty(alert['selected_qty'])} {unit} → PO **{alert['po_id']}**.")
        elif status == "RESOLVED":
            st.success(f"Đã xử lý xong (PO {alert['po_id'] or '—'}).")


def render_po_card(po, ing_by_id, user):
    pid = po["po_id"]
    ing = ing_by_id.get(po["ingredient_id"], {})
    with st.container(border=True):
        left, right = st.columns([3, 2])
        with left:
            st.markdown(f"**{pid}** · {ing.get('name', po['ingredient_id'])} · "
                        f"{fmt_qty(po['order_qty'])} {ing.get('unit', '')} · "
                        f"{po.get('sup_name') or po['sup_id']} · {fmt_money(po['total_cost'])}")
            st.caption(f"status: **{po['status']}** · tạo bởi {po['created_by']} lúc {fmt_dt(po['created_at'])}")
            if po.get("last_send_error"):
                st.error(f"Gửi thất bại (lần {po['send_attempts']}): {po['last_send_error']}")
        with right:
            if po["status"] == "DRAFT":
                reason = st.text_input("Lý do từ chối (không bắt buộc)", key=f"reason_{pid}")
                b1, b2 = st.columns(2)
                if b1.button("✅ Duyệt", type="primary", key=f"approve_{pid}"):
                    run_action("tab2", lambda: api.approve_po(pid, user), f"✅ Đã duyệt {pid}. Có thể gửi PO.")
                if b2.button("❌ Từ chối", key=f"reject_{pid}"):
                    run_action("tab2", lambda: api.reject_po(pid, user, reason), f"Đã từ chối {pid}.")
            elif po["status"] == "APPROVED":
                retry = bool(po.get("last_send_error"))
                b1, b2 = st.columns(2)
                if b1.button("🔁 Gửi lại" if retry else "📤 Gửi PO", type="primary", key=f"send_{pid}"):
                    run_action("tab2", lambda: api.send_po(pid, user),
                               lambda path: f"✅ Đã gửi {pid}. File PO: {path}")
                if b2.button("🗑 Hủy", key=f"cancel_{pid}"):
                    run_action("tab2", lambda: api.cancel_po(pid, user), f"Đã hủy {pid}.")


def render_tab2(user):
    show_flash("tab2")
    ing_by_id = {i["id"]: i for i in api.list_ingredients()}
    render_run_block(user)

    st.divider()
    st.subheader("Cảnh báo")
    alerts = api.list_alerts()
    if not alerts:
        st.info("Chưa có cảnh báo nào. Hãy chạy rà soát.")
    pending = [a for a in alerts if a["status"] in ("OPEN", "SELECTED")]
    finished = [a for a in alerts if a["status"] in ("RESOLVED", "DISMISSED")]
    for alert in pending:
        render_alert_card(alert, user)
    if alerts and not pending:
        st.success("Không còn cảnh báo nào cần xử lý.")
    if finished:
        with st.expander(f"Đã xử lý / đã bỏ qua ({len(finished)})"):
            for alert in finished:
                render_alert_card(alert, user)

    st.divider()
    st.subheader("PO cần xử lý")
    active = [p for p in api.list_pos()
              if p["closed_reason"] is None and p["status"] in ("DRAFT", "APPROVED")]
    if not active:
        st.info("Không có PO nào đang chờ duyệt hoặc chờ gửi.")
    for po in active:
        render_po_card(po, ing_by_id, user)


# ===========================================================================
# ====== TAB 3, TAB 4 VÀ HÀM MAIN ======
# ===========================================================================
def render_tab3(user):
    st.subheader("Hỏi đáp tồn kho (chỉ đọc)")
    st.info("Tab này sẽ được nối với chat_service ở việc Y09.")


def render_tab4(user):
    st.subheader("Nhật ký hệ thống")
    st.info("Tab này sẽ hiện bảng audit_runs và audit_logs ở việc Y09.")


def main():
    load_env_file()
    if "user" not in st.session_state:
        render_login()
        return
    user = st.session_state["user"]
    render_sidebar(user)
    st.title("🥐 Trợ lý cung ứng nguyên vật liệu - Tiệm bánh")
    render_banner()
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📦 Kho & Đơn đặt trước", "🔎 Rà soát 08:00", "💬 Hỏi đáp tồn kho", "📜 Nhật ký hệ thống"])
    with tab1:
        render_tab1(user)
    with tab2:
        render_tab2(user)
    with tab3:
        render_tab3(user)
    with tab4:
        render_tab4(user)


main()
