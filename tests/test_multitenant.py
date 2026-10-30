# -*- coding: utf-8 -*-
"""Multi-tenant (utils/tenancy): شركتين على نفس السيرفر — كل واحدة ماتشوفش التانية بأي طريقة.

بيشتغل بس على PostgreSQL:  HR_DB_URL=postgresql://… python -m pytest tests/test_multitenant.py
"""
import importlib
import os
import sys
import threading
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytestmark = pytest.mark.skipif(
    not (os.environ.get('HR_DB_URL') or '').startswith(('postgres://', 'postgresql://')),
    reason='multi-tenant محتاج PostgreSQL (HR_DB_URL)')


@pytest.fixture
def mt(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('HR_MULTI_TENANT', '1')
    monkeypatch.setenv('HR_TENANT_BASE_DOMAIN', 'example.test')
    monkeypatch.delenv('HR_DB_SCHEMA', raising=False)
    import utils.db as db
    importlib.reload(db)
    from utils import tenancy, dbcompat
    importlib.reload(tenancy)
    sfx = uuid.uuid4().hex[:8]
    a, b = f'a{sfx}', f'b{sfx}'
    tenancy.create(a, 'شركة أ')
    tenancy.create(b, 'شركة ب')
    for slug, name in ((a, 'موظف_أ_سري'), (b, 'موظف_ب_سري')):
        with tenancy.use(slug):
            c = db.connect()
            c.execute("INSERT INTO employees (name, employee_number, department, position, hire_date, salary, is_active)"
                      " VALUES (?, '1', 'D', 'P', '2025-01-01', 100, 1)", (name,))
            c.commit()
            c.close()
    yield db, tenancy, a, b
    dbcompat.close_all()
    import psycopg
    with psycopg.connect(os.environ['HR_DB_URL'], autocommit=True) as c:
        c.execute("SET lock_timeout = '10s'")
        for s in (a, b):
            c.execute(f'DROP SCHEMA IF EXISTS "{tenancy.schema_of(s)}" CASCADE')
            c.execute(f'DELETE FROM {tenancy.PLATFORM}.tenants WHERE slug = %s', (s,))
    tenancy.invalidate()


def _app(db, tenancy):
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    A.app.config['TESTING'] = True
    return A


def _names(r):
    j = r.get_json(silent=True) or {}
    return [e.get('name') for e in j.get('employees', [])]


def _login(client, tenancy, slug, host):
    with tenancy.use(slug):
        import utils.db as db
        c = db.connect()
        uid = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        c.close()
    client.get('/login', base_url=f'http://{host}')            # الجلسة تتربط بالشركة
    with client.session_transaction(base_url=f'http://{host}') as s:
        s.update({'_tenant': slug, 'user_id': uid, 'role': 'admin', 'username': 'admin'})


def test_each_host_sees_only_its_company(mt):
    db, tenancy, a, b = mt
    A = _app(db, tenancy)
    ca, cb = A.app.test_client(), A.app.test_client()
    _login(ca, tenancy, a, f'{a}.example.test')
    _login(cb, tenancy, b, f'{b}.example.test')
    pa = _names(ca.get('/api/employees', base_url=f'http://{a}.example.test'))
    pb = _names(cb.get('/api/employees', base_url=f'http://{b}.example.test'))
    assert 'موظف_أ_سري' in pa and 'موظف_ب_سري' not in pa
    assert 'موظف_ب_سري' in pb and 'موظف_أ_سري' not in pb


def test_unknown_or_suspended_host_is_404(mt):
    db, tenancy, a, b = mt
    A = _app(db, tenancy)
    c = A.app.test_client()
    assert c.get('/login', base_url='http://nobody.example.test').status_code == 404
    assert c.get('/login', base_url='http://evil.com').status_code == 404
    tenancy.set_status(b, 'suspended')
    assert c.get('/login', base_url=f'http://{b}.example.test').status_code == 404
    assert c.get('/login', base_url=f'http://{a}.example.test').status_code == 200


def test_a_session_from_one_company_does_not_open_another(mt):
    db, tenancy, a, b = mt
    A = _app(db, tenancy)
    c = A.app.test_client()
    _login(c, tenancy, a, f'{a}.example.test')
    assert 'موظف_أ_سري' in _names(c.get('/api/employees', base_url=f'http://{a}.example.test'))
    # نفس الكوكي (المتصفح مابيبعتهوش لدومين تاني، بس حد ممكن ينسخه) على شركة ب
    with c.session_transaction(base_url=f'http://{a}.example.test') as s:
        stolen = dict(s)
    with c.session_transaction(base_url=f'http://{b}.example.test') as s:
        s.update(stolen)
    r = c.get('/api/employees', base_url=f'http://{b}.example.test')
    assert r.status_code in (302, 401) and 'موظف_ب_سري' not in _names(r), 'اتطلب منه يدخل تاني'


def test_no_company_no_database(mt):
    db, tenancy, a, b = mt
    with pytest.raises(tenancy.NoTenant):
        db.connect()
    with tenancy.use(a):
        assert db.connect().execute('SELECT name FROM employees').fetchone()[0] == 'موظف_أ_سري'


def test_adms_device_goes_to_its_company_only(mt):
    db, tenancy, a, b = mt
    tenancy.route_device('SNA1', a)
    with pytest.raises(ValueError):
        tenancy.route_device('SNA1', b)                     # جهاز مايتنقلش لشركة تانية في صمت
    import adms_server as S
    importlib.reload(S)
    c = S.app.test_client()
    assert c.get('/iclock/getrequest?SN=SNA1').status_code == 200
    assert c.get('/iclock/getrequest?SN=UNKNOWN9').status_code == 404
    A = _app(db, tenancy)
    ca = A.app.test_client()
    assert ca.get('/iclock/getrequest?SN=UNKNOWN9', base_url=f'http://{a}.example.test').status_code == 404, \
        'جهاز مش متسجّل مايدخلش بالدومين'
    assert ca.get('/iclock/getrequest?SN=SNA1', base_url='http://whatever.test').status_code == 200


def test_caches_threads_and_files_are_per_company(mt):
    db, tenancy, a, b = mt
    from utils import settings_utils as su
    importlib.reload(su)
    for slug, n in ((a, 'اسم أ'), (b, 'اسم ب')):
        with tenancy.use(slug):
            c = db.connect()
            c.execute('UPDATE system_settings SET company_name = ? WHERE id = 1', (n,))
            c.commit()
            c.close()
    import flask
    app = flask.Flask('t')
    with app.app_context():
        with tenancy.use(a):
            assert su.get_system_settings().get('company_name') == 'اسم أ'
    with app.app_context():
        with tenancy.use(b):
            assert su.get_system_settings().get('company_name') == 'اسم ب', 'الكاش مش مشترك'
    seen = []
    with tenancy.use(a):
        t = threading.Thread(target=tenancy.bind(lambda: seen.append(tenancy.current())))
        t.start(); t.join()
        fa = tenancy.upload_folder()
    with tenancy.use(b):
        fb = tenancy.upload_folder()
    assert seen == [a]
    assert fa != fb and fa.endswith(os.path.join(a, 'uploads'))
    assert sorted(set(tenancy.for_each(tenancy.current).values()) & {a, b}) == sorted([a, b])
