import os
import json
import time
import math
import hashlib
import base64
import re
import threading
import logging
from datetime import datetime
import requests
import socketio
import telebot
from flask import Flask

# ==========================================
# 👑 CẤU HÌNH HỆ THỐNG BOT & LOGGING (GIỐNG GỐC)
# ==========================================
def log_msg(level, m):
    print(f"[{level}] {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} → {m}")

logger = {
    'debug': lambda m: log_msg('DEBUG', m),
    'info':  lambda m: log_msg('INFO ', m),
    'warn':  lambda m: log_msg('WARN ', m),
    'error': lambda m: log_msg('ERROR', m),
}

# 🔴 GIỮ NGUYÊN GIÁ TRỊ CỦA BẠN
BOT_TOKEN = '8768885823:AAFM2luVnUwS3_hWmHqgQk0GoZGClUsKPAk'
ADMIN_ID = 7833803456
ADMIN_USERNAME = "@cskhvilong1"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')

# ✅ Đặt MENU LỆNH ĐÃ ĐƯỢC NÂNG CẤP & CẬP NHẬT CHỨC NĂNG MỚI
bot.set_my_commands([
    telebot.types.BotCommand("start", "🏠 Mở menu chính hệ thống"),
    telebot.types.BotCommand("huongdan", "📖 Bảng hướng dẫn sử dụng"),
    telebot.types.BotCommand("nhapkey", "🔑 Nhập key kích hoạt bản quyền"),
    telebot.types.BotCommand("thongtin", "💎 Xem thông tin tài khoản & hạn dùng"),
    telebot.types.BotCommand("login", "🔐 Đăng nhập tài khoản game"),
    telebot.types.BotCommand("autobet", "⚡ Bật / tắt tự động đặt cược"),
    telebot.types.BotCommand("x2", "💥 Bật/tắt chế độ gấp thếp (on/off)"),
    telebot.types.BotCommand("tudong", "🎯 Chia vốn tự động (VD: /tudong 40000)"),
    telebot.types.BotCommand("lichsucau", "📊 Xem lịch sử cầu gần nhất"),
    telebot.types.BotCommand("stop", "⏹️ Ngắt kết nối an toàn"),
    telebot.types.BotCommand("taokey", "👑 [ADMIN] Tạo key bản quyền"),
    telebot.types.BotCommand("danhsachkey", "📋 [ADMIN] Xem danh sách key còn lại"),
])

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ CẤU HÌNH API & TRẠNG THÁI HỆ THỐNG                     ║
# ╚══════════════════════════════════════════════════════════════╝
HISTORY_API_URL = "https://wtxmd52.tele68.com/v1/txmd5/lite-sessions"
MAX_HISTORY_STORE = 200
MIN_CONFIDENCE_AUTO_BET = 60
AUTO_BET_RUN_UNTIL_STOP = True

active_sockets = {}
user_states = {}
valid_keys = {}
authorized_users = {}

# ✅ LƯU KEY / NGƯỜI DÙNG RA FILE → KHÔNG MẤT KHI RESTART
SAVE_FILE = './bot_save.json'

def save_data():
    try:
        with open(SAVE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'valid_keys': valid_keys, 'authorized_users': authorized_users}, f, indent=2)
    except Exception as e:
        logger['error'](f"Lỗi lưu file: {e}")

try:
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
            valid_keys = d.get('valid_keys', {})
            authorized_users = {int(k): v for k, v in d.get('authorized_users', {}).items()}
    else:
        logger['info']('Chưa có dữ liệu lưu, tạo mới')
except Exception as e:
    logger['info'](f'Chưa có dữ liệu lưu, tạo mới. Lỗi: {e}')

def init_user_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {
            'history': [],                # Danh sách dict các phiên lịch sử
            'last_session_id': None,
            'prediction': None,           # Kết quả AI Ensemble
            'last_prediction': None,
            'model_perf': {},             # Hiệu suất các sub-models
            'pred_history': [],
            'learning_boost': 1.0,
            'total_correct': 0,
            'total_checked': 0,
            'caustats': {'patterns': {}, 'hits': {}},
            'auto_bet_enabled': False, 
            'bet_amount': 10000, 
            'base_bet': 10000, 
            'x2_enabled': False,          # 💥 Mặc định TẮT tự động x2 (chỉ x2 khi bật /x2 on)
            'current_prediction': None, 
            'waiting_for_result': False,
            'has_bet_this_session': False, 
            'session_id': None,
            'balance': 0, 
            'win_streak': 0, 
            'lose_streak': 0,
            'total_win': 0, 
            'total_lose': 0,
            'profit_loss': 0, 
            'last_detected_pattern': 'Chưa xác định',
            'lastPingAt': 0, 
            'betLock': False
        }

# ╔══════════════════════════════════════════════════════════════╗
# ║  ✅ TẢI LỊCH SỬ TỪ API (GIỐNG GỐC)                            ║
# ╚══════════════════════════════════════════════════════════════╝
function_extract_api = None # Sẽ gán sau khi định nghĩa AI Engine

def fetch_history_from_api(limit=50):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://lc79b.bet",
            "Referer": "https://lc79b.bet/",
            "Accept": "application/json"
        }
        r = requests.get(HISTORY_API_URL, headers=headers, timeout=15)
        lst = r.json().get('list', [])
        if not lst:
            return []
        
        lst = list(reversed(lst))[-limit:]
        parsed_history = []
        for p in lst:
            res_raw = p.get('resultTruyenThong')
            tong = p.get('point') or sum(p.get('dices', [0,0,0]))
            sess = p.get('session') or p.get('id')
            res = 'TÀI' if res_raw == 'TAI' else ('XỈU' if res_raw == 'XIU' else None)
            if res and sess:
                parsed_history.append({
                    'session': int(sess),
                    'result': res,
                    'total': int(tong) if tong else None,
                    'dice': p.get('dices', []),
                    'time': p.get('created_at') or None
                })
        return parsed_history
    except Exception as e:
        logger['error']('LỖI TẢI LỊCH SỬ API: ' + str(e))
        return []

# ==========================================
# 🛡️ BẢO MẬT & KIỂM TRA BẢN QUYỀN
# ==========================================
def check_auth(chat_id):
    if chat_id == ADMIN_ID:
        return True
    if chat_id in authorized_users:
        if time.time() <= authorized_users[chat_id]:
            return True
        else:
            del authorized_users[chat_id]
            save_data()
    return False

def locked_msg():
    return f"""<pre>╔════════════════════════════════════════════╗
║    🔒 HỆ THỐNG BẢO MẬT VI LONG VIP 🔒      ║
╠════════════════════════════════════════════╣
║ ⚠️ TÀI KHOẢN CHƯA KÍCH HOẠT BẢN QUYỀN VIP ║
║ ❌ KHÔNG THỂ TRUY CẬP HỆ THỐNG PHÂN TÍCH   ║
╠════════════════════════════════════════════╣
║ 🔑 MỞ KHÓA NGAY → LIÊN HỆ {ADMIN_USERNAME}
║ 💡 CÚ PHÁP KÍCH HOẠT: /nhapkey MÃ_KEY      ║
╚════════════════════════════════════════════╝</pre>"""

def format_expire_time(ts):
    remain = ts - time.time()
    if remain <= 0: return "❌ ĐÃ HẾT HẠN"
    d = math.floor(remain / 86400)
    h = math.floor((remain % 86400) / 3600)
    m = math.floor((remain % 3600) / 60)
    if d > 0: return f"✅ CÒN {d} NGÀY {h} GIỜ {m} PHÚT"
    if h > 0: return f"✅ CÒN {h} GIỜ {m} PHÚT"
    return f"✅ CÒN {m} PHÚT"

# =====================================================================
# 🧠 NÂNG CẤP HOÀN CHỈNH: THUẬT TOÁN AI PREDICTION ENGINE ULTRA PRO MAX (15 ENSEMBLE MODELS)
# XÓA 100% THUẬT TOÁN CŨ (MOULD LOCAL & THUẬT TOÁN CŨ KHÁC)
# =====================================================================

def clamp(v, lo, hi):
    return Math.max(lo, Math.min(hi, v)) if 'Math' in globals() else max(lo, min(hi, v))

def safeDiv(a, b, def_val=0.5):
    return def_val if b == 0 else a / b

def round4(x):
    return round(x, 4)

def normalizeResult(value):
    if value is None: return None
    t = str(value).strip().lower()
    if 'tài' in t or 'tai' in t or t == 't': return 'TÀI'
    if 'xỉu' in t or 'xiu' in t or t == 'x': return 'XỈU'
    return None

def toSessionNumber(value):
    try:
        n = int(value)
        return n
    except:
        return None

def extractApiData(payload):
    if not payload or not isinstance(payload, dict): return None
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    session = toSessionNumber(data.get('phien') or data.get('session') or data.get('id'))
    result = normalizeResult(data.get('ket_qua') or data.get('resultTruyenThong') or data.get('result'))
    if session is None and result is None: return None
    
    dices = data.get('dice') or data.get('dices') or [data.get('xuc_xac_1'), data.get('xuc_xac_2'), data.get('xuc_xac_3')]
    clean_dices = []
    if isinstance(dices, list):
        for d in dices:
            try:
                if d is not None: clean_dices.append(int(d))
            except: pass
            
    return {
        'session': session,
        'result': result,
        'total': int(data.get('tong') or data.get('point') or sum(clean_dices)) if (data.get('tong') or data.get('point') or clean_dices) else None,
        'dice': clean_dices,
        'time': data.get('thoi_gian') or data.get('created_at') or None,
        'id': data.get('id') or None
    }

function_extract_api = extractApiData

def getResults(history, n):
    return [h['result'] for h in history[:min(n, len(history))] if h.get('result')]

def countTai(results):
    return results.count('TÀI')

def currentStreak(results):
    if not results: return {'type': None, 'len': 0}
    t = results[0]
    length = 1
    for i in range(1, len(results)):
        if results[i] == t: length += 1
        else: break
    return {'type': t, 'len': length}

def transitionCounts(results):
    TT = TX = XT = XX = 0
    for i in range(len(results) - 1):
        older = results[i + 1]
        newer = results[i]
        if older == 'TÀI' and newer == 'TÀI': TT += 1
        elif older == 'TÀI' and newer == 'XỈU': TX += 1
        elif older == 'XỈU' and newer == 'TÀI': XT += 1
        elif older == 'XỈU' and newer == 'XỈU': XX += 1
    return {'TT': TT, 'TX': TX, 'XT': XT, 'XX': XX}

def shannonEntropy(results):
    if len(results) < 2: return 1.0
    p = countTai(results) / len(results)
    if p <= 0 or p >= 1: return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))

def bayesianTaiProb(results, alpha=1, beta=1):
    a = 1 if alpha is None else alpha
    b = 1 if beta is None else beta
    t = countTai(results)
    n = len(results)
    return (t + a) / (n + a + b)

def analyzeCau(history):
    res = getResults(history, 30)
    if len(res) < 3:
        return {'type': 'UNKNOWN', 'strength': 0, 'pTai': 0.5, 'label': 'Chưa đủ cầu'}

    st = currentStreak(res)
    trans = transitionCounts(res)
    switches = trans['TX'] + trans['XT']
    ratio = countTai(res) / len(res)
    ent = shannonEntropy(res)

    # 1. CẦU BỆT
    if st['len'] >= 3:
        cont = total = 0
        for i in range(len(res) - st['len']):
            ok = True
            for k in range(st['len']):
                if res[i + k] != st['type']: ok = False; break
            if ok and (i + st['len'] < len(res)):
                total += 1
                if res[i + st['len']] == st['type']: cont += 1
        pCont = cont / total if total >= 2 else (0.4 if st['len'] >= 5 else 0.52)
        pCont = max(0.22, min(0.78, pCont))
        pTai = pCont if st['type'] == 'TÀI' else (1 - pCont)
        return {
            'type': 'CAU_BET_T' if st['type'] == 'TÀI' else 'CAU_BET_X',
            'strength': max(0.3, min(1.0, st['len'] / 8.0)),
            'pTai': pTai,
            'label': f"Cầu bệt {st['type']} x{st['len']}",
            'streak': st
        }

    # 2. CẦU 1-1
    if switches >= max(2, len(res) - 2) * 0.85 and len(res) >= 4:
        nextIsOpposite = 0 if res[0] == 'TÀI' else 1
        pTai = nextIsOpposite
        soft = 0.5 + (pTai - 0.5) * 0.7
        return {'type': 'CAU_1_1', 'strength': 0.65, 'pTai': soft, 'label': 'Cầu 1-1 (xen kẽ)'}

    # 3. CẦU 2-2
    if len(res) >= 6:
        pairs = []
        for i in range(0, min(6, len(res) - 1), 2):
            if res[i] == res[i + 1]: pairs.append(res[i])
        if len(pairs) >= 2 and pairs[0] != pairs[1]:
            lastPairType = res[0] if res[0] == res[1] else None
            if lastPairType:
                pTai = (0.62 if st['len'] == 1 else 0.38) if lastPairType == 'TÀI' else (0.38 if st['len'] == 1 else 0.62)
                return {'type': 'CAU_2_2', 'strength': 0.55, 'pTai': pTai, 'label': 'Cầu 2-2'}

    # 4. CẦU NGHIÊNG TÀI
    if ratio >= 0.68:
        return {'type': 'CAU_NGHIENG_T', 'strength': max(0.3, min(0.9, (ratio - 0.5) * 2)), 'pTai': max(0.55, min(0.78, 0.5 + (ratio - 0.5) * 0.8)), 'label': 'Cầu nghiêng Tài'}

    # 5. CẦU NGHIÊNG XỈU
    if ratio <= 0.32:
        return {'type': 'CAU_NGHIENG_X', 'strength': max(0.3, min(0.9, (0.5 - ratio) * 2)), 'pTai': max(0.22, min(0.45, 0.5 - (0.5 - ratio) * 0.8)), 'label': 'Cầu nghiêng Xỉu'}

    # 6. CẦU HỖN HỢP
    if ent > 0.95:
        return {'type': 'CAU_HON_HOP', 'strength': 0.2, 'pTai': 0.5, 'label': 'Cầu hỗn hợp'}

    # 7. CẦU THƯỜNG
    return {'type': 'CAU_THUONG', 'strength': 0.35, 'pTai': max(0.35, min(0.65, 0.5 + (ratio - 0.5) * 0.6)), 'label': 'Cầu thường'}

def analyzePreviousResults(history):
    res = getResults(history, 20)
    if not res: return {'pTai': 0.5, 'conf': 0.2, 'signals': []}

    signals = []
    score = 0.0
    weightSum = 0.0

    for w in [3, 5, 8, 12, 20]:
        if len(res) >= min(w, 3):
            slice_res = res[:min(w, len(res))]
            p = bayesianTaiProb(slice_res, 1, 1)
            wgt = math.sqrt(len(slice_res)) * (1.3 if w <= 5 else 1.0)
            score += (p - 0.5) * wgt
            weightSum += wgt
            signals.append({'name': f'freq_{w}', 'pTai': p})

    if len(res) >= 3:
        last3 = "".join(res[:3])
        t = n = 0
        for i in range(len(res) - 3):
            if "".join(res[i + 1:i + 4]) == last3:
                n += 1
                if res[i] == 'TÀI': t += 1
        if n >= 2:
            p = (t + 1) / (n + 2)
            score += (p - 0.5) * (2 + n * 0.3)
            weightSum += 2 + n * 0.3
            signals.append({'name': 'last3_match', 'pTai': p, 'n': n})

    withTotal = [h for h in history if h.get('total') is not None][:15]
    if len(withTotal) >= 5:
        recentAvg = sum(h['total'] for h in withTotal[:5]) / min(5, len(withTotal))
        totalBias = (recentAvg - 10.5) / 10.0
        score += totalBias * 0.8
        weightSum += 0.8
        signals.append({'name': 'dice_total', 'recentAvg': round4(recentAvg)})

    pTai = max(0.18, min(0.82, 0.5 + score / weightSum)) if weightSum > 0 else 0.5
    conf = max(0.25, min(0.85, 0.3 + min(len(res) / 20.0, 1.0) * 0.4 + abs(pTai - 0.5)))
    return {'pTai': pTai, 'conf': conf, 'signals': signals, 'sample': len(res)}

def detectRegime(history):
    res = getResults(history, 20)
    if len(res) < 5: return 'COLD_START'
    st = currentStreak(res)
    ent = shannonEntropy(res)
    trans = transitionCounts(res)
    switches = trans['TX'] + trans['XT']
    ratio = countTai(res) / len(res)

    if st['len'] >= 5: return 'STREAK_T' if st['type'] == 'TÀI' else 'STREAK_X'
    if switches >= len(res) * 0.7: return 'ALTERNATING'
    if ent < 0.6 and ratio > 0.65: return 'STABLE_T'
    if ent < 0.6 and ratio < 0.35: return 'STABLE_X'
    if ent > 0.95: return 'HIGH_ENTROPY'
    if abs(ratio - 0.5) < 0.12: return 'BALANCED'
    return 'TRENDING'

# ----- 15 ENSEMBLE SUB-MODELS -----
def modelFrequency(history, window):
    res = getResults(history, window)
    if not res: return None
    pTai = bayesianTaiProb(res, 1, 1)
    shrink = min(1.0, len(res) / 8.0)
    adj = 0.5 + (pTai - 0.5) * shrink
    return {'id': f'freq_{window}', 'pTai': adj, 'sample': len(res), 'conf': max(0.35, min(0.82, 0.35 + len(res) * 0.03))}

def modelExpFreq(history):
    res = getResults(history, 30)
    if not res: return None
    wTai = wTotal = 0.0
    w = 1.0
    for r in res:
        if r == 'TÀI': wTai += w
        wTotal += w
        w *= 0.88
    return {'id': 'exp_freq', 'pTai': safeDiv(wTai, wTotal), 'sample': len(res), 'conf': max(0.4, min(0.85, 0.4 + len(res) * 0.025))}

def modelStreak(history):
    res = getResults(history, 40)
    if len(res) < 2: return None
    st = currentStreak(res)
    cont = total = 0
    for i in range(len(res) - st['len']):
        same = True
        for k in range(st['len']):
            if res[i + k] != st['type']: same = False; break
        if same and (i + st['len'] < len(res)):
            total += 1
            if res[i + st['len']] == st['type']: cont += 1
    pContinue = cont / total if total > 0 else (0.42 if st['len'] >= 4 else 0.55)
    pContinue = max(0.25, min(0.75, pContinue))
    pTai = pContinue if st['type'] == 'TÀI' else (1.0 - pContinue)
    return {'id': 'streak', 'pTai': pTai, 'sample': total or len(res), 'conf': max(0.4, min(0.8, 0.4 + min(st['len'], 6) * 0.05))}

def modelMarkov1(history):
    res = getResults(history, 60)
    if len(res) < 3: return None
    c = transitionCounts(res)
    fromT = c['TT'] + c['TX']
    fromX = c['XT'] + c['XX']
    last = res[0]
    pTai = safeDiv(c['TT'], fromT) if last == 'TÀI' else safeDiv(c['XT'], fromX)
    s = fromT if last == 'TÀI' else fromX
    return {'id': 'markov1', 'pTai': max(0.15, min(0.85, pTai)), 'sample': s, 'conf': max(0.45, min(0.88, 0.45 + s * 0.02))}

def modelMarkov2(history):
    res = getResults(history, 60)
    if len(res) < 5: return None
    counts = {}
    for i in range(len(res) - 2):
        key = f"{res[i + 2]}|{res[i + 1]}"
        if key not in counts: counts[key] = {'T': 0, 'X': 0}
        if res[i] == 'TÀI': counts[key]['T'] += 1
        else: counts[key]['X'] += 1
    key = f"{res[1]}|{res[0]}"
    c = counts.get(key, {'T': 1, 'X': 1})
    return {'id': 'markov2', 'pTai': safeDiv(c['T'] + 1, c['T'] + c['X'] + 2), 'sample': c['T'] + c['X'], 'conf': max(0.4, min(0.85, 0.4 + (c['T'] + c['X']) * 0.03))}

def modelNgram(history):
    res = getResults(history, 50)
    if len(res) < 4: return None
    last2 = res[1] + res[0]
    last3 = (res[2] if len(res) > 2 else '') + res[1] + res[0]
    t2 = n2 = t3 = n3 = 0
    for i in range(len(res) - 2):
        if res[i + 2] + res[i + 1] == last2:
            n2 += 1
            if res[i] == 'TÀI': t2 += 1
    for i in range(len(res) - 3):
        if res[i + 3] + res[i + 2] + res[i + 1] == last3:
            n3 += 1
            if res[i] == 'TÀI': t3 += 1
    if n3 >= 2: pTai = (t3 + 1) / (n3 + 2); sample = n3
    elif n2 >= 2: pTai = (t2 + 1) / (n2 + 2); sample = n2
    else: return None
    return {'id': 'ngram', 'pTai': pTai, 'sample': sample, 'conf': max(0.38, min(0.8, 0.38 + sample * 0.04))}

def modelMomentum(history):
    res = getResults(history, 15)
    if len(res) < 3: return None
    score = 0.0; sumW = 0.0; w = 1.0
    for r in res:
        score += (1 if r == 'TÀI' else -1) * w
        sumW += w
        w *= 0.75
    pTai = max(0.22, min(0.78, 0.5 + (score / sumW) * 0.28))
    return {'id': 'momentum', 'pTai': pTai, 'sample': len(res), 'conf': 0.55}

def modelPattern10(history):
    res = getResults(history, 10)
    if len(res) < 4: return None
    tCount = countTai(res)
    ratio = tCount / len(res)
    st = currentStreak(res)
    ent = shannonEntropy(res)
    trans = transitionCounts(res)
    switches = trans['TX'] + trans['XT']
    bias = (ratio - 0.5) * 0.9
    if st['len'] >= 4: bias += (-0.12 if st['type'] == 'TÀI' else 0.12)
    if switches >= len(res) - 2: bias *= 0.6
    damp = 1.0 - min(ent, 1.0) * 0.35
    pTai = max(0.2, min(0.8, 0.5 + bias * damp))
    return {
        'id': 'pattern10', 'pTai': pTai, 'sample': len(res),
        'conf': max(0.35, min(0.78, 0.5 + (10 - abs(tCount - 5)) * 0.03 - ent * 0.15)),
        'meta': {'tCount': tCount, 'ratio': round4(ratio), 'streak': st, 'entropy': round4(ent), 'switches': switches}
    }

def modelCau(history):
    cau = analyzeCau(history)
    return {'id': 'cau_engine', 'pTai': cau['pTai'], 'sample': len(getResults(history, 30)), 'conf': max(0.35, min(0.85, 0.4 + cau['strength'] * 0.4)), 'meta': cau}

def modelPrevAnalysis(history):
    a = analyzePreviousResults(history)
    return {'id': 'prev_analysis', 'pTai': a['pTai'], 'sample': a.get('sample', 0), 'conf': a['conf'], 'meta': a}

def modelBayesianGlobal(history):
    res = getResults(history, 100)
    if not res: return None
    return {'id': 'bayes_global', 'pTai': bayesianTaiProb(res, 2, 2), 'sample': len(res), 'conf': max(0.4, min(0.75, 0.4 + math.log2(1 + len(res)) * 0.08))}

def modelSimilarity(history):
    res = getResults(history, 80)
    if len(res) < 12: return None
    patternLen = min(5, math.floor(len(res) / 4.0))
    target = "".join(res[:patternLen])
    tNext = nNext = 0
    for i in range(patternLen, len(res) - 1):
        if "".join(res[i:i + patternLen]) == target:
            nNext += 1
            if res[i - 1] == 'TÀI': tNext += 1
    if nNext < 2: return None
    return {'id': 'similarity', 'pTai': (tNext + 1) / (nNext + 2), 'sample': nNext, 'conf': max(0.42, min(0.82, 0.42 + nNext * 0.05))}

def getModelWeight(chat_id, modelId, baseConf, sample):
    st = user_states.get(chat_id, {})
    perf = st.get('model_perf', {}).get(modelId)
    recentAcc = 0.5
    if perf and perf.get('recent') and len(perf['recent']) >= 3:
        recentAcc = perf['recent'].count(True) / len(perf['recent'])
    
    streamBonus = 1.0
    if perf and perf.get('recent'):
        s = 0
        for item in reversed(perf['recent']):
            if item: s += 1
            else: break
        streamBonus = 1.0 + min(s, 8) * 0.05

    longAcc = 0.5
    if perf and perf.get('total', 0) >= 5: longAcc = perf['hits'] / perf['total']

    boost = st.get('learning_boost', 1.0)
    sampleFactor = max(0.35, min(1.3, sample / 12.0))
    w = baseConf * (0.35 + recentAcc * 1.15 + longAcc * 0.35) * sampleFactor * streamBonus * boost
    return max(0.03, w)

def runEnsemble(chat_id, history):
    models = []
    n = len(history)

    candidates = [
        lambda: modelFrequency(history, 5),
        lambda: modelFrequency(history, 10),
        lambda: modelFrequency(history, 20) if n >= 15 else None,
        lambda: modelFrequency(history, 50) if n >= 30 else None,
        lambda: modelExpFreq(history),
        lambda: modelStreak(history),
        lambda: modelMarkov1(history),
        lambda: modelMarkov2(history) if n >= 12 else None,
        lambda: modelNgram(history),
        lambda: modelMomentum(history),
        lambda: modelPattern10(history),
        lambda: modelCau(history),
        lambda: modelPrevAnalysis(history),
        lambda: modelBayesianGlobal(history),
        lambda: modelSimilarity(history)
    ]

    for fn in candidates:
        try:
            m = fn()
            if m and isinstance(m.get('pTai'), (int, float)) and not math.isnan(m['pTai']):
                m['pTai'] = max(0.12, min(0.88, m['pTai']))
                models.append(m)
        except: pass

    if not models:
        return {
            'prediction': 'TÀI',
            'tai_probability': 0.5,
            'xiu_probability': 0.5,
            'confidence': 0.2,
            'risk': 'VERY_HIGH',
            'sample_size': n,
            'regime': 'COLD_START',
            'model_count': 0,
            'models_agree': 0,
            'top_models': [],
            'analysis': {'note': 'Prior 50/50'},
            'models': []
        }

    sumW = sumP = 0.0
    weighted = []
    for m in models:
        w = getModelWeight(chat_id, m['id'], m.get('conf', 0.5), m.get('sample', 1))
        sumW += w
        sumP += m['pTai'] * w
        m_copy = dict(m)
        m_copy['weight'] = w
        weighted.append(m_copy)

    pTai = max(0.18, min(0.82, sumP / sumW)) if sumW > 0 else 0.5
    predT = len([m for m in weighted if m['pTai'] >= 0.5])
    agree = max(predT, len(models) - predT)
    agreeRatio = agree / len(models)
    regime = detectRegime(history)
    ent = shannonEntropy(getResults(history, 15))
    cau = analyzeCau(history)

    gap = abs(pTai - 0.5) * 2
    st = user_states.get(chat_id, {})
    learning_boost = st.get('learning_boost', 1.0)

    conf = 0.25 + gap * 0.35 + agreeRatio * 0.25 + min(n / 40.0, 1.0) * 0.15 - ent * 0.12
    conf *= (0.92 + (learning_boost - 1.0) * 0.25)
    if regime in ['HIGH_ENTROPY', 'COLD_START']: conf *= 0.7
    if n < 5: conf *= 0.55
    elif n < 10: conf *= 0.75
    conf = max(0.15, min(0.88, conf))

    risk = 'MEDIUM'
    if conf < 0.35 or n < 4: risk = 'VERY_HIGH'
    elif conf < 0.48: risk = 'HIGH'
    elif conf > 0.68 and gap > 0.22: risk = 'LOW'

    prediction = 'TÀI' if pTai >= 0.5 else 'XỈU'
    top = sorted(weighted, key=lambda x: x['weight'], reverse=True)[:5]
    top_models = [{'id': m['id'], 'pTai': round4(m['pTai']), 'w': round4(m['weight'])} for m in top]
    p10 = modelPattern10(history)

    return {
        'prediction': prediction,
        'tai_probability': round4(pTai),
        'xiu_probability': round4(1 - pTai),
        'confidence': round4(conf),
        'risk': risk,
        'sample_size': n,
        'regime': regime,
        'model_count': len(models),
        'models_agree': agree,
        'top_models': top_models,
        'pattern10': p10['meta'] if p10 else {},
        'cau': cau,
        'analysis': {
            'entropy': round4(ent),
            'streak': currentStreak(getResults(history, 30)),
            'freq10': round4(countTai(getResults(history, 10)) / min(10, n)) if n >= 5 else None,
            'learningBoost': round4(learning_boost),
            'cauLabel': cau['label']
        },
        'models': weighted
    }

def updateModelPerformance(chat_id, actual):
    st = user_states.get(chat_id)
    if not st or not st.get('last_prediction'): return
    models = st['last_prediction'].get('models', [])

    for m in models:
        m_id = m.get('id')
        if not m_id: continue
        if m_id not in st['model_perf']:
            st['model_perf'][m_id] = {'hits': 0, 'total': 0, 'recent': [], 'streak': 0}
        p = st['model_perf'][m_id]
        pred = 'TÀI' if m.get('pTai', 0.5) >= 0.5 else 'XỈU'
        hit = (pred == actual)
        p['total'] += 1
        if hit:
            p['hits'] += 1
            p['streak'] = p.get('streak', 0) + 1
        else:
            p['streak'] = 0
        p['recent'].append(hit)
        if len(p['recent']) > 30: p['recent'].pop(0)

    if st['last_prediction'].get('cauType'):
        key = st['last_prediction']['cauType']
        st['caustats']['patterns'][key] = st['caustats']['patterns'].get(key, 0) + 1
        if st['last_prediction'].get('prediction') == actual:
            st['caustats']['hits'][key] = st['caustats']['hits'].get(key, 0) + 1

    recentPreds = st['pred_history'][:12]
    if len(recentPreds) >= 3:
        recentHits = len([x for x in recentPreds if x.get('correct')])
        rate = recentHits / len(recentPreds)
        winStreak = loseStreak = 0
        for item in recentPreds:
            if item.get('correct'): winStreak += 1
            else: break
        for item in recentPreds:
            if not item.get('correct'): loseStreak += 1
            else: break

        if winStreak >= 3 or rate >= 0.7:
            st['learning_boost'] = max(0.6, min(1.45, st['learning_boost'] + 0.05 + winStreak * 0.01))
        elif loseStreak >= 3 or rate <= 0.35:
            st['learning_boost'] = max(0.6, min(1.45, st['learning_boost'] - 0.07 - loseStreak * 0.015))
        else:
            st['learning_boost'] = max(0.6, min(1.45, st['learning_boost'] * 0.97 + 1.0 * 0.03))

def processApiData(chat_id, apiData):
    st = user_states.get(chat_id)
    if not st or not apiData: return None

    session = apiData.get('session')
    result = apiData.get('result')

    if result and session is not None:
        if st.get('last_prediction') and st['last_prediction'].get('session') == session:
            predicted = st['last_prediction'].get('prediction')
            correct = (predicted == result)
            already = any(h.get('session') == session for h in st['pred_history'])
            if not already:
                st['pred_history'].insert(0, {
                    'session': session,
                    'predicted': predicted,
                    'actual': result,
                    'correct': correct,
                    'conf': st['last_prediction'].get('confidence'),
                    'time': apiData.get('time') or datetime.now().isoformat()
                })
                if len(st['pred_history']) > 40: st['pred_history'].pop()
                st['total_checked'] += 1
                if correct: st['total_correct'] += 1
                updateModelPerformance(chat_id, result)

        exists = any(item.get('session') == session for item in st['history'])
        if not exists:
            st['history'].insert(0, {
                'session': session,
                'result': result,
                'total': apiData.get('total'),
                'dice': apiData.get('dice'),
                'time': apiData.get('time')
            })
            if len(st['history']) > MAX_HISTORY_STORE:
                st['history'].pop()

    if session is not None:
        predictSession = session + 1
        needPredict = (not st.get('prediction')) or (st['prediction'].get('session') != predictSession) or (predictSession != st.get('last_session_id'))

        if needPredict:
            st['last_session_id'] = predictSession
            engineOut = runEnsemble(chat_id, st['history'])
            predValue = engineOut.get('prediction', 'TÀI')

            st['prediction'] = dict({'session': predictSession, 'value': predValue}, **engineOut)
            st['last_prediction'] = {
                'session': predictSession,
                'prediction': predValue,
                'models': engineOut.get('models', []),
                'confidence': engineOut.get('confidence'),
                'tai_prob': engineOut.get('tai_probability'),
                'cauType': engineOut.get('cau', {}).get('type') if engineOut.get('cau') else None
            }

    return st.get('prediction')

# 🚀 TÍNH NĂNG NÂNG CẤP X2 (CHỈ KHI BẬT /x2 on MỚI X2) + THEO DÕI LÃI LỖ
def ai_tu_hoc(chat_id, du_doan, thuc_te):
    st = user_states.get(chat_id)
    if not st: return
    
    bet_was = st['bet_amount']
    # Chuẩn hóa dự đoán
    norm_pred = 'TÀI' if du_doan in ['TAI', 'TÀI'] else 'XỈU'
    norm_actual = 'TÀI' if thuc_te in ['TAI', 'TÀI'] else 'XỈU'

    if norm_pred == norm_actual:
        st['win_streak'] += 1
        st['lose_streak'] = 0
        st['total_win'] += 1
        st['profit_loss'] += int(bet_was * 0.95)
        # ✅ Thắng -> Khôi phục cược gốc
        st['bet_amount'] = st['base_bet']
    else:
        st['lose_streak'] += 1
        st['win_streak'] = 0
        st['total_lose'] += 1
        st['profit_loss'] -= bet_was
        # 💥 CHỈ X2 KHI BẬT /x2 on
        if st.get('x2_enabled', False):
            st['bet_amount'] = int(st['bet_amount'] * 2)
        else:
            st['bet_amount'] = st['base_bet']

# ==========================================
# 🌐 ĐĂNG NHẬP + SOCKET.IO (GIỐNG GỐC)
# ==========================================
def md5_hash(text):
    return hashlib.md5(text.encode()).hexdigest()

def login_and_get_token(u, p):
    try:
        pw = md5_hash(p)
        url = f"https://apifo88daigia.tele68.com/api?c=3&un={requests.utils.quote(u)}&pw={pw}&cp=R&cl=R&pf=web&at="
        r = requests.get(url, timeout=12)
        d = r.json()
        if not d.get('success'):
            return {'_error': 'Lỗi Game: ' + (d.get('message') or 'Sai thông tin')}
            
        sk = d.get('sessionKey', '')
        sk += '=' * ((4 - len(sk) % 4) % 4)
        sd = json.loads(base64.b64decode(sk).decode('utf-8'))
        nickname = sd.get('nickname') or sd.get('nickName')
        
        headers = {
            'authority': 'wlb.tele68.com',
            'content-type': 'application/json',
            'origin': 'https://lc79b.bet',
            'referer': 'https://lc79b.bet/'
        }
        payload = {'nickName': nickname, 'accessToken': d.get('accessToken')}
        r2 = requests.post(
            'https://wlb.tele68.com/v1/lobby/auth/login?cp=R&cl=R&pf=web&at=',
            json=payload, headers=headers, timeout=12
        )
        data2 = r2.json()
        token = data2.get('token')
        if not token:
            return {'_error': 'Lobby không trả token'}
        money = data2.get('remoteLoginResp', {}).get('money', 0)
        return {'token': token, 'nickname': nickname, 'money': money}
    except Exception as e:
        return {'_error': 'Lỗi kết nối: ' + str(e)}

# ⭐⭐⭐ ANTI-SLEEP PING + WATCHDOG ⭐⭐⭐
def start_anti_sleep():
    def pinger():
        while True:
            try:
                requests.get('https://lc79b.bet', timeout=8)
                logger['info']('🌐 PING RENDER OK — giữ kết nối 100%')
            except: pass
            time.sleep(40)
            
    def watchdog():
        while True:
            now = time.time() * 1000
            for cid, sio in list(active_sockets.items()):
                st = user_states.get(cid)
                if st and (now - st['lastPingAt']) > 90000:
                    logger['warn'](f"🐶 WATCHDOG: {cid} đứng hình → ngắt & kết nối lại")
                    try: sio.disconnect()
                    except: pass
            time.sleep(30)
            
    threading.Thread(target=pinger, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

def start_websocket(chat_id, token):
    init_user_state(chat_id)
    if chat_id in active_sockets:
        try: active_sockets[chat_id].disconnect()
        except: pass

    sio = socketio.Client(
        reconnection=True, reconnection_attempts=99999,
        reconnection_delay=3, reconnection_delay_max=5
    )
    active_sockets[chat_id] = sio
    st = user_states[chat_id]

    def ping_socket():
        while chat_id in active_sockets and active_sockets[chat_id] == sio:
            try:
                if sio.connected:
                    sio.emit('ping', {}, namespace='/txmd5')
                    st['lastPingAt'] = time.time() * 1000
            except: pass
            time.sleep(25)
    
    threading.Thread(target=ping_socket, daemon=True).start()

    @sio.on('connect', namespace='/txmd5')
    def on_connect():
        logger['info'](f"[{chat_id}] ✅ SOCKET KẾT NỐI")
        st['lastPingAt'] = time.time() * 1000
        hist_data = fetch_history_from_api(50)
        tb = ''
        if hist_data:
            st['history'] = hist_data
            tb = f"
║ 📥 LỊCH SỬ THU THẬP: <b>{len(hist_data)}</b> PHIÊN ✅"
        else:
            tb = "
║ ⚠️ Thu thập tự động realtime"
            
        msg = f"""<pre>╔════════════════════════════════════════════╗
║     🟢 KẾT NỐI MÁY CHỦ THÀNH CÔNG 🟢       ║
╠════════════════════════════════════════════╣
║ ✅ ĐÃ KẾT NỐI THÀNH CÔNG SERVER MD5{tb}
║ ⚡ AI PREDICTION ENGINE PRO MAX READY     ║
║ 📡 CHẾ ĐỘ ANTI-DISCONNECT ĐÃ KÍCH HOẠT    ║
╚════════════════════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('disconnect', namespace='/txmd5')
    def on_disconnect():
        logger['warn'](f"[{chat_id}] 🔴 NGẮT KẾT NỐI — TỰ KẾT NỐI LẠI")
        msg = """<pre>╔════════════════════════════════════════════╗
║     🔴 MẤT KẾT NỐI MÁY CHỦ MD5            ║
╠════════════════════════════════════════════╣
║ ⚙️ ĐANG TỰ ĐỘNG KHÔI PHÚC KẾT NỐI SAU 3S...║
╚════════════════════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('new-session', namespace='/txmd5')
    def on_new_session(data):
        st['session_id'] = data.get('id', 'N/A')
        st['has_bet_this_session'] = False
        st['betLock'] = False
        n = len(st['history'])
        
        # NÂNG CẤP DÙNG THUẬT TOÁN ENSEMBLE AI MỚI
        pred_res = runEnsemble(chat_id, st['history'])
        pred_val = pred_res.get('prediction', 'TÀI')
        dt = int(pred_res.get('confidence', 0.5) * 100)
        st['prediction'] = pred_res
        st['current_prediction'] = pred_val
        
        icon = '🔵 TÀI' if pred_val == 'TÀI' else '🔴 XỈU'
        p_tai = int(pred_res.get('tai_probability', 0.5) * 100)
        p_xiu = int(pred_res.get('xiu_probability', 0.5) * 100)
        cau_label = pred_res.get('cau', {}).get('label', 'Phân tích tổng hợp')
        x2_status = '💥 BẬT' if st.get('x2_enabled') else '🔴 TẮT'
        
        msg = f"""<pre>╔════════════════════════════════════════════╗
║      💎 VI LONG AI PREDICTION ENGINE 💎     ║
║           ✨ PHIÊN MỚI: #{st['session_id']} ✨        ║
╠════════════════════════════════════════════╣
║ 📊 DỮ LIỆU THU THẬP: {n}/200 PHIÊN
╠════════════════════════════════════════════╣
║ 🤖 DỰ ĐOÁN AI: {icon}
║ 🎯 ĐỘ TIN CẬY: {dt}% (Rủi ro: {pred_res.get('risk', 'MEDIUM')})
║ 🧬 MẪU CẦU: {cau_label}
║ 📊 XÁC SUẤT: TÀI [{p_tai}%] 🆚 [{p_xiu}%] XỈU
║ ⚡ MÔ HÌNH DÙNG: {pred_res.get('model_count', 0)} Ensemble Models
║ 🎲 CHẾ ĐỘ X2: {x2_status}</pre>"""

        if st['auto_bet_enabled']:
            if dt >= MIN_CONFIDENCE_AUTO_BET:
                msg += f"
<pre>║ ⚡ AUTO CƯỢC: {st['bet_amount']:,} WIN (READY)</pre>"
            else:
                msg += f"
<pre>║ ⚠️ ĐỘ TIN CẬY <{MIN_CONFIDENCE_AUTO_BET}% → BỎ QUA</pre>"
            
        msg += "
<pre>╚════════════════════════════════════════════╝</pre>"
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass

    @sio.on('tick-update', namespace='/txmd5')
    def on_tick_update(data):
        gs = data.get('state')
        pred_res = st.get('prediction') or {}
        dt = int(pred_res.get('confidence', 0.5) * 100)
        
        if gs == 'BETTING' and st['auto_bet_enabled'] and st['current_prediction'] and AUTO_BET_RUN_UNTIL_STOP:
            if not st['has_bet_this_session'] and not st['betLock'] and dt >= MIN_CONFIDENCE_AUTO_BET:
                st['betLock'] = True
                bet_type = 'TAI' if st['current_prediction'] in ['TÀI', 'TAI'] else 'XIU'
                pay = {'type': bet_type, 'amount': st['bet_amount']}
                try:
                    sio.emit('bet', pay, namespace='/txmd5')
                    st['has_bet_this_session'] = True
                    st['waiting_for_result'] = True
                    icon = '🔵 TÀI' if bet_type == 'TAI' else '🔴 XỈU'
                    mode_x2 = '💥 GẤP THẾP X2' if (st.get('x2_enabled') and st['bet_amount'] > st['base_bet']) else '🟢 CƯỢC GỐC'
                    msg = f"""<pre>╔════════════════════════════════════════════╗
║        🚀 LỆNH CƯỢC TỰ ĐỘNG ĐÃ GỬI        ║
╠════════════════════════════════════════════╣
║ 🎯 ĐẶT CƯỢC: {icon}
║ 💰 SỐ TIỀN: {st['bet_amount']:,} WIN
║ 🔄 CHẾ ĐỘ: {mode_x2}
║ ⏳ ĐANG CHỜ MÁY CHỦ XÁC NHẬN KẾT QUẢ...    ║
╚════════════════════════════════════════════╝</pre>"""
                    bot.send_message(chat_id, msg, parse_mode='HTML')
                except Exception as e:
                    st['betLock'] = False

    @sio.on('bet-result', namespace='/txmd5')
    def on_bet_result(data):
        if data.get('postBalance') is not None:
            st['balance'] = data['postBalance']
        msg = f"""<pre>╔════════════════════════════════════════════╗
║        ✅ MÁY CHỦ XÁC NHẬN CƯỢC           ║
╠════════════════════════════════════════════╣
║ 💵 SỐ DƯ VÍ CHÍNH: {st['balance']:,} WIN
╚════════════════════════════════════════════╝</pre>"""
        try: bot.send_message(chat_id, msg, parse_mode='HTML')
        except: pass
        try: sio.emit('get-current-my-info', None, namespace='/txmd5')
        except: pass

    @sio.on('session-result', namespace='/txmd5')
    def on_session_result(data):
        st['betLock'] = False
        d = data.get('dices', [0,0,0])
        tong = sum(d)
        kq_raw = data.get('resultTruyenThong', 'N/A')
        kq = 'TÀI' if kq_raw == 'TAI' else ('XỈU' if kq_raw == 'XIU' else 'N/A')
        sess_id = data.get('session') or data.get('id') or st.get('session_id')
        
        if kq in ['TÀI', 'XỈU']:
            api_data_obj = {
                'session': int(sess_id) if sess_id else None,
                'result': kq,
                'total': tong,
                'dice': d,
                'time': datetime.now().isoformat()
            }
            processApiData(chat_id, api_data_obj)
            if st.get('current_prediction'):
                ai_tu_hoc(chat_id, st['current_prediction'], kq)
                
        icon = '🔵 TÀI' if kq == 'TÀI' else ('🔴 XỈU' if kq == 'XỈU' else '⚪ LỖI')
        
        p_l = st.get('profit_loss', 0)
        pl_str = f"+{p_l:,} WIN 🟢" if p_l >= 0 else f"{p_l:,} WIN 🔴"
        
        row = f"""<pre>╔════════════════════════════════════════════╗
║ 🎲 KẾT QUẢ: {d[0]}-{d[1]}-{d[2]} = {tong} → {icon}</pre>"""
        
        if st.get('current_prediction'):
            norm_pred = 'TÀI' if st['current_prediction'] in ['TÀI', 'TAI'] else 'XỈU'
            ok = (norm_pred == kq)
            x2_text = "(X2 PHIÊN SAU)" if (st.get('x2_enabled') and not ok) else ""
            text_kq = f"🟢 ĐÚNG ✅ WIN STREAM: {st['win_streak']}" if ok else f"🔴 SAI ⚠️ LOSE STREAM: {st['lose_streak']} {x2_text}"
            row += f"
<pre>║ 📊 ĐÁNH GIÁ: {text_kq}
║ 📈 TỔNG LÃI/LỖ THU THẬP: {pl_str}</pre>"
            st['waiting_for_result'] = False
            
        recent_res = getResults(st['history'], 14)
        ls = "".join(['🔵' if x == 'TÀI' else '🔴' for x in recent_res])
        row += f"
<pre>║ 📜 LỊCH SỬ CẦU: {ls}</pre>
<pre>╚════════════════════════════════════════════╝</pre>"
        try: bot.send_message(chat_id, row, parse_mode='HTML')
        except: pass

    try:
        sio.connect('https://wtxmd52.tele68.com', socketio_path='txmd5/', namespaces=['/txmd5'], auth={'token': token})
    except Exception as e:
        logger['error']("LỖI CONNECT SOCKET: " + str(e))

# ==========================================
# 🔑 TẤT CẢ LỆNH BOT (NÂNG CẤP HOÀN CHỈNH GIAO DIỆN MỚI & BỔ SUNG LỆNH /x2, /tudong)
# ==========================================
@bot.message_handler(commands=['start'])
def send_start(message):
    cid = message.chat.id
    init_user_state(cid)
    if check_auth(cid):
        han = '👑 VĨNH VIỄN - ADMIN' if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
        msg = f"""<pre>╔════════════════════════════════════════════╗
║      💎 CHÀO MỪNG TỚI VIP SYSTEM 💎        ║
║          ✨ VI LONG ELITE ULTRA PRO ✨     ║
╠════════════════════════════════════════════╣
║ ✅ TRẠNG THÁI TÀI KHOẢN: ĐÃ KÍCH HOẠT VIP ║
║ ⏳ HẠN BẢN QUYỀN: {han}
╠════════════════════════════════════════════╣
║ 📖 /huongdan  | 🔐 /login                  ║
║ ⚡ /autobet   | 💥 /x2 on/off              ║
║ 🎯 /tudong    | 📊 /lichsucau              ║
║ 💎 /thongtin  | ⏹️ /stop                   ║
╚════════════════════════════════════════════╝</pre>"""
    else:
        msg = f"""<pre>╔════════════════════════════════════════════╗
║    🏠 MẢNG HỆ THỐNG VIP VI LONG ELITE      ║
╠════════════════════════════════════════════╣
║ 🔒 TRẠNG THÁI: YÊU CẦU KEY BẢN QUYỀN VIP   ║
║ 🔑 CÚ PHÁP: /nhapkey MÃ_KEY_CỦA_BẠN        ║
║ 📩 ĐẶT MUA KEY TẠI ADMIN: {ADMIN_USERNAME}  ║
╚════════════════════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['huongdan'])
def send_huongdan(message):
    msg = f"""<pre>╔════════════════════════════════════════════╗
║ 📖 BẢNG HƯỚNG DẪN VIP | ✨ VI LONG ELITE   ║
╠════════════════════════════════════════════╣
║ 🔑 /nhapkey KEY_CỦA_BẠN                    ║
║ 🔐 /login TAIKHOAN MATKHAU                 ║
║ ⚡ /autobet on 10000 | off                 ║
║ 💥 /x2 on | off (Chỉ x2 khi bật on)        ║
║ 🎯 /tudong SỐ_VỐN (VD: /tudong 40000)       ║
║ 📊 /lichsucau | 💎 /thongtin               ║
║ ⏹️ /stop | 👑 /taokey 30                   ║
╠════════════════════════════════════════════╣
║ 🚀 AI ENSEMBLE ENGINE - 15 MÔ HÌNH HỌC MÁY ║
║ 🧠 AUTO-LEARN SIÊU CHUẨN REALTIME          ║
║ 📈 CHIA VỐN TỰ ĐỘNG & QUẢN LÝ LÃI LỖ       ║
║ 📩 HỖ TRỢ KỸ THUẬT: {ADMIN_USERNAME}       ║
╚════════════════════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

# 💥 NÂNG CẤP MỚI: CHỨC NĂNG BẬT/TẮT X2 CÓ ĐIỀU KIỆN
@bot.message_handler(commands=['x2'])
def send_x2_toggle(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    init_user_state(cid)
    st = user_states[cid]
    parts = message.text.split()
    if len(parts) < 2:
        status = "💥 BẬT" if st.get('x2_enabled') else "🔴 TẮT"
        return bot.reply_to(message, f"✅ Trạng thái Gấp thếp X2 hiện tại: <b>{status}</b>
👉 Dùng <code>/x2 on</code> để bật hoặc <code>/x2 off</code> để tắt.")
    
    cmd = parts[1].lower()
    if cmd == 'on':
        st['x2_enabled'] = True
        bot.reply_to(message, "💥 <b>ĐÃ BẬT CHẾ ĐỘ GẤP THẾP X2!</b>
Khi cược thua, hệ thống sẽ tự động nhân đôi tiền cược ở phiên tiếp theo.")
    elif cmd == 'off':
        st['x2_enabled'] = False
        st['bet_amount'] = st['base_bet']
        bot.reply_to(message, "🔴 <b>ĐÃ TẮT CHẾ ĐỘ GẤP THẾP X2!</b>
Hệ thống sẽ giữ nguyên tiền cược cố định ngay cả khi thua.")
    else:
        bot.reply_to(message, "✅ Cú pháp: <code>/x2 on</code> hoặc <code>/x2 off</code>")

# 🎯 NÂNG CẤP MỚI: CHỨC NĂNG CHIA VỐN TỰ ĐỘNG
@bot.message_handler(commands=['tudong'])
def send_tudong_chiavon(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    init_user_state(cid)
    st = user_states[cid]
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return bot.reply_to(message, "✅ Cú pháp chia vốn tự động: <code>/tudong [SỐ_VỐN]</code>
Ví dụ: <code>/tudong 40000</code>")
    
    capital = int(parts[1])
    if capital < 1000:
        return bot.reply_to(message, "⚠️ Số vốn tự động tối thiểu là 1,000 WIN!")
    
    # Chiến lược chia vốn: Nếu bật X2 thì cược cơ bản = Vốn / 15 để chịu được chuỗi 4 phiên gấp thếp, ngược lại = Vốn / 10
    divisor = 15 if st.get('x2_enabled') else 10
    calculated_bet = max(1000, math.floor(capital / divisor / 1000) * 1000)
    
    st['base_bet'] = calculated_bet
    st['bet_amount'] = calculated_bet
    
    msg = f"""<pre>╔════════════════════════════════════════════╗
║     🎯 TỰ ĐỘNG CHIA VỐN THÔNG MINH         ║
╠════════════════════════════════════════════╣
║ 💰 TỔNG VỐN KHỞI ĐIỂM: {capital:,} WIN
║ 📊 MỨC CƯỢC CƠ BẢN TÍNH TOÁN: {calculated_bet:,} WIN
║ 🎲 CHẾ ĐỘ X2 GẤP THẾP: {"💥 BẬT" if st.get("x2_enabled") else "🔴 TẮT"}
║ 💡 LỜI KHUYÊN: Hệ thống đã tối ưu mức cược
║    giúp bạn an toàn vốn và quản lý rủi ro.
╚════════════════════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['taokey'])
def send_taokey(message):
    if message.chat.id != ADMIN_ID:
        return bot.reply_to(message, '⛔ Chỉ admin mới có quyền tạo key')
    
    parts = message.text.split()
    n = 30
    if len(parts) > 1 and parts[1].isdigit():
        n = int(parts[1])
        
    if n <= 0:
        return bot.reply_to(message, '✅ Hướng dẫn: /taokey 7 / 30 / 90')
        
    import random, string
    key = 'VIP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    valid_keys[key] = n
    save_data()
    
    het = datetime.fromtimestamp(time.time() + n * 86400).strftime('%d/%m/%Y %H:%M:%S')
    msg = f"✅ TẠO KEY VIP THÀNH CÔNG:
🔑 <code>{key}</code>
⏳ Thời hạn: {n} NGÀY
📅 Hết hạn: {het}
📊 TỔNG KEY CHƯA DÙNG: {len(valid_keys)}"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['danhsachkey'])
def send_danhsachkey(message):
    if message.chat.id != ADMIN_ID:
        return bot.reply_to(message, '⛔ Chỉ admin')
    if not valid_keys:
        return bot.reply_to(message, '📭 Danh sách key kho trống')
        
    lines = [f"<code>{k}</code> → {v} NGÀY" for k, v in valid_keys.items()]
    msg = "
".join(lines) + f"

📊 TỔNG CỘNG KHỎ: {len(valid_keys)} KEY VIP"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['nhapkey'])
def send_nhapkey(message):
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ Hướng dẫn: /nhapkey VIP-XXXX')
        
    k = parts[1].strip().upper()
    if k in valid_keys:
        d = valid_keys[k]
        authorized_users[message.chat.id] = time.time() + d * 86400
        del valid_keys[k]
        save_data()
        bot.reply_to(message, f"🎉 KÍCH HOẠT THÀNH CÔNG GÓI VIP {d} NGÀY ✅")
    else:
        bot.reply_to(message, f"❌ KEY KHÔNG HỢP LỆ HOẶC ĐÃ ĐƯỢC SỬ DỤNG
📩 MUA TẠI: {ADMIN_USERNAME}")

@bot.message_handler(commands=['thongtin'])
def send_thongtin(message):
    cid = message.chat.id
    init_user_state(cid)
    if not check_auth(cid):
        return bot.reply_to(message, '🔒 TÀI KHOẢN CHƯA KÍCH HOẠT VIP')
        
    st = user_states[cid]
    han = '👑 VĨNH VIỄN - ADMIN' if cid == ADMIN_ID else format_expire_time(authorized_users[cid])
    auto_status = '🟢 ĐANG BẬT AUTO' if st['auto_bet_enabled'] else '🔴 ĐÃ TẮT AUTO'
    x2_status = '💥 BẬT' if st.get('x2_enabled') else '🔴 TẮT'
    
    p_l = st.get('profit_loss', 0)
    pl_str = f"+{p_l:,} WIN 🟢" if p_l >= 0 else f"{p_l:,} WIN 🔴"
    
    msg = f"""<pre>╔════════════════════════════════════════════╗
║      💎 BẢNG THỐNG KÊ VIP ACCOUNT 💎       ║
╠════════════════════════════════════════════╣
║ 🆔 ID TELEGRAM: <code>{cid}</code>
║ ⏳ HẠN DÙNG: {han}
║ ⚡ AUTO CƯỢC: {auto_status}
║ 🎲 CHẾ ĐỘ X2 GẤP THẾP: {x2_status}
║ 💰 VỐN CƠ BẢN: {st['base_bet']:,} WIN
║ 💸 CƯỢC HIỆN TẠI: {st['bet_amount']:,} WIN
║ 💵 SỐ DƯ GAME: {st['balance']:,} WIN
║ 📈 LÃI / LỖ THU THẬP: {pl_str}
║ ✅ THẮNG: {st['total_win']} PHIÊN | ❌ THUA: {st['total_lose']} PHIÊN
║ 📊 DỮ LIỆU CẦU LƯU TRỮ: {len(st['history'])} PHIÊN
╚════════════════════════════════════════════╝</pre>"""
    bot.reply_to(message, msg)

@bot.message_handler(commands=['lichsucau'])
def send_lichsucau(message):
    if not check_auth(message.chat.id):
        return bot.reply_to(message, locked_msg())
        
    st = user_states[message.chat.id]
    if not st['history']:
        return bot.reply_to(message, '📭 Chưa có dữ liệu phiên nào, hãy chờ AI thu thập thêm.')
        
    ls = getResults(st['history'], 20)
    t = ls.count('TÀI')
    x = ls.count('XỈU')
    icons = "".join(['🔵' if i == 'TÀI' else '🔴' for i in ls])
    msg = f"📊 THỐNG KÊ 20 PHIÊN GẦN NHẤT:
🔵 TÀI: {t} | 🔴 XỈU: {x}

{icons}"
    bot.reply_to(message, msg)

@bot.message_handler(commands=['login'])
def send_login(message):
    if not check_auth(message.chat.id):
        return bot.reply_to(message, locked_msg())
        
    parts = message.text.split()
    if len(parts) != 3:
        return bot.reply_to(message, '✅ Hướng dẫn: /login TAIKHOAN MATKHAU')
        
    m = bot.reply_to(message, '🔄 Đang mã hóa và kết nối máy chủ...')
    r = login_and_get_token(parts[1], parts[2])
    
    if r.get('_error'):
        return bot.edit_message_text('❌ ' + r['_error'], chat_id=m.chat.id, message_id=m.message_id)
        
    init_user_state(message.chat.id)
    user_states[message.chat.id]['balance'] = r['money']
    msg_success = f"✅ ĐĂNG NHẬP THÀNH CÔNG
👤 NICKNAME: {r['nickname']}
💰 SỐ DƯ: {r['money']:,} WIN"
    bot.edit_message_text(msg_success, chat_id=m.chat.id, message_id=m.message_id)
    start_websocket(message.chat.id, r['token'])

@bot.message_handler(commands=['autobet'])
def send_autobet(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
    if cid not in active_sockets:
        return bot.reply_to(message, '⚠️ Bạn phải /login tài khoản game trước!')
        
    parts = message.text.split()
    if len(parts) < 2:
        return bot.reply_to(message, '✅ Cú pháp: /autobet on 10000 | off')
        
    st = user_states[cid]
    if parts[1].lower() == 'on':
        amt = 10000
        if len(parts) >= 3 and parts[2].isdigit():
            amt = int(parts[2])
            
        st['auto_bet_enabled'] = True
        st['base_bet'] = amt
        st['bet_amount'] = amt
        x2_str = "💥 BẬT (khi bật /x2 on)" if st.get('x2_enabled') else "🔴 TẮT (dùng /x2 on để bật)"
        msg = f"🟢 AUTO CƯỢC VI LONG ELITE ĐÃ BẬT VIP
💰 VỐN KHỞI ĐIỂM: {amt:,} WIN
🎲 GẤP THẾP X2: {x2_str}
📊 ĐANG THU THẬP VÀ THEO DÕI LÃI LỖ
(Dùng /autobet off hoặc /stop để dừng lại)"
        bot.reply_to(message, msg)
    else:
        st['auto_bet_enabled'] = False
        bot.reply_to(message, '🔴 AUTO CƯỢC ĐÃ DỪNG LẠI AN TOÀN')

@bot.message_handler(commands=['stop'])
def send_stop(message):
    cid = message.chat.id
    if not check_auth(cid):
        return bot.reply_to(message, locked_msg())
        
    if cid in active_sockets:
        try: active_sockets[cid].disconnect()
        except: pass
        del active_sockets[cid]
        if cid in user_states:
            user_states[cid]['auto_bet_enabled'] = False
        bot.reply_to(message, '⏹️ ĐÃ NGẮT KẾT NỐI MÁY CHỦ AN TOÀN')
    else:
        bot.reply_to(message, '⚠️ Bạn chưa kết nối nên không thể ngắt')

# ==========================================
# 🚀 FLASK SERVER - GIỮ BOT SỐNG TRÊN RENDER CỰC MƯỢT
# ==========================================
app = Flask(__name__)

@app.route('/')
def index():
    return f"🚀 BOT VI LONG ELITE ULTRA PRO MAX ĐANG HOẠT ĐỘNG HOÀN HẢO! TIMESTAMP: {int(time.time() * 1000)}"

def run_flask():
    port = int(os.environ.get('PORT', 3000))
    logger['info'](f'🌐 FLASK WEB SERVER CHẠY TRÊN PORT: {port} (CHỐNG TREO RENDER)')
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==========================================
# 🚀 CHẠY BOT + CHỐNG TREO RENDER
# ==========================================
if __name__ == '__main__':
    logger['info']('👑 VIP SYSTEM ONLINE — VI LONG ELITE ULTRA PRO MAX ✨ PYTHON + PING RENDER + FLASK')
    
    # 1. Chạy Anti-Sleep Ping Render
    start_anti_sleep()
    
    # 2. Chạy Flask Web Server ở một luồng độc lập
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 3. Vòng lặp Telegram Bot chính
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            logger['error'](f'BOT POLLING ERR: {e} → ĐANG KẾT NỐI LẠI...')
            time.sleep(3)
