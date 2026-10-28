# -*- coding: utf-8 -*-
"""تسوية البصمات — تكملة بصمات ناقصة لموظف بكتاب معتمد، وتوصل أوراكل.

لكل يوم في الشهر ٣ خانات: دخول، وتواجد، وخروج. كل بصمة بتتصنّف بالنافذة (ساعات) اللي واقعة فيها.
اليوم اللي ناقصه خانة بيظهر بوقت مقترح، والمسؤول يحدّد الأيام ويحفظ برقم الكتاب المعتمد.

- **كل تسوية متعلّم عليها**: `source='adjustment'` وفي الملاحظة رقم الكتاب، ومتسجّلة في
  `punch_adjustments` (مين، وإمتى، ورقم الكتاب، والقديم والجديد) — مش متخفّية كبصمة جهاز.
- الحفظ بيستبدل البصمات اللي في نفس النافذة لليوم ده (والقديمة متحفوظة في السجل).
- الشهر اللي كشفه اتقفل مابيتلمسش.
- بتوصل أوراكل عن طريق الطابور (utils/oracle_db) زي أي بصمة.
"""
import calendar as _cal
import json
from datetime import date, datetime

FIELDS = (('entry', 'in', 'دخول'), ('pres', 'pres', 'تواجد'), ('exit', 'out', 'خروج'))
DEFAULT_WINDOWS = {'in': (5, 11), 'pres': (11, 14), 'out': (14, 24)}
DEFAULT_SUGGEST = {'in': '08:00:00', 'pres': '12:00:00', 'out': '16:00:00'}
SETTING_PASSWORD = 'punch_reconcile_password_hash'
DEFAULT_PASSWORD = '11225588'


# ------------------------------------------------------------ الباسورد

def _hash(pw):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(str(pw))


def check_password(pw):
    """الباسورد بتاع الصفحة دي لوحدها (متخزّن hash في الإعدادات — الافتراضي أول مرة بس)."""
    from werkzeug.security import check_password_hash
    from utils.db import get_setting, set_setting
    h = str(get_setting(SETTING_PASSWORD, '') or '')
    if not h:
        h = _hash(DEFAULT_PASSWORD)
        set_setting(SETTING_PASSWORD, h)
    try:
        return check_password_hash(h, str(pw or ''))
    except Exception:
        return False


def change_password(old, new):
    from utils.db import set_setting
    if not check_password(old):
        return False, 'الباسورد الحالي غلط'
    if len(str(new or '')) < 6:
        return False, 'الباسورد الجديد لازم ٦ حروف أو أرقام على الأقل'
    set_setting(SETTING_PASSWORD, _hash(new))
    return True, 'اتغيّر الباسورد'


# ------------------------------------------------------------ الإعدادات

def parse_windows(args):
    """النوافذ من الطلب ← ({'in': (f, t), ...}, خطأ أو None). لازم مرتّبة ومش متداخلة."""
    w = {}
    try:
        for key in ('in', 'pres', 'out'):
            f = int(args.get(f'{key}_from', DEFAULT_WINDOWS[key][0]))
            t = int(args.get(f'{key}_to', DEFAULT_WINDOWS[key][1]))
            if not (0 <= f < t <= 24):
                return DEFAULT_WINDOWS, f'نافذة {key}: البداية لازم تبقى قبل النهاية وما بين 0 و24'
            w[key] = (f, t)
    except (TypeError, ValueError):
        return DEFAULT_WINDOWS, 'النوافذ لازم أرقام ساعات'
    if not (w['in'][1] <= w['pres'][0] and w['pres'][1] <= w['out'][0]):
        return w, 'النوافذ لازم مرتّبة ومش متداخلة: الدخول يخلص قبل التواجد، والتواجد قبل الخروج'
    return w, None


def _norm_time(s):
    s = str(s or '').strip()
    if not s:
        return None
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            return datetime.strptime(s, fmt).strftime('%H:%M:%S')
        except ValueError:
            continue
    return False


def window_of(t, windows):
    """'in' / 'pres' / 'out' أو None للوقت ده (HH:MM:SS)."""
    h = int(t[:2]) + int(t[3:5]) / 60.0 + int(t[6:8]) / 3600.0
    for key in ('in', 'pres', 'out'):
        f, to = windows[key]
        if f <= h < to or (to == 24 and h >= f):
            return key
    return None


# ------------------------------------------------------------ الشهر

def _employee(conn, user_id):
    return conn.execute('SELECT * FROM employees WHERE CAST(employee_number AS TEXT) = ?',
                        (str(user_id).strip(),)).fetchone()


def build(conn, user_id, month, year, windows, codes=None, suggest=None):
    """{'employee', 'calendar', 'code_dist', 'codes', 'orphans', 'holes'} — أو {'error'}."""
    emp = _employee(conn, user_id)
    if not emp:
        return {'error': f'مفيش موظف برقم {user_id}'}
    from utils.payroll_engine import _weekly_off_set
    off = _weekly_off_set(emp)
    days_in = _cal.monthrange(year, month)[1]
    start, end = f'{year}-{month:02d}-01', f'{year}-{month:02d}-{days_in:02d} 23:59:59'
    rows = conn.execute('''SELECT id, check_time, check_type, COALESCE(source, 'device') AS source, note
                           FROM attendance_records WHERE employee_id = ? AND check_time >= ? AND check_time <= ?
                           ORDER BY check_time''', (emp['id'], start, end)).fetchall()
    by_day, dist, orphans = {}, {'in': {}, 'pres': {}, 'out': {}}, []
    for r in rows:
        ts = str(r['check_time']).replace('T', ' ')
        d, t = ts[:10], ts[11:19]
        k = window_of(t, windows)
        if k is None:
            orphans.append({'date': d, 'time': t, 'type': '' if r['check_type'] is None else str(r['check_type'])})
            continue
        code = '' if r['check_type'] is None else str(r['check_type'])
        if r['source'] != 'adjustment':
            dist[k][code] = dist[k].get(code, 0) + 1
        by_day.setdefault(d, {}).setdefault(k, []).append({'time': t, 'adj': r['source'] == 'adjustment'})
    code_dist = {k: sorted(v.items(), key=lambda x: -x[1]) for k, v in dist.items()}
    codes = dict(codes or {})
    for k in ('in', 'pres', 'out'):
        if str(codes.get(k, '')).strip() == '':
            codes[k] = code_dist[k][0][0] if code_dist[k] else {'in': '0', 'pres': '0', 'out': '1'}[k]
    suggest = {k: (_norm_time((suggest or {}).get(k)) or DEFAULT_SUGGEST[k]) for k in ('in', 'pres', 'out')}
    cal, holes = [], 0
    for day in range(1, days_in + 1):
        d = date(year, month, day)
        ds = d.isoformat()
        punches = by_day.get(ds, {})
        weekend = (d.isoweekday() % 7) in off
        item = {'date': ds, 'dow': d.isoweekday() % 7, 'is_weekend': weekend}
        for field, key, _label in FIELDS:
            lst = punches.get(key) or []
            pick = (lst[-1] if key == 'out' else lst[0]) if lst else None
            item[f'actual_{field}'] = pick['time'] if pick else None
            item[f'adj_{field}'] = bool(pick and pick['adj'])
            item[f's_{field}'] = suggest[key]
        item['holed'] = not weekend and not all(item[f'actual_{f}'] for f, _k, _l in FIELDS)
        holes += item['holed']
        cal.append(item)
    return {'employee': emp, 'calendar': cal, 'code_dist': code_dist, 'codes': codes, 'orphans': orphans,
            'holes': holes, 'suggest': suggest}


# ------------------------------------------------------------ الحفظ

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS punch_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch TEXT NOT NULL,
        employee_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        field TEXT NOT NULL,
        old_times TEXT,
        new_time TEXT NOT NULL,
        check_type TEXT,
        book_no TEXT NOT NULL,
        user_id INTEGER,
        created_at TEXT DEFAULT (datetime('now', 'localtime'))
    )''')


def save(conn, user_id, days, values, windows, codes, book_no, by_user=None):
    """يحفظ الأيام المحددة. values: {'entry_2026-10-05': '08:03', ...}.
    يُرجع {'saved', 'replaced', 'locked': [days], 'errors': [..], 'batch'}."""
    ensure_schema(conn)
    book_no = str(book_no or '').strip()
    if not book_no:
        return {'errors': ['رقم الكتاب المعتمد مطلوب']}
    emp = _employee(conn, user_id)
    if not emp:
        return {'errors': [f'مفيش موظف برقم {user_id}']}
    try:
        from utils.payroll_engine import date_period_locked
    except Exception:
        date_period_locked = None
    batch = datetime.now().strftime('%Y%m%d%H%M%S') + f'-{emp["id"]}'
    out = {'saved': 0, 'replaced': 0, 'locked': [], 'errors': [], 'batch': batch}
    for d in sorted(set(days)):
        try:
            datetime.strptime(d, '%Y-%m-%d')
        except ValueError:
            continue
        if date_period_locked:
            try:
                if date_period_locked(conn, d):
                    out['locked'].append(d)
                    continue
            except Exception:
                pass
        for field, key, label in FIELDS:
            raw = values.get(f'{field}_{d}')
            t = _norm_time(raw)
            if t is None:
                continue                    # فاضية: مابتتحفظش ومابتلغيش اللي موجود
            if t is False or window_of(t, windows) != key:
                out['errors'].append(f'{d} {label}: الوقت «{raw}» مش جوه نافذة {label}')
                continue
            existing = conn.execute('''SELECT id, check_time FROM attendance_records
                                       WHERE employee_id = ? AND check_time >= ? AND check_time <= ?''',
                                    (emp['id'], f'{d} 00:00:00', f'{d} 23:59:59')).fetchall()
            olds = [r for r in existing if window_of(str(r['check_time']).replace('T', ' ')[11:19], windows) == key]
            new_ts = f'{d} {t}'
            if len(olds) == 1 and str(olds[0]['check_time']).replace('T', ' ')[:19] == new_ts:
                continue                    # نفس الوقت: مفيش تغيير
            for r in olds:
                conn.execute('DELETE FROM attendance_records WHERE id = ?', (r['id'],))
            conn.execute('''INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source, note, created_by)
                            VALUES (?, 0, ?, ?, 'adjustment', ?, ?)''',
                         (emp['id'], new_ts, int(codes[key]) if str(codes[key]).lstrip('-').isdigit() else 0,
                          f'تسوية بصمة — كتاب رقم {book_no}', by_user))
            conn.execute('''INSERT INTO punch_adjustments (batch, employee_id, day, field, old_times, new_time, check_type,
                                                          book_no, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                         (batch, emp['id'], d, key, json.dumps([str(r['check_time']) for r in olds]), new_ts,
                          str(codes[key]), book_no, by_user))
            out['saved'] += 1
            out['replaced'] += len(olds)
    conn.commit()
    try:
        from utils.oracle_db import wake
        wake()
    except Exception:
        pass
    return out


def delete_from_oracle(conn, user_id, check_time, book_no, by_user=None, oracle_conn=None):
    """«امسح من أوراكل»: بصمة معيّنة تتشال من جدول أوراكل بس (الحضور عندنا مابيتلمسش)،
    برقم كتاب معتمد، ومتسجّلة في `punch_adjustments` (field = 'oracle_del'). {'ok', 'deleted'} أو {'ok': False, 'error'}."""
    ensure_schema(conn)
    book_no = str(book_no or '').strip()
    if not book_no:
        return {'ok': False, 'error': 'رقم الكتاب المعتمد مطلوب'}
    emp = _employee(conn, user_id)
    if not emp:
        return {'ok': False, 'error': f'مفيش موظف برقم {user_id}'}
    from utils import oracle_db
    r = oracle_db.delete_from_oracle(emp['employee_number'], check_time, oracle_conn=oracle_conn, conn=conn)
    if not r.get('ok'):
        return r
    if not r.get('deleted'):
        return {'ok': False, 'error': 'البصمة دي مش موجودة في أوراكل (يمكن اتمسحت قبل كده)'}
    ts = str(check_time).replace('T', ' ')[:19]
    conn.execute('''INSERT INTO punch_adjustments (batch, employee_id, day, field, old_times, new_time, check_type,
                                                  book_no, user_id) VALUES (?, ?, ?, 'oracle_del', ?, ?, '', ?, ?)''',
                 (datetime.now().strftime('%Y%m%d%H%M%S') + f'-{emp["id"]}-del', emp['id'], ts[:10],
                  json.dumps([ts]), ts, book_no, by_user))
    conn.commit()
    return r


def history(conn, user_id=None, limit=100):
    ensure_schema(conn)
    sql = '''SELECT a.*, e.employee_number, e.name, u.username FROM punch_adjustments a
             JOIN employees e ON e.id = a.employee_id LEFT JOIN users u ON u.id = a.user_id'''
    args = []
    if user_id:
        sql += ' WHERE CAST(e.employee_number AS TEXT) = ?'
        args.append(str(user_id).strip())
    return conn.execute(sql + ' ORDER BY a.id DESC LIMIT ?', args + [limit]).fetchall()
