# -*- coding: utf-8 -*-
"""تاريخُ الراتب: الأساسيُّ بتاريخ سريانه، ونوعُ الأجر.

## تغييرُ الراتب في منتصف الشهر

زيادةٌ تسري يوم 15 تُحسب من يوم 15 لا من أوّل الشهر: أساسيُّ الدورة متوسّطٌ
مرجَّحٌ بالأيّام — كلُّ يومٍ بالراتب الساري فيه. والماضي لا يتغيّر بتعديل
الراتب اليوم: الجديدُ يُسجَّل بتاريخ سريانه في `employee_salary_history`.

## الأجرُ اليوميّ

`pay_type = 'daily'`: الراتبُ في ملفّ الموظّف أجرُ يومٍ لا شهر. ما يعادله
شهريًّا = الأجرُ اليوميّ × قاسمُ اليوم (26، أو 30، أو أيّامُ الجدول)، ومنه يمضي
الحسابُ كلُّه كما للشهريّ: الغيابُ يُخصم بالأجر اليوميّ نفسه، ونهايةُ الخدمة
بقواعد الأجر اليوميّ (المادة 51-أ).
"""
from datetime import timedelta

BASE_DATE = '0001-01-01'


def is_daily(emp):
    return (emp['pay_type'] if 'pay_type' in emp.keys() else None) == 'daily'


def monthly_equivalent(emp, salary, divisor=26.0):
    """الراتبُ الشهريّ المكافئ: كما هو للشهريّ، واليوميُّ × القاسم."""
    salary = float(salary or 0)
    return salary * float(divisor) if is_daily(emp) else salary


def salary_history(conn, employee_ids):
    out = {}
    try:
        rows = conn.execute('SELECT employee_id, salary, effective_from FROM employee_salary_history '
                            'ORDER BY employee_id, effective_from, id').fetchall()
    except Exception:
        return out
    ids = set(employee_ids)
    for r in rows:
        if r['employee_id'] in ids:
            out.setdefault(r['employee_id'], []).append((str(r['effective_from'])[:10],
                                                         float(r['salary'] or 0)))
    return out


def salary_on(history, current, d_iso):
    val = None
    for eff, sal in history or ():
        if eff <= d_iso:
            val = sal
        else:
            break
    return float(current or 0) if val is None else val


def period_salary(history, current, p_start, p_end):
    """الراتبُ للدورة: متوسّطُ الأيّام، كلٌّ براتبه الساري. ويعيد الأجزاء للعرض."""
    n, total, parts = 0, 0.0, []
    d = p_start
    while d <= p_end:
        s = salary_on(history, current, d.isoformat())
        total += s
        n += 1
        if not parts or parts[-1]['salary'] != s:
            parts.append({'from': d.isoformat(), 'salary': s, 'days': 0})
        parts[-1]['days'] += 1
        d += timedelta(days=1)
    if not n:
        return float(current or 0), []
    val = total / n
    # قيمةٌ واحدة طوال الدورة تُعاد كما هي، بلا أثرٍ للقسمة
    return (parts[0]['salary'] if len(parts) == 1 else round(val, 6)), parts


def record_salary_change(conn, employee_id, salary, effective_from, user_id=None, previous=None):
    eff = str(effective_from)[:10]
    if previous is not None and not conn.execute(
            'SELECT 1 FROM employee_salary_history WHERE employee_id = ?', (employee_id,)).fetchone():
        conn.execute('INSERT INTO employee_salary_history (employee_id, salary, effective_from) '
                     'VALUES (?, ?, ?)', (employee_id, float(previous or 0), BASE_DATE))
    conn.execute('DELETE FROM employee_salary_history WHERE employee_id = ? AND effective_from = ?',
                 (employee_id, eff))
    conn.execute('INSERT INTO employee_salary_history (employee_id, salary, effective_from, created_by) '
                 'VALUES (?, ?, ?, ?)', (employee_id, float(salary or 0), eff, user_id))


def migrate(conn):
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS employee_salary_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_id INTEGER NOT NULL,
        salary REAL NOT NULL,
        effective_from DATE NOT NULL,
        created_by INTEGER,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    cur.execute('CREATE INDEX IF NOT EXISTS ix_salary_hist_emp ON employee_salary_history (employee_id, effective_from)')
    cur.execute(f'''INSERT INTO employee_salary_history (employee_id, salary, effective_from)
                   SELECT e.id, COALESCE(e.salary, 0), '{BASE_DATE}' FROM employees e
                   WHERE NOT EXISTS (SELECT 1 FROM employee_salary_history h WHERE h.employee_id = e.id)''')
