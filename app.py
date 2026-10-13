import os, json, asyncio, threading, random, time, re, io, collections, urllib.request
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, redirect, Response, jsonify, render_template_string
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, SessionPasswordNeededError

CONFIG_FILE = "config.json"
BACKUP_NAME = "cc3_config_backup.json"
VN = timezone(timedelta(hours=7))
DEFAULT = {"api_id": "", "api_hash": "", "phone": "", "delay_min": 30, "delay_max": 90,
           "selected_chats": [], "auto_login": True, "post_mode": "blast", "seq_delay": 15,
           "schedule_enabled": True, "schedule_interval_minutes": 15, "posts_links": [],
           "next_post_index": 0, "session_string": ""}

app = Flask(__name__)
LOGS = collections.deque(maxlen=300)          # giới hạn RAM
lock = threading.RLock()
cfg = dict(DEFAULT)
state = {"client": None, "me": None, "phone_hash": None, "need_2fa": False,
         "running": False, "stop": False, "thread": None, "next_run": None, "dirty": False, "otp_status": "", "otp_busy": False}

def log(m):
    LOGS.append(f"[{datetime.now(VN).strftime('%d/%m %H:%M:%S')}] {m}")
    print(m, flush=True)

# ---------- async loop riêng ----------
loop = asyncio.new_event_loop()
threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()
def run(coro, timeout=180):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

# ---------- config ----------
def normalize(d):
    out = dict(DEFAULT); out.update({k: v for k, v in d.items() if k in DEFAULT or k == "schedule_times_per_hour"})
    for k, lo, hi in [("delay_min", 0, 3600), ("delay_max", 0, 3600), ("seq_delay", 0, 86400),
                      ("schedule_interval_minutes", 1, 1440), ("next_post_index", 0, 10**6)]:
        try: out[k] = max(lo, min(hi, int(out[k])))
        except Exception: out[k] = DEFAULT[k]
    out["selected_chats"] = [int(x) for x in out.get("selected_chats") or []]
    out["posts_links"] = [str(x).strip() for x in out.get("posts_links") or [] if str(x).strip()]
    return out

def save_local():
    try:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)
    except Exception as e: log(f"Lỗi lưu config: {e}")
    state["dirty"] = True

def load_initial():
    global cfg
    data = None
    if os.path.exists(CONFIG_FILE):
        try: data = json.load(open(CONFIG_FILE, encoding="utf-8"))
        except Exception: pass
    if data is None and os.environ.get("CONFIG_JSON"):
        try: data = json.loads(os.environ["CONFIG_JSON"])
        except Exception as e: log(f"CONFIG_JSON lỗi: {e}")
    cfg = normalize(data or {})
    if os.environ.get("SESSION_STRING"): cfg["session_string"] = cfg["session_string"] or os.environ["SESSION_STRING"]
    if os.environ.get("API_ID"): cfg["api_id"] = os.environ["API_ID"]
    if os.environ.get("API_HASH"): cfg["api_hash"] = os.environ["API_HASH"]

# ---------- backup vào Saved Messages (chống mất dữ liệu khi Render reset) ----------
async def backup_cloud():
    c = state["client"]
    if not c or not state["me"]: return
    data = {k: v for k, v in cfg.items() if k != "session_string"}
    bio = io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode()); bio.name = BACKUP_NAME
    old = [m async for m in c.iter_messages("me", limit=30) if m.file and m.file.name == BACKUP_NAME]
    await c.send_file("me", bio, caption="cc3 backup - đừng xoá")
    if old: await c.delete_messages("me", [m.id for m in old])

async def restore_cloud():
    c = state["client"]
    async for m in c.iter_messages("me", limit=30):
        if m.file and m.file.name == BACKUP_NAME:
            raw = await c.download_media(m, file=bytes)
            d = json.loads(raw.decode())
            return d
    return None

# ---------- telegram ----------
async def connect(attempts=3):
    last_err = None
    for i in range(attempts):
        if state["client"]:
            try: await state["client"].disconnect()
            except Exception: pass
        c = TelegramClient(StringSession(cfg.get("session_string") or ""), int(cfg["api_id"]), cfg["api_hash"],
                           timeout=30, connection_retries=5, retry_delay=3, auto_reconnect=True, flood_sleep_threshold=60)
        try:
            await c.connect(); state["client"] = c
            if await c.is_user_authorized():
                state["me"] = await c.get_me(); return True
            return False
        except (asyncio.TimeoutError, OSError) as e:
            last_err = e
            log(f"Kết nối Telegram lần {i+1}/{attempts} thất bại: {type(e).__name__}, thử lại...")
            await asyncio.sleep(3 * (i + 1))
    raise last_err or TimeoutError("Không kết nối được Telegram")

async def send_code():
    r = await state["client"].send_code_request(cfg["phone"]); state["phone_hash"] = r.phone_code_hash

async def finish_login(code=None, pw=None):
    c = state["client"]
    if pw: await c.sign_in(password=pw)
    else:
        try: await c.sign_in(cfg["phone"], code, phone_code_hash=state["phone_hash"])
        except SessionPasswordNeededError:
            state["need_2fa"] = True; return False
    state["me"] = await c.get_me(); state["need_2fa"] = False
    cfg["session_string"] = c.session.save(); save_local(); return True

def parse_link(link):
    m = re.match(r'https?://t\.me/(?:c/)?([^/]+)/(\d+)', link.strip())
    if not m: raise ValueError(f"Link sai: {link}")
    p = m.group(1)
    return (int(f"-100{p}") if p.isdigit() else p), int(m.group(2))

async def post_one(link):
    c = state["client"]
    src, mid = parse_link(link)
    msg = await c.get_messages(src, ids=mid)
    if not msg: raise ValueError("Không tìm thấy bài")
    ok = bad = 0
    chats = list(cfg["selected_chats"])
    for i, cid in enumerate(chats):
        if state["stop"]: break
        try:
            # gửi lại bài (copy, không tải media về máy -> không tốn RAM/disk)
            await c.send_message(cid, msg)
            ok += 1; log(f"✅ [{i+1}/{len(chats)}] {cid}")
        except FloodWaitError as e:
            bad += 1; log(f"⏳ Flood {e.seconds}s tại {cid}"); await asyncio.sleep(min(e.seconds, 900))
        except Exception as e:
            bad += 1; log(f"❌ {cid}: {str(e)[:120]}")
        if i < len(chats) - 1 and not state["stop"]:
            if cfg["post_mode"] == "sequential": wait = cfg["seq_delay"]
            else: wait = random.randint(1, 3)
            for _ in range(int(wait)):
                if state["stop"]: break
                await asyncio.sleep(1)
    log(f"🏁 Xong bài: ✅{ok} ❌{bad}")

def worker():
    state["running"] = True; state["stop"] = False
    log(f"⏰ Bắt đầu chạy liên tục, mỗi {cfg['schedule_interval_minutes']} phút 1 bài")
    while not state["stop"]:
        try:
            if not state["me"]:
                if not cfg.get("session_string") or not run(connect()):
                    log("⚠️ Chưa đăng nhập, thử lại sau 60s"); time.sleep(60); continue
            with lock:
                links = cfg["posts_links"]
                if not links: log("⚠️ Không có bài"); break
                if cfg["next_post_index"] >= len(links): cfg["next_post_index"] = 0
                idx = cfg["next_post_index"]; link = links[idx]
            log(f"📤 Rải bài #{idx+1}/{len(links)}: {link}")
            try: run(post_one(link), timeout=86400)
            except Exception as e:
                log(f"❌ Lỗi bài: {e}")
                if "disconnected" in str(e).lower() or "authoriz" in str(e).lower(): state["me"] = None
            with lock:
                cfg["next_post_index"] = (idx + 1) % max(1, len(cfg["posts_links"])); save_local()
            try: run(backup_cloud(), 60); state["dirty"] = False
            except Exception as e: log(f"Backup lỗi: {e}")
            if not cfg["schedule_enabled"]: log("Chạy 1 lần xong."); break
            secs = cfg["schedule_interval_minutes"] * 60
            state["next_run"] = (datetime.now(VN) + timedelta(seconds=secs)).strftime("%H:%M:%S")
            for _ in range(secs):
                if state["stop"]: break
                time.sleep(1)
        except Exception as e:
            log(f"💥 Lỗi vòng lặp: {e}"); time.sleep(30)
    state["running"] = False; state["next_run"] = None; log("⏹ Đã dừng")

def start_worker():
    if state["thread"] and state["thread"].is_alive(): return
    state["thread"] = threading.Thread(target=worker, daemon=True); state["thread"].start()

def keep_alive():
    url = os.environ.get("RENDER_EXTERNAL_URL")
    while url:
        time.sleep(600)
        try: urllib.request.urlopen(url + "/ping", timeout=20).read()
        except Exception: pass

def boot():
    load_initial()
    threading.Thread(target=keep_alive, daemon=True).start()
    if cfg.get("api_id") and cfg.get("api_hash") and cfg.get("session_string"):
        try:
            if run(connect(), 60):
                log(f"🔐 Đã đăng nhập: {state['me'].first_name}")
                try:
                    d = run(restore_cloud(), 60)
                    if d:
                        ss = cfg["session_string"]; cfg.update(normalize(d)); cfg["session_string"] = ss
                        save_local(); log(f"♻️ Khôi phục backup, bài tiếp theo #{cfg['next_post_index']+1}")
                except Exception as e: log(f"Restore lỗi: {e}")
                if cfg.get("auto_login") and cfg["posts_links"] and cfg["selected_chats"]: start_worker()
        except Exception as e: log(f"Boot lỗi: {e}")

# ---------- web ----------
def auth(f):
    @wraps(f)
    def w(*a, **k):
        pw = os.environ.get("ADMIN_PASSWORD")
        if pw:
            au = request.authorization
            if not au or au.password != pw:
                return Response("Login", 401, {"WWW-Authenticate": 'Basic realm="cc3"'})
        return f(*a, **k)
    return w

PAGE = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>CC3 Auto Post</title><style>
body{font-family:system-ui;background:#0f1720;color:#e5e7eb;margin:0;padding:16px}.c{max-width:900px;margin:auto}
.b{background:#17212b;border:1px solid #243447;border-radius:10px;padding:14px;margin-bottom:14px}
h1{color:#2aabee;font-size:22px}h3{margin:0 0 10px;color:#2aabee}input,textarea,select{width:100%;box-sizing:border-box;background:#0f1720;color:#e5e7eb;border:1px solid #2b3d52;border-radius:6px;padding:8px;margin:4px 0 8px}
button{background:#2aabee;color:#fff;border:0;border-radius:6px;padding:9px 16px;margin:3px;cursor:pointer;font-weight:600}.r{background:#e5484d}.g{background:#30a46c}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}pre{background:#000;color:#9fe870;padding:10px;height:340px;overflow:auto;font-size:12px;white-space:pre-wrap}
label{font-size:13px;color:#9ca3af}</style></head><body><div class=c><h1>📢 CC3 Auto Post Telegram</h1>
<div class=b><b>Trạng thái:</b> {{ 'Đã đăng nhập: '+me if me else 'Chưa đăng nhập' }} |
<b>{{ '🟢 Đang chạy' if running else '🔴 Đang dừng' }}</b> {% if next_run %}| Lần tới: {{next_run}}{% endif %}
| Bài tiếp: #{{c.next_post_index+1}}/{{c.posts_links|length}} | Box: {{c.selected_chats|length}}
<form method=post action=/start style=display:inline><button class=g>▶ Bắt đầu</button></form>
<form method=post action=/stop style=display:inline><button class=r>⏹ Dừng</button></form>
<form method=post action=/runnow style=display:inline><button>⚡ Rải bài tiếp ngay</button></form></div>
<div class=b><h3>📁 Nhập file config.json</h3><form method=post action=/upload enctype=multipart/form-data>
<input type=file name=file accept=.json><button>Tải lên</button></form>
<a href=/download style=color:#2aabee>⬇ Tải config hiện tại</a>
<form method=post action=/import-json><label>Dán toàn bộ nội dung config.json tại đây</label><textarea name=config_json rows=8 placeholder='{"api_id":"...","api_hash":"...","phone":"+84..."}'></textarea><button>📥 Nhập JSON cấu hình</button></form>
<p style="font-size:12px;color:#9ca3af">Có thể nhập API ID, API HASH, số điện thoại, SESSION STRING, ID nhóm/kênh, link bài đăng và các cài đặt khác trong cùng một file JSON.</p></div>
<div class=b><h3>🔐 Đăng nhập Telegram</h3>
{% if not me %}<form id=otpform method=post action=/login><button id=otpbtn type=submit>📨 Gửi mã OTP tới {{c.phone}}</button><span id=otpstatus role=status style="display:block;margin-top:8px;color:#9fe870"></span></form>
<form method=post action=/code><input name=code placeholder="Mã OTP"><input name=password placeholder="Mật khẩu 2FA (nếu có)"><button>Xác nhận</button></form>
{% else %}<label>SESSION STRING (dán vào biến môi trường SESSION_STRING trên Render):</label><textarea rows=3 readonly>{{c.session_string}}</textarea>{% endif %}</div>
<form method=post action=/save class=b><h3>⚙️ Cài đặt</h3><div class=row>
<div><label>API ID</label><input name=api_id value="{{c.api_id}}"></div><div><label>API HASH</label><input name=api_hash value="{{c.api_hash}}"></div>
<div><label>Số điện thoại</label><input name=phone value="{{c.phone}}"></div>
<div><label>Chế độ</label><select name=post_mode><option value=blast {{'selected' if c.post_mode=='blast'}}>Blast (rải nhanh)</option><option value=sequential {{'selected' if c.post_mode=='sequential'}}>Tuần tự</option></select></div>
<div><label>Delay giữa box tuần tự (giây)</label><input name=seq_delay value="{{c.seq_delay}}"></div>
<div><label>Mỗi bao nhiêu phút rải 1 bài</label><input name=schedule_interval_minutes value="{{c.schedule_interval_minutes}}"></div>
<div><label>Bài tiếp theo (#)</label><input name=next_post value="{{c.next_post_index+1}}"></div>
<div><label>Lặp liên tục</label><select name=schedule_enabled><option value=1 {{'selected' if c.schedule_enabled}}>Có</option><option value=0 {{'selected' if not c.schedule_enabled}}>Không (1 lần)</option></select></div>
<div><label>Tự chạy khi khởi động</label><select name=auto_login><option value=1 {{'selected' if c.auto_login}}>Có</option><option value=0 {{'selected' if not c.auto_login}}>Không</option></select></div></div>
<label>Link bài đăng (mỗi dòng 1 link)</label><textarea name=posts_links rows=7>{{c.posts_links|join('\\n')}}</textarea>
<label>ID nhóm/kênh nhận bài (mỗi dòng 1 ID)</label><textarea name=selected_chats rows=7>{{c.selected_chats|join('\\n')}}</textarea>
<button>💾 Lưu</button></form>
<div class=b><h3>📜 Nhật ký</h3><pre id=lg>{{logs}}</pre></div></div>
<script>setInterval(()=>fetch('/logs').then(r=>r.text()).then(t=>{let p=document.getElementById('lg');let end=p.scrollTop+p.clientHeight>=p.scrollHeight-20;p.textContent=t;if(end)p.scrollTop=p.scrollHeight}),4000);
document.getElementById('lg').scrollTop=1e9;
const of=document.getElementById('otpform');
if(of){of.addEventListener('submit',async e=>{e.preventDefault();const b=document.getElementById('otpbtn'),st=document.getElementById('otpstatus');b.disabled=true;st.textContent='Đang kết nối Telegram và yêu cầu gửi mã…';try{const r=await fetch('/login',{method:'POST',headers:{'X-Requested-With':'fetch'}});const d=await r.json();st.textContent=d.message||'Đang xử lý…';}catch(err){st.textContent='Không nhận được phản hồi. Kiểm tra Nhật ký (Logs) trên trang.';}finally{setTimeout(()=>{b.disabled=false},2500)}});
setInterval(async()=>{try{const r=await fetch('/otp-status');const d=await r.json();const st=document.getElementById('otpstatus');if(st&&d.message)st.textContent=d.message;const b=document.getElementById('otpbtn');if(b)b.disabled=!!d.busy;}catch(e){}},2000);}
</script></body></html>"""

@app.route("/")
@auth
def index():
    return render_template_string(PAGE, c=cfg, me=(state["me"].first_name if state["me"] else None),
                                  running=state["running"], next_run=state["next_run"], logs="\n".join(LOGS))

@app.route("/ping")
def ping(): return "ok"

@app.route("/logs")
@auth
def logs(): return Response("\n".join(LOGS), mimetype="text/plain")

@app.route("/upload", methods=["POST"])
@auth
def upload():
    global cfg
    try:
        d = json.load(request.files["file"].stream)
        ss = cfg.get("session_string"); new = normalize(d)
        if not new["session_string"]: new["session_string"] = ss
        with lock: cfg = new; save_local()
        log("📁 Đã nạp config mới")
        if state["me"]:
            try: run(backup_cloud(), 60)
            except Exception: pass
    except Exception as e: log(f"Config lỗi: {e}")
    return redirect("/")

@app.route("/import-json", methods=["POST"])
@auth
def import_json():
    global cfg
    try:
        raw = request.form.get("config_json", "").strip()
        d = json.loads(raw)
        if not isinstance(d, dict): raise ValueError("JSON phải là một object")
        ss = cfg.get("session_string")
        new = normalize(d)
        if not new.get("session_string"): new["session_string"] = ss
        with lock:
            cfg = new
            save_local()
        log("📥 Đã nhập JSON cấu hình thành công")
        if state["me"]:
            try: run(backup_cloud(), 60)
            except Exception as e: log(f"Backup sau nhập lỗi: {e}")
    except Exception as e:
        log(f"❌ Không nhập được JSON: {e}")
    return redirect("/")

@app.route("/download")
@auth
def download():
    return Response(json.dumps(cfg, ensure_ascii=False, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=config.json"})

@app.route("/save", methods=["POST"])
@auth
def save():
    f = request.form
    with lock:
        for k in ["api_id", "api_hash", "phone", "post_mode", "seq_delay", "schedule_interval_minutes"]:
            cfg[k] = f.get(k, cfg[k]).strip()
        cfg["schedule_enabled"] = f.get("schedule_enabled") == "1"; cfg["auto_login"] = f.get("auto_login") == "1"
        cfg["posts_links"] = f.get("posts_links", "").splitlines()
        cfg["selected_chats"] = [x.strip() for x in f.get("selected_chats", "").splitlines() if re.match(r"^-?\d+$", x.strip())]
        try: cfg["next_post_index"] = int(f.get("next_post", 1)) - 1
        except Exception: pass
        cfg.update(normalize(cfg)); save_local()
    log("💾 Đã lưu cài đặt")
    if state["me"]:
        try: run(backup_cloud(), 60)
        except Exception: pass
    return redirect("/")

@app.route("/login", methods=["POST"])
@auth
def login():
    # Return immediately; Telegram network calls run in a background thread so
    # a slow send_code_request cannot keep the browser request spinning.
    if state.get("otp_busy"):
        return jsonify(ok=True, busy=True, message=state.get("otp_status") or "Đang gửi yêu cầu OTP…")
    state["otp_busy"] = True
    state["otp_status"] = "Đang kết nối Telegram…"
    def task():
        try:
            state["otp_status"] = "Đang kết nối Telegram (có thể mất 30-60s)…"
            if run(connect(), 240):
                state["otp_status"] = "Tài khoản đã đăng nhập sẵn."
                log("Đã đăng nhập sẵn")
            else:
                state["otp_status"] = "Đang yêu cầu Telegram gửi mã OTP…"
                run(send_code(), 120)
                state["otp_status"] = "Đã yêu cầu gửi OTP. Kiểm tra Telegram (hoặc SMS nếu Telegram cung cấp)."
                log("📨 Đã gửi yêu cầu mã OTP")
        except Exception as e:
            msg = str(e).strip() or type(e).__name__
            if isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "Timeout" in type(e).__name__:
                msg = "Hết thời gian chờ kết nối Telegram. Mạng của server đang chậm — bấm Gửi OTP lại sau 30 giây."
            state["otp_status"] = "Gửi OTP thất bại: " + msg[:220]
            log(f"Login lỗi: {type(e).__name__}: {msg}")
        finally:
            state["otp_busy"] = False
    threading.Thread(target=task, daemon=True).start()
    return jsonify(ok=True, busy=True, message=state["otp_status"])

@app.route("/otp-status")
@auth
def otp_status():
    return jsonify(busy=bool(state.get("otp_busy")), message=state.get("otp_status", ""))

@app.route("/code", methods=["POST"])
@auth
def code():
    try:
        pw = request.form.get("password", "").strip()
        if state["need_2fa"] and pw: okk = run(finish_login(pw=pw))
        else:
            okk = run(finish_login(code=request.form.get("code", "").strip()))
            if not okk and pw: okk = run(finish_login(pw=pw))
        log("🔐 Đăng nhập thành công! Copy SESSION STRING vào Render." if okk else "🔑 Cần mật khẩu 2FA")
    except Exception as e: log(f"Mã lỗi: {e}")
    return redirect("/")

@app.route("/start", methods=["POST"])
@auth
def start():
    start_worker(); return redirect("/")

@app.route("/stop", methods=["POST"])
@auth
def stop():
    state["stop"] = True; log("Đang dừng..."); return redirect("/")

@app.route("/runnow", methods=["POST"])
@auth
def runnow():
    if state["running"]:
        log("Đang chạy rồi, dừng rồi bấm lại hoặc chờ lượt tới")
    else:
        old = cfg["schedule_enabled"]; cfg["schedule_enabled"] = False
        def once():
            worker(); cfg["schedule_enabled"] = old
        state["thread"] = threading.Thread(target=once, daemon=True); state["thread"].start()
    return redirect("/")

threading.Thread(target=boot, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
