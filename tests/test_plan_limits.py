"""حدّ الموظفين من الاشتراك — والتجديد الذي كان يُقفل على من دفع.

## التجديد أوّلًا

مفتاح LE2 يحمل تاريخ انتهائه مكتوبًا فيه عند إصداره، ولا يتغيّر. واللوحة
عند الدفع تمدّد `expiry_date` في قاعدتها وتوقّع رمزًا (ONZ1) بالتاريخ
الجديد — ولا تُصدر مفتاحًا جديدًا. فكان الفحص المحليّ يرفض المفتاح يوم
تاريخه الأصليّ **قبل أن يُسأل الخادم**: عميلُ تجربةٍ دفع يُقفل عليه في
اليوم الثلاثين.

## ثم الحدّ

`max_employees` في الرمز الموقَّع. يُمنع التفعيل فوق الحدّ + 10%، ولا يُمنع
شيء قائم؛ والأجهزة تُنشئ فوق الحدّ موظفين غيرَ نشطين فلا تضيع بصمة.

الرموز هنا **موقَّعة فعلًا** بمفتاحٍ مولَّد للاختبار، والمفتاح العام يُستبدل
به — فيُقاس التحقّق الحقيقي لا بديلٌ عنه.

يُشغَّل:  python -m pytest tests/test_plan_limits.py -v
"""
import base64
import hashlib
import io
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HWID = 'a' * 32
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def sign(payload):
    """كما يوقّع LicenseSigner.php: JSON ثم RSA-SHA256 (PKCS#1 v1.5)."""
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    sig = _KEY.sign(raw, padding.PKCS1v15(), hashes.SHA256())
    return 'ONZ1.' + _b64u(raw) + '.' + _b64u(sig)


def token(key, expiry, max_employees=None, **extra):
    p = {'key': key, 'client': None, 'expiry': str(expiry), 'max_devices': 5,
         'max_employees': max_employees, 'hwid': HWID,
         'issued_at': '2026-09-24T00:00:00Z', 'nonce': 'n', 'v': 1}
    p.update(extra)
    return sign(p)


@pytest.fixture
def lic(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as d
    importlib.reload(d)
    d.init_db()
    import utils.license_verify as V
    importlib.reload(V)
    monkeypatch.setattr(V, 'LICENSE_PUBLIC_KEY', _PUB)
    import utils.license as L
    importlib.reload(L)
    monkeypatch.setattr(L, 'get_system_hwid', lambda: HWID)
    # ومن يستورده من مصدره مباشرةً (get_effective_max_devices) يرى الجهازَ نفسه.
    import utils.system_id as SID
    monkeypatch.setattr(SID, 'get_system_hwid', lambda: HWID)
    L.init_license_table(d.get_db_connection())
    import utils.plan_limits as P
    importlib.reload(P)
    return {'L': L, 'db': d, 'V': V, 'P': P}


def le2(L, expiry):
    """مفتاح LE2 بتوقيعه الصحيح وتاريخه المكتوب فيه."""
    ymd = expiry.strftime('%Y%m%d')
    sig = hashlib.sha256((ymd + L.LICENSE_SECRET).encode()).hexdigest()[:12]
    return L.encode_license_payload_fernet(ymd, sig)


class Server:
    """خادم التراخيص: يجيب بما تقوله اللوحة الآن."""

    def __init__(self, monkeypatch, L):
        self.reply = None          # None = الشبكة مقطوعة
        self.calls = 0
        monkeypatch.setattr(L.requests, 'post', self.post)

    def post(self, url, json=None, timeout=None):
        self.calls += 1
        if self.reply is None:
            raise requests.exceptions.ConnectionError('no route')

        class R:
            status_code = 200

            def __init__(s, body):
                s.body = body

            def json(s):
                return s.body
        return R(self.reply)


YESTERDAY = date.today() - timedelta(days=1)
NEXT_YEAR = date.today() + timedelta(days=365)


# ================================================== التجديد

def test_a_renewed_key_past_its_written_date_is_accepted(lic, monkeypatch):
    """تاريخُ المفتاح أمس، واللوحة جدّدت سنة: يعمل."""
    L = lic['L']
    key = le2(L, YESTERDAY)
    srv = Server(monkeypatch, L)
    srv.reply = {'success': True, 'license_token': token(key, NEXT_YEAR)}

    ok, msg = L.verify_license_full_flow(key)

    assert ok, msg
    assert srv.calls >= 1, 'الخادم يُسأل — هو من يعرف أن العميل دفع'


def test_a_key_the_server_did_not_renew_stays_refused(lic, monkeypatch):
    """بلا رمزٍ ساري، والخادمُ يقول «منتهٍ»: يبقى الرفض."""
    L = lic['L']
    key = le2(L, YESTERDAY)
    srv = Server(monkeypatch, L)
    srv.reply = {'success': False, 'message': 'License Expired'}

    ok, msg = L.verify_license_full_flow(key)

    assert not ok
    assert msg == L.EXPIRED_MSG


def test_a_token_for_another_key_does_not_revive_this_one(lic, monkeypatch):
    L = lic['L']
    key = le2(L, YESTERDAY)
    lic['db'].set_setting('license_token', token('LE2-someone-else', NEXT_YEAR))
    Server(monkeypatch, L)            # مقطوعة

    ok, _ = L.verify_license_full_flow(key)
    assert not ok


def test_an_expired_key_gets_no_offline_grace(lic, monkeypatch):
    """مهلةُ الأيّام لمن انقطع خادمُه — لا لمفتاحٍ انتهى ولا رمزَ يجدّده."""
    L = lic['L']
    key = le2(L, YESTERDAY)
    conn = lic['db'].get_db_connection()
    from datetime import datetime
    conn.execute("UPDATE license_settings SET last_ok_check = ? WHERE id = 1",
                 ((datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S'),))
    conn.commit()
    Server(monkeypatch, L)            # مقطوعة

    ok, _ = L.verify_license_full_flow(key)
    assert not ok


def test_a_renewal_already_held_works_offline(lic, monkeypatch):
    """جدّد وهو متّصل، ثم انقطع يوم تاريخ المفتاح: يعمل."""
    L = lic['L']
    key = le2(L, YESTERDAY)
    lic['db'].set_setting('license_token', token(key, NEXT_YEAR))
    Server(monkeypatch, L)            # مقطوعة

    ok, msg = L.verify_license_full_flow(key)
    assert ok, msg


def test_the_displayed_expiry_is_the_renewed_one(lic, monkeypatch):
    L = lic['L']
    key = le2(L, YESTERDAY)
    L.save_license_key(key, 'acme')
    srv = Server(monkeypatch, L)
    srv.reply = {'success': True, 'license_token': token(key, NEXT_YEAR)}
    L.invalidate_license_cache()

    info = L.get_current_license_info()

    assert info['ok']
    assert info['expiry'] == NEXT_YEAR
    assert info['days_left'] == 365


def test_a_forged_token_is_not_a_renewal(lic, monkeypatch):
    """رمزٌ بتوقيعٍ آخر: لا يُحيي شيئًا."""
    L = lic['L']
    key = le2(L, YESTERDAY)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    raw = json.dumps({'key': key, 'expiry': str(NEXT_YEAR), 'max_devices': 5,
                      'issued_at': 'x', 'hwid': HWID}).encode()
    forged = 'ONZ1.' + _b64u(raw) + '.' + _b64u(
        other.sign(raw, padding.PKCS1v15(), hashes.SHA256()))
    lic['db'].set_setting('license_token', forged)
    Server(monkeypatch, L)

    ok, _ = L.verify_license_full_flow(key)
    assert not ok


# ================================================== الحدّ

def test_hard_cap_is_ten_percent_rounded_up(lic):
    P = lic['P']
    assert [P.hard_cap(c) for c in (10, 20, 25, 40, 1)] == [11, 22, 28, 44, 2]


def _hold(lic, cap, key_expiry=NEXT_YEAR, token_key=None):
    L = lic['L']
    key = le2(L, key_expiry)
    L.save_license_key(key, 'acme')
    lic['db'].set_setting('license_token',
                          token(token_key or key, NEXT_YEAR, max_employees=cap))
    return key


def test_cap_comes_from_the_signed_token(lic):
    _hold(lic, 10)
    assert lic['P'].employee_cap() == 10


def test_no_field_means_no_cap(lic):
    """الرخص القديمة لا حدّ لها — تحديثٌ لا يُقفل على أحد."""
    L = lic['L']
    key = le2(L, NEXT_YEAR)
    L.save_license_key(key, 'acme')
    p = token(key, NEXT_YEAR)
    lic['db'].set_setting('license_token', p)
    assert lic['P'].employee_cap() is None


def test_a_token_for_another_key_sets_no_cap(lic):
    _hold(lic, 10, token_key='LE2-other')
    assert lic['P'].employee_cap() is None


def test_a_local_setting_cannot_raise_it(lic):
    """لا إعدادَ محليًّا يُقرأ: الحدّ من الرمز وحده."""
    _hold(lic, 10)
    lic['db'].set_setting('max_employees', '9999')
    assert lic['P'].employee_cap() == 10


# ================================================== المسارات

@pytest.fixture
def web(lic, tmp_path):
    import importlib
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [
        f for f in A.app.before_request_funcs.get(None, [])
        if f.__name__ != 'check_license_globally']
    A.app.config['TESTING'] = True
    A.app.config['WTF_CSRF_ENABLED'] = False
    path = os.path.join(str(tmp_path), 'hr_system.db')
    admin = sqlite3.connect(path).execute(
        "SELECT id FROM users WHERE role='admin'").fetchone()[0]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = admin
        s['role'] = 'admin'
        s['username'] = 'admin'
    return {'c': c, 'path': path, **lic}


def seed(path, n, active=1, start=1):
    con = sqlite3.connect(path)
    required = [r for r in con.execute('PRAGMA table_info(employees)')
                if r[3] == 1 and r[4] is None and r[1] != 'id']
    for i in range(start, start + n):
        v = {r[1]: (1 if r[2].upper() in ('INTEGER', 'REAL', 'NUMERIC') else 'x')
             for r in required}
        v.update({'employee_number': f'S{i}', 'name': f'seed {i}',
                  'is_active': active})
        con.execute(f"INSERT INTO employees ({', '.join(v)}) "
                    f"VALUES ({', '.join('?' * len(v))})", list(v.values()))
    con.commit()
    con.close()


def active(path):
    con = sqlite3.connect(path)
    try:
        return con.execute('SELECT COUNT(*) FROM employees WHERE is_active=1').fetchone()[0]
    finally:
        con.close()


def row(path, number):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute('SELECT * FROM employees WHERE employee_number=?',
                           (number,)).fetchone()
    finally:
        con.close()


def add(c, number, is_active=1):
    return c.post('/employees/add', data={
        'name': f'new {number}', 'employee_number': number, 'department': 'IT',
        'position': 'Dev', 'hire_date': '2026-09-01', 'salary': '500',
        'is_active': str(is_active)}, follow_redirects=True)


def test_add_is_allowed_up_to_the_margin_then_refused(web):
    """حدّ 10 ← يُقبل حتى 11، ويُرفض الثاني عشر."""
    _hold(web, 10)
    seed(web['path'], 10)

    add(web['c'], 'N1')
    assert row(web['path'], 'N1') is not None, 'الهامش: الحادي عشر يُقبل'

    r = add(web['c'], 'N2')
    assert row(web['path'], 'N2') is None
    assert 'onz.one/client/subscription' in r.get_data(as_text=True)
    assert active(web['path']) == 11


def test_without_a_cap_nothing_is_refused(web):
    seed(web['path'], 40)
    add(web['c'], 'N1')
    assert row(web['path'], 'N1') is not None


def test_an_inactive_employee_can_always_be_added(web):
    _hold(web, 10)
    seed(web['path'], 11)
    add(web['c'], 'N3', is_active=0)
    r = row(web['path'], 'N3')
    assert r is not None and r['is_active'] == 0


def _edit(c, emp, is_active):
    return c.post(f"/employees/edit/{emp['id']}", data={
        'name': emp['name'], 'employee_number': emp['employee_number'],
        'department': 'IT', 'position': 'Dev', 'hire_date': '2026-09-01',
        'salary': '500', 'is_active': str(is_active)}, follow_redirects=True)


def test_reactivation_counts_as_adding(web):
    _hold(web, 10)
    seed(web['path'], 11)
    seed(web['path'], 1, active=0, start=100)
    _edit(web['c'], row(web['path'], 'S100'), 1)
    assert row(web['path'], 'S100')['is_active'] == 0


def test_editing_an_active_employee_is_never_refused(web):
    """فوق الحدّ (نزل الاشتراك): التعديل يعمل — الحدّ لا يُقفل على البيانات."""
    _hold(web, 10)
    seed(web['path'], 15)
    emp = dict(row(web['path'], 'S3'))
    emp['name'] = 'اسمٌ معدَّل'
    _edit(web['c'], emp, 1)
    assert row(web['path'], 'S3')['name'] == 'اسمٌ معدَّل'


def _xlsx(numbers):
    import pandas as pd
    df = pd.DataFrame([{'Employee Number': n, 'Name': f'imp {n}', 'Department': 'IT',
                        'Position': 'Dev', 'Hire Date': '2026-09-01', 'Salary': 500}
                       for n in numbers])
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_import_fills_the_room_and_reports_the_rest(web):
    _hold(web, 10)
    seed(web['path'], 10)            # يبقى مكانٌ واحد (الهامش)
    r = web['c'].post('/employees/import',
                      data={'file': (_xlsx(['I1', 'I2', 'I3']), 'e.xlsx')},
                      content_type='multipart/form-data')
    assert r.status_code == 200
    got = [n for n in ('I1', 'I2', 'I3') if row(web['path'], n) is not None]
    assert got == ['I1']
    assert 'حدَّ الاشتراك' in r.get_data(as_text=True)


def test_import_still_updates_existing_rows_over_the_cap(web):
    _hold(web, 10)
    seed(web['path'], 15)
    web['c'].post('/employees/import',
                  data={'file': (_xlsx(['S2']), 'e.xlsx')},
                  content_type='multipart/form-data')
    assert row(web['path'], 'S2')['name'] == 'imp S2'


def test_a_device_user_over_the_cap_is_kept_inactive(web):
    """بصمةُ مستخدمٍ جديد لا تُرفض: يُنشأ غيرَ نشط."""
    _hold(web, 10)
    seed(web['path'], 11)
    con = sqlite3.connect(web['path'])
    con.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active) "
                "VALUES ('SN123', '10.0.0.9', 1)")
    con.commit()
    con.close()

    r = web['c'].post('/cdata?SN=SN123&table=OPERLOG',
                      data='USER PIN=777\tName=Walk In\tPri=0\tPasswd=\tCard=0\tGrp=1\n',
                      content_type='text/plain')
    assert r.status_code == 200
    emp = row(web['path'], '777')
    assert emp is not None, 'البصمةُ تجد صاحبها'
    assert emp['is_active'] == 0


def test_a_device_user_under_the_cap_is_active(web):
    _hold(web, 10)
    seed(web['path'], 3)
    con = sqlite3.connect(web['path'])
    con.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active) "
                "VALUES ('SN123', '10.0.0.9', 1)")
    con.commit()
    con.close()
    web['c'].post('/cdata?SN=SN123&table=OPERLOG',
                  data='USER PIN=778\tName=Walk In\tPri=0\tPasswd=\tCard=0\tGrp=1\n',
                  content_type='text/plain')
    assert row(web['path'], '778')['is_active'] == 1


def test_license_page_shows_usage(web):
    _hold(web, 10)
    seed(web['path'], 11)
    body = web['c'].get('/license').get_data(as_text=True)
    assert 'employee-usage' in body
    assert '11' in body and '10' in body


def test_device_pull_fills_the_room_then_adds_inactive(lic, tmp_path):
    """«مزامنة مستخدمي الأجهزة إلى الموظفين» لجهاز ADMS — خارج أيّ طلب.

    وكانت لا تُضيف أحدًا أصلًا لجهاز ADMS: سطرٌ يسمّي متغيّرًا غير معرَّف
    (`local_pass`) يرمي NameError بعد قراءة المستخدمين، فيُبتلع ويُسجَّل
    «خطأ في جمع المستخدمين».
    """
    _hold(lic, 10)
    path = os.path.join(str(tmp_path), 'hr_system.db')
    seed(path, 10)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO fingerprint_devices (device_name, device_ip, is_active, is_adms) "
                "VALUES ('SN9', '10.0.0.9', 1, 1)")
    dev = con.execute('SELECT id FROM fingerprint_devices').fetchone()[0]
    for pin in (901, 902, 903):
        con.execute("INSERT INTO fingerprint_users (user_id, device_id, name, privilege) "
                    "VALUES (?, ?, ?, 0)", (pin, dev, f'dev {pin}'))
    con.commit()
    con.close()

    import importlib
    import fingerprint_sync as F
    importlib.reload(F)
    F.FingerprintSyncManager(db_path=path).sync_users_to_employees()

    got = {n: row(path, n) for n in ('901', '902', '903')}
    assert all(got.values()), 'كلُّ مستخدمي الجهاز صاروا موظفين — لا بصمةَ بلا صاحب'
    assert [got[n]['is_active'] for n in ('901', '902', '903')] == [1, 0, 0]
