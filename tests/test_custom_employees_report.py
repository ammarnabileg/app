# -*- coding: utf-8 -*-
"""التقرير الشامل (/fingerprint/custom_employees_report و monthly_employees_report_new).

كان:
  * يوم الإجازة الأسبوعية بلا بصمة يُحسب **غيابًا ويُخصم** — التقرير الذكي يكتب «إجازة أسبوعية»
    والـ API يبحث عن «عطلة أسبوعية»؛
  * شغلُ يوم الراحة أو العطلة الرسمية يُحسب حضورًا عاديًّا بتأخيرٍ وانصراف مبكر، بلا أوفرتايم؛
  * أعمدة الساعات المطلوبة/الفعلية/الناقصة و«مرضي/طارئ» صفرٌ ثابت؛
  * الساعات تُعرض بكسر (9.82) لا «9:49».
"""
import importlib
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active,"
                " weekly_leave_selected_days, default_start_time, default_end_time)"
                " VALUES (1, 'A', '101', 'D', 'P', '2025-01-01', 300, 1, '5,6', '08:00', '16:00')")
    # 2026-09-01 ثلاثاء … 09-07 اثنين. الجمعة 4 والسبت 5 راحة. الاثنين 7 عطلة رسمية.
    for t in ('2026-09-01 08:10:00', '2026-09-01 17:59:00',      # 9:49
              '2026-09-02 08:00:00',                             # بصمة واحدة
              '2026-09-04 09:00:00', '2026-09-04 13:00:00',      # شغل الجمعة 4 ساعات
              '2026-09-06 08:00:00', '2026-09-06 16:00:00',
              '2026-09-07 08:00:00', '2026-09-07 16:00:00'):     # شغل العطلة 8 ساعات
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 1, ?, 0)", (t,))
    con.execute("INSERT INTO official_holidays (date, name, type) VALUES ('2026-09-07', 'X', 'national')")
    con.commit()
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    # smart_reports يأخذ مسار القاعدة وقت الاستيراد — يُعاد تحميله لكلّ قاعدة اختبار.
    import smart_reports
    importlib.reload(smart_reports)
    import routes.report_routes as RR
    importlib.reload(RR)
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    c = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    yield c, con
    con.close()


def _row(c):
    r = c.get('/api/fingerprint/custom_employees_report?start_date=2026-09-01&end_date=2026-09-07').get_json()
    assert r['success'], r
    return r['rows'][0]


def test_rest_days_and_holidays_are_not_absence_and_their_work_is_overtime(client):
    c, con = client
    row = _row(c)
    st = {x['date']: x for x in row['daily_cells']}
    assert st['2026-09-05']['status'] == 'weekly_off', 'السبت راحة — لا غياب'
    assert st['2026-09-04']['status'] == 'weekly_off' and st['2026-09-04']['overtime_minutes'] == 240
    assert st['2026-09-04']['late_minutes'] == 0, 'لا تأخير في يوم الراحة'
    assert st['2026-09-07']['status'] == 'holiday' and st['2026-09-07']['overtime_minutes'] == 480
    s, f = row['stats'], row['financials']
    assert (s['present_days'], s['absent_days'], s['weekly_off_days'], s['holiday_days']) == (3, 1, 2, 1)
    assert s['work_days'] == 7
    assert s['required_hours'] == 32 and s['basic_hours'] == 16 and s['missing_hours'] == 16
    assert s['overtime_hours'] == 12 and s['total_late_mins'] == 10 and s['total_early_leave_mins'] == 0
    # يوم غياب واحد (10)، وأوفرتايم: 4×1.5 + 8×2 = 22 ساعة × 1.25
    assert f['absent_deduction'] == 10 and f['overtime_reward'] == 27.5


def test_sick_leave_column(client):
    c, con = client
    lt = con.execute("INSERT INTO leave_types (name, days_per_year) VALUES ('إجازة مرضية تجربة', 15)").lastrowid
    con.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count)"
                " VALUES (1, ?, '2026-09-03', '2026-09-03', 'approved', 1)", (lt,))
    con.commit()
    s = _row(c)['stats']
    assert s['sick_days'] == 1 and s['leave_days'] == 1 and s['absent_days'] == 0
    assert s['work_days'] == 7, 'يوم الإجازة من أيام الفترة'


@pytest.mark.parametrize('tpl', ['custom_employees_report.html', 'monthly_employees_report_new.html'])
def test_templates_show_hours_as_hh_mm(tpl):
    src = open(os.path.join(ROOT, 'templates', tpl), encoding='utf-8').read()
    assert '? c.work_hours :' not in src, 'خلية اليوم بكسر (9.82)'
    assert 'total_overtime_mins / 60}' not in src and 'total_late_mins / 60}' not in src
    assert 'fmtHM(c.work_hours)' in src and 'fmtHM(r.stats.required_hours)' in src
    assert '>0</td>' not in src, 'مفيش أعمدة صفر ثابت'


def test_broken_symbols_are_gone():
    for tpl in ('attendance_new.html', 'custom_attendance_summary.html', 'attendance_summary.html'):
        src = open(os.path.join(ROOT, 'templates', tpl), encoding='utf-8').read()
        assert '&#1F5C4A;' not in src and '&#163F32;' not in src


def test_attendance_summary_shows_rest_days_and_leaves(client):
    c, con = client
    lt = con.execute("INSERT INTO leave_types (name, days_per_year) VALUES ('سنوية تجربة', 30)").lastrowid
    con.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count)"
                " VALUES (1, ?, '2026-09-03', '2026-09-03', 'approved', 1)", (lt,))
    con.commit()
    r = c.get('/api/fingerprint/custom_attendance_summary?start_date=2026-09-01&end_date=2026-09-07').get_json()
    row = next(x for x in r['rows'] if str(x['employee_number']) == '101')
    st = {x['date']: x['status'] for x in row['cells']}
    assert st['2026-09-05'] == 'weekly_off' and st['2026-09-03'] == 'leave'
