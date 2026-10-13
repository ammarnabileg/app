# -*- coding: utf-8 -*-
"""أقل ضغط على أجهزة ADMS: مفيش سحب كامل كل ساعة، ومفيش اتصال مباشر بيها، وحفظ الموظف
مايبعتش حاجة لو مفيش حاجة تخص الجهاز اتغيّرت، والطابور بيتنضف من كل الحالات."""
import importlib
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active,"
                " card_number) VALUES (1, 'أحمد', '101', 'D', 'P', '2025-01-01', 500, 1, '55')")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms, serial_number)"
                " VALUES (1, 'SNQ', '192.168.2.23', 1, 1, 'SNQ')")
    con.execute("INSERT INTO fingerprint_templates (pin, finger_id, template_type, template_data, device_sn)"
                " VALUES ('101', 0, 1, 'TPL', 'SNQ')")
    con.execute("INSERT INTO fingerprint_users (user_id, device_id, name) VALUES ('101', 1, 'أحمد')")
    con.commit()
    import utils.license as L
    monkeypatch.setattr(L, 'get_effective_max_devices', lambda: 0)
    yield db, con


def test_hourly_sync_never_connects_directly_to_an_adms_device(env, monkeypatch):
    db, con = env
    import fingerprint_sync
    m = fingerprint_sync.FingerprintSyncManager(db_path=db.DB_PATH)
    called = []
    monkeypatch.setattr(m, 'sync_device_attendance', lambda d: called.append(d['id']) or 0)
    m.sync_all_devices()
    assert called == [], 'جهاز ADMS بيبعت بصماته لوحده'


def test_cleanup_removes_every_finished_status(env):
    db, con = env
    from utils import adms_queue
    old = (datetime.now() - timedelta(days=9)).strftime('%Y-%m-%d %H:%M:%S')
    for st in ('OK', 'FAILED', 'FAILED (Code: -1)', 'FAILED (no reply)', 'SKIPPED', 'SENT', 'PENDING'):
        con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status, created_at)"
                    " VALUES (1, 'X', '{}', ?, ?)", (st, old))
    con.execute("INSERT INTO adms_commands (device_id, command_type, payload, status) VALUES (1, 'X', '{}', 'OK')")
    con.commit()
    assert adms_queue.cleanup(con, force=True) == 6
    left = sorted(r[0] for r in con.execute('SELECT status FROM adms_commands'))
    assert left == ['OK', 'PENDING'], 'المعلّق والجديد باقيين'
    assert adms_queue.cleanup(con) == 0, 'مرة كل ١٠ دقايق مش مع كل طلب'


def test_no_daily_set_time_for_a_device_that_asks_for_the_time_itself(env):
    db, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    c = adms_server.app.test_client()
    c.get('/iclock/rtdata?SN=SNQ&type=time')
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.post('/iclock/cdata?SN=SNQ&table=ATTLOG&Stamp=1', data=f'101\t{now}\t0\t1\t0\t0\n', content_type='text/plain')
    assert not con.execute("SELECT 1 FROM adms_commands WHERE command_type = 'SET TIME'").fetchone()


def _client(con):
    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}
    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [f for f in A.app.before_request_funcs.get(None, [])
                                        if f.__name__ not in ('check_license_globally', 'enforce_plan_features')]
    c = A.app.test_client()
    uid = con.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.update({'user_id': uid, 'role': 'admin', 'username': 'admin'})
    return c


def _edit_form(con, **over):
    e = dict(con.execute('SELECT * FROM employees WHERE id = 1').fetchone())
    form = {k: ('' if v is None else str(v)) for k, v in e.items() if k not in ('id',)}
    form.update({'is_active': '1'})
    form.update(over)
    return form


def test_saving_without_device_changes_sends_nothing(env, monkeypatch):
    db, con = env
    import utils.device_access as da
    pushed = []
    monkeypatch.setattr(da, 'push_employee_now', lambda num, templates='all': pushed.append((num, templates)))
    c = _client(con)
    r = c.post('/employees/edit/1', data=_edit_form(con, work_phone='99999999'))
    assert r.status_code in (200, 302)
    assert not con.execute("SELECT 1 FROM adms_commands WHERE device_id = 1 AND command_type LIKE 'DATA UPDATE%'").fetchone(), \
        'رقم تليفون اتغيّر بس — مفيش حاجة للجهاز'
    assert pushed == []
    c.post('/employees/edit/1', data=_edit_form(con, name='أحمد علي'))
    cmds = [r[0] for r in con.execute("SELECT command_type FROM adms_commands WHERE device_id = 1")]
    assert cmds == ['DATA UPDATE USERINFO'], f'الاسم اتغيّر: البيانات بس، بصماته عليه — {cmds}'
    assert pushed == [('101', 'missing')]


def test_biodata_when_db_is_busy_asks_the_device_to_resend(env, monkeypatch):
    """«database is locked» وسط دفعة بصمات: كان بيرد OK فالبصمات دي بتضيع."""
    db, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    import adms_server
    importlib.reload(adms_server)
    import utils.biometric_templates as bio

    def busy(*a, **k):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(bio, 'save', busy)
    c = adms_server.app.test_client()
    r = c.post('/iclock/cdata?SN=SNQ&table=BIODATA', data='BIODATA Pin=101\tNo=0\tType=9\tMajorVer=36\tTmp=AAAA\n',
               content_type='text/plain')
    assert r.get_data(as_text=True) != 'OK', 'الجهاز لازم يعيد الإرسال'


def test_launcher_uses_waitress_and_no_default_oracle_settings():
    src = open(os.path.join(ROOT, 'launcher.py'), encoding='utf-8').read()
    i = src.index('def start_adms_process')
    block = src[i:i + 4000]
    assert 'serve(adms_app' in block, 'سيرفر ADMS بـ waitress مش سيرفر التجارب'
    assert 'cfg.get("oracle_host", "localhost")' not in src, 'مايتحطش أوراكل افتراضي يخلّيه يحاول يتصل'


def test_pages_do_not_write_for_the_license_check_every_time(env):
    """كل صفحة كانت بتعمل INSERT في license_settings، فلو حاجة في الخلفية ماسكة القاعدة الصفحة
    بتستنى لحد ٣٠ ثانية — «البرنامج تقل جامد»."""
    db, con = env
    import utils.license as lic
    importlib.reload(lic)
    c1 = sqlite3.connect(db.DB_PATH)
    lic.init_license_table(c1)
    seen = []
    c2 = sqlite3.connect(db.DB_PATH)
    c2.set_trace_callback(seen.append)
    lic.init_license_table(c2)
    assert not [q for q in seen if q.strip().upper().startswith(('INSERT', 'UPDATE', 'ALTER', 'CREATE', 'BEGIN'))], seen


def test_device_poll_does_not_write_when_nothing_to_requeue(env):
    db, con = env
    import routes.adms_routes as R
    importlib.reload(R)
    seen = []
    c = sqlite3.connect(db.DB_PATH)
    c.set_trace_callback(seen.append)
    R._requeue_unanswered(c, 1)
    assert not [q for q in seen if q.strip().upper().startswith(('UPDATE', 'BEGIN'))], seen
