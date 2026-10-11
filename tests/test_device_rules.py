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


def test_adms_device_gets_disable_then_enable(env):
    db, da, con = env
    da.set_allowed_devices(con, ['201'], [1])
    assert da.apply_rules_adms(con, ['201']) == 1
    p = json.loads(con.execute("SELECT payload FROM adms_commands WHERE device_id = 2").fetchone()[0])
    assert p['PIN'] == '201' and p['Enabled'] == 0 and p['Pri'] & 1, 'متوقف ببصماته — مش متمسح'
    da.set_allowed_devices(con, ['201'], None)
    assert da.apply_rules_adms(con, ['201']) == 1
    p = json.loads(con.execute("SELECT payload FROM adms_commands WHERE device_id = 2 ORDER BY id DESC").fetchone()[0])
    assert p['Enabled'] == 1 and not p['Pri'] & 1, 'رجع يبصم'
    # موظف تاني ماكانش متوقف على الجهاز: مايتبعتلوش حاجة (مايتعملش على جهاز ماكانش عليه).
    assert da.apply_rules_adms(con, ['202']) == 0


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
    assert 'PIN=201' in r and 'Enabled=0' in r


def test_any_userinfo_to_a_denied_device_goes_out_disabled(adms):
    """حفظ الموظف بيبعته لكل الأجهزة مفعّل — على الجهاز الممنوع بيوصل متوقف."""
    c, con, da = adms
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    da.set_allowed_devices(con, ['201'], [1])
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES "
                "(2, 'DATA UPDATE USERINFO', ?, 'PENDING')",
                (json.dumps({'PIN': '201', 'Name': 'x', 'Pri': 0, 'Enabled': 1}),))
    con.commit()
    r = c.get('/iclock/getrequest?SN=SNADMS1').get_data(as_text=True)
    assert 'PIN=201' in r and 'Enabled=0' in r and 'Pri=1' in r, r


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
