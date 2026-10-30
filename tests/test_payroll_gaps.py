# -*- coding: utf-8 -*-
"""اللي كان ناقص في الرواتب (multi-tenant): أوفرتايم بموافقة، بدل مسافة كبند، سلفة على الراتب،
فرق بأثر رجعي، تصفية نهائية، استيراد عمولات، ملف بنك، وسرعة الكشف.

يُشغَّل:  python -m pytest tests/test_payroll_gaps.py -v
"""
import json
from datetime import date, timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401
from tests.test_hr_requests import H  # noqa: F401
from tests.test_device_access import env  # noqa: F401


def _set(conn, k, v):
    conn.execute('INSERT OR REPLACE INTO salary_settings_v2 (setting_name, setting_value) VALUES (?, ?)', (k, v))
    conn.commit()


def _approve_ot(conn, eid, day, hours):
    conn.execute("CREATE TABLE IF NOT EXISTS hr_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id INTEGER, "
                 "type TEXT, payload TEXT, status TEXT DEFAULT 'pending', created_by INTEGER, created_at TEXT, "
                 "decided_at TEXT, result_ref TEXT)")
    conn.execute("INSERT INTO hr_requests (employee_id, type, payload, status) VALUES (?, 'overtime', ?, 'approved')",
                 (eid, json.dumps({'work_date': day, 'hours': hours})))
    conn.commit()


# ------------------------------------------------------------ ٢ أوفرتايم بموافقة

def test_overtime_needs_an_approved_request_when_switched_on(web):
    c, conn, A, pe = web
    start, _ = pe.resolve_period(conn, MONTH, YEAR)
    sun = start + timedelta(days=(7 - start.isoweekday() % 7) % 7)
    mon = sun + timedelta(days=1)
    _punch(conn, 1, sun.isoformat(), '08:00', '19:00')      # 3 ساعات بعد الشفت
    _punch(conn, 1, mon.isoformat(), '08:00', '18:00')      # ساعتين
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['ot_weekday_mins'] == 300, 'الافتراضي زي ما هو'
    _set(conn, 'overtime_requires_approval', '1')
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['ot_weekday_mins'] == 0, 'من غير طلب = صفر'
    _approve_ot(conn, 1, sun.isoformat(), 1.5)
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['ot_weekday_mins'] == 90, 'لحد المعتمد'
    days = {d['date']: d for d in pe.compute_employee_days(conn, 1, MONTH, YEAR)['days']}
    assert days[sun.isoformat()]['ot_mins'] == 90 and days[mon.isoformat()]['ot_mins'] == 0, 'كشف الأيام نفس الحساب'
    _approve_ot(conn, 1, mon.isoformat(), 5)
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['ot_weekday_mins'] == 210, 'المعتمد أكتر من الفعلي = الفعلي'
    assert 'overtime_requires_approval' in c.get('/settings').get_data(as_text=True)


# ------------------------------------------------------------ ٦ و٧ بدل مسافة وسلفة على الراتب

def test_salary_advance_is_capped_and_deducted_once_this_month(H):
    H, con, u = H
    con.execute('UPDATE employees SET salary = 400 WHERE id = 1')
    con.commit()
    with pytest.raises(ValueError, match='أقصى'):
        H.create(con, 1, 'salary_advance', {'amount': '250'})          # 50% = 200
    rid = H.create(con, 1, 'salary_advance', {'amount': '150', 'reason': 'ظرف'})
    with pytest.raises(ValueError, match='قبل كده'):
        H.create(con, 1, 'salary_advance', {'amount': '10'})
    assert H.decide(con, rid, u['mgr'], 'M', True)[0] and H.decide(con, rid, u['hr'], 'H', True)[0]
    tx = con.execute("SELECT t.amount, it.code, it.kind, t.month, t.year FROM payroll_transactions t "
                     "JOIN payroll_item_types it ON it.id = t.item_type_id WHERE t.reference = ?", (f'REQ-{rid}',)).fetchone()
    assert (tx['amount'], tx['code'], tx['kind']) == (150, 'salary_advance', 'deduction')
    assert (tx['month'], tx['year']) == (date.today().month, date.today().year)


def test_mileage_lands_as_its_own_item(H, monkeypatch):
    H, con, u = H
    monkeypatch.setattr(H, 'mileage', lambda conn, e, m: {'mileage_month': m, 'km': 50.0, 'trips': 2, 'rate': 0.1,
                                                          'amount': 5.0, 'description': 'بدل', 'expense_date': m + '-01'})
    rid = H.create(con, 1, 'expense', {'mileage_month': '2026-09'})
    H.decide(con, rid, u['mgr'], 'M', True)
    H.decide(con, rid, u['hr'], 'H', True)
    code = con.execute("SELECT it.code FROM payroll_transactions t JOIN payroll_item_types it ON it.id = t.item_type_id "
                       "WHERE t.reference = ?", (f'REQ-{rid}',)).fetchone()[0]
    assert code == 'mileage'


# ------------------------------------------------------------ ٣ فرق بأثر رجعي

def _approved_run(conn, m, y, ps, pe, basics):
    rid = conn.execute("INSERT INTO payroll_runs (month, year, status, period_start, period_end) VALUES (?, ?, 'approved', ?, ?)",
                       (m, y, ps, pe)).lastrowid
    for eid, b in basics.items():
        conn.execute('INSERT INTO payroll_run_lines (run_id, employee_id, basic, net) VALUES (?, ?, ?, ?)', (rid, eid, b, b))
    conn.commit()


def test_a_back_dated_raise_pays_the_difference_once_in_the_open_month(web):
    c, conn, A, pe = web
    from utils import pay_history as ph
    _approved_run(conn, 6, 2026, '2026-06-01', '2026-06-30', {1: 260})
    _approved_run(conn, 7, 2026, '2026-07-01', '2026-07-31', {1: 260})
    ph.record_salary_change(conn, 1, 300, '2026-06-16', previous=260)       # نص يونيو + يوليو كله
    conn.execute('UPDATE employees SET salary = 300 WHERE id = 1')
    r = ph.retro_arrears(conn, 1)
    assert [(m, y) for m, y, _ in r['items']] == [(6, 2026), (7, 2026)]
    assert r['items'][0][2] == pytest.approx(20.0, abs=0.01) and r['items'][1][2] == pytest.approx(40.0)
    again = ph.retro_arrears(conn, 1)                                        # إعادة الحساب مابتكررش
    n = conn.execute("SELECT COUNT(*), SUM(amount) FROM payroll_transactions WHERE employee_id = 1 AND status = 'active' "
                     "AND reference LIKE 'RETRO-%'").fetchone()
    assert n[0] == 2 and n[1] == pytest.approx(60.0, abs=0.01) and len(again['items']) == 2


def test_the_form_accepts_a_locked_date_only_with_retro(web):
    c, conn, A, pe = web
    m, y = (date.today().month - 2) % 12 + 1, date.today().year - (1 if date.today().month <= 1 else 0)
    ps, pe_ = pe.resolve_period(conn, m, y)
    _approved_run(conn, m, y, ps.isoformat(), pe_.isoformat(), {1: 260})
    r = c.post('/employees/1/salary_history', data={'effective_from': ps.isoformat(), 'salary': '286'})
    assert conn.execute('SELECT COUNT(*) FROM employee_salary_history WHERE employee_id = 1 AND salary = 286').fetchone()[0] == 0
    c.post('/employees/1/salary_history', data={'effective_from': ps.isoformat(), 'salary': '286', 'retro': '1'})
    amt = conn.execute("SELECT amount FROM payroll_transactions WHERE employee_id = 1 AND reference LIKE 'RETRO-%' "
                       "AND status = 'active'").fetchone()
    assert amt and amt[0] == pytest.approx(26.0)


# ------------------------------------------------------------ ٤ التصفية النهائية

def test_final_settlement_adds_last_salary_eos_and_leave_without_double_loans(web):
    c, conn, A, pe = web
    from utils import final_settlement as fsm
    conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month, start_year, status) "
                 "VALUES (1, 100, 20, ?, ?, 'active')", (MONTH, YEAR))
    conn.commit()
    ps, pe_ = pe.resolve_period(conn, MONTH, YEAR)
    term = (ps + timedelta(days=14)).isoformat()
    for i in range(15):
        _punch(conn, 1, (ps + timedelta(days=i)).isoformat(), '08:00', '16:00')
    before = conn.execute('SELECT end_of_service_date FROM employees WHERE id = 1').fetchone()[0]
    fs = fsm.settle(conn, 1, term, 'termination')
    assert conn.execute('SELECT end_of_service_date FROM employees WHERE id = 1').fetchone()[0] == before, 'معاينة بس'
    last = fs['last_salary']
    assert not last['already_paid'] and last['row'] is not None
    assert any(d['code'] == 'post_service_days' for d in last['row']['deductions']), 'الأيام بعد الإنهاء بتتخصم'
    assert last['loan_installments'] == pytest.approx(20.0)
    assert fs['loans_after_last_salary'] == pytest.approx(80.0), 'القسط مايتخصمش مرتين'
    assert fs['total'] == pytest.approx(last['net'] + fs['eos_net'])
    assert fs['eos_net'] == pytest.approx(fs['eos']['gratuity'] + fs['eos']['leave_amount'] + fs['eos']['notice_amount'] - 80.0, abs=0.002)
    r = c.get(f'/eos/final-settlement?employee_id=1&termination_date={term}&reason=termination')
    assert r.status_code == 200 and 'التصفية النهائية' in r.get_data(as_text=True)


def test_final_settlement_after_a_closed_month_does_not_pay_the_salary_again(web):
    c, conn, A, pe = web
    from utils import final_settlement as fsm
    ps, pe_ = pe.resolve_period(conn, MONTH, YEAR)
    conn.execute("INSERT INTO payroll_runs (month, year, status, period_start, period_end) VALUES (?, ?, 'approved', ?, ?)",
                 (MONTH, YEAR, ps.isoformat(), pe_.isoformat()))
    conn.commit()
    fs = fsm.settle(conn, 1, (ps + timedelta(days=10)).isoformat(), 'termination')
    assert fs['last_salary']['already_paid'] and fs['total'] == pytest.approx(fs['eos_net'])
