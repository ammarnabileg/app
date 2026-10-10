# -*- coding: utf-8 -*-
"""حقائق اليوم الموحّدة (المرحلة ١ في docs/PAYROLL_V4_PLAN.md) — جدول `day_facts`.

## ليه

النهارده ٣ أماكن بتحسب نفس اليوم (مسير الرواتب، وv3 بتاعة التقارير، والتقرير الشامل) — وطلّعت نتائج
مختلفة (2.36.1 و2.36.2). V4 فيه **مصدر واحد**: صفّ لكلّ (موظّف، يوم)، وكلّ تقرير ومسير يقرا منه.

## من فين بتيجي الحقيقة

من **الفرع الحقيقيّ** للمسير الحاليّ نفسه: `payroll_engine.compute_month_metrics(..., day_sink=…)` بيرجّع
لكلّ يوم «اللي اليوم ده ضافه لعدّادات الشهر». فمجموع أيام الشهر = أرقام المسير **بالبناء**، مش بالحرص —
مفيش نسخة تانية من قواعد الحضور تختلف عنه.

## الفروقات الرجعية (المرحلة ٤)

`build()` بيرجّع الأيام اللي حقيقتها اتغيّرت عن آخر مرة اتسجّلت. لو اليوم ده في شهر مسيره مقفول ← ده
بالظبط اللي `retro.py` هيحسب فرقه ويحطه بانتظار الاعتماد.
"""
import json
from datetime import datetime

# نوع اليوم كما يصنّفه المسير الحاليّ.
KINDS = {
    'work': 'دوام', 'absent': 'غياب', 'rest': 'راحة أسبوعية', 'holiday': 'عطلة رسمية',
    'leave_paid': 'إجازة مدفوعة', 'leave_unpaid': 'إجازة بدون راتب', 'leave_sick': 'إجازة مرضية',
    'excused': 'عذر', 'outside': 'خارج الخدمة',
}
# أعمدةٌ صريحة لأكثر ما تقرؤه التقارير؛ والباقي كلّه في `delta_json`.
COLS = ('required_hours', 'actual_hours', 'late_mins', 'early_mins', 'ot_weekday_mins', 'ot_weekend_mins',
        'ot_holiday_mins', 'absent_days', 'partial_days', 'presence_missing', 'sick_days', 'unpaid_days',
        'excuse_days', 'hourly_perm_hours')


def ensure_schema(conn):
    conn.execute(f'''CREATE TABLE IF NOT EXISTS day_facts (
        employee_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        kind TEXT NOT NULL,
        note TEXT,
        first_punch TEXT,
        last_punch TEXT,
        punches INTEGER DEFAULT 0,
        span_hours REAL DEFAULT 0,
        {', '.join(c + ' REAL DEFAULT 0' for c in COLS)},
        delta_json TEXT,
        fingerprint TEXT,
        built_at TEXT,
        PRIMARY KEY (employee_id, day))''')
    conn.execute('CREATE INDEX IF NOT EXISTS ix_day_facts_day ON day_facts (day)')
    conn.commit()


def _fingerprint(row):
    """بصمة الحقيقة نفسها (من غير وقت البناء) — تتغيّر لو أيّ رقم في اليوم اتغيّر."""
    import hashlib
    payload = json.dumps({k: row[k] for k in ('kind', 'first', 'last', 'punches', 'span_hours', 'delta')},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def compute(conn, month, year):
    """[صفّ لكلّ (موظّف، يوم)] من الفرع الحقيقيّ للمسير — من غير كتابة."""
    from utils import payroll_engine as pe
    rows = []
    pe.compute_month_metrics(conn, month, year, day_sink=rows)
    return [r for r in rows if r['kind'] != 'outside']


def build(conn, month, year):
    """يحسب ويكتب حقائق الشهر. يُرجع {'written': n, 'changed': [(employee_id, day, قديم, جديد)]}.

    `changed` = أيام كانت مكتوبة وحقيقتها اتغيّرت (بصمة اتعدّلت، إجازة اتضافت…) — مدخل الفروقات الرجعية.
    """
    ensure_schema(conn)
    rows = compute(conn, month, year)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    old = {}
    if rows:
        days = sorted({r['date'] for r in rows})
        for r in conn.execute('SELECT employee_id, day, kind, fingerprint FROM day_facts WHERE day BETWEEN ? AND ?',
                              (days[0], days[-1])):
            old[(r[0], r[1])] = (r[2], r[3])
    changed = []
    for r in rows:
        fp = _fingerprint(r)
        key = (r['employee_id'], r['date'])
        if key in old and old[key][1] != fp:
            changed.append((r['employee_id'], r['date'], old[key][0], r['kind']))
        d = r['delta'] or {}
        conn.execute(f'''INSERT OR REPLACE INTO day_facts
            (employee_id, day, kind, note, first_punch, last_punch, punches, span_hours, {', '.join(COLS)},
             delta_json, fingerprint, built_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(COLS))}, ?, ?, ?)''',
                     (r['employee_id'], r['date'], r['kind'], r['note'], r['first'], r['last'], r['punches'],
                      r['span_hours'], *[d.get(c, 0) for c in COLS],
                      json.dumps(d, ensure_ascii=False, sort_keys=True), fp, now))
    conn.commit()
    return {'written': len(rows), 'changed': changed}


def facts_for(conn, employee_id, start, end):
    """حقائق موظّف بين تاريخين — اللي التقارير هتقراه في المرحلة ١."""
    ensure_schema(conn)
    out = []
    cur = conn.execute('SELECT * FROM day_facts WHERE employee_id = ? AND day BETWEEN ? AND ? ORDER BY day',
                       (employee_id, start, end))
    cols = [c[0] for c in cur.description]
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        d['delta'] = json.loads(d.pop('delta_json') or '{}')
        d['kind_label'] = KINDS.get(d['kind'], d['kind'])
        out.append(d)
    return out


def month_totals(conn, employee_id, start, end):
    """مجموع عدّادات الأيام = عدّادات الشهر في المسير الحاليّ (بالبناء)."""
    tot = {}
    for f in facts_for(conn, employee_id, start, end):
        for k, v in f['delta'].items():
            tot[k] = round(tot.get(k, 0) + v, 4)
    return tot
