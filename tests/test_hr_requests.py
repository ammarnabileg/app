# -*- coding: utf-8 -*-
"""طلبات الموظّفين ومسار الموافقات (المرحلة ٥) — utils/hr_requests وشاشاتها."""
import importlib
from datetime import date

import pytest
from werkzeug.security import generate_password_hash

from tests.test_device_access import env  # noqa: F401
from tests.test_device_drivers import web  # noqa: F401


def _user(con, username, employee_id=None, perm=None):
    cur = con.execute("INSERT INTO users (username, password, full_name, role, employee_id) VALUES (?, ?, ?, 'employee', ?)",
                      (username, generate_password_hash('x'), username.upper(), employee_id))
    uid = cur.lastrowid
    if perm:
        con.execute("INSERT OR IGNORE INTO roles (name) VALUES ('HR')")
        rid = con.execute("SELECT id FROM roles WHERE name = 'HR'").fetchone()[0]
        con.execute('INSERT OR IGNORE INTO role_permissions (role_id, permission_id) '
                    'SELECT ?, id FROM permissions WHERE code = ?', (rid, perm))
        con.execute('INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)', (uid, rid))
    con.commit()
    return uid


@pytest.fixture
def H(env):
    db, da, con = env
    import utils.hr_requests as H
    importlib.reload(H)
    # 101 (id 1) موظّف، ومديره 103 (id 3) له حساب.
    con.execute('UPDATE employees SET manager_id = 3, is_active = 1 WHERE id IN (1, 3)')
    con.execute('UPDATE employees SET manager_id = NULL WHERE id = 3')
    con.commit()
    users = {'emp': _user(con, 'emp', 1), 'mgr': _user(con, 'mgr', 3), 'hr': _user(con, 'hr', None, H.PERMISSION),
             'other': _user(con, 'other', 2)}
    return H, con, users


def test_expense_goes_manager_then_hr_then_lands_in_payroll(H):
    H, con, u = H
    rid = H.create(con, 1, 'expense', {'amount': '12.5', 'description': 'بنزين'})
    req = H.get(con, rid)
    assert [s['status'] for s in req['steps']] == ['current', 'waiting']
    assert H.pending_for_user(con, u['mgr'])[0]['id'] == rid
    assert not H.pending_for_user(con, u['hr']), 'الموارد البشريّة بعد المدير'
    assert H.decide(con, rid, u['other'], 'x', True)[0] is False, 'ليس دوره'
    assert H.decide(con, rid, u['mgr'], 'MGR', True) == (True, 'اتعتمد وراح للخطوة الجاية')
    assert H.pending_for_user(con, u['hr'])[0]['id'] == rid
    ok, msg = H.decide(con, rid, u['hr'], 'HR', True)
    assert ok and msg == 'اتعتمد الطلب نهائيًا'
    tx = con.execute("SELECT t.amount, t.reference, it.code, t.month, t.year FROM payroll_transactions t "
                     "JOIN payroll_item_types it ON it.id = t.item_type_id WHERE t.employee_id = 1").fetchone()
    assert tx['amount'] == 12.5 and tx['reference'] == f'REQ-{rid}' and tx['code'] == 'expense_reimb'
    assert (tx['month'], tx['year']) == (date.today().month, date.today().year)
    req = H.get(con, rid)
    assert req['status'] == 'approved' and req['result_ref'].startswith('رواتب')
    kinds = [r[0] for r in con.execute('SELECT kind FROM notifications WHERE user_id = ?', (u['emp'],))]
    assert 'request_decided' in kinds, 'الموظّف يعرف'
    assert con.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = 'request_pending'",
                       (u['mgr'],)).fetchone()[0] == 1


def test_a_step_nobody_can_do_is_skipped_and_reject_ends_it(H):
    H, con, u = H
    rid = H.create(con, 3, 'loan', {'amount': 600, 'months': 6})          # 103 بلا مدير
    req = H.get(con, rid)
    assert req['steps'][0]['status'] == 'skipped' and req['steps'][1]['status'] == 'current'
    assert H.decide(con, rid, u['hr'], 'HR', False, 'مش دلوقتي') == (True, 'اترفض الطلب')
    req = H.get(con, rid)
    assert req['status'] == 'rejected' and req['steps'][1]['note'] == 'مش دلوقتي'
    assert not con.execute('SELECT 1 FROM employee_loans').fetchone()


def test_loan_starts_next_month_with_installment(H):
    H, con, u = H
    rid = H.create(con, 1, 'loan', {'amount': '900', 'months': '6', 'reason': 'ظرف'})
    H.decide(con, rid, u['mgr'], 'M', True)
    H.decide(con, rid, u['hr'], 'H', True)
    loan = con.execute('SELECT * FROM employee_loans WHERE employee_id = 1').fetchone()
    assert loan['principal'] == 900 and loan['monthly_installment'] == 150 and loan['status'] == 'active'
    t = date.today()
    assert (loan['start_month'], loan['start_year']) == ((1, t.year + 1) if t.month == 12 else (t.month + 1, t.year))


def test_letter_is_numbered_and_renders_the_employee(H):
    H, con, u = H
    tid = con.execute("SELECT id FROM letter_templates WHERE code = 'salary_certificate'").fetchone()[0]
    con.execute("UPDATE employees SET national_id = '290010100000', position = 'محاسب<b>' WHERE id = 1")
    con.commit()
    rid = H.create(con, 1, 'letter', {'template_id': tid, 'addressee': 'بنك الكويت الوطني'})
    assert [s['approver'] for s in H.get(con, rid)['steps']] == ['hr']
    H.decide(con, rid, u['hr'], 'H', True)
    req = H.get(con, rid)
    assert req['result_ref'] == f'L-{date.today().year}-0001'
    out = H.render_letter(con, req)
    assert '290010100000' in out['body'] and 'بنك الكويت الوطني' in out['body']
    assert 'محاسب&lt;b&gt;' in out['body'], 'بيانات الموظّف تُهرَّب'
    assert '500.000' in out['body'] and '{{' not in out['body']


def test_validation_and_cancel(H):
    H, con, u = H
    for t, p in (('expense', {'amount': 0, 'description': 'x'}), ('loan', {'amount': 10, 'months': 0}),
                 ('overtime', {'hours': 3}), ('letter', {'template_id': 999}), ('vacation', {})):
        with pytest.raises(ValueError):
            H.create(con, 1, t, p)
    rid = H.create(con, 1, 'overtime', {'hours': 3, 'work_date': '2026-10-10'})
    assert not H.cancel(con, rid, 2), 'طلبُ غيره'
    assert H.cancel(con, rid, 1)
    assert H.get(con, rid)['status'] == 'cancelled'
    assert H.decide(con, rid, u['mgr'], 'M', True)[0] is False
    H.save_type(con, 'overtime', False, ['manager'])
    with pytest.raises(ValueError):
        H.create(con, 1, 'overtime', {'hours': 3, 'work_date': '2026-10-10'})


def test_specific_user_and_dept_manager_steps(H):
    H, con, u = H
    con.execute("INSERT OR IGNORE INTO departments_master (name) VALUES ('D')")
    did = con.execute("SELECT id FROM departments_master WHERE name = 'D'").fetchone()[0]
    dm = _user(con, 'dm')
    con.execute('UPDATE users SET managed_department_id = ? WHERE id = ?', (did, dm))
    con.commit()
    H.save_type(con, 'expense', True, ['dept_manager', f"user:{u['other']}"])
    rid = H.create(con, 1, 'expense', {'amount': 5, 'description': 'x'})
    assert H.pending_for_user(con, dm)[0]['id'] == rid
    H.decide(con, rid, dm, 'DM', True)
    assert H.pending_for_user(con, u['other'])[0]['id'] == rid
    H.decide(con, rid, u['other'], 'O', True)
    assert H.get(con, rid)['status'] == 'approved'


# ------------------------------------------------------------ الشاشات

def _login(c, con, username):
    uid = con.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()[0]
    with c.session_transaction() as s:
        s.clear()
        s.update({'user_id': uid, 'username': username, 'role': 'employee'})


def test_portal_flow_and_letter_access(web):
    c, con, A = web
    import utils.hr_requests as H
    con.execute('UPDATE employees SET manager_id = 3, is_active = 1 WHERE id = 1')
    con.commit()
    _user(con, 'emp', 1)
    _user(con, 'mgr', 3)
    _user(con, 'stranger', 2)
    _login(c, con, 'emp')
    t = c.get('/portal/api/requests/types').get_json()
    assert t['can_request'] and {x['code'] for x in t['types']} == set(H.TYPES)
    letter = t['letters'][0]['id']
    r = c.post('/portal/api/requests', json={'type': 'expense', 'payload': {
        'amount': '7', 'description': 'تاكسي',
        'attachment': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='}}).get_json()
    assert r['success']
    exp_id = r['id']
    lid = c.post('/portal/api/requests', json={'type': 'letter', 'payload': {'template_id': letter}}).get_json()['id']
    mine = c.get('/portal/api/requests/mine').get_json()['requests']
    assert {m['id'] for m in mine} == {exp_id, lid} and any(m['has_attachment'] for m in mine)
    assert c.get(f'/requests/{exp_id}/attachment').status_code == 200
    assert c.post(f'/portal/api/requests/{exp_id}/decide', json={'action': 'approve'}).status_code == 403, \
        'الموظّف لا يعتمد طلبه'

    _login(c, con, 'stranger')
    assert c.get(f'/requests/{exp_id}/attachment').status_code == 404
    assert c.get(f'/requests/{lid}/letter').status_code == 404
    assert c.post('/portal/api/requests', json={'type': 'expense', 'payload': {'amount': 1, 'description': 'x'}}
                  ).get_json()['success']

    _login(c, con, 'mgr')
    appr = c.get('/portal/api/requests/approvals').get_json()['requests']
    assert [a['id'] for a in appr] == [exp_id]
    assert c.post(f'/portal/api/requests/{exp_id}/decide', json={'action': 'approve'}).get_json()['success']
    assert 'الطلبات والموافقات' in c.get('/requests/').get_data(as_text=True)

    # الأدمن (سوبر أدمن = موارد بشريّة) يعتمد ويطبع.
    admin = con.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
    with c.session_transaction() as s:
        s.clear()
        s.update({'user_id': admin, 'role': 'admin', 'username': 'admin'})
    page = c.get('/requests/').get_data(as_text=True)
    assert 'مسارات الموافقة' in page and 'قوالب الخطابات' in page
    c.post(f'/requests/{lid}/decide', data={'action': 'approve'})
    html = c.get(f'/requests/{lid}/letter').get_data(as_text=True)
    assert f'L-{date.today().year}-0001' in html and 'موظف 101' in html

    _login(c, con, 'emp')
    assert c.get(f'/requests/{lid}/letter').status_code == 200
    assert c.post('/requests/settings', data={}).status_code in (302, 403)
    assert H.type_settings(con)['letter']['enabled'], 'الموظّف لا يغيّر الإعدادات'


def test_admin_without_employee_record_cannot_file_requests(web):
    c, con, A = web
    r = c.post('/portal/api/requests', json={'type': 'expense', 'payload': {'amount': 1, 'description': 'x'}})
    assert r.status_code == 403
