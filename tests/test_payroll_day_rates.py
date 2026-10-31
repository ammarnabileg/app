# -*- coding: utf-8 -*-
"""أجر الساعة لكل يوم بشفته: موظف اتنقل من شفت 8 ساعات لـ 6 ساعات في نص الشهر.

قبل كده: أجر الساعة = الأجر اليومي ÷ ساعات **الشفت الحالي** على الشهر كله، فتأخير وإضافي النص
الأول (شفت 8) اتقيّموا على 6 — أغلى بـ 8/6.
"""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401


def _setup(conn, pe):
    from utils.workday import record_shift_change
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active)"
                 " VALUES ('S6', '08:00', '14:00', 6, 'none', 1)")
    start, end = pe.resolve_period(conn, MONTH, YEAR)
    sun = start + timedelta(days=(7 - start.isoweekday() % 7) % 7)
    change = sun + timedelta(days=7)
    record_shift_change(conn, 1, 'S6', change.isoformat(), previous='S')
    conn.execute("UPDATE employees SET shift_type = 'S6' WHERE id = 1")
    conn.commit()
    return sun, change


def test_lateness_on_an_8_hour_day_is_priced_on_8_hours(web):
    c, conn, A, pe = web
    sun, change = _setup(conn, pe)
    _punch(conn, 1, sun.isoformat(), '09:00', '16:00')                 # ساعة تأخير في شفت الـ 8
    pay = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    late = next(d for d in pay['deductions'] if d['code'] == 'lateness_deduction')
    assert late['amount'] == pytest.approx(pay['daily_rate'] / 8, abs=0.002), 'ساعة على 8 مش على 6'


def test_overtime_follows_each_days_shift(web):
    c, conn, A, pe = web
    sun, change = _setup(conn, pe)
    _punch(conn, 1, sun.isoformat(), '08:00', '17:00')                 # ساعة إضافي يوم شفت 8 (لحد 16)
    _punch(conn, 1, change.isoformat(), '08:00', '15:00')              # ساعة إضافي يوم شفت 6 (لحد 14)
    pay = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    ot = next(a for a in pay['allowances'] if a['code'] == 'overtime')
    m = 1.25
    assert ot['amount'] == pytest.approx(pay['daily_rate'] * m * (1 / 8 + 1 / 6), abs=0.002)
