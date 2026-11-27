# Câu hỏi mẫu để kiểm tra tìm kiếm tài liệu (không đưa vào chỉ mục)

File này bắt đầu bằng dấu "_" nên services/policy_index.py bỏ qua khi lập chỉ mục.
PA4 dùng 11 câu dưới đây để chọn ngưỡng MIN_SCORE; PA5 chép lại vào evals/dataset_policy.jsonl.

## Câu hỏi CÓ trong tài liệu (8 câu)

| # | Câu hỏi | File đúng |
|---|---------|-----------|
| 1 | Kem tươi bảo quản thế nào? | bao_quan.md |
| 2 | Khi nhận hàng cần kiểm tra những gì? | nhan_hang.md |
| 3 | Lô nguyên liệu sắp hết hạn trong 24 giờ thì xử lý ra sao? | xu_ly_sap_het_han.md |
| 4 | NCC Chính chốt đơn lúc mấy giờ? | ncc_chinh.md |
| 5 | NCC Hỏa tốc giao trễ thì làm gì? | ncc_hoa_toc.md |
| 6 | Đặt trứng ở NCC Ánh Dương phải đặt tối thiểu bao nhiêu quả? | ncc_anh_duong.md |
| 7 | PO đã gửi rồi có hủy được không? | quy_trinh_po.md |
| 8 | Ai được xuất kho và xuất theo nguyên tắc nào? | xuat_kho.md |

## Câu hỏi KHÔNG có trong tài liệu (3 câu)

Tìm kiếm phải trả rỗng; trợ lý trả lời "Hiện chưa có quy định về việc này".

| # | Câu hỏi |
|---|---------|
| 9 | Quy định đồng phục nhân viên là gì? |
| 10 | Tiệm có chương trình tích điểm cho khách quen không? |
| 11 | Nhân viên được nghỉ phép mấy ngày một năm? |

## Bảng đối chiếu số liệu với database (data/init_db.py)

Mọi con số dưới đây được kiểm tự động trong tests/unit/test_policy_index.py.

| Tài liệu | Nội dung | Trong tài liệu | Trong DB |
|----------|----------|----------------|----------|
| bao_quan.md, nhan_hang.md | Hạn dùng chuẩn (ngày) | Kem tươi 7, Sữa tươi 7, Mascarpone 14, Trứng 21, Cream cheese 30, Bơ lạt 60, Bột mì 180, Men nở 180, Socola 180, Đường 365, Bột cacao 365, Vani 365 | ingredients.shelf_life_days: giống hệt |
| ncc_chinh.md | Thời gian giao, email | 24 giờ, order@ncc-chinh.vn | lead_time_hours = 24, contact_email giống |
| ncc_chinh.md | Giá / MOQ | Kem 90.000/2, Bơ 250.000/5, Mascarpone 300.000/1, Cream cheese 260.000/1, Sữa 32.000/5, Trứng 3.500/30, Bột mì 20.000/10, Đường 22.000/10, Men nở 150.000/0,5, Cacao 180.000/1, Socola 220.000/1, Vani 800/100 | suppliers (NCC_CHINH): giống hệt |
| ncc_hoa_toc.md | Thời gian giao, email | 2 giờ, order@hoatoc.vn | lead_time_hours = 2 |
| ncc_hoa_toc.md | Giá / MOQ | Kem 103.500/1, Bơ 287.500/2, Bột mì 23.000/5 | suppliers (NCC_HOA_TOC): giống hệt |
| ncc_hoa_toc.md | Phí giao gấp | Giá cao hơn NCC Chính khoảng 15% | 103.500/90.000 = 287.500/250.000 = 23.000/20.000 = 1,15 |
| ncc_hoa_toc.md | Bột mì chưa có email | Chưa có email đặt bột mì | contact_email NULL ở dòng NCC_HOA_TOC – BOT_MI |
| ncc_anh_duong.md | Thời gian giao, email | 48 giờ, order@anhduong.vn | lead_time_hours = 48 |
| ncc_anh_duong.md | Giá / MOQ | Bột mì 18.000/25, Kem 82.000/5, Trứng 3.200/60, Đường 20.000/25 | suppliers (NCC_ANH_DUONG): giống hệt |
| ncc_*.md | Độ tin cậy | Chính 95%, Hỏa tốc 90%, Ánh Dương 85% | reliability 0,95 / 0,90 / 0,85 |
| xuat_kho.md | Định lượng kem cho bánh kem | 0,2 lít / chiếc | recipes: Bánh kem sinh nhật – KEM_TUOI = 0,2 |

Tài liệu không ghi số tồn kho (tồn thay đổi hằng ngày, xem bằng tool check_inventory_fefo).

Cập nhật: 28/11/2026
