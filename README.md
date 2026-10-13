# CC3 Auto Post - Render

## Render Web Service settings
- Runtime: Python 3
- Root Directory: để trống (nếu các file nằm ở gốc repository)
- Build Command: `python -m pip install --upgrade pip && python -m pip install -r requirements.txt`
- Start Command: `python -m gunicorn app:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`

`python -m gunicorn` giúp gọi Gunicorn qua đúng Python environment đã cài dependencies.

## Environment Variables
- `ADMIN_PASSWORD`: mật khẩu bảo vệ trang quản trị (nên đặt, nếu bỏ trống thì trang không yêu cầu Basic Auth).
- `API_ID`: Telegram API ID (có thể nhập trên giao diện).
- `API_HASH`: Telegram API HASH (có thể nhập trên giao diện).
- `SESSION_STRING`: Telegram session string (tuỳ chọn; có thể lấy sau khi đăng nhập từ giao diện).
- `CONFIG_JSON`: JSON cấu hình đầy đủ (tuỳ chọn, chỉ dùng khi khởi tạo; cấu hình `config.json` đã tồn tại sẽ được ưu tiên).

Mở URL Render để cấu hình API ID, API HASH, số điện thoại, đăng nhập OTP/2FA, ID nhóm/kênh, link bài đăng và lịch chạy. Có thể tải file `config.json`, upload file JSON hoặc dán JSON trực tiếp vào giao diện.

Lưu ý: không chia sẻ API HASH hoặc SESSION STRING; SESSION STRING cho phép truy cập tài khoản Telegram. Dùng tính năng đăng bài theo đúng quyền và quy định của Telegram.
