# -*- coding: utf-8 -*-
"""تسوية البصمة: بصمات ناقصة بتتكمّل بكتاب معتمد — متعلّم عليها، ومتسجّلة، وبتوصل أوراكل."""
import importlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active,"
                " weekly_leave_selected_days) VALUES (1, 'أحمد', '365', 'D', 'P', '2025-01-01', 500, 1, '5')")  # الجمعة
    for t, ct in (('2026-10-04 07:55:00', 0), ('2026-10-04 12:10:00', 0), ('2026-10-04 16:05:00', 1),
                  ('2026-10-05 08:01:00', 0), ('2026-10-05 02:00:00', 0)):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, ?, ?)", (t, ct))
    con.commit()
    import utils.punch_reconcile as pr
    importlib.reload(pr)
    yield db, pr, con


W = {'in': (5, 11), 'pres': (11, 14), 'out': (14, 24)}


def test_month_calendar_holes_codes_and_orphans(env):
    db, pr, con = env
    d = pr.build(con, '365', 10, 2026, W)
    day4 = next(x for x in d['calendar'] if x['date'] == '2026-10-04')
    day5 = next(x for x in d['calendar'] if x['date'] == '2026-10-05')
    fri = next(x for x in d['calendar'] if x['date'] == '2026-10-02')
    assert (day4['actual_entry'], day4['actual_pres'], day4['actual_exit']) == ('07:55:00', '12:10:00', '16:05:00')
    assert not day4['holed'] and day5['holed'] and fri['is_weekend'] and not fri['holed']
    assert d['codes'] == {'in': '0', 'pres': '0', 'out': '1'}, 'الأكواد من بصمات الجهاز'
    assert d['orphans'] == [{'date': '2026-10-05', 'time': '02:00:00', 'type': '0'}]
    _w, err = pr.parse_windows({'in_from': 5, 'in_to': 12, 'pres_from': 11, 'pres_to': 14, 'out_from': 14, 'out_to': 24})
    assert err, 'نوافذ متداخلة مرفوضة'


def test_save_marks_logs_replaces_and_reaches_oracle_queue(env):
    db, pr, con = env
    import utils.oracle_db as odb
    importlib.reload(odb)
    db.set_setting('oracle_enabled', '1')
    db.set_setting('oracle_host', 'x')
    odb.refresh_state()
    d = pr.build(con, '365', 10, 2026, W)
    vals = {'entry_2026-10-05': '08:30', 'pres_2026-10-05': '12:00:00', 'exit_2026-10-05': '16:00:00',
            'pres_2026-10-04': '12:30:00', 'exit_2026-10-06': '09:00:00'}
    res = pr.save(con, '365', ['2026-10-05', '2026-10-04', '2026-10-06'], vals, W, d['codes'], 'ك-77', by_user=1)
    assert res['saved'] == 4 and res['replaced'] == 2, res
    assert any('مش جوه نافذة' in e for e in res['errors']), 'خروج ٩ الصبح مرفوض'
    rows = con.execute("SELECT check_time, check_type, source, note FROM attendance_records WHERE source = 'adjustment' ORDER BY check_time").fetchall()
    assert [r['check_time'] for r in rows] == ['2026-10-04 12:30:00', '2026-10-05 08:30:00', '2026-10-05 12:00:00', '2026-10-05 16:00:00']
    assert all('ك-77' in r['note'] for r in rows), 'متعلّم عليها برقم الكتاب'
    assert rows[-1]['check_type'] == 1
    assert not con.execute("SELECT 1 FROM attendance_records WHERE check_time IN ('2026-10-04 12:10:00', '2026-10-05 08:01:00')").fetchone()
    log = con.execute("SELECT * FROM punch_adjustments WHERE day = '2026-10-04'").fetchone()
    assert log['book_no'] == 'ك-77' and '12:10:00' in log['old_times'] and log['user_id'] == 1
    q = [r[0] for r in con.execute("SELECT check_time FROM oracle_sync_queue WHERE op = 'INSERT' ORDER BY id")]
    assert '2026-10-05 08:30:00' in q, 'التسوية بتروح أوراكل'
    assert pr.save(con, '365', ['2026-10-05'], {}, W, d['codes'], '')['errors'] == ['رقم الكتاب المعتمد مطلوب']


def test_page_needs_its_own_password(env):
    db, pr, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    c = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    page = c.get('/attendance/oracle/reconcile?user_id=365&month=10&year=2026').get_data(as_text=True)
    assert 'بباسورد لوحدها' in page and '2026-10-05' not in page
    c.post('/attendance/oracle/reconcile', data={'action': 'unlock', 'password': 'غلط'})
    assert 'بباسورد لوحدها' in c.get('/attendance/oracle/reconcile').get_data(as_text=True)
    c.post('/attendance/oracle/reconcile', data={'action': 'unlock', 'password': '11225588'})
    page = c.get('/attendance/oracle/reconcile?user_id=365&month=10&year=2026').get_data(as_text=True)
    assert 'rc-holed' in page and '2026-10-05' in page and 'رقم الكتاب المعتمد' in page
    r = c.post('/attendance/oracle/reconcile', data={
        'action': 'save', 'user_id': '365', 'month': '10', 'year': '2026', 'selected_days': '2026-10-05',
        'entry_2026-10-05': '08:20', 'pres_2026-10-05': '', 'exit_2026-10-05': '', 'book_no': '12',
        'in_from': 5, 'in_to': 11, 'pres_from': 11, 'pres_to': 14, 'out_from': 14, 'out_to': 24})
    assert r.status_code == 302
    assert con.execute("SELECT 1 FROM attendance_records WHERE check_time = '2026-10-05 08:20:00' AND source = 'adjustment'").fetchone()
    import json as _j
    assert _j.dumps('الباسورد الحالي غلط')[1:-1] in c.post('/attendance/oracle/reconcile', data={
        'action': 'password', 'old_password': 'x', 'new_password': '123456'}, follow_redirects=True).get_data(as_text=True)
    assert _j.dumps('اتغيّر الباسورد')[1:-1] in c.post('/attendance/oracle/reconcile', data={
        'action': 'password', 'old_password': '11225588', 'new_password': '999888'}, follow_redirects=True).get_data(as_text=True)
    assert '11225588' not in open(os.path.join(ROOT, 'templates', 'punch_reconcile.html'), encoding='utf-8').read()


def test_save_only_the_picked_cell(env):
    """علامة على خانة الخروج بس: يتحفظ الخروج بس، حتى لو الدخول والتواجد فيهم وقت مقترح."""
    db, pr, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    c = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    c.post('/attendance/oracle/reconcile', data={'action': 'unlock', 'password': '11225588'})
    page = c.get('/attendance/oracle/reconcile?user_id=365&month=10&year=2026').get_data(as_text=True)
    assert 'name="pick" value="exit_2026-10-06"' in page and 'حفظ الخانات المحددة' in page
    base = {'action': 'save', 'user_id': '365', 'month': '10', 'year': '2026', 'book_no': '77',
            'in_from': 5, 'in_to': 11, 'pres_from': 11, 'pres_to': 14, 'out_from': 14, 'out_to': 24,
            'entry_2026-10-06': '08:00', 'pres_2026-10-06': '12:00', 'exit_2026-10-06': '16:30',
            'selected_days': '2026-10-06'}
    r = c.post('/attendance/oracle/reconcile', data=dict(base, pick='exit_2026-10-06'))
    assert r.status_code == 302
    got = [x[0] for x in con.execute("SELECT check_time FROM attendance_records WHERE source = 'adjustment'"
                                     " AND check_time LIKE '2026-10-06%' ORDER BY check_time")]
    assert got == ['2026-10-06 16:30:00'], got
    # خانتين من يومين مختلفين
    r = c.post('/attendance/oracle/reconcile', data=dict(base, pick=['entry_2026-10-07', 'pres_2026-10-08'],
                                                         **{'entry_2026-10-07': '08:10', 'pres_2026-10-08': '12:10'}))
    got = sorted(x[0] for x in con.execute("SELECT check_time FROM attendance_records WHERE source = 'adjustment'"
                                           " AND check_time >= '2026-10-07'"))
    assert got == ['2026-10-07 08:10:00', '2026-10-08 12:10:00'], got
