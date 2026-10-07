# -*- coding: utf-8 -*-
"""الموظّفُ نفسُه ببصمةٍ على K40 (إصدار 10) وأخرى على SpeedFace (إصدارٌ آخر) — ٢.٣٣.١.

كان الجدولُ يحفظ نسخةً واحدة لكلّ إصبع: سحبُ أحدهما يمحو الآخر، ووجهُ SpeedFace
يمحو الإصبعَ 0، والرفعُ لـADMS بلا نظرٍ للإصدار. الآن نسخةٌ لكلّ إصدار، ولكلّ جهازٍ
ما يقرؤه.
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

from tests.test_device_access import FakeK40, _user, env  # noqa: E402,F401


def b64(x):
    return base64.b64encode(x).decode()


@pytest.fixture
def adms(env):
    db, da, con = env
    con.execute("UPDATE employees SET is_active = 1")
    con.commit()
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    return adms_server.app.test_client(), con, da


def _rows(con, pin='101'):
    return sorted((r[0], r[1], r[2] or '') for r in con.execute(
        'SELECT template_type, finger_id, major_ver FROM fingerprint_templates WHERE pin = ?', (pin,)))


def test_the_old_one_copy_per_finger_table_is_migrated_without_losing_rows(tmp_path):
    from utils import biometric_templates as bio
    con = sqlite3.connect(str(tmp_path / 'old.db'))
    con.execute('''CREATE TABLE fingerprint_templates (id INTEGER PRIMARY KEY AUTOINCREMENT, device_sn TEXT,
        pin TEXT NOT NULL, finger_id INTEGER NOT NULL, valid INTEGER DEFAULT 1, duress INTEGER DEFAULT 0,
        template_type INTEGER DEFAULT 9, major_ver TEXT, minor_ver TEXT, format TEXT,
        template_data TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, UNIQUE(pin, finger_id))''')
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, major_ver, template_data)"
                " VALUES ('101', 0, 1, '10.0', 'A'), ('101', 1, 1, '10', 'B')")
    assert bio.migrate(con) is True
    assert bio.migrate(con) is False, 'مرّةً واحدة — init_db يناديه عند كلّ إقلاع'
    assert sorted(r[0] for r in con.execute('SELECT major_ver FROM fingerprint_templates')) == ['10', '10']
    # الآن يجاورها إصبعُ 0 بإصدار 12 — ولا يمحوها.
    bio.save(con, '101', 0, 'C', major_ver='12')
    bio.save(con, '101', 0, 'A2', major_ver='10')            # نفسُ الإصدار: يستبدل
    got = sorted((r[0], r[1]) for r in con.execute(
        "SELECT major_ver, template_data FROM fingerprint_templates WHERE finger_id = 0"))
    assert got == [('10', 'A2'), ('12', 'C')]


def test_fresh_database_has_the_versioned_key(env):
    db, da, con = env
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name = 'fingerprint_templates'").fetchone()[0]
    assert 'unique(pin,finger_id)' not in sql.lower().replace(' ', '')
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'ux_fp_templates_version'").fetchone()


def test_speedface_fingers_and_face_live_next_to_the_k40_copy(adms):
    c, con, da = adms
    from utils import biometric_templates as bio
    bio.save(con, '101', 0, b64(b'K40-F0'), major_ver='10', fmt='pyzk', device_sn='K40SN001')
    con.commit()
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    body = ('BIODATA Pin=101\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=1\tMajorVer=12\tMinorVer=0\tFormat=0\tTmp=U0YwMA==\n'
            'BIODATA Pin=101\tNo=3\tIndex=0\tValid=1\tDuress=0\tType=1\tMajorVer=12\tMinorVer=0\tFormat=0\tTmp=U0YzMw==\n'
            'BIODATA Pin=101\tNo=0\tIndex=0\tValid=1\tDuress=0\tType=9\tMajorVer=5\tMinorVer=8\tFormat=0\tTmp=RkFDRQ==\n')
    r = c.post('/iclock/cdata?SN=SNADMS1&table=BIODATA&Stamp=1', data=body, content_type='text/plain')
    assert r.status_code == 200
    assert _rows(con) == [(1, 0, '10'), (1, 0, '12'), (1, 3, '12'), (9, 0, '5')], \
        'كلُّ الأسطر حُفظت، ولا نسخةَ محت أخرى'
    vers = json.loads(con.execute('SELECT bio_versions FROM fingerprint_devices WHERE id = 2').fetchone()[0])
    assert vers == {'1': '12', '9': '5'}


def test_the_device_tells_its_versions_in_getrequest_info(adms):
    c, con, da = adms
    c.get('/iclock/getrequest?SN=SNADMS1&INFO=Ver 8.0.4.7-20230116,3,5,40,10.0.0.5,12,7,15,2,111')
    vers = json.loads(con.execute('SELECT bio_versions FROM fingerprint_devices WHERE id = 2').fetchone()[0])
    assert vers == {'1': '12', '2': '7'}


def test_each_device_gets_the_copy_it_can_read(env):
    db, da, con = env
    from utils import biometric_templates as bio
    import utils.fingerprint_utils as fu
    importlib.reload(fu)
    bio.save(con, '101', 0, b64(b'v10-0'), major_ver='10', device_sn='K40SN001')
    bio.save(con, '101', 1, b64(b'v10-1'), major_ver='10', device_sn='K40SN001')
    bio.save(con, '101', 0, b64(b'v12-0'), major_ver='12', device_sn='SPEED1')
    con.execute("UPDATE fingerprint_devices SET bio_versions = '{\"1\": \"12\"}', serial_number = 'SPEED1' WHERE id = 2")
    con.commit()
    st = {}
    cmds = fu.adms_template_commands(con, '101', 2, st)
    sent = [(json.loads(p)['FingerID'], json.loads(p)['MajorVer']) for _t, p in cmds]
    assert sent == [(0, '12')], 'لـSpeedFace نسختُه وحدها — إصبعُ 1 بإصدار 10 لا ينفعه'
    assert st == {'sent': 1, 'skipped': 1}
    # جهازٌ لا نعرف إصدارَه بعد: ما جاء منه هو، وإلّا الأحدث.
    con.execute("UPDATE fingerprint_devices SET bio_versions = NULL WHERE id = 2")
    con.commit()
    sent = sorted((json.loads(p)['FingerID'], json.loads(p)['MajorVer']) for _t, p in fu.adms_template_commands(con, '101', 2))
    assert sent == [(0, '12'), (1, '10')]


def test_k40_gets_its_v10_copy_even_after_speedface_was_pulled(env, monkeypatch):
    db, da, con = env
    from utils import biometric_templates as bio
    src = FakeK40([(7, '101', 'a')], [(7, 0, b'K40-F0'), (7, 6, b'K40-F6')], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: src)
    da.pull_direct(con, {'id': 1, 'device_ip': '192.168.1.201'}, ['101'])
    # ثمّ جاءت من SpeedFace نسخةُ الإصبع 0 بإصدار 12 — كانت تمحو نسخةَ K40.
    bio.save(con, '101', 0, b64(b'SF-F0'), major_ver='12', device_sn='SPEED1')
    con.commit()
    dst = FakeK40([], [], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: dst)
    emp = con.execute("SELECT * FROM employees WHERE employee_number = '101'").fetchone()
    details, ver = da.push_direct(con, {'id': 3, 'device_ip': 'x'}, [emp])
    assert details[0]['status'] == 'success', details
    u = _user(dst, '101')
    assert sorted((t.fid, bytes(t.template)) for t in dst.templates if t.uid == u.uid) == \
        [(0, b'K40-F0'), (6, b'K40-F6')]


def test_a_device_with_no_matching_copy_says_enroll_once(env, monkeypatch):
    db, da, con = env
    from utils import biometric_templates as bio
    bio.save(con, '101', 0, b64(b'SF-F0'), major_ver='12')
    con.commit()
    dst = FakeK40([], [], fp_version=10)
    monkeypatch.setattr(da, '_connect', lambda d: dst)
    emp = con.execute("SELECT * FROM employees WHERE employee_number = '101'").fetchone()
    details, _ = da.push_direct(con, {'id': 3, 'device_ip': 'x'}, [emp])
    assert details[0]['status'] == 'warning' and 'يبصم عليه مرة واحدة' in details[0]['message']
    assert _user(dst, '101') is not None and not dst.templates


def test_info_parsing():
    from utils.biometric_templates import parse_info, norm_ver
    assert parse_info('Ver 2.0.12-20090806,0,0,0,192.168.1.201,10,7,0,0,111') == {1: '10', 2: '7'}
    assert parse_info('') == {} and parse_info('Ver,1,2') == {}
    assert norm_ver('10.0') == '10' and norm_ver(None) == '' and norm_ver(12) == '12'
