"""المسؤول يفتح البوابة فيرى بيانات موظفٍ آخر — فلتقل الشاشة ذلك.

## ما يقع

مسؤول النظام لا سجلّ موظف له عادةً. و`get_portal_employee_id` تُسقطه
إلى «أول موظف نشط» ليرى شكل البوابة — وهو سقوطٌ مقصود، **وصمتُه ليس
مقصودًا**.

فالمسؤول يفتح البوابة فيرى راتبًا وحضورًا وطلباتٍ لموظفٍ بعينه، ولا
شيء في الصفحة يقول إنها ليست له. فإمّا أن يقرأها على أنها بياناته —
وهي بيانات غيره — وإمّا أن يظنّ النظام معطوبًا لأن «بياناته» غريبة
عنه. وكلاهما وقع.

ولا يُكتب شيء باسم أحد على أي حال: مسارات الكتابة تطلب
`for_write=True` فتردّ None للمسؤول. الخلل في القراءة وحدها — وفي
أنها لا تُعلن.

## والوحدة الاختيارية في الشريط الجانبي

«متابعة المناديب» كانت مدفونة داخل قائمة «النظام» المطويّة مع
الأدوار والنسخ الاحتياطي، فمن فعّل الوحدة لم يجدها. وهي شاشة تشغيل
يوميّة لا أداة إدارة.

يُشغَّل:  python -m pytest tests/test_portal_preview_banner.py -v
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ------------------------------------------------ اللافتة

@pytest.fixture
def portal(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import routes.portal_routes as pr
    importlib.reload(pr)
    return pr, db


def _add_employee(db, name):
    conn = db.get_db_connection()
    cur = conn.execute(
        'INSERT INTO employees (name, employee_number, department, position, '
        'hire_date, salary, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)',
        (name, '900', 'الإدارة', 'موظف', '2026-01-01', 0))
    conn.commit()
    return cur.lastrowid


def _add_user(db, username, role, employee_id=None):
    conn = db.get_db_connection()
    cur = conn.execute(
        'INSERT INTO users (username, password, full_name, role, employee_id) '
        'VALUES (?, ?, ?, ?, ?)', (username, 'x', username, role, employee_id))
    conn.commit()
    return cur.lastrowid


def test_an_admin_with_no_employee_record_is_told_whose_portal_this_is(portal):
    from flask import Flask

    pr, db = portal
    emp = _add_employee(db, 'أحمد ممدوح')
    uid = _add_user(db, 'admin2', 'admin', None)

    app = Flask(__name__)
    app.secret_key = 't'
    with app.test_request_context('/portal/'):
        from flask import session
        session['user_id'] = uid
        assert pr.portal_preview_of() == 'أحمد ممدوح'


def test_an_employee_seeing_their_own_portal_gets_no_banner(portal):
    """اللافتة للاستعراض وحده. وظهورها لصاحب البوابة إزعاجٌ يُعلَّم تجاهله."""
    from flask import Flask

    pr, db = portal
    emp = _add_employee(db, 'أحمد ممدوح')
    uid = _add_user(db, 'ahmed', 'employee', emp)

    app = Flask(__name__)
    app.secret_key = 't'
    with app.test_request_context('/portal/'):
        from flask import session
        session['user_id'] = uid
        assert pr.portal_preview_of() is None


def test_an_admin_linked_to_an_employee_sees_their_own_portal(portal):
    """مسؤولٌ مرتبط بموظف ليس مستعرضًا — البوابة بوابته."""
    from flask import Flask

    pr, db = portal
    emp = _add_employee(db, 'أحمد ممدوح')
    uid = _add_user(db, 'boss', 'admin', emp)

    app = Flask(__name__)
    app.secret_key = 't'
    with app.test_request_context('/portal/'):
        from flask import session
        session['user_id'] = uid
        assert pr.portal_preview_of() is None


def test_no_session_no_banner(portal):
    from flask import Flask

    pr, _db = portal
    app = Flask(__name__)
    app.secret_key = 't'
    with app.test_request_context('/portal/'):
        assert pr.portal_preview_of() is None


def test_the_banner_is_in_the_page_and_names_the_employee():
    """القالب يعرضها فعلًا — ولا يعرضها لمن لا استعراض له."""
    path = os.path.join(ROOT, 'templates', 'portal', 'index.html')
    html = open(path, encoding='utf-8').read()

    assert '{% if preview_of %}' in html, 'اللافتة غير مشروطة — ستظهر للجميع'
    assert '{{ preview_of }}' in html, 'اللافتة لا تسمّي الموظف المستعرَض'
    assert 'ولا يُسجَّل باسمك شيء' in html, 'لا تقول إن الكتابة لا تقع'


def test_the_dashboard_passes_the_preview_to_the_template():
    """قفلٌ على الوصلة: قالبٌ يقرأ متغيّرًا لا يُمرَّر يُخفي اللافتة صامتًا."""
    src = open(os.path.join(ROOT, 'routes', 'portal_routes.py'),
               encoding='utf-8').read()
    assert 'preview_of=portal_preview_of()' in src


# ------------------------------------------------ الشريط الجانبي

def test_the_field_module_links_hide_when_the_module_is_off():
    base = open(os.path.join(ROOT, 'templates', 'base.html'), encoding='utf-8').read()

    m = re.search(r"\{% if field_module_on %\}(.*?)\{% endif %\}\s*\{% if has_permission\('page\.settings'\)",
                  base, re.S)
    assert m, 'روابط المناديب ليست داخل شرط الوحدة — ستظهر وهي مطفأة'
    block = m.group(1)
    assert "url_for('field.monitor')" in block
    assert "url_for('field.setup')" in block


def test_the_monitor_link_is_not_buried_in_the_admin_submenu():
    """شاشة تشغيلٍ يوميّة لا تُدفن مع الأدوار والنسخ الاحتياطي.

    كانت داخل `#adminSubmenu` المطويّ، فمن فعّل الوحدة لم يجدها —
    وهو ما وقع فعلًا.
    """
    base = open(os.path.join(ROOT, 'templates', 'base.html'), encoding='utf-8').read()

    start = base.index('id="adminSubmenu"')
    end = base.index('</ul>', start)
    submenu = base[start:end]

    assert "url_for('field.monitor')" not in submenu, (
        'رابط متابعة المناديب ما زال داخل قائمة «النظام» المطويّة')


def test_setup_stays_for_admins_only():
    """«المحطات وخطة المناديب» تكتب الخطة — فتبقى للمسؤول."""
    base = open(os.path.join(ROOT, 'templates', 'base.html'), encoding='utf-8').read()
    i = base.index("url_for('field.setup')")
    before = base[max(0, i - 400):i]
    assert "session.get('role') == 'admin'" in before


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
