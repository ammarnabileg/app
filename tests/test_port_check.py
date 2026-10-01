# -*- coding: utf-8 -*-
"""المُشغِّل لا يقول «بنجاح» ولا يفتح المتصفّح على برنامجٍ آخر يحجز المنفذ.

العطلُ كما وصل: «تم تشغيل الخادم بنجاح على 5001» ثم «Not Found» في
المتصفّح — المنفذُ كان مشغولًا، والحجزُ فشل بصمت، والنجاحُ أُعلن لحظةَ
بدء الخيط.
"""
import http.server
import os
import socket
import sys
import threading
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from utils import port_check as pc  # noqa: E402


def _free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Foreign(http.server.BaseHTTPRequestHandler):
    """برنامجٌ آخر: يردّ «Not Found» كما ردّ على المنفذ 5001."""

    def do_GET(self):
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'Not Found')

    def log_message(self, *a):
        pass


@pytest.fixture
def foreign():
    srv = http.server.HTTPServer(('0.0.0.0', _free_port()), _Foreign)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def ours(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import importlib
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    from werkzeug.serving import make_server
    import app as app_mod
    srv = make_server('127.0.0.1', _free_port(), app_mod.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def test_every_response_is_signed_with_the_version(ours):
    from utils.version_info import CURRENT_VERSION
    r = pc.probe(ours)
    assert r == {'answering': True, 'ours': True, 'version': CURRENT_VERSION}


def test_another_program_on_the_port_is_not_taken_for_ours(foreign):
    r = pc.probe(foreign)
    assert r['answering'] and not r['ours']


def test_nothing_on_the_port():
    assert pc.probe(_free_port(), timeout=1) == {'answering': False, 'ours': False, 'version': None}


def test_a_busy_port_is_seen_before_starting(foreign):
    assert pc.port_free(foreign) is False
    assert pc.port_free(_free_port()) is True


NETSTAT = """
Active Connections

  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1012
  TCP    0.0.0.0:5001           0.0.0.0:0              LISTENING       7344
  TCP    127.0.0.1:50011        0.0.0.0:0              LISTENING       9000
  TCP    127.0.0.1:5001         127.0.0.1:61000        ESTABLISHED     7344
"""


def test_netstat_finds_who_listens_on_exactly_that_port():
    assert pc.parse_netstat_pid(NETSTAT, 5001) == 7344
    assert pc.parse_netstat_pid(NETSTAT, 501) is None     # لا يُخلط 5001 بـ501 ولا بـ50011
    assert pc.parse_netstat_pid(NETSTAT, 50011) == 9000


def test_tasklist_name():
    out = '"HRSystem.exe","7344","Console","1","152,340 K"\r\n'
    assert pc.parse_tasklist_name(out) == 'HRSystem.exe'
    assert pc.parse_tasklist_name('INFO: No tasks are running which match the specified criteria.') is None


def test_the_message_says_an_old_copy_of_the_program_holds_the_port():
    msg = pc.busy_message(5001, (7344, 'HRSystem.exe'), 'ar')
    assert '5001' in msg and 'نسخةٍ أخرى من البرنامج' in msg and 'بجانب الساعة' in msg
    other = pc.busy_message(5001, (55, 'python.exe'), 'ar')
    assert 'python.exe' in other and 'إعدادات المنفذ' in other
    assert 'ببرنامجٍ آخر' in pc.busy_message(5001, None, 'ar')


# ------------------------------------------------ المُشغِّل نفسُه

@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """launcher.py بلا واجهة: customtkinter وpystray وtkinter بدائلُ فارغة."""
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    from unittest.mock import MagicMock

    class _Stub(types.ModuleType):
        def __getattr__(self, attr):
            return MagicMock(name=f'{self.__name__}.{attr}')

    for name in ('customtkinter', 'pystray', 'tkinter', 'tkinter.filedialog'):
        monkeypatch.setitem(sys.modules, name, _Stub(name))
    monkeypatch.delitem(sys.modules, 'launcher', raising=False)
    import importlib
    import utils.db as db
    importlib.reload(db)
    import launcher as L
    return L


def test_the_launcher_reports_a_busy_port_instead_of_success(launcher, foreign, monkeypatch):
    started = []
    monkeypatch.setattr(launcher, 'start_flask', lambda: started.append(1))
    got = []
    launcher.start_server(foreign, lambda ok, err=None: got.append((ok, err)))
    assert got and got[0][0] is False, got
    assert str(foreign) in got[0][1]
    assert not started, 'بدأ الخادمُ على منفذٍ مشغول'


def test_the_launcher_says_success_only_once_the_program_answers(launcher, monkeypatch):
    from werkzeug.serving import make_server
    import app as app_mod
    port = _free_port()

    def serve():
        make_server('0.0.0.0', int(os.environ['APP_PORT']), app_mod.app).serve_forever()

    monkeypatch.setattr(launcher, 'start_flask', serve)
    got = []
    launcher.start_server(port, lambda ok, err=None: got.append((ok, err)))
    assert got == [(True, None)]
    assert pc.probe(port)['ours']


def test_a_server_that_dies_is_a_failure_not_a_success(launcher, monkeypatch):
    monkeypatch.setattr(launcher, 'start_flask', lambda: None)    # يخرج فورًا كما يخرج serve() حين يفشل
    got = []
    launcher.start_server(_free_port(), lambda ok, err=None: got.append((ok, err)))
    assert got and got[0][0] is False and 'error.log' in got[0][1]
