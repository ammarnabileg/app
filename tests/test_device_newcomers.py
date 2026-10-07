# -*- coding: utf-8 -*-
"""الموظّفُ الجديد على جهاز البصمة (٢.٣٥):

- بصماتُه قبل إضافته للبرنامج لا تضيع (utils/pending_punches)؛
- «موظف جديد على جهاز كذا في مكان كذا — كمّل بياناته» (utils/device_new_employees)؛
- ردودُ أجهزة ADMS الأحدث على /iclock/querydata تُحفظ؛
- الوجهُ بإصداره كالإصبع، والبصماتُ نفسُها من كلّ جهاز مرّةً في اليوم.
"""
import importlib
import json
from datetime import datetime, timedelta

import pytest

from tests.test_device_access import FakeK40, env  # noqa: F401


@pytest.fixture
def adms(env):
    db, da, con = env
    con.execute("UPDATE fingerprint_devices SET branch_name = 'فرع الشويخ' WHERE id = 2")
    con.commit()
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    c = adms_server.app.test_client()
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    return c, con, da


def _att(con, num):
    return [r[0] for r in con.execute('SELECT a.check_time FROM attendance_records a JOIN employees e'
                                      ' ON e.id = a.employee_id WHERE e.employee_number = ? ORDER BY 1', (num,))]


# ------------------------------------------------------------ بصماتُ من لم يُضَف بعد

def test_direct_punches_of_an_unknown_number_wait_and_join_when_he_is_added(env, monkeypatch):
    db, da, con = env
    from zk.attendance import Attendance
    import fingerprint_sync as fs
    from utils import pending_punches
    t0 = datetime.now().replace(microsecond=0) - timedelta(hours=2)
    logs = [Attendance('777', t0, 0), Attendance('101', t0 + timedelta(hours=1), 0)]

    class Dev:
        def disable_device(self): pass
        def enable_device(self): pass
        def disconnect(self): pass
        def get_attendance(self): return logs
    m = fs.FingerprintSyncManager(db_path=db.DB_PATH)
    monkeypatch.setattr(m, 'connect_to_device', lambda info: (Dev(), None))
    dev = dict(con.execute('SELECT * FROM fingerprint_devices WHERE id = 1').fetchone())
    m.sync_device_attendance(dev)
    # المزامنةُ التالية تبدأ من بعد بصمة 101 — فبصمةُ 777 لن تُسحب ثانيةً أبدًا.
    assert con.execute('SELECT last_sync_time FROM fingerprint_devices WHERE id = 1').fetchone()[0] > \
        t0.strftime('%Y-%m-%d %H:%M:%S')
    assert con.execute("SELECT COUNT(*) FROM pending_punches WHERE pin = '777'").fetchone()[0] == 1
    con.execute("INSERT INTO employees (name, employee_number, department, position, hire_date, salary)"
                " VALUES ('جديد', '777', 'D', 'P', '2026-01-01', 0)")
    con.commit()
    assert pending_punches.adopt(con) == 1
    assert _att(con, '777') == [t0.strftime('%Y-%m-%d %H:%M:%S')]
    assert not con.execute('SELECT 1 FROM pending_punches').fetchone()
    assert pending_punches.adopt(con) == 0, 'مرّةً واحدة'


def test_adms_unknown_punch_waits_then_the_device_user_creates_him_and_he_gets_it(adms):
    c, con, da = adms
    now = (datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=f'888\t{now}\t0\t1\t0\t0\n',
           content_type='text/plain')
    assert con.execute("SELECT COUNT(*) FROM pending_punches WHERE pin = '888'").fetchone()[0] == 1
    c.post('/iclock/cdata?SN=SNADMS1&table=OPERLOG&Stamp=1',
           data='USER PIN=888\tName=Newcomer\tPri=0\tPasswd=\tCard=0\tGrp=1\n', content_type='text/plain')
    assert _att(con, '888') == [now], 'دخلت بصمتُه حين أُضيف'
    from utils import device_new_employees
    items = device_new_employees.open_items(con)
    assert len(items) == 1 and items[0]['number'] == '888'
    assert items[0]['device'] == 'SNADMS1' and items[0]['location'] == 'فرع الشويخ'


def test_a_stopped_employees_waiting_punch_is_ignored_not_counted(env):
    db, da, con = env
    from utils import pending_punches
    da.ensure_schema(con)
    pending_punches.record(con, 1, '102', '2026-10-05 08:00:00')
    con.commit()
    assert pending_punches.adopt(con) == 0
    assert con.execute('SELECT COUNT(*) FROM ignored_punches WHERE employee_id = 2').fetchone()[0] == 1


# ------------------------------------------------------------ «موظف جديد — كمّل بياناته»

def test_direct_user_sync_flags_the_new_employee_with_device_and_place(env, monkeypatch):
    db, da, con = env
    con.execute("UPDATE fingerprint_devices SET branch_name = 'مستودع الفحيحيل' WHERE id = 1")
    con.execute("UPDATE fingerprint_devices SET is_active = 0 WHERE id = 2")
    con.commit()
    import fingerprint_sync as fs
    from zk.user import User

    class Dev:
        def disable_device(self): pass
        def enable_device(self): pass
        def disconnect(self): pass
        def get_users(self): return [User(5, 'Walid', 0, '', '', '555', 0)]
    m = fs.FingerprintSyncManager(db_path=db.DB_PATH)
    monkeypatch.setattr(fs, 'ZK', lambda *a, **k: type('Z', (), {'connect': lambda self: Dev()})())
    m.sync_users_to_employees()
    from utils import device_new_employees
    items = device_new_employees.open_items(con)
    assert [(i['number'], i['device'], i['location']) for i in items] == [('555', 'K40', 'مستودع الفحيحيل')]


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
    return c, con


def test_the_bell_lists_them_and_saving_the_employee_clears_it(web):
    c, con = web
    from utils import device_new_employees
    device_new_employees.record(con, 1, 1)
    con.commit()
    r = c.get('/api/device_new_employees').get_json()
    assert r['count'] == 1 and r['items'][0]['device'] == 'K40'
    html = c.get('/employees').get_data(as_text=True)
    assert 'newDevEmpContainer' in html and 'كمّل البيانات' in html
    c.post('/api/device_new_employees/1/done')
    assert c.get('/api/device_new_employees').get_json()['count'] == 0


# ------------------------------------------------------------ querydata والوجه

def test_querydata_replies_of_newer_devices_are_saved(adms):
    c, con, da = adms
    body = ('biodata pin=101\tno=2\tindex=0\tvalid=1\tduress=0\ttype=1\tmajorver=12\tminorver=0\tformat=0\ttmp=QUJD\n'
            'biodata pin=101\tno=0\tindex=0\tvalid=1\tduress=0\ttype=9\tmajorver=5\tminorver=8\tformat=0\ttmp=RkFDRQ==\n')
    r = c.post('/iclock/querydata?SN=SNADMS1&type=tabledata&cmdid=5&tablename=biodata&count=2&packcnt=1&packidx=1',
               data=body, content_type='text/plain')
    assert r.status_code == 200
    rows = sorted(tuple(x) for x in con.execute(
        "SELECT template_type, finger_id, major_ver FROM fingerprint_templates WHERE pin = '101'"))
    assert rows == [(1, 2, '12'), (9, 0, '5')]
    c.post('/iclock/querydata?SN=SNADMS1&tablename=user', data='user uid=9\tpin=909\tname=Q\tpri=0\n',
           content_type='text/plain')
    assert con.execute("SELECT 1 FROM fingerprint_users WHERE user_id = '909' AND device_id = 2").fetchone()


def test_querydata_from_an_unapproved_device_is_dropped(adms):
    c, con, da = adms
    c.post('/iclock/querydata?SN=UNKNOWN99&tablename=biodata',
           data='biodata pin=101\tno=1\ttype=1\tmajorver=10\ttmp=WFla\n', content_type='text/plain')
    assert not con.execute('SELECT 1 FROM fingerprint_templates').fetchone()


def test_infrared_faces_are_versioned_and_sent_with_their_number(adms):
    c, con, da = adms
    c.get('/iclock/getrequest?SN=SNADMS1&INFO=Ver 6.60,3,5,40,10.0.0.5,10,7,12,2,111')
    c.post('/iclock/cdata?SN=SNADMS1&table=FACE&Stamp=1',
           data='FACE PIN=101 FID=3 SIZE=4 VALID=1 TMP=RkFDMw==', content_type='text/plain')
    row = con.execute("SELECT template_type, finger_id, major_ver FROM fingerprint_templates WHERE pin = '101'").fetchone()
    assert tuple(row) == (2, 3, '7'), 'وجهٌ نوع 2 بإصدار وجه الجهاز (7 من INFO)'
    import utils.fingerprint_utils as fu
    importlib.reload(fu)
    cmds = fu.adms_template_commands(con, '101', 2)
    assert [(t, json.loads(p)['FID']) for t, p in cmds] == [('DATA UPDATE FACE', 3)]
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (2, ?, ?, 'PENDING')",
                cmds[0])
    con.commit()
    out = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
    assert 'DATA UPDATE FACE\tPIN=101\tFID=3\t' in out and 'TMP=RkFDMw==' in out


# ------------------------------------------------------------ البصماتُ نفسُها مرّةً في اليوم

def test_daily_template_pull_from_k40_and_queries_for_adms(env, monkeypatch):
    db, da, con = env
    import utils.device_autosync as das
    importlib.reload(das)
    k40 = FakeK40([(7, '101', 'a')], [(7, 0, b'F0'), (7, 1, b'F1')], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: k40)
    r = das.run_templates()
    assert r['templates'] == 2 and r['queued'] == 2 and r['devices'] == 2, r
    assert con.execute("SELECT COUNT(*) FROM fingerprint_templates WHERE pin = '101' AND major_ver = '10'"
                       ).fetchone()[0] == 2
    kinds = sorted(json.loads(p)['Type'] for (p,) in con.execute(
        "SELECT payload FROM adms_commands WHERE command_type = 'DATA QUERY BIODATA' AND device_id = 2"))
    assert kinds == [1, 9]
    again = das.run_templates()
    assert again['devices'] == 0, 'مرّةً في اليوم'
    db.set_setting(das.SETTING_TEMPLATES, '0')
    assert das.run_templates(force=False)['devices'] == 0
