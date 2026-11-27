# Quy trình đơn đặt hàng (PO)

## Các trạng thái của PO
PO chỉ có 3 trạng thái: nháp (DRAFT), đã duyệt (APPROVED) và đã gửi (SENT). Từ chối hoặc hủy không phải trạng thái mới mà là đóng PO, ghi lý do đóng là REJECTED hoặc CANCELLED. PO đã đóng không chuyển bước được nữa. Sau khi gửi, PO có thêm trạng thái giao hàng: chờ giao, giao trễ hoặc đã nhận.

## Tạo PO nháp
PO chỉ được tạo từ cảnh báo thiếu hàng của phiên rà soát. Quản lý chọn phương án A hoặc B, có thể sửa số lượng; số lượng được làm tròn lên theo số lượng tối thiểu (MOQ) của nhà cung cấp. Mỗi cảnh báo chỉ có tối đa 1 PO đang mở. Nguyên liệu đã có PO nháp hoặc đã duyệt thì không tạo thêm PO mới.

## Duyệt hoặc từ chối
Chỉ quản lý được duyệt hoặc từ chối PO nháp, ở Tab 2 "Rà soát". Hệ thống ghi lại người duyệt và thời điểm duyệt. Từ chối thì PO đóng với lý do REJECTED và cảnh báo được đánh dấu đã xử lý.

## Gửi PO
Chỉ quản lý được gửi PO đã duyệt. Khi gửi, hệ thống xuất file PO dạng CSV vào thư mục outbox và chuyển PO sang đã gửi. Nếu nhà cung cấp chưa có email, gửi thất bại: PO vẫn ở trạng thái đã duyệt, hệ thống ghi lỗi và tăng số lần gửi. Bổ sung email rồi bấm "Gửi lại". PO đã gửi thì không gửi lại lần nữa.

## Hủy PO
PO đã duyệt nhưng chưa gửi có thể hủy, PO đóng với lý do CANCELLED. PO đã gửi thì không hủy trên hệ thống; nếu muốn đổi đơn, quản lý gọi trực tiếp cho nhà cung cấp.

## Ai được làm gì
Người dùng vai trò STAFF chỉ xem PO, không tạo, duyệt, gửi hay hủy. Trợ lý chat chỉ trả lời câu hỏi, không tạo hay sửa PO, kể cả khi trong tài liệu hoặc câu hỏi có lời yêu cầu tạo PO. Muốn đặt hàng phải làm qua Tab 2.

Cập nhật: 28/11/2026
