# -*- coding: utf-8 -*-
"""مراجعة نظام المناديب (٢.٣١) — كلُّ اختبارٍ هنا أعطالٌ وُجدت فعلًا.

- المندوبُ يُحسب غائبًا الشهرَ كلَّه: الرحلاتُ والزياراتُ لم تكن حضورًا.
- كلُّ نقطةٍ من شاشة الويب متأخّرةٌ ثلاث ساعات (توقيت غرينتش يُخزَّن محلّيًّا).
- نقاطٌ تضيع (دفعةٌ أكبر من ٥٠٠) وتتكرّر (دفعةٌ أُعيدت).
- مندوبٌ واقفٌ ساعةً يُحسب له ١٣ كم من ارتجاف الإشارة.
- رحلةُ أمس «جارية» إلى الأبد، وزيارةُ الأسبوع الماضي «مفتوحة» اليوم.
- الخروجُ يُقبل من ٤٧ كم، والمندوبُ «داخل» محطتين معًا.
"""
import io
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_field_app_api import rep, STATION  # noqa: E402,F401  (fixture)
from utils import field  # noqa: E402


def _conn(tmp_path):
    import importlib
    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import utils.db as db
    importlib.reload(db)
    importlib.reload(field)
    db.init_db()
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.row_factory = sqlite3.Row
    field.init_schema(con)
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date,"
                " salary, is_active, work_mode) VALUES (7, 'مندوب', '7', 'D', 'P', '2026-01-01', 600, 1, 'field')")
    con.execute("INSERT INTO field_stations (id, name, latitude, longitude, radius_meters) "
                "VALUES (1, 'صيدلية', 29.3759, 47.9774, 100)")
    con.commit()
    return con


def _ts(minutes_ago=0):
    return (datetime.now() - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%d %H:%M:%S')


def _att(con, emp=7):
    return [(r['check_type'], r['source']) for r in con.execute(
        'SELECT check_type, source FROM attendance_records WHERE employee_id = ? ORDER BY check_time, id',
        (emp,))]


# ------------------------------------------------------------ الحضورُ من الرحلة

def test_starting_a_trip_is_a_check_in_and_points_move_the_check_out(tmp_path):
    con = _conn(tmp_path)
    trip, created = field.open_trip(con, 7)
    assert created and _att(con) == [(1, 'field_trip')]
    field.add_points(con, trip, 7, [{'lat': 29.37, 'lon': 47.97, 'accuracy': 10, 'at': _ts(0)}])
    assert (0, 'field_trip') in _att(con), 'آخرُ نقطةٍ انصراف — لمن ينسى إنهاء الرحلة'
    field.close_trip(con, trip)
    rows = _att(con)
    assert rows.count((1, 'field_trip')) == 1 and rows.count((0, 'field_trip')) == 1, rows


def test_a_second_trip_the_same_day_keeps_one_check_in_and_one_check_out(tmp_path):
    con = _conn(tmp_path)
    t1, _ = field.open_trip(con, 7)
    field.close_trip(con, t1)
    t2, created = field.open_trip(con, 7)
    assert created
    field.close_trip(con, t2)
    rows = _att(con)
    assert rows.count((1, 'field_trip')) == 1 and rows.count((0, 'field_trip')) == 1


def test_a_field_rep_who_worked_the_month_is_not_paid_zero(tmp_path):
    """العطبُ الأخطر: ٣٠ يومَ رحلات ← صافي صفر، والراتبُ كلُّه خصمُ غياب."""
    con = _conn(tmp_path)
    for d in range(1, 31):
        day = datetime(2026, 9, d, 8, 0)
        field._attendance_mark(con, 7, 'in', day, 'بداية رحلة المندوب')
        field._attendance_mark(con, 7, 'out', day.replace(hour=16), 'نهاية رحلة المندوب')
    con.commit()
    from utils.payroll_engine import compute_monthly_payroll
    row = compute_monthly_payroll(con, 9, 2026)['rows'][0]
    assert row['net'] > 500, row['net']


def test_a_visit_check_in_is_a_presence_punch(rep):
    from tests.test_field_app_api import _camera_jpeg
    rep['client'].post('/portal/api/field/start', json={})
    r = rep['check_in'](_camera_jpeg(datetime.now()))
    assert r.get_json()['success'], r.get_json()
    con = sqlite3.connect(rep['db'])
    kinds = {row[0] for row in con.execute(
        "SELECT source FROM attendance_records WHERE employee_id = ?", (rep['emp_id'],))}
    assert {'field_trip', 'field_visit'} <= kinds


def test_an_approved_period_is_not_touched(tmp_path, monkeypatch):
    con = _conn(tmp_path)
    monkeypatch.setattr(field, '_period_locked', lambda c, d: True)
    field.open_trip(con, 7)
    assert _att(con) == []


# ------------------------------------------------------------ الوقت

def test_epoch_and_utc_times_become_local_time():
    now = datetime.now().replace(microsecond=0)
    assert field.to_local(now.timestamp() * 1000) == now
    assert field.to_local(now.timestamp()) == now
    utc = datetime.utcfromtimestamp(now.timestamp()).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    assert field.to_local(utc) == now, 'غرينتش من toISOString() يصير توقيت الجهاز'
    assert field.to_local('2026-10-01 08:00:00') == datetime(2026, 10, 1, 8, 0)


def test_the_web_screen_sends_epoch_time_not_utc_text():
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'portal', 'trip.html'), encoding='utf-8').read()
    assert 't: pos.timestamp' in html
    assert ".toISOString().slice(0, 19)" not in html


# ------------------------------------------------------------ النقاط

def test_points_are_validated_deduplicated_and_kept_to_the_trip_day(tmp_path):
    con = _conn(tmp_path)
    trip, _ = field.open_trip(con, 7)
    now_ms = datetime.now().timestamp() * 1000
    pts = [
        {'lat': 29.37, 'lon': 47.97, 'accuracy': 10, 't': now_ms},
        {'lat': 29.37, 'lon': 47.97, 'accuracy': 10, 't': now_ms},                  # مكرّرة
        {'lat': 29.38, 'lon': 47.97, 'accuracy': 5000, 't': now_ms},                # برج اتصالات
        {'lat': 29.38, 'lon': 47.97, 'accuracy': 10, 'at': _ts(60 * 13)},           # أقدم من ١٢ ساعة
        {'lat': 29.38, 'lon': 47.97, 'accuracy': 10, 't': now_ms + 3600_000},       # من المستقبل
    ]
    assert field.add_points(con, trip, 7, pts) == 1
    assert field.add_points(con, trip, 7, pts[:1]) == 0, 'دفعةٌ أُعيد إرسالُها لا تتكرّر'


def test_a_long_offline_batch_is_processed_500_at_a_time(rep):
    c = rep['client']
    trip = c.post('/portal/api/field/start', json={}).get_json()['trip_id']
    base = datetime.now().timestamp() * 1000 - 1200 * 1000
    pts = [{'lat': 29.3 + i * 1e-4, 'lon': 47.9, 'accuracy': 8, 't': base + i * 1000} for i in range(1200)]
    sent = 0
    while pts:
        j = c.post('/portal/api/field/track', json={'trip_id': trip, 'points': pts}).get_json()
        assert j['success'] and j['processed'] <= 500
        sent += j['accepted']
        pts = pts[j['processed']:]
    assert sent == 1200, 'كان يُقبل ٥٠٠ ويُمحى الباقي'


def test_yesterdays_open_trip_does_not_take_todays_points(rep):
    c = rep['client']
    con = sqlite3.connect(rep['db'])
    y = (datetime.now() - timedelta(days=1))
    cur = con.execute("INSERT INTO field_trips (employee_id, trip_date, started_at) VALUES (?, ?, ?)",
                      (rep['emp_id'], y.strftime('%Y-%m-%d'), y.strftime('%Y-%m-%d 08:00:00')))
    old = cur.lastrowid
    con.execute("INSERT INTO field_track_points (trip_id, employee_id, latitude, longitude, recorded_at)"
                " VALUES (?, ?, 29.3, 47.9, ?)", (old, rep['emp_id'], y.strftime('%Y-%m-%d 15:30:00')))
    con.commit()
    j = c.post('/portal/api/field/track', json={'trip_id': old, 'points': [
        {'lat': 29.3, 'lon': 47.9, 't': datetime.now().timestamp() * 1000}]}).get_json()
    assert j['code'] == 'stale'
    ended = con.execute('SELECT ended_at, end_reason FROM field_trips WHERE id = ?', (old,)).fetchone()
    assert ended == (y.strftime('%Y-%m-%d 15:30:00'), 'auto'), 'تنتهي عند آخر نقطةٍ منها'
    plan = c.get('/portal/api/field/plan').get_json()
    assert plan['trip'] is None, 'لا رحلةَ لليوم بعد — الشاشةُ تعرض «ابدأ»'
    trip = c.post('/portal/api/field/start', json={}).get_json()['trip_id']
    assert c.get('/portal/api/field/plan').get_json()['trip']['id'] == trip, 'وتُستأنف بعد إعادة التحميل'


# ------------------------------------------------------------ المسافة

def test_standing_still_for_an_hour_is_not_kilometres():
    import random
    random.seed(4)
    base = datetime(2026, 10, 1, 10, 0)
    pts = [{'latitude': 29.3759 + random.uniform(-0.00018, 0.00018),
            'longitude': 47.9774 + random.uniform(-0.00018, 0.00018),
            'accuracy': 20, 'recorded_at': (base + timedelta(seconds=10 * i)).strftime('%Y-%m-%d %H:%M:%S')}
           for i in range(360)]
    _segs, stats = field.build_path(pts)
    assert stats['distance_meters'] < 150, stats['distance_meters']


def test_a_real_drive_is_still_measured():
    base = datetime(2026, 10, 1, 10, 0)
    # ٣ كم شمالًا بـ٣٦ كم/س، نقطةٌ كلَّ ٥ ثوانٍ (٥٠ م)
    pts = [{'latitude': 29.30 + i * 50 / 111_320, 'longitude': 47.90, 'accuracy': 8,
            'recorded_at': (base + timedelta(seconds=5 * i)).strftime('%Y-%m-%d %H:%M:%S')}
           for i in range(61)]
    _segs, stats = field.build_path(pts)
    assert 2900 <= stats['distance_meters'] <= 3050, stats['distance_meters']


def test_poor_accuracy_points_are_kept_but_not_drawn_or_counted():
    base = datetime(2026, 10, 1, 10, 0)
    pts = [{'latitude': 29.30, 'longitude': 47.90, 'accuracy': 8, 'recorded_at': base.strftime('%Y-%m-%d %H:%M:%S')},
           {'latitude': 29.35, 'longitude': 47.90, 'accuracy': 400,
            'recorded_at': (base + timedelta(seconds=30)).strftime('%Y-%m-%d %H:%M:%S')}]
    _segs, stats = field.build_path(pts)
    assert stats['distance_meters'] == 0 and stats['poor_points'] == 1


def test_the_monitor_queries_use_an_index(tmp_path):
    con = _conn(tmp_path)
    plan = ' '.join(str(r[-1]) for r in con.execute(
        'EXPLAIN QUERY PLAN SELECT recorded_at FROM field_track_points '
        'WHERE employee_id = ? AND recorded_at >= ? AND recorded_at < ? ORDER BY recorded_at DESC LIMIT 1',
        (7, '2026-10-01', '2026-10-02')))
    assert 'idx_ftp_emp' in plan, plan
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'routes', 'field_routes.py'), encoding='utf-8').read()
    assert 'DATE(recorded_at) = ?' not in src.replace('`DATE(recorded_at) = ?`', '')


# ------------------------------------------------------------ الزيارات

def test_forgotten_visits_and_trips_close_and_count_as_missed(tmp_path):
    con = _conn(tmp_path)
    y = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    con.execute("INSERT INTO field_assignments (employee_id, station_id, visit_date) VALUES (7, 1, ?)", (y,))
    con.execute("INSERT INTO field_visits (employee_id, station_id, check_in_at, status) "
                "VALUES (7, 1, ?, 'open')", (y + ' 10:00:00',))
    con.commit()
    field.close_stale(con)
    assert field.open_visit(con, 7) is None, 'زيارةُ أمس لا تجعله «داخل محطة» اليوم'
    s = field.day_summary(con, 7, y)
    assert s['stations_done'] == 0 and s['missed'] == ['صيدلية (بلا خروج)'], s


def test_check_out_must_be_at_the_station(rep):
    from tests.test_field_app_api import _camera_jpeg
    c = rep['client']
    c.post('/portal/api/field/start', json={})
    v = rep['check_in'](_camera_jpeg(datetime.now())).get_json()
    r = c.post('/portal/api/field/check-out', data={
        'visit_id': str(v['visit_id']), 'latitude': '29.80', 'longitude': '47.97', 'accuracy': '10',
        'token': rep['token']('out'),
        'photo': (io.BytesIO(_camera_jpeg(datetime.now())), 'shot.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400 and 'تبعد' in r.get_json()['message']


def test_one_open_visit_at_a_time_and_stop_warns(rep):
    from tests.test_field_app_api import _camera_jpeg
    c = rep['client']
    con = sqlite3.connect(rep['db'])
    cur = con.execute('INSERT INTO field_stations (name, latitude, longitude, radius_meters, is_active) '
                      'VALUES (?, ?, ?, 100, 1)', ('محطة ٢', STATION[0], STATION[1]))
    s2 = cur.lastrowid
    con.execute('INSERT INTO field_assignments (employee_id, station_id, visit_date) VALUES (?, ?, ?)',
                (rep['emp_id'], s2, datetime.now().strftime('%Y-%m-%d')))
    con.commit()
    trip = c.post('/portal/api/field/start', json={}).get_json()['trip_id']
    assert rep['check_in'](_camera_jpeg(datetime.now())).get_json()['success']
    tok = c.post('/portal/api/field/token', json={'station_id': s2, 'kind': 'in'}).get_json()['token']
    r = c.post('/portal/api/field/check-in', data={
        'station_id': str(s2), 'latitude': str(STATION[0]), 'longitude': str(STATION[1]), 'accuracy': '10',
        'token': tok, 'photo': (io.BytesIO(_camera_jpeg(datetime.now())), 'shot.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')
    assert r.status_code == 400 and r.get_json()['code'] == 'open_visit'
    r = c.post('/portal/api/field/stop', json={'trip_id': trip})
    assert r.status_code == 409 and r.get_json()['code'] == 'open_visit'
    assert c.post('/portal/api/field/stop', json={'trip_id': trip, 'force': True}).get_json()['success']


def test_check_in_ignores_someone_elses_trip_id(rep):
    from tests.test_field_app_api import _camera_jpeg
    c = rep['client']
    mine = c.post('/portal/api/field/start', json={}).get_json()['trip_id']
    con = sqlite3.connect(rep['db'])
    other = con.execute("INSERT INTO field_trips (employee_id, trip_date, started_at) VALUES (999, ?, ?)",
                        (datetime.now().strftime('%Y-%m-%d'), _ts())).lastrowid
    con.commit()
    r = c.post('/portal/api/field/check-in', data={
        'station_id': str(rep['station_id']), 'latitude': str(STATION[0]), 'longitude': str(STATION[1]),
        'accuracy': '10', 'trip_id': str(other), 'token': rep['token']('in'),
        'photo': (io.BytesIO(_camera_jpeg(datetime.now())), 'shot.jpg', 'image/jpeg'),
    }, content_type='multipart/form-data')
    vid = r.get_json()['visit_id']
    assert con.execute('SELECT trip_id FROM field_visits WHERE id = ?', (vid,)).fetchone()[0] == mine


def test_the_trip_page_is_kept_for_offline_use():
    sw = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'portal', 'sw.js'), encoding='utf-8').read()
    assert "'/portal/trip'" in sw and 'KEEP_FRESH' in sw


# ------------------------------------------------------------ الخطط والجدول والصلاحيات

def _admin(rep):
    from werkzeug.security import generate_password_hash
    con = sqlite3.connect(rep['db'])
    uid = con.execute("INSERT INTO users (username, password, full_name, role, is_active) "
                      "VALUES ('boss', ?, 'مدير', 'admin', 1)", (generate_password_hash('x'),)).lastrowid
    con.commit()
    import app as A
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin'})
    return c


def test_a_rep_cannot_read_stations_territories_or_everyones_plans(rep):
    c = rep['client']
    for url in ('/api/field/stations', '/api/field/territories', '/api/field/assignments'):
        assert c.get(url).status_code == 403, url
    assert _admin(rep).get('/api/field/stations').status_code == 200


def test_an_admin_can_cancel_a_day_and_it_stays_cancelled(rep):
    a = _admin(rep)
    con = sqlite3.connect(rep['db'])
    today = datetime.now().strftime('%Y-%m-%d')
    sid = con.execute("INSERT INTO field_schedules (employee_id, starts_on) VALUES (?, ?)",
                      (rep['emp_id'], '2026-01-01')).lastrowid
    for wd in range(7):
        con.execute('INSERT INTO field_schedule_days (schedule_id, weekday, station_id) VALUES (?, ?, ?)',
                    (sid, wd, rep['station_id']))
    con.commit()
    r = a.post('/api/field/assignments', json={'employee_id': rep['emp_id'], 'station_ids': [],
                                               'visit_date': today}).get_json()
    assert r['success'] and r['plan'] == []
    plan = a.get(f"/api/field/assignments?employee_id={rep['emp_id']}&date={today}").get_json()['plan']
    assert plan == [], 'كانت الخطةُ الملغاةُ ترجع من الجدول عند أوّل قراءة'


def test_no_plan_is_generated_on_leave_or_a_public_holiday(tmp_path):
    con = _conn(tmp_path)
    sid = con.execute("INSERT INTO field_schedules (employee_id, starts_on) VALUES (7, '2026-01-01')").lastrowid
    for wd in range(7):
        con.execute('INSERT INTO field_schedule_days (schedule_id, weekday, station_id) VALUES (?, ?, 1)', (sid, wd))
    lt = con.execute("SELECT id FROM leave_types LIMIT 1").fetchone()[0]
    con.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date, end_date, status, days_count)"
                " VALUES (7, ?, '2026-11-02', '2026-11-03', 'approved', 2)", (lt,))
    con.execute("INSERT INTO official_holidays (name, date, type) VALUES ('عيد', '2026-11-10', 'national')")
    con.commit()
    assert field.generate_day(con, 7, '2026-11-02') == 0, 'إجازة'
    assert field.generate_day(con, 7, '2026-11-10') == 0, 'عطلة رسمية'
    assert field.generate_day(con, 7, '2026-11-04') == 1


def test_past_days_cannot_be_rewritten(rep):
    a = _admin(rep)
    y = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    r = a.post('/api/field/assignments', json={'employee_id': rep['emp_id'], 'station_ids': [], 'visit_date': y})
    assert r.status_code == 400
    r = a.post('/api/field/schedule', json={'employee_id': rep['emp_id'], 'starts_on': '2026-01-01', 'days': {}})
    assert r.status_code == 400


def test_deactivating_a_station_does_not_change_past_results(tmp_path):
    con = _conn(tmp_path)
    y = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    con.execute("INSERT INTO field_assignments (employee_id, station_id, visit_date) VALUES (7, 1, ?)", (y,))
    con.execute("UPDATE field_stations SET is_active = 0 WHERE id = 1")
    con.commit()
    assert field.day_summary(con, 7, y)['missed'] == ['صيدلية'], 'كان إيقافُها يمحوها من الماضي'
    t = datetime.now().strftime('%Y-%m-%d')
    con.execute("INSERT INTO field_assignments (employee_id, station_id, visit_date) VALUES (7, 1, ?)", (t,))
    con.commit()
    assert field.day_plan(con, 7, t, autogenerate=False) == [], 'وتختفي من اليوم وما بعده'


def test_saving_a_schedule_does_not_mix_into_a_hand_made_day(tmp_path):
    con = _conn(tmp_path)
    t = datetime.now().strftime('%Y-%m-%d')
    con.execute("INSERT INTO field_stations (id, name, latitude, longitude) VALUES (2, 'ب', 29.3, 47.9)")
    con.execute("INSERT INTO field_assignments (employee_id, station_id, visit_date, source) VALUES (7, 1, ?, 'manual')", (t,))
    con.commit()
    field.save_schedule(con, 7, t, {str(wd): [2] for wd in range(7)})
    assert field.generate_day(con, 7, t, force=True) == 0
    assert [p['station_id'] for p in field.day_plan(con, 7, t, autogenerate=False)] == [1]


def test_a_schedule_of_another_employee_is_not_readable_by_id(rep):
    a = _admin(rep)
    con = sqlite3.connect(rep['db'])
    other = con.execute("INSERT INTO field_schedules (employee_id, starts_on) VALUES (999, '2026-01-01')").lastrowid
    con.commit()
    r = a.get(f"/api/field/schedule?employee_id={rep['emp_id']}&schedule_id={other}")
    assert r.status_code == 404


def test_preview_shows_the_unsaved_draft(rep):
    import json as _json
    a = _admin(rep)
    days = {str(wd): [rep['station_id']] for wd in range(7)}
    r = a.get(f"/api/field/schedule/preview?employee_id={rep['emp_id']}&days=" + _json.dumps(days)).get_json()
    assert r['success'] and all(d['stations'] == ['صيدلية الروضة'] for d in r['days'] if not d['off'])


def test_a_field_employee_gets_the_punch_button_when_the_module_is_off(rep):
    con = sqlite3.connect(rep['db'])
    con.execute("UPDATE employees SET work_mode = 'field' WHERE id = ?", (rep['emp_id'],))
    field.set_module_enabled(con, False)
    con.commit()
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            'routes', 'portal_routes.py'), encoding='utf-8').read()
    i = src.index('if _field.module_enabled(conn):')
    assert src.index('office_punch = _field.punches_at_office', i) > i, 'البصمةُ تُخفى فقط والوحدةُ مُفعّلة'


def test_the_monitor_keeps_opened_visits_on_auto_refresh():
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'field_monitor.html'), encoding='utf-8').read()
    assert 'keptVisits' in html
