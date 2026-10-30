# -*- coding: utf-8 -*-
"""الوكيل المحلّيّ (Local Connector) — جانب السيرفر (خارطة الطريق، المرحلة ١).

## المشكلة
النسخة الأونلاين (السحابة) لا تصل لجهاز K40 داخل شبكة الشركة: البروتوكول المباشر
يحتاج أن **نتّصل نحن** بالجهاز. أجهزة ADMS لا تحتاج شيئًا (هي التي تتّصل).

## الحلّ
برنامجٌ صغير على أيّ ويندوز في الشركة (`connector_agent.py` — نفس كود البرنامج)
يتّصل بالأجهزة كما يتّصل البرنامج المكتبيّ اليوم، ويكلّم السحابة عبر HTTPS بمفتاح:

| الطلب | ماذا |
|---|---|
| `GET  /api/connector/config`   | الأجهزة المسندة لهذا الوكيل وآخر بصمة استلمناها من كلٍّ منها |
| `POST /api/connector/punches`  | بصمات جهاز → نفس قواعد الحضور (`engine.record_many`) |
| `POST /api/connector/users`    | مستخدمو الجهاز → كبقيّة الماركات (`engine.sync_users`) |
| `GET  /api/connector/commands` | أوامر منتظرة: إيقاف/تفعيل/حذف/رفع موظّف، ضبط الوقت |
| `POST /api/connector/ack`      | نتيجة كلّ أمر (+ بصمات الموظّف قبل إيقافه/حذفه، تُحفظ عندنا) |

الجهاز في السحابة سوّاقه `zk_connector` (utils/devices/connector_driver): عمليّاتُه
تُكتب في طابور `connector_commands` بدل أن تنفَّذ — فالمحرّكُ نفسه (الإيقاف، الحذف،
سلّة المحذوفات) يعمل عليه بلا تغيير. والمفتاح يُحفظ مُجزَّأً (SHA-256) ويظهر مرّةً واحدة.
"""
import hashlib
import json
import secrets
from datetime import datetime, timedelta

DRIVER_KEY = 'zk_connector'
KEY_PREFIX = 'onzc_'
RESEND_AFTER_MINUTES = 10
MAX_ATTEMPTS = 20
KINDS = {'disable_user': 'إيقاف موظف', 'enable_user': 'تفعيل موظف', 'delete_user': 'حذف موظف',
         'upsert_user': 'رفع موظف', 'set_time': 'ضبط الوقت'}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS connectors (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, key_hint TEXT,
        is_active INTEGER DEFAULT 1, last_seen TEXT, last_ip TEXT, agent_version TEXT, last_error TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS connector_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL, kind TEXT NOT NULL, pin TEXT,
        payload TEXT, status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, result TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, sent_at TEXT, done_at TEXT)''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_connector_commands_status ON connector_commands (status, device_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS connector_device_users (
        device_id INTEGER NOT NULL, pin TEXT NOT NULL, name TEXT, card TEXT, enabled INTEGER DEFAULT 1,
        seen_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (device_id, pin))''')
    conn.commit()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _hash(key):
    return hashlib.sha256(str(key or '').encode()).hexdigest()


# ------------------------------------------------------------ المفاتيح

def create(conn, name):
    """وكيلٌ جديد → (رقمه، المفتاح كاملًا — يُعرض مرّة واحدة)."""
    ensure_schema(conn)
    name = (name or '').strip()[:100] or 'الوكيل المحلي'
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    cid = conn.execute('INSERT INTO connectors (name, key_hash, key_hint) VALUES (?, ?, ?)',
                       (name, _hash(key), key[:9] + '…' + key[-4:])).lastrowid
    conn.commit()
    return cid, key


def rotate(conn, connector_id):
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    conn.execute('UPDATE connectors SET key_hash = ?, key_hint = ? WHERE id = ?',
                 (_hash(key), key[:9] + '…' + key[-4:], connector_id))
    conn.commit()
    return key


def authenticate(conn, key, ip=None, version=None):
    ensure_schema(conn)
    if not key or not str(key).startswith(KEY_PREFIX):
        return None
    r = conn.execute('SELECT * FROM connectors WHERE key_hash = ? AND is_active = 1', (_hash(key),)).fetchone()
    if not r:
        return None
    conn.execute('UPDATE connectors SET last_seen = ?, last_ip = ?, agent_version = COALESCE(?, agent_version) '
                 'WHERE id = ?', (_now(), ip, version, r['id']))
    conn.commit()
    return dict(r)


def connectors(conn):
    ensure_schema(conn)
    out = []
    for r in conn.execute('SELECT * FROM connectors ORDER BY id').fetchall():
        d = dict(r)
        d['devices'] = [dict(x) for x in devices_of(conn, d['id'], active_only=False)]
        d['pending'] = conn.execute(
            f"SELECT COUNT(*) FROM connector_commands WHERE status IN ('pending', 'sent') AND device_id IN "
            f"({','.join(str(x['id']) for x in d['devices']) or '0'})").fetchone()[0]
        out.append(d)
    return out


# ------------------------------------------------------------ الأجهزة

def connector_id_of(device):
    try:
        return int((json.loads(device['driver_options'] or '{}') or {}).get('connector_id') or 0)
    except Exception:
        return 0


def devices_of(conn, connector_id, active_only=True):
    sql = "SELECT * FROM fingerprint_devices WHERE driver = ?"
    if active_only:
        sql += " AND is_active = 1"
    return [d for d in conn.execute(sql + ' ORDER BY id', (DRIVER_KEY,)).fetchall()
            if connector_id_of(d) == int(connector_id)]


def _device_for(conn, connector, device_id):
    for d in devices_of(conn, connector['id']):
        if int(d['id']) == int(device_id or 0):
            return d
    return None


def config(conn, connector):
    return {'connector': connector['name'], 'server_time': _now(),
            'devices': [{'id': d['id'], 'name': d['device_name'], 'ip': d['device_ip'],
                         'port': int(d['device_port'] or 4370), 'last_sync_time': d['last_sync_time']}
                        for d in devices_of(conn, connector['id'])]}


# ------------------------------------------------------------ الوارد من الوكيل

def receive_punches(conn, connector, device_id, punches):
    """[{pin, time 'YYYY-mm-dd HH:MM:SS', status, verify}] → ملخّص engine.record_many."""
    from utils.devices import base, engine
    device = _device_for(conn, connector, device_id)
    if device is None:
        return {'ok': False, 'reason': 'unknown_device'}
    items, newest = [], None
    for p in punches or []:
        try:
            t = datetime.strptime(str(p['time'])[:19], '%Y-%m-%d %H:%M:%S')
        except (KeyError, TypeError, ValueError):
            continue
        items.append(base.Punch(pin=str(p.get('pin', '')).strip(), time=t, check_type=int(p.get('status') or 0),
                                verify=str(p.get('verify') or '')))
        newest = t if newest is None or t > newest else newest
    res = engine.record_many(conn, device['id'], [i for i in items if i.pin], source=DRIVER_KEY)
    last = engine._parse_dt(device['last_sync_time'])
    if newest and (last is None or newest > last):
        last = newest
    conn.execute('UPDATE fingerprint_devices SET last_sync_time = ?, last_activity = CURRENT_TIMESTAMP WHERE id = ?',
                 (last.strftime('%Y-%m-%d %H:%M:%S') if last else None, device['id']))
    conn.commit()
    if res.get('saved') or res.get('pending'):
        try:
            from utils import pending_punches
            pending_punches.adopt(conn)
        except Exception:
            pass
    return {'ok': True, 'received': len(items), **res}


def receive_users(conn, connector, device_id, users):
    """قائمة مستخدمي الجهاز كاملة → تحلّ محلّ السابقة، ثمّ engine.sync_users (يضيف الجدد كموظفين)."""
    from utils.devices import engine
    ensure_schema(conn)
    device = _device_for(conn, connector, device_id)
    if device is None:
        return {'ok': False, 'reason': 'unknown_device'}
    conn.execute('DELETE FROM connector_device_users WHERE device_id = ?', (device['id'],))
    for u in users or []:
        pin = str(u.get('pin') or '').strip()
        if pin:
            conn.execute('INSERT OR REPLACE INTO connector_device_users (device_id, pin, name, card, enabled) '
                         'VALUES (?, ?, ?, ?, ?)', (device['id'], pin, (u.get('name') or '')[:60],
                                                    str(u.get('card') or ''), 0 if u.get('disabled') else 1))
    conn.commit()
    return {'ok': True, **engine.sync_users(conn, device)}


# ------------------------------------------------------------ الأوامر

def enqueue(conn, device_id, kind, pin=None, payload=None):
    """أمرٌ لجهازٍ خلف الوكيل. أمرٌ معلّق لنفس الموظف على نفس الجهاز يُستبدل (آخر قرار يفوز)."""
    ensure_schema(conn)
    if pin is not None:
        conn.execute("DELETE FROM connector_commands WHERE device_id = ? AND pin = ? AND status = 'pending' "
                     "AND kind IN ('disable_user', 'enable_user', 'delete_user', 'upsert_user')", (device_id, str(pin)))
    cid = conn.execute('INSERT INTO connector_commands (device_id, kind, pin, payload) VALUES (?, ?, ?, ?)',
                       (device_id, kind, None if pin is None else str(pin),
                        json.dumps(payload or {}, ensure_ascii=False))).lastrowid
    conn.commit()
    return cid


def fetch_commands(conn, connector, limit=100):
    """الأوامر المنتظرة (وما أُرسل ولم يُؤكَّد خلال ١٠ دقائق يُعاد)."""
    ensure_schema(conn)
    ids = [d['id'] for d in devices_of(conn, connector['id'])]
    if not ids:
        return []
    stale = (datetime.now() - timedelta(minutes=RESEND_AFTER_MINUTES)).strftime('%Y-%m-%d %H:%M:%S')
    rows = conn.execute(f'''SELECT * FROM connector_commands WHERE device_id IN ({','.join('?' * len(ids))})
                            AND (status = 'pending' OR (status = 'sent' AND sent_at < ?)) AND attempts < ?
                            ORDER BY id LIMIT ?''', (*ids, stale, MAX_ATTEMPTS, limit)).fetchall()
    out = []
    for r in rows:
        conn.execute("UPDATE connector_commands SET status = 'sent', sent_at = ?, attempts = attempts + 1 WHERE id = ?",
                     (_now(), r['id']))
        out.append({'id': r['id'], 'device_id': r['device_id'], 'kind': r['kind'], 'pin': r['pin'],
                    'payload': json.loads(r['payload'] or '{}')})
    conn.commit()
    return out


def ack(conn, connector, results):
    """[{id, ok, message, templates?}] — الفاشل يرجع «منتظر» ليُعاد."""
    ensure_schema(conn)
    ids = {d['id']: d for d in devices_of(conn, connector['id'], active_only=False)}
    done = 0
    for r in results or []:
        row = conn.execute('SELECT * FROM connector_commands WHERE id = ?', (r.get('id'),)).fetchone()
        if not row or row['device_id'] not in ids:
            continue
        ok = bool(r.get('ok'))
        conn.execute('UPDATE connector_commands SET status = ?, result = ?, done_at = ? WHERE id = ?',
                     ('done' if ok else ('failed' if row['attempts'] >= MAX_ATTEMPTS else 'pending'),
                      str(r.get('message') or '')[:300], _now() if ok else None, row['id']))
        if r.get('templates'):
            _save_templates(conn, row['pin'], r['templates'], ids[row['device_id']])
        if ok and row['pin'] and row['kind'] == 'delete_user':
            conn.execute('DELETE FROM connector_device_users WHERE device_id = ? AND pin = ?', (row['device_id'], row['pin']))
        elif ok and row['pin'] and row['kind'] in ('disable_user', 'enable_user'):
            conn.execute('UPDATE connector_device_users SET enabled = ? WHERE device_id = ? AND pin = ?',
                         (1 if row['kind'] == 'enable_user' else 0, row['device_id'], row['pin']))
        done += int(ok)
    conn.commit()
    return done


def _save_templates(conn, pin, templates, device):
    """بصمات الموظّف قبل إيقافه/حذفه — كما يحفظها المسار المباشر (utils/device_access.backup_templates)."""
    try:
        from utils import biometric_templates as bio
        for t in templates:
            bio.save(conn, str(pin), int(t['fid']), str(t['template']), template_type=1, major_ver=t.get('fp_ver'),
                     fmt='pyzk', valid=int(t.get('valid') or 1), device_sn=t.get('sn') or device['device_ip'],
                     replace=False)
    except Exception:
        pass


def recent_commands(conn, limit=100):
    ensure_schema(conn)
    rows = conn.execute('''SELECT c.*, d.device_name FROM connector_commands c
                           LEFT JOIN fingerprint_devices d ON d.id = c.device_id ORDER BY c.id DESC LIMIT ?''',
                        (limit,)).fetchall()
    return [dict(r, kind_label=KINDS.get(r['kind'], r['kind'])) for r in rows]
