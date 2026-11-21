"""app.py - Giao diện Streamlit 4 tab (việc Y01, Y02, Y03, Y09).

Chạy:  streamlit run app.py

Nguyên tắc: file này CHỈ nhận thao tác và hiển thị, KHÔNG chứa luật nghiệp vụ
(tính tồn, chọn A/B, kiểm tra quyền... đều do services/ làm). Mọi hàm backend
lấy qua services/ui_api.py (contracts Mục 11). Chạy data/init_db.py trước lần đầu.
"""
import hmac
import json
import math
import os
import re
from datetime import date, datetime, timedelta
from datetime import time as dtime

import pandas as pd
import streamlit as st

# Mọi hàm backend lấy qua services/ui_api.py (contracts Mục 11).
from services import ui_api as api

st.set_page_config(page_title="Trợ lý cung ứng tiệm bánh", page_icon="🥐", layout="wide")

# Font Be Vietnam Pro (dấu tiếng Việt đẹp) + vài chỉnh nhỏ cho đồng bộ màu với .streamlit/config.toml.
# Không có mạng thì trình duyệt tự dùng font mặc định, app vẫn chạy bình thường.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap');
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
    font-family: 'Be Vietnam Pro', sans-serif;
}
.stApp h1 { color: #7C2D12; font-weight: 700; }
.stApp h2, .stApp h3 { color: #7C2D12; font-weight: 600; }
[data-testid="stMetricValue"] { color: #B45309; font-weight: 700; }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
.stButton button, .stFormSubmitButton button { border-radius: 10px; font-weight: 600; }
button[data-baseweb="tab"] p, [data-testid="stTab"] p { font-size: 1rem; font-weight: 600; }
/* Bảng HTML (show_table): tiêu đề căn giữa, đứng yên khi cuộn */
.bk-table { overflow: auto; border: 1px solid #EADBC8; border-radius: 12px; margin-bottom: 1rem; background: #FFFFFF; }
.bk-table table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
.bk-table thead th { position: sticky; top: 0; z-index: 1; background: #F5EBDD; color: #7C2D12;
    font-weight: 600; text-align: center !important; padding: 10px 12px; border-bottom: 1px solid #E2CDB4; }
.bk-table td { text-align: center; padding: 8px 12px; border-bottom: 1px solid #F3E8DA; vertical-align: middle;
    white-space: nowrap; }
.bk-table tbody tr:last-child td { border-bottom: none; }
/* Banner: chữ và con số nằm giữa từng ô */
[data-testid="stMetric"] { text-align: center; }
[data-testid="stMetricLabel"], [data-testid="stMetricValue"] { display: flex; justify-content: center; }
/* 4 tab chia đều chiều ngang (bản Streamlit mới: role="tab"; bản cũ: data-baseweb="tab") */
[data-testid="stTabs"] [role="tablist"], div[data-baseweb="tab-list"] { width: 100%; gap: 0; }
[data-testid="stTabs"] [data-testid="stTab"], div[data-baseweb="tab-list"] button[data-baseweb="tab"] {
    flex: 1 1 0; justify-content: center; }
[data-testid="stTab"][data-focus-visible] { box-shadow: none; }
</style>
""", unsafe_allow_html=True)

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


_ISO_DATETIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?")
_ISO_DATE = re.compile(r"(?<![\d/])(\d{4})-(\d{2})-(\d{2})(?![\d])")
_FLOAT_ZERO = re.compile(r"(?<![\d.])(\d+)\.0(?!\d)")


def pretty_text(text):
    """Làm đẹp chữ do Gemini viết, CHỈ để hiển thị (không đổi dữ liệu):
    2026-11-20T20:00:00 -> 20:00 20/11/2026; 2026-11-21 -> 21/11/2026; 10.0 lít -> 10 lít.
    Tiền dạng 360.000đ giữ nguyên."""
    if not text:
        return text
    text = _ISO_DATETIME.sub(lambda m: f"{m[4]}:{m[5]} {m[3]}/{m[2]}/{m[1]}", str(text))
    text = _ISO_DATE.sub(lambda m: f"{m[3]}/{m[2]}/{m[1]}", text)
    return _FLOAT_ZERO.sub(r"\1", text)


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


def is_manager(user):
    """Chỉ để ẩn/hiện nút cho gọn. Quyền thật do hàm ghi kiểm tra (NOT_MANAGER)."""
    return user.get("role") == "MANAGER"


MANAGER_ONLY_NOTE = "🔒 Chỉ quản lý (MANAGER) mới thao tác được ở đây."


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
            st.caption('<div style="text-align:center">👉 Mở tab «Rà soát» để xử lý.</div>',
                       unsafe_allow_html=True)


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


# Cột chứa câu dài thì để căn trái cho dễ đọc; các cột còn lại căn giữa.
LEFT_ALIGNED_COLUMNS = {"Nội dung", "Lý do", "Ghi chú", "Lỗi"}


def _cell_text(value):
    return fmt_qty(value) if isinstance(value, float) else value


def show_table(data, max_height=None):
    """Vẽ bảng dạng HTML để căn giữa được cả TIÊU ĐỀ cột (st.dataframe không làm được).
    data: DataFrame, hoặc Styler đã tô màu hàng (styled_table, log_row_styler).
    max_height: bảng dài thì cuộn trong khung cao tối đa bấy nhiêu px, tiêu đề đứng yên."""
    styler = data if not isinstance(data, pd.DataFrame) else data.style
    columns = list(styler.data.columns)
    styler = styler.format(_cell_text, escape="html").hide(axis="index")
    left = [c for c in columns if c in LEFT_ALIGNED_COLUMNS]
    if left:
        styler = styler.set_properties(subset=left, **{"text-align": "left", "white-space": "normal",
                                                      "min-width": "260px"})
    html = " ".join(line.strip() for line in styler.to_html().splitlines())
    box = f' style="max-height:{max_height}px"' if max_height else ""
    st.markdown(f'<div class="bk-table"{box}>{html}</div>', unsafe_allow_html=True)


def render_tab1(user):
    show_flash("tab1")
    now = api.now()
    ingredients = api.get_ingredients()
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
    show_table(styled_table(df, ("Tình trạng", "⚠️", RED_STYLE)))

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
        show_table(styled_table(pd.DataFrame(lot_rows), ("Ghi chú", "⚠️", RED_STYLE)), max_height=460)
    else:
        st.info("Không có lô nào còn hạn.")

    # ---- 3. Đơn đặt trước ---------------------------------------------------
    st.subheader("3. Đơn đặt trước")
    pre_rows = [{"Mã đơn": p["order_id"], "Bánh": p["cake_name"], "Số lượng": p["quantity"],
                 "Ngày giao": fmt_date(p["delivery_date"])}
                for p in sorted(api.list_preorders(), key=lambda p: p["delivery_date"])]
    show_table(pd.DataFrame(pre_rows))

    # ---- 4. Xuất kho ---------------------------------------------------------
    st.subheader("4. Xuất kho (tự trừ theo FEFO)")
    if not is_manager(user):
        st.caption(MANAGER_ONLY_NOTE)
    else:
        render_issue_form(ingredients, ing_by_id, user)

    # ---- 5. Danh sách PO -----------------------------------------------------
    render_po_list_and_receive(ing_by_id, now, user)


def render_issue_form(ingredients, ing_by_id, user):
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


def render_po_list_and_receive(ing_by_id, now, user):
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
        show_table(styled_table(pd.DataFrame(po_rows), ("delivery_status", "DELAYED", ORANGE_STYLE)))
    else:
        st.info("Chưa có PO nào.")

    # ---- 6. Nhận hàng ---------------------------------------------------------
    st.subheader("6. Xác nhận đã nhận hàng")
    receivable = [p for p in pos if p["status"] == "SENT" and p["delivery_status"] in ("PENDING", "DELAYED")]
    if not is_manager(user):
        st.caption(MANAGER_ONLY_NOTE)
    elif not receivable:
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
    elif is_manager(user):
        st.info("Hôm nay chưa chạy rà soát. Bấm nút bên dưới để chạy.")
    else:
        st.info("Hôm nay chưa chạy rà soát. Quản lý sẽ chạy rà soát hoặc hệ thống tự chạy lúc 08:00.")
    if not is_manager(user):
        st.caption(MANAGER_ONLY_NOTE)
        return
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
            "Chênh lệch so với A": f"{o['cost_diff_pct_vs_A']:g}%", "Lý do": pretty_text(o["reason"]),
        })
    show_table(pd.DataFrame(rows))


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


def superseded_alert_ids():
    """Mã các cảnh báo bị hệ thống tự đóng vì có phiên rà soát mới (đọc từ nhật ký)."""
    return {int(log["entity_id"]) for log in api.get_audit_logs(event_type="ALERT_DISMISSED")
            if is_auto_superseded(log) and str(log["entity_id"]).isdigit()}


def render_alert_card(alert, user, superseded=frozenset()):
    unit = alert["unit"]
    with st.container(border=True):
        st.markdown(f"### {alert['ingredient_name']}")
        if alert["status"] == "DISMISSED" and alert["alert_id"] in superseded:
            st.caption("⚪ Hệ thống tự đóng (đã có phiên rà soát mới)")
        else:
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
            st.info(f"🤖 **Diễn giải của Gemini:** {pretty_text(alert['explanation'])}")

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
                st.markdown(f"- **Đánh đổi A:** {pretty_text(alert['tradeoff_a'])}")
            if alert.get("tradeoff_b"):
                st.markdown(f"- **Đánh đổi B:** {pretty_text(alert['tradeoff_b'])}")

        status = alert["status"]
        if status == "OPEN" and not is_manager(user):
            st.caption(MANAGER_ONLY_NOTE)
        elif status == "OPEN" and alert["options"]:
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
            if not is_manager(user):
                st.caption(MANAGER_ONLY_NOTE)
            elif po["status"] == "DRAFT":
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
    ing_by_id = {i["id"]: i for i in api.get_ingredients()}
    render_run_block(user)

    st.divider()
    st.subheader("Cảnh báo")
    alerts = api.list_alerts()
    if not alerts:
        st.info("Chưa có cảnh báo nào. Hãy chạy rà soát." if is_manager(user) else "Chưa có cảnh báo nào.")
    pending = [a for a in alerts if a["status"] in ("OPEN", "SELECTED")]
    finished = [a for a in alerts if a["status"] in ("RESOLVED", "DISMISSED")]
    for alert in pending:
        render_alert_card(alert, user)
    if alerts and not pending:
        st.success("Không còn cảnh báo nào cần xử lý.")
    if finished:
        superseded = superseded_alert_ids()
        with st.expander(f"Đã xử lý / đã bỏ qua ({len(finished)})"):
            for alert in finished:
                render_alert_card(alert, user, superseded)

    st.divider()
    st.subheader("PO cần xử lý" if is_manager(user) else "PO đang chờ quản lý duyệt / gửi")
    active = [p for p in api.list_pos()
              if p["closed_reason"] is None and p["status"] in ("DRAFT", "APPROVED")]
    if not active:
        st.info("Không có PO nào đang chờ duyệt hoặc chờ gửi.")
    for po in active:
        render_po_card(po, ing_by_id, user)


# ===========================================================================
# ====== TAB 3, TAB 4 VÀ HÀM MAIN ======
# ===========================================================================
CHAT_EXAMPLES = [
    "Kem tươi còn bao nhiêu và lô nào sắp hết hạn?",
    "3 ngày tới cần bao nhiêu bơ cho đơn đặt trước?",
    "PO nào đang giao trễ?",
]


def render_tab3(user):
    st.subheader("Hỏi đáp tồn kho (chỉ đọc)")
    st.caption("Hỏi về tồn kho, lô hàng, đơn đặt trước, NCC, PO. Khung chat chỉ xem số liệu, "
               "không tạo hay sửa PO được. Tối đa 500 ký tự.")
    st.caption("Ví dụ: " + " · ".join(f"«{q}»" for q in CHAT_EXAMPLES))

    history = st.session_state.setdefault("chat_history", [])
    for item in history:
        with st.chat_message("user"):
            st.markdown(item["question"])
        with st.chat_message("assistant"):
            if item["refused"]:
                st.warning(pretty_text(item["answer"]))
            else:
                st.markdown(pretty_text(item["answer"]))

    with st.form("chat_form", clear_on_submit=True):
        question = st.text_input("Câu hỏi của bạn", max_chars=500, key="chat_question")
        c1, c2 = st.columns([1, 5])
        asked = c1.form_submit_button("💬 Hỏi", type="primary")
        cleared = c2.form_submit_button("🧹 Xóa lịch sử")
    if cleared:
        st.session_state["chat_history"] = []
        st.rerun()
    if asked and question.strip():
        with st.spinner("Đang tra cứu..."):
            result = api.ask_chat(question, user)
        history.append({"question": question.strip(), "answer": result["answer"],
                        "refused": result["refused"]})
        st.rerun()


# ---- Tab 4: đổi nhật ký thô sang câu dễ đọc (chỉ để hiển thị) -------------------
EVENT_LABELS = {
    "RUN_STARTED": "▶️ Bắt đầu rà soát", "RUN_SUCCEEDED": "✅ Rà soát xong",
    "RUN_FAILED": "❌ Rà soát lỗi", "RUN_REJECTED_DUPLICATE": "⚠️ Chặn chạy trùng",
    "ALERT_CREATED": "🔔 Tạo cảnh báo", "OPTION_SELECTED": "👉 Chọn phương án",
    "ALERT_DISMISSED": "🚫 Bỏ qua cảnh báo", "PO_CREATED": "📝 Tạo PO nháp",
    "PO_APPROVED": "✅ Duyệt PO", "PO_REJECTED": "❌ Từ chối PO", "PO_CANCELLED": "🗑 Hủy PO",
    "PO_SEND_FAILED": "❌ Gửi PO thất bại", "PO_SENT": "📤 Gửi PO",
    "DELIVERY_DELAYED": "⚠️ Giao trễ", "DELIVERY_RECEIVED": "📥 Nhận hàng",
    "BATCH_RECEIVED": "📦 Nhập lô mới", "STOCK_ISSUED": "📤 Xuất kho",
    "GUARDRAIL_BLOCKED": "⛔ Bị chặn", "CHAT_ANSWERED": "💬 Chat trả lời",
    "CHAT_REFUSED": "⚠️ Chat từ chối",
}
RED_EVENTS = {"RUN_FAILED", "PO_SEND_FAILED", "GUARDRAIL_BLOCKED"}
ORANGE_EVENTS = {"RUN_REJECTED_DUPLICATE", "DELIVERY_DELAYED", "CHAT_REFUSED"}
ACTION_LABELS = {
    "create_draft_po": "tạo PO", "approve_po": "duyệt PO", "reject_po": "từ chối PO",
    "cancel_po": "hủy PO", "send_po": "gửi PO", "mark_received": "nhận hàng",
    "dismiss_alert": "bỏ qua cảnh báo", "issue_stock_fefo": "xuất kho",
}


def fmt_actor(actor):
    key = (actor or "").lower()
    return {"scheduler": "⏰ Lịch 08:00", "system": "⚙️ Hệ thống", "gemini": "🤖 Gemini"}.get(
        key, f"👤 {actor}")


def fmt_entity(log, ing_by_id):
    kind, eid = log["entity_type"], log["entity_id"]
    if kind == "alert":
        return f"Cảnh báo #{eid}"
    if kind == "audit_run":
        return f"Phiên #{eid}"
    if kind == "batch":
        return f"Lô {eid}"
    if kind == "ingredient":
        return ing_by_id.get(eid, {}).get("name", eid)
    if kind == "chat":
        return "Chat"
    return eid or "—"


def log_detail(log):
    """detail_json của một dòng nhật ký, đổi sang dict (lỗi hoặc rỗng thì trả {})."""
    try:
        d = json.loads(log["detail_json"] or "{}")
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def is_auto_superseded(log):
    """Cảnh báo cũ bị hệ thống tự đóng vì đã có phiên rà soát mới (ALERT_DISMISSED + SUPERSEDED)."""
    return log["event_type"] == "ALERT_DISMISSED" and log_detail(log).get("reason") == "SUPERSEDED"


def describe_log(log, ing_by_id, sup_names, po_by_id):
    """Một câu tiếng Việt ngắn tóm tắt sự kiện, dựng từ detail_json."""
    d = log_detail(log)
    ing = ing_by_id.get(d.get("ingredient_id"), {})
    ing_name, unit = ing.get("name", d.get("ingredient_id", "")), ing.get("unit", "")
    qty = fmt_qty(d["qty"]) if isinstance(d.get("qty"), (int, float)) else d.get("qty")
    sup = sup_names.get(d.get("sup_id"), d.get("sup_id"))
    ev = log["event_type"]

    if ev == "RUN_STARTED":
        how = "theo lịch 08:00" if d.get("trigger") == "SCHEDULED" else "do quản lý bấm"
        return f"Rà soát {how}" + (" (chạy lại)" if d.get("rerun") else "")
    if ev == "RUN_SUCCEEDED":
        text = f"Có {d.get('alert_count', 0)} cảnh báo"
        if d.get("superseded"):
            text += f" · Đóng {len(d['superseded'])} cảnh báo cũ của phiên trước"
        return text + (" · Gemini không phản hồi, dùng câu mẫu" if d.get("fallback") else "")
    if ev in ("RUN_FAILED", "RUN_REJECTED_DUPLICATE", "PO_SEND_FAILED"):
        return d.get("error") or d.get("message") or d.get("reason") or ""
    if ev == "DELIVERY_DELAYED":
        return f"Hạn giao {fmt_date(d.get('expected_delivery_date'))} đã qua mà chưa nhận hàng"
    if ev == "ALERT_CREATED":
        cause = CAUSE_LABELS.get(d.get("main_cause"), d.get("main_cause") or "")
        return f"{ing_name} thiếu hàng · nguyên nhân chính: {cause}"
    if ev == "OPTION_SELECTED":
        return f"Chọn phương án {d.get('option')}: {sup}, số lượng {qty}"
    if ev == "PO_APPROVED":
        return "Quản lý đã duyệt, chờ gửi NCC"
    if ev == "PO_CANCELLED":
        return "Hủy PO đã duyệt, không gửi NCC"
    if ev == "ALERT_DISMISSED":
        if is_auto_superseded(log):
            return "Hệ thống tự đóng vì đã có phiên rà soát mới"
        return "Quản lý bỏ qua, không đặt hàng"
    if ev == "PO_CREATED":
        return f"{ing_name}: {qty} {unit} từ {sup}, tổng {fmt_money(d.get('total_cost') or 0)}"
    if ev == "PO_REJECTED":
        return f"Lý do: {d['reason']}" if d.get("reason") else "Không ghi lý do"
    if ev == "PO_SENT":
        po = po_by_id.get(log["entity_id"], {})
        sup_name = po.get("sup_name") or "NCC"
        file_name = os.path.basename(str(d.get("file") or "")) or "—"
        return (f"Đã gửi đơn cho {sup_name} · hàng dự kiến về "
                f"{fmt_date(d.get('expected_delivery_date'))} · file {file_name}")
    if ev == "STOCK_ISSUED":
        stock_ing = ing_by_id.get(d.get("ingredient_id") or log["entity_id"], {})
        what = " ".join(x for x in (str(qty), stock_ing.get("unit", ""), stock_ing.get("name", "")) if x)
        lots = "; ".join(f"{b['batch_id']} -{fmt_qty(b['taken'])}" for b in d.get("batches", []))
        return f"Xuất {what}, trừ lô: {lots}"
    if ev == "BATCH_RECEIVED":
        return f"{qty} {unit} {ing_name} từ {d.get('po_id')}, hạn dùng {fmt_dt(d.get('expiry_date'))}"
    if ev == "DELIVERY_RECEIVED":
        return f"Nhận {qty}, tạo lô {d.get('batch_id')}"
    if ev == "GUARDRAIL_BLOCKED":
        action = ACTION_LABELS.get(d.get("action"), d.get("action") or "thao tác")
        return f"Chặn khi {action} [{d.get('code')}] · {d.get('message', '')}"
    if ev == "CHAT_ANSWERED":
        return f"Hỏi: «{d.get('question', '')}»"
    if ev == "CHAT_REFUSED":
        reason = {"EMPTY": "câu hỏi trống", "TOO_LONG": "câu hỏi quá dài"}.get(
            d.get("reason"), "yêu cầu ghi dữ liệu hoặc ngoài phạm vi")
        return f"Hỏi: «{d.get('question', '')}» · lý do: {reason}"
    return ""


def as_sentences(text):
    """'a · b · c' -> 'A. B. C' (mỗi ý thành một câu, viết hoa chữ đầu)."""
    parts = [p.strip() for p in str(text or "").split(" · ") if p.strip()]
    return ". ".join(p[0].upper() + p[1:] for p in parts)


def log_row_styler(events):
    """Tô màu từng dòng theo loại sự kiện (events[i] là event_type của dòng i)."""
    def _apply(row):
        event = events[row.name]
        style = RED_STYLE if event in RED_EVENTS else ORANGE_STYLE if event in ORANGE_EVENTS else ""
        return [style] * len(row)
    return _apply


def render_tab4(user):
    st.subheader("Nhật ký hệ thống")

    st.markdown("**Phiên rà soát**")
    runs = api.get_audit_runs()
    if runs:
        run_rows = [{
            "Phiên": f"#{r['run_id']}", "Ngày": fmt_date(r["run_date"]),
            "Cách chạy": ("⏰ Lịch 08:00" if r["trigger"] == "SCHEDULED" else "👤 Bấm tay")
                         + (" · chạy lại" if r["is_rerun"] else ""),
            "Trạng thái": r["status"], "Bắt đầu": fmt_dt(r["started_at"]),
            "Kết thúc": fmt_dt(r["finished_at"]), "Số cảnh báo": str(r["alert_count"]),
            "Lỗi": r["error"] or "",
        } for r in runs]
        show_table(styled_table(pd.DataFrame(run_rows), ("Trạng thái", "FAILED", RED_STYLE)))
    else:
        st.info("Chưa có phiên rà soát nào.")

    st.markdown("**Nhật ký sự kiện**")
    c1, c2, c3 = st.columns([3, 1, 1])
    event = c1.selectbox(
        "Lọc theo loại sự kiện", ["__ALL__"] + list(api.list_event_types()), key="log_filter",
        format_func=lambda e: "Tất cả" if e == "__ALL__" else f"{EVENT_LABELS.get(e, e)} ({e})")
    limit = c2.selectbox("Số dòng", [50, 200, 1000], key="log_limit")
    show_raw = c3.checkbox("Hiện mã kỹ thuật", key="log_raw",
                           help="Thêm cột event_type và dữ liệu gốc (detail_json).")
    logs = api.get_audit_logs(event_type=None if event == "__ALL__" else event, limit=limit)
    if not logs:
        st.info("Chưa có sự kiện nào.")
        return

    ing_by_id = {i["id"]: i for i in api.get_ingredients()}
    po_by_id = {p["po_id"]: p for p in api.list_pos()}
    sup_names = {p["sup_id"]: p["sup_name"] for p in po_by_id.values()}
    for ing_id in ing_by_id:
        for s_row in api.get_suppliers(ing_id):
            sup_names.setdefault(s_row["sup_id"], s_row["name"])

    rows = []
    for log in logs:
        row = {
            "Thời điểm": fmt_dt(log["created_at"]), "Ai thực hiện": "⚙️ Hệ thống" if is_auto_superseded(log) else fmt_actor(log["actor"]),
            "Sự kiện": "🔒 Tự đóng cảnh báo" if is_auto_superseded(log)
                       else EVENT_LABELS.get(log["event_type"], log["event_type"]),
            "Đối tượng": fmt_entity(log, ing_by_id),
            "Nội dung": as_sentences(pretty_text(describe_log(log, ing_by_id, sup_names, po_by_id))),
        }
        if show_raw:
            row["event_type"] = log["event_type"]
            row["detail_json"] = log["detail_json"] or ""
        rows.append(row)
    df = pd.DataFrame(rows)
    st.caption("Mới nhất ở trên. Dòng ĐỎ = bị chặn hoặc lỗi; dòng CAM = cần chú ý (giao trễ, chat từ chối, chạy trùng).")
    show_table(df.style.apply(log_row_styler([log["event_type"] for log in logs]), axis=1), max_height=520)


@st.cache_resource
def recover_stale_runs_once():
    """Chạy 1 lần khi app khởi động: phiên RUNNING bị bỏ dở quá lâu -> FAILED (Mục 9)."""
    return api.recover_stale_runs()


def main():
    load_env_file()
    recover_stale_runs_once()
    if "user" not in st.session_state:
        render_login()
        return
    user = st.session_state["user"]
    render_sidebar(user)
    st.title("🥐 Trợ lý cung ứng nguyên vật liệu - Tiệm bánh")
    render_banner()
    tab1, tab2, tab3, tab4 = st.tabs(
        ["📦 Kho & Đơn đặt trước", "🔎 Rà soát", "💬 Hỏi đáp tồn kho", "📜 Nhật ký hệ thống"])
    with tab1:
        render_tab1(user)
    with tab2:
        render_tab2(user)
    with tab3:
        render_tab3(user)
    with tab4:
        render_tab4(user)


main()
