"""app.py - Giao diện Streamlit 4 tab (việc Y01: khung 4 tab, đăng nhập PIN, banner).

Chạy:  streamlit run app.py

Nguyên tắc: file này CHỈ nhận thao tác và hiển thị, KHÔNG chứa luật nghiệp vụ
(tính tồn, chọn A/B, kiểm tra quyền... đều do services/ làm). Hiện đang gọi
HÀM GIẢ trong services/stubs.py (đặt tên là `api`). Ở việc Y03/Y09 chỉ cần đổi
dòng import `api` sang hàm thật.
"""
import hmac
import os
from datetime import date, datetime
from datetime import time as dtime

import pandas as pd
import streamlit as st

# ĐỔI Ở Y03/Y09: thay stubs bằng po_service, inventory_service, audit_service...
from services import stubs as api

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
# TAB 1 và TAB 2: tạm là khung rỗng, nội dung đầy đủ làm ở việc Y02
# ===========================================================================
def render_tab1(user):
    st.subheader("Kho và Đơn đặt trước")
    st.info("Khung tab. Nội dung chi tiết sẽ làm ở việc Y02.")
    st.caption("Kiểm tra hàm giả: danh sách 12 nguyên liệu lấy từ services/stubs.py")
    st.dataframe(pd.DataFrame(api.list_ingredients()), hide_index=True)


def render_tab2(user):
    st.subheader("Rà soát 08:00")
    st.info("Khung tab. Nội dung chi tiết sẽ làm ở việc Y02.")
    st.caption(f"Kiểm tra hàm giả: đang có {len(api.list_alerts())} cảnh báo mẫu và {len(api.list_pos())} PO mẫu.")


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
