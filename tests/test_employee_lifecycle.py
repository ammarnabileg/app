# -*- coding: utf-8 -*-
"""متابعة الموظفين (المرحلة ٥): تنبيهات الوثائق، وقوائم الاستلام والتسليم."""
import importlib
from datetime import date, timedelta

import pytest

from tests.test_device_access import env  # noqa: F401
from tests.test_device_drivers import web  # noqa: F401
from tests.test_hr_requests import _user


@pytest.fixture
def L(env):
    db, da, con = env
    import utils.employee_lifecycle as L
    importlib.reload(L)
    L.ensure_schema(con)
    con.execute('UPDATE employees SET is_active = 1 WHERE id = 1')
    con.commit()
    return L, con


def _kinds(con, uid):
    return [r[0] for r in con.execute("SELECT title FROM notifications WHERE user_id = ? AND kind = 'doc_expiry' "
                                      "ORDER BY id", (uid,))]


def test_milestones_once_each_and_renewal_restarts(L):
    L, con = L
    today = date(2026, 10, 9)
    hr = _user(con, 'hr', None, 'employee.edit')
    emp = _user(con, 'emp', 1)
    con.execute('UPDATE employees SET residency_expiry_date = ? WHERE id = 1', ((today + timedelta(days=20)).isoformat(),))
    con.commit()
    assert L.notify_due(con, today) == 1
    assert L.notify_due(con, today) == 0, 'مرّة واحدة'
    assert _kinds(con, hr) == ['الإقامة — تنتهي بعد 20 يوم'] and len(_kinds(con, emp)) == 1
    assert L.notify_due(con, today + timedelta(days=10)) == 0, 'لسه ما وصلش ٧ أيام'
    assert L.notify_due(con, today + timedelta(days=14)) == 1
    assert L.notify_due(con, today + timedelta(days=25)) == 1, 'انتهت'
    assert L.notify_due(con, today + timedelta(days=26)) == 0
    # اتجدّدت: تاريخ جديد → يبدأ العدّ من أوّل.
    con.execute("UPDATE employees SET residency_expiry_date = '2027-10-20' WHERE id = 1")
    con.commit()
    assert L.notify_due(con, date(2027, 9, 25)) == 1


def test_late_entry_gets_one_notice_not_three(L):
    L, con = L
    today = date(2026, 10, 9)
    _user(con, 'hr', None, 'employee.edit')
    con.execute("UPDATE employees SET passport_expiry_date = '2026-10-08' WHERE id = 1")
    con.commit()
    assert L.notify_due(con, today) == 1
    assert L.notify_due(con, today) == 0
    items = L.expiring(con, within=30, today=today)
    assert items[0]['doc'] == 'جواز السفر' and items[0]['days'] == -1
    assert not [i for i in items if i['employee_id'] == 2], 'الموقوف لا'


def test_checklist_from_template_and_completion(L):
    L, con = L
    cid = L.start(con, 1, 'onboarding', None)
    assert L.start(con, 1, 'onboarding', None) == cid, 'المفتوحة تتفتح'
    lst = L.checklists(con)[0]
    assert lst['total'] == len(L.DEFAULT_TEMPLATES['onboarding']) and lst['done'] == 0
    L.save_template(con, 'onboarding', [{'owner': 'hr', 'title': 'بس ده'}])
    assert len(L.checklists(con)[0]['items']) == lst['total'], 'القالب الجديد لا يغيّر قائمة بدأت'
    for it in lst['items']:
        L.toggle(con, it['id'], True, 'HR')
    assert not L.checklists(con), 'اكتملت'
    assert L.checklists(con, open_only=False)[0]['completed_at']
    L.toggle(con, lst['items'][0]['id'], False, 'HR')
    assert L.checklists(con)[0]['done'] == lst['total'] - 1, 'رجعت مفتوحة'


def test_suggestions(L):
    L, con = L
    today = date.today()
    con.execute('UPDATE employees SET hire_date = ? WHERE id = 1', ((today - timedelta(days=5)).isoformat(),))
    con.execute('UPDATE employees SET end_of_service_date = ? WHERE id = 3', ((today + timedelta(days=10)).isoformat(),))
    con.commit()
    s = {(x['employee_id'], x['kind']) for x in L.suggestions(con)}
    assert (1, 'onboarding') in s and (3, 'offboarding') in s
    L.start(con, 1, 'onboarding')
    assert (1, 'onboarding') not in {(x['employee_id'], x['kind']) for x in L.suggestions(con)}


def test_page_and_residency_saved_from_form(web):
    c, con, A = web
    con.execute("UPDATE employees SET passport_expiry_date = ? WHERE id = 1",
                ((date.today() + timedelta(days=3)).isoformat(),))
    con.commit()
    page = c.get('/hr/lifecycle/').get_data(as_text=True)
    assert 'متابعة الموظفين' in page and 'جواز السفر' in page
    assert c.post('/hr/lifecycle/start', data={'employee_id': 1, 'kind': 'offboarding'}).status_code == 302
    item = con.execute('SELECT id FROM employee_checklist_items LIMIT 1').fetchone()[0]
    assert c.post(f'/hr/lifecycle/item/{item}', json={'done': True}).get_json()['success']
    assert 'name="residency_expiry_date"' in c.get('/employees/edit/1').get_data(as_text=True)
    import routes.employee_routes as E
    with A.app.test_request_context('/', method='POST', data={'residency_number': 'R1',
                                                               'residency_expiry_date': '2027-01-01'}):
        E._apply_residency(con, 1)
    con.commit()
    assert tuple(con.execute('SELECT residency_number, residency_expiry_date FROM employees WHERE id = 1'
                             ).fetchone()) == ('R1', '2027-01-01')
