# -*- coding: utf-8 -*-
"""يومُ العمل: أيُّ شفتٍ يسري عليه، وأيُّ بصمةٍ تخصّه.

مصدرٌ واحد يقرؤه الحسابُ الشهريّ (`compute_month_metrics`) وعرضُ اليوم
(`compute_employee_days`)، فلا يفترقان.

## تاريخُ الشفتات

الشفتُ لا يُقرأ من ملفّ الموظّف كما هو اليوم، بل من `employee_shift_history`:
لكلّ يومٍ آخرُ شفتٍ سرى قبله أو فيه. موظّفٌ داوم 15 يومًا صباحيًّا ثم 5
مسائيًّا ثم عاد صباحيًّا تُحسب كلُّ فترةٍ بشفتها — بساعاتها وتأخيرها
وإضافيّها — ولا يُعاد حسابُ الماضي إن غُيِّر الشفتُ اليوم.

## الشفتُ الليليّ

شفتٌ ينتهي بعد منتصف الليل (22:00 → 06:00) يملك بصماتِ صباح اليوم التالي:
نافذتُه من قبل بدايته بأربع ساعات إلى ما بعد نهايته بأربع (ولا تتعدّى بداية
شفت اليوم التالي). كان يُقسَم عند منتصف الليل: ليلتان تصيران يومًا «حاضرًا»
بـ16 ساعة (من خروج الصبح إلى دخول المساء) ويومين «بصمة ناقصة».

وحسابُ التأخير والانصراف والإضافيّ يجري على «ساعةٍ مُدارة»: يُطرح من كلّ
وقتٍ في يوم الشفت الليليّ (بداية الشفت − 4 ساعات)، فيقع الشفتُ كلُّه في يومٍ
واحد (22:00 → 04:00، و06:00 التالي → 12:00) وتعمل القواعدُ نفسُها بلا فرعٍ
ثانٍ. والعرضُ يُظهر الأوقاتَ الحقيقيّة.

## الشفتُ المقسّم والاستراحة

المقسّمُ تُجمع فتراتُه لا المدى بينها، والناقصُ منه بصمةٌ ناقصة. والاستراحةُ
(المادة 65) تُطرح من الساعات حين يتجاوز اليومُ خمسَ ساعات.
"""
from datetime import date, datetime, timedelta

# حقولُ الشفت في صفّ الموظّف كما يقرؤها المحرّك ← أعمدةُ shift_types
SHIFT_SQL = ('SELECT id, name, start_time, end_time, COALESCE(hours_per_day, 8) AS hpd, '
             'description, presence_start_time, presence_end_time, flex_mode, '
             'COALESCE(is_split, 0) AS is_split, COALESCE(break_minutes, 0) AS break_minutes '
             'FROM shift_types')
WINDOW_BEFORE = 240     # دقائقُ قبل بداية الشفت الليليّ
WINDOW_AFTER = 240      # وبعد نهايته
BASE_DATE = '0001-01-01'


def _m(t):
    try:
        h, mi = str(t)[:5].split(':')
        return int(h) * 60 + int(mi)
    except (TypeError, ValueError):
        return None


def _hhmm(mins):
    mins = int(mins) % 1440
    return f'{mins // 60:02d}:{mins % 60:02d}'


def is_overnight(start, end):
    s, e = _m(start), _m(end)
    return s is not None and e is not None and e < s


class DayView(dict):
    """صفُّ الموظّف بحقول شفت اليوم. dict لأنّ المحرّك يقرأ `.keys()` و`[]`."""


def load_shifts(conn):
    shifts = {}
    for r in conn.execute(SHIFT_SQL).fetchall():
        sh = dict(r)
        sh['periods'] = []
        if sh['is_split']:
            sh['periods'] = [dict(p) for p in conn.execute(
                'SELECT seq, start_time, end_time FROM shift_periods '
                'WHERE shift_type_id = ? ORDER BY seq', (sh['id'],)).fetchall()]
            if len(sh['periods']) < 2:
                sh['is_split'] = 0            # مقسّمٌ بلا فترتين يُعامل عاديًّا
        shifts[sh['name']] = sh
    return shifts


def shift_history(conn, employee_ids):
    """{موظّف: [(يسري من, الشفت), ...]} مرتّبة."""
    out = {}
    try:
        rows = conn.execute('SELECT employee_id, shift_type, effective_from FROM employee_shift_history '
                            'ORDER BY employee_id, effective_from, id').fetchall()
    except Exception:
        return out
    ids = set(employee_ids)
    for r in rows:
        if r['employee_id'] in ids:
            out.setdefault(r['employee_id'], []).append((str(r['effective_from'])[:10], r['shift_type']))
    return out


def shift_on(history, current, d_iso):
    """آخرُ شفتٍ سرى في اليوم أو قبله؛ وبلا تاريخٍ فالشفتُ الحاليّ."""
    name = None
    for eff, sh in history or ():
        if eff <= d_iso:
            name = sh
        else:
            break
    return name if name is not None else current


def _view(emp, sh):
    v = DayView(emp)
    if sh is None:
        v['_offset'] = 0
        v['_overnight'] = False
        v['periods'] = []
        return v
    v.update({'hours_per_day': float(sh['hpd'] or 8), 'shift_start': sh['start_time'],
              'shift_end': sh['end_time'], 'presence_start_time': sh['presence_start_time'],
              'presence_end_time': sh['presence_end_time'], 'flex_mode': sh['flex_mode'],
              'shift_name': sh['name'], 'shift_notes': sh['description'],
              'is_split': sh['is_split'], 'break_minutes': sh['break_minutes'],
              'shift_type': sh['name'], 'periods': sh['periods']})
    night = (not sh['is_split']) and is_overnight(sh['start_time'], sh['end_time'])
    v['_overnight'] = night
    v['_offset'] = 0
    if night:
        off = _m(sh['start_time']) - WINDOW_BEFORE
        v['_offset'] = off
        v['shift_start'] = _hhmm(_m(sh['start_time']) - off)
        v['shift_end'] = _hhmm(_m(sh['end_time']) + 1440 - off)
        for k in ('presence_start_time', 'presence_end_time'):
            if v.get(k):
                pm = _m(v[k])
                v[k] = _hhmm(pm - off if pm >= off else pm + 1440 - off)
    return v


def rotate(view, hhmm):
    """وقتٌ حقيقيّ ← ساعةُ اليوم المُدارة (للأذونات بالساعة في الشفت الليليّ)."""
    off = view.get('_offset') or 0
    if not off or not hhmm:
        return hhmm
    pm = _m(hhmm)
    return _hhmm(pm - off if pm >= off else pm + 1440 - off)


def build(conn, employees, p_start, p_end, net_span_hours):
    """{موظّف: {'views': {يوم: DayView}, 'rec': {يوم: سجلّ}}}.

    السجلّ: (ساعات، أوّل، آخر، [الأوقات]، مقسّمٌ ناقص، [الأوقات الحقيقيّة]،
    [التواريخ الحقيقيّة])
    — والأوقاتُ بالساعة المُدارة؛ و(آخر) None إن لم تكن بصمتان صالحتان."""
    shifts = load_shifts(conn)
    ids = [e['id'] for e in employees]
    hist = shift_history(conn, ids)
    days = []
    d = p_start
    while d <= p_end:
        days.append(d)
        d += timedelta(days=1)

    stamps = {}
    if ids:
        lo = (p_start - timedelta(days=1)).isoformat()
        hi = (p_end + timedelta(days=1)).isoformat()
        for r in conn.execute('SELECT employee_id, check_time FROM attendance_records '
                              'WHERE DATE(check_time) BETWEEN ? AND ? ORDER BY employee_id, check_time',
                              (lo, hi)).fetchall():
            t = _dt(r['check_time'])
            if t is not None:
                stamps.setdefault(r['employee_id'], []).append(t)

    out = {}
    for emp in employees:
        eid = emp['id']
        cur = emp['shift_type'] if 'shift_type' in emp.keys() else None
        cache = {}

        def view_for(dd):
            name = shift_on(hist.get(eid), cur, dd.isoformat())
            if name not in cache:
                cache[name] = _view(emp, shifts.get(name))
            return cache[name]

        # يُضمّ اليومُ السابق للدورة: شفتُه الليليّ يملك صباحَ أوّل يوم
        all_days = [p_start - timedelta(days=1)] + days
        views = {x: view_for(x) for x in all_days + [p_end + timedelta(days=1)]}
        pts = stamps.get(eid, [])
        used = set()
        rec = {}
        for i, dd in enumerate(all_days):
            v = views[dd]
            base = datetime(dd.year, dd.month, dd.day)
            if v['_overnight']:
                s, e = _m(v['shift_start']) + v['_offset'], _m(v['shift_end']) + v['_offset'] - 1440
                lo_t = base + timedelta(minutes=s - WINDOW_BEFORE)
                hi_m = e + WINDOW_AFTER
                nxt = views[dd + timedelta(days=1)]
                if not nxt['_overnight'] and _m(nxt.get('shift_start')) is not None:
                    hi_m = min(hi_m, max(e + 30, _m(nxt['shift_start']) - 60))
                hi_t = base + timedelta(days=1, minutes=hi_m)
            else:
                lo_t = base
                hi_t = base + timedelta(days=1) - timedelta(seconds=1)
            picks = [j for j, t in enumerate(pts) if j not in used and lo_t <= t <= hi_t]
            used.update(picks)
            if dd < p_start or not picks:
                continue
            off = v['_offset']
            mins = [int((pts[j] - base).total_seconds() // 60) - off for j in picks]
            times = [_hhmm(x) for x in mins]
            real = [pts[j].strftime('%H:%M') for j in picks]
            f_t = times[0]
            l_t = times[-1] if len(mins) >= 2 and mins[-1] > mins[0] else None
            split_incomplete = False
            if v.get('is_split') and v.get('periods'):
                from utils.payroll_engine import compute_split_day
                span, pairs, need = compute_split_day(times, v['periods'])
                split_incomplete = pairs < need
            elif l_t:
                span = (mins[-1] - mins[0]) / 60.0
                if (v.get('flex_mode') or 'none') != 'any_time':
                    span = net_span_hours(span, v.get('break_minutes') or 0)
            else:
                span = 0.0
            rec[dd.isoformat()] = (span, f_t, l_t, times, split_incomplete, real,
                                   [pts[j] for j in picks])
        out[eid] = {'views': {x.isoformat(): views[x] for x in days}, 'rec': rec}
    return out


def _dt(v):
    s = str(v or '').replace('T', ' ')[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ------------------------------------------------------------------ التاريخ

def record_shift_change(conn, employee_id, shift_type, effective_from, user_id=None,
                        previous=None):
    """يُسجّل شفتًا يسري من تاريخ. تاريخٌ مكرّر يُستبدل شفتُه.

    موظّفٌ بلا تاريخٍ بعد (أُضيف بعد الترحيل) يُسجَّل له شفتُه السابق أساسًا
    أوّلًا — وإلّا قرأت الأيّامُ التي قبل التغيير الشفتَ الجديد."""
    eff = str(effective_from)[:10]
    if previous and not conn.execute('SELECT 1 FROM employee_shift_history WHERE employee_id = ?',
                                     (employee_id,)).fetchone():
        conn.execute('INSERT INTO employee_shift_history (employee_id, shift_type, effective_from) '
                     'VALUES (?, ?, ?)', (employee_id, previous, BASE_DATE))
    conn.execute('DELETE FROM employee_shift_history WHERE employee_id = ? AND effective_from = ?',
                 (employee_id, eff))
    conn.execute('INSERT INTO employee_shift_history (employee_id, shift_type, effective_from, created_by) '
                 'VALUES (?, ?, ?, ?)', (employee_id, shift_type, eff, user_id))


def migrate(conn):
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS employee_shift_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        shift_type TEXT NOT NULL,
        effective_from DATE NOT NULL,
        created_by INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_shift_hist_emp ON employee_shift_history (employee_id, effective_from)')
    # الأساس: شفتُ كلّ موظّفٍ اليومَ يسري منذ البداية — فلا يتغيّر ماضٍ بالترحيل.
    cur.execute(f'''INSERT INTO employee_shift_history (employee_id, shift_type, effective_from)
                   SELECT e.id, e.shift_type, '{BASE_DATE}' FROM employees e
                   WHERE e.shift_type IS NOT NULL AND e.shift_type != ''
                     AND NOT EXISTS (SELECT 1 FROM employee_shift_history h WHERE h.employee_id = e.id)''')
