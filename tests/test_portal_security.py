"""بوابة الموظفين: ما يفصل «على شبكة المكتب» عن «في البيت».

فحصتُ البوابة على نظام يعمل فوجدت أربعة، ثلاثة منها هنا:

  * قيد شبكة الفرع كان يُقرأ من ترويسة يكتبها المتصفح. موظف خارج
    الشبكة رُدّ بـ403، وبإضافة X-Forwarded-For سُجّلت بصمة حضوره.
  * حساب أُوقف بقيت جلسته عاملة: قرأ بياناته وسجّل بصمة بعد الإيقاف.
  * طلبات الإجازة بلا فحص: نهاية قبل بداية، ونوع لا وجود له، ومكرّر.

يُشغَّل:  python -m pytest tests/test_portal_security.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.net import client_ip, ip_allowed, trusted_proxy_hops


class FakeRequest:
    """طلب بعنوان مُرسِل وترويسات — كما يصل Flask."""

    def __init__(self, remote_addr, xff=None):
        self.remote_addr = remote_addr
        self.headers = {} if xff is None else {'X-Forwarded-For': xff}


# ------------------------------------------- الترويسة لا تُصدَّق بلا وسيط

def test_the_header_is_ignored_when_there_is_no_proxy(monkeypatch):
    """بيت القصيد.

    التركيب المحلي في مكتب العميل — وهو الحال الذي يُستعمل فيه قيد
    الشبكة أصلًا — لا وسيط فيه، فالترويسة كلّها من العميل.
    """
    monkeypatch.delenv('HR_TRUSTED_PROXY_HOPS', raising=False)

    req = FakeRequest('203.0.113.9', xff='10.20.30.0')
    assert client_ip(req) == '203.0.113.9', 'صُدّقت ترويسة المتصفح'


def test_the_spoofed_header_does_not_match_the_branch(monkeypatch):
    """الصورة كاملةً: موظف في البيت يدّعي عنوان المكتب."""
    monkeypatch.delenv('HR_TRUSTED_PROXY_HOPS', raising=False)

    branch_ips = ['10.20.30.0', '10.20.31.*']
    home = FakeRequest('203.0.113.9', xff='10.20.30.0')

    assert ip_allowed(client_ip(home), branch_ips) is False


def test_a_real_office_address_still_passes(monkeypatch):
    monkeypatch.delenv('HR_TRUSTED_PROXY_HOPS', raising=False)

    office = FakeRequest('10.20.30.0')
    assert ip_allowed(client_ip(office), ['10.20.30.0']) is True


# --------------------------------------- وخلف وسيط حقيقي تُقرأ كما يجب

def test_behind_one_proxy_the_client_is_found(monkeypatch):
    """النشر السحابي: Traefik أمام النظام، وremote_addr عنوانه هو."""
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')

    req = FakeRequest('172.18.0.2', xff='10.20.30.0')
    assert client_ip(req) == '10.20.30.0'


def test_behind_one_proxy_a_spoofed_prefix_is_discarded(monkeypatch):
    """الوسيط يُلحق عنوان من كلّمه، فآخر السلسلة هو الصادق.

    العميل يرسل X-Forwarded-For: 10.20.30.0 فيصير لدى الوسيط
    "10.20.30.0, <عنوانه الحقيقي>" — ونحن نأخذ الأخير.
    """
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '1')

    req = FakeRequest('172.18.0.2', xff='10.20.30.0, 203.0.113.9')
    assert client_ip(req) == '203.0.113.9'
    assert ip_allowed(client_ip(req), ['10.20.30.0']) is False


def test_a_chain_shorter_than_declared_falls_back(monkeypatch):
    """سلسلة أقصر مما أُعلن: لا نأخذ أوّلها لأنه من العميل."""
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '3')

    req = FakeRequest('172.18.0.2', xff='10.20.30.0')
    assert client_ip(req) == '172.18.0.2'


def test_hops_are_bounded(monkeypatch):
    monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', '9999')
    assert trusted_proxy_hops() <= 8

    for bad in ('', 'abc', '-4', 'null'):
        monkeypatch.setenv('HR_TRUSTED_PROXY_HOPS', bad)
        assert trusted_proxy_hops() == 0


def test_the_default_is_no_trust(monkeypatch):
    monkeypatch.delenv('HR_TRUSTED_PROXY_HOPS', raising=False)
    assert trusted_proxy_hops() == 0


# --------------------------------------------- المطابقة على حدود النقاط

def test_a_prefix_does_not_swallow_another_network():
    """'startswith' وحدها كانت تجعل 10.0.0.1 تقبل 10.0.0.100."""
    assert ip_allowed('10.0.0.100', ['10.0.0.1']) is False
    assert ip_allowed('10.0.0.1', ['10.0.0.1']) is True


def test_wildcards_match_whole_octets():
    allowed = ['192.168.1.*']

    assert ip_allowed('192.168.1.5', allowed) is True
    assert ip_allowed('192.168.1.200', allowed) is True
    assert ip_allowed('192.168.10.5', allowed) is False, 'ابتلع شبكة أخرى'
    assert ip_allowed('192.168.1', allowed) is True


def test_empty_values_never_match():
    assert ip_allowed('', ['10.0.0.1']) is False
    assert ip_allowed('10.0.0.1', []) is False
    assert ip_allowed('10.0.0.1', ['', '   ']) is False


# ----------------------------------- الحساب الموقوف لا تبقى جلسته عاملة

def _app_with_user(tmp_path, active=1):
    import sqlite3

    os.environ['HR_DATA_DIR'] = str(tmp_path)
    import importlib

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.execute("DELETE FROM users WHERE username = 'someone'")
    con.execute(
        "INSERT INTO users (username, password, full_name, role, is_active) "
        "VALUES ('someone', 'x', 'Someone', 'employee', ?)", (active,))
    uid = con.execute("SELECT id FROM users WHERE username='someone'").fetchone()[0]
    con.commit()
    con.close()
    return uid


def test_a_deactivated_account_fails_the_session_check(tmp_path):
    """جرّبتُه حيًّا: الحساب أُوقف فبقي يقرأ بياناته ويسجّل بصمة."""
    from flask import Flask

    import importlib
    import utils.auth as auth

    uid = _app_with_user(tmp_path, active=1)
    importlib.reload(auth)

    app = Flask(__name__)
    app.secret_key = 'test'

    with app.test_request_context('/'):
        from flask import session
        session['user_id'] = uid
        assert auth._session_user_is_active() is True

    import sqlite3
    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    con.execute("UPDATE users SET is_active = 0 WHERE id = ?", (uid,))
    con.commit()
    con.close()

    with app.test_request_context('/'):
        from flask import session
        session['user_id'] = uid
        assert auth._session_user_is_active() is False, 'بقيت الجلسة نافذة بعد الإيقاف'


def test_a_deleted_account_fails_too(tmp_path):
    from flask import Flask

    import importlib
    import utils.auth as auth

    _app_with_user(tmp_path, active=1)
    importlib.reload(auth)

    app = Flask(__name__)
    app.secret_key = 'test'
    with app.test_request_context('/'):
        from flask import session
        session['user_id'] = 999999
        assert auth._session_user_is_active() is False


def test_no_session_is_not_active(tmp_path):
    from flask import Flask

    import importlib
    import utils.auth as auth

    _app_with_user(tmp_path)
    importlib.reload(auth)

    app = Flask(__name__)
    app.secret_key = 'test'
    with app.test_request_context('/'):
        assert auth._session_user_is_active() is False


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
