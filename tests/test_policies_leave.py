# -*- coding: utf-8 -*-
"""الخطوة ٣ج — سياسات الإجازات لكل نوع (utils/policies)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401
from tests.test_policies_wage import _pol, _sun  # noqa: F401


def _type(conn, name, kind):
    cur = conn.execute("INSERT INTO leave_types (name, days_per_year, is_paid, law_kind) VALUES (?, 30, 1, ?)", (name, kind))
    conn.commit()
    return cur.lastrowid


def _leave(conn, eid, tid, s, e, paid=1):
    conn.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count, is_paid_leave) "
                 "VALUES (?, ?, ?, ?, 'approved', 1, ?)", (eid, tid, s.isoformat(), e.isoformat(), paid))
    conn.commit()


def test_sick_by_classification_not_name(web):
    c, conn, A, pe = web
    tid = _type(conn, 'Medical Absence', 'sick')                   # الاسم مافيهوش «مرض» ولا sick
    mon = _sun(conn, pe) + timedelta(days=1)
    _leave(conn, 1, tid, mon, mon)
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['sick_days'] == 1
    tid2 = _type(conn, 'إجازة طارئة خاصة', 'emergency')             # «طارئة» مابقتش مرضية
    _leave(conn, 1, tid2, mon + timedelta(days=1), mon + timedelta(days=1))
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['sick_days'] == 1 and m['paid_leave_days'] == 1


def test_rest_inside_leave_follows_the_type_policy(web):
    c, conn, A, pe = web
    tid = _type(conn, 'بدون راتب', 'unpaid')
    sun = _sun(conn, pe)
    thu, sat = sun + timedelta(days=4), sun + timedelta(days=6)    # الخميس للسبت: الجمعة والسبت راحة
    _leave(conn, 1, tid, thu, sat, paid=0)
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['unpaid_days'] == 1, 'أيام عمل: الخميس بس'
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    fri = (sun + timedelta(days=5)).isoformat()
    assert days[fri]['status'] == 'weekly_off' and days[fri]['in_leave'], '«ر — داخل فترة إجازة»'
    _pol(conn, 'leave_day_basis', 'calendar', scope='leave_kind', scope_id='unpaid')
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['unpaid_days'] == 3, 'تقويمي: الخميس والجمعة والسبت'
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert days[fri]['status'] == 'leave_unpaid', 'كشف الأيام نفس القاعدة'
    # نوع تاني مايتأثرش
    from utils import policies as PO
    assert PO.load(conn).get('leave_day_basis', leave_kind='annual') == 'working'


def test_request_day_count_follows_the_type(web):
    c, conn, A, pe = web
    from utils.leave_utils import calculate_actual_leave_days
    tid = _type(conn, 'سنوية تجربة', 'annual')
    sun = _sun(conn, pe)
    s, e = sun, sun + timedelta(days=6)                            # أسبوع كامل
    assert calculate_actual_leave_days(conn, s, e, 1, tid) == 5
    _pol(conn, 'leave_excludes_rest', '0', scope='leave_kind', scope_id='annual')
    assert calculate_actual_leave_days(conn, s, e, 1, tid) == 7


def test_punch_on_leave_day_can_count_as_work(web):
    c, conn, A, pe = web
    tid = _type(conn, 'سنوية 2', 'annual')
    mon = _sun(conn, pe) + timedelta(days=1)
    _leave(conn, 1, tid, mon, mon)
    _punch(conn, 1, mon.isoformat(), '08:00', '16:00')
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['paid_leave_days'] == 1
    _pol(conn, 'leave_punch_policy', 'count_as_work', scope='leave_kind', scope_id='annual')
    m = pe.compute_month_metrics(conn, MONTH, YEAR)[1]
    assert m['paid_leave_days'] == 0 and m['actual_days'] == 1


def test_pages_show_classification_and_matrix(web):
    c, conn, A, pe = web
    assert 'التصنيف (بيحدد سياسات الحساب)' in c.get('/leave_settings').get_data(as_text=True)
    page = c.get('/payroll/policies').get_data(as_text=True)
    assert 'لكل نوع إجازة' in page and 'تطبيق على النوع' in page
