# -*- coding: utf-8 -*-
"""الخطوة ٤ — التقرير الشامل بخانات أيام كشف الرواتب نفسها، و«كيف اتحسبت؟»."""
from datetime import timedelta

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401
from tests.test_policies_wage import _pol, _sun  # noqa: F401


def _report(c, conn, pe):
    import importlib
    import smart_reports
    importlib.reload(smart_reports)
    ps, pe_ = pe.resolve_period(conn, MONTH, YEAR)
    r = c.get(f'/api/fingerprint/custom_employees_report?start_date={ps}&end_date={pe_}').get_json()
    return {x['id']: x for x in r['rows']}


def test_cells_follow_shift_history_like_payroll(web):
    """موظف اتنقل لشفت تاني في نص الشهر: الخانة والكشف نفس الحكم (قبل كده التقرير كان بالشفت الحالي)."""
    c, conn, A, pe = web
    from utils.workday import record_shift_change
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active)"
                 " VALUES ('EVE', '14:00', '22:00', 8, 'none', 1)")
    sun = _sun(conn, pe)
    change = sun + timedelta(days=7)
    record_shift_change(conn, 1, 'EVE', change.isoformat(), previous='S')
    conn.execute("UPDATE employees SET shift_type = 'EVE' WHERE id = 1")
    conn.commit()
    _punch(conn, 1, sun.isoformat(), '08:00', '16:00')                 # قبل التغيير: شفت الصبح، في ميعاده
    rows = _report(c, conn, pe)
    cell = next(x for x in rows[1]['daily_cells'] if x['date'] == sun.isoformat())
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert cell['source'] == 'payroll_engine' and cell['status'] == 'present'
    assert cell['late_minutes'] == days[sun.isoformat()]['late_mins'] == 0, 'مش متأخر 6 ساعات عن شفت المسا'


def test_rest_inside_leave_shows_and_counts_match(web):
    c, conn, A, pe = web
    tid = conn.execute("INSERT INTO leave_types (name, days_per_year, is_paid, law_kind) VALUES ('س', 30, 1, 'annual')").lastrowid
    sun = _sun(conn, pe)
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count, is_paid_leave) "
                 "VALUES (1, ?, ?, ?, 'approved', 1, 1)", (tid, (sun + timedelta(days=4)).isoformat(), (sun + timedelta(days=6)).isoformat()))
    conn.commit()
    rows = _report(c, conn, pe)
    fri = next(x for x in rows[1]['daily_cells'] if x['date'] == (sun + timedelta(days=5)).isoformat())
    assert fri['status'] == 'weekly_off' and fri['in_leave']
    met = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert rows[1]['stats']['absent_days'] == met['absent_days']


def test_explain_day_and_money(web):
    c, conn, A, pe = web
    sun = _sun(conn, pe)
    _pol(conn, 'grace_late_minutes', '5', eff=sun.isoformat())
    _punch(conn, 1, sun.isoformat(), '08:30', '17:00')
    r = c.get(f'/api/payroll/explain_day?employee_id=1&date={sun}').get_json()
    text = ' '.join(f'{a} {b}' for a, b in r['lines'])
    assert r['success'] and 'التأخير' in text and 'سياسة الشركة من ' + sun.isoformat() in text
    assert 'الإضافي' in text
    rows = _report(c, conn, pe)
    ded = rows[1]['financials']['deductions_detail']
    late = next(d for d in ded if 'تأخير' in d['name'])
    assert any('سماح التأخير' in e for e in late['explain']) and any('الأجر اليومي' in e for e in late['explain'])
    page = c.get('/fingerprint/custom_employees_report').get_data(as_text=True)
    assert 'explainModal' in page and 'explainDay(' in page
