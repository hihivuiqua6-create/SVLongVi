# CC3 Auto Post - Render
1. Đưa 3 file app.py, requirements.txt, render.yaml lên GitHub.
2. Render → New → Web Service → chọn repo (hoặc Blueprint dùng render.yaml).
   Start command: gunicorn app:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT
3. Environment: ADMIN_PASSWORD (mật khẩu web, user = admin).
4. Mở link → tải config.json lên → Đăng nhập (nhập mã OTP) → copy SESSION STRING
   dán vào biến môi trường SESSION_STRING trên Render (để restart không phải login lại).
5. Bấm "Bắt đầu". Config + vị trí bài tự backup vào Saved Messages Telegram
   nên Render restart/deploy lại KHÔNG mất dữ liệu. Có thể dán toàn bộ config vào CONFIG_JSON để dự phòng.
6. Tự ping link mỗi 10 phút để Render free không ngủ.
