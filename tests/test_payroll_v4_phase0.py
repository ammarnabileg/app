# -*- coding: utf-8 -*-
"""محرّك V4 — المرحلة ٠ (اللقطة والتجهيل) وأساس المرحلة ١ (حقائق اليوم) وطبقة الشركة."""
import importlib
import os
import shutil
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _seed(db_path):
    con = sqlite3.connect(db_path)
    con.execute("INSERT INTO employees (id, name, arabic_name, employee_number, department, position, hire_date, salary,"
                " is_active, weekly_leave_selected_days, default_start_time, default_end_time, national_id, phone,"
                " bank_iban) VALUES (1, 'Ahmed Ali', 'أحمد علي', '101', 'D', 'P', '2025-01-01', 520, 1, '5,6',"
                " '08:00', '16:00', '290010100000', '99990000', 'KW81CBKU0000000000001234560101')")
    con.execute("INSERT INTO employees (id, name, employee_number, department, position, hire_date, salary, is_active,"
                " weekly_leave_selected_days, default_start_time, default_end_time)"
                " VALUES (2, 'Mona Hassan', '102', 'D', 'P', '2026-09-15', 390, 1, '5,6', '08:00', '16:00')")
    days = ['2026-09-%02d' % d for d in (1, 2, 3, 6, 7, 8, 9, 10, 13, 14, 15, 16, 17, 20, 21, 22, 23, 24, 27, 28)]
    for d in days:
        if d == '2026-09-02':
            con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                        " VALUES (1, 1, ?, 0)", (d + ' 08:00:00',))      # بصمة ناقصة
            continue
        if d == '2026-09-03':
            continue                                                      # غياب
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                    " VALUES (1, 1, ?, 0)", (d + (' 08:25:00' if d == '2026-09-06' else ' 08:00:00'),))
        con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                    " VALUES (1, 1, ?, 1)", (d + (' 18:00:00' if d == '2026-09-07' else ' 16:00:00'),))
        if d >= '2026-09-15':
            con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                        " VALUES (2, 1, ?, 0)", (d + ' 08:00:00',))
            con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                        " VALUES (2, 1, ?, 1)", (d + ' 16:00:00',))
    con.execute("INSERT INTO attendance_records (employee_id, device_id, check_time, check_type)"
                " VALUES (1, 1, '2026-09-04 09:00:00', 0), (1, 1, '2026-09-04 13:00:00', 1)")   # شغل جمعة
    con.commit()
    con.close()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path / 'live'))
    os.makedirs(tmp_path / 'live')
    import utils.db as dbm
    importlib.reload(dbm)
    dbm.init_db()
    _seed(dbm.DB_PATH)
    return dbm.DB_PATH, tmp_path


# ------------------------------------------------------------ المرحلة ٠

def test_snapshot_save_check_and_detects_a_change(db):
    path, tmp = db
    from tools.payroll_snapshot import diff, snapshot
    snap = snapshot(path, ['2026-09'])
    e = snap['2026-09']['employees']
    assert set(e) == {'101', '102'} and e['101']['net'] > 0 and e['102']['basic'] < e['101']['basic']
    assert e['101']['days']['2026-09-03']['kind'] == 'absent' and e['101']['days']['2026-09-04']['kind'] == 'rest'
    assert 'Ahmed' not in str(snap), 'اللقطة من غير أسماء'
    assert diff(snap, snapshot(path, ['2026-09'])) == [], 'ثابتة'
    con = sqlite3.connect(path)
    con.execute("DELETE FROM attendance_records WHERE employee_id = 1 AND check_time LIKE '2026-09-10%'")
    con.commit()
    con.close()
    d = diff(snap, snapshot(path, ['2026-09']))
    assert any('/101/' in x for x in d) and not any('/102/' in x for x in d)


def test_anonymized_copy_gives_the_same_payroll(db):
    path, tmp = db
    from tools.anonymize_db import anonymize
    from tools.payroll_snapshot import diff, snapshot
    before = open(path, 'rb').read()
    anon = str(tmp / 'anon' / 'anon.db')
    anonymize(path, anon)
    assert open(path, 'rb').read() == before, 'الأصل ما اتلمسش'
    con = sqlite3.connect(anon)
    row = con.execute("SELECT name, arabic_name, national_id, phone, bank_iban, salary, employee_number"
                      " FROM employees WHERE id = 1").fetchone()
    assert row[:5] == ('موظف 1', 'موظف 1', None, None, None) and row[5] == 520 and row[6] == '101'
    assert con.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0] > 0
    con.close()
    assert diff(snapshot(path, ['2026-09']), snapshot(anon, ['2026-09'])) == [], 'التجهيل ما يغيّرش الحساب'
    with pytest.raises(ValueError):
        anonymize(path, path)


# ------------------------------------------------------------ المرحلة ١: حقائق اليوم

def test_day_facts_add_up_to_the_engines_month_and_flag_changes(db):
    path, tmp = db
    import utils.payroll_engine as pe
    importlib.reload(pe)
    from utils.payroll_v4 import facts
    importlib.reload(facts)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r = facts.build(con, 9, 2026)
    assert r['written'] > 0 and r['changed'] == []
    metrics = pe.compute_month_metrics(con, 9, 2026)
    start, end = pe.resolve_period(con, 9, 2026)
    for emp_id, m in metrics.items():
        tot = facts.month_totals(con, emp_id, start.isoformat(), end.isoformat())
        for k in pe.METRIC_FIELDS:
            if k in ('unentitled_rest_days',):
                continue                  # بيتحسب على مستوى الأسبوع بعد الأيام
            assert tot.get(k, 0) == pytest.approx(m.get(k, 0), abs=0.01), (emp_id, k)
    f = {x['day']: x for x in facts.facts_for(con, 1, '2026-09-01', '2026-09-30')}
    assert f['2026-09-03']['kind'] == 'absent' and f['2026-09-04']['kind'] == 'rest'
    assert f['2026-09-02']['partial_days'] == 1 and f['2026-09-06']['late_mins'] > 0
    assert facts.build(con, 9, 2026)['changed'] == [], 'نفس البيانات = مفيش تغيير'
    con.execute("DELETE FROM attendance_records WHERE employee_id = 1 AND check_time LIKE '2026-09-10%'")
    con.commit()
    ch = facts.build(con, 9, 2026)['changed']
    assert ch == [(1, '2026-09-10', 'work', 'absent')], 'ده مدخل الفروقات الرجعية'


# ------------------------------------------------------------ طبقة الشركة

def test_company_rules_default_to_the_current_engine(db):
    path, tmp = db
    from utils.payroll_v4 import rules
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    r = rules.load(con)
    assert r.matches_current_engine and r.retro_requires_approval and r.retro_back_months == 3
    assert r.day_basis in ('26', '30', 'working')
    for k, v in (('ot_requires_approval', '1'), ('ot_grace_minutes', '30'), ('daily_rate_basis', '30')):
        con.execute('DELETE FROM salary_settings_v2 WHERE setting_name = ?', (k,))
        con.execute('INSERT INTO salary_settings_v2 (setting_name, setting_value) VALUES (?, ?)', (k, v))
    con.commit()
    r = rules.load(con)
    assert r.ot_requires_approval and r.ot_grace_minutes == 30 and not r.matches_current_engine
    assert r.daily_divisor() == 30.0


# ------------------------------------------------------------ لقطات حقيقيّة (خارج git)
# tests/fixtures/payroll_private/<اسم>.db + <اسم>.json (من tools/payroll_snapshot.py --save):
# أيّ تعديل يغيّر رقمًا واحدًا في مسير عميلٍ حقيقيّ يُسقط الاختبار — قبل ما يوصل له.
import glob  # noqa: E402
import json  # noqa: E402

PRIVATE = sorted(glob.glob(os.path.join(ROOT, 'tests', 'fixtures', 'payroll_private', '*.json')))


@pytest.mark.parametrize('golden', PRIVATE, ids=[os.path.basename(p) for p in PRIVATE])
def test_private_payroll_snapshots(golden, tmp_path, monkeypatch):
    from tools.payroll_snapshot import diff, snapshot
    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    want = json.load(open(golden, encoding='utf-8'))
    got = snapshot(golden[:-5] + '.db', sorted(want))
    assert diff(want, got) == []
