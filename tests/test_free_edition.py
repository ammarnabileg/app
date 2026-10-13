# -*- coding: utf-8 -*-
"""النسخة المجانية: تجربة كاملة ٣٠ يوم، وبعدها الأجهزة والبصمات بس — والباقي ظاهر ومقفول."""
import importlib
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _set_license(pf, monkeypatch, features, **data):
    monkeypatch.setattr(pf, 'licensed_features', lambda: frozenset(features) if features is not None else None)
    monkeypatch.setattr(pf, '_license_data', lambda: dict(data, features=features))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import utils.plan_features as pf
    importlib.reload(pf)
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ != 'check_license_globally']
    c = A.app.test_client()
    con = sqlite3.connect(db.DB_PATH)
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    return c, pf, con


def test_editions(client, monkeypatch):
    c, pf, con = client
    _set_license(pf, monkeypatch, None)
    assert pf.edition() == 'full', 'رخصة قديمة من غير مزايا: كاملة'
    _set_license(pf, monkeypatch, ['payroll', 'leaves'])
    assert pf.edition() == 'full'
    future = (date.today() + timedelta(days=29)).isoformat()
    _set_license(pf, monkeypatch, [pf.FREE_MARKER], trial_until=future)
    assert pf.edition() == 'trial' and pf.trial_days_left() == 30 and pf.has_feature('payroll')
    _set_license(pf, monkeypatch, [pf.FREE_MARKER], trial_until=(date.today() - timedelta(days=1)).isoformat())
    assert pf.edition() == 'free' and not pf.has_feature('payroll') and pf.has_feature('devices')


def test_free_after_trial_locks_everything_but_devices(client, monkeypatch):
    c, pf, con = client
    _set_license(pf, monkeypatch, [pf.FREE_MARKER], trial_until='2020-01-01')
    for url in ('/fingerprint/devices', '/fingerprint/punches', '/employees', '/fingerprint/device-lookup',
                '/fingerprint/punches/export'):
        r = c.get(url)
        assert r.status_code == 200, (url, r.status_code)
    for url in ('/payroll/monthly', '/fingerprint/device-access', '/attendance/oracle', '/fingerprint/smart_reports',
                '/leaves', '/reports/first-last'):
        r = c.get(url)
        assert r.status_code == 403 and 'مش في النسخة المجانية' in r.get_data(as_text=True), url
    page = c.get('/fingerprint/devices').get_data(as_text=True)
    assert 'النسخة المجانية' in page and '/payroll/monthly' in page, 'البانر وقائمة المقفول للقفل في القائمة'
    # جهاز ADMS بيكلّم السيرفر عادي
    assert c.get('/iclock/getrequest?SN=X').status_code == 200


def test_trial_opens_everything_with_a_banner(client, monkeypatch):
    c, pf, con = client
    _set_license(pf, monkeypatch, [pf.FREE_MARKER], trial_until=(date.today() + timedelta(days=9)).isoformat())
    r = c.get('/payroll/monthly')
    assert r.status_code == 200
    assert 'تجربة كاملة' in c.get('/fingerprint/devices').get_data(as_text=True)


def test_free_background_jobs_and_cap(client, monkeypatch):
    c, pf, con = client
    _set_license(pf, monkeypatch, [pf.FREE_MARKER], trial_until='2020-01-01')
    from utils import cloud_sync, message_outbox, oracle_db, plan_limits
    assert cloud_sync.run_once(force=True).get('skipped') == 'free_edition'
    assert message_outbox.run_once(force=True).get('skipped') == 'free_edition'
    assert oracle_db._free_edition() is True
    import utils.license as lic
    monkeypatch.setattr(lic, 'signed_license_for', lambda k: {'data': {'features': [pf.FREE_MARKER]}})
    monkeypatch.setattr(lic, 'get_saved_license_key', lambda: 'k')
    assert plan_limits.employee_cap() == 25


def test_paid_license_is_untouched(client, monkeypatch):
    c, pf, con = client
    _set_license(pf, monkeypatch, ['payroll', 'leaves', 'eos'])
    assert c.get('/fingerprint/device-access').status_code == 200, 'المدفوع: نفس القفل القديم بس'
    from utils import oracle_db
    assert oracle_db._free_edition() is False
