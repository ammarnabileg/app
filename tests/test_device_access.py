# -*- coding: utf-8 -*-
"""الموظّفُ الموقوف لا يبصم (٢.٣٢) — والعطلان في رفع بصمات ADMS.

الحالةُ عند العميل: موظّفٌ غيرُ نشط بقي على جهاز K40 (اتّصالٌ مباشر) يبصم كلَّ
يوم، وبصماتُه تظهر في التقرير. وجهازٌ وهميّ هنا يتكلّم كـpyzk تمامًا.
"""
import base64
import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.device_access as da
    importlib.reload(da)
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    for i, (num, active) in enumerate((('101', 1), ('102', 0), ('103', 0)), start=1):
        con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary,"
                    " is_active, card_number) VALUES (?, ?, ?, 'D', 'P', '2025-01-01', 500, ?, '0')",
                    (i, 'موظف ' + num, num, active))
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms)"
                " VALUES (1, 'K40', '192.168.1.201', 4370, 1, 0)")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms)"
                " VALUES (2, 'SNADMS1', '10.0.0.5', 1, 1)")
    con.commit()
    yield db, da, con
    con.close()


def _deactivate(con, emp_id, when):
    con.execute("INSERT INTO employee_audit_log (employee_id, field, old_value, new_value, category, changed_at)"
                " VALUES (?, 'is_active', '1', '0', 'status', ?)", (emp_id, when))
    con.commit()


# ------------------------------------------------------------ مَن الموقوف

def test_who_is_blocked_and_from_when(env):
    db, da, con = env
    _deactivate(con, 2, '2026-10-01 07:00:00')                    # UTC في السجلّ
    con.execute("UPDATE employees SET end_of_service_date = '2026-09-30' WHERE id = 3")
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary,"
                " is_active) VALUES (4, 'فوق الحد', '104', 'غير محدد', 'موظف', '2026-10-01', 0, 0)")
    da.mark_plan_cap(con, 4)
    con.commit()
    m = da.blocked_map(con)
    assert '101' not in m, 'النشط يبصم'
    assert '104' not in m, 'فوق حدّ الاشتراك يبصم حتى يُفعَّل — بصماتُه لا تضيع'
    assert m['103'] == datetime(2026, 10, 1), 'من اليوم التالي لنهاية الخدمة'
    assert m['102'] is not None
    assert da.is_blocked_punch(m, '103', '2026-10-01 08:00:00')
    assert not da.is_blocked_punch(m, '103', '2026-09-30 17:00:00'), 'ما بصمه قبل إيقافه يُحسب'


def test_an_inactive_employee_without_any_date_is_blocked_entirely(env):
    db, da, con = env
    m = da.blocked_map(con)
    assert m['102'] is None and da.is_blocked_punch(m, '102', datetime.now())


# ------------------------------------------------------------ ADMS: البصمة لا تُحسب، والجهاز يُؤمر بالحذف

@pytest.fixture
def adms(env):
    db, da, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    return adms_server.app.test_client(), con


def test_adms_punch_of_a_stopped_employee_is_ignored_and_the_device_told_to_disable_him(adms):
    c, con = adms
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    r = c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1',
               data=f'101\t{now}\t0\t1\t0\t0\n102\t{now}\t0\t1\t0\t0\n', content_type='text/plain')
    assert r.status_code == 200
    rows = [r[0] for r in con.execute('SELECT e.employee_number FROM attendance_records a '
                                      'JOIN employees e ON e.id = a.employee_id')]
    assert rows == ['101'], 'بصمةُ الموقوف لا تدخل الحضور ولا التقرير'
    assert con.execute('SELECT COUNT(*) FROM ignored_punches WHERE employee_id = 2').fetchone()[0] == 1
    assert not con.execute("SELECT 1 FROM adms_commands WHERE command_type = 'DATA DELETE USERINFO'").fetchone()
    cmd = con.execute("SELECT id, payload FROM adms_commands WHERE command_type = 'DATA UPDATE USERINFO'").fetchone()
    p = json.loads(cmd[1])
    assert p['PIN'] == '102' and p['Enabled'] == 0 and p['Pri'] & 1, 'يُعطَّل ببياناته، لا يُحذف'
    r = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
    assert 'DATA UPDATE USERINFO' in r and 'PIN=102' in r and 'Enabled=0' in r and 'Pri=1' in r, r


# ------------------------------------------------------------ الجهاز المباشر (K40) — وهميّ بواجهة pyzk

class FakeK40:
    def __init__(self, users, templates, fp_version=10):
        from zk.user import User
        from zk.finger import Finger
        self.User, self.Finger = User, Finger
        self.users = [User(uid, name, 0, '', '', user_id, 0) for uid, user_id, name in users]
        self.templates = [Finger(uid, fid, 1, data) for uid, fid, data in templates]
        self.fp_version = fp_version
        self.log = []

    def get_users(self):
        return list(self.users)

    def get_templates(self):
        return list(self.templates)

    def get_fp_version(self):
        return self.fp_version

    def get_serialnumber(self):
        return 'K40SN001'

    def delete_user(self, uid=0, user_id=''):
        self.log.append(('delete', uid))
        self.users = [u for u in self.users if u.uid != uid]
        self.templates = [t for t in self.templates if t.uid != uid]

    def set_user(self, uid=None, name='', privilege=0, password='', group_id='', user_id='', card=0):
        self.log.append(('set_user', user_id))
        self.users.append(self.User(uid, name, privilege, password, group_id, user_id, card))

    def save_user_template(self, user, fingers=[]):
        self.log.append(('save_templates', user.user_id, len(fingers)))
        self.templates += [self.Finger(user.uid, f.fid, 1, f.template) for f in fingers]

    # كما يستقبل الجهازُ CMD_USER_WRQ: سجلٌّ مُعبَّأ (٧٢ بايت) — نقرأ منه الصلاحيةَ كما وصلت.
    user_packet_size = 72
    encoding = 'UTF-8'

    def _ZK__send_command(self, command, command_string, response_size):
        from struct import unpack
        from zk import const
        assert command == const.CMD_USER_WRQ
        uid, priv, pwd, name, card, gid, user_id = unpack('HB8s24s4sx7sx24s', command_string)
        user_id = user_id.rstrip(b'\x00').decode()
        self.log.append(('write_user', user_id, priv))
        self.users = [u for u in self.users if u.uid != uid] + [
            self.User(uid, name.rstrip(b'\x00').decode(), priv, pwd.rstrip(b'\x00').decode(),
                      gid.rstrip(b'\x00').decode(), user_id, unpack('<I', card)[0])]
        return {'status': True}

    def refresh_data(self):
        pass

    def disable_device(self):
        pass

    def enable_device(self):
        pass

    def disconnect(self):
        pass


def _user(k40, user_id):
    return next((u for u in k40.users if u.user_id == user_id), None)


def test_a_stopped_employee_is_disabled_on_k40_not_deleted(env, monkeypatch):
    db, da, con = env
    k40 = FakeK40(users=[(1, '101', 'a'), (2, '102', 'b')],
                  templates=[(1, 0, b'\x01' * 40), (2, 0, b'\x02' * 40), (2, 6, b'\x03' * 40)])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    res = da.enforce_all(con)
    assert res['removed'] == 1 and not res['errors'], res
    stopped = _user(k40, '102')
    assert stopped is not None, 'يبقى على الجهاز — لا يُحذف'
    assert stopped.privilege & da.DISABLED_BIT, 'معطّل: الجهازُ يرفض بصمتَه'
    assert stopped.name == 'b' and stopped.uid == 2
    assert len([t for t in k40.templates if t.uid == 2]) == 2, 'وبصماتُه باقيةٌ على الجهاز'
    assert not (_user(k40, '101').privilege & da.DISABLED_BIT)
    saved = con.execute("SELECT finger_id, template_data, major_ver FROM fingerprint_templates "
                        "WHERE pin = '102' ORDER BY finger_id").fetchall()
    assert [(r[0], base64.b64decode(r[1])) for r in saved] == [(0, b'\x02' * 40), (6, b'\x03' * 40)], 'ونسخةٌ عندنا احتياطًا'
    assert saved[0][2] == '10'
    assert da.enforce_all(con)['removed'] == 0, 'مرّةً واحدة'


def test_pyzk_set_user_would_lose_the_disabled_bit_so_it_is_not_used():
    """`set_user` في pyzk يردّ الصلاحيةَ إلى 0 أو 14 — فيضيع التعطيل بصمت."""
    import inspect
    from zk.base import ZK
    assert 'privilege not in [const.USER_DEFAULT, const.USER_ADMIN]' in inspect.getsource(ZK.set_user)
    src = open(os.path.join(ROOT, 'utils', 'device_access.py'), encoding='utf-8').read()
    assert 'write_user(dev, u, int(u.privilege or 0) | DISABLED_BIT)' in src


def test_reactivating_enables_him_again_with_the_same_fingers(env, monkeypatch):
    db, da, con = env
    k40 = FakeK40(users=[(2, '102', 'b')], templates=[(2, 0, b'\x02' * 40)])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    da.enforce_all(con)
    assert _user(k40, '102').privilege & da.DISABLED_BIT
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 2')
    con.commit()
    res = da.enforce_all(con)
    assert res['restored'] == 1, res
    assert not (_user(k40, '102').privilege & da.DISABLED_BIT), 'يبصم تاني'
    assert [t.uid for t in k40.templates] == [2], 'ببصماته نفسِها — لا رفعَ ولا تسجيلَ من جديد'
    assert con.execute('SELECT COUNT(*) FROM device_removed_users').fetchone()[0] == 0


def test_someone_deleted_by_2_32_0_is_put_back_with_his_saved_fingers(env, monkeypatch):
    """نسخة 2.32.0.0 كانت تحذف: من حُذف بها يُرفع ببصماته المحفوظة عند التفعيل."""
    db, da, con = env
    k40 = FakeK40(users=[], templates=[])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    da.ensure_schema(con)
    con.execute("INSERT INTO device_removed_users (device_id, user_id) VALUES (1, '102')")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, template_data)"
                " VALUES ('102', 0, 1, ?)", (base64.b64encode(b'\x02' * 40).decode(),))
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 2')
    con.commit()
    assert da.enforce_all(con)['restored'] == 1
    assert _user(k40, '102') is not None and ('save_templates', '102', 1) in k40.log


def test_a_device_that_is_off_does_not_stop_the_others(env, monkeypatch):
    db, da, con = env
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms)"
                " VALUES (3, 'K40-2', '192.168.1.202', 4370, 1, 0)")
    con.commit()
    k40 = FakeK40(users=[(2, '102', 'b')], templates=[])

    def connect(device):
        if device['id'] == 1:
            raise OSError('timed out')
        return k40
    monkeypatch.setattr(da, '_connect', connect)
    res = da.enforce_all(con)
    assert res['removed'] == 1 and len(res['errors']) == 1


def test_direct_sync_does_not_record_a_stopped_employees_punches(env, monkeypatch):
    db, da, con = env
    from zk.attendance import Attendance
    import fingerprint_sync as fs
    now = datetime.now().replace(microsecond=0)
    logs = [Attendance('101', now, 0), Attendance('102', now, 0)]

    class Dev:
        def disable_device(self): pass
        def enable_device(self): pass
        def disconnect(self): pass
        def get_attendance(self): return logs
    m = fs.FingerprintSyncManager(db_path=db.DB_PATH)
    monkeypatch.setattr(m, 'connect_to_device', lambda info: (Dev(), None))
    dev = dict(con.execute('SELECT * FROM fingerprint_devices WHERE id = 1').fetchone())
    assert m.sync_device_attendance(dev) == 1
    nums = [r[0] for r in con.execute('SELECT e.employee_number FROM attendance_records a '
                                      'JOIN employees e ON e.id = a.employee_id')]
    assert nums == ['101']
    assert con.execute('SELECT COUNT(*) FROM ignored_punches').fetchone()[0] == 1


def test_stopping_an_employee_applies_to_direct_devices_and_eos_too():
    src = open(os.path.join(ROOT, 'routes', 'employee_routes.py'), encoding='utf-8').read()
    assert 'device_access.apply_now()' in src
    # الحذفُ يمرّ بسلّة المحذوفات (٢.٣٣)، وهي تحذف من الأجهزة المباشرة.
    assert 'sensitive_ops.delete_employee' in src
    so = open(os.path.join(ROOT, 'utils', 'sensitive_ops.py'), encoding='utf-8').read()
    assert 'delete_from_direct_now' in so
    assert 'queue_adms_user_disable(employee' in src, 'الإيقافُ تعطيلٌ على ADMS لا حذف'
    eos = open(os.path.join(ROOT, 'routes', 'eos_routes.py'), encoding='utf-8').read()
    assert 'queue_adms_user_disable' in eos and 'device_access.apply_now()' in eos
    auto = open(os.path.join(ROOT, 'utils', 'device_autosync.py'), encoding='utf-8').read()
    assert '_enforce_blocked()' in auto


# ------------------------------------------------------------ عطلا ADMS

def test_saving_an_employee_sends_his_fingerprints_to_adms(env):
    db, da, con = env
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data)"
                " VALUES ('101', 3, 1, '10', 'QUJD')")
    con.execute("INSERT INTO fingerprint_faces (pin, face_id, template_data) VALUES ('101', 0, 'RkFDRQ==')")
    con.commit()
    import utils.fingerprint_utils as fu
    importlib.reload(fu)
    assert fu.queue_adms_user_update({'employee_number': '101', 'name': 'a'}) == 1
    kinds = sorted(r[0] for r in con.execute("SELECT command_type FROM adms_commands WHERE device_id = 2"))
    assert kinds == ['DATA UPDATE BIODATA', 'DATA UPDATE FACE', 'DATA UPDATE USERINFO'], kinds
    bio = json.loads(con.execute("SELECT payload FROM adms_commands WHERE command_type = 'DATA UPDATE BIODATA'")
                     .fetchone()[0])
    assert bio['FingerID'] == 3 and bio['Template'] == 'QUJD' and bio['MajorVer'] == '10'


def test_excel_import_passes_an_id_and_still_reaches_the_devices(env):
    db, da, con = env
    import utils.fingerprint_utils as fu
    importlib.reload(fu)
    assert fu.queue_adms_user_update(1) == 1
    p = json.loads(con.execute("SELECT payload FROM adms_commands WHERE command_type = 'DATA UPDATE USERINFO'")
                   .fetchone()[0])
    assert p['PIN'] == '101'


def test_the_biodata_command_keeps_the_stored_type_and_version():
    src = open(os.path.join(ROOT, 'routes', 'adms_routes.py'), encoding='utf-8').read()
    assert "btype = payload.get('Type') or 1" in src and 'Type={btype}' in src
    assert 'user_fingerprints' not in src and 'user_faces' not in src


def test_punches_recorded_after_he_was_stopped_leave_the_reports(env):
    db, da, con = env
    con.execute("UPDATE employees SET end_of_service_date = '2026-09-30' WHERE id = 3")
    for t in ('2026-09-30 08:00:00', '2026-10-01 08:00:00', '2026-10-02 08:00:00'):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) "
                    "VALUES (3, 1, ?, 0)", (t,))
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source) "
                "VALUES (3, 0, '2026-10-03 09:00:00', 1, 'manual')")
    con.commit()
    assert da.move_existing(con) == 2
    left = [r[0] for r in con.execute('SELECT check_time FROM attendance_records WHERE employee_id = 3 ORDER BY 1')]
    assert left == ['2026-09-30 08:00:00', '2026-10-03 09:00:00'], 'قبل الإيقاف، وما أدخله المسؤولُ بيده، يبقيان'
    assert con.execute('SELECT COUNT(*) FROM ignored_punches WHERE employee_id = 3').fetchone()[0] == 2


def test_saving_an_active_employee_re_enables_him_on_adms(env):
    db, da, con = env
    import utils.fingerprint_utils as fu
    importlib.reload(fu)
    fu.queue_adms_user_update({'employee_number': '101', 'name': 'a', 'privilege': 1})
    p = json.loads(con.execute("SELECT payload FROM adms_commands WHERE command_type = 'DATA UPDATE USERINFO'")
                   .fetchone()[0])
    assert p['Enabled'] == 1 and not (p['Pri'] & 1)
