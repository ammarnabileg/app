# -*- coding: utf-8 -*-
"""التقارير الشهرية الذكية كانت بتسأل القاعدة مرتين لكل موظف في كل يوم (٥٠٠ موظف × ٣١ يوم
= ٣١ ألف استعلام ≈ دقيقة). دلوقتي استعلام للشفتات واستعلام لكل يوم — والنتيجة نفسها."""
import importlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def rep(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    for i in range(1, 21):
        con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary,"
                    " is_active, default_start_time, default_end_time) VALUES (?, ?, ?, 'D', 'P', '2025-01-01', 500, 1,"
                    " '08:00', '17:00')", (i, f'e{i}', str(100 + i)))
        for d in range(1, 31):
            con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                        " VALUES (?, 1, ?, 0)", (i, f'2026-09-{d:02d} 08:1{i % 10}:00'))
            con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                        " VALUES (?, 1, ?, 0)", (i, f'2026-09-{d:02d} 17:05:00'))
    # صيغة قديمة بـ T مع صيغة عادية في نفس اليوم: الترتيب بالوقت الفعلي
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                " VALUES (1, 1, '2026-09-07T07:50:00', 0)")
    con.commit()
    import smart_reports
    importlib.reload(smart_reports)
    return smart_reports.SmartFingerprintReport(db.DB_PATH), db


def test_monthly_report_uses_a_handful_of_queries(rep):
    r, db = rep
    calls = []
    real = r._hot

    def traced():
        c = real()
        c.set_trace_callback(calls.append)
        return c
    r._hot = traced
    out = r.get_monthly_report(2026, 9)
    att = [q for q in calls if 'attendance_records' in q]
    assert len(att) <= 30, f'استعلام لكل يوم مش لكل موظف: {len(att)}'
    assert not any('DATE(check_time)' in q for q in att), 'بمدى وقت عشان الفهرس'
    assert out['summary']['total_employees'] == 20


def test_day_punches_are_ordered_by_real_time(rep):
    r, db = rep
    d = [x for x in r.get_daily_report('2026-09-07') if x['employee_id'] == 1][0]
    assert d['checkin_time'] == '07:50' and d['all_times'][0] == '07:50'
    assert d['checkout_time'] == '17:05'
    other = [x for x in r.get_daily_report('2026-09-08') if x['employee_id'] == 2][0]
    assert other['checkin_time'] == '08:12' and other['status'] == 'حاضر'


def test_attendance_indexes_exist_after_init(rep):
    r, db = rep
    names = {x[0] for x in sqlite3.connect(db.DB_PATH).execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'attendance_records'")}
    assert {'idx_attendance_employee_time', 'idx_attendance_time_type'} <= names
