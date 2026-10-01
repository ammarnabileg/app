# -*- coding: utf-8 -*-
"""أوامرُ بوّابة الشركة وسجلُّ المزامنة (٢.٢٩).

- صاحبُ الشركة لا يصل الجهاز: يعتمد الكشفَ من onz.one/portal، والبرنامجُ
  يُنفّذ — لكن لِما رآه بالضبط (التوقيع)، ومرّةً واحدة.
- المسودّةُ لا تخرج إلى خادمٍ قديم يعرضها معتمَدة.
- كلُّ مزامنةٍ للأجهزة تُسجَّل (تلقائيّة/يدويّة/من البوّابة) ويُرفع السجلّ.
"""
import importlib
import os
import sqlite3
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.device_autosync as das
    importlib.reload(das)
    import utils.remote_commands as rc
    importlib.reload(rc)
    conn = sqlite3.connect(db.DB_PATH)
    conn.row_factory = sqlite3.Row
    yield db, das, rc, conn
    conn.close()


def _run(conn, status='saved'):
    conn.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active)"
                 " VALUES (1, 'أحمد', '101', 'D', 'P', '2026-01-01', 500, 1)")
    conn.execute("INSERT INTO payroll_runs (id, month, year, status, employees_count, total_basic, total_net,"
                 " period_start, period_end, created_at) VALUES (7, 9, 2026, ?, 1, 500, 480,"
                 " '2026-09-01', '2026-09-30', '2026-09-30 10:00:00')", (status,))
    conn.execute("INSERT INTO payroll_run_lines (run_id, employee_id, basic, total_deductions, net, details_json)"
                 " VALUES (7, 1, 500, 20, 480, '{}')")
    conn.commit()
    return conn.execute('SELECT * FROM payroll_runs WHERE id = 7').fetchone()


# ------------------------------------------------------------ الاعتماد

def test_approval_applies_only_to_the_version_the_owner_saw(env):
    db, das, rc, conn = env
    run = _run(conn)
    ok, msg = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 7,
                                        'signature': '7|2026-09-30 09:59:00|1', 'by': 'هاني'})
    assert not ok and 'اتعدّل' in msg
    assert conn.execute('SELECT status FROM payroll_runs WHERE id = 7').fetchone()[0] == 'saved'

    ok, msg = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 7,
                                        'signature': rc.run_signature(run), 'by': 'هاني'})
    assert ok, msg
    assert conn.execute('SELECT status FROM payroll_runs WHERE id = 7').fetchone()[0] == 'approved'
    note = conn.execute('SELECT note FROM payroll_ledger_postings WHERE run_id = 7').fetchone()[0]
    assert 'بوّابة الشركة' in note and 'هاني' in note

    ok, msg = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 7,
                                        'signature': rc.run_signature(run)})
    assert ok and 'بالفعل' in msg
    assert conn.execute('SELECT COUNT(*) FROM payroll_ledger_postings').fetchone()[0] == 1


def test_approval_can_be_turned_off_at_the_office(env):
    db, das, rc, conn = env
    run = _run(conn)
    db.set_setting(rc.SETTING_REMOTE_APPROVAL, '0')
    ok, msg = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 7,
                                        'signature': rc.run_signature(run)})
    assert not ok and 'مقفول' in msg
    assert conn.execute('SELECT status FROM payroll_runs WHERE id = 7').fetchone()[0] == 'saved'


def test_wrong_month_or_missing_run_is_refused(env):
    db, das, rc, conn = env
    run = _run(conn)
    ok, _ = rc.approve_payroll(conn, {'month': 8, 'year': 2026, 'run_id': 7,
                                      'signature': rc.run_signature(run)})
    assert not ok
    ok, _ = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 99, 'signature': 'x'})
    assert not ok


# ------------------------------------------------------------ المسودّات

def test_drafts_leave_only_after_the_server_says_it_shows_them_as_drafts(env):
    db, das, rc, conn = env
    from utils import cloud_outbox as ob
    _run(conn)
    assert ob.read_rows(conn, 'payroll_runs', [7]) == [], 'مسودّةٌ إلى خادمٍ قديم تظهر معتمَدة'
    assert ob.baseline_batch(conn, 'payroll_runs') == []

    conn.execute('DELETE FROM cloud_sync_outbox')
    conn.commit()
    assert ob.enable_drafts(conn) is True
    assert ob.enable_drafts(conn) is False, 'مرّةً واحدة'
    queued = {(r[0], r[1]) for r in conn.execute('SELECT table_name, row_id FROM cloud_sync_outbox')}
    assert ('payroll_runs', 7) in queued
    assert any(t == 'payroll_run_lines' for t, _ in queued)
    rows = ob.read_rows(conn, 'payroll_runs', [7])
    assert rows and rows[0]['status'] == 'saved'


def test_approving_a_draft_still_resends_its_lines(env):
    db, das, rc, conn = env
    from utils import cloud_outbox as ob
    run = _run(conn)
    ob.enable_drafts(conn)
    conn.execute('DELETE FROM cloud_sync_outbox')
    conn.commit()
    ok, _ = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': 7,
                                      'signature': rc.run_signature(run)})
    assert ok
    queued = {r[0] for r in conn.execute('SELECT table_name FROM cloud_sync_outbox')}
    assert {'payroll_runs', 'payroll_run_lines'} <= queued


# ------------------------------------------------------------ القناة

class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


class _Session:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({'url': url, 'json': json})
        return self.replies.pop(0)


def _configure(db):
    from utils import cloud_sync as cs
    db.set_setting(cs.SETTING_ENABLED, '1')
    db.set_setting(cs.SETTING_URL, 'https://onz.one/api/sync')
    db.set_setting(cs.SETTING_CLIENT, 'c1')
    db.set_setting(cs.SETTING_KEY, 'k')


def test_old_server_without_the_channel_is_asked_hourly_not_every_cycle(env):
    db, das, rc, conn = env
    _configure(db)
    s = _Session([_Resp(404)])
    assert rc.poll(conn, s) == {'skipped': 'unsupported'}
    assert s.calls[0]['url'] == 'https://onz.one/api/sync/commands'
    assert rc.poll(conn, s) == {'skipped': 'unsupported'}
    assert len(s.calls) == 1


def test_a_command_runs_once_and_its_result_goes_back(env):
    db, das, rc, conn = env
    _configure(db)
    run = _run(conn)
    cmd = {'id': 'a1', 'type': 'approve_payroll',
           'payload': {'month': 9, 'year': 2026, 'run_id': 7, 'signature': rc.run_signature(run), 'by': 'هاني'}}
    s = _Session([_Resp(200, {'status': 'success', 'features': ['payroll_drafts'], 'commands': [cmd]}),
                  _Resp(200, {'status': 'success', 'commands': [cmd]}),
                  _Resp(200, {'status': 'success', 'commands': []})])
    res = rc.poll(conn, s)
    assert res['ok'] and res['received'] == 1
    from utils import cloud_outbox as ob
    assert ob.get_state(conn, ob.DRAFTS_STATE) == '1'
    assert conn.execute('SELECT status FROM payroll_runs WHERE id = 7').fetchone()[0] == 'approved'

    # الخادم أعاد الأمرَ (لم تصله النتيجة بعد): لا يُنفَّذ ثانيةً، والنتيجةُ تُرسل.
    rc.poll(conn, s)
    sent = s.calls[1]['json']['results']
    assert [r['id'] for r in sent] == ['a1'] and sent[0]['ok'] is True
    assert conn.execute('SELECT COUNT(*) FROM payroll_ledger_postings').fetchone()[0] == 1
    rc.poll(conn, s)
    assert s.calls[2]['json']['results'] == [], 'أُبلغت مرّة'


def test_device_sync_from_the_portal_is_logged_with_its_source(env, monkeypatch):
    db, das, rc, conn = env
    import fingerprint_sync
    monkeypatch.setattr(fingerprint_sync, 'sync_all_fingerprint_devices',
                        lambda: {'success': True, 'attendance_synced': 5, 'successful_devices': 1, 'total_devices': 1})
    monkeypatch.setattr(fingerprint_sync, 'sync_users_to_employees', lambda: {'employees_added': 2})
    rc.execute(conn, {'id': 'd1', 'type': 'device_sync', 'payload': {}})
    for _ in range(50):
        r = conn.execute("SELECT done_at, ok, message FROM remote_commands WHERE command_id = 'd1'").fetchone()
        if r['done_at']:
            break
        time.sleep(0.05)
    assert r['ok'] == 1 and '5 جديدة' in r['message']
    log = das.history()
    assert log[0]['source'] == 'portal' and log[0]['punches_new'] == 5 and log[0]['users_added'] == 2
    assert log[0]['source_label'] == 'من بوّابة الشركة'


def test_unknown_command_says_update_the_program(env):
    db, das, rc, conn = env
    rc.execute(conn, {'id': 'x1', 'type': 'wipe_everything', 'payload': {}})
    r = conn.execute("SELECT ok, message FROM remote_commands WHERE command_id = 'x1'").fetchone()
    assert r['ok'] == 0 and 'حدّث' in r['message']


# ------------------------------------------------------------ السجلّ

def test_manual_and_auto_runs_are_logged_and_uploaded(env, monkeypatch):
    db, das, rc, conn = env
    import fingerprint_sync
    from utils import cloud_outbox as ob
    monkeypatch.setattr(fingerprint_sync, 'sync_all_fingerprint_devices',
                        lambda: {'success': False, 'error': 'الجهاز لا يردّ', 'attendance_synced': 0,
                                 'successful_devices': 0, 'total_devices': 1})
    monkeypatch.setattr(fingerprint_sync, 'sync_users_to_employees', lambda: {'employees_added': 0})
    das.run_once()
    das.log_run('manual', '2026-10-01 10:00:00', das.SKIP, {'employees_added': 3})
    h = das.history()
    assert h[0]['source'] == 'manual' and h[0]['users_added'] == 3 and 'البصمات' not in h[0]['message']
    assert h[1]['source'] == 'auto' and h[1]['ok'] == 0 and 'لا يردّ' in h[1]['message']
    assert 'device_sync_log' in ob.SYNC_TABLES and 'device_sync_log' in ob.LATE_TABLES
    queued = {r[0] for r in conn.execute('SELECT table_name FROM cloud_sync_outbox')}
    assert 'device_sync_log' in queued


def test_the_log_keeps_only_the_latest_runs(env, monkeypatch):
    db, das, rc, conn = env
    monkeypatch.setattr(das, 'LOG_KEEP', 5)
    for i in range(9):
        das.log_run('auto', f'2026-10-01 0{i}:00:00', {'attendance_synced': i}, {'employees_added': 0})
    assert conn.execute('SELECT COUNT(*) FROM device_sync_log').fetchone()[0] == 5
    assert das.history(1)[0]['punches_new'] == 8


def test_pages_have_the_sync_now_button_history_and_the_approval_switch():
    html = open(os.path.join(ROOT, 'templates', 'fingerprint_dashboard.html'), encoding='utf-8').read()
    assert "url_for('attendance.run_device_autosync_now')" in html and 'id="deviceSyncHistory"' in html
    st = open(os.path.join(ROOT, 'templates', 'settings.html'), encoding='utf-8').read()
    assert 'name="remote_payroll_approval"' in st and "'remote_payroll_approval']" in st
    src = open(os.path.join(ROOT, 'utils', 'cloud_sync.py'), encoding='utf-8').read()
    assert 'remote_commands.poll()' in src


# ------------------------------------------------------------ تجهيزُ الكشف (العميل لا يصل الجهاز أصلًا)

def _employees(conn):
    for i, n in ((1, '101'), (2, '102')):
        conn.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active)"
                     " VALUES (?, ?, ?, 'D', 'P', '2025-01-01', 500, 1)", (i, 'م' + n, n))
    conn.commit()


def test_prepare_attests_hours_and_saves_a_draft(env):
    db, das, rc, conn = env
    _employees(conn)
    ok, msg = rc.prepare_payroll(conn, {'month': 9, 'year': 2026, 'by': 'هاني'})
    assert ok, msg
    run = conn.execute('SELECT * FROM payroll_runs WHERE month = 9 AND year = 2026').fetchone()
    assert run['status'] == 'saved', 'مسودّةٌ تُراجَع وتُعتمد — لا اعتمادَ بلا مراجعة'
    assert run['employees_count'] == 2 and 'مسودّة' in msg
    notes = [r[0] for r in conn.execute('SELECT notes FROM payroll_hours_approvals WHERE month = 9 AND year = 2026')]
    assert len(notes) == 2 and all('بوّابة الشركة' in n and 'هاني' in n for n in notes)


def test_prepare_keeps_hours_already_attested_at_the_office(env):
    db, das, rc, conn = env
    _employees(conn)
    from utils.payroll_engine import attest_hours
    attest_hours(conn, 9, 2026, [{'employee_id': 1, 'notes': 'راجعها المحاسب'}], 5)
    conn.commit()
    ok, msg = rc.prepare_payroll(conn, {'month': 9, 'year': 2026})
    assert ok, msg
    r = conn.execute('SELECT notes, approved_by FROM payroll_hours_approvals WHERE employee_id = 1 AND month = 9').fetchone()
    assert r['notes'] == 'راجعها المحاسب' and r['approved_by'] == 5
    assert '1 موظّف ساعاتهم كانت متثبّتة' in msg


def test_prepare_again_refreshes_the_draft_so_an_old_approval_is_refused(env):
    db, das, rc, conn = env
    _employees(conn)
    rc.prepare_payroll(conn, {'month': 9, 'year': 2026})
    first = conn.execute('SELECT * FROM payroll_runs WHERE month = 9').fetchone()
    time.sleep(1.1)
    rc.prepare_payroll(conn, {'month': 9, 'year': 2026})
    second = conn.execute('SELECT * FROM payroll_runs WHERE month = 9').fetchone()
    assert second['id'] == first['id'] and rc.run_signature(second) != rc.run_signature(first)
    ok, _ = rc.approve_payroll(conn, {'month': 9, 'year': 2026, 'run_id': first['id'],
                                      'signature': rc.run_signature(first)})
    assert not ok, 'اعتمادُ ما عُرض قبل التحديث'


def test_prepare_refuses_an_approved_month_and_when_switched_off(env):
    db, das, rc, conn = env
    _employees(conn)
    rc.prepare_payroll(conn, {'month': 9, 'year': 2026})
    conn.execute("UPDATE payroll_runs SET status = 'approved'")
    conn.commit()
    ok, msg = rc.prepare_payroll(conn, {'month': 9, 'year': 2026})
    assert not ok and 'معتمد' in msg
    db.set_setting(rc.SETTING_REMOTE_APPROVAL, '0')
    ok, msg = rc.prepare_payroll(conn, {'month': 10, 'year': 2026})
    assert not ok and 'مقفول' in msg
    assert not rc.prepare_payroll(conn, {'month': 13, 'year': 2026})[0]


def test_prepare_command_syncs_devices_first_then_reports(env, monkeypatch):
    db, das, rc, conn = env
    _employees(conn)
    order = []
    monkeypatch.setattr(das, '_has_active_devices', lambda: True)
    monkeypatch.setattr(das, 'run_once', lambda source='auto': order.append(('sync', source)))
    real = rc.prepare_payroll
    monkeypatch.setattr(rc, 'prepare_payroll', lambda c, p: (order.append(('prepare',)), real(c, p))[1])
    rc.execute(conn, {'id': 'p1', 'type': 'prepare_payroll', 'payload': {'month': 9, 'year': 2026}})
    for _ in range(100):
        r = conn.execute("SELECT done_at, ok, message FROM remote_commands WHERE command_id = 'p1'").fetchone()
        if r['done_at']:
            break
        time.sleep(0.05)
    assert order[0] == ('sync', 'portal') and order[1] == ('prepare',)
    assert r['ok'] == 1 and 'اتجهّز' in r['message']
