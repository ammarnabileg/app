# -*- coding: utf-8 -*-
"""«موظّفٌ جديد على جهاز البصمة الذي في المكان الفلاني — كمّل بياناته».

موظّفٌ يُسجَّل على الجهاز فيُضاف للبرنامج وحده (مزامنة المستخدمين، أو ADMS) بقسمٍ
«غير محدد» وراتبٍ صفر. كان يُضاف بصمت: لا أحد يعرف أنّ عليه إكمالَ بياناته حتى
يظهر صفرًا في كشف الرواتب. الآن يُقيَّد هنا ومعه الجهازُ ومكانُه، ويظهر في شريط
البرنامج العلويّ (جرس «موظفين جداد») حتى تُحفظ بياناتُه من شاشة التعديل أو يُضغط «تم».
"""
import logging

logger = logging.getLogger(__name__)


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS device_new_employees (
        employee_id INTEGER PRIMARY KEY,
        employee_number TEXT,
        device_id INTEGER,
        device_name TEXT,
        location TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        done_at TEXT,
        done_by TEXT
    )''')


def _device_place(conn, device_id):
    """(اسم الجهاز، مكانه) — المكان: فرعُ الجهاز إن ضُبط، وإلّا عنوانه."""
    if not device_id:
        return '', ''
    try:
        cols = [c[1] for c in conn.execute('PRAGMA table_info(fingerprint_devices)').fetchall()]
        pick = ['device_name', 'device_ip'] + [c for c in ('branch_name', 'branch_id') if c in cols]
        row = conn.execute(f"SELECT {', '.join(pick)} FROM fingerprint_devices WHERE id = ?",
                           (device_id,)).fetchone()
    except Exception:
        row = None
    if not row:
        return '', ''
    vals = dict(zip(pick, tuple(row)))
    place = (vals.get('branch_name') or '').strip()
    if not place and vals.get('branch_id'):
        try:
            b = conn.execute('SELECT name FROM branches WHERE id = ?', (vals['branch_id'],)).fetchone()
            place = (b[0] if b else '') or ''
        except Exception:
            place = ''
    return vals.get('device_name') or '', place or (vals.get('device_ip') or '')


def record(conn, employee_id, device_id):
    """موظّفٌ أُضيف وحده من جهاز. لا يرمي."""
    if not employee_id:
        return False
    try:
        ensure_schema(conn)
        num = conn.execute('SELECT employee_number FROM employees WHERE id = ?', (employee_id,)).fetchone()
        name, place = _device_place(conn, device_id)
        cur = conn.execute('''INSERT OR IGNORE INTO device_new_employees
            (employee_id, employee_number, device_id, device_name, location) VALUES (?, ?, ?, ?, ?)''',
                           (employee_id, num[0] if num else None, device_id, name, place))
        return cur.rowcount > 0
    except Exception as e:                      # noqa: BLE001
        logger.warning(f'new device employee {employee_id}: {e}')
        return False


def mark_done(conn, employee_id, by=None):
    ensure_schema(conn)
    conn.execute('''UPDATE device_new_employees SET done_at = CURRENT_TIMESTAMP, done_by = ?
                    WHERE employee_id = ? AND done_at IS NULL''', (by, employee_id))
    conn.commit()


def open_items(conn, limit=50):
    """مَن لم تُكمَّل بياناتُه بعد (والموظّفُ ما زال موجودًا)."""
    ensure_schema(conn)
    rows = conn.execute('''SELECT d.employee_id, d.employee_number, d.device_name, d.location,
                                  datetime(d.created_at, 'localtime') AS created_local, e.name
                           FROM device_new_employees d JOIN employees e ON e.id = d.employee_id
                           WHERE d.done_at IS NULL ORDER BY d.created_at DESC, d.employee_id DESC LIMIT ?''',
                        (limit,)).fetchall()
    return [{'id': r[0], 'number': r[1], 'device': r[2] or '', 'location': r[3] or '',
             'created': r[4] or '', 'name': r[5] or ''} for r in rows]


def count_open(conn):
    ensure_schema(conn)
    return conn.execute('''SELECT COUNT(*) FROM device_new_employees d JOIN employees e ON e.id = d.employee_id
                           WHERE d.done_at IS NULL''').fetchone()[0]


# ------------------------------------------------------------ صفحة «كمّل بياناتهم»

_PLACEHOLDER_DEPTS = {'', 'general', 'غير محدد', 'عام', 'none'}
_PLACEHOLDER_POS = {'', 'employee', 'none', 'موظف'}


def _blank(v):
    return v is None or str(v).strip() in ('', 'None', '0', '0.0')


def missing_fields(e):
    """(الأساسي الناقص، الإضافي الناقص) — بالعربي، لموظف (صف employees)."""
    keys = e.keys() if hasattr(e, 'keys') else e
    g = lambda k: (e[k] if k in keys else None)
    num = str(g('employee_number') or '').strip()
    name = str(g('name') or '').strip()
    must, nice = [], []
    if not name or name in ('New User', num) or name.startswith('مستخدم_'):
        must.append('الاسم')
    if str(g('department') or '').strip().lower() in _PLACEHOLDER_DEPTS:
        must.append('القسم')
    if str(g('position') or '').strip().lower() in _PLACEHOLDER_POS:
        must.append('الوظيفة')
    if _blank(g('salary')):
        must.append('الراتب')
    if _blank(g('national_id')):
        must.append('الرقم المدني')
    if _blank(g('branch_location')):
        must.append('الفرع')
    if _blank(g('weekly_leave_selected_days')) and _blank(g('weekly_leave_days')):
        must.append('الإجازة الأسبوعية')
    for k, label in (('nationality', 'الجنسية'), ('phone', 'التليفون'), ('dob', 'تاريخ الميلاد'),
                     ('gender', 'النوع'), ('shift_type', 'الشفت'), ('contract_type', 'نوع العقد'),
                     ('bank_iban', 'الآيبان'), ('arabic_name', 'الاسم بالعربي')):
        if _blank(g(k)):
            nice.append(label)
    return must, nice


def page_items(conn, scope='new'):
    """صفوف الصفحة. scope: 'new' اللي اتضافوا من الأجهزة ولسه ماتكمّلوش، 'all_new' كل اللي اتضافوا
    من الأجهزة، 'all' كل الموظفين النشطين اللي بياناتهم الأساسية ناقصة."""
    ensure_schema(conn)
    if scope == 'all':
        rows = conn.execute('''SELECT e.*, d.device_name AS dev_name, d.location AS dev_location,
                                      datetime(d.created_at, 'localtime') AS dev_created, d.done_at AS dev_done
                               FROM employees e LEFT JOIN device_new_employees d ON d.employee_id = e.id
                               WHERE COALESCE(e.is_active, 1) = 1
                               ORDER BY CAST(e.employee_number AS INTEGER)''').fetchall()
    else:
        rows = conn.execute(f'''SELECT e.*, d.device_name AS dev_name, d.location AS dev_location,
                                       datetime(d.created_at, 'localtime') AS dev_created, d.done_at AS dev_done
                                FROM device_new_employees d JOIN employees e ON e.id = d.employee_id
                                {"WHERE d.done_at IS NULL" if scope == 'new' else ""}
                                ORDER BY d.created_at DESC''').fetchall()
    out = []
    for r in rows:
        must, nice = missing_fields(r)
        if scope == 'all' and not must:
            continue
        out.append({'id': r['id'], 'number': r['employee_number'], 'name': r['name'] or '',
                    'active': bool(r['is_active']), 'device': r['dev_name'] or '', 'location': r['dev_location'] or '',
                    'created': r['dev_created'] or '', 'done': bool(r['dev_done']),
                    'must': must, 'nice': nice})
    return out
