# -*- coding: utf-8 -*-
"""مزامنة أوراكل أول بأول — من أي مكان البصمة دخلت الحضور، وباتصالات بتتقفل، وطابور مابيقفش."""
import importlib
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
oracledb = pytest.importorskip('oracledb')


class Err:
    def __init__(self, code, message='x'):
        self.code, self.message = code, message


class FakeCursor:
    def __init__(self, db):
        self.db, self.result = db, None

    def execute(self, sql, params=()):
        s = ' '.join(sql.split()).upper()
        self.db.calls.append(s.split(' ')[0])
        key = (str(params[0]), params[1])
        if s.startswith('SELECT'):
            self.result = (1,) if key in self.db.rows else None
        elif s.startswith('INSERT'):
            if self.db.fail_insert_for.get(key):
                raise self.db.fail_insert_for[key]
            self.db.rows.add(key)
        elif s.startswith('DELETE'):
            self.db.rows.discard(key)

    def fetchone(self):
        return self.result


class FakeOra:
    def __init__(self):
        self.rows, self.calls, self.fail_insert_for, self.closed = set(), [], {}, False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.oracle_db as odb
    importlib.reload(odb)
    db.set_setting('oracle_enabled', '1')
    db.set_setting('oracle_host', '10.0.0.50')
    odb.refresh_state()
    con = sqlite3.connect(db.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active)"
                " VALUES (1, 'a', '365', 'D', 'P', '2025-01-01', 1, 1)")
    con.execute("INSERT INTO fingerprint_devices (id, device_name, device_ip, is_active, is_adms) VALUES (3, 'K40', '1.1.1.1', 1, 0)")
    con.commit()
    yield db, odb, con


def _q(con):
    return [(r['op'], r['user_id'], r['check_time'], r['status']) for r in
            con.execute('SELECT * FROM oracle_sync_queue ORDER BY id')]


def test_any_device_punch_is_queued_and_manual_is_not(env):
    db, odb, con = env
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-13 08:00:00', 0)")
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source)"
                " VALUES (1, 0, '2026-10-13 09:00:00', 0, 'manual')")
    con.commit()
    assert _q(con) == [('INSERT', '365', '2026-10-13 08:00:00', 'PENDING')], 'بصمة جهاز IP راحت الطابور، واليدوي لأ'


def test_time_fix_and_delete_reach_oracle_but_only_for_ours(env):
    db, odb, con = env
    con.execute("INSERT INTO attendance_records (id, employee_id, device_id, check_time, check_type) VALUES (9, 1, 3, '2026-10-13 09:00:00', 0)")
    con.commit()
    ora = FakeOra()
    assert odb.process_sync_queue(ora) == 1
    assert ora.rows == {('365', odb._parse_time('2026-10-13 09:00:00'))}
    con.execute("UPDATE attendance_records SET check_time = '2026-10-13 08:00:00' WHERE id = 9")   # تصحيح ساعة
    con.commit()
    odb.process_sync_queue(ora)
    assert ora.rows == {('365', odb._parse_time('2026-10-13 08:00:00'))}, 'القديمة اتمسحت والجديدة اتضافت'
    con.execute('DELETE FROM attendance_records WHERE id = 9')
    con.commit()
    odb.process_sync_queue(ora)
    assert ora.rows == set()
    # بصمة في أوراكل ماحطّيناهاش إحنا: مسحها عندنا مايمسحهاش هناك
    ora.rows.add(('365', odb._parse_time('2026-10-13 07:00:00')))
    con.execute("INSERT INTO oracle_sync_queue (user_id, check_time, check_type, op) VALUES ('365', '2026-10-13 07:00:00', '0', 'DELETE')")
    con.commit()
    odb.process_sync_queue(ora)
    assert ('365', odb._parse_time('2026-10-13 07:00:00')) in ora.rows
    assert _q(con)[-1][3] == 'SKIPPED'


def test_no_duplicates_in_oracle(env):
    db, odb, con = env
    ora = FakeOra()
    ora.rows.add(('365', odb._parse_time('2026-10-13 08:00:00')))
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-13 08:00:00', 0)")
    con.commit()
    odb.process_sync_queue(ora)
    assert ora.calls.count('INSERT') == 0 and _q(con)[0][3] == 'SYNCED'


def test_a_permanent_error_does_not_block_the_queue(env):
    db, odb, con = env
    ora = FakeOra()
    bad = ('365', odb._parse_time('2026-10-13 08:00:00'))
    ora.fail_insert_for[bad] = oracledb.DatabaseError(Err(904, 'invalid identifier'))
    for t in ('08:00:00', '08:01:00', '08:02:00'):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, ?, 0)",
                    (f'2026-10-13 {t}',))
    con.commit()
    assert odb.process_sync_queue(ora) == 2
    st = [r[3] for r in _q(con)]
    assert st == ['FAILED', 'SYNCED', 'SYNCED'], st
    err = con.execute("SELECT last_error FROM oracle_sync_queue WHERE status = 'FAILED'").fetchone()[0]
    assert 'ORA-904' in err


def test_a_network_error_retries_later_without_losing_anything(env):
    db, odb, con = env
    ora = FakeOra()
    first = ('365', odb._parse_time('2026-10-13 08:00:00'))
    ora.fail_insert_for[first] = oracledb.DatabaseError(Err(3113, 'end-of-file on communication channel'))
    for t in ('08:00:00', '08:01:00'):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, ?, 0)",
                    (f'2026-10-13 {t}',))
    con.commit()
    assert odb.process_sync_queue(ora) is False
    rows = con.execute('SELECT status, retry_count, next_try_at FROM oracle_sync_queue ORDER BY id').fetchall()
    assert rows[0]['status'] == 'PENDING' and rows[0]['retry_count'] == 1 and rows[0]['next_try_at']
    assert rows[1]['status'] == 'PENDING'
    del ora.fail_insert_for[first]
    assert odb.retry_failed() >= 1
    assert odb.process_sync_queue(ora) == 2


def test_disabled_oracle_queues_nothing(env):
    db, odb, con = env
    db.set_setting('oracle_enabled', '0')
    odb.refresh_state()
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-13 08:00:00', 0)")
    con.commit()
    assert _q(con) == []
    db.set_setting('oracle_enabled', '1')
    odb.refresh_state()
    assert odb.backfill() == 1, 'مزامنة الناقص بتلحقها'
    assert odb.backfill() == 0


def test_connections_are_closed(env, monkeypatch):
    db, odb, con = env
    made = []

    def connect(**kw):
        made.append(FakeOra())
        return made[-1]
    monkeypatch.setattr(oracledb, 'connect', connect)
    assert odb.check_oracle_connection() is True
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-13 08:00:00', 0)")
    con.commit()
    odb.process_sync_queue()
    assert made and all(m.closed for m in made), 'كل اتصال اتفتح اتقفل'


def test_oracle_pages(env):
    db, odb, con = env
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
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source)"
                " VALUES (1, 3, '2026-10-12 08:00:00', 0, 'manual')")
    con.execute("UPDATE attendance_records SET source = 'device'")      # قديمة قبل الـtrigger
    con.execute('DELETE FROM oracle_sync_queue')
    con.commit()
    assert c.get('/attendance/oracle/history').status_code == 200, 'كانت بتقع: عمود device_ip'
    j = c.post('/attendance/api/oracle/sync_missing').get_json()
    assert j['success'] and j['added_count'] == 1, j
    assert c.get('/attendance/api/oracle/stats').get_json()['stats']['pending'] == 1
    assert 'أول بأول' in c.get('/attendance/oracle').get_data(as_text=True)


def test_worker_catches_up_when_sync_turns_on(env, monkeypatch):
    db, odb, con = env
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, datetime('now', 'localtime', '-1 day'), 0)")
    con.execute('DELETE FROM oracle_sync_queue')          # زي بصمة IP قبل التحديث: ماراحتش
    con.commit()
    ora = FakeOra()
    monkeypatch.setattr(odb, 'get_oracle_connection', lambda: ora)
    monkeypatch.setattr(odb.time, 'sleep', lambda s: None)
    calls = {'n': 0}

    def stop(timeout=None):
        calls['n'] += 1
        if calls['n'] > 1:
            raise SystemExit
    monkeypatch.setattr(odb._wake, 'wait', stop)
    with pytest.raises(SystemExit):
        odb._worker_loop()
    assert len(ora.rows) == 1, 'اتبعتت في أول دورة'


def test_sync_from_date_and_resend_to_new_table_without_duplicates(env):
    """مزامنة من تاريخ لتاريخ، و«ابعت تاني» (جدول جديد) — ومفيش تكرار في أوراكل."""
    db, odb, con = env
    for d in ('2026-10-01 08:00:00', '2026-10-05T08:00:00', '2026-10-10 08:00:00', '2026-10-12 23:59:00'):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, ?, 0)", (d,))
    con.commit()
    ora = FakeOra()
    assert odb.process_sync_queue(ora) == 4
    assert odb.backfill('2026-10-01') == 0, 'العادي: اللي اتبعت مايتبعتش تاني'
    # جدول جديد فاضي: «ابعت تاني» من 5 لـ 10 (شامل اليومين، والوقت بـ T أو بمسافة)
    new = FakeOra()
    assert odb.backfill('2026-10-05', until='2026-10-10', resend=True) == 2
    assert odb.backfill('2026-10-05', until='2026-10-10', resend=True) == 0, 'اللي مستني في الطابور مايتحطش تاني'
    odb.process_sync_queue(new)
    assert new.rows == {('365', odb._parse_time('2026-10-05T08:00:00')), ('365', odb._parse_time('2026-10-10 08:00:00'))}
    # نفس الجدول القديم: «ابعت تاني» على اللي موجود هناك مابيكررش
    assert odb.backfill('2026-10-01', resend=True) == 4
    before = ora.calls.count('INSERT')
    odb.process_sync_queue(ora)
    assert ora.calls.count('INSERT') == before and len(ora.rows) == 4, 'كله موجود: اتعدّى من غير إضافة'
    with pytest.raises(ValueError):
        odb.backfill(resend=True)                       # من غير تاريخ بداية = كل التاريخ
    db.set_setting('oracle_dedupe', '0')
    with pytest.raises(ValueError):
        odb.backfill('2026-10-01', resend=True)         # منع التكرار مقفول: مايتبعتش تاني


def test_sync_range_endpoint(env):
    db, odb, con = env
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
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-01 08:00:00', 0)")
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type) VALUES (1, 3, '2026-10-09 08:00:00', 0)")
    con.commit()
    odb.process_sync_queue(FakeOra())
    url = '/attendance/api/oracle/sync_missing'
    j = c.post(url, json={'since': '2026-10-05', 'resend': 1}).get_json()
    assert j['success'] and j['added_count'] == 1 and 'من 2026-10-05' in j['message'], j
    assert not c.post(url, json={'resend': 1}).get_json()['success'], 'لازم تاريخ بداية'
    assert not c.post(url, json={'since': '2026-10-09', 'until': '2026-10-01'}).get_json()['success']
    assert not c.post(url, json={'since': 'امبارح'}).get_json()['success']
    page = c.get('/attendance/oracle').get_data(as_text=True)
    assert 'مزامنة من تاريخ' in page and 'sync_resend' in page


def test_portal_and_field_punches_go_to_oracle(env):
    """البوابة بالموبايل (GPS) ومشاوير ومحطات التطبيق الميداني بتروح أوراكل — واليدوي لأ."""
    db, odb, con = env
    for src, t in (('portal_mobile', '08:00:00'), ('field_trip', '09:00:00'), ('field_visit', '10:00:00'),
                   ('manual', '11:00:00')):
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type, source)"
                    " VALUES (1, 0, ?, 0, ?)", ('2026-10-14 ' + t, src))
    con.commit()
    assert [r[2][11:] for r in _q(con)] == ['08:00:00', '09:00:00', '10:00:00'], _q(con)
    # المشوار بيتعدّل وقته (أول دخول / آخر خروج): القديم يتمسح من أوراكل والجديد يتضاف
    ora = FakeOra()
    odb.process_sync_queue(ora)
    con.execute("UPDATE attendance_records SET check_time = '2026-10-14 08:30:00' WHERE source = 'field_trip'")
    con.commit()
    odb.process_sync_queue(ora)
    assert ('365', odb._parse_time('2026-10-14 09:00:00')) not in ora.rows
    assert ('365', odb._parse_time('2026-10-14 08:30:00')) in ora.rows
    assert odb.backfill('2026-10-14') == 0, 'مزامنة الناقص بتعرفهم برضه'


def test_old_triggers_are_replaced(env):
    db, odb, con = env
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_oracle%'")}
    assert names == {'trg_oracle_att_insert_v3', 'trg_oracle_att_update_v3', 'trg_oracle_att_delete_v3'}, names
