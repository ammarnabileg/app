"""الإشعارات: هل يعرف من يعنيه الأمر، ولا يعرف غيره؟

المدير لا يفتح «فريقي» كل ساعة. فالطلب كان ينتظر يومين لا لأنه
مرفوض بل لأن أحدًا لم يره. وهذه الاختبارات تمرّ بالمسارات كما يمرّ
المستخدم: يُقدَّم طلب، فيُنظَر في صندوق المدير؛ ويُعتمد، فيُنظَر في
صندوق الموظف.

وأهمّها الثلاثة الأخيرة: صندوقٌ لا يراه غير صاحبه، ولا يُعلَّم من
غير صاحبه، وعطلٌ في الإشعار لا يُبطل الطلب الذي نجح.

يُشغَّل:  python -m pytest tests/test_notifications.py -v
"""
import os
import sqlite3
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def shop(tmp_path):
    """موظف، ومديره، وحسابٌ لكلٍّ منهما."""
    import importlib

    os.environ['HR_DATA_DIR'] = str(tmp_path)

    import utils.auth as auth
    importlib.reload(auth)
    auth.get_current_license_info = lambda: {'ok': True}

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import app as A
    importlib.reload(A)
    A.app.before_request_funcs[None] = [
        f for f in A.app.before_request_funcs.get(None, [])
        if f.__name__ != 'check_license_globally'
    ]
    A.app.config['TESTING'] = True

    path = os.path.join(str(tmp_path), 'hr_system.db')
    con = sqlite3.connect(path)
    cur = con.cursor()

    required = [r for r in cur.execute('PRAGMA table_info(employees)')
                if r[3] == 1 and r[4] is None and r[1] != 'id']

    def mkemp(number, name, manager=None):
        v = {r[1]: (1 if r[2].upper() in ('INTEGER', 'REAL', 'NUMERIC') else 'x')
             for r in required}
        v.update({'employee_number': number, 'name': name, 'is_active': 1})
        if manager:
            v['manager_id'] = manager
        cur.execute(f"INSERT INTO employees ({', '.join(v)}) "
                    f"VALUES ({', '.join('?' * len(v))})", list(v.values()))
        return cur.lastrowid

    boss = mkemp('M1', 'المدير')
    staff = mkemp('E1', 'الموظف', boss)
    other = mkemp('E2', 'زميل', boss)

    from werkzeug.security import generate_password_hash

    def mkuser(username, emp_id, role='user'):
        cur.execute('INSERT INTO users (username, password, full_name, role,'
                    ' is_active, employee_id) VALUES (?,?,?,?,1,?)',
                    (username, generate_password_hash('x'), username, role, emp_id))
        return cur.lastrowid

    boss_u = mkuser('boss', boss)
    staff_u = mkuser('staff', staff)
    other_u = mkuser('other', other)

    leave_type = cur.execute('SELECT id FROM leave_types LIMIT 1').fetchone()[0]
    con.commit()
    con.close()

    def client_for(user_id, emp_id):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s.update({'user_id': user_id, 'role': 'user', 'employee_id': emp_id})
        return c

    start = (date.today() + timedelta(days=7)).strftime('%Y-%m-%d')
    end = (date.today() + timedelta(days=9)).strftime('%Y-%m-%d')

    return {
        'app': A.app, 'db': path, 'leave_type': leave_type,
        'boss': boss, 'staff': staff, 'other': other,
        'boss_u': boss_u, 'staff_u': staff_u, 'other_u': other_u,
        'boss_c': client_for(boss_u, boss),
        'staff_c': client_for(staff_u, staff),
        'other_c': client_for(other_u, other),
        'start': start, 'end': end,
    }


def _inbox(client, unread=False):
    r = client.get('/portal/api/notifications' + ('?unread=1' if unread else ''))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


def _request_leave(shop):
    r = shop['staff_c'].post('/portal/api/request-leave', json={
        'leave_type_id': shop['leave_type'],
        'start_date': shop['start'], 'end_date': shop['end'],
        'reason': 'سفر',
    })
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True
    return r


# ------------------------------------------------------ بيت القصيد

def test_the_manager_is_told_a_request_is_waiting(shop):
    assert _inbox(shop['boss_c'])['unread'] == 0

    _request_leave(shop)

    box = _inbox(shop['boss_c'])
    assert box['unread'] == 1
    item = box['items'][0]
    assert 'الموظف' in item['title']
    assert item['kind'] == 'leave_requested'
    assert item['target'] == 'team'
    assert item['is_read'] == 0


def test_the_employee_is_told_the_decision(shop):
    _request_leave(shop)
    req_id = sqlite3.connect(shop['db']).execute(
        'SELECT id FROM leave_requests ORDER BY id DESC LIMIT 1').fetchone()[0]

    r = shop['boss_c'].post('/portal/api/approve-request',
                            json={'request_id': req_id, 'action': 'approve'})
    assert r.status_code == 200, r.get_json()

    box = _inbox(shop['staff_c'])
    assert box['unread'] == 1
    assert 'اعتُمد' in box['items'][0]['title']
    assert box['items'][0]['kind'] == 'leave_decided'


def test_a_rejection_says_so(shop):
    _request_leave(shop)
    req_id = sqlite3.connect(shop['db']).execute(
        'SELECT id FROM leave_requests ORDER BY id DESC LIMIT 1').fetchone()[0]

    shop['boss_c'].post('/portal/api/approve-request',
                        json={'request_id': req_id, 'action': 'reject'})

    assert 'رُفض' in _inbox(shop['staff_c'])['items'][0]['title']


def test_the_decider_is_not_told_of_their_own_decision(shop):
    """من ضغط «اعتماد» يعرف أنه اعتمد. إشعارٌ بذلك ضجيج."""
    _request_leave(shop)
    req_id = sqlite3.connect(shop['db']).execute(
        'SELECT id FROM leave_requests ORDER BY id DESC LIMIT 1').fetchone()[0]

    before = _inbox(shop['boss_c'])['unread']
    shop['boss_c'].post('/portal/api/approve-request',
                        json={'request_id': req_id, 'action': 'approve'})

    kinds = [i['kind'] for i in _inbox(shop['boss_c'])['items']]
    assert 'leave_decided' not in kinds
    assert _inbox(shop['boss_c'])['unread'] == before


def test_an_excuse_reaches_the_manager(shop):
    r = shop['staff_c'].post('/portal/api/request-excuse',
                             json={'date': shop['start'], 'type': 'personal',
                                   'reason': 'ظرف'})
    assert r.status_code == 200, r.get_json()

    box = _inbox(shop['boss_c'])
    assert box['unread'] == 1
    assert 'استئذان شخصي' in box['items'][0]['title']


# --------------------------------------------- الصندوق لصاحبه وحده

def test_a_colleague_sees_nothing_of_it(shop):
    """زميلٌ له المدير نفسه لا يرى طلبات غيره."""
    _request_leave(shop)

    assert _inbox(shop['other_c'])['unread'] == 0
    assert _inbox(shop['other_c'])['items'] == []


def test_one_account_cannot_mark_another_account_notifications(shop):
    """رقم الإشعار يأتي من طلب، فلولا الشرط على الحساب لعلّم أحدُهم
    إشعارات غيره — وهو كشفٌ لوجودها لا أكثر، لكنه كشفٌ لا داعي له."""
    _request_leave(shop)
    boss_item = _inbox(shop['boss_c'])['items'][0]

    r = shop['other_c'].post('/portal/api/notifications/read',
                             json={'ids': [boss_item['id']]})
    assert r.status_code == 200
    assert r.get_json()['changed'] == 0, 'عُلِّم إشعار حسابٍ آخر'

    assert _inbox(shop['boss_c'])['unread'] == 1, 'تغيّر صندوق المدير'


def test_marking_read_works_for_the_owner(shop):
    _request_leave(shop)
    box = _inbox(shop['boss_c'])

    r = shop['boss_c'].post('/portal/api/notifications/read',
                            json={'ids': [box['items'][0]['id']]})
    assert r.get_json()['changed'] == 1
    assert r.get_json()['unread'] == 0
    assert _inbox(shop['boss_c'], unread=True)['items'] == []


def test_marking_all_read_needs_no_ids(shop):
    _request_leave(shop)
    shop['staff_c'].post('/portal/api/request-excuse',
                         json={'date': shop['start'], 'type': 'mission'})
    assert _inbox(shop['boss_c'])['unread'] == 2

    r = shop['boss_c'].post('/portal/api/notifications/read', json={})
    assert r.get_json()['unread'] == 0


# ------------------------------------- الإشعار لا يُبطل ما نجح

def test_a_broken_notifier_does_not_lose_the_request(shop, monkeypatch):
    """بيت القصيد الثاني.

    الإخطار يقع **بعد** حفظ الطلب. فلو انفجر القيد لسببٍ ما، يجب أن
    يبقى الطلب محفوظًا ويصل ردٌّ ناجح — لا أن يضيع طلب موظفٍ لأن
    جدول إشعارات تعطّل.
    """
    from utils import notifications as notif
    monkeypatch.setattr(notif, 'leave_requested',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))

    _request_leave(shop)   # يؤكّد success=True بنفسه

    con = sqlite3.connect(shop['db'])
    n = con.execute('SELECT COUNT(*) FROM leave_requests').fetchone()[0]
    con.close()
    assert n == 1, 'ضاع الطلب لأن الإشعار تعثّر'


def test_bootstrap_carries_the_unread_count(shop):
    """الشارة تَظهر عند فتح التطبيق بلا طلبٍ ثانٍ."""
    _request_leave(shop)

    j = shop['boss_c'].get('/portal/api/bootstrap').get_json()
    assert j['unread_notifications'] == 1

    j2 = shop['other_c'].get('/portal/api/bootstrap').get_json()
    assert j2['unread_notifications'] == 0


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
