# -*- coding: utf-8 -*-
"""الإعلانات والاستبيانات (خارطة الطريق، المرحلة ٦).

- **الإعلان**: لكل الشركة أو لقسم/فرع، ويظهر في الرئيسيّة بالبوّابة لحدّ ما ينتهي، ونعرف مين قرأه.
- **الاستبيان**: أسئلة (اختيار، تقييم ١–٥، نصّ). كلّ موظّف يجاوب مرّة. المجهول لا يُخزَّن معه
  رقم الموظّف — يُخزَّن بصمةٌ (hash) تمنع التكرار ولا تكشف صاحبها في النتائج.
"""
import hashlib
import json
from datetime import date

from utils.db import get_db_connection  # noqa: F401  (للاستيراد من الشاشات)

PERMISSION = 'employee.edit'
QTYPES = {'choice': 'اختيار', 'rating': 'تقييم 1-5', 'text': 'نص'}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT, audience_type TEXT DEFAULT 'all',
        audience_value TEXT, pinned INTEGER DEFAULT 0, expires_on TEXT, created_by INTEGER,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, is_active INTEGER DEFAULT 1)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS announcement_reads (
        announcement_id INTEGER NOT NULL, employee_id INTEGER NOT NULL, read_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (announcement_id, employee_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS surveys (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT, questions TEXT NOT NULL,
        anonymous INTEGER DEFAULT 1, audience_type TEXT DEFAULT 'all', audience_value TEXT, closes_on TEXT,
        is_open INTEGER DEFAULT 1, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS survey_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT, survey_id INTEGER NOT NULL, respondent TEXT NOT NULL,
        employee_id INTEGER, answers TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (survey_id, respondent))''')
    conn.commit()


# ------------------------------------------------------------ الجمهور

def _employee(conn, employee_id):
    r = conn.execute('SELECT id, department, branch_location FROM employees WHERE id = ?', (employee_id,)).fetchone()
    return dict(r) if r else None


def _targets(emp, audience_type, audience_value):
    if not emp:
        return False
    if audience_type == 'department':
        return (emp.get('department') or '') == (audience_value or '')
    if audience_type == 'branch':
        return (emp.get('branch_location') or '') == (audience_value or '')
    return True


def audience_employees(conn, audience_type, audience_value):
    rows = conn.execute('SELECT id, department, branch_location FROM employees WHERE COALESCE(is_active, 1) = 1')
    return [r['id'] for r in rows if _targets(dict(r), audience_type, audience_value)]


def audience_label(t, v):
    return {'department': f'قسم {v}', 'branch': f'فرع {v}'}.get(t, 'كل الموظفين')


# ------------------------------------------------------------ الإعلانات

def create_announcement(conn, title, body, audience_type='all', audience_value=None, pinned=False, expires_on=None,
                        user_id=None, notify=True):
    ensure_schema(conn)
    title = (title or '').strip()[:200]
    if not title:
        raise ValueError('اكتب عنوان الإعلان')
    if audience_type not in ('all', 'department', 'branch'):
        audience_type = 'all'
    aid = conn.execute('''INSERT INTO announcements (title, body, audience_type, audience_value, pinned, expires_on,
                          created_by) VALUES (?, ?, ?, ?, ?, ?, ?)''',
                       (title, (body or '').strip()[:5000], audience_type, audience_value or None,
                        1 if pinned else 0, (expires_on or None), user_id)).lastrowid
    conn.commit()
    if notify:
        from utils import notifications
        for emp in audience_employees(conn, audience_type, audience_value):
            notifications.notify_employee(conn, emp, 'announcement', f'📢 {title}', (body or '')[:140],
                                          target='home', ref_type='announcement', ref_id=aid)
    return aid


def announcements_for(conn, employee_id, today=None):
    ensure_schema(conn)
    today = (today or date.today()).isoformat()
    emp = _employee(conn, employee_id)
    rows = conn.execute('''SELECT a.*, r.read_at FROM announcements a
                           LEFT JOIN announcement_reads r ON r.announcement_id = a.id AND r.employee_id = ?
                           WHERE a.is_active = 1 AND (a.expires_on IS NULL OR a.expires_on = '' OR a.expires_on >= ?)
                           ORDER BY a.pinned DESC, a.id DESC LIMIT 30''', (employee_id, today)).fetchall()
    return [dict(r) for r in rows if _targets(emp, r['audience_type'], r['audience_value'])]


def mark_read(conn, announcement_id, employee_id):
    ensure_schema(conn)
    conn.execute('INSERT OR IGNORE INTO announcement_reads (announcement_id, employee_id) VALUES (?, ?)',
                 (announcement_id, employee_id))
    conn.commit()


def announcements_admin(conn):
    ensure_schema(conn)
    out = []
    for r in conn.execute('SELECT * FROM announcements ORDER BY id DESC LIMIT 200').fetchall():
        d = dict(r)
        d['audience'] = audience_label(d['audience_type'], d['audience_value'])
        d['target_count'] = len(audience_employees(conn, d['audience_type'], d['audience_value']))
        d['read_count'] = conn.execute('SELECT COUNT(*) FROM announcement_reads WHERE announcement_id = ?',
                                       (d['id'],)).fetchone()[0]
        out.append(d)
    return out


# ------------------------------------------------------------ الاستبيانات

def _clean_questions(questions):
    out = []
    for q in questions or []:
        text = (q.get('q') or '').strip()[:300]
        t = q.get('type') if q.get('type') in QTYPES else 'text'
        if not text:
            continue
        item = {'q': text, 'type': t}
        if t == 'choice':
            opts = [o.strip()[:100] for o in (q.get('options') or []) if o and o.strip()]
            if len(opts) < 2:
                raise ValueError(f'السؤال «{text}» محتاج اختيارين على الأقل')
            item['options'] = opts
        out.append(item)
    if not out:
        raise ValueError('ضيف سؤال واحد على الأقل')
    return out


def create_survey(conn, title, questions, description='', anonymous=True, audience_type='all', audience_value=None,
                  closes_on=None, user_id=None, notify=True):
    ensure_schema(conn)
    title = (title or '').strip()[:200]
    if not title:
        raise ValueError('اكتب عنوان الاستبيان')
    qs = _clean_questions(questions)
    sid = conn.execute('''INSERT INTO surveys (title, description, questions, anonymous, audience_type, audience_value,
                          closes_on, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                       (title, (description or '')[:1000], json.dumps(qs, ensure_ascii=False), 1 if anonymous else 0,
                        audience_type if audience_type in ('all', 'department', 'branch') else 'all',
                        audience_value or None, closes_on or None, user_id)).lastrowid
    conn.commit()
    if notify:
        from utils import notifications
        for emp in audience_employees(conn, audience_type, audience_value):
            notifications.notify_employee(conn, emp, 'survey', f'📝 استبيان: {title}', 'رأيك يهمنا — دقيقة واحدة',
                                          target='home', ref_type='survey', ref_id=sid)
    return sid


def _salt():
    """سرٌّ عشوائيّ ثابت للتركيب: بصمةُ المجيب لا تُحسب من رقم الموظّف وحده."""
    from utils.db import get_setting, set_setting
    s = get_setting('survey_salt', '')
    if not s:
        import secrets
        s = secrets.token_hex(16)
        set_setting('survey_salt', s)
    return s


def _respondent(survey_id, employee_id):
    salt = _salt()
    return hashlib.sha256(f'{salt}|{survey_id}|{employee_id}'.encode()).hexdigest()


def survey(conn, sid):
    ensure_schema(conn)
    r = conn.execute('SELECT * FROM surveys WHERE id = ?', (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d['questions'] = json.loads(d['questions'])
    return d


def is_open(s, today=None):
    today = (today or date.today()).isoformat()
    return bool(s['is_open']) and (not s['closes_on'] or s['closes_on'] >= today)


def surveys_for(conn, employee_id, today=None):
    """الاستبيانات المفتوحة للموظّف ولم يجاوبها."""
    ensure_schema(conn)
    emp = _employee(conn, employee_id)
    out = []
    for r in conn.execute('SELECT id FROM surveys WHERE is_open = 1 ORDER BY id DESC').fetchall():
        s = survey(conn, r[0])
        if not is_open(s, today) or not _targets(emp, s['audience_type'], s['audience_value']):
            continue
        if conn.execute('SELECT 1 FROM survey_responses WHERE survey_id = ? AND respondent = ?',
                        (s['id'], _respondent(s['id'], employee_id))).fetchone():
            continue
        out.append({k: s[k] for k in ('id', 'title', 'description', 'questions', 'anonymous', 'closes_on')})
    return out


def answer(conn, sid, employee_id, answers):
    s = survey(conn, sid)
    if not s or not is_open(s) or not _targets(_employee(conn, employee_id), s['audience_type'], s['audience_value']):
        raise ValueError('الاستبيان ده مش متاح')
    answers = list(answers or [])
    clean = []
    for i, q in enumerate(s['questions']):
        a = answers[i] if i < len(answers) else None
        if q['type'] == 'choice':
            a = a if a in q['options'] else None
        elif q['type'] == 'rating':
            try:
                a = int(a)
                a = a if 1 <= a <= 5 else None
            except (TypeError, ValueError):
                a = None
        else:
            a = (str(a).strip()[:2000] or None) if a is not None else None
        if a is None and q['type'] != 'text':
            raise ValueError(f'جاوب على: {q["q"]}')
        clean.append(a)
    try:
        conn.execute('INSERT INTO survey_responses (survey_id, respondent, employee_id, answers) VALUES (?, ?, ?, ?)',
                     (sid, _respondent(sid, employee_id), None if s['anonymous'] else employee_id,
                      json.dumps(clean, ensure_ascii=False)))
        conn.commit()
    except Exception:
        raise ValueError('انت جاوبت على الاستبيان ده قبل كده')


def results(conn, sid):
    s = survey(conn, sid)
    if not s:
        return None
    rows = conn.execute('''SELECT r.answers, r.created_at, e.name FROM survey_responses r
                           LEFT JOIN employees e ON e.id = r.employee_id WHERE r.survey_id = ? ORDER BY r.id''',
                        (sid,)).fetchall()
    data = [json.loads(r['answers']) for r in rows]
    out = []
    for i, q in enumerate(s['questions']):
        vals = [d[i] for d in data if i < len(d) and d[i] is not None]
        item = {'q': q['q'], 'type': q['type'], 'n': len(vals)}
        if q['type'] == 'choice':
            item['counts'] = [(o, vals.count(o)) for o in q['options']]
        elif q['type'] == 'rating':
            item['avg'] = round(sum(vals) / len(vals), 2) if vals else None
            item['counts'] = [(str(k), vals.count(k)) for k in range(1, 6)]
        else:
            item['texts'] = vals[-200:]
        out.append(item)
    s['results'] = out
    s['responses'] = len(rows)
    s['target_count'] = len(audience_employees(conn, s['audience_type'], s['audience_value']))
    s['who'] = [] if s['anonymous'] else [r['name'] for r in rows]
    return s


def surveys_admin(conn):
    ensure_schema(conn)
    out = []
    for r in conn.execute('SELECT id FROM surveys ORDER BY id DESC LIMIT 100').fetchall():
        out.append(results(conn, r[0]))
    return out
