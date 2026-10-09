# -*- coding: utf-8 -*-
"""متابعة الموظّفين (خارطة الطريق، المرحلة ٥): الوثائق قبل ما تنتهي، وقوائم الاستلام والتسليم.

## الوثائق

البطاقة المدنيّة والجواز والإقامة ونهاية العقد (من ملفّ الموظّف) والمستندات المرفوعة
بتاريخ انتهاء. التنبيه مرّة عند كلّ عتبة (٣٠ يوم، ٧ أيام، يوم الانتهاء) — لمن له صلاحية
`employee.edit` وللموظّف نفسه — ولا يتكرّر لنفس الوثيقة ونفس التاريخ. تجديد الوثيقة
(تاريخ جديد) يبدأ العدّ من أوّل.

## القوائم

قالبان: «استلام» لموظّفٍ جديد و«تسليم» لمن تنتهي خدمته. القائمة نسخةٌ من بنود القالب
وقتَ بدئها؛ تعديل القالب بعدها لا يغيّر قوائم بدأت.
"""
import json
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

PERMISSION = 'employee.edit'
MILESTONES = (30, 7, 0)
DAILY_SETTING = 'doc_expiry_last_run'

EMPLOYEE_DOCS = (
    ('national_id_expiry_date', 'البطاقة المدنية'),
    ('passport_expiry_date', 'جواز السفر'),
    ('residency_expiry_date', 'الإقامة'),
    ('contract_end_date', 'العقد'),
)

KINDS = {'onboarding': 'استلام موظف جديد', 'offboarding': 'تسليم وإنهاء خدمة'}
OWNERS = {'hr': 'الموارد البشرية', 'manager': 'المدير المباشر', 'it': 'تقنية المعلومات',
          'finance': 'الحسابات', 'employee': 'الموظف'}
DEFAULT_TEMPLATES = {
    'onboarding': [
        ('hr', 'العقد موقّع ونسخة في الملف'), ('hr', 'صورة البطاقة المدنية والجواز'),
        ('hr', 'تسجيل الموظف على جهاز البصمة (صباع/وش)'), ('hr', 'حساب بوابة الموظف'),
        ('finance', 'رقم الحساب البنكي (IBAN)'), ('it', 'جهاز / إيميل / صلاحيات'),
        ('manager', 'تعريف بالفريق ومهام أول أسبوع'), ('employee', 'قراءة اللائحة الداخلية'),
    ],
    'offboarding': [
        ('manager', 'استلام العهدة (لابتوب، مفاتيح، كارت)'), ('it', 'قفل الإيميل والصلاحيات'),
        ('hr', 'مسح الموظف من أجهزة البصمة'), ('hr', 'إيقاف حساب البوابة'),
        ('finance', 'تسوية السلف والمستحقات'), ('finance', 'حساب مكافأة نهاية الخدمة'),
        ('hr', 'شهادة الخبرة'), ('hr', 'إخلاء الطرف موقّع'),
    ],
}


def ensure_schema(conn):
    cols = {r[1] for r in conn.execute('PRAGMA table_info(employees)')}
    for col in ('residency_number TEXT', 'residency_expiry_date DATE'):
        if col.split()[0] not in cols:
            conn.execute(f'ALTER TABLE employees ADD COLUMN {col}')
    conn.execute('''CREATE TABLE IF NOT EXISTS doc_expiry_notices (
        doc_key TEXT NOT NULL, expiry TEXT NOT NULL, milestone INTEGER NOT NULL,
        sent_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (doc_key, expiry, milestone))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS checklist_templates (
        kind TEXT PRIMARY KEY, items TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER NOT NULL, kind TEXT NOT NULL,
        created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, completed_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS employee_checklist_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT, checklist_id INTEGER NOT NULL, position INTEGER,
        owner TEXT, title TEXT NOT NULL, done INTEGER DEFAULT 0, done_by TEXT, done_at TEXT, note TEXT)''')
    for kind, items in DEFAULT_TEMPLATES.items():
        conn.execute('INSERT OR IGNORE INTO checklist_templates (kind, items) VALUES (?, ?)',
                     (kind, json.dumps([{'owner': o, 'title': t} for o, t in items], ensure_ascii=False)))
    conn.commit()


# ------------------------------------------------------------ الوثائق

def _d(v):
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def expiring(conn, within=60, today=None):
    """[{key, employee_id, employee_name, employee_number, doc, expiry, days}] — المنتهي وما ينتهي خلال `within`."""
    ensure_schema(conn)
    today = today or date.today()
    limit = today + timedelta(days=within)
    out = []
    emps = conn.execute('SELECT * FROM employees WHERE COALESCE(is_active, 1) = 1').fetchall()
    for e in emps:
        e = dict(e)
        for col, label in EMPLOYEE_DOCS:
            d = _d(e.get(col))
            if d and d <= limit:
                out.append({'key': f'emp:{e["id"]}:{col}', 'employee_id': e['id'], 'employee_name': e['name'],
                            'employee_number': e['employee_number'], 'department': e.get('department'),
                            'doc': label, 'expiry': d.isoformat(), 'days': (d - today).days})
    try:
        rows = conn.execute('''SELECT d.id, d.document_type, d.expiry_date, e.id AS emp, e.name, e.employee_number,
                               e.department FROM documents d JOIN employees e ON e.id = d.employee_id
                               WHERE d.expiry_date IS NOT NULL AND d.expiry_date != ''
                               AND COALESCE(e.is_active, 1) = 1''').fetchall()
    except Exception:
        rows = []
    for r in rows:
        d = _d(r['expiry_date'])
        if d and d <= limit:
            out.append({'key': f'doc:{r["id"]}', 'employee_id': r['emp'], 'employee_name': r['name'],
                        'employee_number': r['employee_number'], 'department': r['department'],
                        'doc': r['document_type'] or 'مستند', 'expiry': d.isoformat(), 'days': (d - today).days})
    out.sort(key=lambda x: x['days'])
    return out


def _milestone(days):
    """أصغر عتبة وصلها: 30 → 7 → 0. None = لسه بدري."""
    hit = [m for m in MILESTONES if days <= m]
    return min(hit) if hit else None


def _hr_users(conn):
    return [r[0] for r in conn.execute('''SELECT DISTINCT u.id FROM users u
        JOIN user_roles ur ON ur.user_id = u.id JOIN role_permissions rp ON rp.role_id = ur.role_id
        JOIN permissions p ON p.id = rp.permission_id WHERE p.code = ? AND COALESCE(u.is_active, 1) = 1''',
                                         (PERMISSION,))]


def notify_due(conn, today=None):
    """يبعت تنبيهات العتبات اللي وصلت ولسه ما اتبعتتش. يُرجع عددها."""
    from utils import notifications
    today = today or date.today()
    hr = _hr_users(conn)
    sent = 0
    for it in expiring(conn, within=max(MILESTONES), today=today):
        m = _milestone(it['days'])
        if m is None:
            continue
        cur = conn.execute('INSERT OR IGNORE INTO doc_expiry_notices (doc_key, expiry, milestone) VALUES (?, ?, ?)',
                           (it['key'], it['expiry'], m))
        if not cur.rowcount:
            continue
        # العتبات الأكبر اللي فاتت (مثلًا أُضيف التاريخ وهو قريب) تتسجّل كأنها اتبعتت — تنبيه واحد يكفي.
        for bigger in MILESTONES:
            if bigger > m:
                conn.execute('INSERT OR IGNORE INTO doc_expiry_notices (doc_key, expiry, milestone) VALUES (?, ?, ?)',
                             (it['key'], it['expiry'], bigger))
        when = 'انتهت' if it['days'] < 0 else ('تنتهي النهارده' if it['days'] == 0 else f'تنتهي بعد {it["days"]} يوم')
        title = f'{it["doc"]} — {when}'
        for uid in hr:
            notifications.notify(conn, uid, 'doc_expiry', title, f'{it["employee_name"]} ({it["employee_number"]}) — {it["expiry"]}',
                                 target='home', ref_type='employee', ref_id=it['employee_id'])
        notifications.notify_employee(conn, it['employee_id'], 'doc_expiry', f'{it["doc"]} بتاعتك {when}',
                                      f'تاريخ الانتهاء {it["expiry"]} — جدّدها وبلّغ الموارد البشرية',
                                      target='profile', ref_type='employee', ref_id=it['employee_id'])
        sent += 1
    conn.commit()
    return sent


def run_daily(conn=None):
    """مرّة في اليوم بالكتير — يُنادى من المزامنة الدوريّة ومن فتح الرئيسيّة. لا يرمي."""
    owned = conn is None
    try:
        from utils.db import get_db_connection, get_setting, set_setting
        today = date.today().isoformat()
        if get_setting(DAILY_SETTING, '') == today:
            return 0
        set_setting(DAILY_SETTING, today)
        if owned:
            conn = get_db_connection()
        return notify_due(conn)
    except Exception as e:                      # noqa: BLE001
        logger.warning(f'doc expiry: {e}')
        return 0


# ------------------------------------------------------------ القوائم

def template(conn, kind):
    ensure_schema(conn)
    r = conn.execute('SELECT items FROM checklist_templates WHERE kind = ?', (kind,)).fetchone()
    try:
        return json.loads(r[0]) if r else []
    except Exception:
        return []


def save_template(conn, kind, items):
    if kind not in KINDS:
        raise ValueError('نوع غير معروف')
    clean = [{'owner': i.get('owner') if i.get('owner') in OWNERS else 'hr', 'title': i['title'].strip()[:200]}
             for i in items if (i.get('title') or '').strip()]
    conn.execute('UPDATE checklist_templates SET items = ?, updated_at = CURRENT_TIMESTAMP WHERE kind = ?',
                 (json.dumps(clean, ensure_ascii=False), kind))
    conn.commit()


def start(conn, employee_id, kind, user_id=None):
    """قائمة جديدة من القالب — أو رقم القائمة المفتوحة لو فيه واحدة من نفس النوع."""
    if kind not in KINDS:
        raise ValueError('نوع غير معروف')
    ensure_schema(conn)
    r = conn.execute('SELECT id FROM employee_checklists WHERE employee_id = ? AND kind = ? AND completed_at IS NULL',
                     (employee_id, kind)).fetchone()
    if r:
        return r[0]
    cid = conn.execute('INSERT INTO employee_checklists (employee_id, kind, created_by) VALUES (?, ?, ?)',
                       (employee_id, kind, user_id)).lastrowid
    for i, it in enumerate(template(conn, kind)):
        conn.execute('INSERT INTO employee_checklist_items (checklist_id, position, owner, title) VALUES (?, ?, ?, ?)',
                     (cid, i, it['owner'], it['title']))
    conn.commit()
    return cid


def toggle(conn, item_id, done, user_name, note=None):
    r = conn.execute('SELECT checklist_id FROM employee_checklist_items WHERE id = ?', (item_id,)).fetchone()
    if not r:
        return None
    conn.execute('UPDATE employee_checklist_items SET done = ?, done_by = ?, done_at = ?, note = COALESCE(?, note) '
                 'WHERE id = ?', (1 if done else 0, user_name if done else None,
                                  datetime.now().strftime('%Y-%m-%d %H:%M') if done else None, note, item_id))
    left = conn.execute('SELECT COUNT(*) FROM employee_checklist_items WHERE checklist_id = ? AND done = 0',
                        (r[0],)).fetchone()[0]
    conn.execute('UPDATE employee_checklists SET completed_at = ? WHERE id = ?',
                 (None if left else datetime.now().strftime('%Y-%m-%d %H:%M'), r[0]))
    conn.commit()
    return r[0]


def checklists(conn, open_only=True):
    ensure_schema(conn)
    sql = '''SELECT c.*, e.name AS employee_name, e.employee_number, e.department,
             (SELECT COUNT(*) FROM employee_checklist_items i WHERE i.checklist_id = c.id) AS total,
             (SELECT COUNT(*) FROM employee_checklist_items i WHERE i.checklist_id = c.id AND i.done = 1) AS done
             FROM employee_checklists c JOIN employees e ON e.id = c.employee_id'''
    if open_only:
        sql += ' WHERE c.completed_at IS NULL'
    out = []
    for r in conn.execute(sql + ' ORDER BY c.id DESC').fetchall():
        d = dict(r)
        d['kind_label'] = KINDS.get(d['kind'], d['kind'])
        d['items'] = [dict(i) for i in conn.execute(
            'SELECT * FROM employee_checklist_items WHERE checklist_id = ? ORDER BY position, id', (d['id'],))]
        out.append(d)
    return out


def suggestions(conn, today=None):
    """موظّفين محتاجين قائمة ولسه ما بدأتش: جديد (آخر ٦٠ يوم) أو خدمته بتنتهي (±٣٠ يوم)."""
    ensure_schema(conn)
    today = today or date.today()
    has = {(r[0], r[1]) for r in conn.execute('SELECT employee_id, kind FROM employee_checklists')}
    out = []
    for e in conn.execute('SELECT id, name, employee_number, hire_date, end_of_service_date, is_active FROM employees'):
        hd, eos = _d(e['hire_date']), _d(e['end_of_service_date'])
        if hd and e['is_active'] and 0 <= (today - hd).days <= 60 and (e['id'], 'onboarding') not in has:
            out.append({'employee_id': e['id'], 'name': e['name'], 'number': e['employee_number'],
                        'kind': 'onboarding', 'why': f'اتعيّن {hd.isoformat()}'})
        if eos and abs((eos - today).days) <= 30 and (e['id'], 'offboarding') not in has:
            out.append({'employee_id': e['id'], 'name': e['name'], 'number': e['employee_number'],
                        'kind': 'offboarding', 'why': f'نهاية الخدمة {eos.isoformat()}'})
    return out
