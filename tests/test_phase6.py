# -*- coding: utf-8 -*-
"""المرحلة ٦: الإعلانات والاستبيانات، الكشك وQR، الموقع المزيّف، وبدل المسافة."""
import importlib
import time
from datetime import date, datetime, timedelta

import pytest

from tests.test_device_access import env  # noqa: F401
from tests.test_device_drivers import web  # noqa: F401
from tests.test_hr_requests import _login, _user


@pytest.fixture
def G(env):
    db, da, con = env
    import utils.engagement as G
    importlib.reload(G)
    con.execute("UPDATE employees SET is_active = 1, department = 'مبيعات' WHERE id = 1")
    con.execute("UPDATE employees SET is_active = 1, department = 'حسابات' WHERE id = 3")
    con.commit()
    return G, con


def test_announcement_targets_department_and_tracks_reads(G):
    G, con = G
    emp = _user(con, 'emp', 1)
    other = _user(con, 'oth', 3)
    aid = G.create_announcement(con, 'اجتماع', 'الساعة 10', 'department', 'مبيعات')
    assert [a['id'] for a in G.announcements_for(con, 1)] == [aid]
    assert not G.announcements_for(con, 3)
    assert con.execute("SELECT COUNT(*) FROM notifications WHERE kind = 'announcement' AND user_id = ?", (emp,)).fetchone()[0] == 1
    assert not con.execute("SELECT 1 FROM notifications WHERE kind = 'announcement' AND user_id = ?", (other,)).fetchone()
    G.mark_read(con, aid, 1)
    G.mark_read(con, aid, 1)
    a = G.announcements_admin(con)[0]
    assert (a['read_count'], a['target_count']) == (1, 1)
    G.create_announcement(con, 'قديم', '', expires_on='2020-01-01', notify=False)
    assert len(G.announcements_for(con, 1)) == 1, 'المنتهي لا يظهر'


def test_anonymous_survey_once_per_employee(G):
    G, con = G
    sid = G.create_survey(con, 'رضا', [{'q': 'تقييمك', 'type': 'rating'},
                                       {'q': 'الأفضل', 'type': 'choice', 'options': ['أ', 'ب']},
                                       {'q': 'ملاحظات', 'type': 'text'}], notify=False)
    assert [s['id'] for s in G.surveys_for(con, 1)] == [sid]
    with pytest.raises(ValueError):
        G.answer(con, sid, 1, [9, 'أ', ''])
    G.answer(con, sid, 1, [4, 'ب', 'كويس'])
    with pytest.raises(ValueError):
        G.answer(con, sid, 1, [5, 'أ', ''])
    assert not G.surveys_for(con, 1)
    G.answer(con, sid, 3, ['2', 'ب', None])
    r = G.results(con, sid)
    assert r['responses'] == 2 and r['results'][0]['avg'] == 3.0
    assert dict(r['results'][1]['counts']) == {'أ': 0, 'ب': 2} and r['results'][2]['texts'] == ['كويس']
    assert r['who'] == [] and not con.execute('SELECT employee_id FROM survey_responses WHERE employee_id IS NOT NULL').fetchone()
    with pytest.raises(ValueError):
        G.create_survey(con, 'x', [{'q': 'y', 'type': 'choice', 'options': ['واحد']}])


# ------------------------------------------------------------ الكشك وQR

@pytest.fixture
def S(env):
    db, da, con = env
    import utils.smart_punch as S
    importlib.reload(S)
    S.ensure_schema(con)
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 1')
    con.commit()
    return S, con


def test_pin_lockout_and_record(S):
    S, con = S
    pin = S.set_pin(con, 1)
    assert len(pin) == 4 and pin.isdigit()
    assert S.verify_pin(con, '101', pin) == (1, None)
    for _ in range(S.PIN_MAX_FAILS):
        assert S.verify_pin(con, '101', 'x')[0] is None
    assert 'اتقفل' in S.verify_pin(con, '101', pin)[1], 'مقفول حتى بالرقم الصح'
    assert con.execute("SELECT kind FROM gps_flags").fetchone()[0] == 'pin_lock'
    with pytest.raises(ValueError):
        S.set_pin(con, 1, '12')
    S.set_pin(con, 1, '4321')
    assert S.verify_pin(con, '101', '4321') == (1, None), 'رقم جديد يفك القفل'
    t = datetime(2026, 10, 9, 8, 0)
    assert S.record(con, 1, 'kiosk', 'x', now=t)[0]
    ok, msg = S.record(con, 1, 'kiosk', 'x', now=t + timedelta(minutes=1))
    assert not ok and 'استنى' in msg
    assert S.record(con, 1, 'kiosk', 'x', now=t + timedelta(hours=9))[0]
    types = [r[0] for r in con.execute("SELECT check_type FROM attendance_records WHERE employee_id = 1 ORDER BY check_time")]
    assert types == [1, 2]


def test_qr_code_rotates(S):
    S, con = S
    k = {'token': 'secret-token'}
    now = 1_000_000.0
    c = S.qr_code(k, now)
    assert S.qr_valid(k, c, now) and S.qr_valid(k, c, now + S.QR_WINDOW), 'الكود السابق مقبول لحظيًّا'
    assert not S.qr_valid(k, c, now + 3 * S.QR_WINDOW), 'القديم يموت'
    assert not S.qr_valid({'token': 'other'}, c, now)
    assert '<svg' in S.qr_svg('https://x/portal/qr/1/abc')


def test_mock_and_teleport_flags(S):
    S, con = S
    assert S.check_mocked(con, 1, {'is_mocked': False}, 'p') is None
    assert S.check_mocked(con, 1, {'is_mocked': True, 'latitude': '29.3', 'longitude': '47.9'}, 'p') == S.MOCK_MESSAGE
    pts = [{'latitude': 29.30, 'longitude': 47.90, 'recorded_at': '2026-10-09 10:00:00'},
           {'latitude': 29.30, 'longitude': 47.95, 'recorded_at': '2026-10-09 10:05:00'},   # 4.8 كم/5 د: عادي
           {'latitude': 30.30, 'longitude': 47.95, 'recorded_at': '2026-10-09 10:06:00'}]   # 111 كم في دقيقة
    assert S.check_teleport(con, 1, pts) == 1
    kinds = [f['kind'] for f in S.flags(con)]
    assert sorted(kinds) == ['mock', 'teleport']


def test_kiosk_and_qr_over_http(web):
    c, con, A = web
    import utils.smart_punch as S
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 1')
    con.commit()
    c.post('/attendance/smart/kiosk', data={'name': 'استقبال', 'allow_pin': '1', 'allow_qr': '1', 'require_selfie': '1'})
    k = dict(con.execute('SELECT * FROM kiosks').fetchone())
    c.post('/attendance/smart/pin', data={'employee_id': 1, 'pin': '2468'})
    assert '2468' in c.get('/attendance/smart?tab=pins').get_data(as_text=True)
    anon = A.app.test_client()
    assert anon.get('/kiosk/wrong').status_code == 404
    assert 'استقبال' in anon.get(f"/kiosk/{k['token']}").get_data(as_text=True)
    assert anon.get(f"/kiosk/{k['token']}/qr.svg").mimetype == 'image/svg+xml'
    r = anon.post(f"/kiosk/{k['token']}/punch", json={'employee_number': '101', 'pin': '2468'})
    assert r.status_code == 400, 'الصورة إجبارية'
    png = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    r = anon.post(f"/kiosk/{k['token']}/punch", json={'employee_number': '101', 'pin': '2468', 'photo': png}).get_json()
    assert r['success'], r
    note = con.execute("SELECT note, source FROM attendance_records WHERE employee_id = 1").fetchone()
    assert note['source'] == 'kiosk' and 'صورة:' in note['note']
    # QR: موبايل الموظّف.
    con.execute('DELETE FROM attendance_records')
    con.commit()
    _user(con, 'emp', 1)
    _login(c, con, 'emp')
    assert c.post('/portal/api/qr-punch', json={'kiosk_id': k['id'], 'code': 'bad'}).status_code == 400
    r = c.post('/portal/api/qr-punch', json={'kiosk_id': k['id'], 'code': S.qr_code(k)}).get_json()
    assert r['success'] and con.execute("SELECT source FROM attendance_records").fetchone()[0] == 'qr'


def test_portal_punch_rejects_mocked_location(web):
    c, con, A = web
    _user(con, 'emp', 1)
    con.execute("DELETE FROM salary_settings_v2 WHERE setting_name = 'portal_attendance_enabled'")
    con.execute("INSERT INTO salary_settings_v2 (setting_name, setting_value) VALUES ('portal_attendance_enabled', '1')")
    con.commit()
    _login(c, con, 'emp')
    r = c.post('/portal/api/punch', json={'latitude': 29.3, 'longitude': 47.9, 'is_mocked': True})
    assert r.status_code == 403 and r.get_json()['code'] == 'mocked'
    assert con.execute("SELECT context FROM gps_flags").fetchone()[0] == 'portal_punch'


# ------------------------------------------------------------ بدل المسافة

def test_mileage_expense_from_trips(env):
    db, da, con = env
    import utils.hr_requests as H
    from utils import field
    from utils.db import set_setting
    field.init_schema(con)
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 1')
    tid = con.execute("INSERT INTO field_trips (employee_id, trip_date, started_at) VALUES (1, '2026-09-10', '2026-09-10 09:00:00')").lastrowid
    t0 = datetime(2026, 9, 10, 9, 0)
    for i in range(11):    # 10 خطوات × ~0.5 كم = ~5 كم، كلّ دقيقتين
        con.execute('INSERT INTO field_track_points (trip_id, employee_id, latitude, longitude, accuracy, recorded_at) '
                    'VALUES (?, 1, ?, 47.9, 5, ?)', (tid, 29.30 + i * 0.0045, (t0 + timedelta(minutes=2 * i)).strftime('%Y-%m-%d %H:%M:%S')))
    con.commit()
    set_setting('mileage_rate_per_km', '0.100')
    m = H.mileage(con, 1, '2026-09')
    assert 4.5 < m['km'] < 5.5 and m['trips'] == 1 and m['amount'] == round(m['km'] * 0.1, 3)
    rid = H.create(con, 1, 'expense', {'mileage_month': '2026-09', 'amount': 999})
    p = H.get(con, rid)['payload']
    assert p['amount'] == m['amount'], 'المبلغ من الرحلات لا مما كتبه الموظّف'
    with pytest.raises(ValueError):
        H.create(con, 1, 'expense', {'mileage_month': '2026-09'})
    with pytest.raises(ValueError):
        H.create(con, 1, 'expense', {'mileage_month': '2026-08'})


def test_engagement_pages(web):
    c, con, A = web
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 1')
    con.commit()
    c.post('/hr/engagement/announcements', data={'title': 'عيد سعيد', 'body': 'إجازة', 'audience_type': 'all'})
    c.post('/hr/engagement/surveys', data={'title': 'رأيك', 'anonymous': '1', 'audience_type': 'all',
                                           'questions': '[{"q": "تقييم", "type": "rating"}]'})
    page = c.get('/hr/engagement/?tab=surveys').get_data(as_text=True)
    assert 'عيد سعيد' in page and 'رأيك' in page
    _user(con, 'emp', 1)
    _login(c, con, 'emp')
    feed = c.get('/portal/api/engagement').get_json()
    assert feed['announcements'][0]['title'] == 'عيد سعيد' and feed['surveys'][0]['title'] == 'رأيك'
    sid = feed['surveys'][0]['id']
    assert c.post(f'/portal/api/surveys/{sid}', json={'answers': [5]}).get_json()['success']
    assert not c.get('/portal/api/engagement').get_json()['surveys']
    assert c.get('/hr/engagement/').status_code == 302, 'الموظّف لا يدخل الإدارة'
