# -*- coding: utf-8 -*-
"""الخطوة ٣أ — سياسات الأجر والشفت (utils/policies)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401


def _row(pe, conn, eid=1):
    return next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == eid)


def _ded(row, code):
    return next((d['amount'] for d in row['deductions'] if d['code'] == code), 0.0)


def _pol(conn, key, value, eff='2000-01-01', **kw):
    from utils import policies as PO
    PO.add(conn, key, value, eff, 1, note='اختبار', **kw)
    conn.commit()


def _sun(conn, pe):
    start, _ = pe.resolve_period(conn, MONTH, YEAR)
    return start + timedelta(days=(7 - start.isoweekday() % 7) % 7)


def _allowance(conn, eid, amount):
    it = conn.execute("SELECT id FROM payroll_item_types WHERE code = 'housing'").fetchone()[0]
    conn.execute('INSERT INTO employee_salary_items (employee_id, item_type_id, calc_method, amount, start_date) '
                 "VALUES (?, ?, 'fixed', ?, '2000-01-01')", (eid, it, amount))
    conn.commit()


def test_custom_divisor(web):
    c, conn, A, pe = web
    assert _row(pe, conn)['daily_rate'] == pytest.approx(260 / 26)
    _pol(conn, 'daily_rate_basis', 'custom')
    _pol(conn, 'daily_rate_custom_divisor', '20')
    assert _row(pe, conn)['daily_rate'] == pytest.approx(13.0)


def test_fixed_hourly_basis(web):
    c, conn, A, pe = web
    _punch(conn, 1, _sun(conn, pe).isoformat(), '09:00', '16:00')          # ساعة تأخير
    base = _ded(_row(pe, conn), 'lateness_deduction')
    assert base == pytest.approx(10 / 8, abs=0.002)
    _pol(conn, 'hourly_rate_basis', 'fixed')
    _pol(conn, 'hourly_rate_fixed_hours', '10')
    assert _ded(_row(pe, conn), 'lateness_deduction') == pytest.approx(10 / 10, abs=0.002)


def test_late_and_overtime_on_full_wage(web):
    c, conn, A, pe = web
    _allowance(conn, 1, 130)                                               # الأجر 390 = 1.5 × الأساسي
    sun = _sun(conn, pe)
    _punch(conn, 1, sun.isoformat(), '09:00', '17:00')                     # ساعة تأخير + ساعة إضافي
    r0 = _row(pe, conn)
    ot0 = next(a['amount'] for a in r0['allowances'] if a['code'] == 'overtime')
    _pol(conn, 'late_wage_base', 'wage')
    _pol(conn, 'overtime_wage_base', 'wage')
    r1 = _row(pe, conn)
    assert _ded(r1, 'lateness_deduction') == pytest.approx(_ded(r0, 'lateness_deduction') * 1.5, abs=0.002)
    assert next(a['amount'] for a in r1['allowances'] if a['code'] == 'overtime') == pytest.approx(ot0 * 1.5, abs=0.002)


def test_absence_priced_at_the_salary_of_that_day(web):
    c, conn, A, pe = web
    from utils.pay_history import record_salary_change
    start, end = pe.resolve_period(conn, MONTH, YEAR)
    mid = start + timedelta(days=15)
    record_salary_change(conn, 1, 520, mid.isoformat(), previous=260)
    d = start
    while d <= end:                                                       # يحضر كل يوم إلا أول يوم شغل
        if d.isoweekday() not in (5, 6) and d != start + timedelta(days=(0 if start.isoweekday() not in (5, 6) else 2)):
            _punch(conn, 1, d.isoformat(), '08:00', '16:00')
        d += timedelta(days=1)
    conn.commit()
    avg = _ded(_row(pe, conn), 'absence_deduction')
    _pol(conn, 'salary_day_basis', 'day')
    day = _ded(_row(pe, conn), 'absence_deduction')
    assert 0 < day < avg, 'يوم الغياب قبل الزيادة بالراتب القديم (أقل من المتوسط)'


def test_loan_cap_and_pifss_bases(web):
    c, conn, A, pe = web
    from utils.labor_law import cap_loan_installments  # noqa: F401
    _allowance(conn, 1, 140)                                               # الأجر 400
    conn.execute("UPDATE employees SET nationality = 'كويتي' WHERE id = 1")
    conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month, start_year, status) "
                 "VALUES (1, 1000, 100, ?, ?, 'active')", (MONTH, YEAR))
    conn.commit()
    s = _sun(conn, pe)
    for i in range(20):
        _punch(conn, 1, (s + timedelta(days=i)).isoformat(), '08:00', '16:00')
    _pol(conn, 'pifss_enabled', '1')
    r = _row(pe, conn)
    assert _ded(r, 'loan_installment') == pytest.approx(40.0), '10% من 400'
    assert _ded(r, 'social_insurance_emp') == pytest.approx(400 * 0.105, abs=0.002)
    _pol(conn, 'loan_cap_wage_base', 'basic')
    _pol(conn, 'pifss_wage_base', 'basic')
    r = _row(pe, conn)
    assert _ded(r, 'loan_installment') == pytest.approx(26.0), '10% من 260'
    assert _ded(r, 'social_insurance_emp') == pytest.approx(260 * 0.105, abs=0.002)


def test_weekly_off_history_and_default(web):
    c, conn, A, pe = web
    from utils import policies as PO
    sun = _sun(conn, pe)
    fri = sun + timedelta(days=5)
    _punch(conn, 1, fri.isoformat(), '08:00', '16:00')
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['ot_weekend_mins'] == 480, 'الجمعة راحة: شغلها إضافي'
    PO.record_weekly_off(conn, 1, '6', (fri - timedelta(days=1)).isoformat(), note='السبت بس')
    conn.commit()
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['ot_weekend_mins'] == 0 and m['ot_weekday_mins'] == 0, 'من التاريخ ده الجمعة يوم شغل'
    # الافتراضي لموظف من غير أيام راحة في ملفه
    conn.execute('UPDATE employees SET weekly_leave_selected_days = NULL, weekly_leave_start = NULL WHERE id = 2')
    conn.commit()
    _pol(conn, 'default_weekly_off', '0')
    _punch(conn, 2, sun.isoformat(), '08:00', '16:00')
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[2]['ot_weekend_mins'] == 480, 'الأحد راحة بالافتراضي'


def test_default_hours_when_no_shift(web):
    c, conn, A, pe = web
    conn.execute('UPDATE employees SET shift_type = NULL WHERE id = 2')
    conn.commit()
    assert float(pe.fetch_payroll_employees(conn, employee_id=2)[0]['hours_per_day']) == 8
    _pol(conn, 'default_hours_per_day', '7')
    assert float(pe.fetch_payroll_employees(conn, employee_id=2)[0]['hours_per_day']) == 7


def test_weekly_off_form_and_day_view(web):
    c, conn, A, pe = web
    sun = _sun(conn, pe)
    fri = sun + timedelta(days=5)
    r = c.post('/employees/1/weekly_off_history', data={'effective_from': sun.isoformat(), 'days': ['6'], 'note': 'السبت بس'})
    assert r.status_code == 302
    page = c.get('/employees/edit/1').get_data(as_text=True)
    assert 'أيام الراحة بتاريخ سريان' in page and 'السبت بس' in page
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert days[fri.isoformat()]['status'] != 'weekly_off', 'كشف الأيام بنفس التاريخ'
