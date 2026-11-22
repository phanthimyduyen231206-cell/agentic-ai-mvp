"""app.py - Giao diện Streamlit 4 tab: Kho, Rà soát, Hỏi đáp, Nhật ký (Yến).

Chạy:  streamlit run app.py

Nguyên tắc: file này CHỈ nhận thao tác và hiển thị, KHÔNG chứa luật nghiệp vụ
(tính tồn, chọn A/B, kiểm tra quyền... đều do services/ làm). Mọi hàm backend
lấy qua services/ui_api.py (contracts Mục 11), kể cả MOQ và cảnh báo số lượng.
Chạy data/init_db.py trước lần đầu.
"""
import hmac
import json
import os
import re
from datetime import date, datetime, timedelta
from datetime import time as dtime

import pandas as pd
import streamlit as st

# Mọi hàm backend lấy qua services/ui_api.py (contracts Mục 11).
from services import ui_api as api

st.set_page_config(page_title="Trợ lý cung ứng tiệm bánh", page_icon="🥐", layout="wide")

# Font Be Vietnam Pro (tải từ Google Fonts) + vài chỉnh nhỏ cho đồng bộ màu với .streamlit/config.toml.
# Mất mạng thì dùng font dự phòng có sẵn trên máy: Segoe UI (Windows), Roboto, Arial.
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700&display=swap');
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button,
.stApp h1, .stApp h2, .stApp h3, .stApp h4, [data-testid="stMetricValue"], [data-testid="stMetricLabel"] {
    font-family: 'Be Vietnam Pro', 'Segoe UI', Roboto, Arial, sans-serif;
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
/* Dòng phiên rà soát ở Tab Rà soát: chữ và nút to hơn, 3 phần thẳng hàng giữa */
[data-testid="stMarkdownContainer"]:has(.run-line) { margin-bottom: 0 !important; }
.run-line { font-family: 'Be Vietnam Pro', 'Segoe UI', Roboto, Arial, sans-serif; font-size: 1.25rem; line-height: 1.5; margin: 0; }
.run-line .run-sub { font-size: 1.05rem; white-space: nowrap; }
.st-key-rerun_flag, .st-key-btn_run_audit { display: flex; align-items: center; justify-content: center; }
.st-key-rerun_flag label p { font-size: 1.1rem; }
.st-key-btn_run_audit button { font-size: 1.1rem; padding: 0.6rem 1.3rem; }
.st-key-btn_run_audit button p { font-size: 1.1rem; }
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


def run_action(tab, action, ok_message, next_view=None):
    """Chạy một thao tác GHI. Thành công -> báo xanh; GuardrailError -> báo đỏ. Rồi vẽ lại trang.
    next_view = (khóa mục con, mã mục): thành công thì tự chuyển sang mục đó (vd Tạo PO -> PO cần xử lý)."""
    try:
        result = action()
        flash(tab, "success", ok_message(result) if callable(ok_message) else ok_message)
        if next_view:
            st.session_state[next_view[0] + "_next"] = next_view[1]
    except api.GuardrailError as err:
        flash(tab, "error", f"⛔ Bị chặn: {err.message}")
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
                st.error("Chưa cài mã PIN quản lý (biến MANAGER_PIN trong file .env). Hãy báo người quản trị hệ thống.")
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


# ---------------------------------------------------------------------------
# Thanh chọn mục con (thay cho st.tabs để NHỚ mục đang xem sau mỗi lần bấm nút)
# ---------------------------------------------------------------------------
def sub_nav(key, options, default):
    """Hàng nút chọn mục con, nhớ lựa chọn trong st.session_state[key].

    options: {mã cố định: nhãn hiện ra}. Nhãn có thể đổi (vd số đếm "(2)" -> "(3)")
    mà mục đang chọn vẫn giữ nguyên, vì lựa chọn lưu theo MÃ chứ không theo nhãn.
    Muốn tự chuyển mục sau một thao tác: đặt st.session_state[key + "_next"] = mã.
    """
    last_key = key + "_last"
    pending = st.session_state.pop(key + "_next", None)
    if pending in options:
        st.session_state[key] = pending
    if st.session_state.get(key) not in options:
        st.session_state[key] = st.session_state.get(last_key) if st.session_state.get(last_key) in options else default

    def _keep_one_selected():
        # Bấm lại vào mục đang chọn sẽ bỏ chọn -> giữ mục cũ, luôn có 1 mục được chọn.
        if st.session_state.get(key) is None:
            st.session_state[key] = st.session_state.get(last_key, default)

    nav_args = dict(format_func=lambda v: options[v], key=key, selection_mode="single",
                    label_visibility="collapsed", on_change=_keep_one_selected)
    try:
        # width="stretch": các nút chia đều hết chiều ngang
        value = st.segmented_control("Chọn mục", list(options), width="stretch", **nav_args)
    except TypeError:  # Streamlit bản cũ chưa có tham số width
        value = st.segmented_control("Chọn mục", list(options), **nav_args)
    if value not in options:
        value = st.session_state.get(key) if st.session_state.get(key) in options else default
    st.session_state[last_key] = value
    return value


PO_STATUS_LABELS = {"DRAFT": "📝 Nháp", "APPROVED": "✅ Đã duyệt", "SENT": "📤 Đã gửi"}
DELIVERY_LABELS = {"PENDING": "🚚 Đang giao", "DELAYED": "⚠️ Giao trễ", "RECEIVED": "📥 Đã nhận"}
CLOSED_LABELS = {"REJECTED": "❌ Bị từ chối", "CANCELLED": "🗑 Đã hủy"}
RUN_STATUS_LABELS = {"SUCCEEDED": "✅ Thành công", "FAILED": "❌ Lỗi", "RUNNING": "⏳ Đang chạy"}


def po_lifecycle_label(po):
    if po.get("closed_reason"):
        return CLOSED_LABELS.get(po["closed_reason"], po["closed_reason"])
    return PO_STATUS_LABELS.get(po["status"], po["status"])


# ===========================================================================
# ====== TAB 1: KHO & ĐƠN ĐẶT TRƯỚC ======
# ===========================================================================
TAB1_VIEWS = {
    "stock": "📊 Tồn kho & dự báo", "lots": "🧾 Lô FEFO", "preorders": "🎂 Đơn đặt trước",
    "issue": "📤 Xuất kho", "po": "🚚 PO & nhận hàng",
}


def render_tab1(user):
    show_flash("tab1")
    ingredients = api.get_ingredients()
    ing_by_id = {i["id"]: i for i in ingredients}
    overview = api.stock_overview(3)
    view = sub_nav("tab1_view", TAB1_VIEWS, "stock")
    if view == "stock":
        render_stock_view(overview)
    elif view == "lots":
        render_lots_view(ingredients, ing_by_id, overview)
    elif view == "preorders":
        render_preorders_view()
    elif view == "issue":
        render_issue_view(ingredients, ing_by_id, user)
    else:
        render_po_list_and_receive(ing_by_id, api.now(), user)


def count_expiring_lots(overview):
    return len({e["batch_id"] for row in overview for e in row["expired_lots"]})


def render_stock_view(overview):
    n_short = sum(1 for r in overview if r["short"])
    n_lots = count_expiring_lots(overview)
    st.markdown(f"**⚠️ {n_short} nguyên liệu thiếu · ✅ {len(overview) - n_short} nguyên liệu đủ · "
                f"🕒 {n_lots} lô sẽ hết hạn trước khi dùng hết**")
    st.caption("Tồn dự kiến = Tồn còn hạn − Đơn đặt trước − Bán lẻ 3 ngày + Hàng sắp về − Sẽ hết hạn")
    st.caption("Hạn tính theo FEFO: Dùng lô hết hạn sớm nhất trước")
    only_short = st.checkbox("Chỉ hiện nguyên liệu thiếu", key="stock_only_short")
    rows = sorted(overview, key=lambda r: (not r["short"], r["name"]))
    if only_short:
        rows = [r for r in rows if r["short"]]
    if not rows:
        st.success("Không có nguyên liệu nào thiếu trong 3 ngày tới.")
        return
    table = [{
        "Nguyên liệu": f"{r['name']} ({r['unit']})",
        "Tồn còn hạn": r["on_hand"],
        "− Đơn đặt trước": r["preorder"],
        "− Bán lẻ 3 ngày": f"{fmt_qty(r['retail'])} ({fmt_qty(r['daily'])}/ngày)",
        "+ Hàng sắp về": r["incoming"],
        "− Sẽ hết hạn": r["expiring"],
        "= Tồn dự kiến": r["projected"],
        "Mức tối thiểu": r["min_stock"],
        "Tình trạng": "⚠️ THIẾU" if r["short"] else "✅ Đủ",
    } for r in rows]
    show_table(styled_table(pd.DataFrame(table), ("Tình trạng", "⚠️", RED_STYLE)))
    mismatched = [r["name"] for r in rows if not r["consistent"]]
    if mismatched:
        st.warning("Số liệu các cột chưa khớp với Tồn dự kiến ở: " + ", ".join(mismatched)
                   + ". Báo nhóm kiểm tra services/fefo.py và agent/tools.py.")


def render_lots_view(ingredients, ing_by_id, overview):
    choice = st.selectbox(
        "Xem nguyên liệu", ["__ALL__"] + [i["id"] for i in ingredients], key="lot_filter",
        format_func=lambda x: "Tất cả nguyên liệu" if x == "__ALL__" else ing_by_id[x]["name"])
    now = api.now()
    expired_map = {}
    for row in overview:
        for e in row["expired_lots"]:
            expired_map[e["batch_id"]] = expired_map.get(e["batch_id"], 0) + e["qty"]
    lots = []
    for ing in ingredients:
        if choice in ("__ALL__", ing["id"]):
            lots += [(ing, lot) for lot in api.get_batches_fefo(ing["id"], now)]
    lots.sort(key=lambda x: (expired_map.get(x[1]["batch_id"], 0) <= 0, x[1]["expiry_date"], x[1]["received_at"]))
    lot_rows = []
    for ing, lot in lots:
        exp_qty = expired_map.get(lot["batch_id"], 0)
        lot_rows.append({
            "Nguyên liệu": ing["name"], "Mã lô": lot["batch_id"], "Số lượng": float(lot["qty"]),
            "Đơn vị": ing["unit"], "Hạn dùng": fmt_dt(lot["expiry_date"]),
            "Còn lại (ngày)": round((to_dt(lot["expiry_date"]) - now).total_seconds() / 86400, 1),
            "Ghi chú": f"⚠️ {fmt_qty(exp_qty)} {ing['unit']} sẽ hết hạn trước khi kịp dùng" if exp_qty else "",
        })
    if lot_rows:
        show_table(styled_table(pd.DataFrame(lot_rows), ("Ghi chú", "⚠️", RED_STYLE)), max_height=520)
    else:
        st.info("Không có lô nào còn hạn.")


def render_preorders_view():
    pre_rows = [{"Mã đơn": p["order_id"], "Bánh": p["cake_name"], "Số lượng": p["quantity"],
                 "Ngày giao": fmt_date(p["delivery_date"])}
                for p in sorted(api.list_preorders(), key=lambda p: (p["delivery_date"], p["order_id"]))]
    if pre_rows:
        show_table(pd.DataFrame(pre_rows))
    else:
        st.info("Chưa có đơn đặt trước nào.")


def render_issue_view(ingredients, ing_by_id, user):
    st.caption("Hệ thống tự trừ theo FEFO: lấy lô hết hạn sớm nhất trước.")
    if not is_manager(user):
        st.caption(MANAGER_ONLY_NOTE)
        return
    with st.form("form_issue"):
        ing_id = st.selectbox(
            "Nguyên liệu", [i["id"] for i in ingredients],
            format_func=lambda x: f"{ing_by_id[x]['name']} ({ing_by_id[x]['unit']})")
        qty = st.number_input("Số lượng xuất", min_value=0.0, value=1.0, step=1.0)
        submitted = st.form_submit_button("📤 Xuất kho", type="primary")
    if submitted:
        ing = ing_by_id[ing_id]

        def _ok(taken):
            detail = "; ".join(f"{t['batch_id']}: -{fmt_qty(t['taken'])}" for t in taken)
            return f"✅ Đã xuất {fmt_qty(qty)} {ing['unit']} {ing['name']}. Đã trừ từ lô: {detail}."

        run_action("tab1", lambda: api.issue_stock_fefo(ing_id, float(qty), user), _ok)


def render_po_list_and_receive(ing_by_id, now, user):
    st.markdown("**Danh sách PO**")
    pos = api.list_pos()
    if pos:
        po_rows = [{
            "Mã PO": p["po_id"],
            "Nguyên liệu": ing_by_id.get(p["ingredient_id"], {}).get("name", p["ingredient_id"]),
            "NCC": p.get("sup_name") or p["sup_id"],
            "Số lượng": f"{fmt_qty(p['order_qty'])} {ing_by_id.get(p['ingredient_id'], {}).get('unit', '')}",
            "Tổng tiền": fmt_money(p["total_cost"]),
            "Vòng đời PO": po_lifecycle_label(p),
            "Giao hàng": DELIVERY_LABELS.get(p["delivery_status"], "—"),
            "Dự kiến giao": fmt_date(p["expected_delivery_date"]),
        } for p in reversed(pos)]
        show_table(styled_table(pd.DataFrame(po_rows), ("Giao hàng", "⚠️", ORANGE_STYLE)), max_height=420)
    else:
        st.info("Chưa có PO nào.")

    st.markdown("**Xác nhận đã nhận hàng**")
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
                f"{fmt_qty(po_by_id[x]['order_qty'])} "
                f"({DELIVERY_LABELS.get(po_by_id[x]['delivery_status'], po_by_id[x]['delivery_status'])})"))
        st.caption("Nhận một lần đủ số lượng đặt.")
        use_expiry = st.checkbox(
            "Nhập hạn dùng in trên bao bì (nếu bỏ trống, hệ thống tự tính theo hạn dùng mặc định)",
            key="recv_use_expiry")
        expiry_date = st.date_input("Hạn dùng", value=now.date() + timedelta(days=7), min_value=now.date(),
                                    key="recv_expiry") if use_expiry else None
        if st.button("📥 Xác nhận đã nhận đủ hàng", type="primary", key="btn_receive"):
            po = po_by_id[po_id]
            expiry_dt = datetime.combine(expiry_date, dtime(23, 59, 59)) if expiry_date else None
            run_action(
                "tab1",
                lambda: api.mark_received(po_id, float(po["order_qty"]), user, expiry_dt),
                lambda batch_id: f"✅ Đã nhận hàng {po_id}. Tạo lô mới {batch_id}.")


# ===========================================================================
# ====== TAB 2: RÀ SOÁT ======
# ===========================================================================
def render_run_block(user):
    """Thông tin phiên rà soát hôm nay gọn trong 1 dòng, nút chạy ngay bên cạnh."""
    run = api.get_today_run()
    with st.container(border=True):
        info, rerun_col, button_col = st.columns([5, 1.6, 1.6], vertical_alignment="center")
        if run:
            how = "⏰ Lịch 08:00" if run["trigger"] == "SCHEDULED" else "👤 Bấm tay"
            if run["is_rerun"]:
                how += " · Chạy lại"
            info.markdown(f'<div class="run-line"><b>Phiên #{run["run_id"]}</b><br>'
                          f'<span class="run-sub">{RUN_STATUS_LABELS.get(run["status"], run["status"])} · {how} · '
                          f'{run["alert_count"]} cảnh báo · {fmt_dt(run["started_at"])}</span></div>',
                          unsafe_allow_html=True)
        elif is_manager(user):
            info.markdown('<div class="run-line"><b>Hôm nay chưa chạy rà soát.</b> Bấm nút bên cạnh để chạy.</div>',
                          unsafe_allow_html=True)
        else:
            info.markdown('<div class="run-line"><b>Hôm nay chưa chạy rà soát.</b> Quản lý sẽ chạy hoặc '
                          'hệ thống tự chạy lúc 08:00.</div>', unsafe_allow_html=True)
        if not is_manager(user):
            rerun_col.caption("🔒 Chỉ quản lý chạy rà soát")
        else:
            rerun = rerun_col.checkbox("Chạy lại", key="rerun_flag",
                                       help="Rà soát thêm lần nữa trong ngày") if run else False
            if button_col.button("▶️ Chạy rà soát", type="primary", key="btn_run_audit"):
                with st.spinner("Đang rà soát..."):
                    result = api.run_morning_audit("MANUAL", rerun=rerun, user=user)
                kind = {"SUCCEEDED": "success", "ALREADY_DONE": "info",
                        "REJECTED_DUPLICATE": "warning", "FAILED": "error"}.get(result["status"], "info")
                flash("tab2", kind, result["message"])
                st.session_state["tab2_view_next"] = "alerts"
                st.rerun()
        if run and run.get("error"):
            st.error(run["error"])


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
    labels = {o["option"]: f"Phương án {o['option']} - {o['sup_name']}" for o in options}
    c1, c2 = st.columns([3, 2])
    letter = c1.radio("Chọn phương án", list(labels), format_func=lambda k: labels[k],
                      horizontal=True, key=f"opt_{aid}")
    opt = next(o for o in options if o["option"] == letter)
    moq = api.get_moq(alert["ingredient_id"], opt["sup_id"])
    qty = c2.number_input(f"Số lượng đặt ({unit})", min_value=0.0, value=float(opt["order_qty"]),
                          step=float(moq), key=f"qty_{aid}_{letter}")
    final_qty = api.round_up_moq(qty, moq)
    if qty <= 0:
        st.warning("Số lượng phải lớn hơn 0.")
    elif final_qty != qty:
        st.info(f"Số lượng sẽ được làm tròn LÊN thành **{fmt_qty(final_qty)} {unit}** "
                f"(MOQ của {opt['sup_name']} là {fmt_qty(moq)}).")
    warning = api.order_qty_warning(final_qty, alert["shortage_qty"], unit)
    if warning:
        st.warning(f"⚠️ {warning} Bạn vẫn có thể tạo PO nếu đã cân nhắc.")
    st.caption(f"MOQ của {opt['sup_name']}: {fmt_qty(moq)} {unit} · "
               f"Tổng tiền dự kiến: {fmt_money(final_qty * opt['unit_price'])}")
    b1, b2, _ = st.columns([1.4, 1.4, 3])
    if b1.button("✅ Tạo PO nháp", type="primary", key=f"create_{aid}", disabled=qty <= 0):
        run_action("tab2", lambda: api.create_draft_po(aid, letter, float(final_qty), user),
                   lambda po_id: f"✅ Đã tạo PO nháp {po_id}. Đã chuyển sang mục «PO cần xử lý» để duyệt.",
                   next_view=("tab2_view", "po"))
    if b2.button("🚫 Bỏ qua", key=f"dismiss_{aid}"):
        run_action("tab2", lambda: api.dismiss_alert(aid, user),
                   f"Đã bỏ qua cảnh báo {alert['ingredient_name']}.")


def superseded_alert_ids():
    """Mã các cảnh báo bị hệ thống tự đóng vì có phiên rà soát mới (đọc từ nhật ký)."""
    return {int(log["entity_id"]) for log in api.get_audit_logs(event_type="ALERT_DISMISSED")
            if is_auto_superseded(log) and str(log["entity_id"]).isdigit()}


def alert_title(alert, superseded=frozenset()):
    """Dòng tóm tắt của một cảnh báo (nhãn của khung thu gọn)."""
    name, unit = alert["ingredient_name"], alert["unit"]
    cause = CAUSE_LABELS.get(alert["main_cause"], alert["main_cause"] or "")
    status = alert["status"]
    if status == "OPEN":
        return f"⚠️ {name} · Thiếu {fmt_qty(alert['shortage_qty'])} {unit} · Nguyên nhân: {cause}"
    if status == "SELECTED":
        return f"🟡 {name} · Đã chọn phương án {alert['selected_option']} → {alert['po_id']}"
    if status == "RESOLVED":
        return f"🟢 {name} · Đã xử lý xong (PO {alert['po_id'] or '—'})"
    if alert["alert_id"] in superseded:
        return f"⚪ {name} · Hệ thống tự đóng (đã có phiên rà soát mới)"
    return f"⚪ {name} · Đã bỏ qua"


def render_alert_detail(alert, user):
    unit = alert["unit"]
    parts = []
    for c in alert["causes"]:
        star = "⭐ " if c["code"] == alert["main_cause"] else ""
        qty_text = f" {fmt_qty(c['qty'])}" if c.get("qty") is not None else ""
        parts.append(f"{star}{CAUSE_LABELS.get(c['code'], c['code'])}{qty_text}")
    st.markdown(f"**Nguyên nhân** ({unit}): " + " · ".join(parts))
    st.markdown(f"Tồn dự kiến **{fmt_qty(alert['projected_stock'])} {unit}** · Tối thiểu "
                f"{fmt_qty(alert['min_stock'])} {unit} · Cần bổ sung **{fmt_qty(alert['shortage_qty'])} {unit}**")
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
        if st.button("🚫 Bỏ qua", key=f"dismiss_{alert['alert_id']}"):
            run_action("tab2", lambda: api.dismiss_alert(alert["alert_id"], user), "Đã bỏ qua cảnh báo.")
    elif status == "SELECTED":
        st.info(f"Đã chọn phương án {alert['selected_option']}, số lượng "
                f"{fmt_qty(alert['selected_qty'])} {unit} → PO **{alert['po_id']}**. Xử lý ở mục «PO cần xử lý».")


def render_alerts_view(alerts, user):
    open_alerts = [a for a in alerts if a["status"] == "OPEN"]
    selected = [a for a in alerts if a["status"] == "SELECTED"]
    if not alerts:
        st.info("Chưa có cảnh báo nào. Hãy chạy rà soát." if is_manager(user) else "Chưa có cảnh báo nào.")
        return
    if not open_alerts:
        st.success("Không còn cảnh báo nào chờ quyết định.")
    for i, alert in enumerate(open_alerts):
        with st.expander(alert_title(alert), expanded=(i == 0)):
            render_alert_detail(alert, user)
    if selected:
        st.caption("Đã chọn phương án, đang chờ xử lý PO:")
        for alert in selected:
            with st.expander(alert_title(alert)):
                render_alert_detail(alert, user)


def render_po_row(po, ing_by_id, user):
    pid = po["po_id"]
    ing = ing_by_id.get(po["ingredient_id"], {})
    with st.container(border=True):
        text, b1, b2 = st.columns([5, 1.3, 1.3], vertical_alignment="center")
        text.markdown(f"**{pid}** · {ing.get('name', po['ingredient_id'])} · "
                      f"{fmt_qty(po['order_qty'])} {ing.get('unit', '')} · "
                      f"{po.get('sup_name') or po['sup_id']} · {fmt_money(po['total_cost'])} · "
                      f"{po_lifecycle_label(po)}")
        if not is_manager(user):
            b2.caption("🔒 Chờ quản lý")
        elif po["status"] == "DRAFT":
            if b1.button("✅ Duyệt", type="primary", key=f"approve_{pid}"):
                run_action("tab2", lambda: api.approve_po(pid, user), f"✅ Đã duyệt {pid}. Có thể gửi PO.")
            with b2.popover("❌ Từ chối"):
                reason = st.text_input("Lý do (không bắt buộc)", key=f"reason_{pid}")
                if st.button("Xác nhận từ chối", key=f"reject_{pid}"):
                    run_action("tab2", lambda: api.reject_po(pid, user, reason), f"Đã từ chối {pid}.")
        elif po["status"] == "APPROVED":
            retry = bool(po.get("last_send_error"))
            if b1.button("🔁 Gửi lại" if retry else "📤 Gửi PO", type="primary", key=f"send_{pid}"):
                run_action("tab2", lambda: api.send_po(pid, user),
                           lambda path: f"✅ Đã gửi {pid}. File PO: {path}")
            if b2.button("🗑 Hủy", key=f"cancel_{pid}"):
                run_action("tab2", lambda: api.cancel_po(pid, user), f"Đã hủy {pid}.")
        if po.get("last_send_error"):
            st.error(f"Gửi thất bại (lần {po['send_attempts']}): {po['last_send_error']}")


def render_po_view(active, ing_by_id, user):
    if not active:
        st.info("Không có PO nào đang chờ duyệt hoặc chờ gửi.")
        return
    st.caption("Nháp: Duyệt hoặc Từ chối · Đã duyệt: Gửi PO hoặc Hủy. PO đã gửi xem ở tab Kho › PO & nhận hàng.")
    for po in active:
        render_po_row(po, ing_by_id, user)


def render_done_view(finished, user):
    if not finished:
        st.info("Chưa có cảnh báo nào đã xử lý hoặc đã bỏ qua.")
        return
    superseded = superseded_alert_ids()
    for alert in finished:
        with st.expander(alert_title(alert, superseded)):
            render_alert_detail(alert, user)


def render_tab2(user):
    show_flash("tab2")
    ing_by_id = {i["id"]: i for i in api.get_ingredients()}
    render_run_block(user)
    alerts = api.list_alerts()
    active = [p for p in api.list_pos() if p["closed_reason"] is None and p["status"] in ("DRAFT", "APPROVED")]
    finished = [a for a in alerts if a["status"] in ("RESOLVED", "DISMISSED")]
    n_open = sum(1 for a in alerts if a["status"] == "OPEN")
    po_label = "📝 PO cần xử lý" if is_manager(user) else "📝 PO chờ quản lý"
    view = sub_nav("tab2_view", {
        "alerts": f"🔔 Cảnh báo đang mở ({n_open})",
        "po": f"{po_label} ({len(active)})",
        "done": f"✅ Đã xử lý / bỏ qua ({len(finished)})",
    }, "alerts")
    if view == "alerts":
        render_alerts_view(alerts, user)
    elif view == "po":
        render_po_view(active, ing_by_id, user)
    else:
        render_done_view(finished, user)


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
        return f"Chặn khi {action} · {d.get('message', '')}"
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
                         + (" · Chạy lại" if r["is_rerun"] else ""),
            "Trạng thái": RUN_STATUS_LABELS.get(r["status"], r["status"]), "Bắt đầu": fmt_dt(r["started_at"]),
            "Kết thúc": fmt_dt(r["finished_at"]), "Số cảnh báo": str(r["alert_count"]),
            "Lỗi": r["error"] or "",
        } for r in runs]
        show_table(styled_table(pd.DataFrame(run_rows), ("Trạng thái", "❌", RED_STYLE)))
    else:
        st.info("Chưa có phiên rà soát nào.")

    st.markdown("**Nhật ký sự kiện**")
    c1, c2, c3 = st.columns([3, 1, 1])
    event = c1.selectbox(
        "Lọc theo loại sự kiện", ["__ALL__"] + list(api.list_event_types()), key="log_filter",
        format_func=lambda e: "Tất cả" if e == "__ALL__" else EVENT_LABELS.get(e, e))
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
