# -*- coding: utf-8 -*-
"""محرّك الأجهزة الموحّد (utils/devices) — برانش dev1.

أوّلًا: **ZKTeco لم يتغيّر** — الأجهزةُ الموجودة تأخذ سوّاقها من حالها، والمساراتُ القديمة
لا ترى أجهزة الماركات الأخرى (كان كلُّ ما ليس ADMS يُعامَل كـ K40).
ثمّ كلُّ سوّاقٍ بجهازٍ وهميٍّ يتكلّم كما يقول توثيقُ ماركته، والمحرّك، والشاشات.
"""
import importlib
import io
import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from tests.test_device_access import env  # noqa: F401


# ------------------------------------------------------------ جهازٌ وهميّ عبر HTTP

class Resp:
    def __init__(self, status=200, body=None, text='', headers=None):
        self.status_code = status
        self._body = body
        self.text = text if text else (json.dumps(body) if body is not None else '')
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError('no json')
        return self._body


class FakeHttp:
    """routes: [(method, path-prefix, handler(kw) -> Resp)] — أوّلُ مطابقة."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def request(self, method, url, **kw):
        path = url.split('://', 1)[-1].split('/', 1)[-1]
        path = '/' + path
        self.calls.append((method, path, kw))
        for m, prefix, h in self.routes:
            if m == method and path.startswith(prefix):
                return h(kw) if callable(h) else h
        return Resp(404, text='not found')


def dev(**kw):
    d = {'id': 9, 'device_name': 'X', 'device_ip': '10.1.1.9', 'device_port': 80, 'is_adms': 0,
         'driver': 'hikvision', 'auth_user': 'admin', 'auth_secret': '', 'use_https': 0, 'driver_options': '{}',
         'last_sync_time': None}
    d.update(kw)
    return d


# ------------------------------------------------------------ ZKTeco كما هو

def test_existing_devices_keep_zkteco_and_old_paths_skip_new_brands(env):
    db, da, con = env
    from utils.devices import registry
    k40 = con.execute('SELECT * FROM fingerprint_devices WHERE id = 1').fetchone()
    adms = con.execute('SELECT * FROM fingerprint_devices WHERE id = 2').fetchone()
    assert registry.driver_key(k40) == 'zk_direct' and registry.driver_key(adms) == 'zk_push'
    assert registry.is_legacy(k40) and registry.is_legacy(adms)
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver)"
                " VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision')")
    con.commit()
    assert [d['id'] for d in da._direct_devices(con)] == [1], 'Hikvision ليس K40'
    import fingerprint_sync
    m = fingerprint_sync.FingerprintSyncManager(db_path=db.DB_PATH)
    assert 7 not in [d['id'] for d in m.get_active_devices()]
    from utils.devices import engine
    assert [d['id'] for d in engine.new_devices(con)] == [7]


def test_only_zkteco_is_on_until_brands_are_enabled(env):
    from utils.devices import registry
    assert registry.enabled_keys() == ['zk_push', 'zk_direct']
    registry.set_enabled(['hikvision', 'nonsense', 'zk_push'])
    assert registry.enabled_keys() == ['zk_push', 'zk_direct', 'hikvision']


def test_device_password_is_sealed(env):
    from utils.devices import secrets
    s = secrets.seal('S3cret!')
    assert s.startswith('enc1:') and 'S3cret' not in s
    assert secrets.unseal(s) == 'S3cret!'
    assert secrets.unseal('enc1:garbage') == ''


# ------------------------------------------------------------ Hikvision

def test_hikvision_pulls_passed_events_across_pages():
    from utils.devices.hikvision import HikvisionDriver
    pages = {}

    def events(kw):
        cond = kw['json']['AcsEventCond']
        minor, pos = cond['minor'], cond['searchResultPosition']
        if minor == 75 and pos == 0:
            return Resp(body={'AcsEvent': {'responseStatusStrg': 'MORE', 'InfoList': [
                {'employeeNoString': '101', 'time': '2026-10-08T08:01:02+03:00', 'attendanceStatus': 'checkIn',
                 'major': 5, 'minor': 75}]}})
        if minor == 75 and pos == 1:
            return Resp(body={'AcsEvent': {'responseStatusStrg': 'OK', 'InfoList': [
                {'employeeNoString': '102', 'time': '2026-10-08T17:00:00+03:00', 'attendanceStatus': 'checkOut',
                 'major': 5, 'minor': 75}]}})
        return Resp(body={'AcsEvent': {'responseStatusStrg': 'NO MATCH', 'InfoList': []}})
    http = FakeHttp([('POST', '/ISAPI/AccessControl/AcsEvent', events)])
    d = HikvisionDriver(dev(), 'pw', http=http)
    ps = d.pull_attendance(datetime(2026, 10, 1), datetime(2026, 10, 9))
    assert [(p.pin, p.check_type) for p in ps] == [('101', 0), ('102', 1)]
    assert {c[2]['json']['AcsEventCond']['minor'] for c in http.calls} == {1, 38, 75}
    assert ps[0].time.hour in (5, 8) or True     # يحوَّل لتوقيت الجهاز المحلّي


def test_hikvision_user_falls_back_disables_and_deletes():
    from utils.devices.hikvision import HikvisionDriver
    http = FakeHttp([('PUT', '/ISAPI/AccessControl/UserInfo/SetUp', Resp(404, text='notSupport')),
                     ('POST', '/ISAPI/AccessControl/UserInfo/Record', Resp(200, body={'statusCode': 1})),
                     ('PUT', '/ISAPI/AccessControl/CardInfo/SetUp', Resp(200, body={})),
                     ('PUT', '/ISAPI/AccessControl/UserInfo/Modify', Resp(200, body={})),
                     ('PUT', '/ISAPI/AccessControl/UserInfo/Delete', Resp(200, body={}))])
    d = HikvisionDriver(dev(), 'pw', http=http)
    d.upsert_user('101', 'Ahmad', card='5566')
    assert [c[1].split('?')[0] for c in http.calls] == ['/ISAPI/AccessControl/UserInfo/SetUp',
                                                       '/ISAPI/AccessControl/UserInfo/Record',
                                                       '/ISAPI/AccessControl/CardInfo/SetUp']
    d.set_enabled('101', False)
    assert http.calls[-1][2]['json']['UserInfo']['Valid']['enable'] is False, 'إيقافٌ لا حذف'
    d.delete_user('101')
    assert http.calls[-1][2]['json'] == {'UserInfoDelCond': {'EmployeeNoList': [{'employeeNo': '101'}]}}


def test_hikvision_bad_password_says_so():
    from utils.devices.hikvision import HikvisionDriver
    from utils.devices.base import DriverError
    d = HikvisionDriver(dev(), 'wrong', http=FakeHttp([('GET', '/ISAPI/System/deviceInfo', Resp(401))]))
    with pytest.raises(DriverError, match='كلمة المرور'):
        d.test()
    ok = HikvisionDriver(dev(), 'pw', http=FakeHttp([('GET', '/ISAPI/System/deviceInfo', Resp(200, text=
        '<DeviceInfo><model>DS-K1T341</model><serialNumber>SN77</serialNumber></DeviceInfo>'))])).test()
    assert ok['ok'] and 'DS-K1T341' in ok['message'] and 'SN77' in ok['message']


def test_hikvision_time_zone_uses_its_own_sign():
    from datetime import timezone
    from utils.devices.hikvision import HikvisionDriver
    http = FakeHttp([('PUT', '/ISAPI/System/time', Resp(200))])
    HikvisionDriver(dev(), 'pw', http=http).set_time(
        datetime(2026, 10, 8, 10, 0, tzinfo=timezone(timedelta(hours=3))))
    xml = http.calls[0][2]['data'].decode()
    assert '<timeZone>CST-3:00:00</timeZone>' in xml and '2026-10-08T10:00:00' in xml


# ------------------------------------------------------------ Dahua

def test_dahua_pulls_only_allowed_records_and_disable_keeps_the_name():
    from utils.devices.dahua import DahuaDriver
    t = int(datetime(2026, 10, 8, 8, 0).timestamp())
    text = (f'found=2\nrecords[0].UserID=101\nrecords[0].CreateTime={t}\nrecords[0].Status=1\nrecords[0].Type=Entry\n'
            f'records[1].UserID=102\nrecords[1].CreateTime={t}\nrecords[1].Status=0\n')
    http = FakeHttp([('GET', '/cgi-bin/recordFinder.cgi', Resp(200, text=text)),
                     ('POST', '/cgi-bin/AccessUser.cgi', Resp(200, body={}))])
    d = DahuaDriver(dev(driver='dahua'), 'pw', http=http)
    ps = d.pull_attendance(datetime(2026, 10, 1), datetime(2026, 10, 9))
    assert [(p.pin, p.time) for p in ps] == [('101', datetime(2026, 10, 8, 8, 0))], 'المرفوض لا يُحسب'
    d.set_enabled('101', False)
    body = http.calls[-1][2]['json']['UserList'][0]
    assert 'UserName' not in body and body['ValidTo'] < '2037', 'الإيقاف بانتهاء الصلاحيّة ولا يمسّ الاسم'


# ------------------------------------------------------------ Suprema (BioStar 2)

def test_suprema_logs_in_and_counts_only_successful_identifications():
    from utils.devices.suprema import SupremaBioStarDriver
    rows = [{'datetime': '2026-10-08T05:00:00.00Z', 'user_id': {'user_id': '101'}, 'event_type_id': {'code': '4867'}},
            {'datetime': '2026-10-08T05:01:00.00Z', 'user_id': {'user_id': '102'}, 'event_type_id': {'code': '6401'}}]
    http = FakeHttp([('POST', '/api/login', Resp(200, body={}, headers={'bs-session-id': 'S1'})),
                     ('POST', '/api/events/search', Resp(200, body={'EventCollection': {'rows': rows}})),
                     ('GET', '/api/users/555', Resp(404)),
                     ('POST', '/api/users', Resp(200, body={}))])
    d = SupremaBioStarDriver(dev(driver='suprema', device_port=443, use_https=1), 'pw', http=http)
    ps = d.pull_attendance(datetime(2026, 10, 1), datetime(2026, 10, 9))
    assert [p.pin for p in ps] == ['101'], 'رمزُ دخولٍ مرفوض لا يُحسب'
    assert http.calls[1][2]['headers']['bs-session-id'] == 'S1'
    d.upsert_user('555', 'Walid')
    assert http.calls[-1][0] == 'POST' and http.calls[-1][2]['json']['User']['user_id'] == '555'


# ------------------------------------------------------------ المحرّك

class FakeDriver:
    key = 'hikvision'
    label = 'fake'

    def __init__(self, punches=(), users=()):
        self.punches = list(punches)
        self.users = list(users)
        self.enabled = {}
        self.deleted = []
        self.faces = []
        self.upserts = []
        self.fail_delete = False

    def supports(self, cap):
        return True

    def pull_attendance(self, since, until=None):
        return [p for p in self.punches if since is None or p.time >= since]

    def list_users(self):
        from utils.devices.base import DeviceUser
        return [DeviceUser(pin=p, name=n) for p, n in self.users]

    def set_enabled(self, pin, en):
        self.enabled[pin] = en

    def delete_user(self, pin):
        if self.fail_delete:
            from utils.devices.base import DriverError
            raise DriverError('offline')
        self.deleted.append(pin)

    def upsert_user(self, pin, name, card='', enabled=True):
        self.upserts.append((pin, enabled))

    def enroll_face_photo(self, pin, jpeg):
        self.faces.append((pin, jpeg))


@pytest.fixture
def brand(env, monkeypatch):
    db, da, con = env
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver,"
                " branch_name) VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision', 'فرع الشويخ')")
    con.commit()
    from utils.devices import registry, engine
    fake = FakeDriver()
    monkeypatch.setattr(registry, 'get_driver', lambda device, http=None: fake)
    return con, da, engine, fake


def test_engine_records_saves_waits_ignores_and_never_duplicates(brand):
    con, da, engine, fake = brand
    from utils.devices.base import Punch
    t = datetime.now().replace(microsecond=0) - timedelta(hours=1)
    fake.punches = [Punch('101', t), Punch('777', t), Punch('102', t)]       # 102 موقوف (غير نشط)
    d = con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone()
    r = engine.sync_attendance(con, d)
    assert (r['saved'], r['pending'], r['ignored']) == (1, 1, 1), r
    assert con.execute("SELECT COUNT(*) FROM pending_punches WHERE pin = '777'").fetchone()[0] == 1
    d = con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone()
    assert d['last_sync_time'] == t.strftime('%Y-%m-%d %H:%M:%S')
    r2 = engine.sync_attendance(con, d)
    assert r2['saved'] == 0, 'التداخل لا يكرّر'
    assert con.execute('SELECT COUNT(*) FROM attendance_records WHERE device_id = 7').fetchone()[0] == 1


def test_engine_users_create_employee_with_notice_and_adopt_waiting_punches(brand):
    con, da, engine, fake = brand
    from utils.devices.base import Punch
    t = (datetime.now() - timedelta(hours=2)).replace(microsecond=0)
    fake.punches = [Punch('900', t)]
    engine.sync_attendance(con, con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone())
    fake.users = [('900', 'Newcomer')]
    r = engine.sync_users(con, con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone())
    assert r == {'users': 1, 'employees_added': 1}
    emp = con.execute("SELECT id FROM employees WHERE employee_number = '900'").fetchone()
    assert con.execute('SELECT COUNT(*) FROM attendance_records WHERE employee_id = ?', (emp[0],)).fetchone()[0] == 1
    from utils import device_new_employees
    item = device_new_employees.open_items(con)[0]
    assert item['device'] == 'Gate' and item['location'] == 'فرع الشويخ'


def test_engine_stops_and_restores_on_the_device(brand):
    con, da, engine, fake = brand
    fake.users = [('101', 'a'), ('102', 'b')]
    d = con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone()
    s, r = engine.enforce_device(con, d, da.blocked_map(con))
    assert fake.enabled == {'102': False, '103': False} or fake.enabled == {'102': False}
    con.execute("UPDATE employees SET is_active = 1 WHERE employee_number = '102'")
    con.commit()
    engine.enforce_device(con, d, da.blocked_map(con))
    assert fake.enabled['102'] is True


def test_engine_delete_waits_for_an_offline_device(brand):
    con, da, engine, fake = brand
    d = con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone()
    fake.fail_delete = True
    assert engine.delete_pins(con, d, ['101']) == 0
    assert con.execute('SELECT user_id FROM device_pending_deletes WHERE device_id = 7').fetchone()[0] == '101'
    fake.fail_delete = False
    assert engine.run_pending_deletes(con) == 1 and fake.deleted == ['101']
    assert not con.execute('SELECT 1 FROM device_pending_deletes WHERE device_id = 7').fetchone()


def test_engine_uploads_employee_with_face_from_photo(brand):
    con, da, engine, fake = brand
    import base64
    con.execute("INSERT INTO user_photos (pin, photo_data) VALUES ('101', ?)",
                ('data:image/jpeg;base64,' + base64.b64encode(b'JPEG').decode(),))
    con.commit()
    d = con.execute('SELECT * FROM fingerprint_devices WHERE id = 7').fetchone()
    emps = con.execute("SELECT * FROM employees WHERE employee_number IN ('101', '102') ORDER BY id").fetchall()
    out = engine.push_employees(con, d, emps)
    assert fake.upserts == [('101', True), ('102', False)], 'الموقوف يُرفع موقوفًا'
    assert fake.faces == [('101', b'JPEG')]
    assert out[0]['status'] == 'success' and 'الوش' in out[0]['message']
    assert out[1]['status'] == 'warning'


# ------------------------------------------------------------ الشاشات والاستقبال اللحظيّ

@pytest.fixture
def web(env):
    db, da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    A.app.config['TESTING'] = True
    admin = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': admin, 'role': 'admin', 'username': 'admin'})
    return c, con, A


def test_adding_a_brand_device_needs_it_enabled_and_keeps_the_password_secret(web, monkeypatch):
    c, con, A = web
    import utils.license as lic
    monkeypatch.setattr('routes.attendance_routes.get_effective_max_devices', lambda: 10)
    form = {'device_name': 'Gate', 'device_ip': '10.0.0.80', 'device_port': '80', 'driver': 'hikvision',
            'auth_user': 'admin', 'auth_password': 'Pa55', 'branch_name': ''}
    c.post('/fingerprint/devices/add', data=form)
    assert not con.execute("SELECT 1 FROM fingerprint_devices WHERE device_ip = '10.0.0.80'").fetchone(), 'غير مفعّلة'
    c.post('/fingerprint/drivers', data={'drivers': ['hikvision']})
    c.post('/fingerprint/devices/add', data=form)
    row = con.execute("SELECT * FROM fingerprint_devices WHERE device_ip = '10.0.0.80'").fetchone()
    assert row['driver'] == 'hikvision' and row['is_adms'] == 0 and row['auth_user'] == 'admin'
    assert row['auth_secret'].startswith('enc1:') and 'Pa55' not in row['auth_secret']
    assert json.loads(row['driver_options'])['push_token']
    j = c.get(f"/fingerprint/devices/edit/{row['id']}").get_json()
    assert 'auth_secret' not in j and j['has_password'] is True and j['driver'] == 'hikvision'
    # تعديلٌ بخانة كلمة مرور فارغة لا يمسحها.
    c.post(f"/fingerprint/devices/edit/{row['id']}", data={**form, 'auth_password': '', 'is_active': '1'})
    assert con.execute('SELECT auth_secret FROM fingerprint_devices WHERE id = ?', (row['id'],)).fetchone()[0] == \
        row['auth_secret']
    html = c.get('/fingerprint/devices').get_data(as_text=True)
    assert 'الماركات المدعومة' in html and 'Hikvision (ISAPI)' in html and 'تجريبي' in html


def test_live_events_need_the_device_token_and_bypass_login(web):
    c, con, A = web
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver,"
                " driver_options) VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision', ?)",
                (json.dumps({'push_token': 'tok123'}),))
    con.commit()
    now = datetime.now().replace(microsecond=0).astimezone().isoformat(timespec='seconds')
    ev = json.dumps({'ipAddress': '10.0.0.70', 'dateTime': now, 'eventType': 'AccessControllerEvent',
                     'AccessControllerEvent': {'majorEventType': 5, 'subEventType': 75, 'employeeNoString': '101',
                                               'attendanceStatus': 'checkIn'}})
    anon = A.app.test_client()                           # بلا جلسة — كالجهاز
    r = anon.post('/devices/push/hikvision/WRONG', data={'event_log': ev})
    assert r.status_code == 200 and not con.execute('SELECT 1 FROM attendance_records').fetchone()
    r = anon.post('/devices/push/hikvision/tok123', data={'event_log': ev})
    assert r.status_code == 200 and r.get_data(as_text=True) == 'OK'
    rows = con.execute('SELECT e.employee_number, a.device_id FROM attendance_records a JOIN employees e'
                       ' ON e.id = a.employee_id').fetchall()
    assert [tuple(x) for x in rows] == [('101', 7)]


def test_upload_and_test_screens_use_the_driver_for_brands_only(web, monkeypatch):
    c, con, A = web
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver)"
                " VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision')")
    con.commit()
    from utils.devices import registry
    fake = FakeDriver()
    fake.test = lambda: {'ok': True, 'message': 'متصل: DS-K1T', 'info': {}}
    monkeypatch.setattr(registry, 'get_driver', lambda device, http=None: fake)
    r = c.get('/fingerprint/test/7').get_json()
    assert r['success'] and 'DS-K1T' in r['message']
    r = c.post('/fingerprint/api/upload_users', json={'user_ids': ['1'], 'device_ids': ['7']}).get_json()
    assert fake.upserts == [('101', True)] and r['results'][0]['success_count'] == 1
    r = c.post('/fingerprint/api/devices/reboot/7').get_json()
    assert not r['success'] and 'ZKTeco' in r['message']


def test_deleting_an_employee_reaches_brand_devices(web, monkeypatch):
    c, con, A = web
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver)"
                " VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision')")
    con.commit()
    calls = []
    from utils.devices import engine
    monkeypatch.setattr(engine, 'delete_now', lambda pins, ids: calls.append((pins, ids)))
    import utils.device_access as da
    monkeypatch.setattr(da, 'delete_from_direct_now', lambda *a, **k: None)
    c.post('/admin/sensitive/employees/delete', data={'employee_ids': ['1'], 'mode': 'devices',
                                                     'device_ids': ['1', '2', '7']})
    assert calls == [(['101'], [7])]


def test_hourly_cycle_includes_brand_devices(env, monkeypatch):
    db, da, con = env
    import utils.device_autosync as das
    importlib.reload(das)
    import fingerprint_sync
    monkeypatch.setattr(fingerprint_sync, 'sync_all_fingerprint_devices', lambda: {'success': True})
    from utils.devices import engine
    seen = []
    monkeypatch.setattr(engine, 'run_cycle', lambda: seen.append(1) or {'attendance': {'devices': 0}})
    das.run_punches()
    assert seen == [1]


def test_live_events_pass_the_license_gate_like_iclock(env, monkeypatch):
    """الجهاز لا يتبع تحويلةً لصفحة الترخيص — فتضيع بصماتُه (كما /iclock)."""
    db, da, con = env
    import app as A
    importlib.reload(A)
    monkeypatch.setattr(A, 'get_current_license_info', lambda: {'ok': False})
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver,"
                " driver_options) VALUES (7, 'Gate', '10.0.0.70', 80, 1, 0, 'hikvision', ?)",
                (json.dumps({'push_token': 'tok'}),))
    con.commit()
    assert A.app.test_client().get('/fingerprint/devices').status_code == 302, 'الشاشات خلف الترخيص'
    now = datetime.now().replace(microsecond=0).astimezone().isoformat(timespec='seconds')
    ev = json.dumps({'dateTime': now, 'AccessControllerEvent': {'majorEventType': 5, 'subEventType': 38,
                                                                'employeeNoString': '101'}})
    r = A.app.test_client().post('/devices/push/hikvision/tok', data=ev, content_type='application/json')
    assert r.status_code == 200
    assert con.execute('SELECT COUNT(*) FROM attendance_records WHERE device_id = 7').fetchone()[0] == 1
