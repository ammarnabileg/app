# -*- coding: utf-8 -*-
"""سياسات الحضور والرواتب بتاريخ سريان (utils/policies)."""
from datetime import timedelta

import pytest

from tests.test_hours_export import MONTH, YEAR, _punch, web  # noqa: F401


def _days(conn, pe):
    start, _ = pe.resolve_period(conn, MONTH, YEAR)
    sun = start + timedelta(days=(7 - start.isoweekday() % 7) % 7)
    return sun, sun + timedelta(days=7)


def test_nothing_changes_without_policies(web):
    c, conn, A, pe = web
    from utils import policies as PO
    P = PO.load(conn)
    assert P.get('grace_late_minutes') == '10' and P.resolve('grace_late_minutes')[1] == 'legacy'
    assert P.resolve('daily_rate_basis')[1] in ('legacy', 'system')


def test_a_policy_change_applies_from_its_date_only(web):
    c, conn, A, pe = web
    from utils import policies as PO
    d1, d2 = _days(conn, pe)
    _punch(conn, 1, d1.isoformat(), '08:20', '16:00')            # 20 دقيقة تأخير
    _punch(conn, 1, d2.isoformat(), '08:20', '16:00')
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['late_mins'] == 40
    PO.add(conn, 'grace_late_minutes', '30', d2.isoformat(), 1, note='سماح جديد')
    conn.commit()
    assert pe.compute_month_metrics(conn, MONTH, YEAR)[1]['late_mins'] == 20, 'اليوم الأول بالقديم والتاني بالجديد'


def test_employee_exception_beats_company_and_group(web):
    c, conn, A, pe = web
    from utils import policies as PO
    d1, _ = _days(conn, pe)
    conn.execute("UPDATE employees SET branch_location = 'فرع أ'")
    for eid in (1, 2):
        _punch(conn, eid, d1.isoformat(), '08:20', '16:00')
    PO.add(conn, 'late_arrival_policy', 'warning', '2000-01-01', 1, note='الشركة إنذار')
    PO.add(conn, 'late_arrival_policy', 'quarter_day', '2000-01-01', 1, scope='group', scope_id='فرع أ', note='الفرع ربع يوم')
    PO.add(conn, 'late_arrival_policy', 'actual_time', '2000-01-01', 1, scope='employee', scope_id='1', note='استثناء')
    conn.commit()
    m = pe.compute_month_metrics(conn, MONTH, YEAR)
    assert m[1]['late_mins'] == 20, 'الموظف بالدقيقة'
    assert m[2]['late_mins'] == 120, 'المجموعة ربع يوم (8 ساعات)'
    with pytest.raises(ValueError):
        PO.add(conn, 'late_arrival_policy', 'bogus', '2000-01-01', 1, note='x y z')
    with pytest.raises(ValueError):
        PO.add(conn, 'late_arrival_policy', 'warning', '2000-01-01', 1, note='')


def test_money_policy_and_revoke(web):
    c, conn, A, pe = web
    from utils import policies as PO
    d1, _ = _days(conn, pe)
    _punch(conn, 1, d1.isoformat(), '08:00', '18:00')
    base = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    pid = PO.add(conn, 'weekday_ot_multiplier', '2', '2000-01-01', 1, note='معامل جديد')
    conn.commit()
    new = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    ot = lambda r: next(a['amount'] for a in r['allowances'] if a['code'] == 'overtime')
    assert ot(new) == pytest.approx(ot(base) * 2 / 1.25, abs=0.002)
    PO.revoke(conn, pid, 1, 'رجوع')
    conn.commit()
    again = next(r for r in pe.compute_monthly_payroll(conn, MONTH, YEAR)['rows'] if r['employee_id'] == 1)
    assert ot(again) == pytest.approx(ot(base))
    assert PO.history(conn)[0]['revoked_at'], 'السطر فاضل في السجل'


def test_approval_keeps_a_snapshot_and_page_confirms_impact(web):
    c, conn, A, pe = web
    from utils import policies as PO
    import json
    PO.add(conn, 'grace_late_minutes', '15', '2000-01-01', 1, note='سماح')
    pe.attest_hours(conn, MONTH, YEAR, [{'employee_id': 1}, {'employee_id': 2}], 1)
    pe.save_monthly_payroll(conn, MONTH, YEAR, 1)
    pe.lock_run(conn, MONTH, YEAR, 1)
    conn.commit()
    snap = json.loads(conn.execute('SELECT policy_snapshot FROM payroll_runs WHERE month = ? AND year = ?',
                                   (MONTH, YEAR)).fetchone()[0])
    assert any(r['key'] == 'grace_late_minutes' and r['value'] == '15' for r in snap['rows'])
    ps, _ = pe.resolve_period(conn, MONTH, YEAR)
    imp = PO.impact(conn, ps.isoformat())
    assert {'month': MONTH, 'year': YEAR, 'saved': True} in imp['locked']
    r = c.post('/payroll/policies', data={'key': 'grace_late_minutes', 'value': '20', 'effective_from': ps.isoformat(),
                                          'scope': 'company', 'note': 'تجربة'})
    page = r.get_data(as_text=True)
    assert 'تأكيد وحفظ' in page and 'مش هتتغيّر' in page
    assert c.get('/payroll/policies').status_code == 200
