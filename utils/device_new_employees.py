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
