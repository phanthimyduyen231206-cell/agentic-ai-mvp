"""Unit test cho agent/output_check.py (MA1) – hậu kiểm con số trong câu Gemini viết.

Chạy: pytest tests/unit/test_output_check.py
"""
import pytest

from agent.output_check import find_unknown_numbers

KEM = {
    "ingredient_id": "KEM_TUOI", "ingredient_name": "Kem tươi Whipping Cream", "unit": "lít",
    "projected_stock": -2.0, "min_stock": 2.0, "shortage_qty": 4.0,
    "causes": [{"code": "EXPIRING_LOT", "qty": 6.0}, {"code": "PREORDER", "qty": 3.0}],
    "options": [
        {"option": "A", "sup_name": "NCC Chính", "unit_price": 90000, "order_qty": 4.0, "total_cost": 360000,
         "lead_time_hours": 24, "arrival_at": "2026-11-21T08:00:00", "cost_diff_pct_vs_A": 0,
         "reason": "Rẻ nhất, về chậm hơn B 22 giờ"},
        {"option": "B", "sup_name": "NCC Hỏa tốc", "unit_price": 103500, "order_qty": 4.0, "total_cost": 414000,
         "lead_time_hours": 2, "arrival_at": "2026-11-20T10:00:00", "cost_diff_pct_vs_A": 15.0,
         "reason": "Nhanh nhất, đắt hơn A 15.0% (54000 đ)"},
    ],
    "snapshot_hash": "9f3a77e1c02b",
}


@pytest.mark.parametrize("text", [
    "Kem tươi dự kiến còn -2.0 lít, thấp hơn mức tối thiểu 2.0 lít, cần bổ sung 4.0 lít.",
    "Còn âm 2 lít; 6 lít sắp hết hạn; đơn đặt trước cần 3 lít.",
    "A: tổng 360000 đ (360.000 đ, 360,000 đ, 360k), đơn giá 90.000 đ/lít.",
    "B về lúc 2026-11-20T10:00:00, tức 10:00 sáng 20/11, trong 2 giờ; đắt hơn A 15% (54000 đ).",
    "A về 08:00 ngày 21/11/2026 (8h sáng), chậm hơn B 22 giờ.",
    "Lô KEM-L1 và PO-004, đơn DH01 là mã, không phải số liệu.",
    "Không có con số nào trong câu này.",
])
def test_numbers_from_data_are_accepted(text):
    assert find_unknown_numbers(text, KEM) == []


@pytest.mark.parametrize("text, bad", [
    ("Cần bổ sung 5.0 lít.", "5.0"),
    ("Tổng chi phí 380000 đ.", "380000"),
    ("B đắt hơn A 25%.", "25"),
    ("Hàng về 2026-11-22T08:00:00.", "2026-11-22T08:00:00"),
    ("Hàng về lúc 09:30.", "09:30"),
    ("Hàng về ngày 25/11.", "25/11"),
    ("Tổng 500k.", "500k"),
])
def test_made_up_numbers_are_reported(text, bad):
    assert bad in find_unknown_numbers(text, KEM)


def test_hash_digits_are_not_allowed_numbers():
    """Chữ số trong snapshot_hash không được tính là số hợp lệ."""
    assert find_unknown_numbers("Thiếu 77 lít.", KEM) == ["77"]


def test_extra_numbers_and_rounding():
    assert find_unknown_numbers("Trong 3 ngày tới.", KEM) == []  # 3 có trong causes
    assert find_unknown_numbers("Trong 5 ngày tới.", KEM, extra=[5]) == []
    assert find_unknown_numbers("Còn 0.33 kg.", {"qty": 0.3333}) == []  # làm tròn đúng 2 chữ số
    assert find_unknown_numbers("Còn 0.3 kg.", {"qty": 0.3333}) == []
    assert find_unknown_numbers("Còn 0 kg.", {"qty": 0.3333}) == ["0"]  # số nguyên không được làm tròn


def test_tool_results_as_source():
    tool = {"ok": True, "on_hand_qty": 10.0, "batches": [
        {"batch_id": "KEM-L1", "qty": 7.0, "expiry_date": "2026-11-20T20:00:00"},
        {"batch_id": "KEM-L2", "qty": 3.0, "expiry_date": "2026-11-25T20:00:00"}]}
    good = "Còn 10 lít: KEM-L1 còn 7 lít (hết hạn 20:00 ngày 20/11), KEM-L2 còn 3 lít (hết hạn 25/11)."
    assert find_unknown_numbers(good, [tool]) == []
    assert find_unknown_numbers("Còn 12 lít.", [tool]) == ["12"]
    assert find_unknown_numbers("", [tool]) == []


def test_count_of_list_items_is_allowed():
    """MA3 – Gemini thật viết "gồm 1 lô" (tự đếm danh sách batches) → không được coi là số bịa."""
    tool = {"ok": True, "ingredient_id": "BO_LAT", "unit": "kg", "on_hand_qty": 5.0,
            "batches": [{"batch_id": "BO-L1", "qty": 5.0, "expiry_date": "2026-12-20T00:00:00"}]}
    answer = "Bơ lạt còn 5 kg, gồm 1 lô: lô BO-L1 còn 5 kg (hết hạn 2026-12-20)."
    assert find_unknown_numbers(answer, [tool]) == []
    assert find_unknown_numbers("Bơ lạt gồm 3 lô.", [tool]) == ["3"]  # đếm sai vẫn bị bắt
