"""ما يصل المستأجرَ من اللوحة: مزامنة بيانات الدخول، وتفعيل الترخيص.

متغيّرات البيئة تُقرأ مرة واحدة عند إنشاء القاعدة، فتكفي للبداية ولا
تكفي للمزامنة: العميل يغيّر كلمة مروره على الموقع بعد شهر، والمتغيّر في
Coolify يبقى كما هو — وتغييره يحتاج إعادة نشر، وإعادة النشر لا تُعيد
إنشاء القاعدة فلا أثر لها. لذلك المزامنة تمشي على ردّ فحص الترخيص، وهي
قناة قائمة تعمل دوريًّا ولا تفتح منفذًا ولا تقبل طلبًا واردًا.

يُشغَّل:  python -m pytest tests/test_panel_sync.py -v
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLD_HASH = '$2y$12$Idudf3XLMT0Gb8JYrr76..ANBzUi39XgB0BUWcJJCMToJJGrQqINe'   # SitePass!2026
NEW_HASH = '$2y$12$6Ck0Y7nZ7dVQK1wq5dQ3E.xQ2mFqZ8p1cQ0LxYwq3hU9nTgWc0Yqi'   # بصمة أخرى


@pytest.fixture
def tenant(tmp_path, monkeypatch):
    """مستأجر مجهَّز من اللوحة: قاعدته منشأة بحساب العميل."""
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('HR_ADMIN_USERNAME', 'harbi')
    monkeypatch.setenv('HR_ADMIN_PASSWORD_HASH', OLD_HASH)

    import utils.db as db
    importlib.reload(db)
    db.init_db()

    import utils.panel_sync as ps
    importlib.reload(ps)

    path = os.path.join(str(tmp_path), 'hr_system.db')

    def admin_row():
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row
        try:
            r = con.execute("SELECT username, password FROM users WHERE role='admin'").fetchone()
            return dict(r) if r else None
        finally:
            con.close()

    return {'sync': ps, 'admin_row': admin_row, 'path': path}


# ------------------------------------------------ مزامنة بيانات الدخول

def test_password_change_on_the_site_reaches_the_tenant(tenant):
    assert tenant['admin_row']()['password'] == OLD_HASH

    changed = tenant['sync'].apply_admin_sync(
        {'username': 'harbi', 'password_hash': NEW_HASH})

    assert changed is True
    assert tenant['admin_row']()['password'] == NEW_HASH


def test_username_change_is_followed_too(tenant):
    tenant['sync'].apply_admin_sync({'username': 'harbi-new', 'password_hash': NEW_HASH})
    assert tenant['admin_row']()['username'] == 'harbi-new'

    # وبعد تغيّر الاسم يبقى الصفّ متتبَّعًا: لولا حفظ الاسم المزامَن
    # لفَقَد النظام أثر الحساب عند التغيير الثاني.
    tenant['sync'].apply_admin_sync({'username': 'harbi-newer', 'password_hash': OLD_HASH})
    row = tenant['admin_row']()
    assert row['username'] == 'harbi-newer'
    assert row['password'] == OLD_HASH


def test_unchanged_credentials_write_nothing(tenant):
    assert tenant['sync'].apply_admin_sync(
        {'username': 'harbi', 'password_hash': OLD_HASH}) is False


def test_malformed_payloads_are_ignored(tenant):
    for bad in [None, {}, 'x', {'username': 'harbi'}, {'password_hash': NEW_HASH},
                {'username': '', 'password_hash': NEW_HASH}]:
        assert tenant['sync'].apply_admin_sync(bad) is False

    assert tenant['admin_row']()['password'] == OLD_HASH


def test_manual_install_is_never_touched(tmp_path, monkeypatch):
    """بلا HR_ADMIN_USERNAME لا حساب مرتبطًا باللوحة — فلا يُمسّ شيء.

    وإلا لغيّرت اللوحة كلمة مرور مدير في تركيب يدوي لا علاقة له بها.
    """
    import importlib

    monkeypatch.setenv('HR_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('HR_ADMIN_USERNAME', raising=False)
    monkeypatch.delenv('HR_ADMIN_PASSWORD_HASH', raising=False)

    import utils.db as db
    importlib.reload(db)
    db.init_db()
    import utils.panel_sync as ps
    importlib.reload(ps)

    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    before = con.execute("SELECT password FROM users WHERE username='admin'").fetchone()[0]
    con.close()

    assert ps.apply_admin_sync({'username': 'admin', 'password_hash': NEW_HASH}) is False

    con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
    after = con.execute("SELECT password FROM users WHERE username='admin'").fetchone()[0]
    con.close()
    assert after == before


# ------------------------------------------------------ تفعيل الترخيص

def test_no_key_in_environment_does_nothing(tenant, monkeypatch):
    monkeypatch.delenv('HR_LICENSE_KEY', raising=False)
    ok, msg = tenant['sync'].bootstrap_license_from_environment()
    assert ok is False


def test_a_rejected_key_is_not_saved(tenant, monkeypatch):
    """مفتاح لم يُقبل لا يُحفَظ.

    حفظُه يجعل الشاشة تقول «مفعَّل» ثم يُمنع العميل عند أول فحص — وهو
    أسوأ من مطالبته بالمفتاح من البداية.
    """
    import utils.license as lic

    monkeypatch.setenv('HR_LICENSE_KEY', 'L-20991231-comaped#')   # الصيغة الملغاة
    ok, _msg = tenant['sync'].bootstrap_license_from_environment()

    assert ok is False
    assert not lic.get_saved_license_key()


def test_a_valid_key_activates_without_the_client_typing_it(tenant, monkeypatch):
    import hashlib

    import utils.license as lic

    d = '20271231'
    sig = hashlib.sha256((d + lic.LICENSE_SECRET).encode()).hexdigest()[:12].lower()
    key = lic.encode_license_payload_fernet(d, sig)

    # الفحص الشبكي معزول: المقصود هنا مسار التفعيل لا الاتصال باللوحة.
    monkeypatch.setattr(lic, 'check_online_license_secure', lambda k: (True, 'ok'))
    monkeypatch.setenv('HR_LICENSE_KEY', key)

    ok, _msg = tenant['sync'].bootstrap_license_from_environment()

    assert ok is True
    assert lic.get_saved_license_key() == key


def test_an_already_activated_tenant_is_left_alone(tenant, monkeypatch):
    """إعادة النشر لا تُرجع المستأجر إلى مفتاح قديم بعد تجديد."""
    import hashlib

    import utils.license as lic

    d = '20271231'
    sig = hashlib.sha256((d + lic.LICENSE_SECRET).encode()).hexdigest()[:12].lower()
    current = lic.encode_license_payload_fernet(d, sig)
    lic.save_license_key(current)

    monkeypatch.setenv('HR_LICENSE_KEY', 'LE2-something-older')
    ok, msg = tenant['sync'].bootstrap_license_from_environment()

    assert ok is False
    assert msg == 'مفعَّل بالفعل'
    assert lic.get_saved_license_key() == current


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
