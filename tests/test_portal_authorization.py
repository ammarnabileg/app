"""صلاحيات بوابة الموظف: من يعتمد الطلبات، ومن يكتب باسم من.

عطلان وجدتُهما بتشغيل البوابة لا بقراءتها:

  1. اعتماد الطلبات كان يفحص `req['manager_id'] != emp_id` وحده. وفي
     بايثون `None != None` تساوي False، فحسابٌ غير مرتبط بموظف
     (emp_id = None) كان يعتمد أي طلب لموظف لا مدير مُسنَد له
     (manager_id = None). الحالان شائعان لا نادران، والطلب انتقل فعلًا
     من pending إلى approved في التجربة.

  2. get_portal_employee_id كانت تُسقِط المسؤول إلى «أول موظف نشط»
     ليستعرض البوابة. مقبول للقراءة، وخطأ في الكتابة: مسؤول يضغط
     «تسجيل حضور» كان يُسجّل حضورًا باسم موظف لم يحضر — وسجلّ الحضور
     مصدر الأجر.

يُشغَّل:  python -m pytest tests/test_portal_authorization.py -v
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def portal(tmp_path):
    """نظام مستأجر حقيقي بقاعدته، والترخيص معزول عن الصلاحيات."""
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

    # أعمدة employees الإلزامية تختلف بين النسخ؛ تُملأ آليًّا.
    required = [r for r in cur.execute("PRAGMA table_info(employees)")
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

    orphan = mkemp('E900', 'موظف بلا مدير')          # manager_id = NULL
    manager = mkemp('E901', 'المدير')
    report = mkemp('E902', 'مرؤوس', manager)

    lt = cur.execute("SELECT id FROM leave_types LIMIT 1").fetchone()[0]
    cur.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date,"
                " end_date, days_count, status) VALUES (?,?,?,?,?,'pending')",
                (orphan, lt, '2026-10-01', '2026-10-03', 3))
    orphan_req = cur.lastrowid
    cur.execute("INSERT INTO leave_requests (employee_id, leave_type_id, start_date,"
                " end_date, days_count, status) VALUES (?,?,?,?,?,'pending')",
                (report, lt, '2026-11-01', '2026-11-02', 2))
    report_req = cur.lastrowid

    from werkzeug.security import generate_password_hash
    cur.execute("INSERT INTO users (username, password, full_name, role, is_active,"
                " employee_id) VALUES (?,?,?,?,1,NULL)",
                ('ghost', generate_password_hash('x'), 'حساب بلا موظف', 'user'))
    ghost_user = cur.lastrowid
    admin_user = cur.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    con.commit()
    con.close()

    def approve(session_data, request_id):
        c = A.app.test_client()
        with c.session_transaction() as s:
            s.update(session_data)
        r = c.post('/portal/api/approve-request',
                   json={'request_id': request_id, 'action': 'approve'})
        return r.status_code

    def status_of(request_id):
        con2 = sqlite3.connect(path)
        try:
            return con2.execute("SELECT status FROM leave_requests WHERE id = ?",
                                (request_id,)).fetchone()[0]
        finally:
            con2.close()

    return {
        'app': A.app, 'db_path': path, 'approve': approve, 'status_of': status_of,
        'orphan_req': orphan_req, 'report_req': report_req,
        'manager': manager, 'report': report,
        'ghost_user': ghost_user, 'admin_user': admin_user,
    }


# ------------------------------------------------- اعتماد طلبات الإجازة

def test_account_without_an_employee_cannot_approve(portal):
    """الثغرة نفسها: None != None تساوي False، فكان الفحص يمرّ."""
    code = portal['approve'](
        {'user_id': portal['ghost_user'], 'role': 'user', 'employee_id': None},
        portal['orphan_req'])

    assert code == 403
    assert portal['status_of'](portal['orphan_req']) == 'pending'


def test_request_without_a_manager_is_not_approvable_by_any_employee(portal):
    """موظف عادي لا يعتمد طلبًا لا مدير مُسنَد لصاحبه."""
    code = portal['approve'](
        {'user_id': portal['ghost_user'], 'role': 'user',
         'employee_id': portal['report']},
        portal['orphan_req'])

    assert code == 403
    assert portal['status_of'](portal['orphan_req']) == 'pending'


def test_employee_cannot_approve_a_colleague_request(portal):
    code = portal['approve'](
        {'user_id': portal['ghost_user'], 'role': 'user',
         'employee_id': portal['report']},
        portal['report_req'])

    assert code == 403
    assert portal['status_of'](portal['report_req']) == 'pending'


def test_direct_manager_can_approve(portal):
    """الحالة المشروعة تبقى تعمل — وإلا لكان الإصلاح تعطيلًا."""
    code = portal['approve'](
        {'user_id': portal['ghost_user'], 'role': 'user',
         'employee_id': portal['manager']},
        portal['report_req'])

    assert code == 200
    assert portal['status_of'](portal['report_req']) == 'approved'


def test_admin_can_approve_anything(portal):
    code = portal['approve'](
        {'user_id': portal['admin_user'], 'role': 'admin', 'employee_id': None},
        portal['orphan_req'])

    assert code == 200
    assert portal['status_of'](portal['orphan_req']) == 'approved'


# --------------------------------- المسؤول: يستعرض ولا يكتب باسم غيره

def test_admin_preview_can_still_read(portal):
    c = portal['app'].test_client()
    with c.session_transaction() as s:
        s['user_id'] = portal['admin_user']
        s['role'] = 'admin'
        s['employee_id'] = None

    assert c.get('/portal/api/my-data').status_code == 200


def test_admin_preview_cannot_punch_as_someone_else(portal):
    """أخطر الحالتين: سجلّ الحضور مصدر الأجر."""
    con = sqlite3.connect(portal['db_path'])
    before = con.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
    con.close()

    c = portal['app'].test_client()
    with c.session_transaction() as s:
        s['user_id'] = portal['admin_user']
        s['role'] = 'admin'
        s['employee_id'] = None

    r = c.post('/portal/api/punch', json={'type': 'in', 'lat': 29.3, 'lng': 48.0})
    assert r.status_code == 400

    con = sqlite3.connect(portal['db_path'])
    after = con.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0]
    con.close()
    assert after == before, 'سُجّل حضور باسم موظف لم يحضر'


def test_portal_never_takes_an_employee_id_from_the_request():
    """هوية الموظف من الجلسة وحدها، وإلا قرأ كلُّ موظف بيانات غيره."""
    import re

    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'routes', 'portal_routes.py'),
        encoding='utf-8').read()

    hits = re.findall(r'request\.(?:args|form|json)[^\n]*?(?:employee_id|emp_id)', src)
    assert not hits, f'هوية الموظف تُقرأ من الطلب: {hits}'


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
