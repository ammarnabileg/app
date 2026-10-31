# -*- coding: utf-8 -*-
"""الخطوة ٣ب — سياسات التأخير والإضافي (utils/policies)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401
from tests.test_policies_wage import _pol, _sun, _row  # noqa: F401


def _m(pe, conn, eid=1):
    return pe.compute_month_metrics(conn, MONTH, YEAR)[eid]


def test_late_counted_from_grace_end(web):
    c, conn, A, pe = web
    _punch(conn, 1, _sun(conn, pe).isoformat(), '08:25', '16:00')     # 25 دقيقة، السماح 10
    assert _m(pe, conn)['late_mins'] == 25
    _pol(conn, 'late_count_from', 'grace_end')
    assert _m(pe, conn)['late_mins'] == 15


def test_early_counted_from_grace_end(web):
    c, conn, A, pe = web
    _punch(conn, 1, _sun(conn, pe).isoformat(), '08:00', '15:30')     # 30 دقيقة بدري، السماح 5
    assert _m(pe, conn)['early_mins'] == 30
    _pol(conn, 'early_count_from', 'grace_end')
    assert _m(pe, conn)['early_mins'] == 25


def test_overtime_minimum_and_approval(web):
    c, conn, A, pe = web
    from utils import policies as PO
    sun = _sun(conn, pe)
    _punch(conn, 1, sun.isoformat(), '08:00', '16:20')                 # 20 دقيقة
    _punch(conn, 1, (sun + timedelta(days=1)).isoformat(), '08:00', '18:00')   # ساعتين
    assert _m(pe, conn)['ot_weekday_mins'] == 140
    _pol(conn, 'overtime_min_minutes', '30')
    assert _m(pe, conn)['ot_weekday_mins'] == 120, 'الـ20 دقيقة أقل من الحد'
    _pol(conn, 'overtime_requires_approval', '1')
    assert _m(pe, conn)['ot_weekday_mins'] == 0, 'من غير موافقة'
    PO.approve_overtime(conn, 1, (sun + timedelta(days=1)).isoformat(), 90, 1)
    conn.commit()
    assert _m(pe, conn)['ot_weekday_mins'] == 90
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert days[(sun + timedelta(days=1)).isoformat()]['ot_mins'] == 90, 'كشف الأيام نفس الحساب'
    page = c.get(f'/payroll/overtime-approvals?month={MONTH}&year={YEAR}').get_data(as_text=True)
    assert 'محتاج موافقة' in page and '2:00' in page
    c.post(f'/payroll/overtime-approvals?month={MONTH}&year={YEAR}',
           data={f'ot_1_{(sun + timedelta(days=1)).isoformat()}': '2'})
    assert _m(pe, conn)['ot_weekday_mins'] == 120


def test_cap_blocks_save_when_policy_says_so(web):
    c, conn, A, pe = web
    _pol(conn, 'overtime_cap_monthly_hours', '1')
    _punch(conn, 1, _sun(conn, pe).isoformat(), '08:00', '19:00')     # 3 ساعات
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}, {'employee_id': 2}], 1)
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)                       # تنبيه بس
    _pol(conn, 'overtime_cap_action', 'block')
    with pytest.raises(pe.OvertimeCapError):
        pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    r = _row(pe, conn)
    assert any(a['code'] == 'overtime' for a in r['allowances']), 'الساعات مابتضيعش'


def test_comp_rest_balance_from_approved_months(web):
    c, conn, A, pe = web
    from utils import policies as PO
    sun = _sun(conn, pe)
    fri, sat = sun + timedelta(days=5), sun + timedelta(days=6)
    _punch(conn, 1, fri.isoformat(), '08:00', '14:00')                  # 6 ساعات
    _punch(conn, 1, sat.isoformat(), '08:00', '10:00')                  # ساعتين (أقل من 4)
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}, {'employee_id': 2}], 1)
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    assert not PO.comp_rest_balance(conn, YEAR), 'لسه مش معتمد'
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    b = PO.comp_rest_balance(conn, YEAR)
    assert len(b) == 1 and b[0]['earned'] == 1 and b[0]['days'][0]['day'] == fri.isoformat()
    assert 'رصيد الراحة البديلة' in c.get(f'/payroll/comp-rest?year={YEAR}').get_data(as_text=True)


def test_permission_reduces_instead_of_cancelling(web):
    c, conn, A, pe = web
    sun = _sun(conn, pe)
    lt = conn.execute("SELECT id FROM leave_types ORDER BY id LIMIT 1").fetchone()
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count, "
                 "start_time, end_time, leave_duration_type) VALUES (1, ?, ?, ?, 'approved', 0, '08:00', '08:30', 'hourly')",
                 (lt[0], sun.isoformat(), sun.isoformat()))
    conn.commit()
    _punch(conn, 1, sun.isoformat(), '09:00', '16:00')                 # ساعة تأخير، استئذان نص ساعة
    assert _m(pe, conn)['late_mins'] == 0
    _pol(conn, 'hourly_perm_effect', 'reduce')
    assert _m(pe, conn)['late_mins'] == 30
