# -*- coding: utf-8 -*-
"""مين يبصم فين — وتصحيح وقت بصمات جهازٍ ساعتُه كانت غلط.

الحالة عند العميل: موظفين في الإدارة وفي السوق وفي الفروع. كلّهم بيتنزّلوا على كل
الأجهزة ببصماتهم، والموظف يبقى متوقف على أجهزة غير مكانه — الجهاز يرفض بصمته.
"""
import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from test_device_access import FakeK40, _user  # noqa: E402


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
    for i, (num, branch) in enumerate((('201', 'الإدارة'), ('202', 'السوق'), ('203', '')), start=1):
        con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary,"
                    " is_active, card_number, branch_location) VALUES (?, ?, ?, 'D', 'P', '2025-01-01', 500, 1, '0', ?)",
                    (i, 'موظف ' + num, num, branch))
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, branch_name)"
                " VALUES (1, 'K40', '192.168.1.201', 4370, 1, 0, 'الإدارة')")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms, branch_name)"
                " VALUES (2, 'SNADMS1', '10.0.0.5', 1, 1, 'السوق')")
    con.commit()
    yield db, da, con


def test_no_rule_means_every_device_as_before(env):
    db, da, con = env
    r = da.restriction_map(con)
    assert r == {} and not da.denied_on(r, '201', 1) and not da.denied_on(r, '201', 2)


def test_limit_to_one_device_and_back(env):
    db, da, con = env
    assert da.set_allowed_devices(con, ['201'], [1]) == ['201']
    r = da.restriction_map(con)
    assert not da.denied_on(r, '201', 1) and da.denied_on(r, '201', 2)
    assert not da.denied_on(r, '202', 2), 'الباقيين زي ما هم'
    assert da.set_allowed_devices(con, ['201'], [1]) == [], 'نفس الأجهزة: مفيش تغيير'
    assert da.set_allowed_devices(con, ['201'], None) == ['201']
    assert da.restriction_map(con) == {}


def test_adms_denied_device_backs_up_then_deletes_and_restores(env):
    """SpeedFace بيتجاهل Enabled=0 — فالممنوع بيتمسح من جهاز ADMS، وبصماته محفوظة عندنا الأول."""
    db, da, con = env
    da.set_allowed_devices(con, ['201'], [1])
    assert da.apply_rules_adms(con, ['201']) == 1
    cmds = [r[0] for r in con.execute("SELECT command_type FROM adms_commands WHERE device_id = 2")]
    assert 'DATA DELETE USERINFO' not in cmds and 'DATA QUERY BIODATA' in cmds, 'بصماته مش عندنا: نطلبها الأول'
    # البصمات وصلت من الجهاز (BIODATA بالـSN بتاعه)
    con.execute("UPDATE fingerprint_devices SET serial_number = 'SNADMS1' WHERE id = 2")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data, device_sn)"
                " VALUES ('201', 0, 1, '12', 'TPL', 'SNADMS1')")
    assert da.adms_restrict_pending(con, 2) == 1
    p = json.loads(con.execute("SELECT payload FROM adms_commands WHERE command_type = 'DATA DELETE USERINFO'").fetchone()[0])
    assert p == {'PIN': '201'}
    # اتسمح له تاني: يترفع ببياناته وبصماته
    da.set_allowed_devices(con, ['201'], None)
    assert da.apply_rules_adms(con, ['201']) == 1
    rows = con.execute("SELECT command_type, payload FROM adms_commands WHERE device_id = 2 ORDER BY id").fetchall()
    up = [json.loads(r[1]) for r in rows if r[0] == 'DATA UPDATE USERINFO']
    assert up and up[-1]['Enabled'] == 1 and not up[-1]['Pri'] & 1
    assert any(r[0] in ('DATA UPDATE BIODATA', 'DATA UPDATE FINGERTMP') and 'TPL' in r[1] for r in rows), 'ببصماته'
    assert da.apply_rules_adms(con, ['202']) == 0, 'موظف ماكانش ممنوع: مفيش أوامر'


def test_adms_delete_after_waiting_if_device_sends_nothing(env):
    db, da, con = env
    da.set_allowed_devices(con, ['202'], [1])
    da.apply_rules_adms(con, ['202'])
    later = datetime.now() + timedelta(hours=da.RESTRICT_WAIT_HOURS, minutes=1)
    assert da.adms_restrict(con, 2, '202', now=later) == 'deleted'


def test_direct_device_disables_restricted_and_restores(env, monkeypatch):
    db, da, con = env
    k40 = FakeK40(users=[(1, '201', 'a'), (2, '202', 'b')], templates=[(2, 0, b'\x02' * 40)])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    da.set_allowed_devices(con, ['202'], [2])            # 202 في السوق بس
    con.commit()
    res = da.enforce_all(con)
    assert res['removed'] == 1, res
    assert _user(k40, '202').privilege & da.DISABLED_BIT, 'متوقف على جهاز الإدارة'
    assert not (_user(k40, '201').privilege & da.DISABLED_BIT)
    assert [t.uid for t in k40.templates] == [2], 'ببصماته'
    assert da.enforce_all(con)['restored'] == 0, 'لسه ممنوع: مايرجعش'
    da.set_allowed_devices(con, ['202'], [1, 2])
    con.commit()
    assert da.enforce_all(con)['restored'] == 1
    assert not (_user(k40, '202').privilege & da.DISABLED_BIT)


@pytest.fixture
def adms(env):
    db, da, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    return adms_server.app.test_client(), con, da


def test_adms_punch_on_a_wrong_device_is_not_counted(adms):
    c, con, da = adms
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    con.execute("UPDATE fingerprint_devices SET serial_number = 'SNADMS1' WHERE id = 2")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, template_data, device_sn)"
                " VALUES ('201', 0, 1, 'T', 'SNADMS1')")
    before = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    da.set_allowed_devices(con, ['201'], [1])
    con.commit()
    now = (datetime.now() + timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1',
           data=f'201\t{before}\t0\t1\t0\t0\n201\t{now}\t0\t1\t0\t0\n202\t{now}\t0\t1\t0\t0\n',
           content_type='text/plain')
    rows = sorted((r[0], r[1]) for r in con.execute(
        'SELECT e.employee_number, a.check_time FROM attendance_records a JOIN employees e ON e.id = a.employee_id'))
    assert rows == sorted([('201', before), ('202', now)]), 'قبل التحديد بتتحسب، وبعده لأ'
    ign = con.execute('SELECT reason FROM ignored_punches').fetchall()
    assert [r[0] for r in ign] == [da.WRONG_DEVICE_REASON]
    r = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
    assert 'DATA DELETE USERINFO PIN=201' in r, r


def test_any_upload_to_a_denied_adms_device_is_skipped(adms):
    """حفظ الموظف بيبعته لكل الأجهزة — على الجهاز الممنوع مايترفعش."""
    c, con, da = adms
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    da.set_allowed_devices(con, ['201'], [1])
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES "
                "(2, 'DATA UPDATE USERINFO', ?, 'PENDING')",
                (json.dumps({'PIN': '201', 'Name': 'x', 'Pri': 0, 'Enabled': 1}),))
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES "
                "(2, 'DATA UPDATE BIODATA', ?, 'PENDING')", (json.dumps({'PIN': '201', 'TMP': 'x'}),))
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES "
                "(2, 'DATA UPDATE USERINFO', ?, 'PENDING')", (json.dumps({'PIN': '202', 'Name': 'y'}),))
    con.commit()
    r = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
    assert 'UPDATE USERINFO\tPIN=201' not in r and 'BIODATA\tPin=201' not in r, r
    assert 'PIN=202' in r
    st = [x[0] for x in con.execute("SELECT status FROM adms_commands WHERE payload LIKE '%\"201\"%' "
                                     "AND command_type LIKE 'DATA UPDATE%'")]
    assert st == ['SKIPPED', 'SKIPPED']


def test_auto_branch_and_page(env, monkeypatch):
    db, _da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    import utils.device_access as da2
    monkeypatch.setattr(da2, 'apply_now', lambda: None)
    client = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with client.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    r = client.post('/fingerprint/device-access/save', json={'auto_branch': ['201', '202', '203']})
    j = r.get_json()
    assert j['changed'] == 2 and len(j['skipped']) == 1, j
    m = da2.restriction_map(con)
    assert m['201'][1] == {1} and m['202'][1] == {2} and '203' not in m
    page = client.get('/fingerprint/device-access').get_data(as_text=True)
    assert 'مين يبصم فين' in page and 'موظف 201' in page
    page = client.get('/fingerprint/punch-shift').get_data(as_text=True)
    assert 'تصحيح وقت البصمات' in page and 'SNADMS1' in page
    j = client.post('/fingerprint/punch-shift', json={'action': 'preview', 'devices': [2], 'minutes': -60,
                                                      'date_from': '2026-10-01 00:00', 'date_to': '2026-10-11 23:59'}).get_json()
    assert j['success'] and j['count'] == 0
    assert 'مين يبصم فين' in client.get('/fingerprint/devices').get_data(as_text=True)


# ------------------------------------------------------------ تصحيح وقت البصمات

def test_punch_shift_preview_apply_undo(env):
    db, da, con = env
    from utils import punch_shift as ps
    rows = [(1, 2, '2026-10-05 09:00:00'), (1, 2, '2026-10-05 10:00:00'), (2, 2, '2026-10-05 18:00:00'),
            (2, 1, '2026-10-05 09:00:00'), (3, 2, '2026-10-12 09:00:00')]
    for emp, dev, t in rows:
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (?, ?, ?, 0)",
                    (emp, dev, t))
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source) "
                "VALUES (1, 2, '2026-10-06 09:00:00', 0, 'manual')")
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) "
                "VALUES (2, 1, '2026-10-05 17:00:00', 0)")   # موجودة أصلًا على جهاز تاني
    con.commit()
    pv = ps.preview(con, [2], '2026-10-01', '2026-10-11', -60)
    assert pv['count'] == 2 and pv['duplicates'] == 1, pv
    res = ps.apply(con, [2], '2026-10-01', '2026-10-11', -60)
    assert res['moved'] == 2
    times = sorted(r[0] for r in con.execute('SELECT check_time FROM attendance_records WHERE employee_id = 1'))
    assert times == ['2026-10-05 08:00:00', '2026-10-05 09:00:00', '2026-10-06 09:00:00'], 'الباليد ماتلمستش'
    assert con.execute("SELECT 1 FROM attendance_records WHERE employee_id = 3 AND check_time = '2026-10-12 09:00:00'").fetchone()
    assert ps.undo(con, res['batch']) == 2
    times = sorted(r[0] for r in con.execute('SELECT check_time FROM attendance_records WHERE employee_id = 1'))
    assert times == ['2026-10-05 09:00:00', '2026-10-05 10:00:00', '2026-10-06 09:00:00']
    assert ps.undo(con, res['batch']) == 0, 'مرة واحدة'


# ------------------------------------------------------------ بصمته تنفع على الجهاز؟

def test_readiness_by_version_and_origin(env):
    db, da, con = env
    con.execute("UPDATE fingerprint_devices SET bio_versions = '{\"1\": \"10\"}' WHERE id = 1")
    con.execute("UPDATE fingerprint_devices SET bio_versions = '{\"1\": \"12\", \"9\": \"7\"}', "
                "serial_number = 'AJE1' WHERE id = 2")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data, device_sn)"
                " VALUES ('201', 0, 1, '10', 'x', '192.168.1.201')")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data, device_sn)"
                " VALUES ('202', 0, 9, '7', 'y', 'OTHER')")
    con.commit()
    devs = con.execute('SELECT * FROM fingerprint_devices').fetchall()
    cells, labels = da.bio_readiness(con, devs)
    assert cells[('201', 1)] == 'ok' and cells[('201', 2)] == 'none', 'بصمة K40 (10) ماتنفعش على SpeedFace (12)'
    assert cells[('202', 2)] == 'ok' and cells[('202', 1)] == 'none', 'وشّه بإصدار SpeedFace'
    assert cells[('203', 1)] == 'empty'
    assert 'بصمة 12' in labels[2]
    con.execute("UPDATE fingerprint_devices SET bio_versions = NULL WHERE id = 2")
    cells, _ = da.bio_readiness(con, con.execute('SELECT * FROM fingerprint_devices').fetchall())
    assert cells[('201', 2)] == 'unknown'


def test_save_warns_when_he_cannot_punch_anywhere_allowed(env, monkeypatch):
    db, _da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    import utils.device_access as da2
    monkeypatch.setattr(da2, 'apply_now', lambda: None)
    con.execute("UPDATE fingerprint_devices SET bio_versions = '{\"1\": \"12\"}' WHERE id = 2")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data)"
                " VALUES ('201', 0, 1, '10', 'x')")
    con.commit()
    client = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with client.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    j = client.post('/fingerprint/device-access/save', json={'rows': [{'num': '201', 'devices': [2]}]}).get_json()
    assert j['changed'] == 1 and j['warnings'] == ['موظف 201'], j
    page = client.get('/fingerprint/device-access').get_data(as_text=True)
    assert 'fa-exclamation-triangle text-warning small' in page and 'data-ready="none"' in page


def test_pushing_to_a_denied_direct_device_lands_disabled(env, monkeypatch):
    db, da, con = env
    k40 = FakeK40(users=[], templates=[])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    da.set_allowed_devices(con, ['201'], [2])
    con.commit()
    dev = con.execute('SELECT * FROM fingerprint_devices WHERE id = 1').fetchone()
    emp = con.execute("SELECT * FROM employees WHERE employee_number = '201'").fetchone()
    da.push_direct(con, dev, [emp])
    assert _user(k40, '201').privilege & da.DISABLED_BIT, 'اترفع ببياناته بس متوقف'
    assert '"1": "10"' in con.execute('SELECT bio_versions FROM fingerprint_devices WHERE id = 1').fetchone()[0], \
        'إصدار الجهاز المباشر اتحفظ'


def test_branches_come_from_the_branches_page_and_can_be_assigned(env, monkeypatch):
    db, _da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    import utils.device_access as da2
    monkeypatch.setattr(da2, 'apply_now', lambda: None)
    con.execute("INSERT OR IGNORE INTO branches (name, is_active) VALUES ('فرع حولي', 1)")
    con.commit()
    client = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with client.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    page = client.get('/fingerprint/device-access').get_data(as_text=True)
    assert 'value="فرع حولي"' in page, 'فرع من صفحة الفروع، حتى لو مفيش موظف فيه لسه'
    assert 'موظف مالهمش فرع' in page
    j = client.post('/fingerprint/device-access/save', json={'set_branch': 'فرع حولي', 'nums': ['203']}).get_json()
    assert j['moved'] == 1
    assert con.execute("SELECT branch_location FROM employees WHERE employee_number = '203'").fetchone()[0] == 'فرع حولي'
    page = client.get('/employees/edit/3').get_data(as_text=True)
    assert 'list="branchNames"' in page and '<option value="فرع حولي">' in page, 'خانة الفرع بتقترح الفروع'


def test_editing_an_employee_reaches_direct_devices_now(env, monkeypatch):
    """تعديل الموظف كان بيتبعت لأجهزة ADMS بس — الأجهزة المباشرة كانت مستنية رفع يدوي."""
    db, da, con = env
    k40 = FakeK40(users=[(5, '201', 'old name')], templates=[])
    monkeypatch.setattr(da, '_connect', lambda device: k40)
    con.execute("UPDATE employees SET name = 'الاسم الجديد', card_number = '777' WHERE employee_number = '201'")
    con.commit()
    res = da.push_employee('201', con)
    assert res['pushed'] == 1 and not res['errors'], res
    u = [x for x in k40.users if x.user_id == '201']
    assert u[-1].name == 'الاسم الجديد' and u[-1].uid == 5, 'نفس مكانه على الجهاز'
    # ممنوع على الجهاز ده: بيوصل متوقف
    da.set_allowed_devices(con, ['202'], [2])
    da.push_employee('202', con)
    assert _user(k40, '202').privilege & da.DISABLED_BIT
    # موقوف: مايترفعش
    con.execute("UPDATE employees SET is_active = 0 WHERE employee_number = '203'")
    con.commit()
    assert da.push_employee('203', con)['pushed'] == 0


def test_employee_edit_route_pushes_to_direct_devices():
    src = open(os.path.join(ROOT, 'routes', 'employee_routes.py'), encoding='utf-8').read()
    assert src.count('device_access.push_employee_now(') == 2, 'الإضافة والتعديل'


def test_print_report_groups_by_branch_and_filters(env, monkeypatch):
    db, _da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    import utils.device_access as da2
    da2.set_allowed_devices(con, ['202'], [2])
    con.commit()
    client = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with client.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    page = client.get('/fingerprint/device-access/print').get_data(as_text=True)
    assert 'تقرير مين يبصم فين' in page and 'الفرع: الإدارة' in page and 'الفرع: السوق' in page
    assert 'كل الأجهزة' in page and 'موظف 202' in page
    row = page[page.index('موظف 202'):page.index('موظف 202') + 600]
    assert 'السوق' in row and 'الإدارة' in row and 'SNADMS1' not in row, 'بأسماء الفروع مش الأجهزة'
    page = client.get('/fingerprint/device-access/print?state=limited').get_data(as_text=True)
    assert 'موظف 202' in page and 'موظف 201' not in page
    page = client.get('/fingerprint/device-access/print?branch=__none__').get_data(as_text=True)
    assert 'موظف 203' in page and 'موظف 201' not in page
    assert 'printReport()' in client.get('/fingerprint/device-access').get_data(as_text=True)


def test_place_labels_use_branch_names(env):
    db, da, con = env
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms, branch_name)"
                " VALUES (3, 'K40-2', '192.168.1.202', 1, 0, 'الإدارة')")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms)"
                " VALUES (4, 'NOBR', '192.168.1.203', 1, 0)")
    con.commit()
    assert da.place_labels(con, [1, 3]) == ['الإدارة']
    assert da.place_labels(con, [1]) == ['الإدارة (K40)']
    assert da.place_labels(con, [2, 4]) == ['السوق', 'NOBR']


def test_move_to_branch_and_restrict_in_one_step(env, monkeypatch):
    db, _da, con = env
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    import utils.device_access as da2
    monkeypatch.setattr(da2, 'apply_now', lambda: None)
    client = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with client.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    j = client.post('/fingerprint/device-access/save',
                    json={'set_branch': 'السوق', 'nums': ['201', '203'], 'restrict': True}).get_json()
    assert j['moved'] == 2 and j['changed'] == 2, j
    m = da2.restriction_map(con)
    assert m['201'][1] == {2} and m['203'][1] == {2}
    assert con.execute("SELECT branch_location FROM employees WHERE employee_number = '203'").fetchone()[0] == 'السوق'
    j = client.post('/fingerprint/device-access/save',
                    json={'set_branch': 'فرع مفيهوش أجهزة', 'nums': ['202'], 'restrict': True}).get_json()
    assert j['no_devices'] and j['changed'] == 0
    page = client.get('/fingerprint/device-access').get_data(as_text=True)
    assert 'id="move-restrict"' in page and 'value="1"' in page or 'value="1' in page
