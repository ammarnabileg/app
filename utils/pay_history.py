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


# ------------------------------------------------------------ فرق بأثر رجعي

RETRO_PREFIX = 'RETRO-'


def retro_arrears(conn, employee_id, user_id=None):
    """زيادة (أو تخفيض) بتاريخ سريان في شهر اتقفل: الفرق يتحسب لكل شهر مقفول وينزل في أوّل شهر مفتوح.

    - الفرق = أساسي الدورة بالتاريخ الجديد − الأساسي اللي اتصرف فعلًا في سطر الكشف (payroll_run_lines.basic).
    - موجب ← بند «متأخرات / فروقات راتب» (arrears). سالب ← «استرداد صرف زائد» (overpayment_recovery).
    - بيتعاد حسابه كل مرة: بنود RETRO اللي لسه في شهر مفتوح بتتلغي وتتكتب من جديد، واللي اتصرف في شهر
      مقفول بيتخصم من الفرق — فمفيش حاجة بتتصرف مرتين.
    - الأجر اليومي مابيتحسبلوش فرق هنا (الأساسي فيه بيتحسب من أيام الشغل) — بيرجع في `skipped`.
    يُرجع {'items': [(شهر، سنة، الفرق)], 'month': (m, y), 'skipped': سبب أو None}."""
    from utils.payroll_engine import is_period_locked, _parse_d
    emp = conn.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
    if not emp:
        return {'items': [], 'month': None, 'skipped': 'مفيش موظف'}
    if is_daily(emp):
        return {'items': [], 'month': None, 'skipped': 'أجر يومي — الفرق بيتحسب يدوي'}
    hist = salary_history(conn, [employee_id]).get(employee_id)
    runs = conn.execute('''SELECT r.month, r.year, r.period_start, r.period_end, l.basic
                           FROM payroll_runs r JOIN payroll_run_lines l ON l.run_id = r.id
                           WHERE r.status = 'approved' AND l.employee_id = ?
                           ORDER BY r.year, r.month''', (employee_id,)).fetchall()
    target = _first_open_month(conn)
    paid = {}
    for r in conn.execute('''SELECT t.reference, t.amount, it.kind, t.month, t.year FROM payroll_transactions t
                             JOIN payroll_item_types it ON it.id = t.item_type_id
                             WHERE t.employee_id = ? AND t.status = 'active' AND t.reference LIKE ?''',
                          (employee_id, RETRO_PREFIX + '%')).fetchall():
        if is_period_locked(conn, r['month'], r['year']):
            sign = 1 if r['kind'] == 'earning' else -1
            paid[r['reference']] = paid.get(r['reference'], 0.0) + sign * float(r['amount'] or 0)
    conn.execute('''UPDATE payroll_transactions SET status = 'cancelled', cancelled_by = ?, cancelled_at = CURRENT_TIMESTAMP
                    WHERE employee_id = ? AND status = 'active' AND reference LIKE ? AND month = ? AND year = ?''',
                 (user_id, employee_id, RETRO_PREFIX + '%', target[0], target[1]))
    items = []
    for r in runs:
        ps, pe = _parse_d(r['period_start']), _parse_d(r['period_end'])
        if not ps or not pe:
            continue
        new_basic, _ = period_salary(hist, emp['salary'], ps, pe)
        ref = f"{RETRO_PREFIX}{employee_id}-{r['year']}-{int(r['month']):02d}"
        diff = round(float(new_basic) - float(r['basic'] or 0) - paid.get(ref, 0.0), 3)
        if abs(diff) < 0.0005:
            continue
        code = 'arrears' if diff > 0 else 'overpayment_recovery'
        it = conn.execute('SELECT id FROM payroll_item_types WHERE code = ?', (code,)).fetchone()
        if not it:
            continue
        conn.execute('''INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount, reason,
                        reference, entered_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                     (employee_id, it[0], target[0], target[1], abs(diff),
                      f"فرق راتب بأثر رجعي عن {int(r['month'])}/{r['year']}", ref, user_id))
        items.append((int(r['month']), int(r['year']), diff))
    return {'items': items, 'month': target, 'skipped': None}


def _first_open_month(conn):
    """أوّل شهر مش مقفول، من الشهر الحالي ورايح."""
    from datetime import date
    from utils.payroll_engine import is_period_locked
    m, y = date.today().month, date.today().year
    for _ in range(24):
        if not is_period_locked(conn, m, y):
            return m, y
        m, y = (1, y + 1) if m == 12 else (m + 1, y)
    return m, y
