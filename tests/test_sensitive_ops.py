# -*- coding: utf-8 -*-
"""العمليات الحساسة (٢.٣٣): مسحٌ بخيارين، وسجلٌّ، وسلّةُ محذوفات — ونقلُ الموظّف
ببصماته بين أجهزة الـIP المباشرة (K40) من «إدارة مستخدمي الأجهزة».

يُشغَّل:  python -m pytest tests/test_sensitive_ops.py -v
"""
import base64
import importlib
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_device_access import FakeK40, _user  # noqa: E402


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.device_access as da
    importlib.reload(da)
    import utils.sensitive_ops as so
    importlib.reload(so)
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    for i, num in enumerate(('101', '102', '103'), start=1):
        con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary,"
                    " is_active, card_number) VALUES (?, ?, ?, 'D', 'P', '2025-01-01', 500, 1, '0')",
                    (i, 'موظف ' + num, num))
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms)"
                " VALUES (1, 'K40-A', '192.168.1.201', 4370, 1, 0)")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms)"
                " VALUES (3, 'K40-B', '192.168.1.202', 4370, 1, 0)")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms)"
                " VALUES (2, 'SNADMS1', '10.0.0.5', 1, 1)")
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source)"
                " VALUES (2, 1, '2026-10-01 08:00:00', 'in', 'device')")
    con.commit()
    # الحذفُ من الأجهزة المباشرة في الخلفيّة — هنا متزامنًا لنراه.
    calls = []

    def now(nums, device_ids=None):
        calls.append((nums, device_ids))
    monkeypatch.setattr(da, 'delete_from_direct_now', now)
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import routes.sensitive_routes as sr
    importlib.reload(sr)
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    A.app.config['TESTING'] = True
    admin = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s.update({'user_id': admin, 'role': 'admin', 'username': 'admin', 'full_name': 'المدير'})
    yield c, con, da, so, calls
    con.close()


def _log(con):
    return [dict(r) for r in con.execute('SELECT * FROM sensitive_audit_log ORDER BY id')]


# ------------------------------------------------------------ زرّ الحذف في إدارة الموظفين

def test_the_employee_delete_button_logs_who_and_goes_to_the_bin(web):
    c, con, da, so, calls = web
    r = c.get('/employees/delete/2')
    assert r.status_code == 302
    assert not con.execute('SELECT 1 FROM employees WHERE id = 2').fetchone()
    log = _log(con)
    assert len(log) == 1 and log[0]['action'] == 'employee.delete'
    assert 'admin' in log[0]['username'] and '102' in log[0]['target_label']
    item = con.execute("SELECT * FROM recycle_bin WHERE kind = 'employee'").fetchone()
    assert json.loads(item['payload'])['employee_number'] == '102'
    # كما كان: من كلّ الأجهزة — ADMS بأمر، والمباشرةُ في الخلفيّة.
    assert con.execute("SELECT COUNT(*) FROM adms_commands WHERE command_type = 'DATA DELETE USERINFO'"
                       " AND device_id = 2").fetchone()[0] == 1
    assert calls == [('102', [1, 3])]


def test_restore_brings_him_back_with_the_same_id_and_his_history(web):
    c, con, da, so, calls = web
    c.get('/employees/delete/2')
    bin_id = con.execute('SELECT id FROM recycle_bin').fetchone()[0]
    r = c.post(f'/admin/sensitive/restore/{bin_id}', data={})
    assert r.status_code == 302
    row = con.execute('SELECT * FROM employees WHERE id = 2').fetchone()
    assert row and row['employee_number'] == '102' and row['salary'] == 500
    n = con.execute('SELECT COUNT(*) FROM attendance_records a JOIN employees e ON e.id = a.employee_id'
                    ' WHERE e.id = 2').fetchone()[0]
    assert n == 1, 'حضورُه القديم رجع معه'
    assert con.execute('SELECT restored_at FROM recycle_bin').fetchone()[0]
    assert [l['action'] for l in _log(con)] == ['employee.delete', 'employee.restore']
    # لا يُسترجع مرتين.
    ok, msg = so.restore(con, bin_id)
    assert not ok


def test_restore_refuses_when_the_number_was_taken_meanwhile(web):
    c, con, da, so, calls = web
    c.get('/employees/delete/2')
    con.execute("INSERT INTO employees (name, employee_number, department, position, hire_date, salary)"
                " VALUES ('جديد', '102', 'D', 'P', '2026-01-01', 1)")
    con.commit()
    ok, msg = so.restore(con, con.execute('SELECT id FROM recycle_bin').fetchone()[0])
    assert not ok and '102' in msg and 'جديد' in msg
    assert con.execute('SELECT COUNT(*) FROM employees WHERE employee_number = ?', ('102',)).fetchone()[0] == 1


# ------------------------------------------------------------ صفحة السوبر أدمن

def test_delete_from_the_system_only_leaves_the_devices_alone(web):
    c, con, da, so, calls = web
    r = c.post('/admin/sensitive/employees/delete',
               data={'employee_ids': ['1', '3'], 'mode': 'system', 'device_ids': ['1', '2'], 'note': 'تجربة'})
    assert r.status_code == 302
    assert con.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 1
    assert not con.execute("SELECT 1 FROM adms_commands WHERE command_type = 'DATA DELETE USERINFO'").fetchone()
    assert calls == []
    log = _log(con)
    assert len(log) == 2 and all('النظام فقط' in l['details'] and 'تجربة' in l['details'] for l in log)
    # ولا تُعيده مزامنةُ الأجهزة موظّفًا جديدًا بعد ساعة.
    assert so.deleted_pins(con) == {'101', '103'}


def test_delete_from_the_system_and_only_the_chosen_devices(web):
    c, con, da, so, calls = web
    c.post('/admin/sensitive/employees/delete',
           data={'employee_ids': ['1', '2'], 'mode': 'devices', 'device_ids': ['3']})
    assert not con.execute("SELECT 1 FROM adms_commands").fetchone(), 'ADMS لم يُختر'
    assert calls == [(['101', '102'], [3])], 'جهازٌ واحد مختار، باتّصالٍ واحد للاثنين'
    scope = json.loads(con.execute('SELECT scope FROM recycle_bin LIMIT 1').fetchone()[0])
    assert scope['mode'] == 'devices' and [d['name'] for d in scope['devices']] == ['K40-B']
    assert 'K40-B' in _log(con)[0]['details']


def test_devices_mode_without_any_device_is_refused(web):
    c, con, da, so, calls = web
    c.post('/admin/sensitive/employees/delete', data={'employee_ids': ['1'], 'mode': 'devices'})
    assert con.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 3


def test_device_delete_and_restore(web):
    c, con, da, so, calls = web
    c.get('/fingerprint/devices/delete/1')
    assert not con.execute('SELECT 1 FROM fingerprint_devices WHERE id = 1').fetchone()
    assert _log(con)[0]['action'] == 'device.delete'
    c.post('/admin/sensitive/devices/delete', data={'device_ids': ['3']})
    ids = [r[0] for r in con.execute("SELECT id FROM recycle_bin WHERE kind = 'device' ORDER BY id")]
    assert len(ids) == 2
    ok, _ = so.restore(con, ids[0])
    assert ok and con.execute("SELECT device_ip FROM fingerprint_devices WHERE id = 1").fetchone()[0] == '192.168.1.201'


def test_the_page_shows_bin_and_log_and_is_for_super_admin_only(web):
    c, con, da, so, calls = web
    c.get('/employees/delete/2')
    html = c.get('/admin/sensitive/?tab=bin').get_data(as_text=True)
    assert 'سلّة المحذوفات' in html and 'موظف 102' in html and 'استرجاع' in html
    # مستخدمٌ بلا صلاحية admin.danger لا يدخلها.
    con.execute("INSERT INTO users (username, password, role, full_name) VALUES ('hr', 'x', 'hr', 'HR')")
    con.commit()
    uid = con.execute("SELECT id FROM users WHERE username = 'hr'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'hr', 'username': 'hr'})
    r = c.get('/admin/sensitive/')
    assert r.status_code in (302, 403)


def test_super_admin_role_has_the_permission(web):
    c, con, da, so, calls = web
    n = con.execute("""SELECT COUNT(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id
                       JOIN permissions p ON p.id = rp.permission_id
                       WHERE r.name = 'Super Admin' AND p.code = 'admin.danger'""").fetchone()[0]
    assert n == 1


def test_delete_all_goes_to_the_bin_and_reaches_direct_devices(web):
    c, con, da, so, calls = web
    c.post('/employees/delete_all')
    assert con.execute('SELECT COUNT(*) FROM employees').fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM recycle_bin WHERE kind = 'employee'").fetchone()[0] == 3
    assert calls == [(['101', '102', '103'], [1, 3])], 'المباشرةُ كانت تُنسى في مسح الكلّ'


# ------------------------------------------------------------ الأجهزة المباشرة: الحذف المعلّق والنقل ببصماته

def test_an_offline_device_gets_the_delete_later_and_his_fingers_are_kept(web, monkeypatch):
    c, con, da, so, calls = web
    k40 = FakeK40([(1, '101', 'a'), (2, '102', 'b')], [(2, 0, b'FINGER-B')])
    state = {'up': False}

    def connect(device):
        if not state['up']:
            raise OSError('timed out')
        return k40
    monkeypatch.setattr(da, '_connect', connect)
    da.delete_from_direct('102', [1], conn=con)
    assert con.execute('SELECT user_id FROM device_pending_deletes WHERE device_id = 1').fetchone()[0] == '102'
    state['up'] = True
    out = da.enforce_all(con)
    assert out['deleted'] == 1 and _user(k40, '102') is None
    assert not con.execute('SELECT 1 FROM device_pending_deletes').fetchone()
    t = con.execute("SELECT template_data FROM fingerprint_templates WHERE pin = '102'").fetchone()
    assert base64.b64decode(t[0]) == b'FINGER-B', 'بصماتُه حُفظت قبل حذفه — تُرفع لو استُرجع'


def test_move_an_employee_with_his_fingers_from_one_k40_to_another(web, monkeypatch):
    c, con, da, so, calls = web
    src = FakeK40([(7, '101', 'a')], [(7, 0, b'F0'), (7, 6, b'F6')], fp_version=10)
    # على الجهاز الثاني موظّفٌ آخر يشغل المكانَ (uid) 1 — كان الرفعُ برقم الجدول يكتب فوقه.
    dst = FakeK40([(1, '999', 'غيره'), (101, '555', 'آخر')], [], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: src if d['id'] == 1 else dst)

    r = c.post('/fingerprint/api/sync_from_device', json={'device_ids': ['1'], 'user_pins': ['101']})
    res = r.get_json()['results'][0]
    assert res['success'] and '2 بصمة' in res['message'], res
    assert con.execute("SELECT COUNT(*) FROM fingerprint_templates WHERE pin = '101' AND major_ver = '10'"
                       ).fetchone()[0] == 2

    r = c.post('/fingerprint/api/upload_users', json={'user_ids': ['1'], 'device_ids': ['3']})
    det = r.get_json()['results'][0]['details'][0]
    assert det['status'] == 'success' and '2 بصمة' in det['message'], det
    moved = _user(dst, '101')
    assert moved is not None and moved.uid not in (1, 101), 'مكانٌ فارغ، لا فوق موظّفٍ آخر'
    assert _user(dst, '999') is not None and _user(dst, '555') is not None
    assert sorted(t.fid for t in dst.templates if t.uid == moved.uid) == [0, 6]


def test_fingers_of_another_algorithm_version_are_not_sent(web, monkeypatch):
    c, con, da, so, calls = web
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data)"
                " VALUES ('101', 1, 1, '12', ?)", (base64.b64encode(b'V12').decode(),))
    con.commit()
    dst = FakeK40([], [], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: dst)
    emp = con.execute("SELECT * FROM employees WHERE id = 1").fetchone()
    details, ver = da.push_direct(con, {'id': 1, 'device_ip': 'x'}, [emp])
    assert details[0]['status'] == 'warning' and 'بإصدار غير إصدار الجهاز' in details[0]['message']
    assert _user(dst, '101') is not None and not dst.templates


def test_the_pull_page_lists_direct_devices_too(web):
    c, con, da, so, calls = web
    html = c.get('/fingerprint/sync_from_device').get_data(as_text=True)
    assert 'K40-A' in html and 'SNADMS1' in html
    assert 'cb.disabled = true' not in html


def test_the_page_shows_each_fingerprint_version(web):
    c, con, da, so, calls = web
    from utils import biometric_templates as bio
    bio.save(con, '101', 0, 'QQ==', major_ver='10')
    bio.save(con, '101', 0, 'Qg==', major_ver='12')
    bio.save(con, '101', 1, 'Qw==', major_ver='12')
    con.execute("UPDATE fingerprint_devices SET bio_versions = '{\"1\": \"12\"}' WHERE id = 2")
    con.commit()
    html = c.get('/fingerprint/upload_users').get_data(as_text=True)
    assert 'v10: 1' in html and 'v12: 2' in html
    assert 'بصمة v12' in html, 'إصدارُ جهاز ADMS ظاهرٌ جنب اسمه'
