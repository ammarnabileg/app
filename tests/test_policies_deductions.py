# -*- coding: utf-8 -*-
"""الخطوة ٣د — الخصومات والتأمينات (utils/policies)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401
from tests.test_policies_wage import _pol, _sun, _row, _ded  # noqa: F401


def _item(conn, eid, code, amount):
    it = conn.execute('SELECT id FROM payroll_item_types WHERE code = ?', (code,)).fetchone()[0]
    conn.execute('INSERT INTO employee_salary_items (employee_id, item_type_id, calc_method, amount, start_date) '
                 "VALUES (?, ?, 'fixed', ?, '2000-01-01')", (eid, it, amount))
    conn.commit()


def _work(conn, pe, eid=1, n=20):
    s = _sun(conn, pe)
    for i in range(n):
        _punch(conn, eid, (s + timedelta(days=i)).isoformat(), '08:00', '16:00')


def test_allowance_by_allowance(web):
    c, conn, A, pe = web
    _item(conn, 1, 'housing', 100)
    _item(conn, 1, 'transport', 40)
    _punch(conn, 1, _sun(conn, pe).isoformat(), '09:00', '16:00')        # ساعة تأخير
    base = _ded(_row(pe, conn), 'lateness_deduction')                    # الأساسي بس (260)
    _pol(conn, 'item_in_late', '1', scope='item', scope_id='housing')    # السكن بس يدخل
    assert _ded(_row(pe, conn), 'lateness_deduction') == pytest.approx(base * 360 / 260, abs=0.002)
    _pol(conn, 'late_wage_base', 'wage')                                  # كلهم — إلا اللي اتقال مايدخلش
    _pol(conn, 'item_in_late', '0', scope='item', scope_id='transport')
    assert _ded(_row(pe, conn), 'lateness_deduction') == pytest.approx(base * 360 / 260, abs=0.002)


def test_net_floor_order(web):
    c, conn, A, pe = web
    it = conn.execute("SELECT id FROM payroll_item_types WHERE code = 'admin_penalty'").fetchone()[0]
    conn.execute('INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount) VALUES (1, ?, ?, ?, 250)',
                 (it, MONTH, YEAR))
    conn.execute("INSERT INTO employee_loans (employee_id, principal, monthly_installment, start_month, start_year, status) "
                 "VALUES (1, 500, 20, ?, ?, 'active')", (MONTH, YEAR))
    conn.commit()
    _work(conn, pe)
    r = _row(pe, conn)
    assert _ded(r, 'loan_installment') < 20, 'الافتراضي: السلفة أول حاجة بتترحل'
    _pol(conn, 'net_floor_order', 'tx_loans_penalties')
    r = _row(pe, conn)
    assert _ded(r, 'loan_installment') == pytest.approx(20.0) and _ded(r, 'admin_penalty') < 250


def test_unexecuted_status_from_policy(web):
    c, conn, A, pe = web
    from utils import deduction_balances as DB
    it = conn.execute("SELECT id FROM payroll_item_types WHERE code = 'admin_penalty'").fetchone()[0]
    conn.execute('INSERT INTO payroll_transactions (employee_id, item_type_id, month, year, amount) VALUES (1, ?, ?, ?, 400)',
                 (it, MONTH, YEAR))
    conn.commit()
    _work(conn, pe)
    _pol(conn, 'unexecuted_admin_penalty', 'pending_review')
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}, {'employee_id': 2}], 1)
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    pe.lock_run(conn, MONTH, YEAR, 1)
    assert [b['status'] for b in DB.listing(conn) if b['code'] == 'admin_penalty'] == ['pending_review']


def test_pifss_subject_and_funds(web):
    c, conn, A, pe = web
    from utils import policies as PO
    _work(conn, pe, 1)
    _work(conn, pe, 2)
    conn.execute("UPDATE employees SET nationality = 'كويتي' WHERE id = 1")
    conn.execute("UPDATE employees SET nationality = 'مصري' WHERE id = 2")
    conn.commit()
    _pol(conn, 'pifss_enabled', '1')
    assert _ded(_row(pe, conn, 1), 'social_insurance_emp') > 0 and _ded(_row(pe, conn, 2), 'social_insurance_emp') == 0
    _pol(conn, 'pifss_subject', 'all')
    assert _ded(_row(pe, conn, 2), 'social_insurance_emp') > 0
    PO.add_pifss_fund(conn, 'الأساسي', 5, 6, 200, '2000-01-01', 1)
    PO.add_pifss_fund(conn, 'التكميلي', 2.5, 3, None, '2000-01-01', 1)
    conn.commit()
    r = _row(pe, conn, 1)
    lines = [d for d in r['deductions'] if d['code'] == 'social_insurance_emp']
    assert len(lines) == 2
    assert sorted(round(d['amount'], 3) for d in lines) == sorted([round(200 * 0.05, 3), round(260 * 0.025, 3)])
    assert r['employer_pifss'] == pytest.approx(200 * 0.06 + 260 * 0.03, abs=0.002)
    page = c.get('/payroll/policies').get_data(as_text=True)
    assert 'صناديق التأمينات' in page and 'البدلات الثابتة بند بند' in page
