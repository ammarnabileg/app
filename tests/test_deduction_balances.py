# -*- coding: utf-8 -*-
"""الخصومات اللي ماتنفّذتش بتتسجل أرصدة عند اعتماد الكشف (utils/deduction_balances)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401


def _tx(conn, eid, code, amount):
    it = conn.execute('SELECT id FROM payroll_item_types WHERE code = ?', (code,)).fetchone()[0]
    conn.execute('INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount) VALUES (?, ?, ?, ?, ?)',
                 (eid, it, MONTH, YEAR, amount))
    conn.commit()


def _approve(conn, pe):
    for eid in (1, 2):
        pass
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}, {'employee_id': 2}], 1)
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    return pe.lock_run(conn, MONTH, YEAR, 1)


def _work(conn, pe):
    ps, pe_ = pe.resolve_period(conn, MONTH, YEAR)
    d = ps
    while d <= pe_:
        if d.isoweekday() not in (5, 6):
            for eid in (1, 2):
                _punch(conn, eid, d.isoformat(), '08:00', '16:00')
        d += timedelta(days=1)


def test_the_uncovered_part_becomes_a_balance_on_approval_only(web):
    c, conn, A, pe = web
    from utils import deduction_balances as DB
    _work(conn, pe)
    _tx(conn, 1, 'overpayment_recovery', 300)                 # الراتب 260 مايكفيش
    row = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    u = row['unexecuted']
    assert u and u[0]['code'] == 'overpayment_recovery' and u[0]['remaining'] == pytest.approx(300 - u[0]['deducted'])
    DB.ensure_schema(conn)
    assert not DB.listing(conn), 'المعاينة مابتسجلش'
    run = _approve(conn, pe)
    b = DB.listing(conn)
    assert len(b) == 1 and b[0]['status'] == 'pending_review' and b[0]['original'] == 300
    assert b[0]['remaining'] == pytest.approx(u[0]['remaining'])
    DB.record_for_run(conn, run)
    assert len(DB.listing(conn)) == 1, 'مايتسجلش مرتين'
    # فتح الشهر: عكس واضح، واعتماد تاني = رصيد جديد واحد بس
    pe.unlock_run(conn, MONTH, YEAR, 1, 'تصحيح')
    assert [x['status'] for x in DB.listing(conn)] == ['reversed']
    pe.lock_run(conn, MONTH, YEAR, 1)
    st = sorted(x['status'] for x in DB.listing(conn))
    assert st == ['pending_review', 'reversed']


def test_schedule_once_and_admin_penalty_needs_a_decision(web):
    c, conn, A, pe = web
    from utils import deduction_balances as DB
    _work(conn, pe)
    _tx(conn, 1, 'admin_penalty', 200)
    _tx(conn, 1, 'overpayment_recovery', 200)
    _approve(conn, pe)
    rows = {b['code']: b for b in DB.listing(conn)}
    assert rows['admin_penalty']['status'] == 'review_only'
    nm, ny = (1, YEAR + 1) if MONTH == 12 else (MONTH + 1, YEAR)
    with pytest.raises(ValueError, match='قرار'):
        DB.schedule(conn, rows['admin_penalty']['id'], nm, ny, 1)
    b = rows.get('overpayment_recovery') or rows['admin_penalty']
    tx = DB.schedule(conn, b['id'], nm, ny, 1, amount=b['remaining'] / 2, note='قرار المدير')
    ref = conn.execute('SELECT reference, amount FROM payroll_transactions WHERE id = ?', (tx,)).fetchone()
    assert ref['reference'] == f"BAL-{b['id']}" and ref['amount'] == pytest.approx(round(b['remaining'] / 2, 3))
    with pytest.raises(ValueError):
        DB.schedule(conn, b['id'], nm, ny, 1, note='تاني')         # مرة واحدة
    left = [x for x in DB.listing(conn) if x['status'] == b['status'] and x['code'] == b['code']]
    assert left and left[0]['remaining'] == pytest.approx(b['remaining'] - round(b['remaining'] / 2, 3), abs=0.002)
    conn.commit()
    page = c.get('/payroll/deduction-balances?status=').get_data(as_text=True)
    assert 'خصومات ماتنفّذتش' in page and 'اتجدول' in page


def test_attendance_penalty_cut_is_not_a_debt(web):
    c, conn, A, pe = web
    from utils import deduction_balances as DB
    conn.execute("INSERT OR REPLACE INTO salary_settings_v2 (setting_name, setting_value) VALUES ('penalty_monthly_cap_days', '0.5')")
    conn.commit()
    _work(conn, pe)
    ps, _ = pe.resolve_period(conn, MONTH, YEAR)
    conn.execute('DELETE FROM attendance_records WHERE employee_id = 1')
    d = ps
    for i in range(6):                                          # بصمة ناقصة 6 أيام
        while d.isoweekday() in (5, 6):
            d += timedelta(days=1)
        _punch(conn, 1, d.isoformat(), '08:00')
        d += timedelta(days=1)
    row = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    assert any(u['reason'] == 'penalty_cap' for u in row['unexecuted'])
    _approve(conn, pe)
    b = [x for x in DB.listing(conn, 'not_executed') if x['code'] == 'missing_punch_penalty']
    assert b
    with pytest.raises(ValueError):
        DB.schedule(conn, b[0]['id'], MONTH, YEAR, 1)
