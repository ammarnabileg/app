# -*- coding: utf-8 -*-
"""طلبات الموظّفين ومسار الموافقات (خارطة الطريق، المرحلة ٥).

## الأنواع

| النوع | ماذا يحدث عند الموافقة الأخيرة |
|---|---|
| `letter`   خطاب (شهادة راتب، لمن يهمه الأمر، خبرة…) | يُرقَّم ويصير جاهزًا للطباعة من البوابة وشاشة الموارد البشريّة |
| `expense`  مصروف بفاتورة (أو بدل مسافة)             | بندُ «استرداد مصاريف» في رواتب أوّل شهرٍ غير مقفول |
| `loan`     سلفة                                     | سلفةٌ في «السلف» تبدأ أقساطُها الشهرَ التالي |
| `overtime` أوفرتايم مسبق                            | موافقةٌ مسجَّلة تظهر في شاشة الطلبات (الحساب من الحضور كما هو) |

الإجازات والاستئذانات **لا تمرّ من هنا** — مسارُها القائم كما هو.

## مسار الموافقة

لكلّ نوعٍ سلسلةُ خطوات تُضبط من الإعدادات، كلُّ خطوةٍ واحدةٌ من:
`manager` (المدير المباشر) · `dept_manager` (مدير القسم) · `hr` (من له صلاحية
`hr.requests`) · `user:<رقم>` (مستخدمٌ بعينه). خطوةٌ لا أحد يقوم بها (موظّفٌ بلا مدير
مثلًا) تُتخطّى — لا يعلق الطلبُ عند أحدٍ غير موجود. والرفضُ في أيّ خطوة يُنهي الطلب.
"""
import json
import logging
import os
from datetime import date, datetime

logger = logging.getLogger(__name__)

TYPES = {
    'letter': 'خطاب',
    'expense': 'مصروف',
    'loan': 'سلفة',
    'overtime': 'أوفرتايم',
}
DEFAULT_CHAINS = {
    'letter': ['hr'],
    'expense': ['manager', 'hr'],
    'loan': ['manager', 'hr'],
    'overtime': ['manager'],
}
STEP_LABELS = {'manager': 'المدير المباشر', 'dept_manager': 'مدير القسم', 'hr': 'الموارد البشرية'}
STATUS_LABELS = {'pending': 'قيد المراجعة', 'approved': 'معتمد', 'rejected': 'مرفوض', 'cancelled': 'ملغي'}
PERMISSION = 'hr.requests'

DEFAULT_LETTERS = [
    ('salary_certificate', 'شهادة راتب', """<p>تشهد {{company_name}} بأن السيد/ة <b>{{name}}</b>، {{nationality}} الجنسية، ويحمل/تحمل البطاقة المدنية رقم <b>{{civil_id}}</b>، يعمل/تعمل لديها بوظيفة <b>{{position}}</b> في قسم {{department}} منذ تاريخ <b>{{hire_date}}</b>، ويتقاضى/تتقاضى راتبًا شهريًا إجماليًا قدره <b>{{total_salary}}</b>.</p>
<p>وقد أُعطيت هذه الشهادة بناءً على طلبه/طلبها لتقديمها إلى <b>{{addressee}}</b>، دون أدنى مسؤولية على الشركة.</p>"""),
    ('to_whom', 'لمن يهمه الأمر', """<p>تفيد {{company_name}} بأن السيد/ة <b>{{name}}</b>، {{nationality}} الجنسية، ويحمل/تحمل البطاقة المدنية رقم <b>{{civil_id}}</b>، يعمل/تعمل لديها بوظيفة <b>{{position}}</b> منذ تاريخ <b>{{hire_date}}</b> ولا يزال/تزال على رأس عمله/عملها حتى تاريخه.</p>
<p>وقد أُعطيت هذه الإفادة بناءً على طلبه/طلبها لتقديمها إلى <b>{{addressee}}</b>.</p>"""),
    ('experience', 'شهادة خبرة', """<p>تشهد {{company_name}} بأن السيد/ة <b>{{name}}</b>، {{nationality}} الجنسية، عمل/عملت لديها بوظيفة <b>{{position}}</b> في قسم {{department}} اعتبارًا من <b>{{hire_date}}</b>{{end_text}}، وكان/كانت خلال فترة عمله/عملها مثالًا للجدية والالتزام.</p>
<p>وقد أُعطيت هذه الشهادة بناءً على طلبه/طلبها، دون أدنى مسؤولية على الشركة.</p>"""),
]


# ------------------------------------------------------------ المخطّط

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS hr_request_types (
        code TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1, chain TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS hr_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        payload TEXT,
        status TEXT DEFAULT 'pending',
        current_step INTEGER DEFAULT 0,
        result_ref TEXT,
        created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS hr_request_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id INTEGER NOT NULL,
        step_no INTEGER NOT NULL,
        approver TEXT NOT NULL,
        status TEXT DEFAULT 'waiting',
        decided_by INTEGER,
        decided_by_name TEXT,
        decided_at TEXT,
        note TEXT,
        UNIQUE (request_id, step_no))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS letter_templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE, name TEXT NOT NULL, body TEXT NOT NULL,
        is_active INTEGER DEFAULT 1, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_hr_requests_emp ON hr_requests (employee_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_hr_requests_status ON hr_requests (status)')
    for code in TYPES:
        conn.execute('INSERT OR IGNORE INTO hr_request_types (code, enabled, chain) VALUES (?, 1, ?)',
                     (code, json.dumps(DEFAULT_CHAINS[code])))
    for code, name, body in DEFAULT_LETTERS:
        conn.execute('INSERT OR IGNORE INTO letter_templates (code, name, body) VALUES (?, ?, ?)', (code, name, body))
    conn.commit()


# ------------------------------------------------------------ الإعدادات

def type_settings(conn):
    ensure_schema(conn)
    out = {}
    for r in conn.execute('SELECT code, enabled, chain FROM hr_request_types').fetchall():
        try:
            chain = json.loads(r[2] or '[]')
        except Exception:
            chain = DEFAULT_CHAINS.get(r[0], ['hr'])
        out[r[0]] = {'code': r[0], 'label': TYPES.get(r[0], r[0]), 'enabled': bool(r[1]), 'chain': chain}
    return out


def save_type(conn, code, enabled, chain):
    if code not in TYPES:
        raise ValueError('نوع غير معروف')
    chain = [c for c in chain if c in STEP_LABELS or str(c).startswith('user:')] or ['hr']
    conn.execute('UPDATE hr_request_types SET enabled = ?, chain = ? WHERE code = ?',
                 (1 if enabled else 0, json.dumps(chain), code))
    conn.commit()


def letter_templates(conn, active_only=True):
    ensure_schema(conn)
    sql = 'SELECT * FROM letter_templates'
    if active_only:
        sql += ' WHERE is_active = 1'
    return [dict(r) for r in conn.execute(sql + ' ORDER BY id').fetchall()]


# ------------------------------------------------------------ من يقرّر

def _users_with_permission(conn, code):
    rows = conn.execute('''SELECT DISTINCT u.id FROM users u
                           JOIN user_roles ur ON ur.user_id = u.id
                           JOIN role_permissions rp ON rp.role_id = ur.role_id
                           JOIN permissions p ON p.id = rp.permission_id
                           WHERE p.code = ? AND COALESCE(u.is_active, 1) = 1''', (code,)).fetchall()
    return {r[0] for r in rows}


def approvers(conn, approver, employee_id):
    """أرقامُ حسابات من يقوم بخطوة — مجموعةٌ فارغة = تُتخطّى."""
    emp = conn.execute('SELECT manager_id, department FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if approver == 'manager':
        if not emp or not emp[0]:
            return set()
        return {r[0] for r in conn.execute('SELECT id FROM users WHERE employee_id = ? AND COALESCE(is_active, 1) = 1',
                                           (emp[0],))}
    if approver == 'dept_manager':
        if not emp or not emp[1]:
            return set()
        return {r[0] for r in conn.execute('''SELECT u.id FROM users u JOIN departments_master d
                                              ON d.id = u.managed_department_id
                                              WHERE d.name = ? AND COALESCE(u.is_active, 1) = 1''', (emp[1],))}
    if approver == 'hr':
        return _users_with_permission(conn, PERMISSION)
    if str(approver).startswith('user:'):
        try:
            uid = int(str(approver).split(':', 1)[1])
        except ValueError:
            return set()
        return {uid} if conn.execute('SELECT 1 FROM users WHERE id = ?', (uid,)).fetchone() else set()
    return set()


def approver_label(conn, approver):
    if str(approver).startswith('user:'):
        r = conn.execute('SELECT full_name, username FROM users WHERE id = ?',
                         (str(approver).split(':', 1)[1],)).fetchone()
        return (r[0] or r[1]) if r else approver
    return STEP_LABELS.get(approver, approver)


# ------------------------------------------------------------ إنشاء وتقدّم

def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def validate(conn, rtype, payload):
    p = dict(payload or {})
    if rtype == 'letter':
        tid = p.get('template_id')
        t = conn.execute('SELECT id, name FROM letter_templates WHERE id = ? AND is_active = 1', (tid,)).fetchone()
        if not t:
            raise ValueError('اختار نوع الخطاب')
        p['template_name'] = t[1]
        p['addressee'] = (p.get('addressee') or 'من يهمه الأمر').strip()[:120]
    elif rtype == 'expense':
        amt = _amount(p.get('amount'))
        if amt <= 0:
            raise ValueError('المبلغ لازم يكون أكبر من صفر')
        p['amount'] = amt
        p['description'] = (p.get('description') or '').strip()[:300]
        if not p['description']:
            raise ValueError('اكتب وصف المصروف')
        p['expense_date'] = (p.get('expense_date') or date.today().isoformat())[:10]
    elif rtype == 'loan':
        amt = _amount(p.get('amount'))
        months = int(_amount(p.get('months')) or 0)
        if amt <= 0 or months <= 0 or months > 60:
            raise ValueError('اكتب مبلغ السلفة وعدد الشهور (من 1 لـ 60)')
        p.update(amount=amt, months=months, installment=round(amt / months, 3))
        p['reason'] = (p.get('reason') or '').strip()[:300]
    elif rtype == 'overtime':
        hours = _amount(p.get('hours'))
        if hours <= 0 or hours > 24:
            raise ValueError('عدد الساعات غلط')
        p['hours'] = hours
        p['work_date'] = (p.get('work_date') or '')[:10]
        if not p['work_date']:
            raise ValueError('اختار اليوم')
        p['reason'] = (p.get('reason') or '').strip()[:300]
    else:
        raise ValueError('نوع غير معروف')
    return p


def _amount(v):
    try:
        return round(float(str(v).replace(',', '').strip()), 3)
    except (TypeError, ValueError):
        return 0.0


def create(conn, employee_id, rtype, payload, created_by=None):
    """طلبٌ جديد → يُرجع رقمه. يرمي ValueError برسالةٍ تُعرض."""
    ensure_schema(conn)
    st = type_settings(conn).get(rtype)
    if not st or not st['enabled']:
        raise ValueError('النوع ده مش متاح')
    p = validate(conn, rtype, payload)
    cur = conn.execute('INSERT INTO hr_requests (employee_id, type, payload, created_by) VALUES (?, ?, ?, ?)',
                       (employee_id, rtype, json.dumps(p, ensure_ascii=False), created_by))
    rid = cur.lastrowid
    for i, a in enumerate(st['chain']):
        conn.execute('INSERT INTO hr_request_steps (request_id, step_no, approver) VALUES (?, ?, ?)', (rid, i, a))
    conn.commit()
    _advance(conn, rid)
    return rid


def _advance(conn, rid):
    """يتقدّم الطلب: يتخطّى خطواتٍ بلا أحد، ويُبلغ من عليه الدور، أو يُنهي الطلب."""
    req = get(conn, rid)
    if not req or req['status'] != 'pending':
        return
    for step in req['steps']:
        if step['status'] in ('approved', 'skipped'):
            continue
        if step['status'] == 'rejected':
            return
        who = approvers(conn, step['approver'], req['employee_id'])
        if not who:
            conn.execute("UPDATE hr_request_steps SET status = 'skipped', decided_at = ?, note = ? WHERE id = ?",
                         (_now(), 'لا يوجد من يقوم بهذه الخطوة', step['id']))
            conn.commit()
            continue
        if step['status'] != 'current':
            conn.execute("UPDATE hr_request_steps SET status = 'current' WHERE id = ?", (step['id'],))
            conn.execute('UPDATE hr_requests SET current_step = ? WHERE id = ?', (step['step_no'], rid))
            conn.commit()
            _notify_users(conn, who, 'request_pending', f"طلب {req['type_label']} بانتظار اعتمادك",
                          f"{req['employee_name']} — {summary(req)}", rid)
        return
    _finish(conn, rid, 'approved')


def get(conn, rid):
    ensure_schema(conn)
    r = conn.execute('''SELECT r.*, e.name AS employee_name, e.employee_number, e.department
                        FROM hr_requests r JOIN employees e ON e.id = r.employee_id WHERE r.id = ?''',
                     (rid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d['payload'] = json.loads(d['payload'] or '{}')
    except Exception:
        d['payload'] = {}
    d['type_label'] = TYPES.get(d['type'], d['type'])
    d['status_label'] = STATUS_LABELS.get(d['status'], d['status'])
    d['steps'] = [dict(s) for s in conn.execute('SELECT * FROM hr_request_steps WHERE request_id = ? ORDER BY step_no',
                                                (rid,)).fetchall()]
    for s in d['steps']:
        s['label'] = approver_label(conn, s['approver'])
    return d


def summary(req):
    p = req['payload']
    t = req['type']
    if t == 'letter':
        return f"{p.get('template_name', 'خطاب')} — إلى {p.get('addressee', '')}"
    if t == 'expense':
        return f"{p.get('amount')} — {p.get('description', '')}"
    if t == 'loan':
        return f"{p.get('amount')} على {p.get('months')} شهر (قسط {p.get('installment')})"
    if t == 'overtime':
        return f"{p.get('hours')} ساعة يوم {p.get('work_date')}"
    return ''


def can_act(conn, req, user_id):
    if not req or req['status'] != 'pending':
        return None
    for s in req['steps']:
        if s['status'] == 'current':
            return s if user_id in approvers(conn, s['approver'], req['employee_id']) else None
    return None


def decide(conn, rid, user_id, user_name, approve, note=''):
    """يُرجع (ok, رسالة)."""
    req = get(conn, rid)
    step = can_act(conn, req, user_id)
    if step is None:
        return False, 'الطلب ده مش عندك دلوقتي'
    conn.execute('''UPDATE hr_request_steps SET status = ?, decided_by = ?, decided_by_name = ?, decided_at = ?,
                    note = ? WHERE id = ?''',
                 ('approved' if approve else 'rejected', user_id, user_name, _now(), (note or '')[:300], step['id']))
    conn.commit()
    if not approve:
        _finish(conn, rid, 'rejected')
        return True, 'اترفض الطلب'
    _advance(conn, rid)
    req = get(conn, rid)
    return True, ('اتعتمد الطلب نهائيًا' if req['status'] == 'approved' else 'اتعتمد وراح للخطوة الجاية')


def cancel(conn, rid, employee_id):
    req = get(conn, rid)
    if not req or req['employee_id'] != employee_id or req['status'] != 'pending':
        return False
    conn.execute("UPDATE hr_requests SET status = 'cancelled', decided_at = ? WHERE id = ?", (_now(), rid))
    conn.commit()
    return True


# ------------------------------------------------------------ النتيجة

def _payroll_month(conn):
    """أوّلُ شهرٍ غير مقفول من الشهر الحاليّ فصاعدًا."""
    today = date.today()
    m, y = today.month, today.year
    try:
        from utils.payroll_engine import is_period_locked
    except Exception:
        return m, y
    for _ in range(12):
        try:
            locked = is_period_locked(conn, m, y)
        except Exception:
            locked = False
        if not locked:
            return m, y
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return m, y


def _finish(conn, rid, status):
    req = get(conn, rid)
    ref = None
    if status == 'approved':
        try:
            ref = _apply(conn, req)
        except Exception as e:                  # noqa: BLE001 — الموافقةُ تبقى، والتنفيذُ يُعاد يدويًّا
            logger.exception('apply request %s', rid)
            ref = f'تعذّر التنفيذ: {str(e)[:100]}'
    conn.execute('UPDATE hr_requests SET status = ?, decided_at = ?, result_ref = ? WHERE id = ?',
                 (status, _now(), ref, rid))
    conn.commit()
    try:
        from utils import notifications
        notifications.notify_employee(conn, req['employee_id'], 'request_decided',
                                      f"طلب {req['type_label']}: {STATUS_LABELS[status]}",
                                      summary(req) + (f' — {ref}' if ref and status == 'approved' else ''),
                                      target='leaves', ref_type='hr_request', ref_id=rid)
    except Exception:
        pass


def _item_type(conn, code):
    r = conn.execute('SELECT id FROM payroll_item_types WHERE code = ?', (code,)).fetchone()
    return r[0] if r else None


def _apply(conn, req):
    p, t = req['payload'], req['type']
    if t == 'letter':
        y = date.today().year
        n = conn.execute("SELECT COUNT(*) FROM hr_requests WHERE type = 'letter' AND result_ref LIKE ?",
                         (f'L-{y}-%',)).fetchone()[0] + 1
        return f'L-{y}-{n:04d}'
    if t == 'expense':
        item = _item_type(conn, 'expense_reimb')
        m, y = _payroll_month(conn)
        conn.execute('''INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount, reason,
                        reference) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                     (req['employee_id'], item, m, y, p['amount'], p.get('description', ''), f"REQ-{req['id']}"))
        return f'رواتب {m}/{y}'
    if t == 'loan':
        m, y = _payroll_month(conn)
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
        conn.execute('''INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month, start_year,
                        reason, status, notes) VALUES (?, ?, ?, ?, ?, ?, 'active', ?)''',
                     (req['employee_id'], p['amount'], p['installment'], m, y, p.get('reason', ''),
                      f"REQ-{req['id']}"))
        return f'أول قسط {m}/{y}'
    if t == 'overtime':
        return 'موافقة مسبقة'
    return None


# ------------------------------------------------------------ القوائم

def list_for_employee(conn, employee_id, limit=50):
    ensure_schema(conn)
    ids = [r[0] for r in conn.execute('SELECT id FROM hr_requests WHERE employee_id = ? ORDER BY id DESC LIMIT ?',
                                      (employee_id, limit))]
    return [get(conn, i) for i in ids]


def pending_for_user(conn, user_id):
    ensure_schema(conn)
    out = []
    for (rid,) in conn.execute("SELECT id FROM hr_requests WHERE status = 'pending' ORDER BY id").fetchall():
        req = get(conn, rid)
        if can_act(conn, req, user_id):
            out.append(req)
    return out


def list_all(conn, status=None, rtype=None, limit=500):
    ensure_schema(conn)
    sql, args = 'SELECT id FROM hr_requests WHERE 1=1', []
    if status:
        sql += ' AND status = ?'
        args.append(status)
    if rtype:
        sql += ' AND type = ?'
        args.append(rtype)
    sql += ' ORDER BY id DESC LIMIT ?'
    args.append(limit)
    return [get(conn, r[0]) for r in conn.execute(sql, args).fetchall()]


def _notify_users(conn, user_ids, kind, title, body, rid):
    try:
        from utils import notifications
        for uid in user_ids:
            notifications.notify(conn, uid, kind, title, body, target='leaves', ref_type='hr_request', ref_id=rid)
    except Exception:
        pass


# ------------------------------------------------------------ الخطاب

def render_letter(conn, req):
    """HTML الخطاب بعد ملء الخانات من ملفّ الموظّف."""
    p = req['payload']
    t = conn.execute('SELECT * FROM letter_templates WHERE id = ?', (p.get('template_id'),)).fetchone()
    body = t['body'] if t else ''
    emp = conn.execute('SELECT * FROM employees WHERE id = ?', (req['employee_id'],)).fetchone()
    e = dict(emp) if emp else {}
    st = conn.execute('SELECT * FROM system_settings LIMIT 1').fetchone()
    s = dict(st) if st else {}
    cur = s.get('currency_symbol') or 'د.ك'
    basic = float(e.get('salary') or 0)
    try:
        allow = conn.execute('''SELECT COALESCE(SUM(amount), 0) FROM employee_salary_items
                                WHERE employee_id = ? AND is_active = 1 AND calc_method = 'fixed'
                                AND (end_date IS NULL OR end_date = '' OR end_date >= date('now'))''',
                             (req['employee_id'],)).fetchone()[0]
    except Exception:
        allow = 0
    total = basic + float(allow or 0)
    end = e.get('end_of_service_date') or ''
    values = {
        'company_name': s.get('company_name') or 'الشركة', 'name': e.get('arabic_name') or e.get('name') or '',
        'employee_number': e.get('employee_number') or '', 'position': e.get('position') or '',
        'department': e.get('department') or '', 'hire_date': e.get('hire_date') or '',
        'civil_id': e.get('national_id') or '—', 'nationality': e.get('nationality') or '',
        'salary': f'{basic:,.3f} {cur}', 'total_salary': f'{total:,.3f} {cur}',
        'addressee': p.get('addressee') or 'من يهمه الأمر', 'today': date.today().strftime('%Y-%m-%d'),
        'end_text': f' وحتى <b>{end}</b>' if end else ' وحتى تاريخه',
    }
    import html as _html
    for k, v in values.items():
        safe = v if k == 'end_text' else _html.escape(str(v))
        body = body.replace('{{' + k + '}}', safe)
    return {'body': body, 'company': s, 'title': (t['name'] if t else 'خطاب'), 'number': req.get('result_ref') or '',
            'date': (req.get('decided_at') or '')[:10] or values['today']}


# ------------------------------------------------------------ المرفقات (فاتورة المصروف)

def attachments_dir():
    from utils.db import DATA_DIR
    d = os.path.join(DATA_DIR, 'request_files')
    os.makedirs(d, exist_ok=True)
    return d


def save_attachment(rid, data_url):
    """صورة الفاتورة (data:image/...;base64,…) — حتى 3 ميجا. يُرجع اسم الملف."""
    import base64
    import re
    m = re.match(r'^data:(image/(?:jpeg|png|webp)|application/pdf);base64,(.+)$', str(data_url or ''), re.S)
    if not m:
        return None
    raw = base64.b64decode(m.group(2))
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError('الملف أكبر من 3 ميجا')
    ext = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'application/pdf': 'pdf'}[m.group(1)]
    name = f'req{rid}.{ext}'
    with open(os.path.join(attachments_dir(), name), 'wb') as f:
        f.write(raw)
    return name
