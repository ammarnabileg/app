# -*- coding: utf-8 -*-
"""الحضور من غير جهاز بصمة (خارطة الطريق، المرحلة ٦): كشك، وQR، وكشف الموقع المزيّف.

## الكشك
تابلت عند الاستقبال يفتح `/kiosk/<رمز>`: الموظّف يكتب رقمه ورقمه السرّي (PIN)، والكاميرا
تلتقط صورته وقت البصمة (إن كان الكشك يطلبها). خمس محاولات غلط = قفل ربع ساعة.

## QR
نفس الشاشة تعرض QR يتغيّر كلّ ٢٠ ثانية (HMAC من سرّ الكشك والوقت). الموظّف يمسحه من
موبايله وهو داخل البوّابة → بصمة. الصورة تتغيّر، فصورتها المبعوثة على واتساب تموت في ثوانٍ.

## الموقع المزيّف
التطبيق يبعت `is_mocked` (Position.isMocked على أندرويد). البصمة تُرفض (إعداد
`gps_reject_mocked`) وتُسجَّل في «مؤشرات التلاعب». وفي تتبّع المناديب: قفزة أسرع من
٢٥٠ كم/س بين نقطتين تُسجَّل كمؤشّر (من غير رفض — الـGPS بيقفز أحيانًا).
"""
import base64
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from datetime import datetime, timedelta

QR_WINDOW = 20
PIN_MAX_FAILS = 5
PIN_LOCK_MINUTES = 15
TELEPORT_KMH = 250
FLAG_KINDS = {'mock': 'موقع وهمي', 'teleport': 'قفزة مستحيلة', 'pin_lock': 'رقم سري غلط متكرر'}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS kiosks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token TEXT UNIQUE NOT NULL,
        branch_name TEXT, allow_pin INTEGER DEFAULT 1, allow_qr INTEGER DEFAULT 1, require_selfie INTEGER DEFAULT 1,
        is_active INTEGER DEFAULT 1, last_seen TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS kiosk_pins (
        employee_id INTEGER PRIMARY KEY, pin_hash TEXT NOT NULL, fails INTEGER DEFAULT 0, locked_until TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS gps_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER, kind TEXT NOT NULL, context TEXT,
        latitude REAL, longitude REAL, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------ مؤشّرات التلاعب

def flag(conn, employee_id, kind, context, lat=None, lon=None, detail=''):
    try:
        ensure_schema(conn)
        conn.execute('INSERT INTO gps_flags (employee_id, kind, context, latitude, longitude, detail) '
                     'VALUES (?, ?, ?, ?, ?, ?)', (employee_id, kind, context, lat, lon, (detail or '')[:300]))
        conn.commit()
    except Exception:
        pass


def is_mocked(data):
    v = (data or {}).get('is_mocked')
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def reject_mocked():
    try:
        from utils.db import get_setting
        return str(get_setting('gps_reject_mocked', '1')) != '0'
    except Exception:
        return True


MOCK_MESSAGE = 'الموبايل بيبعت موقع وهمي (Mock Location) — اقفل تطبيق تغيير الموقع وجرّب تاني. اتسجّلت المحاولة.'


def check_mocked(conn, employee_id, data, context):
    """None لو سليم، أو رسالة الرفض. يسجّل المؤشّر في الحالتين لو مزيّف."""
    if not is_mocked(data):
        return None
    def _f(k):
        try:
            return float(data.get(k))
        except (TypeError, ValueError):
            return None
    flag(conn, employee_id, 'mock', context, _f('latitude'), _f('longitude'))
    return MOCK_MESSAGE if reject_mocked() else None


def _km(a, b):
    from utils.field import haversine
    return haversine(a[0], a[1], b[0], b[1]) / 1000.0


def check_teleport(conn, employee_id, points, context='field_track'):
    """نقاطُ [{latitude, longitude, recorded_at}] بالترتيب → عدد القفزات المسجَّلة."""
    prev, n = None, 0
    for p in points or []:
        try:
            cur = (float(p['latitude']), float(p['longitude']),
                   datetime.strptime(str(p['recorded_at'])[:19], '%Y-%m-%d %H:%M:%S'))
        except (KeyError, TypeError, ValueError):
            continue
        if prev:
            hours = (cur[2] - prev[2]).total_seconds() / 3600.0
            dist = _km(prev, cur)
            if hours > 0 and dist > 1 and dist / hours > TELEPORT_KMH:
                flag(conn, employee_id, 'teleport', context, cur[0], cur[1],
                     f'{dist:.1f} كم في {int(hours * 3600)} ثانية')
                n += 1
        prev = cur
    return n


def flags(conn, days=30):
    ensure_schema(conn)
    rows = conn.execute('''SELECT f.*, e.name AS employee_name, e.employee_number FROM gps_flags f
                           LEFT JOIN employees e ON e.id = f.employee_id
                           WHERE f.created_at >= datetime('now', ?) ORDER BY f.id DESC LIMIT 500''',
                        (f'-{int(days)} days',)).fetchall()
    return [dict(r, kind_label=FLAG_KINDS.get(r['kind'], r['kind'])) for r in rows]


# ------------------------------------------------------------ الأكشاك

def create_kiosk(conn, name, branch_name=None, allow_pin=True, allow_qr=True, require_selfie=True):
    ensure_schema(conn)
    name = (name or '').strip()[:100]
    if not name:
        raise ValueError('اكتب اسم الكشك')
    token = secrets.token_urlsafe(18)
    conn.execute('INSERT INTO kiosks (name, token, branch_name, allow_pin, allow_qr, require_selfie) '
                 'VALUES (?, ?, ?, ?, ?, ?)', (name, token, branch_name or None, int(bool(allow_pin)),
                                              int(bool(allow_qr)), int(bool(require_selfie))))
    conn.commit()
    return token


def kiosk_by_token(conn, token):
    ensure_schema(conn)
    r = conn.execute('SELECT * FROM kiosks WHERE token = ? AND is_active = 1', (token or '',)).fetchone()
    return dict(r) if r else None


def kiosks(conn):
    ensure_schema(conn)
    return [dict(r) for r in conn.execute('SELECT * FROM kiosks ORDER BY id').fetchall()]


def touch(conn, kiosk_id):
    conn.execute('UPDATE kiosks SET last_seen = ? WHERE id = ?', (_now(), kiosk_id))
    conn.commit()


# ------------------------------------------------------------ PIN

def _hash_pin(pin):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(str(pin))


def set_pin(conn, employee_id, pin=None):
    """يضبط الرقم السرّي (أو يولّد ٤ أرقام). يُرجع الرقم نفسه لعرضه مرّة واحدة."""
    ensure_schema(conn)
    pin = str(pin or '').strip() or f'{secrets.randbelow(10000):04d}'
    if not re.fullmatch(r'\d{4,8}', pin):
        raise ValueError('الرقم السري من 4 لـ 8 أرقام')
    conn.execute('INSERT INTO kiosk_pins (employee_id, pin_hash) VALUES (?, ?) ON CONFLICT(employee_id) DO UPDATE '
                 'SET pin_hash = excluded.pin_hash, fails = 0, locked_until = NULL, updated_at = CURRENT_TIMESTAMP',
                 (employee_id, _hash_pin(pin)))
    conn.commit()
    return pin


def has_pin(conn, employee_id):
    ensure_schema(conn)
    return bool(conn.execute('SELECT 1 FROM kiosk_pins WHERE employee_id = ?', (employee_id,)).fetchone())


def verify_pin(conn, employee_number, pin):
    """(employee_id, None) أو (None, رسالة)."""
    from werkzeug.security import check_password_hash
    ensure_schema(conn)
    emp = conn.execute('SELECT id, is_active FROM employees WHERE CAST(employee_number AS TEXT) = ?',
                       (str(employee_number or '').strip(),)).fetchone()
    row = conn.execute('SELECT * FROM kiosk_pins WHERE employee_id = ?', (emp['id'],)).fetchone() if emp else None
    if not emp or not row:
        return None, 'الرقم أو الرقم السري غلط'
    if not emp['is_active']:
        return None, 'الحساب ده موقوف — راجع الموارد البشرية'
    if row['locked_until'] and row['locked_until'] > _now():
        return None, 'اتقفل مؤقتًا بعد محاولات غلط كتير — جرّب بعد ربع ساعة'
    if not check_password_hash(row['pin_hash'], str(pin or '')):
        fails = (row['fails'] or 0) + 1
        locked = None
        if fails >= PIN_MAX_FAILS:
            locked = (datetime.now() + timedelta(minutes=PIN_LOCK_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
            fails = 0
            flag(conn, emp['id'], 'pin_lock', 'kiosk', detail=f'{PIN_MAX_FAILS} محاولات غلط')
        conn.execute('UPDATE kiosk_pins SET fails = ?, locked_until = ? WHERE employee_id = ?', (fails, locked, emp['id']))
        conn.commit()
        return None, 'الرقم أو الرقم السري غلط'
    conn.execute('UPDATE kiosk_pins SET fails = 0, locked_until = NULL WHERE employee_id = ?', (emp['id'],))
    conn.commit()
    return emp['id'], None


# ------------------------------------------------------------ QR

def qr_code(kiosk, at=None):
    window = int((at if at is not None else time.time()) // QR_WINDOW)
    return hmac.new(kiosk['token'].encode(), str(window).encode(), hashlib.sha256).hexdigest()[:16]


def qr_valid(kiosk, code, at=None):
    at = at if at is not None else time.time()
    return any(hmac.compare_digest(qr_code(kiosk, at - k * QR_WINDOW), str(code or '')) for k in (0, 1))


def qr_svg(text):
    import segno
    import io
    buf = io.BytesIO()
    segno.make(text, error='m').save(buf, kind='svg', scale=8, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode()


# ------------------------------------------------------------ تسجيل البصمة

def selfie_dir():
    from utils.db import DATA_DIR
    d = os.path.join(DATA_DIR, 'kiosk_photos')
    os.makedirs(d, exist_ok=True)
    return d


def save_selfie(data_url, employee_id):
    m = re.match(r'^data:image/(jpeg|png|webp);base64,(.+)$', str(data_url or ''), re.S)
    if not m:
        return None
    raw = base64.b64decode(m.group(2))
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('الصورة كبيرة')
    name = f'{employee_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{secrets.token_hex(3)}.{m.group(1).replace("jpeg", "jpg")}'
    with open(os.path.join(selfie_dir(), name), 'wb') as f:
        f.write(raw)
    return name


def record(conn, employee_id, source, note, user_id=None, now=None):
    """يسجّل بصمة (نوعها من عدد بصمات اليوم كالبوّابة). (ok, رسالة)."""
    now = now or datetime.now()
    try:
        from utils.settings_utils import get_portal_attendance_settings
        cooldown = get_portal_attendance_settings(conn)['cooldown_minutes']
    except Exception:
        cooldown = 5
    last = conn.execute('SELECT check_time FROM attendance_records WHERE employee_id = ? ORDER BY check_time DESC '
                        'LIMIT 1', (employee_id,)).fetchone()
    if last:
        try:
            diff = (now - datetime.strptime(last[0], '%Y-%m-%d %H:%M:%S')).total_seconds()
            if 0 <= diff < cooldown * 60:
                return False, f'اتسجّلت بصمة من {int(diff // 60)} دقيقة — استنى {math.ceil((cooldown * 60 - diff) / 60)} دقيقة'
        except ValueError:
            pass
    n = conn.execute('SELECT COUNT(*) FROM attendance_records WHERE employee_id = ? AND DATE(check_time) = ?',
                     (employee_id, now.strftime('%Y-%m-%d'))).fetchone()[0]
    code, label = (1, 'حضور') if n == 0 else ((2, 'تواجد') if n == 1 else (0, 'انصراف'))
    conn.execute('''INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, verify_code, source,
                    note, created_at, created_by) VALUES (?, 0, ?, ?, 15, ?, ?, CURRENT_TIMESTAMP, ?)''',
                 (employee_id, now.strftime('%Y-%m-%d %H:%M:%S'), code, source, f'بصمة {label} — {note}', user_id))
    conn.commit()
    name = conn.execute('SELECT name FROM employees WHERE id = ?', (employee_id,)).fetchone()
    return True, f'{name[0] if name else ""} — اتسجّل {label} {now.strftime("%H:%M")}'
