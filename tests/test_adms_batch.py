# -*- coding: utf-8 -*-
"""الجهاز كان بياخد ١٠ أوامر بس في كل سؤال: رفع ٣٠٠٠ موظف ببصماتهم لجهاز جديد = ساعات.
دلوقتي ١٠٠٠ أمر (ومن غير ما الرد يعدّي ٢ ميجا)، واللي اتبعت ومارجعش ردّه بيتبعت تاني."""
import importlib
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def adms(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms, serial_number)"
                " VALUES (1, 'SNB', '10.0.0.9', 1, 1, 'SNB')")
    con.commit()
    import utils.license as L
    monkeypatch.setattr(L, 'get_effective_max_devices', lambda: 0)
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    return adms_server.app.test_client(), con, R


def _queue(con, n, payload=None):
    con.executemany("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES "
                    "(1, 'DATA UPDATE USERINFO', ?, 'PENDING')",
                    [(json.dumps(payload or {'PIN': str(1000 + i), 'Name': f'e{i}'}),) for i in range(n)])
    con.commit()


def _cmds(r):
    return [x for x in r.get_data(as_text=True).split('\n') if x.startswith('C:')]


def test_up_to_1000_commands_per_poll(adms):
    c, con, R = adms
    _queue(con, 1500)
    assert len(_cmds(c.get('/iclock/getrequest?SN=SNB'))) == 1000
    assert len(_cmds(c.get('/iclock/getrequest?SN=SNB'))) == 500
    assert con.execute("SELECT COUNT(*) FROM adms_commands WHERE status = 'SENT'").fetchone()[0] == 1500


def test_reply_size_is_capped(adms):
    c, con, R = adms
    _queue(con, 30, {'PIN': '1', 'Name': 'x' * 200000})      # ~200KB كل أمر
    first = _cmds(c.get('/iclock/getrequest?SN=SNB'))
    assert 1 <= len(first) <= 11, len(first)
    assert sum(len(x) for x in first) <= R.ADMS_MAX_REPLY_BYTES
    assert con.execute("SELECT COUNT(*) FROM adms_commands WHERE status = 'PENDING'").fetchone()[0] == 30 - len(first)


def test_unanswered_commands_are_resent_then_failed(adms):
    c, con, R = adms
    _queue(con, 2)
    c.get('/iclock/getrequest?SN=SNB')
    ids = [r[0] for r in con.execute('SELECT id FROM adms_commands ORDER BY id')]
    c.post('/iclock/devicecmd?SN=SNB', data=f'ID={ids[0]}&Return=0&CMD=DATA\n', content_type='text/plain')
    for attempt in range(2, 4):
        con.execute("UPDATE adms_commands SET updated_at = datetime('now', '-20 minutes') WHERE status = 'SENT'")
        con.commit()
        assert [x.split(':')[1] for x in _cmds(c.get('/iclock/getrequest?SN=SNB'))] == [str(ids[1])], 'بيتبعت تاني'
        assert con.execute('SELECT attempts FROM adms_commands WHERE id = ?', (ids[1],)).fetchone()[0] == attempt
    con.execute("UPDATE adms_commands SET updated_at = datetime('now', '-20 minutes') WHERE status = 'SENT'")
    con.commit()
    assert _cmds(c.get('/iclock/getrequest?SN=SNB')) == []
    st = dict(con.execute('SELECT id, status FROM adms_commands').fetchall())
    assert st[ids[0]] == 'OK' and st[ids[1]] == 'FAILED (no reply)'


def test_recent_sent_commands_are_not_resent(adms):
    c, con, R = adms
    _queue(con, 1)
    c.get('/iclock/getrequest?SN=SNB')
    assert _cmds(c.get('/iclock/getrequest?SN=SNB')) == [], 'لسه مستني ردّه'
