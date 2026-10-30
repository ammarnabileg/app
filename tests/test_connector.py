# -*- coding: utf-8 -*-
"""الوكيل المحلّيّ (المرحلة ١): السحابة ↔ الوكيل ↔ جهاز K40 — من أوّله لآخره."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.test_device_access import env  # noqa: F401
from tests.test_device_drivers import web  # noqa: F401


class Resp:
    def __init__(self, r):
        self.status_code = r.status_code
        self._data = r.get_json(silent=True)

    def json(self):
        return self._data


class ClientHttp:
    """requests-like فوق Flask test client."""
    def __init__(self, client):
        self.c = client
        self.calls = []

    def request(self, method, url, headers=None, data=None, timeout=None):
        path = url.split('://', 1)[1].split('/', 1)[1]
        self.calls.append((method, '/' + path))
        return Resp(self.c.open('/' + path, method=method, headers=headers, data=data))


class FakeK40:
    def __init__(self, logs, users):
        self.logs, self.users, self.ops = logs, users, []

    def disable_device(self): self.ops.append('disable')
    def enable_device(self): self.ops.append('enable')
    def disconnect(self): pass
    def get_attendance(self): return self.logs
    def get_users(self): return list(self.users)
    def get_fp_version(self): return 10
    def get_serialnumber(self): return 'SN-K40'
    def get_templates(self):
        return [SimpleNamespace(uid=u.uid, fid=0, valid=1, template=b'\x01\x02') for u in self.users]

    def delete_user(self, uid):
        self.ops.append(('delete', uid))
        self.users = [u for u in self.users if u.uid != uid]

    def set_user(self, uid, name, privilege, password, group_id, user_id, card):
        self.ops.append(('set_user', user_id))
        self.users = [u for u in self.users if u.uid != uid] + [
            SimpleNamespace(uid=uid, user_id=user_id, name=name, privilege=privilege, password=password,
                            group_id=group_id, card=card)]

    def set_time(self, t): self.ops.append(('time', t))


def _user(uid, pin, priv=0):
    return SimpleNamespace(uid=uid, user_id=pin, name='U' + pin, privilege=priv, password='', group_id='', card=0)


@pytest.fixture
def cloud(web, monkeypatch):
    c, con, A = web
    from utils import connector as C
    import utils.connector_agent as AG
    cid, key = C.create(con, 'الاستقبال')
    con.execute("UPDATE employees SET is_active = 1 WHERE id = 1")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, device_port, is_active, is_adms, driver, "
                "driver_options) VALUES (9, 'K40-HQ', '192.168.1.50', 4370, 1, 0, 'zk_connector', ?)",
                (json.dumps({'connector_id': cid}),))
    con.commit()
    t0 = datetime.now().replace(microsecond=0) - timedelta(hours=1)
    k40 = FakeK40([SimpleNamespace(user_id='101', timestamp=t0, status=0, punch=1),
                   SimpleNamespace(user_id='555', timestamp=t0 + timedelta(minutes=1), status=0, punch=1)],
                  [_user(1, '101'), _user(2, '555')])
    # write_user يرسل أمرًا خامًا للجهاز — هنا يُسجَّل فقط.
    monkeypatch.setattr('utils.device_access.write_user',
                        lambda dev, user, priv: (dev.ops.append(('write', user.user_id, priv)),
                                                 setattr(user, 'privilege', priv)))
    agent = AG.Agent('https://tenant.example', key, http=ClientHttp(A.app.test_client()),
                     zk_connect=lambda ip, port: k40)
    return agent, k40, con, C, cid, key, t0


def test_agent_brings_punches_and_users_up(cloud):
    agent, k40, con, C, cid, key, t0 = cloud
    r = agent.run_once()
    assert r['errors'] == [] and r['devices'] == 1 and r['punches'] == 2
    att = con.execute("SELECT check_time, device_id FROM attendance_records WHERE employee_id = 1").fetchall()
    assert [tuple(a) for a in att] == [(t0.strftime('%Y-%m-%d %H:%M:%S'), 9)]
    # 555 مش موظف: البصمة انتظرت، ثم قائمة المستخدمين أضافته موظفًا فدخلت حضوره.
    emp555 = con.execute("SELECT id FROM employees WHERE employee_number = '555'").fetchone()
    assert emp555 and con.execute('SELECT COUNT(*) FROM attendance_records WHERE employee_id = ?',
                                  (emp555[0],)).fetchone()[0] == 1
    assert con.execute('SELECT last_sync_time FROM fingerprint_devices WHERE id = 9').fetchone()[0] == \
        (t0 + timedelta(minutes=1)).strftime('%Y-%m-%d %H:%M:%S')
    # الدورة التانية: نفس البصمات (تداخل) — مفيش تكرار.
    agent.run_once()
    assert con.execute('SELECT COUNT(*) FROM attendance_records').fetchone()[0] == 2


def test_stop_and_delete_go_through_the_queue_with_template_backup(cloud):
    agent, k40, con, C, cid, key, t0 = cloud
    agent.run_once()
    from utils.devices import engine, registry
    dev = con.execute('SELECT * FROM fingerprint_devices WHERE id = 9').fetchone()
    assert not registry.is_legacy(dev), 'مسار ZK القديم لا يلمسه'
    # إيقاف 101 من البرنامج → المحرّك يكتب أمرًا، والوكيل ينفّذه بنفس بتّ «معطّل».
    engine.enforce_device(con, dev, {'101': datetime(2020, 1, 1)})
    assert [c['kind'] for c in C.fetch_commands(con, {'id': cid})] == ['disable_user']
    con.execute("UPDATE connector_commands SET status = 'pending'")          # كأنه لم يُرسَل
    con.commit()
    r = agent.run_once()
    assert r['commands'] == 1
    assert ('write', '101', 1) in k40.ops
    assert con.execute("SELECT status FROM connector_commands").fetchone()[0] == 'done'
    assert con.execute("SELECT device_sn FROM fingerprint_templates WHERE pin = '101'").fetchone()[0] == 'SN-K40', \
        'البصمات اتحفظت عندنا قبل الإيقاف'
    # حذف 555 من شاشة الحذف → الطابور → الجهاز.
    engine.delete_pins(con, dev, ['555'])
    agent.run_once()
    assert ('delete', 2) in k40.ops and '555' not in {u.user_id for u in k40.users}


def test_bad_key_and_foreign_device_are_refused(cloud, web):
    agent, k40, con, C, cid, key, t0 = cloud
    c = web[0]
    assert c.get('/api/connector/config', headers={'X-Connector-Key': 'onzc_wrong'}).status_code == 401
    assert c.get('/api/connector/config').status_code == 401
    r = c.post('/api/connector/punches', headers={'X-Connector-Key': key},
               json={'device_id': 1, 'punches': [{'pin': '101', 'time': '2026-10-01 08:00:00'}]})
    assert r.status_code == 404, 'جهاز مش مسند للوكيل ده'
    con.execute('UPDATE connectors SET is_active = 0')
    con.commit()
    assert c.get('/api/connector/config', headers={'X-Connector-Key': key}).status_code == 401


def test_upsert_and_unreachable_device(cloud):
    agent, k40, con, C, cid, key, t0 = cloud
    C.enqueue(con, 9, 'upsert_user', '777', {'name': 'جديد', 'card': '', 'enabled': True})
    C.enqueue(con, 9, 'set_time', None, {'time': '2026-10-09 10:00:00'})
    agent.run_commands({9: {'id': 9, 'name': 'K40-HQ', 'ip': 'x', 'port': 4370}})
    assert ('set_user', '777') in k40.ops and ('time', datetime(2026, 10, 9, 10, 0)) in k40.ops
    C.enqueue(con, 9, 'disable_user', '101')

    def down(ip, port):
        raise OSError('timed out')
    agent.zk_connect = down
    agent.run_commands({9: {'id': 9, 'name': 'K40-HQ', 'ip': 'x', 'port': 4370}})
    row = con.execute("SELECT status, result FROM connector_commands WHERE kind = 'disable_user'").fetchone()
    assert row['status'] == 'pending' and 'مش بيرد' in row['result'], 'يتعاد في الدورة الجاية'


def test_admin_page_shows_key_once(web):
    c, con, A = web
    c.post('/fingerprint/connectors', data={'action': 'create', 'name': 'فرع حولي'})
    first = c.get('/fingerprint/connectors').get_data(as_text=True)
    assert 'onzc_' in first and 'فرع حولي' in first
    assert 'value="onzc_' not in c.get('/fingerprint/connectors').get_data(as_text=True), 'مرة واحدة'
    from utils.devices import registry
    assert 'zk_connector' in registry.enabled_keys()
