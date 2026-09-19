"""شاشة الرفع السحابي: المفتاح لا يُعرض، والحفظ لا يمحوه.

## عطبان يُحرسان هنا

**(١) المفتاح في مصدر الصفحة.** `cloud_sync_api_key` يسمح برفع
بيانات الشركة كلِّها إلى السحابة. وإعادتُه في `value=` تعني ظهورَه
لكل من يفتح الإعدادات، وفي مصدر HTML، وفي أيّ لقطة شاشة. فالشاشة
تقول «محفوظ» ولا تقول ما هو.

**(٢) حفظُ الإعدادات يمحو المفتاح.** ما دام الحقل لا يُملأ من
المحفوظ، فهو يصل فارغًا في كل حفظٍ لأيّ إعدادٍ آخر. فلو قُرئ الفراغ
«امحُه» لتوقّف الرفع عند أوّل تعديلٍ لاسم الشركة — ولا شيء يربط
السببَ بالنتيجة.

يُشغَّل:  python -m pytest tests/test_cloud_sync_settings.py -v
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def live(tmp_path, monkeypatch):
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.cloud_sync as cs
    importlib.reload(cs)
    return db, cs


def test_the_key_is_never_rendered_into_the_page():
    """قفلٌ على القالب: حقلُ المفتاح بلا `value`."""
    html = open(os.path.join(ROOT, 'templates', 'settings.html'),
                encoding='utf-8').read()
    i = html.index('name="cloud_sync_api_key"')
    field = html[max(0, i - 300):i + 300]
    assert 'value=' not in field, 'المفتاح يُطبع في مصدر الصفحة'
    assert 'type="password"' in field


def test_the_route_reports_only_whether_a_key_exists(live):
    db, cs = live
    db.set_setting(cs.SETTING_KEY, 'sk_live_secret_value')

    from routes.main_routes import _cloud_sync_state
    state = _cloud_sync_state(db.get_db_connection())

    assert state['has_key'] is True
    assert 'sk_live_secret_value' not in str(state), 'المفتاح يخرج في حال الشاشة'


def test_no_key_saved_reads_as_absent(live):
    db, cs = live
    from routes.main_routes import _cloud_sync_state
    assert _cloud_sync_state(db.get_db_connection())['has_key'] is False


def test_the_state_carries_what_the_screen_needs(live):
    db, cs = live
    db.set_setting(cs.SETTING_ENABLED, '1')
    db.set_setting(cs.SETTING_URL, 'https://client.onz.one/api/sync')
    db.set_setting(cs.SETTING_CLIENT, 'uuid-9')
    db.set_setting(cs.SETTING_LAST_OK, '2026-09-19 10:00:00')

    from routes.main_routes import _cloud_sync_state
    state = _cloud_sync_state(db.get_db_connection())

    assert state['enabled'] is True
    assert state['url'] == 'https://client.onz.one/api/sync'
    assert state['client_id'] == 'uuid-9'
    assert state['last_ok'] == '2026-09-19 10:00:00'


def test_saving_settings_with_an_empty_key_field_keeps_the_key():
    """القلب. الحقل يصل فارغًا في كل حفظ، فلا يجوز أن يعني «امحُه»."""
    src = open(os.path.join(ROOT, 'routes', 'main_routes.py'),
               encoding='utf-8').read()
    i = src.index("_new_key = request.form.get('cloud_sync_api_key'")
    after = src[i:i + 300]
    assert 'if _new_key:' in after, (
        'المفتاح يُكتب بلا شرط — كل حفظٍ للإعدادات يمحوه')


def test_the_switch_has_a_presence_marker():
    """خانةٌ غير معلَّمة لا تُرسَل، فبلا علامةٍ لا يُميَّز الإطفاء."""
    html = open(os.path.join(ROOT, 'templates', 'settings.html'),
                encoding='utf-8').read()
    assert 'name="cloud_sync_form_present"' in html

    src = open(os.path.join(ROOT, 'routes', 'main_routes.py'),
               encoding='utf-8').read()
    assert "request.form.get('cloud_sync_form_present')" in src


def test_the_screen_says_what_does_not_leave_the_premises():
    """من لا يعرف ما يُرفع لا يستطيع أن يقرّر تشغيله."""
    html = open(os.path.join(ROOT, 'templates', 'settings.html'),
                encoding='utf-8').read()
    i = html.index('id="cloud-sync"')
    card = html[i:i + 5000]
    assert 'ولا يُرفع' in card
    assert 'مفتاح الترخيص' in card


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
