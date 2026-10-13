# -*- coding: utf-8 -*-
"""مزامنة البصمات مع أوراكل — أول بأول.

## اللي كان غلط (لحد ٢.٣٧.١٩)

1. **بصمات أجهزة IP ماكانتش بتروح أوراكل خالص** — بصمات ADMS بس. وكمان البصمات اللي بتدخل الحضور من
   أي طريق تاني (موظف اتضاف بعد ما بصم، «احسبها» للبصمات المرفوضة، …) ماكانتش بتروح.
2. **اتصالات أوراكل ماكانتش بتتقفل**: كل ٥ دقايق اتصال جديد، وكل سؤال من شاشة الإحصائيات اتصال جديد،
   وكلهم بيفضلوا مفتوحين (كان مكتوب «conn.close() removed» — ده كان لاتصالات SQLite مش أوراكل).
   النتيجة: الـsessions بتتراكم على سيرفر أوراكل لحد ما يرفض (ORA-00018 / ORA-12519).
3. **المزامنة كل ٥ دقايق**، والطابور بيتاخد ١٠٠ بـ١٠٠ من غير ترتيب: لو أول ١٠٠ بصمة فيهم مشكلة دايمة
   (جدول ناقص عمود مثلًا) **الطابور كله بيقف للأبد** — ومفيش «فشل» بيتسجّل.
4. «مزامنة الناقص» كانت بتقع دايمًا (استعلام بيسمّي جدول مش موجود فيه)، وصفحة السجل كانت بتطلب عمود
   مش موجود (`device_ip`).
5. تعديل وقت بصمة (أداة تصحيح الوقت) أو مسحها ماكانش بيوصل أوراكل — يفضل الوقت الغلط هناك.

## دلوقتي

- **Trigger على جدول الحضور**: أي بصمة جهاز بتدخل الحضور — من أي مكان — بتتحط في طابور أوراكل في
  نفس اللحظة. وتعديل وقتها = مسح القديمة من أوراكل وإضافة الجديدة، ومسحها = مسحها من أوراكل —
  **بس لو إحنا اللي حطّيناها هناك** (مابنمسحش حاجة ماحطّيناهاش).
- **عامل في الخلفية** بيبعت الطابور كل `SYNC_SECONDS` (١٠ ثواني) — وفورًا لما يتنادى `wake()` —
  باتصال واحد بيتعاد استخدامه وبيتقفل صح.
- كل بصمة بتتحاول لوحدها: الفشل المؤقت (الشبكة) بيتعاد بعد وقت بيزيد، والفشل الدايم (جدول/عمود
  غلط) بيتعلّم **FAILED** ومعاه السبب — ومابيوقفش الطابور.
- قبل الإضافة بيتأكد إن البصمة مش موجودة في أوراكل (مايتكررش لو اتبعتت مرتين).
"""
import datetime
import logging
import os
import sys
import threading
import time

import oracledb

from utils.db import DATA_DIR, get_db_connection, get_setting

SYNC_SECONDS = 10           # كل قد إيه العامل بيبص على الطابور (وفورًا مع wake)
BATCH = 200
MAX_RETRIES = 20
_PERMANENT_ORA = {904, 942, 1722, 12899, 1400, 1438, 1830, 1840, 1841, 1843, 1847, 1858, 1861}

# ================= MASTER SWITCH =================
_oracle_enabled_cache = {'value': None, 'ts': 0}
_oracle_configured_cache = {'value': None, 'ts': 0}
_unconfigured_said = {'done': False}

# ما يدلّ على أن أحدًا ضبط أوراكل فعلًا — إعدادًا مخزَّنًا أو متغيّر بيئة.
_ORACLE_CONF_KEYS = (
    ('oracle_host',     'ORACLE_HOST'),
    ('oracle_user',     'ORACLE_USER'),
    ('oracle_password', 'ORACLE_PASSWORD'),
    ('oracle_service',  'ORACLE_SERVICE'),
)

# Configure a specific logger for Oracle Sync
from utils.safe_log import SafeRotatingHandler as RotatingFileHandler
oracle_log_file = os.path.join(DATA_DIR, 'oracle_sync.log')
oracle_logger = logging.getLogger('oracle_sync')
oracle_logger.setLevel(logging.INFO)
if not oracle_logger.handlers:
    fh = RotatingFileHandler(oracle_log_file, max_bytes=20 * 1024 * 1024, backups=3)
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    oracle_logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    oracle_logger.addHandler(ch)


def oracle_configured():
    """أضبط أحدٌ أوراكل على هذا النظام؟ (مضيف أو مستخدم أو باسورد أو خدمة — إعداد أو متغيّر بيئة)."""
    now = time.time()
    if (_oracle_configured_cache['value'] is None
            or (now - _oracle_configured_cache['ts']) > 30):
        found = False
        for key, env_key in _ORACLE_CONF_KEYS:
            try:
                if str(get_setting(key, '') or '').strip():
                    found = True
                    break
            except Exception:
                found = True
                break
            if str(os.environ.get(env_key, '') or '').strip():
                found = True
                break
        _oracle_configured_cache['value'] = found
        _oracle_configured_cache['ts'] = now
        if not found and not _unconfigured_said['done']:
            _unconfigured_said['done'] = True
            oracle_logger.info(
                'مزامنة أوراكل غير مضبوطة على هذا النظام — لا مضيف ولا '
                'مستخدم في الإعدادات ولا في متغيّرات البيئة. فلا تُحاوَل '
                'اتصالات ولا يُكتب طابور. اضبطها من شاشة أوراكل إن كانت '
                'مطلوبة، أو أطفئها لتختفي هذه الرسالة.')
    return _oracle_configured_cache['value']


def _free_edition():
    try:
        from utils.plan_features import edition
        return edition() == 'free'
    except Exception:
        return False


def is_oracle_enabled():
    """مفتوحة من الشاشة ومضبوطة. لو لأ: لا اتصال ولا طابور ولا ضجّة في اللوج."""
    now = time.time()
    if _oracle_enabled_cache['value'] is None or (now - _oracle_enabled_cache['ts']) > 10:
        try:
            _oracle_enabled_cache['value'] = str(get_setting('oracle_enabled', '1')).strip() == '1'
        except Exception:
            _oracle_enabled_cache['value'] = True
        _oracle_enabled_cache['ts'] = now
    if _oracle_enabled_cache['value'] and _free_edition():
        return False                # النسخة المجانية: أوراكل مقفول
    return _oracle_enabled_cache['value'] and oracle_configured()


def invalidate_caches():
    _oracle_enabled_cache['ts'] = 0
    _oracle_configured_cache['ts'] = 0


def _oracle_conf(key, env_key, default):
    """DB-stored setting wins; empty falls back to the environment variable."""
    try:
        v = str(get_setting(key, '') or '').strip()
    except Exception:
        v = ''
    return v if v else os.environ.get(env_key, default)


def table_name():
    return _oracle_conf('oracle_table', "ORACLE_TABLE_NAME", "ATTENDANCE_LOGS")


# ================= Oracle client (Thick mode) — مرة واحدة، أول ما نحتاجه =================
_client = {'done': False}


def _ensure_client():
    """Thick mode (Instant Client) لو موجود — عشان السيرفرات القديمة. كان بيتعمل وقت تحميل الملف بس،
    فلو أوراكل اتفتح بعد التشغيل كان بيشتغل من غيره."""
    if _client['done']:
        return
    _client['done'] = True
    try:
        try:
            if oracledb.is_thin_mode() is False:
                return
        except AttributeError:
            pass
        lib_dir = str(get_setting("oracle_lib_dir", "") or "").strip()
        if not lib_dir:
            try:
                import json
                config_path = os.path.join(os.path.abspath("."), 'db_config.json')
                if os.path.exists(config_path):
                    with open(config_path, 'r', encoding='utf-8') as f:
                        lib_dir = str(json.load(f).get('lib_dir', '') or '').strip()
            except Exception:
                pass
        if not lib_dir and os.path.exists("C:\\IBR-ZTKTECO-ORCAL\\instantclient"):
            lib_dir = "C:\\IBR-ZTKTECO-ORCAL\\instantclient"
        if not lib_dir:
            base_dir = getattr(sys, '_MEIPASS', os.path.abspath("."))
            for path in (os.path.join(base_dir, "instantclient"), os.path.join(os.path.abspath("."), "instantclient")):
                if os.path.exists(path) and any(f.endswith('.dll') or f.endswith('.so') for f in os.listdir(path)):
                    lib_dir = path
                    break
        if lib_dir:
            oracle_logger.info(f"Initializing Oracle Client (Thick Mode) with lib_dir: {os.path.abspath(lib_dir)}")
            oracledb.init_oracle_client(lib_dir=os.path.abspath(lib_dir))
        else:
            oracledb.init_oracle_client()
    except Exception as e:
        if "DPY-2017" in str(e) or "already" in str(e).lower():
            return
        # مفيش Instant Client: thin mode بيشتغل مع أغلب السيرفرات الحديثة.
        oracle_logger.info(f"Oracle Thick Mode not available, using thin mode: {e}")


def get_oracle_connection():
    """اتصال جديد بأوراكل — **اللي بيفتحه هو اللي يقفله**. None لو فشل (والسبب في اللوج)."""
    _ensure_client()
    host = _oracle_conf('oracle_host', "ORACLE_HOST", "localhost")
    port = _oracle_conf('oracle_port', "ORACLE_PORT", "1521")
    service = _oracle_conf('oracle_service', "ORACLE_SERVICE", "XEPDB1")
    user = _oracle_conf('oracle_user', "ORACLE_USER", "HR")
    password = _oracle_conf('oracle_password', "ORACLE_PASSWORD", "password")
    try:
        return oracledb.connect(user=user, password=password, dsn=f"{host}:{port}/{service}",
                                tcp_connect_timeout=3)
    except oracledb.DatabaseError as e:
        err = e.args[0] if e.args else e
        code = getattr(err, 'code', None)
        msg = getattr(err, 'message', str(e))
        _set_state(error=f'{code}: {msg}' if code else str(msg))
        if code == 1017:
            oracle_logger.critical("CRITICAL: Oracle Login Failed - Check Credentials or Account Status (ORA-01017).")
        else:
            oracle_logger.error(f"Oracle Database Error ({code}): {msg}")
        return None
    except Exception as e:
        _set_state(error=str(e)[:300])
        oracle_logger.error(f"Oracle Connection Error: {e}")
        return None


def _close(c):
    try:
        if c is not None:
            c.close()
    except Exception:
        pass


def check_oracle_connection():
    """أوراكل بيرد؟ (بيفتح اتصال ويقفله — كان بيسيبه مفتوح مع كل سؤال من الشاشة)."""
    if not is_oracle_enabled():
        return False
    c = get_oracle_connection()
    if c is None:
        return False
    _close(c)
    return True


# ================= الطابور في SQLite =================

def _local():
    """اتصال SQLite خاص بالعامل (برّه طلبات Flask) — بيتقفل بعد كل دورة."""
    import sqlite3
    from utils.db import DB_PATH
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def ensure_schema(conn):
    """أعمدة الطابور الجديدة، والفهارس، وحالة المزامنة، وTriggers جدول الحضور."""
    conn.execute('''CREATE TABLE IF NOT EXISTS oracle_sync_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        check_time TEXT NOT NULL,
        check_type TEXT NOT NULL,
        verify_code TEXT,
        device_id INTEGER,
        retry_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'PENDING',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    cols = {r[1] for r in conn.execute('PRAGMA table_info(oracle_sync_queue)')}
    for col, typ in (('device_id', 'INTEGER'), ('op', "TEXT DEFAULT 'INSERT'"), ('last_error', 'TEXT'),
                     ('next_try_at', 'TEXT'), ('synced_at', 'TEXT'), ('attendance_id', 'INTEGER')):
        if col not in cols:
            conn.execute(f'ALTER TABLE oracle_sync_queue ADD COLUMN {col} {typ}')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_oracle_queue_status ON oracle_sync_queue (status, id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_oracle_queue_key ON oracle_sync_queue (user_id, check_time)')
    conn.execute('''CREATE TABLE IF NOT EXISTS oracle_sync_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        active INTEGER DEFAULT 0,
        last_ok_at TEXT,
        last_error TEXT,
        last_error_at TEXT
    )''')
    if not conn.execute('SELECT 1 FROM oracle_sync_state WHERE id = 1').fetchone():
        conn.execute('INSERT OR IGNORE INTO oracle_sync_state (id, active) VALUES (1, 0)')
    _ensure_triggers(conn)
    if conn.in_transaction:
        conn.commit()           # مانسيبش القاعدة ماسكينها (اتصال تاني ممكن يكتب إعداد)


# بصمة جهاز = مصدرها جهاز، أو تسوية بكتاب معتمد (utils/punch_reconcile) — مش إدخال يدوي عادي.
_SOURCES = "('device', 'adms', '', 'adjustment')"
_DEVICE_SRC = "(%s.source IS NULL OR %s.source IN " + _SOURCES + ")"
_ACTIVE = "(SELECT active FROM oracle_sync_state WHERE id = 1) = 1"
_TRIGGERS = {
    'trg_oracle_att_insert_v2': f'''
        CREATE TRIGGER trg_oracle_att_insert_v2 AFTER INSERT ON attendance_records
        WHEN {_ACTIVE} AND {_DEVICE_SRC % ('NEW', 'NEW')}
        BEGIN
            INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, op, attendance_id)
            SELECT e.employee_number, NEW.check_time, COALESCE(NEW.check_type, 0), NEW.verify_code, NEW.device_id,
                   'INSERT', NEW.id
            FROM employees e WHERE e.id = NEW.employee_id;
        END''',
    'trg_oracle_att_update_v2': f'''
        CREATE TRIGGER trg_oracle_att_update_v2 AFTER UPDATE OF check_time, employee_id ON attendance_records
        WHEN {_ACTIVE} AND {_DEVICE_SRC % ('NEW', 'NEW')}
             AND (OLD.check_time != NEW.check_time OR OLD.employee_id != NEW.employee_id)
        BEGIN
            INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, op, attendance_id)
            SELECT e.employee_number, OLD.check_time, COALESCE(OLD.check_type, 0), OLD.verify_code, OLD.device_id,
                   'DELETE', OLD.id
            FROM employees e WHERE e.id = OLD.employee_id;
            INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, op, attendance_id)
            SELECT e.employee_number, NEW.check_time, COALESCE(NEW.check_type, 0), NEW.verify_code, NEW.device_id,
                   'INSERT', NEW.id
            FROM employees e WHERE e.id = NEW.employee_id;
        END''',
    'trg_oracle_att_delete_v2': f'''
        CREATE TRIGGER trg_oracle_att_delete_v2 AFTER DELETE ON attendance_records
        WHEN {_ACTIVE} AND {_DEVICE_SRC % ('OLD', 'OLD')}
        BEGIN
            INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, op, attendance_id)
            SELECT e.employee_number, OLD.check_time, COALESCE(OLD.check_type, 0), OLD.verify_code, OLD.device_id,
                   'DELETE', OLD.id
            FROM employees e WHERE e.id = OLD.employee_id;
        END''',
}


_OLD_TRIGGERS = ('trg_oracle_att_insert', 'trg_oracle_att_update', 'trg_oracle_att_delete')


def _ensure_triggers(conn):
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    for old in _OLD_TRIGGERS:
        if old in have:
            conn.execute(f'DROP TRIGGER IF EXISTS {old}')
    for name, sql in _TRIGGERS.items():
        if name not in have:
            conn.execute(sql)


def _set_state(active=None, ok=False, error=None, conn=None):
    """حالة المزامنة للشاشة، والـflag اللي الـtriggers بتقراه. مابيرميش."""
    own = conn is None
    try:
        c = conn or _local()
        try:
            ensure_schema(c)
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if active is not None:
                cur = c.execute('SELECT active FROM oracle_sync_state WHERE id = 1').fetchone()
                if cur is None or int(cur[0] or 0) != int(bool(active)):
                    c.execute('UPDATE oracle_sync_state SET active = ? WHERE id = 1', (int(bool(active)),))
            if ok:
                c.execute('UPDATE oracle_sync_state SET last_ok_at = ?, last_error = NULL WHERE id = 1', (now,))
            if error:
                c.execute('UPDATE oracle_sync_state SET last_error = ?, last_error_at = ? WHERE id = 1',
                          (str(error)[:500], now))
            c.commit()
        finally:
            if own:
                c.close()
    except Exception:
        pass


def refresh_state():
    """بعد فتح/قفل أوراكل أو تغيير إعداداته: الـtriggers تبدأ/توقف على طول، والعامل يصحى."""
    invalidate_caches()
    _set_state(active=is_oracle_enabled())
    wake()


def add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id, sqlite_conn=None):
    """يحط بصمة في الطابور يدويًا (الـtrigger بيعمل ده لوحده لأي بصمة بتدخل الحضور)."""
    if not is_oracle_enabled():
        return
    try:
        conn = sqlite_conn or get_db_connection()
        conn.execute('''INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, status, op)
                        VALUES (?, ?, ?, ?, ?, 'PENDING', 'INSERT')''',
                     (user_id, check_time, check_type, verify_code, device_id))
        if not sqlite_conn:
            conn.commit()
        wake()
    except Exception as e:
        oracle_logger.error(f"CRITICAL: Failed to add record to offline queue: {e}")


def insert_attendance_to_oracle(user_id, check_time, check_type, verify_code=None, device_id=0):
    """للتوافق: بيحط البصمة في الطابور ويصحّي العامل (اللي بيبعتها في ثواني)."""
    if not is_oracle_enabled():
        return True
    add_to_sync_queue(user_id, check_time, check_type, verify_code, device_id)
    return True


# ================= الإرسال =================

def _sensor_id(conn, raw):
    """SENSORID = رقم الجهاز عندنا. الصفوف القديمة كان فيها IP بدل الرقم."""
    s = str(raw if raw is not None else '').strip()
    if s.isdigit():
        return s[:5]
    if s:
        try:
            r = conn.execute('SELECT id FROM fingerprint_devices WHERE device_ip = ? OR device_name = ?', (s, s)).fetchone()
            if r:
                return str(r[0])
        except Exception:
            pass
        return s.split('.')[-1][:5] or '1'
    return '1'


def _parse_time(t):
    s = str(t).replace('T', ' ')[:19]
    try:
        return datetime.datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return s


def _ora_code(e):
    err = e.args[0] if getattr(e, 'args', None) else None
    return getattr(err, 'code', None), getattr(err, 'message', str(e))


def process_sync_queue(oracle_conn=None, max_batches=50):
    """يبعت الطابور لأوراكل. يُرجع عدد اللي اتبعت، أو False لو أوراكل مقفول/مش بيرد."""
    if not is_oracle_enabled():
        return False
    own_ora = oracle_conn is None
    ora = oracle_conn or get_oracle_connection()
    if ora is None:
        return False
    table = table_name()
    dedupe = str(get_setting('oracle_dedupe', '') or '1').strip() != '0'
    local = _local()
    sent = 0
    try:
        ensure_schema(local)
        cur = ora.cursor()
        for _ in range(max_batches):
            now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            rows = local.execute('''SELECT * FROM oracle_sync_queue
                                    WHERE status = 'PENDING' AND (next_try_at IS NULL OR next_try_at <= ?)
                                    ORDER BY id LIMIT ?''', (now, BATCH)).fetchall()
            if not rows:
                break
            for row in rows:
                op = (row['op'] or 'INSERT').upper()
                user_id = ''.join(ch for ch in str(row['user_id']) if ch.isdigit())
                if not user_id:
                    local.execute("UPDATE oracle_sync_queue SET status = 'SKIPPED', last_error = ? WHERE id = ?",
                                  ('رقم الموظف مش أرقام — جدول أوراكل بياخد أرقام بس', row['id']))
                    continue
                t = _parse_time(row['check_time'])
                try:
                    if op == 'DELETE':
                        # بنمسح بس اللي إحنا حطّيناه هناك.
                        ours = local.execute('''SELECT 1 FROM oracle_sync_queue WHERE user_id = ? AND check_time = ?
                                                AND op = 'INSERT' AND status = 'SYNCED' AND id < ?''',
                                             (row['user_id'], row['check_time'], row['id'])).fetchone()
                        # ولو لسه ماتبعتتش: تتلغي بدل ما تتبعت.
                        local.execute('''UPDATE oracle_sync_queue SET status = 'CANCELLED'
                                         WHERE user_id = ? AND check_time = ? AND op = 'INSERT'
                                           AND status = 'PENDING' AND id < ?''',
                                      (row['user_id'], row['check_time'], row['id']))
                        if ours:
                            cur.execute(f"DELETE FROM {table} WHERE USERID = :1 AND CHECKTIME = :2", (user_id, t))
                        status = 'SYNCED' if ours else 'SKIPPED'
                    else:
                        exists = False
                        if dedupe:
                            cur.execute(f"SELECT 1 FROM {table} WHERE USERID = :1 AND CHECKTIME = :2", (user_id, t))
                            exists = cur.fetchone() is not None
                        if not exists:
                            cur.execute(f"""INSERT INTO {table} (USERID, CHECKTIME, CHECKTYPE, VERIFYCODE, SENSORID, WORKCODE)
                                            VALUES (:1, :2, :3, :4, :5, :6)""",
                                        (user_id, t, str(row['check_type'])[:2], row['verify_code'],
                                         _sensor_id(local, row['device_id']), 1))
                        status = 'SYNCED'
                    ora.commit()
                    local.execute('UPDATE oracle_sync_queue SET status = ?, synced_at = ?, last_error = NULL WHERE id = ?',
                                  (status, now, row['id']))
                    sent += 1
                except oracledb.IntegrityError:
                    # موجودة أصلًا (Unique) — تعتبر اتبعتت.
                    try:
                        ora.rollback()
                    except Exception:
                        pass
                    local.execute("UPDATE oracle_sync_queue SET status = 'SYNCED', synced_at = ? WHERE id = ?",
                                  (now, row['id']))
                    sent += 1
                except oracledb.DatabaseError as e:
                    code, msg = _ora_code(e)
                    try:
                        ora.rollback()
                    except Exception:
                        pass
                    if code in _PERMANENT_ORA:
                        # الجدول أو العمود أو نوع البيانات غلط: محاولة تانية مش هتفرق.
                        local.execute("UPDATE oracle_sync_queue SET status = 'FAILED', last_error = ? WHERE id = ?",
                                      (f'ORA-{code}: {msg}'[:500], row['id']))
                        oracle_logger.error(f"Oracle rejected record {user_id} @ {row['check_time']}: ORA-{code} {msg}")
                        continue
                    # مشكلة اتصال: نوقف الدورة ونرجع بعدين.
                    _retry_later(local, row, f'ORA-{code}: {msg}')
                    local.commit()
                    raise
                except Exception as e:
                    _retry_later(local, row, str(e))
            local.commit()
        if sent:
            _set_state(ok=True, conn=local)
            oracle_logger.info(f"Synced {sent} record(s) to Oracle.")
        else:
            _set_state(ok=True, conn=local)
        return sent
    except Exception as e:
        _set_state(error=str(e)[:300], conn=local)
        oracle_logger.error(f"Error processing sync queue: {e}")
        return False
    finally:
        try:
            local.close()
        except Exception:
            pass
        if own_ora:
            _close(ora)


def _retry_later(local, row, error):
    n = int(row['retry_count'] or 0) + 1
    if n >= MAX_RETRIES:
        local.execute("UPDATE oracle_sync_queue SET status = 'FAILED', retry_count = ?, last_error = ? WHERE id = ?",
                      (n, str(error)[:500], row['id']))
        return
    wait = min(30 * (2 ** min(n, 7)), 3600)
    nxt = (datetime.datetime.now() + datetime.timedelta(seconds=wait)).strftime('%Y-%m-%d %H:%M:%S')
    local.execute('UPDATE oracle_sync_queue SET retry_count = ?, next_try_at = ?, last_error = ? WHERE id = ?',
                  (n, nxt, str(error)[:500], row['id']))


# ================= العامل في الخلفية =================
_wake = threading.Event()
_worker = {'started': False}


def wake():
    """صحّي العامل يبعت دلوقتي (بعد ما بصمات جديدة دخلت)."""
    _wake.set()


CATCHUP_DAYS = 3


def _worker_loop():
    ora = None
    down_said = False
    was_on = False
    time.sleep(5)
    while True:
        try:
            enabled = is_oracle_enabled()
            _set_state(active=enabled)
            if enabled and not was_on:
                # أول ما المزامنة تشتغل (تشغيل البرنامج، أو فتحها من الشاشة): بصمات آخر ٣ أيام اللي
                # ماراحتش أوراكل — زي بصمات أجهزة IP قبل ٢.٣٧.٢٠ — تتحط في الطابور.
                try:
                    since = (datetime.datetime.now() - datetime.timedelta(days=CATCHUP_DAYS)).strftime('%Y-%m-%d')
                    n = backfill(since)
                    if n:
                        oracle_logger.info(f"Catch-up: {n} punch(es) from the last {CATCHUP_DAYS} days queued for Oracle.")
                except Exception as e:
                    oracle_logger.error(f"Oracle catch-up failed: {e}")
            was_on = enabled
            if enabled:
                if ora is None:
                    ora = get_oracle_connection()
                    if ora is None and not down_said:
                        oracle_logger.error("Oracle unreachable — punches stay queued and are sent when it is back.")
                        down_said = True
                if ora is not None:
                    if down_said:
                        oracle_logger.info("Oracle is reachable again — sending the queue.")
                        down_said = False
                    if process_sync_queue(ora) is False:
                        _close(ora)
                        ora = None
            elif ora is not None:
                _close(ora)
                ora = None
        except Exception as e:
            oracle_logger.error(f"Oracle worker error: {e}")
            _close(ora)
            ora = None
        _wake.wait(SYNC_SECONDS)
        _wake.clear()


def start_oracle_heartbeat():
    """العامل اللي بيبعت الطابور أول بأول (اسم الدالة القديم للتوافق) — مرة واحدة لكل تشغيل."""
    if _worker['started']:
        return
    _worker['started'] = True
    threading.Thread(target=_worker_loop, name='مزامنة-أوراكل', daemon=True).start()




# ================= للشاشات =================

def backfill(since=None, conn=None):
    """«مزامنة الناقص»: بصمات أجهزة في الحضور ماتحطّتش في الطابور قبل كده (من `since` لو اتحدد)."""
    own = conn is None
    c = conn or _local()
    try:
        ensure_schema(c)
        args = []
        where = ''
        if since:
            where = 'AND ar.check_time >= ?'
            args.append(str(since))
        cur = c.execute(f'''INSERT INTO oracle_sync_queue (user_id, check_time, check_type, verify_code, device_id, op, attendance_id)
                            SELECT e.employee_number, ar.check_time, COALESCE(ar.check_type, 0), ar.verify_code,
                                   ar.device_id, 'INSERT', ar.id
                            FROM attendance_records ar JOIN employees e ON e.id = ar.employee_id
                            WHERE (ar.source IS NULL OR ar.source IN {_SOURCES}) {where}
                              AND NOT EXISTS (SELECT 1 FROM oracle_sync_queue q
                                              WHERE q.user_id = e.employee_number AND q.check_time = ar.check_time
                                                AND q.op = 'INSERT' AND q.status IN ('PENDING', 'SYNCED'))''', args)
        c.commit()
        wake()
        return cur.rowcount
    finally:
        if own:
            c.close()


def retry_failed(conn=None):
    """«إعادة المحاولة»: الفاشل والمستني يتبعتوا دلوقتي."""
    own = conn is None
    c = conn or _local()
    try:
        ensure_schema(c)
        cur = c.execute("""UPDATE oracle_sync_queue SET status = 'PENDING', retry_count = 0, next_try_at = NULL
                           WHERE status = 'FAILED' OR (status = 'PENDING' AND next_try_at IS NOT NULL)""")
        c.commit()
        wake()
        return cur.rowcount
    finally:
        if own:
            c.close()


def get_sync_queue_stats():
    """أعداد الطابور وحالة المزامنة للشاشات."""
    try:
        conn = get_db_connection()
        ensure_schema(conn)
        r = conn.execute('''SELECT
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN status = 'SYNCED' THEN 1 ELSE 0 END) AS synced,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN status = 'PENDING' AND retry_count > 0 THEN 1 ELSE 0 END) AS retrying,
                COUNT(*) AS total
            FROM oracle_sync_queue''').fetchone()
        st = conn.execute('SELECT * FROM oracle_sync_state WHERE id = 1').fetchone()
        return {'pending': r['pending'] or 0, 'synced': r['synced'] or 0, 'failed': r['failed'] or 0,
                'retrying': r['retrying'] or 0, 'total': r['total'] or 0,
                'active': bool(st['active']) if st else False,
                'last_ok_at': st['last_ok_at'] if st else None,
                'last_error': st['last_error'] if st else None,
                'last_error_at': st['last_error_at'] if st else None,
                'interval_seconds': SYNC_SECONDS}
    except Exception as e:
        oracle_logger.error(f"Error getting sync stats: {e}")
        return {'pending': 0, 'synced': 0, 'failed': 0, 'retrying': 0, 'total': 0, 'active': False,
                'last_ok_at': None, 'last_error': None, 'last_error_at': None, 'interval_seconds': SYNC_SECONDS}
