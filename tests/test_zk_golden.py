# -*- coding: utf-8 -*-
"""لقطةُ سلوك ZKTeco اليوم (خارطة الطريق، المرحلة ٠) — حارسٌ قبل أيّ توحيدٍ لمساره.

دفعةٌ واحدة فيها كلُّ الحالات (موظّف نشط، رقمٌ غير معروف، موقوف، تكرار، جهازان، بصمةٌ
قبل الإيقاف وبعده) تمرّ بمسار ADMS وبالمسار المباشر، والناتجُ كلُّه مكتوبٌ هنا حرفيًّا.
أيُّ تعديلٍ يغيّر نتيجةً واحدة يُسقط الاختبار — فيُرى قبل أن يصل لعميل.
"""
import importlib
from datetime import datetime

import pytest

from tests.test_device_access import env  # noqa: F401


def _seed(con):
    # 102 موقوف منذ 2026-10-05 (اليوم التالي لنهاية خدمته 2026-10-04)
    con.execute("UPDATE employees SET end_of_service_date = '2026-10-04' WHERE id = 2")
    con.commit()


def _snapshot(con):
    att = [tuple(r) for r in con.execute(
        'SELECT e.employee_number, a.device_id, a.check_time, CAST(a.check_type AS TEXT) FROM attendance_records a '
        'JOIN employees e ON e.id = a.employee_id ORDER BY 1, 3, 2')]
    pend = [tuple(r) for r in con.execute('SELECT pin, device_id, check_time FROM pending_punches ORDER BY 1, 3')]
    ign = [tuple(r) for r in con.execute(
        'SELECT e.employee_number, i.check_time FROM ignored_punches i JOIN employees e ON e.id = i.employee_id '
        'ORDER BY 1, 2')]
    return att, pend, ign


BATCH = [
    ('101', '2026-10-06 08:00:00', 0),   # نشط
    ('101', '2026-10-06 08:00:00', 0),   # تكرار حرفيّ
    ('101', '2026-10-06 17:00:00', 1),
    ('777', '2026-10-06 08:05:00', 0),   # ليس موظّفًا بعد
    ('102', '2026-10-04 08:00:00', 0),   # قبل إيقافه — تُحسب
    ('102', '2026-10-06 08:00:00', 0),   # بعد إيقافه — تُتجاهل
]


def test_adms_path_snapshot(env):
    db, da, con = env
    _seed(con)
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    c = adms_server.app.test_client()
    c.get('/iclock/cdata?SN=SNADMS1&options=all')
    body = ''.join(f'{p}\t{t}\t{s}\t1\t0\t0\n' for p, t, s in BATCH)
    assert c.post('/iclock/cdata?SN=SNADMS1&table=ATTLOG&Stamp=1', data=body,
                  content_type='text/plain').get_data(as_text=True) == 'OK'
    att, pend, ign = _snapshot(con)
    assert att == [('101', 2, '2026-10-06 08:00:00', '0'), ('101', 2, '2026-10-06 17:00:00', '1'),
                   ('102', 2, '2026-10-04 08:00:00', '0')]
    assert pend == [('777', 2, '2026-10-06 08:05:00')]
    assert ign == [('102', '2026-10-06 08:00:00')]
    # والموقوفُ يُعطَّل على الجهاز نفسه بأمر — لا يُحذف.
    cmds = [r[0] for r in con.execute("SELECT command_type FROM adms_commands WHERE device_id = 2")]
    assert 'DATA UPDATE USERINFO' in cmds and 'DATA DELETE USERINFO' not in cmds


def test_direct_path_snapshot(env, monkeypatch):
    db, da, con = env
    _seed(con)
    from zk.attendance import Attendance
    import fingerprint_sync as fs
    logs = [Attendance(p, datetime.strptime(t, '%Y-%m-%d %H:%M:%S'), s) for p, t, s in BATCH]

    class Dev:
        def disable_device(self): pass
        def enable_device(self): pass
        def disconnect(self): pass
        def get_attendance(self): return logs
    m = fs.FingerprintSyncManager(db_path=db.DB_PATH)
    monkeypatch.setattr(m, 'connect_to_device', lambda info: (Dev(), None))
    dev = dict(con.execute('SELECT * FROM fingerprint_devices WHERE id = 1').fetchone())
    saved = m.sync_device_attendance(dev)
    att, pend, ign = _snapshot(con)
    assert saved == 3
    assert att == [('101', 1, '2026-10-06 08:00:00', '0'), ('101', 1, '2026-10-06 17:00:00', '1'),
                   ('102', 1, '2026-10-04 08:00:00', '0')]
    assert pend == [('777', 1, '2026-10-06 08:05:00')]
    assert ign == [('102', '2026-10-06 08:00:00')]
    assert con.execute('SELECT last_sync_time FROM fingerprint_devices WHERE id = 1').fetchone()[0] == \
        '2026-10-06 17:00:00'
