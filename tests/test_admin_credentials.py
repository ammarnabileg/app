"""حساب المدير الأول: بيانات العميل نفسها، أو الافتراضي عند غيابها.

كان كل مستأجر جديد يُولد بـ admin/admin123 — كلمة مطبوعة في كل دليل
ويعرفها كل من نصّب النظام مرة، على نظامٍ منشور على عنوان عامّ.

صار المستأجر يقرأ عند أول إقلاع:

    HR_ADMIN_USERNAME       اسم المستخدم كما سجّل به العميل في onz.one
    HR_ADMIN_PASSWORD_HASH  بصمة كلمة مروره كما هي مخزَّنة في اللوحة
    HR_ADMIN_FULLNAME       اسمه المعروض (اختياري)

البصمة لا الكلمة: اللوحة تخزّن password_hash بـPASSWORD_BCRYPT ولا
تحتفظ بالأصل، فلا تمرّ كلمة مرور صريحة في متغيّر بيئة يبقى مقروءًا على
الخادم إلى الأبد.

يُشغَّل:  python -m pytest tests/test_admin_credentials.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# بصمة PHP حقيقية: password_hash('SitePass!2026', PASSWORD_BCRYPT)
PHP_HASH = '$2y$12$Idudf3XLMT0Gb8JYrr76..ANBzUi39XgB0BUWcJJCMToJJGrQqINe'
PHP_PLAIN = 'SitePass!2026'


# --------------------------------------------- التحقّق من صيغتَي البصمة

def test_php_bcrypt_hash_is_accepted():
    """بصمة اللوحة بصيغة '$2y$' يجب أن تُقبل.

    هذا هو بيت القصيد: check_password_hash من werkzeug لا تفهم '$2y$'
    وترمي عندها، فلو بقي الدخول معتمدًا عليها وحدها لكتب العميل كلمته
    الصحيحة ورُدّ في كل مرة.
    """
    from utils.passwords import verify_password

    assert verify_password(PHP_HASH, PHP_PLAIN) is True
    assert verify_password(PHP_HASH, 'wrong') is False


def test_werkzeug_hash_still_accepted():
    """ما ينشئه النظام نفسه يبقى مقبولًا — لا كسر لحسابات قائمة."""
    from werkzeug.security import generate_password_hash
    from utils.passwords import verify_password

    h = generate_password_hash('local-pass')
    assert verify_password(h, 'local-pass') is True
    assert verify_password(h, 'other') is False


def test_broken_hash_denies_instead_of_raising():
    """بصمة تالفة تعني «لا يطابق»، لا صفحة دخول تنهار."""
    from utils.passwords import verify_password

    for bad in ['', None, 'not-a-hash', '$2y$invalid', '$2y$12$tooshort']:
        assert verify_password(bad, 'anything') is False

    assert verify_password(PHP_HASH, None) is False


def test_bcrypt_detection():
    from utils.passwords import is_bcrypt

    assert is_bcrypt('$2y$12$x') and is_bcrypt('$2b$12$x') and is_bcrypt('$2a$12$x')
    assert not is_bcrypt('pbkdf2:sha256:600000$abc$def')
    assert not is_bcrypt('')


# ------------------------------------- الحساب الذي يُنشأ عند أول إقلاع

def _first_admin(tmp_path, env):
    """يبني قاعدة مستأجر جديدة تحت البيئة المعطاة ويعيد صفّ المدير."""
    import importlib
    import sqlite3

    old = {k: os.environ.get(k) for k in
           ('HR_DATA_DIR', 'HR_ADMIN_USERNAME', 'HR_ADMIN_PASSWORD_HASH', 'HR_ADMIN_FULLNAME')}
    try:
        os.environ['HR_DATA_DIR'] = str(tmp_path)
        for k in ('HR_ADMIN_USERNAME', 'HR_ADMIN_PASSWORD_HASH', 'HR_ADMIN_FULLNAME'):
            os.environ.pop(k, None)
        os.environ.update(env)

        import utils.db as db
        importlib.reload(db)
        db.init_db()

        con = sqlite3.connect(os.path.join(str(tmp_path), 'hr_system.db'))
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT username, password, full_name, role FROM users WHERE role='admin'").fetchone()
        con.close()
        return dict(row) if row else None
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_tenant_gets_the_client_own_credentials(tmp_path):
    """المستأجر المجهَّز من اللوحة يدخله العميل ببيانات الموقع نفسها."""
    from utils.passwords import verify_password

    admin = _first_admin(tmp_path, {
        'HR_ADMIN_USERNAME': 'harbi',
        'HR_ADMIN_PASSWORD_HASH': PHP_HASH,
        'HR_ADMIN_FULLNAME': 'شركة الحربي',
    })

    assert admin is not None
    assert admin['username'] == 'harbi'
    assert admin['full_name'] == 'شركة الحربي'
    assert verify_password(admin['password'], PHP_PLAIN) is True

    # والأهم: الحساب المعروف للجميع لم يعد موجودًا على هذا المستأجر.
    assert admin['username'] != 'admin'


def test_manual_install_keeps_the_old_default(tmp_path):
    """بلا متغيّرات — تركيب يدوي أو تطوير — يبقى admin/admin123."""
    from utils.passwords import verify_password

    admin = _first_admin(tmp_path, {})

    assert admin['username'] == 'admin'
    assert verify_password(admin['password'], 'admin123') is True


def test_the_hash_is_stored_not_the_password(tmp_path):
    """ما يُكتب في قاعدة المستأجر بصمة، لا الكلمة."""
    admin = _first_admin(tmp_path, {
        'HR_ADMIN_USERNAME': 'harbi',
        'HR_ADMIN_PASSWORD_HASH': PHP_HASH,
    })

    assert admin['password'] == PHP_HASH
    assert PHP_PLAIN not in admin['password']




# ------------------------------ البصمة ليست كلمة مرور

def test_typing_the_stored_hash_does_not_log_you_in():
    """كان مسار «الكلمة الصريحة» يقارن المخزَّن بالمكتوب دائمًا.

    فمن كتب البصمة نفسها في خانة كلمة المرور دخل. جرّبتُه على نظام يعمل:
    ٣٠٢، أي دخول ناجح.

    وليست حالةً نظرية: نسخة احتياطية واحدة تحمل بصمات كل المستخدمين، وهي
    أكثر ملف يُرسَل بالبريد ويُنسَخ على ذاكرة ويُرفع إلى سحابة — فكان من
    يقرأ نسخةً يملك كلمة مرور كل حساب فيها. ومزامنة اللوحة تنقل البصمة
    أيضًا.
    """
    from werkzeug.security import generate_password_hash
    from utils.passwords import verify_password

    for stored in (generate_password_hash('real-one'), PHP_HASH):
        assert verify_password(stored, stored) is False, 'قُبلت البصمة كلمةَ مرور'

    # والكلمة الصحيحة تبقى تعمل
    h = generate_password_hash('real-one')
    assert verify_password(h, 'real-one') is True


def test_legacy_plaintext_accounts_still_work():
    """الحالة التي يوجد المسار من أجلها: عميل ركّب قبل التشفير."""
    from utils.passwords import verify_password

    assert verify_password('plainpass', 'plainpass') is True
    assert verify_password('plainpass', 'other') is False


def test_looks_hashed_knows_the_formats():
    from werkzeug.security import generate_password_hash
    from utils.passwords import looks_hashed

    assert looks_hashed(generate_password_hash('x'))
    assert looks_hashed(PHP_HASH)
    assert looks_hashed('$2b$12$abc')
    assert looks_hashed('scrypt:32768:8:1$salt$hash')

    # كلمات صريحة — يجب ألّا تُعدّ بصمات، وإلا انقفل الحساب القديم
    for plain in ['plainpass', '123456', '', None, 'pbkdf2', '$2y']:
        assert not looks_hashed(plain), plain


if __name__ == '__main__':
    import pytest
    raise SystemExit(pytest.main([__file__, '-v']))
