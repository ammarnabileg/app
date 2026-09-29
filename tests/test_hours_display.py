"""الساعاتُ «ساعات:دقائق»، والتأخيرُ الفعليّ بجوار المحتسَب.

## ما وقع

عميلٌ قرأ في اعتماد الساعات «8.85» فسأل: كيف والساعةُ ستّون دقيقة؟ الرقمُ
صحيح — 8 ساعات و0.85 من ساعة = 8:51 — لكنه يُقرأ «8 ساعات و85 دقيقة».
وقرأ «تأخير 120 د» ليومٍ تأخّر فيه 13 دقيقة: سياسةُ «ربع يوم» تحسبه ربعَ
شفت (120 د من 480)، والشاشةُ لا تقول ذلك.

الآن: 8:51، و«تأخير 13 د (يُحسب 120 د — ربع يوم)».

يُشغَّل:  python -m pytest tests/test_hours_display.py -v
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ------------------------------------------------ الصيغة

@pytest.mark.parametrize('hours, shown', [
    (8.85, '8:51'), (8.8167, '8:49'), (9.0, '9:00'), (0, '0:00'),
    (185.52, '185:31'), (-0.5, '-0:30'), (8.999, '9:00'),
])
def test_decimal_hours_read_as_hours_and_minutes(hours, shown):
    import app as A
    assert A.hours_hm_filter(hours) == shown


def test_the_filter_leaves_non_numbers_alone():
    import app as A
    assert A.hours_hm_filter('--') == '--'


# ------------------------------------------------ اليوم

@pytest.fixture
def env(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    conn = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    conn.row_factory = sqlite3.Row
    conn.execute("INSERT INTO shift_types (name, start_time, end_time, hours_per_day, flex_mode, is_active)"
                 " VALUES ('صباحي-عرض', '08:00', '17:00', 8, 'none', 1)")
    conn.execute("INSERT INTO employees (id, employee_number, name, department, position, hire_date,"
                 " salary, is_active, shift_type) VALUES (1,'8','موظف','الإدارة','فنّي','2025-01-01',"
                 " 300, 1, 'صباحي-عرض')")
    conn.execute("UPDATE salary_settings_v2 SET setting_value = 'quarter_day'"
                 " WHERE setting_name = 'late_arrival_policy'")
    if conn.execute("SELECT changes()").fetchone()[0] == 0:
        conn.execute("INSERT INTO salary_settings_v2 (setting_name, setting_value) VALUES"
                     " ('late_arrival_policy', 'quarter_day')")
    conn.commit()
    import utils.payroll_engine as pe
    importlib.reload(pe)
    return conn, pe


def _punch(conn, day, *times):
    for t in times:
        conn.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                     " VALUES (1, 'T1', ?, 'I')", (f'{day} {t}',))
    conn.commit()


def _days(conn, pe):
    out = pe.compute_employee_days(conn, 1, 8, 2026)
    return {d['date']: d for d in out['days']}, out['period']


def test_a_quarter_day_late_carries_the_actual_minutes(env):
    conn, pe = env
    _, period = _days(conn, pe)
    # يومان داخل الدورة، أيًّا كان يومُ بدايتها.
    from datetime import date, timedelta
    d0 = date.fromisoformat(period['start'])
    days = [d0 + timedelta(days=i) for i in range(10)]
    work = [d for d in days if (d.isoweekday() % 7) not in (5, 6)][:2]
    _punch(conn, work[0].isoformat(), '08:08:12', '16:59:08')
    _punch(conn, work[1].isoformat(), '08:13:05', '17:02:00')
    rows, _ = _days(conn, pe)

    on_time, late = rows[work[0].isoformat()], rows[work[1].isoformat()]
    assert round(on_time['hours'], 2) == 8.85
    assert on_time['late_mins'] == 0 and on_time['late_actual'] == 0
    assert late['late_mins'] == 120, 'ربعُ شفت 8 ساعات'
    assert late['late_actual'] == 13
    assert late['late_policy'] == 'quarter_day'


def test_actual_time_policy_shows_one_number(env):
    conn, pe = env
    conn.execute("UPDATE salary_settings_v2 SET setting_value = 'actual_time'"
                 " WHERE setting_name = 'late_arrival_policy'")
    conn.commit()
    _, period = _days(conn, pe)
    from datetime import date, timedelta
    d0 = date.fromisoformat(period['start'])
    work = [d for d in (d0 + timedelta(days=i) for i in range(10)) if (d.isoweekday() % 7) not in (5, 6)][0]
    _punch(conn, work.isoformat(), '08:13:05', '17:02:00')
    row = _days(conn, pe)[0][work.isoformat()]
    assert row['late_mins'] == 13 and row['late_actual'] == 13


# ------------------------------------------------ الطباعة

def test_the_print_view_shows_hm_and_both_late_numbers(env, tmp_path):
    conn, pe = env
    _, period = _days(conn, pe)
    from datetime import date, timedelta
    d0 = date.fromisoformat(period['start'])
    work = [d for d in (d0 + timedelta(days=i) for i in range(10)) if (d.isoweekday() % 7) not in (5, 6)][:2]
    _punch(conn, work[0].isoformat(), '08:08:12', '16:59:08')
    _punch(conn, work[1].isoformat(), '08:13:05', '17:02:00')

    import importlib
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ != 'check_license_globally']
    A.app.config['TESTING'] = True
    admin = conn.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = admin
        s['role'] = 'admin'
        s['username'] = 'admin'
    r = c.get('/payroll/hours_approval/print?month=8&year=2026')
    body = r.get_data(as_text=True)
    assert r.status_code == 200, r.status_code
    assert '>8:51<' in body, 'الساعاتُ ساعات:دقائق'
    assert '>8.85<' not in body
    import re as _re
    notes = [n for n in _re.findall(r'class="notes">(.*?)</td>', body, _re.S) if '120' in n]
    assert notes and '13' in notes[0] and 'ربع يوم' in notes[0], notes
